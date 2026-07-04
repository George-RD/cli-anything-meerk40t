# Shared Context for cli-anything-meerk40t Subagents

## Project: Build a CLI harness for MeerK40t laser software.

You are building `cli-anything-meerk40t`, a Click-based CLI + REPL that wraps the
real MeerK40t kernel as the backend. The harness lives in
`/Users/george/repos/meerk40t/agent-harness/`.

## Verified Backend Facts (smoke-tested)

1. **Headless bootstrap** works without a display. Pattern (from `test/bootstrap.py`):
   ```python
   from meerk40t.kernel import Kernel
   kernel = Kernel("MeerK40t", "0.0.0-testing", "MeerK40t_CLI", ansi=False, ignore_settings=True)
   from meerk40t.network import kernelserver; kernel.add_plugin(kernelserver.plugin)
   from meerk40t.device import dummydevice; kernel.add_plugin(dummydevice.plugin)
   from meerk40t.core import core; kernel.add_plugin(core.plugin)
   from meerk40t.image import imagetools; kernel.add_plugin(imagetools.plugin)
   from meerk40t.fill import fills; kernel.add_plugin(fills.plugin)
   from meerk40t.extra.coolant import plugin as coolantplugin; kernel.add_plugin(coolantplugin)
   from meerk40t.lihuiyu import plugin as lhystudiosdevice; kernel.add_plugin(lhystudiosdevice.plugin)
   from meerk40t.moshi import plugin as moshidevice; kernel.add_plugin(moshidevice.plugin)
   from meerk40t.grbl import plugin as grbldevice; kernel.add_plugin(grbldevice.plugin)
   from meerk40t.ruida import plugin as ruidadevice; kernel.add_plugin(ruidadevice.plugin)
   from meerk40t.newly import plugin as newlydevice; kernel.add_plugin(newlydevice.plugin)
   from meerk40t.balormk import plugin as balormkdevice; kernel.add_plugin(balormkdevice.plugin)
   from meerk40t.core import svg_io; kernel.add_plugin(svg_io.plugin)
   from meerk40t.dxf.plugin import plugin as dxf_io_plugin; kernel.add_plugin(dxf_io_plugin)
   from meerk40t.rotary import rotary; kernel.add_plugin(rotary.plugin)
   kernel(partial=True)
   kernel.console("channel print console\n")
   kernel.console("service device start dummy 0\n")
   ```
2. **Execute commands**: `kernel.console("circle 1in 1in 1in\n")`. Use `\n` to execute.
   `|` separates commands on one line.
3. **Capture output**: `kernel._console_channel.watch(callable)` — callable receives
   each line (string with timestamp prefix like `[15:13:41] text`).
4. **SVG save**: `kernel.console("save /tmp/out.svg\n")` → real SVG file via SVGWriter.
   **SVG load**: `kernel.console("load /tmp/in.svg\n")` → restores elements tree.
5. **Elements API**: `kernel.elements.elems()` → list of element nodes.
   `kernel.elements.ops()` → list of operation nodes. Each node has `.type` (e.g.
   "elem rect", "op cut"), geometry attrs, `.stroke`, `.fill`, `.id`, `.label`.
6. **PNG/raster is GUI-dependent**: `render-op/make_raster` only registered by
   `meerk40t/gui/plugin.py` (needs wxPython). Headless `render` fails with
   "No renderer is registered". Document PNG as requiring wxPython; error clearly.
7. **Native units**: `UNITS_PER_MIL = 1000`, `UNITS_PER_INCH = 39370`. Console
   accepts `mm`, `cm`, `in`, `mil`, `px`, `steps`.

## Directory Structure (already scaffolded)

```
agent-harness/
├── MEERK40T.md          # SOP (written)
├── DESIGN.md            # This design (written)
├── SHARED_CONTEXT.md    # This file
├── setup.py             # PyPI package (to create)
└── cli_anything/        # namespace pkg (NO __init__.py here)
    └── meerk40t/        # HAS __init__.py
        ├── __init__.py
        ├── __main__.py
        ├── README.md
        ├── meerk40t_cli.py      # Main Click CLI + REPL
        ├── core/
        │   ├── __init__.py
        │   ├── project.py       # SVG project open/save/new/info
        │   ├── session.py       # Session JSON, undo/redo, history
        │   ├── elements.py      # Element CRUD commands
        │   ├── operations.py    # Operation commands
        │   ├── device.py        # Device status/home/move
        │   └── export.py        # SVG export via real backend
        ├── utils/
        │   ├── __init__.py
        │   ├── meerk40t_backend.py  # Headless kernel wrapper
        │   └── repl_skin.py         # Copy from scripts/repl_skin.py
        └── tests/
            ├── TEST.md
            ├── test_core.py
            └── test_full_e2e.py
```

## Import Convention

All imports use `cli_anything.meerk40t.*` namespace. The `cli_anything/` dir has
**NO** `__init__.py` (PEP 420 namespace package). Each sub-package has `__init__.py`.

## REPL Skin

Copy `repl_skin.py` from
`/Users/george/repos/meerk40t/.omp/extensions/cli-anything/scripts/repl_skin.py`
to `agent-harness/cli_anything/meerk40t/utils/repl_skin.py`. Use `ReplSkin` for
banner, prompt, help, success/error/warning/info messages.

## Auto-save + --dry-run

Session-based CLI must auto-save after one-shot mutations. `--dry-run` suppresses
the save. Session JSON saved with exclusive file locking
(open `"r+"`, lock, truncate inside lock).

## JSON Output

Every command supports `--json` flag → JSON dict on stdout.

## Skip formatters/linters

Do NOT run formatters, linters, or project-wide tests. The orchestrator runs
verification once at the end.