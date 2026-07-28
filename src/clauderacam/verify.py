"""Verification gate: every .nc must pass here before touching metal
(requirement 3). Rebuilt after the 2026-07-28 adversarial review.

Checks, each traced to a real incident or a review finding:
  - fatal strict parse: anything the simulator cannot model fails the file
    (unknown tools, arcs in any spelling, G-less modal lines)
  - lateral AND descending rapids vs remaining stock (footprint-dilated)
  - TRUE tool contact for EVERY tool: max stock above the cutting surface
    over the whole footprint, measured before each sample carves — limits
    per tool type (a 1mm ball snapped at ~1.4mm of true contact; the old
    center-column metric reported 0.06mm for that class of cut)
  - depth floor: no commanded cut and no simulated stock below
    stock bottom + MAX_OVERCUT (a G1 to the machine bed used to pass)
  - gouge: no stock cut below the target model surface (tolerance covers
    grid quantization, calibrated against the metal-validated mango job)
  - surface completeness: model machined to the skim plane, field ring
    cleared, slot floor at depth
  - sever: the cutout ring is actually through the stock everywhere except
    the tab arcs (previously the floor check rubber-stamped the config)
  - fixture keep-out: NOTHING machined beyond job.keepout_radius
  - dialect lint on the program text: M5 before M6, G4 dwell after M3,
    G21/G90 before motion, S>0, 128-char lines (the simulator is blind to
    spindle-state semantics; the lint is not)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from . import heightmap
from .emit import lint_program
from .job import Job
from .simulate import CarveResult, GcodeError, carve_check

# Contact limits on the TRUE per-move footprint metric (see simulate.py),
# re-derived 2026-07-28 because the metric changed scales: the old
# center-column metric reported ~0.06-0.13mm for everything, so its 0.5
# limit measured nothing. Ball limits scale with diameter (flute stiffness
# ~d^4; a bite that flexes a 1mm flute is a nick to a 2mm one). Anchors on
# the new scale, all from metal: the bytes that cut the flawless brass coin
# measure T2(2mm ball) 1.007mm and T4(1mm ball) 0.544mm max single-move
# flank contact on art walls — SURVIVED; the incident that snapped a 1mm
# ball measures ~1.4mm — KILLED. 0.65×d gives T4 a 0.65 limit (0.11 above
# validated, less than half the kill) and T2 1.3 (0.29 above validated).
# Full derivation: DESIGN.md "Engagement recalibration".
BALL_ENGAGE_FRAC = 0.65  # of tool diameter
MAX_ENGAGE_FLAT = 1.0    # measured max on validated jobs: 0.40 (rough end caps)
MAX_OVERCUT = 0.5        # below stock bottom (sacrificial board territory)
GOUGE_TOL = 0.15         # measured max on validated jobs: 0.102


@dataclass
class Check:
    name: str
    value: float
    limit: str
    ok: bool
    detail: str = ""

    def __post_init__(self):
        # numpy scalars leak in from comparisons; JSON needs plain types
        self.value = float(self.value)
        self.ok = bool(self.ok)


@dataclass
class Report:
    checks: list[Check]
    carve: CarveResult | None

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @classmethod
    def fatal(cls, msg: str) -> "Report":
        return cls([Check("gcode fatal", 0.0, "must parse", False, msg)], None)

    def text(self) -> str:
        lines = []
        for c in self.checks:
            mark = "PASS" if c.ok else "FAIL"
            lines.append(f"{c.name}: {c.value:.3f}mm ({c.limit})  {mark}"
                         + (f"  {c.detail}" if c.detail else ""))
        lines.append("VERDICT: " + ("PASS — cleared for metal" if self.ok
                                    else "FAIL — do NOT cut this file"))
        return "\n".join(lines)


def verify(job: Job, nc_path=None) -> Report:
    nc_path = nc_path or job.out
    try:
        res = carve_check(nc_path, job)
    except GcodeError as e:
        return Report.fatal(str(e))
    stock, ppm, half = res.stock, res.ppm, res.half
    n = stock.shape[0]
    checks: list[Check] = []

    checks.append(Check("rapid-vs-stock", res.worst_rapid, "must be 0",
                        res.worst_rapid <= 1e-4,
                        f"at {res.rapid_at}" if res.rapid_at else ""))

    for t in sorted(res.contact):
        tool = job.tool(t)
        limit = BALL_ENGAGE_FRAC * tool.diameter if tool.type == "ball" \
            else MAX_ENGAGE_FLAT
        c = res.contact[t]
        checks.append(Check(
            f"T{t} {tool.type} contact", c.max, f"< {limit:g}", c.max < limit,
            f"at {c.at}, {c.samples} contact samples" if c.at else ""))

    depth_limit = -(job.stock_thickness + MAX_OVERCUT)
    worst_depth = min(res.min_cut_z, float(stock.min()))
    checks.append(Check("depth floor", worst_depth,
                        f">= {depth_limit:.3f}", worst_depth >= depth_limit))

    # world coords MUST match the carve mapping (see simulate.py docstring)
    yy, xx = np.mgrid[0:n, 0:n]
    xw = xx / ppm - half
    yw = half - yy / ppm
    rr = np.hypot(xw, yw)
    r = job.model_radius

    # gouge: nowhere inside the model may the stock be cut below the target
    # surface. The target is rasterized in heightmap convention and then
    # SAMPLED at each carve pixel's world coordinates — never assume the two
    # grids share indexing (requirement 4). The comparison uses the LOWER
    # envelope (3x3 min-filter) of the max-z raster: at a near-vertical wall
    # a single pixel legitimately contains both wall top and wall bottom, and
    # the cut correctly reaches the bottom — without the envelope that reads
    # as a wall-height "gouge". Real gouges are deeper than one pixel wide.
    tris = heightmap.load_stl(job.stl)
    H = heightmap.rasterize(tris, half, 1.0 / ppm).astype(np.float32)
    H = ndimage.minimum_filter(H, size=3, mode="nearest")
    npx = H.shape[0]
    iH = np.clip(np.round((yw + half) * ppm).astype(int), 0, npx - 1)
    jH = np.clip(np.round((xw + half) * ppm).astype(int), 0, npx - 1)
    target = H[iH, jH]
    core = rr < r - 0.3
    gouge = float((target[core] - stock[core]).max())
    checks.append(Check("gouge below target", gouge,
                        f"<= {GOUGE_TOL}", gouge <= GOUGE_TOL))

    coin_top = float(stock[rr < r - 0.2].max())
    top_limit = -(job.skim - 0.005)
    checks.append(Check("model surface top", coin_top,
                        f"<= {top_limit:.3f}", coin_top <= top_limit))

    ring = (rr > r + 0.35) & (rr < r + 1.15)
    rough_allow = next((op["allowance"] for op in job.ops
                        if op["kind"] == "rough"), 0.2)
    ring_limit = job.floor_z + rough_allow + 0.1
    ring_top = float(stock[ring].max())
    checks.append(Check("field ring top", ring_top,
                        f"<= {ring_limit:.3f}", ring_top <= ring_limit))

    cut = next((op for op in job.ops if op["kind"] == "cutout"), None)
    if cut:
        floor = float(stock.min())
        checks.append(Check("slot floor", floor,
                            f"== {cut['z_final']:.3f} ±0.02",
                            abs(floor - cut["z_final"]) <= 0.02))
        # sever: the slot ring must be through the stock except at the tabs
        tool = job.tool(cut["tool"])
        rc = job.model_radius + tool.radius
        tab_arc = (cut["tab_width"] + tool.diameter) / rc
        seg_arc = 2 * np.pi / cut["seg"]
        halfw = tab_arc / 2 + 0.5 / rc + 2 * seg_arc
        theta = np.arctan2(yw, xw)
        in_tab = np.zeros_like(theta, dtype=bool)
        for a_deg in cut["tabs"]:
            a = np.radians(a_deg)
            in_tab |= np.abs((theta - a + np.pi) % (2 * np.pi) - np.pi) < halfw
        ringc = (np.abs(rr - rc) < 0.25) & ~in_tab
        sever_top = float(stock[ringc].max())
        checks.append(Check("sever (slot through stock)", sever_top,
                            f"<= {-job.stock_thickness:.3f}",
                            sever_top <= -job.stock_thickness))

    keep = (rr > job.keepout_radius) & \
        (np.abs(xw) <= job.stock_half) & (np.abs(yw) <= job.stock_half)
    kmin = float(stock[keep].min())
    detail = ""
    if kmin <= -0.001:
        flat = np.where(keep, stock, 0.0)
        i_b, j_b = np.unravel_index(np.argmin(flat), stock.shape)
        detail = (f"deepest at x={j_b/ppm-half:.2f} y={half-i_b/ppm:.2f} "
                  f"r={np.hypot(j_b/ppm-half, half-i_b/ppm):.3f}")
    checks.append(Check(f"fixture keep-out (r>{job.keepout_radius})", kmin,
                        "must be 0", kmin > -0.001, detail))

    # dialect lint on the raw program text (spindle-state semantics the
    # geometric simulator cannot see)
    problems = lint_program(open(nc_path).read().splitlines())
    checks.append(Check("dialect lint", float(len(problems)), "0 problems",
                        not problems, "; ".join(problems[:3])))

    return Report(checks, res)
