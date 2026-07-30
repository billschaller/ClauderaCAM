# Board B — "orbit" (double-sided pin-and-flip, chase-the-light game)

Double-sided, isolation-milled on the Carvera Air by the ClauderaCAM PCB
lane composed with the pin-and-flip machinery (`[pcb]` + `[twosided]`,
PCB-PLAN.md WS8 + WS3). This spec is the contract: the schematic is built
from it, the design review audits against it, and every BOM line is
traced to a parts-crib file (`parts/*.toml`, Article XI for components).

Board A proved one side of the six-phase chain. Board B proves the FLIP.
Its three jobs:

1. **A reflex game that works** — a 12-LED charlieplexed ring chases,
   ramping, and you catch it at the marker with a button; the buzzer
   scores you. Firmware IS the functional verification.
2. **The via lane** — there is no plating on this machine. Vias are
   unplated holes stitched with wire, soldered on BOTH faces, by hand.
   They are load-bearing (four charlieplex lines have to cross sides) and
   therefore first-class: counted, sized, budgeted, and checked.
3. **The second reflow exercise** — the SOT-23 roster Board A did not
   touch (dual diode, MMBT BJT, AO3401 P-FET) plus twelve identical
   resistors deliberately spread across all three package books: same
   value, same job, three sizes, one stencil, one hotplate. If a size
   fails to wet, the ring tells you which.

## Process rules (override ALL konnect/fab defaults — milled, TWO sides)

Board A's table is inherited. Rows marked **Δ** change for two sides;
rows marked **NEW** exist only because this board flips.

| rule | value | why |
|---|---|---|
| copper **Δ** | **F.Cu + B.Cu**, double-sided 1.5 mm FR-1/FR-4, 35 µm both faces | SMD reflows on B.Cu (back); THT bodies on F.Cu (front), leads solder on the back |
| clearance | ≥ 0.4 mm, both sides | one pass of the 0.2 mm-tip 30° vee at Z−0.15 (kerf ≈0.28) clears a 0.4 gap from both sides |
| track | ≥ 0.5 mm min; 0.6 signal / 0.8 rails | conservative, hand-solderable, no mask needed to survive |
| vias **Δ NEW** | **unplated WIRE vias only**: Ø1.0 hole, Ø2.4 pad BOTH sides, hand-soldered both faces, **budget 6 / ceiling 10** | no plating exists; every via is two hand joints in a board that gets handled — see the via section |
| THT annular **Δ** | ≥ **0.7 mm** (was 0.6) on every hole-centered pad, BOTH sides | must be solderable on both faces AND annularly scrubbable on side 2 (see the scrub delta) |
| copper-to-edge | ≥ 0.4 mm, both sides | edge cut is a separate phase |
| tab-zone copper **NEW** | ≥ 1.0 mm clear of every cutout tab, both sides | tabs are snapped by hand; a tab that bridges copper tears it off the laminate |
| drills **Δ** | **ALL holes helical-bored with the 0.8 corn**; min hole Ø1.0 (tool + 0.2); classes 1.0 / 1.1 / 1.2 / 1.5 / 3.4 | Article XI: the PCB drill set (0.3–1.2) is NOT in `jobs/inventory.toml` — reach unmeasured, so it does not exist. A 0.8 tool cannot bore its own diameter |
| registration holes **NEW** | 2 × Ø2.0, `Twist Drill 2x12 (Spare Tools)`, peck 0.8, **feed F100**, through the blank into the spoilboard | the pins law from the coin jobs; F100 not the coin's F120 — that is a brass number reading 107 % of fr4's chip limit, F100 lands at 89 % (Decision Q12) |
| footprints | hand-solder variants everywhere; SOIC-8 (1.27 pitch) is the finest pitch on the board | stenchill stencils are happiest at 0603+ and large-pitch ICs |
| paste **Δ** | **B.Paste only** — one stencil, back side. Vias, THT pads and ISP pads carry NO aperture | vias are soldered after reflow; a pasted via hole wicks solder and blocks the wire |
| solder mask **Δ** | BOTH sides masked, cured and scrubbed — two squeegee/cure/scrub cycles, one per setup | there is no second way to reach the down-facing side |
| scrub **Δ NEW** | side 1: disc laps, pads deflated −0.10 (Board A's rule). side 2: same for SMD pads, but **annular laps** on every hole-centered pad — tool edge ≥0.15 inside copper AND ≥0.20 outside the hole rim | on side 2 the holes are already drilled; a 0.3 mm spring tip spiralling across a Ø1.0 hole drops in and levers the pad off. Ø2.4 pad + Ø1.0 hole leaves one legal 0.3-wide lap at r≈0.9 |
| silkscreen **Δ** | **F.Silkscreen AND B.Silkscreen**, each lasered onto that side's own cured white mask, in that side's own setup; strokes ≥0.3 mm from that side's solderable pads | the laser only reaches the up-facing side. On this board the front legend is FUNCTIONAL, not decoration: it is the only thing that tells the operator which way 12 LEDs go in |
| levelling **Δ NEW** | auto-level per side; on side 2 the probe grid must not land in a drilled hole | a probe point that drops into a Ø1.0 hole writes a false low into the height map and every side-2 depth after it is fiction |
| pours | GND pour on BOTH sides; ≥0.5 mm fill channels; **1 `filled_polygon` block per side** | Board A's rule, twice. A fragmented pour costs a via, which is the thing this board is trying not to spend |
| clearing | no clearing region narrower than 1.2 mm anywhere | the morphological opening drops features below 0.9; 1.2 keeps real margin over the 0.8 corn (the castellation-chewing incident) |

**Deliberate exceptions** (confined to named rule areas in
`orbit.kicad_dru`; nothing else is exempt):

- **Flip gauges** (4): Ø1.0 hole with a Ø1.7 pad — a 0.35 mm annulus
  DECLARED at 0.3 (0.05 of raster margin, Decision Q13: the earlier Ø1.6
  sat exactly at its own bar and read 0.29 — a gauge that fails its own
  check on a perfect flip gauges nothing), violating the 0.7 rule on
  purpose. They are NOT solderable and NOT in the scrub set; they are
  read with a loupe at the machine, after side-2 iso and BEFORE the mask
  squeegee. A thin annulus is what makes them sensitive.
- Those four pads are floating copper islands by design (no net, 0.4 mm
  clear of the pour on both sides) — DRC "unconnected" is expected there
  and only there.

## Power budget & supply

Wire pads only. The operator owns no connectors, so nothing on this board
is a connector: PAD+ / PAD− are Ø1.5 mm holes with Ø3.6 pads on both
faces, sized for a bench-supply pigtail or a battery pack's leads
soldered straight in (Board A's convention).

- **Nominal 5.0 V, band 4.5–5.5 V.** 3×AA (4.5 V) or 4×NiMH (4.8 V) run
  it; **2×AA (3.0 V) does not** — not because of the MCU (ATtiny85 runs
  to 2.7 V) but because a fixed 560 Ω series resistor at 3.0 V leaves
  ~1.6 mA peak / 0.4 mA average per LED. A single Li-ion (3.7 V) works
  with a visibly dimmer ring (2.9 mA peak).
- **Reverse guard drop**: AO3401 in the zigbee configuration (gate to
  GND, source to the load) ≈ 10 mV at these currents — a Schottky's
  0.35 V would be a real brightness tax, which is why the FET is here
  and no series diode is.
- **Ring**: 5.2 mA peak per LED at 5.0 V, 25 % duty (4-slot row scan) →
  1.3 mA average per lit LED. Worst case 3 LEDs in one slot = 15.5 mA
  out of one pin, plus ≤1.1 mA of held-button current on that line =
  16.6 mA — inside the ATtiny85's 20 mA per-pin limit with margin. That
  margin is why the resistor is 560 Ω and not 470 Ω.
- **MCU**: ≈5 mA at 8 MHz / 5 V (datasheet-class estimate, not measured).
- **Buzzer**: Cylewet CYT1036, 5 V ACTIVE magnetic (internal
  oscillator); ≤30 mA while sounding (listing-class number,
  bench-confirm), duty <5 % → ~1.5 mA average.
- **Totals**: ~10 mA typical during play, ~55 mA peak (chirp + full ring
  slot). Sleep (power-down, pin-change wake, all lines input-pullup):
  MCU <1 µA + FET leakage; SW1 is belt-and-braces on top of that.

Every current above is a MODEL number from part datasheets and the
resistor values, not a measurement. The bench numbers land after the
first play.

## Circuit

### Pin budget — the whole design is this table

ATtiny85 SOIC-8 has 5 usable I/O; RESET stays RESET (ISP lives, and
RSTDISBL is never set — a fused-off RESET on a board with no connector
and no HV programmer is a brick). Nothing is spare:

| pin | net | duty |
|---|---|---|
| 1 PB5 / RESET | RESET | ISP only. R13 10k pull-up, C4 10nF filter |
| 2 PB3 / ADC3 | L3 + S1 | charlieplex line 3; button S1 (CATCH) read in the display blanking window |
| 3 PB4 / OC1B | SND | buzzer gate → Q2 base resistor. BZ1 is ACTIVE (self-oscillating), so PB4 gates beep patterns — no pitch control. Timer1 OC1B on this exact pin is unused headroom, kept in case BZ1 is ever swapped for a passive element |
| 4 GND | GND | |
| 5 PB0 / MOSI | L0 | charlieplex line 0; ISP MOSI |
| 6 PB1 / MISO | L1 | charlieplex line 1; ISP MISO |
| 7 PB2 / SCK / ADC1 | L2 + S2 | charlieplex line 2; button S2 (START) read in blanking; ISP SCK |
| 8 VCC | VCC | C2 100nF at the pin |

**Buttons share the charlieplex lines, deliberately.** Each button sits
between its line and GND through a 4.7 kΩ series resistor. Twelve LEDs
need four lines and the buzzer needs the fifth; there is no sixth pin, so
the buttons are read in the ~10 µs blanking window between display
slots: all four lines to input, the line under test to input-pullup,
read, restore. With the internal pull-up (20–50 kΩ) against 4.7 kΩ the
pressed level is 0.09–0.19·VCC — hard LOW at 5 V and at 3.3 V. While a
button is *held* it costs ≤1.1 mA on slots where its line sources, which
is inside the pin budget above and invisible in the ring.

Both buttons are on **ADC-capable** lines (PB3 = ADC3, PB2 = ADC1) on
purpose: if the digital margin disappoints on the bench, firmware
escalates to an ADC threshold read with **no hardware change**. Nothing
else may ever hang off a charlieplex line — in particular **no debounce
capacitor**: a cap on a matrix line smears every LED slot. Debounce is
firmware.

The same lines carry PCINT, so sleep/wake needs no extra part: idle the
lines as input-pullup, enable PCINT on PB2/PB3, wake on any press.

### The ring — 12 LEDs, 4 lines, explicit matrix

Ring positions are numbered like a clock face, 1 at 12 o'clock (the
marker) running clockwise. Charlieplexing 4 lines gives exactly
4×3 = 12 ordered pairs, so the ring is fully populated with no spare
positions:

| pos | anode (HIGH) | cathode (LOW) | series R | package |
|---|---|---|---|---|
| 1 (marker) | L0 | L1 | R1 560 Ω | 1206 |
| 2 | L1 | L0 | R2 560 Ω | 0805 |
| 3 | L0 | L2 | R3 560 Ω | 0603 |
| 4 | L2 | L0 | R4 560 Ω | 1206 |
| 5 | L0 | L3 | R5 560 Ω | 0805 |
| 6 | L3 | L0 | R6 560 Ω | 0603 |
| 7 | L1 | L2 | R7 560 Ω | 1206 |
| 8 | L2 | L1 | R8 560 Ω | 0805 |
| 9 | L1 | L3 | R9 560 Ω | 0603 |
| 10 | L3 | L1 | R10 560 Ω | 1206 |
| 11 | L2 | L3 | R11 560 Ω | 0805 |
| 12 | L3 | L2 | R12 560 Ω | 0603 |

Read the table as six *sectors* of two adjacent positions, each sector
being one antiparallel pair on one line pair (1,2 = L0/L1; 3,4 = L0/L2;
5,6 = L0/L3; 7,8 = L1/L2; 9,10 = L1/L3; 11,12 = L2/L3). That grouping is
the via-saving move: two adjacent ring positions are fed by ONE two-track
corridor, so the ring needs six corridors, not twelve.

- **One resistor per LED, not per line.** Per-line resistors make
  brightness depend on how many LEDs share the slot; per-LED resistors
  make every position independent, and they buy the package-size
  experiment for free.
- **Row scan, 4 slots.** Drive one line HIGH, up to three lines LOW,
  the rest hi-Z; 4 slots per frame at ~250 Hz frame rate (1 kHz slot
  rate, Timer0 CTC). Any lit LED gets 25 % duty regardless of how many
  are lit — the difference between a visible dot and a 1/12-duty smudge.
- **The mapping is a LAYOUT degree of freedom.** Firmware holds a
  12-entry `{high, low}` table; the layout may permute the ring→pair
  assignment freely to cut crossings, and the firmware table follows.
  The table above is the default the layout starts from, not a
  constraint it must fight.
- **The marker is silk, not a part.** Position 1 carries a lasered arrow
  on F.Silkscreen and firmware blinks it differently. All 12 LEDs are
  therefore the same part, one bin, one resistor value. (If the bins turn
  out to hold two usable colours, swapping position 1 for a red and its
  resistor for 470 Ω is a legal option — noted, not required.)

### Power entry

PAD+ → SW1 (slide SPDT used as on/off) → Q1 AO3401 P-FET reverse guard
(**source to the load, drain to the switched input, gate to GND** — the
zigbee-button configuration; body diode blocks on reversed leads and the
channel shorts it out at ~10 mV in the right direction) → VCC rail.
C1 10 µF bulk at the entry; C2 100 nF at U1 pin 8; PAD− is GND.

There is no series Schottky, no LC filter and no inductor on this board:
the rail feeds a digital toy with a pulsed LED load, where a series L
makes ripple rather than removing it. Board A exercised the LC pi filter
and the SMA Schottky; adding either here would be decoration.

### Buzzer cell (BZ1)

BZ1 is IDENTIFIED (Decision Q2): **Cylewet CYT1036**, a 5 V ACTIVE
magnetic buzzer with an internal oscillator, from the operator's stock
(Amazon B01N7NHSY6, pack of 10 per the listing — count, body Ø (~12)
and pin pitch (~7.6) are calipers rows on the bench inventory sheet,
not assertions here).

PB4 → R14 2.2 kΩ → Q2 MMBT2222A (NPN, SOT-23) base; emitter GND;
collector sinks BZ1 from VCC. D1 BAV99 clamps the collector node to
both rails (its series pair is exactly a rail-to-rail clamp: common pin
to the collector, one diode to VCC, one to GND) — with a magnetic coil
on the collector this clamp is the **mandatory flyback**, no hedging
needed now that the element is known. C3 1 µF sits local to the cell as
the switching reservoir.

ACTIVE means the drive is a GATE, not a tone: firmware patterns beeps
and chirps by switching Q2; the pitch is the buzzer's own. The plan's
BJT cell stands (Decision Q1, MMBT2222A + 2.2 k), and the v2 bridge
idea is dead — a bridge driver does nothing for a self-oscillating
element. MMBT2222A's 600 mA rating covers the ≤30 mA coil with room.

### ISP — bare pads, no connector

Six bare copper pads on B.Cu beside U1, laid out as the standard 2×3 AVR
ISP grid at 2.54 mm pitch (MISO/VCC / SCK/MOSI / RST/GND), pad Ø1.8,
mask scrubbed, no paste aperture, pin 1 marked in silk with a square
tick and all six labelled. Pogo-press or tack-solder a wire; the
operator owns no headers, and this board buys none.

Known and accepted: three ISP lines (MOSI/MISO/SCK) are charlieplex
lines, so the programmer sees up to two LEDs + 560 Ω across driven
pairs — ≈6 mA per lit path, well inside any programmer's drive, and the
ring flickers while flashing. That flicker is a feature: it says the
matrix is alive before any firmware runs. A pressed button during
programming adds 4.7 kΩ to GND on PB2 or PB3 and is equally harmless.
C4 (10 nF on RESET) is the one part on the board specified to be
*removable*: if a programmer stumbles on the RC, lift it.

### What is deliberately absent

No connectors of any kind. No 74HC logic (the MCU *is* the logic, the kit
is one-piece-per-type, and the LED-relevant 74HC595 is box-photo-only and
bench-unconfirmed). No DIP IC and no socket. No SOP-8 analog. No SMD
power-indicator LED — an always-on indicator burns 5 mA in a battery toy
whose whole front face already shows that it is powered.

## BOM — every line traced to a crib file

| ref | value / part | package | source crib |
|---|---|---|---|
| U1 | ATtiny85, SOIC-8 | SOIC-8 | **NOT IN ANY CRIB — bench-catalog first.** operator-stated on hand (2026-07-29); `parts/README.md` lists "SMD ATtiny85 (SOIC-8)" under *not yet cataloged*. Article XI: it needs a crib entry before layout freeze |
| Q1 | AO3401 P-FET (marking X1·) | SOT-23 | kokiso-smd |
| Q2 | MMBT2222A NPN | SOT-23 | kokiso-smd |
| D1 | BAV99 dual (series pair) | SOT-23 | kokiso-smd |
| R1, R4, R7, R10 | 560 Ω | 1206 | egscst-1206 (E24) |
| R2, R5, R8, R11 | 560 Ω | 0805 | egscst-0805 (E24) |
| R3, R6, R9, R12 | 560 Ω | 0603 | egscst-0603 (E24) |
| R13 | 10 kΩ RESET pull-up | 0603 | egscst-0603 |
| R14 | 2.2 kΩ Q2 base | 0805 | egscst-0805 |
| R15 | 4.7 kΩ S1 series | 1206 | egscst-1206 |
| R16 | 4.7 kΩ S2 series | 0603 | egscst-0603 |
| C1 | 10 µF 25 V X5R bulk | 0805 | egscst-0805 (**only** 10 µF source) |
| C2 | 100 nF U1 decoupling | 0805 | egscst-0805 (**only** ≥100 nF source) |
| C3 | 1 µF buzzer-cell reservoir | 0603 | egscst-0603 (its top value) |
| C4 | 10 nF RESET filter | 1206 | egscst-1206 (its top value) |
| LED1–LED12 | 5 mm THT (Decision Q9), one colour, Vf ≈2.0–2.2 V | THT | THT bins — **bench-confirm** colour, Vf, 2.54 lead pitch |
| S1, S2 | 6×6 tactile | THT | THT bins — **bench-confirm** footprint (2-leg vs 4-leg) |
| SW1 | SS-12D00-class slide SPDT | THT 2.54 | THT bins — **bench-confirm** footprint |
| BZ1 | Cylewet CYT1036, 5 V ACTIVE magnetic buzzer (Decision Q2) | THT 2 lead | operator stock (Amazon B01N7NHSY6, ×10 per listing) — **bench-confirm** count, body Ø (~12), pin pitch (~7.6) |
| V1–V6 | via wire, 22 AWG solid (0.64) or a clipped 0.5 mm component lead | — | consumable, **not a crib part** — bench-confirm the gauge passes a Ø1.0 hole |
| PAD+, PAD− | wire pads | Ø1.5 hole, Ø3.6 pad both sides | copper + wire |
| TP1–TP6 | ISP pads | bare Ø1.8, B.Cu | none |
| G1–G4 | flip gauges | Ø1.0 hole, Ø1.7 pad both sides (Decision Q13) | copper artifact, no net |
| H1–H4 | M3 mounting holes | Ø3.4 bore | no hardware in BOM |

**Species audit — what this board exercises that Board A did not.**

| species | part | crib | its honest job |
|---|---|---|---|
| SOT-23 dual diode | BAV99 | kokiso-smd | both diodes used: rail-to-rail flyback clamp on the buzzer node |
| SOT-23 MMBT BJT | MMBT2222A | kokiso-smd | buzzer low-side driver (plan's "SOT-23 + base R") |
| SOT-23 P-FET, high-side | AO3401 | kokiso-smd | reverse-polarity guard on wire-pad power |
| SOIC-8 | ATtiny85 | *uncatalogued* | the brain; finest pitch on the board (1.27) |
| R in 0603 / 0805 / 1206 | 560 Ω ×12 + 4 more | all three books | ring drive — same value, same job, three sizes, one stencil: a controlled reflow comparison |
| C in 0603 / 0805 / 1206 | 1 µF / 100 nF+10 µF / 10 nF | all three books | reservoir, decoupling+bulk, RESET filter |

**Deliberately not exercised, with reasons** (Article XI cuts both ways —
a part with no honest job is decoration, and decoration on a board that
must be hand-soldered on both faces costs real joints):

- **AO3400, 2N7002, SI23xx** — after the reverse guard there is no second
  switching load. The plan specifies the buzzer cell as a BJT with a base
  resistor and the plan wins (Decision Q1); a 5.7 A FET switching a 30 mA
  buzzer would be a species tick, not a circuit. The once-mooted v2
  bridge seat (where an N-FET was genuinely the right half) is dead:
  BZ1 is an ACTIVE element and a bridge does nothing for it.
- **BAV70, BAW56** — BAV99's series pair is the correct topology for a
  two-rail clamp; a common-cathode or common-anode dual would need a
  second part to do the same job.
- **SMA/SOD-123 diodes, SMD inductors, SMD LEDs, SOP-8 ICs, DIP ICs,
  74HC** — no rectification, no filtering, no analog and no logic on a
  DC-fed microcontroller toy. Board A exercised the first four.
- **TL431** (miscatalogued as a BJT in kokiso-smd) — no voltage reference
  need, and no free pin to read one with.

## Wire vias & pin-and-flip registration

### Vias are a process feature, so they are budgeted

There is no plating. A via is a hole, a piece of wire, and two hand
joints — one of them on the reflowed side, made after reflow. Every via
is therefore a permanent liability in a board that gets picked up and
played with, and the design's job is to need as few as possible.

**Count: 6 planned (V1–V6), hard ceiling 10.** The layout review reports
the final count with a one-line justification per via; exceeding 10 is a
redesign of the ring wiring, not a footnote.

Three levers keep the count there:

1. **A THT lead soldered on both faces IS a via.** 24 LED leads, 8 button
   legs, 3 switch legs, 2 buzzer leads and 2 wire pads pass through the
   board with a pad on each side. Any net that needs to change sides at
   one of those points crosses for free. The layout freeze publishes a
   **dual-solder list** naming every lead that is load-bearing as a via;
   those joints are not optional and the assembly card repeats them.
2. **Six sector corridors, not twelve.** The antiparallel grouping in the
   matrix table means each two-track corridor feeds two adjacent ring
   positions, and the ring's interior on the FRONT side is empty copper —
   a free crossing plane reached through LED leads.
3. **Pours both sides.** GND never needs a dedicated via while the wire
   pad, the button legs and the switch legs stitch it; dedicated stitches
   are only added if a pour fragments (the `filled_polygon` count rule).

Expected allocation: **4 charlieplex crossings** in the ring's outer
annulus where the concentric line arcs must swap radial order, and
**2 GND stitches** held in reserve for pour fragmentation.

### Via geometry and rules

| item | value | why |
|---|---|---|
| hole | **Ø1.0** | the 0.8 corn cannot bore its own diameter and no PCB drill is in the crib (Article XI); 1.0 = tool + 0.2 radial. This is a stated deviation from PCB-PLAN's Ø0.8 — if the 0.3–1.2 drill set is ever measured into `inventory.toml`, 0.8 becomes legal and these pads shrink |
| pad | **Ø2.4 both sides** (0.7 annular) | solderable on both faces AND wide enough for one legal annular scrub lap on side 2 |
| clearance | ≥ 0.4 to any other copper, both sides | house rule, no exception |
| mask | opening on both sides; in the scrub set on both sides (annular lap on side 2) | a via under mask cannot be soldered |
| paste | **none** | vias are stitched after reflow; paste in the hole wicks and blocks the wire |
| keep-out | ≥1.5 mm clear of any SMD body, ≥2.0 mm clear of any THT body, ≥3.0 mm from the board edge, never inside another pad | both faces must be reachable by an iron; the front face must stay flat enough for a THT body to sit down |
| wire | 22 AWG solid copper (0.64) or a clipped 0.5 mm component lead; soldered both faces, clipped ≤0.3 proud | flush enough that nothing rocks on it |
| footprint | `WireVia`: F.Cu + B.Cu pads, F.Mask + B.Mask openings, no paste, no silk inside 0.3 | KiCad plated vias cannot model a hand-soldered wire — a THT pad footprint can |

### Pin-and-flip registration

Straight reuse of the shipped pins law (DESIGN.md 2026-07-28 / 07-29,
`[twosided]` + `[pins]`), with the PCB numbers filled in:

- **Blank**: double-sided 1.5 mm FR-1/FR-4, ≥ 70 × 64 mm (board + pin
  margin); the operator's stock is **150 × 100** (Decision Q8), which
  clears the requirement comfortably. One board per blank as designed;
  150 × 100 admits a second orbit per blank as a cut-time option, but a
  second board re-plans the pin geometry — noted, not designed for.
  Full-coverage double-stick tape is **mandatory** — a bowed blank turns
  every depth number into fiction (mask guide §2), and this board
  depends on depth numbers on both faces.
- **Flip axis: Y** (x → −x), the mode the coin cut. The mirror line is
  the board's own vertical centreline, x = 28.0 in board coordinates.
- **Pins**: 2 × Ø2.0 × 12 dowels at **(28.0, −8.0)** and **(28.0, 56.0)**
  — on the mirror line, so flip-symmetric by construction (the pins law
  refuses asymmetry), and 8 mm outside the board outline in the blank's
  waste, so the finished board carries no pin holes. Burr skim
  `spot_depth 0.1`, `peck 0.8`, **feed F100** (Decision Q12 — the coin
  lane's F120 is a brass number at 107 % of fr4's sustained chip limit;
  F100 measures 89 %). Hole depth **12.0** from Z0, DECLARED in the job
  as `seat_extra 0` + `tip_allowance 0` (Decision Q11: the `[pins]`
  defaults 0.2 + 0.6 derive 12.8, but 1.5 blank over 12.7 spoilboard
  allows only 12.2 before the bed and the grammar refuses; 12.0 clears
  by 0.2). The honest consequence of declaring the tip allowance away:
  the drill's real ~0.6 point cone means the pin seats at ~11.4 of full
  diameter and stands **~0.6 mm proud of the blank** (~2.1 above the
  MDF) instead of flush — harmless, because nothing is machined over a
  pin (the cutout path stays ≥7.5 mm away; the pin keep-out check is
  the judge) and the flipped blank engages the pin through its full
  1.5 mm thickness — more engagement than the flush seat gave, not
  less.
- **One WCS for both sides.** Z0 is re-touched per side (each face has
  its own bow); XY is NEVER re-zeroed — re-zeroing throws away the
  registration the holes bought.
- **Drill once, from side A.** All through-holes are bored from the front
  in side A's setup, so both artworks reference the same physical holes
  and flip accuracy = pin-to-hole clearance (~0.02–0.04, under the
  simulation pixel).

**Side A = FRONT (F.Cu, THT side). Side 2 = BACK (B.Cu, SMD side),
cutout last.** That assignment is a decision this spec makes, not a plan
inheritance, and the reasons are physical:

- the drill's exit burr lands on the back, which is then deburred and
  machined afterwards anyway — a burr on the reflow side would be baked
  under paste;
- the back is the LAST face machined and therefore never tape-mounted,
  so the stencil/paste/hotplate side stays free of adhesive residue;
- the front (hand-soldered, robust) is the face that takes the tape.

### The flip gauges — this board measures the number DESIGN.md is missing

DESIGN.md still lists front-to-back art registration as *unmeasured*:
"the number that turns the pin-slop estimate into calibration fact."
Orbit is the board that measures it, two ways:

1. **G1–G4**, one near each board corner: Ø1.0 hole, Ø1.7 pad on both
   sides (0.35 annulus declared at 0.3 — Decision Q13, the named DRU
   exception). The hole is bored in side A's frame; side 2's pad is
   placed in the mirrored frame. Loupe the annulus on each face after
   side-2 iso and before the mask squeegee: an even ~0.35 ring means a
   perfect flip, and thick/thin reads the offset directly. Four corners
   give translation *and* rotation.
2. **The 24 LED holes** are the same measurement at 0.7 annulus — less
   sensitive, but a population instead of four samples.

Both readings are run-sheet steps with a place to write the number down.
The board is opaque, so a see-through vernier is not an option — reading
the same hole's annulus from each side is.

### Checks this board asks WS5 to add

The double-sided checks already planned (side-frame mirror consistency,
via/hole concentricity across the flip, pins-law carry-over, per-program
ZMIN echo) get their first real board here. Orbit adds four:

1. **Both-side annular ring**: every hole in the Excellon schedule has
   ≥0.7 mm annulus in F.Cu *and* B.Cu (0.3 for the named gauge
   exception). A good back pad over a shaved front pad is a hand-solder
   failure that no single-sided check can see.
2. **Annular scrub margin** (side 2 hole-centered pads): tool edge
   ≥0.15 inside copper AND ≥0.20 outside the hole rim — the disc-lap
   check cannot express "don't drive into the hole".
3. **Paste excludes holes**: no B.Paste aperture intersects any hole in
   the schedule.
4. **Tab-zone copper keep-out**: no copper within 1.0 mm of a cutout tab,
   either side.

And one requirement on the WS3 grammar itself: `[pcb]` currently carries
**one** six-phase `[phases]` table. Orbit needs the chain twice — per-side
phase params (each side has its own iso/clear/mask/silk/scrub, its own
Z0 echo and its own dose), the drill + pin block between them, and the
cutout on side 2 only. That composition is the software deliverable this
board exists to exercise.

## Layout notes

- **Board 56.0 × 48.0 mm**, rectangle with 2.0 mm corner radii. Edge.Cuts
  stays a rectangle-with-quarter-arcs: WS2 derives the raster window from
  outline coordinate words and refuses an outline whose ink escapes the
  endpoint extents, so no circular board, however tempting a round game
  is.
- **Origin** at the board's lower-left corner (the WCS both sides share).
- **Ring**: centre (22.0, 26.0), pitch circle Ø26.0 (r = 13.0), positions
  every 30°, position 1 at 12 o'clock = (22.0, 39.0). Adjacent LED
  spacing 6.73 mm — a 5 mm body with 1.7 mm of air. Leads radial on the
  2.54 pitch, at r = 11.73 and 14.27.
- **Ring interior**: FRONT is empty copper (a free crossing plane, and
  the place the game's name gets lasered). BACK carries U1 at the centre,
  the 12 series resistors tangentially at r ≈ 10 (5.2 mm of arc each —
  a 1206 hand-solder footprint fits with ≥0.6 mm between neighbours),
  plus C2, R13, C4.
- **Power parts at the entry, driver at the load** (this is what keeps the
  ring interior habitable): Q1 + C1 on the back beneath the bottom edge
  strip; Q2 + D1 + C3 + R14 on the back beside BZ1.
- **Bottom strip (y 0–8)**: PAD+ (10, 4), PAD− (16, 4), SW1 (26, 4),
  ISP pad grid on the back at ≈(33–38, 3–6).
- **Right strip (x 40–56)**: S1 CATCH (48, 13), BZ1 (48, 26), S2 START
  (48, 39).
- **Mounting**: H1–H4 Ø3.4 at (3.5, 3.5), (52.5, 3.5), (3.5, 44.5),
  (52.5, 44.5), copper keep-out both sides.
- **Flip gauges** G1–G4 just inside the corners, in pour area, 0.4 mm
  clear of the pour on both sides.
- **Pours** GND both sides, ≥0.5 mm fill channels, one fragment per side.
  Watch the ring: 24 holes with 0.4 clearance rings punch a lot of
  Swiss cheese into both pours — plan the escape channels at design
  time, exactly as the single-sided guide teaches, twice.
- **Silk, FRONT** (functional, gets the laser time): a cathode tick beside
  every LED's cathode hole (12), the marker arrow at position 1, ring
  numbers at 12/3/6/9 only, "CATCH" / "START" at the buttons, "ON" at
  SW1, "+" / "−" at the wire pads, "ORBIT v1" + date. Text 1.5 mm,
  stroke 0.25 (Makera's floor), dose S0.03 / F100 pending Board A's silk
  ladder — if 1.2 mm proves readable there, this legend shrinks.
- **Silk, BACK**: U1 pin-1 dot + ref, Q1/Q2/D1 orientation marks + refs,
  the six ISP labels + a pin-1 square tick, "SIDE B". Passive refs and
  all values omitted — dose is not free and the BOM is the authority.
- **Text orientation**: front legend readable with the front up; back
  legend readable with the back up. Each side is lasered in its own
  setup, so neither has to be mirrored by hand.

## Assembly / run-sheet order

1. Tape the blank (full coverage), clamp the waste outside the pin holes
   if clamping, auto-level over the board area, Z0 on front copper.
2. **Side A (FRONT) phases 1–5**: iso (vee) → clear (0.8 corn) →
   mask squeegee + UV cure *[operator]* → silk laser → scrub.
3. **Drills**: all 53 board through-holes with the 0.8 corn (helical),
   then burr-skim + peck the two Ø2.0 pin holes into the spoilboard.
4. Set the pins, flip about Y, re-tape, re-level (Z0 on back copper;
   **confirm no probe point sits in a hole**).
5. **Deburr side B by hand** (scotchbrite) before anything else touches
   it — the drill's exit burr lives there.
6. **Read the flip gauges** after side-B iso, before the mask squeegee.
   Write the numbers on the run sheet.
7. **Side B (BACK) phases 1–5**: iso → clear → mask + cure *[operator]* →
   silk laser → scrub (disc laps on SMD pads, annular laps on every
   hole-centered pad).
8. **Cutout** with tabs (1.0 corn, ≥2 tabs ≥1.0), snap by hand, file.
9. **Off machine**: stenchill stencil from B.Paste → paste → place →
   hotplate reflow the BACK.
10. **Wire vias**: insert, solder both faces, clip flush. After reflow,
    never before — a wire standing off the back holds the board off the
    hotplate.
11. **THT from the FRONT**, leads soldered on the back; every lead on the
    dual-solder list soldered on the front too.
12. **Flash** through the ISP pads: internal 8 MHz RC (CKDIV8 cleared),
    SPIEN enabled, **RSTDISBL never programmed**.
13. **Power up.** The boot self-test walks all 12 ring positions in order,
    then chirps. That walk is an electrical continuity test of every
    matrix pair, every series resistor and every via on the board — if a
    position stays dark, the fault is in one of three named places.

## Firmware (the functional verification, ~150 lines AVR C)

avr-gcc or arduino-cli, flashed via the operator's programmer. Structure:
Timer0 CTC drives the 4-slot row scan at 1 kHz (frame 250 Hz) from a
12-entry `{high, low, position}` table; the blanking window at the end
of each slot reads S1/S2 (digital first — Decision Q3 — with the ADC
threshold read as the no-hardware-change escalation); PB4 gates the
active buzzer's beep patterns off Timer0's tick (Timer1 OC1B stays
free in case BZ1 is ever swapped for a passive element); the game loop
ramps the chase interval, scores the catch against position 1, and
sleeps to power-down with PCINT wake after an idle timeout. Boot
self-test first, always — it is the board's own continuity check.

## What this board verifies (tied to the double-sided phases)

| # | phase / step | what orbit proves |
|---|---|---|
| 1 | iso, **twice** | side A unmirrored, side B mirrored, both frames derived from one Edge.Cuts — the side-frame mirror consistency check gets a real board instead of a synthetic one |
| 2 | clear, twice | two pours, two sets of Swiss-cheese clearance rings, 1.2 mm minimum clearing feature |
| 3 | mask, twice | two squeegee/cure cycles in two setups; whether cured mask + white legend survive being taped face-down (Decision Q4: risk accepted — if the tape lifts the legend, that is an Article II incident) |
| 4 | silk, twice | first board where the legend is load-bearing: 12 cathode ticks decide whether the ring works at all |
| 5 | scrub, twice | disc laps on side A (no holes yet) and the new **annular** laps on side B (holes everywhere) |
| — | drill-once-from-side-A | 53 bores in five diameter classes with one 0.8 corn, plus the hole schedule check |
| — | pins + flip | pins law on a 1.5 mm sheet: symmetry, keep-out, spotface + peck into the spoilboard, flush seat |
| — | **flip gauges** | turns pin-slop from an estimate into a measured number — the open item DESIGN.md names |
| 6 | cutout | tabs on side 2, tab census, hand snap, 1.0 mm tab-zone copper keep-out |
| — | stencil + reflow | one stencil, SOIC-8 at 1.27 pitch as the finest feature, and 12 identical resistors in three package sizes as a controlled wetting comparison |
| — | wire vias | the whole point: 6 unplated holes, 12 hand joints, and a self-test that says whether each one took |
| — | firmware | the ring chases, the button catches, the buzzer scores. Nothing else proves the board |

## Decisions (2026-07-31, operator review)

The thirteen open questions this spec carried were put to the operator
and resolved; each decision is folded into the sections above, recorded
here with its consequence:

1. **Buzzer driver → MMBT2222A** (the plan's BJT cell stands). With Q2's
   answer the once-mooted v2 bridge seat is dead: a bridge does nothing
   for a self-oscillating element.
2. **BZ1 → identified from the operator's stock**: Cylewet CYT1036, 5 V
   ACTIVE magnetic buzzer with internal oscillator (Amazon B01N7NHSY6,
   pack of 10 per the listing). ACTIVE reshapes the sound design: PB4
   gates patterns, no pitch control; the BAV99 clamp is unambiguously
   the mandatory flyback. Count, body Ø and pin pitch are bench-sheet
   rows, not assertions.
3. **Button read → blanking-window digital first**; the ADC threshold
   read stays as the designed-in, no-hardware-change escalation.
4. **Silk order → follow the plan** (side A's legend gets taped over at
   the flip; risk accepted). If the tape lifts the cured legend, that
   is an Article II incident and the law changes on evidence.
5. **Vias → trial then permute**: route once with the default matrix;
   if the trial wants more than the 10 ceiling, the ring→pair mapping
   is permuted before any via is added. Budget 6 / ceiling 10 stands.
6. + 7. **Crib blockers → bench inventory sheet**: the operator fills
   `parts/bench-inventory-sheet.md` (ATtiny85, THT bins, buzzer, via
   wire, dowels, blanks, 74HC595) and the crib files are written from
   it verbatim. Layout freeze remains gated on the sheet coming back.
8. **Blank stock → 150 × 100** (not the assumed 100 × 80). One board
   per blank as designed; a second orbit per blank is a cut-time option
   that would re-plan the pins.
9. **LEDs → 5 mm** (ring Ø26, board 56 × 48 as drawn).
10. **M3 → 4 corners.**
11. **Dowel stack → hole depth 12.0 with the Ø2×12 dowel**, declared in
    the job as `seat_extra 0` + `tip_allowance 0` (the `[pins]` grammar
    grew validated declarable knobs for exactly this — defaults 0.2/0.6
    stand, negative refuses, the bed check still judges the result).
    The pin seats on the drill's real tip cone ~0.6 proud of the blank;
    the registration section states the honest arithmetic.
12. **Pin feed → F100** (89 % of fr4's sustained chip limit; the coin
    lane's F120 is a brass number reading 107 %).
13. **Flip gauges → Ø1.7 pad** (0.35 annulus declared at 0.3 — 0.05 of
    real margin; a gauge that fails its own check on a perfect flip
    gauges nothing).
