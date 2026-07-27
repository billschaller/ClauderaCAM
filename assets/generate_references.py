#!/usr/bin/env python3
"""Generate the reference STL set into assets/generated/.

Each shape is a deterministic analytic heightfield designed to stress a
specific hazard the verifier guards (matching job in jobs/):

  terraces     ten 0.22mm steps + spiral cutout: layered-rough terracing,
               simplify on flats, ball engagement on step walls (~0.4mm)
  dome         smooth gaussian: 2mm flat rough + two-ball chain, no cutout
  ripple       gentle cosine rings: 1mm ball finishing DIRECTLY after rough —
               only safe because every slope is inside the engagement budget
  pocketfield  graduated pockets/slots: which bit reaches which feature —
               big pocket (rough enters), 2.2mm pocket (2mm tools), 1.8mm
               pocket and 1.3mm slots (1mm ball only), all depth-limited so
               the first ball into each feature stays under 0.5mm engagement

The governing envelope (from the mango job's measured numbers): the first
ball pass after a flat rough engages ~ slope*rough_radius + allowance, and
~ wall_height + allowance at a step. Shapes here are constructed inside that
budget; if you steepen them, the verifier will fail the job — that is the
point of the suite.

Convention: model XY-centered, top below -skim, mesh minimum == job floor_z.
Run: .venv/bin/python assets/generate_references.py
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

OUT = Path(__file__).parent / "generated"
STEP = 0.2  # mesh grid, mm — coarser than the 0.05 CAM raster, fine enough


def save_stl(tris: np.ndarray, path: Path) -> None:
    n = len(tris)
    e1 = tris[:, 1] - tris[:, 0]
    e2 = tris[:, 2] - tris[:, 0]
    nrm = np.cross(e1, e2)
    ln = np.linalg.norm(nrm, axis=1, keepdims=True)
    nrm = np.where(ln > 1e-12, nrm / ln, 0)
    rec = np.zeros((n,), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)),
                                ("attr", "<u2")])
    rec["n"] = nrm
    rec["v"] = tris
    with open(path, "wb") as f:
        f.write(b"clauderacam reference shape".ljust(80, b" "))
        f.write(struct.pack("<I", n))
        f.write(rec.tobytes())


def disc_mesh(zf, R: float, floor: float) -> np.ndarray:
    xs = np.arange(-R, R + STEP / 2, STEP)
    X, Y = np.meshgrid(xs, xs)
    rr = np.hypot(X, Y)
    Z = np.clip(zf(X, Y, rr), floor, None)
    inside = rr <= R + 1e-9
    tris = []
    for i in range(len(xs) - 1):
        for j in range(len(xs) - 1):
            if (inside[i, j] and inside[i, j + 1]
                    and inside[i + 1, j] and inside[i + 1, j + 1]):
                a = (X[i, j], Y[i, j], Z[i, j])
                b = (X[i, j + 1], Y[i, j + 1], Z[i, j + 1])
                c = (X[i + 1, j], Y[i + 1, j], Z[i + 1, j])
                d = (X[i + 1, j + 1], Y[i + 1, j + 1], Z[i + 1, j + 1])
                tris.append([a, c, b])
                tris.append([b, c, d])
    return np.asarray(tris, float)


def terraces(X, Y, rr):
    # ten 0.22mm steps from -0.15 to -2.13, then a gentle ramp to the floor
    stepped = -0.15 - 0.22 * np.floor(np.clip(rr / 1.35, 0, 9.999))
    ramp = -2.13 - 0.22 * (rr - 13.5) / 1.5
    return np.where(rr < 13.5, stepped, ramp)


def dome(X, Y, rr):
    # normalized gaussian: exactly -0.15 at center, exactly floor at R=12
    e_edge = np.exp(-(12.0 / 5.6) ** 2)
    g = (np.exp(-(rr / 5.6) ** 2) - e_edge) / (1 - e_edge)
    return -1.95 + 1.8 * g


def ripple(X, Y, rr):
    prof = -1.0 + 0.2 * np.cos(2 * np.pi * rr / 14.0)
    w = np.clip((17.0 - rr) / 6.0, 0, 1)
    return -1.35 + (prof + 1.35) * w


def pocketfield(X, Y, rr):
    z = np.full_like(rr, -0.2)                          # plate top
    z = np.where(np.hypot(X + 6, Y) < 3.5, -0.45, z)    # big: rough enters
    z = np.where(np.hypot(X - 6, Y - 4) < 1.1, -0.45, z)  # 2.2mm: 2mm tools
    z = np.where(np.hypot(X - 6, Y + 5) < 0.9, -0.42, z)  # 1.8mm: 1mm ball only
    z = np.where((np.abs(Y + 10) < 0.65) & (np.abs(X) < 6), -0.48, z)  # 1.3 slot
    z = np.where((np.abs(Y - 9.5) < 0.75) & (np.abs(X) < 5), -0.45, z)  # 1.5 slot
    w = np.clip((16.0 - rr) / 2.5, 0, 1)
    return -0.7 + (z + 0.7) * w


SHAPES = {
    "terraces": (terraces, 15.0, -2.35),
    "dome": (dome, 12.0, -1.95),
    "ripple": (ripple, 17.0, -1.35),
    "pocketfield": (pocketfield, 16.0, -0.7),
}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name, (zf, R, floor) in SHAPES.items():
        tris = disc_mesh(zf, R, floor)
        path = OUT / f"{name}.stl"
        save_stl(tris, path)
        z = tris[:, :, 2]
        print(f"{name}: {len(tris)} tris, r<={R}, z {z.min():.3f}..{z.max():.3f}"
              f" -> {path.name}")


if __name__ == "__main__":
    main()
