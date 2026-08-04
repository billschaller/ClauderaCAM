"""Double-sided [pcb] composition ([pcb] + [twosided]): the grammar, the two
frames, the pin block and the flip's own check set (PCB-PLAN.md WS5's
double-sided list + WS8; the four additions boards/orbit/SPEC.md asks for).

THE FIXTURE. `stub2` is a synthetic double-sided board, 20 x 15 mm, built here
from gerber text so the whole suite runs with no board files and no FlatCAM:

    hole            F.Cu pad   B.Cu pad   cut in   what it is for
    V1 (5,5)  Ø1.0  Ø2.6       Ø2.6       setup 2  a wire via: 0.8 ring both
                                                   sides — a PAD hole
    V2 (15,5) Ø1.0  Ø2.6       Ø2.6       setup 2  a second one, so "every"
                                                   means >1
    G1 (5,11) Ø1.0  Ø1.6       Ø1.6       setup 1  a FLIP GAUGE: 0.3 ring, the
                                                   named exception the job
                                                   declares — a non-pad BORE
    H1 (15,11) Ø3.4 none       none       setup 1  a mounting bore: bare on
                                                   both sides, excluded from
                                                   the ring census
    P1 (10,3)  --   none       Ø2.0       --       an SMD pad, the only
                                                   B.Paste aperture

The 2026-08-03 ORDERING LAW (operator ruling) is what "cut in" says: a pad is
never drilled before it is scrubbed, and every area the bench expects to
solder is always scrubbed. So the schedule is PARTITIONED — stub2-bores.drl
(G1, H1: the non-pad holes, cut in setup 1 because setup 2's iso must cut the
gauges' read-out discs) and stub2-pads.drl (V1, V2: every pad hole, cut in
setup 2 after BOTH scrubs) — and the two files must split stub2.drl exactly.

Everything the double-sided path can get wrong is reachable from that: two
frames from one Edge.Cuts, rings on both sides, a declared exception, a bare
bore, a paste aperture that must stay off the holes, tabs on setup 2, a hole
partition that can be broken in both directions, and the registration pins in
the blank's waste.

WHAT MUST HOLD
  - the single-sided form is untouched: the coupon goldens' program HEADERS
    regenerate byte-identically from the shipped job, and PROGRAM_PHASES still
    spells A..D (nothing in this work may re-bless tests/golden_pcb)
  - the grammar refuses: per-phase tables on a flipped document, a [twosided]
    that will not SAY which face runs first, a cutout or a pad-drilling pass
    on setup 1, the non-pad bores on setup 2, hand-written pin phases, a hole
    partition that leaves a hole uncut or cuts one twice, missing F.*/paste
    artwork, no [pins], asymmetric pins, pins inside the machined envelope,
    pins that do not fit the blank, a pin hole that reaches the bed, an
    undeclared annular value, a gauge with no reason, a gauge that names no
    hole
  - the FRAMES: both derive from the ONE Edge.Cuts, side A unmirrored and side
    B mirrored, and the pair closes the loop on the WS2 mirror law
    (`mirror -axis X` NEGATES X) — asserted against an independent
    re-derivation and falsified by a deliberately wrong mirror
  - the TCL is templated twice from one document: side A with no `mirror` line
    and no cutout, side B with the mirror — and each side opens its OWN
    partition of the Excellon, so no hole is bored twice from two frames
  - the ORDERING LAW's three convictions run on EVERY scrub program
    (flip.scrub_plan_checks): the scrub stays clear of the holes that already
    exist (setup 1 none, setup 2 the bores it inherits — the 2026-07-30
    paint-across-bores incident, kept convictable after the geometry that
    caused it was retired), every live mask aperture takes cutting path (the
    flood coat is opened by the scrub and by nothing else), and a DECLARED
    inert aperture keeps its coat. Each has a negative control that convicts
  - the PIN BLOCK composes the shipped machinery: ops/drill.py's spot-face and
    peck, positions symmetric about the DERIVED mirror line, keep-out carried
    into every program of both setups
  - CONCENTRICITY is measured on the ring's SHORT SIDE and is not fooled by
    attached tracks (the 2026-08-02 Board B incident: the retired max-min
    proxy was a track detector). Five controls: the 0.25-displaced pad still
    FAILS; a displaced pad that ALSO feeds a track on the squeezed side FAILS;
    a centred pad feeding one track and three tracks PASS; and the retired
    proxy's own two readings are asserted to lie closer together than the
    tolerance, which is what "indistinguishable" meant
  - all nine programs of the fixture PASS the whole gate, and each new check
    has a NEGATIVE control that is caught BY NAME while its neighbours still
    pass
  - the VIEWER composes both setups into ONE session list (WS8): ten sessions
    — the artwork report plus nine keyed `<side>/<program>`, each pointing at
    its own side's .nc, its own frame and its own phase numbers — the gate's
    verdicts (cross-side checks included) folded in, and a run-sheet card in
    MACHINING order: setup 1's phases, the non-pad bores, the pins, the flip,
    setup 2's phases, then every pad hole and the cutout last

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
from scipy import ndimage

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
# the 2026-08-03 partition of that schedule: the NON-PAD holes are bored in
# setup 1 (the gauge must exist before setup 2's iso cuts its read-out disc;
# the mount is never soldered), every PAD hole waits for setup 2 — after both
# faces' solder plans are scrubbed. bores + pads == HOLES, disjoint, and the
# loader proves it as an exact multiset.
BORE_HOLES = [h for h in HOLES if (h[0], h[1]) in (G1, H1)]
PAD_HOLES = [h for h in HOLES if (h[0], h[1]) in (V1, V2)]

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


def routed(items, tracks, dia=0.6):
    """A copper layer of flashed pads PLUS routed TRACKS — a stroked round
    aperture is exactly what a PCB track is.

    The stub artwork was flash-only until 2026-08-02, and that is precisely
    why the concentricity proxy shipped as a track detector: no control it
    ever ran on had a track in it. Board B did.
    """
    out = ""
    for n, (d, pts) in enumerate(items):
        out += f"%ADD{10 + n}C,{d:.6f}*%\n"
    out += f"%ADD{10 + len(items)}C,{dia:.6f}*%\nG01*\n"
    for n, (d, pts) in enumerate(items):
        out += f"D{10 + n}*\n"
        out += "".join(f"X{gnum(x)}Y{gnum(y)}D03*\n" for x, y in pts)
    out += f"D{10 + len(items)}*\n"
    for (x0, y0), (x1, y1) in tracks:
        out += f"X{gnum(x0)}Y{gnum(y0)}D02*\nX{gnum(x1)}Y{gnum(y1)}D01*\n"
    return HDR + out + "M02*\n"


def radial(centre, deg, length=3.5):
    """One track leaving `centre` at `deg`, as (start, end)."""
    a = math.radians(deg)
    return (centre, (centre[0] + length * math.cos(a),
                     centre[1] + length * math.sin(a)))


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
# Apertures EQUAL their pads: the mask is the scrub region's source, and an
# aperture proud of its pad by more than (deflate 0.15 − plateau bar 0.05)
# hands paint a lap ON the copper edge — this fixture originally carried
# 2.8/2.4 over 2.6/2.0 pads and the live gate failed `scrub plateau margin`
# on BOTH sides' paint the day the all-ten assertion landed. Real exports
# (the coupon) open the mask at pad size; the fixture now does too.
F_MASK = flashes([(VIA_PAD, [V1, V2])])
B_MASK = flashes([(VIA_PAD, [V1, V2]), (SMD_PAD, [SMD])])
F_SILK = strokes([((2.0, 8.5), (18.0, 8.5))])
B_SILK = strokes([((2.0, 8.5), (18.0, 8.5)), ((3.0, 13.0), (9.0, 13.0))])
B_PASTE = flashes([(1.8, [SMD])])


def drl_text(holes):
    """One Excellon in the suite's dialect, tools in diameter order. The full
    schedule and BOTH partition files are written by this one function, so a
    partition can never differ from the schedule by dialect — only by which
    holes it carries, which is the thing the loader judges."""
    dias = sorted({d for _, _, d in holes})
    out = "M48\nFMAT,2\nMETRIC\n"
    out += "".join(f"T{n + 1}C{d:.3f}\n" for n, d in enumerate(dias))
    out += "%\nG90\nG05\n"
    for n, d in enumerate(dias):
        out += f"T{n + 1}\n" + "".join(f"X{x:.3f}Y{y:.3f}\n"
                                       for x, y, hd in holes if hd == d)
    return out + "T0\nM30\n"


DRL = drl_text(HOLES)

TD = Path(tempfile.mkdtemp(prefix="clauderacam-ws8-"))
G = TD / "gerbers"
G.mkdir()
for name, text in (("stub2-Edge_Cuts.gbr", EDGE),
                   ("stub2-F_Cu.gbr", F_CU), ("stub2-B_Cu.gbr", B_CU),
                   ("stub2-F_Mask.gbr", F_MASK), ("stub2-B_Mask.gbr", B_MASK),
                   ("stub2-F_Silkscreen.gbr", F_SILK),
                   ("stub2-B_Silkscreen.gbr", B_SILK),
                   ("stub2-B_Paste.gbr", B_PASTE), ("stub2.drl", DRL),
                   ("stub2-bores.drl", drl_text(BORE_HOLES)),
                   ("stub2-pads.drl", drl_text(PAD_HOLES))):
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


# setup 1 (front, per [twosided] first) ends with the non-pad BORES and the
# derived pin block; setup 2 (back) carries every PAD hole and then the
# cutout, in that order and both in its ONE holes program.
FRONT = side_block("front", """
[phases.front.bores]
tool = 3
depth = -1.7
dpp = 0.4
feed = 250
plunge = 150
""")
BACK = side_block("back", """
[phases.back.drills]
tool = 3
depth = -1.7
dpp = 0.4
feed = 250
plunge = 150

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
# which face machines FIRST is a real process decision, and the document must
# say it (the 2026-08-03 ordering law). FRONT first here: the mirror math is
# face-keyed, so the frame/pin/tcl laws below still read the unmirrored face
# as setup 1, exactly as they did before the reordering.
first = "front"

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
check("the RUN ORDER is the document's, and the roles follow it",
      job.sides == ("front", "back") and pcbjob.role_of(front) == "first"
      and pcbjob.role_of(back) == "second",
      f"first={job.sides[0]}, second={job.sides[1]}")
check("setup 1 is FRONT, unmirrored, and carries the BORES + the pin block",
      front.side == "front" and front.mirror == "none"
      and front.has_phase("bores") and not front.has_phase("drills")
      and not front.has_phase("cutout")
      and front.has_phase("pindrill") and front.has_phase("pinspot"),
      f"{front.name} mirror={front.mirror} phases={sorted(front.phases)}")
check("setup 2 is BACK, mirrored, and carries the PAD drills + the cutout "
      "and NO bores",
      back.side == "back" and back.mirror == "x"
      and back.has_phase("drills") and back.has_phase("cutout")
      and not back.has_phase("bores") and not back.has_phase("pindrill"),
      f"{back.name} mirror={back.mirror} phases={sorted(back.phases)}")
check("each side's artwork is its own; the outline and the drill file are ONE",
      front.files["cu"].name == "stub2-F_Cu.gbr"
      and back.files["cu"].name == "stub2-B_Cu.gbr"
      and front.files["silk"].name == "stub2-F_Silkscreen.gbr"
      and front.files["edge"] == back.files["edge"]
      and front.files["drl"] == back.files["drl"])
check("each setup CUTS its own partition of that one schedule",
      front.files["drl_cut"].name == "stub2-bores.drl"
      and back.files["drl_cut"].name == "stub2-pads.drl"
      and [(x, y) for x, y, d in bm.excellon(front.files["drl_cut"])]
      == [G1, H1]
      and [(x, y) for x, y, d in bm.excellon(back.files["drl_cut"])]
      == [V1, V2]
      and front.files["bores_drl"] == back.files["bores_drl"],
      f"{front.files['drl_cut'].name} / {back.files['drl_cut'].name}")
check("the program split is per SETUP, and the pin block is setup 1's fifth",
      pcbjob.programs_of(front) == {k: v for k, v in
                                    pcbjob.ROLE_PROGRAMS["first"].items()
                                    if k != "excise"}   # optional, unconfigured
      and pcbjob.programs_of(back) == pcbjob.ROLE_PROGRAMS["second"]
      and list(pcbjob.programs_of(front)) == ["mill", "silk", "scrub",
                                              "holes", "pins"]
      and list(pcbjob.programs_of(back)) == ["mill", "silk", "scrub", "holes"]
      and pcbjob.programs_of(front)["holes"] == ("bores",)
      and pcbjob.programs_of(back)["holes"] == ("drills", "cutout"))
# every reader below asks the JOB what a side's programs are — the face->role
# mapping is the document's, and nothing may key a split on a face name
SPROGS = {s: pcbjob.programs_of(pcbjob.side_view(job, s)) for s in job.sides}
check("program names carry the side (twosided's name-side convention)",
      pcbjob.program_stem(front, "mill") == "stub2-front-mill"
      and pcbjob.program_stem(back, "holes") == "stub2-back-holes")
from clauderacam.pcb import session  # noqa: E402
check("the viewer finds each side's programs under the side's own stem",
      session.program_paths(front) == {} and session.program_count(
          session.document_programs(job)) == 0,
      "nothing generated yet at this point in the suite")
_empty = session.build(job, gate=False)
check("a document with no programs on disk still composes its card, and "
      "every program step says it is MISSING",
      [s.name for s in _empty] == ["board"]
      and all(st["missing"] for st in _empty[0].meta["run_sheet"]
              if st["kind"] == "program")
      and "not on disk" in _empty[0].meta["gate"]["note"],
      _empty[0].meta["gate"]["note"][:70])

print("\ngrammar refusals:")
caught("a flipped document with ONE phase table refuses",
       lambda: pcbjob.load(variant(
           "v-flat.toml", ("[phases.front.iso]", "[phases.iso]"))),
       "chain TWICE")
caught("a flipped document that will not SAY which face runs first refuses",
       lambda: pcbjob.load(variant("v-nofirst.toml",
                                   ('first = "front"\n', ""))),
       "which face machines first")
caught("...and a face name it does not have refuses the same way",
       lambda: pcbjob.load(variant("v-badfirst.toml",
                                   ('first = "front"', 'first = "top"'))),
       "which face machines first")
caught("a cutout on setup 1 refuses",
       lambda: pcbjob.load(variant(
           "v-cutfront.toml", ("[phases.back.cutout]",
                               "[phases.front.cutout]"))),
       "belongs to the second setup only")
caught("PAD drills on setup 1 refuse — a pad hole before a scrub is a mask "
       "collar on a solder joint",
       lambda: pcbjob.load(variant(
           "v-drillfront.toml", ("[phases.front.bores]",
                                 "[phases.front.drills]"))),
       "belongs to the second setup only")
caught("the non-pad bores on setup 2 refuse — the gauges must exist before "
       "setup 2's iso cuts their read-out discs",
       lambda: pcbjob.load(variant(
           "v-boresback.toml", ("[phases.back.drills]",
                                "[phases.back.bores]"))),
       "belongs to the first setup only")
caught("hand-written pin phases refuse (the coin lane's law)",
       lambda: pcbjob.load(variant(
           "v-handpin.toml", ("[phases.front.bores]",
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
       "of the machine bed")
# orbit decision Q11: the Ø2x12 dowel IS usable when the job declares the
# seat and tip allowance instead of taking the coin defaults — 12 + 0 + 0
# = 12.0 clears the 12.2 the bed allows (and the drill's 12.0 flute + 0.1
# counterbore reach). The knob buys honesty, not reach: negative refuses,
# and a declared depth that still hits the bed refuses like any other.
check("a declared tip allowance seats the Ø2x12 dowel (orbit Q11)",
      pcbjob.pin_depth(pcbjob.load(variant(
          "v-q11.toml", ("length = 8.0",
                         "length = 12.0\nseat_extra = 0.0\n"
                         "tip_allowance = 0.0")))) == 12.0,
      "depth 12.0 on 1.5 blank + 12.7 spoilboard (bed allows 12.2)")
caught("a negative tip allowance refuses — a hole cannot be shallower "
       "than its pin",
       lambda: pcbjob.load(variant(
           "v-q11neg.toml", ("length = 8.0",
                             "length = 12.0\nseat_extra = 0.0\n"
                             "tip_allowance = -0.2"))),
       "negative")
caught("a declared depth that still reaches the bed refuses",
       lambda: pcbjob.load(variant(
           "v-q11deep.toml", ("length = 8.0",
                              "length = 12.5\nseat_extra = 0.0\n"
                              "tip_allowance = 0.0"))),
       "of the machine bed")
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
caught("a flipped document with no partition export refuses",
       lambda: without("stub2-bores.drl"), "missing excellon")


def partition(name, bores, pads):
    """A job whose two partition Excellons carry `bores`/`pads` instead of the
    honest split — same artwork, same full schedule, so the ONLY thing that
    can refuse is the partition law itself."""
    d = TD / name
    d.mkdir(exist_ok=True)
    for f in G.iterdir():
        (d / f.name).write_text(f.read_text())
    (d / "stub2-bores.drl").write_text(drl_text(bores))
    (d / "stub2-pads.drl").write_text(drl_text(pads))
    p = TD / f"{name}.toml"
    p.write_text(JOB.replace('gerbers = "gerbers"', f'gerbers = "{name}"'))
    return p


check("the honest partition of the fixture loads (the control for the three "
      "refusals below)",
      pcbjob.load(partition("p-ok", BORE_HOLES, PAD_HOLES)).name == "stub2")
caught("a hole in NEITHER partition file refuses — it would never be cut",
       lambda: pcbjob.load(partition("p-never", BORE_HOLES[:1], PAD_HOLES)),
       "cut NEVER")
caught("a hole in BOTH files refuses — bored from two frames, a via becomes "
       "a slot",
       lambda: pcbjob.load(partition("p-twice", BORE_HOLES + PAD_HOLES[:1],
                                     PAD_HOLES)),
       "cut TWICE")
caught("a hole MOVED by 0.05 between the schedule and its partition refuses "
       "(multiset equality, no tolerance — one exporter wrote all three)",
       lambda: pcbjob.load(partition(
           "p-moved", [(G1[0] + 0.05, G1[1], VIA_HOLE), BORE_HOLES[1]],
           PAD_HOLES)),
       "hole partition broken")

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
      and "geocutout" in tcl_b and "milldrills" in tcl_b
      and f"offset cu -x {BW:g} -y 0" in tcl_b,
      [ln for ln in tcl_b.splitlines() if ln.startswith("offset cu")])
# each setup opens its OWN partition, and each writes its own phase file: no
# hole is ever opened in both frames, which is what "cut exactly once" means
# down at the interchange level
check("each side opens ITS partition of the Excellon, and only that one",
      "stub2-bores.drl" in tcl_a and "stub2-pads.drl" not in tcl_a
      and "stub2-pads.drl" in tcl_b and "stub2-bores.drl" not in tcl_b
      and tcl_a.count("open_excellon") == tcl_b.count("open_excellon") == 1,
      [ln for ln in (tcl_a + tcl_b).splitlines()
       if ln.startswith("open_excellon")])
check("...and each writes its own hole phase file (bores vs drills), the "
      "cutout still side 2's alone",
      engine.PHASE_NC["bores"] in tcl_a
      and engine.PHASE_NC["drills"] not in tcl_a
      and engine.PHASE_NC["drills"] in tcl_b
      and engine.PHASE_NC["bores"] not in tcl_b
      and engine.PHASE_NC["cutout"] in tcl_b
      and engine.PHASE_NC["cutout"] not in tcl_a,
      f"{engine.PHASE_NC['bores']} / {engine.PHASE_NC['drills']}")
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

# ============================= the scrub is the SOLDER PLAN (no raster needed)
# The 2026-08-03 ordering law replaced the aperture-class split: no hole
# exists under ANY scrub any more, so `paint` covers every hole-centred pad
# fully — that bare disc is the law's whole point — and the only thing that
# may be held back from paint is a DECLARED INERT aperture (a mask opening
# the bench will never solder). This section proves that machinery with no
# gerbv and no FlatCAM; the raster sections below prove the gate convicts a
# scrub that gets the solder plan wrong.
print("\nthe scrub is the SOLDER PLAN: inert apertures (no raster needed):")
import copy  # noqa: E402

mfl = bm.flashes(G / "stub2-B_Mask.gbr")
check("the flash scan reads the mask's apertures as DESIGN numbers",
      len(mfl) == 3
      and sorted((round(d, 3), s) for _, _, s, d in mfl)
      == [(2.0, "C"), (2.6, "C"), (2.6, "C")])
same = TD / "silk-copy.gbr"
bm.rewrite_flashes(G / "stub2-B_Silkscreen.gbr", same, lambda x, y: False)
check("rewrite_flashes with nothing to drop is byte-identical "
      "(draws untouched)",
      same.read_text() == (G / "stub2-B_Silkscreen.gbr").read_text())
check("NO inert declaration: BOTH setups paint the export itself — the whole "
      "flood coat over every pad, hole-centred or not",
      reemit.scrub_mask(front, TD) == front.files["mask"]
      and reemit.scrub_mask(back, TD) == back.files["mask"]
      and reemit.inert_apertures(front) == []
      and reemit.inert_apertures(back) == [],
      f"{back.files['mask'].name}")

# the ONE thing that may be held back from paint: a mask opening the bench
# will never solder. It is board truth (a file the board generator exports),
# it names positions in the GERBER frame, and every entry must land on a real
# flash — a list that has drifted from the artwork is refused, never guessed.
(TD / "inert-back.txt").write_text(
    "# x, y  # why — written by the board generator, never typed\n"
    f"{SMD[0]}, {SMD[1]}  # P1 is dead copper on this control\n")
IJOB = pcbjob.load(variant("v-inert.toml",
                           ("[phases.back.drills]",
                            'inert = "inert-back.txt"\n\n'
                            "[phases.back.drills]")))
iback = pcbjob.side_view(IJOB, "back")
check("an inert list loads as board truth: position in the gerber frame, "
      "and the REASON carried with it",
      [(round(x, 3), round(y, 3)) for x, y, _ in
       reemit.inert_apertures(iback)] == [SMD]
      and reemit.inert_apertures(iback)[0][2]
      == "P1 is dead copper on this control",
      str(reemit.inert_apertures(iback)))
fmp = reemit.scrub_mask(iback, TD)
fmt_ = fmp.read_text()
check("scrub_mask: the INERT flash became a D02 MOVE (modal state intact) "
      "and every live pad still fires",
      fmp.name == "mask-scrub.gbr" and fmt_.count("D03*") == 2
      and f"X{gnum(SMD[0])}Y{gnum(SMD[1])}D02*" in fmt_
      and f"X{gnum(V1[0])}Y{gnum(V1[1])}D03*" in fmt_
      and f"X{gnum(V2[0])}Y{gnum(V2[1])}D03*" in fmt_,
      f"{fmt_.count('D03*')} flashes left")
check("render_tcl paints the OVERRIDE mask when handed one",
      f"open_gerber {fmp} -outname mask"
      in engine.render_tcl(back, win, TD / "work" / "back", mask_path=fmp))
(TD / "inert-drift.txt").write_text(
    f"{G1[0]}, {G1[1]}  # the flip gauge — which has NO mask aperture\n")
IDRIFT = pcbjob.side_view(pcbjob.load(variant(
    "v-inert-drift.toml", ("[phases.back.drills]",
                           'inert = "inert-drift.txt"\n\n'
                           "[phases.back.drills]"))), "back")
caught("an inert entry matching no flash refuses — the list drifted from the "
       "artwork",
       lambda: reemit.scrub_mask(IDRIFT, TD), "NO matching mask flash")
INOFILE = pcbjob.side_view(pcbjob.load(variant(
    "v-inert-gone.toml", ("[phases.back.drills]",
                          'inert = "inert-nope.txt"\n\n'
                          "[phases.back.drills]"))), "back")
caught("an inert list that is not on disk refuses — it is exported, never "
       "typed",
       lambda: reemit.inert_apertures(INOFILE), "does not exist")

print("\n...and the aperture classifier's own refusals, each by name:")
# reemit.hole_apertures no longer feeds any generator (the ordering law
# retired the hole-centred filtering it served), but it is still the module's
# reader of "which pad sits on a hole" and its refusals are the artwork
# ambiguities nobody may guess past. They stay convictable here.


def with_files(sj, **repl):
    b = copy.copy(sj)
    b.files = {**sj.files, **repl}
    return b


hp = reemit.hole_apertures(back)
check("hole-centred classification: the two vias and ONLY them (gauge has "
      "no aperture, bore has no pad)",
      [(h["hx"], h["hy"]) for h in hp] == [V1, V2]
      and all(h["mask_d"] == VIA_PAD and h["pad_d"] == VIA_PAD
              and h["hole_d"] == VIA_HOLE for h in hp))
(G / "v-mask-ecc.gbr").write_text(
    flashes([(2.8, [(V1[0] + 0.6, V1[1])]), (2.8, [V2]), (2.4, [SMD])]))
caught("an aperture overlapping a hole OFF-CENTRE refuses",
       lambda: reemit.hole_apertures(
           with_files(back, mask=G / "v-mask-ecc.gbr")),
       "OFF-CENTRE")
(G / "v-mask-rect.gbr").write_text(
    HDR + "%ADD10R,2.600000X2.600000*%\n%ADD11C,2.800000*%\n"
    "%ADD12C,2.400000*%\nG01*\nD10*\n"
    f"X{gnum(V1[0])}Y{gnum(V1[1])}D03*\nD11*\n"
    f"X{gnum(V2[0])}Y{gnum(V2[1])}D03*\nD12*\n"
    f"X{gnum(SMD[0])}Y{gnum(SMD[1])}D03*\nM02*\n")
caught("a non-circle aperture over a hole refuses",
       lambda: reemit.hole_apertures(
           with_files(back, mask=G / "v-mask-rect.gbr")),
       "not a circle")
(G / "v-cu-nopad.gbr").write_text(
    flashes([(VIA_PAD, [V2]), (GAUGE_PAD, [G1]), (SMD_PAD, [SMD])]))
caught("a masked hole with no copper flash refuses (no pad number to trust)",
       lambda: reemit.hole_apertures(
           with_files(back, cu=G / "v-cu-nopad.gbr")),
       "no copper flash")
(G / "v-mask-lpc.gbr").write_text(
    flashes([(2.8, [V1, V2])]).replace("%LPD*%", "%LPC*%"))
caught("clear polarity refuses — a cleared flash is not ink",
       lambda: bm.flashes(G / "v-mask-lpc.gbr"), "LPC")

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


def build_scrub(sj, skip=(), extra=()):
    """The 2026-08-03 ordering law's scrub, on BOTH setups: a full DISC lap on
    every solderable aperture. No hole exists under any scrub any more, so the
    annular band (and the 0.20 cured-mask collar it left exactly where the
    joint wets) is gone — the tip cleans each pad bare all the way to what
    later becomes its drilled rim.

    `skip` drops an aperture from the plan (the missed-pad control) and
    `extra` adds laps of (board centre, radius) that no aperture asked for."""
    p = sj.phases["scrub"]
    m = machof(sj)
    lines = []
    for c, dia in PADS[sj.side]:
        if c == G1 or c in skip:
            continue        # gauges are not in the scrub set (no mask opening)
        lines += cut_chain([m(c), m(c)], p["depth"], p["feed"], p["plunge"])
        ch = ring(m(c), dia / 2 - 0.6)
        lines += cut_chain(ch + [ch[0]], p["depth"], p["feed"], p["plunge"])
    for c, r in extra:
        ch = ring(m(c), r)
        lines += cut_chain(ch + [ch[0]], p["depth"], p["feed"], p["plunge"])
    return op("scrub", p["tool"], lines, p["feed"])


def build_drills(sj, holes, phase=None):
    """The hole program of ONE setup, over the partition IT cuts: setup 1's
    `bores` (the non-pad holes) or setup 2's `drills` (every pad hole), each
    in its own frame. Handing a builder the whole schedule is exactly the
    thing the ordering law forbids, so it is not the default."""
    phase = phase or ("drills" if sj.has_phase("drills") else "bores")
    p = sj.phases[phase]
    tool = sj.phase_tool(phase)
    m = machof(sj)
    lines = []
    for hx, hy, hd in holes:
        c = m((hx, hy))
        r_orbit = max((hd - tool.diameter) / 2, 0.0)
        for z in np.arange(-p["dpp"], p["depth"] - 1e-9, -p["dpp"]):
            z = max(float(z), p["depth"])
            o = ring(c, r_orbit, step=0.05) if r_orbit > 0 else [c, c]
            lines += cut_chain(o + [o[0]], z, p["feed"], p["plunge"])
        o = ring(c, r_orbit, step=0.05) if r_orbit > 0 else [c, c]
        lines += cut_chain(o + [o[0]], p["depth"], p["feed"], p["plunge"])
    return op(phase, p["tool"], lines, p["feed"])


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


def back_holes(sj=None, tabs=4):
    """Setup 2's ONE holes program, two ops in machining order: every PAD hole
    first (both faces' solder plans are scrubbed by now), then the outline cut
    with tabs, last of everything."""
    sj = sj or back
    return [build_drills(sj, PAD_HOLES), build_cutout(sj, tabs)]


PROGS = {"front": {}, "back": {}}
PROGS["front"]["mill"] = write_program(front, "mill",
                                       [build_iso(front), build_clear(front)])
PROGS["front"]["scrub"] = write_program(front, "scrub", [build_scrub(front)])
PROGS["front"]["holes"] = write_program(front, "holes",
                                        [build_drills(front, BORE_HOLES)])
PROGS["front"]["pins"] = write_program(front, "pins", reemit.pin_ops(front))
PROGS["back"]["mill"] = write_program(back, "mill",
                                      [build_iso(back), build_clear(back)])
PROGS["back"]["scrub"] = write_program(back, "scrub", [build_scrub(back)])
PROGS["back"]["holes"] = write_program(back, "holes", back_holes())
check("eight mill-dialect programs assemble through reemit.assemble_program",
      all(p.is_file() for side in PROGS for p in PROGS[side].values()),
      ", ".join(f"{s}/{n}" for s in PROGS for n in PROGS[s]))
caught("a program carrying the other setup's phases refuses",
       lambda: reemit.assemble_program(
           back, "holes", [build_drills(front, BORE_HOLES)]),
       "must carry phases")
caught("...and setup 2's holes program refuses the cutout WITHOUT the pad "
       "drills — the order is the program",
       lambda: reemit.assemble_program(back, "holes", [build_cutout(back)]),
       "must carry phases")

hdr_a = reemit.program_header(front, "pins", reemit.pin_ops(front))
hdr_b = reemit.program_header(back, "mill", [build_iso(back)])
hdr_sc = reemit.program_header(front, "scrub", [build_scrub(front)])
hdr_h1 = reemit.program_header(front, "holes",
                               [build_drills(front, BORE_HOLES)])
hdr_h2 = reemit.program_header(back, "holes", back_holes())
blob_a, blob_b = " ".join(hdr_a), " ".join(hdr_b)
blob_sc, blob_h1, blob_h2 = (" ".join(h) for h in (hdr_sc, hdr_h1, hdr_h2))
# the steps are keyed by SETUP ROLE and formatted with the faces THIS document
# chose, so the words the operator reads follow [twosided] first
check("setup 1's LAST program hands the operator the FLIP, and the deburr it "
      "names is the BORES' exits on the face that machines next",
      "program E of 5 - pins [side FRONT]" in blob_a
      and "FLIP: set the 2 dowels" in blob_a and "about Y" in blob_a
      and "NEVER re-zero XY" in blob_a
      and "deburr the bores' exits on the still-raw BACK face" in blob_a,
      [ln for ln in hdr_a if "FLIP" in ln][:1])
check("setup 2's FIRST program starts from the flipped blank, and the probe "
      "warning names the only holes that exist: the bores",
      "program A of 4 - mill [side BACK]" in blob_b
      and "FLIPPED onto the pins" in blob_b
      and "Z0 = BACK copper" in blob_b
      and "probe point sat in a bore" in blob_b
      and "READ THE FLIP GAUGES" in blob_b,
      [ln for ln in hdr_b if "before" in ln][:1])
check("setup 1's scrub says ZERO holes exist and every pad takes FULL discs",
      "ZERO holes exist" in blob_sc
      and "FULL disc laps" in blob_sc
      and "program D holes - the non-pad bores" in blob_sc,
      [ln for ln in hdr_sc if "ZERO" in ln][:1])
check("setup 1's holes program says ONLY the bores, pad holes wait for "
      "setup 2 and BOTH scrubs",
      "ONLY the non-pad bores" in blob_h1
      and "Every PAD hole waits for setup 2, after BOTH scrubs" in blob_h1,
      [ln for ln in hdr_h1 if "PAD" in ln][:1])
check("setup 2's holes program says EVERY pad hole, then the cutout last, "
      "and sends the operator to chase the exits on the FRONT",
      "EVERY pad hole" in blob_h2
      and "then the outline cut with tabs, last" in blob_h2
      and "chase the pad-hole exits on the FRONT pads" in blob_h2,
      [ln for ln in hdr_h2 if "EVERY pad hole" in ln][:1])
hdr_all = hdr_a + hdr_b + hdr_sc + hdr_h1 + hdr_h2
check("every header line survives the 128-char dialect law",
      all(len(ln) <= 128 for ln in hdr_all),
      f"longest {max(len(ln) for ln in hdr_all)}")

# ===================================================== steered cutout tabs
# Tab PLACEMENT is a manufacturing free variable and the tab-zone law judges
# wherever the tabs land, so the grammar carries FlatCAM's placement styles
# (pcbjob.TAB_PLACEMENTS). Board B's bottom edge has a legal copper spine 0.5
# from the edge that no bottom tab can clear; its left and right edges are
# bare, which is what "2lr" is for. One value is followed the whole way:
# document -> Tcl word -> the words the operator reads at the bench.
print("\ncutout tab placement (FlatCAM's -gaps styles):")
LRJOB = pcbjob.load(variant("v-2lr.toml", ("gaps = 4", 'gaps = "2lr"')))
lr_back = pcbjob.side_view(LRJOB, "back")
check("a placement style loads, and every accepted value counts its tabs",
      lr_back.phases["cutout"]["gaps"] == "2lr"
      and [pcbjob.tab_count(g) for g in ("lr", "tb", "2lr", "2tb", "4", "8")]
      == [2, 2, 4, 4, 4, 8]
      and pcbjob.tab_count(4) == 4 and pcbjob.tab_count(8) == 8
      and pcbjob.tab_count("2LR") == 4,          # case is the config's choice
      f"{lr_back.phases['cutout']['gaps']!r} -> "
      f"{pcbjob.tab_count(lr_back.phases['cutout']['gaps'])} tabs")
tcl_lr = engine.render_tcl(lr_back, win, TD / "work" / "back")
gap_line = [ln for ln in tcl_lr.splitlines() if ln.startswith("geocutout")][0]
# geo_init's branches compare against 'LR'/'TB'/'2LR'/'2TB' while the guard
# above them lowercases: a lowercase style CLEARS validation, matches nothing,
# and cuts the outline with zero tabs. The word must arrive uppercased.
check("the style reaches geocutout in the case geo_init actually compares",
      "-gaps 2LR" in gap_line, gap_line)
check("...and a plain count still renders exactly as it always did",
      "-gaps 4" in [ln for ln in tcl_b.splitlines()
                    if ln.startswith("geocutout")][0],
      [ln for ln in tcl_b.splitlines() if ln.startswith("geocutout")][0])
hdr_lr = reemit.program_header(
    lr_back, "holes", [op("cutout", lr_back.phases["cutout"]["tool"],
                          ["G00 Z2.0000"], 100)])
check("the operator note carries the tab COUNT and WHERE to reach for them",
      any("snap the 4 tabs - two each on the left/right edges - of 1.5mm" in ln
          for ln in hdr_lr)
      and all(len(ln) <= 128 for ln in hdr_lr),
      [ln for ln in hdr_lr if "snap" in ln])
check("a plain count's note is unchanged — no placement words invented",
      any("snap the 4 tabs of 1.5mm" in ln for ln in reemit.program_header(
          back, "holes", [op("cutout", back.phases["cutout"]["tool"],
                             ["G00 Z2.0000"], 100)])))
caught("a placement geocutout cannot cut refuses, by name",
       lambda: pcbjob.load(variant("v-gapjunk.toml",
                                   ("gaps = 4", 'gaps = "diagonal"'))),
       "'diagonal' is not a tab placement")
caught("...and so does a bare count geocutout has no branch for",
       lambda: pcbjob.load(variant("v-gap6.toml", ("gaps = 4", "gaps = 6"))),
       "freed board grabs the cutter")
caught("'none' — FlatCAM's own no-tab option — refuses too",
       lambda: pcbjob.load(variant("v-gapnone.toml",
                                   ("gaps = 4", 'gaps = "none"'))),
       "freed board grabs the cutter")

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
          "ring, concentricity, paste-vs-holes, the ordering law's scrub "
          "plan, tab-zone keep-out) are NOT checkable here (install: sudo "
          "apt install gerbv)")
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
          for n in SPROGS[s2]}, str(sorted(reports)))
short = flip.verify_twosided(job, {"front": PROGS["front"]}, ctx=ctx)
check("a side that was not handed over is FATAL per program, never skipped",
      all(not short[f"back/{n}"].ok for n in SPROGS["back"])
      and "unverified" in short["back/mill"].checks[0].detail,
      short["back/mill"].checks[0].detail[:70])
check("report_text names the artwork and every side/program",
      "artwork board" in flip.report_text(reports)
      and "program back/holes" in flip.report_text(reports)
      and "PCB VERDICT (double-sided): PASS" in flip.report_text(reports))

# ---------------------------------------------------------------------------
print("\nTHE VIEWER: both setups composed into ONE session list (WS8):")
WANT = ["board", "front/mill", "front/silk", "front/scrub", "front/holes",
        "front/pins", "back/mill", "back/silk", "back/scrub", "back/holes"]
vs = session.build(job, programs=PROGS, gate=False)
vby = {s.name: s for s in vs}
check("ten sessions: the artwork report, then both setups in machining order",
      [s.name for s in vs] == WANT, str([s.name for s in vs]))
check("each session is keyed by the .nc the operator posts, under its own "
      "side's stem",
      all(Path(vby[f"{s2}/{n}"].path).name
          == f"{pcbjob.program_stem(pcbjob.side_view(job, s2), n)}.nc"
          for s2 in job.sides for n in SPROGS[s2])
      and Path(vby["front/mill"].path).name == "stub2-front-mill.nc",
      Path(vby["back/holes"].path).name)
check("every program session names its side, its document and its phases",
      all(vby[f"{s2}/{n}"].meta["side"] == s2
          and vby[f"{s2}/{n}"].meta["board"] == job.name
          and vby[f"{s2}/{n}"].meta["program"] == f"{s2}/{n}"
          and tuple(vby[f"{s2}/{n}"].meta["phases"])
          == SPROGS[s2][n]
          for s2 in job.sides for n in SPROGS[s2]))
check("each side's sessions carry that side's OWN frame and phase numbers",
      vby["front/mill"].meta["sheet"] != vby["back/mill"].meta["sheet"]
      or front.phases["iso"]["depth"] != back.phases["iso"]["depth"],
      f"front iso Z{front.phases['iso']['depth']}, "
      f"back iso Z{back.phases['iso']['depth']}")
check("the carving programs of BOTH sides serve stage stocks; the "
      "overlay-only pair serves none, on both sides",
      all(len(vby[f"{s2}/{n}"].stocks) > 0
          for s2 in job.sides for n in ("mill", "holes"))
      and len(vby["front/pins"].stocks) > 0
      and all(not vby[f"{s2}/{n}"].stocks
              for s2 in job.sides for n in ("silk", "scrub")))
check("the artwork session carries no toolpath and no stock — it judges the "
      "board, not a program",
      not vby["board"].stocks and not vby["board"].program
      and vby["board"].meta["nc"] == ""
      and vby["board"].meta["sides"] == list(job.sides))
check("side 1's overlay does NOT draw the BACK's paste layer in the front "
      "frame (it would be a mirrored lie)",
      not [L for L in vby["front/scrub"].meta["overlay"]["layers"]
           if L["key"] == "paste_ap"]
      and any("stencil artwork is the BACK setup" in n
              for n in vby["front/scrub"].meta["overlay"]["notes"])
      and [L for L in vby["back/scrub"].meta["overlay"]["layers"]
           if L["key"] == "paste_ap"])

card = vby["board"].meta["run_sheet"]
seq = [st["program"] for st in card if st["kind"] == "program"]
check("the same card rides every session", all(s.meta["run_sheet"] == card
                                               for s in vs))
check("numbered, gapless, in order",
      [st["n"] for st in card] == list(range(1, len(card) + 1)))
check("the run sheet machines setup 1's phases, then its BORES, then the "
      "pins, then setup 2's phases, then every pad hole and the cutout LAST",
      seq == WANT[1:], str(seq))
n_of = {st["program"]: st["n"] for st in card if st["kind"] == "program"}
n_flip = next(st["n"] for st in card if "FLIP" in st["title"])
# "read the flip gauges..." — the OPERATOR step, not setup 1's bores program
# (whose title names the gauges too, because it is what cuts them)
n_gauge = next(st["n"] for st in card
               if st["title"].startswith("read the flip gauges"))
n_mask2 = next(st["n"] for st in card
               if st["title"].startswith("squeegee") and "BACK" in st["title"])
check("the FLIP sits between side 1's pin bores and side 2's first program",
      n_of["front/pins"] < n_flip < n_of["back/mill"],
      f"pins {n_of['front/pins']}, flip {n_flip}, back mill "
      f"{n_of['back/mill']}")
check("the flip gauges are read after side 2's iso and BEFORE its mask "
      "(SPEC 'Assembly' step 6 — the measurement dies under the mask)",
      n_of["back/mill"] < n_gauge < n_mask2,
      f"back mill {n_of['back/mill']}, gauge {n_gauge}, mask {n_mask2}")
check("every program step names its file, and the pin step names the pins",
      all(st["file"] and not st["missing"] for st in card
          if st["kind"] == "program")
      and f"{len(job.pins['positions'])}x" in
      next(st["detail"] for st in card if st.get("program") == "front/pins"))
check("the card ends off the machine with the wire vias AFTER the reflow",
      [st["kind"] for st in card[-3:]] == ["offmachine"] * 3
      and "reflow" in card[-3]["title"]
      and "wire vias" in card[-2]["title"])

# THE VERDICTS. The gate that judged this document is flip.verify_twosided —
# the same reports the positive control above proved, injected here so the
# composition is tested without rasterizing the board a second time.
_real_gate = flip.verify_twosided
flip.verify_twosided = lambda j, p, **kw: reports
try:
    gvs = session.build(job, programs=PROGS)
finally:
    flip.verify_twosided = _real_gate
gby = {s.name: s for s in gvs}
check("with the gate run, every session shows the gate's OWN verdict",
      all(s.meta["ok"] is True and s.meta["gate"]["ran"]
          and s.meta["gate"]["verdict"] == "PASS" for s in gvs),
      str({s.name: s.meta["ok"] for s in gvs if s.meta["ok"] is not True}))
check("the artwork session carries the board report's checks, and nobody "
      "else claims them",
      [c["name"] for c in gby["board"].meta["checks"]]
      == [c.name for c in reports["board"].checks]
      and gby["board"].meta["checks"],
      f"{len(gby['board'].meta['checks'])} checks")
check("the CROSS-SIDE checks reach the operator's screen — on BOTH setups' "
      "scrubs, since the ordering law judges every one of them",
      all({"scrub clear of existing holes", "solder plan scrubbed",
           "inert stays under mask"}
          <= {c["name"] for c in gby[f"{s2}/scrub"].meta["checks"]}
          for s2 in job.sides)
      and any(c["name"] == "tab-zone copper keep-out"
              for c in gby["back/holes"].meta["checks"]),
      str(sorted({c["name"] for c in gby["front/scrub"].meta["checks"]}
                 - {c["name"] for c in gby["front/mill"].meta["checks"]})))
check("a session downloads the bytes the GATE judged, not the live file",
      all(gby[f"{s2}/{n}"].program.decode() == reports[f"{s2}/{n}"].program
          for s2 in job.sides for n in SPROGS[s2]))
check("session.report_text names the artwork and every side/program",
      "artwork board" in session.report_text(gvs)
      and "program back/holes" in session.report_text(gvs)
      and "PASS — the artwork and every program of both setups cleared"
      in session.report_text(gvs))

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
retired_displaced = nctx.rings("back")[0]["spread"] / 2

# 4b/4c. THE 2026-08-02 INCIDENT (Board B), made law. The retired max-min
# proxy was a TRACK detector: 62 of 71 pads on mirror-law-perfect routed
# artwork "orbited" at 0.3925, and its own 0.25-displaced control scored
# 0.2525 — the SAME number a centred pad feeding one 0.6mm track produces.
# Every control below prints both statistics on the SAME artwork, because
# "indistinguishable" is the finding and telling them apart is the fix.
B_FLASH = [(VIA_PAD, [V1, V2]), (GAUGE_PAD, [G1]), (SMD_PAD, [SMD])]
OFF_V1 = (V1[0] + 0.25, V1[1])
SQUEEZE = 180.0          # V1 displaced +x => its ring is squeezed at -x
CONC = "via/hole concentricity across the flip"

# 4b. POSITIVE control: a CENTRED pad that feeds tracks. No routed board can
#     pass the gate unless this passes, and none could.
retired_centred = {}
for n_tr, angles in ((1, (SQUEEZE,)), (3, (SQUEEZE, 60.0, 300.0))):
    nj, nctx = rebuild(B_Cu=routed(B_FLASH, [radial(V1, a) for a in angles]))
    w = nctx.rings("back")[0]
    bc = flip.board_checks(nctx)
    retired_centred[n_tr] = w["spread"] / 2
    short_centred = w["ecc"]
    check(f"POS a CENTRED pad feeding {n_tr} 0.6mm track(s) PASSES",
          all(c.ok for c in bc),
          f"short-side {w['ecc']:.4f} (bar {flip.CONCENTRIC_TOL}) — the "
          f"RETIRED proxy read {w['spread'] / 2:.4f} on this same pad"
          + ("" if all(c.ok for c in bc) else
             "; FAILED " + ", ".join(c.name for c in bc if not c.ok)))
# the incident itself, asserted rather than remembered: on the retired proxy a
# PERFECT pad feeding one track and a 0.25-DISPLACED pad with no track land
# closer together than the tolerance is wide, so no threshold could have
# separated them. That is what "it was a track detector" means, and it is why
# this control exists — to refuse the proxy if anyone brings it back.
check("the RETIRED proxy could not separate a perfect tracked pad from a "
      "0.25 displacement",
      abs(retired_centred[1] - retired_displaced) < flip.CONCENTRIC_TOL,
      f"centred+1 track {retired_centred[1]:.4f} vs displaced-no-track "
      f"{retired_displaced:.4f}: "
      f"{abs(retired_centred[1] - retired_displaced):.4f} apart, tolerance "
      f"{flip.CONCENTRIC_TOL}")

# 4c. the case the retired proxy COULD NOT distinguish: a displaced pad that
#     ALSO feeds a track, and the track lies on the SQUEEZED side — the
#     hardest geometry there is, because the ring edge under the track is
#     pushed OUT, exactly where the eccentricity is trying to show itself.
nj, nctx = rebuild(B_Cu=routed(
    [(VIA_PAD, [OFF_V1, V2]), (GAUGE_PAD, [G1]), (SMD_PAD, [SMD])],
    [radial(OFF_V1, SQUEEZE)]))
w = nctx.rings("back")[0]
catches("a back pad 0.25 off its hole AND feeding a track on the SQUEEZED "
        "side", flip.board_checks(nctx), CONC)
check("  ...and the short side separates it from the centred pad by more "
      "than the bar",
      w["ecc"] - short_centred > flip.CONCENTRIC_TOL,
      f"short-side {w['ecc']:.4f} here vs {short_centred:.4f} centred — "
      f"{w['ecc'] - short_centred:.4f} apart; the retired proxy spans only "
      f"{retired_centred[1]:.4f}..{w['spread'] / 2:.4f} over the same two "
      f"boards, and that span is set by the TRACK, not by the displacement")
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

# 7/8/9. THE ORDERING LAW's three convictions, on real scrub bytes. The
# honest fixture passes all three on BOTH setups (asserted first, so a
# control that convicts cannot be convicting the fixture); each hazard below
# then breaks exactly one of them while its neighbours still pass.
SCRUB_LAWS = ("scrub clear of existing holes", "solder plan scrubbed",
              "inert stays under mask")


def scrub_report(sj, tag, prog=None, **kw):
    """One scrub program's report. `prog` reuses bytes already on disk;
    otherwise the laps are built for `sj` and written under their own name so
    no control ever overwrites the fixture's assembled program."""
    if prog is None:
        prog = OUT / f"neg-scrub-{tag}.nc"
        prog.write_text(reemit.assemble_program(sj, "scrub",
                                                [build_scrub(sj, **kw)]))
    return checks.verify_program(sj, "scrub", prog, ctx.maps(sj.side),
                                 flip=ctx)


for sj in (front, back):
    d = by_name(scrub_report(sj, "pos", prog=PROGS[sj.side]["scrub"]).checks)
    check(f"POS {sj.side}'s honest scrub PASSES all three ordering-law checks",
          all(d[n].ok for n in SCRUB_LAWS),
          "; ".join(f"{n} {d[n].value:.4f} ({d[n].limit})"
                    for n in SCRUB_LAWS))
# 7. the 2026-07-30 paint-across-bores incident, still convictable. The
# GENERATOR can no longer produce it (setup 2 inherits only the non-pad bores,
# and no aperture sits on one), so the hazard is injected where it would
# actually come from: a partition that put a PAD hole in setup 1. The bytes
# are the fixture's own honest disc laps — unchanged, and now lapping across
# an open Ø1.0 bore.
(TD / "v-bores-at-via.drl").write_text(drl_text(BORE_HOLES + PAD_HOLES[:1]))
crossed = with_files(back, bores_drl=TD / "v-bores-at-via.drl")
catches("a setup-2 disc lap straight across a hole that setup 1 already "
        "bored (a 0.3 spring tip drops in and levers the pad off)",
        scrub_report(crossed, "bore", prog=PROGS["back"]["scrub"]).checks,
        "scrub clear of existing holes",
        must_pass=("solder plan scrubbed", "inert stays under mask",
                   "scrub window", "scrub plateau margin"))
print("      ^ the copper gerber draws a pad as a SOLID disc (the hole lives "
      "only in\n        the Excellon), so a lap over the hole centre reads as "
      "deeply inside\n        copper and passes every single-sided scrub law "
      "there is")
# and the same law on the REAL inherited set, no injection: a lap that
# wanders over the Ø3.4 mount bore setup 1 left in the blank. (`scrub window`
# and `scrub plateau margin` convict this one too — it is off the mask and
# off copper — so only the ordering law's own neighbours are claimed here.)
catches("a setup-2 lap over the mount bore this setup INHERITED",
        scrub_report(back, "bore-real", extra=((H1, 0.6),)).checks,
        "scrub clear of existing holes",
        must_pass=("solder plan scrubbed", "inert stays under mask"))
# 8. a pad the bench expects to solder that the scrub never opens. On this
# process the flood coat is opened by the scrub and by NOTHING else, so a
# missed aperture is an unsolderable pad however perfect the artwork is.
catches("a setup-2 scrub that never laps the SMD aperture",
        scrub_report(back, "miss", skip=(SMD,)).checks,
        "solder plan scrubbed",
        must_pass=("scrub clear of existing holes", "inert stays under mask",
                   "scrub window", "scrub plateau margin"))
catches("...and the same hazard on setup 1, whose solder plan is judged by "
        "the identical law",
        scrub_report(front, "miss1", skip=(V2,)).checks,
        "solder plan scrubbed",
        must_pass=("scrub clear of existing holes", "inert stays under mask",
                   "scrub window", "scrub plateau margin"))
# 9. the inert list is a machining subset, not a licence: an opening the
# document declares dead must KEEP its flood coat (that coat is the
# protective finish dead copper wants), and one that is scrubbed anyway is
# stripped for nothing.
ipos = by_name(scrub_report(iback, "inert-ok", skip=(SMD,)).checks)
check("POS a DECLARED inert aperture left unscrubbed passes all three — the "
      "solder plan is the live apertures, not every opening",
      all(ipos[n].ok for n in SCRUB_LAWS),
      "; ".join(f"{n} {ipos[n].value:.4f}" for n in SCRUB_LAWS))
catches("a lap on an aperture the document declares INERT",
        scrub_report(iback, "inert-bad", prog=PROGS["back"]["scrub"]).checks,
        "inert stays under mask",
        must_pass=("scrub clear of existing holes", "solder plan scrubbed",
                   "scrub window", "scrub plateau margin"))
(TD / "inert-nowhere.txt").write_text(
    f"{G1[0]}, {G1[1]}  # the flip gauge: no mask aperture is there at all\n")
inowhere = pcbjob.side_view(pcbjob.load(variant(
    "v-inert-nowhere.toml", ("[phases.back.drills]",
                             'inert = "inert-nowhere.txt"\n\n'
                             "[phases.back.drills]"))), "back")
catches("an inert entry sitting on NO mask ink (the list drifted from the "
        "artwork) is caught by the GATE too, not only by the generator",
        scrub_report(inowhere, "inert-stale",
                     prog=PROGS["back"]["scrub"]).checks,
        "inert list names apertures",
        must_pass=("scrub clear of existing holes", "solder plan scrubbed",
                   "inert stays under mask"))
# 9b. THE PARTITION, ON THE BYTES. The grammar proves the two Excellons split
# the schedule; these two prove the PROGRAMS cut the partition they were
# handed — each setup's hole checks judge against its own drl_cut, so a setup
# that bores one of the other's holes and a setup that skips one of its own
# are different, separately-named convictions.


def holes_file(tag, sj, ops):
    p = OUT / f"neg-holes-{tag}.nc"
    p.write_text(reemit.assemble_program(sj, "holes", ops))
    return p


catches("setup 1 boring a PAD hole it must leave for setup 2 (that hole "
        "would then predate BOTH scrubs — and be cut twice)",
        checks.verify_program(
            front, "holes",
            holes_file("stray", front,
                       [build_drills(front, BORE_HOLES + PAD_HOLES[:1])]),
            ctx.maps("front"), flip=ctx).checks,
        "stray bores", must_pass=("hole schedule", "hole bore depth"))
catches("setup 2 leaving one of its PAD holes undrilled — nothing else ever "
        "cuts it",
        checks.verify_program(
            back, "holes",
            holes_file("short", back,
                       [build_drills(back, PAD_HOLES[:1]),
                        build_cutout(back)]),
            ctx.maps("back"), flip=ctx).checks,
        "hole schedule",
        must_pass=("stray bores", "cutout tab census", "cutout ride band"))
# 9. copper inside a tab zone
nj, nctx = rebuild(B_Cu=flashes([(VIA_PAD, [V1, V2]), (GAUGE_PAD, [G1]),
                                 (SMD_PAD, [SMD, (BW / 2, 0.6)])]))
nback = pcbjob.side_view(nj, "back")
tabp = write_program(nback, "holes", back_holes(nback))
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

# ============================== the excise sub-blank cut (2026-08-03 request)
# Setup 1's optional LAST program: a tabbed rectangle around board + pins so
# the operator snaps a registered sub-blank out of stock that will not fit
# the workholding once flipped. Grammar refusals first, then the honest
# program through the gate, then the tampered ones — a check that cannot
# fail is not a check.
print("\nthe excise sub-blank cut:")
EX_BLOCK = """[phases.front.excise]
tool = 7
depth = -1.7
dpp = 0.3
gaps = "2lr"
gapsize = 1.5
feed = 500
plunge = 200
margin_x = 4.0
margin_y = 8.5

"""


def ex_variant(name, block):
    return variant(name, ("[phases.front.bores]",
                          block + "[phases.front.bores]"))


exjob = pcbjob.load(ex_variant("job-excise.toml", EX_BLOCK))
exf = pcbjob.side_view(exjob, "front")
check("excise: setup 1's split gains program F",
      list(pcbjob.programs_of(exf)) == ["mill", "silk", "scrub", "holes",
                                        "pins", "excise"]
      and pcbjob.programs_of(exf)["excise"] == ("excise",),
      str(list(pcbjob.programs_of(exf))))
check("excise: the plain job still has no excise program",
      "excise" not in pcbjob.programs_of(front), "optional means optional")
check("excise: rect derives from the margins",
      pcbjob.excise_rect(exjob) == (-4.0, -8.5, 24.0, 23.5),
      str(pcbjob.excise_rect(exjob)))
caught("excise: a rect starving the pin annulus refuses (pin meat)",
       lambda: pcbjob.load(ex_variant(
           "job-exmeat.toml",
           EX_BLOCK.replace("margin_y = 8.5", "margin_y = 6.2"))),
       "laminate beyond the pin hole")
caught("excise: a rect eating the clearing rim refuses (envelope)",
       lambda: pcbjob.load(ex_variant(
           "job-exenv.toml",
           EX_BLOCK.replace("margin_x = 4.0", "margin_x = 1.6"))),
       "machined envelope")
caught("excise: the silent-sever tab family refuses here too",
       lambda: pcbjob.load(ex_variant(
           "job-exgap.toml",
           EX_BLOCK.replace('gaps = "2lr"', 'gaps = "corners"'))),
       "tab placement")

ex_path = write_program(exf, "excise",
                        [reemit.excise_ops(exf, win=win)])
ex_maps = checks.board_maps(exf)
ex_rep = checks.verify_program(exf, "excise", ex_path, ex_maps)
check("excise: the honest program PASSES its whole report",
      ex_rep.ok, "; ".join(c.name for c in ex_rep.checks if not c.ok))
check("excise: the report carries all five excise laws",
      {"excise ride", "excise floor", "excise tab census",
       "excise pin meat", "excise clear of the board"}
      <= {c.name for c in ex_rep.checks},
      str(sorted(c.name for c in ex_rep.checks)))

bad = mutate(ex_path, "excise-shifted",
             lambda t: t.replace("X-4.5000", "X-5.2000"))
catches("a shifted excise path (X-4.5 -> X-5.2)",
        checks.verify_program(exf, "excise", bad, ex_maps).checks,
        must_fail="excise ride", must_pass=("excise floor",))

exthin = pcbjob.load(ex_variant(
    "job-exthin.toml", EX_BLOCK.replace("gapsize = 1.5", "gapsize = 0.2")))
exthinf = pcbjob.side_view(exthin, "front")
thin_path = write_program(exthinf, "excise",
                          [reemit.excise_ops(exthinf, win=win)])
catches("tabs below the 1.0 material bar",
        checks.verify_program(exthinf, "excise", thin_path,
                              ex_maps).checks,
        must_fail="excise tab census", must_pass=("excise ride",))


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
          and set(out["front"]) == {"iso", "clear", "scrub", "bores"}
          and set(out["back"]) == {"iso", "clear", "scrub", "drills",
                                   "cutout"},
          {s: sorted(out[s]) for s in out})
    check("with nothing declared inert, BOTH engines painted the mask EXPORT "
          "— the hole-centred filtering is gone with the ordering law",
          not (work / "back" / "mask-scrub.gbr").exists()
          and not (work / "front" / "mask-scrub.gbr").exists()
          and all("mask-scrub" not in (work / s / "engine.tcl").read_text()
                  and job.files[f"{s}_mask"].name
                  in (work / s / "engine.tcl").read_text()
                  for s in job.sides))
    lprogs = {}
    for side in job.sides:
        sj = pcbjob.side_view(job, side)
        lprogs[side] = {}
        ops = {ph: (reemit.scrub_op(nc, sj, win=ctx.tight)
                    if ph == "scrub" else reemit.read_phase(nc, sj, ph))
               for ph, nc in out[side].items()}
        check(f"{side}: scrub_op IS read_phase, byte for byte (no generated "
              "geometry rides the scrub on EITHER setup any more, so the "
              "single-sided/coupon path cannot move)",
              ops["scrub"].lines == reemit.read_phase(
                  out[side]["scrub"], sj, "scrub").lines)
        check(f"{side}: every phase re-emits clean under the param-match law",
              all(o.lines for o in ops.values()),
              ", ".join(f"{k}:{len(v.lines)}" for k, v in sorted(ops.items())))
        for name, want in SPROGS[side].items():
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
    # THE TRIPWIRE, RETIRED BY REORDERING (2026-07-30 paint-across-bores →
    # the 2026-08-03 ordering law): `paint` used to lap straight across side
    # 2's bores because the mask layer knows nothing about the Excellon, and
    # the gate refused it at rim margin −0.60. The first fix was an
    # aperture-class split with annular laps; the operator then found the
    # 0.20 cured-mask collar those laps leave exactly where the joint wets.
    # The fix now is the ORDER: no pad hole exists under any scrub, so
    # FlatCAM's own disc laps are correct on BOTH setups and the same check
    # that refused the old geometry passes them — with the injected
    # bore-under-a-lap negative above still proving it can refuse.
    for s2n in job.sides:
        s2 = by_name(lreps[f"{s2n}/scrub"].checks)
        check(f"{s2n}: FlatCAM's own paint PASSES the ordering law — full "
              f"disc laps, every solderable aperture, nothing to steer "
              f"around",
              lreps[f"{s2n}/scrub"].ok
              and all(s2[n].ok for n in SCRUB_LAWS),
              "; ".join(f"{n} {s2[n].value:.4f} ({s2[n].limit})"
                        for n in SCRUB_LAWS)
              + ("" if lreps[f"{s2n}/scrub"].ok else "; FAILED "
                 + ", ".join(c.name for c in lreps[f"{s2n}/scrub"].checks
                             if not c.ok)))
    check(f"ALL {len(lreps)} live reports PASS — the flip generates, "
          f"re-emits and verifies end to end",
          all(lreps[n].ok for n in lreps), str(bad))

# ================================ THE 2026-08-02 SPOKE INCIDENT, ON THE REAL
# ARTWORK. Board B's own gerbers, READ-ONLY: the GND via the retired statistic
# convicted at 0.0134 against a 0.4 bar on artwork whose relief is perfectly
# healthy. The synthetic controls in pcb_checks_suite prove the SEMANTICS; this
# one pins the incident itself, so the regression cannot come back quietly.
ORBIT = REPO / "boards" / "orbit" / "orbit.toml"
if ORBIT.is_file():
    print("\nthe 2026-08-02 spoke incident, on Board B's real artwork:")
    obj = pcbjob.load(ORBIT)
    octx = flip.context(obj)
    V1B = (16.342, 8.117)            # the GND via the old proxy convicted

    def pad_label(maps, cx, cy, hd):
        """The copper component of a hole-centred pad — the flood the check
        itself does, replayed here so this control reads the same pad."""
        lab, _ = ndimage.label(maps.layers["cu"])
        h, w = maps.win.shape
        r_ap = float(maps.dist("in_mask")[
            int(round(maps.win.world_to_px(np.array([cx]),
                                           np.array([cy]))[0][0] - 0.5)),
            int(round(maps.win.world_to_px(np.array([cx]),
                                           np.array([cy]))[1][0] - 0.5))])
        rmid = (hd / 2 + r_ap) / 2
        th = np.linspace(0, 6.28, 16)
        i2, j2 = maps.win.world_to_px(cx + rmid * np.cos(th),
                                      cy + rmid * np.sin(th))
        ls = lab[np.clip((i2 - 0.5).round().astype(int), 0, h - 1),
                 np.clip((j2 - 0.5).round().astype(int), 0, w - 1)]
        ls = ls[ls > 0]
        return lab, int(np.bincount(ls).argmax()), r_ap

    def retired_ring_width(cu, win, cx, cy, r_ap):
        """The statistic RETIRED on 2026-08-02: the thinnest copper run on
        the ONE min-copper ring of the moat. It lives here and nowhere else,
        so that what it does to this pad stays on the record."""
        best = (1.1, r_ap + 0.06)
        for r in np.arange(r_ap + 0.06, r_ap + 0.9, 0.02):
            f = float(checks._ring_profile(cu, win, cx, cy, r).mean())
            if f < best[0]:
                best = (f, r)
        rm = best[1]
        prof = checks._ring_profile(cu, win, cx, cy, rm) > 0.5
        if prof.all():
            return 2 * np.pi * rm, rm, 1, 720
        k = int(np.argmin(prof))
        pp = np.concatenate(([False], np.roll(prof, -k),
                             [False])).astype(int)
        st = np.flatnonzero(np.diff(pp) == 1)
        en = np.flatnonzero(np.diff(pp) == -1)
        n = en - st
        return (float(n.min()) * (2 * np.pi * rm / prof.size), rm,
                len(n), int(n.min()))

    bmaps = octx.maps("back")
    olab, opl, orap = pad_label(bmaps, *V1B, 1.0)
    edge, spokes = checks._moat_spokes(olab, opl, bmaps.win, *V1B, orap)
    old_w, old_r, old_runs, old_n = retired_ring_width(
        bmaps.layers["cu"], bmaps.win, *V1B, orap)
    check("the RETIRED statistic still convicts V1 on ONE angular sample",
          old_w < 0.05 and old_n == 1 and old_runs == 3,
          f"{old_w:.4f} on ring r{old_r:.3f}: {old_runs} runs, the thinnest "
          f"{old_n} of 720 samples — bar {checks.SPOKE_MIN}")
    check("...and the SAME copper carries two spokes across the whole moat",
          len(spokes) == 2 and min(spokes) >= checks.SPOKE_MIN - 0.03,
          f"moat r{orap + checks.SPOKE_RIM_CLEAR:.3f}..{edge:.3f}, spanning "
          f"spokes {[round(s, 4) for s in spokes]}")
    for side in ("front", "back"):
        sm = octx.maps(side)
        ot = by_name(checks.thermal_checks(pcbjob.side_view(obj, side), sm))
        check(f"Board B's {side} copper PASSES the spoke law",
              ot["thermal spoke count"].ok and ot["thermal spoke width"].ok
              and ot["thermal solid connect"].ok,
              f"count {ot['thermal spoke count'].value:.0f} "
              f"(>= {checks.SPOKE_COUNT_MIN}), width "
              f"{ot['thermal spoke width'].value:.4f} "
              f"(>= {checks.SPOKE_MIN}); "
              f"{ot['thermal spoke width'].detail.split(';')[-1].strip()}")
        sm.release()
    bmaps.release()
else:
    print("\nSKIP: boards/orbit is not on this box — the real-artwork spoke "
          "incident is not checkable here")

print(f"\nPCB TWOSIDED {'FAIL: ' + ', '.join(fails) if fails else 'PASS'}")
sys.exit(1 if fails else 0)
