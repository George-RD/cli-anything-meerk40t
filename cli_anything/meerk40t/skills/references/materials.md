# Power and speed for materials

*How to find working values for a new material. Published numbers are
starting points, never guarantees: results depend on the specific machine,
lens, focus, and material batch. Calibrate on scrap first.*

## Fundamentals

- Travel speed is the fast move between cuts (G0). It is not the burn speed.
- Engrave and cut speed is the feed set per operation (G1). This is what
  burns the material.
- Set power and speed on every operation before burning: the auto-created
  operation defaults to 100% power, which will burn through thin stock.
- Tune with `operations set <id> power <n> speed <mm/min>`.

## Calibration procedure: the power ladder

One burn tells you where a new material responds. On scrap of the actual
material:

1. Place 3-5 small squares (20mm) in a row, one power step apart at a fixed
   feed. For an unknown organic material on a ~5W diode, a first ladder of
   10/15/20% at 1500 mm/min keeps every square low-energy; it is a starting
   point, not a safety guarantee - the operator watches the whole burn.
2. Read the result: faintest visible square = marking threshold; pick the
   step that gives the contrast you want. Nothing visible = raise power or
   halve feed and repeat. Charring or flame-licking = stop and back off.
3. Step power up between passes, never down - a too-hot first pass cannot be
   undone.
4. Record what you saw with provenance (machine, material, feed, steps,
   observation, date) so the next job starts from evidence.

A single ladder proves relative response only. Repeatability, grayscale
linearity, and cut-through depth each need their own tests before you rely
on them.

## Recorded calibrations

| Machine | Material | Test | Observation | Limits |
|---|---|---|---|---|
| Sculpfun S9 (5.5W diode) | 350gsm kraft card | 10/15/20% ladder at 1500 mm/min, vector engrave (2026-07) | All three visible, monotonic intensity increase | Single burn; relative intensity only; repeatability and grayscale linearity untested |
