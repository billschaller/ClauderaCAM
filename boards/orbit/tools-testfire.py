#!/usr/bin/env python3
"""Laser dose ladder on scrap — the S0.04-with-thin-coat pair is a
PREDICTION, not a landed result, and this rig is how it lands (operator
request 2026-08-03; Board A debrief: dose and coat thickness move together,
re-bracket on scrap when either changes).

    python3 tools-testfire.py           # generate + verify testfire/

What comes out (testfire/):

    testfire-clear.nc        ONE spindle program, T3 0.8 corn at Board A's
                             clearing numbers: rasters a 20x24 window down to
                             -0.15 so the ladder can fire on BOTH substrates
                             (copper left, bared fiberglass right), and
                             engraves an ID tick beside every rung so the
                             wiped board still says which line was which.
                             The LONG tick marks the S0.04 target rung.
    testfire-ladder-sNNN.nc  EIGHT laser programs, one dose each — the laser
                             law allows exactly one M3 S per program
                             (emit.lint_laser), so the ladder is one small
                             file per rung, run back to back.  M321 in every
                             file is safe by firmware design: the mode switch
                             is guarded by get_laser_mode() and later calls
                             are no-ops (Carvera_Community_Firmware
                             Laser.cpp:261).
    testfire-preview.png     rendered from the PARSED program bytes, never
                             from intent (Article VI).
    README.md                the bench card: fixture, coat, run order, and
                             the rung->dose map.

Frame: G54 zero at the SW corner of the region you give it on the scrap,
Z0 = copper top.  Everything lives in x 0..46, y 0..26 — a 50x30 offcut
with copper is enough.  Feed is FIXED at the landed F100: this ladder
varies dose alone, one variable at a time.

Verification (Article I): the written bytes are re-linted independently
(emit.lint_program / emit.lint_laser), the clear raster is coverage-checked
on a 0.1mm grid against the window it promises, every cut outside the window
must be a declared tick, the Z floor must be exactly -0.15, and each ladder
file must carry exactly its table dose on the expected two strokes.  Two
negative controls prove the checks can fail (a raster with a dropped row is
convicted by coverage; a dose swap is convicted by the ladder check).
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# same venv re-exec as tools-fab/tools-cam/tools-route: the checks read
# numpy/PIL, which live in the repo venv — one command, one answer.
_VENV = os.path.join(os.path.dirname(os.path.dirname(HERE)), ".venv")
if (os.path.isfile(os.path.join(_VENV, "bin", "python"))
        and sys.prefix != _VENV):
    _py = os.path.join(_VENV, "bin", "python")
    os.execv(_py, [_py, os.path.abspath(__file__), *sys.argv[1:]])

from pathlib import Path  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)),
                                "src"))
import numpy as np  # noqa: E402

from clauderacam import emit  # noqa: E402
from clauderacam.engine import OpResult, path_length  # noqa: E402
from clauderacam.pcb import pcbjob  # noqa: E402

OUT = Path(HERE) / "testfire"

# ---------------------------------------------------------------- the ladder
# Bracket history (Board A, 2026-07-30 debrief): S0.03/F100 field-validated,
# S0.06 cut the board THROUGH a too-thick coat, S0.10 burned.  The going-
# forward prediction is S0.04 + thin coat.  Rungs cluster where the answer
# lives; 0.01 is the deliberate sub-threshold floor; 0.06 is the operator's
# stated ceiling for this test.
DOSES = [0.01, 0.02, 0.03, 0.035, 0.04, 0.045, 0.05, 0.06]
TARGET_DOSE = 0.04            # the prediction — its tick is the long one
FEED = 100.0                  # landed pair feed, FIXED for the whole ladder

# ---------------------------------------------------------------- the frame
# All in mm from G54 zero at the scrap region's SW corner, Z0 = copper top.
COPPER_X = (2.0, 16.0)        # rung strokes over intact copper
TICK_X0 = 17.0                # ID ticks engraved in the copper lane between
TICK_LEN = 2.0                # the zones; the TARGET_DOSE tick is double
TICK_LEN_TARGET = 4.0
WINDOW = (24.0, 1.0, 44.0, 25.0)   # x0,y0,x1,y1 cleared to bare fiberglass
BARE_X = (27.0, 41.0)         # rung strokes over the cleared window (3.0mm
#                               inside its walls)
RUNG_Y0, RUNG_DY = 3.0, 2.75  # bottom rung, spacing; 8 rungs end at y 22.25
FOOTPRINT = (0.0, 0.0, 46.0, 26.0)

CLEAR_DEPTH = -0.15           # Board A clearing verbatim (orbit.toml
CLEAR_FEED = 500.0            # phases.*.clear): 0.8 corn, -0.15, F500,
PLUNGE_FEED = 200.0           # plunge 200, S12000 from the tool entry
STEPOVER = 0.6                # 25% overlap on the 0.8 corn
SAFE_Z = 2.0
TOOL = 3


def rung_y(i: int) -> float:
    return RUNG_Y0 + RUNG_DY * i


def stem(dose: float) -> str:
    return f"testfire-ladder-s{round(dose * 1000):03d}"


# ---------------------------------------------------------------- clear op
def clear_lines() -> list[str]:
    """Serpentine raster over WINDOW + perimeter contour + the ID ticks.
    Link moves stay at depth INSIDE the window (everything there is being
    cleared anyway); every excursion outside it retracts first."""
    x0, y0, x1, y1 = WINDOW
    r = 0.4                                  # tool radius, 0.8 corn
    cx0, cx1 = x0 + r, x1 - r                # tool-centre bounds
    cy0, cy1 = y0 + r, y1 - r
    n = int(np.ceil((cy1 - cy0) / STEPOVER))
    ys = [cy0 + (cy1 - cy0) * i / n for i in range(n + 1)]

    L = [f"G0 Z{SAFE_Z:.3f}",
         f"G0 X{cx0:.3f} Y{ys[0]:.3f}",
         f"G1 Z{CLEAR_DEPTH:.3f} F{PLUNGE_FEED:g}"]
    left = True                              # first row cuts cx0 -> cx1
    for i, y in enumerate(ys):               # serpentine, at depth
        if i:
            L.append(f"G1 X{(cx0 if left else cx1):.3f} Y{y:.3f} "
                     f"F{CLEAR_FEED:g}")
        L.append(f"G1 X{(cx1 if left else cx0):.3f} Y{y:.3f} "
                 f"F{CLEAR_FEED:g}")
        left = not left
    # perimeter contour cleans the raster cusps off the walls
    ex, ey = (cx0, ys[-1]) if left else (cx1, ys[-1])
    L += [f"G1 X{ex:.3f} Y{cy1:.3f} F{CLEAR_FEED:g}",
          f"G1 X{cx0:.3f} Y{cy1:.3f} F{CLEAR_FEED:g}",
          f"G1 X{cx0:.3f} Y{cy0:.3f} F{CLEAR_FEED:g}",
          f"G1 X{cx1:.3f} Y{cy0:.3f} F{CLEAR_FEED:g}",
          f"G1 X{cx1:.3f} Y{cy1:.3f} F{CLEAR_FEED:g}",
          f"G0 Z{SAFE_Z:.3f}"]
    # ID ticks: one scratch per rung in the copper lane, long = TARGET_DOSE
    for i, dose in enumerate(DOSES):
        ln = TICK_LEN_TARGET if dose == TARGET_DOSE else TICK_LEN
        y = rung_y(i)
        L += [f"G0 X{TICK_X0:.3f} Y{y:.3f}",
              f"G1 Z{CLEAR_DEPTH:.3f} F{PLUNGE_FEED:g}",
              f"G1 X{TICK_X0 + ln:.3f} Y{y:.3f} F{CLEAR_FEED:g}",
              f"G0 Z{SAFE_Z:.3f}"]
    return L


def build_clear(job) -> str:
    lines = clear_lines()
    plen = path_length(lines)
    op = OpResult(label="testfire-clear", kind="clear", tool=TOOL,
                  lines=lines, path_len_mm=plen,
                  est_min=plen / CLEAR_FEED)
    header = [
        "(TEST FIRE rig, program 1 of 9 - clears the fiberglass window and "
        "engraves the rung ID ticks)",
        "(scrap: a 50x30mm or larger copper-clad offcut. G54 zero = SW "
        "corner of the region, Z0 = COPPER TOP)",
        f"(window x{WINDOW[0]:g}..{WINDOW[2]:g} y{WINDOW[1]:g}.."
        f"{WINDOW[3]:g} cleared to {CLEAR_DEPTH:g} - bare fiberglass)",
        "(then: squeegee ONE THIN white coat over the whole footprint, "
        "fit the laser, run the 8 ladder programs)",
    ]
    return emit.assemble(job, [op], header=header)


# ---------------------------------------------------------------- ladder ops
def rung_strokes(i: int) -> list[list[tuple]]:
    y = rung_y(i)
    return [[(COPPER_X[0], y), (COPPER_X[1], y)],
            [(BARE_X[0], y), (BARE_X[1], y)]]


def build_rung(i: int, dose: float) -> str:
    header = [
        f"(TEST FIRE rung {i + 1} of {len(DOSES)}: dose S{dose:g} at "
        f"F{FEED:g}, y = {rung_y(i):.2f})",
        "(one stroke over copper, one over the cleared window. The tick "
        "beside this y names the rung after the IPA wipe)",
        "(run the 8 rungs back to back - M321 is a no-op once laser mode "
        "is on. M322 exits when you are done)",
    ]
    return emit.assemble_laser(stem(dose), rung_strokes(i), dose_s=dose,
                               feed=FEED, header=header)


# ---------------------------------------------------------------- checks
def _moves(text: str):
    """(cmd, x, y, z, has_xy) per motion line, modal coords carried —
    has_xy distinguishes real strokes from Z-only moves like the laser's
    G0 Z0 focus move."""
    import re
    out, x, y, z = [], 0.0, 0.0, SAFE_Z
    for ln in text.splitlines():
        body = re.sub(r"\([^)]*\)", " ", ln).strip()
        m = re.match(r"\bG0?([01])\b", body)
        if not m:
            continue
        w = dict(re.findall(r"([XYZF])(-?\d+\.?\d*)", body))
        x = float(w.get("X", x)); y = float(w.get("Y", y))
        z = float(w.get("Z", z))
        out.append((int(m.group(1)), x, y, z, "X" in w or "Y" in w))
    return out


def check_clear(text: str) -> list[str]:
    """Convictions, [] == clean: floor, envelope, tick discipline, and a
    grid coverage proof that the window it promises is the window it cuts."""
    bad: list[str] = []
    mv = _moves(text)
    zs = {m[3] for m in mv}
    if min(zs) != CLEAR_DEPTH:
        bad.append(f"Z floor {min(zs)} is not {CLEAR_DEPTH}")
    if any(z < 0 and c == 0 for c, _, _, z, _ in mv):
        bad.append("rapid at cutting depth")
    x0, y0, x1, y1 = WINDOW
    tick_boxes = [(TICK_X0 - 0.45,
                   rung_y(i) - 0.45,
                   TICK_X0 + (TICK_LEN_TARGET if d == TARGET_DOSE
                              else TICK_LEN) + 0.45,
                   rung_y(i) + 0.45)
                  for i, d in enumerate(DOSES)]

    # coverage grid over the window, 0.1mm; cut cells outside window+radius
    # must lie in a declared tick box
    res = 0.1
    gx = np.arange(FOOTPRINT[0], FOOTPRINT[2], res) + res / 2
    gy = np.arange(FOOTPRINT[1], FOOTPRINT[3], res) + res / 2
    XX, YY = np.meshgrid(gx, gy)
    cut = np.zeros(XX.shape, bool)
    px, py, pz = None, None, SAFE_Z
    for c, x, y, z, _ in mv:
        if c == 1 and z == CLEAR_DEPTH and px is not None \
                and pz == CLEAR_DEPTH:
            seg = max(abs(x - px), abs(y - py))
            for t in np.linspace(0, 1, max(2, int(seg / res) + 2)):
                cx, cy = px + (x - px) * t, py + (y - py) * t
                cut |= (XX - cx) ** 2 + (YY - cy) ** 2 <= 0.4 ** 2
        px, py, pz = x, y, z
    inside = (XX > x0 + 0.05) & (XX < x1 - 0.05) \
        & (YY > y0 + 0.05) & (YY < y1 - 0.05)
    missed = inside & ~cut
    if missed.sum() / max(inside.sum(), 1) > 0.001:
        bad.append(f"window coverage {1 - missed.sum() / inside.sum():.4%} "
                   f"< 99.9%")
    outside = cut & ~((XX > x0 - 0.05) & (XX < x1 + 0.05)
                      & (YY > y0 - 0.05) & (YY < y1 + 0.05))
    for bx0, by0, bx1, by1 in tick_boxes:
        outside &= ~((XX > bx0) & (XX < bx1) & (YY > by0) & (YY < by1))
    if outside.any():
        i, j = np.argwhere(outside)[0]
        bad.append(f"cut outside window and ticks near "
                   f"({gx[j]:.1f},{gy[i]:.1f})")
    return bad


def check_rung(text: str, i: int, dose: float) -> list[str]:
    """Convictions, [] == clean: the one dose it promises, the two strokes
    it promises, nothing else."""
    import re
    bad: list[str] = []
    m3 = re.findall(r"^M3 S([\d.]+)\s*$",
                    re.sub(r"\([^)]*\)", "", text), re.M)
    if len(m3) != 1 or float(m3[0]) != dose:
        bad.append(f"dose is {m3} not [{dose}]")
    want = rung_strokes(i)
    got, cur = [], None
    for c, x, y, z, has_xy in _moves(text):
        if not has_xy:
            continue                       # the G0 Z0 focus move
        if c == 0:
            cur = [(x, y)]
            got.append(cur)
        elif cur is not None:
            cur.append((x, y))
    if len(got) != len(want) or any(
            max(abs(a - c) + abs(b - d)
                for (a, b), (c, d) in zip(g, w)) > 1e-6
            for g, w in zip(got, want)):
        bad.append(f"strokes {got} do not match the table {want}")
    return bad


def preview(clear_text: str, rung_texts: list[str], path: Path) -> None:
    """Drawn from the PARSED bytes (Article VI), 20 px/mm."""
    from PIL import Image, ImageDraw
    ppm, mx = 20, 10
    W = int((FOOTPRINT[2] - FOOTPRINT[0]) * ppm) + 2 * mx + 90
    H = int((FOOTPRINT[3] - FOOTPRINT[1]) * ppm) + 2 * mx

    def P(x, y):
        return (90 + mx + x * ppm, H - mx - y * ppm)

    im = Image.new("RGB", (W, H), (184, 115, 51))          # copper
    d = ImageDraw.Draw(im)
    px = py = pz = None
    for c, x, y, z, _ in _moves(clear_text):               # carved = tan
        if c == 1 and z == CLEAR_DEPTH and px is not None:
            d.line([P(px, py), P(x, y)], fill=(214, 196, 151),
                   width=int(0.8 * ppm))
        px, py, pz = x, y, z
    for i, (t, dose) in enumerate(zip(rung_texts, DOSES)):
        heat = int(255 * i / (len(DOSES) - 1))
        col = (heat, 40, 255 - heat)
        sx = sy = None
        for c, x, y, z, has_xy in _moves(t):
            if not has_xy:
                continue
            if c == 1 and sx is not None:
                d.line([P(sx, sy), P(x, y)], fill=col, width=3)
            sx, sy = x, y
        d.text((6, P(0, rung_y(i))[1] - 7), f"S{dose:g}", fill=col)
    x0, y0, x1, y1 = WINDOW
    d.rectangle([P(x0, y1), P(x1, y0)], outline=(90, 60, 30), width=2)
    d.rectangle([P(FOOTPRINT[0], FOOTPRINT[3]),
                 P(FOOTPRINT[2], FOOTPRINT[1])], outline=(0, 0, 0), width=2)
    im.save(path)


README = """# Test-fire dose ladder ({n} rungs, S{lo:g} .. S{hi:g} at F{feed:g})

Finds the landed dose for the S{target:g}-with-thin-coat pair on BOTH
substrates before orbit's silk programs fire (Board A debrief: dose and
coat move together).  One variable: dose.  Feed is fixed at F{feed:g}.

## Fixture

- Scrap: copper-clad FR-4 offcut, at least 50 x 30 mm, taped flat.
- G54 zero: SW corner of the region to use; Z0 = COPPER TOP.
- Everything stays inside x 0..46, y 0..26.

## Run order

1. `testfire-clear.nc` - T3 (0.8 corn).  Rasters the window at
   x {wx0:g}..{wx1:g} / y {wy0:g}..{wy1:g} down to bare fiberglass and
   engraves one ID tick per rung in the copper lane (the LONG tick is the
   S{target:g} rung).  Vacuum the dust.
2. OPERATOR: squeegee ONE THIN white UV coat over the whole footprint -
   copper, ticks and window alike.  Thin is the experiment.
3. Fit the 455nm laser.  Run the eight ladder programs in any order
   (position encodes dose; each file arms its own dose):
{ladder_rows}
   M321 in every file is a no-op once laser mode is on
   (community firmware Laser.cpp:261).  M322 exits when done.
4. OPERATOR: wipe the uncured white off with IPA.
5. Read the ladder against the ticks, bottom rung = S{lo:g}:
   - lowest dose whose line survives the wipe CRISP on each substrate,
   - any dose that scorches or cuts fiberglass in the window
     (S0.06 cut a board through a thick coat on 2026-07-30),
   - difference between the copper half and the fiberglass half - that
     delta is what this rig exists to measure.

A rung that reads well on copper but scorches on fiberglass means the
production pair holds for legend-over-mask-over-copper and the number to
carry into orbit.toml is the copper-side one.
"""


def main() -> int:
    job = pcbjob.load(Path(HERE) / "orbit.toml")    # Article XI: T3 must be
    t = job.tool(TOOL)                              # a real crib tool
    assert (t.type, t.diameter) == ("flat", 0.8), t
    OUT.mkdir(exist_ok=True)

    clear_text = build_clear(job)
    (OUT / "testfire-clear.nc").write_text(clear_text)
    rung_texts = []
    for i, dose in enumerate(DOSES):
        text = build_rung(i, dose)
        (OUT / f"{stem(dose)}.nc").write_text(text)
        rung_texts.append(text)

    print("verify (independent re-lint + geometry on the WRITTEN bytes):")
    fails = 0

    text = (OUT / "testfire-clear.nc").read_text()
    probs = emit.lint_program(text.splitlines()) + check_clear(text)
    print(f"  [{'FAIL' if probs else 'PASS'}] testfire-clear.nc")
    for p in probs:
        print(f"          {p}")
    fails += len(probs)

    for i, (dose, rt) in enumerate(zip(DOSES, rung_texts)):
        written = (OUT / f"{stem(dose)}.nc").read_text()
        probs = emit.lint_laser(written.splitlines()) \
            + check_rung(written, i, dose)
        print(f"  [{'FAIL' if probs else 'PASS'}] {stem(dose)}.nc")
        for p in probs:
            print(f"          {p}")
        fails += len(probs)

    print("negative controls (a check that cannot fail is not a check):")
    broken = "\n".join(ln for ln in clear_text.splitlines()
                       if "Y12." not in ln)          # drop mid-window rows
    ok = bool(check_clear(broken))
    print(f"  [{'PASS' if ok else 'FAIL'}] coverage convicts a raster "
          f"with dropped rows ({ok})")
    fails += 0 if ok else 1
    ok = bool(check_rung(rung_texts[0], 0, DOSES[1]))
    print(f"  [{'PASS' if ok else 'FAIL'}] ladder check convicts a dose "
          f"swap ({ok})")
    fails += 0 if ok else 1

    preview(clear_text, rung_texts, OUT / "testfire-preview.png")
    print(f"  preview: {OUT / 'testfire-preview.png'}")

    rows = "\n".join(f"   - `{stem(d)}.nc` -> S{d:g} at y {rung_y(i):.2f}"
                     for i, d in enumerate(DOSES))
    (OUT / "README.md").write_text(README.format(
        n=len(DOSES), lo=DOSES[0], hi=DOSES[-1], feed=FEED,
        target=TARGET_DOSE, wx0=WINDOW[0], wx1=WINDOW[2], wy0=WINDOW[1],
        wy1=WINDOW[3], ladder_rows=rows))

    print(f"\nTESTFIRE VERDICT: "
          + ("PASS — ladder ready for scrap" if fails == 0
             else f"FAIL ({fails}) — do NOT run"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
