"""Verification gate: every .nc must pass here before touching metal
(requirement 3). Checks, each traced to a real incident:
  - lateral rapids vs remaining stock (footprint-dilated)
  - ball engagement <= MAX_ENGAGE (a 1mm ball snapped when this didn't exist)
  - surface completeness: model top actually machined to the skim plane,
    the ring outside the model cleared, slot floor at depth
  - fixture keep-out: NOTHING machined beyond job.keepout_radius
    (FreeCAD's box-bounded rough swept under the corner clamps)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .job import Job
from .simulate import CarveResult, carve_check

MAX_ENGAGE = 0.5


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
    carve: CarveResult

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

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
    res = carve_check(nc_path, job)
    stock, ppm, half = res.stock, res.ppm, res.half
    n = stock.shape[0]
    checks: list[Check] = []

    checks.append(Check("rapid-vs-stock", res.worst_rapid, "must be 0",
                        res.worst_rapid <= 1e-4,
                        f"at {res.rapid_at}" if res.rapid_at else ""))
    for t, (mx, at, cnt) in res.engagement.items():
        checks.append(Check(f"T{t} ball engagement", mx,
                            f"< {MAX_ENGAGE}",
                            mx < MAX_ENGAGE,
                            f"at {at}, {cnt} engaging samples"))

    # world coords MUST match the carve mapping (see simulate.py docstring)
    yy, xx = np.mgrid[0:n, 0:n]
    xw = xx / ppm - half
    yw = half - yy / ppm
    rr = np.hypot(xw, yw)

    r = job.model_radius
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

    return Report(checks, res)
