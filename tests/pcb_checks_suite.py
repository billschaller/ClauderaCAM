"""PCB check set (WS5): the named, incident-traced checks of
clauderacam.pcb.checks over synthetic gerbers and hand-built programs, plus
NEGATIVE controls for every one of them (Article III: the negatives are what
prove the gate can fail).

What must hold, and why:
  - the raster measurement is CALIBRATED: EDT-to-ink-centers over-reads the
    true polygon distance by exactly half a pixel at every resolution. Every
    threshold in checks.py leans on that constant, so it is asserted against
    a closed-form circle before anything else runs.
  - a hand-built, geometrically exact program for a synthetic board passes
    every check (positive control) — and the checks are exact enough that the
    same board with ONE thing wrong fails by NAME.
  - every hazard from the field record is synthesized and caught: displaced
    drill, extra hole, missing tab, an iso pass that skips an island, clearing
    into copper, clearing a channel narrower than the tool (the castellation
    incident — caught by `clear opening` while `clear keep-out` still passes,
    which is the whole reason both checks exist), scrubbing over a pad edge
    (caught by `scrub plateau margin` while `scrub window` still passes),
    silk on a pad, an UNCLIPPED silk stroke that is clear at both ENDS and
    crosses a pad mid-segment (the 2026-07-30 field-legend incident, moved
    here from the field the moment the field legend started passing), a
    missing focus move, an over-dose S, a stale ZMIN, a wrong F, a wrong
    tool, and a program simply not handed over.
  - LOCAL BONUS: on the operator's zigbee-button V2 run set the whole chain
    is re-assembled and verified, and the first PASS is cross-checked against
    the 2026-07-19 pcbnew numbers (iso containment 0.005, mask margin 0.145,
    10/10 bores). The silk program is part of that PASS since the stroke clip
    landed; before it, the legend FAILED `silk pad clearance` at 0.085.

gerbv-dependent sections skip LOUDLY when gerbv is absent (same posture as
pcb_groundtruth_suite); the hole schedule, the outline walk and the per-program
echoes need no raster and always run.

Run: .venv/bin/python tests/pcb_checks_suite.py
"""
import math
import sys
import tempfile
from pathlib import Path

import numpy as np
from scipy import ndimage

from clauderacam import emit
from clauderacam.engine import OpResult, path_length
from clauderacam.pcb import boardmaps as bm
from clauderacam.pcb import checks, pcbjob, reemit

REPO = Path(__file__).resolve().parents[1]
ZB = Path.home() / "scratch/ha/devices/projects/zigbee-button/pcb"
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
        check(name, needle in str(e), f"got: {str(e)[:90]}")
        return
    check(name, False, "no exception raised")


def by_name(chks):
    return {c.name: c for c in chks}


def catches(hazard, chks, must_fail, must_pass=()):
    """A negative control: `must_fail` names the check that has to catch this
    hazard, and every name in `must_pass` must still PASS (that is how we know
    the checks are independent and not all failing at once)."""
    d = by_name(chks)
    if must_fail not in d:
        check(f"NEG {hazard}", False, f"no check named {must_fail!r}")
        return
    ok = not d[must_fail].ok
    still = [n for n in must_pass if n in d and not d[n].ok]
    check(f"NEG {hazard} -> {must_fail}", ok and not still,
          (f"value {d[must_fail].value:.4f} ({d[must_fail].limit})"
           if ok else "check PASSED the hazard")
          + (f"; collateral failures {still}" if still else ""))


# =============================================================== the geometry
def e(t):
    return math.cos(t), math.sin(t)


def arc(c, r, t0, t1, step=0.1):
    n = max(2, int(math.ceil(abs(t1 - t0) * r / step)))
    return [(c[0] + r * math.cos(t), c[1] + r * math.sin(t))
            for t in np.linspace(t0, t1, n + 1)]


def ring(c, r, step=0.05):
    return arc(c, r, 0.0, 2 * math.pi, step)


def stadium(a, b, r, step=0.05):
    """Outline of the segment a-b offset by r (a trace's isolation path)."""
    ang = math.atan2(b[1] - a[1], b[0] - a[0])
    o1 = e(ang + math.pi / 2)
    o2 = e(ang - math.pi / 2)
    pts = [(a[0] + r * o1[0], a[1] + r * o1[1]),
           (b[0] + r * o1[0], b[1] + r * o1[1])]
    pts += arc(b, r, ang + math.pi / 2, ang - math.pi / 2, step)
    pts += [(a[0] + r * o2[0], a[1] + r * o2[1])]
    pts += arc(a, r, ang - math.pi / 2, ang - 3 * math.pi / 2, step)
    return pts


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
    """G-code for one plunge-and-cut chain (the shape every phase uses)."""
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


# ============================================================ synthetic board
# Board window 0..20 x 0..15 (gerber frame). Copper: two Ø1.2 pads and one
# 0.3 trace = THREE islands, so `iso coverage` has something to miss. Mask
# apertures are deliberately generous over pad A (Ø2.0 over Ø1.2 copper) so a
# scrub path can satisfy the window law and still straddle the copper edge —
# the peeled-trace incident, reproducible on demand.
TD = Path(tempfile.mkdtemp(prefix="clauderacam-ws5-"))
G = TD / "gerbers"
G.mkdir()
PAD_A = (4.0, 4.0)
PAD_B = (10.0, 4.0)
PAD_R = 0.6
TRACE = ((4.0, 10.0), (12.0, 10.0))
TRACE_R = 0.15
BW, BH = 20.0, 15.0

HDR = "%FSLAX46Y46*%\n%MOMM*%\n%LPD*%\n"


def gnum(v):
    return f"{int(round(v * 1e6)):d}"


# Edge.Cuts: four separate two-point draws, DELIBERATELY out of perimeter
# order — that is what KiCad writes (top, left, right, bottom on the zigbee
# board), and the tab walk has to chain them itself.
EDGE = HDR + "%ADD10C,0.100000*%\nG01*\nD10*\n" + "".join(
    f"X{gnum(x1)}Y{gnum(y1)}D02*\nX{gnum(x2)}Y{gnum(y2)}D01*\n"
    for x1, y1, x2, y2 in [(0, BH, BW, BH), (0, 0, 0, BH),
                           (BW, BH, BW, 0), (BW, 0, 0, 0)]) + "M02*\n"
CU = (HDR + "%ADD10C,1.200000*%\n%ADD11C,0.300000*%\nG01*\n"
      f"D10*\nX{gnum(PAD_A[0])}Y{gnum(PAD_A[1])}D03*\n"
      f"X{gnum(PAD_B[0])}Y{gnum(PAD_B[1])}D03*\n"
      f"D11*\nX{gnum(TRACE[0][0])}Y{gnum(TRACE[0][1])}D02*\n"
      f"X{gnum(TRACE[1][0])}Y{gnum(TRACE[1][1])}D01*\nM02*\n")
MASK = (HDR + "%ADD10C,2.000000*%\n%ADD11C,1.400000*%\nG01*\n"
        f"D10*\nX{gnum(PAD_A[0])}Y{gnum(PAD_A[1])}D03*\n"
        f"D11*\nX{gnum(PAD_B[0])}Y{gnum(PAD_B[1])}D03*\nM02*\n")
SILK = (HDR + "%ADD10C,0.200000*%\nG01*\nD10*\n"
        f"X{gnum(2.0)}Y{gnum(8.0)}D02*\nX{gnum(18.0)}Y{gnum(8.0)}D01*\n"
        f"X{gnum(16.0)}Y{gnum(2.0)}D02*\nX{gnum(16.0)}Y{gnum(6.0)}D01*\n"
        "M02*\n")
HOLES = [(PAD_A[0], PAD_A[1], 1.0), (PAD_B[0], PAD_B[1], 1.6)]
DRL = ("M48\nFMAT,2\nMETRIC\nT1C1.000\nT2C1.600\n%\nG90\nG05\n"
       f"T1\nX{PAD_A[0]:.3f}Y{PAD_A[1]:.3f}\n"
       f"T2\nX{PAD_B[0]:.3f}Y{PAD_B[1]:.3f}\nT0\nM30\n")
(G / "stub-Edge_Cuts.gbr").write_text(EDGE)
(G / "stub-B_Cu.gbr").write_text(CU)
(G / "stub-B_Mask.gbr").write_text(MASK)
(G / "stub-B_Silkscreen.gbr").write_text(SILK)
(G / "stub.drl").write_text(DRL)

JOB = f"""
[pcb]
name = "ws5-stub"
stem = "stub"
gerbers = "gerbers"
out = "out"

[blank]
width = 40.0
height = 30.0
thickness = 1.5
anchor = [0.0, 0.0]

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

[phases.iso]
tool = 2
depth = -0.15
feed = 500
plunge = 200

[phases.clear]
tool = 3
depth = -0.15
margin = 1.0
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
JOBP = TD / "job.toml"
job = pcbjob.load(JOBP)
OUT = TD / "out"
OUT.mkdir(exist_ok=True)

# machine frame of this board: mirror-x about the extents + anchor (0,0)
MX, MY = BW, 0.0        # machine = (MX - x, y + MY)


def mach(p):
    return (MX - p[0], p[1] + MY)


# ------------------------------------------------------------ the programs
def build_iso(skip_pad_b=False):
    tip_r = job.phase_tool("iso").tip_diameter / 2
    p = job.phases["iso"]
    lines = []
    chains = [ring(mach(PAD_A), PAD_R + tip_r)]
    if not skip_pad_b:
        chains.append(ring(mach(PAD_B), PAD_R + tip_r))
    chains.append(stadium(mach(TRACE[0]), mach(TRACE[1]), TRACE_R + tip_r))
    for ch in chains:
        lines += cut_chain(ch + [ch[0]], p["depth"], p["feed"], p["plunge"])
    return op("iso", p["tool"], lines, p["feed"])


def build_clear(into_copper=False):
    p = job.phases["clear"]
    # a serpentine in the copper-free band between the pad row and the trace
    ys = [6.5, 7.0, 7.5]
    pts = []
    for n, y in enumerate(ys):
        xs = (1.0, 19.0) if n % 2 == 0 else (19.0, 1.0)
        pts += [(xs[0], y), (xs[1], y)]
    lines = cut_chain(pts, p["depth"], p["feed"], p["plunge"])
    if into_copper:
        # a pass straight across both pads: the clearing tool eats kept copper
        lines += cut_chain([(1.0, PAD_A[1]), (19.0, PAD_A[1])],
                           p["depth"], p["feed"], p["plunge"])
    return op("clear", p["tool"], lines, p["feed"])


def build_scrub(radius_a=0.3, radius_b=0.3):
    p = job.phases["scrub"]
    lines = []
    for pad, radius in ((PAD_A, radius_a), (PAD_B, radius_b)):
        c = mach(pad)
        lines += cut_chain([c, c], p["depth"], p["feed"], p["plunge"])
        r = ring(c, radius, step=0.05)
        lines += cut_chain(r + [r[0]], p["depth"], p["feed"], p["plunge"])
    return op("scrub", p["tool"], lines, p["feed"])


def build_drills(displace=0.0, extra=None):
    p = job.phases["drills"]
    tool = job.phase_tool("drills")
    zs = [-0.5, -1.0, -1.5, -1.7]
    lines = []
    bores = [(hx + displace, hy, hd) for hx, hy, hd in HOLES]
    if extra:
        bores.append(extra)
    for hx, hy, hd in bores:
        c = mach((hx, hy))
        r_orbit = max((hd - tool.diameter) / 2, 0.0)
        for z in zs:
            o = ring(c, r_orbit, step=0.05) if r_orbit > 0 else [c, c]
            lines += cut_chain(o + [o[0]], z, p["feed"], p["plunge"])
    return op("drills", p["tool"], lines, p["feed"])


def cutout_chains(tabs=4):
    """The offset loop, split into cut arcs by the tab windows."""
    tool = job.phase_tool("cutout")
    off = tool.radius + 0.05        # tool radius + half the Edge.Cuts width
    loop = [(0.0, -off), (BW, -off)]
    loop += arc((BW, 0.0), off, -math.pi / 2, 0.0)
    loop += [(BW + off, BH)]
    loop += arc((BW, BH), off, 0.0, math.pi / 2)
    loop += [(0.0, BH + off)]
    loop += arc((0.0, BH), off, math.pi / 2, math.pi)
    loop += [(-off, 0.0)]
    loop += arc((0.0, 0.0), off, math.pi, 1.5 * math.pi)
    pts = densify_loop(loop, 0.1)
    P = np.array(pts)
    seg = np.hypot(*(np.roll(P, -1, axis=0) - P).T)
    s = np.concatenate([[0.0], np.cumsum(seg)[:-1]])
    total = float(seg.sum())
    centers = [(BW / 2, -off), (BW + off, BH / 2), (BW / 2, BH + off),
               (-off, BH / 2)][:tabs]
    keep = np.ones(len(P), bool)
    half = (1.0 + tool.diameter) / 2      # 1.0mm material tab
    for cx, cy in centers:
        k = int(np.hypot(P[:, 0] - cx, P[:, 1] - cy).argmin())
        d = np.abs((s - s[k] + total / 2) % total - total / 2)
        keep &= d > half
    chains, cur = [], []
    for n in range(len(P)):
        if keep[n]:
            cur.append(tuple(mach(tuple(P[n]))))
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


def build_cutout(tabs=4):
    p = job.phases["cutout"]
    zs = [-0.3, -0.6, -0.9, -1.2, -1.5, -1.7]
    lines = []
    for z in zs:
        for ch in cutout_chains(tabs):
            lines += cut_chain(ch, z, p["feed"], p["plunge"])
    return op("cutout", p["tool"], lines, p["feed"])


def write(name, ops):
    path = OUT / f"{name}.nc"
    path.write_text(emit.assemble(job, ops))
    return path


def mutate(src, name, fn):
    """Corrupt an assembled program's BYTES and hand the file to the gate."""
    path = OUT / f"{name}.nc"
    path.write_text(fn(Path(src).read_text()))
    return path


print("assembling the synthetic board's four programs:")
MILL = write("mill", [build_iso(), build_clear()])
SCRUB = write("scrub", [build_scrub()])
HOLESP = write("holes", [build_drills(), build_cutout()])
check("mill/scrub/holes assemble through emit.assemble",
      MILL.is_file() and SCRUB.is_file() and HOLESP.is_file(),
      f"{len(MILL.read_text().splitlines())} + "
      f"{len(SCRUB.read_text().splitlines())} + "
      f"{len(HOLESP.read_text().splitlines())} lines")

# ------------------------------------------------- checks that need no raster
# A maps object with the derived transform and the hole schedule but no
# rasters: enough for the hole checks, the outline walk and the echoes.
LITE = checks.BoardMaps(
    tight=bm.extents(job.files["edge"], cross_check=False),
    win=bm.extents(job.files["edge"], cross_check=False),
    layers={}, holes=bm.excellon(job.files["drl"]),
    offset=bm.machine_offset(bm.extents(job.files["edge"],
                                        cross_check=False), job.anchor))

print("\noutline walk (no gerbv needed):")
loop = checks._outline_loop(job)
check("four out-of-order edge draws chain into one loop",
      loop.shape == (4, 4)
      and abs(loop[:, 2:] - np.roll(loop[:, :2], -1, axis=0)).max() < 1e-9,
      f"{loop.shape[0]} segments, perimeter "
      f"{np.hypot(*(loop[:, 2:] - loop[:, :2]).T).sum():.1f}mm")
BROKEN = TD / "broken"
BROKEN.mkdir(exist_ok=True)
for f in G.iterdir():
    (BROKEN / f.name).write_text(f.read_text())
(BROKEN / "stub-Edge_Cuts.gbr").write_text(
    EDGE.replace(f"X{gnum(BW)}Y{gnum(0)}D02*\nX{gnum(0)}Y{gnum(0)}D01*\n", ""))
bjob = pcbjob.load(TD / "job.toml")
bjob.files["edge"] = BROKEN / "stub-Edge_Cuts.gbr"
caught("an open outline refuses the tab walk",
       lambda: checks._outline_loop(bjob), "closed loop")

print("\nhole schedule + per-program echoes (no gerbv needed):")
m, text = checks.program_moves(job, HOLESP)
s_drl = checks.phase_samples(m, LITE, "drills")
hchk = checks.hole_checks(job, LITE, s_drl)
d = by_name(hchk)
check("hole schedule: 2/2 bores at position and diameter",
      d["hole schedule"].ok, d["hole schedule"].detail)
check("hole bore depth: reaches -1.7 in >= 4 passes",
      d["hole bore depth"].ok, d["hole bore depth"].detail)
check("no stray bores", d["stray bores"].ok)
echo = checks.echo_checks(job, ("drills", "cutout"),
                          {"drills": s_drl,
                           "cutout": checks.phase_samples(m, LITE, "cutout")},
                          text, m)
check("per-program echoes all pass on a clean program",
      all(c.ok for c in echo),
      ", ".join(c.name for c in echo if not c.ok)
      or f"{len(echo)}/{len(echo)}")

# --- negatives that need no raster ---
print("\nNEGATIVE controls (no gerbv needed):")
bad = write("holes_displaced", [build_drills(displace=0.30),
                               build_cutout()])
mm, _ = checks.program_moves(job, bad)
catches("displaced drill (0.30 off the schedule)",
        checks.hole_checks(job, LITE,
                           checks.phase_samples(mm, LITE, "drills")),
        "hole schedule")
bad = write("holes_extra", [build_drills(extra=(7.0, 7.0, 1.0)),
                            build_cutout()])
mm, _ = checks.program_moves(job, bad)
catches("an extra bore nobody scheduled",
        checks.hole_checks(job, LITE,
                           checks.phase_samples(mm, LITE, "drills")),
        "stray bores", must_pass=("hole schedule",))

stale = mutate(HOLESP, "holes_stale",
               lambda t: t.replace("Z-1.7000", "Z-1.6000"))
mm, mt = checks.program_moves(job, stale)
catches("stale ZMIN (a repost of the previous revision)",
        checks.echo_checks(job, ("drills",),
                           {"drills": checks.phase_samples(mm, LITE,
                                                           "drills")}, mt),
        "drills floor Z")
wrongf = mutate(HOLESP, "holes_feed",
                lambda t: t.replace("F300", "F450"))
mm, mt = checks.program_moves(job, wrongf)
catches("wrong F (a feed from some other job)",
        checks.echo_checks(job, ("drills",),
                           {"drills": checks.phase_samples(mm, LITE,
                                                           "drills")}, mt),
        "drills params")
wrongt = mutate(HOLESP, "holes_tool",
                lambda t: t.replace("M6 T3", "M6 T7", 1))
mm, mt = checks.program_moves(job, wrongt)
catches("wrong tool in the drills stage",
        checks.echo_checks(job, ("drills",),
                           {"drills": checks.phase_samples(mm, LITE,
                                                           "drills")}, mt),
        "drills tool")
def replace_nth(t, old, new, n):
    parts = t.split(old)
    if len(parts) <= n:
        raise AssertionError(f"{old!r} occurs {len(parts)-1} times, need {n}")
    return old.join(parts[:n]) + new + old.join(parts[n:])


# retract only part-way before the move to the SECOND bore, so the next
# rapid crosses the board laterally at Z-1.0 (occurrence 5 = bore 2's first
# pass; a zero-length G0 at depth, which FlatCAM writes before every
# step-down, is deliberately NOT a hazard)
divein = mutate(HOLESP, "holes_rapid",
                lambda t: replace_nth(t, "G00 Z2.0000", "G00 Z-1.0000", 5))
mm, mt = checks.program_moves(job, divein)
catches("a rapid below the work surface (the blank IS stock)",
        checks.echo_checks(job, ("drills",),
                           {"drills": checks.phase_samples(mm, LITE,
                                                           "drills")}, mt, mm),
        "rapid depth", must_pass=("drills floor Z", "drills params"))

print("\nprogram census:")
reps = checks.verify_pcb(job, {}, maps=LITE)
check("a program not handed to the gate is FATAL, not skipped",
      all(not reps[n].ok for n in checks.PROGRAM_PHASES)
      and "unverified" in reps["silk"].checks[0].detail,
      reps["scrub"].checks[0].detail[:60])
reps2 = checks.verify_pcb(job, {"wat": HOLESP}, maps=LITE)
check("an unknown program name is FATAL too", "wat" in reps2
      and not reps2["wat"].ok, reps2["wat"].checks[0].detail[:50])

# ===================================================== raster sections (gerbv)
if not bm.have_gerbv():
    print("\nSKIP: gerbv not available — the raster checks (iso, clear, "
          "scrub, silk clearance, cutout ride) are NOT checkable here "
          "(install: sudo apt install gerbv)")
    print(f"\nPCB CHECKS {'FAIL: ' + ', '.join(fails) if fails else 'PASS (raster skipped)'}")
    sys.exit(1 if fails else 0)

print("\nraster bias calibration (the half-pixel law every threshold uses):")
CAL = TD / "cal.gbr"
CAL.write_text(HDR + "%ADD10C,1.000000*%\nG01*\nD10*\n"
               f"X{gnum(5.0)}Y{gnum(5.0)}D03*\nM02*\n")
worst = 0.0
for dpi in (1270, 2540, 5080):
    w = bm.BoardWindow(3.0, 3.0, 8.0, 8.0, dpi)
    dist = bm.dist_mm(bm.rasterize(CAL, w), w)
    for probe, truth in ((6.0, 0.5), (6.5, 1.0), (7.0, 1.5)):
        i, j = w.world_to_px(probe, 5.0)
        v = float(ndimage.map_coordinates(
            dist, [[float(i) - 0.5], [float(j) - 0.5]], order=1)[0])
        worst = max(worst, abs((v - truth) * w.ppmm - 0.5))
check("EDT over-reads the true distance by 0.50px at every dpi",
      worst < 0.05, f"worst deviation {worst:.3f}px")

print("\nboard maps:")
maps = checks.board_maps(job)
check("one padded window, transform derived from the TIGHT extents",
      maps.offset == (BW, 0.0)
      and maps.win.x0 < maps.tight.x0 and maps.win.x1 > maps.tight.x1,
      f"window {maps.win.shape} offset {maps.offset} eps {maps.eps:.4f}")
islands = ndimage.label(maps.layers["cu"])[1]
check("three copper islands rasterized", islands == 3, f"{islands} islands")

print("\nsilk program (through the real reemit chain):")
mask_t = bm.rasterize(job.files["mask"], maps.tight)
silk_text, silk_clip = reemit.silk_program(job, maps.tight, mask_t)
SILKP = OUT / "silk.nc"
SILKP.write_text(silk_text)
check("both silk strokes survive the pad clearance clip untouched",
      silk_clip.dropped == 0 and silk_clip.clipped == 0
      and len(silk_clip.strokes) == 2, silk_clip.note)

print("\nPOSITIVE control — the whole synthetic board through verify_pcb:")
reports = checks.verify_pcb(job, {"mill": MILL, "silk": SILKP,
                                  "scrub": SCRUB, "holes": HOLESP},
                            maps=maps)
for name, rep in reports.items():
    bad_names = [c.name for c in rep.checks if not c.ok]
    check(f"program {name} PASSES every check", rep.ok,
          f"{len(rep.checks)} checks"
          + (f"; FAILED {bad_names}" if bad_names else ""))
    for c in rep.checks:
        print(f"      {c.name}: {c.value:.4f} ({c.limit}) "
              f"{'PASS' if c.ok else 'FAIL'}")

# ------------------------------------------------------- raster negatives
print("\nNEGATIVE controls (raster):")
mill_noB = write("mill_skip_island", [build_iso(skip_pad_b=True),
                                      build_clear()])
mm, _ = checks.program_moves(job, mill_noB)
catches("an iso pass that skips a copper island",
        checks.iso_checks(job, maps, checks.phase_samples(mm, maps, "iso")),
        "iso coverage", must_pass=("iso containment",))
maps.release()

# radially outward by 0.12 at pad A's angle-0 vertex (a tangential nudge
# would barely move the distance — the point is to leave the offset curve)
ISO_V = f"X{MX - PAD_A[0] + PAD_R + 0.1:.4f}"
mill_off = mutate(MILL, "mill_iso_off",
                  lambda t: t.replace(ISO_V,
                                      f"X{MX - PAD_A[0] + PAD_R + 0.22:.4f}"))
check("the iso-off mutation actually landed", ISO_V in MILL.read_text())
mm, _ = checks.program_moves(job, mill_off)
catches("an iso pass 0.12 off the copper boundary",
        checks.iso_checks(job, maps, checks.phase_samples(mm, maps, "iso")),
        "iso containment")
maps.release()

mill_cu = write("mill_into_copper", [build_iso(), build_clear(True)])
mm, _ = checks.program_moves(job, mill_cu)
catches("clearing straight across kept copper",
        checks.clear_checks(job, maps,
                            checks.phase_samples(mm, maps, "clear")),
        "clear keep-out")
maps.release()

# pad A's mask aperture is Ø2.0 over Ø1.2 copper, so a scrub lap at r=0.6
# sits ON the copper edge while still 0.25 inside its aperture: the window
# law is satisfied and the plateau law is not. That IS the incident.
scrub_edge = write("scrub_edge", [build_scrub(radius_a=PAD_R)])
mm, _ = checks.program_moves(job, scrub_edge)
catches("the spring tool riding the pad EDGE (peeled traces)",
        checks.scrub_checks(job, maps,
                            checks.phase_samples(mm, maps, "scrub")),
        "scrub plateau margin", must_pass=("scrub window",))
maps.release()

holes_notab = write("holes_notab", [build_drills(), build_cutout(tabs=3)])
mm, _ = checks.program_moves(job, holes_notab)
catches("a cutout with one tab missing",
        checks.cutout_checks(job, maps,
                             checks.phase_samples(mm, maps, "cutout")),
        "cutout tab census", must_pass=("cutout ride band", "cutout side"))
maps.release()

silk_pad = mutate(SILKP, "silk_on_pad",
                  lambda t: t.replace("G1 X2.000 Y8.000",
                                      f"G1 X{MX - PAD_A[0]:.3f} "
                                      f"Y{PAD_A[1]:.3f}"))
catches("a silk stroke cured onto a solderable pad",
        checks.silk_checks(job, maps, silk_pad.read_text()),
        "silk pad clearance", must_pass=("silk dialect lint",
                                         "silk focus move", "silk dose"))
silk_nofocus = mutate(SILKP, "silk_nofocus",
                      lambda t: t.replace("G0 Z0\n", ""))
catches("a laser file with no focus move (the defocus incident)",
        checks.silk_checks(job, maps, silk_nofocus.read_text()),
        "silk focus move")
silk_hot = mutate(SILKP, "silk_hot",
                  lambda t: t.replace("M3 S0.03", "M3 S0.5"))
catches("an over-dose S (char/vaporize territory)",
        checks.silk_checks(job, maps, silk_hot.read_text()),
        "silk dose")
maps.release()

# --- the MID-SEGMENT stroke: the 2026-07-30 field-legend incident, moved
# onto synthetic geometry (the field legend now PASSES, so the negative
# control cannot live there any more — Article III: convert the fodder,
# never delete the negative). Both ENDPOINTS of this stroke are 2mm+ clear
# of any aperture and it drives straight through both pads, which is exactly
# what reemit's old vertex test shipped intact.
print("\nNEGATIVE control — a stroke clear at both ENDS, over a pad in the "
      "middle:")
MID = (HDR + "%ADD10C,0.200000*%\nG01*\nD10*\n"
       f"X{gnum(1.0)}Y{gnum(4.0)}D02*\nX{gnum(13.0)}Y{gnum(4.0)}D01*\n"
       "M02*\n")
(G / "mid-B_Silkscreen.gbr").write_text(MID)
midjob = pcbjob.load(JOBP)
midjob.files["silk"] = G / "mid-B_Silkscreen.gbr"
raw_mid = OUT / "silk_midsegment_unclipped.nc"
raw_mid.write_text(emit.assemble_laser(
    "midsegment", [[(MX - 1.0, 4.0), (MX - 13.0, 4.0)]],
    dose_s=job.phases["silk"]["dose"], feed=job.phases["silk"]["feed"]))
catches("an UNCLIPPED stroke whose endpoints are both clear",
        checks.silk_checks(midjob, maps, raw_mid.read_text()),
        "silk pad clearance", must_pass=("silk dialect lint",
                                        "silk focus move", "silk dose",
                                        "silk feed echo"))
mid_text, mid_clip = reemit.silk_program(midjob, maps.tight, mask_t)
mid_p = OUT / "silk_midsegment.nc"
mid_p.write_text(mid_text)
check("the same stroke through the CLIP is split, not dropped",
      mid_clip.chains == 1 and mid_clip.clipped == 1 and mid_clip.dropped == 0
      and len(mid_clip.strokes) == 3, mid_clip.note)
mid_chk = by_name(checks.silk_checks(midjob, maps, mid_text))
check("and the clipped program PASSES silk pad clearance",
      mid_chk["silk pad clearance"].ok,
      f"{mid_chk['silk pad clearance'].value:.4f} "
      f"(>= {midjob.phases['silk']['clearance']})")
maps.release()

# the castellation-chewing incident needs a narrow copper channel: its own
# two-rectangle board, where `clear keep-out` PASSES and only the
# morphological-opening rule catches the hazard
print("\nNEGATIVE control — the castellation slot (its own board):")
SLOT = TD / "slot"
SLOT.mkdir(exist_ok=True)
GAP = 0.88          # the incident measured 0.84 with an 0.8 tool
recty = [BH / 2 - GAP / 2 - 1.0, BH / 2 + GAP / 2 + 1.0]
(SLOT / "stub-B_Cu.gbr").write_text(
    HDR + "%ADD10R,12.000000X2.000000*%\nG01*\nD10*\n"
    + "".join(f"X{gnum(BW/2)}Y{gnum(y)}D03*\n" for y in recty) + "M02*\n")
for n in ("stub-Edge_Cuts.gbr", "stub-B_Mask.gbr", "stub-B_Silkscreen.gbr"):
    (SLOT / n).write_text((G / n).read_text())
(SLOT / "stub.drl").write_text(DRL)
sjob = pcbjob.load(JOBP)
for k, suf in (("cu", "-B_Cu.gbr"), ("edge", "-Edge_Cuts.gbr"),
               ("mask", "-B_Mask.gbr"), ("silk", "-B_Silkscreen.gbr")):
    sjob.files[k] = SLOT / f"stub{suf}"
sjob.files["drl"] = SLOT / "stub.drl"
smaps = checks.board_maps(sjob)
p = sjob.phases["clear"]
slot_lines = cut_chain([(1.0, BH / 2), (19.0, BH / 2)], p["depth"],
                       p["feed"], p["plunge"])
SLOTP = OUT / "slot_clear.nc"
SLOTP.write_text(emit.assemble(sjob, [op("iso", 2, cut_chain(
    [(1.0, 1.0), (2.0, 1.0)], -0.15, 500, 200), 500),
    op("clear", p["tool"], slot_lines, p["feed"])]))
mm, _ = checks.program_moves(sjob, SLOTP)
sc = checks.clear_checks(sjob, smaps, checks.phase_samples(mm, smaps,
                                                            "clear"))
catches(f"an 0.8 corn threading a {GAP} slot (castellation chewing)",
        sc, "clear opening", must_pass=("clear keep-out",))
print(f"      keep-out reads {by_name(sc)['clear keep-out'].value:.4f} "
      f"(>= {checks.CLEAR_KEEP_MIN}) — the naive rule is satisfied, which "
      f"is exactly how the pads got chewed")
smaps.release()

# ============================================================== golden-pcb
print("\ngolden-pcb (Board A's blessed assets):")
if GOLDEN.is_dir():
    gj = sorted(GOLDEN.glob("*.toml"))
    check("golden-pcb job present", bool(gj), str([p.name for p in gj]))
    for jp in gj:
        gjob = pcbjob.load(jp)
        progs = {n: GOLDEN / f"{jp.stem}-{n}.nc" for n in
                 checks.PROGRAM_PHASES}
        missing = [str(p) for p in progs.values() if not p.is_file()]
        check(f"{jp.stem}: blessed programs present", not missing,
              f"missing {missing}" if missing else "")
        if missing:
            continue
        greps = checks.verify_pcb(gjob, progs)
        for name, rep in greps.items():
            check(f"{jp.stem}/{name} PASSES", rep.ok,
                  ", ".join(c.name for c in rep.checks if not c.ok))
else:
    print("  " + "=" * 68)
    print("  TODO(Board A) — THE GOLDEN-PCB SLOT IS EMPTY.")
    print("  This suite proves the checks on synthetic geometry and (here)")
    print("  on the zigbee run set, but the lane has no BLESSED board yet.")
    print("  When Board A is cut and its programs are verified at the")
    print("  machine, drop them in as:")
    print(f"    {GOLDEN.relative_to(REPO)}/<board>.toml       (the [pcb] job)")
    print(f"    {GOLDEN.relative_to(REPO)}/gerbers/...        (its gerbers +"
          " .drl, committed)")
    print(f"    {GOLDEN.relative_to(REPO)}/<board>-mill.nc    (blessed bytes)")
    print("    ...-silk.nc, ...-scrub.nc, ...-holes.nc")
    print("  The loop above then verifies every blessed program in CI, and")
    print("  a change that moves ANY check value shows up as a FAIL — the")
    print("  golden-pcb equivalent of tests/golden/mango-brass.nc.")
    print("  Until then this line is the loud gap, not a silent one.")
    print("  " + "=" * 68)

# ================================================== local bonus: the real run
ZPH = ZB / "cam/flatcam/phases"
ZNC = {"iso": "fc-1-iso.nc", "clear": "fc-3a-clear.nc",
       "scrub": "fc-2-mask.nc", "drills": "fc-3b-holes.nc",
       "cutout": "fc-4-cutout.nc"}
if all((ZPH / f).is_file() for f in ZNC.values()) and \
        (ZB / "zigbee-button-v2-B_Cu.gbr").is_file():
    print("\nzigbee-button V2, the whole chain re-assembled (local bonus):")
    ZG = TD / "zgerbers"
    ZG.mkdir(exist_ok=True)
    for suf in ("-B_Cu.gbr", "-B_Mask.gbr", "-B_Silkscreen.gbr",
                "-Edge_Cuts.gbr"):
        (ZG / f"zigbee-button-v2{suf}").write_bytes(
            (ZB / f"zigbee-button-v2{suf}").read_bytes())
    (ZG / "zigbee-button-v2.drl").write_bytes(
        (ZB / "cam/flatcam/gerbers/zigbee-button-v2.drl").read_bytes())
    ZJOB = TD / "zigbee.toml"
    ZJOB.write_text(JOB.replace('name = "ws5-stub"',
                                'name = "zigbee-button-v2"')
                    .replace('stem = "stub"', 'stem = "zigbee-button-v2"')
                    .replace('gerbers = "gerbers"', 'gerbers = "zgerbers"')
                    .replace('out = "out"', 'out = "zout"')
                    .replace("margin = 1.0", "margin = 1.4")
                    .replace("dpp = 0.5", "dpp = 0.5"))
    zjob = pcbjob.load(ZJOB)
    zout = TD / "zout"
    zout.mkdir(exist_ok=True)
    zops = {ph: reemit.read_phase(ZPH / f, zjob, ph)
            for ph, f in ZNC.items()}
    check("every field phase file re-emits clean (param-match law)",
          all(o.lines for o in zops.values()),
          ", ".join(f"{k}:{len(v.lines)}" for k, v in zops.items()))
    zprogs = {}
    for name, phs in (("mill", ("iso", "clear")), ("scrub", ("scrub",)),
                      ("holes", ("drills", "cutout"))):
        p = zout / f"{name}.nc"
        p.write_text(emit.assemble(zjob, [zops[ph] for ph in phs]))
        zprogs[name] = p
    zmaps = checks.board_maps(zjob)
    ztight_mask = bm.rasterize(zjob.files["mask"], zmaps.tight)
    ztext, zclip = reemit.silk_program(zjob, zmaps.tight, ztight_mask)
    zprogs["silk"] = zout / "silk.nc"
    zprogs["silk"].write_text(ztext)
    zreps = checks.verify_pcb(zjob, zprogs, maps=zmaps)
    for name in ("mill", "scrub", "holes"):
        rep = zreps[name]
        check(f"field program {name} PASSES the new check set", rep.ok,
              ", ".join(f"{c.name}={c.value:.4f}" for c in rep.checks
                        if not c.ok) or f"{len(rep.checks)} checks")
        for c in rep.checks:
            print(f"      {c.name}: {c.value:.4f} ({c.limit}) "
                  f"{'PASS' if c.ok else 'FAIL'}")

    # --- the 2026-07-19 pcbnew cross-check ---
    print("\n  cross-check vs the 2026-07-19 pcbnew numbers:")
    zd = by_name(zreps["mill"].checks)
    zsd = by_name(zreps["scrub"].checks)
    zhd = by_name(zreps["holes"].checks)

    def probe(jb, layer, bxx, byy, dpi=25400, half=1.0):
        """Re-measure ONE point against a 0.001mm/px patch: the declared
        0.01mm/px raster is deliberately pessimistic, and this says by how
        much."""
        w = bm.BoardWindow(bxx - half, byy - half, bxx + half, byy + half, dpi)
        dist = bm.dist_mm(bm.rasterize(jb.files[layer], w), w)
        i, j = w.world_to_px(bxx, byy)
        return float(ndimage.map_coordinates(
            dist, [[float(i) - 0.5], [float(j) - 0.5]], order=1)[0]) \
            - 0.5 / w.ppmm

    mm, _ = checks.program_moves(zjob, zprogs["mill"])
    zs = checks.phase_samples(mm, zmaps, "iso")
    tip_r = zjob.phase_tool("iso").tip_diameter / 2
    err = np.abs(zmaps.sample(zmaps.dist("cu"), zs.bx, zs.by)
                 - zmaps.eps - tip_r)
    k = int(err.argmax())
    truth = abs(probe(zjob, "cu", float(zs.bx[k]), float(zs.by[k])) - tip_r)
    check("iso containment worst point re-probed at 0.001mm/px == pcbnew's "
          "0.005", abs(truth - 0.005) < 0.004,
          f"probe {truth:.4f} vs field 0.005 (declared-dpi reading "
          f"{zd['iso containment'].value:.4f}, bar "
          f"{checks.ISO_CENTERLINE_TOL})")
    check("iso coverage in the same band as pcbnew's 0.048",
          zd["iso coverage"].value <= checks.ISO_COVERAGE_TOL,
          f"{zd['iso coverage'].value:.4f} vs field 0.048")

    # pcbnew measured the tool CENTER 0.145 inside pads-deflated-0.10; our
    # plateau margin is the tool EDGE inside copper, so the same number is
    # margin + tool radius - 0.10.
    zr = zjob.phase_tool("scrub").radius
    ring_equiv = zsd["scrub plateau margin"].value + zr - 0.10
    check("scrub margin reproduces pcbnew's 0.145 mask-ring number",
          abs(ring_equiv - 0.145) < 0.025,
          f"{ring_equiv:.4f} vs field 0.145 (edge-inside-copper "
          f"{zsd['scrub plateau margin'].value:.4f} vs the guide's 0.095)")
    check("10/10 bores, exactly the pcbnew schedule",
          zhd["hole schedule"].ok and len(zmaps.holes) == 10,
          zhd["hole schedule"].detail)
    check("cutout rides uniformly with 4x1.5mm tabs, as verified in the field",
          zhd["cutout ride band"].ok and zhd["cutout tab census"].ok,
          zhd["cutout tab census"].detail)

    # --- the field silk program: a FINDING on 2026-07-30, a PASS since ---
    print("\n  the field silk program (was a FINDING; the clip closed it):")
    zsilk = by_name(zreps["silk"].checks)
    check("silk pad clearance PASSES on the clipped field legend",
          zreps["silk"].ok,
          f"{zsilk['silk pad clearance'].value:.4f} >= "
          f"{zjob.phases['silk']['clearance']} "
          f"(was 0.0850 = FAIL when whole chains were kept or dropped on a "
          f"VERTEX test)")
    check("the clip reports what it took off the legend",
          zclip.chains == 99 and zclip.clipped == 11 and zclip.dropped == 0
          and 52.0 < zclip.removed_mm < 53.0, zclip.note)
    print("      The 23.5mm legend box drives through a field of 1.8-3.7mm "
          "mask\n      apertures: 49.4mm of the 123.1mm legend is genuinely "
          "inside the\n      0.3 forbidden region (measured off the aperture "
          "list, no raster).\n      The old vertex test dropped 10 chains "
          "whole and shipped that box\n      intact; the clip splits 11 "
          "chains into 117 strokes and drops none.")
    zmaps.release()
else:
    print("\nSKIP: the zigbee-button run set is not on this box — the field "
          "cross-check (iso 0.005 / mask 0.145 / 10-of-10 bores) is not "
          "checkable here")

print(f"\nPCB CHECKS {'FAIL: ' + ', '.join(fails) if fails else 'PASS'}")
sys.exit(1 if fails else 0)
