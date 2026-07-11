# Community machine profiles

This directory holds machine profiles contributed by operators through the
`profile submit` command or the `machine-profile` issue template. Each file
is a single JSON document named `<name>.json` (the `name` field matches the
filename).

A community profile is **not** a code change. It is data captured from a real
device, and it must meet the quality bar below before it is accepted.

## Quality bar

Every value in a community profile must be **measured, not guessed**. The
source of truth is the live machine, captured through the harness:
- **`bedwidth` / `bedheight`**: read from `$$` (`$130` / `$131`) via
  `device setup --save-profile NAME` (the live readback relies on the
  upstream MeerK40t fix meerk40t/meerk40t#3249). These come from the live
  GRBL readback, not the manufacturer's marketing sheet.
- **`baud`**: the actual baud rate negotiated with the controller.
- **`device`**: the backend driver the machine uses (e.g. `grbl`).
- **`has_endstops`**: the real homing capability of the hardware, confirmed
  by the device state machine (a machine with no endstops cannot be
  physically homed; see the hardware workflow in `MEERK40T.md`).
- **`provenance.firmware`**: the firmware string reported by `$I`
  (e.g. `Grbl 1.1f`), and **`provenance.verified`** set `true` only when the
  values above were read back from a connected device.
- **`notes`**: how the operator verified the values and anything safety
  relevant (focus, origin convention, gotchas).

## Schema

```json
{
  "name": "my-machine",
  "device": "grbl",
  "baud": 115200,
  "bedwidth": "410mm",
  "bedheight": "400mm",
  "has_endstops": false,
  "notes": "Verified via device setup --save-profile on a connected unit.",
  "provenance": {
    "firmware": "Grbl 1.1f",
    "verified": true
  }
}
```

All eight keys are required. The `name` must be a safe identifier
(letters, digits, `.`, `_`, `-`); it is used directly as the filename.

## How a profile gets here

1. An operator runs `cli-anything-meerk40t device setup --save-profile NAME`
   against a live machine, then `cli-anything-meerk40t profile submit NAME`.
2. Without `--yes` the command only prints the plan (the JSON and a
   pre-filled issue URL). With `--yes` and an authenticated `gh` CLI it
   forks, branches `profile/<name>`, and opens a pull request.
3. Alternatively, the operator opens a `machine-profile` issue; the
   `profile-to-pr.yml` workflow validates the JSON block and opens the pull
   request automatically. Invalid JSON is reported back as a comment.

No secrets or tokens are handled client-side. The only network action is the
operator's own authenticated `gh` session or the GitHub issue flow.
