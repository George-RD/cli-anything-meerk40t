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
- `.github/workflows/publish.yml` triggers only on `v*` tags, builds sdist +
  trusted publishing (no API token). There is deliberately **no test job in CI**:
  the test suite is run locally before a `v*` tag is cut; the publish workflow
  only builds and verifies the artifact.
- `TestSkillPackaging` (`test_core.py:1434`) asserts the packaged `skills/SKILL.md`
  and every linked `references/*.md` are byte-identical to the canonical
  `skills/cli-anything-meerk40t/` tree, and self-skips when that canonical tree
  is absent (installed wheel) (`test_core.py:1448-1472`).

## Consequences
- The published wheel is deterministic and minimal.
- Shipped skill docs are verified identical to source before publish.
- CI never silently publishes an untested artifact; the `v*` tag is only cut
  after the suite is green.
