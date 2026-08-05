#!/usr/bin/env python3
"""ONE-OFF rescue scrub — 2026-08-05 bench incident.

Mid-run, the blank bowed after milling: the scrub's -0.21 spring preload
cleaned the OUTER pads but left the middle ones green (the low spots got
less preload), and two full re-runs did not close the gap. Re-probing is
illegal at this point in the chain (mask + legend are on the surface —
the run sheet's own law), so the fix is the same laps, deeper preload,
ONLY where needed.

This tool re-emits laps FROM THE SHIPPED PROGRAM, byte-verbatim in XY:
it parses out/orbit-<side>-scrub.nc, selects the laps whose centroid
falls in a region you control, swaps ONLY the plunge depth, and
reassembles through emit.assemble (Article V — same parser, same lint).
No geometry is regenerated, so a rescued lap lands exactly where its
parent did.

    python3 tools-rescue-scrub.py                        # back, r=20, -0.23
    python3 tools-rescue-scrub.py --side front           # the 24-lap scrub
    python3 tools-rescue-scrub.py --radius 24            # wider middle
    python3 tools-rescue-scrub.py --box 20,12,46,44      # explicit window
    python3 tools-rescue-scrub.py --depth -0.24          # more preload

Outputs rescue-scrub-<side>.nc + rescue-scrub-<side>.png (the map:
selected laps vs skipped, over the board box, in MACHINE frame — hold it
up against the physical board before cutting). Every run overwrites both.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_VENV = os.path.join(os.path.dirname(os.path.dirname(HERE)), ".venv")
if (os.path.isfile(os.path.join(_VENV, "bin", "python"))
        and sys.prefix != _VENV):
    _py = os.path.join(_VENV, "bin", "python")
    os.execv(_py, [_py, os.path.abspath(__file__), *sys.argv[1:]])

from pathlib import Path  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)),
                                "src"))
from clauderacam import emit  # noqa: E402
from clauderacam.engine import OpResult, path_length  # noqa: E402
from clauderacam.pcb import pcbjob  # noqa: E402
from clauderacam.pcb.pcbjob import SCRUB_Z_MAX, SCRUB_Z_MIN  # noqa: E402

_XY = re.compile(r"X([-\d.]+)\s*Y([-\d.]+)")
_PLUNGE = re.compile(r"^G0?1\s+Z(-[\d.]+)\s+F([\d.]+)\s*$")


def parse_laps(text: str) -> list[dict]:
    """The scrub program's laps: each = rapid-to-start, plunge, cut lines.
    Lines are kept VERBATIM — the rescue must land exactly on the parent."""
    laps, cur = [], None
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("(") or not s:
            continue
        if re.match(r"^G0?0\s+Z", s):            # retract closes a lap
            if cur and cur["cuts"]:
                laps.append(cur)
            cur = None
            continue
        if re.match(r"^G0?0\s+X", s):
            cur = {"start": s, "plunge": None, "cuts": []}
            continue
        m = _PLUNGE.match(s)
        if m and cur is not None and cur["plunge"] is None:
            cur["plunge"] = (float(m.group(1)), float(m.group(2)))
            continue
        if re.match(r"^G0?1\s+X", s) and cur is not None and cur["plunge"]:
            cur["cuts"].append(s)
    if cur and cur["cuts"]:
        laps.append(cur)
    for lap in laps:
        pts = [(float(a), float(b)) for c in lap["cuts"]
               for a, b in _XY.findall(c)]
        lap["cx"] = sum(p[0] for p in pts) / len(pts)
        lap["cy"] = sum(p[1] for p in pts) / len(pts)
    return laps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--side", choices=("back", "front"), default="back")
    ap.add_argument("--depth", type=float, default=-0.23)
    ap.add_argument("--center", default="33,28")
    ap.add_argument("--radius", type=float, default=20.0)
    ap.add_argument("--box", default=None,
                    help="x0,y0,x1,y1 machine frame; overrides center/radius")
    a = ap.parse_args()

    if not (SCRUB_Z_MIN <= a.depth <= SCRUB_Z_MAX):
        raise SystemExit(
            f"depth {a.depth} outside the field-tuned preload band "
            f"[{SCRUB_Z_MIN}, {SCRUB_Z_MAX}] — {SCRUB_Z_MIN} peeled traces "
            f"off a bowed blank once already; this rescue exists BECAUSE "
            f"of a bow")

    src = Path(HERE) / "out" / f"orbit-{a.side}-scrub.nc"
    if not src.is_file():
        raise SystemExit(f"no {src} — generate the board's programs first")
    laps = parse_laps(src.read_text())

    if a.box:
        x0, y0, x1, y1 = (float(v) for v in a.box.split(","))
        sel = [(x0 <= L["cx"] <= x1 and y0 <= L["cy"] <= y1) for L in laps]
        where = f"box {a.box}"
    else:
        cx, cy = (float(v) for v in a.center.split(","))
        sel = [((L["cx"] - cx) ** 2 + (L["cy"] - cy) ** 2)
               <= a.radius ** 2 for L in laps]
        where = f"r<={a.radius:g} of {cx:g},{cy:g}"   # no parens: a nested
        #                                   paren in a G-code comment is
        #                                   unparseable (the styled-tab law)
    picked = [L for L, s in zip(laps, sel) if s]
    if not picked:
        raise SystemExit(f"selection {where} picks 0 of {len(laps)} laps")

    job = pcbjob.load(Path(HERE) / "orbit.toml")
    sj = pcbjob.side_view(job, a.side)
    p = sj.phases["scrub"]
    lines: list[str] = []
    for L in picked:
        lines += ["G0 Z2.0000", L["start"],
                  f"G1 Z{a.depth:.4f} F{L['plunge'][1]:g}"]
        lines += L["cuts"]
    lines.append("G0 Z2.0000")
    op = OpResult(label="pcb-scrub-rescue", kind="scrub",
                  tool=sj.phases["scrub"]["tool"], lines=lines,
                  path_len_mm=path_length(lines),
                  est_min=path_length(lines) / float(p["feed"]))
    header = [
        f"(ONE-OFF rescue scrub [{a.side.upper()}] - the bowed-middle "
        f"incident, 2026-08-05)",
        f"({len(picked)} of {len(laps)} laps, {where}, preload "
        f"{a.depth:g} vs the run's {p['depth']:g})",
        "(XY is byte-verbatim from the shipped scrub program - only the "
        "preload moved)",
        "(same setup, same zero; do NOT re-probe - mask and legend are "
        "on the surface)",
    ]
    text = emit.assemble(sj, [op], header=header)
    probs = emit.lint_program(text.splitlines())
    floor = min(float(m.group(1)) for m in
                (_PLUNGE.match(ln.strip()) for ln in text.splitlines())
                if m)
    src_xy = {ln.strip() for ln in src.read_text().splitlines()}
    stray = [c for L in picked for c in L["cuts"] if c not in src_xy]
    out = Path(HERE) / f"rescue-scrub-{a.side}.nc"
    if probs or stray or abs(floor - a.depth) > 1e-9:
        raise SystemExit(f"REFUSED: lint {probs[:2]} stray {stray[:2]} "
                         f"floor {floor}")
    out.write_text(text)

    from PIL import Image, ImageDraw
    ppm = 12
    im = Image.new("RGB", (int(70 * ppm) + 40, int(60 * ppm) + 40),
                   (24, 28, 34))
    d = ImageDraw.Draw(im)

    def P(x, y):
        return (20 + (x + 2) * ppm, im.height - 20 - (y + 2) * ppm)

    d.rectangle([P(0, 56), P(66, 0)], outline=(90, 100, 110), width=2)
    for L, s in zip(laps, sel):
        col = (80, 220, 120) if s else (200, 60, 60)
        for c in L["cuts"]:
            pts = [(float(x), float(y)) for x, y in _XY.findall(c)]
            for q in pts:
                d.ellipse([P(*q)[0] - 1, P(*q)[1] - 1,
                           P(*q)[0] + 1, P(*q)[1] + 1], fill=col)
    png = Path(HERE) / f"rescue-scrub-{a.side}.png"
    im.save(png)

    print(f"{out.name}: {len(picked)}/{len(laps)} laps at Z{a.depth:g} "
          f"({where})")
    print(f"map: {png.name}  — GREEN = will re-scrub, RED = left alone. "
          f"Hold it against the board (machine frame, {a.side} setup).")
    print("VERDICT: PASS — lint clean, XY verbatim, floor exact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
