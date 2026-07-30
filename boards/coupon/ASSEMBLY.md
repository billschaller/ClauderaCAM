# Board A "coupon" — assembly companion

Pairs with `assembly-map.png` (FRONT = THT insertion side; BACK = copper/
SMD/probe side, drawn copper-up so it matches the board on your bench).
Machine-side counterpart: `TOOLS.md` — the bit pull-list for the four
programs.
Order of operations is the run-sheet's: mill → mask → silk → scrub →
holes/cutout, then **SMD reflow or hand-solder on the BACK, wire jumpers
on the FRONT (soldered on the back), THT last**.

## SMD — back (copper) side

| ref | part | package | source | notes |
|---|---|---|---|---|
| R1 | 1 kΩ | 1206 | EGSCST 1206 book | LED1 series |
| R2 | 10 kΩ | 0603 | EGSCST 0603 book | 555 RA |
| R3 | 33 kΩ | 0805 | EGSCST 0805 book | 555 RB |
| R4 | 6.8 kΩ | 1206 | EGSCST 1206 book | rate-kick, in series with S2 |
| R5 | 1 kΩ | 0603 | EGSCST 0603 book | Q1 base |
| R6 | 330 Ω | 0805 | EGSCST 0805 book | LED2 series |
| R7 | 330 Ω | 1206 | EGSCST 1206 book | LED3 series |
| R8 | 100 kΩ | 0603 | EGSCST 0603 book | LM358 IN+ from CT node |
| R9 | 10 kΩ | 0603 | EGSCST 0603 book | VHALF divider, top |
| R10 | 10 kΩ | 0805 | EGSCST 0805 book | VHALF divider, bottom |
| R11 | 330 Ω | 1206 | EGSCST 1206 book | LED4 series |
| C1 | 10 µF 25 V X5R | 0805 | EGSCST 0805 book | bulk at power entry |
| C2 | 1 µF | 0603 | EGSCST 0603 book | pi filter |
| C3 | 100 nF | 0805 | EGSCST 0805 book | pi filter |
| C4 | 10 µF 25 V X5R | 0805 | EGSCST 0805 book | **555 timing CT** |
| C5 | 10 nF | 1206 | EGSCST 1206 book | 555 pin 5 |
| C6 | 100 nF | 0805 | EGSCST 0805 book | across R10 |
| C7 | 100 nF | 0805 | EGSCST 0805 book | U1 pin 8 decoupling |
| C8 | 100 nF | 0805 | EGSCST 0805 book | U2 VCC decoupling |
| L1 | 10 µH | 0805 | EGSCST 0805 book | pi filter |
| D1 | SS14 Schottky | SMA | EGSCST 0805 / KOKISO | **cathode band matches silk bar** |
| Q1 | S8050 (marking J3Y) | SOT-23 | EGSCST 0805 book | orientation from pad pattern |
| LED1 | red 0805 | 0805 | EGSCST 0805 book | power indicator — **cathode tick in silk** |
| LED4 | green 0805 | 0805 | EGSCST 0805 book | phase eye — **cathode tick in silk** |
| U2 | LM358 | SOP-8 | EGSCST 0805 book | **pin-1 dot to silk dot**; pads widened to 0.8 for hand solder |

Caps and SOT-23s are unmarked or tiny-marked — take one book pocket at a
time and place immediately.

## THT — insert from the FRONT, solder on the back

| ref | part | source | notes |
|---|---|---|---|
| U1 | NE555P DIP-8 **in socket** | BOJACK kit + socket stock | solder the SOCKET; notch to silk notch; chip in after testing rails |
| LED2, LED3 | 5 mm LED | THT bins (bench-confirm) | **flat/short leg = cathode, matches silk tick on the solder side** |
| SW1 | slide SPDT (SS-12D00 class) | THT bins (bench-confirm) | custom 2.54 footprint |
| S2 | 6×6 tactile | THT bins (bench-confirm) | rate-kick button |
| PAD1 | +5 V wire pad | wire stock | 1.5 mm hole, "+5V" silk |
| PAD2 | GND wire pad | wire stock | "GND" silk |

## Wire jumpers — bare wire on the FRONT, soldered both ends on the back

Spans are pad-center to pad-center; bend on a jig, clip flush.

| ref | span | carries | notes |
|---|---|---|---|
| JP1 | 10.2 mm | N_DISCH over the TRIG wall to S2 | |
| JP2 | 12.7 mm | VCC stitch (D1/C1 belt) | |
| JP3 | 15.2 mm | VCC stitch (LED/Q1 belt) | wire passes ~0.3 mm from socket edge |
| JP4 | 15.2 mm | GND into the U1 moat pocket | |
| JP5 | 12.7 mm | GND into the C8 pocket | |
| JP6 | 12.7 mm | GND, both sides of its run | **crosses JP2 — use insulated wire** |
| JP7 | 12.7 mm | GND into the S2-sealed pocket | has a dogleg — follow the copper path |

## Not populated (coupon artifacts)

TP1–TP6 ladder probe pads (probe resistance/continuity from the back:
TP1↔TP2 = 0.4 mm trace, TP3↔TP4 = 0.5, TP5↔TP6 = 0.6); CP1/CP2/CP3
stencil-test pad pairs (paste only, no part); the scrub ring gauge;
H1–H4 M3 mounting holes; SILK 1.0/1.2/1.5/2.0 legend ladder.

## Power-up

Bench supply 5 V (≥ 4.5 V; 2×AA is not enough) on PAD1/PAD2, SW1 on:
LED1 solid, LED2/3 blink ≈2–3 Hz (hold S2 → ≈7 Hz), LED4 flips
phase-shifted from the blink. Ladder TPs read continuity; an open means
the mill broke that trace width.
