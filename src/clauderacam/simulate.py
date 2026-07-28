"""Sequential stock simulation — the physical ground truth for verification
and previews.

Rewritten after the 2026-07-28 adversarial review. Two principles:

1. STRICT PARSING (Article I): the gate refuses G-code it cannot fully model
   instead of skipping it. Unknown tools, arcs (any spelling), G-less modal
   coordinate lines, machine-coordinate moves, and unrecognized words are all
   fatal. The previous parser silently dropped compact arcs ('G2X...'),
   moves under undefined tools, and no-space words — each of which let a
   file that cuts through the fixture keep-out verify PASS.

2. TRUE ENGAGEMENT: contact is measured as the max over the tool footprint
   of (stock − tool cutting surface) BEFORE each sample is carved, for every
   tool. The previous metric read one pixel under the tool center after the
   preceding sample had already carved it, so it reported its own ~0.06mm
   sampling step on plunges and ~0 on buried cuts — it could essentially
   never fail. Measured on the incident class that snapped a 1mm ball, the
   old metric said 0.06mm; this one says the full burial depth.

Coordinate convention (requirement 4): carve maps world->pixel via
i = round((half - y) * ppm), j = round((x + half) * ppm). Any analysis mask
MUST invert with x = j/ppm - half, y = half - i/ppm — never re-derive
centers from (n-1)/2.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from .job import Job

SAFE_Z = 3.0

WORD = re.compile(r"([A-Za-z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")
COMMENT = re.compile(r"\([^)]*\)|;.*")
# Modeled or provably motion-free words. Everything else is fatal.
IGNORED_G = {4, 17, 21, 28, 54, 90, 94}   # dwell, XY plane, mm, park, WCS1, abs, feed/min
IGNORED_M = {0, 1, 2, 3, 5, 7, 8, 9, 30}  # stops, spindle, coolant, end


class GcodeError(ValueError):
    """Raised when a file contains anything the simulator cannot fully model.
    Fatal by design: an unmodeled move is an unverified move."""


def parse_line(line: str, lineno: int):
    """-> ('none',) | ('tool', n) | ('move', 0|1, {axis: value})"""
    body = COMMENT.sub(" ", line).strip()
    if not body:
        return ("none",)
    words = WORD.findall(body)
    if WORD.sub("", body).strip():
        raise GcodeError(
            f"line {lineno}: unparseable text in {line.strip()!r} — "
            f"the gate refuses what it cannot model")
    motion = None
    coords: dict[str, float] = {}
    m6 = False
    tsel = None
    for L, V in words:
        L = L.upper()
        v = float(V)
        if L == "G":
            g = int(round(v))
            if abs(v - g) > 1e-9:
                raise GcodeError(f"line {lineno}: non-integer word G{V}")
            if g in (0, 1):
                if motion is not None and motion != g:
                    raise GcodeError(f"line {lineno}: conflicting motion words")
                motion = g
            elif g in IGNORED_G:
                pass
            else:
                raise GcodeError(
                    f"line {lineno}: unsupported G{g} in {line.strip()!r} — "
                    f"arcs, cutter comp and machine-coordinate moves cannot "
                    f"be simulated")
        elif L == "M":
            m = int(round(v))
            if m == 6:
                m6 = True
            elif m not in IGNORED_M:
                raise GcodeError(f"line {lineno}: unsupported M{m}")
        elif L == "T":
            tsel = int(round(v))
        elif L in "XYZ":
            coords[L] = v
        elif L in "FSP":
            pass
        else:
            raise GcodeError(
                f"line {lineno}: unsupported word {L}{V} "
                f"(I/J/K/R arc parameters etc. cannot be simulated)")
    if m6:
        if tsel is None:
            raise GcodeError(f"line {lineno}: M6 without a T word")
        if motion is not None or coords:
            raise GcodeError(f"line {lineno}: motion mixed with tool change")
        return ("tool", tsel)
    if coords and motion is None:
        raise GcodeError(
            f"line {lineno}: coordinates without G0/G1 in {line.strip()!r} — "
            f"modal G-less lines are rejected by the Carvera and by this gate")
    if motion is not None:
        return ("move", motion, coords)
    return ("none",)


def kit(dia_mm: float, ball: bool, ppm: float):
    r_px = int(np.ceil(dia_mm / 2 * ppm))
    dy, dx = np.mgrid[-r_px:r_px + 1, -r_px:r_px + 1]
    rr = np.hypot(dx, dy) / ppm
    f = rr <= dia_mm / 2 + 1e-9
    R = dia_mm / 2
    drop = (R - np.sqrt(np.maximum(R * R - rr * rr, 0))) if ball \
        else np.zeros_like(rr)
    return r_px, f, drop.astype(np.float32)


@dataclass
class Contact:
    max: float = 0.0
    at: tuple | None = None
    samples: int = 0


@dataclass
class CarveResult:
    stock: np.ndarray
    ppm: float
    half: float
    worst_rapid: float = 0.0
    rapid_at: tuple | None = None
    contact: dict[int, Contact] = field(default_factory=dict)  # ALL tools
    min_cut_z: float = 0.0


def carve(nc_path, job: Job, *, ppm: float = 12.5, extra_half: float = 3.0,
          step: float = 0.06, check: bool = True) -> CarveResult:
    half = job.stock_half + extra_half
    n = int(half * 2 * ppm)
    stock = np.zeros((n, n), np.float32)
    kits = {t.num: kit(t.diameter, t.type == "ball", ppm)
            for t in job.tools.values()}
    res = CarveResult(stock, ppm, half,
                      contact={t: Contact() for t in kits})

    cur = {"X": None, "Y": None, "Z": None}
    tool = None
    for lineno, line in enumerate(open(nc_path), 1):
        parsed = parse_line(line, lineno)
        if parsed[0] == "tool":
            t = parsed[1]
            if t not in kits:
                raise GcodeError(
                    f"line {lineno}: M6 T{t} but tool {t} is not defined in "
                    f"the job — refusing to simulate moves blind")
            tool = t
            continue
        if parsed[0] != "move":
            continue
        motion, coords = parsed[1], parsed[2]
        if tool is None:
            raise GcodeError(
                f"line {lineno}: motion before any tool change — cannot "
                f"attribute the move to a tool")
        prev = dict(cur)
        cur.update(coords)
        unknown = None in prev.values() or None in cur.values()
        if motion == 1 and unknown:
            raise GcodeError(
                f"line {lineno}: cutting move before XYZ position is fully "
                f"established")
        if unknown:
            continue  # G0 accumulating the initial position
        r_px, foot, drop = kits[tool]
        rapid = motion == 0
        lateral = (cur["X"] != prev["X"]) or (cur["Y"] != prev["Y"])
        dz = cur["Z"] - prev["Z"]
        if rapid and not lateral and dz >= -1e-9:
            continue  # pure vertical lift off the just-cut position: safe
        L = max(np.hypot(cur["X"] - prev["X"], cur["Y"] - prev["Y"]), abs(dz))
        snap = bi0 = bj0 = None
        if check and not rapid:
            # contact is measured against the stock AS OF MOVE START: a
            # plunge carves its own footprint 0.06mm at a time, so per-sample
            # pre-carve measurement still only sees the sampling step — the
            # physical quantity is how much material THIS move's pass removes
            ii = [int(round((half - p["Y"]) * ppm)) for p in (prev, cur)]
            jj = [int(round((p["X"] + half) * ppm)) for p in (prev, cur)]
            bi0 = max(0, min(ii) - r_px - 2)
            bi1 = min(n, max(ii) + r_px + 3)
            bj0 = max(0, min(jj) - r_px - 2)
            bj1 = min(n, max(jj) + r_px + 3)
            snap = stock[bi0:bi1, bj0:bj1].copy()
        for t_ in np.linspace(0, 1, max(2, int(L / step) + 1)):
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
            if not f.any():
                continue
            if rapid:
                # lateral rapids at safe height are exempt; lateral rapids
                # below it and ALL descending rapids are checked
                if check and (not lateral or z < SAFE_Z - 0.1):
                    v = float(region[f].max() - z)
                    if v > res.worst_rapid:
                        res.worst_rapid = v
                        res.rapid_at = (float(round(x, 2)), float(round(y, 2)),
                                        float(round(z, 2)))
            else:
                if check:
                    # TRUE engagement: stock above the tool's cutting surface
                    # anywhere in the footprint, vs the move-start snapshot
                    sregion = snap[i0 - bi0:i1b - bi0, j0 - bj0:j1b - bj0]
                    over = float((sregion[f] - (z + d[f])).max())
                    if over > 1e-6:
                        rec = res.contact[tool]
                        rec.samples += 1
                        if over > rec.max:
                            rec.max = over
                            rec.at = (float(round(x, 1)), float(round(y, 1)))
                if z < res.min_cut_z:
                    res.min_cut_z = float(z)
                region[f] = np.minimum(region[f], z + d[f])
    return res


def carve_check(nc_path, job: Job, *, ppm: float = 12.5,
                extra_half: float = 3.0) -> CarveResult:
    return carve(nc_path, job, ppm=ppm, extra_half=extra_half,
                 step=0.06, check=True)


def carve_fast(nc_path, job: Job, *, ppm: float = 20.0,
               extra_half: float = 1.0) -> CarveResult:
    """Higher-resolution carve for previews. Same strict parser — the
    preview must refuse what the gate refuses (Article VI)."""
    return carve(nc_path, job, ppm=ppm, extra_half=extra_half,
                 step=0.04, check=False)
