"""Release-integrity regressions for issue #34 (Wave 5 publish boundary).

These exercise the *same* code the CI pipeline runs:

  * ``scripts/verify_dist.py``  -- exact-allowlist + digest verification of
    the built wheel/sdist, used by the test/build, clean-wheel, and publish
    jobs.
  * ``scripts/smoke_installed_wheel.py`` -- installed-wheel smoke (packaged
    resources, both entry points, real backend), used by clean-wheel.

Per the issue's execution contract, each required failure class must FAIL for
its intended reason and PASS on valid input. The behavioral tests serve as the
evidence; they are not authored strictly test-first.

Failure-path evidence requirement: for every negative integrity test we
snapshot the bundle's byte map (filenames + per-file SHA-256, plus the manifest
text) *before* the failing ``verify()``, then assert it is byte-for-byte
identical *after* -- proving a rejected publish never mutates the durable
distribution bytes (no repair/rename/rebuild downstream).
"""

import hashlib
import importlib.metadata as _md
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
# Allow running the suite against an installed wheel from a directory outside the
# checkout: point at the repo's scripts/ via RELEASE_SCRIPTS_DIR. Falls back to
# the checkout-relative path when running from the source tree.
SCRIPTS_DIR = Path(os.environ.get("RELEASE_SCRIPTS_DIR", str(REPO_ROOT / "scripts")))
sys.path.insert(0, str(SCRIPTS_DIR))

import verify_dist  # noqa: E402
import smoke_installed_wheel  # noqa: E402

WHEEL = "cli_anything_meerk40t-1.4.0-py3-none-any.whl"
SDIST = "cli_anything_meerk40t-1.4.0.tar.gz"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _write_dist(d: Path) -> None:
    (d / WHEEL).write_bytes(b"wheel-bytes-v1")
    (d / SDIST).write_bytes(b"sdist-bytes-v1")


def _snapshot(d: Path, manifest_name: str = "SHA256SUMS") -> dict:
    """Byte-identity map of every file in the bundle (incl. the manifest)."""
    snap = {}
    for p in sorted(d.iterdir()):
        if p.is_file():
            snap[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return snap


def _fake_entry_points(console_value, extension_value):
    eps = []
    if console_value is not None:
        eps.append(
            _md.EntryPoint(
                name="cli-anything-meerk40t",
                value=console_value,
                group="console_scripts",
            )
        )
    if extension_value is not None:
        eps.append(
            _md.EntryPoint(
                name="cli_anything_bridge",
                value=extension_value,
                group="meerk40t.extension",
            )
        )

    class _Fake:
        def select(self, group):
            return [e for e in eps if e.group == group]

    return lambda: _Fake()


# --------------------------------------------------------------------------- #
# verify_dist: generate / valid                                                #
# --------------------------------------------------------------------------- #
def test_generate_writes_manifest(tmp_path):
    _write_dist(tmp_path)
    manifest = verify_dist.generate(tmp_path)
    assert manifest.name == "SHA256SUMS"
    lines = manifest.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    names = {line.split("  ", 1)[1] for line in lines}
    assert names == {WHEEL, SDIST}


def test_generate_rejects_non_exact_distribution_set(tmp_path):
    _write_dist(tmp_path)
    (tmp_path / "extra.whl").write_bytes(b"another-wheel")  # two wheels
    with pytest.raises(RuntimeError):
        verify_dist.generate(tmp_path)


def test_verify_valid_passes(tmp_path):
    _write_dist(tmp_path)
    verify_dist.generate(tmp_path)
    verified = verify_dist.verify(tmp_path)
    assert set(verified) == {WHEEL, SDIST}


# --------------------------------------------------------------------------- #
# verify_dist: failure classes (with durable-byte evidence)                     #
# --------------------------------------------------------------------------- #
def test_missing_checksum_rejected(tmp_path):
    _write_dist(tmp_path)
    verify_dist.generate(tmp_path)
    # Drop the wheel's line -> the built wheel is present but unchecksummed.
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(
        f"{verify_dist.sha256_of(tmp_path / SDIST)}  {SDIST}\n", encoding="utf-8"
    )
    before = _snapshot(tmp_path)
    with pytest.raises(verify_dist.AllowlistMismatchError) as exc:
        verify_dist.verify(tmp_path)
    assert WHEEL in exc.value.unexpected
    assert _snapshot(tmp_path) == before  # durable bytes unchanged


def test_extra_file_rejected(tmp_path):
    _write_dist(tmp_path)
    verify_dist.generate(tmp_path)
    (tmp_path / "notes.txt").write_bytes(b"stray-bytes")  # add after generate
    before = _snapshot(tmp_path)
    with pytest.raises(verify_dist.AllowlistMismatchError) as exc:
        verify_dist.verify(tmp_path)
    assert "notes.txt" in exc.value.unexpected
    assert _snapshot(tmp_path) == before  # durable bytes unchanged


def test_renamed_file_rejected(tmp_path):
    _write_dist(tmp_path)
    verify_dist.generate(tmp_path)  # manifest records the original WHEEL name
    (tmp_path / WHEEL).rename(tmp_path / "cli_anything_meerk40t-1.4.0-renamed.whl")
    before = _snapshot(tmp_path)
    with pytest.raises(verify_dist.AllowlistMismatchError) as exc:
        verify_dist.verify(tmp_path)
    # A rename surfaces as both the original name missing and the new one unexpected.
    assert WHEEL in exc.value.missing
    assert "cli_anything_meerk40t-1.4.0-renamed.whl" in exc.value.unexpected
    assert _snapshot(tmp_path) == before  # durable bytes unchanged


def test_digest_mismatch_rejected(tmp_path):
    _write_dist(tmp_path)
    verify_dist.generate(tmp_path)  # manifest records the correct digest
    (tmp_path / WHEEL).write_bytes(b"wheel-bytes-TAMPERED")  # change bytes, keep name
    before = _snapshot(tmp_path)
    with pytest.raises(verify_dist.DigestMismatchError) as exc:
        verify_dist.verify(tmp_path)
    assert exc.value.name == WHEEL
    assert _snapshot(tmp_path) == before  # durable bytes unchanged


def test_manifest_missing_rejected(tmp_path):
    _write_dist(tmp_path)  # no SHA256SUMS written
    before = _snapshot(tmp_path)
    with pytest.raises(verify_dist.ManifestMissingError):
        verify_dist.verify(tmp_path)
    assert _snapshot(tmp_path) == before


# --------------------------------------------------------------------------- #
# smoke: packaged resources                                                    #
# --------------------------------------------------------------------------- #
def test_packaged_resource_absent_raises():
    with pytest.raises(smoke_installed_wheel.SmokeError):
        smoke_installed_wheel.check_packaged_resources(
            resources=[("skills", "DOES_NOT_EXIST.md")]
        )


def test_packaged_resources_present_ok():
    # Real source tree ships these resources; this is the valid-input path.
    # Read-only check: it must not mutate the package.
    smoke_installed_wheel.check_packaged_resources()


# --------------------------------------------------------------------------- #
# smoke: entry points                                                          #
# --------------------------------------------------------------------------- #
def test_entry_point_broken_console_raises():
    getter = _fake_entry_points(
        console_value="no_such_module_xyz:cli",  # fails to import
        extension_value="cli_anything.meerk40t.mk_plugin:plugin",
    )
    with pytest.raises(smoke_installed_wheel.SmokeError):
        smoke_installed_wheel.check_entry_points(get_entry_points=getter)


def test_entry_point_broken_extension_raises():
    getter = _fake_entry_points(
        console_value="cli_anything.meerk40t.meerk40t_cli:cli",
        extension_value="no_such_module_xyz:plugin",  # fails to import
    )
    with pytest.raises(smoke_installed_wheel.SmokeError):
        smoke_installed_wheel.check_entry_points(get_entry_points=getter)


def test_entry_points_present_ok():
    getter = _fake_entry_points(
        console_value="cli_anything.meerk40t.meerk40t_cli:cli",
        extension_value="cli_anything.meerk40t.mk_plugin:plugin",
    )
    smoke_installed_wheel.check_entry_points(get_entry_points=getter)


# --------------------------------------------------------------------------- #
# smoke: CLI invocation (valid input, real package)                            #
# --------------------------------------------------------------------------- #
def test_cli_help_ok():
    smoke_installed_wheel.run_cli_help()


def test_cli_json_machine_list_ok():
    out = smoke_installed_wheel.run_cli_json_command(["machine", "list"])
    assert isinstance(out, dict)


# --------------------------------------------------------------------------- #
# smoke: real backend                                                          #
# --------------------------------------------------------------------------- #
class _BrokenBackend:
    def start(self):
        raise RuntimeError("injected backend failure")

    def shutdown(self):
        pass


def test_backend_smoke_failing_raises():
    with pytest.raises(smoke_installed_wheel.SmokeError):
        smoke_installed_wheel.run_backend_smoke(backend_factory=_BrokenBackend)


def test_backend_smoke_real_ok():
    # Valid-input path: boot the real MeerK40t kernel, load + re-save a SVG.
    # Read-only with respect to the source tree; only writes to a temp dir.
    smoke_installed_wheel.run_backend_smoke()
