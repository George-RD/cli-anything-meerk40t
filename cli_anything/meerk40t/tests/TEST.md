# cli-anything-meerk40t Test Plan

This document describes the test strategy for `cli-anything-meerk40t`, a headless Click CLI + REPL harness that wraps the real MeerK40t kernel. The harness lives at `/Users/george/repos/meerk40t/agent-harness/cli_anything/meerk40t` and the tests live in `tests/`.

All tests use the Python standard library `unittest` module (no pytest). The backend under test is the real MeerK40t kernel booted headlessly via `Meerk40tBackend`; tests do not mock the kernel.

---

## 1. Test Inventory Plan

| File | Count | Scope |
|------|-------|-------|
| `tests/test_core.py` | ~15 unit tests | Backend wrapper, project, elements, operations, session, export modules in isolation. |
| `tests/test_full_e2e.py` | ~12 E2E tests | CLI subprocess workflows, backend round-trips, and realistic laser-job scenarios. |

Both test modules create fresh backends in `setUp` and tear them down in `tearDown`. E2E tests that exercise the installed CLI also fall back to `python -m cli_anything.meerk40t.meerk40t_cli` if the console script is not on `PATH`.

---

## 2. Unit Test Plan (`test_core.py`)

### `TestBackend` — Meerk40tBackend wrapper
- `test_start_shutdown` — backend can be started and shut down cleanly.
- `test_run_captures_output` — `b.run("...")` returns a non-empty list of captured console-channel lines.
- `test_save_svg_creates_valid_svg` — `b.save_svg(...)` writes a file, and `xml.etree.ElementTree` parses it with a root tag ending in `svg`.
- `test_load_file` — after saving an SVG, a fresh backend can `load_file(...)` it and restore elements.
- `test_elems_after_add` — adding a circle increases `b.elem_count()`.
- `test_ops_exist` — classifying elements produces at least one operation node.
- `test_help_text` — `b.help_text("circle")` returns a non-empty string containing usage information.

### `TestProject` — project management
- `test_create_project` — `project.create_project(...)` returns a dict with `name`, `elements`, and `operations`.
- `test_open_nonexistent_creates_empty` — opening a path that does not exist clears the elements tree and returns zero elements.
- `test_save_project` — `project.save_project(...)` writes a non-empty SVG file.
- `test_project_info` — `project.project_info(...)` reports element/operation counts and device string.

### `TestElements` — element CRUD
- `test_add_circle` — `elements.add_circle(...)` increases the element count and reports `added: true`.
- `test_add_rect_with_stroke_fill` — adding a rect with `stroke="#ff0000"` and `fill="#0000ff"` produces a node whose `stroke` and `fill` attributes match those values.
- `test_add_ellipse` — `elements.add_ellipse(...)` increases the element count.
- `test_add_line` — `elements.add_line(...)` increases the element count.
- `test_add_text` — `elements.add_text(...)` increases the element count.
- `test_list_elements` — `elements.list_elements(...)` returns a list of dicts describing each node.
- `test_delete_element` — `elements.delete_element(..., 0)` removes an element and returns `deleted: true`.
- `test_clear_elements` — `elements.clear_elements(...)` leaves the element count at zero.

### `TestOperations` — operation management
- `test_list_operations` — `operations.list_operations(...)` returns a list (possibly empty).
- `test_add_operation` — `operations.add_operation(..., "cut")` increases the operation count.
- `test_classify` — `operations.classify_elements(...)` classifies elements into operations and returns `classified: true`.

### `TestSession` — session persistence
- `test_session_save_load` — a `Session` can be saved to JSON and reloaded, preserving command history and status fields.
- `test_undo_redo` — recording commands then calling `undo()` and `redo()` moves the expected command through the history stacks.

### `TestExport` — export formats
- `test_export_svg` — `export.export_svg(...)` writes a valid, non-empty SVG file.
- `test_export_svgz` — `export.export_svgz(...)` writes a non-empty compressed SVGZ file.
- `test_export_png_raises_without_renderer` — `export.export_png(...)` raises `RuntimeError` in headless mode because `render-op/make_raster` is not registered.

---

## 3. E2E Test Plan (`test_full_e2e.py`)

### `TestCLISubprocess` — subprocess against the installed CLI
All tests use the helper `_resolve_cli("cli-anything-meerk40t")`, which returns either the installed console script or `[sys.executable, "-m", "cli_anything.meerk40t.meerk40t_cli"]`.

- `test_help` — `cli-anything-meerk40t --help` exits 0 and mentions `project`.
- `test_project_new_json` — `cli-anything-meerk40t --json project new` prints parseable JSON with an `elements` key.
- `test_elements_circle_json` — `cli-anything-meerk40t --json elements circle 1in 1in 1in` returns `{"added": true, ...}`.
- `test_elements_rect_stroke_fill` — `cli-anything-meerk40t --json elements rect 2in 2in 1in 1in --stroke red --fill blue` returns `{"added": true, ...}`.
- `test_elements_list` — `cli-anything-meerk40t --json elements list` prints a JSON array.
- `test_export_svg` — `cli-anything-meerk40t --json export svg /tmp/mk_e2e_out.svg` returns a JSON dict with `size_bytes > 0`, the file exists, and the file is valid XML.
- `test_console_passthrough` — `cli-anything-meerk40t --json console "circle 2in 2in 1in"` returns a JSON dict containing an `output` list.
- `test_persistence` — `cli-anything-meerk40t --json -p /tmp/mk_e2e_p.svg elements circle 1in 1in 1in` followed by `cli-anything-meerk40t --json -p /tmp/mk_e2e_p.svg elements list` yields a non-empty list.

### `TestBackendE2E` — real backend round-trips
- `test_gcode_export_with_grbl` — create a backend, add a circle, classify, activate a GRBL device (`service device start -i grbl`), then `export.export_gcode(...)` writes a file containing G-code tokens such as `G90`, `G0`, or `M4`.
- `test_svg_round_trip` — create a backend, add a circle and a rectangle, save an SVG, create a new backend, load the SVG, and assert at least two elements are restored.
- `test_full_workflow` — create a project, add circle/rect/text, classify, export SVG, validate the XML, and assert the file is larger than 1000 bytes. This simulates a realistic laser-job preparation workflow.

---

## 4. Realistic Workflow Scenarios

### Laser job preparation (covered by `test_full_workflow`)
1. Create a fresh project.
2. Add geometry: a circle, a rectangle, and a text label.
3. Classify elements into operations (cut/engrave/raster).
4. Export the prepared job as a real SVG using the Meerk40t SVG writer.
5. Validate that the exported SVG is well-formed and contains the expected geometry.

### G-code generation (covered by `test_gcode_export_with_grbl`)
1. Add geometry to the elements tree.
2. Classify elements into operations so a cut plan can be generated.
3. Activate a GRBL device service.
4. Use the Meerk40t plan/save-job pipeline to produce a `.gcode` file.
5. Inspect the file for standard G-code initialization (`G90`, `G0`, `M4`, etc.).

---

## Running the tests

```bash
/tmp/mk_venv/bin/python -m unittest cli_anything.meerk40t.tests.test_core -v
/tmp/mk_venv/bin/python -m unittest cli_anything.meerk40t.tests.test_full_e2e -v
```



## 5. Test Results

### Unit Tests (test_core.py)

```
$ .venv/bin/python -m unittest discover -s cli_anything/meerk40t/tests -p "test_core.py" -v

Ran 34 tests in 1.015s

OK
```

All 34 unit tests passed:
- TestBackend: 7 tests (start/shutdown, run/capture, save_svg, load_file, elems, ops, help_text)
- TestProject: 4 tests (create, open_nonexistent, save, info)
- TestElements: 7 tests (circle, rect_stroke_fill, ellipse, line, text, list, delete, clear)
- TestOperations: 5 tests (list, add, classify, delete, clear)
- TestSession: 2 tests (save_load, undo_redo)
- TestExport: 3 tests (svg, svgz, png_raises_without_renderer)
- TestGeometryTransforms: 5 tests (translate, scale, rotate, align, group_ungroup)
- TestREPLDispatch: 1 test (dispatch_repl_commands)

### E2E Tests (test_full_e2e.py)

```
$ .venv/bin/python -m unittest discover -s cli_anything/meerk40t/tests -p "test_full_e2e.py" -v

Ran 14 tests in 7.004s

OK
```

All 14 E2E tests passed:
- TestCLISubprocess: 10 tests (help, project_new_json, elements_circle_json, elements_rect_stroke_fill, elements_list, export_svg, console_passthrough, persistence, elements_transformations_cli, operations_management_cli) — all via the installed `cli-anything-meerk40t` command
- TestBackendE2E: 4 tests (gcode_export_with_grbl, svg_round_trip, full_workflow)

### Summary Statistics

| Suite | Tests | Passed | Failed | Time |
|---|---|---|---|---|
| test_core | 34 | 34 | 0 | 1.02s |
| test_full_e2e | 14 | 14 | 0 | 7.00s |
| **Total** | **48** | **48** | **0** | **8.02s** |

Pass rate: 100%

### Coverage Notes

- Backend wrapper fully tested (start/shutdown/run/capture/save/load/introspect)
- All element types tested (circle, rect, ellipse, line, text) with stroke/fill verification
- Operations tested (list, add, classify)
- Session save/load and undo/redo tested
- SVG export verified as valid XML via ElementTree parsing
- SVGZ (compressed) export tested
- PNG export correctly errors in headless mode (no renderer)
- G-code export verified with real GRBL device — output contains real G/M codes
- CLI subprocess tests use `_resolve_cli()` and the installed command
- Persistence across CLI invocations verified via `-p` flag
- Console passthrough verified
- SVG round-trip (save → load) verified with element count check

### Artifacts

Tests print artifact paths for manual inspection:
- SVG files: exported via real MeerK40t SVGWriter
- G-code files: exported via real GRBL `save_job` pipeline