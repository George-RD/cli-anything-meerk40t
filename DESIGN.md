# Design: cli-anything-meerk40t

## Verified Backend Contract (smoke-tested 2026-07-04)

The harness wraps the **real MeerK40t kernel** as the backend. Verified facts:

1. **Headless bootstrap works**: `Kernel` + core/device/svg plugins + `kernel(partial=True)`
   boots without a display (same path as `test/bootstrap.py` and `meerk40t -z`).
2. **SVG is the truthful headless save format**: `kernel.console('save <path>.svg\n')`
   produces a valid SVG (1947 bytes for 2 elements, valid XML, meerk40t namespace).
3. **SVG load round-trips**: `kernel.console('load <path>.svg\n')` restores elements.
4. **DXF is load-only** headless (no saver registered). SVGZ save also unregistered
   in headless bootstrap.
5. **PNG/raster needs wxPython GUI**: `render-op/make_raster` is only registered by
   `meerk40t/gui/plugin.py` (LaserRender). Headless `render` command prints
   "No renderer is registered to perform render." Document PNG as GUI-dependent.
6. **Channel capture**: console output flows to `kernel._console_channel`. Watch it
   with `kernel._console_channel.watch(callable)` to capture command output.
7. **Command execution**: `kernel.console('<cmd>\n')` executes; `|` separates
   commands; `\n` executes a buffered line.
8. **Elements API**: `kernel.elements.elems()` yields element nodes; `.ops()` yields
   operations; nodes have `.type`, geometry attrs (cx/cy/rx/ry for circles,
   x/y/width/height for rects), stroke/fill colors.
9. **Native units**: `UNITS_PER_MIL` (1000/mil = 39370/inch). Console accepts
   mm/cm/in/mil/px/steps via `Length`.

## State Model

- **Project**: a single SVG file path + the in-kernel elements tree.
- **Session JSON** (`<name>.mksession.json`): `{ "svg_path": str|null, "name": str,
  "modified": bool, "undo_stack": [...], "redo_stack": [...], "device": str|null,
  "history": [{"cmd": str, "ts": float}] }`.
- The kernel owns the live elements tree; the session file tracks metadata + undo.
- Auto-save after one-shot mutations (write SVG + session JSON) unless `--dry-run`.

## Command Groups (Click)

- `project` — new, open, save, info, close
- `elements` — circle, rect, ellipse, line, polyline, text, list, select, delete, frame
- `operations` — list, add (cut/engrave/raster/image/dots), set, classify, declassify
- `device` — list, activate, status, home, move, devinfo
- `export` — svg (real backend), png (GUI-dependent, errors clearly if no renderer)
- `console` — raw pass-through to kernel console (escape hatch)
- `session` — undo, redo, history, status
- `repl` — interactive shell (default when no subcommand)

## Output

- Human-readable tables/messages by default.
- `--json` on every command → JSON dict on stdout.

## Backend wrapper (`utils/meerk40t_backend.py`)

- `Meerk40tBackend` class: boots a headless kernel, captures channel output,
  exposes `run(cmd) -> list[str]`, `save(path)`, `load(path)`, `elems() -> list`,
  `ops() -> list`, `shutdown()`.
- Reuses bootstrap pattern but minimal (core + svg + dummy device + grbl for gcode).