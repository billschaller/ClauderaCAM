# Board A — "coupon" (process characterization + functional blinky)

Single-sided, isolation-milled on the Carvera Air via the ClauderaCAM
PCB lane (PCB-PLAN.md WS7). This spec is the contract: the schematic is
built from it, the design review audits against it, and every BOM line
below is traced to a parts-crib file (`parts/*.toml`, Article XI for
components). The board is three things at once: a characterization
coupon read with a loupe, a 555 heartbeat you can see working, and a
component zoo that forces the stencil/reflow process through every
species and package size the bench holds.

## Process rules (override ALL konnect/fab defaults — this is a milled board)

| rule | value | why |
|---|---|---|
| copper | **B.Cu ONLY**, single-sided blank | SMD mounts on copper (back) side; THT inserts from front, solders on back |
| clearance | ≥ 0.4 mm | one pass of the 0.2mm-tip engraver clears 0.4 gaps from both sides |
| track | ≥ 0.5 mm min; 0.6 signal / 0.8–1.2 power | conservative, hand-solderable |
| jumpers | unplated wire links, front side | no plated vias; where flat routing would contort the board, a bare wire link (JPn) hops on the component side -- expected practice on a single-sided milled board |
| THT annular | ≥ 0.6 mm | milled pads lift easier than plated |
| copper-to-edge | ≥ 0.4 mm | edge cut is separate |
| drills | ≤ 1.2 straight; larger helical-bored (0.8 corn) | drill set 0.3–1.2; 3.4 (M3) bores fine |
| footprints | hand-solder variants where they exist | no plating, big pads |
| silkscreen | **B.Silkscreen ONLY**, lasered onto cured white mask | one setup, copper side up; strokes ≥ 0.3 from solderable pads |
| solder mask | whole B side masked, pads scrubbed (deflate −0.10) | spring-tool scrub phase |

Exception (deliberate): the 0.4 mm ladder trace in the coupon block
violates the 0.5 track minimum — that is the point of a
characterization ladder. It is confined to a named rule area; the
`.kicad_dru` exempts that area and nothing else.

## Power budget & supply

5 V nominal at the wire pads (NE555 bipolar needs ≥ 4.5 V — 2×AA is NOT
enough; bench supply or USB pigtail). Draw ≈ 35 mA peak (two THT LEDs
~12 mA each + logic). Series Schottky drops ~0.25 V.

## Circuit

**Power entry**: PAD+ (5V) and PAD− (GND) wire pads (THT, 1.5 mm drill,
≥3.2 mm pad) → SW1 slide switch (SPDT used as on/off) → D1 SS14 series
reverse guard → C1 → L1 → VCC rail (pi filter) → C2 ∥ C3. LED1 (0805
red) + R1 from VCC: power indicator.

**555 heartbeat**: U1 = NE555P DIP-8 **in a socket**. Astable: RA=R2
10k VCC→pin7, RB=R3 33k pin7→pins2+6, CT=C4 10µF pins2+6→GND. Pin 5:
C5 10nF to GND. Pin 4 tied to VCC. f ≈ 1.9 Hz nominal (X5R DC-bias
derating will raise it toward ~3 Hz — acceptable, it's a heartbeat).
Rate-kick: S2 tactile button in series with R4 6.8k, the pair in
parallel with R3 → ~7 Hz while held.

**LED driver**: pin 3 → R5 1k → Q1 S8050 (SOT-23) base; emitter GND;
collector sinks LED2 and LED3 (THT 5 mm, front-mounted) each through
its own series resistor (R6, R7 330Ω) from VCC.

**LM358 phase eye** (the SOIC reflow exercise, and it makes the analog
ramp visible): U2 = LM358 SOP-8, unit A as comparator — IN+ from the
CT node via R8 100k, IN− from VCC/2 divider (R9 10k over R10 10k, C6
100nF across R10), OUT → R11 330Ω → LED4 (0805 green) → GND. LED4
flips as the ramp crosses VCC/2, phase-offset from the 555's own
1/3–2/3 window. Unit B parked: IN+→GND, IN−→OUT.

**Decoupling**: C7 100nF at U1 pin 8, C8 100nF at U2 VCC — placed at
the pins.

**Ladder nets** (routed as serpentines at layout): TP1↔TP2 = LADDER_040
(0.4 mm track), TP3↔TP4 = LADDER_050 (0.5 mm), TP5↔TP6 = LADDER_060
(0.6 mm). Probe with a meter: an open means the mill broke the trace.

## BOM — every line traced to a crib file

Sizes are deliberately spread across all three EGSCST books
("Exercise them!"): every reflowed species at every size the stencil
must render.

| ref | value / part | package | source crib |
|---|---|---|---|
| R1 | 1k | 1206 | egscst-1206 |
| R2 | 10k | 0603 | egscst-0603 |
| R3 | 33k | 0805 | egscst-0805 |
| R4 | 6.8k | 1206 | egscst-1206 |
| R5 | 1k | 0603 | egscst-0603 |
| R6 | 330Ω | 0805 | egscst-0805 |
| R7 | 330Ω | 1206 | egscst-1206 |
| R8 | 100k | 0603 | egscst-0603 |
| R9 | 10k | 0603 | egscst-0603 |
| R10 | 10k | 0805 | egscst-0805 |
| R11 | 330Ω | 1206 | egscst-1206 |
| C1 | 10µF 25V X5R | 0805 | egscst-0805 (only 10µF source) |
| C2 | 1µF | 0603 | egscst-0603 (its top value) |
| C3, C6, C7, C8 | 100nF | 0805 | egscst-0805 (only ≥100nF source) |
| C4 | 10µF 25V X5R | 0805 | egscst-0805 (timing; bias derating noted) |
| C5 | 10nF | 1206 | egscst-1206 (its top value) |
| L1 | 10µH | 0805 | egscst-0805 (top of the L range) |
| D1 | SS14 (1N5819) | SMA | egscst-0805 / kokiso-smd (two sources) |
| Q1 | S8050 (J3Y) | SOT-23 | egscst-0805 |
| LED1 | red, Vf 2–2.3V | 0805 | egscst-0805 |
| LED4 | green, Vf ~2.1V | 0805 | egscst-0805 |
| U1 | NE555P | DIP-8 + socket | bojack-dip-ic; socket: operator stock |
| U2 | LM358 | SOP-8 | egscst-0805 |
| LED2, LED3 | 5 mm THT LED | THT | THT bins — **bench-confirm at BOM freeze** |
| SW1 | SS-12D00-class slide SPDT | THT 2.54 | THT bins — **bench-confirm footprint** |
| S2 | 6×6 tactile | THT | THT bins — **bench-confirm** |
| PAD± | wire pads | 1.5 mm drill | copper + wire |
| JP1–JP6 | wire links (10.16 / 12.7 / 15.24 mm spans) | 0.9 mm drill | operator wire stock |
| H1–H4 | M3 mounting holes | 3.4 mm bore | no hardware in BOM |
| TP1–TP6 | ladder probe pads | bare copper ~2 mm | none |

Species audit: R in 0603/0805/1206 ✓, C in 0603/0805/1206 ✓, L ✓,
SMA diode ✓, SOT-23 BJT ✓, 0805 LEDs ✓, SOP-8 IC ✓, DIP-8 socketed ✓,
THT LED/switch/button ✓. KOKISO's deeper roster (duals, FETs) is Board
B's job.

## Jumpers (board-only, front-side wire links)

Six unplated wire links, all on the component side, pads 2.1 mm / drill
0.9 mm (annular 0.6), spans limited to 10.16 / 12.7 / 15.24 mm so the
operator can bend wire on a jig. JP1 carries N_DISCH across the
TRIG_THR wall to S2; JP2 and JP3 stitch the VCC tree across the LED and
power belts; JP4–JP6 drop GND into the three pour pockets that
single-sided routing fences off. **Bench notes**: JP6 crosses the JP2
wire run — use insulated wire for JP6 (or bend it per the fab-layer
dogleg); JP3's wire passes ~0.3 mm from the U1 socket's west edge —
dress it tight to the board. Jumper footprints are board-only (not in
the schematic); KiCad models each wire as an F.Cu track inside the
footprint so connectivity and DRC see the link — F.Cu is never
exported or milled.

## Coupon block (layout artifacts, no schematic presence)

Reserved corner block ~14×40 mm, self-labeled in silk:
- **Trace/gap ladder**: three serpentines (the LADDER_* nets) at
  0.4/0.5/0.6 mm width with matching 0.4/0.5/0.6 gaps between folds.
- **Pad ladder**: one unconnected footprint column 1206 → 0805 → 0603
  (two-pad passive footprints, paste apertures included — they test
  the stenchill stencil at every size).
- **Silk ladder**: "0.4 0.5 0.6" markings plus text at 1.2 / 1.5 /
  2.0 mm heights ("SILK 1.2" etc.).
- **Scrub ring**: an annular copper ring pad (~8 mm OD, 2 mm band)
  for reading scrub-margin registration with a loupe.

## Layout notes

- ~55×40 mm rectangle, M3 holes in corners (3.4 mm, copper keepout).
- GND pour on B.Cu (minimizes clearing phase); keep ≥ 0.5 mm fill
  channels — no unbroken copper walls to the edge.
- SMD parts on the BACK (copper) side; THT on front. ALL text/legend
  on B.Silkscreen (the laser only reaches the copper side) — move THT
  refs to B side, orient text readable with copper side up.
- Silk labels that earn their laser time: "+5V" / "GND" at the wire
  pads, LED polarity ticks AT THE SOLDER SIDE for LED2/3, "COUPON
  v1" + date, board name.
- Wire pads and switch at one edge; LEDs 2/3 visible from the front
  top edge; coupon block owns the opposite corner.

## What this board verifies (ties back to the six phases)

1. iso: 0.4 clearances everywhere + the deliberate ladder
2. clear: pour keeps clearing small; coupon gaps exercise it
3. mask: full B side
4. silk: the silk ladder calibrates smallest readable text
5. scrub: every pad size 0603→DIP; the scrub ring is the gauge
6. drills+edge: 0.9–1.5 straight drills, 3.4 bores, tabs on the cutout
