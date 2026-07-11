# GUI operation (console server)

*Requires the bridge plugin. Field-verified on stock MeerK40t 0.9.9100 with
this package installed; see gate below. For headless work use the CLI - it is
the default path and does not need the GUI at all.*

Driving a running MeerK40t GUI over its telnet console server lets the
operator watch on canvas while an agent stages the job. Different failure
modes from the CLI; every gate here exists because it caught a real fault.

## Bridge plugin gate

Stock MeerK40t has three defects that make remote GUI driving unsafe:
element-mutating console commands segfault wx off the main thread, bare
`set` writes silently miss the device, and web/GUI handover binds stale.
This package back-fills the fixes (upstream meerk40t#3249) at runtime via a
`meerk40t.extension` entry point - MeerK40t auto-loads it when both are in
the same environment. Nothing works reliably without it.

Verify before any remote command:

```
# in the MeerK40t console (GUI window or telnet)
bridge_status
```

Expect every patch reported `applied` (or `skipped-already-fixed` on a
future upstream release). If the command is unknown, the plugin did not
load: MeerK40t is running from a different environment than this package.
Only the three #3249 behaviours are patched; anything else is stock.

## Boot and connect

1. Launch with `consoleserver -p 2323` ONLY. Never autoload a job file at
   boot - jobs are staged after the connection gate passes.
2. Device config needs an explicit context path: `set -p grbl serial_port
   ...` / `baud_rate` / `bedwidth` / `bedheight`. Bare `set` targets root
   and does nothing to the device. `flush` after config. Bed-size changes
   need a GUI restart to rebuild the coordinate view.
3. Telnet replies can lag or backlog (a reply may belong to the previous
   command, or nothing arrives until the next kick). Use one persistent
   socket with a drain-before-send helper; verify file-producing commands
   via the filesystem, never by echo.

## CONNECTION-FIRST gate (mandatory order)

The spooler happily "runs" a job (steps count up) into a serial connection
that never opened - the machine does nothing - and aborting from that state
has coincided with a full GUI freeze. Do not stage or queue anything before
step 3 passes:

1. Serial device enumerated (`/dev/cu.usbserial-*`; a power-cycled CH340
   may need a USB replug).
2. Connection explicitly opened from the console (`gcode $I` opens on
   demand; queue echo is not proof - the controller must answer).
3. Controller answers `Idle`, no alarm, sane position.
4. Stage the job; verify the spooler queue is empty/expected.
5. Operator Arm -> Start. Arm is a real gate (`laserpane_arm` default-on;
   Start stays disabled until armed).

If the GUI stops answering: suspect a frozen main thread. Do NOT keep
queueing commands - inspect, then kill and restart.

## Staging an operator-started job via console

Agent stages, human presses Start in the Laser panel:

- `operation* delete` + `element* delete` for a clean tree.
- `rect <x>mm <y>mm <w>mm <h>mm stroke <colour> engrave -s <mm/s> -p <0-1000>`
  chains element creation into a configured op. Scene y = bedheight -
  machine_y_top (canvas origin is top-left design space; machine dot at
  scene bottom-left is machine (0,0), physical front-left).
- TRAP: stroke colours auto-classify into default ops at full power - a red
  stroke spawns `Cut 1mm/s @1000`. Always `operation* list` afterwards and
  delete strays with `operationN,M,K delete` (comma form, no space).
- Verify before handover: `plan clear`, then `plan copy preprocess validate
  blob save_job /tmp/preview.gcode` from the SAME GUI kernel; regex-check
  X/Y ranges, S values (travel S0), and F against intent.

## Motion visibility and placement

- Driver commands (`move_absolute 0mm 400mm`) and spooled jobs update the
  canvas position dot. Raw `gcode G0 ...` moves the machine INVISIBLY -
  reserve raw gcode for `$` housekeeping.
- Outline/trace spools a real motion job and its commanded optical power is
  UNVERIFIED - never present it as a safe beam-off placement check. Use the
  CLI's `device frame` or the staged-preview regex check instead.
- An empty spooler only means software cannot send a NEW job; the powered
  controller's own state stays unknown until it reports Idle/no-alarm after
  reconnect.
- `set show_tips False` + `flush` kills the startup tips popup.
