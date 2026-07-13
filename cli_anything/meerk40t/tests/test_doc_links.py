"""Red/green evidence for issue #25: documentation link/path integrity.

Scans the canonical documentation set this issue governs for stale references
and verifies that every local markdown link resolves from a fresh checkout.

This test is the issue's red/green mechanism: it FAILS first (for the intended
stale-reference reason) while the docs still carry `agent-harness/` and
`/Users/george/` paths, and PASSES once the docs are reconciled. No production
modules are imported — this is documentation/test code only.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# Docs this issue governs, relative to the repo root. Local links inside these
# must resolve, and none may contain a stale reference. README.md /
# MEERK40T.md are intentionally excluded: they are user-facing, not part of the
# canonical architecture documentation set reconciled by #25.
IN_SCOPE = [
    "DESIGN.md",
    "BACKEND_CONTRACT.md",
    "SHARED_CONTEXT.md",
    "cli_anything/meerk40t/tests/TEST.md",
    "docs/plans/foundational-remediation.md",
]
DECISIONS_DIR = REPO_ROOT / "docs" / "decisions"

# Stale references that must not appear anywhere in the governed docs.
FORBIDDEN = ["agent-harness/", "/Users/george/"]

LINK_RE = re.compile(r"\]\(\s*([^)\s]+)\s*\)")


def _governed_docs():
    for rel in IN_SCOPE:
        p = REPO_ROOT / rel
        if p.exists():
            yield p
    if DECISIONS_DIR.is_dir():
        yield from DECISIONS_DIR.glob("*.md")


class TestDocLinkIntegrity(unittest.TestCase):
    def test_no_stale_references(self):
        offenders = []
        for path in _governed_docs():
            text = path.read_text(encoding="utf-8", errors="replace")
            for bad in FORBIDDEN:
                if bad in text:
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}: contains stale '{bad}'"
                    )
        self.assertEqual(
            offenders,
            [],
            "Stale references found in docs (issue #25):\n" + "\n".join(offenders),
        )

    def test_local_links_resolve(self):
        broken = []
        for doc in _governed_docs():
            text = doc.read_text(encoding="utf-8", errors="replace")
            for m in LINK_RE.finditer(text):
                target = m.group(1).strip()
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                target = target.split("#", 1)[0]  # drop anchor
                if not target:
                    continue
                resolved = (doc.parent / target).resolve()
                if not resolved.exists():
                    broken.append(f"{doc.relative_to(REPO_ROOT)} -> {target}")
        self.assertEqual(
            broken,
            [],
            "Broken local doc links (issue #25):\n" + "\n".join(broken),
        )


if __name__ == "__main__":
    unittest.main()
