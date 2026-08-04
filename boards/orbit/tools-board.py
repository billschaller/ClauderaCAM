#!/usr/bin/env python3
"""Board B "orbit" — the pcb-rnd-native physical board generator (R4a).

Emits, deterministically, from SPEC.md's constants plus the SCHEMATIC's
netlist (which is ground truth — this program refuses to run if its placement
model and `orbit.kicad_sch` disagree about a single pin):

  orbit.lht        the UNROUTED physical board in lihata (pcb-rnd native):
                   every piece of copper that will exist on the milled board,
                   both GND pours, both silk legends, the fixed power tracks.
  orbit-route.dsn  the ROUTABLE serialization for R4b: only what an autorouter
                   may legally use, stated in R3's proven Specctra semantics.

ROUTING IS NOT THIS PROGRAM'S JOB (R4b).  Nothing here emits a signal track
except the four pre-placed, protected ones the geometry cannot survive without.

THE PLATING MODEL — nothing on this board is plated by default
--------------------------------------------------------------
There is no plating process on a milled board (SPEC "Wire vias").  A hole
conducts on one face only unless a human puts metal through it.  So:

  * A THT terminal owns copper on the BACK face only, and its physical FRONT
    ring is emitted as a separate ANONYMOUS copper island belonging to no
    terminal and no net.  MEASURED (r3_probe_ring.py, re-run by this file's
    gate): pcb-rnd seeds a terminal search on every face a terminal has copper
    on, so a terminal with rings on both faces + hplated=0 falsely CLOSES a net
    that no metal bridges — the board would report "layout is complete" while
    the operator holds an open circuit.
  * hplated=1 is a DECLARATION that the bench will stitch that hole.  At R4a
    the declared set is EMPTY: the wire-via prototype and the dual-solder wire
    PAD prototype both exist, but no pin is promoted.  R4b promotes exactly the
    leads its router actually uses, and each promotion is a bench joint the
    assembly card must repeat.  Since the operator ruling of 2026-08-01 only
    the two bare wire pads are promotable at all (build_parts, at the LEDs):
    a lead with the part's own body over it is not a joint a human can make,
    so the layer change is bought with a wire via instead.

Laws in force (SPEC process table, R2 surface-3 thresholds): clearance 0.4,
annular ring 0.7, drill 1.0, track 0.5 min / 0.6 signal / 0.8 rails.

Usage:
    python3 tools-board.py            # emit orbit.lht + orbit-route.dsn
    python3 tools-board.py --gate     # emit, then run every gate (exit 1 = fail)
"""

from __future__ import annotations

import math
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))          # boards/ -> silklabel.py

import kicadnet                                     # noqa: E402
import lihata as L                                  # noqa: E402
import silklabel as SL                              # noqa: E402

PCB_RND = os.path.expanduser("~/.clauderacam/tools/pcbrnd/pcb-rnd.sh")
SCH = os.path.join(HERE, "orbit.kicad_sch")
OUT_LHT = os.path.join(HERE, "orbit.lht")
OUT_DSN = os.path.join(HERE, "orbit-route.dsn")

# The legend's date is a FROZEN constant, not today's date: an artefact whose
# bytes change with the wall clock cannot satisfy the determinism gate.
DATE_STAMP = "2026-08-01"

# ---------------------------------------------------------------------------
# LAWS (SPEC "Process rules"; thresholds proven in R2 surface 3)
# ---------------------------------------------------------------------------
CLEAR = 0.4              # copper-to-copper, both sides
RING = 0.7               # annular ring on every hole-centered pad
DRILL_MIN = 1.0          # 0.8 corn + 0.2 radial; no PCB drill is in the crib
TRACK = 0.6              # signal
RAIL = 0.8               # power rails
EDGE_CLEAR = 0.4         # copper to board edge
# THE POUR'S OWN SETBACK, and it is five times the copper-to-edge law on
# purpose.  The board is snapped out of its blank by hand on four TABS, and
# flip.tab_zone_checks refuses any copper within TAB_KEEPOUT (1.0) of a tab —
# measured at the cut path AND at its projection onto the outline, because the
# fracture happens at the outline and that is where the copper is.  A tab that
# bridges copper tears it off the laminate when it snaps.
#
# MEASURED on the first run of the double-sided gate: with the pour inset at
# EDGE_CLEAR the front copper stood 0.395 from the tab zone at board
# (0.00,28.25) and back/holes refused the board.  Both pours ran 0.40-0.64 from
# the outline everywhere, so no tab PLACEMENT could have passed — the fix has
# to be the pour, not the tabs.
#
# 1.10, not the 1.0 the law asks for: the bar is a '>=' test on a rasterized
# distance and this file does not sit on bars.  The perimeter GND this costs is
# a 0.70 mm wide ring of fill that carried no terminal (verified by re-running
# the pour census — every GND terminal keeps its path to the plane).
#
# IT IS NOT FREE, and the cascade is the interesting part: the M3 copper
# keep-outs are punched into the pour as declared hole contours, and a hole
# that is not WHOLLY INSIDE its polygon is degenerate — pcb-rnd's boolean then
# fails and SILENTLY DISCARDS THE WHOLE POUR (the 2026-08-01 incident).  Moving
# the pour boundary inward moves that cliff, so MOUNT_INSET had to follow it
# outward-of-the-edge, and the flip gauges had to move with the mounts to keep
# their own 4.77 mm separation.  assert_pour_holes_inside() measures against
# THIS constant now; it used to measure against EDGE_CLEAR, which would have
# gone on approving a boundary the pour no longer had.
POUR_EDGE_SETBACK = 1.10

# MEASURED, both toolchains independently: a nominal ring diameter of exactly
# hole+2*RING sits ON the bar and loses to coordinate quantization — the KiCad
# round read 0.699 and raised 16 annular_width errors at Ø2.40/Ø1.0.  Geometry
# that must pass a '<' test needs a margin bigger than the quantum, so every
# ring diameter below is nominal + 2*RING_MARGIN.
RING_MARGIN = 0.02
DRC_MARGIN = 0.02        # what the ROUTER is told, so its exactly-on-target
ROUTE_TRACK = TRACK + DRC_MARGIN     # traces still clear the law after um
# ROUTE_CLEAR carries TWICE the margin, and R4b measured why.  Routed copper is
# separated from other nets by exactly what the router is told, so at
# CLEAR + DRC_MARGIN it lands on 0.420 — the same number COPPER_CLEAR enforces
# on the pour.  Once GND copper WELDS into that pour (see emit_lihata) the pour
# inherits those gaps, quantization shaves the tightest to 0.419, and pcb-rnd
# reads "shorted nets: net too close to other net" twice on a board whose
# independent geometric scan is clean at 0.419 >= the 0.400 LAW.  The law does
# not move; the routed geometry clears the enforcement instead.
ROUTE_CLEAR = CLEAR + 2 * DRC_MARGIN
# The same lesson applied to STATIC copper: an object that declares exactly the
# legal clearance is clipped out of the pour at exactly the legal gap, and
# pcb-rnd then reads it as "insufficient clearance" — MEASURED here as 21
# net-short violations before this margin existed.  The LAW is unchanged at
# 0.4; what changes is that the geometry clears it instead of tying it.
COPPER_CLEAR = CLEAR + DRC_MARGIN

# ---------------------------------------------------------------------------
# GEOMETRY (SPEC "Layout notes"), board frame: y-UP, origin at the lower-left
# corner — the WCS both machining setups share.
# ---------------------------------------------------------------------------
# GROWN 2026-08-01 from 56.0 x 48.0, and the growth is the fix for four of the
# six galvanic residues R4b inherited (U1-4 GND x2, L0 R4-2/TP4-1, the L3 front
# fragment).  Operator ruling 2026-08-01, density philosophy: "components low /
# vias medium / crowded board HIGH — when geometry fights back, GROW the outline
# or SPREAD the placement instead of being clever."  The binding fight was the
# ring INTERIOR: 12 radial resistors capped at RES_OUTER by the cathode ring
# left U1 a 0.7 mm annulus to escape into, and U1-4 (GND, mid pin field) could
# not reach open copper at all.  Growing LEAD_R_IN by 4.00 mm moves every
# resistor 4.00 mm outward and turns that annulus into 4.7 mm — three routable
# corridors where there was not one.  The board grows to carry the bigger ring.
#
# DOWNSTREAM CONSEQUENCES OF THESE TWO NUMBERS, stated here because they live in
# other files: the flip mirror line is BOARD_W/2 (now x = 33.0) and the
# registration pins sit ON it at (BOARD_W/2, -8.0) and (BOARD_W/2, BOARD_H+8.0)
# = (33, -8) and (33, 64); the blank must cover BOARD_W x (BOARD_H+16) = 66 x
# 72, which the operator's 150 x 100 stock clears comfortably.  Pins and job
# frames are declared in the JOB TOML, not here — and tools-fab.py MEASURES the
# exported Edge_Cuts extents against them rather than trusting either file.
# W grew a second time (60 -> 64) for the RIGHT STRIP, measured the same way:
# both button nets (L2, L3) can only enter their button from its outboard leg
# column, and on the 60-wide board the only lane into S2's was 1.67 mm against a
# 1.46 mm need — with D1 standing in it.  Widening the strip and clearing the
# lane is the ruling's answer; being clever with a 0.2 mm margin is not.
#
# GROWN A THIRD TIME 2026-08-02, 64 x 54 -> 66 x 56, and this one is the
# CROWDING AUDIT's roll: it is the fix the audit asked for in writing and could
# not perform itself (see BUTTONS, where the finding was recorded rather than
# fixed).  The CATCH and START legends seated 0.345 from their own button's leg
# rings — inside the +25% band that marks a squeezed seat — because the
# top-right corner holds a mount, a flip gauge and a button at once and S2 had
# 0.06 mm of travel: spreading the buttons the +/-0.6 that opens the band put
# S2's 2B leg ring 0.03 INTO flip gauge G4, which needs 0.42 and cannot move
# either (pushed cornerward it breaks the 4.77 mm it owes mount H4).
#
# GROWTH IS THE ONLY LEVER THAT MOVES THAT CORNER, because the mounts and the
# gauges are DERIVED from the outline's insets and the buttons are not: +2 in
# each axis carries H2/H4 and G2/G4 outward while S1/S2 stay on their strip, so
# the S2-to-G4 gap goes from 0.474 (and -0.03 once spread) to 2.787.  The
# legends then seat at 0.645/0.655 — +117% over the 0.30 ink-to-pad law, where
# the audit asks for +25% — and the board reports ZERO crowding flags for the
# first time.  Operator directive, 2026-08-02: "if labels can't be placed
# cleanly it suggests the board is congested and needs to un-compress."
#
# NOTHING ELSE IN THIS FILE MOVES WITH IT, on purpose.  The ring, the bottom
# strip, the ISP block, the driver cell and the right strip are all absolute
# coordinates, so the 2 mm lands entirely in the top and right margins — which
# is where the congestion was.  What re-derives: MOUNTS, GAUGES, the pour and
# its holes (assert_pour_holes_inside re-measured after the move and still
# clears — the mounts keep their 4.75 inset from the new edge, so every margin
# in that check is unchanged by construction), rounded_rect, lht_xy, the DSN
# boundary, "SIDE B", and the seed searches' board-bounds tests.
BOARD_W, BOARD_H = 66.0, 56.0
CORNER_R = 2.0
# Ring centre moved with the growth: +2 in x so the enlarged ring keeps a 4.3 mm
# pour margin on the left edge, +4 in y so its lowest lead still clears the
# bottom strip (SW1's blade ring tops out at 6.62).
RING_CX, RING_CY = 24.0, 30.0
RING_R = 17.0                    # LED body pitch circle (bodies 8.9 mm apart)
LEAD_R_IN, LEAD_R_OUT = 15.55, 18.63   # cathode INWARD, anode outward
# LED lead pitch is 3.08, NOT the SPEC's 2.54.  Ø2.50 rings on two DIFFERENT
# nets need hole pitch >= 2.50 + 0.40 = 2.90 before any routing happens; 2.54
# caps the ring at 0.57 and is UNBUILDABLE under ring>=0.7.  Found independently
# in both toolchains ("24 clearance errors, one per LED" on the first KiCad
# build).  3.08 leaves 0.58 of real gap.
#
# RE-SPREAD 2026-08-02, and this is THE MARGIN LESSON arriving for the fourth
# time on this board.  The pitch was 2.90 against Ø2.44 rings — 0.46 of gap,
# comfortable.  Growing the rings to the scrub law's Ø2.50 spends 0.06 of that
# and lands the anode/cathode pair at EXACTLY 0.400, i.e. exactly on the
# clearance law, 24 times over.  MEASURED at that pitch before the fix: the
# independent scan reads 0.3996 on eight of the twelve LEDs (quantization takes
# the tie the wrong way) and convicts the board.  So the pitch grows by the same
# 0.06 the rings did and the gap is preserved, not merely made legal.
#
# ONLY LEAD_R_OUT MOVES, and it moves much further than the law needs: 18.45 ->
# 18.67, a pitch of 3.12 where 2.92 would already be legal.  THE ROUTER CHOSE
# THIS NUMBER, and that is the same doctrine this file applies everywhere else
# ("a placement the router cannot solve is not an improvement, whatever the
# wire lengths say"; "the ROUTER is the scarcer resource").
#
# WHY IT HAD TO BE SWEPT.  The scrub growth wedges FreeRouting, and the wedge
# is a COMBINATION effect that no single change predicts.  Each ring grown
# ALONE converges — MEASURED, one unbounded run each, on boards that all pass
# the clearance scan:
#
#     baseline            (2.44/2.44/3.24)  CONVERGED 28 passes,  4 unrouted
#     via ring only       (2.44/2.50/3.24)  CONVERGED 20 passes,  5 unrouted
#     LED ring only       (2.50/2.44/3.24)  CONVERGED 18 passes,  8 unrouted
#     SW1 ring only       (2.44/2.44/3.30)  CONVERGED 20 passes,  3 unrouted
#
# and every combination of the LED and via rings wedges, wherever the pitch is
# taken from and whether or not SW1 grows too:
#
#     LED+via, pitch 2.96 (cathodes out)    WEDGE at pass #4, 20 open
#     LED+via, pitch 2.96 (anodes out)      WEDGE at pass #6, 11 open
#     all three, pitch 2.96                 WEDGE at pass #4, 20 open
#
# So the pitch was swept with everything at the scrub law, and convergence is
# NOT monotonic in it — it switches back on and then improves:
#
#     LEAD_R_OUT   18.51   18.55   18.59    18.63    18.67
#     result       wedge#6 wedge#5 conv 11  conv  5  conv  4
#
# 18.59 is where convergence switches back on, and it is NOT enough: 11 open is
# a route the closer cannot finish.  18.63 returns a baseline-class 5 open, and
# 18.67 returns the baseline's own 4.  The board is at 18.67 and the difference
# between the last two is the whole reason to record this table: at 18.63 the
# closer finishes four of the five and pcb-rnd hangs ONE rat line on the board,
# which fails the galvanic gate and takes two more checks down with it — the
# fantasy controls stop discriminating (they condemn a board by having MORE
# rats than the honest one, and 1 is not more than 1) and the back-pour probe
# loses its signal the same way.  One unclosed connection is not a near miss;
# it is three failed checks and a board that is not a board.
#
# WHAT IT COSTS, stated plainly.  A 5 mm LED's leads leave the body 2.54 apart
# and the operator splays them to 3.12 — 0.29 per lead, against the 0.18 the
# 2.90 pitch already asked for.  That is a bend, not a law: nothing in the
# process table bounds it, and the alternative on offer was a board the router
# cannot route.  The geometry it buys is roomy, not tight: 0.62 mm between the
# anode and cathode rings where the law needs 0.40.
#
# The ring's outer copper now reaches 24.0 + 18.67 + 1.25 = 43.92 (was 43.67),
# still 1.21 mm clear of the right strip's nearest land (R15's 1206 edge at
# 45.13), 4.08 mm from the left board edge against EDGE_CLEAR's 0.40, and
# 10.08/49.92 in y inside a 0.4..53.6 pour.  The 12 LED BODIES do not move at
# all — they sit on RING_R's pitch circle, which is untouched — so the silk
# ticks that say which lead is the cathode stay exactly where the bodies are.
# The leads are no longer symmetric about the body centre (15.55/18.67 midpoints
# at 17.11 against RING_R 17.0); 0.11 mm of a 5 mm body, and the tick is what
# tells the operator which hole is which, not the symmetry.
LEAD_PITCH = LEAD_R_OUT - LEAD_R_IN

HOLE_LED, HOLE_BTN, HOLE_BZ = 1.0, 1.0, 1.0
HOLE_SW1, HOLE_PAD, HOLE_GAUGE = 1.8, 1.5, 1.0
HOLE_MOUNT, HOLE_VIA = 3.4, 1.0

# ---------------------------------------------------------------------------
# THE SCRUB LAW — why every hole-centred pad on this board grew on 2026-08-02
# ---------------------------------------------------------------------------
# A milled double-sided board is not soldered on side 2 until the paint over
# side 2's rings has been SCRUBBED off, and that scrub is a real toolpath the
# lane generates: `reemit.annular_laps` walks the 0.30 mm scrub bit round and
# round inside the annulus.  It needs somewhere to walk.  Its bounds are
#
#     rc_min = hole_r + r + (SCRUB_ANNULAR_RIM    + SCRUB_LAP_MARGIN)
#     rc_max = pad_r  - r - (SCRUB_ANNULAR_INSIDE + SCRUB_LAP_MARGIN)
#
# and it REFUSES when rc_min > rc_max — no lap radius exists that both stays
# off the bore rim and stays inside the pad.  MEASURED on this board's own
# artwork, Ø2.44 pad over a Ø1.0 hole:
#
#     rc_min = 0.5 + 0.15 + 0.25 = 0.900   >   rc_max = 1.22 - 0.15 - 0.20
#                                                     = 0.870      REFUSED
#
# Every Ø1.0 ring on orbit was that pad.  Solving the inequality gives one law
# with no diameter special-casing:
#
#     pad_d >= hole_d + 2*(2r + INSIDE + RIM + 2*MARGIN) = hole_d + 1.50
#
# so the required ANNULAR RING is 0.30 + 0.15 + 0.20 + 0.10 = 0.75 mm.  That it
# lands 0.05 above the 0.70 ring LAW is arithmetic, not design: the ring law is
# about copper that survives milling, this is about a bit that has to fit.
#
# SPEC SAYS Ø2.4 AND SPEC IS OUT BY THE MARGIN — worth naming, because the two
# numbers look like a contradiction and are not.  SPEC's process table does the
# same arithmetic without SCRUB_LAP_MARGIN ("Ø2.4 pad + Ø1.0 hole leaves one
# legal 0.3-wide lap at r~0.9"), which is 0.85..0.90 of band and true as far as
# it goes.  The GENERATOR applies the margin to both bars, and it is the
# generator that emits the toolpath: 0.900 > 0.850, refused.  SPEC's Ø2.4 misses
# by 0.10 and this board's Ø2.44 missed by 0.06.
#
# THIS BAR ALREADY CARRIES ITS MARGIN, which is why sitting on it is allowed
# here and nowhere else in this file.  Both bounds are built from SCRUB_LAP_
# MARGIN, so a pad at exactly hole+1.50 gives a zero-width band whose single
# lap still holds 0.05 mm off the rim and 0.05 mm inside the pad edge.  (The
# recorded live run is the same shape: pad 2.6 / hole 1.0 -> band 0.05, one lap
# at rc 0.925.)  RING_MARGIN's 0.02 is not added on top — it exists to keep a
# ring off the min_ring bar, and 0.75 clears that bar by 0.05 already.
SCRUB_TOOL_DIA = 0.30        # jobs' [[tool]] type="scrub"; r = 0.15
SCRUB_ANNULAR_INSIDE = 0.15  # flip.SCRUB_ANNULAR_INSIDE
SCRUB_ANNULAR_RIM = 0.20     # flip.SCRUB_ANNULAR_RIM
SCRUB_LAP_MARGIN = 0.05      # reemit.SCRUB_LAP_MARGIN
SCRUB_RING = (SCRUB_TOOL_DIA + SCRUB_ANNULAR_INSIDE + SCRUB_ANNULAR_RIM
              + 2 * SCRUB_LAP_MARGIN)                     # 0.75
assert SCRUB_RING >= RING + RING_MARGIN, \
    "the scrub ring must also satisfy the annular-ring law it sits above"


def scrubbable(hole: float) -> float:
    """Smallest pad diameter a side-2 annular scrub can be walked inside.

    (Rounds like q() does; q itself is defined further down and this runs at
    import time, so the quantum is spelled out rather than borrowed.)
    """
    return round(hole + 2 * SCRUB_RING, 3)


RING_LED = scrubbable(HOLE_LED)                           # 2.50 (was 2.44)
RING_SW1 = scrubbable(HOLE_SW1)                           # 3.30 (was 3.24)
RING_VIA = scrubbable(HOLE_VIA)                           # 2.50 (was 2.44)
# SPEC's Ø3.6 wire pad is UNCHANGED: on a Ø1.5 hole it is a 1.05 ring, and the
# scrub law asks for 3.00.  It clears by 0.60 — grow only what is short.
RING_PAD = 3.6                                            # SPEC: Ø3.6 wire pad
assert RING_PAD >= scrubbable(HOLE_PAD)
# The flip gauges are the DECLARED exception and stay at 1.7 (0.35 annulus,
# SPEC Decision Q13).  They are not solderable, they are never scrubbed, and
# they take no mask aperture — a loupe reads them through bare paint BEFORE the
# squeegee.  A gauge grown to the scrub law would be a pad pretending to be a
# joint; the honest encoding is a small ring that no scrub set contains.
RING_GAUGE = 1.7         # DECLARED sub-law: 0.35 annulus, SPEC Decision Q13
MOUNT_KEEPOUT_R = 3.2    # copper keep-out radius around each M3 bore

# MEASURED 2026-08-01 (R4b), and the pour depends on it: when a copper LINE's
# pour cutout touches the contour of a HOLE punched in that pour, pcb-rnd's
# polygon boolean fails ("Error while clipping RND_PBO_SUB: 3") and SILENTLY
# DISCARDS THE WHOLE POUR.  The back GND plane went dead and its 11 SMD GND
# terminals came open — 59 rat lines became 70 — while the file still parsed
# and DRC still passed, so nothing but connectivity said a word.
#
# The cliff is brutally narrow.  Sweeping the S2 leg link against H4's hole:
#     gap +0.633 +0.433 +0.233 +0.133 | +0.033  -0.067  -0.167  -0.367
#     err      0      0      0      0 |     113     113     113     113
# and the hole's facet count (16/24/32/64) makes no difference, so this is a
# tolerance cliff and not a facet artefact.  PADSTACK cutouts are unaffected —
# S2's own rings straddle the same hole harmlessly — so the law binds LINES.
# A geometry that must pass a cliff test cannot sit ON the cliff (the same
# lesson as RING_MARGIN), hence a margin an order of magnitude clear of it.
POUR_HOLE_MARGIN = 0.30
# What the ROUTER must be told so its traces obey the law too.  FreeRouting
# keeps trace COPPER outside a keepout, so a trace centre lands at K + w/2 and
# its cutout reaches K - COPPER_CLEAR, independent of width.
ROUTE_MOUNT_KEEPOUT_R = MOUNT_KEEPOUT_R + COPPER_CLEAR + POUR_HOLE_MARGIN
ISP_PAD = 1.8            # bare B.Cu pad, no hole
ISP_PITCH = 2.54
# x0 = 44.0 (was 38.0, SPEC's ~37).  MEASURED, and the ISP was the board's real
# routing bottleneck: six Ø1.8 pads on a 2.54 grid leave 0.74 mm between
# neighbours, which is 0.72 mm too little for a track, so EVERY pad has to be
# entered from outside the block — the left column from the west, the right from
# the east.  At x0 38 the western corridor was the 3.6 mm slot between SW1's
# unused blade and the grid, and three nets wanted it (TP1 L1, TP3 L2, TP5
# RESET): the router left TP3 and TP5 open, and TP2 with them.  At 44 the west
# corridor is 9.6 mm and the east side is open to the edge.  Rotating the block
# 90 deg would also free every pad, but three labels per row cannot fit on a
# 2.54 pitch and the legend is what makes bare pads usable.
ISP_X0, ISP_Y0 = 44.0, 3.0

# The bottom strip's vertical stack is the tightest geometry on the board and
# is therefore PRE-PLACED and protected rather than left to a router.  Reading
# up from the board edge: [edge 0.4] VCC spine [0.4] VBAT corridor [0.4] SW1's
# blade row.  SW1 blade 1 (VSW) sits directly over the corridor, which carries
# VBAT — a different net — so the corridor's centre must clear the blade ring
# by RING_SW1/2 + CLEAR + RAIL/2 = 2.42.  The KiCad rounds measured this window
# at 0.15 mm and never fixed the tracks; these numbers give 0.28.
SPINE_Y = 0.9            # VCC spine, y
CORRIDOR_Y = 2.3         # VBAT power-entry corridor, y
SW1_Y = 5.0              # SW1 blade row, y
SW1_X, SW1_PITCH = 27.0, 4.86
SPINE_X0, SPINE_X1 = 19.5, 29.9        # SPEC/R4b contract: spine ends x<=29.9
# The power-entry cell, named because fixed_tracks() draws the VCC rail from
# Q1's pin 2 and THROUGH C1's pin 1: the rail, the regulator and the reservoir
# cap are one geometric statement and may not drift apart.  C1_XY is derived
# from the rail's descent (x 14) minus the C0805 half-pitch, so pin 1 lands on
# the rail's centreline exactly.
# x 20.64 = SW1_X - SW1_PITCH - 1.5, i.e. Q1's OUTPUT land sits directly above
# SW1's VSW blade.  That alignment is the whole point: VSW is board-only copper
# hidden from the router (see board_only_tracks), so the corridor it reserves
# has to be as short as a corridor can be — one 4.5 mm vertical drop instead of
# an L across the artery between the power entry and the bottom strip.
Q1_XY = (SW1_X - SW1_PITCH - 1.5, 9.5)
# C4 (the RESET cap) is NAMED because its position is not a free choice: its GND
# land has to sit in pour the BACK PLANE actually reaches.  See build_parts.
C4_XY = (40.5, 12.5, 0.0)
# R13 is NAMED because fixed_tracks() runs the VCC rail to its pin 1: the rail
# and the part that terminates it are one statement.  R0603 pin 1 sits one
# half-pitch (0.9125) left of centre.
R13_XY = (36.0, 10.5)
R13_VCC_X = R13_XY[0] - 0.9125
VCC_DESCENT_X = 14.0     # the rail's descent leg, x; C1's VCC land sits ON it
# C1 is rotated 180, so its pin 1 is at centre + C0805 half-pitch (1.0375, from
# SMD_FP below).  Asserted against SMD_FP at import time so the two can never
# disagree — a land that drifts off the rail is a silently open VCC net.
C1_XY = (VCC_DESCENT_X - 1.0375, 9.0)

# Radial resistor rings.  MEASURED failure the KiCad rounds recorded: laid
# TANGENTIALLY the 1206s "spend 4.40 mm of arc each and WALL OFF their own two
# channels ... only four of twelve gaps were crossable at all", and the gap to
# the neighbour read 0.374 — below the law.  Radial fixes both.  The per-package
# radius is set so every land's OUTER edge stops short of the LED cathode ring
# (inner edge at LEAD_R_IN - RING_LED/2 = 10.33) by >= CLEAR.
# radius set so every land's OUTER edge stops short of the LED cathode ring
# (inner edge at LEAD_R_IN - RING_LED/2 = 14.33) by >= CLEAR.  Both numbers grew
# by the same 4.00 mm as LEAD_R_IN, so the outer annulus is unchanged (0.63) and
# all 4.00 mm of new room lands in the INTERIOR, where U1 is.
# RUNG 2, 2026-08-02: both radii pushed to the CEILING the cathode ring allows.
# The bound is stated two comments above — a land's outer edge must stop
# CLEAR (0.40) short of the cathode ring's inner edge at 14.33, i.e. 13.93 —
# and the old 13.7/13.5 left 0.23 and 0.43 mm of that slack unspent.  13.93
# itself is NOT usable, and that is this file's oldest lesson repeating: at
# 13.93 the land edge sits at exactly 0.400 from the cathode ring, ties the
# law, and pcb-rnd convicts the UNROUTED board 12 times.  13.91 leaves
# COPPER_CLEAR (0.42) and reads clean.  Spending
# it moves every resistor outward, which is the only way to widen the INTERIOR
# annulus: the 1206 inner lands go from radius 9.10 to 9.53, and that annulus
# is the escape space U1's west pins (U1-1 RESET, U1-2 L3) have to reach open
# copper through now that the LED anodes are no longer layer bridges.
#
# RUNG 3, 2026-08-02 (the SCRUB growth), and it moves the ceiling DOWN by 0.03.
# The bound is written against the cathode ring's INNER edge, and that edge
# moved when the ring grew to the scrub law: LEAD_R_IN - RING_LED/2 is now
# 15.55 - 1.25 = 14.30, not 14.33.  Leaving RES_OUTER at 13.91 would hand back
# exactly the tie this comment was written to avoid — the arc's 0.42 falls to
# 0.39, UNDER the 0.40 law — so the arc is re-spread by the same 0.03 the ring
# grew inward: 14.30 - 13.88 = 0.42, COPPER_CLEAR again, the number that reads
# clean.  The interior annulus pays 0.03 for the scrub.
#
# Holding the ceiling at 13.91 by pulling the CATHODES outward instead was
# tried, and the router rejected it: it converges as an LED-only change but
# wedges at pass #4 with 20 open once the via ring grows too (see LEAD_R_IN).
# The pitch is bought from the anode side alone for that reason.
RES_OUTER, RES_OUTER_1206 = 13.88, 13.88

# DERIVED from the edges, not hard-coded: these four bores belong to the corners
# of the outline, so when the outline grows they move WITH it (2026-08-01).
#
# INSET 4.2, not the 3.5 the KiCad rounds and R4a used.  MEASURED 2026-08-01,
# and it is a real defect the old board carried by luck: the M3 copper keep-out
# is punched into each pour as a DECLARED hole contour (ta:hole), and at 3.5 that
# Ø6.4 contour reached x 0.3 — 0.1 mm OUTSIDE the pour's own boundary at
# EDGE_CLEAR 0.4.  A hole that is not wholly inside its polygon is degenerate,
# and pcb-rnd's boolean fails on it once enough other copper has been
# subtracted: on the routed board it threw "Error while clipping RND_PBO_SUB: 3"
# 324 times and SILENTLY DISCARDED THE BACK POUR (the unrouted board, with far
# less to subtract, clipped clean — which is exactly how the defect stayed
# hidden).  Sweeping the inset against the routed board: 3.5 -> 324 errors,
# 4.0 / 4.5 / 5.0 / 5.5 / 6.0 -> 0.  4.2 puts the contour 0.6 mm inside the
# straight boundary and 1.19 mm off the corner arc, and is bounded ABOVE by
# PAD2's ring (the keep-out may not swallow it) at 5.4.
# assert_pour_holes_inside() below refuses to emit if this is ever violated
# again — the measurement is not enough, because the symptom is silence.
# MOVED 4.2 -> 4.75 on 2026-08-02, and the pour's new setback is what moved it.
# The bound is assert_pour_holes_inside(): the Ø6.4 keep-out contour must sit
# POUR_HOLE_INSIDE_MIN (0.30) inside the pour boundary, which now stands at
# POUR_EDGE_SETBACK.  4.75 - 3.2 = 1.55 against a boundary at 1.10 leaves 0.45.
# Still bounded ABOVE by PAD2's ring, which the keep-out may not swallow:
# H1 to PAD2 is 5.303 mm against the 5.00 the two radii need, so 0.30 spare.
MOUNT_INSET = 4.75
MOUNTS = {"H1": (MOUNT_INSET, MOUNT_INSET),
          "H2": (BOARD_W - MOUNT_INSET, MOUNT_INSET),
          "H3": (MOUNT_INSET, BOARD_H - MOUNT_INSET),
          "H4": (BOARD_W - MOUNT_INSET, BOARD_H - MOUNT_INSET)}
# A flip gauge is a zero-length LINE (a disc of copper), so POUR_HOLE_MARGIN
# binds it against the M3 pour holes: it must stay RING_GAUGE/2 + COPPER_CLEAR +
# MOUNT_KEEPOUT_R + POUR_HOLE_MARGIN = 4.77 mm from a mount centre.  On the
# 56x48 board the top pair had to be pulled off the symmetric inset to 6 mm
# below the edge (it read +0.058 — inside the fatal band, passing by luck); on
# the grown outline the symmetric 8 mm inset clears it by 6.36 - 4.77 = 1.59, so
# the exception is retired and all four sit at the same inset.  They stay
# mirror-symmetric about BOARD_W/2, which the flip depends on.
# MOVED 8.0 -> 8.5 on 2026-08-02, following the mounts.  The binding rule is
# unchanged and stated above: a gauge disc must stay RING_GAUGE/2 +
# COPPER_CLEAR + MOUNT_KEEPOUT_R + POUR_HOLE_MARGIN = 4.77 mm from a mount
# centre, or its pour cutout grazes the M3 hole and kills the plane.  With the
# mounts at 4.75 the old 8.0 inset gave only 4.60 — inside the fatal band — and
# 8.5 restores 5.303, clear by 0.53.  Still mirror-symmetric about BOARD_W/2,
# which the flip depends on.
GAUGE_INSET = 8.5
GAUGES = {"G1": (GAUGE_INSET, GAUGE_INSET),
          "G2": (BOARD_W - GAUGE_INSET, GAUGE_INSET),
          "G3": (GAUGE_INSET, BOARD_H - GAUGE_INSET),
          "G4": (BOARD_W - GAUGE_INSET, BOARD_H - GAUGE_INSET)}

# Tactile switch bodies and the half-pitch of their four legs.  ONE definition:
# build_parts() places the pads from it and fixed_tracks() draws the leg links
# from the same numbers, so a link can never drift off the pad it shorts.
#
# The link may never be SHORTENED to dodge geometry: FreeRouting only counts a
# protected wire as reaching a pin when it ENDS on the pin, so a trimmed link
# stops being the short it exists to declare and the router reports S2-1 and
# S2-1B as an open connection.  When a link's pour cutout grazes a mount hole,
# the part moves — that keeps both the electrical statement and the geometry
# true.  (On the 56x48 board this forced S2 to y 37.5, off the 39.0 the KiCad
# rounds used, where the cutout read +0.033 against H4 and killed the back
# plane; perturb_leg() is the negative control that still proves the scan sees
# it.)
#
# SPREAD 2026-08-01 with the ring: the enlarged ring's copper now reaches
# x 43.67, so the whole right strip moved outboard (+7 in x) and the two buttons
# followed the ring centre (+4 in y).  S2 no longer needs its own y exception —
# on the grown board the L2 leg link reads +5.36 against H4, decisively outside
# POUR_HOLE_MARGIN's band — so S1 and S2 sit symmetrically again about the ring
# centre line, 12 mm apart from it.
# CROWDING AUDIT 2026-08-02, and the finding is RECORDED here rather than
# fixed, because fixing it is not a placement move.  The CATCH and START
# legends seat 0.345 from their own button's leg rings — inside the 25% band
# that marks a squeezed seat — and the arithmetic says why: between S1's upper
# leg ring (top 22.50) and BZ1's lower lead ring (bottom 24.95) there are
# 2.45 mm for 1.75 mm of text plus two 0.30 gaps, leaving 0.10 to share.
#
# Spreading the buttons +/-0.6 WAS tried and is measured illegal: S2's 2B leg
# ring then overlaps flip gauge G4 by 0.03 (it needs 0.42), and G4 cannot move
# either — pushed to the corner it breaks the 4.77 mm it owes mount H4, pushed
# inward it walks further into the button.  S2 has exactly 0.06 mm of travel.
# The top-right corner holds a mount, a gauge and a button, and there is no
# arrangement of the three that opens the legend's band.
#
# So this is the class of congestion the audit exists to surface and the only
# real cure is ROOM: grow the outline (the 150x100 blank carries 64x54 twice
# over) so the inset-derived mounts and gauges move out with it.  That is a
# board-growth roll, not a nudge, and it is left for one.
#
# THE ROLL HAPPENED (2026-08-02, see BOARD_W): the outline is 66 x 56 and G4
# now sits at (57.5, 47.5) instead of (55.5, 45.5), so the +/-0.6 spread this
# comment measured illegal is legal on the board that exists.  SPREAD, and the
# arithmetic is the same one that convicted it:
#
#   S2's 2B leg ring (54.25, 43.85) to G4 (57.5, 47.5) = 4.887 centre to
#   centre, minus 1.25 and 0.85 of ring = 2.787 mm, against the 0.42 it needs.
#   On the old board the same pair read -0.03.
#
# The buttons do NOT follow the outline — they belong to the right strip's
# absolute geometry, and moving them outboard as well would just carry the
# congestion along with them.  They move APART instead, which is what the
# legends between them and the buzzer were short of: the band between S1's
# upper leg ring and BZ1's lower lead ring goes from 2.45 mm to 3.65, and a
# symmetric spread of d gives each legend (9.45 + d)/2 - 4.375 of margin (see
# BUTTON_LEGEND_R for the derivation and the seat it picks).
#
# R15 follows S1 by construction (R15_XY reads BUTTONS), so the S1_R link still
# ends ON a pad rather than mid-span, which is the form FreeRouting counts as
# reaching the pin.  R16 does not move: it sits at x 46 with nothing but open
# back copper between it and S2's new y.
BUTTONS = {"S1": (57.5, 18.4), "S2": (57.5, 41.6)}
BTN_DX, BTN_DY = 3.25, 2.25
# The right strip's series resistors.  R15_XY is NAMED because fixed_tracks()
# draws the S1_R link from it, and its y is CHOSEN so that R15's pin 1 (a 1206
# rotated 90 puts pin 1 one half-pitch BELOW centre, 1.55 mm) lands level with
# S1's upper-left leg — the link then ends ON a pad instead of mid-span, which
# is the form FreeRouting counts as reaching the pin.
R15_XY = (46.0, BUTTONS["S1"][1] + BTN_DY + 1.55)
R16_XY = (46.0, 39.0)
# The buzzer's leads bound the lanes both button nets must use, and its y is
# pinned from BELOW by the front legend, not by copper: dropping it to 28.5 to
# widen L2's lane walked its lower ring into the "CATCH" legend and the front
# silk gate convicted it at -1.15 mm (a stroke essentially on the pad).  So the
# buzzer stays centred and the lane is bought by moving the DRIVER CELL out of
# the y 35.4..37.1 band instead (see D1/Q2 in build_parts).
BZ1_XY = (58.0, 30.0)

# Ring position -> LED index.  SPEC grants the layout this permutation ("the
# mapping is a LAYOUT degree of freedom ... the firmware table follows"), and
# it preserves SPEC's sector structure: every ADJACENT pair of positions still
# holds one antiparallel pair on one line pair, so the ring still needs six
# two-track corridors and not twelve.  Inherited from the KiCad rounds, which
# chose it to shorten the runs into U1.  R4b may re-permute (SPEC Decision 5).
POS_LED = {1: 8, 2: 7, 3: 12, 4: 11, 5: 9, 6: 10,
           7: 2, 8: 1, 9: 6, 10: 5, 11: 3, 12: 4}


# ---------------------------------------------------------------------------
# ONE COORDINATE CONVERSION (Article IV's spirit: never re-derive a frame)
# ---------------------------------------------------------------------------
def q(v: float) -> float:
    """Quantize to 1 um so lihata and DSN agree to the last representable bit."""
    return round(v + 0.0, 3)


def lht_xy(x: float, y: float) -> tuple[float, float]:
    """Board frame (y-up, origin lower-left) -> lihata frame (y-down)."""
    return (q(x), q(BOARD_H - y))


DSN_U = 10000.0          # (resolution um 10): 1 DSN unit = 0.1 um


def dsn_xy(x: float, y: float) -> tuple[int, int]:
    """Board frame -> integer DSN units.  DERIVED from lht_xy, never re-derived:
    DSN is y-up where lihata is y-down, so dsn_y = -lht_y (R3-proven)."""
    lx, ly = lht_xy(x, y)
    return (int(round(lx * DSN_U)), int(round(-ly * DSN_U)))


def dsn_len(mm: float) -> int:
    return int(round(mm * DSN_U))


def polar(cx: float, cy: float, ang_deg: float, r: float) -> tuple[float, float]:
    a = math.radians(ang_deg)
    return (cx + r * math.cos(a), cy + r * math.sin(a))


def pos_angle(pos: int) -> float:
    """Ring position -> angle.  Position 1 at 12 o'clock, running CLOCKWISE."""
    return 90.0 - 30.0 * (pos - 1)


def rot(px: float, py: float, deg: float) -> tuple[float, float]:
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    return (px * ca - py * sa, px * sa + py * ca)


# ---------------------------------------------------------------------------
# FOOTPRINT LIBRARY — relative pin tables, board frame, rotation 0.
# Land dimensions are the hand-solder variants the KiCad rounds resolved from
# the KiCad footprint library (SPEC "footprints": hand-solder everywhere);
# nothing here is estimated.  Each entry: {term: (dx, dy, w, h)} for SMD lands,
# or (dx, dy) for THT holes.
# ---------------------------------------------------------------------------
def _chip(cc: float, w: float, h: float) -> dict:
    """Two-terminal chip land: pin 1 at -x, pin 2 at +x, centres cc apart."""
    return {"1": (-cc / 2, 0.0, w, h), "2": (+cc / 2, 0.0, w, h)}


SMD_FP = {
    "R0603": _chip(1.825, 0.975, 0.95),
    "R0805": _chip(2.000, 1.200, 1.40),
    "R1206": _chip(3.100, 1.300, 1.75),
    "C0603": _chip(1.725, 1.075, 0.95),
    "C0805": _chip(2.075, 1.175, 1.45),
    "C1206": _chip(3.125, 1.325, 1.80),
    # SOT-23 hand-solder: pins 1,2 on one side, 3 opposite.
    "SOT23": {"1": (-1.5, -0.95, 1.9, 0.8), "2": (-1.5, +0.95, 1.9, 0.8),
              "3": (+1.5, 0.0, 1.9, 0.8)},
    # SOIC-8 WIDE, EIAJ 8S2, 5.3 mm body, 1.27 pitch — the -SU marking's body.
    # A narrow SOIC-8 here is a reflow failure (SPEC BOM, U1).
    #
    # The land is 0.72 ACROSS THE PITCH, not the hand-solder library's 0.65,
    # and the reason is the side-2 SCRUB.  A land narrower than the scrub gate's
    # 0.70 bar has no plateau the 0.30 bit can walk without touching an edge:
    # MEASURED at 0.65 the gate read 0.660 against >= 0.70 and refused the whole
    # back/scrub program.  0.72 clears the bar by 0.02 rather than tying it
    # (this file's oldest lesson), and it is bounded ABOVE by the 1.27 pitch:
    # the gap between neighbouring lands falls from 0.62 to 0.55, still 0.15
    # clear of the 0.40 clearance law.  Paste and mask follow the pad by the
    # zero-swell convention, so all three shapes move together.
    "SOIC8W": {str(n + 1): (
        (-3.5875 if n < 4 else 3.5875),
        (1.905 - 1.27 * n) if n < 4 else (-1.905 + 1.27 * (n - 4)),
        1.625, 0.72) for n in range(8)},
}

# Radial length of each chip land, used to seat the ring resistors so their
# OUTER edge lands on RES_OUTER.
SMD_LEN = {k: max(abs(dx) + w / 2 for dx, _dy, w, _h in v.values()) * 2
           for k, v in SMD_FP.items()}

# C1's VCC land must sit ON the VCC rail's descent (see C1_XY): pin 1 of a
# C0805 rotated 180 lands at centre_x + half-pitch, and that has to BE the
# rail's x.  Stated as an assertion because the alternative is an open rail
# nobody notices until pcb-rnd hangs a rat line on it.
assert q(C1_XY[0] + SMD_FP["C0805"]["1"][0] * -1) == VCC_DESCENT_X, \
    "C1's pin-1 land has drifted off the VCC rail descent"


# ---------------------------------------------------------------------------
# MODEL
# ---------------------------------------------------------------------------
class Pin:
    """One terminal.  `shape` is one of:

      ("rect", w, h)      SMD land, BACK copper only (SPEC: SMD reflows on B.Cu)
      ("circ", dia)       bare round pad, BACK copper only (the ISP pads)
      ("tht", hole, ring) through hole.  The TERMINAL owns the BACK ring only;
                          the front ring is emitted separately as a dead island.
    `dual` marks the dual-solder-CAPABLE class: leads the bench can reach on
    both faces.  Since the operator ruling of 2026-08-01 that class is the two
    bare WIRE PADS (PAD1, PAD2) and nothing else — see build_parts for the
    ruling verbatim.  A part with a body over its own lead (an LED flange, a
    buzzer can, a switch housing) is NOT in it, because "capable" means an iron
    tip can reach the front ring with the part seated normally.
    """
    __slots__ = ("ref", "term", "x", "y", "shape", "dual", "net", "prot")

    def __init__(self, ref, term, x, y, shape, dual=False, prot=0.0):
        self.ref, self.term = ref, term
        self.x, self.y, self.shape, self.dual = q(x), q(y), shape, dual
        self.prot = prot % 360.0        # pad rotation, degrees (SMD lands)
        self.net = None

    @property
    def pid(self) -> str:
        return f"{self.ref}-{self.term}"

    @property
    def kind(self) -> str:
        return self.shape[0]

    def corners(self) -> list:
        """Absolute corners of a rotated rectangular land."""
        return [(self.x + cx, self.y + cy) for cx, cy in
                L.rect_corners(self.shape[1], self.shape[2], self.prot)]

    def extent(self) -> tuple[float, float]:
        """Half-width, half-height of this pin's copper, ROTATION INCLUDED."""
        if self.shape[0] == "rect":
            c = self.corners()
            return (max(abs(x - self.x) for x, _ in c),
                    max(abs(y - self.y) for _, y in c))
        if self.shape[0] == "circ":
            return (self.shape[1] / 2, self.shape[1] / 2)
        return (self.shape[2] / 2, self.shape[2] / 2)


class Part:
    __slots__ = ("ref", "fp", "side", "pins", "oid", "rot")

    def __init__(self, ref, fp, side, pins, oid, rotation=0.0):
        self.ref, self.fp, self.side = ref, fp, side
        self.pins, self.oid, self.rot = pins, oid, rotation

    @property
    def on_bottom(self) -> bool:
        return self.side == "back"

    def body(self) -> SL.Rect:
        """Courtyard for label placement, in the LIHATA frame (y-down), which
        is the frame silklabel's slot names were written for."""
        xs, ys = [], []
        for p in self.pins:
            hw, hh = p.extent()
            lx, ly = lht_xy(p.x, p.y)
            xs += [lx - hw, lx + hw]
            ys += [ly - hh, ly + hh]
        return SL.Rect(min(xs), min(ys), max(xs), max(ys))


def smd(ref, fp, x, y, rotation=0.0, oid=0, side="back") -> Part:
    """Place an SMD footprint: pin coordinates are resolved into the board frame
    HERE, so no mirror or rotation flag ever reaches the emitters.  The KiCad
    rounds lost days to side-mirrored pin conventions; a back-side part is
    simply a part whose lands are drawn on B.Cu at their top-view coordinates."""
    pins = []
    for term, (dx, dy, w, h) in SMD_FP[fp].items():
        rx, ry = rot(dx, dy, rotation)
        pins.append(Pin(ref, term, x + rx, y + ry, ("rect", w, h),
                        prot=rotation))
    return Part(ref, fp, side, pins, oid, rotation)


def tht(ref, holes, oid=0, hole=HOLE_LED, ring=RING_LED, dual=False) -> Part:
    """A through-hole part: holes = [(term, x, y), ...] in board coordinates."""
    pins = [Pin(ref, t, x, y, ("tht", hole, ring), dual) for t, x, y in holes]
    return Part(ref, "THT", "front", pins, oid)


# ---------------------------------------------------------------------------
# PLACEMENT (SPEC "Layout notes" + the KiCad rounds' measured resolutions)
# ---------------------------------------------------------------------------
RES_PKG = {1: "R1206", 2: "R0805", 3: "R0603", 4: "R1206", 5: "R0805",
           6: "R0603", 7: "R1206", 8: "R0805", 9: "R0603", 10: "R1206",
           11: "R0805", 12: "R0603"}

# Every leg of a 4-leg tactile switch: the schematic knows two pins, the part
# has four legs internally shorted in pairs.  The twin legs carry the SAME net
# (that is a fact about the part, not an assumption), and the pre-placed link
# tracks below make the router see what the plastic already does.
LEG_TWIN = {"1": "1B", "2": "2B"}


def build_parts() -> list[Part]:
    """The whole board, in one table.  Object ids are assigned from a fixed
    counter so two runs produce byte-identical files."""
    parts: list[Part] = []
    oid = 1000

    def add(p):
        nonlocal oid
        p.oid = oid
        oid += 40
        parts.append(p)
        return p

    # --- the ring: 12 LEDs, cathode INWARD (the silk tick's whole job) -------
    for pos in range(1, 13):
        led = POS_LED[pos]
        ang = pos_angle(pos)
        kx, ky = polar(RING_CX, RING_CY, ang, LEAD_R_IN)
        ax, ay = polar(RING_CX, RING_CY, ang, LEAD_R_OUT)
        # OPERATOR RULING (Bill, 2026-08-01), recorded verbatim:
        #
        #   "seating an LED 1.5mm proud for an under-flange front joint is
        #    EXTREMELY annoying by hand — ten promoted LED anodes is
        #    unacceptable. A bare wire via (open access, both faces) is
        #    jumper-class and fine. Therefore: LED leads may no longer be
        #    layer bridges."
        #
        # So an LED lead is an ordinary back-only through terminal now: every
        # LED seats FLUSH and is soldered on the BACK alone, and the layer
        # changes the ten anode joints used to carry are bought with wire vias,
        # which the operator ruled are jumper-class and unbudgeted.
        #
        # HOW the dead front ring is shown to the router is the whole story,
        # and it was MEASURED the expensive way first.  Dropping `dual` alone
        # makes emit_dsn fence each of the 24 rings with a KEEPOUT, like it
        # does for SW1/BZ1/S1/S2 — and that board cannot be routed at all:
        # FreeRouting 2.2.4 reports 23 unrouted at pass #1 (against 8 for the
        # whole board before) and WEDGES inside pass #3, writing no session
        # ever.  Three runs: unbounded 60 min (killed by our own subprocess
        # timeout), MAX_PASSES=5 (7 min), JOB_TIMEOUT=00:02:00 (5.5 min — that
        # timeout is only honoured BETWEEN passes, so it never fires).  Before:
        # 20.10 seconds and a session.  This file's "interior keepouts are
        # POISON to this router" law at its strongest: those ten promotions
        # WERE the router's way across the ring, and 24 keepouts where they
        # used to be leaves it nothing but a fence.
        #
        # The ruling that unblocked it (operator, 2026-08-01): the wedge is the
        # KEEPOUTS, not the copper.  So an LED's dead front ring goes into the
        # DSN as VISIBLE COPPER — a protected wire on its own single-member
        # `__dead_<pid>` pseudo-net (see emit_dsn) — which FreeRouting treats
        # as an ordinary foreign-copper clearance obstacle and not as topology.
        # The router gets the ring area back as routable space minus 24 round
        # obstacles, and pays for its crossings in wire vias, which is the
        # intended outcome.  The 13 keepouts that predate the wedge (SW1, BZ1,
        # S1, S2) are UNCHANGED — they never caused it.
        add(tht(f"LED{led}", [("1", kx, ky), ("2", ax, ay)]))

    # --- 12 series resistors, RADIAL around U1 ------------------------------
    # Tangential 1206s were MEASURED to wall off 8 of the 12 crossing gaps and
    # to read 0.374 against their neighbour.  Radial, with the per-package
    # radius set so the outer land edge stops at RES_OUTER.
    for pos in range(1, 13):
        led = POS_LED[pos]
        fp = RES_PKG[led]
        outer = RES_OUTER_1206 if fp == "R1206" else RES_OUTER
        r = outer - SMD_LEN[fp] / 2
        ang = pos_angle(pos)
        cx, cy = polar(RING_CX, RING_CY, ang, r)
        # rotation = ang + 180 puts pin 1 OUTWARD, facing the LED.  Pin 1 is
        # LEDn_K, the same net as the cathode hole directly outboard of it, so
        # that net is a short radial hop; the charlieplex line reaches pin 2 on
        # the inside, where U1 is.  The other way round (pin 1 inward) parks a
        # LINE-net pad against a cathode ring of a different net and makes the
        # cathode net cross its own resistor — MEASURED as DRC shorts.
        add(smd(f"R{led}", fp, cx, cy, ang + 180.0))

    # --- the brain and its neighbours, inside the ring on the BACK ----------
    # U1 stays at rotation 0, and R4b TRIED the alternative and rejected it on
    # measurement.  The case for 180 looked strong: pins 1-3 are RESET, L3 and
    # SND, every load they serve sits in the RIGHT half of the board, and at
    # rotation 0 those three pins face LEFT — the router dragged RESET out to
    # x 6.6 and spent two wire vias getting back.  But 180 (with C2 point-
    # reflected to (22, 22.6) to keep the 2.96 mm decoupling link) put all
    # eight U1 pins into the same corridors as the bottom-strip traffic and
    # FreeRouting thrashed: 15 MINUTES without converging, against 27 seconds
    # at rotation 0.  A placement the router cannot solve is not an
    # improvement, whatever the wire lengths say.
    add(smd("U1", "SOIC8W", RING_CX, RING_CY, 0.0))
    # C2 rotated 180 so its VCC land (pin 1) faces U1 pin 8: 2.96 mm link.
    # y 29.4, not 29.8: pulling the 1206 ring resistors inward (see
    # RES_OUTER_1206) walked their inner lands to within 0.369 of C2 — caught
    # by the independent clearance scan, NOT by pcb-rnd.  C2 moves toward U1
    # instead, which also shortens the VCC link it exists to make short.
    # C2 STAYS at the ring centre line, and the alternative was MEASURED and
    # rejected on 2026-08-02.  The case for moving it was strong: with the LED
    # anodes no longer layer bridges, U1-1 (RESET) has exactly one back escape,
    # a 3.00 mm CUL-DE-SAC at bearing 65, and no wire via could be threaded
    # into it because C2's pin-2 land sat 1.77 mm away where a via a human can
    # solder needs 2.72 (SPEC "Via geometry": 1.5 mm of body clearance plus the
    # ring).  Sweeping C2 over its whole legal neighbourhood — every position
    # keeping the SPEC decoupling link and passing the clearance scan — the
    # cul-de-sac opens at dx >= +2.50: a via at 3.00 mm from U1-1 with 2.509 mm
    # of clearance, against 1.77 blocked before, and it shortened the SPEC
    # decoupling link from 2.96 to 1.51 mm into the bargain.
    # It still loses, because the ROUTER is the scarcer resource: with C2 at
    # +2.75 FreeRouting wedges in pass #4 instead of #9 and the last completed
    # pass leaves 19 connections open, against 8 with C2 where it is.  Trading
    # 8 stragglers for 19 to unblock one pin is not a trade.  The wedge point
    # moves with ANY change to this board — that is the standing hazard here,
    # and it is why every geometry move is measured against the router and not
    # just against the clearance law.
    add(smd("C2", "C0805", RING_CX, RING_CY + 3.4, 180.0))
    # R13 + C4 sit OUTSIDE the ring near its rim, reachable from U1.1 through
    # the gap between two LED sectors.  Both slid LEFT along the strip when the
    # ring grew (2026-08-01): the enlarged ring's lower-right leads sit at
    # (33.2, 14.0) and the 1206 could no longer stand under them, and at its old
    # x it collided with the ISP pin-1 silk tick.  R13 keeps its VCC land within
    # reach of the spine end (x 29.9); C4 keeps the RESET node compact.
    add(smd("R13", "R0603", *R13_XY, 0.0))
    add(smd("C4", "C1206", *C4_XY))
    # Power parts at the entry.  Q1 moved +0.5 in x to open a 0.96 mm gap to
    # C1's new VCC land (it was 0.46), and the VCC rail's origin follows it —
    # fixed_tracks() reads Q1_XY, so the rail can never start off the pin.
    add(smd("Q1", "SOT23", *Q1_XY))
    # C1 ROTATED 180 and moved so its pin-1 (VCC) land is CENTRED ON the fixed
    # VCC rail's descent at x 14.  That closes the last of R4b's six residues by
    # geometry instead of copper: C1-1 was a singleton because C1-2's own GND
    # land walls the VCC land off from the rail, and a pre-placed branch around
    # it was MEASURED to cost four other connections (see fixed_tracks).  A pad
    # the rail already runs through costs nothing, adds no obstacle, and is the
    # same joint the bench would solder anyway.  Pin 2 (GND) faces away, into
    # the pour.
    add(smd("C1", "C0805", *C1_XY, 180.0))
    # ... driver at the load: the buzzer cell lives on the BACK, under BZ1's
    # front-side body, which is empty back copper.
    # The buzzer cell keeps its internal geometry and moves as a BLOCK with the
    # right strip (+7 in x, +4 in y, 2026-08-01): the grown ring's copper now
    # reaches x 43.67, and R14 at its old x would have stood inside the LED
    # leads.  R14-2/Q2-2 remains the board's tightest different-net pair at
    # 0.45, exactly as measured before the move.
    # The driver cell is packed into the band y 23..31 ON PURPOSE (2026-08-01):
    # the two button nets can only enter their buttons from the outboard leg
    # column, so the lanes at y 35.4..37.1 (to S2) and y 22.5..25.0 (to S1) are
    # RESERVED ROUTING SPACE and no part may stand in them.  D1 above Q2 (its
    # first home) put a land 0.24 mm into the S2 lane and the router abandoned
    # L2; below Q2 it is 9 mm clear of it.
    add(smd("R14", "R0805", 47.0, 30.0, 0.0))
    add(smd("Q2", "SOT23", 52.0, 30.0, 0.0))
    add(smd("D1", "SOT23", 52.0, 25.0, 0.0))
    # C3 decouples the buzzer rail and may sit anywhere on it, so it is the part
    # that YIELDS when the cell gets tight.  Packed in beside Q2 it left the
    # R14 -> Q2 base hop with 0.298 mm of room (the law is 0.400) and put ZERO
    # legal wire-via positions within 5 mm of Q2-3 — a cell so dense that
    # neither the router nor a hand route could finish it.  At (49, 34) it is
    # 0.97 mm clear of the reserved L2 lane and the cell breathes.
    # C3 moved to (51.5, 27.5) on 2026-08-02, and this part is the DESIGNATED
    # one to move: it decouples the buzzer rail and may sit anywhere on it, so
    # it yields when the cell gets tight — which is exactly what happened.  At
    # (49, 34) the router walled its VCC land in completely: C3-1 came out as a
    # one-land island and neither the closer nor its path search could reach it
    # (measured: NO LEGAL PATH on either face).  Sweeping every position that
    # keeps the clearance scan clean, stays out of the two reserved button
    # lanes, and does not put C3's body on top of D1 or Q2 — a collision the
    # clearance scan CANNOT see, because it skips same-net pairs and would
    # happily overlap C3-1 onto D1-2 — the new seat puts C3-1 1.56 mm from the
    # nearest VCC terminal instead of 8, a hop the router closes on its own.
    # C3 STAYS at (49, 34).  Two alternatives were measured on 2026-08-02 and
    # both lose: at (51.5, 27.5) — the roomiest seat in a full sweep — the
    # router wedges in pass #2 with 20 unrouted; and seating it so its VCC land
    # MERGES with D1-2's (the C1 trick, connection by construction, no routing
    # demand at all) lets the router finish but costs three other connections
    # where it was buying one.  The wedge point and the residue both move with
    # any change to this cell, so the part that "yields" only helps if the
    # router agrees, and here it does not.
    add(smd("C3", "C0603", 49.0, 34.0, 0.0))
    add(smd("R15", "R1206", *R15_XY, 90.0))
    add(smd("R16", "R0603", *R16_XY, 90.0))

    # --- ISP: six bare Ø1.8 pads on B.Cu, standard 2x3 AVR grid -------------
    #   TP1 MISO  TP2 VCC        pin 1 top-left, square-ticked in back silk
    #   TP3 SCK   TP4 MOSI
    #   TP5 RST   TP6 GND       (grid origin and its measured x0 at ISP_X0)
    for i, ref in enumerate(("TP5", "TP6", "TP3", "TP4", "TP1", "TP2")):
        px = ISP_X0 + ISP_PITCH * (i % 2)
        py = ISP_Y0 + ISP_PITCH * (i // 2)
        add(Part(ref, "ISP", "back",
                 [Pin(ref, "1", px, py, ("circ", ISP_PAD))], 0))

    # --- bottom strip, front THT -------------------------------------------
    # PAD1 (+) is the RIGHT pad and PAD2 (-) the left, transposing SPEC's
    # layout note.  FORCED, and measured: the VBAT corridor runs rightward from
    # the + pad to SW1 at CORRIDOR_Y, and passing beneath a Ø3.6 ring needs
    # 1.8+0.4+0.4 = 2.6 mm of vertical room where the strip has 1.7.  Which
    # physical pad is + is a layout degree of freedom; the corridor's window is
    # not.  The silk "+"/"-" legend follows the layout.
    add(tht("PAD1", [("1", 16.0, 4.0)], hole=HOLE_PAD, ring=RING_PAD, dual=True))
    add(tht("PAD2", [("1", 10.0, 4.0)], hole=HOLE_PAD, ring=RING_PAD, dual=True))
    # SW1: blade 1 = VSW (left, over the corridor), 2 = VBAT common (middle),
    # 3 = the unused throw (right, away from the corridor).
    add(tht("SW1", [("1", SW1_X - SW1_PITCH, SW1_Y), ("2", SW1_X, SW1_Y),
                    ("3", SW1_X + SW1_PITCH, SW1_Y)],
            hole=HOLE_SW1, ring=RING_SW1))

    # --- right strip, front THT --------------------------------------------
    for ref, (bx, by) in BUTTONS.items():
        add(tht(ref, [("2", bx - BTN_DX, by - BTN_DY),
                      ("2B", bx - BTN_DX, by + BTN_DY),
                      ("1", bx + BTN_DX, by - BTN_DY),
                      ("1B", bx + BTN_DX, by + BTN_DY)],
                hole=HOLE_BTN, ring=RING_LED))
    add(tht("BZ1", [("1", BZ1_XY[0], BZ1_XY[1] - 3.8),
                    ("2", BZ1_XY[0], BZ1_XY[1] + 3.8)],
            hole=HOLE_BZ, ring=RING_LED))
    return parts


# ---------------------------------------------------------------------------
# NETLIST RECONCILIATION — the schematic is ground truth, and disagreement is
# fatal.  Article I's habit applied to a board: refuse what you cannot model.
# ---------------------------------------------------------------------------
NO_CONNECT = {"SW1-3"}       # SPEC: the slide switch's unused throw


def bind_nets(parts: list[Part], nets: dict[str, list[str]]) -> dict:
    """Attach schematic nets to model pins, or die naming the exact mismatch.

    A schematic pin may map to SEVERAL physical terminals (the tactile
    switches' twin legs); every physical terminal must map back to exactly one
    schematic pin, or be a declared no-connect.
    """
    by_pid = {p.pid: p for part in parts for p in part.pins}
    twins = {f"{ref}-{a}": f"{ref}-{b}" for ref in ("S1", "S2")
             for a, b in LEG_TWIN.items()}
    missing, out = [], {}
    for net, pids in sorted(nets.items()):
        physical = []
        for pid in pids:
            if pid not in by_pid:
                missing.append(f"schematic {net}:{pid} has no pad in the layout")
                continue
            physical.append(pid)
            by_pid[pid].net = net
            tw = twins.get(pid)
            if tw:
                if tw not in by_pid:
                    missing.append(f"{pid}: twin leg {tw} missing")
                else:
                    by_pid[tw].net = net
                    physical.append(tw)
        out[net] = sorted(physical)
    orphan = sorted(p for p, pin in by_pid.items()
                    if pin.net is None and p not in NO_CONNECT)
    if missing or orphan:
        for m in missing:
            print(f"NETLIST MISMATCH: {m}", file=sys.stderr)
        for o in orphan:
            print(f"NETLIST MISMATCH: pad {o} is on no schematic net",
                  file=sys.stderr)
        raise SystemExit("refusing to emit a board the schematic does not describe")
    return out


# ---------------------------------------------------------------------------
# BOARD-LEVEL GEOMETRY: outline, pours, dead islands, fixed tracks
# ---------------------------------------------------------------------------
def rounded_rect(inset: float = 0.0, seg: int = 12) -> list[tuple[float, float]]:
    """Board outline (or an inset of it) as a closed polyline, CCW.

    Quarter-arcs are chorded, never arc objects: the chords lie INSIDE the true
    arc, so the ink can never escape the endpoint extents that WS2 derives the
    raster window from (SPEC "Layout notes", first bullet).  Sag at seg=12 is
    0.004 mm, four times under the simulation pixel.
    """
    r = max(CORNER_R - inset, 0.0)
    x0, y0 = inset, inset
    x1, y1 = BOARD_W - inset, BOARD_H - inset
    pts = []
    for (cx, cy), a0 in (((x1 - r, y0 + r), -90.0), ((x1 - r, y1 - r), 0.0),
                         ((x0 + r, y1 - r), 90.0), ((x0 + r, y0 + r), 180.0)):
        for k in range(seg + 1):
            pts.append(polar(cx, cy, a0 + 90.0 * k / seg, r))
    return [(q(x), q(y)) for x, y in pts]


def circle_pts(cx: float, cy: float, r: float, seg: int = 16):
    return [(q(x), q(y)) for x, y in
            (polar(cx, cy, 360.0 * k / seg, r) for k in range(seg))]


# The pour's declared holes must lie WHOLLY INSIDE the pour, and this is checked
# at emit time rather than in a gate because the failure it guards is SILENT:
# a hole that crosses its polygon's boundary is degenerate, pcb-rnd's boolean
# gives up on it once there is enough other copper to subtract, and the answer
# is a DISCARDED POUR that still parses, still passes DRC, and only shows up as
# connectivity nobody can explain (MEASURED 2026-08-01 — see MOUNT_INSET).  A
# generator that can emit that file should refuse to, not report it later.
POUR_HOLE_INSIDE_MIN = 0.30      # same margin class as POUR_HOLE_MARGIN


def assert_pour_holes_inside() -> float:
    """-> the tightest margin.  Raises if any M3 keep-out escapes the pour."""
    edge = rounded_rect(POUR_EDGE_SETBACK)     # the pour's ACTUAL boundary
    worst, who = 99.0, None
    for ref, (mx, my) in MOUNTS.items():
        d = min(pt_seg(mx, my, a[0], a[1], b[0], b[1])
                for a, b in zip(edge, edge[1:] + edge[:1])) - MOUNT_KEEPOUT_R
        if d < worst:
            worst, who = d, ref
    if worst < POUR_HOLE_INSIDE_MIN:
        raise SystemExit(
            f"refusing to emit: {who}'s Ø{2 * MOUNT_KEEPOUT_R} copper keep-out "
            f"reaches {-worst:.3f} mm past the pour boundary (needs "
            f"{POUR_HOLE_INSIDE_MIN} inside) — the pour would be silently "
            f"discarded.  Move the mount inward (MOUNT_INSET).")
    return worst


def fixed_tracks() -> list[tuple]:
    """(layer, net, width, [(x, y), ...]) — pre-placed and PROTECTED.

    These four runs are not routing; they are geometry the board cannot survive
    without, and the KiCad rounds proved it by measuring the corridor's legal
    window at 0.15 mm while leaving it to a router that could move it.  R4b
    must treat them as fixed (DSN `(type protect)`).

      VBAT corridor  the power entry, threaded under SW1's VSW blade
      VCC spine      the rail out of the entry, ending at x<=29.9 by contract
      leg links x4   what the tactile switches' plastic already does: the twin
                     legs are internally one node, so the router must not spend
                     a track (or worse, a via) discovering it
    """
    out = [
        ("bottom", "VBAT", RAIL,
         [(16.0, 4.0), (16.0, CORRIDOR_Y), (SW1_X, CORRIDOR_Y), (SW1_X, SW1_Y)]),
        # The VCC rail runs from the regulator output ALL THE WAY to the spine,
        # not just along the spine.  MEASURED why: the VBAT corridor at y 2.3
        # spans x 16..27 and walls the spine in, so the only way onto it from
        # above is round one of its ends — and the router never found the trip.
        # It left VCC in three pieces (Q1-2+C1-1 | spine | R13-1 D1-2 C3-1
        # TP2-1 BZ1-1), i.e. the regulator was not connected to the load.  The
        # dog-leg west of PAD1 and PAD2 is the one lane that crosses the strip
        # without touching VBAT.  Both jogs are forced and both were MEASURED:
        # leaving Q1-2 downward drives the rail straight through Q1-1's GND
        # land (-0.400), so it exits LEFT; and the descent must sit in
        # x 12.51..13.49 to clear PAD1 and PAD2's Ø3.6 rings by 0.4, while
        # passing C1-2's GND land needs x >= 13.93 — no single x satisfies
        # both, which is why the lane steps in at y 7.0.  x 13.0 then clears
        # both wire pads by 0.80 and the VBAT corridor by 0.60.
        # The rail now also PASSES THROUGH C1's pin-1 land at (14.0, 9.0), which
        # is why C1 was rotated and re-seated there (see build_parts): the
        # descent leg is VCC and so is the land, so the contact is copper the
        # bench already has to make, not a branch the router has to work around.
        ("bottom", "VCC", RAIL,
         [(Q1_XY[0] - 1.5, Q1_XY[1] + 0.95), (VCC_DESCENT_X, 10.45),
          (VCC_DESCENT_X, 7.0), (13.0, 7.0),
          (13.0, SPINE_Y), (SPINE_X1, SPINE_Y)]),
        # (VCC's eastward extension is board-only: see board_only_tracks)
        # NOT a branch to C1-1.  C1's VCC land is walled off from the rail by
        # C1-2's own GND land 2.07 mm away and the router leaves it a
        # singleton, and a legal pre-placed branch down the west side of C1
        # into the rail's corner does close it — at a cost of FOUR other
        # connections (6 rat lines became 10, and the router dropped from 10
        # wire vias to 3, i.e. it stopped using the front face at all).  Same
        # lesson as the U1-4 stub below: on a board this tight, protected
        # copper is an obstacle, and buying one connection with four is not a
        # trade.  MEASURED both ways; the cheaper board is the one without it.
        # S1_R: R15's series pad to S1's blade row.  2.75 mm of straight, empty
        # B.Cu that the router failed to find on every pass of every attempt,
        # leaving R15-1 a singleton.  The leg link it lands on is fixed too, so
        # this makes the whole S1_R node one pre-placed piece.
        ("bottom", "S1_R", TRACK,
         [(q(R15_XY[0]), q(R15_XY[1] - 1.55)),
          (q(BUTTONS["S1"][0] - BTN_DX), q(BUTTONS["S1"][1] + BTN_DY))]),
        # U1-4 is the brain's GND pin and it sits INSIDE U1's own pin field,
        # with seven escaping traces between it and open copper — the router
        # spent a via trying and still reported it open, and pcb-rnd hung two
        # rat lines on it (to the back pour and to C2-2).  A stub out of it was
        # BUILT and then REJECTED, and the rejection is the finding: the widest
        # lane out of U1-4 is bearing 320 (measured by sweeping every bearing;
        # 0.946 mm clear, where straight down would have sat 0.095 from R1's
        # 1206 land), and a protected stub along it does close both rat lines —
        # but pre-placed copper inside the ring is an obstacle FreeRouting
        # cannot move, and it turned a 32-second route into 20+ minutes with no
        # convergence.  Two rat lines is the cheaper honest residue.  The real
        # fix is a placement that gives U1-4 an escape, not a protected stub.
        #
        # THE BUZZER CELL'S GND ESCAPE (2026-08-02).  Q2-2 and C3-2 are the
        # driver cell's two GND lands, and on the scrub-grown board they came
        # out as a 4.0 mm2 island joined to nothing: pcb-rnd hung the board's
        # last rat line on them.  MEASURED on the raster — the live back plane
        # is 1386.2 mm2 and its closest approach to that island is 4.213 mm,
        # so this is not a pinched fill channel to widen.  The pour has been
        # pushed clean out of the cell by FIFTEEN foreign-net back tracks
        # (SND_B off Q2-1, SND_C off Q2-3, VCC into C3-1, plus the long runs at
        # y 35.23 and y 36.74).  The cell is where four foreign nets converge,
        # so every reroute fences it again — which is why the answer is not a
        # placement nudge and not a wire via.
        #
        # A WIRE VIA IS IMPOSSIBLE HERE, and that was measured before this was
        # written: SPEC's via geometry keeps a threaded via 1.5 mm clear of an
        # SMD land, the orphan IS two SMD lands, and a sweep found ZERO legal
        # via seats within 7 mm of it — before even asking for a track to reach
        # one.  The reserve stitch cannot be spent on a defect like this.
        #
        # So the cell gets a PRE-PLACED, PROTECTED GND escape instead, and
        # being GND it WELDS into the back pour along its whole length
        # (emit_lihata: clearpoly False for GND) rather than cutting a moat
        # through it.  That is what makes it deterministic: wherever the fill
        # survives beside ANY part of this run, both lands are connected, so it
        # does not depend on where the router happens to fence next time.
        #
        # The geometry was measured, not chosen.  Bearing and length were swept
        # against every other net's copper on the unrouted board: the run
        #
        #     ("bottom", "GND", TRACK,
        #      [(50.5, 30.95), (49.8625, 34.0), (56.52, 31.837)])
        #
        # holds 0.887 mm to the nearest foreign copper (law 0.40) and stays OUT
        # of both reserved routing lanes — 2.84 mm below L2's lane into S2
        # (y 35.4..37.1) and 6.6 mm above L1's into S1 (y 22.5..25.0).  The
        # roomier northern bearings were rejected for sitting 0.09 mm off the
        # L2 lane, which is this file's oldest mistake wearing a new hat.  The
        # board gate passed it 21/21 with the tightest gap unmoved at 0.479.
        #
        # AND IT IS NOT EMITTED, because the ROUTER rejected it — the same way,
        # and for the same reason, as the U1-4 stub above.  MEASURED: with this
        # run protected in the DSN, FreeRouting completes six passes at its
        # usual 1.3-2.1 s each (22, 23, 16, 12, 10, 11 unrouted) and then WEDGES
        # inside pass #7, still running at 74 s where a healthy pass finishes
        # under 2.5.  Bounding at the last completed pass returns a session 10-11
        # connections short, which this board has already proven the closer
        # cannot finish (that route came back with 3 rat lines, an overlapping-
        # hole DRC hit, and a pour cutout inside a mount keep-out).
        #
        # So BOTH stubs this file has ever tried say the same thing, and it is
        # worth stating as a law rather than an anecdote: on this board a
        # pre-placed protected track that crosses routing space is an obstacle
        # FreeRouting cannot move, and it costs convergence — the cost does not
        # depend on whether the copper is inside the ring or out in the right
        # strip.  Pre-placed copper belongs where the router would never go
        # anyway (the bottom strip's power stack) or where it replaces routing
        # demand outright (the leg links), never where it merely helps.
    ]
    # The leg links, drawn from the SAME table build_parts() places the pads
    # from, PAD CENTRE to PAD CENTRE — see BUTTONS for why they may not be
    # trimmed, and why S2 moved instead.
    for ref, net_l, net_r in (("S1", "S1_R", "L3"), ("S2", "S2_R", "L2")):
        bx, by = BUTTONS[ref]
        for dx, net in ((-BTN_DX, net_l), (+BTN_DX, net_r)):
            out.append(("bottom", net, TRACK,
                        [(q(bx + dx), q(by - BTN_DY)),
                         (q(bx + dx), q(by + BTN_DY))]))
    return out


def board_only_tracks() -> list[tuple]:
    """(layer, net, width, [(x, y), ...]) — copper the BOARD carries and the
    ROUTER is never shown.  Same shape as fixed_tracks(), and every oracle that
    measures copper (clearance_scan, pour_hole_scan, the emitted lihata) counts
    it; only emit_dsn leaves it out.

    VSW — SW1's blade to the regulator input — is the sole member, and it is
    here rather than in fixed_tracks() because BOTH ways of telling the router
    about it were MEASURED and both are worse:

      * as a `(type protect)` wire at RAIL width, FreeRouting stops converging
        altogether: 240 s without finishing a pass, against 14 s for the same
        board without it (the same pathology this file already records for
        protected copper inside the ring).  At TRACK width it converges but
        surrenders 7 more connections;
      * as a path keepout reserving the corridor, it converges in 18 s but
        still costs 5 connections, because the Q1-to-SW1 corridor is the artery
        between the power entry and the bottom strip.

    And it must be SOMEWHERE, because FreeRouting will not route this net at
    all: the session carries no VSW wire and the router does not list it as
    unrouted either — it believes the two terminals are already one node, while
    pcb-rnd, which measures metal, correctly hangs a rat line on them.

    The risk of hiding copper from the router is that it routes something else
    through the same space, and that risk is REAL — MEASURED: told nothing, the
    router laid GND through this corridor and pcb-rnd read "SHORT: net GND is
    shorted to VSW at terminal Q1-3".  So the space is RESERVED with a path
    keepout in the DSN (emit_dsn) while the copper itself stays out of the
    router's wiring.  The keepout costs the router options, which is why Q1 was
    aligned over SW1's blade first: the reservation is one 4.5 mm vertical drop,
    not an L across the artery.  Both oracles still measure this copper like any
    other, so a trespass fails the gate rather than shipping.
    """
    return [("bottom", "VSW", RAIL,
             [(q(Q1_XY[0] + 1.5), q(Q1_XY[1])),
              (q(Q1_XY[0] + 1.5), q(SW1_Y))]),
            # VCC EAST — the spine reaching its loads.  SPEC's "spine ends
            # x<=29.9" was written for a 56 mm board whose VCC loads all sat
            # west of 30; on the grown board they sit at x 35..58, and a rail
            # that stops at 29.9 leaves the router to invent the whole east-half
            # distribution.  MEASURED across three geometries: VCC lands in 2 to
            # 4 islands every time, and every closure joining them along the
            # bottom edge fences a pour pocket instead.
            ("bottom", "VCC", RAIL,
             [(q(SPINE_X1), q(SPINE_Y)), (q(R13_VCC_X), q(SPINE_Y)),
              (q(R13_VCC_X), q(R13_XY[1]))])]


def dead_front_rings(parts: list[Part]) -> list[tuple]:
    """(pid, x, y, dia, joins_pour) for every THT terminal's FRONT ring.

    The ring is real copper on the milled board but belongs to no terminal —
    that separation is the whole R3 finding.  A ring whose net is GND JOINS the
    front pour instead of clearing it: the front plane has no other conductor
    to hang from, so it is dead copper until R4b promotes that lead to a
    dual-solder joint.  On orbit exactly one through-hole is on GND (PAD2, the
    minus wire pad), which makes PAD2-1 a MANDATORY promotion for R4b.
    """
    out = []
    for part in parts:
        for p in part.pins:
            if p.kind == "tht":
                out.append((p.pid, p.x, p.y, p.shape[2], p.net == "GND"))
    return out


# ---------------------------------------------------------------------------
# SILK — a stroke font we own outright
# ---------------------------------------------------------------------------
# On this board the front legend is FUNCTIONAL, not decoration: 12 cathode
# ticks are the only thing that tells the operator which way the LEDs go in
# (SPEC "silkscreen").  So the glyphs are emitted as ordinary silk LINES from
# the table below rather than as pcb-rnd text objects: line geometry is exact
# and font-independent, which means the height (1.5) and stroke (0.25, Makera's
# floor) are the numbers SPEC states, not whatever an embedded font renders.
# Glyph lattice is 3 wide x 5 tall; uniform scale h/5 gives a 0.6h-wide glyph
# and a 0.85h advance (the width factor the KiCad rounds used).
GLYPHS = {
    "A": "0,0 0,4 1,5 2,5 3,4 3,0|0,2 3,2",
    "B": "0,0 0,5 2,5 3,4 3,3.5 2,2.5 0,2.5|2,2.5 3,1.5 3,1 2,0 0,0",
    "C": "3,4 2,5 1,5 0,4 0,1 1,0 2,0 3,1",
    "D": "0,0 0,5 2,5 3,4 3,1 2,0 0,0",
    "E": "3,5 0,5 0,0 3,0|0,2.5 2,2.5",
    "F": "3,5 0,5 0,0|0,2.5 2,2.5",
    "G": "3,4 2,5 1,5 0,4 0,1 1,0 2,0 3,1 3,2 2,2",
    "H": "0,5 0,0|3,5 3,0|0,2.5 3,2.5",
    "I": "0,5 3,5|1.5,5 1.5,0|0,0 3,0",
    "J": "3,5 3,1 2,0 1,0 0,1",
    "K": "0,5 0,0|3,5 0,2|1,3 3,0",
    "L": "0,5 0,0 3,0",
    "M": "0,0 0,5 1.5,3 3,5 3,0",
    "N": "0,0 0,5 3,0 3,5",
    "O": "1,5 2,5 3,4 3,1 2,0 1,0 0,1 0,4 1,5",
    "P": "0,0 0,5 2,5 3,4 3,3 2,2 0,2",
    "Q": "1,5 2,5 3,4 3,1 2,0 1,0 0,1 0,4 1,5|1.8,1.2 3,0",
    "R": "0,0 0,5 2,5 3,4 3,3 2,2 0,2|1.8,2 3,0",
    "S": "3,4 2,5 1,5 0,4 0,3 1,2.5 2,2.5 3,2 3,1 2,0 1,0 0,1",
    "T": "0,5 3,5|1.5,5 1.5,0",
    "U": "0,5 0,1 1,0 2,0 3,1 3,5",
    "V": "0,5 1.5,0 3,5",
    "W": "0,5 0.8,0 1.5,3 2.2,0 3,5",
    "X": "0,5 3,0|0,0 3,5",
    "Y": "0,5 1.5,2.5 3,5|1.5,2.5 1.5,0",
    "Z": "0,5 3,5 0,0 3,0",
    "0": "1,5 2,5 3,4 3,1 2,0 1,0 0,1 0,4 1,5",
    "1": "0.5,4 1.5,5 1.5,0|0.3,0 2.7,0",
    "2": "0,4 1,5 2,5 3,4 3,3 0,0 3,0",
    "3": "0,5 3,5 1.5,3 3,2 3,1 2,0 1,0 0,1",
    "4": "2.4,0 2.4,5 0,1.5 3,1.5",
    "5": "3,5 0,5 0,3 2,3 3,2 3,1 2,0 1,0 0,1",
    "6": "3,4 2,5 1,5 0,4 0,1 1,0 2,0 3,1 3,2 2,3 1,3 0,2",
    "7": "0,5 3,5 1,0",
    "8": "1,5 2,5 3,4 2,2.5 1,2.5 0,4 1,5|1,2.5 0,1 1,0 2,0 3,1 2,2.5",
    "9": "0,1 1,0 2,0 3,1 3,4 2,5 1,5 0,4 0,3 1,2 2,2 3,3",
    "-": "0.5,2.5 2.5,2.5",
    "+": "1.5,1 1.5,4|0.5,2.5 2.5,2.5",
    ".": "1.2,0 1.8,0",
    "/": "0,0 3,5",
    " ": "",
}

# Both button legends are SQUEEZED between their own button and the buzzer, and
# 2026-08-02's ring growth is what made that a measurement rather than a
# comfort.  CATCH sits above S1 with BZ1-1 above it; START sits below S2 with
# BZ1-2 below it; the arrangement is mirror-symmetric, so one number serves
# both and one number moves both.
#
# At 4.8 the near edge of each label stood 1.650 mm from the buzzer ring's
# centre.  Growing that ring from Ø2.44 to Ø2.50 moves its edge out 0.03 and
# leaves 0.276 against the 0.30 silk law — MEASURED as the ONE check the scrub
# growth broke (front silk, 20/21).  Moving the buzzer is not the answer and
# the record says why: BZ1's y is already pinned from below by this very
# legend ("dropping it to 28.5 ... walked its lower ring into the CATCH
# legend", see BZ1_XY), so the part that yields is the label.
#
# 4.72 was the max-min seat on the 64 x 54 board.  SWEPT against both
# neighbours (law 0.300): the legal window was 4.66..4.77, pulled in toward its
# button the label closes on S1/S2's own leg rings and pushed out it closes on
# the buzzer, and the two constraints crossed at 4.72 holding 0.345 to the leg
# rings and 0.356 to the buzzer.  That was inside the crowding audit's 25% band
# and could not be improved from there — see BUTTONS, where the copper move
# that would open it was measured illegal against flip gauge G4.
#
# 5.03 IS THE RIGHT ANSWER ON A GROWN BOARD, and the board is grown, so it is
# the answer now.  The seat is DERIVED, not swept, because the geometry is a
# one-dimensional squeeze and its max-min has a closed form.  Reading up from
# S1 (START is the mirror image about the buzzer, so one number serves both):
#
#   leg ring top      S1y + BTN_DY + RING_LED/2      = S1y + 3.500
#   label ink bottom  S1y + R - SILK_H/2 - SILK_W/2  = S1y + R - 0.875
#   label ink top     S1y + R + 0.875
#   BZ1 ring bottom   BZ1y - 3.8 - RING_LED/2        = 24.950
#
# so the two gaps are R - 4.375 and 24.075 - S1y - R, equal at R = (28.45 -
# S1y)/2 — which is 4.725 at the old S1y 19.0 and 5.025 at the new 18.4.  5.03
# is that value on this file's 0.01 grid and it lands 0.655 against the leg
# rings and 0.645 against the buzzer, both +115% or better over the 0.30 law
# where the audit asks for +25%.  The 0.01 of asymmetry is rounding, not
# design; sweeping it away would buy 0.005 mm and cost the closed form.
BUTTON_LEGEND_R = 5.03
SILK_H = 1.5             # SPEC: text 1.5 mm
SILK_W = 0.25            # SPEC: stroke 0.25, Makera's floor
# 1.05, up from 0.85, and the glyph GAP law is what sets it.  The ink left
# between neighbouring glyphs is (ADVANCE - GLYPH_WIDE) * h - SILK_W, and
# checks.SILK_GAP_MIN wants >= 0.15 (JLCPCB's floor raised for laser bloom).
# At 0.85 that arithmetic gives 0.125 for the 1.5 mm legend — scraping a bar it
# only cleared on the check's 2-pixel tolerance — and EXACTLY ZERO for the
# 1.0 mm ISP labels, which the double-sided gate measured at 0.0781 and
# refused.  1.05 gives 0.20 at h=1.0 and 0.425 at h=1.5, so both sizes clear
# the law on their own rather than on tolerance.
ADVANCE = 1.05           # x text height
GLYPH_WIDE = 0.60        # x text height


def text_size(s: str, h: float = SILK_H) -> tuple[float, float]:
    """Bounding box of *s* rendered at height *h*.  Monospaced, so this is
    exact — silklabel is fed a measured box, never an estimate."""
    n = max(len(s), 1)
    return (q(n * ADVANCE * h - (ADVANCE - GLYPH_WIDE) * h), q(h))


def text_strokes(s: str, cx: float, cy: float, h: float = SILK_H,
                 mirror: bool = False, rotation: int = 0) -> list[tuple]:
    """Render *s* centred on (cx, cy) as [(x1, y1, x2, y2), ...], board frame.

    ONE transform, applied in one order: glyphs are built about the run's own
    centre, then mirrored, then rotated, then translated.

    *mirror* flips the run about its own centre in x.  Back-side silk is stored
    in the top-view board frame like every other object, so it must be mirrored
    HERE to be readable when the board is turned over — SPEC "text
    orientation": each side is lasered in its own setup, so neither legend is
    mirrored by hand at the machine.

    *rotation* must match the rotation silklabel chose for this label.  It was
    once ignored: silklabel reserved a tall 90-degree box, the emitter drew a
    wide one, and the label landed on a pad the placer had carefully avoided
    (MEASURED: 20 front and 45 back strokes inside a pad, worst -1.14 mm).
    """
    w, _ = text_size(s, h)
    scale = h / 5.0
    x0, y0 = -w / 2, -h / 2
    pts_all = []
    for i, ch in enumerate(s.upper()):
        spec = GLYPHS.get(ch)
        if spec is None:
            raise SystemExit(f"silk: no glyph for {ch!r} in {s!r}")
        ox = x0 + i * ADVANCE * h
        for poly in spec.split("|"):
            if not poly:
                continue
            pts = []
            for tok in poly.split():
                gx, gy = tok.split(",")
                pts.append((ox + float(gx) * scale, y0 + float(gy) * scale))
            pts_all += list(zip(pts, pts[1:]))
    out = []
    for a, b in pts_all:
        seg = []
        for px, py in (a, b):
            if mirror:
                px = -px
            px, py = rot(px, py, rotation)
            seg += [q(cx + px), q(cy + py)]
        out.append(tuple(seg))
    return out


def box_strokes(cx: float, cy: float, side: float) -> list[tuple]:
    """A square outline — the ISP pin-1 tick (SPEC "ISP": pin 1 marked in silk
    with a square tick)."""
    h = side / 2.0
    p = [(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h)]
    return [(q(a[0]), q(a[1]), q(b[0]), q(b[1]))
            for a, b in zip(p, p[1:] + p[:1])]


ISP_LABEL = {"TP1": "MISO", "TP2": "VCC", "TP3": "SCK",
             "TP4": "MOSI", "TP5": "RST", "TP6": "GND"}


def legend_ink(items: list[tuple]) -> list[tuple]:
    """Flatten legend ITEMS into the flat stroke list the emitters want."""
    return [s for _name, _owner, strokes, _h in items for s in strokes]


# --- the legend seats itself now, for the same reason the labels do --------
# Before 2026-08-02 every legend mark was a hand-placed constant, which was
# honest while the only things it had to dodge were parts.  Then R4b spent 23
# wire vias and put SIX of them under the fixed legend — "ORBIT V1" over one,
# the date stamp over another, "+" over a third, four of the six ISP names
# over four more.  A constant cannot dodge a via the ROUTER chooses, so the
# marks that CAN move now search a fixed, ordered candidate list and take the
# first seat that satisfies all four silk laws.  Nothing here is random and
# nothing is hand-tuned: same board, same route, same seat, every run.
#
# The marks that CANNOT move do not search — the 12 cathode ticks and the
# three transistor pin-1 bars are functional geometry whose POSITION is the
# information ("which lead is the cathode"), so a collision there is a refusal
# to emit, not a nudge.  None has ever collided.
def _ring_candidates(cx: float, cy: float, w: float, h: float,
                     gaps=(0.45, 0.85, 1.35, 2.0, 2.8)) -> list[tuple]:
    """Text centres around an anchor, nearest first, reading order first.

    The same preference order silklabel uses for ref labels (N, S, E, W, then
    corners), so the legend and the labels agree about what a good seat is.
    """
    out = []
    for g in gaps:
        for dx, dy in ((0.0, 1.0), (0.0, -1.0), (1.0, 0.0), (-1.0, 0.0),
                       (1.0, 1.0), (-1.0, 1.0), (1.0, -1.0), (-1.0, -1.0)):
            out.append((q(cx + dx * (g + w / 2)), q(cy + dy * (g + h / 2))))
    return out


# Legend marks that could not be seated, with the reason, for MATRIX.md and
# the gate.  A legend DROP is never silent: it is the operator's third branch
# ("the label shouldn't exist") and it comes with an un-compression request.
SILK_DROPS: dict[str, list] = {"front": [], "back": []}


def seat_mark(what: str, owner: str | None, render, cands: list[tuple],
              keep: list[Pin], placed: list[tuple],
              h: float = SILK_H, named: set | None = None,
              optional: bool = False) -> list[tuple] | None:
    """First candidate centre whose INK satisfies every silk law, or refuse.

    *render* draws the mark at a centre — text or box, the laws are the same.
    *placed* is the legend ink already seated on this side; ref labels come
    later and treat all of it as an obstacle, so the legend only has to be
    self-consistent here.  A legend mark is FUNCTIONAL, so exhausting the
    candidates is a refusal to emit rather than a drop: the board is
    congested and the operator's answer to congestion is to un-compress it.
    """
    for cx, cy in cands:
        strokes = render(cx, cy)
        if _seat_ok(strokes, owner, keep, placed, h, named):
            return strokes
    if optional:
        return None
    raise SystemExit(
        f"silk: no legal seat for the legend {what!r} near "
        f"({cands[0][0]:.2f},{cands[0][1]:.2f}) — the legend is FUNCTIONAL, "
        f"so this is a congested board, not a label to drop: un-compress it")


def _seat_ok(strokes, owner, keep: list[Pin], placed: list[tuple],
             h: float = SILK_H, named: set | None = None) -> bool:
    """All four laws on one candidate's ink, with the audit's own numbers.

    *placed* is [(strokes, cap-height), ...] — the height travels with the ink
    because the text-separation bar is one cap height of the LARGER of the two
    texts, not a flat number (see silk_seats).
    """
    gaps = [(min(seg_pad_gap(s, pin) for s in strokes), pin) for pin in keep]
    worst = min(g for g, _p in gaps)
    if worst < SILK_CLEAR * CROWD_BAND:
        return False            # seat OUT of the squeezed band, not just legal
    tg = float("inf")
    for s2, h2 in placed:
        g = _ink_gap(strokes, s2)
        if g < max(h, h2):
            return False
        tg = min(tg, g)
    if owner is None:
        return min(worst, tg) >= h
    return (attribution_ratio(stroke_bbox(strokes), owner, keep, named)
            <= ATTRIB_RATIO)


def front_legend(parts: list[Part], route: dict | None = None) -> list[tuple]:
    """The functional front legend (SPEC "Silk, FRONT"), as ITEMS.

    Each item is (name, owner, strokes, cap-height), and the OWNER is what the
    third silk law needs: a legend mark is a claim about a feature exactly as
    a ref label is, so it is judged by the same attribution rule (a cathode
    tick belongs to its LED, CATCH to S1, "+" to PAD1).  An owner of None
    means the mark names NOTHING on the board — the clock numerals, ORBIT V1,
    the date — and those are held one cap height clear of every feature and
    every other text instead, or the eye reads them as a label for whatever
    they sit beside.

    The 12 cathode ticks are the load-bearing part: they sit at r 9.8, just
    inside the LED bodies' 5 mm footprint and radially inward of the cathode
    hole, which is the side the cathode is on.  Get these wrong and the ring
    does not light — no other check on the board catches it.
    """
    keep = silk_keepouts(parts, "front", route)
    named = named_features(parts)
    SILK_DROPS["front"] = []
    out, placed = [], []

    def add(name, owner, strokes, h=SILK_H):
        out.append((name, owner, strokes, h))
        placed.append((strokes, h))

    def seat(name, owner, txt, cands, h=SILK_H):
        add(name, owner, seat_mark(
            txt, owner, lambda cx, cy: text_strokes(txt, cx, cy, h),
            cands, keep, placed, h, named), h)

    for pos in range(1, 13):
        led = POS_LED[pos]
        ang = pos_angle(pos)
        # 3.2 inside the body pitch circle: 0.7 clear of the 5 mm body's inner
        # edge and radially inward of the cathode hole, which is the side the
        # cathode is on.  DERIVED from RING_R so it followed the grown ring.
        # RING_R - 3.4, not - 3.2.  At 3.2 the tick's inboard end stood 0.375
        # from its own cathode ring — exactly 25% over the 0.30 law, the last
        # seat on the board still sitting on the crowding audit's threshold.
        # Moving 0.2 further in costs nothing (it increases the clearance to
        # the 5 mm body it must stay inside, and the 12/3/6/9 numerals at
        # r 11.5 are still 1.2 away) and buys 0.575.
        cx, cy = polar(RING_CX, RING_CY, ang, RING_R - 3.4)
        tx, ty = polar(0.0, 0.0, ang + 90.0, 0.8)
        # FIXED, and it does not search: the tick's POSITION is the
        # information (which lead is the cathode), so a collision here would
        # be a board to un-compress, not a mark to nudge.  The audit measures
        # it with everything else.
        add(f"tick {led}", f"LED{led}",
            [(q(cx - tx), q(cy - ty), q(cx + tx), q(cy + ty))], 1.6)
    # marker arrow at position 1, outboard of the ring.  It OWNS position 1's
    # LED: that is the whole point of a position marker, and it is why the
    # arrow may stand closer to LED8 than any other mark on the board.
    apex = polar(RING_CX, RING_CY, 90.0, LEAD_R_OUT + 2.15)
    add("pos1 arrow", f"LED{POS_LED[1]}",
        [(q(apex[0] + dx), q(apex[1] + 1.3), q(apex[0]), q(apex[1]))
         for dx in (-1.3, 1.3)], 1.3)
    # The 12/3/6/9 numerals moved INSIDE the ring when it grew (2026-08-01).
    # Outboard they would have wanted r 22.6, which on the grown ring runs the
    # "9" off the left edge and drives the "6" into SW1's blade ring — and the
    # answer to that is not a bigger board still, because a clock face reads
    # correctly with its numbers inside the hands.  r 11.5 keeps them 2.0 mm
    # clear of the cathode rings and over nothing but back-side copper.
    #
    # r 10.5, NOT 11.5, and the 1.0 mm is the operator's third law arriving on
    # a mark that owns nothing.  MEASURED on the shipped render: the "3" stood
    # 1.07 mm from LED11's cathode TICK and the pair read "3 |" — a numeral
    # the bench binds to the LED beside it, which is exactly the ambiguity the
    # law forbids.  At 10.5 the same gap is 2.40, over one cap height, and the
    # numeral reads as what it is: a clock position, not a part label.  Bounded
    # from below by the centre block — at 10.5 the "3" and "9" hold 2.27 mm to
    # the date stamp's ink, where 10.0 would leave 1.56 and put the numerals a
    # hair over the same bar they were moved to clear.
    # and r is SEARCHED now, from 10.5 outward-then-inward, because R4b puts
    # vias in this annulus too: at 10.5 the "12" seats 0.263 from V1's ring.
    for label, ang in (("12", 90.0), ("3", 0.0), ("6", -90.0), ("9", 180.0)):
        seat(f"numeral {label}", None, label,
             [polar(RING_CX, RING_CY, ang + da, r)
              for r in (10.5, 9.6, 8.7, 11.4, 7.8)
              for da in (0.0, 7.0, -7.0, 14.0, -14.0)])
    # THE BUTTON LEGENDS MOVED OUTBOARD, to the far side of their own button,
    # and the operator's attribution law is why.  BUTTON_LEGEND_R's closed
    # form seated CATCH between S1 and the buzzer at 0.655 from its own button
    # and 0.646 from BZ1 — a dead heat, and a legend the bench cannot bind to
    # either part (ratio 1.01).  The squeeze has no seat that satisfies the
    # law with any margin: the 3.05 mm between S1's leg ring and BZ1's lead
    # ring holds 1.75 of text, and splitting the remaining 1.30 two-to-one
    # leaves a 0.058 mm window to aim at, which this file's oldest lesson
    # forbids sitting in.  On the OUTBOARD side the same text seats 0.405 from
    # its own button against 3.4 to the nearest foreign feature — ratio 0.12,
    # decisive — because the corner it faces holds only a flip gauge, and the
    # gauge is 4.8 mm away.  The buzzer keeps its own BZ1 ref label, which is
    # now the only text between the two buttons.
    for ref, sign in (("S1", -1.0), ("S2", +1.0)):
        bx, by_ = BUTTONS[ref]
        txt = "CATCH" if ref == "S1" else "START"
        seat(txt, ref, txt,
             [(bx, q(by_ + sign * d)) for d in (BUTTON_LEGEND_R - 0.25,
                                                BUTTON_LEGEND_R,
                                                BUTTON_LEGEND_R + 0.5,
                                                BUTTON_LEGEND_R + 1.0)]
             + [(bx, q(by_ - sign * d)) for d in (BUTTON_LEGEND_R - 0.25,
                                                  BUTTON_LEGEND_R)])
    seat("ON", "SW1", "ON",
         [(SW1_X, 8.1), (SW1_X, 8.6), (SW1_X, 9.2),
          (q(SW1_X + SW1_PITCH), 8.1), (q(SW1_X - SW1_PITCH), 8.1)])
    # follows the layout, not the note: PAD1 (+) is the right-hand pad.  Both
    # search: V2's ring sits 3.0 mm above PAD1, exactly where "+" used to be
    # drawn (measured -0.625 INSIDE it on the shipped artwork), and "-" at
    # (10,7) stood 0.946 from flip gauge G1 against 1.075 from its own PAD2 —
    # a minus sign the bench would read as the gauge's.
    by = {p.ref: p for p in parts}
    for ref, txt in (("PAD1", "+"), ("PAD2", "-")):
        pin = by[ref].pins[0]
        seat(txt, ref, txt, _ring_candidates(pin.x, pin.y, RING_PAD, RING_PAD,
                                             gaps=(0.45, 0.9, 1.5, 2.2)))
    # THE IDENTITY BLOCK LEFT THE RING INTERIOR, and the router is why.  It
    # was two centred lines at the hub; R4b then spent SEVEN wire vias inside
    # the ring, one of them (V15) 0.86 mm from the hub itself, and a mark that
    # names nothing has to stand a cap height clear of every one of them.  The
    # interior has no such pocket left: MEASURED by scanning the whole board on
    # a 0.5 mm grid, the ring holds ZERO feasible centres for an 11.9 mm line
    # and the open top margin holds hundreds (best 4.98 mm of clearance).  The
    # top band is where the 2026-08-02 growth actually went, so this is the
    # growth being spent on the thing that needed it.
    seat("ORBIT V1", None, "ORBIT V1",
         [(q(x), q(y)) for y in (54.0, 53.5) for x in (41.0, 44.0, 38.0,
                                                       47.0, 35.0)])
    seat("date", None, DATE_STAMP,
         [(q(x), q(y)) for y in (50.6, 50.1, 49.6)
          for x in (41.0, 44.0, 38.0, 47.0, 35.0)])
    return out


def back_legend(parts: list[Part], route: dict | None = None) -> list[tuple]:
    """SPEC "Silk, BACK": U1 pin-1 dot, transistor orientation marks, the six
    ISP labels + pin-1 square tick, "SIDE B".  All mirrored so the legend reads
    with the BACK up, which is the only way it is ever seen.

    Items are (name, owner, strokes, cap-height), like the front."""
    by = {p.ref: p for p in parts}
    keep = silk_keepouts(parts, "back", route)
    named = named_features(parts)
    SILK_DROPS["back"] = []
    out, placed = [], []

    def add(name, owner, strokes, h=SILK_H):
        out.append((name, owner, strokes, h))
        placed.append((strokes, h))

    def seat(name, owner, txt, cands, h=SILK_H):
        add(name, owner, seat_mark(
            txt, owner,
            lambda cx, cy: text_strokes(txt, cx, cy, h, mirror=True),
            cands, keep, placed, h, named), h)

    def seat_box(name, owner, side_mm, cands):
        add(name, owner, seat_mark(
            name, owner, lambda cx, cy: box_strokes(cx, cy, side_mm),
            cands, keep, placed, side_mm, named), side_mm)

    # U1 pin-1 dot: a small square OUTBOARD of pin 1's land.  The offset is
    # rotated with the part, never hard-coded: at rotation 0 pin 1 is U1's
    # upper-left corner and outboard is (-1.1, +1.0), but U1 now sits at 180
    # and the same literal offset put the dot INSIDE the package, 0.087 mm
    # into pin 8's land — a pin-1 marker pointing at the wrong pin is worse
    # than none, and the silk gate caught it.
    p1 = by["U1"].pins[0]
    # (-1.25, 1.15), not (-1.1, 1.0): widening U1's lands to the scrub bar
    # (SMD_FP["SOIC8W"], 0.65 -> 0.72) grew each pad 0.035 along the pitch and
    # walked this dot to 0.267 from pin 1's own land, under the 0.30 silk law.
    # The MARKER yields, not the pad — it is silk, the cheapest thing on the
    # board to move — and it stays diagonally adjacent to pin 1, which is the
    # only thing that makes a pin-1 dot mean anything.
    # SEARCHED along pin 1's own diagonal: V13's ring landed 0.295 INSIDE the
    # dot on the shipped artwork.  The dot keeps its meaning as long as it is
    # diagonally adjacent to pin 1, so the candidates walk out along that
    # diagonal and never around the package.
    # SEARCHED, and it had to leave the diagonal: V13's ring landed 0.295
    # INSIDE the dot on the shipped artwork, and walking further out along the
    # same diagonal only pushes deeper into the via (measured -0.606, -0.893,
    # -1.064).  The candidates therefore step outboard first and then SLIDE
    # ALONG pin 1's own edge of the package, which keeps the only thing that
    # makes a pin-1 dot mean anything: it is the mark at pin 1's corner, on
    # pin 1's side, with pin 8 seven millimetres away at the other end.
    # The dot is 0.45 rather than 0.5 for the same reason the label boxes are
    # the ink: at 0.5 the best seat in this corner reads 0.52 on the
    # attribution ratio and at 0.45 it reads 0.395, and 0.05 mm of white
    # square is not what makes a pin-1 marker legible.
    seat_box("U1 pin-1 dot", "U1", 0.45,
             [(q(p1.x + ox), q(p1.y + oy)) for ox, oy in
              (rot(*o, by["U1"].rot) for o in
               ((-1.25, 1.15), (-1.55, 1.40), (0.65, 1.10), (0.35, 1.10),
                (0.95, 1.10), (0.00, 1.10), (-1.85, 1.65)))])
    # Q1 / Q2 / D1: a bar under pin 1 says which corner pin 1 is.  FIXED, for
    # the cathode tick's reason: the bar's position IS the orientation.
    for ref in ("Q1", "Q2", "D1"):
        pin1 = [p for p in by[ref].pins if p.term == "1"][0]
        add(f"{ref} pin-1 bar", ref,
            [(q(pin1.x - 0.4), q(pin1.y - 1.0),
              q(pin1.x + 0.4), q(pin1.y - 1.0))], 0.8)
    # ISP: six labels, left column labelled to the left, right to the right
    for ref, txt in ISP_LABEL.items():
        pad = by[ref].pins[0]
        w, _ = text_size(txt, 1.0)
        left = pad.x < ISP_X0 + ISP_PITCH / 2
        # 0.45, not the 0.30 law: the half-stroke counts (leaving it out put
        # every ISP label 0.175 from its own pad) and so does a MARGIN.
        # Placing the label at exactly the law made all twelve ISP seats read
        # 0.300-0.334 in the crowding audit — the single densest cluster of
        # near-bar silk on the board, and every millimetre of it self-inflicted
        # by this constant rather than by congestion.  0.45 seats them at
        # 0.45 and costs nothing: these labels are drawn OUTWARD, away from
        # the 2.54 grid, into open back copper.
        cx = pad.x + (-1 if left else 1) * (ISP_PAD / 2 + 0.45 + SILK_W / 2
                                            + w / 2)
        # OUTWARD FIRST, THEN FURTHER OUT, THEN ABOVE AND BELOW.  Four of the
        # six names were sitting ON via rings in the shipped artwork (MISO
        # -1.130 into V6, VCC -0.867, MOSI -1.140, RST -1.261): the ISP block
        # is the board's densest corner and R4b routes through it, so a fixed
        # offset from the pad is a promise the layout cannot keep.  The ISP
        # names are the whole reason the block is usable, so they SEARCH and
        # refuse rather than drop.
        sgn = -1 if left else 1
        got = seat_mark(
            txt, ref, lambda mx, my: text_strokes(txt, mx, my, 1.0,
                                                  mirror=True),
            [(q(cx + sgn * k), pad.y) for k in (0.0, 0.5, 1.1, 1.8, 2.6)]
            + [(q(cx + sgn * k), q(pad.y + dy))
               for dy in (1.35, -1.35, 1.9, -1.9)
               for k in (0.0, 0.6, 1.3)],
            keep, placed, 1.0, named, optional=True)
        if got is None:
            # THE ISP BLOCK IS BLOCKED BY COPPER THIS PASS MAY NOT MOVE.
            # MEASURED across the whole board: with R4b's four crossings
            # around the block (V9/V15 pre-seeded beside TP1 and TP5, the L0
            # and VCC closers beside TP2/TP4), the nearest clearance-legal
            # seat for this name is 5-7 mm from its own pad and 0.3-1.3 mm
            # from a DIFFERENT pad — MOSI would sit 0.46 from TP3 and 5.90
            # from TP4, i.e. it would name the wrong hole.  Every branch of
            # the operator's rule is then closed except the last: re-seating
            # is exhausted, un-compressing this corner means moving the
            # crossings (copper, a reroute, reported not rolled), so the name
            # is DROPPED and the request is recorded.  Nothing is lost that
            # the bench had: on the shipped artwork these four names were
            # 0.87-1.26 mm INSIDE a via ring, which the CAM lane's silk clip
            # chops into fragments (43 chains dropped on this side alone).
            SILK_DROPS["back"].append(
                (f"ISP {txt}", ref, "boxed in by R4b's crossings: no seat "
                 "within 5 mm that names the right pad"))
            continue
        add(f"ISP {txt}", ref, got, 1.0)
    # pin-1 (TP1) square tick, placed RELATIVE to TP1 so it follows the grid
    tp1 = by["TP1"].pins[0]
    # the diagonal seat is inside V9's ring on this route, so the tick walks
    # up over its own pad instead: straight above TP1 it still marks the
    # block's pin-1 corner, with TP2 2.5 mm away across the grid.
    seat_box("ISP pin-1 tick", "TP1", 0.8,
             [(q(tp1.x - 1.4), q(tp1.y + 1.62)),
              (q(tp1.x - 1.75), q(tp1.y + 2.0)),
              (q(tp1.x), q(tp1.y + 2.0)),
              (q(tp1.x), q(tp1.y + 2.3)),
              (q(tp1.x - 0.7), q(tp1.y + 2.2)),
              (q(tp1.x - 0.35), q(tp1.y + 2.5))])
    # centred on the flip mirror line, 1.25 below the top edge
    seat("SIDE B", None, "SIDE B",
         [(BOARD_W / 2, q(BOARD_H - dy)) for dy in (2.0, 2.6, 3.2, 4.0)])
    return out


# ---------------------------------------------------------------------------
# THE SILK KEEP-OUT SET — every bare-copper feature the laser must miss
# ---------------------------------------------------------------------------
# INCIDENT 2026-08-02, operator-caught on the SHIPPED front render while the
# board gate read 21/21, the CAM gate read 180/180 and the crowding audit read
# ZERO flags: "LED8" started on its own LED's front ring, "LED12" ran into a
# via ring, "LED2" sat against a pad ring, and the bottom strip read "PAD1ON"
# — the PAD1 ref label and the ON legend fused into one word.  MEASURED on the
# artwork afterwards: front silk ink stood -1.280 mm INSIDE V21's ring, -0.487
# inside gauge G1's disc, and 68 stroke/feature pairs sat under the 0.30 law.
#
# THE SEMANTICS WERE WRONG, NOT THE ARITHMETIC.  The old harvest fed silklabel
# that side's SOLDERABLE pads, and solderability is not what the physics turns
# on.  The silk laser cures white ink ON MASK; a stroke that crosses a mask
# APERTURE lands on bare copper, where nothing cures — a garbage streak the
# scrub then smears, and on a via ring it fouls a joint the bench must solder.
# So the keep-out set is every APERTURE, plus the bare copper that never had
# one, plus the holes:
#
#   * THT rings open the mask on BOTH faces, dead or live (emit_lihata: "THE
#     MASK OPENS OVER EVERY ONE OF THEM").  This board's 24 front LED rings
#     are dead AND bare — never solderable, always bare copper after cure.
#   * the 23 WIRE VIAS are apertures on both faces and hand-soldered joints.
#     They do not exist until R4b routes, so a placer fed only R4a's part
#     list cannot see them at all: that is how three labels came to sit on
#     via rings while every check on the unrouted board passed.
#   * the four FLIP GAUGES are bare copper with NO aperture (RING_GAUGE's
#     note).  They stay in the set for a second reason as well — a gauge is
#     read with a loupe, and white ink over it gauges nothing.
#   * a HOLE has no substrate to cure on at all.
#
# The CAM lane does not save us here and that is the point: reemit.silk_strokes
# CLIPS every stroke that comes within the clearance of an aperture, so the
# machine would have engraved chewed labels while `silk pad clearance` passed
# on the clipped bytes.  A label the clip would eat must never be placed.
def silk_keepouts(parts: list[Part], side: str,
                  route: dict | None = None) -> list[Pin]:
    """Every feature silk ink must clear on *side*, as Pin geometry.

    ONE model with THREE consumers, so none of them can drift: label_parts
    hands silklabel their boxes, the gate measures stroke-to-feature gaps with
    seg_pad_gap, and silk_audit.py re-measures the same set on the exported
    gerbers.  Synthetic pins carry the features that are not terminals (vias,
    gauge discs, mount bores) — the geometry is a disc either way.
    """
    out = []
    for part in parts:
        for pin in part.pins:
            # THT: the mask opens on both faces.  SMD lands and ISP pads are
            # back-side copper and open only there.
            if pin.kind == "tht" or side == "back":
                out.append(pin)
    for i, (vx, vy, _net) in enumerate((route or {}).get("vias", ())):
        # a routed via arrives in the LIHATA frame; Pin geometry is board frame
        out.append(Pin(f"V{i + 1}", "1", vx, BOARD_H - vy,
                       ("tht", HOLE_VIA, RING_VIA)))
    for ref, (gx, gy) in GAUGES.items():
        out.append(Pin(ref, "1", gx, gy, ("tht", HOLE_GAUGE, RING_GAUGE)))
    for ref, (mx, my) in MOUNTS.items():
        out.append(Pin(ref, "1", mx, my, ("tht", HOLE_MOUNT, HOLE_MOUNT)))
    return out


# The three silk laws this board now places to, each with its number.
SILK_CLEAR = 0.30        # SPEC: ink to bare copper.  silklabel adds its own
#                          0.10 of raster/kerf slack on top (CLIP_KEEPOUT)
TEXT_GAP = SILK_H        # ink between two SEPARATE texts — one CAP HEIGHT.
#                          The operator's floor was 0.5, and the artwork says
#                          0.5 is not enough: "PAD1" and "ON" measured 0.709
#                          apart and still came back from the render fused as
#                          "PAD1ON", because the eye judges a seam RELATIVE to
#                          the 0.425 gap between two glyphs of the same word —
#                          0.709 is only 1.7x that, while a real word space in
#                          this font is 2.0 mm.  The second, independent
#                          reading of the same bar: "S1" and "G2" stacked
#                          1.450 apart in the bottom-right corner, which the
#                          operator read as one ambiguous block and which no
#                          clearance or attribution law catches.  One cap
#                          height (1.5) fails both and is anchored to the type
#                          rather than to either incident
ATTRIB_RATIO = 0.50      # own-feature gap / nearest-foreign-feature gap.
#                          Operator, 2026-08-02: "if a human can't tell what
#                          board feature a label would refer to, the label
#                          shouldn't exist or the board should be
#                          decompressed."  A label is a claim about ONE
#                          feature, so its owner must be DECISIVELY nearest —
#                          twice as near.  Judged on the render's own
#                          failures: "G4 S2" stacked in the top-right corner,
#                          "S1 G2" bottom-right, "G1 PAD2" fused bottom-left,
#                          each a label the bench cannot bind to a feature.
# THE STANDOFF TIERS ORBIT SEARCHES, extended past silklabel's 2.80 default.
# This is the un-compression the operator's canary rule asks for, spent where
# it costs nothing: the corrected keep-out set closes the near tiers around
# every part that shares its neighbourhood with a wire via, and a label that
# has to sit 4 mm out is only dangerous if it stops obviously belonging to its
# part — which is exactly what the attribution rule refuses.  Growing the
# OUTLINE would not have helped these fifteen: they are stranded by copper in
# the board's interior, not by its rim.
LABEL_GAPS = (0.30, 0.55, 0.90, 1.40, 2.00, 2.80, 3.60, 4.60, 5.80)
N_LABELS = 52            # every part the board offers a ref label to: 19
#                          front THT parts + 4 flip gauges + 29 back parts.
#                          The population, not the yield — a part that fell
#                          out of the label set entirely would otherwise be
#                          indistinguishable from one that could not be seated
OWNERLESS_CLEAR = SILK_H  # a mark that names NOTHING (the 12/3/6/9 numerals,
#                          ORBIT V1, the date, SIDE B) must stand one cap
#                          height clear of every feature and every other text,
#                          or it reads as a label for whatever it is nearest:
#                          the render's "3 |" — the 3 o'clock numeral 1.07 mm
#                          from LED11's cathode tick — read as a label for
#                          LED11 and is exactly the operator's third law


def _lht_disc(pin: Pin) -> tuple[float, float, float, float]:
    """(cx, cy, half-w, half-h) of a keep-out feature in the LIHATA frame."""
    hw, hh = pin.extent()
    lx, ly = lht_xy(pin.x, pin.y)
    return (lx, ly, hw, hh)


def rect_feature_gap(r: SL.Rect, pin: Pin) -> float:
    """Edge-to-edge gap between a label BOX and a feature, LIHATA frame.

    A round feature is measured as a disc (its bbox would over-read by 0.41 r
    at the corners and cost seats the physics does not ask for); a rotated SMD
    land is measured as its bbox, which can only UNDER-read a gap.
    """
    cx, cy, hw, hh = _lht_disc(pin)
    if pin.kind == "tht" or pin.kind == "circ":
        dx = max(r.x0 - cx, cx - r.x1, 0.0)
        dy = max(r.y0 - cy, cy - r.y1, 0.0)
        return math.hypot(dx, dy) - hw
    dx = max(r.x0 - (cx + hw), (cx - hw) - r.x1, 0.0)
    dy = max(r.y0 - (cy + hh), (cy - hh) - r.y1, 0.0)
    return math.hypot(dx, dy)


SHADOW_COS = 0.707       # 45 degrees.  A LABEL POINTS, and the eye stops at
#                          the first feature it points at: a foreign feature
#                          that lies FARTHER AWAY within 45 deg of the owner's
#                          own bearing is BEHIND the owner and is not a rival.
#                          Without this term the rule is unsatisfiable on any
#                          connector grid, and the ISP block is the proof —
#                          six O1.8 pads on a 2.54 pitch leave 0.74 mm between
#                          neighbours, so a name written beside its own pad can
#                          never be twice as near it as it is to the next pad
#                          along.  MEASURED: with every foreign feature counted
#                          a rival, five of the six ISP names have ZERO legal
#                          seats anywhere on the board, and the sixth is the
#                          corner pad.  The bearing test costs the rule nothing
#                          it was written for: every failure the operator
#                          caught on the render is a rival that is NEARER than
#                          the owner (PAD1's label 0.574 from SW1's ring
#                          against 2.777 from its own pad) or one sitting on
#                          foreign copper outright, and neither is shadowed.


def named_features(parts: list[Part]) -> set:
    """The refs a silk text could plausibly BE ABOUT.

    Every part, plus the four flip gauges (the run sheet names them and the
    operator hunts for them with a loupe).  NOT the wire vias and NOT the M3
    bores: they carry no designator, nothing on either legend refers to one,
    and no bench has ever asked which name belongs to a via.  They stay in the
    CLEARANCE set — they are bare copper and the laser cannot cure on them —
    but they are not RIVALS, because a rival has to be a possible referent.
    Keeping them as rivals is not conservatism, it is a different (wrong)
    claim: MEASURED, it leaves five of the six ISP names with zero legal seats
    anywhere on the board, because R4b parks four crossings around a block
    whose pads are 0.74 mm apart.
    """
    return {p.ref for p in parts} | set(GAUGES)


def attribution_ratio(r: SL.Rect, ref: str, keepouts: list[Pin],
                      named: set | None = None) -> float:
    """How ambiguous a label box is: own-feature gap / rival-feature gap.

    0 is perfect (the label points at its owner and nothing else is near), 1
    is a coin toss, above ATTRIB_RATIO is a label the bench cannot bind.  A
    label with no rival anywhere is unambiguous by construction (0.0).
    """
    own_all = [(rect_feature_gap(r, p), p) for p in keepouts if p.ref == ref]
    if not own_all:
        return 0.0
    own, own_pin = min(own_all, key=lambda t: t[0])
    ox, oy = own_pin.x - r.cx, lht_xy(0.0, own_pin.y)[1] - r.cy
    on = math.hypot(ox, oy) or 1.0
    best = None
    for p in keepouts:
        if p.ref == ref or (named is not None and p.ref not in named):
            continue
        g = rect_feature_gap(r, p)
        if g > own:                     # only a farther feature can hide
            fx, fy = p.x - r.cx, lht_xy(0.0, p.y)[1] - r.cy
            fn = math.hypot(fx, fy) or 1.0
            if (ox * fx + oy * fy) / (on * fn) >= SHADOW_COS:
                continue                # behind the owner, on the same bearing
        best = g if best is None else min(best, g)
    if best is None:
        return 0.0
    if best <= 0.0:
        return float("inf")            # sitting ON someone else's copper
    return max(own, 0.0) / best


def label_parts(parts: list[Part], side: str, legend: list[tuple],
                route: dict | None = None):
    """Feed silklabel the geometry for one side and take back its placements.

    silklabel is pure geometry in a y-DOWN frame, which is the lihata frame, so
    everything handed over goes through lht_xy and comes back the same way.
    The keep-out set is silk_keepouts(): EVERY mask aperture on this side plus
    the bare copper that has none plus the holes — see its incident note.  A
    label the silk-clip would eat, or one the bench could not bind to a
    feature, is never emitted: silklabel reports it unplaced instead, which is
    a bench note rather than misinformation.
    """
    want = [p for p in parts if (p.side == side)]
    keepouts = silk_keepouts(parts, side, route)
    apertures = []
    for pin in keepouts:
        cx, cy, hw, hh = _lht_disc(pin)
        apertures.append(SL.Rect(cx - hw, cy - hh, cx + hw, cy + hh))
    hard = []
    for _ref, (mx, my) in MOUNTS.items():
        lx, ly = lht_xy(mx, my)
        r = MOUNT_KEEPOUT_R
        hard.append(SL.Rect(lx - r, ly - r, lx + r, ly + r))
    silk = [stroke_bbox([s]) for s in legend]
    # NO ROTATED REF LABELS, and it is a legend-METRICS law rather than taste.
    #
    # checks.silk_metric_checks clusters silk marks into "text lines" by bbox
    # adjacency (vertical overlap, horizontal gap <= 1.6) and judges each line
    # on the MEDIAN of its glyph heights against the median ink thickness,
    # demanding stroke:height inside 1:7.5..1:3.5.  A rotated label's glyphs
    # are WIDE AND SHORT — bbox 1.75 across by ~1.15 tall where upright glyphs
    # are 1.75 tall — so the moment a rotated label lands within 1.6 mm of an
    # upright one the cluster's median height collapses to the rotated value
    # while the thickness stays put.  MEASURED on the first double-sided gate:
    # a five-glyph line at (7.29,42.72) mixed two upright glyphs with three
    # rotated ones and read 0.3197 against the 0.286 bar, failing front/silk.
    #
    # Un-mixing that one cluster would not have been a fix: the all-rotated
    # lines on the same board read 0.2783 against 0.286 — eight thousandths of
    # margin — so rotated legend text on this font is one nudge from failing
    # wherever it appears.  Withdrawing the rotated candidate entirely puts
    # every front line at medh 1.75 and ratio 0.16..0.18, mid-band.
    #
    # The withdrawal is expressed the only way a CALLER can express it:
    # silklabel picks from ((0, wh), (90, wh90)), so a wh90 that cannot fit
    # inside the board leaves rot 0 as the only survivor.
    NO_ROT = (BOARD_W * 10, BOARD_H * 10)

    # THE LABEL BOX MUST BE THE INK, not the centreline.  text_size measures
    # the glyph run's centreline extent, so the true ink reaches SILK_W/2
    # beyond it on every side.  silklabel keeps a label's BOX CLIP_KEEPOUT
    # (0.40) clear of solderable copper, and with an understated box that came
    # out as 0.40 - 0.125 = 0.275 of real ink clearance — under this board's
    # 0.30 silk law.  MEASURED the moment the glyphs widened (ADVANCE 1.05):
    # PAD1's label slid under SW1-1's ring at 0.2754 and the front silk check
    # convicted it.  Handing over the inked size makes 0.40 mean 0.40 and
    # leaves 0.10 over the law instead of 0.025 under it.
    def inked(ref: str) -> tuple[float, float]:
        w, h = text_size(ref)
        return (w + SILK_W, h + SILK_W)

    sl_parts = []
    for part in want:
        sl_parts.append(SL.Part(part.ref, part.body(), inked(part.ref),
                                NO_ROT))
    if side == "front":
        for ref, (gx, gy) in GAUGES.items():
            lx, ly = lht_xy(gx, gy)
            r = RING_GAUGE / 2
            sl_parts.append(SL.Part(ref, SL.Rect(lx - r, ly - r, lx + r, ly + r),
                                    inked(ref), NO_ROT))
    board = SL.Rect(0.0, 0.0, BOARD_W, BOARD_H)
    # The two raised laws and the attribution rule, all three expressed to the
    # placer rather than hand-applied afterwards: a law enforced by inspection
    # is a law that ships broken the first time nobody inspects (this whole
    # incident).  silklabel's own defaults are unchanged, so Board A's coupon
    # legend — the same module, the other caller — cannot move under this.
    return SL.place_labels(sl_parts, board, apertures, silk, hard,
                           label_gap=TEXT_GAP, silk_gap=TEXT_GAP,
                           attribution=lambda r, ref: attribution_ratio(
                               r, ref, keepouts, named_features(parts)),
                           attribution_max=ATTRIB_RATIO, gaps=LABEL_GAPS)


def stroke_bbox(strokes: list[tuple], pad: float = SILK_W / 2) -> SL.Rect:
    """Bounding rect of a stroke run in the LIHATA frame, inflated by the
    half-stroke so silklabel sees the ink, not the centrelines."""
    xs = [c for s in strokes for c in (s[0], s[2])]
    ys = [c for s in strokes for c in (s[1], s[3])]
    lx0, ly0 = lht_xy(min(xs), max(ys))
    lx1, ly1 = lht_xy(max(xs), min(ys))
    return SL.Rect(lx0 - pad, ly0 - pad, lx1 + pad, ly1 + pad)


# ---------------------------------------------------------------------------
# SERIALIZATION 1 — lihata: the PHYSICAL truth (every piece of copper)
# ---------------------------------------------------------------------------
PROTO_VIA, PROTO_MOUNT, PROTO_GAUGE = 0, 1, 2


def emit_lihata(parts: list[Part], nets: dict, labels: dict,
                route: dict | None = None) -> str:
    """*route* is R4b's merged routing result, or None for the UNROUTED board.

        {"tracks":   [(layer, x1, y1, x2, y2, width), ...]  LIHATA frame,
         "vias":     [(x, y, net), ...]                     LIHATA frame,
         "promoted": {"PAD2-1", ...}}                       dual-solder joints

    Routing never invents a prototype: a via instantiates WIRE_VIA_STITCHED and
    a promotion switches an existing lead from THT_BACK_ONLY (proto 0) to
    THT_DUAL_SOLDER_DECLARED (proto 1), both of which R4a already emitted.  So
    the routed board is the same board with more copper, not a different one.
    """
    route = route or {"tracks": [], "vias": [], "promoted": set()}
    promoted = set(route["promoted"])
    protos = (
        # The DECLARED stitch: the only prototype on the board with hplated=1.
        # No object references it at R4a — R4b instantiates one per wire via.
        # Mask OPEN on both faces: a wire via is a hole the operator threads and
        # solders on BOTH sides, so both rings are joints and both get scrubbed.
        L.ps_proto(PROTO_VIA, "WIRE_VIA_STITCHED", HOLE_VIA, True, RING_VIA,
                   mask_sides=("top", "bottom")) +
        # Bare bores: a hole with no copper anywhere.  H1-H4 carry no annulus
        # at all (SPEC: Ø3.4 bore, copper keep-out), and the G1-G4 gauge rings
        # are DEAD ISLANDS, not annuli of a terminal, so they are emitted as
        # separate copper below.  MEASURED: pcb-rnd raises no ring or drill
        # violation on a copper-less padstack, so this encoding is silent in
        # DRC rather than a false positive.  No mask on either: there is no
        # copper here to expose, and the gauge rings are covered ON PURPOSE.
        L.ps_proto(PROTO_MOUNT, "MOUNT_BORE_M3", HOLE_MOUNT, False, 0.0,
                   sides=()) +
        L.ps_proto(PROTO_GAUGE, "GAUGE_BORE", HOLE_GAUGE, False, 0.0, sides=()))

    objs, top, bot, top_mask = [], [], [], []
    for ref, (mx, my) in MOUNTS.items():
        objs.append(L.ps_ref(25000 + 2 * len(objs), PROTO_MOUNT,
                             *lht_xy(mx, my), name=ref, clearance=COPPER_CLEAR))
    for i, (ref, (gx, gy)) in enumerate(GAUGES.items()):
        objs.append(L.ps_ref(25100 + 2 * i, PROTO_GAUGE,
                             *lht_xy(gx, gy), name=ref, clearance=COPPER_CLEAR))
        # the gauge's readable copper: one dead island per face, on no net,
        # 0.4 clear of the pour on both sides (SPEC "Deliberate exceptions")
        lx, ly = lht_xy(gx, gy)
        top.append(L.line(21000 + i, lx, ly, lx, ly, RING_GAUGE, COPPER_CLEAR))
        bot.append(L.line(21100 + i, lx, ly, lx, ly, RING_GAUGE, COPPER_CLEAR))

    gnd_terms = {p.pid for part in parts for p in part.pins if p.net == "GND"}
    for part in parts:
        pins, protos_sub = [], ""
        if part.pins[0].kind == "tht":
            hole, ring = part.pins[0].shape[1], part.pins[0].shape[2]
            # The mask opens where the terminal HAS copper — bottom only for an
            # ordinary back-soldered lead.  The physical front ring of the same
            # hole is a dead island belonging to no terminal, and it gets its
            # own mask aperture below, from the same place its copper comes
            # from.  Encoding it here instead would put an opening on a face
            # this padstack does not own.
            protos_sub = L.ps_proto(0, "THT_BACK_ONLY", hole, False, ring,
                                    sides=("bottom",), mask_sides=("bottom",))
            if part.pins[0].dual:
                # Present, referenced by nothing: R4b promotes a lead by
                # switching its proto to 1.  The prototypes differ ONLY in
                # hplated and in owning the front face — and therefore in
                # having a front OPENING, which is the point: a promotion is a
                # bench joint on the reflow face, and paint over it is a joint
                # the operator cannot make.
                protos_sub += L.ps_proto(1, "THT_DUAL_SOLDER_DECLARED",
                                         hole, True, ring,
                                         mask_sides=("top", "bottom"))
        elif part.pins[0].kind == "circ":
            # The 6 ISP pads: mask OPEN (they are touched with a pogo or a
            # soldered wire), paste NONE.  SPEC's paste rule is about holes
            # wicking, but the principle binds here too — an ISP pad is a
            # contact, not a reflow land, and a stencil window on it would put
            # solder where a probe tip has to sit flat.
            protos_sub = L.ps_proto(0, "ISP_BARE_PAD", 0.0, False,
                                    part.pins[0].shape[1], sides=("bottom",),
                                    mask_sides=("bottom",))
        else:
            w, h = part.pins[0].shape[1], part.pins[0].shape[2]
            # -prot, not +prot.  The model's rotation is CCW in the BOARD frame
            # (y-up); lihata is y-DOWN, and mirroring y turns a CCW rotation
            # into a CW one.  Emitting +prot here reflected every land about
            # its own centre — invisible on the 4 ring resistors whose angle is
            # a multiple of 90 and on every axis-aligned part, and wrong on the
            # other 8, which is exactly the class of bug rect_corners' own
            # docstring was written for.  MEASURED: R4 (prot 300) came out as a
            # land at 60, i.e. 120 degrees off the body it has to solder to.
            # The ONLY class that takes paste: a reflow land on the reflow
            # face.  SPEC's rule is stated as a prohibition — no paste on a
            # hole, because a pasted hole wicks and blocks the wire — and this
            # is its positive form.  Mask and paste reuse the land's own
            # rotated polygon, so all three shapes are literally the same
            # corners (see lihata.ps_proto_rect).
            protos_sub = L.ps_proto_rect(0, "SMD_LAND", 0.0, False, w, h,
                                         sides=("bottom",),
                                         rotation=-part.pins[0].prot,
                                         mask_sides=("bottom",),
                                         paste_sides=("bottom",))
        therm = {}
        for p in part.pins:
            # A promoted lead is the ONE object on this board with copper on
            # both faces, so it is also the one that can take two thermals.
            proto_id = 1 if p.pid in promoted else 0
            pins.append((p.term, *lht_xy(p.x, p.y), proto_id))
            if p.pid in gnd_terms:
                # Thermal on the lids this pin ACTUALLY has copper on — never
                # on the lid its body happens to sit on (see lihata.subc).
                lids = [L.LID_BOT_CU]
                if p.kind == "tht" and proto_id == 1:
                    lids.insert(0, L.LID_TOP_CU)
                therm[p.term] = tuple(lids)
        ox, oy = lht_xy(part.pins[0].x, part.pins[0].y)
        objs.append(L.subc(part.oid, part.ref, pins=pins, protos=protos_sub,
                           x=ox, y=oy, footprint=part.fp,
                           on_bottom=part.on_bottom, clearance=COPPER_CLEAR,
                           thermal=therm))

    # dead front rings: real copper, no terminal, no net (the R3 finding).
    # A PROMOTED lead is the exception: its padstack now owns the front ring as
    # electrical copper, so emitting the dead island too would stack a second
    # disc of copper on the same spot and hand DRC a self-overlap.
    #
    # THE MASK OPENS OVER EVERY ONE OF THEM, dead or not, and the uniformity is
    # the whole argument.  A dead front ring is copper no one will ever solder,
    # so an opening there buys the operator nothing — but paint over it costs
    # something real.  The lane's raster mask-blind law reads the mask against
    # the hole-centred ring set and CANNOT tell a dead ring from a live one:
    # it sees a ring with no window and reports a joint the bench cannot make.
    # Openings that are uniform per hole class are therefore checkable;
    # openings that encode which lead a human happens to solder are not.  And
    # the asymmetry is one-sided — an open dead ring is a disc of bare copper
    # on a board with no plating process, which is exactly what its flip-gauge
    # neighbours are already — so the uniform rule is also the safe one.
    for i, (pid, x, y, dia, joins) in enumerate(dead_front_rings(parts)):
        if pid in promoted:
            continue
        lx, ly = lht_xy(x, y)
        top.append(L.line(20000 + i, lx, ly, lx, ly, dia,
                          0.0 if joins else COPPER_CLEAR,
                          clearpoly=not joins))
        # A zero-length line on the mask layer flashes one aperture of its own
        # thickness — ROUND-TRIPPED through pcb-rnd's cam exporter, same as the
        # padstack shapes.  Clearance 0: a mask layer has no pour to clear.
        top_mask.append(L.line(60000 + i, lx, ly, lx, ly, dia, 0.0))

    # --- R4b: the routed copper --------------------------------------------
    # GND copper WELDS into the pour it crosses (clearpoly False) instead of
    # cutting a channel through it: it is the same net, so a clearance there
    # would be a gap between a conductor and itself, and welding is what turns
    # the router's GND traces into the stitches that keep the fill one piece
    # (see emit_dsn on why no plane is declared).  Everything else clears.
    net_of = dict(zip(range(len(route["tracks"])), route.get("track_nets", ())))
    for i, (layer, x1, y1, x2, y2, width) in enumerate(route["tracks"]):
        ln = L.line(50000 + i, x1, y1, x2, y2, thickness=width,
                    clearance=COPPER_CLEAR,
                    clearpoly=net_of.get(i) != "GND")
        (top if layer == "top" else bot).append(ln)
    for i, (vx, vy, vnet) in enumerate(route["vias"]):
        objs.append(L.ps_ref(26000 + 2 * i, PROTO_VIA, vx, vy,
                             name=f"VIA{i + 1}", clearance=COPPER_CLEAR,
                             thermal_lids=(L.LID_TOP_CU, L.LID_BOT_CU)
                             if vnet == "GND" else ()))

    for i, (layer, fnet, width, path) in enumerate(fixed_tracks()
                                                   + board_only_tracks()):
        for k, (a, b) in enumerate(zip(path, path[1:])):
            # GND welds into the pour on the same terms as routed GND copper:
            # a pre-placed GND stub that CLEARED the fill would cut itself a
            # moat and connect to nothing, which is the opposite of its job.
            ln = L.line(22000 + 10 * i + k, *lht_xy(*a), *lht_xy(*b),
                        thickness=width, clearance=COPPER_CLEAR,
                        clearpoly=fnet != "GND")
            (top if layer == "top" else bot).append(ln)

    # GND pour, both sides, inset to the copper-to-edge law, with the M3
    # copper keep-outs punched out.  Fill-channel width and fragment count are
    # judged DOWNSTREAM on the gerber raster (WS5), not here: this file states
    # the pour, the rasterizer is the oracle for what survives of it.
    # MEASURED: the polygon's OWN clearance field is what pcb-rnd checks the
    # pour against, and at exactly 0.40 it ties the law and reads as 13 net-
    # short violations against every track and island the pour clips around.
    # At COPPER_CLEAR it clears.  Same lesson as every other margin here: a
    # number that must pass a '<' test cannot BE the number in the test.
    pour = L.polygon(30000, rounded_rect(POUR_EDGE_SETBACK), COPPER_CLEAR)
    assert_pour_holes_inside()
    holes = "".join(
        "\n       ta:hole {\n" +
        "\n".join(f"        {{ {x}mm; {y}mm }}" for x, y in
                  circle_pts(*lht_xy(mx, my), MOUNT_KEEPOUT_R)) +
        "\n       }" for mx, my in MOUNTS.values())
    pour = pour.replace("\n      }\n     }", holes + "\n      }\n     }")
    top.append(pour)
    bot.append(pour.replace("ha:polygon.30000", "ha:polygon.30001"))

    edge = [lht_xy(*p) for p in rounded_rect()]
    outline = "\n".join(
        L.line(9000 + i, *a, *b, thickness=0.15, clearance=0.0)
        for i, (a, b) in enumerate(zip(edge, edge[1:] + edge[:1])))

    silk = {"front": [], "back": []}
    for side, base, legend in (("front", 40000, labels["front_legend"]),
                               ("back", 41000, labels["back_legend"])):
        strokes = list(legend)
        for pl in labels[side]:
            bx, by = pl.x, BOARD_H - pl.y          # silklabel answers y-down
            strokes += text_strokes(pl.ref, bx, by, SILK_H,
                                    mirror=(side == "back"), rotation=pl.rot)
        silk[side] = [L.line(base + i, *lht_xy(s[0], s[1]),
                             *lht_xy(s[2], s[3]), thickness=SILK_W,
                             clearance=0.0)
                      for i, s in enumerate(strokes)]

    # Both mask layers and the paste layer are otherwise EMPTY, and that is the
    # design: every other opening on this board belongs to a padstack, so it
    # travels with the pad instead of being drawn a second time at coordinates
    # that could drift.  The only free-standing mask objects are the dead front
    # rings above, because their copper is free-standing too.
    return L.board(BOARD_W, BOARD_H, protos=protos, objects="\n".join(objs),
                   top="\n".join(top), bottom="\n".join(bot), outline=outline,
                   top_silk="\n".join(silk["front"]),
                   bottom_silk="\n".join(silk["back"]),
                   top_mask="\n".join(top_mask),
                   netlist=L.netlist_block(sorted(nets.items())),
                   track=TRACK, clearance=CLEAR)


# ---------------------------------------------------------------------------
# SERIALIZATION 2 — Specctra DSN: the ROUTABLE truth
# ---------------------------------------------------------------------------
# We write the DSN ourselves.  pcb-rnd's own exporter cannot state this board
# (back-side SMD lands make it emit "side-mirrored pin not supported" and no
# subcircuit reaches the file), and no exporter has a field for the milled-board
# plating model anyway.  R3-proven semantics, unchanged:
#   * resolution um 10, integer coordinates, dsn_y = -lihata_y
#   * ONE image per component, placed `front` at its own origin with relative
#     pins and NO mirroring — this is what defeats the side-mirrored-pin
#     problem; a back-side land is simply a shape on the B.Cu layer
#   * layer reachability IS which layers carry a (shape ...).  FreeRouting
#     ignores (plating ...), so a lead the bench cannot solder on the front is
#     expressed by having no front shape, plus a front keepout standing in for
#     the dead physical ring so the router neither uses nor collides with it
#   * every class carries (circuit (use_via ...)): FreeRouting 2.2.4 throws a
#     NullPointerException in AutorouteControl.init_net if a net has no via rule
#   * rule widths at law + DRC_MARGIN, so um quantization still lands legal
FCU, BCU = "F.Cu", "B.Cu"

# Nets the router is NEVER shown, because the board already carries them as
# fixed copper (board_only_tracks) and their corridors are reserved as keepouts.
# Offering a net the router cannot legally complete is not neutral: on RESET it
# cost >13 minutes of non-convergence and no session at all.  Withheld here,
# closed in copper, and judged by the galvanic gate like everything else.
DSN_OMIT_NETS = {"VSW"}
VIA_NAME = "VIA_STITCH"

# NEGATIVE CONTROL HOOK (R4b gate D, R3's pattern).  Set to a pid to describe
# that ONE pin to the router as an ordinary through pin — front shape, no front
# keepout — when the bench cannot solder it on the front at all.  Nothing else
# changes and vias stay available, so the router is invited, never coerced.
# The merge must then name it a FANTASY BRIDGE, refuse to promote it, and
# pcb-rnd must independently find the net it "closed" still open.
FANTASY_PIN = None


def is_thru(pin: Pin) -> bool:
    """Does the DSN show this THT lead as reachable on BOTH faces?"""
    return pin.dual or pin.pid == FANTASY_PIN


def dead_net(pid: str) -> str:
    """The private pseudo-net of one dead front ring.

    SINGLE-MEMBER by design: one net, one piece of copper, no pins.  A shared
    pseudo-net would tell the router that 24 scattered discs are one node and
    invite it to go and connect them; a net with nothing to connect is inert.
    """
    return "__dead_" + pid.replace("-", "_")


def shows_dead_copper(part: Part) -> bool:
    """Which parts' dead front rings are shown to the router as COPPER rather
    than fenced off with a keepout.

    The LED ring only, and the distinction is empirical, not aesthetic: the
    keepout encoding is what wedges FreeRouting (see build_parts), and it
    wedges because the 24 LED rings sit across the router's only crossing of
    the ring.  The other 13 keepouts predate the wedge and never caused it, so
    they are left exactly as they are — this changes ONE thing at a time.
    """
    return part.ref.startswith("LED")


# The crossings the forbidden anode bridges used to carry, PRE-SEEDED into the
# DSN as wire vias the router INHERITS instead of having to invent.
#
# Measured necessity (see build_parts): with the bridges simply removed, the
# router reports 23 unrouted at pass #1 against 8 before, and wedges — under
# either encoding of the dead rings, and with vias made cheap.  The +15 are the
# ring crossings, and FreeRouting will not buy them with vias of its own.  So
# the board buys them, which is exactly the trade the operator ruled for: "a
# bare wire via (open access, both faces) is jumper-class and fine."
#
# The LIST is the old promoted set, unchanged, because the crossing DEMAND has
# not moved — these are the ten places the routed front copper actually reached
# the back through an anode.  LED2-2 and LED3-2 are absent for the same reason
# they were never promoted: nothing crossed there.
SEEDED_BRIDGES = ("LED1-2", "LED4-2", "LED5-2", "LED6-2", "LED7-2",
                  "LED8-2", "LED9-2", "LED10-2", "LED11-2", "LED12-2")
# The seed sits on the anode pitch circle, offset along it from its own anode.
# At LEAD_R_OUT two rings 15 deg apart are 2*18.45*sin(7.5) = 4.82 mm centre to
# centre — 2.38 mm of copper gap against both neighbouring leads, where the law
# needs 0.40 — and the geometry admits any offset in 8.83..21.17 deg on either
# side.  Which one is a MEASUREMENT, not the midpoint: the ring is not alone on
# this board, and the first version of this code took a flat +15 deg and put
# LED1's seed 0.109 mm from the VCC rail as it leaves Q1.  The window is swept
# and the roomiest offset wins.
SEED_WINDOW = [round(s * (9.0 + 0.5 * k), 2)
               for k in range(25) for s in (1, -1)]

# A SECOND class of seed, MEASURED AND REJECTED — kept as the record of why
# the three stragglers below are not closed this way.  Adding an escape seed
# re-wedges FreeRouting: all three (13 seeds) hangs, and so does the single
# non-interior one (11 seeds, TP1-1, 5 min, no session).  The pass-8 bound is
# tuned to the ten-seed DSN — it is the last pass that COMPLETES on that board,
# and the wedge point moves when the board does.  Set this back to a non-empty
# tuple only with a router that does not wedge.
#
# The reasoning that motivated it still stands and is the record of the
# residue.  With
# the ten anode crossings inherited, FreeRouting converges (11.4 s, 8 unrouted,
# the same figure the bridged board finished with) but three connections stay
# open that no closer can finish: U1-1 (RESET), U1-2 (L3) and TP1-1 (L1).
# probe_lane measured why — U1-1's only back escape is a 3.00 mm CUL-DE-SAC at
# bearing 65 (corridor 0.334), and no wire via fits inside it because C2's land
# is 1.77 mm away where a threadable via needs 2.72.  A pin that cannot reach
# open copper and cannot change layer is not a routing problem, it is a
# geometry problem, and the closer works on a FROZEN board.  The router does
# not: give it a layer change within reach and it can rip up its own work to
# get there.  So these three get a seed as well, placed by the same measured
# search — nearest legal position wins, because a short hop is the whole point.
# RE-ARMED 2026-08-02 for VCC, and the board named the pin itself.
#
# The scrub growth left exactly one connection that neither FreeRouting nor the
# closer could make, and for the first time the closer said WHICH (see
# closing_tracks' `unclosed`): "UNCLOSED VCC: 2 pieces, no legal path
# (25.04, 30.96) <-> (19.14, 10.45) on ['bottom']".  Those two points are the
# whole story — the first is a stub off C2's VCC land INSIDE the LED ring, the
# second is Q1-2, where the fixed rail starts, OUTSIDE it.  VCC has to cross the
# ring to feed U1-8 and C2-1, the crossing is on the bottom face only, and the
# grown rings closed the last lane that admitted a 0.8 mm RAIL.
#
# This is the same problem the ten anode seeds solve and it gets the same
# answer: the BOARD buys the crossing instead of asking the router to find one.
# C2-1 rather than U1-8 because C2's land is the outer of the two and the seed
# search wants open copper to stand in.
#
# Q2-2 ADDED 2026-08-02 for the buzzer cell's GND, and it is the THIRD thing
# tried on that defect — the first two are recorded where they failed, because
# each ruled out a whole class of fix:
#
#   * a wire via ON the orphan is IMPOSSIBLE: SPEC's iron access keeps a via
#     1.5 mm off an SMD land, the orphan IS two SMD lands (Q2-2, C3-2), and a
#     sweep found ZERO legal seats within 7 mm under the extra demand that the
#     via also land in the front pour and on the orphan at once.
#   * a pre-placed protected GND track out of the cell CLEARS every law
#     (0.887 mm, board gate 21/21) and still loses, because it WEDGES the
#     router in pass #7 — see fixed_tracks, where that experiment is kept.
#
# A SEED IS NEITHER.  It is a point, not a barrier: FreeRouting treats it as
# one more place GND may change layer, so it can route THROUGH the cell and
# shove the four fencing nets aside to do it, which is exactly the demand the
# orphan represents.  That is the difference this board keeps re-learning —
# points do not wedge, tracks do.  The DSN already names Q2-2 and C3-2 among
# GND's twelve pins, so the router was never unaware of them; what it lacked
# was a way across, and a seed is that way.
#
# U1-1 ADDED next, and it is this list's ORIGINAL intended tenant — the escape
# class above was written for "U1-1 (RESET), U1-2 (L3) and TP1-1 (L1)" and then
# emptied because seeding them re-wedged the router of the day.  What re-armed
# it is the measurement two paragraphs up: the wedge belonged to pre-placed
# TRACKS, not to seeds, and the Q2-2 seed proved it by routing in 13 s.  So
# when the Q2-2 reroute closed the buzzer cell and left RESET open between
# U1-1 and TP5 instead, the fix was not a new mechanism but one more point.
#
# TP3-1 WAS TRIED AS A SEVENTH SEED AND REJECTED, and the rejection corrects a
# law this file wrote one working day earlier.  The four artwork fixes (pour
# setback, the mounts and gauges that followed it, U1's widened lands) forced a
# fresh roll and the board came back TWO connections short instead of one — L2
# stranded at TP3-1 and L3 at S1-1 — so TP3-1 was seeded to buy one of them
# back.  It WEDGED FreeRouting: no session in five minutes where this board
# routes in 23 s, killed at the subprocess timeout.
#
# So "points do not wedge, tracks do" is TOO STRONG and is corrected here to
# what was actually measured: a seed is far safer than a protected track, and
# six of them cost nothing, but a seed is still geometry the router must plan
# around and the seventh one in the ISP block was one too many.  The wedge
# remains what tools-route's own comment calls it — a property of the geometry,
# discovered only by running it — and the honest response is the same as ever:
# keep the change that converges, and spend the residue deliberately.  The
# board therefore ships TWO declared bench jumpers instead of one, each named
# and each chosen for two bare solder points (see tools-route.DECLARED_JUMPERS).
#
# Seeds are cheap in exactly the way the operator's 2026-08-01 ruling says a
# via is cheap, and expensive in the one way that matters here: each is a bench
# joint, so this list is kept as short as the board's rat count allows and
# every entry names the connection it bought.
#
# U1-3 AND TP5-1 ADDED 2026-08-02 ON THE GROWN BOARD (66 x 56), and between
# them they retire BOTH declared bench jumpers.  The seventh seed is the one
# the record above says wedged the router; it does not wedge here, and the
# reason is the reason for the whole roll — the board has 2 mm more of it.
#
# THE MIGRATION, measured, one full reroute per row (the same experiment the
# DECLARED_JUMPERS block runs on the old board, repeated on this one):
#
#     seeds                        vias  residual open connection
#     6 (as before the growth)      25   SND    U1-3   -> R14-1
#     7 (+U1-3)                     21   RESET  U1-1.. -> TP5-1
#     8 (+TP5-1)                    23   NONE — pcb-rnd: 0 rat lines, complete
#
# and the first two rows are why the eighth seed is not a treadmill step but
# the end of one.  A residue is only SPENDABLE as a declared jumper if both its
# terminals are bare metal — that is the declaration's own law (bare rings and
# pads over SMD lands) and jumper_audit enforces it by construction, since it
# resolves an endpoint by matching the pin's centre against the copper object's
# first point, which is the centre for a ring or an ISP pad and a CORNER for an
# SMD land.  SND's two pieces are U1-3 (a SOIC pin) and R14-1 (an 0805 land):
# no bare metal at all.  RESET's are {U1-1, R13-2, C4-1} and {TP5-1}: one bare
# pad against three lands.  NEITHER can be declared, so on this board a residue
# is not a thing to spend — it is a thing to close, and the seed is what closes
# it.  The eighth seed was chosen for that reason and not for the count.
SEEDED_ESCAPES = ("C2-1", "Q2-2", "U1-1", "U1-2", "TP1-1", "C3-1", "U1-3",
                  "TP5-1")
SEED_ESCAPE_R = [round(3.0 + 0.25 * k, 2) for k in range(21)]
VIA_BODY_SMD, VIA_BODY_THT = 1.5, 2.0     # SPEC "Via geometry": iron access
LED_POS = {led: pos for pos, led in POS_LED.items()}
_SEED_CACHE: dict = {}


def seed_clearance(x: float, y: float, net: str, objs: list) -> float:
    """Tightest gap a wire via at (x, y) on *net* would hold: copper on either
    face, the M3 keep-outs, and the board edge."""
    worst = min((shape_gap(([(x, y)], RING_VIA / 2), (pts, rad))
                 for n2, _lay, pts, rad in objs
                 if n2 is not None and n2 != net), default=99.0)
    for mx, my in MOUNTS.values():
        worst = min(worst,
                    math.hypot(mx - x, my - y) - MOUNT_KEEPOUT_R - RING_VIA / 2)
    edge = rounded_rect(EDGE_CLEAR)
    worst = min(worst, min(pt_seg(x, y, a[0], a[1], b[0], b[1])
                           for a, b in zip(edge, edge[1:] + edge[:1]))
                - RING_VIA / 2)
    return worst


def seeded_vias(parts: list[Part]) -> list[tuple]:
    """(pid, x, y, net) — one inherited wire via per forbidden anode bridge.

    Empty when the LEDs are dual-solder-capable, so this whole mechanism
    switches itself off if the 2026-08-01 ruling is ever reverted: a seeded
    crossing beside a lead that is ITSELF a layer bridge would be a second way
    over the same fence, and the bench would drill it for nothing.

    Deterministic: a fixed candidate sequence, scored by measured clearance,
    tie-broken by the smallest offset and then by sign.
    """
    by_pid = {p.pid: p for part in parts for p in part.pins}
    key = tuple((pid, by_pid[pid].net, by_pid[pid].dual)
                for pid in SEEDED_BRIDGES)
    if key in _SEED_CACHE:
        return _SEED_CACHE[key]
    objs = copper_objects(parts)
    out = []
    for pid in SEEDED_BRIDGES:
        pin = by_pid[pid]
        if pin.dual:
            continue
        base = pos_angle(LED_POS[int(pin.ref[3:])])
        best = None
        for off in SEED_WINDOW:
            x, y = polar(RING_CX, RING_CY, base + off, LEAD_R_OUT)
            x, y = q(x), q(y)
            g = seed_clearance(x, y, pin.net, objs)
            rank = (-round(min(g, COPPER_CLEAR), 3), abs(off), -off)
            if best is None or rank < best[0]:
                best = (rank, x, y, g)
        if best is None or best[3] < CLEAR:
            raise SystemExit(
                f"refusing to emit: no legal pre-seeded crossing for {pid} "
                f"anywhere on its anode circle (best "
                f"{-1 if best is None else best[3]:.3f} mm, law {CLEAR})")
        out.append((pid, best[1], best[2], pin.net))
        # the seed is copper the NEXT seed has to clear, too
        objs = objs + [(pin.net, "top", [(best[1], best[2])], RING_VIA / 2),
                       (pin.net, "bottom", [(best[1], best[2])], RING_VIA / 2)]
    bodies = [(p.x, p.y, VIA_BODY_SMD if p.kind == "rect" else VIA_BODY_THT)
              for part in parts for p in part.pins]
    for pid in SEEDED_ESCAPES:
        pin = by_pid[pid]
        if pin.dual:
            continue
        best = None
        for rad in SEED_ESCAPE_R:
            for deg in range(0, 360, 5):
                x, y = polar(pin.x, pin.y, float(deg), rad)
                x, y = q(x), q(y)
                if not (3.0 < x < BOARD_W - 3.0 and 3.0 < y < BOARD_H - 3.0):
                    continue
                if any(math.hypot(bx - x, by - y) < keep + RING_VIA / 2
                       for bx, by, keep in bodies):
                    continue          # no iron could reach it
                g = seed_clearance(x, y, pin.net, objs)
                if g < COPPER_CLEAR:
                    continue
                rank = (rad, -round(g, 3), deg)
                if best is None or rank < best[0]:
                    best = (rank, x, y, g)
            if best is not None:
                break                 # nearest legal ring wins: a SHORT hop
        if best is None:
            raise SystemExit(
                f"refusing to emit: no legal escape crossing for {pid} within "
                f"{SEED_ESCAPE_R[-1]} mm — that pin cannot be reached at all")
        out.append((pid, best[1], best[2], pin.net))
        objs = objs + [(pin.net, "top", [(best[1], best[2])], RING_VIA / 2),
                       (pin.net, "bottom", [(best[1], best[2])], RING_VIA / 2)]
    _SEED_CACHE[key] = out
    return out


def assert_seeded_vias_legal(parts: list[Part]) -> float:
    """-> the tightest gap any seed holds.  Refuses to emit an illegal one.

    A seed is a hole a human drills and threads, so it is checked like any
    other copper BEFORE it reaches the router — the alternative is discovering
    at the gate that the board asked the bench for an impossible joint.
    """
    objs = copper_objects(parts)
    seeds = seeded_vias(parts)
    worst, who = 99.0, None
    for i, (pid, x, y, net) in enumerate(seeds):
        extra = [(n, lay, [(sx, sy)], RING_VIA / 2)
                 for j, (_p, sx, sy, n) in enumerate(seeds) if j != i
                 for lay in ("top", "bottom")]
        g = seed_clearance(x, y, net, objs + extra)
        if g < worst:
            worst, who = g, pid
    if worst < CLEAR:
        raise SystemExit(
            f"refusing to emit: pre-seeded crossing for {who} is "
            f"{worst:.3f} mm from other copper, law {CLEAR}")
    return worst


def ps_name(pin: Pin) -> str:
    if pin.kind == "rect":
        return (f"PS_SMD_{pin.shape[1]:.3f}x{pin.shape[2]:.3f}"
                f"r{pin.prot:.1f}").replace(".", "M")
    if pin.kind == "circ":
        return f"PS_PAD_{pin.shape[1]:.2f}".replace(".", "M")
    kind = "THRU" if is_thru(pin) else "BACK"
    return f"PS_{kind}_{pin.shape[2]:.2f}".replace(".", "M")


def emit_dsn(parts: list[Part], nets: dict) -> str:
    out = ["(pcb orbit", "  (parser", '    (string_quote ")',
           "    (space_in_quoted_tokens on)", '    (host_cad "ClauderaCAM")',
           '    (host_version "R4a")', "  )",
           "  (resolution um 10)", "  (unit um)", "  (structure",
           f"    (layer {FCU} (type signal) (property (index 0)))",
           f"    (layer {BCU} (type signal) (property (index 1)))"]
    path = " ".join(f"{x} {y}" for x, y in
                    (dsn_xy(*p) for p in rounded_rect()))
    first = " ".join(str(v) for v in dsn_xy(*rounded_rect()[0]))
    out.append(f"    (boundary (path pcb 0  {path}  {first}))")
    out.append(f"    (via {VIA_NAME})")
    out.append(f"    (rule (width {dsn_len(ROUTE_TRACK)}) "
               f"(clearance {dsn_len(ROUTE_CLEAR)}))")
    # NEITHER pour is declared as a (plane ...), and that is the R4b finding.
    #
    # A Specctra plane tells FreeRouting "every pin on this net is already
    # connected", and FreeRouting then drives signal traces straight through it
    # without ever checking that what is left is still one conductor.  MEASURED
    # on this board: with the back pour declared, seven GND terminals — U1-4,
    # C2-2, C3-2, C4-2, Q2-2, R15-2, D1-1 — ended up on copper islands joined
    # to nothing, and the router reported the net complete.  Declaring the
    # FRONT pour as a second GND plane did not help: same seven.
    #
    # So GND is handed over as an ordinary 12-terminal net and the router draws
    # real copper for it.  The pours are still emitted as physical truth, and
    # emit_lihata WELDS GND copper into them (clearpoly False) instead of
    # clearing it, so every trace the router draws also stitches the fill it
    # crosses.  Connectivity then comes from metal we can point at, and the
    # pour is what it honestly is on a milled board: a conductor where it
    # survives, and shielding where it does not.
    dead = []            # (pid, x, y, dia) — rings shown as COPPER, not fence
    for part in parts:
        for p in part.pins:
            if p.kind == "tht" and not is_thru(p):
                x, y = dsn_xy(p.x, p.y)
                if shows_dead_copper(part):
                    dead.append((p.pid, x, y, dsn_len(p.shape[2])))
                else:
                    out.append(f'    (keepout "" (circle {FCU} '
                               f"{dsn_len(p.shape[2])} {x} {y}))")
    for gx, gy in GAUGES.values():                 # dead islands, both faces
        x, y = dsn_xy(gx, gy)
        for lay in (FCU, BCU):
            out.append(f'    (keepout "" (circle {lay} '
                       f"{dsn_len(RING_GAUGE)} {x} {y}))")
    # M3 copper keep-out, INFLATED to ROUTE_MOUNT_KEEPOUT_R: the router must
    # stay far enough out that its own pour cutout still clears the hole this
    # keep-out punches in the plane (see POUR_HOLE_MARGIN — a trace parked on
    # the bare 3.2 keep-out would put its cutout 0.42 INSIDE the hole and take
    # the whole GND plane down with it).
    for mx, my in MOUNTS.values():
        x, y = dsn_xy(mx, my)
        for lay in (FCU, BCU):
            out.append(f'    (keepout "" (circle {lay} '
                       f"{dsn_len(2 * ROUTE_MOUNT_KEEPOUT_R)} {x} {y}))")
    # The board-only copper's corridor, RESERVED so the router cannot lay a
    # different net through metal it was never shown (see board_only_tracks:
    # untold, it shorted GND to VSW at Q1-3).  Aperture = the track's own width
    # plus a full clearance on each side, so the router's copper lands legal by
    # construction rather than by luck.
    for layer, _net, width, pts in board_only_tracks():
        lay = FCU if layer == "top" else BCU
        path = " ".join(f"{x} {y}" for x, y in (dsn_xy(*p) for p in pts))
        out.append(f'    (keepout "" (path {lay} '
                   f"{dsn_len(width + 2 * ROUTE_CLEAR)} {path}))")
    out.append("  )")

    out.append("  (placement")
    for part in parts:
        ox, oy = dsn_xy(part.pins[0].x, part.pins[0].y)
        out.append(f"    (component IMG_{part.ref} "
                   f"(place {part.ref} {ox} {oy} front 0))")
    out.append("  )")

    out.append("  (library")
    stacks: dict[str, str] = {}
    for part in parts:
        out.append(f"    (image IMG_{part.ref}")
        ox, oy = part.pins[0].x, part.pins[0].y
        for p in part.pins:
            rx, ry = dsn_xy(p.x, p.y)
            bx, by = dsn_xy(ox, oy)
            out.append(f"      (pin {ps_name(p)} {p.term} {rx - bx} {ry - by})")
            if ps_name(p) not in stacks:
                if p.kind == "rect":
                    # DSN y runs the SAME way as the board frame (dsn_xy is a
                    # translation in y, not a flip), so the corners go in as
                    # they come out of the model.  Negating cy here mirrored
                    # every rotated land — see the -prot note in emit_lihata;
                    # the router was being shown 8 ring-resistor pads reflected
                    # about their own centres and attaching traces to copper
                    # that is not there.
                    pts = L.rect_corners(p.shape[1], p.shape[2], p.prot)
                    poly = " ".join(f"{dsn_len(cx)} {dsn_len(cy)}"
                                    for cx, cy in pts + pts[:1])
                    stacks[ps_name(p)] = (f"      (shape (polygon {BCU} 0 "
                                          f"{poly}))")
                elif p.kind == "circ":
                    stacks[ps_name(p)] = (f"      (shape (circle {BCU} "
                                          f"{dsn_len(p.shape[1])}))")
                else:
                    d = dsn_len(p.shape[2])
                    shapes = [f"      (shape (circle {BCU} {d}))"]
                    if is_thru(p):
                        shapes.insert(0, f"      (shape (circle {FCU} {d}))")
                    stacks[ps_name(p)] = "\n".join(shapes)
        out.append("    )")
    for name in sorted(stacks):
        out += [f"    (padstack {name}", stacks[name], "      (attach off)",
                "    )"]
    d = dsn_len(RING_VIA)
    out += [f"    (padstack {VIA_NAME}",
            f"      (shape (circle {FCU} {d}))",
            f"      (shape (circle {BCU} {d}))", "      (attach off)", "    )",
            "  )"]

    out.append("  (network")
    for name in sorted(nets):
        if name in DSN_OMIT_NETS:
            continue
        out.append(f"    (net {name}")
        out.append("      (pins " + " ".join(sorted(nets[name])) + ")")
        out.append("    )")
    # The dead rings' pseudo-nets are DECLARED, with no pins, so the class
    # below can carry a via rule for them: FreeRouting 2.2.4 throws a
    # NullPointerException in AutorouteControl.init_net for any net without
    # one, and a net it learned about only from a wire is still a net.
    for pid, _x, _y, _d in dead:
        out.append(f"    (net {dead_net(pid)}")
        out.append("      (pins )")
        out.append("    )")
    out.append("    (class signal "
               + " ".join([n for n in sorted(nets)
                           if n not in DSN_OMIT_NETS]
                          + [dead_net(pid) for pid, _x, _y, _d in dead]))
    out.append(f"      (circuit (use_via {VIA_NAME}))")
    out.append(f"      (rule (width {dsn_len(ROUTE_TRACK)}) "
               f"(clearance {dsn_len(ROUTE_CLEAR)}))")
    out += ["    )", "  )"]

    out.append("  (wiring")
    # Each dead front ring, as a disc of foreign copper the router must clear.
    # The path is two points 0.2 um apart rather than one point repeated: a
    # zero-length wire is a degenerate object to hand a parser, and at this
    # length the swept shape is the Ø{ring} disc to well under the 0.1 um
    # resolution the file is written in.
    for pid, x, y, d in dead:
        out.append(f"    (wire (path {FCU} {d} {x - 1} {y} {x + 1} {y}) "
                   f"(net {dead_net(pid)}) (type protect))")
    # The inherited crossings.  PROTECTED so the router connects to them
    # instead of ripping them out and re-wedging; a single via POINT is not the
    # long-protected-wire poison this file records elsewhere — the VBAT
    # corridor is a protected run and routes fine, what breaks the router is
    # protected copper lying ACROSS its corridors.
    assert_seeded_vias_legal(parts)
    for pid, x, y, net in seeded_vias(parts):
        vx, vy = dsn_xy(x, y)
        out.append(f"    (via {VIA_NAME} {vx} {vy} (net {net}) "
                   f"(type protect))")
    for layer, net, width, pts in fixed_tracks():
        lay = FCU if layer == "top" else BCU
        for a, b in zip(pts, pts[1:]):
            ax, ay = dsn_xy(*a)
            bx, by = dsn_xy(*b)
            out.append(f"    (wire (path {lay} {dsn_len(width)} "
                       f"{ax} {ay} {bx} {by}) (net {net}) (type protect))")
    out += ["  )", ")"]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# BUILD
# ---------------------------------------------------------------------------
NET_FILE = os.path.join(HERE, "orbit.net")
TDX_FILE = os.path.join(HERE, "orbit.tdx")


def export_netlist() -> str:
    """Refresh orbit.net from the schematic if kicad-cli is present.

    kicad-cli is an EXTERNAL, optional dependency (CLAUDE.md working rules):
    when it is absent the cached netlist is used and the skip is loud.  When
    neither exists the generator refuses — a board whose connectivity nobody
    can check is not a board.
    """
    from shutil import which
    if which("kicad-cli"):
        subprocess.run(["kicad-cli", "sch", "export", "netlist", "--format",
                        "kicadsexpr", "-o", NET_FILE, SCH],
                       check=True, capture_output=True)
    elif os.path.exists(NET_FILE):
        print("SKIP: kicad-cli absent — using the cached orbit.net",
              file=sys.stderr)
    else:
        raise SystemExit("no kicad-cli and no cached orbit.net: refusing")
    return NET_FILE


def build(route: dict | None = None, out_lht: str = OUT_LHT) -> dict:
    """Emit the board.  With *route* None this is R4a's unrouted physical truth;
    with R4b's merged session it is the FINAL routed board.  ONE emitter, one
    placement model, so the routed board can never drift from the one the
    router was shown."""
    nets_sch, stats = kicadnet.read_nets(export_netlist())
    parts = build_parts()
    nets = bind_nets(parts, nets_sch)
    fi, bi = front_legend(parts, route), back_legend(parts, route)
    fl, bl = legend_ink(fi), legend_ink(bi)
    # ROUTE GOES TO THE LABELLER TOO, and it is half the fix for the 2026-08-02
    # render incident: 23 wire vias are mask apertures on both faces and they
    # exist only in the routed board, so a placer that never sees `route` seats
    # labels on via rings and every check on the UNROUTED board agrees with it.
    pf, uf = label_parts(parts, "front", fl, route)
    pb, ub = label_parts(parts, "back", bl, route)
    labels = {"front": pf, "back": pb,
              "front_legend": fl, "back_legend": bl,
              "front_items": fi, "back_items": bi}
    with open(out_lht, "w", encoding="utf-8") as fh:
        fh.write(emit_lihata(parts, nets, labels, route))
    with open(OUT_DSN, "w", encoding="utf-8") as fh:
        fh.write(emit_dsn(parts, nets))
    with open(TDX_FILE, "w", encoding="utf-8") as fh:
        fh.write(kicadnet.to_tedax(nets))
    # --- the 2026-08-03 ordering-law exports, gerber/lht frame ------------
    # inert-front: every dead front ring the FINAL board carries — the mask
    # opens over them (the uniformity/checkability law above) but the bench
    # never solders them, so the scrub skips them and they keep their flood
    # coat. Consumed by reemit.scrub_mask via [phases.front.scrub] inert.
    promoted = set((route or {}).get("promoted", ()))
    # BOARD frame, not lht: the FILTERED mask gerbers carry board-frame
    # coordinates (measured 2026-08-03: F_Mask flash (16.342, 8.117) == the
    # board-frame via), while the Excellon is the y-flipped export — the
    # bores list below matches THAT frame instead. Two exports, two frames,
    # each matched to its consumer and each refused loudly on drift.
    inert = [(pid, q(x), q(y))
             for pid, x, y, dia, joins in dead_front_rings(parts)
             if pid not in promoted]
    with open(os.path.join(HERE, "orbit-inert-front.txt"),
              "w", encoding="utf-8") as fh:
        fh.write(
        "# dead front rings of the FINAL board — mask-open, never soldered,"
        " never scrubbed\n# x,y in the BOARD frame"
        " (board frame - the filtered gerbers\' own)\n"
        + "".join(f"{x:.3f},{y:.3f}  # {pid} dead front ring\n"
                  for pid, x, y in sorted(inert, key=lambda t: t[0])))
    # bores: the NON-PAD holes setup 1 cuts (gauges + mounts). tools-fab
    # splits the merged Excellon on these positions; everything else is a
    # pad hole and waits for setup 2, after both scrubs.
    bores = ([(f"gauge {r}", *lht_xy(gx, gy), HOLE_GAUGE)
              for r, (gx, gy) in GAUGES.items()]
             + [(f"mount {r}", *lht_xy(mx, my), HOLE_MOUNT)
                for r, (mx, my) in MOUNTS.items()])
    with open(os.path.join(HERE, "orbit-bores.txt"),
              "w", encoding="utf-8") as fh:
        fh.write(
        "# non-pad holes cut in SETUP 1 (the ordering law) — gerber/lht"
        " frame\n"
        + "".join(f"{x:.3f},{y:.3f},{d:.3f}  # {name}\n"
                  for name, x, y, d in bores))
    return {"parts": parts, "nets": nets, "stats": stats, "labels": labels,
            "unplaced": uf + ub, "placed": len(pf) + len(pb), "route": route}


# ---------------------------------------------------------------------------
# GATE
# ---------------------------------------------------------------------------
def _probes(step, n, fn):
    return "+".join(f"({fn}{k * step})==0)" for k in range(1, n + 1))


def drc_rules() -> str:
    """The two hand-written drc_query rules from R2 surface 3.

    They introduce NO threshold of their own: both read the same native conf
    nodes the stock rules read ($min_copper_clearance, $min_ring), so there is
    exactly one number per law.  Their only job is to attach a measurement to
    the violation, which the stock rules do not.
    """
    clr = ("assert (A.IID < B.IID) && (A.netname != B.netname) && "
           "intersect(A, B, $min_copper_clearance) thus violation(DRCGRP1, A, "
           f"DRCGRP2, B, DRCMEASURE, coord(50000*("
           f"{_probes(50000, 8, 'intersect(A,B,')})), "
           "DRCEXPECT, $min_copper_clearance)")
    ring = ("assert (@.type == PSTK) && (@.hole > 0) && "
            "(pstkring(@, $min_ring) > 0) thus violation(DRCGRP1, @, "
            f"DRCMEASURE, coord(100000*({_probes(100000, 10, 'pstkring(@,')})), "
            "DRCEXPECT, $min_ring)")
    return f"""tEDAx v1

begin drc_query_rule v1 mill_clearance
\ttype mill copper clearance
\ttitle L1 copper clearance below minimum
\tdesc ClauderaCAM law L1: gap between copper of different nets is below the required minimum (measured value is floored to 0.05 mm)
\tquery rule mill_clearance
\tquery let A ((@.layer.type == COPPER) && (@.netname != "")) thus @
\tquery let B A
\tquery {clr}
end drc_query_rule

begin drc_query_rule v1 mill_ring
\ttype mill annular ring
\ttitle L2 annular ring below minimum
\tdesc ClauderaCAM law L2: padstack annular ring WIDTH (neck between hole contour and copper contour) is below the required minimum (measured value is floored to 0.10 mm)
\tquery rule mill_ring
\tquery {ring}
end drc_query_rule
"""


NOISE = ("font-symbol-file", "default.pcb", "footprint library", "DRC: ")


def pcb_rnd(actions: str, board_file: str) -> str:
    """Run pcb-rnd headless with *actions* on stdin.  The three noise lines are
    artefacts of extracting the .debs to a non-/usr prefix and cannot be
    configured away; they carry no geometry (see the wrapper's header)."""
    r = subprocess.run([PCB_RND, "--gui", "batch", board_file],
                       input=actions, capture_output=True, text=True)
    return "\n".join(ln for ln in (r.stdout + r.stderr).splitlines()
                     if ln.strip() and not any(n in ln for n in NOISE))


def run_drc(board_file: str) -> str:
    """DRC with the three milled-board thresholds set through BATCH conf actions.

    Never the `-c design/drc/...` CLI form: it half-applies (R2 finding) and a
    threshold that silently fails to take is worse than no check at all.
    """
    rules = os.path.join(HERE, "orbit-drc.tdx")
    with open(rules, "w", encoding="utf-8") as fh:
        fh.write(drc_rules())
    return pcb_rnd(f"LoadTedaxFrom(drc_query, {rules})\n"
                   f"conf(set, design/drc/min_copper_clearance, {CLEAR}mm)\n"
                   f"conf(set, design/drc/min_ring, {RING}mm)\n"
                   f"conf(set, design/drc/min_drill, {DRILL_MIN}mm)\n"
                   "DRC(list)\n", board_file)


def pt_seg(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def seg_pad_gap(seg, pin: Pin) -> float:
    """Edge-to-edge gap between a silk stroke's ink and a pad's copper."""
    x1, y1, x2, y2 = seg
    hw, hh = pin.extent()
    if pin.kind == "tht" or pin.kind == "circ":
        d = pt_seg(pin.x, pin.y, x1, y1, x2, y2) - hw
    else:
        cx = min(max((x1 + x2) / 2, pin.x - hw), pin.x + hw)
        cy = min(max((y1 + y2) / 2, pin.y - hh), pin.y + hh)
        corners = pin.corners() + [(cx, cy)]
        d = min(pt_seg(px, py, x1, y1, x2, y2) for px, py in corners)
    return d - SILK_W / 2


# --- an INDEPENDENT clearance oracle ---------------------------------------
# MEASURED during R4a, and the reason this section exists: pcb-rnd resolves an
# object's netname through CONNECTIVITY, so on an UNROUTED board — where almost
# nothing is connected to anything — the netname-driven clearance rule selects
# almost no objects and cannot fail.  A control board with two different-net
# lands 0.25 mm apart drew ZERO violations, while the same rule correctly
# convicts the R3 lab's routed bad_clr board.  A check that cannot fail is not
# a check (Article III), so the law is also measured here, by geometry that
# shares no lineage with pcb-rnd's, and the negative control below proves this
# one CAN fail.
def seg_seg(a, b, c, d) -> float:
    if max(a[0], b[0]) >= min(c[0], d[0]) and max(c[0], d[0]) >= min(a[0], b[0]) \
       and max(a[1], b[1]) >= min(c[1], d[1]) \
       and max(c[1], d[1]) >= min(a[1], b[1]):
        def cross(p, qq, r):
            return ((qq[0] - p[0]) * (r[1] - p[1])
                    - (qq[1] - p[1]) * (r[0] - p[0]))
        d1, d2 = cross(c, d, a), cross(c, d, b)
        d3, d4 = cross(a, b, c), cross(a, b, d)
        if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
            return 0.0
    return min(pt_seg(a[0], a[1], *c, *d), pt_seg(b[0], b[1], *c, *d),
               pt_seg(c[0], c[1], *a, *b), pt_seg(d[0], d[1], *a, *b))


# ---------------------------------------------------------------------------
# THE CROWDING AUDIT — every silk seat on the board, measured
# ---------------------------------------------------------------------------
# The audit that missed the 2026-08-02 render defects was an ad-hoc sweep over
# SOLDERABLE pads, so it could not see the via rings three labels were sitting
# on and it had no notion of one text touching another.  It is code now, it
# reads silk_keepouts (the corrected set), and it measures all four laws at
# once.  ZERO flags is the bar the board ships at; the +25 % band is a warning
# that a seat is squeezed even though it is legal, and the board's history says
# that band is where the next defect comes from.
CROWD_BAND = 1.25        # a seat inside +25 % of a law is squeezed, not safe


def silk_items(labels: dict, side: str) -> list[tuple]:
    """(name, owner, strokes, cap-h) for EVERY text on one side.

    A ref label and a legend mark obey the same laws, so the audit must see
    them as one population: "PAD1ON" was a ref label fusing with a legend, and
    a check that judged only one of the two classes could not have caught it.
    """
    out = list(labels[f"{side}_items"])
    for pl in labels[side]:
        out.append((pl.ref, pl.ref,
                    text_strokes(pl.ref, pl.x, BOARD_H - pl.y, SILK_H,
                                 mirror=(side == "back"), rotation=pl.rot),
                    SILK_H))
    return out


def _ink_bbox(strokes, pad=SILK_W / 2):
    xs = [c for s in strokes for c in (s[0], s[2])]
    ys = [c for s in strokes for c in (s[1], s[3])]
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def _ink_gap(sa: list, sb: list) -> float:
    """Ink-to-ink gap between two stroke runs (each stroke is SILK_W wide)."""
    best = float("inf")
    for p in sa:
        for r in sb:
            best = min(best, seg_seg((p[0], p[1]), (p[2], p[3]),
                                     (r[0], r[1]), (r[2], r[3])))
            if best <= 0.0:
                return -SILK_W
    return best - SILK_W


def silk_seats(parts: list[Part], labels: dict,
               route: dict | None = None) -> list[dict]:
    """One row per silk text per side, with every law's measurement on it.

    Rows carry the numbers, not verdicts — the gate, the audit script and the
    bench notes all read the same rows and apply the bars themselves.
    """
    rows = []
    for side in ("front", "back"):
        keep = silk_keepouts(parts, side, route)
        named = named_features(parts)
        items = silk_items(labels, side)
        boxes = [_ink_bbox(s) for _n, _o, s, _h in items]
        for i, (name, owner, strokes, h) in enumerate(items):
            gaps = [(min(seg_pad_gap(s, pin) for s in strokes), pin)
                    for pin in keep]
            fg, fpin = min(gaps, key=lambda g: g[0])
            # ATTRIBUTION IS JUDGED ON THE INK BOX, not the strokes, and the
            # two consumers must agree or the audit convicts seats the placer
            # was entitled to take: silklabel scores a candidate BOX (it has
            # no glyphs yet), so the audit scores the same box.  The box
            # contains the ink, so this can only ever be the stricter read.
            box = stroke_bbox(strokes)
            bg = [(rect_feature_gap(box, pin), pin) for pin in keep]
            own = min((g for g, pin in bg if pin.ref == owner), default=None)
            oth = min(((g, pin) for g, pin in bg
                       if pin.ref != owner and pin.ref in named),
                      key=lambda g: g[0], default=(None, None))
            # THE TEXT-SEPARATION BAR SCALES WITH THE TYPE, because the eye
            # judges a seam against the size of the glyphs beside it: one cap
            # height of the LARGER text.  A flat 1.5 would make the ISP block
            # (1.0 mm names on a 2.54 grid, 1.29 of real gap) unbuildable
            # while judging nothing about how it reads.
            tg, tname, tbar = float("inf"), "", 0.0
            bx = boxes[i]
            for j, (n2, _o2, s2, h2) in enumerate(items):
                if j == i:
                    continue
                b2 = boxes[j]
                if (bx[0] - b2[2] > 3.0 or b2[0] - bx[2] > 3.0
                        or bx[1] - b2[3] > 3.0 or b2[1] - bx[3] > 3.0):
                    continue
                g = _ink_gap(strokes, s2)
                if g - max(h, h2) < tg - tbar or tname == "":
                    tg, tname, tbar = g, n2, max(h, h2)
            ratio = (attribution_ratio(box, owner, keep, named)
                     if owner is not None else None)
            rows.append({"side": side, "item": name, "owner": owner, "h": h,
                         "feature_gap": fg, "feature": fpin.pid,
                         "own_gap": own, "rival_gap": oth[0],
                         "rival": oth[1].pid if oth[1] is not None else "",
                         "ratio": ratio, "text_gap": tg, "text": tname,
                         "text_bar": tbar, "n_features": len(keep)})
    return rows


def silk_flags(rows: list[dict]) -> list[str]:
    """Every way a seat can be wrong, in one place.  ZERO is the bar."""
    out = []
    for r in rows:
        who = f"{r['side']}/{r['item']}"
        if r["feature_gap"] < SILK_CLEAR:
            out.append(f"{who}: ink {r['feature_gap']:+.3f} from "
                       f"{r['feature']} — the clip would eat it (law "
                       f"{SILK_CLEAR})")
        elif r["feature_gap"] < SILK_CLEAR * CROWD_BAND:
            out.append(f"{who}: SQUEEZED, ink {r['feature_gap']:.3f} from "
                       f"{r['feature']} (+25 % band is "
                       f"{SILK_CLEAR * CROWD_BAND:.3f})")
        if r["text_gap"] < r["text_bar"]:
            out.append(f"{who}: {r['text_gap']:+.3f} of ink to text "
                       f"{r['text']!r} — two texts read as one word "
                       f"(law {r['text_bar']:.2f}, one cap height)")
        if r["owner"] is None:
            near = min(r["feature_gap"], r["text_gap"])
            if near < r["h"]:
                out.append(
                    f"{who}: names nothing yet sits {near:.3f} from "
                    f"{r['feature'] if near == r['feature_gap'] else r['text']}"
                    f" — reads as its label (law {r['h']:.2f})")
        elif r["ratio"] is not None and r["ratio"] > ATTRIB_RATIO:
            out.append(f"{who}: AMBIGUOUS, {r['own_gap']:.3f} to its own "
                       f"{r['owner']} against {r['rival_gap']:.3f} to "
                       f"{r['rival']} (ratio {r['ratio']:.2f} > "
                       f"{ATTRIB_RATIO})")
    return out


def silk_controls(parts: list[Part], labels: dict) -> list[tuple]:
    """Three deliberately broken legends, one per silk law.

    Each reproduces a defect the operator caught on the shipped render, which
    is the only honest way to prove the audit would have caught it: a label
    parked on a ring ("LED8"), a label fused to a legend ("PAD1ON"), and a
    label the bench cannot bind to a feature ("G4 S2").
    """
    import copy
    by = {p.ref: p for p in parts}

    def reseat(ref, bx, by_):
        """Park *ref*'s label at a board-frame point, placed or not.

        It has to INSERT rather than replace: a label the real placer dropped
        (PAD1 is one, since the bottom strip's "+" supersedes it) would
        otherwise make its own control vacuous — the control would perturb
        nothing and the audit would rightly stay silent, which reads exactly
        like an audit that cannot fail.
        """
        hurt = copy.deepcopy(labels)
        hurt["front"] = [pl for pl in labels["front"] if pl.ref != ref]
        hurt["front"].append(SL.Placement(ref, bx, BOARD_H - by_, 0, "N"))
        return hurt

    out = []
    pin = by["LED1"].pins[0]
    out.append(("a label parked on a ring|the clip would eat it",
                reseat("LED1", pin.x, pin.y)))
    on = [it for it in labels["front_items"] if it[0] == "ON"][0]
    bb = _ink_bbox(on[2])
    out.append(("a label fused to a legend|read as one word",
                reseat("PAD1", (bb[0] + bb[2]) / 2,
                       bb[3] + 0.15 + SILK_W / 2 + SILK_H / 2)))
    # The third control is the operator's own case ("G4 S2", "S1 G2"): a label
    # that is CLEAR of every aperture and still unbindable, because it sits
    # beside somebody else's part.  It is parked just outboard of LED1's
    # nearest neighbour, so its own LED is the FARTHER of the two — legal ink,
    # unreadable claim, and the only law that can catch it is attribution.
    own = by["LED1"].pins[1]
    rival = min((p for part in parts if part.ref != "LED1"
                 for p in part.pins if part.side == "front"),
                key=lambda p: math.hypot(p.x - own.x, p.y - own.y))
    ux, uy = rival.x - own.x, rival.y - own.y
    un = math.hypot(ux, uy) or 1.0
    reach = rival.extent()[0] + 0.5 + text_size("LED1")[0] / 2 + SILK_W / 2
    out.append(("a label the bench cannot bind|AMBIGUOUS",
                reseat("LED1", rival.x + ux / un * reach,
                       rival.y + uy / un * reach)))
    return out


def _edges(pts):
    if len(pts) == 1:
        return [(pts[0], pts[0])]
    if len(pts) == 2:
        return [(pts[0], pts[1])]
    return list(zip(pts, pts[1:] + pts[:1]))


def _inside(p, pts) -> bool:
    if len(pts) < 3:
        return False
    sign = None
    for a, b in _edges(pts):
        c = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
        if c == 0:
            continue
        if sign is None:
            sign = c > 0
        elif (c > 0) != sign:
            return False
    return True


def shape_gap(sa, sb) -> float:
    """Edge-to-edge gap between two (points, radius) shapes.  A pad is a
    polygon with radius 0, a ring is one point with radius, a track is a
    two-point segment with radius = half its width."""
    (pa, ra), (pb, rb) = sa, sb
    if any(_inside(p, pb) for p in pa) or any(_inside(p, pa) for p in pb):
        return -(ra + rb)
    d = min(seg_seg(e1[0], e1[1], e2[0], e2[1])
            for e1 in _edges(pa) for e2 in _edges(pb))
    return d - ra - rb


def copper_objects(parts: list[Part], route: dict | None = None) -> list[tuple]:
    """(net, layer, points, radius) for every piece of copper the generator
    places.  The pour is excluded on purpose: it is CLIPPED around these
    objects at their own clearance, so its gap is guaranteed by construction
    rather than by measurement."""
    out = []
    anon = 0
    promoted = set((route or {}).get("promoted", ()))
    for part in parts:
        for p in part.pins:
            # A NO-CONNECT terminal is copper too, and it gets a private
            # pseudo-net so the scan can see it — exactly what the flip gauges
            # already get.  MEASURED 2026-08-01, and it was a hole in this
            # oracle rather than a curiosity: SW1-3 (the slide switch's unused
            # throw) is the board's only netless terminal, and while `None` it
            # was skipped by every different-net test here, by closing_tracks'
            # `nearby`/`via_ok`, and so by the path search built on them.  The
            # first closure that ever ran through the bottom strip laid RESET
            # past SW1-3's dead front ring, this scan reported the board clean,
            # and pcb-rnd — which resolves copper, not netnames — convicted it:
            # "shorted nets: net too close to other net".  Copper the operator
            # will hold in his hand does not stop being copper because the
            # schematic has nothing to say about it.
            net = p.net if p.net is not None else f"__nc_{p.pid}"
            if p.kind == "rect":
                out.append((net, "bottom", p.corners(), 0.0))
            elif p.kind == "circ":
                out.append((net, "bottom", [(p.x, p.y)], p.shape[1] / 2))
            else:
                r = p.shape[2] / 2
                out.append((net, "bottom", [(p.x, p.y)], r))
                # The FRONT ring is that pin's net only if the bench is
                # actually going to solder it — i.e. if the lead is PROMOTED.
                # Otherwise it is a dead island belonging to no net (the R3
                # finding), and it gets the same private pseudo-net treatment
                # as a no-connect terminal so every other net must clear it.
                #
                # It used to be checked at the pin's net unconditionally, on
                # the argument that this is "the net that copper becomes the
                # moment a human solders the lead".  The 2026-08-01 ruling
                # retires that argument for the LEDs: nobody will ever solder
                # those rings, so a same-net trace laid across one is not a
                # future connection, it is a trace grazing a floating island
                # that an unsoldered lead passes through.  This is also what
                # makes the scan agree with the DSN, where the same ring is
                # now foreign copper on __dead_<pid>.
                out.append((net if p.pid in promoted else f"__dead_{p.pid}",
                            "top", [(p.x, p.y)], r))
    for gx, gy in GAUGES.values():
        for layer in ("top", "bottom"):
            anon += 1
            out.append((f"__gauge{anon}", layer, [(gx, gy)], RING_GAUGE / 2))
    for layer, net, width, pts in fixed_tracks() + board_only_tracks():
        for a, b in zip(pts, pts[1:]):
            out.append((net, layer, [a, b], width / 2))
    # R4b's routed copper, carried back from the LIHATA frame into the board
    # frame this scan works in.
    r = route or {}
    for i, (lay, x1, y1, x2, y2, w) in enumerate(r.get("tracks", ())):
        net = r.get("track_nets", [None] * (i + 1))[i]
        out.append((net, lay, [(x1, BOARD_H - y1), (x2, BOARD_H - y2)], w / 2))
    for vx, vy, vnet in r.get("vias", ()):
        for lay in ("top", "bottom"):
            out.append((vnet, lay, [(vx, BOARD_H - vy)], RING_VIA / 2))
    return out


def clearance_scan(parts: list[Part], route: dict | None = None) -> tuple:
    """-> (worst gap, [(gap, netA, netB, layer), ...]) over different-net pairs."""
    objs = copper_objects(parts, route)
    worst, bad = 99.0, []
    for i, (na, la, pa, ra) in enumerate(objs):
        for nb, lb, pb, rb in objs[i + 1:]:
            if la != lb or na == nb or na is None or nb is None:
                continue
            g = shape_gap((pa, ra), (pb, rb))
            worst = min(worst, g)
            if g < CLEAR:
                bad.append((round(g, 3), na, nb, la))
    return worst, sorted(bad)[:8]


def copper_lines(parts: list[Part], route: dict | None = None) -> list[tuple]:
    """(who, x1, y1, x2, y2, thickness) for every copper LINE, board frame.

    Everything pcb-rnd will subtract from a pour as a line: the flip-gauge and
    dead-ring discs (zero-length lines), the pre-placed tracks, and R4b's
    routed copper.  Padstacks are deliberately absent — POUR_HOLE_MARGIN was
    measured to bind lines only.
    """
    promoted = set((route or {}).get("promoted", ()))
    out = [(f"gauge {ref}", gx, gy, gx, gy, RING_GAUGE)
           for ref, (gx, gy) in GAUGES.items()]
    out += [(f"dead ring {pid}", x, y, x, y, dia)
            for pid, x, y, dia, _j in dead_front_rings(parts)
            if pid not in promoted]
    for _layer, net, width, pts in fixed_tracks() + board_only_tracks():
        for a, b in zip(pts, pts[1:]):
            out.append((f"fixed {net}", a[0], a[1], b[0], b[1], width))
    for i, (_lay, x1, y1, x2, y2, w) in enumerate((route or {}).get("tracks", ())):
        # routed copper arrives in the LIHATA frame; bring it home
        out.append((f"routed seg {i}", x1, BOARD_H - y1, x2, BOARD_H - y2, w))
    return out


# The failure is a NEAR-TANGENCY of two contours, so the unsafe band has two
# edges, and both were measured (see the sweep at POUR_HOLE_MARGIN):
#     margin  +0.133 | +0.033  -0.067  -0.167  -0.367 | -0.887
#     pour     LIVES |  dies    dies    dies    dies  | LIVES
# Clearing the hole is safe, and being deep inside it is safe; grazing it is
# what kills the plane.  The far edge is BRACKETED, not pinned: -0.367 dies and
# -0.887 lives, so the rule takes the conservative -0.60 and says so.  Nothing
# on the shipped board sits in either grey zone.
POUR_HOLE_DEEP = -0.60


def pour_hole_scan(parts: list[Part], route: dict | None = None) -> tuple:
    """-> (worst margin, [(margin, who, mount) ...]) in the unsafe band.

    The oracle for the law documented at POUR_HOLE_MARGIN.  It is GEOMETRIC and
    independent of pcb-rnd, which is the point: pcb-rnd's own symptom is a
    silently discarded pour, i.e. the check it fails is one it does not run.
    Holes are treated as true circles; the emitted contour is a 16-gon
    INSCRIBED in that circle, so every real gap is at least this one.
    """
    worst, bad = 99.0, []
    for who, x1, y1, x2, y2, th in copper_lines(parts, route):
        reach = th / 2 + COPPER_CLEAR
        for ref, (mx, my) in MOUNTS.items():
            m = pt_seg(mx, my, x1, y1, x2, y2) - reach - MOUNT_KEEPOUT_R
            if m < POUR_HOLE_MARGIN:
                worst = min(worst, m)
            if POUR_HOLE_DEEP < m < POUR_HOLE_MARGIN:
                bad.append((round(m, 3), who, ref))
    return worst, sorted(bad)


def perturb_leg(parts: list[Part]) -> dict:
    """NEGATIVE CONTROL 3 for pour_hole_scan: walk S2 up until its L2 leg link's
    pour cutout grazes H4's hole, the failure that silently kills a plane.

    The y is DERIVED, not a literal, and that is the lesson of 2026-08-01: the
    control was written as "put S2 back at 39.0", which convicted on the 56x48
    board (+0.033) and then silently stopped convicting when the board grew
    (+5.36 — decisively safe).  A negative control that quietly passes because
    the geometry moved under it is worse than none, so this one now SOLVES for
    the y that lands the L2 leg link 0.25 mm inside the fatal band against
    whatever corner H4 currently occupies."""
    keep, out = BUTTONS["S2"], {}
    hx, hy = MOUNTS["H4"]
    dx = abs(keep[0] + BTN_DX - hx)
    want = MOUNT_KEEPOUT_R + TRACK / 2 + COPPER_CLEAR - 0.25
    try:
        BUTTONS["S2"] = (keep[0], hy - math.sqrt(want ** 2 - dx ** 2) - BTN_DY)
        out["worst"], out["bad"] = pour_hole_scan(parts)
    finally:
        BUTTONS["S2"] = keep
    return out


def perturb(parts: list[Part]) -> list[Part]:
    """NEGATIVE CONTROL: shove R14 toward Q2's GND land until the gap reads
    0.15 — a quarter of the law, which both oracles must convict.

    The SHOVE IS SOLVED FOR, not a literal.  It used to be a flat 0.30 mm,
    which worked only while R14-2/Q2-2 happened to sit at 0.45: when the right
    strip was spread on 2026-08-01 the same 0.30 left the pair legal and the
    control silently stopped convicting.  A negative control has to bite
    whatever board it is handed."""
    import copy
    out = copy.deepcopy(parts)
    by = {p.pid: p for part in out for p in part.pins}
    gap = shape_gap((by["R14-2"].corners(), 0.0), (by["Q2-2"].corners(), 0.0))
    shove = q(gap - 0.15)
    for part in out:
        if part.ref == "R14":
            for p in part.pins:
                p.x = q(p.x + shove)
    return out


def perturb_ring(parts: list[Part]) -> list[Part]:
    """NEGATIVE CONTROL 2: starve one LED ring to a 0.40 annulus.  min_ring
    reads geometry rather than connectivity, so this proves the pcb-rnd leg of
    the gate is alive even where its clearance leg is blind."""
    import copy
    out = copy.deepcopy(parts)
    for part in out:
        if part.ref == "LED1":
            for p in part.pins:
                p.shape = ("tht", p.shape[1], 1.8)
    return out


def gate(b: dict) -> int:
    """Every check that can be run on an UNROUTED board.  Exit 0 iff all pass."""
    import hashlib
    import re
    parts, nets = b["parts"], b["nets"]
    npass = nfail = 0

    def chk(label, got, want):
        nonlocal npass, nfail
        if got == want:
            npass += 1
            print(f"  [PASS] {label} ({got})")
        else:
            nfail += 1
            print(f"  [FAIL] {label} (got {got!r}, want {want!r})")

    print("### 0. PRECONDITION: the dead-island encoding is the honest one ###")
    probe = os.path.join(HERE, "probe_ring.py")
    for mode in ("both", "split"):
        f = os.path.join(HERE, f".probe_{mode}.lht")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(subprocess.run([sys.executable, probe, mode],
                                    capture_output=True, text=True).stdout)
        v = pcb_rnd("AddRats(AllRats)\n", f)
        got = "complete" if "layout is complete" in v else \
              (re.search(r"(\d+) rat line", v).group(0) if "rat line" in v else v)
        print(f"    unplated TERMINAL, rings {mode:6s} -> {got}")
        if mode == "both":
            chk("rings-on-both-faces FALSELY closes the net", got, "complete")
        else:
            chk("one face + dead island stays honestly open", got, "1 rat line")
        os.unlink(f)

    print("### 1. the netlist is ground truth ###")
    chk("nets carried from the schematic", len(nets), 26)
    chk("terminals bound (97 schematic + 4 twin legs)",
        sum(len(v) for v in nets.values()), 101)
    chk("schematic no-connects", b["stats"]["no_connect"],
        ["unconnected-(SW1-C-Pad3)"])
    out = pcb_rnd("AddRats(AllRats)\n", OUT_LHT)
    chk("unrouted lihata parses", out.count("io_lihata parse error"), 0)
    rats = re.search(r"(\d+) rat line", out)
    print(f"    unrouted board presents {rats.group(1) if rats else '?'} rat "
          f"lines — the routing debt R4b inherits")
    chk("an unrouted board must NOT claim completeness",
        "layout is complete" in out, False)

    print("### 2. the three milled-board laws ###")
    drc = run_drc(OUT_LHT)
    viol = [ln for ln in drc.splitlines() if re.match(r"^\d+: ", ln)]
    for v in viol[:12]:
        print(f"    {v}")
    chk("clearance 0.4 / ring 0.7 / drill 1.0 all clean", len(viol), 0)

    print("### 2b. the same law, measured independently of pcb-rnd ###")
    worst, bad = clearance_scan(parts)
    for x in bad:
        print(f"    {x}")
    print(f"    tightest different-net gap anywhere: {worst:.3f} mm")
    chk("independent scan: every different-net gap >= 0.40", len(bad), 0)

    print("### 2c. NEGATIVE CONTROLS — both oracles must be able to fail ###")
    hurt = perturb(parts)
    hworst, hbad = clearance_scan(hurt)
    print(f"    control 1: R14 shoved into Q2's GND land, gap now "
          f"{hworst:.3f} mm")
    chk("the independent scan convicts a 0.15 mm gap", len(hbad) > 0, True)
    bad_lht = os.path.join(HERE, ".negative.lht")
    with open(bad_lht, "w", encoding="utf-8") as fh:
        fh.write(emit_lihata(hurt, nets, b["labels"]))
    nviol = len([ln for ln in run_drc(bad_lht).splitlines()
                 if re.match(r"^\d+: ", ln)])
    print(f"    pcb-rnd on that same board: {nviol} violations — MEASURED "
          f"BLINDNESS, and the whole reason 2b exists: with nothing routed, "
          f"pcb-rnd has no netname to hang a clearance rule on")
    chk("pcb-rnd's clearance leg is blind here, so the scan is not optional",
        nviol, 0)
    # control 2 proves the pcb-rnd leg is nonetheless LIVE: min_ring reads
    # geometry, not connectivity, so it convicts a starved annulus.
    thin = perturb_ring(parts)
    with open(bad_lht, "w", encoding="utf-8") as fh:
        fh.write(emit_lihata(thin, nets, b["labels"]))
    rviol = len([ln for ln in run_drc(bad_lht).splitlines()
                 if re.match(r"^\d+: ", ln)])
    print(f"    control 2: one LED ring starved to 0.40 annulus -> "
          f"{rviol} pcb-rnd violations")
    chk("the pcb-rnd leg can still fail", rviol > 0, True)
    os.unlink(bad_lht)

    print("### 3. geometry that had to fight the laws, measured ###")
    pitch = LEAD_R_OUT - LEAD_R_IN
    chk("LED lead pitch clears ring+clearance",
        round(pitch - (RING_LED + CLEAR), 3) >= 0, True)
    print(f"    LED lead pitch {pitch:.2f} vs floor "
          f"{RING_LED + CLEAR:.2f} (SPEC's 2.54 is UNBUILDABLE here)")
    need = RING_SW1 / 2 + CLEAR + RAIL / 2
    got = SW1_Y - CORRIDOR_Y
    print(f"    VBAT corridor under SW1's VSW blade: {got:.2f} vs {need:.2f} "
          f"required — window {got - need:+.2f}")
    chk("power-entry corridor clears the blade it passes under",
        round(got - need, 3) >= 0, True)
    print(f"    VCC spine to corridor: "
          f"{CORRIDOR_Y - SPINE_Y - RAIL:.2f} edge-to-edge")
    chk("spine/corridor/edge stack legal",
        round(min(CORRIDOR_Y - SPINE_Y - RAIL, SPINE_Y - RAIL / 2), 3) >= CLEAR,
        True)
    by = {p.ref: p for p in parts}
    c2 = [p for p in by["C2"].pins if p.term == "1"][0]
    u8 = [p for p in by["U1"].pins if p.term == "8"][0]
    link = math.hypot(c2.x - u8.x, c2.y - u8.y)
    print(f"    C2's VCC land to U1 pin 8: {link:.2f} mm")
    chk("C2 decouples AT the pin (SPEC: <= 3.2 mm)", round(link, 2) <= 3.2, True)
    ann = (RING_GAUGE - HOLE_GAUGE) / 2
    print(f"    flip-gauge annulus {ann:.2f}, DECLARED at 0.30 (SPEC Q13) — a "
          f"deliberate sub-law exception, judged on the raster by WS5")
    chk("gauge annulus matches its declaration", round(ann, 2), 0.35)

    print("### 4. silk: every seat against the CORRECTED keep-out law ###")
    rows = silk_seats(parts, b["labels"], b.get("route"))
    for side in ("front", "back"):
        sub = [r for r in rows if r["side"] == side]
        wf = min(sub, key=lambda r: r["feature_gap"])
        wt = min(sub, key=lambda r: r["text_gap"])
        wa = max((r for r in sub if r["ratio"] is not None),
                 key=lambda r: r["ratio"])
        print(f"    {side}: {len(sub)} texts vs {sub[0]['n_features']} "
              f"features (apertures + bare copper + bores)")
        print(f"      closest ink-to-copper {wf['feature_gap']:.3f} "
              f"({wf['item']} -> {wf['feature']}), law {SILK_CLEAR}")
        print(f"      closest text-to-text  {wt['text_gap']:.3f} "
              f"({wt['item']} -> {wt['text']}), law {TEXT_GAP}")
        print(f"      worst attribution     {wa['ratio']:.2f} "
              f"({wa['item']}: {wa['own_gap']:.3f} own vs "
              f"{wa['rival_gap']:.3f} to {wa['rival']}), law {ATTRIB_RATIO}")
    flags = silk_flags(rows)
    for f in flags[:16]:
        print(f"      FLAG {f}")
    chk("crowding audit: zero flags on the corrected semantics", len(flags), 0)
    print("    NEGATIVE CONTROLS — an audit that cannot flag is not an audit:")
    import copy as _copy
    for name, hurt in silk_controls(parts, b["labels"]):
        got = silk_flags(silk_seats(parts, hurt, b.get("route")))
        hit = [g for g in got if name.split("|")[1] in g]
        print(f"      {name.split('|')[0]}: {len(got)} flags"
              + (f" — e.g. {hit[0][:96]}" if hit else " — NOTHING CAUGHT"))
        chk(f"the audit convicts {name.split('|')[0]}", bool(hit), True)
    del _copy
    chk("ref labels placed", b["placed"] + len(b["unplaced"]), N_LABELS)
    print(f"    {b['placed']}/{N_LABELS} refs placed; "
          f"unplaced {b['unplaced']}")

    print("### 5. determinism: we own every byte ###")
    sums = []
    for _ in range(2):
        build()
        sums.append(tuple(hashlib.md5(open(f, "rb").read()).hexdigest()
                          for f in (OUT_LHT, OUT_DSN)))
    chk("two full runs give one .lht and one .dsn", len(set(sums)), 1)

    print("\n### HANDOFF TO R4b ###")
    print("  FIXED, PROTECTED tracks (already in both .lht and .dsn):")
    for layer, net, width, pts in fixed_tracks():
        path = " -> ".join(f"({x},{y})" for x, y in pts)
        print(f"    {net:6s} {layer:6s} w{width}  {path}")
    print("  BOARD-ONLY tracks (in the .lht, HIDDEN from the router — the "
          "clearance scan is what keeps the router honest about them):")
    for layer, net, width, pts in board_only_tracks():
        path = " -> ".join(f"({x},{y})" for x, y in pts)
        print(f"    {net:6s} {layer:6s} w{width}  {path}")
    dual = sorted(p.pid for part in parts for p in part.pins if p.dual)
    print(f"  DUAL-SOLDER-CAPABLE pins ({len(dual)}), all UNDECLARED at R4a — "
          f"promoting one costs a bench joint on the reflow side:")
    print(f"    {' '.join(dual)}")
    print("  MANDATORY promotion: PAD2-1 is the only GND through-hole, so it "
          "is the sole conductor that can make the FRONT pour live.")
    print(f"  Via budget: 6 planned, NO hard ceiling (operator ruling "
          f"2026-08-01 — a wire via costs about what a jumper wire costs; "
          f"they are ledgered and reported, never gated), prototype "
          f"WIRE_VIA_STITCHED (Ø{HOLE_VIA}/Ø{RING_VIA}), declared set empty.")
    print(f"\n### {npass}/{npass + nfail} checks passed, {nfail} failed ###")
    return 1 if nfail else 0


if __name__ == "__main__":
    built = build()
    print(f"orbit.lht        {os.path.getsize(OUT_LHT):7d} bytes")
    print(f"orbit-route.dsn  {os.path.getsize(OUT_DSN):7d} bytes")
    print(f"{len(built['parts'])} parts, "
          f"{sum(len(p.pins) for p in built['parts'])} pads, "
          f"{len(built['nets'])} nets")
    sys.exit(gate(built) if "--gate" in sys.argv else 0)
