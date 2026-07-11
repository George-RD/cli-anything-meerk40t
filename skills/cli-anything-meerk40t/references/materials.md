# Power and speed for materials

*Which values to pick and how to prove them cheaply on a new material.*

## Power and speed recipes

Start every new material at low power. For cardboard, a first burn at 15%
power and 1500 mm/min is a safe way to read the result. Step the power up on
later passes, never down.

- Travel speed is the fast move between cuts (G0). It is not the burn speed.
- Engrave and cut speed is the feed set per operation (G1). This is what burns
  the material.
- Set power and speed on every operation before burning. The auto-created
  operation defaults to 100% power, which will burn through thin stock.

Use `operations set <id> power <n> speed <mm/min>` to tune each operation.

### Field-verified data points

| Machine | Material | Operation | Result |
|---|---|---|---|
| Sculpfun S9 (5.5W diode) | 350gsm kraft card | Vector engrave, 1500 mm/min, 10/15/20% power ladder | All three visible; monotonic intensity increase with power. Relative intensity only: grayscale linearity and repeatability not yet tested. |

A 3-square power ladder (20mm squares, one power step apart) is a cheap first
test on any new material. It proves the power axis works and gives a starting
point in one burn.

