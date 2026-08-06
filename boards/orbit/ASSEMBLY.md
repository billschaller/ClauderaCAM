# ORBIT — LED catch game — assembly guide

Goes with the assembly map sheet. Board rev B "orbit", 66 x 56 mm. 2026-08-05.

## What you're building

A palm-size reflex game. Twelve red LEDs form a ring; a dot of light orbits it,
faster and faster, and you try to catch it exactly on the arrow with the CATCH
button. Hits earn a fanfare and more speed; misses end the round with a raspberry
from the buzzer. An ATtiny85 runs the show. Runs on 3xAA batteries or a 5 V supply.

## Know your board

- The FRONT is the side the LED bodies sit on (it has the ring with the 12/3/6/9
  clock ticks and the arrow). The BACK is the side with the small rectangular
  solder lands.
- The holes are NOT plated through (this board was milled, not ordered from a fab).
  That is why some steps below say "solder BOTH sides" — a wire through the board
  is what connects the two faces.
- A few labels did not fit on the board itself: Q1, D1, C2, R14, R15, LED2, LED8,
  and four of the six programming pads are unlabeled. THE MAP IS THE REFERENCE —
  keep it next to you the whole time.

## Parts checklist

Surface mount (all on the BACK):

| qty | part | value / type | size | refs |
|----:|------|--------------|------|------|
| 1 | [ ] chip | ATtiny85, 8 pins | SOIC-8 wide | U1 |
| 1 | [ ] transistor | AO3401 (marked X1) | SOT-23, 3 legs | Q1 |
| 1 | [ ] transistor | MMBT2222A | SOT-23, 3 legs | Q2 |
| 1 | [ ] diode | BAV99 | SOT-23, 3 legs | D1 |
| 4 | [ ] resistor | 560 ohm | 1206 (large) | R1 R4 R7 R10 |
| 4 | [ ] resistor | 560 ohm | 0805 (medium) | R2 R5 R8 R11 |
| 4 | [ ] resistor | 560 ohm | 0603 (small) | R3 R6 R9 R12 |
| 1 | [ ] resistor | 10k | 0603 (small) | R13 |
| 1 | [ ] resistor | 2.2k | 0805 (medium) | R14 |
| 1 | [ ] resistor | 4.7k | 1206 (large) | R15 |
| 1 | [ ] resistor | 4.7k | 0603 (small) | R16 |
| 1 | [ ] capacitor | 10uF | 0805 (medium) | C1 |
| 1 | [ ] capacitor | 100nF | 0805 (medium) | C2 |
| 1 | [ ] capacitor | 1uF | 0603 (small) | C3 |
| 1 | [ ] capacitor | 10nF | 1206 (large) | C4 |

Through-hole (bodies on the FRONT):

| qty | part | notes | refs |
|----:|------|-------|------|
| 12 | [ ] LED, 5 mm red | all identical — any LED in any ring seat | LED1-12 |
| 2 | [ ] tactile button, 6x6, 4 legs | S1 = CATCH, S2 = START | S1 S2 |
| 1 | [ ] slide switch | power on/off | SW1 |
| 1 | [ ] buzzer, round, "+" marked | Cylewet CYT1036 | BZ1 |

Wire:

- [ ] ~30 cm bare solid wire, 22 AWG / 0.6 mm (via stitching — must pass a 1.0 mm hole)
- [ ] 2 power leads (battery pack or supply pigtail)

Tools: soldering iron, solder, flux, tweezers, flush cutters. Solder paste and a
hot plate / hot-air station make step 1 nicer but hand soldering works. Step 5
needs an ISP programmer (a USBasp is fine).

## Step 1 — surface mount, back side up

BEFORE ANY PART GOES DOWN, stitch via V16. One via hides UNDER the chip — on the
map its diamond sits inside U1's outline. Wire through the hole, solder BOTH
faces, clip flush, then file the back-side joint flat: U1 has to sit on top of
it, and once the chip is down that hole is gone for good. If you are stenciling
paste and the filed joint still tilts the stencil at U1's lands, reflow
everything except U1 and hand-solder the chip afterwards.

Everything in the SMD table goes on the back. Paste-and-reflow the lot, or hand
solder in any order. Watch these:

- U1: the dot on the chip is pin 1 — match the dot on the map. Wide-body SOIC-8;
  it should sit comfortably across the lands, not balance on top of them.
- Q1, Q2, D1 are three IDENTICAL-LOOKING 3-leg parts and they are all different.
  Keep each in its labeled tape until the moment you place it. Q1 goes bottom-left
  near the switch; Q2 and D1 go in the cluster on the right, per the map.
- All twelve ring resistors are the same value (560 ohm) in three sizes on
  purpose — this is the soldering-practice part. Size must match the land: large
  1206 seats, medium 0805 seats, small 0603 seats around the ring. Within a size
  they are interchangeable.
- The four odd resistors (10k, 2.2k, 4.7k, 4.7k) look just like the 560s. Read
  the tape label before placing — a 560 where the 10k goes will not be fun to find.
- Capacitors and resistors have no orientation. The diode and transistors only
  fit one way (2 legs one side, 1 leg the other).

## Step 2 — stitch the wire vias (the other 22)

The small filled diamonds on the map are wire vias: a bare wire through the hole,
soldered on BOTH faces, clipped flush on both sides. They carry signals between
the faces — every single one matters, and a missed or half-soldered one means dead
LEDs or a dead board.

Thread a straightened wire through, bend it slightly so it stays put, solder one
face, flip, solder the other, clip both sides flush. V16 is already in from
step 1 — when you are done, all 23 diamonds must be stitched.

Five of these sit tight against a part outline on the FRONT face: V4 tucks under
the buzzer's rim, and V8, V10, V12 and V13 touch the base of the LED next to
them. Keep those five front joints low and flat — a tall blob there and the part
that lands on it in step 3 will rock instead of seating flush.

## Step 3 — through-hole parts, from the front

Bodies seat FLUSH on the front; solder on the BACK only; clip the leads.

- 12 LEDs: the SHORT leg (flat spot on the rim) points INWARD, toward the center
  of the ring. Every single one. This is the classic mistake on a board like this —
  double-check each LED before soldering.
- S1 (CATCH, lower right) and S2 (START, upper right): the rectangular leg pattern
  only fits the correct way round.
- SW1 (power slide, bottom): fits either way round — both work, the ON direction
  just swaps.
- BZ1 (buzzer): the "+"/"-" marking is on the UNDERSIDE of the buzzer — note
  which lead is "+" BEFORE you seat it, because the marking is hidden once it
  sits. The "+" lead goes in the hole marked with the DOUBLE RING on the map
  (board front-up, arrow at 12 o'clock: the lower of its two holes, the one
  toward CATCH). Backwards = silence.

## Step 4 — power wires

The two big pads on the bottom edge: "+" is the RIGHT pad, "-" is the LEFT pad —
the silk next to each pad says which, trust the silk.

- The "-" wire must be soldered on BOTH faces of the board. It is the only ground
  connection between the front and back copper — skip the front joint and half the
  board is dead.
- The "+" wire is a normal joint on the back.
- Power: 3xAA (4.5 V), 4x NiMH, or a 5 V bench supply. A single Li-ion cell works
  but noticeably dimmer. 2xAA is not enough.
- Wired backwards nothing lights and nothing breaks (there is a reverse-polarity
  guard) — just swap the leads.

## Step 5 — program the chip

The six bare pads on the back, bottom-right, are a standard AVR ISP header laid
flat. The square silk tick marks pad 1; the map numbers all six:

    1 MISO   2 VCC   3 SCK   4 MOSI   5 RST   6 GND

Tack-solder six thin wires (or hold pogo pins) from your programmer. The
programmer powers the board through pad 2 — the slide switch position does not
matter while programming.

Fuses first — a factory-fresh chip talks too slowly for the default programmer
speed, hence the -B 8:

    avrdude -c usbasp -p attiny85 -B 8 \
        -U lfuse:w:0xE2:m -U hfuse:w:0xDF:m -U efuse:w:0xFF:m

then flash the game (orbit.hex, in firmware/):

    avrdude -c usbasp -p attiny85 -B 1 -U flash:w:orbit.hex

Full detail (and a fallback build) in firmware/README.md.

## Step 6 — play

Slide the switch on. The ring runs a quick self-test walk and chirps. In attract
mode a dot orbits lazily while the marker position (the arrow, 12 o'clock) blinks.

- START begins a round: the dot starts opposite the arrow and steps every 220 ms.
- Press CATCH exactly when the dot lands on the arrow.
- Hit: five-pulse win beep, and the dot gets faster (down to a 45 ms step —
  good luck).
- One position early or late: single beep, round over.
- Anything else: long buzz, the whole ring flashes, round over.
- Left alone for 30 seconds it goes to sleep; any button wakes it back to attract.

## Before first power — 60-second check

- [ ] all 23 via diamonds stitched, soldered both sides, clipped — including V16
  under the chip (step 1)
- [ ] "-" power pad soldered on BOTH faces
- [ ] every LED flat/short leg pointing at the ring center
- [ ] the three 3-leg parts in the right seats (Q1 by the switch, Q2+D1 by the buzzer)
- [ ] buzzer "+" at the double-ring hole
