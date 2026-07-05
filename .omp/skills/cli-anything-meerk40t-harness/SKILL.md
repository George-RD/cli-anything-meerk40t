---
name: cli-anything-meerk40t-harness
description: "Build/run the cli-anything-meerk40t agent CLI harness that wraps the real MeerK40t kernel headless for laser job preparation — covers backend bootstrap, SVG/G-code export, ops accumulation fix, test execution, and standalone-repo PyPI publishing"
---

# cli-anything-meerk40t Harness

## When to use
Building, testing, extending, or publishing the agent CLI harness for MeerK40t laser software.

## Prerequisites
- Python venv with: pyserial, pyusb, numpy, ezdxf, Pillow, requests, websocket-client, click, prompt_toolkit
- MeerK40t pip-installed editable into the venv (`pip install -e .` from repo root)
- Harness pip-installed editable (`pip install -e .`)

## Backend bootstrap (verified)
The harness wraps the real MeerK40t kernel headless. Bootstrap pattern (from `test/bootstrap.py`):
```python
from meerk40t.kernel import Kernel
kernel = Kernel("MeerK40t", "0.0.0-cli", "MeerK40t_CLI", ansi=False, ignore_settings=True)
# Add plugins: kernelserver, dummydevice, core, imagetools, fills, coolant,
# lihuiyu, moshi, grbl, ruida, newly, balormk, svg_io, dxf, rotary
kernel(partial=True)
kernel._console_channel.watch(callable)  # capture output
kernel.console("service device start dummy 0\n")
```
Do NOT run `channel print console` if you capture via watch() — it pollutes stdout.

## Key console syntax
- Stroke/fill: `rect 1in 1in 1in 1in stroke red fill blue` (inline, NOT -s/-f, NOT pipe)
- Text: `text "the text"` (NO x/y; position via matrix.post_translate after)
- SVG save: `save path.svg` (default), `save path.svgz -v compressed`, `save path.svg -v plain`
- G-code: `service device start -i grbl` (the `-i` activates!), then `plan copy preprocess validate blob save_job path.gcode`

## Critical fixes applied
1. **Ops accumulation**: kernel auto-creates default ops at boot. Clear `op_branch.remove_all_children()` BEFORE loading SVG, or ops multiply across save/load cycles (1KB → 1.6MB in 3 cycles).
2. **DXF is load-only** headless (no saver). SVG is the only truthful headless export.
3. **PNG needs wxPython GUI** — `render-op/make_raster` only registered by gui/plugin.py. Error clearly if requested headless.

## Running tests
```bash
python -m unittest cli_anything.meerk40t.tests.test_core -v      # 27 unit
python -m unittest cli_anything.meerk40t.tests.test_full_e2e -v  # 11 E2E
CLI_ANYTHING_FORCE_INSTALLED=1 python -m unittest cli_anything.meerk40t.tests.test_full_e2e -v
```

## CLI usage
```bash
cli-anything-meerk40t --json -p /tmp/job.svg elements circle 1in 1in 1in
cli-anything-meerk40t --json -p /tmp/job.svg elements list
cli-anything-meerk40t --json export svg /tmp/out.svg
cli-anything-meerk40t console 'service device start -i grbl'
```

## Standalone repo + PyPI publishing

### Naming convention (strict)
`cli-anything-<software>` keyed on the SOFTWARE name, never its function. Must be `cli-anything-meerk40t`, NOT a laser-themed name. The domain goes in registry `category` + `description` fields.

### Repo structure (this repo)
```
cli-anything-meerk40t/
├── setup.py              # MUST include meerk40t>=0.9.0 + headless deps in install_requires
├── README.md             # root-level README
├── LICENSE               # MIT
├── .gitignore
├── .github/workflows/publish.yml  # OIDC trusted publishing
├── .omp/skills/cli-anything-meerk40t-harness/SKILL.md  # this skill (repo-scoped)
├── skills/cli-anything-meerk40t/SKILL.md   # canonical CLI-Anything skill
└── cli_anything/          # NO __init__.py (PEP 420 namespace package)
    └── meerk40t/          # HAS __init__.py
        ├── meerk40t_cli.py, __main__.py, README.md
        ├── core/, utils/, tests/
        └── skills/SKILL.md  # packaged copy
```

### setup.py install_requires (critical — omitting these ships a broken package)
```python
install_requires=[
    "click>=8.0",
    "prompt_toolkit>=3.0",
    "meerk40t>=0.9.0",
    "pyusb>=1.0.0", "pyserial", "numpy",
    "Pillow>=7.0.0", "ezdxf>=0.14.0",
    "requests>=2.25.0", "websocket-client",
],
```

### Release flow (OIDC trusted publishing — no credentials needed)
```bash
git tag v1.0.1
git push --tags
# GitHub Actions builds + publishes to PyPI automatically via pypa/gh-action-pypi-publish
```
PyPI trusted publisher configured at pypi.org/manage/account/publishing/:
- Owner: George-RD, Repo: cli-anything-meerk40t, Workflow: publish.yml, Environment: (blank)

### CLI-Anything registry (HKUDS/CLI-Anything)
- registry.json is a dict with `meta` + `clis` keys; append to `clis` list
- Registry PR: https://github.com/HKUDS/CLI-Anything/pull/374
- Hold PR until PyPI is live (maintainers reject if install_cmd 404s)
- Verify skill_md raw URL returns 200 before submitting

## Current state (as of 2026-07-04)
- Repo: https://github.com/George-RD/cli-anything-meerk40t (live)
- PyPI: https://pypi.org/project/cli-anything-meerk40t/ (v1.0.0 live)
- Registry PR: https://github.com/HKUDS/CLI-Anything/pull/374 (submitted, awaiting merge)
- Tests: 38 (27 unit + 11 E2E), 100% pass
- OIDC trusted publishing: configured and working (v1.0.0 published via GitHub Actions)