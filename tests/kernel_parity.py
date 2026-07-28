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
    ball=np.array([1 if j.tools[t].type == "ball" else 0
                   for t in tool_nums], dtype=np.uint8),
    flute_len=np.array([j.tools[t].flute_length for t in tool_nums]),
    shank_d=np.array([j.tools[t].shank_diameter for t in tool_nums]),
    motion=m.motion, x0=m.x0, y0=m.y0, z0=m.z0,
    x1=m.x1, y1=m.y1, z1=m.z1,
    tool_idx=np.array([idx_of[int(t)] for t in m.tool_num], dtype=np.uint16))

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
cmp("volume", rp["volume"], rr["volume"], 1e-3)      # float accumulation order
cmp("contact", rp["contact"], rr["contact"], 1e-5)
cmp("contact_samples", rp["contact_samples"], rr["contact_samples"], 0.0)
cmp("efrac", rp["efrac"], rr["efrac"], 1e-9)
cmp("shank_over", rp["shank_over"], rr["shank_over"], 1e-5)
cmp("worst_rapid", rp["worst_rapid"], rr["worst_rapid"], 1e-9)
cmp("min_cut_z", rp["min_cut_z"], rr["min_cut_z"], 1e-9)

print("PARITY " + ("FAIL" if fail else "PASS"))
sys.exit(1 if fail else 0)
