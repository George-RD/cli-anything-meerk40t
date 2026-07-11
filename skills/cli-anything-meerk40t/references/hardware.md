# Hardware, safety, and diagnosis

*Read before driving a real laser. The operator must be present for any step that moves or burns.*

## Safety gates

The beam is live once the device is connected. Before any burn:

- The operator wears laser-safety glasses rated OD5 or better for 445nm
  (blue diode). Ordinary glasses do not protect the eyes.
- The material is flat, focused, and clear of flammables.
- A fire-safe area is kept. Water or a fire extinguisher is within reach.
- No one looks into the beam path.

`device frame` and `device check` never fire the beam. Only the console
passthrough (`console 'plan default copy preprocess blob spool'`) drives a
real burn. Run burns at low power first and step up, never down.

## Origin discipline (machines without endstops)

Most low-cost diode lasers have no endstops. There is no `device physical-home`
that is safe to call. Set the origin by hand:

1. Power the machine OFF. Never hand-move the head while powered. Idle steppers
   can still hold the position and fight you.
2. Park the head near the front-left corner by hand.
3. Power the machine ON. The head's position is now (0,0).

Ask the operator to do this. Never call `device physical-home` on these
machines.

## GRBL preflight meaning

`device check` connects and reads the controller settings. Know what they mean:

- `$32` must be 1. This is laser mode. If it is 0 the controller treats the
  tool as a spindle and may not fire per pixel.
- `$N` startup blocks must be empty. A startup block that moves the head on
  connect will lose your origin.
- `$130` and `$131` are the real bed travel in mm. Treat them as the measured
  truth for the bed size, not the profile's guess.
- `$30` is the maximum S value (the power scale). It sets how `power` maps to
  the controller.

`device check` verifies `$32` and empty `$N`. It reports `$130`, `$131`, and
`$30` but does NOT verify them against the job. It does not check power, feed,
or placement, and it does not burn.

## Coordinate conventions

Two coordinate systems meet here:

- Design space: the SVG canvas. Its ruler origin is top-left, like a screen.
- Machine space: physical millimetres. The origin is front-left, and +Y points
  away from the operator.

The export maths flips Y to map design space onto the machine bed. The
placement summary printed by `export gcode` shows the bounding box and the
Y-flip applied, so you can confirm the part sits where you expect.

`device jog`, `device goto`, and `device frame` all take machine mm with the
front-left origin.

## Failure diagnosis

- Offset after a move: if the head returns to a different point than it left,
  the belt is racked or the controller lost steps. Re-home, lower the feed and
  acceleration, and check belt tension.
- Noise on the return move: a whine or skip on return is a loose or resonant
  belt, not a driver fault. Tighten the belt and re-test.
- Cable snag: the drag cable can catch the gantry and pull the head off
  position. Route the cable so it cannot snag. If it does, re-frame before you
  trust the placement.

## Burn sequence (connection-first)

The spooler accepts jobs even when the serial connection never opened: step
counts rise, the machine does nothing, and aborting from that state can hang
the host software. Field-learned order, every burn:

1. Serial port enumerated (`device detect`). A machine power-cycle on CH340
   adapters may need the USB cable re-seated before the port reappears.
2. `device connect`, then confirm the controller reports Idle with no alarm
   and a sane position (`device status`).
3. Stage the job and set power/speed on every operation.
4. `device frame` at the burn location; operator confirms placement.
5. Operator starts the burn and stays until it ends.

## When to re-frame

Re-run `device frame` (laser off) to confirm placement:

- After any physical move of the head.
- After a cable snag or a loss-of-position event.
- After loading a new piece of material.
- As the final check at the burn location, before the operator starts the job.
