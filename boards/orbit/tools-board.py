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
    the declared set is EMPTY: the wire-via prototype and the dual-solder LED
    lead prototype both exist, but no pin is promoted.  R4b promotes exactly
    the leads its router actually uses, and each promotion is a bench joint the
    assembly card must repeat.

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
# other files: the flip mirror line is BOARD_W/2 (now x = 30.0, was 28.0) and
# the registration pins sit ON it at (BOARD_W/2, -8.0) and (BOARD_W/2,
# BOARD_H+8.0) = (30, -8) and (30, 62); the blank must cover BOARD_W x
# (BOARD_H+16) = 60 x 70, which the operator's 150 x 100 stock clears twice
# over.  Pins and job frames are declared in the JOB TOML, not here.
# W grew a second time (60 -> 64) for the RIGHT STRIP, measured the same way:
# both button nets (L2, L3) can only enter their button from its outboard leg
# column, and on the 60-wide board the only lane into S2's was 1.67 mm against a
# 1.46 mm need — with D1 standing in it.  Widening the strip and clearing the
# lane is the ruling's answer; being clever with a 0.2 mm margin is not.
BOARD_W, BOARD_H = 64.0, 54.0
CORNER_R = 2.0
# Ring centre moved with the growth: +2 in x so the enlarged ring keeps a 4.3 mm
# pour margin on the left edge, +4 in y so its lowest lead still clears the
# bottom strip (SW1's blade ring tops out at 6.62).
RING_CX, RING_CY = 24.0, 30.0
RING_R = 17.0                    # LED body pitch circle (bodies 8.9 mm apart)
LEAD_R_IN, LEAD_R_OUT = 15.55, 18.45   # cathode INWARD, anode outward
# LED lead pitch is 2.90, NOT the SPEC's 2.54.  Ø2.44 rings on two DIFFERENT
# nets need hole pitch >= 2.44 + 0.40 = 2.84 before any routing happens; 2.54
# caps the ring at 0.57 and is UNBUILDABLE under ring>=0.7.  Found independently
# in both toolchains ("24 clearance errors, one per LED" on the first KiCad
# build).  2.90 leaves 0.46 of real gap.
LEAD_PITCH = LEAD_R_OUT - LEAD_R_IN

HOLE_LED, HOLE_BTN, HOLE_BZ = 1.0, 1.0, 1.0
HOLE_SW1, HOLE_PAD, HOLE_GAUGE = 1.8, 1.5, 1.0
HOLE_MOUNT, HOLE_VIA = 3.4, 1.0
RING_LED = HOLE_LED + 2 * RING + 2 * RING_MARGIN          # 2.44
RING_SW1 = HOLE_SW1 + 2 * RING + 2 * RING_MARGIN          # 3.24
RING_PAD = 3.6                                            # SPEC: Ø3.6 wire pad
RING_VIA = HOLE_VIA + 2 * RING + 2 * RING_MARGIN          # 2.44
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
RES_OUTER, RES_OUTER_1206 = 13.7, 13.5

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
MOUNT_INSET = 4.2
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
GAUGE_INSET = 8.0
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
BUTTONS = {"S1": (57.5, 19.0), "S2": (57.5, 41.0)}
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
    "SOIC8W": {str(n + 1): (
        (-3.5875 if n < 4 else 3.5875),
        (1.905 - 1.27 * n) if n < 4 else (-1.905 + 1.27 * (n - 4)),
        1.625, 0.65) for n in range(8)},
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
    both faces (LED leads, wire pads).  Capable is not declared — at R4a every
    pin is undeclared and `promoted` is empty; R4b promotes what it uses.
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
        add(tht(f"LED{led}", [("1", kx, ky), ("2", ax, ay)], dual=True))

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
    edge = rounded_rect(EDGE_CLEAR)
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

SILK_H = 1.5             # SPEC: text 1.5 mm
SILK_W = 0.25            # SPEC: stroke 0.25, Makera's floor
ADVANCE = 0.85           # x text height
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


def front_legend() -> list[tuple]:
    """The functional front legend (SPEC "Silk, FRONT").

    The 12 cathode ticks are the load-bearing part: they sit at r 9.8, just
    inside the LED bodies' 5 mm footprint and radially inward of the cathode
    hole, which is the side the cathode is on.  Get these wrong and the ring
    does not light — no other check on the board catches it.
    """
    out = []
    for pos in range(1, 13):
        ang = pos_angle(pos)
        # 3.2 inside the body pitch circle: 0.7 clear of the 5 mm body's inner
        # edge and radially inward of the cathode hole, which is the side the
        # cathode is on.  DERIVED from RING_R so it followed the grown ring.
        cx, cy = polar(RING_CX, RING_CY, ang, RING_R - 3.2)
        tx, ty = polar(0.0, 0.0, ang + 90.0, 0.8)
        out.append((q(cx - tx), q(cy - ty), q(cx + tx), q(cy + ty)))
    # marker arrow at position 1, outboard of the ring
    apex = polar(RING_CX, RING_CY, 90.0, LEAD_R_OUT + 2.15)
    for dx in (-1.3, 1.3):
        out.append((q(apex[0] + dx), q(apex[1] + 1.3), q(apex[0]), q(apex[1])))
    # The 12/3/6/9 numerals moved INSIDE the ring when it grew (2026-08-01).
    # Outboard they would have wanted r 22.6, which on the grown ring runs the
    # "9" off the left edge and drives the "6" into SW1's blade ring — and the
    # answer to that is not a bigger board still, because a clock face reads
    # correctly with its numbers inside the hands.  r 11.5 keeps them 2.0 mm
    # clear of the cathode rings and over nothing but back-side copper.
    for label, ang in (("12", 90.0), ("3", 0.0), ("6", -90.0), ("9", 180.0)):
        out += text_strokes(label, *polar(RING_CX, RING_CY, ang, 11.5))
    # Each button's legend sits 4.8 mm outboard of its body centre, so START
    # followed S2 down when POUR_HOLE_MARGIN moved it (see BUTTONS).
    out += text_strokes("CATCH", *polar(BUTTONS["S1"][0], BUTTONS["S1"][1],
                                        90.0, 4.8))
    out += text_strokes("START", *polar(BUTTONS["S2"][0], BUTTONS["S2"][1],
                                        -90.0, 4.8))
    out += text_strokes("ON", SW1_X, 8.1)
    out += text_strokes("+", 16.0, 7.0)      # follows the layout, not the note:
    out += text_strokes("-", 10.0, 7.0)      # PAD1 (+) is the right-hand pad
    out += text_strokes("ORBIT V1", RING_CX, RING_CY + 2.2)
    out += text_strokes(DATE_STAMP, RING_CX, RING_CY - 1.6)
    return out


def back_legend(parts: list[Part]) -> list[tuple]:
    """SPEC "Silk, BACK": U1 pin-1 dot, transistor orientation marks, the six
    ISP labels + pin-1 square tick, "SIDE B".  All mirrored so the legend reads
    with the BACK up, which is the only way it is ever seen."""
    by = {p.ref: p for p in parts}
    out = []
    # U1 pin-1 dot: a small square OUTBOARD of pin 1's land.  The offset is
    # rotated with the part, never hard-coded: at rotation 0 pin 1 is U1's
    # upper-left corner and outboard is (-1.1, +1.0), but U1 now sits at 180
    # and the same literal offset put the dot INSIDE the package, 0.087 mm
    # into pin 8's land — a pin-1 marker pointing at the wrong pin is worse
    # than none, and the silk gate caught it.
    p1 = by["U1"].pins[0]
    dx, dy = rot(-1.1, 1.0, by["U1"].rot)
    out += box_strokes(p1.x + dx, p1.y + dy, 0.5)
    # Q1 / Q2 / D1: a bar under pin 1 says which corner pin 1 is
    for ref in ("Q1", "Q2", "D1"):
        pin1 = [p for p in by[ref].pins if p.term == "1"][0]
        out.append((q(pin1.x - 0.4), q(pin1.y - 1.0),
                    q(pin1.x + 0.4), q(pin1.y - 1.0)))
    # ISP: six labels, left column labelled to the left, right to the right
    for ref, txt in ISP_LABEL.items():
        pad = by[ref].pins[0]
        w, _ = text_size(txt, 1.0)
        left = pad.x < ISP_X0 + ISP_PITCH / 2
        # 0.3 is the law between silk INK and pad copper, so the half-stroke
        # counts: leaving it out put every ISP label 0.175 from its own pad.
        cx = pad.x + (-1 if left else 1) * (ISP_PAD / 2 + 0.3 + SILK_W / 2
                                            + w / 2)
        out += text_strokes(txt, cx, pad.y, 1.0, mirror=True)
    # pin-1 (TP1) square tick, placed RELATIVE to TP1 so it follows the grid
    tp1 = by["TP1"].pins[0]
    out += box_strokes(tp1.x - 1.4, tp1.y + 1.62, 0.8)
    # centred on the flip mirror line, 1.25 below the top edge
    out += text_strokes("SIDE B", BOARD_W / 2, BOARD_H - 2.0, mirror=True)
    return out


def label_parts(parts: list[Part], side: str, legend: list[tuple]):
    """Feed silklabel the geometry for one side and take back its placements.

    silklabel is pure geometry in a y-DOWN frame, which is the lihata frame, so
    everything handed over goes through lht_xy and comes back the same way.
    Apertures are that side's SOLDERABLE pads: on the front the THT rings (the
    only front copper a human ever solders), on the back every SMD land, ISP
    pad and THT ring.  A label the silk-clip would eat is never emitted —
    silklabel reports it unplaced instead, which is a bench note rather than
    misinformation.
    """
    want = [p for p in parts if (p.side == side)]
    apertures = []
    for part in parts:
        for pin in part.pins:
            hw, hh = pin.extent()
            lx, ly = lht_xy(pin.x, pin.y)
            if pin.kind == "tht" or (side == "back" and pin.kind != "tht"):
                apertures.append(SL.Rect(lx - hw, ly - hh, lx + hw, ly + hh))
    hard = []
    for _ref, (mx, my) in MOUNTS.items():
        lx, ly = lht_xy(mx, my)
        r = MOUNT_KEEPOUT_R
        hard.append(SL.Rect(lx - r, ly - r, lx + r, ly + r))
    silk = [stroke_bbox([s]) for s in legend]
    sl_parts = []
    for part in want:
        w, h = text_size(part.ref)
        sl_parts.append(SL.Part(part.ref, part.body(), (w, h), (h, w)))
    if side == "front":
        for ref, (gx, gy) in GAUGES.items():
            lx, ly = lht_xy(gx, gy)
            r = RING_GAUGE / 2
            w, h = text_size(ref)
            sl_parts.append(SL.Part(ref, SL.Rect(lx - r, ly - r, lx + r, ly + r),
                                    (w, h), (h, w)))
    board = SL.Rect(0.0, 0.0, BOARD_W, BOARD_H)
    return SL.place_labels(sl_parts, board, apertures, silk, hard)


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
        L.ps_proto(PROTO_VIA, "WIRE_VIA_STITCHED", HOLE_VIA, True, RING_VIA) +
        # Bare bores: a hole with no copper anywhere.  H1-H4 carry no annulus
        # at all (SPEC: Ø3.4 bore, copper keep-out), and the G1-G4 gauge rings
        # are DEAD ISLANDS, not annuli of a terminal, so they are emitted as
        # separate copper below.  MEASURED: pcb-rnd raises no ring or drill
        # violation on a copper-less padstack, so this encoding is silent in
        # DRC rather than a false positive.
        L.ps_proto(PROTO_MOUNT, "MOUNT_BORE_M3", HOLE_MOUNT, False, 0.0,
                   sides=()) +
        L.ps_proto(PROTO_GAUGE, "GAUGE_BORE", HOLE_GAUGE, False, 0.0, sides=()))

    objs, top, bot = [], [], []
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
            protos_sub = L.ps_proto(0, "THT_BACK_ONLY", hole, False, ring,
                                    sides=("bottom",))
            if part.pins[0].dual:
                # Present, referenced by nothing: R4b promotes a lead by
                # switching its proto to 1.  The prototypes differ ONLY in
                # hplated and in owning the front face.
                protos_sub += L.ps_proto(1, "THT_DUAL_SOLDER_DECLARED",
                                         hole, True, ring)
        elif part.pins[0].kind == "circ":
            protos_sub = L.ps_proto(0, "ISP_BARE_PAD", 0.0, False,
                                    part.pins[0].shape[1], sides=("bottom",))
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
            protos_sub = L.ps_proto_rect(0, "SMD_LAND", 0.0, False, w, h,
                                         sides=("bottom",),
                                         rotation=-part.pins[0].prot)
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
    for i, (pid, x, y, dia, joins) in enumerate(dead_front_rings(parts)):
        if pid in promoted:
            continue
        lx, ly = lht_xy(x, y)
        top.append(L.line(20000 + i, lx, ly, lx, ly, dia,
                          0.0 if joins else COPPER_CLEAR,
                          clearpoly=not joins))

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
    pour = L.polygon(30000, rounded_rect(EDGE_CLEAR), COPPER_CLEAR)
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

    return L.board(BOARD_W, BOARD_H, protos=protos, objects="\n".join(objs),
                   top="\n".join(top), bottom="\n".join(bot), outline=outline,
                   top_silk="\n".join(silk["front"]),
                   bottom_silk="\n".join(silk["back"]),
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
    for part in parts:
        for p in part.pins:
            if p.kind == "tht" and not is_thru(p):
                x, y = dsn_xy(p.x, p.y)
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
    out.append("    (class signal "
               + " ".join(n for n in sorted(nets)
                          if n not in DSN_OMIT_NETS))
    out.append(f"      (circuit (use_via {VIA_NAME}))")
    out.append(f"      (rule (width {dsn_len(ROUTE_TRACK)}) "
               f"(clearance {dsn_len(ROUTE_CLEAR)}))")
    out += ["    )", "  )"]

    out.append("  (wiring")
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
    fl, bl = front_legend(), back_legend(parts)
    pf, uf = label_parts(parts, "front", fl)
    pb, ub = label_parts(parts, "back", bl)
    labels = {"front": pf, "back": pb,
              "front_legend": fl, "back_legend": bl}
    with open(out_lht, "w", encoding="utf-8") as fh:
        fh.write(emit_lihata(parts, nets, labels, route))
    with open(OUT_DSN, "w", encoding="utf-8") as fh:
        fh.write(emit_dsn(parts, nets))
    with open(TDX_FILE, "w", encoding="utf-8") as fh:
        fh.write(kicadnet.to_tedax(nets))
    return {"parts": parts, "nets": nets, "stats": stats, "labels": labels,
            "unplaced": uf + ub, "placed": len(pf) + len(pb)}


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
                # the dead FRONT ring is checked at its pin's net: it is the
                # net that copper becomes the moment a human solders the lead,
                # which is exactly what R4b may promote it to do
                out.append((net, "top", [(p.x, p.y)], r))
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

    print("### 4. silk stays off the pads it would otherwise ruin ###")
    for side in ("front", "back"):
        pads = [p for part in parts for p in part.pins
                if (p.kind == "tht" if side == "front" else True)]
        strokes = list(b["labels"][f"{side}_legend"])
        for pl in b["labels"][side]:
            strokes += text_strokes(pl.ref, pl.x, BOARD_H - pl.y, SILK_H,
                                    mirror=(side == "back"), rotation=pl.rot)
        worst = min(seg_pad_gap(s, p) for s in strokes for p in pads)
        print(f"    {side}: {len(strokes)} strokes, closest pad {worst:.3f} mm")
        chk(f"{side} silk >= 0.30 from that side's solderable pads",
            round(worst, 3) >= 0.30, True)
    chk("ref labels placed", b["placed"] + len(b["unplaced"]), 52)
    print(f"    {b['placed']}/52 refs placed; unplaced {b['unplaced']}")

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
