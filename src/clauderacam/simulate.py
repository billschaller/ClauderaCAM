"""Sequential stock simulation — the physical ground truth for verification
and previews. Ported from mango-brass/verify_nc.py (checking carve) and the
proven hillshade preview kernel (fast carve).

Coordinate convention (requirement 4): carve maps world->pixel via
i = round((half - y) * ppm), j = round((x + half) * ppm). Any analysis mask
MUST invert with x = j/ppm - half, y = half - i/ppm — a mask that re-derived
its center from (n-1)/2 was 0.075mm off and produced a false FAIL.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from .job import Job

SAFE_Z = 3.0


def kit(dia_mm: float, ball: bool, ppm: float):
    r_px = int(np.ceil(dia_mm / 2 * ppm))
    dy, dx = np.mgrid[-r_px:r_px + 1, -r_px:r_px + 1]
    rr = np.hypot(dx, dy) / ppm
    f = rr <= dia_mm / 2 + 1e-9
    R = dia_mm / 2
    drop = (R - np.sqrt(np.maximum(R * R - rr * rr, 0))) if ball \
        else np.zeros_like(rr)
    return r_px, f, drop.astype(np.float32)


TOOLCHANGE = [re.compile(r"T(\d+)\s*M0?6\b"), re.compile(r"M0?6\s*T(\d+)")]
ARC = re.compile(r"G0?[23]\b")
MOVE = re.compile(r"G0?([01])\b")
AXIS = {a: re.compile(rf"{a}(-?\d+\.?\d*)") for a in "XYZ"}


def parse_toolchange(line: str) -> int | None:
    for rx in TOOLCHANGE:
        m = rx.search(line)
        if m:
            return int(m.group(1))
    return None


@dataclass
class CarveResult:
    stock: np.ndarray
    ppm: float
    half: float
    worst_rapid: float = 0.0
    rapid_at: tuple | None = None
    engagement: dict = field(default_factory=dict)  # tool -> [max, at, count]


def carve_check(nc_path, job: Job, *, ppm: float = 12.5,
                extra_half: float = 3.0) -> CarveResult:
    """Carve every move, checking lateral rapids against remaining stock and
    ball-tool engagement (stock above tip on every cutting sample)."""
    half = job.stock_half + extra_half
    n = int(half * 2 * ppm)
    stock = np.zeros((n, n), np.float32)
    kits = {t.num: kit(t.diameter, t.type == "ball", ppm)
            for t in job.tools.values()}
    ball_tools = [t.num for t in job.tools.values() if t.type == "ball"]

    res = CarveResult(stock, ppm, half,
                      engagement={t: [0.0, None, 0] for t in ball_tools})
    cur = {"X": None, "Y": None, "Z": None}
    tool = None
    for lineno, line in enumerate(open(nc_path), 1):
        line = line.strip()
        tc = parse_toolchange(line)
        if tc is not None:
            tool = tc
            continue
        if ARC.match(line):
            raise ValueError(f"arc move at line {lineno}: {line}")
        m0 = MOVE.match(line)
        if not m0 or tool not in kits:
            continue
        prev = dict(cur)
        for a in "XYZ":
            m = AXIS[a].search(line)
            if m:
                cur[a] = float(m.group(1))
        if None in prev.values() or None in cur.values():
            continue
        r_px, foot, drop = kits[tool]
        rapid = m0.group(1) == "0"
        lateral = (cur["X"] != prev["X"]) or (cur["Y"] != prev["Y"])
        L = max(np.hypot(cur["X"] - prev["X"], cur["Y"] - prev["Y"]),
                abs(cur["Z"] - prev["Z"]))
        for t_ in np.linspace(0, 1, max(2, int(L / 0.06) + 1)):
            x = prev["X"] + (cur["X"] - prev["X"]) * t_
            y = prev["Y"] + (cur["Y"] - prev["Y"]) * t_
            z = prev["Z"] + (cur["Z"] - prev["Z"]) * t_
            i, j = int(round((half - y) * ppm)), int(round((x + half) * ppm))
            if not (0 <= i < n and 0 <= j < n):
                continue
            i0, i1b = max(0, i - r_px), min(n, i + r_px + 1)
            j0, j1b = max(0, j - r_px), min(n, j + r_px + 1)
            fs = (slice(i0 - (i - r_px), i1b - (i - r_px)),
                  slice(j0 - (j - r_px), j1b - (j - r_px)))
            f, d = foot[fs], drop[fs]
            region = stock[i0:i1b, j0:j1b]
            if rapid:
                # vertical lifts at the just-cut position cannot collide
                if lateral and z < SAFE_Z - 0.1 and f.any():
                    v = float(region[f].max() - z)
                    if v > res.worst_rapid:
                        res.worst_rapid = v
                        res.rapid_at = (float(round(x, 2)), float(round(y, 2)),
                                        float(round(z, 2)))
            else:
                if tool in res.engagement:
                    e = float(stock[i, j] - z)
                    if e > 0:
                        rec = res.engagement[tool]
                        rec[2] += 1
                        if e > rec[0]:
                            rec[0] = e
                            rec[1] = (float(round(x, 1)), float(round(y, 1)))
                region[f] = np.minimum(region[f], z + d[f])
    return res


def carve_fast(nc_path, job: Job, *, ppm: float = 20.0,
               extra_half: float = 1.0) -> CarveResult:
    """Cut-only carve at higher resolution for previews (rapids skipped)."""
    half = job.stock_half + extra_half
    n = int(half * 2 * ppm)
    stock = np.zeros((n, n), np.float32)
    kits = {t.num: kit(t.diameter, t.type == "ball", ppm)
            for t in job.tools.values()}
    cur = {"X": None, "Y": None, "Z": None}
    tool = None
    for line in open(nc_path):
        tc = parse_toolchange(line)
        if tc is not None:
            tool = tc
            continue
        m0 = MOVE.match(line)
        if not m0 or tool not in kits:
            continue
        prev = dict(cur)
        for a in "XYZ":
            m = AXIS[a].search(line)
            if m:
                cur[a] = float(m.group(1))
        if None in prev.values() or None in cur.values() or m0.group(1) == "0":
            continue
        r_px, foot, drop = kits[tool]
        L = max(np.hypot(cur["X"] - prev["X"], cur["Y"] - prev["Y"]),
                abs(cur["Z"] - prev["Z"]))
        for t_ in np.linspace(0, 1, max(2, int(L / 0.04) + 1)):
            x = prev["X"] + (cur["X"] - prev["X"]) * t_
            y = prev["Y"] + (cur["Y"] - prev["Y"]) * t_
            z = prev["Z"] + (cur["Z"] - prev["Z"]) * t_
            i, j = int(round((half - y) * ppm)), int(round((x + half) * ppm))
            if not (0 <= i < n and 0 <= j < n):
                continue
            i0, i1b = max(0, i - r_px), min(n, i + r_px + 1)
            j0, j1b = max(0, j - r_px), min(n, j + r_px + 1)
            fs = (slice(i0 - (i - r_px), i1b - (i - r_px)),
                  slice(j0 - (j - r_px), j1b - (j - r_px)))
            f, d = foot[fs], drop[fs]
            region = stock[i0:i1b, j0:j1b]
            region[f] = np.minimum(region[f], z + d[f])
    return CarveResult(stock, ppm, half)
