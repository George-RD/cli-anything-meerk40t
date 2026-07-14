#!/usr/bin/env python3
"""Generate and verify a SHA-256 checksum manifest for built distributions.

This is the single source of truth for build-integrity enforcement in the
publish pipeline. The same code runs in the ``test/build`` job (generate),
the ``clean-wheel`` job (verify), and the ``publish`` job (verify again),
so the exact wheel/sdist accepted by clean-environment verification is the
exact artifact PyPI receives.

Design (per cli-anything-meerk40t issue #34 / Wave 5):
- The manifest (``SHA256SUMS``) IS the exact allowlist: it names, byte-for-byte,
  the wheel and sdist built once.
- Verification enforces an exact *allowlist match* first (the set of actual
  distribution filenames must equal the set named in the manifest), then a
  per-file *digest match*.
- This yields the four required failure classes with distinct, observable
  errors:
    * allowlist mismatch -> ``AllowlistMismatchError`` (missing / unexpected
      filename). Covers "missing checksum" (a built file absent from the
      manifest), "extra file" (a stray file not in the manifest), and
      "renamed file" (manifest name missing + new name unexpected).
    * digest mismatch   -> ``DigestMismatchError`` (filename matches, hash
      does not).
- Stdlib only, so it runs identically whether or not the package is installed.

CLI:
    python scripts/verify_dist.py generate <dist-dir> [--manifest SHA256SUMS]
    python scripts/verify_dist.py verify   <dist-dir> [--manifest SHA256SUMS]
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

# Only real distribution artifacts may appear in the bundle. Anything else
# (stray logs, notes, a second wheel) is rejected by verification.
DIST_SUFFIXES = (".whl", ".tar.gz")
MANIFEST_NAME = "SHA256SUMS"


class AllowlistMismatchError(Exception):
    """The set of distribution filenames on disk does not equal the manifest.

    Attributes:
        missing: manifest names with no matching file on disk.
        unexpected: files on disk not named in the manifest.
    """

    def __init__(self, missing: list[str], unexpected: list[str]):
        self.missing = missing
        self.unexpected = unexpected
        parts: list[str] = []
        if missing:
            parts.append("missing from dist: " + ", ".join(missing))
        if unexpected:
            parts.append("unexpected in dist: " + ", ".join(unexpected))
        super().__init__("allowlist mismatch: " + "; ".join(parts))


class DigestMismatchError(Exception):
    """A distribution filename matches the manifest but its bytes do not."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"digest mismatch for {name}")


class ManifestMissingError(Exception):
    """The checksum manifest itself is absent from the distribution bundle."""


class ManifestNameError(Exception):
    """A manifest entry name is unsafe (path traversal / not a bare filename)."""


def _is_distribution(name: str) -> bool:
    return name.endswith(DIST_SUFFIXES)


def _list_distributions(dist_dir: Path) -> list[Path]:
    return sorted(
        p for p in dist_dir.iterdir() if p.is_file() and _is_distribution(p.name)
    )


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate(dist_dir, manifest_name: str = MANIFEST_NAME) -> Path:
    """Compute SHA-256 for every built distribution and write the manifest.

    The manifest is the authoritative allowlist: it lists exactly the wheel
    and sdist present in ``dist_dir``. Refuses to run unless the distribution
    set is exactly one wheel + one sdist (so a mis-built or polluted dist fails
    closed); any stray non-distribution file is ignored here and then rejected
    as "unexpected" by ``verify``.
    """
    dist_dir = Path(dist_dir)
    files = _list_distributions(dist_dir)
    wheels = [f for f in files if f.name.endswith(".whl")]
    sdists = [f for f in files if f.name.endswith(".tar.gz")]
    if len(wheels) != 1 or len(sdists) != 1:
        raise RuntimeError(
            f"expected exactly one wheel and one sdist in {dist_dir}; "
            f"found {len(wheels)} wheel(s) and {len(sdists)} sdist(s)"
        )
    lines = [f"{sha256_of(f)}  {f.name}\n" for f in files]
    manifest_path = dist_dir / manifest_name
    manifest_path.write_text("".join(lines), encoding="utf-8")
    return manifest_path


def _parse_manifest(manifest_path: Path) -> dict[str, str]:
    expected: dict[str, str] = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        name = name.strip()
        if not name or not digest:
            raise RuntimeError(f"malformed manifest line in {manifest_path}: {line!r}")
        # N5: reject any path component / traversal so a tampered manifest
        # cannot make us read or write outside dist_dir. Backslashes are not
        # path separators on POSIX, so Path(name).name alone would miss them.
        if (
            "/" in name
            or "\\" in name
            or ".." in name
            or name != Path(name).name
        ):
            raise ManifestNameError(
                f"unsafe manifest name (path components not allowed): {name!r}"
            )
        expected[name] = digest
    return expected


def verify(dist_dir, manifest_name: str = MANIFEST_NAME) -> dict[str, str]:
    """Strictly verify ``dist_dir`` against its checksum manifest.

    Enforces (in order):
      1. the manifest exists,
      2. every manifest name has a matching file and every file in the bundle
         (except the manifest itself) is named in the manifest (exact
         allowlist -- this is what rejects missing / renamed / extra files),
      3. each file's SHA-256 equals the manifest digest.

    Returns the verified ``{name: digest}`` map on success.
    Raises ``ManifestMissingError``, ``AllowlistMismatchError``, or
    ``DigestMismatchError`` on the corresponding failure class.
    """
    dist_dir = Path(dist_dir)
    manifest_path = dist_dir / manifest_name
    if not manifest_path.exists():
        raise ManifestMissingError(
            f"{manifest_name} not found in {dist_dir}; nothing to verify against"
        )

    expected = _parse_manifest(manifest_path)
    # M1 (part 1): an empty manifest would vacuously satisfy the allowlist +
    # digest checks below (nothing to match against), so reject it up front.
    if not expected:
        raise RuntimeError(
            f"manifest {manifest_name} is empty; expected at least one distribution"
        )

    # Every entry in the bundle (except the manifest) must be a regular file.
    # M2: reject symlinks (to files or dirs) and subdirectories so the "exact
    # bundle" is fully enforced rather than silently ignoring non-files.
    actual_names: set[str] = set()
    for p in dist_dir.iterdir():
        if p.name == manifest_name:
            continue
        if p.is_symlink():
            raise AllowlistMismatchError(missing=[], unexpected=[p.name])
        if not p.is_file():
            raise AllowlistMismatchError(missing=[], unexpected=[p.name])
        actual_names.add(p.name)

    expected_names = set(expected)
    missing = sorted(expected_names - actual_names)
    unexpected = sorted(actual_names - expected_names)
    if missing or unexpected:
        raise AllowlistMismatchError(missing=missing, unexpected=unexpected)

    # M1 (part 2): the manifest must describe exactly one wheel and one sdist.
    # This is a belt-and-braces invariant on top of the exact allowlist above
    # (which would otherwise accept a self-consistent wrong-composition bundle),
    # mirroring the count check in generate().
    wheels = [n for n in expected if n.endswith(".whl")]
    sdists = [n for n in expected if n.endswith(".tar.gz")]
    if len(wheels) != 1 or len(sdists) != 1:
        raise RuntimeError(
            f"expected exactly one wheel and one sdist in manifest; "
            f"found {len(wheels)} wheel(s) and {len(sdists)} sdist(s)"
        )

    for name in expected_names:
        if sha256_of(dist_dir / name) != expected[name]:
            raise DigestMismatchError(name)
    return expected


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for verb in ("generate", "verify"):
        p = sub.add_parser(verb, help=f"{verb} the checksum manifest")
        p.add_argument("dist_dir", help="directory containing built distributions")
        p.add_argument(
            "--manifest",
            default=MANIFEST_NAME,
            help=f"manifest filename (default: {MANIFEST_NAME})",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "generate":
            path = generate(args.dist_dir, args.manifest)
            print(f"wrote {path}")
        else:
            verify(args.dist_dir, args.manifest)
            print(f"verified {args.dist_dir}/{args.manifest}: allowlist + digests OK")
    except (AllowlistMismatchError, DigestMismatchError, ManifestMissingError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
