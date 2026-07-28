"""Layered flat-endmill roughing inside a circular boundary.

Requirement 1 (DESIGN.md): the boundary is a DISC of tool-center radius
`rb`, never a bounding box. Each layer serpentines the disc with z clamped
to max(layer, flat_offset + allowance), then runs a full perimeter ring at
r = rb, so axial engagement never exceeds one stepdown and the promise
"cleared to rb + tool_radius" holds over the whole disc.

The 2026-07-28 review found the original row placement started at c = -rb —
a zero-length chord that was silently skipped, leaving the disc's south pole
uncovered by a full stepover. In the job that cut the brass coin that gap
put ~21mm² of full-height stock inside the cutout annulus, and the cutout's
first revolution took a 1.97mm full-width bite instead of its 0.15mm/rev
ramp. Rows are now centered (never touching the rim), and the per-layer
perimeter ring guarantees the boundary annulus regardless of how stepover
divides the disc.
"""
from __future__ import annotations

import numpy as np

from ..heightmap import simplify


def make_layers(stepdown: float, z_final: float) -> list[float]:
    layers = [round(z, 4) for z in np.arange(-stepdown, z_final, -stepdown)]
    if not layers or layers[-1] > z_final + 1e-9:
        layers.append(round(z_final, 4))
    return layers


def row_coords(rb: float, stepover: float) -> np.ndarray:
    """Serpentine row positions: spacing `stepover`, centered so no row
    lands on the rim (a rim row has a zero-length chord and cuts nothing)."""
    n_rows = int(np.floor(2 * rb / stepover)) + 1
    span = (n_rows - 1) * stepover
    if span >= 2 * rb - 1e-9:   # stepover divides 2*rb exactly: inset
        n_rows -= 1
        span -= stepover
    off0 = (2 * rb - span) / 2
    return -rb + off0 + np.arange(n_rows) * stepover


def generate(off: np.ndarray, *, half: float, grid: float, rb: float,
             stepover: float, layers: list[float],
             feed: int, plunge_feed: int,
             safe_z: float = 3.0, simplify_tol: float = 0.01) -> list[str]:
    """`off` is the flat-tool offset surface WITH allowance already added."""
    npx = off.shape[0]

    def sample(x, y):
        j = int(round(np.clip((x + half) / grid, 0, npx - 1)))
        i = int(round(np.clip((y + half) / grid, 0, npx - 1)))
        return off[i, j]

    lines: list[str] = []
    emit = lines.append
    coords = row_coords(rb, stepover)
    ring_seg = max(96, int(np.ceil(2 * np.pi * rb)))   # ~1mm chords
    fwd = True
    for L in layers:
        first = True
        prev_end = None
        for c in coords:
            span = rb**2 - c**2
            if span <= 0:
                continue
            s = np.sqrt(span)
            ts = np.arange(-s, s + grid / 2, grid)
            if len(ts) < 2:
                continue
            if not fwd:
                ts = ts[::-1]
            zs_r = np.maximum(L, [sample(x, c) for x in ts])
            pts = simplify(list(zip(ts, zs_r)), simplify_tol)
            x0, z0 = pts[0][0], pts[0][1]
            if first:
                emit(f"G0 Z{safe_z:.3f}")
                emit(f"G0 X{x0:.3f} Y{c:.3f}")
                emit(f"G1 Z{z0:.3f} F{plunge_feed}")
                emit(f"G1 X{x0:.3f} Y{c:.3f} F{feed}")
                first = False
            else:
                xp, yp, zp = prev_end
                link_z = max(zp, z0)
                steps = max(2, int(np.hypot(x0 - xp, c - yp) / grid) + 1)
                for tt in np.linspace(0, 1, steps):
                    lx, ly = xp + (x0 - xp) * tt, yp + (c - yp) * tt
                    link_z = max(link_z, max(L, sample(lx, ly)))
                if link_z > zp + 1e-6:
                    emit(f"G1 Z{link_z:.3f} F{plunge_feed}")
                emit(f"G1 X{x0:.3f} Y{c:.3f} F{feed}")
                if z0 < link_z - 1e-6:
                    emit(f"G1 Z{z0:.3f} F{plunge_feed}")
            for t, z in pts[1:]:
                emit(f"G1 X{t:.3f} Y{c:.3f} Z{z:.3f} F{feed}")
            prev_end = (pts[-1][0], c, pts[-1][1])
            fwd = not fwd
        # perimeter ring: guarantees the boundary annulus at this layer
        emit(f"G0 Z{safe_z:.3f}")
        thetas = 2 * np.pi * np.arange(ring_seg + 1) / ring_seg
        rx = rb * np.cos(thetas)
        ry = rb * np.sin(thetas)
        rz = [max(L, sample(x, y)) for x, y in zip(rx, ry)]
        emit(f"G0 X{rx[0]:.3f} Y{ry[0]:.3f}")
        emit(f"G1 Z{rz[0]:.3f} F{plunge_feed}")
        for k in range(1, ring_seg + 1):
            emit(f"G1 X{rx[k]:.3f} Y{ry[k]:.3f} Z{rz[k]:.3f} F{feed}")
        emit(f"G0 Z{safe_z:.3f}")
    return lines
