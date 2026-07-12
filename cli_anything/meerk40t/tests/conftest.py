"""Ensure the real top-level ``meerk40t`` package wins over the harness shadow.

This test package lives at ``cli_anything/meerk40t``. Under pytest's import
machinery ``cli_anything`` can land on ``sys.path`` ahead of the real MeerK40t
install, so a bare ``import meerk40t`` resolves to this shadow package (which
has no ``kernel`` submodule). Importing the real package here - before any test
module is imported - caches it in ``sys.modules`` so every later ``import
meerk40t`` resolves to the installed kernel regardless of path ordering.
"""

import sys

# Drop any shadow already cached, then import the real package fresh.
for _name in [
    m for m in list(sys.modules) if m == "meerk40t" or m.startswith("meerk40t.")
]:
    _mod = sys.modules[_name]
    if "cli_anything" in (getattr(_mod, "__file__", "") or ""):
        del sys.modules[_name]

import meerk40t.kernel  # noqa: E402,F401  (cache the real package)

# Fail loudly if the shadow still won, rather than letting tests fail obscurely
# deep inside a backend boot.
assert "cli_anything" not in (meerk40t.__file__ or ""), (
    f"shadow meerk40t won: {meerk40t.__file__}"
)
assert "cli_anything" not in (meerk40t.kernel.__file__ or ""), (
    f"shadow meerk40t.kernel won: {meerk40t.kernel.__file__}"
)
