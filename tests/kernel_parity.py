"""Kernel parity: the Rust kernel is an optimization of the Python reference
in kernel_py.py, never a divergence from it. This test runs BOTH on a real
job and requires agreement — bit-exact for the carved stock and integer
counts, tight float tolerance for the accumulated measurements (the two
accumulate volume in different float orders).

Run: .venv/bin/python tests/kernel_parity.py
Skips (exit 0, loudly) if the Rust extension is not built.
"""
import sys
import time
from pathlib import Path

import numpy as np

from clauderacam import emit, engine, job as jobmod, kernel, simulate

if kernel.BACKEND != "rust":
    print("SKIP: rust kernel not built — parity not checkable here")
    sys.exit(0)

REPO = Path(__file__).resolve().parents[1]

# the synthetic reference STLs are generated artifacts; make sure they exist
# (CI may run this test before the reference suite, which also builds them)
if not (REPO / "assets" / "generated" / "dome.stl").exists():
    import runpy
    runpy.run_path(str(REPO / "assets" / "generate_references.py"),
                   run_name="__main__")
j = jobmod.load(REPO / "jobs" / "dome.toml")
ops = engine.generate_ops(j)
emit.write(j, ops)

m = simulate.prep_moves(j.out, j, j.machine["rapid_feed"])
tool_nums = sorted(j.tools)
idx_of = {t: i for i, t in enumerate(tool_nums)}
kw = dict(
    n=int((j.stock_half + 3.0) * 2 * 12.5), ppm=12.5,
    half=j.stock_half + 3.0, step=0.06, check=True,
    dia=np.array([j.tools[t].diameter for t in tool_nums]),
    shape=np.array([simulate.SHAPE_OF[j.tools[t].type]
                    for t in tool_nums], dtype=np.uint8),
    tip_r=np.array([(j.tools[t].tip_diameter or 0.0) / 2.0
                    for t in tool_nums]),
    slope=np.array([0.0 for _ in tool_nums]),
    flute_len=np.array([j.tools[t].flute_length for t in tool_nums]),
    shank_d=np.array([j.tools[t].shank_diameter for t in tool_nums]),
    motion=m.motion, x0=m.x0, y0=m.y0, z0=m.z0,
    x1=m.x1, y1=m.y1, z1=m.z1,
    tool_idx=np.array([idx_of[int(t)] for t in m.tool_num], dtype=np.uint16),
    # mid-run snapshots (the stage-preview mechanism) must also agree —
    # including one landing on a possibly-skipped final move
    snap_after=np.array([len(m.motion) // 3, 2 * len(m.motion) // 3,
                         len(m.motion) - 1], dtype=np.int64))

t0 = time.time()
rp = kernel.measure_py(**kw)
t_py = time.time() - t0
t0 = time.time()
rr = kernel.measure_rust(**kw)
t_rs = time.time() - t0
print(f"python kernel {t_py:.1f}s, rust kernel {t_rs:.2f}s "
      f"({t_py / max(t_rs, 1e-9):.0f}x)")

fail = False


def cmp(name, a, b, tol):
    global fail
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    d = float(np.abs(a - b).max()) if a.size else 0.0
    ok = d <= tol
    print(f"  {name}: max diff {d:.2e} (tol {tol:g}) {'OK' if ok else 'FAIL'}")
    if not ok:
        fail = True


cmp("stock", rp["stock"], rr["stock"], 0.0)          # identical f32 op order
if not (len(rp["snapshots"]) == len(rr["snapshots"]) == 3):
    print(f"  snapshots: count mismatch {len(rp['snapshots'])} vs "
          f"{len(rr['snapshots'])} FAIL")
    fail = True
else:
    for si, (a, b) in enumerate(zip(rp["snapshots"], rr["snapshots"])):
        cmp(f"snapshot[{si}]", a, b, 0.0)            # bit-exact, like stock
cmp("volume", rp["volume"], rr["volume"], 1e-3)      # float accumulation order
cmp("contact", rp["contact"], rr["contact"], 1e-5)
cmp("contact_samples", rp["contact_samples"], rr["contact_samples"], 0.0)
cmp("efrac", rp["efrac"], rr["efrac"], 1e-9)
cmp("shank_over", rp["shank_over"], rr["shank_over"], 1e-5)
cmp("worst_rapid", rp["worst_rapid"], rr["worst_rapid"], 1e-9)
cmp("min_cut_z", rp["min_cut_z"], rr["min_cut_z"], 1e-9)

# --- PCB-lane shapes (WS1): vee cone + scrub must agree the same way ------
print("vee/scrub synthetic case:")
kw2 = dict(
    n=200, ppm=12.5, half=8.0, step=0.06, check=True,
    dia=np.array([3.175, 0.3]),
    shape=np.array([2, 3], dtype=np.uint8),          # vee, scrub
    tip_r=np.array([0.1, 0.0]),
    slope=np.array([1.0 / np.tan(np.radians(15.0)), 0.0]),
    flute_len=np.array([10.0, 2.0]),
    shank_d=np.array([3.175, 3.175]),
    motion=np.array([0, 1, 1, 0, 1], dtype=np.uint8),
    x0=np.array([0.0, -5.0, -5.0, 5.0, -5.0]),
    y0=np.array([0.0, 0.0, 0.0, 0.0, 2.0]),
    z0=np.array([3.0, 3.0, -0.15, -0.15, -0.21]),
    x1=np.array([-5.0, -5.0, 5.0, -5.0, 5.0]),
    y1=np.array([0.0, 0.0, 0.0, 2.0, 2.0]),
    z1=np.array([3.0, -0.15, -0.15, -0.21, -0.21]),
    tool_idx=np.array([0, 0, 0, 1, 1], dtype=np.uint16),
    snap_after=np.array([2, 4], dtype=np.int64))
rp2 = kernel.measure_py(**kw2)
rr2 = kernel.measure_rust(**kw2)
cmp("stock", rp2["stock"], rr2["stock"], 0.0)
for si, (a, b) in enumerate(zip(rp2["snapshots"], rr2["snapshots"])):
    cmp(f"snapshot[{si}]", a, b, 0.0)
cmp("volume", rp2["volume"], rr2["volume"], 1e-3)
cmp("contact", rp2["contact"], rr2["contact"], 1e-5)
cmp("contact_samples", rp2["contact_samples"], rr2["contact_samples"], 0.0)
cmp("efrac", rp2["efrac"], rr2["efrac"], 1e-9)
cmp("worst_rapid", rp2["worst_rapid"], rr2["worst_rapid"], 1e-9)
cmp("min_cut_z", rp2["min_cut_z"], rr2["min_cut_z"], 1e-9)

print("PARITY " + ("FAIL" if fail else "PASS"))
sys.exit(1 if fail else 0)
