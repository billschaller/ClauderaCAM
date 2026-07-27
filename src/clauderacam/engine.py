"""Orchestrates toolpath generation for a Job: heightmaps and offset surfaces
are cached per resolution, each op dispatches to its generator, and the result
carries stats (moves, path length, time estimate) for reporting."""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from . import heightmap, offset
from .job import Job
from .ops import cutout, raster, rough


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


def generate_ops(job: Job) -> list[OpResult]:
    tris = heightmap.load_stl(job.stl)
    hmaps: dict[tuple, np.ndarray] = {}
    offs: dict[tuple, np.ndarray] = {}

    def hmap(half: float, grid: float) -> np.ndarray:
        key = (round(half, 6), round(grid, 6))
        if key not in hmaps:
            hmaps[key] = heightmap.rasterize(tris, half, grid)
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
        else:
            raise ValueError(f"unknown op kind: {kind}")
        plen = path_length(lines)
        results.append(OpResult(label, kind, tool.num, lines,
                                plen, plen / op["feed"]))
    return results
