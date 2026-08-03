# Orbit (Board B) — operator-commissioned high-level review

Hypothesis (Bill, verbatim): "the majority of the problems we've faced are just that you
[the assistant] are bad at PCB routing, and don't know enough about it to correctly
navigate the problem space given the constraints (home-milled PCBs, mix of hand and
reflow soldering, no plated vias or through holes)."

Evidence base: git log fe21d23..4b44d09 with full milestone messages (00adc1b, 20ba2e1,
2dd01b9, 728f054, 8a0bb75, f638c4e, 1c50e6a, ea809c2, 13ed55d, 34a0fbc, ca7a669,
bc04af7, 4b44d09, e1939b7, 96cc6cc, 313c67f, 0d9a2fe, 1d9202e, a39419e); DESIGN.md
2026-07-30 onward (esp. lines 1857-1921 mask-blind, 2271-2337 silk truth, 2338-2382 arc
closure); boards/orbit/SPEC.md (Layout notes 450-493; As-built 608-658); MATRIX.md;
tools-board.py / tools-route.py headers and law comments.

Timeline (commit dates): SPEC draft ~07-29; schematic 07-30 17:47 (5ddea52); the KiCad
routing era ran 07-31 to 08-01 midday with ZERO commits (layout "parked untracked",
atticked later at a39419e); migration 08-01 14:41 (00adc1b); via repeal 15:00; growth +
closure 08-01 evening; LED-ruling reroute 08-02 01:46; final copper 04:39; 180/180
09:43; done 14:04; silk truth 08-02 20:02. Roughly 3.5-4 days end to end, of which
~2.5 days were fighting problems (the rest: schematic, firmware, viewer, retirement).

---

## A. Root-cause taxonomy

Buckets: (1) domain-knowledge gap for THIS process; (2) tool limitation;
(3) process/verification gap (checks that lied or were blind); (4) genuinely hard.

| # | Problem | Bucket | Evidence | Cost (est.) |
|---|---------|--------|----------|-------------|
| 1 | Plated-barrel assumption: routed to 14/15 in KiCad "modeling plated barrels our milled holes do not have", hacking around it until operator ruled "no more hacks" | 1 (choice to fight the tool) + 2 (KiCad genuinely cannot express unplated THT connectivity) | DESIGN 2346-2349; 00adc1b; a39419e | ~1 day (largest single cost: the whole uncommitted 07-31 era + migration) |
| 2 | 2.54 LED lead pitch impossible: hole 1.0 + 2x0.7 ring + 0.4 clearance = 2.8 hard floor; drawn 2.54 | 1 (arithmetic from SPEC's own constants, knowable at draft) | SPEC 618-620; 00adc1b "THT pitch floor 2.82" | footprint + placement rework mid-era |
| 3 | Scrub-law pad growth omitted: SPEC Delta-row said O2.4 works; real law pad >= hole+1.50 (O2.50); growth rippled pitch 2.90->3.08 and re-placement 3x | 1 | 1c50e6a; SPEC 652-654 | re-placement churn late in closure |
| 4 | Ring-wall: draft placed 12 series resistors TANGENTIALLY at r~10 — "tangential 1206s wall off 8 of 12 crossing gaps" of the very corridors the K4 structure forces | 1 (placement fluency) | SPEC 462-466 (draft) vs 620-622 (as-built RADIAL) | rework + contributed to router starvation |
| 5 | Board undersized at 56x48 on free 150x100 stock; grew twice; ROOM alone closed 4/6 rats, then deleted both bench jumpers and zeroed silk flags | 1 (density instinct imported from fab-house economics; "density is a cost not a virtue" arrived as an operator ruling, not a design input) | 2dd01b9; 34a0fbc; DESIGN 2364-2367 | 2 growth rolls + jumper saga + RESET fight + silk crowding — ~0.5 day aggregate |
| 6 | Via budget 6 / ceiling 10 treated as scarce resource; checkpoint BLOCKED at the ceiling awaiting ruling; operator: "a wire via is cost-class of a jumper" | 1 (process cost model wrong; via aversion is a plated-fab norm) | fe21d23 (budget set); 00adc1b (blocked); 20ba2e1 (repeal) | checkpoint stall + ruling round-trip |
| 7 | Proud-LED dual-solder plan: pipeline promoted 10 LED anode leads as layer bridges = under-flange front joints on proud LEDs; operator: worst soldering on the board | 1 (hand-solder fluency) | 8a0bb75; SPEC 635-637; MATRIX 58 | full reroute night session; wedge fight; pre-seed invention |
| 8 | Router wedges: fencing 24 LED rings deadlocked FreeRouting (8->23 unrouted); interior copper stalls convergence 649s/11min/13min vs 58s; pass-9 wedge measured | 2 (FreeRouting brittleness) aggravated by 1 (placement fed it pathology) | 8a0bb75; 2dd01b9; tools-route.py header ("wedges in pass #9") | woven through 08-01/08-02 nights |
| 9 | Stale-data RESET fight: the "0.645mm escape" was measured BEFORE the growth; a 2.375mm corridor had existed for a whole parked-session cycle | 3 | 728f054 ("STALE DATA") | one parked session + pathfind.py build (reusable) |
| 10 | Mask-blind pads (Board A, in-scope per arc): LSET.AllCuMask() name trap + static-aliasing trap + the assert's own escape hatch skipping exactly the defect class | 2 (two pcbnew traps) + 3 (assert escape) | e1939b7; DESIGN 1857-1921 | bench rescue mid-run; found by Bill's loupe, not a gate |
| 11 | Proxies that lied: concentricity "track detector" (62/71 false convictions); spoke check measured a ring (1-of-720 sample); sliver metric a per-object minimum "blind by construction"; flash-based fab assert green over 2/3 of board; clearance oracle blind on unrouted/netless copper; via-pruner stale cache; pcb-rnd success line matching the shorts grep | 3 | f638c4e; 13ed55d; ea809c2; 1c50e6a; 00adc1b; 728f054; 34a0fbc | ~0.5 day aggregate; each produced a permanent check with the retired statistic asserted to still convict |
| 12 | Silk truth: keep-out set was "solderable pads" but the laser cures on MASK — dead front rings are bare copper (24 unknown discs), vias ARE soldered; labels placed on the UNROUTED board; no text-fusion or attribution concept ("PAD1ON" at 0.709); crowding audit read ZERO flags on a board a human couldn't read | 1 (laser/mask physics of THIS process) + 3 (clip = censored evidence: 47mm shredded before the gate judged; ordering error) | 4b44d09; DESIGN 2271-2337; SPEC 638-648 | full evening session; ref yield honesty 49/52 -> 37/52; 7 un-compression requests + 4 ISP names remain OPEN copper debt |
| 13 | Silent pour discards: keep-out grazing pour boundary / hole not wholly inside polygon makes pcb-rnd DISCARD the whole plane while parsing clean and passing DRC (324 clipper errors); Specctra (plane) makes FreeRouting sever GND while reporting success; .ses resolution header lies; FlatCAM geocutout lowercase 'lr' cuts CLEAN THROUGH silently | 2 | 00adc1b; 2dd01b9; 0d9a2fe; tools-board.py POUR_EDGE_SETBACK comment | caught by census/oracles; moderate |
| 14 | Margin ties: land at exactly 0.400 TIES the 0.4 law ("third strike"); ink gap passed at exactly 0.0; O2.40 ring read 0.699 | 3 (craft: geometry must never sit on a bar) — institutionalized late as RING_MARGIN/DRC_MARGIN | 8a0bb75; ea809c2; tools-board.py | repeated small losses |
| 15 | K4 crossing structure: 6 two-track corridors forced for ANY LED permutation (four vertices cannot each own a contiguous arc of a 6-cycle) | 4 (intrinsic to a 2-layer charlieplexed ring) | MATRIX.md line 7 | irreducible — but it was proven POST-HOC, not budgeted pre-placement |
| 16 | PAD+/- transposition forced by VBAT corridor | 4 (minor, honest layout override) | 00adc1b; MATRIX 116-120 | trivial |

Effort fractions (of the ~2.5 problem-days; judgment, anchored to the timeline):
- (1) domain-knowledge gaps for this process: **~45%** (items 1-choice, 2, 3, 4, 5, 6, 7, 12-physics)
- (3) process/verification gaps: **~25%** (items 9, 10-assert, 11, 12-censorship/ordering, 14)
- (2) tool limitations: **~20%** (items 1-KiCad-axiom, 8, 10-traps, 13)
- (4) genuinely hard: **~10%** (items 15, 16, and the irreducible novelty of an unplated double-sided charlieplex ring)

---

## B. The hypothesis, tested

**Where it is RIGHT.** A designer fluent in home-milled/unplated/mixed-solder boards
makes at least six different day-one decisions, and every one of them was instead a
late correction with a measured cost:

1. **Model unplated holes from the first route.** The process truth was WRITTEN in the
   SPEC (wire vias, dual-solder leads, fe21d23) — yet routing began inside KiCad's
   plated-barrel axiom and spent ~a day of hacks (DSN surgery, scratch-copy DRC,
   external unplated model — a39419e) before the operator, not the assistant, called
   it: "no more hacks" (00adc1b). Fluency here means knowing every mainstream EDA
   connectivity model assumes plating and picking the substrate (pcb-rnd hplated)
   before the first airwire. Cost: the largest single line item.
2. **Size the board to the process, not to habit.** Area on 150x100 blanks is nearly
   free; density bought nothing and cost rats, jumpers, the RESET saga, and silk
   crowding. Growth cured all four — twice (2dd01b9, 34a0fbc). "Grow before
   cleverness" had to arrive as an operator ruling.
3. **Cost vias like jumpers.** Budget 6 / ceiling 10 was plated-fab instinct; the
   checkpoint literally halted at the ceiling (00adc1b) until Bill repealed it
   (20ba2e1). Final board: 23 vias, all ledgered, zero regret.
4. **Never plan a joint a human can't make.** Ten under-flange front joints on
   proud-seated LEDs survived SPEC review AND routing until the bench ruling
   (8a0bb75). "Dual-solder only bare open-access conductors" is day-one knowledge for
   anyone who has stitched an unplated board. Cost: a full night reroute.
5. **Derive footprint arithmetic from process law before drawing.** Pitch floor
   (2.8+), pad = hole+1.50 (scrub laps) were both derivable from constants the SPEC
   itself stated; both were discovered by toolchain fights instead (00adc1b, 1c50e6a).
6. **Treat silk as a placement input, not a decoration pass.** Keep-out = every mask
   aperture + bare copper (laser physics), attribution needs reserved seats, labels
   follow ROUTED copper. Instead: labels placed on the unrouted board, a keep-out set
   defined by solderability, and a final render Bill had to read himself (4b44d09).
   Residue: 7 un-compression requests and 4 missing ISP names are STILL open copper
   debt on the shipped artwork.

**Where it is INCOMPLETE.**

- **The locus is placement and process economics, not routing execution.** The actual
  routing machinery ended up strong: pre-seeded protected crossings (19s convergence
  vs 1-hour wedge), the sealed deterministic session, the A* closer verified against
  the clearance oracle, the K4 proof that no permutation escapes 6 corridors. "Bad at
  PCB routing" misnames a deficit that lived UPSTREAM of the router: what the router
  was given, and what the joints/vias/area were assumed to cost.
- **~25% of the pain was verification craft, which the fluent designer does not
  avoid.** The track-detector proxy, the ring-not-spoke check, the sliver minimum, the
  censoring clip, the stale lane data — these are proxy-design failures in a gate
  system most fluent PCB designers don't even build. They were also one-time costs:
  each ended as a permanent check that asserts its retired statistic still convicts.
- **~20% was tool betrayal that would hit anyone**: pcb-rnd silently discarding a
  whole pour while passing DRC, FreeRouting severing GND under (plane), geocutout's
  case-sensitive silent sever, pcbnew's static-aliasing LSET trap. Several are
  documented nowhere upstream; the loupe or an independent oracle found them.
- **~10% was intrinsically hard.** A double-sided charlieplexed ring with no plating
  is close to the worst case for this process; 6 forced corridors are graph theory,
  not ignorance. The flip/registration machinery worked.
- Scoreboard honesty: the board DID ship — zero jumpers, every net in copper, 180/180,
  firmware green (34a0fbc, ca7a669) — in ~4 days including building a new board-
  authoring substrate. The knowledge gap was real and expensive; it was not fatal, and
  the record shows it converting into law at each step.

Verdict: the hypothesis is **directionally right, roughly half right by effort share**
(45% domain-knowledge, rising to ~55% if you count the *choice* to fight KiCad rather
than switch as knowledge rather than tooling) — and wrong in its noun: not "bad at
routing" but "arrived with plated-fab instincts for placement, joint design, and cost
models, and learned this process's economics mid-flight by operator ruling."

---

## C. The counterfactual day-one design

Given: unplated holes, wire via ~ jumper cost, area nearly free, hand+reflow mix, silk
must attribute to a human.

- **Substrate**: pcb-rnd-native from the first airwire (hplated as declaration; dead
  front rings modeled). The KiCad schematic stays canonical — plating is a board
  concept (bc04af7's final architecture, chosen first).
- **Size**: 66x56 minimum from the start (two-up on 150x100 still possible). Density
  budget stated as a NEGATIVE: whitespace is the router's raw material.
- **Crossing budget before placement**: K4 analysis on day one gives 6 two-track
  corridors; assign them to specific ring gaps; each crossing = a planned stitched-via
  pair with a ledger row written AT DESIGN TIME (~20 seats, vs the 23 the board
  actually spent — the plan would have been honest within ~15%).
- **Placement philosophy**: ring interior copper-free on BOTH faces (the measured
  interior-cell pathology, 2dd01b9); U1 + RESET/SND cells OUTSIDE the ring; resistors
  RADIAL; joint classes fixed first (bodied THT = back-solder flush, dual-solder only
  bare pads); legend seat (one cap-height lane) reserved per attributable ref before
  any track exists; nothing designed on a bar (+0.02 everywhere).
- **The router's job**: connect a solved topology — inherit pre-seeded protected vias
  at every planned crossing, bounded passes, sealed session. Measured basis: that
  exact configuration converged in 19 seconds (8a0bb75).
- **Avoided**: the KiCad era (~1 day), the growth/jumper/proud-LED churn (~0.5 day),
  the silk reroute debt (~0.2 day) — **roughly 55-65% of problem effort**. NOT
  avoided: proxy failures, tool traps, the pipeline build itself (~35-45%).

## D. Lane laws for Board C — design-phase checklist (inputs, in application order)

1. **Process constants sheet** (before any geometry): drill floor 1.0; ring 0.7; pad =
   hole + 1.50 (scrub law incl. laps); THT pitch floor = ring dia + clearance + margin
   (>= 2.84; use 3.0); track 0.6 / rail 0.8; pour edge setback 1.10; tab zones 1.0;
   probe grid must miss holes on side 2.
2. **Cost model declaration**: via = jumper class, plan tens, ledger each with reason;
   area free to blank/2; joint classes — bodied THT back-only flush, dual-solder only
   bare open-access, NO under-body joints ever (8a0bb75 law).
3. **Topology math**: crossing number of the netlist over the layer partition (the K4
   exercise) -> corridor count; corridor width = 2xtrack + 3xclearance (~2.5mm);
   corridors assigned to named physical gaps BEFORE any part is placed.
4. **Reservations at placement**: via seats (O2.5 + clearance) at every planned
   crossing; one legend lane per attributable ref (cap-height text + 0.30 to every
   same-side mask aperture AND bare copper feature — the corrected 4b44d09 set);
   dense-structure interiors copper-free; mounts/gauges/tabs with the pour-hole-
   wholly-inside rule pre-checked.
5. **Margin law**: every designed dimension that meets a </>= test carries >= 0.02
   over the bar (RING_MARGIN pattern, institutionalized not rediscovered).
6. **Only then route**: pre-seed the ledgered vias as protected copper (points, not
   fences — protected copper CROSSING routing space is convergence poison, 1c50e6a);
   bounded passes; labels placed on ROUTED copper; silk audit as acceptance canary;
   gates judge ARTWORK, never post-clip programs (censored-evidence law).

## E. What went right (keep all of it)

- **The constitution held.** Article I refusals were real: the programs stage refused
  to invent artwork (1c50e6a), fantasy-bridge and negative controls bit on every
  build, determinism was byte-level everywhere. No check was weakened to ship.
- **The operator's eye as a gate.** Four of the five biggest catches were Bill's
  (plated hack, proud LEDs, mask-blind loupe, the silk render). That is Article II
  working as designed — but see D: each was also a purchasable design input.
- **Ruling -> law mechanism.** Every ruling landed in code + SPEC/MATRIX within hours,
  with its reasoning verbatim (20ba2e1 is the model: "a threshold leaves the same way
  it arrived"). Retired statistics assert they still convict (f638c4e, 13ed55d) — the
  disease class cannot return.
- **Pipeline determinism.** Sealed router sessions, byte-identical re-emission, the
  firmware table provably transcribed from MATRIX (ca7a669), the stitch list == the
  plated Excellon program: the bench artifacts are machine-derived, not hand-copied.
- **Measurement culture.** Falsified its own theories on numbers (pseudo-net rings
  "moved the score 0.15 — falsified", C2 nudge "rejected on measurement", 8a0bb75).

## Residual open items (flagged, not resolved here)

- MATRIX.md line 75 prints "**7 unexplained** back pour regions [21,22,34,35,44,49] —
  INVESTIGATE" — a 7-vs-6 count/list mismatch, and it contradicts 1c50e6a/34a0fbc's
  "0 unexplained / artifacts explained". Worth one look before the cut.
- 7 silk un-compression requests + 4 ISP names = open copper debt (MATRIX 83-101);
  operator holds the trade-off vs the zero-jumper route (4b44d09).
- Board B is not yet cut; every conclusion above is artwork-verified, not bench-verified.
