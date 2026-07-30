"""Verification ground truth for the PCB lane: gerbv-rasterized layer
masks + the in-repo Excellon hole schedule (PCB-PLAN.md WS2).

Lineage law: the geometry ENGINE (FlatCAM) and this module read the same
gerber files through parsers that share no code — like the STL lane,
where the mesh rasterizer and the g-code simulator meet only at the
verification gate. Nothing here imports from, shells to, or trusts the
engine.

One window for every layer, derived ONCE:
  The board window is the Edge.Cuts CENTERLINE extents (x0, y0, x1, y1)
  in the gerbers' own mm frame. Every layer is rasterized into exactly
  that window at one declared dpi, so layer masks are pixel-registered
  by construction. The machine-frame transform is DERIVED from these
  extents — this is where the zigbee build's hand-computed 154/124
  offset constants die (they were measured once and trusted forever).

Raster convention (the lane's Article IV — one mapping, stated once):
  a map is bool[H, W]; pixel (i, j) covers the world square
    x in [x0 + j/ppmm, x0 + (j+1)/ppmm)
    y in (y1 - (i+1)/ppmm, y1 - i/ppmm]
  i.e. row 0 is the TOP of the board window. Convert with
  world_to_px / px_to_world on BoardWindow; never re-derive.

Extents caveat (stated, not hidden): centerline extents come from the
coordinate words of draws/flashes. A G02/G03 arc can bulge past its
endpoints; for rectangular outlines with quarter-corner arcs the
extremes land ON endpoints, which is every board this shop draws. The
raster cross-check in extents() (ink must stay inside extents plus half
the widest aperture) turns an exotic outline into a loud failure
instead of a silent misregistration.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import ndimage

DPI_DEFAULT = 2540          # 0.01 mm/px — the plan's declared resolution
_MM_PER_IN = 25.4

_INSTALL_HINT = (
    "gerbv not found — install it (sudo apt install gerbv), set "
    "CLAUDERACAM_GERBV, or place an extracted package under "
    "~/.clauderacam/tools/gerbv (usr/bin + usr/lib)")


def find_gerbv() -> tuple[list[str], dict]:
    """-> (argv prefix, extra env). Search order: $CLAUDERACAM_GERBV,
    PATH, the ~/.clauderacam/tools/gerbv extracted-package fallback
    (needs its own LD_LIBRARY_PATH — no system install required)."""
    envv = os.environ.get("CLAUDERACAM_GERBV")
    if envv:
        return [envv], {}
    onpath = shutil.which("gerbv")
    if onpath:
        return [onpath], {}
    local = Path.home() / ".clauderacam" / "tools" / "gerbv"
    exe = local / "usr" / "bin" / "gerbv"
    lib = local / "usr" / "lib" / "x86_64-linux-gnu"
    if exe.is_file():
        env = {"LD_LIBRARY_PATH": str(lib)
               + (":" + os.environ["LD_LIBRARY_PATH"]
                  if "LD_LIBRARY_PATH" in os.environ else "")}
        return [str(exe)], env
    raise FileNotFoundError(_INSTALL_HINT)


def have_gerbv() -> bool:
    try:
        find_gerbv()
        return True
    except FileNotFoundError:
        return False


@dataclass(frozen=True)
class BoardWindow:
    """The one shared raster window: Edge.Cuts centerline extents + dpi."""
    x0: float
    y0: float
    x1: float
    y1: float
    dpi: int = DPI_DEFAULT

    @property
    def ppmm(self) -> float:
        return self.dpi / _MM_PER_IN

    @property
    def w_mm(self) -> float:
        return self.x1 - self.x0

    @property
    def h_mm(self) -> float:
        return self.y1 - self.y0

    @property
    def shape(self) -> tuple[int, int]:
        return (int(round(self.h_mm * self.ppmm)),
                int(round(self.w_mm * self.ppmm)))

    def world_to_px(self, x, y):
        """world mm -> (i, j) float pixel coordinates (see module doc)."""
        return (self.y1 - np.asarray(y)) * self.ppmm, \
               (np.asarray(x) - self.x0) * self.ppmm

    def px_to_world(self, i, j):
        """pixel CENTERS -> world mm."""
        return self.x0 + (np.asarray(j) + 0.5) / self.ppmm, \
               self.y1 - (np.asarray(i) + 0.5) / self.ppmm


# ---------------------------------------------------------------- gerber scan
_FS = re.compile(r"%FSLAX(\d)(\d)Y(\d)(\d)\*%")
_MO = re.compile(r"%MO(MM|IN)\*%")
_AD_MOD = re.compile(r"%ADD\d+[A-Za-z_.$][^,*]*,([0-9.]+)")
_COORD = re.compile(r"([XY])([-+]?\d+)")
_OP = re.compile(r"D0([123])\*")


def _scan_coords(gbr_path: Path):
    """Yield (x_mm, y_mm) for every D01/D02/D03 coordinate in an RS-274X
    file (modal partners carried), plus the widest aperture definition.
    Strict: refuses inch units and trailing-zero suppression on sight —
    the gate does not guess number formats."""
    text = Path(gbr_path).read_text(errors="replace")
    fs = _FS.search(text)
    if not fs:
        raise ValueError(f"{gbr_path}: no %FSLAX..*% format spec — cannot "
                         f"interpret coordinates")
    if "%FST" in text:
        raise ValueError(f"{gbr_path}: trailing-zero suppression is not "
                         f"supported — the gate does not guess digits")
    mo = _MO.search(text)
    if not mo or mo.group(1) != "MM":
        raise ValueError(f"{gbr_path}: units are not MM — refuse rather "
                         f"than convert blind")
    xdiv = 10.0 ** int(fs.group(2))
    ydiv = 10.0 ** int(fs.group(4))
    widest = 0.0
    for m in _AD_MOD.finditer(text):
        widest = max(widest, float(m.group(1)))
    pts = []
    cur = {"X": None, "Y": None}
    for line in text.splitlines():
        if not _OP.search(line):
            continue
        for axis, digits in _COORD.findall(line):
            cur[axis] = int(digits) / (xdiv if axis == "X" else ydiv)
        if cur["X"] is not None and cur["Y"] is not None:
            pts.append((cur["X"], cur["Y"]))
    if not pts:
        raise ValueError(f"{gbr_path}: no drawn coordinates")
    return np.array(pts), widest


_AD_FULL = re.compile(r"%ADD(\d+)([A-Za-z_.$][A-Za-z0-9_.$]*?)"
                      r"(?:,([^*]*))?\*%")
_DSEL = re.compile(r"^(?:G54)?D(\d+)\*\s*$")


def _flash_scan(gbr_path: Path):
    """Shared strict scan for the two flash readers below: -> (text, lines,
    events). Each event is (line_index, op_code, x, y, shape, dia) for every
    D01/D02/D03 with a resolved modal position; shape is the selected
    aperture's template letter ("C", "R", "O", "P") or macro name, dia its
    first modifier (a circle's diameter) or None. Strictness matches
    _scan_coords (MM, no trailing-zero suppression) plus two of its own:
    %LPC*% (clear polarity) refuses — a cleared flash is NOT ink and a scan
    that counted it would invent copper — and so does a line carrying more
    than one D0n op, which the per-line rewrite below could not split."""
    text = Path(gbr_path).read_text(errors="replace")
    fs = _FS.search(text)
    if not fs:
        raise ValueError(f"{gbr_path}: no %FSLAX..*% format spec")
    if "%FST" in text:
        raise ValueError(f"{gbr_path}: trailing-zero suppression is not "
                         f"supported — the gate does not guess digits")
    mo = _MO.search(text)
    if not mo or mo.group(1) != "MM":
        raise ValueError(f"{gbr_path}: units are not MM — refuse rather "
                         f"than convert blind")
    if "%LPC*%" in text:
        raise ValueError(f"{gbr_path}: %LPC*% clear polarity — a cleared "
                         f"flash is not ink, and this scan refuses rather "
                         f"than misread it as some")
    xdiv = 10.0 ** int(fs.group(2))
    ydiv = 10.0 ** int(fs.group(4))
    defs: dict[str, tuple[str, float | None]] = {}
    for m in _AD_FULL.finditer(text):
        mods = m.group(3)
        dia = None
        if mods:
            try:
                dia = float(mods.split("X")[0])
            except ValueError:
                dia = None
        defs[m.group(1)] = (m.group(2), dia)
    lines = text.splitlines()
    events = []
    cur: dict[str, float | None] = {"X": None, "Y": None}
    shape, dia = "", None
    for k, line in enumerate(lines):
        sel = _DSEL.match(line.strip())
        if sel:
            shape, dia = defs.get(sel.group(1), ("", None))
            continue
        ops = _OP.findall(line)
        if not ops:
            continue
        if len(ops) > 1:
            raise ValueError(f"{gbr_path}:{k + 1}: {len(ops)} draw/flash ops "
                             f"on one line — the per-line scan cannot split "
                             f"them")
        for axis, digits in _COORD.findall(line):
            cur[axis] = int(digits) / (xdiv if axis == "X" else ydiv)
        if cur["X"] is None or cur["Y"] is None:
            continue
        events.append((k, ops[0], cur["X"], cur["Y"], shape, dia))
    return text, lines, events


def flashes(gbr_path: Path) -> list[tuple[float, float, str, float | None]]:
    """Every D03 flash in a gerber: (x, y, shape, dia). The scrub lane's
    aperture-class split reads pads and mask openings from here — flash
    positions and circle diameters are DESIGN numbers straight from the
    file's text, with no raster bias to argue about."""
    _, _, events = _flash_scan(gbr_path)
    return [(x, y, shape, dia) for _, op, x, y, shape, dia in events
            if op == "3"]


def rewrite_flashes(gbr_path: Path, out_path: Path, drop) -> int:
    """Copy a gerber, turning every D03 flash for which drop(x, y) is true
    into a D02 MOVE to the same point: zero ink, and the modal X/Y state
    every later line sees is bit-identical (deleting the line outright would
    hand the next partially-worded coordinate a stale position). Everything
    else passes through byte-identical. -> count rewritten."""
    _, lines, events = _flash_scan(gbr_path)
    n = 0
    for k, op, x, y, _, _ in events:
        if op == "3" and drop(x, y):
            lines[k] = lines[k].replace("D03*", "D02*")
            n += 1
    Path(out_path).write_text("\n".join(lines) + "\n")
    return n


def extents(edge_gbr: Path, dpi: int = DPI_DEFAULT,
            cross_check: bool = True) -> BoardWindow:
    """Board window from Edge.Cuts centerline extents. With cross_check
    (needs gerbv), rasterizes the outline into a padded window and
    requires all ink within extents + half the widest aperture + 2px —
    an arc bulging past its endpoints fails HERE, loudly."""
    pts, widest = _scan_coords(edge_gbr)
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    win = BoardWindow(float(x0), float(y0), float(x1), float(y1), dpi)
    if cross_check:
        pad = widest / 2 + 1.0
        wide = BoardWindow(win.x0 - pad, win.y0 - pad,
                           win.x1 + pad, win.y1 + pad, dpi)
        ink = rasterize(edge_gbr, wide)
        ii, jj = np.nonzero(ink)
        if ii.size:
            xs, ys = wide.px_to_world(ii, jj)
            tol = widest / 2 + 2.0 / win.ppmm
            if (xs.min() < win.x0 - tol or xs.max() > win.x1 + tol
                    or ys.min() < win.y0 - tol or ys.max() > win.y1 + tol):
                raise ValueError(
                    f"{edge_gbr}: outline ink escapes the coordinate-word "
                    f"extents by more than half an aperture — an arc "
                    f"bulges past its endpoints; this outline shape is "
                    f"not supported")
    return win


def rasterize(gbr_path: Path, win: BoardWindow) -> np.ndarray:
    """One layer -> bool[H, W] in the shared window via gerbv PNG export
    (black ink on white, no antialiasing, threshold at mid-grey)."""
    argv, env = find_gerbv()
    h, w = win.shape
    with tempfile.TemporaryDirectory(prefix="clauderacam-gerbv-") as td:
        png = Path(td) / "layer.png"
        cmd = argv + [
            "--export=png", f"--output={png}",
            "--border=0",           # gerbv defaults to a 5% border, which
            #                         would silently rescale every window
            f"--dpi={win.dpi}",
            f"--origin={win.x0 / _MM_PER_IN:.6f}x{win.y0 / _MM_PER_IN:.6f}",
            f"--window_inch={win.w_mm / _MM_PER_IN:.6f}x"
            f"{win.h_mm / _MM_PER_IN:.6f}",
            "--foreground=#000000", "--background=#FFFFFF",
            str(gbr_path)]
        proc = subprocess.run(cmd, env={**os.environ, **env},
                              capture_output=True, text=True, timeout=120)
        if not png.is_file():
            raise RuntimeError(
                f"gerbv failed on {gbr_path}: {proc.stderr[-400:]}")
        from PIL import Image
        arr = np.asarray(Image.open(png).convert("L"))
    mask = arr < 128
    if mask.shape != (h, w):
        # gerbv rounds the pixel window itself; a one-pixel disagreement
        # would silently misregister every layer — align by crop/pad and
        # refuse anything larger
        if abs(mask.shape[0] - h) > 1 or abs(mask.shape[1] - w) > 1:
            raise RuntimeError(
                f"gerbv window {mask.shape} vs expected {(h, w)} — dpi/"
                f"window math drifted more than one pixel")
        out = np.zeros((h, w), dtype=bool)
        hh, ww = min(h, mask.shape[0]), min(w, mask.shape[1])
        out[:hh, :ww] = mask[:hh, :ww]
        mask = out
    return mask


# ------------------------------------------------------------------ excellon
def excellon(path: Path) -> list[tuple[float, float, float]]:
    """KiCad-dialect Excellon -> [(x_mm, y_mm, dia_mm)]. Strict: requires
    M48 + METRIC + decimal coordinates (FMAT,2 as KiCad writes); refuses
    INCH and integer-coordinate files rather than guess a digit format.
    ~50 lines, no external parser — the hole schedule is ground truth."""
    holes: list[tuple[float, float, float]] = []
    tools: dict[str, float] = {}
    cur: float | None = None
    metric = False
    lines = Path(path).read_text(errors="replace").splitlines()
    if not lines or lines[0].strip() != "M48":
        raise ValueError(f"{path}: not an Excellon file (no M48 header)")
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith(";"):
            continue
        if s == "METRIC" or s.startswith("METRIC,"):
            metric = True
            continue
        if s == "INCH" or s.startswith("INCH,"):
            raise ValueError(f"{path}: INCH excellon refused — export "
                             f"metric from KiCad")
        m = re.fullmatch(r"T(\d+)C([0-9.]+)", s)
        if m:
            tools[m.group(1)] = float(m.group(2))
            continue
        m = re.fullmatch(r"T(\d+)", s)
        if m and m.group(1) != "0":
            if m.group(1) not in tools:
                raise ValueError(f"{path}: T{m.group(1)} selected but "
                                 f"never defined")
            cur = tools[m.group(1)]
            continue
        m = re.fullmatch(r"X([-+]?[0-9.]+)Y([-+]?[0-9.]+)", s)
        if m:
            if not metric:
                raise ValueError(f"{path}: coordinates before METRIC")
            if "." not in m.group(1) or "." not in m.group(2):
                raise ValueError(
                    f"{path}: integer excellon coordinates refused — the "
                    f"gate does not guess digit formats (KiCad writes "
                    f"decimal)")
            if cur is None:
                raise ValueError(f"{path}: drill hit before tool select")
            holes.append((float(m.group(1)), float(m.group(2)), cur))
    if not holes:
        raise ValueError(f"{path}: no holes")
    return holes


# ------------------------------------------------------------------- margins
def dist_mm(mask: np.ndarray, win: BoardWindow) -> np.ndarray:
    """Distance (mm) from each pixel to the nearest ink pixel of mask."""
    if not mask.any():
        return np.full(mask.shape, np.inf)
    return ndimage.distance_transform_edt(~mask) / win.ppmm


def escape_mm(inner: np.ndarray, outer: np.ndarray,
              win: BoardWindow) -> float:
    """Worst distance any inner ink stands OUTSIDE outer (0.0 == fully
    contained). The containment number every lane check is built on."""
    if not inner.any():
        return 0.0
    return float(dist_mm(outer, win)[inner].max())


def min_gap_mm(a: np.ndarray, b: np.ndarray, win: BoardWindow) -> float:
    """Smallest distance from any ink of a to any ink of b (inf if either
    is empty)."""
    if not a.any() or not b.any():
        return float("inf")
    return float(dist_mm(b, win)[a].min())


# ----------------------------------------------------- machine-frame transform
def machine_offset(win: BoardWindow, anchor_xy: tuple[float, float],
                   mirror: str = "x") -> tuple[float, float]:
    """DERIVE the FlatCAM-side translation that lands the (mirrored)
    board with its lower-left corner on the machine-frame anchor.

    mirror="x" is the single-sided back-copper convention and matches
    FlatCAM's `mirror <obj> -axis X -origin 0,0`, which NEGATES X
    (mirrors across the Y axis — verified against the zigbee V2 Tcl:
    its hand-computed `offset -x 154 -y 124` is exactly (x1, -y0) of
    the board extents, landing the SW corner on G54 (0,0)). The
    mirrored x-span [-x1, -x0] plus (ax + x1, ay - y0) puts the board
    at [ax, ax+w] x [ay, ay+h]. mirror "none" places the unmirrored
    board. Anything else refuses.

    Which side gets which: a KiCad gerber is drawn as seen from the TOP,
    so a layer whose copper faces the spindle needs no mirror and one
    whose copper faces away needs one. F.Cu machined front-up -> "none";
    B.Cu machined back-up (the single-sided default, and side 2 of a
    pin-and-flip board) -> "x"."""
    ax, ay = anchor_xy
    if mirror == "x":
        return ax + win.x1, ay - win.y0
    if mirror == "none":
        return ax - win.x0, ay - win.y0
    raise ValueError(f"unsupported mirror {mirror!r}")


def machine_xy(offset: tuple[float, float], mirror: str, bx, by):
    """gerber frame -> machine frame under a derived (offset, mirror). The
    ONE place the sign of the mirror is spelled out for consumers: the Tcl
    template, the raster checks, the silk strokes and the sheet model all
    come here instead of each writing `dx - x` and hoping."""
    dx, dy = offset
    x = np.asarray(bx, dtype=float)
    return (dx - x if mirror == "x" else dx + x), np.asarray(by,
                                                             dtype=float) + dy


def board_xy(offset: tuple[float, float], mirror: str, x, y):
    """machine frame -> gerber frame: the exact inverse of machine_xy."""
    dx, dy = offset
    mx = np.asarray(x, dtype=float)
    return (dx - mx if mirror == "x" else mx - dx), np.asarray(y,
                                                              dtype=float) - dy


def flip_line(win: BoardWindow, anchor_xy: tuple[float, float]) -> float:
    """The machine-frame X of the board's OWN mirror line — the axis the
    blank physically rotates about in a pin-and-flip job — DERIVED from the
    same Edge.Cuts extents both side frames come from.

    A board feature at gerber x lands at (ax - x0) + x in side A's frame
    (mirror "none") and at (ax + x1) - x in side B's (mirror "x"). Those two
    machine positions average to ((ax - x0) + (ax + x1)) / 2 for EVERY x —
    the x-independence is the proof, and the constant it leaves is the
    mirror line: ax + (x1 - x0)/2, the board's centreline.

    The assert below closes the loop on the falsified-and-fixed WS2 law
    (`mirror -axis X` NEGATES X, i.e. mirrors across the Y axis). If either
    transform's sign or offset formula drifts, the average stops being
    constant and this raises instead of shipping two frames that disagree
    about where the board is.
    """
    dxa, dya = machine_offset(win, anchor_xy, "none")
    dxb, dyb = machine_offset(win, anchor_xy, "x")
    line = (dxa + dxb) / 2.0
    probes = np.array([win.x0, win.x1, (win.x0 + win.x1) / 2.0,
                       win.x0 + 0.37 * (win.x1 - win.x0)])
    ys = np.array([win.y0, win.y1, win.y0, win.y1])
    ax, ay = machine_xy((dxa, dya), "none", probes, ys)
    bx, by = machine_xy((dxb, dyb), "x", probes, ys)
    worst = float(np.abs((ax + bx) / 2.0 - line).max())
    if worst > 1e-9 or float(np.abs(ay - by).max()) > 1e-9:
        raise ValueError(
            f"the two side frames do not mirror about one line (worst "
            f"{worst:.6g}mm) — `mirror -axis X` must NEGATE X and leave Y "
            f"alone; one of machine_offset's two branches has drifted")
    return line
