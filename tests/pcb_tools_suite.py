"""PCB-lane tool modeling (WS1): the vee cone, the scrub exemption, the
laser dialect, and the crib entries that back them.

What must hold, and why (each traced to PCB-PLAN.md / the guides):
  - vee drop is the closed-form cone: drop(rr) = max(rr - tip_r, 0) * cot
    (the kernel is the ONLY place the shape lives — a wrong cone lies to
    every downstream check)
  - a scrub tool removes NOTHING in simulation (commanded depth = spring
    preload, mask guide §1; Article IX exemption stated in kernel_py and
    physics.py) — and its plunge is exempt from mill plunge limits
  - a vee job tool without tip/angle refuses to load; a vee whose cone
    doesn't match the crib entry refuses (Article XI: the cone IS the tool)
  - the laser dialect law (mask guide §5): M321 first, then EXACTLY G0 Z0
    (the defocus incident), one M3 with 0 < S <= ceiling, no other Z, no
    tool changes; the mill gate refuses M321 on sight so the two program
    families can never cross-contaminate

Run: .venv/bin/python tests/pcb_tools_suite.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np

from clauderacam import emit, job as jobmod, kernel_py, physics, simulate
from clauderacam.simulate import GcodeError

REPO = Path(__file__).resolve().parents[1]
fails = []


def check(name, ok, detail=""):
    print(f"  {name}: {'OK' if ok else 'FAIL'}" + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def caught(name, fn, needle):
    try:
        fn()
    except Exception as e:
        check(name, needle in str(e), f"got: {str(e)[:90]}")
        return
    check(name, False, "no exception raised")


# --- 1. vee kit: closed-form cone ------------------------------------------
print("vee kit drop:")
ppm = 25.0
tip_r, slope = 0.1, 1.0 / np.tan(np.radians(15.0))
r_px, foot, foot_in, drop = kernel_py._kit(3.175, kernel_py.VEE, ppm,
                                           tip_r, slope)
c = r_px  # center index
for dxpx in (0, 1, 3, 10, 30):
    rr = dxpx / ppm
    want = np.float32(max(rr - tip_r, 0.0) * slope)
    got = drop[c, c + dxpx]
    check(f"drop @ {rr:.2f}mm", got == want, f"{got} vs {want}")
check("footprint is the full cutting disc",
      bool(foot[c, c + int(3.175 / 2 * ppm) - 1]) and not bool(foot[c, 0]))

# --- 2. scrub kit: empty footprint -----------------------------------------
print("scrub kit:")
_, sfoot, sfoot_in, sdrop = kernel_py._kit(0.3, kernel_py.SCRUB, ppm)
check("empty footprint", not sfoot.any() and not sfoot_in.any())

# --- 3. kernel semantics ----------------------------------------------------
print("kernel semantics:")
base = dict(n=200, ppm=12.5, half=8.0, step=0.06, check=True,
            flute_len=np.array([10.0]), shank_d=np.array([3.175]),
            motion=np.array([1], dtype=np.uint8),
            x0=np.array([-5.0]), y0=np.array([0.0]), z0=np.array([-0.21]),
            x1=np.array([5.0]), y1=np.array([0.0]), z1=np.array([-0.21]),
            tool_idx=np.array([0], dtype=np.uint16))
# scrub: a cutting move buried 0.21 into stock touches NOTHING
rs = kernel_py.measure(dia=np.array([0.3]),
                       shape=np.array([3], dtype=np.uint8),
                       tip_r=np.array([0.0]), slope=np.array([0.0]), **base)
check("scrub removes nothing", float(rs["stock"].min()) == 0.0
      and float(rs["volume"].sum()) == 0.0)
check("scrub contacts nothing", float(rs["contact"].max()) == 0.0
      and int(rs["contact_samples"].sum()) == 0)
check("scrub leaves min_cut_z untouched", rs["min_cut_z"] == 0.0)

# vee: same move at -0.15 carves the closed-form kerf
vb = dict(base)
vb.update(z0=np.array([-0.15]), z1=np.array([-0.15]))
rv = kernel_py.measure(dia=np.array([3.175]),
                       shape=np.array([2], dtype=np.uint8),
                       tip_r=np.array([tip_r]), slope=np.array([slope]),
                       **vb)
stock = rv["stock"]
n, half_, ppm_ = 200, 8.0, 12.5
i0 = int(round((half_ - 0.0) * ppm_))          # row at y=0 (Article IV)
prof = stock[i0]
check("vee centerline depth", abs(float(prof.min()) + 0.15) < 1e-6,
      f"{prof.min():.4f}")
kerf_r = tip_r + 0.15 * np.tan(np.radians(15.0))
ycoords = half_ - np.arange(n) / ppm_
cut_rows = np.nonzero(stock.min(axis=1) < -1e-6)[0]
worst_y = float(np.abs(ycoords[cut_rows]).max())
check("vee kerf bounded by cone", worst_y <= kerf_r + 1.5 / ppm_,
      f"kerf half-width {worst_y:.3f} vs cone {kerf_r:.3f}")
check("vee removed volume > 0", float(rv["volume"].sum()) > 0)

# --- 4. job grammar + crib (Article XI) -------------------------------------
print("job grammar + crib:")
if not (REPO / "assets" / "generated" / "dome.stl").exists():
    import runpy
    runpy.run_path(str(REPO / "assets" / "generate_references.py"),
                   run_name="__main__")
dome = (REPO / "jobs" / "dome.toml").read_text()
VEE_TOOL = """
[[tool]]
num = 9
type = "vee"
diameter = 3.175
tip_diameter = 0.2
included_angle_deg = 30.0
rpm = 12000
flutes = 1
flute_length = 10.0
shank_diameter = 3.175
"""
SCRUB_TOOL = """
[[tool]]
num = 10
type = "scrub"
diameter = 0.3
rpm = 6000
flutes = 1
flute_length = 2.0
shank_diameter = 3.175
"""


def load_variant(extra):
    with tempfile.NamedTemporaryFile("w", dir=REPO / "jobs",
                                     suffix=".toml", delete=False) as f:
        f.write(dome + extra)
        p = Path(f.name)
    try:
        return jobmod.load(p)
    finally:
        p.unlink()


jv = load_variant(VEE_TOOL + SCRUB_TOOL)
t9, t10 = jv.tools[9], jv.tools[10]
check("vee + scrub tools load against the crib",
      t9.type == "vee" and t9.tip_diameter == 0.2
      and t9.included_angle_deg == 30.0 and t10.type == "scrub")
caught("vee without tip refuses",
       lambda: load_variant(VEE_TOOL.replace("tip_diameter = 0.2\n", "")),
       "vee tools require")
caught("vee with the wrong cone refuses (crib mismatch)",
       lambda: load_variant(VEE_TOOL.replace("included_angle_deg = 30.0",
                                             "included_angle_deg = 25.0")),
       "not in the inventory")
caught("scrub the shop does not hold refuses",
       lambda: load_variant(SCRUB_TOOL.replace("diameter = 0.3",
                                               "diameter = 0.4")),
       "not in the inventory")

# --- 5. scrub plunge exemption (Article IX) ---------------------------------
print("physics exemptions:")
m = simulate.MoveMetrics(
    motion=np.array([1], dtype=np.uint8),
    x0=np.array([2.0]), y0=np.array([0.0]), z0=np.array([0.0]),
    x1=np.array([2.0]), y1=np.array([0.0]), z1=np.array([-0.21]),
    tool_num=np.array([10]), feed=np.array([500.0]),
    rpm=np.array([6000.0]), lineno=np.array([1], dtype=np.uint32))
m.volume = np.zeros(1); m.contact = np.zeros(1)
m.contact_x = np.zeros(1); m.contact_y = np.zeros(1)
m.contact_samples = np.zeros(1, dtype=np.uint32)
m.efrac = np.zeros(1); m.shank_over = np.zeros(1)
pcs = physics.physics_checks(jv, m)
pl = next(c for c in pcs if c.name == "plunge feed")
check("scrub plunge exempt from mill plunge limit",
      pl.ok and pl.value == 0.0, f"value {pl.value}")
check("fr4/fr1 material entries exist",
      all(k in physics.MATERIALS for k in ("fr4", "fr1"))
      and all(f in physics.MATERIALS["fr4"] for f in
              ("specific_energy", "fz_min_frac", "a_tooth_max_frac",
               "plunge_feed_max", "drill_plunge_max", "sustained_w",
               "heat_window_s", "enclosed_chip_mm3")))

# --- 6. laser dialect --------------------------------------------------------
print("laser dialect:")
text = emit.assemble_laser(
    "silk-test", [[(0.0, 0.0), (5.0, 0.0), (5.0, 5.0)],
                  [(1.0, 1.0), (2.0, 2.0)]])
lines = text.splitlines()
check("assembled laser program lints clean", emit.lint_laser(lines) == [])
caught("mill gate refuses M321 on sight",
       lambda: simulate.parse_line("M321", 1), "unsupported M321")
caught("dose above ceiling refuses at emit",
       lambda: emit.assemble_laser("hot", [[(0, 0), (1, 1)]], dose_s=0.5),
       "outside")

no_focus = [ln for ln in lines if ln.strip() != "G0 Z0"]
check("missing G0 Z0 focus move caught",
      any("focus" in p for p in emit.lint_laser(no_focus)))
z_stroke = lines[:-2] + ["G1 X1.000 Y1.000 Z-0.200"] + lines[-2:]
check("stray Z word caught",
      any("Z word" in p for p in emit.lint_laser(z_stroke)))
toolchange = lines[:-2] + ["M5", "M6 T2"] + lines[-2:]
check("tool change caught",
      any("laser" in p and ("tool" in p or "dialect" in p)
          for p in emit.lint_laser(toolchange)))
hot = [ln.replace("S0.03", "S0.5") if ln.startswith("M3") else ln
       for ln in lines]
check("hot dose caught by lint",
      any("ceiling" in p for p in emit.lint_laser(hot)))
no_m5 = [ln for ln in lines if ln.strip() != "M5"]
check("missing disarm caught",
      any("disarmed" in p for p in emit.lint_laser(no_m5)))

print(f"\nPCB TOOLS SUITE {'FAIL: ' + ', '.join(fails) if fails else 'PASS'}")
sys.exit(1 if fails else 0)
