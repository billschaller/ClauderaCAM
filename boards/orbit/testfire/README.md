# Test-fire dose ladder (8 rungs, S0.01 .. S0.06 at F100)

Finds the landed dose for the S0.04-with-thin-coat pair on BOTH
substrates before orbit's silk programs fire (Board A debrief: dose and
coat move together).  One variable: dose.  Feed is fixed at F100.

## Fixture

- Scrap: copper-clad FR-4 offcut, at least 50 x 30 mm, taped flat.
- G54 zero: SW corner of the region to use; Z0 = COPPER TOP.
- Everything stays inside x 0..46, y 0..26.

## Run order

1. `testfire-clear.nc` - T3 (0.8 corn).  Rasters the window at
   x 24..44 / y 1..25 down to bare fiberglass and
   engraves one ID tick per rung in the copper lane (the LONG tick is the
   S0.04 rung).  Vacuum the dust.
2. OPERATOR: squeegee ONE THIN white UV coat over the whole footprint -
   copper, ticks and window alike.  Thin is the experiment.
3. Fit the 455nm laser.  Run the eight ladder programs in any order
   (position encodes dose; each file arms its own dose):
   - `testfire-ladder-s010.nc` -> S0.01 at y 3.00
   - `testfire-ladder-s020.nc` -> S0.02 at y 5.75
   - `testfire-ladder-s030.nc` -> S0.03 at y 8.50
   - `testfire-ladder-s035.nc` -> S0.035 at y 11.25
   - `testfire-ladder-s040.nc` -> S0.04 at y 14.00
   - `testfire-ladder-s045.nc` -> S0.045 at y 16.75
   - `testfire-ladder-s050.nc` -> S0.05 at y 19.50
   - `testfire-ladder-s060.nc` -> S0.06 at y 22.25
   M321 in every file is a no-op once laser mode is on
   (community firmware Laser.cpp:261).  M322 exits when done.
4. OPERATOR: wipe the uncured white off with IPA.
5. Read the ladder against the ticks, bottom rung = S0.01:
   - lowest dose whose line survives the wipe CRISP on each substrate,
   - any dose that scorches or cuts fiberglass in the window
     (S0.06 cut a board through a thick coat on 2026-07-30),
   - difference between the copper half and the fiberglass half - that
     delta is what this rig exists to measure.

A rung that reads well on copper but scorches on fiberglass means the
production pair holds for legend-over-mask-over-copper and the number to
carry into orbit.toml is the copper-side one.
