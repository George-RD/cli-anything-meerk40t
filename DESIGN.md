# Design: cli-anything-meerk40t

## Verified Backend Contract (smoke-tested 2026-07-04)

The harness wraps the **real MeerK40t kernel** as the backend. Verified facts:

1. **Headless bootstrap works**: `Kernel` + core/device/svg plugins + `kernel(partial=True)`
   boots without a display (same path as `test/bootstrap.py` and `meerk40t -z`).
2. **SVG is the truthful headless save format**: `kernel.console('save <path>.svg\n')`
   produces a valid SVG (1947 bytes for 2 elements, valid XML, meerk40t namespace).
3. **SVG load round-trips**: `kernel.console('load <path>.svg\n')` restores elements.
4. **DXF is load-only** headless (no saver registered). **SVGZ (compressed) save is
   supported** headless via `save_svg(..., version="compressed")` / `export_svgz`
   (utils/meerk40t_backend.py:319, core/export.py:90-91) — the SVGWriter registers the
   SVGZ saver under the `compressed` version.
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

The CLI exposes 11 command groups under the root `cli` (`meerk40t_cli.py:365`);
`console` is a command and `repl` is the default mode. `--json` is available on
every command; `--dry-run` suppresses autosave (`meerk40t_cli.py:369`).

- `project` — new, open, save, info, close
- `elements` — circle, rect, ellipse, line, polyline, text, list, select, delete,
  frame, translate, scale, rotate, align, group, ungroup
- `operations` — list, add (cut/engrave/raster/dots), set, classify, declassify, delete, clear
- `device` — list, status, home, move, info, connect, disconnect, check, setup, jog, goto, frame, machines
- `machine` — list (bundled + user machine profiles)
- `profile` — submit (community machine-profile submission)
- `export` — svg, svgz (compressed), png (GUI-dependent), gcode (GRBL, refuses default-power ops)
- `session` — undo, redo, history, status
- `materials` — list, show, record (calibrated laser settings per machine)
- `job` — prepare, preflight, ladder (job prep + verification + calibration)
- `attach` — status, stage (thin client over the consoleserver control channel)
- `console` — raw pass-through command to the kernel console (escape hatch; not a group)
- `repl` — interactive shell, the default mode when no subcommand is given

## Output

- Human-readable tables/messages by default.
- `--json` on every command → JSON dict on stdout.

## Backend wrapper (`utils/meerk40t_backend.py`)

- `Meerk40tBackend` class: boots a headless kernel, captures channel output,
  exposes `run(cmd) -> list[str]`, `save(path)`, `load(path)`, `elems() -> list`,
  `ops() -> list`, `shutdown()`.
- Reuses bootstrap pattern but minimal (core + svg + dummy device + grbl for gcode).

## Decision records

Behavioural guarantees are recorded as ADRs under `docs/decisions/`, each mapped
to the code that enforces it:

- [ADR-0001 command outcome + autosave](docs/decisions/ADR-0001-command-outcome-autosave.md) (issue #26)
- [ADR-0002 transactional mutable state](docs/decisions/ADR-0002-transactional-mutable-state.md) (issue #27)
- [ADR-0003 receiver-verified artifacts](docs/decisions/ADR-0003-receiver-verified-artifacts.md) (issue #28)
- [ADR-0004 acknowledged motion](docs/decisions/ADR-0004-acknowledged-motion.md) (issue #29)
- [ADR-0005 build-once publish](docs/decisions/ADR-0005-build-once-publish.md) (issue #30)
