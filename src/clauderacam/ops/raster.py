"""Ball-nose serpentine raster (semi-finish / finish passes).

Faithful port of mango-brass/make_ball_passes.py raster() — the generator
behind both cut coins. Serpentine rows inside a circular boundary, z from the
ball offset surface, slope-corridor simplification, and links that climb to
the max surface height along the step-over move (the link IS a cutting move).
"""
from __future__ import annotations

import numpy as np

from ..heightmap import simplify


def generate(zoff: np.ndarray, *, half: float, grid: float, bound: float,
             stepover: float, zextra: float, axis: str,
             feed: float, plunge_feed: float,
             safe_z: float = 3.0, simplify_tol: float = 0.005) -> list[str]:
    npx = zoff.shape[0]

    def sample(x, y):
        j = np.clip((x + half) / grid, 0, npx - 1)
        i = np.clip((y + half) / grid, 0, npx - 1)
        return zoff[int(round(i)), int(round(j))]

    lines: list[str] = []
    emit = lines.append
    n_rows = int(np.floor(2 * bound / stepover)) + 1
    coords = -bound + np.arange(n_rows) * stepover
    first = True
    prev_end = None
    fwd = True
    for c in coords:
        span = bound**2 - c**2
        if span <= 0:
            continue
        s = np.sqrt(span)
        ts = np.arange(-s, s + grid / 2, grid)
        if len(ts) < 2:
            continue
        if not fwd:
            ts = ts[::-1]
        if axis == "x":
            xs_r, ys_r = ts, np.full_like(ts, c)
        else:
            xs_r, ys_r = np.full_like(ts, c), ts
        zs_r = np.array([sample(x, y) for x, y in zip(xs_r, ys_r)]) + zextra
        pts = simplify(list(zip(ts, zs_r)), simplify_tol)
        x0, y0 = (pts[0][0], c) if axis == "x" else (c, pts[0][0])
        z0 = pts[0][1]
        if first:
            emit(f"G0 Z{safe_z:.3f}")
            emit(f"G0 X{x0:.3f} Y{y0:.3f}")
            emit(f"G1 Z{z0:.3f} F{plunge_feed:.0f}")
            emit(f"G1 X{x0:.3f} Y{y0:.3f} F{feed:.0f}")
            first = False
        else:
            xp, yp, zp = prev_end
            link_z = max(zp, z0)
            steps = max(2, int(np.hypot(x0 - xp, y0 - yp) / grid) + 1)
            for tt in np.linspace(0, 1, steps):
                lx, ly = xp + (x0 - xp) * tt, yp + (y0 - yp) * tt
                link_z = max(link_z, sample(lx, ly) + zextra)
            if link_z > zp + 1e-6:
                emit(f"G1 Z{link_z:.3f} F{plunge_feed:.0f}")
            emit(f"G1 X{x0:.3f} Y{y0:.3f} F{feed:.0f}")
            if z0 < link_z - 1e-6:
                emit(f"G1 Z{z0:.3f} F{plunge_feed:.0f}")
        for t, z in pts[1:]:
            x, y = (t, c) if axis == "x" else (c, t)
            emit(f"G1 X{x:.3f} Y{y:.3f} Z{z:.3f} F{feed:.0f}")
        tend = pts[-1]
        prev_end = ((tend[0], c, tend[1]) if axis == "x" else (c, tend[0], tend[1]))
        fwd = not fwd
    emit(f"G0 Z{safe_z:.3f}")
    return lines
