"""Machine profile loading and persistence.

Profiles are JSON data files, not code. Bundled profiles ship inside the
package (``cli_anything/meerk40t/profiles/``) and user profiles live under
``~/.config/cli-anything-meerk40t/profiles/`` (overridable via the
``CLI_ANYTHING_CONFIG_HOME`` environment variable). A user profile of the
same name overrides the bundled one.

Schema (all keys optional unless noted)::

    {
      "name": "sculpfun-s9",          # string
      "device": "grbl",               # backend device type
      "baud": 115200,                 # int
      "bedwidth": "410mm",            # Length string
      "bedheight": "400mm",           # Length string
      "has_endstops": false,          # bool
      "notes": "",                    # free text
      "provenance": {"firmware": "Grbl", "verified": true}
    }
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

CONFIG_ENV = "CLI_ANYTHING_CONFIG_HOME"
BUNDLED_PACKAGE = "cli_anything.meerk40t"
PROFILES_SUBDIR = "profiles"

# Allowed profile identifiers: a safe basename with no path separators.
PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not PROFILE_NAME_RE.match(name):
        raise ValueError(f"invalid profile name: {name!r}")
    if "/" in name or "\\" in name or name in (".", "..") or name.startswith(".."):
        raise ValueError(f"invalid profile name: {name!r}")


def resolve_config_home() -> Path:
    """Return the config directory, honouring ``CLI_ANYTHING_CONFIG_HOME``."""
    env = os.environ.get(CONFIG_ENV)
    if env:
        return Path(env)
    return Path.home() / ".config" / "cli-anything-meerk40t"


def bundled_profiles_dir() -> Path:
    """Return the package-bundled profiles directory (via importlib resources)."""
    try:
        import importlib.resources as ir

        ref = ir.files(BUNDLED_PACKAGE).joinpath(PROFILES_SUBDIR)
        return Path(str(ref))
    except Exception:  # pragma: no cover - extremely defensive fallback
        return Path(__file__).resolve().parent.parent / PROFILES_SUBDIR


def user_profiles_dir(config_home=None) -> Path:
    base = Path(config_home) if config_home is not None else resolve_config_home()
    return Path(base) / PROFILES_SUBDIR


def _load_json_file(path: Path) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def load_profile(name: str, config_home=None) -> dict | None:
    """Load a profile by name. User profiles override bundled ones.

    Returns the parsed schema dict or ``None`` when the name is unknown.
    Raises ``ValueError`` for an invalid name. The returned dict contains
    only persisted schema fields (never ``origin``); callers that need the
    source should use :func:`list_profiles`.
    """
    _validate_name(name)
    user_dir = user_profiles_dir(config_home)
    user_path = user_dir / f"{name}.json"
    if user_path.exists():
        data = _load_json_file(user_path)
        if data is not None:
            return data
    bundled_path = bundled_profiles_dir() / f"{name}.json"
    if bundled_path.exists():
        data = _load_json_file(bundled_path)
        if data is not None:
            return data
    return None


def list_profiles(config_home=None) -> list[dict]:
    """Enumerate profiles from both sources, returning ``{name, origin, path}``."""
    found: dict[str, dict] = {}
    bundled_dir = bundled_profiles_dir()
    if bundled_dir.exists():
        for p in sorted(bundled_dir.glob("*.json")):
            data = _load_json_file(p)
            if data is None:
                continue
            name = str(data.get("name") or p.stem)
            found[name] = {"name": name, "origin": "bundled", "path": str(p)}
    user_dir = user_profiles_dir(config_home)
    if user_dir.exists():
        for p in sorted(user_dir.glob("*.json")):
            data = _load_json_file(p)
            if data is None:
                continue
            name = str(data.get("name") or p.stem)
            found[name] = {"name": name, "origin": "user", "path": str(p)}
    return sorted(found.values(), key=lambda d: d["name"])


def save_user_profile(name: str, profile: dict, config_home=None) -> Path:
    """Write a user profile JSON. Returns the written path.

    The file is named ``<name>.json`` inside the user profiles directory and
    contains only the documented schema fields (``origin`` is a display
    adjunct and is never persisted).
    """
    _validate_name(name)
    user_dir = user_profiles_dir(config_home)
    user_dir.mkdir(parents=True, exist_ok=True)
    path = user_dir / f"{name}.json"
    schema_keys = (
        "name",
        "device",
        "baud",
        "bedwidth",
        "bedheight",
        "has_endstops",
        "notes",
        "provenance",
    )
    data = {k: profile[k] for k in schema_keys if k in profile}
    data["name"] = name
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    return path


def available_names(config_home=None) -> list[str]:
    """Return sorted profile names available from either source."""
    return [p["name"] for p in list_profiles(config_home)]
