#!/usr/bin/env python3
"""Installed-wheel smoke test for cli-anything-meerk40t.

Run AFTER ``pip install`` of the digest-verified wheel, in a clean
environment, to prove the published artifact actually works. It exercises
both shipped entry points:

  * the console script (``cli-anything-meerk40t``) via ``--help`` and a
    read-only ``--json machine list``;
  * the ``meerk40t.extension`` plugin (``cli_anything_bridge``) implicitly,
    by booting a real MeerK40t kernel through the bundled backend wrapper and
    performing a real load + SVG save (default ``dummy`` device, no hardware);

and confirms the packaged runtime resources resolve via ``importlib.resources``.

The verification logic is factored into importable functions so the same code
runs in CI and is covered by pytest regressions (issue #34). Heavy imports of
the harness package / meerk40t happen lazily inside the functions, keeping the
module importable and unit-testable without a live kernel.

CLI:
    python scripts/smoke_installed_wheel.py
"""

from __future__ import annotations

import importlib.metadata as _md
import importlib.resources as _ir
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PKG = "cli_anything.meerk40t"
CONSOLE_SCRIPT = "cli-anything-meerk40t"
EXTENSION_EP = "cli_anything_bridge"

# (subpackage-relative directory, filename) pairs that MUST ship in the wheel
# for the installed CLI/tests to function.
REQUIRED_RESOURCES = [
    ("profiles", "sculpfun-s9.json"),
    ("materials", "kraft-350gsm.json"),
    ("skills", "SKILL.md"),
]

# A minimal but well-formed SVG a real MeerK40t backend can load and re-save.
# Generated at runtime; never depends on a test-only fixture not shipped.
MINIMAL_SVG = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" '
    'viewBox="0 0 20 20"><rect x="0" y="0" width="20" height="20" /></svg>\n'
)


class SmokeError(Exception):
    """Raised when any installed-wheel smoke check fails."""


# --------------------------------------------------------------------------- #
# 1. Packaged resources                                                        #
# --------------------------------------------------------------------------- #
def check_packaged_resources(
    package: str = PKG, resources: list[tuple[str, str]] | None = None
) -> None:
    """Assert every required runtime resource is present and non-empty.

    Args:
        package: importable package whose resources are inspected.
        resources: override the required (subdir, filename) list (for tests).
    """
    needed = REQUIRED_RESOURCES if resources is None else resources
    for subdir, filename in needed:
        try:
            ref = _ir.files(package).joinpath(subdir, filename)
            text = ref.read_text(encoding="utf-8")
        except Exception as exc:  # FileNotFoundError / ModuleNotFoundError / etc.
            raise SmokeError(
                f"packaged resource missing or unreadable: "
                f"{package}/{subdir}/{filename} ({exc})"
            ) from exc
        if not text.strip():
            raise SmokeError(
                f"packaged resource is empty: {package}/{subdir}/{filename}"
            )


# --------------------------------------------------------------------------- #
# 2. Entry points (both shipped)                                               #
# --------------------------------------------------------------------------- #
def check_entry_points(get_entry_points=_md.entry_points) -> None:
    """Assert both shipped entry points resolve and load.

    Args:
        get_entry_points: injectable entry-point source (for tests). Defaults
            to ``importlib.metadata.entry_points``.
    """
    eps = get_entry_points()

    console = [e for e in eps.select(group="console_scripts") if e.name == CONSOLE_SCRIPT]
    if not console:
        raise SmokeError(f"missing console_script entry point: {CONSOLE_SCRIPT}")
    try:
        console[0].load()
    except Exception as exc:
        raise SmokeError(
            f"console_script entry point {CONSOLE_SCRIPT} fails to load: {exc}"
        ) from exc

    extension = [e for e in eps.select(group="meerk40t.extension") if e.name == EXTENSION_EP]
    if not extension:
        raise SmokeError(f"missing meerk40t.extension entry point: {EXTENSION_EP}")
    try:
        extension[0].load()
    except Exception as exc:
        raise SmokeError(
            f"meerk40t.extension entry point {EXTENSION_EP} fails to load: {exc}"
        ) from exc


# --------------------------------------------------------------------------- #
# 3. Console script invocation                                                 #
# --------------------------------------------------------------------------- #
def _cli_argv(args: list[str]) -> list[str]:
    """Resolve how to invoke the CLI.

    Prefer the console script installed alongside the running interpreter
    (the wheel just ``pip install``-ed in the clean environment) -- the real
    shipped entry point. Fall back to ``python -m`` (also used by the existing
    e2e tests) when that script is absent, so the check still exercises the
    installed package's ``cli`` object.
    """
    script = Path(sys.executable).parent / CONSOLE_SCRIPT
    if script.is_file():
        return [str(script), *args]
    return [sys.executable, "-m", "cli_anything.meerk40t.meerk40t_cli", *args]


def run_cli_help() -> None:
    """``cli-anything-meerk40t --help`` must exit 0."""
    proc = subprocess.run(
        _cli_argv(["--help"]),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SmokeError(
            f"`{CONSOLE_SCRIPT} --help` exited {proc.returncode}: {proc.stderr}"
        )


def run_cli_json_command(args: list[str]) -> dict:
    """Run a read-only ``--json`` command and return parsed output.

    Default command is ``machine list`` (offline, reads bundled profiles).
    """
    if not args:
        args = ["machine", "list"]
    proc = subprocess.run(
        _cli_argv(["--json", *args]),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SmokeError(
            f"`{CONSOLE_SCRIPT} --json {' '.join(args)}` exited "
            f"{proc.returncode}: {proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SmokeError(
            f"`{CONSOLE_SCRIPT} --json {' '.join(args)}` produced invalid JSON: "
            f"{proc.stdout!r}"
        ) from exc


# --------------------------------------------------------------------------- #
# 4. Real backend (meerk40t.extension plugin path)                            #
# --------------------------------------------------------------------------- #
def run_backend_smoke(backend_factory=None) -> None:
    """Boot a real MeerK40t kernel, load a generated SVG, and re-save it.

    Args:
        backend_factory: injectable backend constructor (for tests). Defaults
            to the bundled ``Meerk40tBackend`` (lazy import).
    """
    if backend_factory is None:
        from cli_anything.meerk40t.utils.meerk40t_backend import Meerk40tBackend

        backend_factory = Meerk40tBackend

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        src = td_path / "in.svg"
        src.write_text(MINIMAL_SVG, encoding="utf-8")
        dst = td_path / "out.svg"

        backend = None
        primary: Exception | None = None
        try:
            try:
                backend = backend_factory()
                backend.start()
                backend.load_file(str(src))
                if backend.elem_count() < 1:
                    raise SmokeError("backend loaded zero elements from generated SVG")
                backend.save_svg(str(dst))
            except SmokeError as exc:
                primary = exc
                raise
            except Exception as exc:
                primary = exc
                raise SmokeError(f"backend smoke failed: {exc}") from exc
        finally:
            if backend is not None:
                try:
                    backend.shutdown()
                except Exception as sdex:
                    # Best-effort cleanup: never mask the primary SmokeError,
                    # but surface a clean SmokeError when there was no primary.
                    if primary is None:
                        raise SmokeError(f"backend shutdown failed: {sdex}") from sdex

        if not dst.exists() or dst.stat().st_size == 0:
            raise SmokeError("backend save_svg produced no output file")


# --------------------------------------------------------------------------- #
# Orchestration                                                               #
# --------------------------------------------------------------------------- #
def run_all() -> None:
    check_packaged_resources()
    check_entry_points()
    run_cli_help()
    run_cli_json_command(["machine", "list"])
    run_backend_smoke()


def main(argv: list[str] | None = None) -> int:
    try:
        run_all()
    except SmokeError as exc:
        print(f"SMOKE FAIL: {exc}", file=sys.stderr)
        return 1
    print("installed-wheel smoke: OK (resources, entry points, --help, --json, backend)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
