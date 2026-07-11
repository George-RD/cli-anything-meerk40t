---
name: Machine profile
about: Submit a community machine profile for cli-anything-meerk40t
title: "Community machine profile: "
labels: community-profile
assignees: ''
---

Replace the contents of the code block below with your complete machine
profile JSON. A maintainer workflow validates this block and opens a pull
request that adds `profiles/community/<name>.json`.

You can generate the JSON locally:

```
cli-anything-meerk40t device setup --save-profile NAME
cli-anything-meerk40t profile submit NAME
```

The second command prints the profile JSON and a pre-filled issue URL even
without `--yes`, so nothing is sent until you confirm.

```json
{
  "name": "my-machine",
  "device": "grbl",
  "baud": 115200,
  "bedwidth": "410mm",
  "bedheight": "400mm",
  "has_endstops": false,
  "notes": "Describe the machine, how you verified the values, and the firmware state.",
  "provenance": {
    "firmware": "Grbl 1.1f",
    "verified": true
  }
}
```

Required fields:

- `name`: safe identifier, used as the filename (letters, digits, `.`, `_`, `-`)
- `device`: backend driver (e.g. `grbl`)
- `baud`: integer baud rate
- `bedwidth` / `bedheight`: length strings such as `"410mm"`
- `has_endstops`: boolean
- `notes`: free text describing the machine and how the values were verified
- `provenance`: object with at least `firmware` and `verified`

Quality bar: values must come from `device setup --save-profile` (live `$$`
readback), the device state machine, and the firmware banner (not guessed).
