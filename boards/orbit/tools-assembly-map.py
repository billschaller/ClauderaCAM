#!/usr/bin/env python3
"""Orbit assembly map — the coupon's print edition, for the flip board.

Board A's lesson (ecf0077): a dark render prints as a toner slab on the
mono laser. Maps are LINE ART ON WHITE: pad outlines with open holes,
halo'd bold refs per ACTING side, orientation marks only where they
matter. Orbit adds the flip's own truths: TWO panels (the front as you
look at it; the back AS SEEN FROM THE BACK, mirrored — hold the board
flipped and the panel matches), and the 24 stitch joints drawn as FILLED
DIAMONDS with their ledger names, because every one is a bench joint on
BOTH faces.

Geometry comes from tools-board's own model (build_parts — the emitter's
single placement table) and the MATRIX via ledger; nothing is re-derived.

    python3 tools-assembly-map.py   ->  assembly-map.png  (letter, 300dpi)
"""
from __future__ import annotations

import importlib.util
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

from PIL import Image, ImageDraw, ImageFont  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, HERE)
    spec.loader.exec_module(mod)
    return mod


TB = _load("tools_board", os.path.join(HERE, "tools-board.py"))

BW, BH = TB.BOARD_W, TB.BOARD_H
ISP_NAMES = {"TP1": "MISO", "TP2": "VCC", "TP3": "SCK",
             "TP4": "MOSI", "TP5": "RST", "TP6": "GND"}


def vias():
    txt = (Path(HERE) / "MATRIX.md").read_text()
    return [(f"V{n}", net, float(x), BH - float(y)) for n, x, y, net in
            re.findall(r"`V(\d+)\s*\(\s*([\d.]+),\s*([\d.]+)\)\s+(\S+)",
                       txt)]


PPM = 16                       # px/mm per panel
MARGIN = 70
_DEJA = "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf"
try:
    FW = ImageFont.truetype(_DEJA % "-Bold", 30)
    FS = ImageFont.truetype(_DEJA % "", 21)
    FT = ImageFont.truetype(_DEJA % "-Bold", 44)
    FI = ImageFont.truetype(_DEJA % "-Bold", 17)
except OSError:
    FW, FS, FT = (ImageFont.load_default(z) for z in (28, 20, 40))
    FI = ImageFont.load_default(16)


def halo_text(d, xy, s, font=FW, fill=(0, 0, 0)):
    x, y = xy
    for dx in (-3, 0, 3):
        for dy in (-3, 0, 3):
            d.text((x + dx, y + dy), s, font=font, fill=(255, 255, 255))
    d.text((x, y), s, font=font, fill=fill)


def panel(side_view_back: bool, parts, vlist):
    """One panel, line art. side_view_back: mirror x (as seen from BACK)."""
    W, H = int(BW * PPM) + 2 * MARGIN, int(BH * PPM) + 2 * MARGIN
    im = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(im)

    def P(x, y):
        if side_view_back:
            x = BW - x
        return (MARGIN + x * PPM, H - MARGIN - y * PPM)

    def rect(a, b, **kw):
        (x0, y0), (x1, y1) = a, b
        d.rectangle([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)],
                    **kw)

    def circle(x, y, dia, width=3, fill=None, outline=(0, 0, 0)):
        cx, cy = P(x, y)
        r = dia / 2 * PPM
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=outline,
                  width=width, fill=fill)

    rect(P(0, BH), P(BW, 0), outline=(0, 0, 0), width=4)
    for ref, (mx, my) in TB.MOUNTS.items():
        circle(mx, my, TB.HOLE_MOUNT, width=3)
        circle(mx, my, TB.HOLE_MOUNT + 1.6, width=2)
    for ref, (gx, gy) in TB.GAUGES.items():
        circle(gx, gy, TB.RING_GAUGE, width=2)
        circle(gx, gy, TB.HOLE_GAUGE, width=2)
        px, py = P(gx, gy)
        halo_text(d, (px + 16, py - 34), ref, FS)

    acting = "back" if side_view_back else "front"
    for part in parts:
        tht = part.pins[0].kind == "tht"
        # pads draw on BOTH panels (a through hole is a joint target on the
        # back and a body seat on the front; SMD/bare pads are back-only)
        for p in part.pins:
            if p.kind == "tht":
                circle(p.x, p.y, p.shape[2], width=3)
                circle(p.x, p.y, p.shape[1], width=2)
            elif side_view_back:
                if p.kind == "circ":
                    circle(p.x, p.y, p.shape[1], width=3)
                else:
                    d.polygon([P(cx, cy) for cx, cy in p.corners()],
                              outline=(0, 0, 0), width=3)
        act = (part.on_bottom and side_view_back) or \
              (tht and not part.on_bottom and not side_view_back)
        if not act:
            continue
        # body + halo'd ref on the ACTING panel only
        xs = [p.x for p in part.pins]
        ys = [p.y for p in part.pins]
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        if part.ref.startswith("LED"):
            circle(cx, cy, 5.0, width=2)
            # cathode tick: the INWARD lead (the silk convention) — a bold
            # bar just inside it, pointing at the ring centre
            kpin = min(part.pins, key=lambda p: (p.x - TB.RING_CX) ** 2
                       + (p.y - TB.RING_CY) ** 2)
            import math
            ang = math.atan2(kpin.y - TB.RING_CY, kpin.x - TB.RING_CX)
            tx = kpin.x - 1.6 * math.cos(ang)
            ty = kpin.y - 1.6 * math.sin(ang)
            a, b = P(tx + 0.9 * math.sin(ang), ty - 0.9 * math.cos(ang)), \
                P(tx - 0.9 * math.sin(ang), ty + 0.9 * math.cos(ang))
            d.line([a, b], fill=(0, 0, 0), width=6)
        elif len(part.pins) > 1:
            hw = max(abs(p.x - cx) for p in part.pins) + 1.2
            hh = max(abs(p.y - cy) for p in part.pins) + 1.2
            rect(P(cx - hw, cy + hh), P(cx + hw, cy - hh),
                 outline=(0, 0, 0), width=2)
        if part.ref == "U1":                      # pin 1 dot
            p1 = part.pins[0]
            circle(p1.x, p1.y, 0.9, fill=(0, 0, 0))
        if part.ref == "BZ1":                     # pin 1 double ring
            p1 = part.pins[0]
            circle(p1.x, p1.y, p1.shape[2] + 1.0, width=2)
        if part.ref in ISP_NAMES:
            p0 = part.pins[0]
            px, py = P(p0.x, p0.y)
            # single DIGITS: the function words collided with the V9/V15
            # diamonds (true positions), and the legend already carries
            # the order. TP1..TP6 = MISO VCC SCK MOSI RST GND.
            lbl = part.ref[2]
            if p0.x > 45.2:
                halo_text(d, (px - 30, py - 12), lbl, FS)
            else:
                halo_text(d, (px + 16, py - 12), lbl, FS)
            continue
        px, py = P(cx, cy)
        label = part.ref
        if part.ref == "U1":
            px -= 3.2 * PPM          # clear of V16's diamond on the lands
        if part.ref == "LED7":
            px += 2.4 * PPM          # clear of V8's diamond next door
        if part.ref.startswith("LED"):
            import math
            ang = math.atan2(cy - TB.RING_CY, cx - TB.RING_CX)
            px += 3.6 * PPM * math.cos(ang) * (-1 if side_view_back else 1)
            py -= 3.6 * PPM * math.sin(ang)
        halo_text(d, (px - 9 * len(label), py - 16), label)

    # PAD2-1 is the 24th both-faces joint: outline diamond around it
    for part in parts:
        if part.ref == "PAD2":
            p0 = part.pins[0]
            cx, cy = P(p0.x, p0.y)
            r = 2.6 * PPM
            d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r),
                       (cx - r, cy)], outline=(0, 0, 0), width=4)

    # the stitch joints: filled diamonds + names, on BOTH panels
    for name, net, x, y in vlist:
        cx, cy = P(x, y)
        r = 1.25 * PPM
        d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)],
                  fill=(0, 0, 0))
        hx = 0.5 * PPM
        d.polygon([(cx, cy - hx), (cx + hx, cy), (cx, cy + hx),
                   (cx - hx, cy)], fill=(255, 255, 255))
        # near the ISP cluster the right side is taken by function labels:
        # those vias label LEFT of their diamond
        if side_view_back and (BW - x) < 20 and y < 13:
            halo_text(d, (cx - r - 2 - 12 * len(name), cy - 12), name, FS)
        else:
            halo_text(d, (cx + r + 2, cy - 12), name, FS)

    title = ("BACK — seen from the back (reflow side; every joint "
             "lands here)" if side_view_back
             else "FRONT — component bodies seat here")
    halo_text(d, (MARGIN, 8), title, FW)
    return im


def main() -> int:
    parts = TB.build_parts()
    vlist = vias()
    fr = panel(False, parts, vlist)
    bk = panel(True, parts, vlist)
    gap = 60
    leg_h = 240
    W = fr.width + bk.width + gap
    H = max(fr.height, bk.height) + leg_h + 80
    sheet = Image.new("RGB", (W, H), (255, 255, 255))
    sheet.paste(fr, (0, 70))
    sheet.paste(bk, (fr.width + gap, 70))
    d = ImageDraw.Draw(sheet)
    halo_text(d, (10, 6), "ORBIT V1 assembly map — 2026-08-05", FT)
    y0 = max(fr.height, bk.height) + 90
    r = 20
    d.polygon([(30, y0 + 10 - r), (30 + r, y0 + 10), (30, y0 + 10 + r),
               (30 - r, y0 + 10)], fill=(0, 0, 0))
    legend = [
        "diamond = WIRE VIA: thread bare wire, solder BOTH faces, clip "
        "flush - 23 vias, names from MATRIX.md; PAD2-1 is the 24th "
        "both-faces joint (big pad, bottom left)",
        "double circle with open centre = through hole pad (solder on the "
        "BACK only; bodies seat FLUSH on the front)",
        "LED bar = CATHODE lead (inward, toward the ring centre) - the "
        "clock ticks 12/3/6/9 on the board silk agree",
        "U1 filled dot = pin 1; BZ1 double ring = pin 1; ISP pads are "
        "numbered: 1 MISO  2 VCC  3 SCK  4 MOSI  5 RST  6 GND",
        "order: 1 stitch V16 - it is UNDER U1, unreachable later   2 stencil"
        "+reflow the BACK   3 stitch the other vias   4 seat THT from the "
        "FRONT, solder on the back   5 PAD wires last",
    ]
    for i, ln in enumerate(legend):
        d.text((60, y0 - 10 + i * 42), ln, font=FS, fill=(0, 0, 0))
    out = Path(HERE) / "assembly-map.png"
    sheet.save(out, dpi=(300, 300))
    print(f"{out.name}: {sheet.width}x{sheet.height} "
          f"({len(parts)} parts, {len(vlist)} vias + PAD2-1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
