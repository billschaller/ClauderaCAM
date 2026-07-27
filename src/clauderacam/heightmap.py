"""STL loading and heightmap rasterization (barycentric z-buffer).

Requirement 4 (DESIGN.md): ONE coordinate convention, defined here and
imported everywhere. World XY origin is the stock/model center, Z0 is the
stock top. A map of half-extent `half` and cell size `grid` maps world to
pixels via px(v) = (v + half) / grid; row i is +y down in array order for
sampling maps (heightmaps), and analysis rasters must use x = j/ppm - half,
y = half - i/ppm (see simulate.py) — never re-derive centers from (n-1)/2.
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np


def load_stl(path: str | Path) -> np.ndarray:
    """Binary STL -> (n, 3, 3) float64 triangle vertex array."""
    with open(path, "rb") as f:
        f.seek(80)
        n = struct.unpack("<I", f.read(4))[0]
        raw = np.frombuffer(f.read(n * 50), dtype=np.uint8).reshape(n, 50)
    return raw[:, 12:48].copy().view("<f4").reshape(n, 3, 3).astype(np.float64)


def rasterize(tris: np.ndarray, half: float, grid: float) -> np.ndarray:
    """Max-z heightmap of the mesh on a (npx, npx) grid; cells outside the
    mesh get the mesh minimum z (the relief field level)."""
    npx = int(round(2 * half / grid)) + 1
    zmin = tris[:, :, 2].min()
    H = np.full((npx, npx), zmin, np.float64)

    def px(v):
        return (v + half) / grid

    for t in range(len(tris)):
        p = tris[t]
        xs, ys, zs = p[:, 0], p[:, 1], p[:, 2]
        j0 = max(int(np.floor(px(xs.min()))), 0)
        j1 = min(int(np.ceil(px(xs.max()))), npx - 1)
        i0 = max(int(np.floor(px(ys.min()))), 0)
        i1 = min(int(np.ceil(px(ys.max()))), npx - 1)
        if j1 < j0 or i1 < i0:
            continue
        jj, ii = np.meshgrid(np.arange(j0, j1 + 1), np.arange(i0, i1 + 1))
        X = jj * grid - half
        Y = ii * grid - half
        d = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
        if abs(d) < 1e-12:
            continue
        a = ((ys[1] - ys[2]) * (X - xs[2]) + (xs[2] - xs[1]) * (Y - ys[2])) / d
        b = ((ys[2] - ys[0]) * (X - xs[2]) + (xs[0] - xs[2]) * (Y - ys[2])) / d
        c = 1.0 - a - b
        m = (a >= -1e-9) & (b >= -1e-9) & (c >= -1e-9)
        if not m.any():
            continue
        Z = a * zs[0] + b * zs[1] + c * zs[2]
        sub = H[i0:i1 + 1, j0:j1 + 1]
        np.maximum(sub, np.where(m, Z, -1e9), out=sub)
    return H


def simplify(pts: list, tol: float) -> list:
    """Slope-corridor 1D simplification of (t, z) samples along a line."""
    if len(pts) <= 2:
        return pts
    out = [pts[0]]
    anchor = 0
    for k in range(2, len(pts)):
        t0, z0 = pts[anchor]
        tk, zk = pts[k]
        ok = True
        for m in range(anchor + 1, k):
            tm, zm = pts[m]
            zi = z0 + (zk - z0) * (tm - t0) / (tk - t0)
            if abs(zi - zm) > tol:
                ok = False
                break
        if not ok:
            out.append(pts[k - 1])
            anchor = k - 1
    out.append(pts[-1])
    return out
