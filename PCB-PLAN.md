# The PCB Lane — execution plan

Written 2026-07-29, green-lit by the operator ("Let's move on to PCB").
This document is the handoff across the context compact: it is the plan,
DESIGN.md remains the law, and everything here lands the same way
everything else landed — gate first, viewer first, incidents become
checks. Fold the results into DESIGN.md/README as workstreams ship, then
delete this file.

## Mission

Three deliverables, in dependency order:

1. **The pipeline**: a `[pcb]` job type in ClauderaCAM that turns KiCad
   gerbers into verified, viewer-previewable, downloadable Carvera
   programs for the operator's revised six-phase chain:
   **1 cut traces → 2 clear copper → 3 solder mask (apply+cure) →
   4 silkscreen (laser) → 5 scrub mask off pads → 6 drills + edge cut.**
   (Order revised from the zigbee-button build: silk now precedes the
   pad scrub; drills and edge cut close the job.)
2. **Board A — "coupon"** (single-sided): a process characterization
   board that is ALSO a functional blinky. Exercises every phase on one
   side. First metal— er, first copper for the lane.
3. **Board B — "orbit"** (double-sided): pin-and-flip PCB with
   hand-soldered wire vias, stencil+hotplate reflowed SMD side, THT
   side, and firmware that proves the board works. Soldering practice
   by design.

Source truth from the zigbee-button V2 build (all paths on this box):
- Working FlatCAM chain: `~/scratch/ha/devices/projects/zigbee-button/pcb/cam/flatcam/`
  (`zigbee-button.tcl`, `assemble_multitool.py`, `verify_outputs.py`,
  verified 2026-07-19 vs pcbnew; silk laser dose S0.03 field-validated)
- Process law: `~/scratch/carvera/guides/pcb-milling-workflow.md` and
  `solder-mask-and-silkscreen.md` (design rules, spring-tool doctrine,
  laser focus incident, ZMIN staleness rule)
- Patched FlatCAM EVO fork: `~/scratch/carvera/flatcam`

## Workstream 0 — safeguard (do this first, it is one `git checkout .` from gone)

The three headless-Tcl fixes in the FlatCAM checkout are UNCOMMITTED
local diffs (TclCommandPaint/TclCommandCopperClear lazy `gen`
construction, PaintGen unbound `proc`). Commit them to a branch on a
fork under the operator's GitHub, record the pinned commit in the
`[pcb]` machine config, and note the fork+patches in DESIGN.md. Also
capture the non-code setup: `~/.FlatCAM/current_defaults_Unstable.FlatConfig`
(circle steps 64; `set_sys` is broken in the fork).

## Workstream 1 — physical modeling (both kernels, Article X)

- **Vee tool** (`type = "vee"`): conical drop profile
  `drop(r) = (r − tip_r)/tan(half_angle)` above a flat tip — same shape-
  function slot as the ball. Retires the conical-tools roadmap item for
  the fixed-shallow-depth case. Contact/engagement limits: provenance =
  Makera PCB tutorial params + the cut zigbee boards, marked PROVISIONAL.
- **Scrub tool** (`type = "scrub"`, the Makera spring mask tool):
  commanded depth is spring preload, not cut depth — ZERO material
  removal in simulation, exempt from chip/power physics WITH the
  exemption stated in physics.py (Article IX). Its checks are
  containment/coverage/exact-depth/param-match instead.
- **Laser program type**: no spindle, no tools, own dialect law:
  `M321` then a MANDATORY `G0 Z0` (the defocus incident — a big square
  beam cures mask in washes), `M3 S ≤ dose limit` (S is 0–1.0 on the
  Air), XY feed moves only, `M5` close. The emitter refuses everything
  else; the verifier checks stroke containment and pad clearance.
- **Materials**: FR-1/FR-4 entry (chip load, plunge, rubbing floors) —
  provenance: Makera params + zigbee cut evidence, PROVISIONAL.
- **Crib entries** (Article XI, measured or catalog only): 0.2mm-tip 30°
  V engraver, 0.8 and 1.0 corn mills, the spring mask tool, and the PCB
  drill set 0.3–1.2 IF used (zigbee milled all holes with the 0.8 corn —
  default to milldrills, drills optional).

## Workstream 2 — ground truth (independent of FlatCAM, house style)

- **gerbv** rasterizes each layer (B.Cu/F.Cu, mask, silk, paste,
  Edge.Cuts) to PNG at a declared DPI (~2540 = 0.01mm/px) → numpy bool
  board maps. gerbv's parser shares no lineage with FlatCAM's — the
  generator and the verifier read the same source through independent
  implementations, exactly like the STL lane.
- **Excellon parser** in-repo (~50 lines): the hole schedule
  (position, diameter) is ground truth for drill verification.
- **Board-map module**: masks + `scipy.ndimage.distance_transform_edt`
  give every containment/coverage/margin check in existing house style.
- The machine-frame transform (mirror + offset) is DERIVED from the
  Edge.Cuts extents — the hand-computed 154/124 constants die here.

## Workstream 3 — job grammar + the FlatCAM engine

- `[pcb]` TOML: gerber directory, stock (blank size/thickness/copper
  sides), spoilboard, transform (derived, assertable against one known
  feature), phases with per-phase params (iso dia/depth, clear margins
  and morphological-opening threshold, scrub offset/overlap, silk
  dose/clearance, hole strategy, cutout tabs), operator steps as
  first-class entries, tools by crib reference.
- FlatCAM runs headless as a GEOMETRY ENGINE only: Tcl templated from
  the TOML (never hand-written), sentinel-poll-kill runner formalized in
  a module, per-phase `.nc` treated as geometry interchange. External
  binary, configured path, optional dependency — like the Rust kernel.
- Double-sided composes with the EXISTING pin-and-flip machinery:
  `[pcb]` + `[twosided]` in one TOML. Registration pin holes (Ø2,
  flip-symmetric, through into spoilboard) reuse the pins law wholesale
  — symmetry enforcement, keep-out, spotface+peck. Sequence: side A
  phases 1–5 → drill ALL through-holes (vias, THT, mounting) plus the
  registration pins → flip → side B phases 1–5 → edge cut with tabs.
  Holes drilled once, from side A, so both sides' artwork aligns to the
  same physical holes; flip accuracy is the coins' proven pin-to-hole
  clearance.

## Workstream 4 — emission & assembly (Article V)

- Parse FlatCAM phase output with the strict parser (verified dialect-
  clean: no arcs, no G-less lines) and RE-EMIT through emit.py:
  `(begin operation: …)` stage markers, `M5`-before-`M6`, `G4 P2` after
  `M3` (the zigbee files genuinely lack the dwell — assemble_multitool
  retires), 128-char law, header tool table + run-sheet comments.
- Program split follows the six phases and the operator steps between
  them (mask squeegee+cure at a tool-change pause, white mask + IPA
  around the silk job). Laser silk is its own program file with its own
  dialect. Every program: verified bytes stored at push, downloadable
  from its session, ZMIN staleness impossible by construction.

## Workstream 5 — verification (the gate grows a PCB lane)

Geometric sim runs as-is on the thin sheet (rapids, plunge, keep-out,
depth-vs-bed, physics) once WS1 lands. New named checks, each traced to
a zigbee incident or guide rule:
- iso containment (≤ tol of copper+tip-radius centerlines) and iso
  COVERAGE of every copper island's exterior outline
- clear containment (tool edge ≥ 0.02 from copper; the morphological
  opening must exceed tool dia with real margin — the castellation-
  chewing incident)
- scrub margin (tool edge ≥ 0.05 INSIDE pads — the peeled-trace
  incident; regions are pads deflated, mask-defined)
- silk clearance (strokes ≥ 0.3 from solderable pads, dose ≤ limit,
  the mandatory focus move present)
- hole schedule (every Excellon hole bored at position/diameter/depth;
  none extra — the displaced-drill class)
- cutout ride band + tab census (reuse the coin walk approach)
- double-sided: side-frame mirror consistency (B.Cu frame == mirrored
  F.Cu frame from the same Edge.Cuts), via/hole concentricity across
  the flip, pins law checks carried over
- per-program ZMIN/param echo (the stale-repost incident)
Suites: **golden-pcb** from Board A's committed gerbers + blessed phase
outputs (CI never runs live FlatCAM; a separate optional workflow job
does), negatives distilled from the incidents above, parity additions
for the vee kernel. First PASS cross-checked against the 2026-07-19
pcbnew numbers (iso containment 0.005, mask margin 0.145, 10/10 bores).

## Workstream 6 — viewer

PCB jobs are sessions like any other: stage list from markers, stock
sim of the sheet, per-stage stats, downloads. Additions:
- 2D overlay for non-carving stages (silk strokes, scrub regions,
  paste apertures) — this pulls the toolpath-overlay roadmap item in,
  scoped to what the lane needs.
- Run-sheet card: the operator steps (tape, auto-level, squeegee, cure,
  stenchill stencil print, reflow) rendered in order with the programs
  between them. The M6-pause instructions also live in the program
  headers, as before.

## Workstream 7 — Board A: "coupon" (single-sided, the full chain)

Concept: **process ladder + functional blinky in ~50×35mm.** One board,
two jobs:
- A corner block of characterization ladders: trace/gap pairs at
  0.4/0.5/0.6mm, pad sizes stepping 1206→0603, silk text at 1.2/1.5/2mm,
  a scrub-margin ring — read the board with a loupe, learn the process
  window, keep it as the reference artifact.
- A 555 heartbeat: NE555 (DIP, BOJACK kit) astable → SOT-23 driver
  (KOKISO) → LED pair; slide switch, tactile button (rate kick), wire-
  pad power (2×AA or 14430). Deliberately one resistor each in 0603,
  0805, 1206 (the three EGSCST books) — the BOM is itself a process
  test. SMD on copper side for stencil+hotplate practice; THT from the
  front.
Design in KiCad via the konnect flow (schematic build agent, mill
design rules from the guide as `.kicad_dru`: 0.4 clearance / 0.5 track
/ B.Cu only for the single-sided board, design-review agent before
export). Board A's gerbers become the golden-pcb assets.

## Workstream 8 — Board B: "orbit" (double-sided pin-and-flip)

Concept: **a chase-the-light reflex game.** 12-LED charlieplexed ring
(THT LEDs, front) spins at ramping speed; catch it at the marker with
the button; the piezo scores you. ATtiny85 SOIC-8 brain (on hand, AVR
programmer on hand), 4 charlieplex lines — which HAVE to cross sides:
the vias are load-bearing, not decorative.
- Front: LED ring, 2 buttons, piezo, power switch, battery pads.
- Back: ATtiny85 + SOT-23 P-FET reverse protection (the zigbee circuit)
  + 0805 passives — stencil, paste, hotplate reflow.
- Wire vias: a `WireVia` footprint (0.8mm hole, ~2.0 annular both
  sides) — KiCad plated vias can't model hand-soldered wire; THT-pad
  footprints can. Drilled from side A with everything else.
- ISP: 2×3 header or pogo pads (decide at layout vs board space).
- Firmware: ~150 lines of AVR C, avr-gcc or arduino-cli, flashed via
  the operator's programmer. Firmware IS the functional verification.
Assembly order (run-sheet): reflow the flat SMD side first, then wire
vias (solder both faces), then THT, then flash, then play.

## The parts gate (Article XI for components)

The three EGSCST books are confirmed 0603/0805/1206 RESISTOR kits —
everything else on their labels ("capacitors, diodes, ICs…") is Amazon
boilerplate until verified. Before layout freezes, each board's BOM is
checked line-by-line against the physical kits (KOKISO SOT-23 contents,
BOJACK DIP list, 74HC inventory, piezo, switches, LED colors); a part
the bench does not hold does not enter the schematic. Same law as the
tool crib, same reason: the 14mm drill that never existed.

## Stencil + reflow (off-machine, in the run-sheet)

stenchill.com converts a gerber ZIP (paste layer) into a 3D-printable
stencil STL — 0.3–0.4mm PLA/PETG, 0.2mm nozzle, registration shoulders
built in; happiest at 0603+ and large-pitch ICs, which both boards
respect by construction (SOIC-8 is the finest pitch anywhere). Export
the paste gerbers as a first-class job output; stencil print + paste +
hotplate reflow are run-sheet steps. The operator validated stenchill
on the zigbee-button project.

## Sequencing (each milestone lands CI-green, viewer-live, committed)

1. WS0 safeguard (same day it starts — the diffs are fragile)
2. Board A design in KiCad → parts gate → gerbers committed
3. WS1 vee/scrub/laser/material/crib + parity
4. WS2 ground truth (gerbv, Excellon, board maps)
5. WS3+4 grammar, engine, runner, re-emission — Board A end-to-end
6. WS5 checks + golden-pcb + negatives (Board A = the golden asset)
7. WS6 viewer additions → **cut Board A, full six phases, assemble,
   blink** — process window learned, incidents (there will be some)
   become law
8. Board B design → parts gate → twosided composition (WS3's
   double-sided path) → cut, flip, solder, flash, play
9. Fold everything into DESIGN.md/README, retire this file

## Deliberately deferred

- ESP32-H2-Zero Zigbee board (Board C candidate — the modules are on
  hand and the H2 footprint is proven; after B)
- Real twist-drill drilling cycles for PCB holes (milldrills first)
- Panelization (the 3-up Tcl trick is documented in the zigbee README)
- MakeraCAM interop (stays the documented quick-path alternative)
