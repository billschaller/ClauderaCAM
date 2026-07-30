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
from .physics import physics_checks
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
MAX_ENGAGE_DRILL = 1.2   # a peck advances 0.8 into its own hole; drills cut
#                          full-face by design, so the bound exists to catch
#                          a runaway peck, not flank flex. PROVISIONAL: no
#                          pin hole drilled on this machine yet.
MAX_ENGAGE_VEE = 0.35    # a vee's working depth is shallow by design (the
#                          zigbee boards validated -0.15 isolation); a cone
#                          buried past ~2x that is a snapped engraver tip,
#                          the most fragile thing in the crib. PROVISIONAL:
#                          no vee cut by this pipeline yet.
MAX_OVERCUT = 0.5        # below stock bottom (sacrificial board territory)
GOUGE_TOL = 0.15         # measured max on validated jobs: 0.102


def contact_limit(tool) -> float:
    """Per-tool footprint-contact limit (derivation above). Single source —
    used by the gate here and by the per-stage model in stages.py."""
    if tool.type == "ball":
        return BALL_ENGAGE_FRAC * tool.diameter
    if tool.type == "drill":
        return MAX_ENGAGE_DRILL
    if tool.type == "vee":
        return MAX_ENGAGE_VEE
    # scrub tools have an empty kernel footprint (contact is always 0);
    # the flat limit here is a formality, never the judge
    return MAX_ENGAGE_FLAT


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
    program: str = ""   # the EXACT text this report judged — downloads
    #                     serve these bytes, never the (possibly newer)
    #                     file on disk

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
        limit = contact_limit(tool)
        c = res.contact[t]
        checks.append(Check(
            f"T{t} {tool.type} contact", c.max, f"< {limit:g}", c.max < limit,
            f"at {c.at}, {c.samples} contact samples" if c.at else ""))

    # shank/holder clearance: geometric FACT — stock standing above the
    # bottom of the shank anywhere under its footprint is a crash
    checks.append(Check("shank clearance", res.shank_worst, "must be 0",
                        res.shank_worst <= 1e-6,
                        f"at {res.shank_at[:2]}, line {res.shank_at[2]}"
                        if res.shank_at else ""))

    # world coords MUST match the carve mapping (see simulate.py docstring)
    yy, xx = np.mgrid[0:n, 0:n]
    xw = xx / ppm - half
    yw = half - yy / ppm
    rr = np.hypot(xw, yw)
    r = job.model_radius

    # pin holes (pindrill ops) are the ONE legal way below the overcut
    # floor: through the stock into the spoilboard. Everything they exempt
    # from the depth floor they must themselves account for — position,
    # depth, and staying clear of the machine bed.
    pin_specs = [(x, y, op["depth"], job.tool(op["tool"]).radius)
                 for op in job.ops if op["kind"] == "pindrill"
                 for x, y in op["positions"]]
    depth_limit = -(job.stock_thickness + MAX_OVERCUT)
    if not pin_specs:
        worst_depth = min(res.min_cut_z, float(stock.min()))
        checks.append(Check("depth floor", worst_depth,
                            f">= {depth_limit:.3f}",
                            worst_depth >= depth_limit))
    else:
        m = res.metrics
        is_drill = np.array([job.tool(int(t)).type == "drill"
                             for t in m.tool_num])
        cutmove = m.motion == 1
        nz = np.minimum(m.z0, m.z1)
        sel = cutmove & ~is_drill
        worst_nondrill = float(nz[sel].min()) if sel.any() else 0.0
        pinmask = np.zeros_like(stock, dtype=bool)
        for px_, py_, _, prad in pin_specs:
            pinmask |= np.hypot(xw - px_, yw - py_) <= prad + 0.3
        worst_depth = min(worst_nondrill, float(stock[~pinmask].min()))
        checks.append(Check("depth floor (excl. pin holes)", worst_depth,
                            f">= {depth_limit:.3f}",
                            worst_depth >= depth_limit))
        # every drill cutting move must be AT a configured pin position
        dsel = cutmove & is_drill
        if dsel.any():
            dx = m.x1[dsel]
            dy = m.y1[dsel]
            dist = np.min(np.stack([np.hypot(dx - px_, dy - py_)
                                    for px_, py_, _, _ in pin_specs]),
                          axis=0)
            worst_off = float(dist.max())
        else:
            worst_off = 0.0
        checks.append(Check("drill only at pin positions", worst_off,
                            "<= 0.2", worst_off <= 0.2))
        # drilled depth at each pin (simulated as a flat-bottom hole; the
        # real drill point adds its cone below this — depth already
        # includes the tip allowance)
        worst_err = 0.0
        for px_, py_, pdepth, prad in pin_specs:
            disc = np.hypot(xw - px_, yw - py_) <= max(prad - 0.15, 0.1)
            hole = float(stock[disc].min())
            worst_err = max(worst_err, abs(hole + pdepth))
        checks.append(Check("pin hole depth error", worst_err, "<= 0.1",
                            worst_err <= 0.1,
                            f"{len(pin_specs)} holes"))
        bed_limit = -(job.stock_thickness + job.spoil_thickness - 2.0)
        drill_tip = float(nz[dsel].min()) if dsel.any() else 0.0
        checks.append(Check("drill clear of machine bed", drill_tip,
                            f">= {bed_limit:.3f}", drill_tip >= bed_limit))

    # gouge: nowhere inside the model may the stock be cut below the target
    # surface. The target is rasterized in heightmap convention and then
    # SAMPLED at each carve pixel's world coordinates — never assume the two
    # grids share indexing (requirement 4). The comparison uses the LOWER
    # envelope (3x3 min-filter) of the max-z raster: at a near-vertical wall
    # a single pixel legitimately contains both wall top and wall bottom, and
    # the cut correctly reaches the bottom — without the envelope that reads
    # as a wall-height "gouge". Real gouges are deeper than one pixel wide.
    tris = job.model_tris()
    H = heightmap.rasterize(tris, half, 1.0 / ppm).astype(np.float32)
    if job.clip_disc:
        H = heightmap.clip_disc(H, half, 1.0 / ppm, job.model_radius,
                                job.floor_z).astype(np.float32)
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

    cut = next((op for op in job.ops if op["kind"] == "cutout"), None)
    if cut:
        # field ring: the annulus the cutout enters must be cleared to
        # rough depth (guards the slam its z_start promises against).
        # Scoped to sides WITH a cutout: on a two-sided front the ring
        # legitimately carries art fillets, and the back side — the one
        # that cuts — runs this check on its own field.
        ring = (rr > r + 0.35) & (rr < r + 1.15)
        rough_allow = next((op["allowance"] for op in job.ops
                            if op["kind"] == "rough"), 0.2)
        ring_limit = job.floor_z + rough_allow + 0.1
        ring_top = float(stock[ring].max())
        checks.append(Check("field ring top", ring_top,
                            f"<= {ring_limit:.3f}", ring_top <= ring_limit))
        floor = float(stock.min()) if not pin_specs \
            else float(stock[~pinmask].min())
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
    program_text = open(nc_path).read()
    problems = lint_program(program_text.splitlines())
    checks.append(Check("dialect lint", float(len(problems)), "0 problems",
                        not problems, "; ".join(problems[:3])))

    # cutting-physics checks (model-based verdicts — see physics.py for the
    # honesty contract and each limit's provenance)
    for pc in physics_checks(job, res.metrics):
        checks.append(Check(pc.name, pc.value, pc.limit, pc.ok, pc.detail))

    return Report(checks, res, program=program_text)
