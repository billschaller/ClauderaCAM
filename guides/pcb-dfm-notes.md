# Fab-house DFM, translated for the milled lane

**What this is.** The operator asked for "pcb best practices for one of the big
houses like pcbway or jlcpcb" to be looked up and incorporated. This file is
that work: the published design rules of JLCPCB and PCBWay (plus IPC-derived
practice where both houses are silent), each one held against the physical
process this lane actually runs, and each one either adopted, adapted with a new
derivation, or refused with a reason.

**Provenance rules for every number below.** A number is either (a) quoted from
a fab house's own public capability/DFM page — with the house named — or (b)
derived from this lane's tool geometry and the bars already in `checks.py`, with
the arithmetic shown. Nothing is invented. Where a fab house publishes no
number for a practice, this file says so instead of borrowing one from a blog
and dressing it as a spec.

**Standing on existing law.** The lane's laws (0.4 clearance, 0.5 track, ≥0.6
annular on Board A / ≥0.7 on Board B, silk ≥0.3 from pads, mask deflate,
clearing ≥0.9 with Board B's ≥1.2) are incident-traced and are *not* modified
by this file. Where fab practice suggests a change to one, it is written into
the "Proposed changes — operator decision required" section and flagged, never
folded silently into a rule table.

The two pain points that triggered this research are answered in §1 (thermal
reliefs, for the heat-sunk hand joints) and §0 + §2 + §8 (the kerf arithmetic,
for the residual copper slivers).

---

## §0 — The four milled facts that rewrite every fab rule

Every DFM rule a fab publishes is a statement about one of four processes this
lane does not run. Before adapting anything, name them:

1. **No wet etch.** Copper is not dissolved, it is *cut away* by a tool of
   finite width. Every rule about etchant behaviour (acid traps, etch
   compensation, ±20 % trace-width tolerance, copper slivers that "cannot be
   etched reliably") is either void or needs a new derivation.
2. **No plating.** No barrel, no knee, no pad anchored by a plated wall. A pad's
   only attachment is the laminate's own adhesive. Annular-ring and pad-lift
   rules get *stricter*, not looser.
3. **Clearing is kerf-based and has a minimum feature size.** Unwanted copper
   leaves in two ways only: the isolation vee's trench, or the clearing corn's
   pocket. Between the two there is a **band of gap widths that neither tool
   removes.** This is the single most important consequence in this document.
4. **The solder mask is squeegeed by hand and opened by a spring tool.** There
   is no photo-imaged aperture. The mask "expansion" is *negative* here — the
   opening must land *inside* the pad, the opposite sign from every fab number.

### The kerf arithmetic (derive it, don't memorise it)

The iso tool is the 30° vee, tip Ø0.2, cutting at Z−0.15
(`boards/coupon/coupon.toml`, `[[tool]]` T2 + `[phases.iso]`):

```
kerf          = tip_dia + 2·depth·tan(15°) = 0.200 + 2(0.15)(0.26795) = 0.2804 mm
centerline    = tip_r = 0.100 mm outside the copper edge (FlatCAM isolate -dia 0.2)
bite into copper   = kerf/2 − tip_r = 0.1402 − 0.100 = 0.040 mm  per edge
reach into the gap = tip_r + kerf/2  = 0.100 + 0.1402 = 0.240 mm  per edge

G_iso  = 2·(tip_r + kerf/2) = tip_dia + kerf = 0.480 mm
         → any non-copper gap ≤ 0.480 mm is fully consumed by the iso pass alone
G_corn = corn_dia + CLEAR_OPENING_MARGIN = 0.800 + 0.100 = 0.900 mm
         → the clearing corn's centre only exists in gaps ≥ 0.900 mm
           (checks.py CLEAR_OPENING_MARGIN = 0.10; Board B raises the design
            floor to 1.200 mm for real margin — orbit SPEC, clearing row)
```

**The forbidden band: 0.480 mm < gap < 0.900 mm (design floor 1.200 mm).**
A gap in this band leaves a standing copper sliver of width `gap − 0.480` that
*no phase removes*. It is electrically isolated — so every existing gate check
passes — but it is a floating copper hair bonded to laminate by nothing but
adhesive, in a board that gets handled, hand-soldered and snapped off tabs.
That is pain point (a), and this is its number.

Worked values:

| drawn gap | sliver after iso | who removes it |
|---|---|---|
| 0.40 (the clearance law) | none — kerfs **overlap by 0.080** | iso |
| 0.48 | 0.000 (exactly tangent) | iso, with zero margin |
| 0.50 | **0.020** | nobody |
| 0.60 | **0.120** | nobody |
| 0.80 | **0.320** | nobody |
| 0.90 | 0.420 | the corn, at its own bar |
| 1.20 | 0.720 | the corn, with margin (Board B law) |

Two immediate consequences worth stating out loud:

- **The 0.4 mm clearance law is not conservative padding — it is the number
  that puts 0.080 mm of kerf overlap in every gap.** Fab houses accept
  0.10–0.20 mm spacing (JLCPCB pad-to-track 0.10 mm; JLCPCB's own "safe
  default" 0.20 mm) because etchant reaches anywhere. A 0.15 mm gap here is
  perfectly millable (0.15 ≤ 0.480); a 0.50 mm gap is not. **In this lane
  "wider is safer" is false in a 0.42 mm-wide window.**
- **Board A's coupon gap ladder is already the experiment for this.** Its three
  gaps are 0.4 / 0.5 / 0.6 — i.e. sliver 0.000 / 0.020 / 0.120 by the arithmetic
  above, and DESIGN.md's live-run closure confirms *zero* clearing samples enter
  any ladder block, so iso is the only tool that touches them. Read the ladder
  with the loupe and the 0.480 figure is either confirmed or corrected by metal.
  That is an Article II measurement waiting to be taken, not a prediction.

Also derived from the same arithmetic, used repeatedly below:

```
delivered track width = drawn − 2(0.040) = drawn − 0.080     (nominal)
worst case allowed by the gate = drawn − 0.080 − 2(ISO_CENTERLINE_TOL 0.06)
                              = drawn − 0.200
  → drawn 0.50 may deliver as little as 0.30
  → drawn 0.60 may deliver as little as 0.40
```

---

## §1 — Thermal reliefs (the hand-soldering pain point)

**What the fab houses say.**

- **PCBWay** (*What are Thermal Relief Pads?*): a thermal relief is "three or
  four traces connected to the pad in a + or x shape"; the spokes' purpose is to
  create "a small gap between the copper trace and component pin"; and relief
  may be skipped when "the design does not involve high-power components or
  large copper pours, or if the assembly process does not involve wave
  soldering". **PCBWay publishes no spoke width or gap number.**
- **PCBWay** (*Is it a Relief to Use Thermal Relief?*): "the pad is connected to
  the copper pour through only 4 traces or spokes… we minimize the amount of
  heat that is transferred to the plane, making it easier focus heat and solder
  components." Again no numbers.
- **JLCPCB** (*PCB Copper Pour Basics*): thermal reliefs are *required* for
  component connections to a pour, because copper conducts at "approx.
  380 W/(m·K)"; their function is to "reduce heat dissipation and help with
  soldering". **JLCPCB publishes no spoke geometry either** — it is left to the
  CAD tool.
- **JLCPCB's DFM tool** does check the failure mode: **"starved thermals"** —
  spokes too thin, or gaps in the pour leaving the connection incomplete.
- The only fab-published *numbers* found anywhere in this research come from a
  smaller house, **BestPCBs** (*PCB Thermal Relief Design Guidelines*, 2026-06):
  spoke width "0.20–0.50 mm may be a common starting range", relief gap "a
  typical starting range may be around 0.20–0.50 mm", "Four spokes usually
  provide better current distribution than two spokes", and the design principle
  "The total copper cross-section of all spokes is more important than one
  single spoke width." IPC-2221-derived practice adds the ampacity rule: size
  the *total* spoke cross-section to the incoming conductor (e.g. four 0.25 mm
  spokes to carry a 1.0 mm trace).

**Does it apply here? ADAPTED — and it becomes mandatory, not optional.**

The fab houses' escape clause ("skip relief if there's no wave soldering") does
not release this lane, because this lane's *entire* THT assembly, every wire
via, and all rework is **hand soldering with an iron** — the one process where
plane heat-sinking is worst and where the fab houses' own reasoning ("easier to
focus heat", PCBWay) applies hardest. Board A hand-solders every THT lead on
the back face; Board B hand-solders twelve wire-via joints on *both* faces.
Meanwhile the SMD side is reflowed on a **hotplate**, which heats the whole
laminate from below rather than blowing hot air at pads — so plane heat-sinking
is a much smaller *reflow* problem here than in a fab's convection oven, and a
much bigger *iron* problem.

But the geometry must be re-derived, because the fab's numbers land inside the
forbidden band:

- **A 0.5 mm thermal gap is the worst possible choice in this lane.** By §0 it
  leaves a 0.020 mm copper hair ringing every relieved pad, in the exact place
  a hand iron then applies heat and mechanical force. BestPCBs' "0.20–0.50 mm"
  range is fine at its bottom end and forbidden at its top. **Board A currently
  draws thermal gap 0.500** (`tools-layout.py:521`,
  `SetThermalReliefGap(NM(0.5))`) — see the proposed-change list.
- **Spoke width is floored by the track law, not by ampacity.** GND return on
  Board A is ≈35 mA and on Board B ≈55 mA peak; four 0.6 mm spokes in 35 µm
  copper is 0.084 mm² of section, orders of magnitude past need. Ampacity is
  irrelevant; *deliverability* is the constraint. A 0.5 mm spoke may deliver
  0.30 mm at the gate's worst allowed centerline error (§0); 0.6 mm delivers
  0.40 mm worst case. **Draw spokes at 0.6 mm** — the SPEC's own "signal" width,
  which is also comfortably above the 0.5 mm track floor.
- **Spoke count: four, per PCBWay's "+ or x" and BestPCBs' "four better than
  two".** Four also matters mechanically here in a way it does not at a fab: on
  an unplated pad the spokes are part of what keeps the pad registered while an
  iron pushes on it. Two spokes on a bored pad is a hinge.
- **On small SMD pads a spoke is degenerate and a *neck* replaces it.** An 0603
  hand-solder pad is ~0.9 mm across; a 0.6 mm spoke is two-thirds of that edge,
  which is a solid connect with extra gerber. The correct lane construct is a
  **single routed neck**: hold the pour off the pad by the 0.4 mm clearance
  (zone connection = *none*) and route one 0.6 mm track stub, ≥0.5 mm long, from
  the pad into the pour. It is one heat path instead of a perimeter, it is an
  ordinary track so the 0.5 mm law already governs it, it is visible to DRC and
  to the netlist (so it can never be a "starved thermal"), and every gap it
  creates is the 0.4 mm law's gap, which iso consumes.

**When solid connect stays acceptable** (adapting PCBWay's escape clause and
the common "no relief on planes under ~5 mm²" practice):

- a pad whose net island is a *local pocket* rather than the main pour — under
  ~5 mm² of connected copper has no meaningful thermal mass;
- a pad that exists to spread heat (none on Board A or B);
- pads on a net that is only ever reflowed and never reworked — **this lane
  should not claim that exemption**, because the run-sheet's own failure
  handling is an iron.

**Relief (or neck) is required on:** every hole-centered GND pad (THT leads,
switch legs, button legs, buzzer leads), both wire pads, every wire via on
Board B, and every SMD GND pad — because rework happens.

**Adopted numbers.**

| item | value | derivation |
|---|---|---|
| thermal gap (relief ring width) | **0.40 mm** | the clearance law; §0 gives 0.080 mm kerf overlap. Never 0.5. |
| spoke width, drawn | **0.60 mm** | 0.40 mm delivered at the gate's worst centerline error; ≥0.5 track law |
| spoke count, hole-centered pads | **4**, at 45° | PCBWay "+ or x"; BestPCBs "four better than two"; pad registration under an iron |
| SMD GND pads | **single 0.6 mm neck ≥0.5 mm long**, zone connection *none* | a 0.6 spoke on a 0.9 mm pad edge is a solid connect |
| minimum delivered spoke | **0.40 mm** | starved-thermal bar, milled restatement (see checks list) |

**Cost of the change, quantified** (because `tools-layout.py:454` currently
chooses solid connect explicitly to save clearing work): a relief ring or a neck
adds a 0.4 mm-wide loop around each pad. By §0 that loop is *iso-only* work —
the clearing corn cannot and will not enter it, so **the clearing phase gains
zero material**. Cost is iso path length: ≈30 GND-connected pads × ≈4 mm
perimeter × 2 passes ≈ 240 mm at F500 ≈ **29 seconds**. That is the whole price
of the fix.

---

## §2 — Pad-to-plane and general copper clearance

**What the fab houses say.**

| rule | JLCPCB | PCBWay |
|---|---|---|
| min trace/space, 1 oz outer | 0.10 / 0.10 mm (4/4 mil) | 0.10 mm / 0.10 mm (4 mil) |
| pad to track | 0.10 mm | — |
| SMD pad to SMD pad, different nets | 0.15 mm | — |
| "safe default" clearance for a design | 0.20 mm (8 mil), IPC-2221 cited | 0.33 mm (13 mil) standard, 0.254 mm (10 mil) high-density |
| same-net track spacing | 0.25 mm | 0.254 mm |
| hatch grid width/spacing | 0.25 mm | ≥0.254 mm |

**Does it apply here? NOT APPLICABLE as a floor; REPLACED by a band.**

Etch-based minimum spacing answers "how narrow can a gap be before the etchant
fails to clear it". This lane's answer is 0.15 mm or less — far finer than any
fab floor, because the vee cuts a 0.28 mm kerf and two overlapping kerfs clear
anything up to 0.480 mm. The lane's real constraint is at the **other end**: a
gap can be *too wide to be cleared by iso and too narrow to admit the corn*.

**The rule to carry forward is therefore not a minimum, it is an exclusion:**

> Every non-copper gap in the artwork must be **≤ 0.480 mm** (iso consumes it)
> or **≥ 1.200 mm** (the corn clears it with Board B's margin). The interval
> **(0.480, 1.200)** is forbidden. 0.900–1.200 is tolerable only where the
> `clear opening` check is satisfied and the operator accepts the corn working
> at its bar.

The lane's existing 0.4 mm clearance law is the correct design value inside the
lower window and is adopted unchanged. The pour's own local clearance on Board
A is already 0.4 (`coupon.kicad_pcb` zone `connect_pads (clearance 0.4)`) ✓.
The thing that needs auditing is every *other* place a gap width is set —
thermal gaps, pour necks, keep-out rings, coupon-block spacings.

**Adopted:** clearance 0.40 mm (unchanged law) **plus** the forbidden-band
exclusion above as a new design rule.

---

## §3 — Annular ring

**What the fab houses say.**

- **JLCPCB capabilities:** PTH annular ring **≧0.20 mm** (recommends 0.25 mm+ on
  2-layer 1 oz) — but **NPTH pad annular ring ≧0.45 mm**.
- **JLCPCB** *Design Rule Check* article: "safe practice" 0.15 mm (6 mil),
  advanced 0.075 mm; IPC-6012 Class 2 floor 0.05 mm.
- **PCBWay capabilities:** min annular ring (PTH) **0.15 mm (6 mil)**; PTH hole
  tolerance ±0.08 mm, NPTH ±0.05 mm.
- **PCBWay** *PCB Design Guidelines*: standard THT pad "disc diameter 0.6 mm
  larger than hole" — i.e. **0.30 mm annular**; "mounting aperture 0.2–0.4 mm
  larger than component pin".
- **JLCPCB** *Annular Rings*: `W = (OD − D)/2`; the failure modes are tangency,
  **breakout**, **pad lifting** and rupture; teardrops add copper strength.

**Does it apply here? APPLIES, and the fab houses' own numbers vindicate the
lane's law — quote the NPTH row at anyone who calls ≥0.6 excessive.**

The single most useful number in this entire research is JLCPCB's split:
**0.20 mm when the hole is plated, 0.45 mm when it is not** — a 2.25× penalty
imposed by the same factory on the same laminate, purely for the absence of
plating. Every hole in this lane is an NPTH. The lane then stacks two more
liabilities the fab does not have: the hole is **helically bored with a 0.8 mm
corn**, not drilled, so the rim sees a side-cutting tool rather than a point;
and the pad is loaded by an **iron and a hand** during assembly, which is
exactly JLCPCB's "pad lifting" failure mode.

Lane law ≥0.60 mm (Board A) and ≥0.70 mm (Board B) sit **1.33× and 1.56× above
JLCPCB's own NPTH figure** and 2× above PCBWay's standard THT pad rule. Keep
both. No change proposed.

Board B's flip gauges (0.35 annulus declared 0.30) remain the named exception —
and note they now also fall *below* JLCPCB's NPTH number, which is the correct
reading of a deliberately sensitive gauge: it is built to fail early.

**Adopted:** annular ≥0.60 mm single-sided, ≥0.70 mm double-sided (unchanged),
now cited against JLCPCB NPTH ≧0.45 mm. Pad diameter from the ring, not the
reverse: `pad_dia = hole_dia + 2×0.60` minimum.

---

## §4 — Drill-to-copper, hole-to-hole, and hole sizing

**What the fab houses say.**

- **JLCPCB capabilities:** PTH-to-track **0.28 mm**; NPTH-to-track **0.20 mm**;
  pad hole-to-hole **0.45 mm**; via hole-to-hole 0.20 mm; inner-layer PTH pad
  hole-to-copper 0.30 mm.
- **JLCPCB** *Common Design Issues*: "Leave at least 2 mm clearance between NPTH
  and copper traces."
- **PCBWay capabilities:** hole-to-copper (inner) ≥7 mil; drill range
  0.15–6.0 mm; NPTH tolerance ±0.05 mm.
- **PCBWay** *PCB Design Guidelines*: mounting aperture **0.2–0.4 mm larger
  than the component pin**.
- **JLCPCB** *Panelization*: drill hits too close to the board edge are a DFM
  flag (their DFM tool checks it).

**Does it apply here? ADAPTED — the numbers change owner.**

- **Hole-to-foreign-copper.** JLCPCB's 0.20/0.28 mm are *registration* numbers
  (drill wander ±0.08 mm against a photo-imaged copper layer). This lane's
  registration error is the machine's — DESIGN.md puts pin-to-hole slop at
  ~0.02–0.04 mm, below the simulation pixel, and Board B is built to measure it.
  So the fab's floor is not the binding constraint. What *is* binding: the
  non-copper region around a hole is the hole disc **plus** its clearance ring,
  and that combined region must clear the §0 band. For any bore in the lane's
  classes (0.8–3.4 mm) with 0.4 mm of clearance the region is ≥1.6 mm wide, so
  the corn clears it and there is no sliver. **The 0.4 mm clearance law is
  therefore the correct hole-to-copper number and no separate rule is needed.**
  Board A's `min_hole_clearance 0.25` in `coupon.kicad_pro` is *looser* than the
  law it sits next to — harmless today (nothing on the board is at 0.25) but
  worth tightening so DRC and the SPEC agree.
- **JLCPCB's 2 mm NPTH-to-copper does not transfer.** It guards against router
  tear-out and plating chemistry around unplated features. The lane's real
  concern at an M3 hole is **electrical**: an M3 screw head shorting the pour.
  Derive it instead: keep-out radius ≥ `head_dia/2 + 0.4`. For an M3 pan head
  (Ø5.6 mm) that is **3.20 mm radius (6.40 mm diameter)**. Board A's octagonal
  `m3_keepout` rule areas measure 6.6 mm across the flats → 3.30 mm radial,
  which clears by 0.10 mm. Tight but legal; state the number so it stops being
  accidental.
- **Hole-to-hole.** JLCPCB pad hole-to-hole 0.45 mm; Board A's
  `min_hole_to_hole 0.50` already exceeds it ✓. Milled reading: two 0.9 mm bores
  0.5 mm apart leave 0.5 mm of laminate — fine for a 0.8 mm corn boring each in
  turn. Keep 0.50.
- **Hole sizing from the lead.** Adopt **PCBWay's +0.2 to +0.4 mm** rule, with
  +0.3 mm nominal, then **round up to the nearest available bore class**
  (Board A straight drills 0.9/1.1/1.2/1.5, bores 3.4; Board B all classes
  helically bored, Ø1.0 minimum because the 0.8 corn cannot bore its own
  diameter). This is the rule that keeps hand insertion possible on an unplated
  board without turning the pad into a washer.

**Adopted:** hole-to-copper 0.40 mm (= the clearance law); hole-to-hole
0.50 mm; hole Ø = lead Ø + 0.30 mm nominal (range +0.20…+0.40, PCBWay), rounded
up to a bore class; M3 copper keep-out radius ≥3.20 mm.

---

## §5 — Solder-mask expansion, dams and slivers

**What the fab houses say.**

- **JLCPCB capabilities:** solder mask expansion **1:1**; solder-mask bridge
  minimum spacing **0.10 mm** for 1 oz green/red/yellow/blue/purple, **0.13 mm
  for black or WHITE**, 0.20 mm at 2 oz.
- **JLCPCB** *Basic Design of Solder Mask*: openings should be "generally about
  0.1–0.2 mm larger overall… equivalent to **0.05–0.1 mm expansion on each
  side**"; too little clearance causes "solder mask encroachment onto the pad
  and… poor solder wetting", too much "unnecessarily expose[s] copper" and
  invites oxidation.
- **JLCPCB** *Mastering PCB Footprints*: solder mask expansion **0.05–0.1 mm
  per side**; solder-mask dam **0.1 mm**.
- **JLCPCB** *Why Solder Pad Design Matters*: minimum solder-mask dam **75 µm**.
- **JLCPCB** *Design Rule Check*: solder-mask sliver safe **0.10 mm** (4 mil),
  advanced 0.05 mm, IPC-7525 cited.
- **PCBWay capabilities:** minimum mask opening ≥2 mil; minimum mask bridge
  **4 mil (0.10 mm)** for copper <2 oz.

**Does it apply here? ADAPTED — and the SIGN of the expansion inverts. This is
the most dangerous rule in the document to copy carelessly.**

A fab images the mask photographically and needs the opening *bigger* than the
pad so that registration error never leaves resist on the land. This lane has
no photo-imaging: the mask is squeegeed over the whole face, UV-cured, and then
**opened mechanically by a 0.3 mm spring tool** that laps inside the region the
B.Mask gerber defines, **deflated** by the job's `phases.scrub.offset`. If the
opening were inflated the fab way, the spring tip would ride off the pad's
plateau, into the isolation groove — which is 0.28 mm wide and bites 0.04 mm
into the pad edge — and lever the pad or the neighbouring trace off the
laminate. That is the mechanism the mask guide documents and the ancestor of
`SCRUB_PLATEAU_MIN = 0.05`.

So:

- **Mask expansion in KiCad must be exactly 0.** Board A already sets
  `solder_mask_to_copper_clearance: 0.0` — aperture == pad outline — and lets
  CAM apply the deflate. **Adopt 0 as a law and assert it**, because a KiCad
  default or an imported footprint carrying +0.05 mm (the JLCPCB number!) would
  silently eat 0.05 of the plateau margin whose bar is 0.05.
- **The effective opening is pad − 0.15 mm per side** (`phases.scrub.offset =
  0.15` in `coupon.toml`; DESIGN.md measures the delivered deflate at
  0.145 inner / 0.149 outer). Note for the record: the prose figure "deflate
  −0.10" in both board SPECs and the mask guide is the **retired Fusion
  pipeline's** `Inflate(−0.10)` on pcbnew pad polygons; the live FlatCAM
  pipeline deflates the mask aperture by **0.15**. Quote 0.15 for what the
  process does; quote −0.10 only when citing the historical derivation of the
  0.05 plateau bar.
- **The deflate is the registration budget, and it is sized.** DESIGN.md's
  false-FAIL incident measured a **0.075 mm** mask-registration error. The
  0.15 mm deflate covers that 0.075 with the 0.05 plateau bar on top and
  0.025 spare. That is why the number is 0.15 and not 0.10.
- **Minimum solderable pad size falls straight out of this** — a rule no fab
  publishes because no fab opens mask with a tool:

  ```
  min pad dimension = 2·(scrub_r + SCRUB_WINDOW_MIN) + 2·deflate
                    = 2·(0.150 + 0.050) + 2·(0.150)
                    = 0.700 mm
  ```

  A pad narrower than **0.70 mm** cannot be scrubbed at all: the deflated
  aperture is thinner than the spring tip, FlatCAM `paint` emits nothing, and —
  because `scrub coverage` is **deliberately un-barred** (DESIGN.md, twice) —
  the gate says PASS on a pad that will still be under mask at assembly.
  **Recommend 0.80 mm** as the design floor (leaves 0.10 mm of window per side
  against the 0.05 bar). 0603 hand-solder pads (~0.9 mm) clear it; **0402 does
  not, at any pad variant** — which is now a *process* reason, not a taste
  reason, for the boards' 0603 floor.
- **Mask dams are a non-issue here, but check the reasoning.** The untouched
  mask band between two scrubbed apertures is `gap + 2·deflate` = 0.40 + 0.30 =
  **0.70 mm** at the lane's minimum clearance — 5.4× JLCPCB's white-mask floor
  of 0.13 mm (white is the lane's mask colour, so 0.13 is the row that applies,
  not 0.10). Mask slivers likewise. **The lane's mask risk is the opposite of
  the fab's**: not resist bridging between pads, but hand-squeegeed film left
  *on* a pad. The scrub phase is the mitigation and the loupe is the gauge.

**Adopted:** mask expansion **0.00 mm** in KiCad (aperture == pad); process
deflate **0.15 mm** per side (CAM); minimum solderable pad dimension **0.70 mm
absolute / 0.80 mm design floor**; mask dam — informational only, 0.70 mm
delivered vs JLCPCB's 0.13 mm white-mask floor.

---

## §6 — Silkscreen: stroke, height, and clearance to pads

**What the fab houses say.**

| rule | JLCPCB | PCBWay |
|---|---|---|
| min stroke / line width | **≥0.15 mm** (6 mil absolute, 8 mil recommended); 0.10 mm high-precision; hollow font ≥0.2 mm | **0.15 mm** |
| min character height | **1.0 mm (40 mil)** standard font; **0.8 mm** high-precision floor; hollow font ≥1.5 mm | **0.8 mm** |
| stroke : height ratio | **1:6** target, 1:5–1:6 range | 1:5 |
| pad to silkscreen | **0.15 mm** minimum, **≥0.25 mm recommended** "for optimal yield" | — |
| clearance between character strokes | >0.15 mm minimum, ≥0.2 mm recommended | — |
| legend on exposed copper | "**will be directly removed**, as silkscreen ink in these areas can cause soldering issues" | — |

**Does it apply here? APPLIES with one adaptation, and the lane's law already
beats the recommendation.**

- **Silk-to-pad ≥0.30 mm** (grammar floor, gate-checked per densified firing
  segment, clipped rather than dropped at re-emission) is **above JLCPCB's
  0.25 mm recommendation and 2× its 0.15 mm minimum.** Keep it; no change.
- **JLCPCB's "legend on exposed copper is removed" is reproduced mechanically
  here, for free.** The run order is mask → **silk laser** → **scrub**, so any
  legend inside a mask aperture is physically scrubbed off after it is written.
  The lane therefore *cannot* ship ink on a pad. What it can ship is **lost
  information** — a pin-1 dot or a cathode tick that was drawn too close and
  came off in the scrub. On Board B the front legend is load-bearing (twelve
  cathode ticks decide whether the ring works), so the 0.30 mm rule is protecting
  function, not cosmetics. Say so on the run sheet.
- **Text height: adopt JLCPCB's 1.0 mm standard-font floor as the lane's floor**
  until Board A's silk ladder says otherwise. Board A's ladder currently tests
  1.2 / 1.5 / 2.0 mm; the fab floor (1.0) and the fab's absolute (0.8) are both
  *below* the ladder's smallest rung, so the ladder cannot currently answer
  "is the fab floor legible on a lasered white mask?". `coupon.kicad_pro` sets
  `min_text_height 0.8` — the same as PCBWay's floor, which is legal but
  untested here. **Add a 1.0 mm rung** (and optionally 0.8 mm) to the ladder and
  the answer arrives with the first board.
- **Stroke: Makera's 0.25 mm floor governs, not the fab's 0.15 mm** — the laser
  cannot draw finer, so the fab minimum is unreachable and irrelevant. The
  interesting consequence is the ratio: Board B's 1.5 mm / 0.25 mm legend is
  **exactly 1:6**, JLCPCB's target. A 1.2 mm rung at 0.25 mm stroke is 1:4.8 —
  outside JLCPCB's 1:5–1:6 range, i.e. slightly fat glyphs. **1.5 mm is the
  right default height for this lane**, because it is the smallest height whose
  1:6 stroke the laser can actually draw.
- **Inter-stroke clearance: adopt ≥0.30 mm** (one stroke width) rather than
  JLCPCB's 0.20 mm recommendation, because the failure mode here is dose bloom
  filling a glyph's counters, not ink spread. The silk ladder should read this
  too.

**Adopted:** silk-to-pad ≥0.30 mm (unchanged law); text height ≥1.0 mm hard
floor / **1.5 mm default**; stroke 0.25 mm (Makera floor); ratio target 1:5–1:6
(JLCPCB); inter-stroke ≥0.30 mm.

---

## §7 — Copper to board edge

**What the fab houses say.**

- **JLCPCB capabilities:** copper clearance from **routed** edges **≧0.20 mm**;
  from **V-cut** edges **≧0.40 mm**.
- **JLCPCB** *Design Rule Check*: 0.25 mm minimum to the board edge, 0.20 mm
  "advanced", IPC-2221 cited.
- **PCBWay capabilities:** line-to-board-edge (CNC routed) **0.25 mm** normal,
  0.20 mm medium difficulty.
- **JLCPCB** *Panelization*: keep copper ≥0.4 mm from a V-cut centreline;
  components/traces **3–5 mm** from any separation line.

**Does it apply here? APPLIES; the lane's 0.40 mm law already matches JLCPCB's
strictest row (the V-cut one) and exceeds every routed-edge figure.**

The milled derivation is different and worth recording: the outline is cut by a
Ø1.0 corn riding the outline ink at tool radius, at full depth, with tabs. So
copper *outside* the outline is removed by the cutter itself, and the 0.40 mm
law is protecting against (i) the cutter's own positional error, (ii) laminate
splintering at the cut, and (iii) the hand file that follows the snap. The
fab's concern — interlayer shorts and edge plating — does not exist here; the
number survives on new grounds. No change.

The interesting borrow from this row is not the edge number but the **3–5 mm
separation-line rule**, which the lane has only half-adopted: see §11.

**Adopted:** copper-to-edge ≥0.40 mm (unchanged law).

---

## §8 — Acid traps and acute angles

**What the fab houses say.**

- **JLCPCB's DFM tool** flags "**acid traps** in acute-angle trace junctions"
  and "too-narrow **copper slivers** that cannot be etched reliably" as issues
  beyond ordinary DRC.
- **JLCPCB** *PCB Design Rules and Guidelines*: avoid 90° trace corners, use
  45°; general practice is to keep every copper-to-copper interior angle ≥90°.

**Does it apply here? The MECHANISM is not applicable; the RULE survives with a
completely new derivation, and it turns out to be one of the two sources of the
operator's sliver problem.**

There is no etchant, so nothing pools and nothing over-etches. But run the §0
arithmetic on a wedge-shaped non-copper region — which is exactly what an acute
angle between two copper edges produces:

```
a V-shaped void of half-angle θ has width w(L) = 2·L·tan θ at distance L from the apex
it is iso-cleared where w ≤ 0.480 and corn-cleared where w ≥ 0.900
⇒ a standing sliver occupies the strip between those two, of length

      L_sliver = (0.900 − 0.480) / (2·tan θ) = 0.210 / tan θ

   θ = 45° (a 90° corner)  → 0.21 mm of sliver
   θ = 30° (a 60° corner)  → 0.36 mm
   θ = 15° (a 30° corner)  → 0.78 mm
   θ =  5° (a 10° corner)  → 2.40 mm
```

**Every tapering void produces a sliver; the acute angle decides how LONG it
is.** At 90° the residue is a 0.21 mm speck, bound at both ends and mechanically
uninteresting. At 30° it is a 0.78 mm needle. At a few degrees — the shape you
get where a pour necks down to nothing against a trace, or where two
serpentine folds converge — it is a millimetres-long copper whisker attached
to laminate by adhesive alone, sitting between two live nets. That is the other
half of pain point (a), and it is why the fab houses' rule should be kept even
though its stated reason evaporated.

**Adopted:** no copper-to-copper interior angle below **90°**; route at 45°/90°
only; where a pour would taper to a point, **truncate it** — end the pour on a
flat ≥1.2 mm wide or let the region close at ≤0.48 mm, never let it feather.
The same applies to the tips of the coupon serpentine folds and to any pour
pocket entrance.

Note on JLCPCB's *copper* sliver check: their number ("too narrow to etch") is
not transferable, but the concept maps precisely onto the residual-copper census
proposed in the checks list.

---

## §9 — Teardrops

**What the fab houses say.**

- **JLCPCB** *PCB Teardrop You Should Know*: add teardrops "for through-holes
  where the trace-to-pad ratio is small", on high-density boards "where the
  annular ring around vias needs to be preserved", on flex "to reduce the stress
  where the trace joins the pad", under BGAs, and "when traces exit the pad".
  The one number: "**No need to add teardrops when the conductor is more than
  20 mils**" (0.508 mm).
- **JLCPCB** *Annular Rings*: teardrops add copper strength at the junction.

**Does it apply here? ADAPTED — the reason changes from etch/registration to
mechanical, and it becomes *more* attractive, not less.**

At a fab, teardrops defend the annular ring against drill wander and etch
undercut. Neither exists here (bore position ~±0.02–0.04 mm; no etch). But two
milled facts point the same direction:

- The lane's dominant THT failure mode is **pad lift** on an unplated pad
  (JLCPCB's own NPTH reasoning, §3), and a lifting pad tears the trace off at
  exactly the junction a teardrop reinforces.
- The iso vee **bites 0.04 mm off each edge**, so a 0.50 mm track meeting a pad
  is delivered at 0.42 mm nominal and as little as 0.30 mm at the gate's worst
  allowed centerline error (§0). The junction is the thinnest, most stressed
  copper on the board. A teardrop restores it.

And the cost is negative: a teardrop *adds* retained copper, so the clearing
phase does less work, not more.

The one caution is the §0 band: a teardrop widens copper and therefore narrows
the void beside it. A junction whose neighbouring gap was 0.9–1.2 mm can be
pushed into the forbidden band by a teardrop. So teardrops must be added
*before* the clearance audit, not after.

JLCPCB's 20 mil (0.508 mm) threshold lands almost exactly on the lane's 0.5 mm
track floor — read literally, **every conductor at the lane's minimum width is
in the "add teardrops" region**, and the SPEC's 0.6 mm signal / 0.8–1.2 mm power
widths are above it. That is a clean, quotable justification for treating 0.5 mm
as a floor for characterization features rather than a working width.

**Adopted:** teardrops **on** at every THT pad-to-track junction; optional on
SMD pad junctions; not needed on conductors >0.508 mm (JLCPCB) except where a
THT pad is involved. Teardrop length ≤ 1.0 × pad radius. **Re-run the clearance
and forbidden-band audit after enabling them.**

---

## §10 — Hatched vs solid copper pours

**What the fab houses say.**

- **JLCPCB** *PCB Copper Pour Basics*: solid pours "increase current capacity
  and provide shielding, but may cause warping and copper detachment when
  passed through wave soldering"; hatched pours are "mainly used for shielding
  and don't have very high current-carrying ability", can raise EMI when segment
  lengths approach the operating frequency, and "can cause the film to crack
  with dry film processes".
- **JLCPCB** *Character/PCB Art* guidance: "when filling large areas with
  copper, avoid using small hatched grids as this can severely impact
  production."
- **JLCPCB capabilities:** hatched grid width/spacing **0.25 mm**.
  **PCBWay:** grid line width/spacing ≥0.254 mm.

**Does it apply here? NOT ADOPTED — but for the opposite reason than at a fab,
and the trade is worth writing down because hatching looks like a tempting fix
for §1.**

At a fab, solid is the default and hatching is nearly free. Here the economics
invert: **a solid pour is the cheapest possible artwork** (retained copper is
copper the clearing phase never has to remove — which is exactly why both board
SPECs mandate a GND pour and one `filled_polygon` per side), and **a hatched
pour is the most expensive** (every hatch line is another pair of isolation
passes; a 0.25 mm hatch gap is ≤0.480 so iso *can* clear it, but the path length
scales with pour area divided by hatch pitch, and every one of those grooves is
another place §0's arithmetic must hold).

Hatching *would* genuinely help hand soldering — roughly halving the copper
cross-section around a joint. But it is the wrong instrument: it pays a global
cost in path length and sliver surface area to fix a problem that exists only
at ~30 specific pads, where §1's per-pad relief or neck fixes it locally for
29 seconds of iso time.

**Adopted:** solid pours, one fragment per side (unchanged law). Hatching is
recorded as available and rejected, with the reason, so the question does not
need re-litigating. If it is ever revisited: hatch gap ≤0.48 mm (never in the
band), hatch line ≥0.5 mm (track law), and expect the iso program to grow by
roughly `pour_area / hatch_pitch` of path.

---

## §11 — Panelization, mouse bites, and tabs

**What the fab houses say (JLCPCB, *The Ultimate Guide to PCB
Panelization*).** Mouse bites: hole diameter **0.60 mm**, **5–8 holes per set**,
edge-to-edge spacing **0.35–0.40 mm (min 0.30 mm)**, **≥2 symmetric sets**,
additional sets every **50–60 mm** for longer boards, spacing between boards
**1.6–2.0 mm (min 1.2 mm)**, and the separated edge is serrated and "may need
sanding". V-cut: 25°, ≥0.40 mm copper clearance from the centreline, tolerance
±0.40 mm, min connecting edge 3 mm. Both methods: keep components and traces
**3–5 mm** from any separation line. Process rails **5–10 mm** wide.

**Does it apply here? PARTLY ADOPTED — the lane already borrows the structure;
three specifics are worth taking and one is worth considering.**

The lane does not panelize (one board per blank), but the **cutout-with-tabs**
step is structurally identical to depanelization: a hand snap along a line, with
tabs holding the part until then. The gate already encodes the shape of
JLCPCB's rules — `TAB_MIN_COUNT = 2` matches "≥2 symmetric sets",
`TAB_MATERIAL_MIN = 1.0` mm and Board A's `gaps 4 / gapsize 1.5` are inside
JLCPCB's per-board tab budget, and Board B's `tab-zone copper keep-out 1.0 mm`
is the copper half of JLCPCB's 3–5 mm rule.

What to take:

1. **"≥2 *symmetric* sets" — adopt the symmetry, explicitly.** The check counts
   tabs; it does not require them to be balanced. Four tabs clustered on one
   edge satisfies the census and snaps badly. Board A's perimeter is
   2(55+40) = 190 mm, so JLCPCB's "extra set every 50–60 mm" gives 3–4 sets:
   **4 tabs, one per edge, at each edge's midpoint** is exactly right and should
   be stated as the placement rule rather than left to chance.
2. **"3–5 mm from any separation line" — adopt 3 mm, for part BODIES.** The
   lane's 1.0 mm tab-zone rule covers *copper*; it says nothing about a 5 mm LED
   or a buzzer sitting where the board flexes during the snap. Adopt **≥3.0 mm
   from any part body to any tab**, and keep the 1.0 mm copper number.
3. **"The separated edge is serrated and may need sanding" — adopt the run-sheet
   step.** Board B's sheet already says "snap by hand, file"; Board A's should
   say it too, next to the copper-to-edge number that the file is about to
   approach.
4. **Perforated tabs — worth considering, flagged as a change.** JLCPCB's mouse
   bite is a *perforated* tab, not a solid one, because perforation controls
   where the fracture goes. The lane's tab is 1.5 mm of solid laminate. One
   Ø0.8 mm bore centred in a 1.5 mm tab leaves 0.35 mm of material each side —
   it would snap cleanly and file flat. But it interacts with two checks:
   `cutout tab census` measures uncut *path* length (a perforation does not
   reduce it, so `TAB_MATERIAL_MIN` would still read 1.5 while the real material
   is 0.7), and the bore must appear in the Excellon schedule or `stray bores`
   fires. **Not adopted; recorded as a candidate** requiring a tab-census change
   and an operator decision.

Not applicable: V-cut (no scoring blade), process rails and fiducials (no
pick-and-place — the lane's registration analogue is the pin holes and the flip
gauges), board-to-board spacing (one board per blank). Note that the blank
*does* function as JLCPCB's process rail: 150×100 mm around a 55×40 mm board
leaves waste to clamp and to hold the pin holes.

**Adopted:** ≥2 tabs, symmetric, one per edge, extra set per 50–60 mm of
perimeter (JLCPCB); tab-zone copper keep-out 1.0 mm (unchanged Board B law);
**part body ≥3.0 mm from any tab** (new, from JLCPCB's 3–5 mm); file the snap
edge (run-sheet step).

---

## §12 — Hand-soldering-friendly footprints and placement

**What the fab houses say.**

- **JLCPCB** *Mastering PCB Footprints*: follow **IPC-7351**, which defines
  three density levels — **Least (A) / Nominal (B) / Most (C)** — and gives
  formulas for pad width, gap and overall length "accounting for component
  tolerances, solder fillet goals, and placement accuracy". Sample land
  patterns: 0402 pad 0.50–0.60 × 0.55–0.70 mm; SOIC-8 gull wing pad
  0.60–0.65 × 1.80–2.00 mm; QFN 0.5 mm pitch 0.25–0.30 × 0.60–0.80 mm.
- **JLCPCB** *Why Solder Pad Design Matters*: Level A is the "largest" pad set,
  for military/industrial; Level C the smallest, for mobile/wearable.
- **JLCPCB** (prototype guidance): prioritise hand-solder-friendly layouts with
  **larger pads**; automated assembly tolerates smaller, denser pads.
- **Paste apertures** (JLCPCB *Mastering PCB Footprints*): reduce to **80–90 %**
  for 0402, **60–70 % segmented** for QFN, **5–10 %** for QFP; stencil
  positioning accuracy ±0.003 mm.
- **PCBWay** *PCB Design Guidelines*: typical THT pad/hole pairs 1.6/0.8 mm for
  passives and ICs, 1.8/1.0 mm for connectors and diodes.

**Does it apply here? APPLIES, and it vindicates a SPEC row that currently reads
as taste.**

Both boards' process tables say "hand-solder variants where they exist". That
row is now traceable to IPC-7351 **density level A** and to JLCPCB's own
prototype advice — it is the standard's own answer for this assembly method, not
a preference. Keep it and cite it.

Two lane-specific additions:

- **The pad floor is 0.70 mm (§5), not IPC's smallest.** IPC-7351 level C and
  JLCPCB's 0402 land pattern (0.50–0.60 mm wide) are *unscrubbable* in this lane.
  The scrub tool sets the floor, and it sits above the fab's minimum — a rare
  case where the desktop process is the stricter one.
- **Iron access is a placement rule, and the lane already has half of it.**
  Board B specifies ≥0.6 mm between tangential 1206 neighbours and via
  keep-outs of 1.5 mm (SMD body) / 2.0 mm (THT body) / 3.0 mm (board edge).
  Neither fab publishes a hand-rework spacing number, so this is derived from the
  bench, not borrowed: **≥0.5 mm body-to-body for reflowed neighbours, ≥1.0 mm
  where an iron tip must reach one specific pad, ≥2.0 mm from a THT body to any
  joint that must be made after that body is seated.** Board B's numbers already
  satisfy this; state it once so Board A's relayout inherits it.

**Paste** (adjacent scope, one line): the fabs' aperture *reductions* are
fine-pitch measures. The lane's finest pitch is SOIC-8 at 1.27 mm and its
stencil is hand-registered, so **1:1 apertures at 0603 and above** is correct
and no reduction should be copied in. Board B's "no aperture on vias, THT pads
or ISP pads" is already stricter than any fab rule and is gate-checked
(`paste clear of the hole schedule`).

**Adopted:** IPC-7351 **level A / hand-solder** footprint variants (JLCPCB);
minimum pad dimension 0.70 mm absolute / 0.80 mm design (§5); paste apertures
1:1; placement 0.5 / 1.0 / 2.0 mm per the access rule above.

---

## §13 — Fab rules that do not transfer, with reasons

One line each, so nobody has to re-check them:

| fab rule | house & number | why it does not transfer |
|---|---|---|
| trace-width tolerance ±20 % | JLCPCB | that is etch bias; here the error is kerf placement, and the gate models it (`ISO_CENTERLINE_TOL 0.06`) |
| copper balancing across layers | JLCPCB (*Copper Balancing*) | no lamination press, no plating bath, no warpage from copper imbalance; a single-sided milled blank has no balance to keep |
| via-in-pad / POFV, filled vias (Ø≤0.5 mm, ≥0.35 mm from other openings) | JLCPCB | there is no plating and no fill; Board B's vias are wire and two hand joints |
| castellated holes (Ø≥0.5 mm, edge-to-edge ≥0.3 mm) | JLCPCB, PCBWay | the 0.8 mm corn chews them — this is the "castellation-chewing incident" behind Board B's ≥1.2 mm clearing floor |
| V-cut (25°, ±0.4 mm, ≥0.4 mm from centreline) | JLCPCB | no scoring blade; tabs are the lane's separation method (§11) |
| plated/non-plated slots (0.35/0.5/1.0 mm minimums) | JLCPCB | any slot here is a bored pocket; the corn's 0.9 mm channel is the real floor |
| fiducials on process rails | JLCPCB | no pick-and-place; the registration analogue is the Ø2.0 pin holes and Board B's flip gauges |
| min board size 3×3 mm (JLCPCB) / ≥20 mm (PCBWay) | both | tab and clamp geometry, not the fab's handling, sets the lane's floor |
| impedance targets, 3W rule, via stubs >5 Gbps | JLCPCB, PCBWay | a 2 Hz heartbeat and a 1 kHz LED scan; no controlled impedance on a single-sided milled board |
| solder-mask bridges between fine-pitch IC pads | JLCPCB (0.4 mm pitch) | the lane's finest pitch is 1.27 mm and its mask is squeegeed, not imaged |
| NPTH-to-copper 2 mm | JLCPCB | guards router tear-out and plating chemistry; replaced by a derived screw-head keep-out (§4) |

---

## Adopted numbers — the table

Rows marked **=** are existing lane law, now cited against a fab number.
Rows marked **+** are new. Rows marked **!** are proposed changes to a currently
drawn value and need the operator (see the next section).

| # | rule | lane value | fab reference |
|---|---|---|---|
| = | copper clearance | **0.40 mm** | JLCPCB pad-to-track 0.10, "safe" 0.20; PCBWay 0.254–0.33 — all met with room |
| **+** | **forbidden gap band** | **no non-copper gap in (0.480, 1.200) mm** | none — derived from kerf (§0). Replaces the fab concept of a spacing *minimum* |
| = | track width | 0.50 mm floor / 0.60 signal / 0.80–1.20 power | JLCPCB signal 0.15, power 0.30–0.50; JLCPCB teardrop threshold 0.508 says 0.50 is a floor, not a working width |
| **!** | thermal relief gap | **0.40 mm** (drawn today: 0.50) | BestPCBs 0.20–0.50; the top of that range is forbidden here (§0) |
| **!** | thermal spoke width | **0.60 mm** (drawn today: 0.80) | BestPCBs 0.20–0.50; floored here by the 0.50 track law + 0.20 mm of kerf tolerance |
| **+** | thermal spoke count, hole-centered pads | **4** at 45° | PCBWay "+ or x"; BestPCBs "four better than two" |
| **!** | SMD GND pad connection | **single 0.60 mm neck, ≥0.50 mm long** (today: solid connect) | PCBWay/JLCPCB relief rationale; a 0.6 spoke on a 0.9 mm pad is a solid connect |
| **+** | minimum delivered spoke | **0.40 mm** | milled restatement of JLCPCB DFM's "starved thermal" |
| = | THT annular ring | **≥0.60 mm** (A) / **≥0.70 mm** (B) | **JLCPCB NPTH ≧0.45 mm** vs PTH ≧0.20 mm; PCBWay THT 0.30 mm |
| **+** | hole Ø from lead | **lead + 0.30 mm** (range +0.20…+0.40), rounded up to a bore class | PCBWay "0.2–0.4 mm larger than component pin" |
| = | hole-to-hole | ≥0.50 mm | JLCPCB pad hole-to-hole 0.45 mm |
| **+** | hole rim to foreign copper | **0.40 mm** (= the clearance law) | JLCPCB NPTH-to-track 0.20 mm (registration-driven, not binding here) |
| **+** | M3 copper keep-out radius | **≥3.20 mm** (= head Ø5.6/2 + 0.4) | replaces JLCPCB's non-transferable 2 mm NPTH rule |
| **+** | solder-mask expansion in KiCad | **0.00 mm** — aperture == pad, asserted | **inverts** JLCPCB/PCBWay +0.05…+0.10 mm per side |
| **+** | process mask deflate | **0.15 mm** per side (CAM) | no fab analogue; = 0.075 registration incident + 0.05 plateau bar + 0.025 |
| **+** | minimum solderable pad dimension | **0.70 mm** absolute / **0.80 mm** design | none — derived from scrub tool 0.3 + window 0.05 + deflate 0.15. Stricter than IPC-7351 level C / JLCPCB 0402 |
| = | silk to solderable pad | **≥0.30 mm** | JLCPCB min 0.15, **recommended 0.25** — law already exceeds it |
| **!** | silk text height | **≥1.00 mm floor, 1.50 mm default** (DRU floor today: 0.80) | JLCPCB standard font 1.0 mm (40 mil), high-precision floor 0.8; PCBWay 0.8 |
| = | silk stroke | 0.25 mm (Makera floor) | JLCPCB ≥0.15; unreachable here, so the machine governs |
| **+** | silk stroke:height ratio | **1:5 – 1:6** (1.5/0.25 = exactly 1:6) | JLCPCB 1:6 target; PCBWay 1:5 |
| **+** | silk inter-stroke clearance | **≥0.30 mm** | JLCPCB >0.15 min / ≥0.2 rec; raised for dose bloom |
| = | copper to board edge | **0.40 mm** | JLCPCB routed ≥0.20 / V-cut ≥0.40; PCBWay 0.25 |
| **+** | minimum copper interior angle | **≥90°** | JLCPCB acid-trap/acute-angle rule, re-derived: sliver length = 0.210/tan θ |
| **+** | teardrops | **on at THT pad-to-track junctions**; length ≤1.0 × pad radius | JLCPCB "no need above 20 mil (0.508)" — every 0.5 mm conductor is inside their rule |
| = | pours | solid, one fragment per side | JLCPCB solid-vs-hatched trade, inverted here (§10) |
| = | tab count | ≥2, **symmetric, one per edge**, +1 set per 50–60 mm perimeter | JLCPCB "≥2 symmetric sets", "every 50–60 mm" |
| = | tab-zone copper keep-out | 1.00 mm | copper half of JLCPCB's 3–5 mm separation-line rule |
| **+** | part body to tab | **≥3.00 mm** | JLCPCB "3–5 mm from separation lines" |
| **+** | footprint density level | **IPC-7351 level A (hand solder)** | JLCPCB footprint guide + prototype guidance |
| **+** | placement for iron access | 0.50 body-to-body / 1.00 iron-to-pad / 2.00 THT-body-to-later-joint | no fab number exists; bench-derived, matches Board B |
| **+** | paste apertures | **1:1** at 0603 and above | JLCPCB reductions are fine-pitch measures; lane's finest pitch is 1.27 mm |

---

## Proposed changes — operator decision required

None of these contradicts an incident-traced law. Each is a currently-drawn
value or a code comment that this research disagrees with, stated loudly rather
than edited quietly.

1. **`tools-layout.py:521` — thermal relief gap 0.50 → 0.40 mm.** The 0.50 value
   sits 0.020 mm into the forbidden band (§0), so today every relieved THT pad
   on Board A is ringed by a 0.020 mm standing copper hair. This is a *direct
   instance* of pain point (a), created by a value chosen before the kerf
   arithmetic was written down. **Highest-value single change in this file.**
2. **`tools-layout.py:521` — thermal spoke width 0.80 → 0.60 mm.** 0.80 is not
   wrong, just over-connected: four 0.80 spokes deliver 2.88 mm of copper into
   the joint versus 2.08 mm at 0.60, and ampacity needs neither. Reducing it is
   the second half of the hand-soldering fix. (Do **not** go to 0.50: worst-case
   delivery is 0.30 mm.)
3. **`tools-layout.py:454-455` — reverse the SMD GND solid-connect decision.**
   The code comment reads "milled board: solid connect SMD GND pads (less
   clearing, no starved thermals; THT keep reliefs)". Both stated reasons are
   answerable: the clearing phase gains *nothing* from a 0.4 mm relief ring
   (§0 — the corn cannot enter it), total cost is ≈29 s of iso path, and a
   routed neck cannot starve because it is a netlisted track. The counter-
   evidence is the operator's own report that these joints are miserable. This
   comment is a reasoned trade, **not** incident-traced law — but it is an
   explicit decision in the code and reversing it is the operator's call.
4. **`coupon.kicad_pro` — `min_text_height 0.80 → 1.00 mm`** to match JLCPCB's
   standard-font floor, and **add a 1.0 mm rung (optionally 0.8 mm) to the
   coupon silk ladder** so the fab floor is actually measured on lasered white
   mask. Today the ladder's smallest rung (1.2 mm) is *above* both fab floors,
   so it cannot answer the question it exists to answer.
5. **`coupon.kicad_pro` — `min_hole_clearance 0.25 → 0.40 mm`** so DRC and the
   SPEC's clearance law agree. Nothing on the board is currently between the
   two, so this is a tightening with no layout consequence.
6. **Perforated tabs (§11.4).** Deferred, not adopted: it would require the
   `cutout tab census` check to measure *material* rather than uncut path
   length, and the perforations to enter the Excellon schedule.

**Explicitly NOT proposed** (checked and left alone): the 0.40 clearance law,
the 0.50 track floor, the ≥0.60/≥0.70 annular rings, silk ≥0.30 from pads, the
0.40 copper-to-edge law, solid pours, and the 1.20 mm clearing floor. Every one
of them is met or exceeded relative to both fab houses' published numbers, and
§0–§12 give each an independent milled derivation.

---

## Changes recommended for Board A's relayout

In order. Item 1 is the operator's stated pain point (b); items 2–4 are pain
point (a) at the design level.

1. **Thermal-relief spec — the headline change.**
   - GND zone: `SetThermalReliefGap(NM(0.4))`, `SetThermalReliefSpokeWidth(NM(0.6))`,
     keep `ZONE_CONNECTION_THERMAL` as the zone default, keep
     `thermal_bridge_angle 45`. Result: 4 × 0.6 mm spokes across a 0.4 mm ring —
     compatible with the 0.5 mm track law (0.6 drawn, 0.4 delivered worst case)
     and with the 0.8 mm clearing corn (the ring is iso-only work, so the corn
     never tries to enter it).
   - **Hole-centered GND pads** (SW1, S2, LED2/LED3 cathodes, U1 socket pin 1,
     PAD−, JP4–JP6, TP pads on GND): thermal relief, 4 × 0.6 / gap 0.4. Verify
     each ring's arcs: a Ø2.1 mm pad gives four ≈1.05 mm arcs at 0.4 mm wide —
     all inside the iso window.
   - **SMD GND pads** (currently `ZONE_CONNECTION_FULL`, `tools-layout.py:455`):
     switch to `ZONE_CONNECTION_NONE` and route one **0.6 mm neck ≥0.5 mm long**
     from each pad into the pour. Applies to the 0805/1206/0603 GND-side
     terminals, Q1's emitter, U2's GND pin, and every 100 nF decoupling cap's
     GND pad.
   - **Leave solid** only where the connected island is under ~5 mm² of copper
     (a local pour pocket, not the main plane). Audit and list them; expect zero
     on Board A.
2. **Forbidden-band audit of every gap on the board.** With the relief gap fixed
   at 0.4 the pour is compliant by construction, but sweep for gaps in
   (0.480, 1.200) mm anywhere else: pour necks and pocket entrances, the coupon
   block's inter-feature spacings (the 0.5 and 0.6 ladder gaps stay — they are
   the gauge, and they are what will *measure* the 0.480 figure), the M3
   keep-out ring widths, the scrub-ring annulus surroundings, and the gaps
   around each new neck. Where one is found, move it to ≤0.44 (0.4 with margin)
   or ≥1.2.
3. **Truncate every tapering copper feature (§8).** No interior angle below 90°.
   Check the serpentine fold tips in the coupon block, and every place the pour
   feathers to a point against a trace or a keep-out. End pours on flats.
4. **Teardrops on** at THT pad-to-track junctions (KiCad Board Setup →
   Teardrops), length ≤1.0 × pad radius — then **re-run item 2**, because
   teardrops narrow the adjacent voids.
5. **Pad-size sweep against the 0.70 mm scrub floor (§5).** Every solderable pad
   must be ≥0.70 mm in its short dimension, ≥0.80 preferred. The 0603 pads and
   the coupon pad ladder's smallest rung are the ones to measure. Record the
   number on the run sheet — a pad that fails this is silently unscrubbed,
   because `scrub coverage` has no bar.
6. **Assert mask expansion 0.** Confirm `solder_mask_to_copper_clearance: 0.0`
   survives the relayout and that no imported footprint carries a local mask
   margin. A +0.05 mm from a stock footprint would eat the whole 0.05 mm
   plateau bar.
7. **Silk: floor to 1.0 mm, default 1.5 mm, add a 1.0 mm ladder rung**, keep
   stroke 0.25 and the ≥0.30 mm pad clearance, add ≥0.30 mm inter-stroke
   clearance. The ladder should be able to say "the fab floor is / is not
   legible on lasered white mask".
8. **Tabs: 4, one per edge midpoint** (perimeter 190 mm → JLCPCB's 50–60 mm rule
   gives 3–4 sets); add the **≥3.0 mm part-body-to-tab** rule and check LED2/LED3,
   SW1 and S2 against it; add "file the snap edges" to the run sheet.
9. **State the M3 keep-out radius as ≥3.20 mm** in the SPEC (current octagons
   give 3.30 mm — passes by 0.10, and the number should not be accidental).
10. **Hole-size sweep:** every THT hole = lead + 0.30 mm nominal, rounded up to a
    bore class. Bench-confirm the LED2/LED3, SW1 and S2 lead dimensions at BOM
    freeze — the SPEC already flags those three as unconfirmed.

For Board B, one carry-over worth noting now: **wire vias must be relieved or
necked too** (Ø2.4 pad, 4 × 0.6 spokes, gap 0.4). A wire via soldered on both
faces into a solid pour is the worst joint on that board, and there are twelve
of those joints.

---

## Changes recommended for the checks

Each is a hazard the current gate does not model. Listed with the incident or
pain point it would guard, per Article II.

1. **`copper gap band` (design-side, from the gerbers) — highest value.**
   FAIL any non-copper gap whose width lands in **(0.480, 1.200) mm**, with the
   bound derived from the job's own `iso` tool and depth (`tip_dia + kerf`) and
   from `clear` (`corn_dia + CLEAR_OPENING_MARGIN`) rather than hard-coded — so
   changing the iso depth moves the bar automatically. *Guards:* pain point (a),
   residual copper slivers. Named exception list for the coupon ladder, which
   exists to sit in the band. This is the cheapest possible fix: it refuses the
   *artwork* instead of detecting the *sliver*.
2. **`residual copper` census (from the simulated stock, Article VI).**
   After `mill` (iso + clear), enumerate connected copper regions in the carved
   stock that are not in the retained-copper set. FAIL (or report a census with
   a bar) on any such region narrower than **0.30 mm** or longer than
   **0.50 mm**. This is the milled equivalent of JLCPCB DFM's copper-sliver
   check, and it is the detector that pairs with check 1's preventer. *Guards:*
   pain point (a), and §8's acute-angle whiskers.
3. **`thermal spoke` (design-side).** For every pad connected to a pour: count
   the connections and measure each one's drawn width. FAIL if any spoke's
   **delivered** width (`drawn − 0.080`) is below **0.40 mm**, or if a
   hole-centered pad on a pour has fewer than **2** connections. This is
   JLCPCB DFM's "starved thermal" restated in kerf terms. *Guards:* the fix in
   §1 silently degrading — a 0.5 mm spoke drawn by a future layout script would
   deliver 0.30 mm.
4. **`pad scrubbability` (design-side) — closes a real hole.** FAIL any
   solderable pad whose short dimension is below
   `2·(scrub_r + SCRUB_WINDOW_MIN) + 2·deflate` (= **0.70 mm** at the current
   job). Today such a pad produces *no* scrub toolpath, and because
   `scrub coverage` is deliberately un-barred (DESIGN.md, twice) the gate reports
   PASS on a pad that ships under mask. *Guards:* an un-scrubbed pad reaching
   assembly — a failure the operator can only find with an iron.
5. **`copper interior angle` (design-side).** FAIL any copper-to-copper interior
   angle below **90°**, reporting the implied sliver length `0.210/tan θ` so the
   message explains itself. *Guards:* §8. Lower priority than 1 and 2, which
   catch the same defect by its width.
6. **`silk legend metrics`.** Extend the existing silk phase checks (which cover
   dose, feed, focus move and pad clearance) with glyph geometry: text height
   ≥**1.00 mm**, stroke ≥**0.15 mm**, ratio within **1:4.5–1:6.5**, inter-stroke
   ≥**0.30 mm**. Cheapest at the DRU level for height, in the gate for the ratio
   and inter-stroke (the gate already densifies every firing segment, so it has
   the geometry in hand). *Guards:* an illegible legend on Board B, where twelve
   cathode ticks are load-bearing.
7. **`tab body keep-out`.** Extend Board B's `tab-zone copper keep-out` with a
   part-body variant: no footprint courtyard within **3.00 mm** of a cutout tab.
   *Guards:* JLCPCB's separation-line rule — a 5 mm LED or a buzzer sitting
   where the board flexes during the hand snap.

Two notes on what *not* to do:

- **Do not put a number on `scrub coverage`.** It is un-barred deliberately and
  the open measurement is a loupe on Board A's copper. Check 4 attacks the same
  risk from the design side, where the arithmetic is exact.
- **Do not add a "minimum spacing" check.** The lane's spacing risk is a band,
  not a floor (§0/§2). A conventional minimum-spacing check would pass every
  sliver in this document.

---

## Sources

Fetched 2026-07-30. JLCPCB and PCBWay pages are the primary references; the two
non-primary sources are labelled where used.

- JLCPCB, [PCB Capabilities](https://jlcpcb.com/capabilities/pcb-capabilities) — trace/space, PTH **and NPTH** annular ring, hole-to-copper, mask expansion 1:1, mask bridge by colour, silkscreen, copper-to-edge (routed and V-cut), hatch grid
- JLCPCB, [PCB Design Rules and Guidelines: A Complete Best Practices Guide](https://jlcpcb.com/blog/pcb-design-rules-best-practices) — trace width vs current, 45° routing, drill and annular minimums
- JLCPCB, [Design Rule Check Prevents Costly PCB Production Mistakes](https://jlcpcb.com/blog/design-rule-check) — "safe default" vs "advanced" numbers for clearance, annular ring, copper-to-edge, mask sliver; IPC citations
- JLCPCB, [Basic Design of Solder Mask](https://jlcpcb.com/blog/basic-design-of-solder-mask) — mask expansion 0.05–0.1 mm per side, encroachment vs over-exposure
- JLCPCB, [Technical Guidance: Character Design Specifications](https://jlcpcb.com/blog/character-design-specifications) — silkscreen height, stroke, ratio, pad clearance, inter-stroke clearance, legend on exposed copper
- JLCPCB, [Mastering PCB Footprints: Design Best Practices](https://jlcpcb.com/blog/pcb-footprint-design-practices) — IPC-7351, land patterns, mask expansion, paste aperture reductions
- JLCPCB, [Why Solder Pad Design Matters](https://jlcpcb.com/blog/solder-pad-design-explained) — IPC-7351 density levels A/B/C, 75 µm mask dam
- JLCPCB, [PCB Copper Pour Basics](https://jlcpcb.com/blog/pcb-copper-pour-basics) — solid vs hatched, thermal relief rationale, copper conductivity
- JLCPCB, [PCB Teardrop You Should Know](https://jlcpcb.com/blog/pcb-teardrop-you-should-know) — when to add teardrops; the 20 mil threshold
- JLCPCB, [Understanding the Importance of Annular Rings](https://jlcpcb.com/blog/annular-rings) — `W=(OD−D)/2`, tangency, breakout, pad lifting
- JLCPCB, [The Ultimate Guide to PCB Panelization](https://jlcpcb.com/blog/pcb-panelization-tools-techniques) — mouse bites, tab sets, V-cut, 3–5 mm separation-line rule, rails
- JLCPCB, [Common Design Issues and Recommendations](https://jlcpcb.com/blog/common-pcb-design-issues-recommendations) — NPTH-to-copper 2 mm, process edge 5 mm
- JLCPCB, [Top DFM and DFA Rules That Ensure PCB Assembly Success](https://jlcpcb.com/blog/optimizing-dfm-for-pcb-assembly) and [Free DFM Tool](https://jlcpcb.com/blog/free-dfm-tool) / [JLCDFM](https://jlcdfm.com/) — the DFM rule *set*: acid traps, copper slivers, starved thermals, mask dams, drill-to-edge
- PCBWay, [PCB Capabilities](https://www.pcbway.com/capabilities.html) — trace/space, annular ring, drill range and tolerances, hole-to-copper, line-to-edge, mask opening and bridge, silkscreen, castellations
- PCBWay, [PCB Design Guidelines](https://www.pcbway.com/blog/Engineering_Technical/PCB_Design_Guidelines.html) — pad/hole ratios (+0.6 mm disc), mounting aperture +0.2–0.4 mm, clearance 0.33/0.254 mm, grid fill 0.254 mm
- PCBWay, [What are Thermal Relief Pads?](https://www.pcbway.com/blog/PCB_Basic_Information/What_are_Thermal_Relief_Pads_PCB_Knowledge_802bdef3.html) — "+ or x", three or four spokes, when relief is unnecessary
- PCBWay, [Is it a Relief to Use Thermal Relief?](https://www.pcbway.com/project/share/Is_it_a_Relief_to_Use_Thermal_Relief_.html) — the four-spoke hand-soldering argument
- PCBWay, [Complete PCB Design Guidelines: Layout, Routing and Manufacturing Best Practices](https://www.pcbway.com/blog/PCB_Design_Layout/Complete_PCB_Design_Guidelines_Layout_Routing_and_Manufacturing_Best_Practices_7a28d618.html) — IPC-2152 trace width, decoupling placement, 3W rule
- **(non-primary, labelled in §1)** BestPCBs, [PCB Thermal Relief Design Guidelines for High-Current Circuits](https://www.bestpcbs.com/blog/2026/06/pcb-thermal-relief/) — the only fab-published spoke width (0.20–0.50 mm) and relief gap (0.20–0.50 mm) numbers found; "four spokes better than two"; total spoke cross-section over single spoke width
- **(non-primary)** IPC-2221 / IPC-7351 as cited *by* the pages above; no IPC document was read directly, and no number in this file is attributed to IPC except through a citing fab page

Lane-side references (numbers derived, not borrowed): `boards/coupon/SPEC.md`
process-rules table; `boards/orbit/SPEC.md` process-rules deltas;
`boards/coupon/coupon.toml` `[[tool]]` + `[phases.*]`;
`boards/coupon/coupon.kicad_dru` and `coupon.kicad_pro`;
`boards/coupon/tools-layout.py:454-455, 521`;
`src/clauderacam/pcb/checks.py` threshold block and `PHASE_CHECKS`;
`src/clauderacam/pcb/flip.py` threshold block;
`src/clauderacam/pcb/reemit.py` `annular_laps()`;
`DESIGN.md` WS5 check list, the live-run findings, and the scrub-gauge law;
`~/scratch/carvera/guides/pcb-milling-workflow.md` §3/§3.2 and
`solder-mask-and-silkscreen.md` §1–§5.
