"""Pure-Python reference measurement kernel.

Semantics are law here (the Rust kernel in kernel/ must reproduce them —
tests/kernel_parity.py enforces it):

  - pixel mapping: i = round(  (half - y) * ppm), j = round((x + half) * ppm)
    with PYTHON round-half-even semantics
  - a move samples linspace(0, 1, max(2, int(L/step)+1)) inclusive
  - pure vertical rapid LIFTS (dz >= 0, no lateral) are skipped entirely
  - rapid checks: lateral rapids below SAFE_Z-0.1 and ALL descending rapids
    are tested footprint-max-stock vs z
  - cutting contact: max over the tool footprint of stock - (z + drop),
    measured against the MOVE-START snapshot (a plunge carves its own
    footprint incrementally; per-sample pre-carve is not enough).
    Contact uses the INNER footprint (radius - half a pixel): a wall at
    exactly tool-edge radius — every slot's own walls — is a tangential
    kiss with ~zero radial engagement, and whether its boundary pixel
    rounds into the footprint is sub-pixel luck (the twoside reference
    read its slot wall as a 1.6mm "bite" because rc landed on a half
    pixel; the mango golden was one alignment away from the same false
    FAIL). Removal, engagement fraction and rapids keep the full
    footprint.
  - removed volume: sum over footprint of max(0, stock - (z + drop)) * px²,
    against the LIVE stock (actual removal, no double counting)
  - engagement fraction: removing pixels / footprint pixels, max over samples
  - shank clearance: max over the SHANK footprint of stock - (z + flute_len)
  - snap_after: sorted move indices; AFTER each listed move completes
    (including skipped vertical lifts) a copy of the stock is appended to
    "snapshots" — the per-stage previews of the viewer's stage model
  - tool SHAPE (codes shared with the Rust kernel): 0 flat, 1 ball,
    2 vee, 3 scrub. A vee's cutting surface at radius rr sits
    (rr - tip_r) * slope above the tip (slope = cot(half_angle), flat tip
    of radius tip_r). A scrub tool (the spring-loaded mask scraper) has an
    EMPTY footprint: commanded depth is spring preload, not cut depth —
    zero removal, zero contact, no rapid reading, min_cut_z untouched.
    That silence is deliberate (Article IX: the exemption is stated here
    and in physics.py); the scrub program's own checks are containment/
    coverage/exact-depth, not carving physics.
"""
from __future__ import annotations

import numpy as np

SAFE_Z = 3.0

FLAT, BALL, VEE, SCRUB = 0, 1, 2, 3


def _kit(dia_mm: float, shape: int, ppm: float,
         tip_r: float = 0.0, slope: float = 0.0):
    r_px = int(np.ceil(dia_mm / 2 * ppm))
    dy, dx = np.mgrid[-r_px:r_px + 1, -r_px:r_px + 1]
    rr = np.hypot(dx, dy) / ppm
    if shape == SCRUB:
        f = np.zeros_like(rr, dtype=bool)
        return r_px, f, f, np.zeros_like(rr, dtype=np.float32)
    f = rr <= dia_mm / 2 + 1e-9
    f_in = rr <= dia_mm / 2 - 0.5 / ppm + 1e-9   # contact: real penetration
    R = dia_mm / 2
    if shape == BALL:
        drop = R - np.sqrt(np.maximum(R * R - rr * rr, 0))
    elif shape == VEE:
        drop = np.maximum(rr - tip_r, 0.0) * slope
    else:
        drop = np.zeros_like(rr)
    return r_px, f, f_in, drop.astype(np.float32)


def measure(*, n, ppm, half, step, check,
            dia, shape, tip_r, slope, flute_len, shank_d,
            motion, x0, y0, z0, x1, y1, z1, tool_idx, snap_after=None):
    stock = np.zeros((n, n), np.float32)
    ntools = len(dia)
    kits = [_kit(float(dia[t]), int(shape[t]), ppm,
                 float(tip_r[t]), float(slope[t])) for t in range(ntools)]
    skits = [_kit(float(shank_d[t]), FLAT, ppm) for t in range(ntools)]
    px_area = (1.0 / ppm) ** 2

    nm = len(motion)
    volume = np.zeros(nm)
    contact = np.zeros(nm)
    contact_x = np.zeros(nm)
    contact_y = np.zeros(nm)
    contact_samples = np.zeros(nm, dtype=np.uint32)
    efrac = np.zeros(nm)
    shank_over = np.zeros(nm)
    worst_rapid = 0.0
    rapid_x = rapid_y = rapid_z = 0.0
    min_cut_z = 0.0

    snap_after = np.asarray(snap_after, dtype=np.int64) \
        if snap_after is not None else np.empty(0, np.int64)
    snapshots: list[np.ndarray] = []
    sp = 0

    def take_snap(k):
        # AFTER move k, even when the move itself was skipped as a safe lift
        nonlocal sp
        if sp < len(snap_after) and snap_after[sp] == k:
            snapshots.append(stock.copy())
            sp += 1

    for k in range(nm):
        t = int(tool_idx[k])
        r_px, foot, foot_in, drop = kits[t]
        rapid = motion[k] == 0
        lateral = (x1[k] != x0[k]) or (y1[k] != y0[k])
        dz = z1[k] - z0[k]
        if rapid and not lateral and dz >= -1e-9:
            take_snap(k)
            continue  # pure vertical lift off the just-cut position: safe
        L = max(np.hypot(x1[k] - x0[k], y1[k] - y0[k]), abs(dz))
        snap = bi0 = bj0 = None
        if check and not rapid:
            ii = [int(round((half - y0[k]) * ppm)),
                  int(round((half - y1[k]) * ppm))]
            jj = [int(round((x0[k] + half) * ppm)),
                  int(round((x1[k] + half) * ppm))]
            bi0 = max(0, min(ii) - r_px - 2)
            bi1 = min(n, max(ii) + r_px + 3)
            bj0 = max(0, min(jj) - r_px - 2)
            bj1 = min(n, max(jj) + r_px + 3)
            snap = stock[bi0:bi1, bj0:bj1].copy()
        r_pxs, foots, _, _ = skits[t]
        fl = float(flute_len[t])

        for t_ in np.linspace(0, 1, max(2, int(L / step) + 1)):
            x = x0[k] + (x1[k] - x0[k]) * t_
            y = y0[k] + (y1[k] - y0[k]) * t_
            z = z0[k] + (z1[k] - z0[k]) * t_
            i, j = int(round((half - y) * ppm)), int(round((x + half) * ppm))
            if not (0 <= i < n and 0 <= j < n):
                continue
            i0, i1b = max(0, i - r_px), min(n, i + r_px + 1)
            j0, j1b = max(0, j - r_px), min(n, j + r_px + 1)
            fs = (slice(i0 - (i - r_px), i1b - (i - r_px)),
                  slice(j0 - (j - r_px), j1b - (j - r_px)))
            f, fi, d = foot[fs], foot_in[fs], drop[fs]
            region = stock[i0:i1b, j0:j1b]
            if not f.any():
                continue
            if rapid:
                if check and (not lateral or z < SAFE_Z - 0.1):
                    v = float(region[f].max() - z)
                    if v > worst_rapid:
                        worst_rapid = v
                        rapid_x, rapid_y, rapid_z = x, y, z
            else:
                surf = z + d[f]
                live = region[f]
                if check:
                    sregion = snap[i0 - bi0:i1b - bi0, j0 - bj0:j1b - bj0]
                    # contact on the INNER footprint only (see module doc:
                    # tangential wall kisses are not flank engagement)
                    if fi.any():
                        over = float((sregion[fi] - (z + d[fi])).max())
                        if over > 1e-6:
                            contact_samples[k] += 1
                            if over > contact[k]:
                                contact[k] = over
                                contact_x[k], contact_y[k] = x, y
                    removed = live - surf
                    removing = removed > 1e-9
                    volume[k] += float(removed[removing].sum()) * px_area
                    ef = float(removing.sum()) / float(f.sum())
                    if ef > efrac[k]:
                        efrac[k] = ef
                    # shank clearance (live stock; walls persist)
                    si0, si1b = max(0, i - r_pxs), min(n, i + r_pxs + 1)
                    sj0, sj1b = max(0, j - r_pxs), min(n, j + r_pxs + 1)
                    sfs = (slice(si0 - (i - r_pxs), si1b - (i - r_pxs)),
                           slice(sj0 - (j - r_pxs), sj1b - (j - r_pxs)))
                    sf = foots[sfs]
                    if sf.any():
                        so = float(stock[si0:si1b, sj0:sj1b][sf].max()
                                   - (z + fl))
                        if so > shank_over[k]:
                            shank_over[k] = so
                if z < min_cut_z:
                    min_cut_z = float(z)
                region[f] = np.minimum(region[f], surf)
        take_snap(k)
    return {
        "stock": stock, "volume": volume, "contact": contact,
        "snapshots": snapshots,
        "contact_x": contact_x, "contact_y": contact_y,
        "contact_samples": contact_samples, "efrac": efrac,
        "shank_over": shank_over, "worst_rapid": worst_rapid,
        "rapid_x": rapid_x, "rapid_y": rapid_y, "rapid_z": rapid_z,
        "min_cut_z": min_cut_z,
    }
