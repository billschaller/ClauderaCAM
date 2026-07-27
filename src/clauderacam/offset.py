"""Tool offset surfaces: the z the tool CENTER may reach with the tip
touching (ball) or the flat bottom clearing (flat) the surface."""
from __future__ import annotations

import numpy as np
from scipy import ndimage


def ball_offset(hmap: np.ndarray, radius: float, grid: float) -> np.ndarray:
    """Tip-referenced ball offset via grey dilation with a sphere-drop
    structure (validated ±0.02mm against pngcam-go)."""
    r_px = int(np.ceil(radius / grid))
    dy, dx = np.mgrid[-r_px:r_px + 1, -r_px:r_px + 1]
    rr = np.hypot(dx, dy) * grid
    inside = rr <= radius + 1e-9
    drop = radius - np.sqrt(np.maximum(radius**2 - rr**2, 0.0))
    struct = np.where(inside, -drop, -1e9)
    return ndimage.grey_dilation(hmap, structure=struct, mode="nearest")


def flat_offset(hmap: np.ndarray, radius: float, grid: float) -> np.ndarray:
    """Flat endmill offset: max over the disc footprint."""
    r_px = int(np.ceil(radius / grid))
    dy, dx = np.mgrid[-r_px:r_px + 1, -r_px:r_px + 1]
    foot = np.hypot(dx, dy) * grid <= radius + 1e-9
    return ndimage.maximum_filter(hmap, footprint=foot, mode="nearest")
