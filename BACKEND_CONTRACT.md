# Backend Contract — cli-anything-meerk40t

The shared backend module is **already implemented and verified** at:
`agent-harness/cli_anything/meerk40t/utils/meerk40t_backend.py`

Import it as:
```python
from cli_anything.meerk40t.utils.meerk40t_backend import Meerk40tBackend
```

## Interface (verified working 2026-07-04)

```python
b = Meerk40tBackend(profile="MeerK40t_CLI", ignore_settings=True)
b.start()                     # boot headless kernel (idempotent)
b.shutdown()                  # tear down
# context manager: with Meerk40tBackend() as b: ...

b.run("circle 1in 1in 1in")   # -> list[str] of captured console lines (includes echo line)
b.run("rect 1mm 1mm 2mm 2mm | save /tmp/out.svg")  # | separates commands
b.reset_capture()             # clear captured buffer
b.captured                    # list[str] property

b.save_svg("/tmp/out.svg")    # -> bool (real SVGWriter). version="default"|"plain"|"compressed"
b.save_svg("/tmp/out.svgz", version="compressed")
b.load_file("/tmp/in.svg")    # -> bool (real SVGLoader; also loads DXF)

b.elems()                     # -> list of element nodes (kernel.elements.elems())
b.ops()                       # -> list of operation nodes
b.elem_count()                # int
b.op_count()                  # int
b.device()                    # active device service or None
b.has_command("circle")       # bool
b.help_text("circle")         # str (full help text)
b.elements                    # the kernel.elements service
b.kernel                      # the raw Kernel instance
```

## Captured output notes

- `b.run(cmd)` returns captured console-channel lines. The FIRST line is usually
  the echo of the command itself (e.g. `"circle 1in 1in 1in"`). Filter it if you
  only want command output: `out = b.run(cmd); real = [l for l in out if l.strip() != cmd.strip()]`
- Lines have timestamp prefix stripped and ANSI codes stripped already.
- Indented lines (starting with spaces) are command output/results.

## Verified backend capabilities

- **SVG save**: `save <path>.svg` (default), `-v plain`, `-v compressed` (svgz)
- **SVG/DXF load**: `load <path>`
- **Elements**: circle, rect, ellipse, line, polyline, path, text, image, frame, grid
- **Operations**: cut, engrave, raster, image, dots; classify/declassify
- **Device**: home, physical_home, move, move_absolute, devinfo
- **PNG/raster**: GUI-DEPENDENT (needs wxPython). Error clearly if requested headless.
- **Console passthrough**: any kernel console command works via `b.run()`

## Headless export formats

- **SVG** (default, plain, compressed/svgz) — truthful, real backend
- **PNG** — requires wxPython GUI + display; error with install instructions if unavailable