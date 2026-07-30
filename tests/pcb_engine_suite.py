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
  - silk strokes come from the gerber's draw words; flashes refuse

Run: .venv/bin/python tests/pcb_engine_suite.py
"""
import sys
import tempfile
from pathlib import Path

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
check("scrub painted at the modeled flat width",
      "paint mask -tooldia 0.3" in tcl)
check("sentinel last", tcl.rstrip().endswith('puts "ALL-PHASES-DONE"'))
check("every phase writes its nc",
      all(nc in tcl for nc in engine.PHASE_NC.values()))
check("templating banner present", "DO NOT hand-edit" in tcl)

print("silk strokes:")
chains = reemit._stroke_chains(G / "brd-B_Silkscreen.gbr")
check("two chains, right shapes",
      len(chains) == 2 and len(chains[0]) == 3 and len(chains[1]) == 2,
      str(chains))
caught("flash in silk refuses", lambda: reemit._stroke_chains(
    _w("flash.gbr", SILK.replace("X9000000Y8000000D01*",
                                 "X9000000Y8000000D03*"))), "flash")

zb = (Path.home() / "scratch/ha/devices/projects/zigbee-button/pcb/cam"
      / "flatcam/phases/fc-1-iso.nc")
if zb.is_file():
    print("re-emission on the real zigbee iso phase (local bonus):")
    op = reemit.read_phase(zb, j, "iso")
    check("reads clean into an OpResult",
          op.tool == 2 and len(op.lines) > 1000 and op.kind == "iso",
          f"{len(op.lines)} lines, {op.path_len_mm:.0f}mm")
    from clauderacam import emit
    text = emit.assemble(j, [op])
    check("re-emitted program carries the dwell law",
          "G4 P2" in text and "M6 T2" in text
          and "(begin operation: pcb-iso" in text)
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
