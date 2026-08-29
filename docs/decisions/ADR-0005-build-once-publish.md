# ADR-0005: Build-once publish (setup.py + publish.yml)

- Status: Accepted
- Issue: #30
- Date: 2026-07-14

## Context
The package must build exactly once and publish through trusted publishing
without shipping untested code or extraneous files (dev docs, eval corpora, the
canonical skills source tree).

## Decision
- `setup.py` discovers packages with
  `find_namespace_packages(include=["cli_anything.*"])` and ships only the
  runtime `package_data`: `skills/*.md`, `skills/references/*.md`, `README.md`,
  `profiles/*.json`, `materials/*.json`. It intentionally does **not** package
  the top-level `skills/` canonical tree, `evals/`, or docs.
- `.github/workflows/publish.yml` triggers only on `v*` tags and uses three
  gated jobs: `test-build` -> `clean-wheel` -> `publish`. `test-build` runs the
  source behavioral suite before building, builds the sdist and wheel exactly
  once, generates an exact checksum allowlist, and validates the built wheel in
  a clean venv. `clean-wheel` downloads and smoke-tests that same artifact.
  `publish` re-verifies the same artifact and is the only job granted OIDC
  `id-token: write` for trusted publishing.
- A source-test, artifact-integrity, installed-wheel, or clean-wheel failure
  blocks publication. Later jobs never rebuild the distribution they receive.
- `TestSkillPackaging` (`test_core.py:1434`) asserts the packaged `skills/SKILL.md`
  and every linked `references/*.md` are byte-identical to the canonical
  `skills/cli-anything-meerk40t/` tree, and self-skips when that canonical tree
  is absent (installed wheel) (`test_core.py:1448-1472`).

## Consequences
- The published wheel is deterministic and minimal.
- Shipped skill docs are verified identical to source before publish.
- CI cannot publish when the source suite or the exact built artifact fails its
  verification gates.
- The compatibility workflow introduced by #60 is a separate pre-merge/runtime
  contract and does not change this build-once artifact handoff.
