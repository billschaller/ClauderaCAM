"""PCB engine + grammar (WS3/4): the [pcb] TOML gate, templated Tcl,
and the strict re-emission reader.

What must hold, and why:
  - the grammar refuses what the bench taught: scrub outside the
    preload band (-0.25 peeled traces), cutout without real tabs (a
    freed board grabs the cutter), depths that miss the blank or
    excavate the spoilboard, wrong tool class per phase, non-PCB
    materials, no spoilboard
  - the Tcl is TEMPLATED with the DERIVED transform (never hand-written
    numbers) and ends with the sentinel the runner polls for
  - read_phase() enforces the param-match law on engine output: wrong
    spindle, stray feeds, or a floor Z that isn't the configured depth
    all refuse (the stale-ZMIN incident class)
  - read_phase() FOLDS FlatCAM's coordinate-less feed setters (`G01
    F500.00`) onto the next motion line, so an assembled [pcb] program
    contains only fully-worded moves and rides simulate.prep_moves —
    the mill gate is never relaxed to accept the interchange dialect
    (2026-07-30, the unsimulatable-program incident)
  - silk strokes come from the gerber's draw words; flashes refuse; and
    a stroke is CLIPPED against the mask apertures dilated by the silk
    clearance, mid-segment, not dropped whole (2026-07-30, the
    field-legend incident)

Run: .venv/bin/python tests/pcb_engine_suite.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np

from clauderacam import emit, simulate
from clauderacam.pcb import boardmaps as bm
from clauderacam.pcb import engine, pcbjob, reemit

REPO = Path(__file__).resolve().parents[1]
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
        check(name, needle in str(e), f"got: {str(e)[:90]}")
        return
    check(name, False, "no exception raised")


TD = Path(tempfile.mkdtemp(prefix="clauderacam-ws3-"))
G = TD / "gerbers"
G.mkdir()


def _w(name, text):
    p = TD / name
    p.write_text(text)
    return p

EDGE = """%FSLAX46Y46*%
%MOMM*%
%ADD10C,0.050000*%
G01*
D10*
X0Y0D02*
X20000000Y0D01*
X20000000Y15000000D01*
X0Y15000000D01*
X0Y0D01*
M02*
"""
STUB = EDGE  # content only matters for the edge layer in these tests
SILK = """%FSLAX46Y46*%
%MOMM*%
%ADD10C,0.200000*%
G01*
D10*
X2000000Y2000000D02*
X5000000Y2000000D01*
X5000000Y4000000D01*
X8000000Y8000000D02*
X9000000Y8000000D01*
M02*
"""
DRL = "M48\nFMAT,2\nMETRIC\nT1C0.800\n%\nG90\nG05\nT1\nX5.0Y5.0\nT0\nM30\n"

(G / "brd-Edge_Cuts.gbr").write_text(EDGE)
(G / "brd-B_Cu.gbr").write_text(STUB)
(G / "brd-B_Mask.gbr").write_text(STUB)
(G / "brd-B_Silkscreen.gbr").write_text(SILK)
(G / "brd.drl").write_text(DRL)

JOB = f"""
[pcb]
name = "engine-test"
stem = "brd"
gerbers = "gerbers"
out = "out"

[blank]
width = 70.0
height = 50.0
thickness = 1.5
anchor = [10.0, 5.0]

[spoilboard]
thickness = 5.0

[material]
name = "fr4"

[machine]
inventory = "{REPO}/jobs/inventory.toml"

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
num = 7
type = "flat"
diameter = 1.0
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

[phases.iso]
tool = 2
depth = -0.15
feed = 500
plunge = 200

[phases.clear]
tool = 3
depth = -0.15
margin = 1.4
offset = 0.05
overlap = 25
feed = 500
plunge = 200

[phases.silk]
clearance = 0.3

[phases.scrub]
tool = 5
depth = -0.21
overlap = 45
offset = 0.15
feed = 400
plunge = 200

[phases.drills]
tool = 3
depth = -1.7
dpp = 0.5
feed = 300
plunge = 200

[phases.cutout]
tool = 7
depth = -1.7
dpp = 0.3
gaps = 4
gapsize = 1.5
feed = 500
plunge = 200
"""
(TD / "job.toml").write_text(JOB)


def variant(old, new, name):
    p = TD / name
    p.write_text(JOB.replace(old, new))
    return p


print("grammar:")
j = pcbjob.load(TD / "job.toml")
check("job loads against the real crib", j.name == "engine-test"
      and j.phase_tool("iso").type == "vee"
      and j.phases["silk"]["dose"] == 0.03)
caught("scrub outside the preload band refuses",
       lambda: pcbjob.load(variant("depth = -0.21", "depth = -0.30",
                                   "v1.toml")), "preload band")
caught("tab-less cutout refuses",
       lambda: pcbjob.load(variant("gaps = 4", "gaps = 1", "v2.toml")),
       "freed board")
caught("cutout that misses the blank refuses",
       lambda: pcbjob.load(variant('depth = -1.7\ndpp = 0.3',
                                   'depth = -1.2\ndpp = 0.3', "v3.toml")),
       "break through")
caught("brass pcb refuses",
       lambda: pcbjob.load(variant('name = "fr4"', 'name = "brass"',
                                   "v4.toml")), "no place")
caught("no spoilboard refuses",
       lambda: pcbjob.load(variant("[spoilboard]\nthickness = 5.0",
                                   "[spoilboard]\nthickness = 0.0",
                                   "v5.toml")), "spoilboard")
caught("wrong tool class refuses",
       lambda: pcbjob.load(variant("[phases.iso]\ntool = 2",
                                   "[phases.iso]\ntool = 3", "v6.toml")),
       "vee tool")
caught("silk clearance below law refuses",
       lambda: pcbjob.load(variant("clearance = 0.3", "clearance = 0.2",
                                   "v7.toml")), "repels solder")

print("tcl templating:")
win = bm.BoardWindow(0.0, 0.0, 20.0, 15.0)
tcl = engine.render_tcl(j, win, TD / "work")
check("derived offset in the tcl (ax+x1, ay-y0)",
      "offset cu -x 30 -y 5" in tcl)
check("iso cuts with the TIP diameter", "isolate cu -dia 0.2" in tcl)
# multi-pass isolation (the bridging-sliver incident): the ladder comes from
# pcbjob.iso_pass_plan — engine emits it, checks judge it, ONE definition
_np, _ov, _top = pcbjob.iso_pass_plan(j)
check("iso is multi-pass per the shared ladder plan",
      f"-passes {_np} -overlap {_ov:.6g} -combine 0" in tcl,
      f"plan: {_np} passes, top rung {_top}")
check("the passes are JOINED, not combined (FlatCAM's combine keeps only "
      "the last pass)",
      "join_geometry cu_iso " in tcl and "-outname iso_geo" in tcl)
check("clearing uses -method seed (standard silently skips complex "
      "polygons)", "-method seed" in tcl and "-method standard" not in tcl)
# ... and the iso CNCJOB carries the tip too, not the cone's 3.175 shank:
# the -dia the cncjob gets is only a header annotation (re-emission drops
# it), but fc-1-iso.nc printing "TOOL DIAMETER: 3.175" is a lie in a file
# an operator can open (2026-07-30, found on Board A's first live run; the
# assembled mill program was byte-identical across the fix, proving it
# geometry-free)
check("iso cncjob header dia is the TIP, not the shank",
      "cncjob iso_geo -dia 0.2 " in tcl
      and "cncjob iso_geo -dia 3.175" not in tcl)
check("scrub painted at the modeled flat width",
      "paint mask -tooldia 0.3" in tcl)
# the sentinel is a FILE, written last: FlatCAM's embedded Tcl interpreter
# discards `puts` to stdout, so a stdout sentinel is unobservable and every
# successful run would time out (2026-07-30, the unreachable-sentinel
# incident, caught by Board A's first live run)
check("sentinel written LAST, and to a file (stdout is unreachable)",
      tcl.rstrip().endswith(
          f'set fh [open $OUT/{engine.SENTINEL_FILE} w]\n'
          f'puts $fh "{engine.SENTINEL}"\nclose $fh')
      and tcl.index(engine.SENTINEL_FILE) > tcl.rindex("write_gcode"),
      tcl.rstrip().splitlines()[-1])
check("every phase writes its nc",
      all(nc in tcl for nc in engine.PHASE_NC.values()))
check("templating banner present", "DO NOT hand-edit" in tcl)

print("feed-setter folding (the unsimulatable-program incident):")
# FlatCAM's own shape: `G01 F500.00` sets the modal feed and moves nothing.
# prep_moves reads a motion word with no axis word as a cutting move with no
# position established and refuses the file — correctly. Folding fixes it at
# the dialect boundary (Article V), and the F still goes through the
# param-match law on its way.
FOLD_NC = """(interchange, FlatCAM default post shape)
G21
G90
G94
G01 F500.00
G00 Z2.0000
M03 S12000.0
G01 F500.00
G00 X1.0000 Y1.0000
G01 F200.00
G01 Z-0.1500
G01 F500.00
G01 X2.0000 Y1.0000
G00 Z2.0000
M05
G00 Z15.00
"""
fold = reemit.read_phase(_w("fold.nc", FOLD_NC), j, "iso")
check("every feed setter folds onto the next motion line",
      fold.lines == ["G00 Z2.0000 F500.00",
                     "G00 X1.0000 Y1.0000 F500.00",
                     "G01 Z-0.1500 F200.00",
                     "G01 X2.0000 Y1.0000 F500.00",
                     "G00 Z2.0000",
                     "G00 Z15.00"], str(fold.lines))
check("the folded F keeps the SOURCE token (no reformatting drift)",
      all("F500.00" in ln or "F200.00" in ln or "F" not in ln
          for ln in fold.lines))
caught("a stray feed still refuses AFTER folding (param-match law)",
       lambda: reemit.read_phase(
           _w("fold_stray.nc", FOLD_NC.replace("G01 F500.00\nG01 X2.0000",
                                               "G01 F450.00\nG01 X2.0000")),
           j, "iso"), "param-match")
caught("a feed setter with no motion left to fold onto refuses",
       lambda: reemit.read_phase(
           _w("fold_dangle.nc", FOLD_NC + "G01 F500.00\n"), j, "iso"),
       "fold it onto")

print("silk strokes:")
chains = reemit._stroke_chains(G / "brd-B_Silkscreen.gbr")
check("two chains, right shapes",
      len(chains) == 2 and len(chains[0]) == 3 and len(chains[1]) == 2,
      str(chains))
caught("flash in silk refuses", lambda: reemit._stroke_chains(
    _w("flash.gbr", SILK.replace("X9000000Y8000000D01*",
                                 "X9000000Y8000000D03*"))), "flash")

print("silk CLIP against the mask apertures (the field-legend incident):")
# A hand-built mask map — no gerbv needed, so this runs on every box. Disc A
# sits ON the middle of chain 1's first segment, whose ENDPOINTS are both far
# clear: that is exactly the geometry the old vertex test shipped intact.
# Disc B swallows chain 2 whole, so `dropped` still has a witness.
CWIN = bm.BoardWindow(0.0, 0.0, 20.0, 15.0)     # == the tcl transform frame
DISC_A, RA = (3.5, 2.0), 0.5      # dead centre of chain 1's first segment
DISC_B, RB = (8.5, 8.0), 1.0      # swallows chain 2 whole
_h, _w2 = CWIN.shape
_cx, _ = CWIN.px_to_world(0, np.arange(_w2))
_, _cy = CWIN.px_to_world(np.arange(_h), 0)
MASKMAP = ((np.hypot(_cx[None, :] - DISC_A[0], _cy[:, None] - DISC_A[1]) <= RA)
           | (np.hypot(_cx[None, :] - DISC_B[0],
                       _cy[:, None] - DISC_B[1]) <= RB))
CLR = float(j.phases["silk"]["clearance"])
NEED = CLR + reemit.SILK_EPS_PX / CWIN.ppmm + reemit.SILK_EPS_MM
clip = reemit.silk_strokes(j, CWIN, MASKMAP)
check("the crossing chain is CLIPPED, not dropped; the buried one is dropped",
      clip.chains == 2 and clip.clipped == 1 and clip.dropped == 1
      and len(clip.strokes) == 2, clip.note)


def mach(p):                      # this job's derived transform: (30-x, y+5)
    return (30.0 - p[0], p[1] + 5.0)


def near(a, b, tol=1e-9):
    return abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol


s1, s2 = clip.strokes
check("the split lands ON the forbidden circle and the ORIGINAL vertices "
      "survive exactly",
      len(s1) == 2 and len(s2) == 3
      and near(s1[0], mach((2.0, 2.0)))
      and near(s1[1], mach((DISC_A[0] - RA - NEED, 2.0)), 0.02)
      and near(s2[0], mach((DISC_A[0] + RA + NEED, 2.0)), 0.02)
      and near(s2[1], mach((5.0, 2.0))) and near(s2[2], mach((5.0, 4.0))),
      f"{s1} | {s2}")

# INDEPENDENT re-measurement of the emitted bytes' geometry: exact circles,
# no raster anywhere in this loop.
worst = float("inf")
for st in clip.strokes:
    for a, b in zip(st[:-1], st[1:]):
        for t in np.linspace(0.0, 1.0, 801):
            bx = 30.0 - (a[0] + (b[0] - a[0]) * t)     # back to board frame
            by = (a[1] + (b[1] - a[1]) * t) - 5.0
            for (cx, cy), r in ((DISC_A, RA), (DISC_B, RB)):
                worst = min(worst, float(np.hypot(bx - cx, by - cy) - r))
check("every emitted point clears the job clearance by exact geometry",
      worst >= CLR, f"worst exact clearance {worst:.4f} >= {CLR}")
caught("a mask raster with ink on the window border refuses",
       lambda: reemit.silk_strokes(
           j, CWIN, np.ones(CWIN.shape, bool)), "window border")

zb = (Path.home() / "scratch/ha/devices/projects/zigbee-button/pcb/cam"
      / "flatcam/phases/fc-1-iso.nc")
if zb.is_file():
    print("re-emission on the real zigbee iso phase (local bonus):")
    op = reemit.read_phase(zb, j, "iso")
    # BLESSED (Article III): 2486 kept lines. It was 2564 before feed-setter
    # folding — the file carries 78 coordinate-less `G01 F<n>` setters, each
    # of which now rides the next motion line instead of occupying one of its
    # own. path_len_mm is untouched: a feed setter has no XYZ word.
    check("reads clean into an OpResult",
          op.tool == 2 and len(op.lines) == 2486 and op.kind == "iso"
          and abs(op.path_len_mm - 1208.573) < 0.01,
          f"{len(op.lines)} lines, {op.path_len_mm:.3f}mm")
    text = emit.assemble(j, [op])
    check("re-emitted program carries the dwell law",
          "G4 P2" in text and "M6 T2" in text
          and "(begin operation: pcb-iso" in text)
    # DEFECT 2's payoff, on the field bytes: the assembled program goes
    # through the MILL gate's own resolver. Before folding this raised
    # "cutting move before XYZ position is fully established" on line 33.
    # prep_moves reads only the tool table off the job, so the [pcb] job
    # stands in for a Job here.
    prog = _w("zigbee-iso.nc", text)
    mv = simulate.prep_moves(prog, j, 1500.0)
    check("the assembled program parses through simulate.prep_moves",
          mv.motion.size == 2484 and mv.stage_labels == ["pcb-iso"],
          f"{mv.motion.size} moves, stages {mv.stage_labels}")
    caught("wrong depth refuses (stale-ZMIN class)",
           lambda: reemit.read_phase(
               zb, pcbjob.load(variant("depth = -0.15", "depth = -0.20",
                                       "v8.toml")), "iso"), "stale-ZMIN")
    caught("wrong feed refuses (param-match law)",
           lambda: reemit.read_phase(
               zb, pcbjob.load(variant("feed = 500", "feed = 400",
                                       "v9.toml")), "iso"), "param-match")
else:
    print("SKIP: zigbee phase files not on this box — re-emission bonus "
          "not checkable")

print(f"\nPCB ENGINE SUITE {'FAIL: ' + ', '.join(fails) if fails else 'PASS'}")
sys.exit(1 if fails else 0)
