"""Hillshade preview rendered from the SIMULATED STOCK, never the target
model (requirement 7) — it shows what the machine will actually produce."""
from __future__ import annotations

import numpy as np
from PIL import Image

from .job import Job
from .simulate import CarveResult, carve_fast


def hillshade(stock: np.ndarray, ppm: float) -> np.ndarray:
    gy, gx = np.gradient(stock.astype(np.float64))
    az, alt = np.radians(315), np.radians(45)
    slope = np.arctan(np.hypot(gx, gy) * ppm * 1.5)
    aspect = np.arctan2(-gx, gy)
    shade = np.sin(alt) * np.cos(slope) \
        + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    shade = np.clip((shade + 1) / 2, 0, 1)
    return (shade * 255).astype(np.uint8)


def render(job: Job, nc_path=None, out_path=None,
           carve: CarveResult | None = None) -> str:
    nc_path = nc_path or job.out
    res = carve or carve_fast(nc_path, job)
    img = hillshade(res.stock, res.ppm)
    out_path = out_path or job.out.with_name(job.out.stem + "-preview.png")
    Image.fromarray(img, mode="L").save(out_path)
    return str(out_path)
