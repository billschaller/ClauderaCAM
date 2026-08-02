"""Board B "orbit" layout builder — emits orbit.kicad_pcb from the netlist.

SPEC.md is the contract; every constant below cites it. Deterministic and
rerunnable: hand-edits to the board file are forbidden, this script IS the
layout's source of truth (Board A precedent, boards/coupon/tools-layout.py —
read it for the pcbnew idioms and the two LSET traps in _pth()).

SKELETON NOTE (2026-07-31): scaffold laid down by the orchestrator —
constants, coordinate law, main() flow. Fill-in happens section by section;
every function keeps its docstring's SPEC citation when implemented.

Runs under SYSTEM python3 (pcbnew). Gate after emission:
  kicad-cli pcb drc --severity-error --exit-code-violations orbit.kicad_pcb
"""
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pcbnew
from pcbnew import EDA_ANGLE, VECTOR2I

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "orbit.kicad_pcb")
SCH = os.path.join(HERE, "orbit.kicad_sch")
LIBROOT = "/usr/share/kicad/footprints"

# ---------------------------------------------------------- coordinate law
# SPEC "Layout notes": board origin = LOWER-LEFT corner, y UP (ring pos 1 at
# (22.0, 39.0) is near the TOP edge). KiCad page y grows DOWN. Board A pinned
# its board top-left at page (100,100); orbit keeps that page anchor, so:
#   page_x = OX + x_board          page_y = OY + (H - y_board)
# The flip axis (SPEC: vertical centreline x=28.0 board) is page x=128.0.
OX, OY = 100.0, 100.0
W, H = 56.0, 48.0               # SPEC: 56.0 x 48.0, corner radius 2.0
CORNER_R = 2.0
FLIP_X = OX + 28.0              # [twosided] mirror line, page coords


def MM(x, y):
    """Board mm (lower-left, y up) -> page nm vector. THE only converter —
    never inline the y flip anywhere else (Article IV's spirit)."""
    return VECTOR2I(int(round((OX + x) * 1e6)), int(round((OY + (H - y)) * 1e6)))


def NM(v):
    return int(round(v * 1e6))


# ------------------------------------------------------------ SPEC numbers
# Ring (SPEC "Layout notes"): centre (22,26), pitch circle r=13, pos 1 at 12
# o'clock, clockwise; leads radial on 2.54 pitch at r=11.73 / 14.27.
RING_C = (22.0, 26.0)
RING_R = 13.0
# DEVIATION (bench-arithmetic, 2026-07-31): SPEC says leads on the 2.54
# pitch at r = 11.73 / 14.27. Those three SPEC laws cannot all hold at once
# on a 2.54-pitch pair -- hole 1.0 + two 0.7 annuli + 0.4 clearance needs
# pitch >= 2.8, and 2.54 leaves 0.14 (DRC measured exactly that on the first
# build: 24 clearance errors, one per LED). The annulus cannot shrink (the
# side-2 annular scrub lap needs 0.15+0.30+0.20) and the hole cannot shrink
# (the 0.8 corn cannot bore its own diameter), so the PITCH moves: 2.90,
# symmetric about the same r = 13.0 pitch circle, gap 0.50. Cost: each 5 mm
# LED's leads splay 0.18 mm per side going in -- routine forming for a THT
# part, and the holes are still on one radius line. Reported to the spec
# owner as a SPEC arithmetic bug, not silently absorbed.
LEAD_PITCH = 2.90
LEAD_R_IN = RING_R - LEAD_PITCH / 2      # 11.55
LEAD_R_OUT = RING_R + LEAD_PITCH / 2     # 14.45
# Resistor arc on the BACK, tangential at r ~= 10 (SPEC). 9.0 is the value
# the two real bounds leave: measured copper extents say a 1206 (half-length
# 2.20) beside an 0805 (1.60) needs chord 2*r*sin15 >= 3.80 + 0.40 -> r >=
# 8.12; the 1206's 0.875 half-height must stay 0.4 clear of the cathode pad
# ring (r 11.73, pad 2.4) -> r <= 9.25. r = 9.0 sits mid-band (neighbour gap
# 0.86, radial gap 0.66). SPEC's "r ~= 10" is the target, not a dimension.
RES_R = 9.0
# C2 rides this radius, in the gap between U1 and the spoke ring.
INNER_C2 = 3.8

# ---- CATHODE ORIENTATION, decided once (SPEC "Silk, FRONT" is downstream)
# CATHODE INWARD: every LED's pin 1 (K) sits on the INNER lead circle
# (r = 11.73), pin 2 (A) on the outer (r = 14.27). Chosen because (a) all 12
# ticks then read identically -- one rule for the operator, "flat side /
# short lead points at the middle" -- and a legend that needs 12 separate
# readings is the failure mode SPEC calls load-bearing; (b) the cathode is
# the lead that must reach its own series resistor, and the resistor arc is
# INSIDE the ring at r = 9.0, so inward cathodes make all 12 K-to-R links
# 1.73 mm radial stubs. The silk ticks, MATRIX.md and the firmware README
# all quote this constant; flipping it is a board revision, not an edit.
CATHODE_INWARD = True

# Strips (SPEC): PAD+ (10,4)  PAD- (16,4)  SW1 (26,4)  ISP ~(33-38, 3-6);
# right strip S1 (48,13) BZ1 (48,26) S2 (48,39).
# SW1 26.0 -> 27.0 (2026-07-31): SPEC's 26.0 puts the 4.86-pitch switch's
# west blade at x 21.14, leaving PAD- (Ø3.6 at 16.0) a 1.74 mm corridor to
# hand /VBAT through -- and a 0.8 rail needs 0.8 + 2*0.4 = 1.6, so the
# router refused it (run 1: /VBAT PAD1-1 -> SW1-2 unrouted). At 27.0 the
# corridor is 2.74 and the blade still clears the ISP grid by 0.64.
POS = {
    # PAD+/PAD- SWAPPED vs SPEC's (10,4)/(16,4): with GND on the outside the
    # /VBAT rail has a clear 2.74 lane from PAD+ to the switch's centre
    # blade, and GND lands straight on the pour it belongs to. Unswapped,
    # /VBAT has to dogleg around a Ø3.6 GND pad and the router refused it
    # twice. Two identical pads 6 mm apart; the silk "+"/"-" follows them.
    # SW1 y 4.0 -> 4.6 (2026-07-31): /VBAT PAD1.1 -> SW1.2 went unrouted in
    # FOUR consecutive router runs. The only legal corridors past the west
    # blade were the under-blade band (exactly 1.6 -- zero slack) and a 1.7
    # squeeze above; +0.6 widens the under-blade copper band to 2.2 and the
    # top gap to 2.3. Named connection: /VBAT PAD1 -> SW1.2.
    "PAD1": (16.0, 4.0), "PAD2": (10.0, 4.0), "SW1": (27.0, 4.6),
    # S1/S2 48.0 -> 47.0: at 48.0 the switch's east leg pair sits 3.48 from
    # H4's centre, and the 0.6 link between its two same-numbered legs then
    # clips the 3.2 M3 keep-out (DRC items_not_allowed). 1 mm west clears it.
    "S1": (47.0, 13.0), "BZ1": (48.0, 26.0), "S2": (47.0, 39.0),
    "U1": RING_C,
}
# H1-H4 M3 (Ø3.4 NPTH, copper keep-out both sides), G1-G4 flip gauges just
# inside the corners in pour area (Ø1.0 hole / Ø1.7 pad both sides, no net).
M3 = {"H1": (3.5, 3.5), "H2": (52.5, 3.5), "H3": (3.5, 44.5), "H4": (52.5, 44.5)}

# Copper laws (SPEC process table): track >=0.6 signal / 0.8 rails,
# clearance >=0.4, copper-edge >=0.4, THT annular >=0.7 BOTH sides (gauge
# exception 0.3 confined to the DRU named areas), min drill 1.0 (0.8 corn),
# drill classes 1.0/1.1/1.2/1.5/1.8/3.4, pad >= drill + 1.4.
TRACK_SIG, TRACK_RAIL = 0.6, 0.8
# THT pad for the 1.0 drill class. 2.40 is exactly drill + 2*0.7 and lands
# ON the annular bar: KiCad polygonises a rotated circular pad, and the
# eight ring LEDs that sit at non-90-degree angles then read 0.699 and fail
# the DRU (16 annular_width errors, measured 2026-07-31). 2.44 carries 0.72
# of real annulus and still leaves 0.46 between the two leads at the 2.90
# pitch -- above the 0.4 clearance law.
THT_PAD = 2.44
CLEAR = 0.4
# Wire vias (SPEC "Via geometry"): Ø1.0 hole, Ø2.4 pads both sides, no
# paste, budget 6 / hard ceiling 10, keep-outs 1.5 SMD / 2.0 THT / 3.0 edge.
VIA_HOLE, VIA_PAD = 1.0, 2.4
VIA_BUDGET, VIA_CEILING = 6, 10

# ---- UNPLATED HOLES: which THT ring may carry an electrical claim -------
# THIS BOARD HAS NO PLATED BARRELS. It is milled on a Carvera: a hole is a
# hole. A THT pad's FRONT ring and BACK ring are two separate conductors
# that meet only where a human iron melts solder onto both faces of the
# same lead. KiCad models every PTH pad as a plated barrel and FreeRouting
# does the same, so BOTH will happily claim a connection that does not
# physically exist -- the operator caught exactly that on this board
# (incident 2026-07-31, Article II). Everything below exists to make the
# tools tell the unplated truth.
#
# BACK_ONLY -- the body sits flush on the FRONT face and an iron can NEVER
# reach the front ring. The ring stays on the real board (annular law: the
# drill needs its collar, and the scrub lap needs the copper) but it
# carries NO electrical claim. The DSN surgery hands the router a back-only
# padstack plus an F.Cu keepout over the ring, so the router physically
# cannot attach a front track there or use the lead as a layer bridge; the
# unplated gate deletes the front ring before asking KiCad what is
# connected.
#   SW1   slide switch -- blades run UNDER the body
#   BZ1   O12 buzzer -- the can sits on the board
#   S1,S2 6x6 tactile -- pads half-under the body. CONSERVATIVE by choice:
#         a lead you can only half-reach is a lead you cannot promise.
#
# DUAL_OK -- the front ring IS reachable, so the lead may be soldered on
# BOTH faces, and a lead soldered both faces IS a legal layer bridge.
#   LED1-12  the twelve ring LEDs are seated ~1.5 mm PROUD of the board for
#            exactly this reason: the standoff is what lets an iron tip
#            land on the front ring. That is an ASSEMBLY REQUIREMENT, not a
#            preference -- it goes on the assembly card, and MATRIX.md's
#            dual-solder list names every lead that depends on it.
#   PAD1/PAD2  bare wire pads with nothing on top of them at all.
#
# orbit:WireVia footprints are always bridges: a wire through the hole,
# soldered both faces, is the only via this process has (SPEC "Via
# geometry"). TP1-6 are B.Cu SMD (no hole) and G1-G4 carry no net, so
# neither can lie about a barrel.
UNPLATED_BACK_ONLY = ("SW1", "BZ1", "S1", "S2")
UNPLATED_DUAL_OK = tuple(f"LED{k}" for k in range(1, 13)) + ("PAD1", "PAD2")

# Charlieplex wiring per LED REF -- FROZEN by the schematic (SPEC BOM table;
# LEDk's anode line and cathode line are nets in orbit.kicad_sch and may not
# change). ref k: (anode line, cathode line).
REF_PAIR = {
    1: (0, 1), 2: (1, 0), 3: (0, 2), 4: (2, 0), 5: (0, 3), 6: (3, 0),
    7: (1, 2), 8: (2, 1), 9: (1, 3), 10: (3, 1), 11: (2, 3), 12: (3, 2),
}
# ---- PLACEMENT permutation (Decision Q5, taken 2026-07-31) --------------
# Position -> LED ref (Rk follows LEDk). All 12 LEDs are identical red
# parts, so position->ref reassignment IS the ring->pair permutation, and
# the schematic never moves. Derivation, from measured geometry:
#   * U1's rotation (pin 3 /SND east) fixes the line pins: L0=pin5 (west
#     row bottom), L1=pin6 (west), L2=pin7 (west top), L3=pin2 (EAST row).
#   * Off-ring commitments: L3->S1 (47,13) SE; L2->S2 (47,39) NE and TP3
#     (37,5.5) S; L0->TP4 (39.5,5.5) S; L1->TP1 (37,8.1) S.
#   * The DEFAULT matrix gives L0 (west pin) the whole EAST arc (sectors
#     1-6) and L3 (east pin) the WEST arc -- backwards on both counts; three
#     router runs left 5-8 opens against it (MATRIX.md history).
#   * A line's 3 cathode spokes are its hard B.Cu ring-road commitments
#     (SMD resistors); anode leads are THT and ride the empty F.Cu ring
#     interior. So cluster each line's CATHODE positions at its pin side.
#   * Sectors are the 6 unordered line pairs (K4's edges). A cyclic sector
#     order where every line's 3 sectors are consecutive does not exist
#     (4 blocks of 3 consecutive slots covering each slot twice forces two
#     equal sectors); best is 3 contiguous lines + 1 alternating. L2 takes
#     the alternating role -- it must span the board (S2 NE + TP3 S) anyway.
#   * Sector map: (1,2)={L1,L2} N, (3,4)={L1,L3} E, (5,6)={L2,L3} SE,
#     (7,8)={L0,L3} S, (9,10)={L0,L2} W, (11,12)={L0,L1} NW.
#     Cathodes: L3 at 4,5,7 (E->S, its pin AND the S1 corridor); L0 at
#     8,9,12 (west, its pin); L1 at 2,3,11 (north road); L2 at 1,6,10.
#   * WHERE THE FOUR 1206s GO -- rewritten 2026-07-31 when the spokes went
#     radial (RES_OUTER_1206). The old rule was "never adjacent", because
#     two TANGENTIAL 1206s side by side left chord 4.57 < 4.80. Radial they
#     are 1.75 wide, so adjacency is now decided by the CHANNEL law
#     (assert_channels): two adjacent 1206s pinch to 1.07 mm, under the 1.4
#     a signal needs -- so non-adjacent still holds, for a new reason.
#     The new, binding rule is WHICH RAY a 1206 may own. A radial 1206
#     reaches in to r 5.45, and the four directions where U1's own copper
#     reaches furthest are its pad-row corners at +-27.96 and +-152.04 deg
#     (corner at (4.400, 2.230), 4.933 from centre). Put a 1206 on the
#     30/150/-30/-150 rays and the gap between U1's corner and that spoke's
#     inner pad is 0.52 mm: legal clearance, but a 0.6 track needs 1.4, so
#     the corner pins CANNOT leave. Measured, three consecutive draws with
#     1206s at 90/30/150/-150: U1 pins 1, 2, 7 and 8 -- /RESET, /L3, /L2
#     and VCC, exactly the four corner pins -- went open in every one
#     (4, 5 and 8 opens; drawing again made it worse, not better).
#     The four rays a 1206 may own are therefore 60/120/-60/-120: the
#     furthest from both the corners AND the pad-row axis (0/180, where the
#     four east/west pins escape into a 2.5 mm pocket). U1's corner sits
#     2.05 mm clear of a 60-degree spoke, laterally outside its shadow.
#     Those are positions 2, 6, 8, 12 -- one per sector, always the SECOND
#     (even) position, which forces each 1206-bearing pair's orientation.
#   * That forcing leaves 4! = 24 legal sector assignments. The one below
#     minimises the total angle from each cathode spoke to its own line's
#     U1 pin (cost 580 of a 528..700 range; the tie at 580 is broken toward
#     the flatter assignment, worst single spoke 130 deg instead of 148).
#     Sectors 2 and 5 -- positions 3,4 and 9,10 -- take the two pairs with
#     no 1206 in them ({5,6} and {11,12}), so the four corner rays
#     (+-30, +-150) all get 0603 spokes, the smallest package on the board:
#     the corner road opens from 0.52 to 1.97 mm.
POS_REF = {
    1: 8, 2: 7, 3: 12, 4: 11, 5: 9, 6: 10,
    7: 2, 8: 1, 9: 6, 10: 5, 11: 3, 12: 4,
}
# The 1206 ray law, asserted rather than trusted to the table above.
assert {n for n in POS_REF if POS_REF[n] % 3 == 1} == {2, 6, 8, 12}, \
    "a 1206 spoke moved off the 60/120/-60/-120 rays (see the note above)"
# Effective ring matrix BY POSITION (what MATRIX.md publishes and firmware
# transcribes): position n carries LED{POS_REF[n]} wired per REF_PAIR.
MATRIX = {n: REF_PAIR[k] for n, k in POS_REF.items()}
assert sorted(MATRIX.values()) == sorted(REF_PAIR.values()), \
    "placement permutation lost a line pair"
assert all(MATRIX[2*s - 1] == MATRIX[2*s][::-1] for s in range(1, 7)), \
    "sector structure broken: adjacent positions must be antiparallel"


# ------------------------------------------------------------- net helpers
def parse_netlist():
    """kicad-cli sch export netlist -> ({ref: (value, fpid)}, {net: nodes}).
    Ground truth for connectivity — the schematic's labels, not this file's
    opinion. Board A idiom verbatim (boards/coupon/tools-layout.py)."""
    out = os.path.join(HERE, ".layout-net.tmp")
    subprocess.run(["kicad-cli", "sch", "export", "netlist",
                    "--format", "kicadsexpr", "-o", out, SCH],
                   check=True, capture_output=True)
    text = open(out).read()
    os.unlink(out)
    comps = {}
    body = text.split("(components")[1].split("(libparts")[0]
    for m in re.finditer(r'\(comp\s+\(ref "([^"]+)"\)(.*?)(?=\(comp\s|\Z)',
                         body, re.S):
        fp = re.search(r'\(footprint "([^"]*)"\)', m.group(2))
        val = re.search(r'\(value "([^"]*)"\)', m.group(2))
        comps[m.group(1)] = (val.group(1) if val else "",
                             fp.group(1) if fp else "")
    nets = {}
    for block in re.split(r'\(net\b', text.split("(nets")[1])[1:]:
        name = re.search(r'\(name "([^"]*)"\)', block).group(1)
        nodes = re.findall(r'\(node\s+\(ref "([^"]+)"\)\s+\(pin "([^"]+)"\)',
                           block, re.S)
        if nodes:
            nets[name] = nodes
    return comps, nets


def deg(a):
    return EDA_ANGLE(a, pcbnew.DEGREES_T)


def pad_by_num(fp, num):
    for p in fp.Pads():
        if p.GetNumber() == num:
            return p
    raise KeyError(f"{fp.GetReference()} pad {num}")


# ----------------------------------------------------- footprint builders
def _pth(fp, num, x, y, dia, drill, npth=False, mask=True):
    """Hand-built PTH pad, BOTH LSET TRAPS honored (Board A, bench-found
    2026-07-30):
    TRAP 1: LSET.AllCuMask() is the bit-mask of all COPPER layers only —
    the name does not mean "Cu + Mask". Shipping it bare leaves the pad
    with no solder-mask aperture: unscrubbable, unsolderable. On THIS
    board every soldered THT pad opens F.Mask AND B.Mask (two faces).
    TRAP 2: AllCuMask() hands back the shared STATIC; AddLayer on it
    poisons every later caller in-process (the serializer compares pad
    sets against that static for the "*.Cu" shorthand — one mutated
    static silently wrote all THT pads copper-only while live-object
    asserts passed). Mutate a COPY, never the static.
    mask=False (gauges): copper both sides, NO aperture — G1-G4 are read
    with a loupe BEFORE the mask squeegee and are not in the scrub set."""
    pad = pcbnew.PAD(fp)
    pad.SetNumber("" if npth else num)
    pad.SetAttribute(pcbnew.PAD_ATTRIB_NPTH if npth else pcbnew.PAD_ATTRIB_PTH)
    pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
    pad.SetSize(VECTOR2I(NM(dia), NM(dia)))
    pad.SetDrillSize(VECTOR2I(NM(drill), NM(drill)))
    lset = pcbnew.LSET(pcbnew.LSET.AllCuMask())    # the copy is load-bearing
    if not npth and mask:
        lset.AddLayer(pcbnew.F_Mask)
        lset.AddLayer(pcbnew.B_Mask)
    pad.SetLayerSet(lset)
    pad.SetPos(VECTOR2I(NM(x), NM(y)))
    fp.Add(pad)
    return pad


def _board_only(fp):
    fp.SetAttributes(fp.GetAttributes()
                     | pcbnew.FP_BOARD_ONLY | pcbnew.FP_EXCLUDE_FROM_BOM
                     | pcbnew.FP_EXCLUDE_FROM_POS_FILES)
    return fp


def make_wire_via(board, name):
    """orbit:WireVia — Ø1.0 hole, Ø2.4 pads both sides (0.7 annular), mask
    open both faces, NO paste (a pasted hole wicks and blocks the wire),
    no silk inside 0.3 (SPEC 'Via geometry'). KiCad plated vias cannot
    model a hand-soldered wire — a THT pad footprint can."""
    fp = pcbnew.FOOTPRINT(board)
    fp.SetFPID(pcbnew.LIB_ID("orbit", "WireVia"))
    fp.SetReference(name)
    _pth(fp, "1", 0, 0, VIA_PAD, VIA_HOLE)
    return _board_only(fp)


def make_isp_pad(board):
    """orbit:ISP_Pad_D1.8mm — bare copper Ø1.8, mask open, NO paste.
    Built front-side like every library SMD footprint; placement flips it
    to B.Cu (Board A's flip idiom)."""
    fp = pcbnew.FOOTPRINT(board)
    fp.SetFPID(pcbnew.LIB_ID("orbit", "ISP_Pad_D1.8mm"))
    pad = pcbnew.PAD(fp)
    pad.SetNumber("1")
    pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
    pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
    pad.SetSize(VECTOR2I(NM(1.8), NM(1.8)))
    lset = pcbnew.LSET()
    lset.AddLayer(pcbnew.F_Cu)
    lset.AddLayer(pcbnew.F_Mask)          # no paste: soldered by hand
    pad.SetLayerSet(lset)
    fp.Add(pad)
    return fp


def make_wire_pad(board):
    """orbit:WirePad_D3.6mm_Drill1.5mm — Ø1.5 hole, Ø3.6 pads both faces
    (SPEC 'Power budget': bench-supply pigtail soldered straight in)."""
    fp = pcbnew.FOOTPRINT(board)
    fp.SetFPID(pcbnew.LIB_ID("orbit", "WirePad_D3.6mm_Drill1.5mm"))
    _pth(fp, "1", 0, 0, 3.6, 1.5)
    return fp


def make_slide_switch(board):
    """orbit:SW_Slide_SPDT_P4.86mm_D1.8mm — 3 in-line blades, 4.86 pitch,
    Ø1.8 holes (parts/tht-bins.toml: blade 1.4 wide, thickness unmeasured,
    hole covers the blade class — crib derivation, not a guess). Pad 3.2 =
    drill+1.4, annular 0.7. CENTRE blade = common = pin 2 (netlist: VBAT
    into pin 2 from PAD+; pin 1 -> Q1 drain /VSW; pin 3 NC). Blades along
    x so the switch lies along the bottom strip."""
    fp = pcbnew.FOOTPRINT(board)
    fp.SetFPID(pcbnew.LIB_ID("orbit", "SW_Slide_SPDT_P4.86mm_D1.8mm"))
    for i in (1, 2, 3):
        _pth(fp, str(i), (i - 2) * 4.86, 0, 3.2, 1.8)
    box = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_RECT)
    # body unmeasured (crib is honest about it): advisory F.Fab box only
    # spans the blade row; no courtyard claim is made from it.
    box.SetStart(VECTOR2I(NM(-6.5), NM(-3.0)))
    box.SetEnd(VECTOR2I(NM(6.5), NM(3.0)))
    box.SetLayer(pcbnew.F_Fab)
    box.SetWidth(NM(0.1))
    fp.Add(box)
    return fp


def make_gauge(board):
    """G1-G4 flip gauge: Ø1.0 hole, Ø1.7 pad both sides, NO net, floating
    copper island (SPEC 'Deliberate exceptions', Decision Q13) — 0.35
    annulus DECLARED 0.3; legal only inside the named DRU areas. mask=False:
    read with a loupe before the squeegee, never soldered, never scrubbed."""
    fp = pcbnew.FOOTPRINT(board)
    fp.SetFPID(pcbnew.LIB_ID("orbit", "FlipGauge_D1.7mm_Drill1.0mm"))
    _pth(fp, "1", 0, 0, 1.7, 1.0, mask=False)
    return _board_only(fp)


def make_m3(board):
    """H1-H4: Ø3.4 NPTH bore, no copper, no mask (Board A idiom)."""
    fp = pcbnew.FOOTPRINT(board)
    fp.SetFPID(pcbnew.LIB_ID("orbit", "MountingHole_3.4mm_NPTH"))
    _pth(fp, "", 0, 0, 3.4, 3.4, npth=True)
    return _board_only(fp)


def fixup_tht(fp):
    """Rework stock THT pads to orbit's laws: annular >=0.7 both sides,
    drill >=1.0 (the 0.8 corn helical floor), pad >= drill+1.4.
      LED_D5.0mm  stock 1.8/0.9 -> 2.4/1.0 (lead 0.45 sq diag 0.64, DFM
                  band [0.84,1.04]; 1.0 = this board's min hole class)
      SW_PUSH_6mm stock -> 2.4/1.0 (crib tht-bins [tactile] hole_mm = 1.0,
                  leg 0.7x0.3; SPEC BOM row says exactly this)
      Buzzer_12x9.5RM7.6 stock 2.0/1.0 -> 2.4/1.0, SHAPE KEPT (stock pin 1
                  is the rect pad = '+' = VCC; netlist BZ1.1 = VCC, asserted
                  at netting time)
    Gauge pads are exempt from the 0.7 law by name (DRU-confined 0.3)."""
    ref = fp.GetReference()
    for pad in fp.Pads():
        if pad.GetAttribute() != pcbnew.PAD_ATTRIB_PTH:
            continue
        if ref.startswith("LED") or ref in ("S1", "S2"):
            pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
            pad.SetSize(VECTOR2I(NM(THT_PAD), NM(THT_PAD)))
            pad.SetDrillSize(VECTOR2I(NM(1.0), NM(1.0)))
        elif ref == "BZ1":
            pad.SetSize(VECTOR2I(NM(THT_PAD), NM(THT_PAD)))   # shape kept (rect '+')
            pad.SetDrillSize(VECTOR2I(NM(1.0), NM(1.0)))
        d = pad.GetDrillSize().x
        s = pad.GetSize(pcbnew.F_Cu)
        ann = (min(s.x, s.y) - d) / 2e6
        floor = 0.299 if ref.startswith("G") else 0.699
        assert ann >= floor, f"annular {ref} {pad.GetNumber()} = {ann:.3f}"
        assert d >= NM(1.0) - 1, f"drill {ref} {pad.GetNumber()} < 1.0"


# ------------------------------------------------------- placement tables
# Flip gauges: inboard of each M3 on the SAME diagonal, 3.54 from it (gap
# 0.99 to the M3 bore) and >=3.0 from the board edge (SPEC via/edge law
# reused as the gauge's own margin, and they must sit in pour area).
# Flip-SYMMETRIC by construction (x mirrors about the flip line 28.0, like
# the pins themselves): a gauge rectangle that is not symmetric about the
# flip axis cannot separate a translation error from a rotation one.
# x mirrors about 28.0 in both pairs. y is NOT symmetric and does not need
# to be: the flip is about Y (x -> -x), so it is the left/right pairing that
# turns four annulus reads into translation AND rotation. G3/G4 sit at
# y = 45 rather than the corners because S2's body (a button you press) and
# H3/H4's 3.2 keepout own the two top corners -- a gauge under a button
# cannot be loupe-read, which is the only thing a gauge is for.
GAUGE = {"G1": (8.0, 8.0), "G2": (48.0, 8.0),
         "G3": (8.0, 45.0), "G4": (48.0, 45.0)}
M3_KEEPOUT_R = 3.2      # M3 socket head 5.5/2 + 0.4 clearance (Board A: 3.3)

# BACK-side parts that are NOT on the ring: ref -> (x, y, rot_deg, seek).
# seek = (padnum, compass) applied AFTER the flip, Board A's quadrant idiom;
# compass is in BOARD terms (orbit's MM flips y, so page-up IS board-north).
BACK = {
    # brain + its three interior neighbours (SPEC: ring interior, BACK)
    "U1":  (RING_C[0], RING_C[1], None, ("3", "E")),   # SND faces BZ1
    "C2":  (None, None, 180.0, None),    # VCC decoupling, INNER_R at pin-8 side
    # R13 + C4 EVICTED from the ring interior (SPEC "Ring interior" lists
    # them there). U1's eight pins escape the centre through the resistor
    # arc, and with three passives also in the 2.6 mm annulus the router
    # could not land /RESET or R13's VCC tap at all (run 2). C2 stays --
    # it is a decoupling cap and belongs AT the pin. R13/C4 do not: they
    # are a RESET pull-up and its filter, and their third node is TP5 on
    # the ISP grid, which is exactly where they now sit. C4 is the one part
    # SPEC calls removable, so a short lead to it is the cheap one.
    # R13 stays at the SPEC-precedent (32.0,7.2) after a measured tour of
    # four alternatives (2026-07-31): vertical at x34.75 sealed the only
    # west lane to TP3/TP5 (/L2 + /RESET broke); at (19.5,6.4) it ate the
    # PAD1<->SW1.1 channel -- the ONE legal crossing of the bottom row (the
    # corner past PAD2 is sealed by PAD2+G1+H1) -- stranding the west VCC
    # cluster; in the north road at (22,31.9) it pinched the ring road and
    # the router collapsed (7 unrouted nets, two /RESET vias). At (32,7.2)
    # every other net closed in the best measured run; its one failure --
    # VCC -> R13.1, pad encircled with 0.06 rail slack -- is solved by a
    # FIXED 0.6 feed through the SW1.2<->SW1.3 blade gap instead (a 0.6
    # track has 0.26 of legal slack there; the 0.8 rail's 0.06 is why the
    # router refused it four times). See add_vcc_spine. Named: VCC->R13.1.
    "R13": (32.0, 7.2, 0.0, None),
    "C4":  (31.5, 10.0, 0.0, None),
    # power entry cell, under the bottom strip
    # Q1/C1 lifted clear of the bottom strip (were 19.5,8.2 and 14.0,8.5):
    # at 8.2 the P-FET's copper left /VBAT a 1.25 mm lane over the switch's
    # west blade and a 0.8 rail needs 1.6, so the rail went unrouted in two
    # consecutive router runs. At 9.5 the lane is 2.55.
    "Q1":  (17.5, 9.5, 0.0, None),
    "C1":  (11.5, 9.0, 0.0, None),
    # buzzer cell beside BZ1 -- C3 sits UNDER the buzzer body, which is a
    # front-side THT can: the back copper there is free real estate
    "R14": (39.5, 29.0, 0.0, None),
    "Q2":  (44.5, 30.5, 0.0, None),
    "D1":  (43.5, 22.0, 0.0, None),
    "C3":  (48.0, 21.0, 0.0, None),
    # button series resistors, each beside its own button
    "R15": (41.0, 14.0, 90.0, None),
    "R16": (40.5, 38.0, 90.0, None),
}
# ISP 2x3 on the 2.54 grid, standard AVR pinout read with the BACK up:
#   MISO VCC / SCK MOSI / RST GND  (TP1..TP6 carry those values in the sch)
# 35.0 -> 37.0 (2026-07-31): at 35.0 the grid sat in a 0.64 mm pocket
# between SW1's east blade (x 33.46) and its own west pad column, and
# /RESET could not reach TP5 at all. 37.0 opens that approach to 2.64.
# SPEC's "~(33-38, 3-6)" becomes x 37.00/39.54 -- 1.5 mm past its upper
# figure, which is a bound the bottom strip cannot honour once SW1 is a
# 4.86-pitch part (SPEC assumed a 2.54 switch; the bench overrode it).
ISP_ORIGIN = (37.0, 3.0)                 # TP5 (RST), the grid's lower-left
ISP_GRID = {"TP5": (0, 0), "TP6": (1, 0),
            "TP3": (0, 1), "TP4": (1, 1),
            "TP1": (0, 2), "TP2": (1, 2)}
ISP_PITCH = 2.54


# ---- the resistor spokes: RADIAL, not tangential (SPEC deviation, measured)
# U1 sits at the ring centre and SEVEN nets must escape past the resistors
# (L0..L3, VCC, RESET, SND -- GND is planed). Tangential at r=9 the twelve
# gaps come out 0.86 / 1.66 / 1.06 mm on the repeating 1206-0805-0603 cycle
# and a 0.6 track needs 0.6 + 2*0.4 = 1.4, so only FOUR are crossable and a
# 0.8 rail crosses NONE. Three consecutive router runs failed on exactly
# that: /SND, /RESET, /L0..L3 stranded at the centre, and the vias the
# router spent working around it ran to 9 of a ceiling of 10.
# RADIAL spokes cost the same arc (a package's WIDTH, 1.75/1.40/0.95, not
# its length) and open twelve channels 1.5 mm at the inner end and 3.4 at
# the outer. The band is unchanged -- every outer copper edge lands on
# RES_OUTER, just inside the cathode pad ring -- and each resistor still
# sits under its own LED, now as a straight radial stub: pad 1 (its LED's
# cathode) outward, pad 2 (its charlieplex line) inward toward U1.
# SPEC says "tangentially at r ~= 10"; this is the same r band with the
# packages turned 90 degrees, and it is what makes the board route.
RES_OUTER = 9.7          # outer copper edge of every spoke: cathode pad
                         # inner edge is 10.33, so this keeps 0.63
# ---- the last four tangential spokes go radial too (2026-07-31, measured)
# The four 1206s (R1/R4/R7/R10, positions 11/9/1/3) were the ONE exception
# left: laid tangentially they spend 4.40 mm of arc each and WALL OFF their
# own two channels. Measured on the placed board, the twelve channel widths
# then read 1.32 / 1.79 / ... -- a 0.6 track needs 1.4 and a 0.8 rail 1.6,
# so only four of twelve gaps were crossable at all, and VCC's ring entry,
# /RESET (U1.1 -> C4.1) and /SND (R14.1 -> U1.3) had nowhere to go.
# Radial they spend 1.75. WHY THE RADIUS MOVES 0.15 for this package only:
#   * the 1206's copper is 4.40 long, so a radial one reaches in to
#     outer - 4.40, and the old note ("it pinches U1's road to 0.90") was
#     right about the arithmetic at RES_OUTER = 9.7 -- and 0.84 of it is
#     CLEARANCE, not road: transformed into the spoke frame, U1's copper
#     corner (4.400, 2.230) sits at radial 4.926 with tangential offset
#     0.269, inside the spoke's 0.875 half-width, so the gap to a 9.7 spoke
#     is 9.7 - 4.40 - 4.926 = 0.374 -- BELOW the 0.4 law. The old comment
#     called it a road problem; it is a DRC violation.
#   * upper bound: cathode pad inner edge 10.33 - CLEAR = 9.93.
#   * lower bound: 4.926 + 4.40 + CLEAR = 9.726.
#   * 9.85 sits in that 0.20 band with 0.52 to U1's corner and 0.48 to the
#     cathode ring -- slack on both sides, leaning to U1 because U1's
#     position is SPEC's (the ring centre) and the cathode ring's is this
#     file's arithmetic.
# Result, computed from the same numbers and asserted at build time: all
# twelve channels open, the narrowest 1.79 mm (a 1206 beside an 0805 at
# r = 6.50), which admits a 0.8 RAIL with 0.19 to spare. The ring road round
# U1 is no longer load-bearing: every U1 pin now has a channel it can leave
# through radially instead of driving around the block to find one.
RES_OUTER_1206 = 9.85


def _copper_radius(fp):
    """Circumscribed radius of a footprint's pad copper about the ring
    centre, mm. Built from KiCad's own pad bounding boxes, which are
    AXIS-ALIGNED supersets of a rotated pad: exact for an axis-aligned
    footprint (U1 sits at 180 deg) and an over-estimate otherwise, which is
    the safe direction for something used as a clearance floor."""
    c = MM(*RING_C)
    r = 0.0
    for p in fp.Pads():
        bb = p.GetBoundingBox()
        for x in (bb.GetLeft(), bb.GetRight()):
            for y in (bb.GetTop(), bb.GetBottom()):
                r = max(r, math.hypot(x - c.x, y - c.y) / 1e6)
    return r


def assert_channels(spokes):
    """THE WALL LAW (2026-07-31). Twelve radial spokes at 30 deg leave twelve
    channels, and seven nets plus the GND plane have to cross them. A channel
    that no track fits through is not a routing difficulty, it is a wall --
    that is what three sessions of unrouted /RESET, /SND and VCC ring entry
    were, and no amount of re-drawing fixes geometry.

    THE ARITHMETIC. Spoke n is a rectangle straddling its own ray: arc
    half-width w_n, copper starting at radius in_n. For neighbours 30 deg
    apart, a straight radial track on the bisector is r*sin(15 deg) from
    each ray at radius r, so the free width between the two spokes is
        width(r) = 2*r*sin(15 deg) - w_i - w_j
    and it is NARROWEST at the inner end of the channel, r = max(in_i,in_j),
    where the two spokes first both exist. A 0.8 mm RAIL needs
    0.8 + 2*CLEAR = 1.6; a 0.6 signal needs 1.4. This asserts the RAIL
    figure for all twelve: VCC's ring entry is a rail, and a board whose
    channels only pass signal has already spent the via budget deciding
    which net loses.

    `spokes` is {ring position: (inner radius, arc half-width)}, both
    measured off the placed footprints -- never off a package name."""
    need = TRACK_RAIL + 2 * CLEAR
    widths = {}
    for n in range(1, 13):
        i, j = spokes[n], spokes[n % 12 + 1]
        r = max(i[0], j[0])
        widths[n] = 2 * r * math.sin(math.radians(15.0)) - i[1] - j[1]
    worst = min(widths, key=widths.get)
    for n, w in sorted(widths.items()):
        assert w >= need - 1e-9, (
            f"channel {n}->{n % 12 + 1} is {w:.3f} mm at its pinch, under "
            f"the {need:.1f} a {TRACK_RAIL} rail needs. The resistor arc is "
            "a WALL again -- see RES_OUTER_1206.")
    print(f"resistor wall: 12/12 channels open, narrowest {widths[worst]:.2f}"
          f" mm at position {worst}->{worst % 12 + 1} (a {TRACK_RAIL} rail "
          f"needs {need:.1f}, a {TRACK_SIG} signal {TRACK_SIG + 2 * CLEAR:.1f})")


def ring_angle(n):
    """Ring pos n -> board-frame angle in degrees. SPEC: pos 1 at 12
    o'clock, 30 deg steps, CLOCKWISE (so the angle DECREASES with n)."""
    return 90.0 - 30.0 * (n - 1)


def res_angle(n):
    """Resistor n's angle on the arc: arc-length packing (see the channel
    note), not the naive 30 deg step. Asserted to close on the circle."""
    s = 0.0
    for k in range(1, n):
        s += (_HALF_LEN[k % 3] + _HALF_LEN[(k + 1) % 3]
              + (GAP_1206_0805, GAP_0805_0603, GAP_0603_1206)[(k - 1) % 3])
    return 90.0 - math.degrees(s / RES_R)


def polar(ang, r):
    a = math.radians(ang)
    return (RING_C[0] + r * math.cos(a), RING_C[1] + r * math.sin(a))


def ring_xy(n, r):
    return polar(ring_angle(n), r)


# ---------------------------------------------------------------- stages
def quadrant_ok(fp, padnum, want):
    """Board A idiom verbatim. Compass is BOARD north/east because orbit's
    MM() already flips y: page-up == board-up."""
    pads = list(fp.Pads())
    cx = sum(q.GetPosition().x for q in pads) / len(pads)
    cy = sum(q.GetPosition().y for q in pads) / len(pads)
    p = pad_by_num(fp, padnum).GetPosition()
    x, y = p.x - cx, p.y - cy
    if want == "N": return y < 0 and abs(y) > abs(x)
    if want == "S": return y > 0 and abs(y) > abs(x)
    if want == "E": return x > 0 and abs(x) > abs(y)
    if want == "W": return x < 0 and abs(x) > abs(y)
    raise ValueError(want)


def load_fp(board, fpid):
    """Every footprint comes from LIBROOT or an orbit builder -- never a
    hand-drawn one-off (SPEC BOM traces each line to a crib)."""
    lib, name = fpid.split(":", 1)
    if lib == "orbit":
        fp = {"WirePad_D3.6mm_Drill1.5mm": make_wire_pad,
              "ISP_Pad_D1.8mm": make_isp_pad,
              "SW_Slide_SPDT_P4.86mm_D1.8mm": make_slide_switch}[name](board)
    else:
        fp = pcbnew.FootprintLoad(f"{LIBROOT}/{lib}.pretty", name)
        assert fp, f"missing footprint {fpid}"
    return fp


def cu_bbox(fp):
    """Copper bounding box of a footprint's pads, in nm page coords. Used to
    centre a part whose library ANCHOR is a pad (SW_PUSH_6mm, the buzzer),
    not its body -- placing those by anchor puts the body somewhere else."""
    xs, ys = [], []
    for p in fp.Pads():
        q, s = p.GetPosition(), p.GetSize(pcbnew.F_Cu)
        xs += [q.x - s.x // 2, q.x + s.x // 2]
        ys += [q.y - s.y // 2, q.y + s.y // 2]
    return min(xs), min(ys), max(xs), max(ys)


def centre_on(fp, x, y):
    tgt = MM(x, y)
    x0, y0, x1, y1 = cu_bbox(fp)
    d = VECTOR2I(tgt.x - (x0 + x1) // 2, tgt.y - (y0 + y1) // 2)
    fp.SetPosition(fp.GetPosition() + d)


def pad_on(fp, num, x, y):
    """Translate fp so that pad `num` lands exactly on board (x, y)."""
    d = MM(x, y) - pad_by_num(fp, num).GetPosition()
    fp.SetPosition(fp.GetPosition() + d)


def add_part(board, ref, comps, flip):
    val, fpid = comps[ref]
    fp = load_fp(board, fpid)
    fp.SetReference(ref)
    fp.SetValue(val)
    board.Add(fp)
    fp.SetFPID(pcbnew.LIB_ID(*fpid.split(":", 1)))   # schematic parity
    if flip:
        fp.Flip(fp.GetPosition(), False)
    return fp


def place_front_tht(board, comps, fps):
    """Ring LEDs (12x, radial leads, CATHODE_INWARD), S1/S2/BZ1/SW1/PADs per
    POS, H1-H4, G1-G4 (SPEC 'Layout notes'). Nothing here is flipped: THT
    bodies live on the FRONT and their leads solder on the back, so the
    stock front-side footprints are already the right way round."""
    import math
    r_k = LEAD_R_IN if CATHODE_INWARD else LEAD_R_OUT
    r_a = LEAD_R_OUT if CATHODE_INWARD else LEAD_R_IN
    for n in range(1, 13):
        ref = f"LED{POS_REF[n]}"          # Decision Q5 placement permutation
        fp = add_part(board, ref, comps, flip=False)
        # spread the lead pair to LEAD_PITCH while the footprint is still
        # unrotated (pad positions are local): see the LEAD_PITCH note.
        pad_by_num(fp, "2").SetPos(VECTOR2I(NM(LEAD_PITCH), 0))
        # LED_D5.0mm is pad1(K) at local (0,0), pad2(A) at (+2.54, 0): the
        # local +x axis IS the K->A vector, so orienting the footprint to
        # the ring angle points it radially. Verified by assert, not faith.
        fp.SetOrientation(deg(ring_angle(n) if CATHODE_INWARD
                             else ring_angle(n) + 180.0))
        pad_on(fp, "1", *ring_xy(n, r_k))
        got = pad_by_num(fp, "2").GetPosition()
        want = MM(*ring_xy(n, r_a))
        assert abs(got.x - want.x) < 2000 and abs(got.y - want.y) < 2000, \
            f"{ref} anode lead off-radius by {(got - want).EuclideanNorm()/1e6:.3f}"
        fps[ref] = fp
    for ref in ("PAD1", "PAD2", "SW1", "S1", "S2", "BZ1"):
        fp = add_part(board, ref, comps, flip=False)
        if ref == "BZ1":
            # rot 180 puts pad 2 (/SND_C) WEST, nearest Q2's collector; the
            # rect pad 1 ("+" on the can, SPEC) stays VCC and faces east.
            fp.SetOrientation(deg(180.0))
        centre_on(fp, *POS[ref])
        fps[ref] = fp
    for ref, (x, y) in M3.items():
        fp = make_m3(board)
        fp.SetReference(ref)
        board.Add(fp)
        fp.SetPosition(MM(x, y))
        fps[ref] = fp
    for ref, (x, y) in GAUGE.items():
        fp = make_gauge(board)
        fp.SetReference(ref)
        fp.SetValue("flip gauge")
        board.Add(fp)
        fp.SetPosition(MM(x, y))
        fps[ref] = fp


def place_back_smd(board, comps, fps):
    """U1 wide-SOIC (5.3 mm EIAJ 8S2, from the schematic's footprint field)
    at the ring centre, rotated so pin 3 /SND faces BZ1; R1..R12 tangential
    at RES_R in the matrix table's package pattern; C2/R13/C4 on INNER_R;
    Q1+C1 under the bottom strip; Q2+D1+C3+R14 beside BZ1; R15/R16 beside
    their buttons; the ISP 2x3 grid at 2.54 (SPEC 'Layout notes')."""
    u1 = add_part(board, "U1", comps, flip=True)
    for a in (0, 90, 180, 270):
        u1.SetOrientation(deg(a))
        if quadrant_ok(u1, *BACK["U1"][3]):
            break
    else:
        raise RuntimeError("U1: no orientation puts /SND (pin 3) east")
    centre_on(u1, *RING_C)
    fps["U1"] = u1
    spokes = {}
    for n in range(1, 13):                     # the ring's series resistors
        ref = f"R{POS_REF[n]}"       # Rk follows LEDk (Decision Q5 placement)
        fp = add_part(board, ref, comps, flip=True)
        # RADIAL: the package's local +x runs pad1 -> pad2, so pointing it
        # INWARD (angle + 180) puts pad 1 outward at its LED's cathode and
        # pad 2 inward at U1. Half-length is MEASURED off the footprint, so
        # the three package sizes align on RES_OUTER without magic numbers.
        x0, y0, x1, y1 = cu_bbox(fp)
        halfL, halfH = (x1 - x0) / 2e6, (y1 - y0) / 2e6
        a = ring_angle(n)
        # ALL TWELVE are radial now (RES_OUTER_1206's note): local +x runs
        # pad1 -> pad2, so pointing it INWARD puts pad 1 outward at its LED's
        # cathode and pad 2 inward at U1. The 1206 rides 0.15 further out,
        # the only per-package number on the arc and the only thing that
        # makes a 4.40 mm package legal in a 5.0 mm band.
        outer = RES_OUTER_1206 if "1206" in comps[ref][1] else RES_OUTER
        fp.SetOrientation(deg(a + 180.0))
        centre_on(fp, *polar(a, outer - halfL))
        # The two ends of the band, both asserted against MEASURED copper
        # rather than against a remembered number. Inner: U1's copper
        # CIRCUMSCRIBED radius, which bounds the true rect-to-rect distance
        # from below for a spoke at any angle -- conservative on purpose, so
        # this can never pass a violation it did not model. Outer: this
        # LED's own cathode pad, the thing the spoke is reaching for.
        inner, r_u1 = outer - 2 * halfL, _copper_radius(u1)
        assert inner - r_u1 >= CLEAR - 1e-9, \
            (f"{ref} reaches r={inner:.3f}; U1's copper circumscribes "
             f"r={r_u1:.3f}, leaving {inner - r_u1:.3f} < {CLEAR}")
        assert (LEAD_R_IN - THT_PAD / 2) - outer >= CLEAR - 1e-9, \
            (f"{ref} outer edge {outer} is inside the cathode pad ring "
             f"({LEAD_R_IN - THT_PAD / 2:.3f}) by less than {CLEAR}")
        spokes[n] = (inner, halfH)     # radial reach + arc half-width
        fps[ref] = fp
    assert_channels(spokes)
    # C2 / R13 / C4 ride INNER_R at angles chosen to miss both U1's body and
    # the resistor arc: 180 (pin-8 side, decoupling), -45 and +45.
    # C2 (100 nF at U1 pin 8) lives in the only interior space the radial
    # spokes leave: due north or south of U1, where the SOIC's SHORT side
    # is only 2.23 from centre and the nearest spoke (a 1206) starts at
    # 5.30. Whichever of the two is closer to pad 8 wins -- measured, not
    # assumed, because U1's rotation is chosen by a seek.
    p8 = pad_by_num(u1, "8").GetPosition()
    ang = min((90.0, 270.0),
              key=lambda t: (MM(*polar(t, INNER_C2)) - p8).EuclideanNorm())
    fp = add_part(board, "C2", comps, flip=True)
    fp.SetOrientation(deg(ang + 90.0))            # tangential: fits the slot
    centre_on(fp, *polar(ang, INNER_C2))
    # PAD 1 (VCC) FACES U1 PIN 8 -- measured, not assumed (the R13 idiom
    # below; the flip makes rotation signs easy to get backwards).
    # WHY (2026-07-31, named connection: VCC C2.1 -> U1.8): tangential
    # placement is free to put either pad first, and it had put pad 1 on
    # the FAR side -- C2.1 at (23.04,29.80), U1.8 at (18.41,27.91), a 5.00
    # mm hop that has to climb over C2's own body. The router lost exactly
    # that hop in 3 of 4 measured draws (`U1-8 <-> C2-1`), and losing it
    # splits VCC into a U1.8 island and everything else, which is then
    # reported as two or three further VCC opens. Facing pad 1 at the pin
    # shortens the hop to 3.17 mm on a clear diagonal, and
    # add_vcc_pin_link() then LAYS it: a decoupling capacitor's path to
    # the pin it decouples is not a thing an autorouter should be
    # choosing. Pad 2 (GND) moves to the east side, where add_gnd_tails
    # still reaches the measured main-pour region southwest of U1.
    def _d(num):
        return (pad_by_num(fp, num).GetPosition() - p8).EuclideanNorm()
    if _d("1") > _d("2"):
        fp.SetOrientation(fp.GetOrientation() + deg(180.0))
        centre_on(fp, *polar(ang, INNER_C2))
    assert _d("1") < _d("2"), "C2 pad 1 (VCC) does not face U1 pin 8"
    # C2 shares the interior with the spokes' inner ends, and the radial
    # 1206s now reach 1.45 mm further in than the tangential ones did, so
    # the cap is no longer trivially clear of them: asserted, same
    # circumscribed-radius argument as the U1 floor (_copper_radius).
    c2_r, in_min = _copper_radius(fp), min(s[0] for s in spokes.values())
    assert in_min - c2_r >= CLEAR - 1e-9, \
        (f"C2's copper circumscribes r={c2_r:.3f}; the innermost spoke "
         f"reaches r={in_min:.3f}, leaving {in_min - c2_r:.3f} < {CLEAR}")
    fps["C2"] = fp
    for ref, (x, y, rot, _seek) in BACK.items():
        if ref in fps:
            continue
        fp = add_part(board, ref, comps, flip=True)
        fp.SetOrientation(deg(rot))
        centre_on(fp, x, y)
        fps[ref] = fp
    # R13 pad 2 (/RESET) faces EAST toward U1.1 (25.59,27.91); pad 1 (VCC)
    # west toward the U1.8/C2.1 cluster. Measured, not assumed -- the flip
    # idiom makes rotation signs easy to get backwards.
    r13 = fps["R13"]
    if (pad_by_num(r13, "2").GetPosition().x
            < pad_by_num(r13, "1").GetPosition().x):
        r13.SetOrientation(r13.GetOrientation() + deg(180.0))
        centre_on(r13, *BACK["R13"][:2])
    assert (pad_by_num(r13, "2").GetPosition().x
            > pad_by_num(r13, "1").GetPosition().x), "R13 pad 2 not east"
    for ref, (cx, ry) in ISP_GRID.items():     # bare B.Cu ISP pads
        fp = add_part(board, ref, comps, flip=True)
        fp.SetPosition(MM(ISP_ORIGIN[0] + cx * ISP_PITCH,
                          ISP_ORIGIN[1] + ry * ISP_PITCH))
        assert fp.Pads()[0].IsOnLayer(pcbnew.B_Cu), f"{ref} not on B.Cu"
        fps[ref] = fp


FR_HOME = os.path.expanduser("~/.clauderacam/tools/freerouting")
FR_JAVA = os.path.join(FR_HOME, "jdk-25.0.4+7-jre/bin/java")
FR_JAR = os.path.join(FR_HOME, "freerouting-2.2.4.jar")
DSN = os.path.join(HERE, "orbit.dsn")
SES = os.path.join(HERE, "orbit.ses")
# What .ses belongs to what .dsn, plus the router's own unrouted list.
ROUTE_REC = os.path.join(HERE, "orbit.route.json")


# ---- SMD GND necks (Board A law, boards/coupon/tools-layout.py) ---------
NECK_EMBED = 1.2                 # 0.4 moat crossing + >=0.8 embedded in pour
# (ref, padnum) -> (ux, uy[, embed]) or None; PAGE axes (uy=+1 is board
# south). Measured overrides only, never guessed:
#   C2.2 and U1.4: every cardinal neck landed in a router-carved pocket in
#   at least one measured fill (west/east defaults AND the south retry) --
#   they are hand-drawn GND tails instead (add_gnd_tails), reaching the one
#   region that stayed main-pour in ALL measured fills.
#   D1.1 default (west) tip anchored the same recurring sliver pocket in
#   two fills (x[39.5,41] y[20.6,22.1]); SOUTH with embed 2.0 puts the tip
#   at y18.6, below the y~19.8 corridor that east-cluster VCC sweeps have
#   used (a 1.2-embed tip at y19.4 would sit exactly in that sweep's moat),
#   in the S1-north band that is main in every fill measured.
NECK_DIR = {("C2", "2"): None, ("U1", "4"): None, ("D1", "1"): (0, 1, 2.0)}


def add_gnd_tails(board, fps):
    """Hand-drawn GND tails for the two pads a 1.2 mm neck cannot save
    (the NECK_DIR note): U1.4 and C2.2 sit in the ring interior where the
    router wraps tracks around U1's pad rows and pockets every short neck
    (measured in five consecutive fills). Both tails run to (20.5,21.9) /
    (20.1,22.4) -- southwest of U1's body, the one nearby region that was
    MAIN pour in every fill measured this session. Clearances: C2.2 tail
    passes U1's west pad row at >= 1.10, pin 5 at 0.80; U1.4 tail crosses
    only the dead under-body zone (nearest foreign pad 2.7). Laid before
    the export like every fixed wire, embedded solid by the fill."""
    for ref, num, end in (("U1", "4", MM(20.5, 21.9)),
                          ("C2", "2", MM(20.1, 22.4))):
        pad = pad_by_num(fps[ref], num)
        assert pad.GetNetname() == "GND"
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(pad.GetPosition()); t.SetEnd(end)
        t.SetWidth(NM(TRACK_SIG)); t.SetLayer(pcbnew.B_Cu)
        t.SetNet(pad.GetNet()); board.Add(t)
    print("gnd tails: 2 hand-drawn B.Cu tails (U1.4, C2.2)")


def add_gnd_necks(board, fps):
    """One routed 0.6 mm neck per SMD GND pad (Board A's add_gnd_necks,
    ported 2026-07-31 as the missing law). The pad itself becomes
    ZONE_CONNECTION_NONE, so the pour pulls back 0.4 all around; the neck
    is a plain GND track from the pad centre outward -- tracks are always
    solid-embedded by the fill, so each pad gets exactly one 0.6 heat path.
    On THIS board the law also carries the fragment rule: measured
    2026-07-31, the B.Cu pour was 5 fragments, four of them pockets
    anchored only by a thermal spoke to D1.1 / U1.4 / Q2.2 / C2.2 (D1.1
    and C2.2 starved at 1 spoke). With the pads NONE-connected a sealed
    pocket holds no connected item and the filler deletes it, while the
    neck ties the pad to real pour. Direction defaults to 'away from the
    footprint centre along the dominant axis' (clears the body and the
    second pad); NECK_DIR overrides where that default is measured to land
    on foreign copper. Laid BEFORE the Specctra export so the router
    inherits the necks as fixed copper."""
    gnd_smd = []
    for fp in fps.values():
        for pad in fp.Pads():
            if (pad.GetNetname() == "GND"
                    and pad.GetAttribute() == pcbnew.PAD_ATTRIB_SMD):
                assert pad.IsOnLayer(pcbnew.B_Cu), \
                    f"{fp.GetReference()} GND pad not on B.Cu"
                pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_NONE)
                gnd_smd.append((fp.GetReference(), pad))
    for ref, pad in gnd_smd:
        fp = fps[ref]
        p, c = pad.GetPosition(), fp.GetPosition()
        dx, dy = p.x - c.x, p.y - c.y
        embed = NECK_EMBED
        key = (ref, pad.GetNumber())
        if key in NECK_DIR:
            d = NECK_DIR[key]
            if d is None:
                continue           # this pad's neck is a hand-drawn route
            if len(d) == 3:
                ux, uy, embed = d
            else:
                ux, uy = d
        elif abs(dx) >= abs(dy):
            ux, uy = (1 if dx >= 0 else -1), 0
        else:
            ux, uy = 0, (1 if dy >= 0 else -1)
        s = pad.GetSize(pcbnew.B_Cu)
        half = (s.x if ux else s.y) / 2e6
        L = half + embed
        end = VECTOR2I(p.x + NM(L) * ux, p.y + NM(L) * uy)
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(p); t.SetEnd(end)
        t.SetWidth(NM(0.6)); t.SetLayer(pcbnew.B_Cu)
        t.SetNet(pad.GetNet()); board.Add(t)
    print(f"gnd necks: {len(gnd_smd)} SMD GND pads -> ZONE_CONNECTION_NONE"
          " + 0.6 B.Cu neck")


def link_switch_legs(board, fps):
    """The 4-leg tactiles carry TWO pads numbered 1 and two numbered 2; the
    real part shorts each pair inside its own body, but neither KiCad's
    connectivity nor Specctra knows that, so run 1 reported 'S2-1@1 -> S2-1'
    as an unrouted connection and the DRC gate agreed. One 0.6 B.Cu track
    per pair, laid BEFORE the export so the router inherits the fact rather
    than trying to discover it. The back under a front-mounted button body
    is empty copper -- this is the one place on the board where a link costs
    nothing."""
    n = 0
    for ref in ("S1", "S2"):
        for num in ("1", "2"):
            pads = [p for p in fps[ref].Pads() if p.GetNumber() == num]
            assert len(pads) == 2, f"{ref}.{num}: expected 2 legs"
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(pads[0].GetPosition()); t.SetEnd(pads[1].GetPosition())
            t.SetWidth(NM(TRACK_SIG)); t.SetLayer(pcbnew.B_Cu)
            t.SetNet(pads[0].GetNet()); board.Add(t)
            n += 1
    print(f"switch-leg links: {n} B.Cu tracks")


def add_vcc_spine(board, fps):
    """Fixed VCC pre-route (link_switch_legs precedent: laid before the
    Specctra export so the router inherits the fact). WHY (measured,
    2026-07-31): left to itself the router swept VCC around the entire
    board perimeter -- TP2 -> right edge -> top y42.5 -> left edge -- which
    walled off three of six B.Cu pour fragments and STILL left VCC ->
    R13.1 unrouted. The spine rides the dead band below the pour inset
    (track centre y 0.85: bottom edge 0.45 honours the 0.4 edge law; the
    pour outline stops at 0.6, so no fill strip fits underneath and the
    spine fragments nothing). An earlier, longer spine taught the lesson
    baked into the start point: the C1.1 drop leg violated C1.2's
    hand-solder pad (0.23 measured) and boxed the west GND pocket -- so
    the spine STARTS at x19.5 and the router brings the west cluster down
    the PAD1<->SW1.1 channel itself.

    THE SPINE NOW STOPS AT x29.9 (2026-07-31). It used to run to x43.7,
    rise to the TP2 row and come west into TP2, and those three legs
    together BUILT A CLOSED BOX round the east ISP column: west wall the
    x39.54 pad column (TP2/TP4/TP6 at 2.54 pitch -- a 0.74 gap, and a 0.6
    track between two pads needs 1.4, so that wall has no door), north
    wall the y8.08 run into TP2, east wall the x43.7 riser, south wall the
    spine itself. Measured consequences, both of them gate failures:
      * the B.Cu pour inside the box was an ORPHAN FRAGMENT, x[40.18,
        42.90] y[1.65, 7.28], 12.99 mm^2 -- one of the four zone-to-zone
        opens, and one reason B.Cu filled as 5 fragments where SPEC allows
        exactly 1;
      * TP4 (/L0) sat INSIDE the box. Its west, north and south neighbours
        are ISP pads at 2.54, so its only approach is from the east -- and
        the east was the riser. `/L0 LED5-2 -> TP4-1` was therefore not a
        weak router draw but a geometric impossibility, which is exactly
        why it survived every knob setting and every re-draw.
    Ending the spine at x29.9 (0.47 short of the r13 feed's riser at
    x29.43+0.3) deletes all three walls at once. TP2 keeps its VCC from
    the north instead: the router already ran VCC up x39.32 from the TP2
    row to y20.81 on its own in every measured draw, so this hands that
    leg back to it rather than inventing a new one. Audited gaps on what
    remains: SW1 blades (y4.6) 1.35, /VBAT link 0.47 (add_vbat_link),
    C1.2 hand-solder pad 1.10. All >= 0.4."""
    tp2 = pad_by_num(fps["TP2"], "1")   # ISP VCC pad -- fed from the north now
    assert tp2.GetNetname() == "VCC"
    y = 0.85
    segs = [(MM(19.5, y), MM(29.9, y))]            # the edge band run
    net = tp2.GetNet()
    for a, b in segs:
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(a); t.SetEnd(b)
        t.SetWidth(NM(TRACK_RAIL)); t.SetLayer(pcbnew.B_Cu)
        t.SetNet(net); board.Add(t)
    print(f"vcc spine: {len(segs)} fixed B.Cu track(s) along the edge band "
          "(stops at x29.9: the east ISP box is deleted)")


def add_vcc_pin_link(board, fps):
    """C2.1 -> U1.8 as fixed copper: the decoupling cap's own hop to the pin
    it decouples (add_gnd_tails precedent -- that function already hand-draws
    U1.4 and C2.2 for the same reason, that the ring interior is no place to
    let a router improvise).

    WHY IT IS FIXED AND NOT ROUTED. This is the shortest and most important
    connection on the board -- a 100 nF cap exists to be AT its pin, and a
    decoupling loop the autorouter drew around three sides of a SOIC is a
    decoupling loop that does not decouple. It is also the connection the
    router most reliably LOST: `U1-8 <-> C2-1` went unrouted in 3 of the 4
    measured draws, and because U1.8 has no second VCC neighbour inside the
    ring, losing it strands pin 8 as a one-pad island and turns one failure
    into three (`U1-8 -> Q1-2`, `C2-1 -> U1-8`, `via -> U1-8` are all the
    same wound). With C2 turned so pad 1 faces the pin (place_back_smd) the
    hop is a single clear 3.17 mm diagonal, so it is stated here instead.

    CLEARANCES (measured on the placed board, foreign copper only):
      U1 pad 7, the nearest other pin  1.27 below pad 8, not on the path
      U1 body corner (19.35, 28.65)    the track enters the body shadow at
                                       y 28.61 -- no copper there, the SOIC
                                       sits above it
      C2 pad 2 (GND), 2.08 east        the track runs the other way
      R2 / R7 spoke pads               >= 2.9
    The 0.8 RAIL width is kept: this is the rail, and freerouting refuses a
    fixed wire below its net-class width (see add_r13_feed)."""
    c2 = pad_by_num(fps["C2"], "1")
    p8 = pad_by_num(fps["U1"], "8")
    assert c2.GetNetname() == "VCC" and p8.GetNetname() == "VCC", \
        "C2.1/U1.8 are not both VCC"
    d = (c2.GetPosition() - p8.GetPosition()).EuclideanNorm() / 1e6
    assert d < 4.0, f"C2.1 -> U1.8 is {d:.2f} mm: C2 is no longer at the pin"
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(c2.GetPosition()); t.SetEnd(p8.GetPosition())
    t.SetWidth(NM(TRACK_RAIL)); t.SetLayer(pcbnew.B_Cu)
    t.SetNet(c2.GetNet()); board.Add(t)
    print(f"vcc pin link: 1 fixed B.Cu track C2.1 -> U1.8 ({d:.2f} mm)")


def add_vbat_link(board, fps):
    """The battery rail PAD1.1 -> SW1.2, as fixed copper (add_vcc_spine and
    link_switch_legs precedent: laid before the export so the router
    inherits the fact instead of re-deriving it).

    WHY IT CANNOT BE THE ROUTER'S JOB. SW1 is a BACK-ONLY component (the
    blades run under the body; UNPLATED_BACK_ONLY), so this rail has
    exactly one legal layer -- B.Cu -- and exactly one legal corridor:
    under the west blade, between the VCC spine and the blade row. It is
    a 0.95 mm slot and /VBAT is a RAIL net at 0.8, so the whole corridor
    admits a 0.15 mm band of track centres and nothing wider will ever
    fit. Freerouting declined it in every draw measured across two
    sessions -- nine consecutive runs at the SPEC placement, then five
    more after SW1 moved 26.0 -> 27.0 and y 4.0 -> 4.6 to widen this very
    slot. A corridor that tight is a fact to state, not a search to
    re-run: the arithmetic below IS the route.

    THE ARITHMETIC (page-parallel, so it is checkable by hand):
      VCC spine  centre y 0.85, width 0.80  -> top edge    y 1.25
      SW1.1 pad  centre y 4.60, 3.2 square  -> bottom edge y 3.00
      a 0.8 track needs 0.4 of half-width and CLEAR=0.4 to each neighbour
      -> y_c >= 1.25 + 0.4 + 0.4 = 2.05   and   y_c <= 3.00 - 0.4 - 0.4 = 2.60
    y = 2.12 sits in that window with 0.47 to the spine and 0.48 to the
    blade -- both above the 0.4 law, and deliberately NOT at the midpoint:
    the spine is fixed copper this file owns, the blade is a pad whose
    position SPEC owns, so the slack leans toward the thing this file
    cannot move. The riser into SW1.2 clears SW1.1 by 2.86 and SW1.3 by
    2.86. Asserted below, so a placement nudge that closes the slot stops
    the build instead of silently shorting the battery to the switch."""
    pad1 = pad_by_num(fps["PAD1"], "1")
    sw2 = pad_by_num(fps["SW1"], "2")
    sw1 = pad_by_num(fps["SW1"], "1")
    assert pad1.GetNetname() == sw2.GetNetname() == "/VBAT", "not the /VBAT rail"
    y = 2.12
    half = TRACK_RAIL / 2
    spine_top = 0.85 + TRACK_RAIL / 2
    blade_bot = (H - (sw1.GetPosition().y / 1e6 - OY)) - \
        sw1.GetSize(pcbnew.B_Cu).y / 2e6
    assert y - half - spine_top >= CLEAR - 1e-9, \
        f"/VBAT link to VCC spine {y - half - spine_top:.3f} < {CLEAR}"
    assert blade_bot - (y + half) >= CLEAR - 1e-9, \
        f"/VBAT link to SW1.1 blade {blade_bot - (y + half):.3f} < {CLEAR}"
    px = pad1.GetPosition().x
    sx, sy = sw2.GetPosition().x, sw2.GetPosition().y
    yn = MM(0, y).y
    pts = [pad1.GetPosition(), VECTOR2I(px, yn), VECTOR2I(sx, yn),
           VECTOR2I(sx, sy)]
    for a, b in zip(pts, pts[1:]):
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(a); t.SetEnd(b)
        t.SetWidth(NM(TRACK_RAIL)); t.SetLayer(pcbnew.B_Cu)
        t.SetNet(pad1.GetNet()); board.Add(t)
    print(f"vbat link: 3 fixed B.Cu tracks PAD1.1 -> SW1.2 under the west "
          f"blade (y {y}, slack {y - half - spine_top:.2f} spine / "
          f"{blade_bot - (y + half):.2f} blade)")


def add_r13_feed(board):
    """The VCC -> R13.1 feed, added AFTER the router (bounded hand segments
    for one named net). It cannot ride the DSN: the only approach to R13.1
    is the SW1.2<->SW1.3 blade gap (1.66 mm between clearance boundaries),
    where a 0.6 track keeps 0.26 of slack and the 0.8 rail width keeps
    0.06 -- why four router runs left the pad open -- and freerouting
    2.2.4 CANNOT be handed a wire below its net's class width: measured
    2026-07-31, a 0.6 VCC wire in the DSN hangs the session before its
    first log line as '(type route)' and throws StackOverflowError as
    '(type fix)'. Post-import the stub is plain KiCad copper and the DRC
    gate judges it like anything else. A 0.6 stub feeding one 10 k
    pull-up carries microamps; the >= 0.5 width law holds and route()
    reports the necking."""
    fps = {f.GetReference(): f for f in board.Footprints()}
    r13 = pad_by_num(fps["R13"], "1")
    assert r13.GetNetname() == "VCC"
    assert abs(r13.GetPosition().x - MM(31.09, 0).x) < 200000, \
        "R13 moved: re-audit the blade-gap feed"
    pts = [MM(29.43, 0.85), MM(29.43, 7.2), r13.GetPosition()]
    for a, b in zip(pts, pts[1:]):
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(a); t.SetEnd(b)
        t.SetWidth(NM(TRACK_SIG)); t.SetLayer(pcbnew.B_Cu)
        t.SetNet(r13.GetNet()); board.Add(t)
    print("r13 feed: 2 hand B.Cu segments through the blade gap (0.6, "
          "necked by measurement -- see docstring)")


# ------------------------------------------- unplated-hole DSN surgery ---
# Specctra padstacks are SHARED library objects: Round[A]Pad_2440 is worn by
# S1/S2 (back-only) AND by all twelve LEDs (dual-ok). Editing one in place
# would silently strip the LEDs' front rings too -- the same shared-object
# trap _pth() documents for LSET.AllCuMask(). So the surgery CLONES: a
# back-only padstack per shape, a private image per back-only component,
# and it repoints only that component's own place line.
_PIN_RE = re.compile(
    r'\(pin\s+("[^"]*"|[^\s()]+)((?:\s*\(rotate[^)]*\))?)\s+'
    r'(\S+)\s+(-?[\d.]+)\s+(-?[\d.]+)\)')
_PLACE_RE = re.compile(
    r'^[ \t]*\(place\s+(\S+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+'
    r'(front|back)\s+(-?[\d.]+).*\)[ \t]*\n', re.M)


def _sexp_end(txt, start):
    """Index just past the balanced s-expression that begins at `start`.
    Quoted tokens are skipped whole (image names carry ':' and spaces)."""
    depth, i = 0, start
    while True:
        c = txt[i]
        if c == '"':
            i = txt.index('"', i + 1)
        elif c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1


def _dsn_blocks(txt, kind):
    """{name: (start, end)} for every `(kind NAME ...)` block in the file."""
    out = {}
    for m in re.finditer(r'\(' + kind + r'\s+("[^"]*"|[^\s()]+)', txt):
        out[m.group(1)] = (m.start(), _sexp_end(txt, m.start()))
    return out


def _suffixed(name, suffix):
    base = name.strip('"') + suffix
    return f'"{base}"' if name.startswith('"') else base


def _pad_radius(ps_text):
    """Circumscribed radius of a padstack's copper, DSN units. REFUSES a
    shape it cannot model rather than guessing a keep-out that is too small
    (Article I): an under-sized keepout would let the router lay F.Cu
    straight across a front ring the operator can never solder."""
    r = 0.0
    for m in re.finditer(r'\(shape\s+\((\w+)\s+(\S+)\s+([^)]*)\)\)', ps_text):
        kind, v = m.group(1), [float(x) for x in m.group(3).split()]
        if kind == "circle":
            off = math.hypot(v[1], v[2]) if len(v) >= 3 else 0.0
            r = max(r, v[0] / 2.0 + off)
        elif kind == "rect":
            r = max(r, max(math.hypot(x, y) for x in v[0::2] for y in v[1::2]))
        else:
            raise AssertionError(f"padstack shape '{kind}' not modelled")
    assert r > 0, "padstack has no copper shape at all"
    return r


def _keepout(layer, cx, cy, r, n=16):
    pts = "  ".join(f"{cx + r * math.cos(2 * math.pi * k / n):.3f} "
                    f"{cy + r * math.sin(2 * math.pi * k / n):.3f}"
                    for k in range(n))
    return f'    (keepout "" (polygon {layer} 0  {pts}))\n'


def unplated_dsn_surgery(path):
    """Rewrite the exported DSN so every BACK_ONLY pin exists ONLY on B.Cu.

    Two edits per pin, and BOTH are needed:
      1. a back-only padstack clone -- the router cannot attach an F.Cu
         track to copper the DSN never declares, so it can no longer use
         these leads as layer bridges (the barrel it assumed does not
         exist);
      2. an F.Cu keepout over the ring -- the ring and its Ø1.0-1.8 bore
         are STILL THERE on the real board. Deleting the pad from the
         router's model without the keepout would invite an F.Cu track
         straight across a hole. The keepout is the ring's real radius plus
         the board clearance, which is exactly the obstacle a foreign-net
         pad would have been.
    Returns the number of pins operated on."""
    txt = open(path).read()
    pads, imgs = _dsn_blocks(txt, "padstack"), _dsn_blocks(txt, "image")
    comps = _dsn_blocks(txt, "component")
    place_of, comp_of = {}, {}
    for cname, (cs, ce) in comps.items():
        for m in _PLACE_RE.finditer(txt, cs, ce):
            place_of[m.group(1)] = m
            comp_of[m.group(1)] = cname
    edits, new_pads, new_imgs, new_comps, kos = [], {}, [], [], []
    pins_done = []
    for ref in UNPLATED_BACK_ONLY:
        assert ref in place_of, f"{ref} has no (place) in the DSN"
        m = place_of[ref]
        px, py, side, rot = (float(m.group(2)), float(m.group(3)),
                             m.group(4), float(m.group(5)))
        # the front ring is only "the F.Cu shape" while the body is on the
        # front; a flipped BACK_ONLY part would need the mirror image of
        # this whole argument, so refuse rather than model it wrong.
        assert side == "front", f"{ref} is placed {side}: BACK_ONLY assumes " \
            "the body sits on the FRONT face (see UNPLATED_BACK_ONLY)"
        iname = comp_of[ref]
        istart, iend = imgs[iname]
        ibody = txt[istart:iend]
        out, last = [], 0
        for pm in _PIN_RE.finditer(ibody):
            ps = pm.group(1)
            ps_s, ps_e = pads[ps]
            clone = _suffixed(ps, "_BACKONLY")
            if clone not in new_pads:
                body = txt[ps_s:ps_e]
                keep = [l for l in body.splitlines()
                        if "(shape" not in l or " B.Cu " in l]
                assert any("(shape" in l and " B.Cu " in l for l in keep), \
                    f"padstack {ps} has no B.Cu shape to keep"
                new_pads[clone] = ("\n".join(keep).replace(ps, clone, 1)
                                   + "\n")
            out.append(ibody[last:pm.start(1)]); out.append(clone)
            last = pm.end(1)
            # ring centre in board space: mirror-x for a back part, then
            # rotate (the transform KiCad's own Specctra writer uses).
            lx, ly = float(pm.group(4)), float(pm.group(5))
            if side == "back":
                lx = -lx
            a = math.radians(rot)
            gx = px + lx * math.cos(a) - ly * math.sin(a)
            gy = py + lx * math.sin(a) + ly * math.cos(a)
            kos.append(_keepout("F.Cu", gx, gy,
                                _pad_radius(txt[ps_s:ps_e]) + CLEAR * 1000))
            pins_done.append(f"{ref}-{pm.group(3)}")
        out.append(ibody[last:])
        newi = _suffixed(iname, f"::UNPLATED_{ref}")
        body = "".join(out)
        new_imgs.append(body.replace(iname, newi, 1) + "\n")
        new_comps.append(f"    (component {newi}\n{m.group(0)}    )\n")
    # A component shell with no (place) left is illegal Specctra, so a block
    # that loses ALL its places is dropped WHOLE -- and its place-line edits
    # are never queued, because two edits over the same span apply against
    # stale offsets and shred the file (bench-found while writing this).
    emptied = {comp_of[r] for r in UNPLATED_BACK_ONLY
               if not any(comp_of[o] == comp_of[r] and o not in
                          UNPLATED_BACK_ONLY for o in place_of)}
    for ref in UNPLATED_BACK_ONLY:
        if comp_of[ref] not in emptied:
            m = place_of[ref]
            edits.append((m.start(), m.end(), ""))   # drop just this place
    for cname in emptied:
        cs, ce = comps[cname]
        cs = txt.rfind("\n", 0, cs) + 1              # take the whole lines
        ce = txt.index("\n", ce) + 1
        edits.append((cs, ce, ""))
    anchor_ps = max(e for _, e in pads.values())
    anchor_im = max(e for _, e in imgs.values())
    anchor_co = max(e for _, e in comps.values())
    # ONLY the keepouts in (structure) are board-absolute; KiCad also emits
    # (keepout (circle ...)) INSIDE the MountingHole image, where a copy
    # rides every H1-H4 placement. Anchoring to the file's last keepout put
    # 13 absolute polygons inside that image -- caught by this comment's
    # existence, fixed by bounding the search to the structure block.
    st = txt.index("(structure")
    st_end = _sexp_end(txt, st)
    ko_anchor = max(_sexp_end(txt, m.start())
                    for m in re.finditer(r'\(keepout\b', txt[:st_end])
                    if m.start() > st)
    edits.append((anchor_ps, anchor_ps,
                  "\n    " + "    ".join(new_pads[k] for k in sorted(new_pads))))
    edits.append((anchor_im, anchor_im, "\n    " + "    ".join(new_imgs)))
    edits.append((anchor_co, anchor_co, "\n" + "".join(new_comps).rstrip("\n")))
    edits.append((ko_anchor, ko_anchor, "\n" + "".join(kos).rstrip("\n")))
    for s, e, rep in sorted(set(edits), key=lambda t: -t[0]):
        txt = txt[:s] + rep + txt[e:]
    open(path, "w").write(txt)
    _assert_surgery(path, pins_done)
    print(f"DSN unplated surgery: {len(pins_done)} back-only pin(s) "
          f"({', '.join(sorted(UNPLATED_BACK_ONLY))}) lost their F.Cu ring "
          f"in the router's model; {len(kos)} F.Cu keepout(s) added")
    return len(pins_done)


def _assert_surgery(path, pins_done):
    """The negative half of the surgery: prove it hit ONLY what it aimed at.
    A shared padstack quietly stripped from the LEDs would delete the very
    bridges this board's charlieplex crossings are built on."""
    txt = open(path).read()
    pads, imgs = _dsn_blocks(txt, "padstack"), _dsn_blocks(txt, "image")
    comps = _dsn_blocks(txt, "component")
    place_of = {}
    for cname, (cs, ce) in comps.items():
        for m in _PLACE_RE.finditer(txt, cs, ce):
            place_of[m.group(1)] = cname
    def front_ok(ref):
        s, e = imgs[place_of[ref]]
        return all("F.Cu" in txt[slice(*pads[pm.group(1)])]
                   for pm in _PIN_RE.finditer(txt, s, e))
    for ref in UNPLATED_BACK_ONLY:
        s, e = imgs[place_of[ref]]
        n = 0
        for pm in _PIN_RE.finditer(txt, s, e):
            body = txt[slice(*pads[pm.group(1)])]
            assert "F.Cu" not in body, f"{ref}-{pm.group(3)} kept an F.Cu shape"
            assert "B.Cu" in body, f"{ref}-{pm.group(3)} lost its B.Cu shape"
            n += 1
        assert n, f"{ref} image has no pins after surgery"
    for ref in UNPLATED_DUAL_OK:
        assert front_ok(ref), f"{ref} lost its F.Cu ring -- shared-padstack " \
            "collateral damage, the exact trap this clone dance avoids"
    assert len(pins_done) == sum(
        1 for ref in UNPLATED_BACK_ONLY
        for _ in _PIN_RE.finditer(txt, *imgs[place_of[ref]])), \
        "surgery pin count disagrees with the surgered file"


def export_dsn(board):
    """3b. One patch on the way out: KiCad's Specctra writer emits a fixed
    `(clearance 100 (type smd_smd))` -- 0.1 mm between SMD pads, a fab-house
    default that has nothing to do with a 0.2 mm vee cutting a 0.4 gap. Left
    alone the router would happily lay 0.1 mm gaps that the DRC gate then
    fails. Raising it to the board law is not weakening a check; it is
    stopping the export from handing the router a weaker one."""
    assert pcbnew.ExportSpecctraDSN(board, DSN), "Specctra export failed"
    txt = open(DSN).read()
    n = txt.count("(clearance 100 (type smd_smd))")
    txt = txt.replace("(clearance 100 (type smd_smd))",
                      f"(clearance {int(CLEAR * 1000)} (type smd_smd))")
    # Both GND planes are declared to the router. Measured alternatives,
    # 2026-07-31, same placement and netlist:
    #   both planes            9 vias, 4 open connections, back pour 9 frags
    #   front plane withheld  11 vias (OVER the 10 ceiling), 2 open
    #   no planes             11 vias, 7 open
    # Declaring both is the only variant that stays inside SPEC's via
    # ceiling, so it is the one this script ships. None of the three closes
    # the board -- see MATRIX.md, that finding belongs to the reviewer.
    # NOT injected: FreeRouting 2.2.4 reads (autoroute_settings) out of the
    # DSN in principle, and lowering via_costs from its default 50 is the
    # right lever for a board whose front face inside the ring is empty by
    # design. Measured 2026-07-31: ANY autoroute_settings block in the
    # structure makes this build load a board with 1 net and emit an empty
    # SES -- silently, with no parse error. The lever does not exist here,
    # so the escape geometry carries the whole job instead.
    open(DSN, "w").write(txt)
    print(f"DSN written, smd_smd clearance 0.1 -> {CLEAR} in {n} rule block(s)")
    protect_prerouted(DSN)
    unplated_dsn_surgery(DSN)


def protect_prerouted(path):
    """Every wire this file laid BEFORE the export is exported as PROTECTED,
    not as routable copper.

    THE DEFECT (measured 2026-07-31). KiCad's Specctra writer stamps every
    existing track `(type route)`, which in Specctra means "this is a
    suggestion, rip it up if you like". FreeRouting duly ripped 18 of our
    20 fixed wires (the SES echoed only 2 back unchanged) and then reported
    `U1-8 -> C2-1` as a connection it COULD NOT MAKE -- while the copper
    for it sat on the board the whole time, laid by add_vcc_pin_link().
    The build then failed a gate on a connection that physically exists.
    That is a paperwork lie in the same family as the stale .ses incident:
    the router was answering a question about copper it had deleted.

    `(type protect)` is the Specctra word for "keep this wire exactly where
    it is". Wires that are protected are never in the router's unrouted
    list, because they were never its job.

    WIDTH WARNING, inherited from add_r13_feed's bench notes: freerouting
    2.2.4 chokes on a fixed/protected wire NARROWER than its net class
    (StackOverflowError as `fix`, a pre-log hang as `route`). Every wire
    protected here is laid at its own class width -- 0.8 for the RAIL nets
    (/VBAT, VCC), 0.6 for signal and GND -- which is why the 0.6 VCC r13
    feed is still laid AFTER the router instead of here. Asserted below:
    if a future pre-route is necked, this refuses to protect it rather
    than handing the router a wire it will die on."""
    txt = open(path).read()
    ws = txt.index("(wiring")
    we = _sexp_end(txt, ws)
    body, n = txt[ws:we], txt[ws:we].count("(type route)")
    for m in re.finditer(r'\(wire \(path (\S+) (\d+)\b[^)]*\)\(net ([^)]*)\)',
                         body):
        w = int(m.group(2)) / 1000.0
        cls = TRACK_RAIL if m.group(3) in ("GND", "VCC", "/VBAT", "/VSW") \
            else TRACK_SIG
        # GND's class is RAIL but its necks are deliberately 0.6 stubs into
        # a plane, and a plane wire is not a class-width wire -- freerouting
        # only objects when a wire is thinner than the class of a net it
        # must ROUTE, and GND is routed as a plane. Signal nets have no such
        # excuse, so they are held to the class exactly.
        assert w >= (TRACK_SIG if m.group(3) == "GND" else cls) - 1e-9, \
            (f"pre-routed wire on {m.group(3)} is {w} mm, under its class "
             f"{cls} -- freerouting 2.2.4 dies on that; lay it after the "
             "router like add_r13_feed does")
    body = body.replace("(type route)", "(type protect)")
    open(path, "w").write(txt[:ws] + body + txt[we:])
    assert "(type route)" not in body, "a pre-route stayed routable"
    print(f"DSN pre-routes: {n} fixed wire(s) exported as (type protect) -- "
          "the router may not rip them, and may not report them unrouted")
    return n


def dsn_digest(path=None):
    """Identity of a DSN's CONTENT, not of its bytes.

    Measured 2026-07-31: two consecutive builds of the identical board emit
    non-identical orbit.dsn files. KiCad's Specctra writer walks pads and
    rule areas in a container order that is not stable across processes, so
    `(pins BZ1-1 C1-1 TP2-1 ...)` comes back as `(pins BZ1-1 C2-1 TP2-1
    ...)` and the M3 keepout blocks trade places. Nothing moved; the file
    just got shuffled.

    A raw sha256 would therefore refuse every honest rebuild, and a gate
    that cries wolf on every rebuild is a gate someone deletes. So the seal
    hashes the file's line multiset with each pin list internally sorted:
    insensitive to the shuffle, sensitive to content -- verified by moving
    one pad 1 um, which changes the digest. The known cost: a change that
    only PERMUTES identical lines between blocks would slip through. On a
    file whose lines are almost all unique coordinates that is a trade
    worth naming out loud rather than a hole worth pretending away."""
    lines = []
    for line in open(path or DSN):
        m = re.match(r'^(\s*\(pins )(.*)(\)\s*)$', line)
        if m:
            line = m.group(1) + " ".join(sorted(m.group(2).split())) + ")\n"
        lines.append(line)
    return hashlib.sha256("".join(sorted(lines)).encode()).hexdigest()


def parse_unrouted(text):
    """The router's OWN list of connections it gave up on. FreeRouting
    prints it once, after the pass log, and nothing used to read it --
    which is how R8-2/R10-2 came to look like a SILENT non-route
    (incident 2026-07-31, see route()'s docstring). Parsed, recorded and
    gated now, so the router can never again finish 'successfully' while
    naming connections it never made."""
    out, on = [], False
    for line in text.splitlines():
        if "could not be routed" in line:
            on = True
            continue
        if not on:
            continue
        m = re.match(r"\s+-\s+(\S+)\s+->\s+(\S+)\s*$", line)
        if m:
            out.append(f"{m.group(1)} -> {m.group(2)}")
        elif line.strip() and not re.match(r"\s+Net ", line):
            break
    return out


def run_freerouting():
    """3c. FreeRouting 2.2.4, headless. The committed orbit.ses IS the
    routing record: if it exists the router is SKIPPED, so a rebuild
    reproduces this exact copper. REROUTE=1 in the environment re-runs it
    (and the new .ses becomes the record, deliberately).

    THE RECORD IS NOW SEALED TO ITS DSN (incident 2026-07-31). export_dsn()
    rewrites orbit.dsn on EVERY build while the router only runs on
    REROUTE=1, so the two drifted apart silently: the .ses and
    freerouting.log on disk had been produced from a DIFFERENT dsn than the
    one beside them. A reviewer reading that log saw '4 unrouted, none on
    /L1' and concluded R8-2 / R10-2 were a silent router failure; re-running
    the router on the committed dsn reports '/L1 (4 unrouted connections):
    R10-2 -> R8-2 ...' out loud. Nothing was silent -- the paperwork was
    stale. orbit.route.json now pins the sha256 of the dsn each .ses came
    from, and a replay whose hash does not match REFUSES rather than
    presenting someone else's routing as this board's (Article I)."""
    if os.path.exists(SES) and not os.environ.get("REROUTE"):
        rec = json.load(open(ROUTE_REC)) if os.path.exists(ROUTE_REC) else {}
        assert rec.get("dsn_sha256") == dsn_digest(), (
            f"REFUSING to replay {os.path.basename(SES)}: it was routed from "
            f"a different orbit.dsn ({rec.get('dsn_sha256', 'no record')[:12]}"
            f" != {dsn_digest()[:12]}). The placement, rules or unplated "
            "surgery changed since. Re-run with REROUTE=1.")
        print(f"router SKIPPED: {os.path.basename(SES)} is the committed "
              f"routing record, sealed to this dsn "
              f"({rec['dsn_sha256'][:12]}); {len(rec.get('unrouted', []))} "
              "connection(s) the router could not make (set REROUTE=1)")
        return rec.get("unrouted", [])
    # -oit is OFF, measured twice (2026-07-31): the old comment here claimed
    # "-oit 0" forces all 100 passes, but the flag was never actually in the
    # command -- and when it WAS added, freerouting 2.2.4 finished routing
    # and then hung IDLE (43 min wall, 6 s CPU, no .ses ever written; the
    # session only writes its output on clean completion). The early-quit
    # behaviour the old note complained about is real but survivable; the
    # hang is not. Do not re-add the flag without bench-proving the jar.
    # -is random, measured 2026-07-31: on this DSN it closes more than
    # sequential does. The old note here claimed it was "fully
    # deterministic (8 consecutive sessions produced byte-identical .ses
    # files)" -- FALSIFIED 2026-07-31 by re-running this exact command on
    # this exact dsn: pass 21 vs pass 30, score 952.92 vs 979.16, a
    # different .ses. The stop rule is "no 0.5-point improvement since pass
    # N", and how many passes fit before that depends on wall clock, so run
    # length varies with machine load. Reproducibility on this board comes
    # from REPLAYING the committed .ses (and now from the dsn hash that
    # seals it), never from the router being deterministic.
    cmd = [FR_JAVA, "-Djava.awt.headless=true", "-jar", FR_JAR,
           "-de", DSN, "-do", SES, "-mp", "100", "-mt", "1",
           "-us", "global", "-is", "random"]
    print("running:", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE)
    log = os.path.join(HERE, "freerouting.log")
    open(log, "w").write(r.stdout + r.stderr)
    tail = [l for l in (r.stdout + r.stderr).splitlines() if l.strip()]
    for l in tail[-12:]:
        print("  fr|", l[:150])
    assert os.path.exists(SES), f"router produced no {SES}; see {log}"
    unrouted = parse_unrouted(r.stdout + r.stderr)
    json.dump({"dsn_sha256": dsn_digest(), "unrouted": unrouted,
               "jar": os.path.basename(FR_JAR), "cmd": cmd[3:]},
              open(ROUTE_REC, "w"), indent=1)
    print(f"routing record sealed to dsn {dsn_digest()[:12]}; the router "
          f"reports {len(unrouted)} connection(s) it could not make")
    for u in unrouted:
        print("   unrouted:", u)
    return unrouted


def import_ses(board):
    """3d. ImportSpecctraSES mutates the in-memory board; save + reload so
    the tracks are facts on disk before anything is asserted about them."""
    before = len(list(board.GetTracks()))
    assert pcbnew.ImportSpecctraSES(board, SES), "SES import failed"
    # The SES echoes the FIXED pre-routes (switch-leg links, GND necks,
    # VCC spine) back as new wires on top of the originals. Identical
    # copper twice is invisible to DRC but poisons every count and the
    # byte-stability contract, so exact duplicates are dropped.
    seen, dups = set(), []
    for t in board.GetTracks():
        if t.Type() != pcbnew.PCB_TRACE_T:
            continue
        a, b = t.GetStart(), t.GetEnd()
        key = (t.GetLayer(), t.GetWidth(), t.GetNetCode(),
               tuple(sorted([(a.x, a.y), (b.x, b.y)])))
        if key in seen:
            dups.append(t)
        else:
            seen.add(key)
    for t in dups:
        board.Delete(t)
    if dups:
        print(f"SES import: dropped {len(dups)} duplicate fixed-wire echoes")
    pcbnew.SaveBoard(BOARD, board)
    board = pcbnew.LoadBoard(BOARD)
    tracks = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_TRACE_T]
    vias = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_VIA_T]
    board.BuildConnectivity()
    print(f"SES import: {before} -> {len(tracks)} tracks, {len(vias)} vias, "
          f"{board.GetConnectivity().GetUnconnectedCount(True)} unconnected")
    assert tracks, "SES imported no tracks at all"
    return board


def convert_vias(board):
    """3e. Every imported PCB_VIA becomes an orbit:WireVia FOOTPRINT at the
    same place on the same net, and the via is deleted. A KiCad plated via
    is a lie on this machine -- there is no plating; a via is a hole, a
    piece of wire and two hand joints, and only a THT pad footprint models
    that (SPEC 'Via geometry'). Returns the ledger MATRIX.md publishes."""
    vias = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_VIA_T]
    ledger = {}
    for i, v in enumerate(sorted(vias, key=lambda v: (v.GetPosition().y,
                                                      v.GetPosition().x)), 1):
        ref = f"V{i}"
        fp = make_wire_via(board, ref)
        fp.SetValue("wire via")
        board.Add(fp)
        fp.SetPosition(v.GetPosition())
        fp.Pads()[0].SetNet(v.GetNet())
        p = v.GetPosition()
        ledger[ref] = (v.GetNetname(), p.x / 1e6 - OX, H - (p.y / 1e6 - OY))
        board.Delete(v)
    for ref, (net, x, y) in sorted(ledger.items(),
                                   key=lambda kv: int(kv[0][1:])):
        print(f"  {ref}: {net:10s} at ({x:6.2f}, {y:6.2f})")
    assert len(ledger) <= VIA_CEILING, (
        f"{len(ledger)} wire vias > ceiling {VIA_CEILING} (SPEC). STOP: "
        "permuting the ring->pair matrix is the sanctioned next move and it "
        "is the reviewer's decision, not this script's.")
    if len(ledger) > VIA_BUDGET:
        print(f"NOTE: {len(ledger)} vias is over the budget of {VIA_BUDGET} "
              f"but inside the ceiling of {VIA_CEILING}")
    return ledger


def route(board, fps, widths):
    """3b-3f: the AUTOROUTER pipeline. This board's copper is not drawn by
    hand -- it is the replayed product of the committed orbit.ses, sealed to
    the dsn it came from. Returns (board, ledger, unrouted)."""
    link_switch_legs(board, fps)
    add_vcc_spine(board, fps)
    add_vcc_pin_link(board, fps)
    add_vbat_link(board, fps)
    export_dsn(board)
    unrouted = run_freerouting()
    board = import_ses(board)
    add_r13_feed(board)
    ledger = convert_vias(board)
    board.BuildConnectivity()
    print("after via->WireVia conversion:",
          board.GetConnectivity().GetUnconnectedCount(True), "unconnected")
    # 3f: track widths. SPEC's table reads ">= 0.5 mm min; 0.6 signal / 0.8
    # rails" -- 0.5 is the LAW and 0.6/0.8 are the routing widths. The
    # router necks down to the pad width where a class track is wider than
    # the pad it lands on (U1's SOIC pads are 0.65), which is correct
    # practice and stays above the law. The law is asserted; the necking is
    # reported so it can never hide.
    narrow, worst = 0, (9e9, "")
    for t in board.GetTracks():
        if t.Type() != pcbnew.PCB_TRACE_T:
            continue
        w = t.GetWidth() / 1e6
        assert w >= 0.499, f"track on {t.GetNetname()} is {w:.3f} < 0.5 (SPEC)"
        if w < widths.get(t.GetNet().GetNetClassName(), TRACK_SIG) - 0.001:
            narrow += 1
            if w < worst[0]:
                worst = (w, t.GetNetname())
    if narrow:
        print(f"track widths: {narrow} segment(s) necked below class width "
              f"(pad-width landings); narrowest {worst[0]:.2f} on {worst[1]}")
    return board, ledger, unrouted


# ------------------------------------------------- the unplated-hole gate
def unplated_class(fp, pad):
    """Which barrel story does this THT pad get to tell? REFUSES to guess:
    a netted PTH pad this table does not name stops the build, because the
    only two honest answers are 'the operator can reach the front ring' and
    'the operator cannot', and nobody but a human knows which (Article I)."""
    ref = fp.GetReference()
    if fp.GetFPID().GetLibItemName() == "WireVia":
        return "bridge"                       # wire + two hand joints
    if ref in UNPLATED_BACK_ONLY:
        return "back"
    if ref in UNPLATED_DUAL_OK:
        return "dual"
    if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
        return "npth"                         # H1-H4: no copper at all
    if not pad.GetNetCode():
        return "floating"                     # G1-G4 gauges, netless by SPEC
    raise AssertionError(
        f"{ref}.{pad.GetNumber()} is a netted THT pad that UNPLATED_BACK_ONLY "
        "/ UNPLATED_DUAL_OK do not classify -- decide whether an iron can "
        "reach its FRONT ring and add it to the table; the gate will not "
        "guess a barrel that may not exist")


def _pad_snapshot(pad):
    """Everything _strip_front_ring() disturbs, restorable.
    LSET WARNING (the trap _pth() paid for): pcbnew.LSET(x) is the COPY
    constructor and is the only safe way to hold a layer set. Never
    AddLayer/RemoveLayer on LSET.AllCuMask() -- that is a shared static,
    and poisoning it once wrote copper-only THT pads onto a board that was
    then CUT."""
    return (pad.GetAttribute(), pcbnew.VECTOR2I(pad.GetDrillSize()),
            pcbnew.LSET(pad.GetLayerSet()))


def _pad_restore(pad, snap):
    attr, drill, lset = snap
    pad.SetAttribute(attr)
    pad.SetDrillSize(drill)
    pad.SetLayerSet(lset)


def _strip_front_ring(pad):
    """Delete a lead's FRONT ring from the model: the pad becomes B.Cu-only
    copper with no barrel and no bore.

    MEASURED 2026-07-31, and the reason this is not the one-line
    SetLayerSet(B.Cu) it looks like it should be: KiCad's connectivity
    engine treats EVERY through-hole pad as a plated barrel regardless of
    its layer set. Shrink a PTH pad to B.Cu and the file even serialises
    without F.Cu -- and the ratsnest still walks straight through it
    (10 unconnected, unchanged). Only when the pad stops being a PTH pad
    does KiCad answer the unplated question (14 unconnected, and the four
    extra opens are exactly the four fantasy joints: SW1.2, BZ1.1, S1.1,
    S2.1). That stubbornness IS the barrel lie, in KiCad's own code.

    The bore goes too, and that is deliberate: this scratch copy is a
    CONNECTIVITY model, not a geometry model. Geometry is judged where the
    geometry is real -- kicad-cli drc on orbit.kicad_pcb itself, with every
    ring and every hole present (real_drc(), run in the same build).
    Re-adding the bore here as an NPTH pad was tried and produced 17
    hole-clearance artifacts against the pad's own back ring: noise that
    would drown the one question this gate exists to ask."""
    keep = pcbnew.LSET()
    keep.AddLayer(pcbnew.B_Cu)
    keep.AddLayer(pcbnew.B_Mask)
    pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
    pad.SetDrillSize(VECTOR2I(0, 0))
    pad.SetLayerSet(keep)


def _unconnected(board):
    board.BuildConnectivity()
    return board.GetConnectivity().GetUnconnectedCount(True)


def unplated_model(board, dual=None):
    """Turn a board IN MEMORY into the unplated truth, and return the
    dual-solder list it needed to get there.

    Every BACK_ONLY front ring goes first: those leads can never be a
    bridge, full stop. Then each DUAL_OK front ring is removed too, in a
    fixed order, and put BACK only if its removal actually breaks a
    connection -- so the published list is the set of hand joints the
    routing DEPENDS on, computed from copper, never from intent. Pass
    `dual` to force a list instead of computing one.

    NEVER SaveBoard() the argument: callers hand this a scratch copy."""
    forced = None if dual is None else {tuple(d[:2]) for d in dual}
    for fp in board.Footprints():
        for pad in fp.Pads():
            if pad.GetAttribute() != pcbnew.PAD_ATTRIB_PTH:
                continue
            if unplated_class(fp, pad) == "back":
                _strip_front_ring(pad)
    n = _unconnected(board)
    need = []
    cand = sorted(((fp, pad) for fp in board.Footprints() for pad in fp.Pads()
                   if pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH
                   and unplated_class(fp, pad) == "dual" and pad.GetNetCode()),
                  key=lambda t: (t[0].GetReference(), t[1].GetNumber()))
    for fp, pad in cand:
        key = (fp.GetReference(), pad.GetNumber())
        if forced is not None:
            if key in forced:
                need.append((*key, pad.GetNetname()))
            else:
                _strip_front_ring(pad)
            continue
        before = _pad_snapshot(pad)
        _strip_front_ring(pad)
        after = _unconnected(board)
        if after > n:
            _pad_restore(pad, before)                # the net needs this joint
            need.append((*key, pad.GetNetname()))
            assert _unconnected(board) == n, "restoring a front ring changed " \
                "the connectivity count -- the greedy is not reversible"
        else:
            n = after
    return need


# ------------------------------------------- the normalized replay law ---
BUILD_REC = os.path.join(HERE, "orbit.build.json")


def _canon_poly(chain):
    """One filled-zone outline as a canonical vertex string.

    TWO normalisations, each for a measured reason and neither able to hide
    a millimetre of copper:
      COLLINEAR VERTICES ARE DROPPED. A point that lies exactly on the
      straight line between its neighbours is on the boundary either way --
      the polygon is the same set of points with or without it. KiCad's
      filler emits it or not depending on how Clipper happened to merge
      spans, so it wobbles between builds while the copper does not.
      THE START VERTEX IS ROTATED to the lexicographically smallest point.
      A closed ring has no first vertex; the file has to write one down.
      Winding order is PRESERVED -- reversing a ring turns an island into a
      hole, so that is a difference this digest must keep seeing."""
    pts = [(chain.CPoint(i).x, chain.CPoint(i).y)
           for i in range(chain.PointCount())]
    keep = []
    for i, p in enumerate(pts):
        a, c = pts[i - 1], pts[(i + 1) % len(pts)]
        # cross product of (p-a) and (c-p): zero == p adds no corner
        if (p[0] - a[0]) * (c[1] - p[1]) != (p[1] - a[1]) * (c[0] - p[0]):
            keep.append(p)
    if not keep:                       # degenerate ring: keep it verbatim
        keep = pts
    k = keep.index(min(keep))
    return ";".join(f"{x},{y}" for x, y in keep[k:] + keep[:k])


def board_digest(path=None):
    """Identity of the BOARD's geometry, not of its bytes.

    WHY NOT sha256 OF THE FILE. Measured 2026-07-31: two plain (replaying)
    builds of the identical board differ by 567 lines, and every one of
    them is a `(uuid ...)` -- KiCad mints a fresh UUID for every item it
    creates, on every build. The geometry is identical. A byte bar would
    therefore fail every honest rebuild, and this repo has already learned
    that lesson one layer down ("KiCad permutes D-codes; compare geometry,
    not bytes" -- DESIGN.md, the gerber bar).

    WHAT IT IGNORES, and why none of it can move cut copper:
      * UUIDs. A UUID is KiCad's database key. Nothing downstream reads it:
        the plotter rasterises shapes, the drill writer reads hole
        positions, the mill reads gerbers. It is never a coordinate.
      * ITEM ORDER in the file, and container order inside the board --
        every record below is sorted, because a board is a SET of copper
        features and the order KiCad walks them is an artefact of its
        containers (the same shuffle dsn_digest already documents).
      * COLLINEAR zone-fill vertices and each fill ring's starting vertex
        (_canon_poly). Same boundary, different bookkeeping.
    Everything a mill can see IS in here: every pad's position, size,
    drill, shape and layer set; every track's layer, width, net and
    endpoints; every zone's FILLED outline; every graphic and every piece
    of silk text. Verified by moving one pad 1 um -- the digest changes."""
    b = pcbnew.LoadBoard(path or BOARD)
    rec = []
    for fp in b.Footprints():
        rec.append("FP %s|%s|%s|%s|%d|%d"
                   % (fp.GetReference(), fp.GetFPID().Format(),
                      _xy(fp.GetPosition()), pcbnew.LayerName(fp.GetLayer()),
                      fp.GetOrientation().AsDegrees() * 1000,
                      fp.GetAttributes()))
        for p in fp.Pads():
            rec.append("PAD %s.%s|%s|%s|%s|%d|%dx%d|%dx%d|%d|%d"
                       % (fp.GetReference(), p.GetNumber(), p.GetNetname(),
                          _xy(p.GetPosition()), _layers(p.GetLayerSet()),
                          p.GetShape(), p.GetSize(pcbnew.F_Cu).x,
                          p.GetSize(pcbnew.F_Cu).y, p.GetDrillSize().x,
                          p.GetDrillSize().y, p.GetAttribute(),
                          p.GetLocalZoneConnection()))
    for t in b.GetTracks():
        ends = sorted([_xy(t.GetStart()), _xy(t.GetEnd())])
        rec.append("TRK %s|%s|%d|%s|%s"
                   % (t.GetNetname(), pcbnew.LayerName(t.GetLayer()),
                      t.GetWidth(), ends[0], ends[1]))
    for z in b.Zones():
        head = ("ZONE %s|%s|%s|%d|%d|%d"
                % (z.GetZoneName(), z.GetNetname(), _layers(z.GetLayerSet()),
                   z.GetIsRuleArea(), z.GetMinThickness(),
                   z.GetPadConnection() if not z.GetIsRuleArea() else -1))
        rings = []
        for layer in z.GetLayerSet().Seq():
            ps = z.GetFilledPolysList(layer)
            for i in range(ps.OutlineCount()):
                rings.append("%s/O%s" % (pcbnew.LayerName(layer),
                                         _canon_poly(ps.Outline(i))))
                for h in range(ps.HoleCount(i)):
                    rings.append("%s/H%s" % (pcbnew.LayerName(layer),
                                             _canon_poly(ps.Hole(i, h))))
        o = z.Outline()
        for i in range(o.OutlineCount()):
            rings.append("DRAWN%s" % _canon_poly(o.Outline(i)))
        rec.append(head + "|" + "|".join(sorted(rings)))
    for d in b.Drawings():
        if d.Type() == pcbnew.PCB_TEXT_T:
            rec.append("TXT %s|%s|%s|%dx%d|%d|%d|%d"
                       % (d.GetText(), pcbnew.LayerName(d.GetLayer()),
                          _xy(d.GetPosition()), d.GetTextWidth(),
                          d.GetTextHeight(), d.GetTextThickness(),
                          d.GetTextAngle().AsDegrees() * 1000, d.IsMirrored()))
        else:
            rec.append("SHP %s|%d|%s|%s|%s|%d"
                       % (pcbnew.LayerName(d.GetLayer()), d.GetShape(),
                          _xy(d.GetStart()), _xy(d.GetEnd()),
                          _xy(d.GetCenter()), d.GetWidth()))
    rec.sort()
    return hashlib.sha256("\n".join(rec).encode()).hexdigest(), len(rec)


def _xy(v):
    return f"{v.x},{v.y}"


def _layers(lset):
    return ",".join(sorted(pcbnew.LayerName(l) for l in lset.Seq()))


def replay_law(digest, n_items):
    """THE REPLAY LAW: the same script, the same schematic and the same
    committed .ses must produce the same BOARD, every time, for ever.

    It is enforced across builds rather than inside one: the three input
    hashes and the resulting board digest are recorded in orbit.build.json,
    and a later build that presents the SAME inputs and a DIFFERENT board
    is a gate failure -- something non-deterministic got into the geometry.
    When an input hash changes the record is re-baselined and says so out
    loud, because that is an intentional change, not a drift.

    So 'two plain builds agree' is the observable form of this law: build,
    build again, and the second one is the one that checks."""
    ins = {}
    for name, p in (("script", os.path.abspath(__file__)), ("sch", SCH),
                    ("ses", SES)):
        ins[name] = hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]
    old = json.load(open(BUILD_REC)) if os.path.exists(BUILD_REC) else {}
    json.dump({"inputs": ins, "board_digest": digest, "items": n_items},
              open(BUILD_REC, "w"), indent=1)
    if old.get("inputs") != ins:
        changed = [k for k in ins if old.get("inputs", {}).get(k) != ins[k]]
        print(f"replay law: RE-BASELINED, {'+'.join(changed) or 'first build'}"
              f" changed; board digest {digest[:12]} ({n_items} items)")
        return None
    if old.get("board_digest") != digest:
        return (f"REPLAY LAW BROKEN: identical script + schematic + .ses "
                f"produced a different board ({old['board_digest'][:12]} -> "
                f"{digest[:12]}). The geometry is not deterministic.")
    print(f"replay law: HELD, same inputs -> same board digest "
          f"{digest[:12]} ({n_items} items)")
    return None


def _kicad_drc(path, label, outdir=None):
    """kicad-cli's verdict on one board file. Returns (rc, violations,
    unconnected, report-dict). The json lands in a temp dir, never beside
    the board: a build artifact in the source tree is a build artifact
    someone eventually hand-edits."""
    rpt = os.path.join(outdir or tempfile.mkdtemp(prefix="orbit-drc-"),
                       f"drc-{label}.json")
    r = subprocess.run(["kicad-cli", "pcb", "drc", "--severity-error",
                        "--exit-code-violations", "--format", "json",
                        "-o", rpt, path], capture_output=True, text=True)
    d = json.load(open(rpt))
    return r.returncode, len(d["violations"]), len(d["unconnected_items"]), d


def real_drc():
    """KiCad's own DRC on the REAL board, every ring and bore present. This
    is the geometry authority (clearance, annular, holes, edges); the
    unplated gate deliberately does not model geometry. Its connectivity
    number is KiCad's PLATED count and is reported as exactly that -- the
    optimistic one, kept beside the unplated count so the difference between
    them is always visible."""
    rc, vio, un, _ = _kicad_drc(BOARD, "real")
    print(f"kicad DRC [real board, PLATED model]: exit {rc}, {vio} "
          f"violation(s), {un} unconnected item(s)")
    return rc, vio, un


def unplated_drc(dual=None, mutate=None, label="unplated"):
    """THE UNPLATED CONNECTIVITY GATE. Loads the SAVED board fresh, applies
    the unplated model to that scratch copy, saves the copy to a temp dir
    (honours $TMPDIR; the project + .kicad_dru travel with it so the gauge
    exemption still applies) and asks KiCad's OWN drc the question:

        with the front rings that nobody can solder deleted, is every net
        still connected?

    Stock KiCad answers honestly once the copper it is judging is the
    copper that will exist. Anything that was riding a barrel this process
    does not build now shows up as an unconnected item.

    `mutate(board)` runs on the copy BEFORE the model is applied -- that is
    the hook the negative control uses. Returns (rc, unconnected,
    violations, dual_solder_list)."""
    scratch = tempfile.mkdtemp(prefix=f"orbit-{label}-")
    copy = pcbnew.LoadBoard(BOARD)
    if mutate:
        mutate(copy)
    need = unplated_model(copy, dual)
    tmp = os.path.join(scratch, "orbit.kicad_pcb")
    pcbnew.SaveBoard(tmp, copy)                  # the COPY, never BOARD
    for ext in (".kicad_pro", ".kicad_dru"):
        src = os.path.join(HERE, "orbit" + ext)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(scratch, "orbit" + ext))
    rc, vio, un, d = _kicad_drc(tmp, label, scratch)
    print(f"unplated DRC [{label}]: exit {rc}, {vio} violation(s), "
          f"{un} unconnected item(s), {len(need)} dual-solder lead(s)")
    for u in d["unconnected_items"][:12]:
        print("   open:", " <-> ".join(i.get("description", "?")
                                       for i in u.get("items", [])))
    for v in d["violations"][:8]:
        print("   viol:", v.get("type"), v.get("description", "")[:90])
    return rc, un, vio, need


def assert_connected(board):
    """3f: every net fully routed. The 4 flip gauges are netless floating
    islands BY DESIGN (SPEC 'Deliberate exceptions'), so they are the only
    thing this count is allowed to forgive -- and being netless they never
    enter the ratsnest in the first place."""
    board.BuildConnectivity()
    conn = board.GetConnectivity()
    n = conn.GetUnconnectedCount(True)
    assert n == 0, f"{n} unconnected items remain after routing"
    print("connectivity: 0 unconnected")


def outline_chain(inset):
    """The board outline pulled in by `inset`, corner arcs included as 8
    chords each. One definition, so a pour edge cannot drift off the
    Edge.Cuts it is supposed to shadow."""
    r = CORNER_R - inset
    ch = pcbnew.SHAPE_LINE_CHAIN()
    for cx, cy, a0 in ((W - CORNER_R, CORNER_R, -90.0),
                       (W - CORNER_R, H - CORNER_R, 0.0),
                       (CORNER_R, H - CORNER_R, 90.0),
                       (CORNER_R, CORNER_R, 180.0)):
        for k in range(9):
            a = math.radians(a0 + k * 90.0 / 8)
            ch.Append(MM(cx + r * math.cos(a), cy + r * math.sin(a)))
    ch.SetClosed(True)
    return ch


def octagon_chain(cx, cy, r):
    ch = pcbnew.SHAPE_LINE_CHAIN()
    for k in range(8):
        a = math.pi / 8 + k * math.pi / 4
        ch.Append(MM(cx + r * math.cos(a) / math.cos(math.pi / 8),
                     cy + r * math.sin(a) / math.cos(math.pi / 8)))
    ch.SetClosed(True)
    return ch


def keepouts(board):
    """H1-H4 copper keep-out BOTH sides (SPEC 'Mounting'). Pads allowed, so
    the M3 bore's own zero-width annulus is not a violation; fills, tracks
    and vias are not -- a pour that reaches an M3 bore is shorted by the
    first washer anyone fits."""
    for ref, (x, y) in M3.items():
        for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
            ka = pcbnew.ZONE(board)
            ka.SetIsRuleArea(True)
            ka.SetZoneName(f"m3_keepout_{ref}")
            ka.SetDoNotAllowZoneFills(True)
            ka.SetDoNotAllowTracks(True)
            ka.SetDoNotAllowVias(True)
            ka.SetDoNotAllowPads(False)
            ka.SetLayer(layer)
            ka.Outline().AddOutline(octagon_chain(x, y, M3_KEEPOUT_R))
            board.Add(ka)


def pours(board, nets):
    """GND pour BOTH sides, >=0.5 fill channels, thermal spokes (never solid
    connects -- Board A's relayout law: gap 0.4 == the board clearance law so
    the iso ladder treats relief rings like every other gap, spoke 0.6 drawn
    -> >=0.52 after the 0.08 kerf overcut, 4 spokes at 45 deg because the
    diagonals miss radial track corridors more often).

    ORDER DEVIATION, declared: the task sheet puts pours() after route(),
    but the zones are CREATED here, before the Specctra export, so GND
    reaches the router as a PLANE. That is not a convenience -- SPEC's whole
    via budget rests on it ('GND never needs a dedicated via'), and GND's 12
    nodes are scattered over the entire back face. Routed as ordinary tracks
    they must cross the r=9.0 resistor arc, whose widest channel is 1.66 mm,
    and every crossing that fails buys a via out of a budget of six. The
    FILL still happens last, on the reloaded board, exactly as Board A does
    it -- ZONE_FILLER needs a project-attached board."""
    zones = {}
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        z = pcbnew.ZONE(board)
        z.SetLayer(layer)
        z.SetNet(nets["GND"])
        z.SetZoneName("gnd_front" if layer == pcbnew.F_Cu else "gnd_back")
        z.SetMinThickness(NM(0.5))
        z.SetLocalClearance(NM(CLEAR))
        z.SetThermalReliefGap(NM(0.4))
        z.SetThermalReliefSpokeWidth(NM(0.6))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
        z.Outline().AddOutline(outline_chain(0.6))
        board.Add(z)
        zones[layer] = z
    return zones


def fill_and_count(board):
    """Fill both pours and report fragments per side. SPEC: EXACTLY one
    filled_polygon block per side -- a fragmented pour costs a via, which is
    the thing this board is trying not to spend."""
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    counts = {}
    for z in board.Zones():
        if z.GetIsRuleArea():
            continue
        for layer in z.GetLayerSet().Seq():
            counts[pcbnew.LayerName(layer)] = z.GetFilledPolysList(layer).OutlineCount()
    return counts


TICK_R = 9.8            # cathode ticks: outer edge 9.925, cathode pad inner
                        # edge 10.35 -> 0.425 clear of that side's pads
SILK_H, SILK_W = 1.5, 0.25       # SPEC: text 1.5 mm, stroke 0.25 (Makera floor)


def _text(board, txt, x, y, layer, h=SILK_H, rot=0.0):
    t = pcbnew.PCB_TEXT(board)
    t.SetText(txt); t.SetPosition(MM(x, y)); t.SetLayer(layer)
    t.SetMirrored(layer == pcbnew.B_SilkS)   # each side readable ITS side up
    t.SetTextSize(VECTOR2I(NM(h * 0.85), NM(h)))
    t.SetTextThickness(NM(SILK_W)); t.SetTextAngle(deg(rot))
    board.Add(t)
    return t


def _seg(board, a, b, layer, w=SILK_W):
    s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
    s.SetStart(MM(*a)); s.SetEnd(MM(*b)); s.SetWidth(NM(w)); s.SetLayer(layer)
    board.Add(s)
    return s


def silk(board, fps):
    """FUNCTIONAL front legend (SPEC): this is the only thing that tells the
    operator which way twelve LEDs go in, so it is load-bearing, not
    decoration. 12 cathode ticks (CATHODE_INWARD, so every tick reads the
    same: the tick is on the INSIDE, at the cathode hole), marker arrow at
    pos 1, numerals at 12/3/6/9, CATCH/START/ON/+/-, and the board's name.
    Back: U1 pin-1 dot, Q1/Q2/D1 pin-1 marks, the six ISP labels + a pin-1
    square tick, 'SIDE B'."""
    F, B = pcbnew.F_SilkS, pcbnew.B_SilkS
    assert CATHODE_INWARD, "silk ticks below assume the inward cathode"
    for n in range(1, 13):
        a = ring_angle(n)
        # a tangential bar just INBOARD of the cathode hole: one rule for
        # all twelve, "the bar side is the flat/short lead side"
        t = a + 90.0
        c = polar(a, TICK_R)
        d = (0.8 * math.cos(math.radians(t)), 0.8 * math.sin(math.radians(t)))
        _seg(board, (c[0] - d[0], c[1] - d[1]), (c[0] + d[0], c[1] + d[1]), F)
    # marker arrow at pos 1: a chevron OUTSIDE the ring pointing inward
    ax, ay = polar(90.0, 16.6)
    for dx in (-1.3, 1.3):
        _seg(board, (ax + dx, ay + 1.3), (ax, ay), F)
    for lbl, ang in (("12", 90.0), ("3", 0.0), ("6", -90.0), ("9", 180.0)):
        _text(board, lbl, *polar(ang, 18.6 if ang == 90.0 else 17.6), F)
    _text(board, "ORBIT v1", RING_C[0], RING_C[1] + 1.6, F, h=2.0)
    _text(board, "2026-07-31", RING_C[0], RING_C[1] - 1.6, F)
    _text(board, "CATCH", POS["S1"][0], POS["S1"][1] + 4.8, F)
    _text(board, "START", POS["S2"][0], POS["S2"][1] - 4.8, F)
    _text(board, "ON", POS["SW1"][0], POS["SW1"][1] + 3.5, F)
    _text(board, "+", POS["PAD1"][0], POS["PAD1"][1] + 3.0, F)
    _text(board, "-", POS["PAD2"][0], POS["PAD2"][1] + 3.0, F)
    # ---- back marks
    u1 = fps["U1"]
    p1 = pad_by_num(u1, "1").GetPosition()
    dot = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_CIRCLE)
    ctr = u1.GetPosition()
    off = VECTOR2I((p1.x - ctr.x) * 13 // 10, (p1.y - ctr.y) * 13 // 10)
    dot.SetCenter(ctr + off); dot.SetEnd(ctr + off + VECTOR2I(NM(0.25), 0))
    dot.SetWidth(NM(0.25)); dot.SetFilled(True); dot.SetLayer(B)
    board.Add(dot)
    for ref in ("Q1", "Q2", "D1"):
        fp = fps[ref]
        c, q = fp.GetPosition(), pad_by_num(fps[ref], "1").GetPosition()
        v = VECTOR2I((q.x - c.x) * 17 // 10, (q.y - c.y) * 17 // 10)
        s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(c + v); s.SetEnd(q + v - VECTOR2I((q.x - c.x) // 2,
                                                     (q.y - c.y) // 2))
        s.SetWidth(NM(SILK_W)); s.SetLayer(B); board.Add(s)
    for ref, (cx, ry) in ISP_GRID.items():
        x = ISP_ORIGIN[0] + cx * ISP_PITCH
        y = ISP_ORIGIN[1] + ry * ISP_PITCH
        # label OUTBOARD of its column so it never sits on a neighbour pad
        _text(board, fps[ref].GetValue(), x + (2.1 if cx else -2.1), y, B,
              h=1.2)
    # pin-1 square tick beside TP1 (MISO) -- the ISP grid's pin 1
    tx = ISP_ORIGIN[0] + ISP_GRID["TP1"][0] * ISP_PITCH
    ty = ISP_ORIGIN[1] + ISP_GRID["TP1"][1] * ISP_PITCH + 1.7
    for a, b in (((-0.5, -0.5), (0.5, -0.5)), ((0.5, -0.5), (0.5, 0.5)),
                 ((0.5, 0.5), (-0.5, 0.5)), ((-0.5, 0.5), (-0.5, -0.5))):
        _seg(board, (tx + a[0], ty + a[1]), (tx + b[0], ty + b[1]), B)
    _text(board, "SIDE B", 28.0, 45.5, B)


def auto_label(board, layer, side_tag):
    """Reference labels on ONE side, positions computed by
    boards/silklabel.py -- never hand-placed (Board A, Bill 2026-07-30).
    Called once per side: each side's obstacles are its OWN mask apertures
    and its OWN existing silk, because each face is lasered in its own
    setup and can only clash with itself."""
    sys.path.insert(0, os.path.join(HERE, ".."))
    import silklabel as sl
    mask = pcbnew.F_Mask if layer == pcbnew.F_SilkS else pcbnew.B_Mask
    cu = pcbnew.F_Cu if layer == pcbnew.F_SilkS else pcbnew.B_Cu
    crt = pcbnew.F_CrtYd if layer == pcbnew.F_SilkS else pcbnew.B_CrtYd

    def rect(bb):
        return sl.Rect(bb.GetLeft() / 1e6, bb.GetTop() / 1e6,
                       bb.GetRight() / 1e6, bb.GetBottom() / 1e6)

    def text_wh(ref, rot):
        t = pcbnew.PCB_TEXT(board)
        t.SetText(ref); t.SetLayer(layer); t.SetMirrored(layer == pcbnew.B_SilkS)
        t.SetTextSize(VECTOR2I(NM(0.9), NM(1.0)))
        t.SetTextThickness(NM(0.16)); t.SetTextAngle(deg(rot))
        bb = t.GetBoundingBox()
        return (bb.GetWidth() / 1e6, bb.GetHeight() / 1e6)

    parts, apertures, silks, extra = [], [], [], []
    for fp in board.Footprints():
        ref = fp.GetReference()
        for pad in fp.Pads():
            if pad.IsOnLayer(mask):
                apertures.append(rect(pad.GetBoundingBox()))
        if ref.startswith("H"):
            continue                      # M3 mounts: nothing to name
        if not any(p.IsOnLayer(cu) for p in fp.Pads()):
            continue
        cy = fp.GetCourtyard(crt)
        if cy.OutlineCount() == 0:
            cy = fp.GetCourtyard(pcbnew.F_CrtYd if crt == pcbnew.B_CrtYd
                                 else pcbnew.B_CrtYd)
        body = rect(cy.BBox()) if cy.OutlineCount() \
            else rect(fp.GetBoundingBox(False))
        parts.append(sl.Part(ref, body, text_wh(ref, 0), text_wh(ref, 90)))
    for d in board.Drawings():
        if d.GetLayer() == layer:
            silks.append(rect(d.GetBoundingBox()))
    for z in board.Zones():
        if z.GetIsRuleArea() and z.GetZoneName().startswith("m3_keepout"):
            extra.append(rect(z.GetBoundingBox()))
    placed, unplaced = sl.place_labels(
        parts, sl.Rect(OX, OY, OX + W, OY + H), apertures, silks,
        bodies_extra=extra)
    for pl in placed:
        t = pcbnew.PCB_TEXT(board)
        t.SetText(pl.ref); t.SetLayer(layer)
        t.SetMirrored(layer == pcbnew.B_SilkS)
        t.SetTextSize(VECTOR2I(NM(0.9), NM(1.0)))
        t.SetTextThickness(NM(0.16)); t.SetTextAngle(deg(pl.rot))
        t.SetPosition(VECTOR2I(NM(pl.x), NM(pl.y)))
        board.Add(t)
    print(f"auto-label {side_tag}: {len(placed)}/{len(parts)} placed"
          + (f", UNPLACED: {unplaced}" if unplaced else ""))
    return placed, unplaced


DRU = '''(version 1)
# orbit.kicad_dru -- generated by tools-layout.py, do not hand-edit.
# The ONLY exemption on this board: the four flip gauges (SPEC "Deliberate
# exceptions", Decision Q13). Their 0.35 annulus is DECLARED at 0.3 so the
# gauge keeps 0.05 of real margin -- a gauge that fails its own check on a
# perfect flip gauges nothing. Confined to a named area per gauge; every
# other hole-centered pad on the board answers to the 0.7 law.
(rule "flip gauge annulus exception"
  (condition "A.insideArea('gauge_exception')")
  (constraint annular_width (min 0.3mm)))
(rule "THT annular ring, both sides"
  (condition "!A.insideArea('gauge_exception')")
  (constraint annular_width (min 0.7mm)))
(rule "copper to board edge"
  (constraint edge_clearance (min 0.4mm)))
(rule "silk clear of scrubbed apertures"
  (constraint silk_clearance (min 0.3mm)))
(rule "minimum through drill"
  (constraint hole_size (min 1.0mm)))
'''


def write_dru():
    """orbit.kicad_dru -- named rule areas confining the G1-G4 0.3-annulus
    exception; nothing else exempt (SPEC)."""
    path = os.path.join(HERE, "orbit.kicad_dru")
    open(path, "w").write(DRU)
    return path


def gauge_areas(board):
    """One named rule area per flip gauge -- the DRU's 0.3-annulus
    exemption is scoped to these and nothing else."""
    for ref, (x, y) in GAUGE.items():
        for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
            ra = pcbnew.ZONE(board)
            ra.SetIsRuleArea(True)
            ra.SetZoneName("gauge_exception")
            for f in ("SetDoNotAllowZoneFills", "SetDoNotAllowTracks",
                      "SetDoNotAllowVias", "SetDoNotAllowPads"):
                getattr(ra, f)(False)
            ra.SetLayer(layer)
            ra.Outline().AddOutline(octagon_chain(x, y, 1.4))
            board.Add(ra)


def matrix_published_dual():
    """The dual-solder list MATRIX.md currently publishes, so a build can
    say whether the assembly card it replaces was already right."""
    path = os.path.join(HERE, "MATRIX.md")
    if not os.path.exists(path):
        return None
    out, on = [], False
    for line in open(path):
        if line.startswith("## Dual-solder"):
            on = True
        elif on and line.startswith("## "):
            break
        elif on and line.startswith("| ") and "ref.pad" not in line \
                and not line.startswith("|---"):
            cell = [c.strip() for c in line.strip().strip("|").split("|")]
            ref, _, pad = cell[0].partition(".")
            out.append((ref, pad, cell[1]))
    return sorted(out)


def dual_solder(board):
    """SPEC lever 1: 'a THT lead soldered on both faces IS a via'. THIS
    BOARD HAS NO BARRELS, so that lever is the ONLY thing that makes a THT
    lead a layer change, and the list below is a list of hand joints the
    routing depends on -- not optional, not advisory.

    Computed by unplated_model() on a scratch copy of the ROUTED board:
    a DUAL_OK front ring is on the list exactly when deleting it breaks a
    connection KiCad can otherwise make. Front rings that merely happen to
    touch a same-net pour are NOT listed -- an assembly card that cries
    wolf on redundant joints is how a real one gets skipped. BACK_ONLY
    leads can never appear here: their front rings are gone before the
    question is asked, which is the whole point of the surgery."""
    scratch = pcbnew.LoadBoard(BOARD)     # never mutate the caller's board
    return sorted(unplated_model(scratch))


def negative_control():
    """ARTICLE III, applied to the unplated gate: a gate nobody has watched
    FAIL is a gate nobody should trust. tools-unplated-negative.py solders a
    fantasy -- an F.Cu stub on a SW1 blade, the one lead an iron provably
    cannot reach -- and demands that the unplated gate catch it AND that
    stock KiCad's plated model MISS it. It runs on every build; if it ever
    stops catching, this board's connectivity claims are worthless."""
    prog = os.path.join(HERE, "tools-unplated-negative.py")
    r = subprocess.run([sys.executable, prog], capture_output=True, text=True)
    for line in r.stdout.splitlines()[-6:]:
        print("  neg|", line)
    if r.returncode:
        print(r.stderr[-1500:])
    return r.returncode


def run_asserts(board, ledger):
    """Build-time laws (Board A precedent). Returns a list of GATE FAILURES
    rather than throwing on the first one: a board that fails must still be
    readable, and the reviewer needs the whole list, not the first item."""
    fail = []
    for fp in board.Footprints():
        ref = fp.GetReference()
        for pad in fp.Pads():
            if pad.GetAttribute() != pcbnew.PAD_ATTRIB_PTH:
                continue
            d = pad.GetDrillSize().x
            if d < NM(1.0) - 1 and pad.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH:
                fail.append(f"{ref}.{pad.GetNumber()} drill {d/1e6:.2f} < 1.0")
            for layer, name in ((pcbnew.F_Cu, "F"), (pcbnew.B_Cu, "B")):
                s = pad.GetSize(layer)
                ann = (min(s.x, s.y) - d) / 2e6
                floor = 0.299 if ref.startswith("G") else 0.699
                if ann < floor and d < NM(3.0):
                    fail.append(f"{ref}.{pad.GetNumber()} annular {name} "
                                f"{ann:.3f} < {floor}")
            if pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH and d < NM(3.0):
                for m, nm_ in ((pcbnew.F_Mask, "F.Mask"), (pcbnew.B_Mask, "B.Mask")):
                    if not pad.IsOnLayer(m) and not ref.startswith("G"):
                        fail.append(f"{ref}.{pad.GetNumber()} opens no {nm_}")
    if len(ledger) > VIA_CEILING:
        fail.append(f"{len(ledger)} wire vias > ceiling {VIA_CEILING}")
    for ref, (net, x, y) in ledger.items():           # SPEC via keep-outs
        if min(x, y, W - x, H - y) < 3.0:
            fail.append(f"{ref} is {min(x, y, W-x, H-y):.2f} from the edge (<3.0)")
    counts = {}
    for z in board.Zones():
        if z.GetIsRuleArea():
            continue
        for layer in z.GetLayerSet().Seq():
            counts[pcbnew.LayerName(layer)] = \
                z.GetFilledPolysList(layer).OutlineCount()
    for layer, n in sorted(counts.items()):
        if n != 1:
            fail.append(f"{layer} pour is {n} fragments, SPEC allows 1")
    board.BuildConnectivity()
    n = board.GetConnectivity().GetUnconnectedCount(True)
    if n:
        fail.append(f"{n} unconnected items, KiCad's PLATED model "
                    "(SPEC: every net fully routed)")
    return fail, counts


def edge_cuts(board):
    """56.0 x 48.0 with 2.0 corner radii: four straights + four quarter
    arcs. SPEC forbids anything fancier -- WS2 derives the raster window
    from outline coordinate words and refuses ink that escapes the endpoint
    extents, so a round board is out however much the game wants one."""
    r = CORNER_R
    for a, b in (((r, 0), (W - r, 0)), ((W, r), (W, H - r)),
                 ((W - r, H), (r, H)), ((0, H - r), (0, r))):
        e = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
        e.SetStart(MM(*a)); e.SetEnd(MM(*b))
        e.SetLayer(pcbnew.Edge_Cuts); e.SetWidth(NM(0.1)); board.Add(e)
    for cx, cy, a0 in ((W - r, r, -90.0), (W - r, H - r, 0.0),
                       (r, H - r, 90.0), (r, r, 180.0)):
        pt = lambda t: MM(cx + r * math.cos(math.radians(t)),
                          cy + r * math.sin(math.radians(t)))
        arc = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_ARC)
        arc.SetArcGeometry(pt(a0), pt(a0 + 45.0), pt(a0 + 90.0))
        arc.SetLayer(pcbnew.Edge_Cuts); arc.SetWidth(NM(0.1)); board.Add(arc)


def assign_nets(board, netnodes, fps):
    """The netlist assigns nets to pads; this file has no opinion about
    connectivity. Returns {name: NETINFO_ITEM}. 'unconnected-*' pseudo-nets
    are NOT created: SW1.3 is a genuinely dead blade (SPEC: pin 3 NC) and a
    one-node net would only make the router's completion figure lie."""
    nets = {}
    for name in netnodes:
        if name.startswith("unconnected-"):
            continue
        ni = pcbnew.NETINFO_ITEM(board, name)
        board.Add(ni)
        nets[name] = ni
    hit = 0
    for name, nodes in netnodes.items():
        if name not in nets:
            continue
        for ref, pin in nodes:
            found = False
            for pad in fps[ref].Pads():          # SW_PUSH_6mm repeats 1 and 2
                if pad.GetNumber() == pin:
                    pad.SetNet(nets[name]); found = True; hit += 1
            assert found, f"no pad {ref}.{pin} for net {name}"
    # SPEC BOM: the buzzer's RECT pad is the can's "+" and must be VCC.
    plus = [p for p in fps["BZ1"].Pads()
            if p.GetShape() == pcbnew.PAD_SHAPE_RECT]
    assert len(plus) == 1 and plus[0].GetNetname() == "VCC", \
        "BZ1 '+' (rect) pad is not VCC"
    for ref in GAUGE:                            # SPEC: floating by design
        for pad in fps[ref].Pads():
            assert pad.GetNetCode() == 0, f"{ref} must stay netless"
    print(f"nets: {len(nets)} nets, {hit} pad assignments")
    return nets


def board_settings(board):
    """Copper laws from the SPEC process table, as DRC settings. The
    net-class numbers (stage 3a) ride into the Specctra DSN, so the router
    inherits the same rules the DRC gate will judge it by."""
    b = board.GetDesignSettings()
    b.m_MinClearance = NM(CLEAR)
    b.m_TrackMinWidth = NM(0.5)          # SPEC: >=0.5 min, 0.6/0.8 by class
    b.m_MinThroughDrill = NM(1.0)        # the 0.8 corn cannot bore its own dia
    b.m_HoleToHoleMin = NM(0.5)
    b.m_HoleClearance = NM(CLEAR)
    b.m_CopperEdgeClearance = NM(CLEAR)
    b.m_MinSilkTextHeight = NM(1.0)
    b.m_SolderMaskExpansion = 0          # aperture == pad (Board A's scrub law)
    # 3a: the net classes ARE the router's rulebook. Specctra carries width,
    # clearance and the via padstack out of these, so the DSN geometry and
    # the DRC gate are the same numbers by construction -- a router that
    # invents its own via would hand back holes this board cannot drill.
    ns = b.m_NetSettings
    d = ns.GetDefaultNetclass()
    d.SetClearance(NM(CLEAR)); d.SetTrackWidth(NM(TRACK_SIG))
    d.SetViaDiameter(NM(VIA_PAD)); d.SetViaDrill(NM(VIA_HOLE))
    rail = pcbnew.NETCLASS("RAIL")
    rail.SetClearance(NM(CLEAR)); rail.SetTrackWidth(NM(TRACK_RAIL))
    rail.SetViaDiameter(NM(VIA_PAD)); rail.SetViaDrill(NM(VIA_HOLE))
    ns.SetNetclass("RAIL", rail)
    for pattern in ("GND", "VCC", "/VBAT", "/VSW"):
        ns.SetNetclassPatternAssignment(pattern, "RAIL")
    ns.RecomputeEffectiveNetclasses()
    return {"Default": TRACK_SIG, "RAIL": TRACK_RAIL}


def main():
    comps, netnodes = parse_netlist()
    board = pcbnew.CreateEmptyBoard()
    widths = board_settings(board)
    edge_cuts(board)
    fps = {}
    place_front_tht(board, comps, fps)
    place_back_smd(board, comps, fps)
    missing = set(comps) - set(fps)
    assert not missing, f"schematic parts never placed: {sorted(missing)}"
    for ref in ("LED%d" % n for n in range(1, 13)):
        fixup_tht(fps[ref])
    for ref in ("S1", "S2", "BZ1"):
        fixup_tht(fps[ref])
    nets = assign_nets(board, netnodes, fps)
    add_gnd_necks(board, fps)
    add_gnd_tails(board, fps)
    keepouts(board)
    pours(board, nets)
    pcbnew.SaveBoard(BOARD, board)
    board = pcbnew.LoadBoard(BOARD)          # reload: placement is now fact
    assert len(list(board.Footprints())) == len(fps), "reload lost footprints"
    fps = {f.GetReference(): f for f in board.Footprints()}
    nets = {n: board.FindNet(n) for n in nets}
    print(f"placed {len(fps)} footprints")
    counts = fill_and_count(board)
    print("pour fragments (pre-route):",
          ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    pcbnew.SaveBoard(BOARD, board)
    board, ledger, unrouted = route(board, fps, widths)
    fps = {f.GetReference(): f for f in board.Footprints()}
    # silk + gauge rule areas BEFORE the fill: rule areas move copper, silk
    # does not, but both must be on the board the asserts finally judge.
    gauge_areas(board)
    silk(board, fps)
    counts = fill_and_count(board)
    pcbnew.SaveBoard(BOARD, board)
    board = pcbnew.LoadBoard(BOARD)
    # labels last, on the POST-FILL board: they are silk-only and copper
    # can no longer move (Board A's ordering law).
    auto_label(board, pcbnew.F_SilkS, "front")
    auto_label(board, pcbnew.B_SilkS, "back")
    for fp in board.Footprints():          # silk = earned labels only
        for g in [g for g in fp.GraphicalItems()
                  if g.GetLayer() in (pcbnew.F_SilkS, pcbnew.B_SilkS)]:
            fp.Remove(g)
        for item, lay in ((fp.Reference(), pcbnew.F_Fab),
                          (fp.Value(), pcbnew.F_Fab)):
            item.SetLayer(lay); item.SetVisible(False)
    write_dru()
    pcbnew.SaveBoard(BOARD, board)
    board = pcbnew.LoadBoard(BOARD)        # final reload: judge what is ON DISK
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    fail, counts = run_asserts(board, ledger)
    pcbnew.SaveBoard(BOARD, board)
    patch_project()          # BEFORE the drc gates: they judge what is on
                             # disk, and rule severities live in the .kicad_pro
                             # this call fixes up. Still the last write of any
                             # kind -- nothing below may SaveBoard(BOARD).
    # --- the unplated truth, on the board that is now ON DISK -------------
    # Order matters: the gate loads the SAVED file, so it must run after the
    # last SaveBoard and it must never write one of its own.
    for u in unrouted:
        fail.append(f"router could not connect {u}")
    broke = replay_law(*board_digest())
    if broke:
        fail.append(broke)
    rrc, rvio, run_ = real_drc()
    if rvio:
        fail.append(f"kicad DRC on the real board: {rvio} violation(s)")
    rc, un, vio, ds = unplated_drc()
    if rc:
        fail.append(f"UNPLATED gate: kicad drc exit {rc} on the unplated "
                    f"model ({vio} violation(s), {un} unconnected item(s)) "
                    "-- copper that only connects through a barrel this "
                    "process does not build")
    was = matrix_published_dual()
    if was is not None and was != ds:
        print(f"dual-solder list CHANGED: MATRIX.md published {len(was)} "
              f"lead(s), the routed copper needs {len(ds)}; regenerating")
        for row in sorted(set(was) ^ set(ds)):
            print(f"   {'-' if row in was else '+'} {row[0]}.{row[1]} {row[2]}")
    print(f"dual-solder leads ({len(ds)}), each a REQUIRED front-face joint:")
    for r_, p_, n_ in ds:
        print(f"   {r_}.{p_:<3s} {n_}")
    if negative_control():
        fail.append("NEGATIVE CONTROL FAILED: the unplated gate did not "
                    "catch a deliberate F.Cu stub on a SW1 blade -- the "
                    "gate's teeth are unproven, so its PASS means nothing")
    write_matrix(board, ledger, counts, fail, ds, un)
    print("pour fragments:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"wire vias: {len(ledger)} (budget {VIA_BUDGET}, ceiling {VIA_CEILING})")
    print("saved", BOARD)
    if fail:
        print("\n=== GATE FAILURES (%d) ===" % len(fail))
        for f in fail:
            print("  FAIL:", f)
        sys.exit(1)
    print("all build-time gates PASS")


def patch_project():
    """Courtyard overlaps -> warning. Adjacent ring positions are 6.73 apart
    with 6.52 courtyards, so the 5 mm LED bodies keep 1.73 mm of real air
    while their pick-and-place envelopes touch at the 30 deg rotation; the
    radial resistor spokes do the same. Every COPPER clearance stays at
    error. Board A precedent, and its hard-won note applies: this must be
    the LAST write, because SaveBoard re-serialises the project file from
    the board's own settings and silently reverts an earlier patch."""
    import json
    pro = os.path.join(HERE, "orbit.kicad_pro")
    if not os.path.exists(pro):
        return
    p = json.load(open(pro))
    rs = (p.setdefault("board", {}).setdefault("design_settings", {})
           .setdefault("rule_severities", {}))
    rs["courtyards_overlap"] = "warning"
    rs["pth_inside_courtyard"] = "warning"
    open(pro, "w").write(json.dumps(p, indent=2) + "\n")


def write_matrix(board, ledger, counts, fail, ds=None, unplated_open=None):
    """MATRIX.md: the ring->pair table ACTUALLY used, the via ledger with a
    one-liner each, and the dual-solder list computed from routed copper."""
    ds = dual_solder(board) if ds is None else ds
    L = ["# ORBIT -- ring matrix, via ledger, dual-solder list", "",
         "Generated by `tools-layout.py`. Do not hand-edit.", "",
         "## Ring -> line pair (the table firmware transcribes)", "",
         f"Cathode orientation: **CATHODE_INWARD = {CATHODE_INWARD}** -- pin 1",
         "(K) on the inner lead circle r = %.2f, pin 2 (A) on the outer" % LEAD_R_IN,
         "r = %.2f. All twelve silk ticks read identically." % LEAD_R_OUT, "",
         "PERMUTED placement (Decision Q5, taken 2026-07-31): position n",
         "carries LED/R ref POS_REF[n], wiring frozen in the schematic.",
         "Cathode arcs sit at each line's own U1 pin side (L3 east, L0",
         "west, L1 north, L2 alternating -- derivation at POS_REF in",
         "tools-layout.py). Firmware transcribes THIS table.", "",
         "| pos | anode (HIGH) | cathode (LOW) | LED | R | package |",
         "|---|---|---|---|---|---|"]
    pkg = {1: "1206", 2: "0805", 0: "0603"}
    for n in range(1, 13):
        hi, lo = MATRIX[n]
        k = POS_REF[n]
        L.append(f"| {n} | L{hi} | L{lo} | LED{k} | R{k} | {pkg[k % 3]} |")
    L += ["", "## Wire via ledger", "",
          f"{len(ledger)} vias (SPEC budget {VIA_BUDGET}, hard ceiling "
          f"{VIA_CEILING}). Every via is Ø1.0 hole / Ø2.4 pad both sides,",
          "unplated, wire-stitched, soldered BOTH faces after reflow.", "",
          "| ref | net | x | y | why |", "|---|---|---|---|---|"]
    for ref in sorted(ledger, key=lambda r: int(r[1:])):
        net, x, y = ledger[ref]
        r = math.hypot(x - RING_C[0], y - RING_C[1])
        why = ("charlieplex crossing: the line changes sides inside the ring, "
               "where the FRONT is empty copper" if r < LEAD_R_IN else
               "layer change outside the ring, where B.Cu is fully committed")
        L.append(f"| {ref} | {net} | {x:.2f} | {y:.2f} | {why} |")
    L += ["", "## Dual-solder list (THT leads that ARE vias)", "",
          "THE HOLES ARE NOT PLATED. A THT lead's front ring and back ring",
          "are separate conductors; only an iron on BOTH faces joins them.",
          "Every lead below is a lead the routing DEPENDS on: solder it",
          "front and back or that net is open. Not optional.", "",
          "LED1-12 are seated ~1.5 mm PROUD of the board so an iron tip can",
          "reach the front ring at all -- that standoff is an assembly",
          "requirement, not a preference. SW1, BZ1, S1 and S2 can never",
          "appear here: their bodies sit on the front rings, so this build",
          "gives the router back-only pads and the gate deletes those",
          "front rings before asking KiCad what is connected.", ""]
    if ds:
        L += ["| ref.pad | net |", "|---|---|"]
        L += [f"| {r}.{p} | {n} |" for r, p, n in ds]
    else:
        L.append("_None: no THT pad carries an F.Cu track in this routing._")
    L += ["", "## Build state", "",
          "| item | value |", "|---|---|",
          f"| pour fragments | {', '.join(f'{k}={v}' for k, v in sorted(counts.items()))} |",
          f"| wire vias | {len(ledger)} |",
          f"| dual-solder leads | {len(ds)} |",
          f"| unconnected, UNPLATED model | {unplated_open} |",
          f"| gate failures | {len(fail)} |", ""]
    if fail:
        L += ["### OPEN ITEMS FOR THE ROUTING REVIEWER", ""]
        L += [f"- {f}" for f in fail]
        L += ["", "The unplated model is the one that counts: KiCad's own",
              "count assumes a plated barrel at every THT lead and this",
              "process builds none. Closing the board is the next work",
              "item; these gates only promise to tell the truth about it.",
              ""]
    open(os.path.join(HERE, "MATRIX.md"), "w").write("\n".join(L))


if __name__ == "__main__":
    main()
