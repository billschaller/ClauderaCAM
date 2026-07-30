"""PCB ground truth (WS2): gerbv raster maps, the Excellon parser, margin
math, and the derived machine transform.

What must hold, and why:
  - extents come from Edge.Cuts coordinate words and the raster
    cross-check catches outlines whose arcs bulge past their endpoints
    (misregistered extents would silently shift EVERY layer)
  - every layer rasterizes into ONE shared window, pixel-registered:
    a known pad lands at its known world coordinates through the
    module's own mapping, never a re-derived one
  - ink areas match closed-form geometry (the renderer is the ground
    truth for containment checks — if IT lies, everything lies)
  - the Excellon parser is strict: INCH, integer coordinates, undefined
    tools and missing headers all refuse (the gate does not guess)
  - the machine transform is DERIVED from extents (the 154/124 law)

gerbv-dependent sections skip LOUDLY when gerbv is absent (same posture
as kernel_parity without the Rust extension); CI installs gerbv.

Run: .venv/bin/python tests/pcb_groundtruth_suite.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np

from clauderacam.pcb import boardmaps as bm

fails = []


def check(name, ok, detail=""):
    print(f"  {name}: {'OK' if ok else 'FAIL'}"
          + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def caught(name, fn, needle):
    try:
        fn()
    except Exception as e:
        check(name, needle in str(e), f"got: {str(e)[:90]}")
        return
    check(name, False, "no exception raised")


TD = Path(tempfile.mkdtemp(prefix="clauderacam-ws2-"))


def _w(name, text):
    p = TD / name
    p.write_text(text)
    return p

EDGE = """%FSLAX46Y46*%
%MOMM*%
%ADD10C,0.050000*%
G01*
D10*
X0Y0D02*
X20000000Y0D01*
X20000000Y15000000D01*
X0Y15000000D01*
X0Y0D01*
M02*
"""
COPPER = """%FSLAX46Y46*%
%MOMM*%
%LPD*%
%ADD10C,1.000000*%
%ADD11R,2.000000X1.000000*%
G01*
D10*
X5000000Y5000000D02*
X15000000Y5000000D01*
D11*
X10000000Y10000000D03*
M02*
"""
TRACE_ONLY = COPPER.replace(
    "D11*\nX10000000Y10000000D03*\n", "")
PAD_ONLY = COPPER.replace(
    "D10*\nX5000000Y5000000D02*\nX15000000Y5000000D01*\n", "")
DRL = """M48
; synthetic
FMAT,2
METRIC
T1C0.800
T2C3.400
%
G90
G05
T1
X5.0Y5.0
X15.0Y5.0
T2
X10.0Y12.5
T0
M30
"""
(TD / "edge.gbr").write_text(EDGE)
(TD / "copper.gbr").write_text(COPPER)
(TD / "trace.gbr").write_text(TRACE_ONLY)
(TD / "pad.gbr").write_text(PAD_ONLY)
(TD / "test.drl").write_text(DRL)

# --- excellon (no gerbv needed) ---------------------------------------------
print("excellon:")
holes = bm.excellon(TD / "test.drl")
check("3 holes, exact schedule",
      holes == [(5.0, 5.0, 0.8), (15.0, 5.0, 0.8), (10.0, 12.5, 3.4)],
      str(holes))
caught("INCH refused", lambda: bm.excellon(_w("i.drl", DRL.replace(
    "METRIC", "INCH"))), "INCH")
caught("integer coordinates refused", lambda: bm.excellon(_w(
    "n.drl", DRL.replace("X5.0Y5.0", "X5Y5"))), "guess")
caught("undefined tool refused", lambda: bm.excellon(_w(
    "t.drl", DRL.replace("T2\n", "T9\n"))), "never defined")
caught("no M48 refused", lambda: bm.excellon(_w(
    "m.drl", DRL.replace("M48\n", ""))), "M48")

# --- margin math on hand-built masks (no gerbv needed) ----------------------
print("margin math:")
win_s = bm.BoardWindow(0, 0, 10, 10, dpi=254)   # 10 px/mm
a = np.zeros(win_s.shape, bool)
b = np.zeros(win_s.shape, bool)
a[50, 10] = True            # world (1.05, 4.95)
b[50, 40] = True            # world (4.05, 4.95) — 3.0 mm to the right
check("min_gap 3.0mm", abs(bm.min_gap_mm(a, b, win_s) - 3.0) < 1e-9)
check("escape of contained ink is 0", bm.escape_mm(a, a, win_s) == 0.0)
check("escape of displaced ink is its distance",
      abs(bm.escape_mm(a, b, win_s) - 3.0) < 1e-9)
check("empty mask distances are inf",
      bm.min_gap_mm(np.zeros_like(a), b, win_s) == float("inf"))

# --- machine transform (the 154/124 law) ------------------------------------
print("machine transform:")
win20 = bm.BoardWindow(0, 0, 20, 15)
check("mirror-x offset lands lower-left on the anchor",
      bm.machine_offset(win20, (154.0, 124.0)) == (154.0, 139.0))
check("unmirrored offset", bm.machine_offset(
    win20, (154.0, 124.0), mirror="none") == (154.0, 124.0))
off = bm.machine_offset(bm.BoardWindow(95.0, -114.5, 125.0, -90.5),
                        (10.0, 10.0))
check("offset derived from non-origin extents",
      off == (10.0 - 95.0, 10.0 + -90.5), str(off))
caught("unsupported mirror refused",
       lambda: bm.machine_offset(win20, (0, 0), mirror="y"), "unsupported")

# --- gerbv raster sections ---------------------------------------------------
if not bm.have_gerbv():
    print("SKIP: gerbv not available — raster ground truth not checkable "
          "here (install: sudo apt install gerbv)")
    print(f"\nPCB GROUND TRUTH {'FAIL: ' + ', '.join(fails) if fails else 'PASS (raster skipped)'}")
    sys.exit(1 if fails else 0)

print("extents + raster:")
win = bm.extents(TD / "edge.gbr", dpi=508)
check("edge extents exact",
      (win.x0, win.y0, win.x1, win.y1) == (0.0, 0.0, 20.0, 15.0),
      f"{(win.x0, win.y0, win.x1, win.y1)}")
check("window shape", win.shape == (300, 400), str(win.shape))

cu = bm.rasterize(TD / "copper.gbr", win)
pad = bm.rasterize(TD / "pad.gbr", win)
trace = bm.rasterize(TD / "trace.gbr", win)
ppmm = win.ppmm
pad_mm2 = pad.sum() / ppmm ** 2
trace_mm2 = trace.sum() / ppmm ** 2
check("pad area ~ 2.0 mm²", abs(pad_mm2 - 2.0) < 0.1, f"{pad_mm2:.3f}")
want_trace = 10.0 * 1.0 + np.pi * 0.5 ** 2      # stroke + round caps
check("trace area ~ closed form", abs(trace_mm2 - want_trace) < 0.35,
      f"{trace_mm2:.3f} vs {want_trace:.3f}")

i, j = win.world_to_px(10.0, 10.0)               # pad center
check("pad center ink at its world coordinates",
      bool(cu[int(i), int(j)]))
i, j = win.world_to_px(10.0, 5.0)                # trace centerline
check("trace centerline ink", bool(cu[int(i), int(j)]))
i, j = win.world_to_px(2.0, 12.0)                # empty corner
check("empty region clear", not bool(cu[int(i), int(j)]))

gap = bm.min_gap_mm(pad, trace, win)
check("pad-to-trace gap ~ 4.0mm (edge to edge)", abs(gap - 4.0) < 0.06,
      f"{gap:.3f}")
check("trace contained in full copper", bm.escape_mm(trace, cu, win) == 0.0)

# --- real zigbee files, when this box has them (bonus, not CI) --------------
zb = Path.home() / "scratch/ha/devices/projects/zigbee-button/pcb"
if (zb / "zigbee-button-v2-Edge_Cuts.gbr").is_file():
    print("zigbee-button V2 (local bonus):")
    zwin = bm.extents(zb / "zigbee-button-v2-Edge_Cuts.gbr", dpi=1270)
    print(f"  extents {zwin.x0:.2f},{zwin.y0:.2f} .. "
          f"{zwin.x1:.2f},{zwin.y1:.2f}  ({zwin.w_mm:.1f} x {zwin.h_mm:.1f})")
    zdrl = zb / "cam/flatcam/gerbers/zigbee-button-v2.drl"
    if zdrl.is_file():
        zh = bm.excellon(zdrl)
        check("zigbee hole schedule = the 10 verified bores",
              len(zh) == 10, f"{len(zh)} holes")
    zcu = bm.rasterize(zb / "zigbee-button-v2-B_Cu.gbr", zwin)
    zmask = bm.rasterize(zb / "zigbee-button-v2-B_Mask.gbr", zwin)
    check("zigbee copper renders ink", zcu.sum() > 1000)
    check("zigbee mask openings render ink", zmask.sum() > 100)

print(f"\nPCB GROUND TRUTH {'FAIL: ' + ', '.join(fails) if fails else 'PASS'}")
sys.exit(1 if fails else 0)
