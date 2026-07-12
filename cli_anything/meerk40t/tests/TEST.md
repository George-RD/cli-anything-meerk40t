# cli-anything-meerk40t Test Plan

This document describes the test strategy for `cli-anything-meerk40t`, a headless Click CLI + REPL harness that wraps the real MeerK40t kernel. The harness lives at `/Users/george/repos/meerk40t/agent-harness/cli_anything/meerk40t` and the tests live in `tests/`.

All tests use the Python standard library `unittest` module (no pytest). The backend under test is the real MeerK40t kernel booted headlessly via `Meerk40tBackend`; tests do not mock the kernel.

---

## 1. Test Inventory Plan

| File | Count | Scope |
|------|-------|-------|
| `tests/test_core.py` | 104 unit tests | Backend wrapper, project, elements, operations, session, export, device, serial/GRBL probe parsers, profile overlay, export guard, CLI device/machine command-suite wiring, packaged-skill integrity, materials loader, job-prep provenance, job manifest, and client-frame attachment.
| `tests/test_mk_plugin.py` | 15 unit tests | MeerK40t back-fill bridge plugin: behavioural upstream detection, `set` replacement, handover transforms, patch idempotence, runtime web-server patch. |
| `tests/test_full_e2e.py` | 20 E2E tests | CLI subprocess workflows, backend round-trips, realistic laser-job scenarios, the smart-laser workflow (materials, job-prepare provenance gate, determinism, new-material lifecycle), and client-frame attach round-trips over a live consoleserver.

Total: 119 unit + 20 E2E = 139 tests.

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

### `TestDevice` — device commands
- `test_default_device_is_dummy` — a fresh `Meerk40tBackend()` reports `kernel.device` is a `DummyDevice` (default behaviour unchanged).
- `test_list_devices_returns_active` — `device.list_devices(...)` returns the active device label and provider.
- `test_device_status_has_connection_state` — `device.device_status(...)` reports `connected` and `port` without touching any serial port.
- `test_connect_dummy_returns_error_shape` — `device.connect(...)` on the dummy device returns an error JSON shape (no connectable controller).
- `test_disconnect_dummy_returns_error_shape` — `device.disconnect(...)` on the dummy device returns an error JSON shape.
- `test_disconnect_failed_close_preserves_connected_state` — `device.disconnect(...)` on a fake device whose `controller.close()` raises still reports the observed live `connected: true` plus an `error` key (a failed close is not misreported as a clean disconnect).
- `test_active_info_reads_is_connected_for_lihuiyu` — `device._active_info(...)` reports `connected: true` for a controller that exposes `is_connected()` (the Lihuiyu hardware path), not only a boolean `connected` attribute.

### `TestDeviceConfig` — driver selection without hardware
- `test_grbl_config_without_opening_serial` — `Meerk40tBackend(device="grbl", port="/dev/fake", baud=115200)` activates a `GRBLDevice` with `serial_port="/dev/fake"` and `controller.connection.connected == False`; no serial port is opened.
- `test_backend_serial_config_setter_failure_raises` — `Meerk40tBackend._apply_serial_config(...)` raises `RuntimeError` naming `serial_port` when the device's setter rejects the value; the silent `except: pass` is gone.
### `TestDeviceProviderAlias` — device provider alias (P2 CR finding 1)
- `test_lihuiyu_alias_resolves_to_lhystudios` — `_device_provider_name("lihuiyu") == "lhystudios"` and every other advertised choice (`moshi`, `ruida`, `newly`, `balor`, `grbl`) maps 1:1 to its registered provider name (verified against the installed meerk40t package).
- `test_backend_lihuiyu_starts_lhystudios_device` — `Meerk40tBackend(device="lihuiyu")` boots a `LihuiyuDevice`, proving `service device start -i lhystudios 0` was issued; booting does not open any serial/USB port, so it is safe without hardware.

### `TestDeviceConnectError` — connect error shape (P2 CR finding 2)
- `test_connect_grbl_fake_port_returns_error` — `device.connect()` on a GRBL device with `port="/dev/fake"` returns `connected: false` **and** an `error` key (`connection failed to open (port=/dev/fake)`); the serial failure is swallowed inside `controller.open()`, so the post-open connected check is what surfaces it.

### `TestCliDevice` — CLI wiring
- `test_cli_grbl_status_wiring` — `cli-anything-meerk40t --json --device grbl --port /dev/fake device status` returns GRBL device JSON with `connected: false` and opens no serial port.
- `test_cli_dummy_connect_error_wiring` — `device connect` on the default dummy driver returns the error shape and exits 0.
- `test_cli_help_lists_device_options` — `--help` lists the `--device`, `--port`, and `--baud` top-level options.

### `TestSkillPackaging` — packaged skill integrity
- `test_packaged_router_matches_canonical` — the packaged `skills/SKILL.md` is byte-identical to the canonical `skills/cli-anything-meerk40t/SKILL.md` (skipped on installed wheels without the repo tree).
- `test_every_linked_reference_is_packaged` — every `references/*.md` linked from the router exists in the package and matches the canonical copy.

### `TestMaterialsLoader` — materials loader
- `test_bundled_kraft_loads` - the bundled kraft material definition loads from the packaged materials file and resolves to a known profile.
- `test_user_override_wins` - a user supplied materials file overrides the bundled entry for the same machine or material key.
- `test_resolve_settings_unknown_machine_raises` - resolving settings for an unknown machine raises an error rather than silently returning defaults.

### `TestJobPrepProvenance` — job-prep provenance gate
- `test_estimated_cut_raises_without_allow` - preparing a job that relies on an estimated cut raises an error when the provenance allowance flag is not set.
- `test_estimated_cut_passes_with_allow` - the same job prepares successfully once the allowance flag is set, so the provenance gate is the only thing blocking it.

### `TestJobManifest` — job manifest integrity
- `test_manifest_written_and_hashes_verify` - a prepared job writes a manifest whose recorded hashes verify against the generated G-code.
- `test_preflight_rejects_tampered_gcode` - the preflight check rejects G-code whose contents no longer match the recorded manifest hashes.

### `TestAttachClientFrame` — client-frame attachment parser
- `test_valid_frame_parses` - a well formed protocol 1 client frame parses into its structured fields.
- `test_prose_only_raises_attacherror` - a message containing only prose with no frame raises AttachError.
- `test_interleaved_noise_then_frame_parses` - a stream of interleaved noise followed by a valid frame still parses the frame correctly.

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
- `test_gcode_export_with_grbl` — create a backend, add a circle, classify, set the operation power to 150 (avoiding the default-power export guard), activate a GRBL device (`service device start -i grbl`), then `export.export_gcode(...)` writes a file containing G-code tokens such as `G90`, `G0`, or `M4`.
- `test_svg_round_trip` — create a backend, add a circle and a rectangle, save an SVG, create a new backend, load the SVG, and assert at least two elements are restored.
- `test_full_workflow` — create a project, add circle/rect/text, classify, export SVG, validate the XML, and assert the file is larger than 1000 bytes. This simulates a realistic laser-job preparation workflow.

### `TestSmartLaserWorkflow` — smart-laser workflow
- `test_materials_list_and_show` - the materials command lists the bundled materials and shows a chosen material's resolved settings.
- `test_job_prepare_gate` - a job that relies on an estimated cut exits 2 without the allowance flag, then exits 0 with `--allow-estimated`; the generated G-code carries sensible S-values and the preflight check passes.
- `test_determinism_swap` - swapping the material changes the resolved cut powers, confirming the prepare step is deterministic per material.
- `test_new_material_lifecycle` - creating a new material with a missing role exits 1, the role is added via the material ladder, a recorded-evidence gate blocks until evidence is supplied, and the job then prepares.

### `TestAttachRoundTrip` — client-frame attach round-trips
- `test_attach_status_live` - attach status reports protocol 1 over a live consoleserver bound to a free ephemeral port.
- `test_attach_stage_live` - attach staging over the live consoleserver inventories three operations.
- `test_attach_status_closed` - attach status returns a no-frame error when the consoleserver port is closed.

---

## 3b. Bridge Plugin Suite (`test_mk_plugin.py`)

15 tests covering the MeerK40t back-fill bridge plugin: behavioural upstream
detection, console `set` replacement (typed values, feedback, `-p` path flag),
console/web server handover transforms, patch idempotence and failure
isolation, the `bridge_status` console command, and the runtime web-server
patch against a real temp module.
Together with `test_core.py` (104) this makes 119 unit tests; `test_full_e2e.py`
adds 20 E2E tests (139 total).

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
.venv/bin/python -m unittest cli_anything.meerk40t.tests.test_core -v
.venv/bin/python -m unittest cli_anything.meerk40t.tests.test_mk_plugin -v
.venv/bin/python -m unittest cli_anything.meerk40t.tests.test_full_e2e -v
```



## 5. Test Results
### Unit Tests (test_core.py)

```
$ .venv/bin/python -m unittest discover -s cli_anything/meerk40t/tests -p "test_core.py" -v

Ran 104 tests

OK
```

All unit tests passed (current inventory above):
- TestBackend: 7 tests (start/shutdown, run/capture, save_svg, load_file, elems, ops, help_text)
- TestProject: 4 tests (create, open_nonexistent, save, info)
- TestElements: 8 tests (circle, rect_stroke_fill, ellipse, line, text, list, delete, clear)
- TestOperations: 5 tests (list, add, classify, delete, clear)
- TestSession: 2 tests (save_load, undo_redo)
- TestExport: 3 tests (svg, svgz, png_raises_without_renderer)
- TestGeometryTransforms: 5 tests (translate, scale, rotate, align, group_ungroup)
- TestREPLDispatch: 1 test (dispatch_repl_commands)
- TestDevice: 7 tests (default_device_is_dummy, list_devices_returns_active, device_status_has_connection_state, connect_dummy_returns_error_shape, disconnect_dummy_returns_error_shape, disconnect_failed_close_preserves_connected_state, active_info_reads_is_connected_for_lihuiyu)
- TestDeviceConfig: 2 tests (grbl_config_without_opening_serial, backend_serial_config_setter_failure_raises)
- TestCliDevice: 3 tests (cli_grbl_status_wiring, cli_dummy_connect_error_wiring, cli_help_lists_device_options)
- TestDeviceProviderAlias: 2 tests (lihuiyu_alias_resolves_to_lhystudios, backend_lihuiyu_starts_lhystudios_device)
- TestDeviceConnectError: 1 test (connect_grbl_fake_port_returns_error)
- TestGrblParsers: 5 tests (parse_settings_canned, parse_startup_blocks_canned, parse_grbl_probe_banner, parse_grbl_probe_ver_and_state, parse_grbl_probe_empty)
- TestJogRefusalWithoutConnection: 3 tests (jog_refused, goto_refused, frame_refused)
- TestFrameCornerMath: 1 test (frame_traces_five_corners)
- TestProfileOverlay: 4 tests (user_overrides_bundled, user_only_profile, unknown_profile_is_none, available_names_includes_both)
- TestSetupProfileWrites: 2 tests (setup_writes_correct_json, setup_writes_via_config_home_env)
- TestExportGuard: 2 tests (export_gcode_refuses_default_power, parse_placement_summary)
- TestCliMachineProfile: 2 tests (cli_unknown_machine_error, cli_machine_list_bundled)

- TestMaterialsLoader: 3 tests (bundled_kraft_loads, user_override_wins, resolve_settings_unknown_machine_raises)
- TestJobPrepProvenance: 2 tests (estimated_cut_raises_without_allow, estimated_cut_passes_with_allow)
- TestJobManifest: 2 tests (manifest_written_and_hashes_verify, preflight_rejects_tampered_gcode)
- TestAttachClientFrame: 3 tests (valid_frame_parses, prose_only_raises_attacherror, interleaved_noise_then_frame_parses)

### E2E Tests (test_full_e2e.py)

```
$ .venv/bin/python -m pytest cli_anything/meerk40t/tests/test_full_e2e.py -v

20 passed in ~16s

OK
```

All 20 E2E tests passed:
- TestCLISubprocess: 10 tests (help, project_new_json, elements_circle_json, elements_rect_stroke_fill, elements_list, export_svg, console_passthrough, persistence, elements_transformations_cli, operations_management_cli) via the installed `cli-anything-meerk40t` command
- TestBackendE2E: 3 tests (gcode_export_with_grbl, svg_round_trip, full_workflow)
- TestSmartLaserWorkflow: 4 tests (materials_list_and_show, job_prepare_gate, determinism_swap, new_material_lifecycle) covering the provenance gate, deterministic material swap, and new-material calibration lifecycle
- TestAttachRoundTrip: 3 tests (attach_status_live, attach_stage_live, attach_status_closed) over a live consoleserver on a free ephemeral port

### Summary Statistics

| Suite | Tests | Passed | Failed | Time |
|---|---|---|---|---|
| test_core | 104 | 104 | 0 | 4.2s |
| test_mk_plugin | 15 | 15 | 0 | 0.12s |
| test_full_e2e | 20 | 20 | 0 | 16.0s |
| **Total** | **139** | **139** | **0** | **~20s** |

Pass rate: 100%

Each test file also passes standalone. A `conftest.py` caches the real
top-level `meerk40t` package before any test module imports, fixing an
order-dependent import-shadow bug (the `cli_anything/meerk40t` harness package
shadowing the installed `meerk40t`) that otherwise broke single-file runs.

### Coverage Notes

- Backend wrapper fully tested (start/shutdown/run/capture/save/load/introspect)
- All element types tested (circle, rect, ellipse, line, text) with stroke/fill verification
- Operations tested (list, add, classify)
- Session save/load and undo/redo tested
- SVG export verified as valid XML via ElementTree parsing
- SVGZ (compressed) export tested
- PNG export correctly errors in headless mode (no renderer)
- G-code export verified with real GRBL device — output contains real G/M codes
- Device connect/disconnect return an error shape on the dummy driver (no serial port touched)
- CLI `--device`/`--port`/`--baud` options wire the GRBL driver without opening a serial port
- GRBL configuration verified as `GRBLDevice` with `controller.connection.connected == False`
- CLI subprocess tests use `_resolve_cli()` and the installed command
- Persistence across CLI invocations verified via `-p` flag
- Console passthrough verified
- SVG round-trip (save → load) verified with element count check

- A new `conftest.py` caches the real top-level `meerk40t` package before the test modules import, fixing an order-dependent import-shadowing bug (the `cli_anything/meerk40t` shadow versus the installed `meerk40t`) so each test file now runs green on its own.

### Artifacts

Tests print artifact paths for manual inspection:
- SVG files: exported via real MeerK40t SVGWriter
- G-code files: exported via real GRBL `save_job` pipeline