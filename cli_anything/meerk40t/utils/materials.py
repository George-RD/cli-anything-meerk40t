"""Material profile loading and persistence.

Material profiles are JSON data files. Bundled materials ship inside the
package (``cli_anything/meerk40t/materials/``) and user materials live
under ``~/.config/cli-anything-meerk40t/materials/`` (overridable via the
``CLI_ANYTHING_CONFIG_HOME`` environment variable). A user material of the
same name overrides the bundled one.

Loading is fail-closed: a corrupt file raises ``MaterialError`` rather than
being silently skipped or fallen back to a bundled copy. Writes are atomic and
durable (see ``cli_anything.meerk40t.utils.atomic_io``).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from cli_anything.meerk40t.utils.atomic_io import atomic_write_json
from cli_anything.meerk40t.utils.profiles import (
    BUNDLED_PACKAGE,
    _validate_name,
    resolve_config_home,
)

MATERIALS_SUBDIR = "materials"


class MaterialError(Exception):
    """Base class for material loading/validation failures."""


class MaterialParseError(MaterialError):
    """The material file is not valid JSON (or contains non-finite constants)."""


class MaterialValidationError(MaterialError):
    """The material structure violates the required schema."""


def bundled_materials_dir() -> Path:
    """Return the package-bundled materials directory (via importlib resources)."""
    try:
        import importlib.resources as ir

        ref = ir.files(BUNDLED_PACKAGE).joinpath(MATERIALS_SUBDIR)
        return Path(str(ref))
    except Exception:  # pragma: no cover - extremely defensive fallback
        return Path(__file__).resolve().parent.parent / MATERIALS_SUBDIR


def user_materials_dir(config_home=None) -> Path:
    base = Path(config_home) if config_home is not None else resolve_config_home()
    return Path(base) / MATERIALS_SUBDIR


def _load_material_json(path: Path):
    """Strictly parse a material JSON file.

    Rejects NaN/Infinity/-Infinity anywhere in the file (json.load's default
    would happily accept them). Raises ``MaterialParseError`` on any failure;
    the caller treats that as a corrupt, unreadable material.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(
                fh,
                parse_constant=lambda c: (_ for _ in ()).throw(
                    ValueError(f"non-finite JSON constant is not allowed: {c}")
                ),
            )
    except (OSError, ValueError) as exc:
        raise MaterialParseError(f"{path}: {exc}") from exc


def validate_material(data) -> dict:
    """Validate the material structure.

    Returns *data* unchanged when valid. Raises ``MaterialValidationError`` for
    any structural or value defect. Bounds enforced here are the universally
    safe lower bounds (power/passes integer >= 1, speed > 0, finite). Tighter
    per-machine ranges (e.g. power <= 1000) remain the job-prep layer's job.
    """
    if not isinstance(data, dict):
        raise MaterialValidationError("material must be a JSON object")
    machines = data.get("machines")
    if not isinstance(machines, dict):
        raise MaterialValidationError("material.machines must be an object")
    for mname, mcfg in machines.items():
        if not isinstance(mcfg, dict):
            raise MaterialValidationError(f"machine {mname!r} must be an object")
        roles = mcfg.get("roles")
        if not isinstance(roles, dict):
            raise MaterialValidationError(f"machine {mname!r}.roles must be an object")
        for rname, rset in roles.items():
            if not isinstance(rset, dict):
                raise MaterialValidationError(f"role {rname!r} must be an object")
            for field in ("kind", "power", "speed", "passes"):
                if field not in rset:
                    raise MaterialValidationError(
                        f"role {rname!r} is missing required field {field!r}"
                    )
            power = rset["power"]
            if isinstance(power, bool) or not isinstance(power, int) or power < 1:
                raise MaterialValidationError(
                    f"role {rname!r}.power must be an integer >= 1"
                )
            speed = rset["speed"]
            if (
                isinstance(speed, bool)
                or not isinstance(speed, (int, float))
                or not math.isfinite(speed)
                or speed <= 0
            ):
                raise MaterialValidationError(
                    f"role {rname!r}.speed must be a number greater than zero"
                )
            passes = rset["passes"]
            if isinstance(passes, bool) or not isinstance(passes, int) or passes < 1:
                raise MaterialValidationError(
                    f"role {rname!r}.passes must be an integer >= 1"
                )
    return data


def load_material(name: str, config_home=None) -> dict | None:
    """Load a material by name. User materials override bundled ones.

    Returns the parsed material dict, or ``None`` when the name is unknown.
    Raises ``ValueError`` for an invalid name. Raises ``MaterialError`` (parse
    or validation) for a corrupt material -- it is never silently fallen back
    to a bundled copy of the same name.
    """
    _validate_name(name)
    user_dir = user_materials_dir(config_home)
    user_path = user_dir / f"{name}.json"
    if user_path.exists():
        # Fail-closed: report the corrupt user override instead of silently
        # returning the bundled material of the same name.
        return validate_material(_load_material_json(user_path))
    bundled_path = bundled_materials_dir() / f"{name}.json"
    if bundled_path.exists():
        return validate_material(_load_material_json(bundled_path))
    return None


def list_materials(config_home=None) -> list[dict]:
    """Enumerate materials from both sources, returning ``{name, origin, path}``.

    A corrupt material file is skipped (never crashes the index) but is not
    silently repaired or listed.
    """
    found: dict[str, dict] = {}
    bundled_dir = bundled_materials_dir()
    if bundled_dir.exists():
        for p in sorted(bundled_dir.glob("*.json")):
            try:
                data = validate_material(_load_material_json(p))
            except MaterialError:
                continue
            name = str(data.get("name") or p.stem)
            found[name] = {"name": name, "origin": "bundled", "path": str(p)}
    user_dir = user_materials_dir(config_home)
    if user_dir.exists():
        for p in sorted(user_dir.glob("*.json")):
            try:
                data = validate_material(_load_material_json(p))
            except MaterialError:
                continue
            name = str(data.get("name") or p.stem)
            found[name] = {"name": name, "origin": "user", "path": str(p)}
    return sorted(found.values(), key=lambda d: d["name"])


def available_names(config_home=None) -> list[str]:
    """Return sorted material names available from either source."""
    return [m["name"] for m in list_materials(config_home)]


def save_user_material(name: str, data: dict, config_home=None) -> Path:
    """Validate and write a user material JSON atomically. Returns the path.

    Validation runs before any write, so a rejected material leaves an existing
    file byte-for-byte intact and never creates a file for a new invalid name.
    """
    _validate_name(name)
    material = dict(data)
    material["name"] = name
    validate_material(material)  # fail-closed: raises before touching disk
    user_dir = user_materials_dir(config_home)
    user_dir.mkdir(parents=True, exist_ok=True)
    path = user_dir / f"{name}.json"
    atomic_write_json(path, material)
    return path


def resolve_settings(material: dict, machine: str) -> dict[str, dict]:
    """Return the role settings dict for the given machine.

    Raises ``ValueError`` if the material has no entry for the machine.
    """
    name = material.get("name", "<unknown>")
    machines = material.get("machines", {})
    if machine not in machines:
        known = sorted(machines.keys())
        raise ValueError(
            f"material {name!r} has no settings for machine {machine!r}; known: {known}"
        )
    return dict(machines[machine].get("roles", {}))
