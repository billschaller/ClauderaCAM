"""Orchestrates toolpath generation for a Job: heightmaps and offset surfaces
are cached per resolution, each op dispatches to its generator, and the result
carries stats (moves, path length, time estimate) for reporting."""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from . import heightmap, offset
from .job import Job
from .ops import cutout, drill, raster, rough


@dataclass
class OpResult:
    label: str
    kind: str
    tool: int
    lines: list[str]
    path_len_mm: float
    est_min: float


def path_length(lines: list[str]) -> float:
    L, last = 0.0, None
    for ln in lines:
        m = dict(re.findall(r"([XYZ])(-?\d+\.?\d*)", ln))
        if not m:
            continue
        cur = [float(m.get(a, last[i] if last else 0)) for i, a in enumerate("XYZ")]
        if last:
            L += np.hypot(np.hypot(cur[0] - last[0], cur[1] - last[1]),
                          cur[2] - last[2])
        last = cur
    return float(L)


MAX_OVERCUT = 0.5   # deepest legal cut below the stock bottom (sacrificial)


def op_reach(job: Job, op: dict) -> float:
    """Outermost radius this op's tool EDGE can touch, including the up-to-
    grid/2 row-endpoint overshoot of the sampled serpentine generators
    (2026-07-28 review: the analytic formula understated the true reach)."""
    tool = job.tool(op["tool"])
    if op["kind"] == "rough":
        return op["boundary_r"] + tool.radius + op["grid"] / 2
    if op["kind"] == "raster":
        return job.model_radius + op["bound_extra"] + tool.radius \
            + op["grid"] / 2
    if op["kind"] == "cutout":
        return job.model_radius + tool.diameter
    if op["kind"] in ("spotface", "pindrill"):
        return max(np.hypot(x, y) for x, y in op["positions"]) + tool.radius
    raise ValueError(f"unknown op kind: {op['kind']}")


def check_job_plan(job: Job) -> None:
    """Static guards run before any toolpath exists. Every rule here is a
    review finding turned law (Article II):
      - fixture keep-out constrains generation, not just verification
      - op ORDER: rough first, cutout last (a cutout-first program severs
        the part onto its tabs and then roughs on top of it — and the
        geometric simulator, being order-commutative, cannot catch it)
      - cross-op COVERAGE: every ball raster and the cutout annulus must lie
        inside the rough-cleared disc (shrinking boundary_r once left the
        2mm ball slotting 2.1mm-deep virgin stock)
      - cutout depth semantics: enters above the cleared field, severs the
        stock, stays out of the machine bed
    """
    if not job.ops:
        raise ValueError("job has no ops")
    if job.ops[0]["kind"] != "rough":
        raise ValueError("first op must be a rough pass — every later op "
                         "assumes roughed stock")
    for i, op in enumerate(job.ops):
        if op["kind"] == "cutout" and i != len(job.ops) - 1:
            raise ValueError("cutout must be the LAST op — after it the part "
                             "hangs on its tabs")

    for op in job.ops:
        reach = op_reach(job, op)
        if reach > job.keepout_radius:
            raise ValueError(
                f"op '{op.get('label', op['kind'])}' reaches r={reach:.3f} "
                f"but fixture keep-out starts at r={job.keepout_radius}")

    for oi, op in enumerate(job.ops):
        if op["kind"] != "pindrill":
            continue
        # pin holes go through the stock into a SPOILBOARD, never the bed
        tool = job.tool(op["tool"])
        if tool.type != "drill":
            raise ValueError(
                f"pindrill op uses T{tool.num} ({tool.type}) — pecking a "
                f"12mm hole is a twist drill's job, not an end mill's")
        if job.spoil_thickness <= 0:
            raise ValueError(
                "pindrill without [spoilboard] thickness — a through hole "
                "must land in sacrificial material, not the machine bed")
        if op["depth"] <= job.stock_thickness:
            raise ValueError(
                f"pindrill depth {op['depth']} does not pass through "
                f"{job.stock_thickness}mm stock — a blind hole cannot "
                f"register a flip")
        if op["depth"] > job.stock_thickness + job.spoil_thickness - 2.0:
            raise ValueError(
                f"pindrill depth {op['depth']} comes within 2mm of the "
                f"machine bed (stock {job.stock_thickness} + spoilboard "
                f"{job.spoil_thickness})")
        # counterbore credit: a preceding spot-face at the SAME positions,
        # cut at least as wide as the drill's shank, lowers the drill's
        # entry — its reach extends by the counterbore depth. (The kernel
        # shank-clearance check remains the physical judge; this rule just
        # refuses obviously-impossible plans early.)
        spot = 0.0
        for prior in job.ops[:oi]:
            if prior["kind"] != "spotface":
                continue
            spot_tool = job.tool(prior["tool"])
            have = {(round(x, 3), round(y, 3))
                    for x, y in prior["positions"]}
            need = {(round(x, 3), round(y, 3)) for x, y in op["positions"]}
            if need <= have and spot_tool.diameter + 1e-9 >= \
                    tool.shank_diameter:
                spot = max(spot, prior["depth"])
        if op["depth"] > tool.flute_length + spot:
            raise ValueError(
                f"pindrill depth {op['depth']} exceeds T{tool.num}'s "
                f"{tool.flute_length}mm reach"
                + (f" plus the {spot}mm counterbore" if spot else "")
                + " — the shank would enter the hole (measure the drill's "
                "reach-to-shank-step before trusting this number; a deeper "
                "spot-face counterbore at the pin positions extends reach)")

    cleared = max((op["boundary_r"] + job.tool(op["tool"]).radius
                   for op in job.ops if op["kind"] == "rough"))
    rough_allow = max(op["allowance"] for op in job.ops
                      if op["kind"] == "rough")
    for op in job.ops:
        label = op.get("label", op["kind"])
        if op["kind"] == "raster":
            reach = op_reach(job, op)
            if reach > cleared + 1e-9:
                raise ValueError(
                    f"op '{label}' ball reaches r={reach:.3f} but roughing "
                    f"only clears r={cleared:.3f} — the ball would meet "
                    f"full-height stock")
        elif op["kind"] == "cutout":
            tool = job.tool(op["tool"])
            slot_outer = job.model_radius + tool.diameter
            if slot_outer > cleared + 1e-9:
                raise ValueError(
                    f"cutout slot reaches r={slot_outer:.3f} but roughing "
                    f"only clears r={cleared:.3f}")
            if op["z_start"] < job.floor_z + rough_allow - 1e-9:
                raise ValueError(
                    f"cutout z_start {op['z_start']} is below the "
                    f"rough-cleared field "
                    f"({job.floor_z + rough_allow:.3f}) — the entry would "
                    f"slam full depth")
            if op["z_final"] > -job.stock_thickness + 1e-9:
                raise ValueError(
                    f"cutout z_final {op['z_final']} does not sever "
                    f"{job.stock_thickness}mm stock")
            if op["z_final"] < -(job.stock_thickness + MAX_OVERCUT) - 1e-9:
                raise ValueError(
                    f"cutout z_final {op['z_final']} cuts more than "
                    f"{MAX_OVERCUT}mm below the stock bottom")


def generate_ops(job: Job) -> list[OpResult]:
    check_job_plan(job)

    tris = job.model_tris()
    hmaps: dict[tuple, np.ndarray] = {}
    offs: dict[tuple, np.ndarray] = {}

    def hmap(half: float, grid: float) -> np.ndarray:
        key = (round(half, 6), round(grid, 6))
        if key not in hmaps:
            H = heightmap.rasterize(tris, half, grid)
            if job.clip_disc:
                # tris already carry z_offset; the map is in job coords
                H = heightmap.clip_disc(H, half, grid, job.model_radius,
                                        job.floor_z)
            hmaps[key] = H
        return hmaps[key]

    results: list[OpResult] = []
    for op in job.ops:
        kind = op["kind"]
        label = op.get("label", kind)
        tool = job.tool(op["tool"])
        if kind == "rough":
            half, grid = op["map_half"], op["grid"]
            key = ("flat", tool.num, half, grid, op["allowance"])
            if key not in offs:
                offs[key] = offset.flat_offset(hmap(half, grid), tool.radius, grid) \
                    + op["allowance"]
            z_final = job.floor_z + op["allowance"]
            lines = rough.generate(
                offs[key], half=half, grid=grid, rb=op["boundary_r"],
                stepover=op["stepover"],
                layers=rough.make_layers(op["stepdown"], z_final),
                feed=op["feed"], plunge_feed=op["plunge"],
                simplify_tol=op.get("simplify", 0.01))
        elif kind == "raster":
            half = job.model_radius + op["margin"]
            grid = op["grid"]
            key = ("ball", tool.num, half, grid)
            if key not in offs:
                offs[key] = offset.ball_offset(hmap(half, grid), tool.radius, grid)
            lines = raster.generate(
                offs[key], half=half, grid=grid,
                bound=job.model_radius + op["bound_extra"],
                stepover=op["stepover"], zextra=op["offset"], axis=op["axis"],
                feed=op["feed"], plunge_feed=op["plunge"],
                simplify_tol=op.get("simplify", 0.005))
        elif kind == "cutout":
            lines = cutout.generate(
                rc=job.model_radius + tool.radius,
                z_start=op["z_start"], z_final=op["z_final"], ramp=op["ramp"],
                tab_top=op["tab_top"], tab_width=op["tab_width"],
                tool_dia=tool.diameter, tab_centers_deg=op["tabs"],
                seg=op["seg"], feed=op["feed"], plunge_feed=op["plunge"])
        elif kind == "spotface":
            lines = drill.spotface(op["positions"], op["depth"], op["feed"])
        elif kind == "pindrill":
            lines = drill.pindrill(op["positions"], op["depth"],
                                   op["peck"], op["feed"])
        else:
            raise ValueError(f"unknown op kind: {kind}")
        plen = path_length(lines)
        results.append(OpResult(label, kind, tool.num, lines,
                                plen, plen / op["feed"]))
    return results
