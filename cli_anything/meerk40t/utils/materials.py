"""Material profile loading and persistence.

Material profiles are JSON data files. Bundled materials ship inside the
package (``cli_anything/meerk40t/materials/``) and user materials live
under ``~/.config/cli-anything-meerk40t/materials/`` (overridable via the
``CLI_ANYTHING_CONFIG_HOME`` environment variable). A user material of the
same name overrides the bundled one.
"""

from __future__ import annotations

import json
from pathlib import Path

from cli_anything.meerk40t.utils.profiles import (
    BUNDLED_PACKAGE,
    _load_json_file,
    _validate_name,
    resolve_config_home,
)

MATERIALS_SUBDIR = "materials"


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


def load_material(name: str, config_home=None) -> dict | None:
    """Load a material by name. User materials override bundled ones.

    Returns the parsed material dict or ``None`` when the name is unknown.
    Raises ``ValueError`` for an invalid name.
    """
    _validate_name(name)
    user_dir = user_materials_dir(config_home)
    user_path = user_dir / f"{name}.json"
    if user_path.exists():
        data = _load_json_file(user_path)
        if data is not None:
            return data
    bundled_path = bundled_materials_dir() / f"{name}.json"
    if bundled_path.exists():
        data = _load_json_file(bundled_path)
        if data is not None:
            return data
    return None


def list_materials(config_home=None) -> list[dict]:
    """Enumerate materials from both sources, returning ``{name, origin, path}``."""
    found: dict[str, dict] = {}
    bundled_dir = bundled_materials_dir()
    if bundled_dir.exists():
        for p in sorted(bundled_dir.glob("*.json")):
            data = _load_json_file(p)
            if data is None:
                continue
            name = str(data.get("name") or p.stem)
            found[name] = {"name": name, "origin": "bundled", "path": str(p)}
    user_dir = user_materials_dir(config_home)
    if user_dir.exists():
        for p in sorted(user_dir.glob("*.json")):
            data = _load_json_file(p)
            if data is None:
                continue
            name = str(data.get("name") or p.stem)
            found[name] = {"name": name, "origin": "user", "path": str(p)}
    return sorted(found.values(), key=lambda d: d["name"])


def available_names(config_home=None) -> list[str]:
    """Return sorted material names available from either source."""
    return [m["name"] for m in list_materials(config_home)]


def save_user_material(name: str, data: dict, config_home=None) -> Path:
    """Write a user material JSON. Returns the written path.

    The file is named ``<name>.json`` inside the user materials directory.
    """
    _validate_name(name)
    user_dir = user_materials_dir(config_home)
    user_dir.mkdir(parents=True, exist_ok=True)
    material = dict(data)
    material["name"] = name
    path = user_dir / f"{name}.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(material, fh, indent=2)
        fh.write("\n")
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
