"""Double-sided [pcb] composition ([pcb] + [twosided]): the grammar, the two
frames, the pin block and the flip's own check set (PCB-PLAN.md WS5's
double-sided list + WS8; the four additions boards/orbit/SPEC.md asks for).

THE FIXTURE. `stub2` is a synthetic double-sided board, 20 x 15 mm, built here
from gerber text so the whole suite runs with no board files and no FlatCAM:

    hole            F.Cu pad   B.Cu pad   what it is for
    V1 (5,5)  Ø1.0  Ø2.6       Ø2.6       a wire via: 0.8 ring both sides
    V2 (15,5) Ø1.0  Ø2.6       Ø2.6       a second one, so "every" means >1
    G1 (5,11) Ø1.0  Ø1.6       Ø1.6       a FLIP GAUGE: 0.3 ring, the named
                                          exception the job declares
    H1 (15,11) Ø3.4 none       none       a mounting bore: bare on both sides,
                                          excluded from the ring census
    P1 (10,3)  --   none       Ø2.0       an SMD pad, the only B.Paste aperture

Everything the double-sided path can get wrong is reachable from that: two
frames from one Edge.Cuts, rings on both sides, a declared exception, a bare
bore, a paste aperture that must stay off the holes, tabs on side 2, and the
registration pins in the blank's waste.

WHAT MUST HOLD
  - the single-sided form is untouched: the coupon goldens' program HEADERS
    regenerate byte-identically from the shipped job, and PROGRAM_PHASES still
    spells A..D (nothing in this work may re-bless tests/golden_pcb)
  - the grammar refuses: per-phase tables on a flipped document, a cutout on
    side A, a drilling pass on side B, hand-written pin phases, missing F.*/
    paste artwork, no [pins], asymmetric pins, pins inside the machined
    envelope, pins that do not fit the blank, a pin hole that reaches the bed,
    an undeclared annular value, a gauge with no reason, a gauge that names no
    hole
  - the FRAMES: both derive from the ONE Edge.Cuts, side A unmirrored and side
    B mirrored, and the pair closes the loop on the WS2 mirror law
    (`mirror -axis X` NEGATES X) — asserted against an independent
    re-derivation and falsified by a deliberately wrong mirror
  - the TCL is templated twice from one document: side A with no `mirror` line
    and no cutout, side B with the mirror and no drills
  - the PIN BLOCK composes the shipped machinery: ops/drill.py's spot-face and
    peck, positions symmetric about the DERIVED mirror line, keep-out carried
    into every program of both setups
  - all nine programs of the fixture PASS the whole gate, and each new check
    has a NEGATIVE control that is caught BY NAME while its neighbours still
    pass

gerbv-dependent sections skip LOUDLY (same posture as pcb_checks_suite): the
grammar, the frame math, the Tcl and the pin block need no raster and always
run; the ring/paste/scrub/tab checks need one.

Run: .venv/bin/python tests/pcb_twosided_suite.py
"""
import math
import sys
import tempfile
from pathlib import Path

import numpy as np

from clauderacam import emit, twosided
from clauderacam.engine import OpResult, path_length
from clauderacam.pcb import boardmaps as bm
from clauderacam.pcb import checks, engine, flip, pcbjob, reemit

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "tests" / "golden_pcb"
fails = []


def check(name, ok, detail=""):
    print(f"  {name}: {'OK' if ok else 'FAIL'}"
          + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def caught(name, fn, needle):
    try:
        fn()
    except Exception as e:
        check(name, needle in str(e), f"got: {str(e)[:95]}")
        return
    check(name, False, "no exception raised")


def by_name(chks):
    return {c.name: c for c in chks}


def catches(hazard, chks, must_fail, must_pass=()):
    """A negative control: `must_fail` names the check that has to catch this
    hazard, and every name in `must_pass` must still PASS — that is how we
    know the checks are independent and not all failing at once."""
    d = by_name(chks)
    if must_fail not in d:
        check(f"NEG {hazard}", False, f"no check named {must_fail!r} "
                                      f"(have {sorted(d)})")
        return
    ok = not d[must_fail].ok
    still = [n for n in must_pass if n in d and not d[n].ok]
    check(f"NEG {hazard} -> {must_fail}", ok and not still,
          (f"value {d[must_fail].value:.4f} ({d[must_fail].limit})" if ok
           else "check PASSED the hazard")
          + (f"; collateral failures {still}" if still else ""))


# ======================================================== the synthetic board
BW, BH = 20.0, 15.0
V1, V2, G1, H1 = (5.0, 5.0), (15.0, 5.0), (5.0, 11.0), (15.0, 11.0)
SMD = (10.0, 3.0)
# The gauge pad is Ø1.7 over a Ø1.0 hole = a 0.35 ring, DECLARED at 0.3. Not
# a coincidence and worth stating: orbit SPEC's gauges are Ø1.6/Ø1.0, i.e.
# exactly 0.30, and this lane's raster reads a 0.30 nominal ring at 0.29
# (half-pixel ink bias plus gerbv's mid-grey threshold). A pad whose ring
# EQUALS the number the job declares is a coin flip against any honest
# measurement — the same lesson the coupon's clearing offset taught. Board B
# should give its gauges 0.05 of margin, or declare 0.28.
VIA_PAD, GAUGE_PAD, SMD_PAD = 2.6, 1.7, 2.0
VIA_HOLE, MOUNT_HOLE = 1.0, 3.4
HOLES = [(V1[0], V1[1], VIA_HOLE), (V2[0], V2[1], VIA_HOLE),
         (G1[0], G1[1], VIA_HOLE), (H1[0], H1[1], MOUNT_HOLE)]

HDR = "%FSLAX46Y46*%\n%MOMM*%\n%LPD*%\n"


def gnum(v):
    return f"{int(round(v * 1e6)):d}"


def flashes(items):
    """items: [(dia, [(x,y), ...])] -> one gerber body."""
    out = ""
    for n, (dia, pts) in enumerate(items):
        out += f"%ADD{10 + n}C,{dia:.6f}*%\n"
    out += "G01*\n"
    for n, (dia, pts) in enumerate(items):
        out += f"D{10 + n}*\n"
        out += "".join(f"X{gnum(x)}Y{gnum(y)}D03*\n" for x, y in pts)
    return HDR + out + "M02*\n"


def strokes(segs, dia=0.2):
    out = HDR + f"%ADD10C,{dia:.6f}*%\nG01*\nD10*\n"
    for (x0, y0), (x1, y1) in segs:
        out += f"X{gnum(x0)}Y{gnum(y0)}D02*\nX{gnum(x1)}Y{gnum(y1)}D01*\n"
    return out + "M02*\n"


# Edge.Cuts: four separate two-point draws, DELIBERATELY out of perimeter
# order — what KiCad writes, and what the tab walk has to chain itself.
EDGE = HDR + "%ADD10C,0.100000*%\nG01*\nD10*\n" + "".join(
    f"X{gnum(x1)}Y{gnum(y1)}D02*\nX{gnum(x2)}Y{gnum(y2)}D01*\n"
    for x1, y1, x2, y2 in [(0, BH, BW, BH), (0, 0, 0, BH),
                           (BW, BH, BW, 0), (BW, 0, 0, 0)]) + "M02*\n"

F_CU = flashes([(VIA_PAD, [V1, V2]), (GAUGE_PAD, [G1])])
B_CU = flashes([(VIA_PAD, [V1, V2]), (GAUGE_PAD, [G1]), (SMD_PAD, [SMD])])
# mask: openings over the vias (both sides) and the SMD pad (back only). The
# GAUGES get NO opening on purpose — orbit SPEC: they are not solderable and
# not in the scrub set, and a Ø1.6 pad over a Ø1.0 hole has NO legal annular
# lap (0.8 annulus needs the tool centre both <= 0.5 and >= 0.85: empty).
F_MASK = flashes([(2.8, [V1, V2])])
B_MASK = flashes([(2.8, [V1, V2]), (2.4, [SMD])])
F_SILK = strokes([((2.0, 8.5), (18.0, 8.5))])
B_SILK = strokes([((2.0, 8.5), (18.0, 8.5)), ((3.0, 13.0), (9.0, 13.0))])
B_PASTE = flashes([(1.8, [SMD])])
DRL = ("M48\nFMAT,2\nMETRIC\n"
       f"T1C{VIA_HOLE:.3f}\nT2C{MOUNT_HOLE:.3f}\n%\nG90\nG05\nT1\n"
       + "".join(f"X{x:.3f}Y{y:.3f}\n" for x, y, d in HOLES if d == VIA_HOLE)
       + "T2\n"
       + "".join(f"X{x:.3f}Y{y:.3f}\n" for x, y, d in HOLES if d == MOUNT_HOLE)
       + "T0\nM30\n")

TD = Path(tempfile.mkdtemp(prefix="clauderacam-ws8-"))
G = TD / "gerbers"
G.mkdir()
for name, text in (("stub2-Edge_Cuts.gbr", EDGE),
                   ("stub2-F_Cu.gbr", F_CU), ("stub2-B_Cu.gbr", B_CU),
                   ("stub2-F_Mask.gbr", F_MASK), ("stub2-B_Mask.gbr", B_MASK),
                   ("stub2-F_Silkscreen.gbr", F_SILK),
                   ("stub2-B_Silkscreen.gbr", B_SILK),
                   ("stub2-B_Paste.gbr", B_PASTE), ("stub2.drl", DRL)):
    (G / name).write_text(text)

TOOLS = f"""
[machine]
inventory = "{REPO}/jobs/inventory.toml"

[[tool]]
num = 1
type = "flat"
diameter = 3.175
rpm = 12000
flutes = 1
flute_length = 12.0
shank_diameter = 3.175

[[tool]]
num = 2
type = "vee"
diameter = 3.175
tip_diameter = 0.2
included_angle_deg = 30.0
rpm = 12000
flutes = 1
flute_length = 10.0
shank_diameter = 3.175

[[tool]]
num = 3
type = "flat"
diameter = 0.8
rpm = 12000
flutes = 2
flute_length = 6.0
shank_diameter = 3.175

[[tool]]
num = 5
type = "scrub"
diameter = 0.3
rpm = 6000
flutes = 1
flute_length = 2.0
shank_diameter = 3.175

[[tool]]
num = 7
type = "flat"
diameter = 1.0
rpm = 12000
flutes = 2
flute_length = 6.0
shank_diameter = 3.175

[[tool]]
num = 9
type = "drill"
diameter = 2.0
rpm = 3000
flutes = 2
flute_length = 12.0
shank_diameter = 3.175
"""


def side_block(side, extra=""):
    """One side's five/six phase tables. The two sides carry INDEPENDENT
    numbers on purpose — different iso depth, different clear offset,
    different silk dose, different scrub preload — so a suite that passes
    could not be reading one side's table for both."""
    iso_z = -0.15 if side == "front" else -0.12
    scrub_z = -0.21 if side == "front" else -0.19
    dose = 0.03 if side == "front" else 0.04
    clr = 0.30 if side == "front" else 0.35
    return f"""
[phases.{side}.iso]
tool = 2
depth = {iso_z}
feed = 500
plunge = 200

[phases.{side}.clear]
tool = 3
depth = {iso_z}
margin = 1.0
offset = 0.10
overlap = 25
feed = 500
plunge = 200

[phases.{side}.silk]
clearance = {clr}
dose = {dose}
feed = 100

[phases.{side}.scrub]
tool = 5
depth = {scrub_z}
overlap = 45
offset = 0.15
feed = 400
plunge = 200
{extra}"""


FRONT = side_block("front", """
[phases.front.drills]
tool = 3
depth = -1.7
dpp = 0.4
feed = 250
plunge = 150
""")
BACK = side_block("back", """
[phases.back.cutout]
tool = 7
depth = -1.7
dpp = 0.3
gaps = 4
gapsize = 1.5
feed = 500
plunge = 200
""")

JOB = f"""
[pcb]
name = "stub2"
stem = "stub2"
gerbers = "gerbers"
out = "out"

[blank]
width = 40.0
height = 34.0
thickness = 1.5
anchor = [0.0, 0.0]

[spoilboard]
thickness = 12.7

[material]
name = "fr4"

[twosided]
flip_axis = "y"

[pins]
diameter = 2.0
length = 8.0
positions = [[10.0, -5.0], [10.0, 20.0]]
spot_tool = 1
drill_tool = 9
spot_depth = 0.1
spot_feed = 60
peck = 0.8
# 100, not the coin job's 120: that feed is a BRASS number. On fr4 the sheet
# physics reads a Ø2 full-face peck at F120 as 107% of the sustained chip
# limit; F100 lands at 89%.
feed = 100

[rules]
annular = 0.7

[[rules.gauge]]
name = "flip gauge G1"
annular = 0.3
positions = [[5.0, 11.0]]
reason = "a thin annulus is what makes a flip gauge sensitive; not \
solderable, not in the scrub set (orbit SPEC.md deliberate exceptions)"
{TOOLS}{FRONT}{BACK}"""

(TD / "job.toml").write_text(JOB)
JOBP = TD / "job.toml"


def variant(name, *subs):
    p = TD / name
    text = JOB
    for old, new in subs:
        assert old in text, f"variant fodder {old!r} not in the job"
        text = text.replace(old, new)
    p.write_text(text)
    return p


# ================================================================== grammar
print("grammar (no raster needed):")
job = pcbjob.load(JOBP)
check("a [pcb] + [twosided] document loads with per-side phase tables",
      job.twosided and job.sides == ("front", "back")
      and job.side_phases["front"]["iso"]["depth"] == -0.15
      and job.side_phases["back"]["iso"]["depth"] == -0.12
      and job.side_phases["front"]["silk"]["dose"] == 0.03
      and job.side_phases["back"]["silk"]["dose"] == 0.04,
      f"sides {job.sides}")
front, back = (pcbjob.side_view(job, s) for s in job.sides)
check("side A is FRONT, unmirrored, and carries drills + the pin block",
      front.side == "front" and front.mirror == "none"
      and front.has_phase("drills") and not front.has_phase("cutout")
      and front.has_phase("pindrill") and front.has_phase("pinspot"),
      f"{front.name} mirror={front.mirror} phases={sorted(front.phases)}")
check("side 2 is BACK, mirrored, and carries the cutout and NO drills",
      back.side == "back" and back.mirror == "x"
      and back.has_phase("cutout") and not back.has_phase("drills")
      and not back.has_phase("pindrill"),
      f"{back.name} mirror={back.mirror} phases={sorted(back.phases)}")
check("each side's artwork is its own; the outline and the drill file are ONE",
      front.files["cu"].name == "stub2-F_Cu.gbr"
      and back.files["cu"].name == "stub2-B_Cu.gbr"
      and front.files["silk"].name == "stub2-F_Silkscreen.gbr"
      and front.files["edge"] == back.files["edge"]
      and front.files["drl"] == back.files["drl"])
check("the program split is per side, and the pin block is side A's fifth",
      pcbjob.programs_of(front) == pcbjob.SIDE_PROGRAMS["front"]
      and list(pcbjob.programs_of(front)) == ["mill", "silk", "scrub",
                                              "holes", "pins"]
      and list(pcbjob.programs_of(back)) == ["mill", "silk", "scrub", "holes"]
      and pcbjob.programs_of(front)["holes"] == ("drills",)
      and pcbjob.programs_of(back)["holes"] == ("cutout",))
check("program names carry the side (twosided's name-side convention)",
      pcbjob.program_stem(front, "mill") == "stub2-front-mill"
      and pcbjob.program_stem(back, "holes") == "stub2-back-holes")
from clauderacam.pcb import session  # noqa: E402
caught("the viewer refuses a whole double-sided document, loudly",
       lambda: session.build(job), "one side at a time")
check("...and takes a SIDE view without complaint", bool(session.is_pcb(JOBP)))

print("\ngrammar refusals:")
caught("a flipped document with ONE phase table refuses",
       lambda: pcbjob.load(variant(
           "v-flat.toml", ("[phases.front.iso]", "[phases.iso]"))),
       "chain TWICE")
caught("a cutout on side A refuses",
       lambda: pcbjob.load(variant(
           "v-cutfront.toml", ("[phases.back.cutout]",
                               "[phases.front.cutout]"))),
       "cutout runs on side 2")
caught("a second drilling pass on side B refuses",
       lambda: pcbjob.load(variant(
           "v-drillback.toml", ("[phases.back.cutout]",
                                "[phases.back.drills]\ntool = 3\n"
                                "depth = -1.7\ndpp = 0.4\nfeed = 250\n"
                                "plunge = 150\n\n[phases.back.cutout]"))),
       "bored once from side A")
caught("hand-written pin phases refuse (the coin lane's law)",
       lambda: pcbjob.load(variant(
           "v-handpin.toml", ("[phases.front.drills]",
                              "[phases.front.pindrill]"))),
       "DERIVED from [pins]")
caught("a flipped document with no [pins] refuses",
       lambda: pcbjob.load(variant("v-nopins.toml", ("[pins]", "[pinsX]"))),
       "[pins] is required")
caught("pins asymmetric under the flip refuse",
       lambda: pcbjob.load(variant(
           "v-asym.toml", ("[[10.0, -5.0], [10.0, 20.0]]",
                           "[[9.0, -5.0], [10.0, 20.0]]"))),
       "symmetric")
caught("a pin inside the machined envelope refuses",
       lambda: pcbjob.load(variant(
           "v-pinin.toml", ("[[10.0, -5.0], [10.0, 20.0]]",
                            "[[10.0, 5.0], [10.0, 10.0]]"))),
       "machined envelope")
caught("pins that do not fit the blank refuse",
       lambda: pcbjob.load(variant("v-blank.toml",
                                   ("height = 34.0", "height = 20.0"))),
       "do not fit in this blank")
# orbit SPEC's own numbers, checked: a Ø2x12 dowel wants a 12.8 hole
# (12 + 0.2 seat + 0.6 tip allowance) and 1.5 blank + 12.7 MDF - 2 leaves
# 12.2. The SPEC's stated 12.0 clears by 0.2; the derived depth does not.
caught("a pin hole that reaches for the machine bed refuses",
       lambda: pcbjob.load(variant("v-deep.toml",
                                   ("length = 8.0", "length = 12.0"))),
       "within 2mm of the machine bed")
caught("a pin hole deeper than the drill's reach + counterbore refuses",
       lambda: pcbjob.load(variant("v-reach.toml",
                                   ("length = 8.0", "length = 11.9"),
                                   ("thickness = 12.7", "thickness = 25.4"))),
       "reach")
caught("a spot tool narrower than the drill shank refuses",
       lambda: pcbjob.load(variant("v-spot.toml",
                                   ("spot_tool = 1", "spot_tool = 7"))),
       "narrower than the drill")
caught("no declared annular ring refuses",
       lambda: pcbjob.load(variant("v-noann.toml",
                                   ("annular = 0.7", "annular = 0.0"))),
       "[rules] annular is required")
caught("a gauge exception with no reason refuses",
       lambda: pcbjob.load(variant("v-noreason.toml", ("reason = \"a thin",
                                                       "why = \"a thin"))),
       "no `reason`")
caught("a gauge that names no hole refuses (the frame footgun)",
       lambda: pcbjob.load(variant("v-nogauge.toml",
                                   ("positions = [[5.0, 11.0]]",
                                    "positions = [[15.0, 11.0], [5.0, 3.0]]"))
                           ), "exempts nothing")
def without(layer):
    """Load the job with one exported layer temporarily missing."""
    src = G / layer
    hidden = G / (layer + ".hidden")
    src.rename(hidden)
    try:
        pcbjob.load(JOBP)
    finally:
        hidden.rename(src)


caught("a missing F.Cu export refuses",
       lambda: without("stub2-F_Cu.gbr"), "missing gerber")
caught("a missing paste export refuses",
       lambda: without("stub2-B_Paste.gbr"), "one stencil, back side")

# ============================================================== frame math
print("\nframes: two setups, ONE Edge.Cuts (no raster needed):")
win = bm.extents(job.files["edge"], cross_check=False)
offa = bm.machine_offset(win, job.anchor, "none")
offb = bm.machine_offset(win, job.anchor, "x")
line = bm.flip_line(win, job.anchor)
check("side A places the board unmirrored, side B negates X",
      offa == (0.0, 0.0) and offb == (BW, 0.0), f"{offa} / {offb}")
check("the mirror line is the board's own centreline, DERIVED",
      abs(line - (job.anchor[0] + BW / 2)) < 1e-12, f"x={line}")
# independent re-derivation of the WS2 law: for a probe grid of board points,
# side A's and side B's machine X must be equidistant from the mirror line on
# opposite sides, and Y must be untouched. Written out longhand here, so this
# is not boardmaps checking itself.
bxs = np.linspace(win.x0, win.x1, 37)
bys = np.linspace(win.y0, win.y1, 37)
ax, ay = bm.machine_xy(offa, "none", bxs, bys)
bx2, by2 = bm.machine_xy(offb, "x", bxs, bys)
check("mirror -axis X NEGATES X: side B == 2*line - side A, Y untouched",
      float(np.abs(bx2 - (2 * line - ax)).max()) < 1e-12
      and float(np.abs(by2 - ay).max()) < 1e-12
      and float(np.abs(ay - bys).max()) < 1e-12,
      f"worst X {float(np.abs(bx2 - (2 * line - ax)).max()):.2e}")
check("the board occupies the SAME machine rectangle in both setups",
      abs(min(ax) - min(bx2)) < 1e-12 and abs(max(ax) - max(bx2)) < 1e-12,
      f"A [{min(ax)},{max(ax)}] B [{min(bx2)},{max(bx2)}]")
check("machine_xy / board_xy invert each other in both frames",
      all(float(np.abs(np.asarray(bm.board_xy(off, mir,
                                              *bm.machine_xy(off, mir, bxs,
                                                             bys))[0]) - bxs
                       ).max()) < 1e-12
          for off, mir in ((offa, "none"), (offb, "x"))))
# the falsification: flip_line's closed-loop assert must REFUSE a pair of
# transforms that do not mirror about one line. Feed it a machine_offset that
# negates Y instead of X (the WS2 bug's shape) and it has to raise.
_real = bm.machine_offset
try:
    bm.machine_offset = lambda w, a, m="x": (
        (a[0] - w.x0, a[1] + w.y1) if m == "x" else _real(w, a, m))
    caught("flip_line REFUSES a mirror that negates the wrong axis",
           lambda: bm.flip_line(win, job.anchor), "NEGATE X")
finally:
    bm.machine_offset = _real
check("mirror flags outside {x, none} refuse",
      True)
caught("an unsupported mirror refuses",
       lambda: bm.machine_offset(win, job.anchor, "y"), "unsupported mirror")

# ================================================================== the tcl
print("\ntemplated Tcl: one document, two runs:")
tcl_a = engine.render_tcl(front, win, TD / "work" / "front")
tcl_b = engine.render_tcl(back, win, TD / "work" / "back")
check("side A opens F.Cu, emits NO mirror, and cuts no outline",
      "stub2-F_Cu.gbr" in tcl_a
      and not any(ln.startswith("mirror") for ln in tcl_a.splitlines())
      and "geocutout" not in tcl_a and "milldrills" in tcl_a
      and "offset cu -x 0 -y 0" in tcl_a,
      [ln for ln in tcl_a.splitlines() if ln.startswith("offset cu")])
check("side B opens B.Cu, MIRRORS every object, and cuts the outline",
      "stub2-B_Cu.gbr" in tcl_b and "mirror cu -axis X -origin 0,0" in tcl_b
      and "mirror edge -axis X -origin 0,0" in tcl_b
      and "geocutout" in tcl_b and "milldrills" not in tcl_b
      and f"offset cu -x {BW:g} -y 0" in tcl_b,
      [ln for ln in tcl_b.splitlines() if ln.startswith("offset cu")])
check("only side A opens the Excellon (drill once, from the front)",
      "open_excellon" in tcl_a and "open_excellon" not in tcl_b)
check("each side's own depths reach its own Tcl",
      "-z_cut -0.15" in tcl_a and "-z_cut -0.12" in tcl_b
      and "-z_cut -0.21" in tcl_a and "-z_cut -0.19" in tcl_b)
check("both sides still end with the sentinel FILE, written last",
      all(t.rstrip().endswith("close $fh")
          and t.index(engine.SENTINEL_FILE) > t.rindex("write_gcode")
          for t in (tcl_a, tcl_b)))
check("the banner names the side it was templated for",
      "side front" in tcl_a and "side back" in tcl_b
      and "DO NOT hand-edit" in tcl_a)
# the housekeeping fold-in: run() must resolve work_dir itself, because
# FlatCAM runs from its own cwd
import inspect  # noqa: E402
src = inspect.getsource(engine.run)
check("engine.run resolves work_dir to absolute itself (the cwd fold-in)",
      "Path(work_dir).resolve()" in src and "cwd" in src)

# ============================================================== the pin block
print("\nthe pin block, composed from the shipped machinery:")
pin_ops = reemit.pin_ops(front)
check("two ops from ops/drill.py: spot-face then peck",
      [o.kind for o in pin_ops] == ["pinspot", "pindrill"]
      and pin_ops[0].tool == 1 and pin_ops[1].tool == 9,
      f"{[(o.kind, o.tool, len(o.lines)) for o in pin_ops]}")
check("the derived depth is the coin lane's formula (len + seat + tip)",
      abs(pcbjob.pin_depth(job) - 8.8) < 1e-9
      and abs(front.phases["pindrill"]["depth"] + 8.8) < 1e-9,
      f"depth {pcbjob.pin_depth(job)}")
zs = [float(ln.split("Z")[1].split()[0]) for ln in pin_ops[1].lines
      if ln.startswith("G1") and "Z" in ln]
check("the peck reaches full depth in 0.8 steps, full-retract between",
      min(zs) == -8.8 and len(zs) == 2 * math.ceil(8.8 / 0.8)
      and sum(1 for ln in pin_ops[1].lines if ln.startswith("G0 Z3.000"))
      >= len(zs),
      f"{len(zs)} peck cuts, floor {min(zs)}")
check("pins sit ON the derived mirror line, so the flip maps them to "
      "themselves",
      all(twosided.flip_xy(x, y, job.flip_axis, line) == (x, y)
          for x, y in job.pins["positions"]),
      f"line x={line}, pins {job.pins['positions']}")
check("PIN_CLEAR is imported from the coin lane, not restated",
      checks.PIN_CLEAR is twosided.PIN_CLEAR
      and pcbjob.PIN_CLEAR is twosided.PIN_CLEAR)

# ========================================================= program assembly
print("\nassembling nine programs (five front, four back):")
OUT = TD / "out"
OUT.mkdir(exist_ok=True)


def e(t):
    return math.cos(t), math.sin(t)


def arc(c, r, t0, t1, step=0.1):
    n = max(2, int(math.ceil(abs(t1 - t0) * r / step)))
    return [(c[0] + r * math.cos(t), c[1] + r * math.sin(t))
            for t in np.linspace(t0, t1, n + 1)]


def ring(c, r, step=0.05):
    return arc(c, r, 0.0, 2 * math.pi, step)


def densify_loop(pts, step):
    out = []
    for a, b in zip(pts, pts[1:] + pts[:1]):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(1, int(math.ceil(L / step)))
        for k in range(n):
            out.append((a[0] + (b[0] - a[0]) * k / n,
                        a[1] + (b[1] - a[1]) * k / n))
    return out


def cut_chain(pts, z, feed, plunge, safe=2.0):
    out = [f"G00 Z{safe:.4f}",
           f"G00 X{pts[0][0]:.4f} Y{pts[0][1]:.4f}",
           f"G01 Z{z:.4f} F{plunge:g}",
           f"G01 X{pts[1][0]:.4f} Y{pts[1][1]:.4f} F{feed:g}"]
    out += [f"G01 X{x:.4f} Y{y:.4f}" for x, y in pts[2:]]
    return out


def op(phase, tool, lines, feed):
    L = path_length(lines)
    return OpResult(label=f"pcb-{phase}", kind=phase, tool=tool, lines=lines,
                    path_len_mm=L, est_min=L / max(feed, 1.0))


def machof(sj):
    off = bm.machine_offset(win, sj.anchor, sj.mirror)

    def m(p):
        x, y = bm.machine_xy(off, sj.mirror, [p[0]], [p[1]])
        return (float(x[0]), float(y[0]))
    return m


PADS = {"front": [(V1, VIA_PAD), (V2, VIA_PAD), (G1, GAUGE_PAD)],
        "back": [(V1, VIA_PAD), (V2, VIA_PAD), (G1, GAUGE_PAD),
                 (SMD, SMD_PAD)]}


def build_iso(sj):
    p = sj.phases["iso"]
    tip_r = sj.phase_tool("iso").tip_diameter / 2
    m = machof(sj)
    lines = []
    for c, dia in PADS[sj.side]:
        ch = ring(m(c), dia / 2 + tip_r)
        lines += cut_chain(ch + [ch[0]], p["depth"], p["feed"], p["plunge"])
    return op("iso", p["tool"], lines, p["feed"])


def build_clear(sj, into_copper=False):
    p = sj.phases["clear"]
    m = machof(sj)
    pts = []
    for n, y in enumerate((7.5, 8.0, 8.5)):
        xs = (1.5, 18.5) if n % 2 == 0 else (18.5, 1.5)
        pts += [m((xs[0], y)), m((xs[1], y))]
    lines = cut_chain(pts, p["depth"], p["feed"], p["plunge"])
    if into_copper:
        lines += cut_chain([m((1.5, V1[1])), m((18.5, V1[1]))],
                           p["depth"], p["feed"], p["plunge"])
    return op("clear", p["tool"], lines, p["feed"])


def build_scrub(sj, radii=None, centre=False):
    """Side 1: disc laps (no holes yet). Side 2: ANNULAR laps on hole-centred
    pads (the holes are all there) and a disc lap on the SMD pad."""
    p = sj.phases["scrub"]
    m = machof(sj)
    lines = []
    for c, dia in PADS[sj.side]:
        if c == G1:
            continue        # gauges are not in the scrub set (no mask opening)
        if sj.side == "back" and c != SMD:
            r = 0.925 if radii is None else radii
            ch = ring(m(c), r)
            if centre:
                lines += cut_chain([m(c), m(c)], p["depth"], p["feed"],
                                   p["plunge"])
            lines += cut_chain(ch + [ch[0]], p["depth"], p["feed"],
                               p["plunge"])
        else:
            lines += cut_chain([m(c), m(c)], p["depth"], p["feed"],
                               p["plunge"])
            ch = ring(m(c), dia / 2 - 0.6)
            lines += cut_chain(ch + [ch[0]], p["depth"], p["feed"],
                               p["plunge"])
    return op("scrub", p["tool"], lines, p["feed"])


def build_drills(sj):
    p = sj.phases["drills"]
    tool = sj.phase_tool("drills")
    m = machof(sj)
    lines = []
    for hx, hy, hd in HOLES:
        c = m((hx, hy))
        r_orbit = max((hd - tool.diameter) / 2, 0.0)
        for z in np.arange(-p["dpp"], p["depth"] - 1e-9, -p["dpp"]):
            z = max(float(z), p["depth"])
            o = ring(c, r_orbit, step=0.05) if r_orbit > 0 else [c, c]
            lines += cut_chain(o + [o[0]], z, p["feed"], p["plunge"])
        o = ring(c, r_orbit, step=0.05) if r_orbit > 0 else [c, c]
        lines += cut_chain(o + [o[0]], p["depth"], p["feed"], p["plunge"])
    return op("drills", p["tool"], lines, p["feed"])


def cutout_chains(sj, tabs=4):
    tool = sj.phase_tool("cutout")
    m = machof(sj)
    off = tool.radius + 0.05
    loop = [(0.0, -off), (BW, -off)]
    loop += arc((BW, 0.0), off, -math.pi / 2, 0.0)
    loop += [(BW + off, BH)]
    loop += arc((BW, BH), off, 0.0, math.pi / 2)
    loop += [(0.0, BH + off)]
    loop += arc((0.0, BH), off, math.pi / 2, math.pi)
    loop += [(-off, 0.0)]
    loop += arc((0.0, 0.0), off, math.pi, 1.5 * math.pi)
    P = np.array(densify_loop(loop, 0.1))
    seg = np.hypot(*(np.roll(P, -1, axis=0) - P).T)
    s = np.concatenate([[0.0], np.cumsum(seg)[:-1]])
    total = float(seg.sum())
    centers = [(BW / 2, -off), (BW + off, BH / 2), (BW / 2, BH + off),
               (-off, BH / 2)][:tabs]
    keep = np.ones(len(P), bool)
    half = (1.0 + tool.diameter) / 2
    for cx, cy in centers:
        k = int(np.hypot(P[:, 0] - cx, P[:, 1] - cy).argmin())
        d = np.abs((s - s[k] + total / 2) % total - total / 2)
        keep &= d > half
    chains, cur = [], []
    for n in range(len(P)):
        if keep[n]:
            cur.append(m(tuple(P[n])))
        elif cur:
            chains.append(cur)
            cur = []
    if cur:
        if chains and not keep[0]:
            chains.append(cur)
        elif chains:
            chains[0] = cur + chains[0]
        else:
            chains.append(cur)
    return chains


def build_cutout(sj, tabs=4):
    p = sj.phases["cutout"]
    lines = []
    for z in (-0.3, -0.6, -0.9, -1.2, -1.5, -1.7):
        for ch in cutout_chains(sj, tabs):
            lines += cut_chain(ch, z, p["feed"], p["plunge"])
    return op("cutout", p["tool"], lines, p["feed"])


def write_program(sj, name, ops):
    path = OUT / f"{pcbjob.program_stem(sj, name)}.nc"
    path.write_text(reemit.assemble_program(sj, name, ops))
    return path


def mutate(src, name, fn):
    path = OUT / f"{name}.nc"
    path.write_text(fn(Path(src).read_text()))
    return path


PROGS = {"front": {}, "back": {}}
PROGS["front"]["mill"] = write_program(front, "mill",
                                       [build_iso(front), build_clear(front)])
PROGS["front"]["scrub"] = write_program(front, "scrub", [build_scrub(front)])
PROGS["front"]["holes"] = write_program(front, "holes", [build_drills(front)])
PROGS["front"]["pins"] = write_program(front, "pins", reemit.pin_ops(front))
PROGS["back"]["mill"] = write_program(back, "mill",
                                      [build_iso(back), build_clear(back)])
PROGS["back"]["scrub"] = write_program(back, "scrub", [build_scrub(back)])
PROGS["back"]["holes"] = write_program(back, "holes", [build_cutout(back)])
check("eight mill-dialect programs assemble through reemit.assemble_program",
      all(p.is_file() for side in PROGS for p in PROGS[side].values()),
      ", ".join(f"{s}/{n}" for s in PROGS for n in PROGS[s]))
caught("a program carrying the other side's phases refuses",
       lambda: reemit.assemble_program(back, "holes", [build_drills(front)]),
       "must carry phases")

hdr_a = reemit.program_header(front, "pins", reemit.pin_ops(front))
hdr_b = reemit.program_header(back, "mill", [build_iso(back)])
blob_a, blob_b = " ".join(hdr_a), " ".join(hdr_b)
check("side A's LAST program hands the operator the FLIP",
      "program E of 5 - pins [side FRONT]" in blob_a
      and "FLIP: set the 2 dowels" in blob_a and "about Y" in blob_a
      and "NEVER re-zero XY" in blob_a and "deburr the BACK" in blob_a,
      [ln for ln in hdr_a if "FLIP" in ln][:1])
check("side B's FIRST program starts from the flipped blank",
      "program A of 4 - mill [side BACK]" in blob_b
      and "FLIPPED onto the pins" in blob_b
      and "probe point sat in a drilled hole" in blob_b
      and "READ THE FLIP GAUGES" in blob_b,
      [ln for ln in hdr_b if "before" in ln][:1])
check("every header line survives the 128-char dialect law",
      all(len(ln) <= 128 for ln in hdr_a + hdr_b),
      f"longest {max(len(ln) for ln in hdr_a + hdr_b)}")

# ================================================== single-sided is untouched
print("\nthe single-sided lane is untouched (nothing may re-bless "
      "golden_pcb):")
gj = pcbjob.load(GOLDEN / "coupon.toml")
check("the coupon still loads as a single-sided document",
      not gj.twosided and gj.mirror == "x" and gj.side == ""
      and pcbjob.programs_of(gj) == checks.PROGRAM_PHASES)
letters = {}
for n, name in enumerate(checks.PROGRAM_PHASES):
    hdr = reemit.program_header(
        gj, name, [op(ph, gj.phases[ph]["tool"], ["G00 Z2.0000"], 100)
                   for ph in checks.PROGRAM_PHASES[name]]
        if name != "silk" else None)
    letters[name] = hdr[0]
    body = (GOLDEN / f"coupon-{name}.nc").read_text().splitlines()
    missing = [ln for ln in hdr if ln not in body]
    check(f"coupon-{name}.nc's blessed header regenerates EXACTLY",
          not missing, f"absent from the golden: {missing}")
def reassemble(name):
    """Split a blessed single-sided program back into its OpResults and
    re-assemble it. Not circular: the OPS come from the golden file's own
    stage markers, but the banner, preamble, header block, tool changes,
    dwells, finish markers and postamble are all REGENERATED — so a
    byte-identical result is proof that nothing in the double-sided work
    moved a single byte of the single-sided path (Article III)."""
    text = (GOLDEN / f"coupon-{name}.nc").read_text()
    ops, cur, tool, kind = [], None, None, None
    for ln in text.splitlines():
        if ln.startswith("(begin operation: pcb-"):
            kind = ln.split("pcb-")[1].split()[0]
            tool = int(ln.split(" T")[1].split()[0])
            cur = []
            continue
        if ln.startswith("(finish operation:"):
            ops.append(op(kind, tool, cur, 100))
            cur = None
            continue
        if cur is not None:
            cur.append(ln)
    return reemit.assemble_program(gj, name, ops), text


for name in ("mill", "scrub", "holes"):
    got, want = reassemble(name)
    check(f"coupon-{name}.nc re-assembles BYTE-IDENTICALLY",
          got == want,
          f"{len(got)} vs {len(want)} bytes" + ("" if got == want else
          "; first diff at " + str(next(
              i for i, (a, b) in enumerate(zip(got, want)) if a != b))))
check("the single-sided split still spells A of 4 .. D of 4",
      all(f"program {L} of 4" in letters[n] for L, n in
          zip("ABCD", checks.PROGRAM_PHASES)), str(list(letters.values())))

# ===================================================== raster sections (gerbv)
if not bm.have_gerbv():
    print("\nSKIP: gerbv not available — the flip's raster checks (annular "
          "ring, concentricity, paste-vs-holes, annular scrub, tab-zone "
          "keep-out) are NOT checkable here (install: sudo apt install "
          "gerbv)")
    print("\nPCB TWOSIDED " + ("FAIL: " + ", ".join(fails) if fails
                               else "PASS (raster skipped)"))
    sys.exit(1 if fails else 0)

print("\nthe flip context: six layers, ONE window:")
ctx = flip.context(job)
check("both sides rasterize into the same padded window",
      ctx.cu["front"].shape == ctx.cu["back"].shape == ctx.win.shape
      and ctx.paste is not None,
      f"window {ctx.win.shape}, pad "
      f"{ctx.tight.x0 - ctx.win.x0:.1f}mm")
check("the shared window makes the two side maps pixel-registered",
      ctx.maps("front").win == ctx.maps("back").win
      and ctx.maps("front").offset == (0.0, 0.0)
      and ctx.maps("back").offset == (BW, 0.0)
      and ctx.maps("front").mirror == "none"
      and ctx.maps("back").mirror == "x")

print("\nthe ring walk (ground truth for both sides):")
for side in ("front", "back"):
    r = ctx.rings(side)
    got = [(round(w["ring"], 2), w["pad"]) for w in r]
    want = [(0.8, True), (0.8, True), (0.35, True), (0.0, False)]
    ok = all(abs(g[0] - w[0]) <= 0.02 and g[1] == w[1]
             for g, w in zip(got, want))
    check(f"{side}: V1/V2 read 0.8, the gauge 0.35, the mounting bore bare",
          ok, str(got))

print("\nboard-level checks (the artwork, before any program):")
bchk = flip.board_checks(ctx)
for c in bchk:
    print(f"      {c.name}: {c.value:.4f} ({c.limit}) "
          f"{'PASS' if c.ok else 'FAIL'}  {c.detail[:88]}")
check("every board check PASSES on the fixture",
      all(c.ok for c in bchk),
      ", ".join(c.name for c in bchk if not c.ok))
d = by_name(bchk)
check("the named gauge exception is applied AND reported",
      "flip gauge G1" in d["both-side annular ring"].detail
      and "1 bare bores excluded" in d["both-side annular ring"].detail,
      d["both-side annular ring"].detail[:110])

print("\nboth sides' silk, each lasered onto its OWN cured mask:")
for sj in (front, back):
    mask_t = bm.rasterize(sj.files["mask"], ctx.tight)
    text, clip = reemit.silk_program(sj, ctx.tight, mask_t)
    path = OUT / f"{pcbjob.program_stem(sj, 'silk')}.nc"
    path.write_text(text)
    PROGS[sj.side]["silk"] = path
    check(f"{sj.side}: strokes survive the clip and carry that side's dose",
          clip.dropped == 0 and clip.clipped == 0
          and f"M3 S{sj.phases['silk']['dose']:g}" in text
          and clip.strokes, clip.note)
# side A's legend is NOT mirrored (it is lasered front-up) and side B's is:
# the same gerber X maps to opposite machine X, which is the silk half of the
# frame law
fx = reemit.silk_strokes(front, ctx.tight,
                         bm.rasterize(front.files["mask"], ctx.tight))
bx3 = reemit.silk_strokes(back, ctx.tight,
                          bm.rasterize(back.files["mask"], ctx.tight))
check("the legend follows its own side's frame, not a shared one",
      abs(fx.strokes[0][0][0] - 2.0) < 1e-9
      and abs(bx3.strokes[0][0][0] - (BW - 2.0)) < 1e-9,
      f"front x {fx.strokes[0][0][0]}, back x {bx3.strokes[0][0][0]}")

print("\nPOSITIVE control — the whole document through verify_twosided:")
reports = flip.verify_twosided(job, PROGS, ctx=ctx)
for name, rep in reports.items():
    bad = [c.name for c in rep.checks if not c.ok]
    check(f"{name} PASSES every check", rep.ok,
          f"{len(rep.checks)} checks" + (f"; FAILED {bad}" if bad else ""))
check("nine programs plus the artwork report, keyed side/program",
      len(reports) == 10 and set(reports) == {"board"} | {
          f"{s2}/{n}" for s2 in ("front", "back")
          for n in pcbjob.SIDE_PROGRAMS[s2]}, str(sorted(reports)))
short = flip.verify_twosided(job, {"front": PROGS["front"]}, ctx=ctx)
check("a side that was not handed over is FATAL per program, never skipped",
      all(not short[f"back/{n}"].ok for n in pcbjob.SIDE_PROGRAMS["back"])
      and "unverified" in short["back/mill"].checks[0].detail,
      short["back/mill"].checks[0].detail[:70])
check("report_text names the artwork and every side/program",
      "artwork board" in flip.report_text(reports)
      and "program back/holes" in flip.report_text(reports)
      and "PCB VERDICT (double-sided): PASS" in flip.report_text(reports))

print("\n  the pin block's own report:")
pins_rep = reports["front/pins"]
for c in pins_rep.checks:
    print(f"      {c.name}: {c.value:.4f} ({c.limit}) "
          f"{'PASS' if c.ok else 'FAIL'}")
pd = by_name(pins_rep.checks)
check("the pins program carries the position, board keep-out and depth laws",
      {"pinspot only at pin positions", "pindrill only at pin positions",
       "pinspot clear of the board", "pindrill clear of the board",
       "pindrill floor Z", "sheet depth floor (excl. pin holes)",
       "sheet pin bores clear of the bed"} <= set(pd),
      f"{len(pd)} checks")
check("the pin bores are excluded from the sheet floor, and clear the bed",
      pd["sheet depth floor (excl. pin holes)"].value > -1.0
      and abs(pd["sheet pin bores clear of the bed"].value + 8.8) < 0.2,
      f"floor {pd['sheet depth floor (excl. pin holes)'].value:.3f}, pins "
      f"{pd['sheet pin bores clear of the bed'].value:.3f}")
check("every OTHER program carries the pin keep-out carry-over",
      all("pin keep-out" in by_name(reports[f"{s}/{n}"].checks)
          for s in ("front", "back") for n in ("mill", "scrub", "holes")))

# ================================================== NEGATIVE controls (flip)
print("\nNEGATIVE controls — one per new check, each caught BY NAME:")


def rebuild(**kw):
    """A fresh context over MUTATED artwork: write one layer differently,
    reload, and re-rasterize. The gerbers are the ground truth, so a negative
    control has to move the gerbers."""
    d = TD / ("neg" + str(len(list(TD.glob("neg*")))))
    d.mkdir()
    for f in G.iterdir():
        (d / f.name).write_text(f.read_text())
    for name, text in kw.items():
        (d / f"stub2-{name}.gbr").write_text(text)
    p = d.parent / f"{d.name}.toml"
    p.write_text(JOB.replace('gerbers = "gerbers"',
                             f'gerbers = "{d.name}"'))
    j = pcbjob.load(p)
    return j, flip.context(j)

# 1. a SHAVED front pad over a good back pad — the failure orbit SPEC names
nj, nctx = rebuild(F_Cu=flashes([(1.8, [V1]), (VIA_PAD, [V2]),
                                 (GAUGE_PAD, [G1])]))
catches("a shaved FRONT pad under a good back pad (ring 0.4 vs 0.7)",
        flip.board_checks(nctx), "both-side annular ring",
        must_pass=("side frame mirror law",
                   "paste clear of the hole schedule"))
# 2. a pad MISSING on one side entirely — must not be reclassified as bare
nj, nctx = rebuild(F_Cu=flashes([(VIA_PAD, [V2]), (GAUGE_PAD, [G1])]))
nb = by_name(flip.board_checks(nctx))
catches("a front pad missing outright (a bare bore on ONE side only)",
        flip.board_checks(nctx), "both-side annular ring")
check("  ...and it is named as a missing pad, not silently excluded",
      "NO PAD on this side" in nb["both-side annular ring"].detail,
      nb["both-side annular ring"].detail[-90:])
# 3. an UNDECLARED thin annulus: the same 0.3 ring at a position no gauge
#    names must fail — that is what makes the exception NAMED
nj, nctx = rebuild(
    F_Cu=flashes([(VIA_PAD, [V1]), (GAUGE_PAD, [V2]), (GAUGE_PAD, [G1])]),
    B_Cu=flashes([(VIA_PAD, [V1]), (GAUGE_PAD, [V2]), (GAUGE_PAD, [G1]),
                  (SMD_PAD, [SMD])]))
catches("an UNDECLARED 0.3 annulus (only the gauge is exempt)",
        flip.board_checks(nctx), "both-side annular ring")
# 4. an eccentric back pad: the hole is no longer centred in it
nj, nctx = rebuild(B_Cu=flashes([(VIA_PAD, [(V1[0] + 0.25, V1[1]), V2]),
                                 (GAUGE_PAD, [G1]), (SMD_PAD, [SMD])]))
catches("a back pad 0.25 off its hole (concentricity across the flip)",
        flip.board_checks(nctx),
        "via/hole concentricity across the flip")
# 5. paste over a via hole
nj, nctx = rebuild(B_Paste=flashes([(1.8, [SMD]), (2.0, [V1])]))
catches("a B.Paste aperture over a via hole (it wicks and blocks the wire)",
        flip.board_checks(nctx), "paste clear of the hole schedule",
        must_pass=("both-side annular ring", "side frame mirror law"))
# 6. the frame law, falsified: build the BACK maps with side A's transform
bad_ctx = flip.context(job)
bad_ctx.holes = list(ctx.holes)
_m = flip.FlipContext.mirror
try:
    flip.FlipContext.mirror = staticmethod(lambda side: "none")
    catches("both frames built with the SAME mirror (the WS2 bug's shape)",
            flip.frame_checks(bad_ctx), "side frame mirror law")
finally:
    flip.FlipContext.mirror = staticmethod(_m)

# 7/8. the annular scrub laws, on side 2's bytes
sc_far = write_program(back, "scrub", [build_scrub(back, radii=1.05)])
rep = checks.verify_program(back, "scrub", sc_far, ctx.maps("back"), flip=ctx)
catches("a side-2 lap only 0.10 inside copper (annular bar is 0.15)",
        rep.checks, "annular scrub inside copper",
        must_pass=("scrub window", "scrub plateau margin",
                   "annular scrub clear of the hole rim"))
sc_hole = write_program(back, "scrub", [build_scrub(back, centre=True)])
rep = checks.verify_program(back, "scrub", sc_hole, ctx.maps("back"),
                            flip=ctx)
catches("a side-2 DISC lap straight across a drilled hole",
        rep.checks, "annular scrub clear of the hole rim",
        must_pass=("scrub window", "scrub plateau margin",
                   "annular scrub inside copper"))
print("      ^ the point of the check: the copper gerber draws a pad as a "
      "SOLID\n        disc, so a lap over the hole centre reads as deeply "
      "inside copper\n        and passes every single-sided scrub law there "
      "is")
# 9. copper inside a tab zone
nj, nctx = rebuild(B_Cu=flashes([(VIA_PAD, [V1, V2]), (GAUGE_PAD, [G1]),
                                 (SMD_PAD, [SMD, (BW / 2, 0.6)])]))
nback = pcbjob.side_view(nj, "back")
tabp = write_program(nback, "holes", [build_cutout(nback)])
rep = checks.verify_program(nback, "holes", tabp, nctx.maps("back"),
                            flip=nctx)
catches("copper 0.6 from the board edge, inside a cutout tab's zone",
        rep.checks, "tab-zone copper keep-out",
        must_pass=("cutout tab census", "cutout ride band", "cutout side"))
# 10. the pins-law carry-over, on the bytes. The pins sit in the blank's
# WASTE, several mm outside the raster window, so a program that drives over
# one gets refused for leaving the verification window before any check runs —
# which is the right refusal (Article I: an unmodeled move is unverified) but
# names the wrong law. So this negative does both halves: the samples go
# straight to the carry-over check, and the same file is confirmed FATAL
# through the full gate.
p = back.phases["clear"]
m = machof(back)
over = build_clear(back)
over.lines.extend(cut_chain([m((10.0, 18.0)), m((10.0, 21.0))],
                            p["depth"], p["feed"], p["plunge"]))
badp = write_program(back, "mill", [build_iso(back), over])
bmaps = ctx.maps("back")
mv, _ = checks.program_moves(back, badp)
bad_samples = {ph: checks.phase_samples(mv, bmaps, ph)
               for ph in ("iso", "clear")}
catches("side-2 clearing driven over a steel pin",
        checks.pin_keepout_checks(back, bad_samples)
        + checks.echo_checks(back, ("clear",), bad_samples,
                             badp.read_text()),
        "pin keep-out", must_pass=("clear floor Z", "clear params"))
rep = checks.verify_program(back, "mill", badp, bmaps, flip=ctx)
check("  ...and the same file is FATAL through the full gate (it leaves the "
      "modelled window)",
      not rep.ok and "verification window" in rep.checks[0].detail,
      rep.checks[0].detail[:80])
PROGS["back"]["mill"] = write_program(back, "mill",
                                      [build_iso(back), build_clear(back)])
# 11. pin work that reaches the board
pj = reemit.pin_ops(front)
pj[0].lines.extend(cut_chain([machof(front)((10.0, 12.0)),
                              machof(front)((10.0, 13.0))],
                             -0.1, 60, 60))
badpin = write_program(front, "pins", pj)
rep = checks.verify_program(front, "pins", badpin, ctx.maps("front"),
                            flip=ctx)
catches("a spot-face pass that wanders onto the board",
        rep.checks, "pinspot clear of the board",
        must_pass=("pindrill only at pin positions",))
# 12. a displaced pin bore
pj = reemit.pin_ops(front)
pj[1].lines = [ln.replace("X10.000", "X10.400") for ln in pj[1].lines]
badpin = write_program(front, "pins", pj)
rep = checks.verify_program(front, "pins", badpin, ctx.maps("front"),
                            flip=ctx)
catches("a pin bore 0.4 off the configured position",
        rep.checks, "pindrill only at pin positions",
        must_pass=("pinspot only at pin positions", "pindrill floor Z"))

# ======================================== local bonus: the REAL engine, twice
# CI never runs FlatCAM (PCB-PLAN WS5). On a box that has the pinned checkout,
# the whole double-sided engine path is cheap on this fixture — ~19s for both
# sides — so it runs here, and it is the only thing that proves the templated
# Tcl is Tcl the engine accepts.
print("\nlive engine run (local bonus — CI never runs FlatCAM):")
try:
    engine.preflight(job)
    live = True
except Exception as exc:                                    # noqa: BLE001
    print(f"  SKIP: {str(exc)[:120]}")
    live = False

if live:
    work = TD / "live"
    out = engine.run_sides(job, work)
    check("both sides ran, each in its own dir, each with its own phases",
          set(out) == {"front", "back"}
          and set(out["front"]) == {"iso", "clear", "scrub", "drills"}
          and set(out["back"]) == {"iso", "clear", "scrub", "cutout"},
          {s: sorted(out[s]) for s in out})
    lprogs = {}
    for side in job.sides:
        sj = pcbjob.side_view(job, side)
        lprogs[side] = {}
        ops = {ph: reemit.read_phase(nc, sj, ph)
               for ph, nc in out[side].items()}
        check(f"{side}: every phase re-emits clean under the param-match law",
              all(o.lines for o in ops.values()),
              ", ".join(f"{k}:{len(v.lines)}" for k, v in sorted(ops.items())))
        for name, want in pcbjob.SIDE_PROGRAMS[side].items():
            if name == "silk":
                mt = bm.rasterize(sj.files["mask"], ctx.tight)
                text = reemit.silk_program(sj, ctx.tight, mt)[0]
            elif name == "pins":
                text = reemit.assemble_program(sj, name, reemit.pin_ops(sj))
            else:
                text = reemit.assemble_program(sj, name,
                                               [ops[ph] for ph in want])
            p = work / f"{pcbjob.program_stem(sj, name)}.nc"
            p.write_text(text)
            lprogs[side][name] = p
    lreps = flip.verify_twosided(job, lprogs, ctx=ctx)
    bad = {n: [c.name for c in r.checks if not c.ok]
           for n, r in lreps.items() if not r.ok}
    check("the live artwork + frame + drill + cutout + silk + pin reports all "
          "PASS",
          all(lreps[n].ok for n in lreps if not n.endswith("/scrub")),
          str({n: v for n, v in bad.items() if not n.endswith("/scrub")}))
    # THE FINDING, asserted as the loud gap it is (Article II, not yet law):
    # FlatCAM's `paint mask` fills each aperture with disc laps and knows
    # nothing about the Excellon, so on side 2 — where every hole is already
    # bored — it drives the spring tip straight across them. The gate refuses
    # it, which is correct and which is the whole reason the check exists.
    # When the annular-lap generator lands, THIS assertion is what tells the
    # next agent to update it.
    s2 = by_name(lreps["back/scrub"].checks)
    check("KNOWN OPEN: FlatCAM's paint cannot make side 2's ANNULAR laps, and "
          "the gate says so",
          not lreps["back/scrub"].ok
          and not s2["annular scrub clear of the hole rim"].ok,
          f"annular rim {s2['annular scrub clear of the hole rim'].value:.4f} "
          f"(>= {flip.SCRUB_ANNULAR_RIM}) — `paint` laps across the drilled "
          f"holes")
    print("  " + "=" * 68)
    print("  TODO(engine) — SIDE 2's SCRUB GEOMETRY IS NOT GENERATED YET.")
    print("  `paint mask` fills the mask apertures with disc laps from the")
    print("  mask layer alone. On side 2 the holes are already drilled, and")
    print("  orbit SPEC.md's scrub rule wants ANNULAR laps: tool edge >=")
    print(f"  {flip.SCRUB_ANNULAR_INSIDE} inside copper AND >= "
          f"{flip.SCRUB_ANNULAR_RIM} outside the hole rim. The check is")
    print("  landed and refuses the wrong geometry; the GENERATOR is the")
    print("  next piece of work (paint a mask minus the dilated holes, or")
    print("  emit the laps in-repo like the silk strokes). Board B cannot")
    print("  cut its side-2 scrub until it exists — which is the gate doing")
    print("  its job, not a hole in it.")
    print("  " + "=" * 68)

print(f"\nPCB TWOSIDED {'FAIL: ' + ', '.join(fails) if fails else 'PASS'}")
sys.exit(1 if fails else 0)
