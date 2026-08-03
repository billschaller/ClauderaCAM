"""[pcb] viewer sessions (PCB-PLAN.md WS6): a PCB job in front of human eyes.

A PCB job is FOUR sessions, one per program of the canonical split — the
same shape twosided.py uses for its two sides (sessions are keyed by the
artifact the operator posts, and a [pcb] document posts four files). Each
session carries what every other session carries: a stage list recovered
from the program's own `(begin operation: ...)` markers, per-stage stats,
the gate's checks, and the verified bytes to download.

A DOUBLE-SIDED document is the same thing TWICE plus its artwork report
(WS8, 2026-08-02). Each setup is a pcbjob.side_view — a job shaped like a
single-sided one — so every function below runs on it unchanged; the
sessions are then composed into ONE list, keyed and named `<side>/<program>`
the way flip.verify_twosided keys its reports and tools-cam.py names its
files (`orbit-front-mill.nc`). The document's own checks (flip.board_checks:
artwork, frame, annular ring, paste-vs-hole) belong to no program, so they
get a session of their own named `board` — the operator sees one list with
every artifact in it, in machining order, and nothing that judged this board
is left off the screen. Board A's four sessions are untouched by all of it:
the single-sided path builds from the same helpers with no side prefix.

Two things are new here, and both exist because half this chain does not
carve:

  THE SHEET STOCK MODEL (the WS5 debt, paid — and since 2026-07-30 OWNED
  BY THE GATE).  `verify.verify()` needs a target mesh and a square coin
  blank; a PCB has neither. What the geometric machinery actually needs
  is smaller: a stock plane, a grid, and a tool table. `sheet_stock()`
  defines it — thickness from the blank, XY window DERIVED from the
  Edge.Cuts board window in machine frame (boardmaps.machine_offset, the
  154/124 law) grown by the widest off-board reach the job configures,
  exactly the way checks.board_maps pads its raster window. With that,
  the MILL and HOLES programs ride simulate.carve() unchanged: real
  per-move volume, contact, engagement, shank clearance, rapid-vs-stock
  and depth-vs-spoilboard on the actual bytes. WS6 built the model here
  for the viewer; checks.verify_pcb has since ADOPTED it (the gate must
  be the strictest reader), so the definitions live in checks.py, the
  gate's Reports carry the CarveResult, and this module serves the SAME
  simulation the gate judged instead of running a second one.

  Three honesty notes, stated because they are load-bearing:
    * the kernel's grid is square and centred on the machine origin
      (Article IV, one mapping, never re-derived), so the simulated
      square is larger than the modelled sheet. Everything served to the
      viewer is CROPPED to the declared sheet window, and the crop is
      proven lossless: if any material was removed outside it, this
      module refuses rather than serve a preview that hides a cut.
    * every program starts from a VIRGIN sheet. Carrying stock from one
      program to the next needs an initial-stock kernel parameter that
      does not exist (Article X: both kernels or neither). A virgin sheet
      holds MORE material than the real one, so every contact, rapid and
      engagement reading is an upper bound — the conservative reading,
      which is the one that wins.
    * the sim resolution is the gate's own (simulate.carve_check's
      12.5 px/mm). At 0.08mm/px a 0.2mm-tip isolation groove is one to
      two pixels wide, so the iso stage's REMOVED VOLUME is a quantized
      number (53-63mm³ across 10-25 px/mm on Board A), not a precision
      claim. Depths, contact and the pass/fail checks are stable across
      that range; volume is the number to distrust.

  THE 2D OVERLAY (Article VI).  Silk and scrub are not carving. The laser
  removes nothing, and the spring tool's commanded depth is preload —
  the kernel gives it an EMPTY footprint by law (kernel_py's Article IX
  exemption), so a heightmap "simulation" of either would render a flat
  sheet and call it a preview. That is a lie with a picture attached.
  Instead those programs get a 2D overlay of what the program actually
  DRAWS, parsed from the same bytes the gate judged: the laser's firing
  G1 segments, the spring tool's cutting polylines. Two gerber-derived
  reference layers ride along, labelled as gerber and off by default —
  B.Mask apertures (the regions the scrub is supposed to cover; the gate
  deliberately does not bar scrub COVERAGE, see checks.py, so this is a
  picture and never a verdict) and B.Paste apertures (the stencil source
  for the run sheet's off-machine steps).

  THE RUN SHEET.  The operator steps between the programs are the job
  (pcbjob.py's module docstring is the law). `run_sheet()` renders the
  whole bench workflow in order with the programs in their places, every
  step traced to the guides it comes from
  (~/scratch/carvera/guides/pcb-milling-workflow.md §2/§4/§6,
  solder-mask-and-silkscreen.md §1-§5) or to the bytes it describes.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .. import simulate, stages as stagesmod
from ..verify import Report
from . import boardmaps, checks, flip, pcbjob
# The sheet stock model MOVED to checks.py (2026-07-30): a bare verify_pcb()
# used to prove less than a viewer session, and the gate must be the
# strictest reader. The names are re-imported here because this module and
# its tests are the model's other reader — same definitions, one home.
from .checks import (CARVING, OVERLAY_ONLY, SHEET_PPM,  # noqa: F401
                     SheetJob, SheetStock, carve_program, sheet_checks,
                     sheet_job, sheet_stock)
from .pcbjob import PcbJob

_LASER_XY = re.compile(r"^(G0?[01])\b")


def is_pcb(path) -> bool:
    """Is this TOML a [pcb] document? (viewer/server.py dispatches on it the
    same way it dispatches on twosided.is_twosided)."""
    try:
        with open(path, "rb") as f:
            return "pcb" in tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return False


def program_paths(job: PcbJob) -> dict[str, Path]:
    """{program: path} for the split THIS job is made of. The engine writes
    into `out_dir`; a blessed asset set (tests/golden_pcb) keeps the programs
    beside the TOML — try both, and report what is missing rather than
    inventing a path.

    On a SIDE VIEW the file stem is pcbjob.program_stem's (`orbit-front-mill`,
    the convention tools-cam.py writes with) and the split is that side's —
    one naming law, so the viewer looks where the generator wrote."""
    def nc(name: str) -> str:
        return (f"{pcbjob.program_stem(job, name)}.nc" if job.side
                else f"{job.stem}-{name}.nc")

    out: dict[str, Path] = {}
    for name in pcbjob.programs_of(job):
        for cand in (job.out_dir / nc(name),
                     job.path.parent / nc(name)):
            if cand.is_file():
                out[name] = cand
                break
    return out


def document_programs(job: PcbJob):
    """Everything this DOCUMENT's programs are on disk, in the shape build()
    takes as `programs`: {program: path} single-sided, {side: {program: path}}
    for a flipped board (flip.verify_twosided's shape — one convention for the
    gate and the viewer)."""
    if job.twosided:
        return {side: program_paths(pcbjob.side_view(job, side))
                for side in job.sides}
    return program_paths(job)


def program_count(progs) -> int:
    """How many program files document_programs() actually found."""
    return sum(len(v) if isinstance(v, dict) else 1 for v in progs.values())


# ----------------------------------------------------------------- overlays
def laser_strokes(text: str) -> tuple[list[list[list[float]]], float]:
    """FIRING polylines of a laser program -> (polylines, mm). The laser
    fires only on feed moves (firmware fact, mask guide §5), so a G0 starts a
    new dark hop and every G1 extends the stroke that is burning. Parsed from
    the bytes the gate judged, never from the gerber the emitter read."""
    polys: list[list[list[float]]] = []
    cur: list[list[float]] | None = None
    at: list[float] | None = None
    total = 0.0
    for raw in text.splitlines():
        body = raw.split("(")[0].strip()
        if not _LASER_XY.match(body) or "X" not in body or "Y" not in body:
            continue
        x = float(body.split("X")[1].split()[0])
        y = float(body.split("Y")[1].split()[0])
        if body.startswith("G1") or body.startswith("G01"):
            if at is None:
                continue
            if cur is None:
                cur = [list(at)]
                polys.append(cur)
            cur.append([x, y])
            total += float(np.hypot(x - at[0], y - at[1]))
        else:
            cur = None
        at = [x, y]
    return polys, total


def cutting_polylines(sj: SheetJob, nc_path) -> tuple[list, float, float]:
    """Cutting polylines of a MILL-dialect program -> (polylines, mm, zmin).
    Chained through the strict parser (simulate.prep_moves), so the overlay
    describes exactly the moves the gate resolved."""
    m = simulate.prep_moves(nc_path, sj, sj.machine["rapid_feed"])
    cut = (m.motion == 1) & (np.minimum(m.z0, m.z1) < -1e-3)
    polys: list[list[list[float]]] = []
    cur: list[list[float]] | None = None
    total = 0.0
    prev = None
    for k in np.nonzero(cut)[0]:
        a = (float(m.x0[k]), float(m.y0[k]))
        b = (float(m.x1[k]), float(m.y1[k]))
        if a == b:
            continue                      # a plunge draws nothing in XY
        if cur is None or prev != a:
            cur = [[a[0], a[1]]]
            polys.append(cur)
        cur.append([b[0], b[1]])
        total += float(np.hypot(b[0] - a[0], b[1] - a[1]))
        prev = b
    zmin = float(np.minimum(m.z0, m.z1)[cut].min()) if cut.any() else 0.0
    return polys, total, zmin


# Aperture flashes, for the two gerber REFERENCE layers. Circles, rectangles,
# obrounds and KiCad's RoundRect macro (whose parameters are the four corner
# offsets, so the quad is exact — only the corner rounding is not drawn).
# Anything else is COUNTED and reported, never silently dropped: an overlay
# that quietly omits pads is the same class of lie as a preview of the
# target model.
_AD = re.compile(r"%ADD(\d+)([A-Za-z_.$][A-Za-z0-9_.$]*),([-0-9.X]+)\*%")
_DN = re.compile(r"^D(\d+)\*")
_GN = re.compile(r"\bG0?([123])(?=[XYIJD*])|\bG(7[45])\*")
_IJ = re.compile(r"([IJ])([-+]?\d+)")
_ARC_STEP = 0.05        # mm along a G75 arc — a reference layer, not a path


def apertures(gbr: Path) -> tuple[list[dict], list[list[list[float]]],
                                  dict[str, int]]:
    """-> (flashes, draws, skipped) for one gerber, in its own mm frame.

    A layer is not only its flashes: KiCad writes graphics as D01 DRAW
    chains, and mask ink drawn that way is mask ink (Board A's scrub-margin
    ring became exactly that on 2026-07-30 — the board-side fix for the
    'ring is not a gauge' incident). An overlay that showed flashes only
    would have quietly omitted it, so draws come back too, as centerlines
    (their aperture WIDTH is not drawn, and the layer's note says so)."""
    text = Path(gbr).read_text(errors="replace")
    fs = boardmaps._FS.search(text)
    mo = boardmaps._MO.search(text)
    if not fs or not mo or mo.group(1) != "MM":
        raise ValueError(f"{gbr}: needs FSLA + MM (KiCad export)")
    xdiv, ydiv = 10.0 ** int(fs.group(2)), 10.0 ** int(fs.group(4))
    defs: dict[str, tuple[str, list[float]]] = {}
    for m in _AD.finditer(text):
        try:
            ps = [float(v) for v in m.group(3).split("X")]
        except ValueError:
            ps = []
        defs[m.group(1)] = (m.group(2), ps)
    flashes: list[dict] = []
    draws: list[list[list[float]]] = []
    skipped: dict[str, int] = {}
    cur = {"X": None, "Y": None}
    sel: str | None = None
    chain: list[list[float]] = []
    mode, quad = 1, 75
    for line in text.splitlines():
        if line.startswith("G04") or line.startswith("G4"):
            continue                          # a comment, whose text is free
        gn = _GN.search(line.strip())
        if gn:
            if gn.group(1):
                mode = int(gn.group(1))
            else:
                quad = int(gn.group(2))
        dn = _DN.match(line.strip())
        if dn and dn.group(1) not in ("01", "02", "03", "1", "2", "3"):
            sel = dn.group(1)
            continue
        op = boardmaps._OP.search(line)
        if not op:
            continue
        prev = (cur["X"], cur["Y"])
        for axis, digits in boardmaps._COORD.findall(line):
            cur[axis] = int(digits) / (xdiv if axis == "X" else ydiv)
        if cur["X"] is None or cur["Y"] is None:
            continue
        code = op.group(1)
        x, y = cur["X"], cur["Y"]
        if code == "1":                       # draw: extend the chain
            if mode in (2, 3) and None not in prev:
                ij = {a: int(d) for a, d in _IJ.findall(line)}
                arc = _arc_points(prev, (x, y), ij.get("I", 0) / xdiv,
                                  ij.get("J", 0) / ydiv, mode == 2,
                                  quad == 75)
                if arc is None:
                    # a shape this scanner will not guess at: counted, so the
                    # layer's own note admits the omission
                    skipped["arc draw"] = skipped.get("arc draw", 0) + 1
                    if len(chain) >= 2:
                        draws.append(chain)
                    chain = []
                    continue
                if not chain:
                    chain = [[prev[0], prev[1]]]
                chain += arc
                continue
            chain.append([x, y])
            continue
        if len(chain) >= 2:
            draws.append(chain)
        chain = [[x, y]] if code == "2" else []
        if code != "3":
            continue
        kind, ps = defs.get(sel or "", ("?", []))
        poly = _aperture_poly(kind, ps, x, y)
        if poly is None:
            skipped[kind] = skipped.get(kind, 0) + 1
            continue
        flashes.append({"poly": poly})
    if len(chain) >= 2:
        draws.append(chain)
    return flashes, draws, skipped


def _arc_points(a, b, i: float, j: float, cw: bool, multi: bool):
    """G75 circular draw a->b about (a + (i,j)) -> polyline points after `a`,
    or None when this scanner will not guess the shape.

    Refuses single-quadrant mode (G74, where the sign of I/J is dropped and
    the quadrant has to be inferred) and an inconsistent radius: the ONE
    thing this layer must never do is invent geometry, because the operator
    reads it as the board (Board A's scrub-margin ring is exactly this — two
    G75 half-circles, and drawn as chords it would have been a straight line
    through the ring's middle)."""
    if not multi:
        return None
    cx, cy = a[0] + i, a[1] + j
    r0 = float(np.hypot(a[0] - cx, a[1] - cy))
    r1 = float(np.hypot(b[0] - cx, b[1] - cy))
    if r0 <= 1e-9 or abs(r1 - r0) > max(1e-3, 0.01 * r0):
        return None
    t0 = float(np.arctan2(a[1] - cy, a[0] - cx))
    t1 = float(np.arctan2(b[1] - cy, b[0] - cx))
    sweep = t1 - t0
    if cw:
        while sweep >= 0:
            sweep -= 2 * np.pi
    else:
        while sweep <= 0:
            sweep += 2 * np.pi
    if abs(b[0] - a[0]) < 1e-9 and abs(b[1] - a[1]) < 1e-9:
        sweep = -2 * np.pi if cw else 2 * np.pi   # full circle
    n = max(2, int(np.ceil(abs(sweep) * r0 / _ARC_STEP)))
    ts = t0 + sweep * np.linspace(0.0, 1.0, n + 1)[1:]
    return [[cx + r0 * float(np.cos(t)), cy + r0 * float(np.sin(t))]
            for t in ts]


def _aperture_poly(kind: str, ps: list[float], x: float, y: float):
    if kind == "C" and ps:
        r = ps[0] / 2
        t = np.linspace(0, 2 * np.pi, 25)
        return [[x + r * float(np.cos(a)), y + r * float(np.sin(a))]
                for a in t]
    if kind in ("R", "O") and len(ps) >= 2:
        w, h = ps[0] / 2, ps[1] / 2
        return [[x - w, y - h], [x + w, y - h], [x + w, y + h],
                [x - w, y + h], [x - w, y - h]]
    if kind == "RoundRect" and len(ps) >= 9:
        c = ps[1:9]
        pts = [[x + c[i], y + c[i + 1]] for i in range(0, 8, 2)]
        return pts + [pts[0]]
    return None


def _gerber_layer(offset: tuple[float, float], path: Path, key: str,
                  label: str, color: str, on: bool = False,
                  mirror: str = "x") -> dict | None:
    """One aperture layer, transformed into machine frame with the SAME
    derived mirror+offset every other layer gets."""
    if not path.is_file():
        return None
    flashes, draws, skipped = apertures(path)

    def frame(pts):
        xs, ys = boardmaps.machine_xy(offset, mirror,
                                      [p[0] for p in pts],
                                      [p[1] for p in pts])
        return [[float(a), float(b)] for a, b in zip(xs, ys)]

    polys = [frame(f["poly"]) for f in flashes] + [frame(c) for c in draws]
    note = f"{len(flashes)} flashes from {path.name} (gerber, not a toolpath)"
    if draws:
        note += (f" + {len(draws)} drawn chains as CENTERLINES (their "
                 f"aperture width is not drawn)")
    if skipped:
        note += "; NOT DRAWN: " + ", ".join(
            f"{n}x{k}" for k, n in sorted(skipped.items()))
    return {"key": key, "label": label, "kind": "polys", "color": color,
            "on": on, "polys": polys, "note": note}


def overlay_for(job: PcbJob, name: str, text: str, nc_path,
                sj: SheetJob) -> dict:
    """The 2D overlay payload of one program: what the bytes DRAW, plus the
    labelled gerber reference layers."""
    layers: list[dict] = []
    notes: list[str] = []
    if name == "silk":
        polys, mm = laser_strokes(text)
        dose = float(job.phases["silk"]["dose"])
        layers.append({
            "key": "silk", "label": "laser firing strokes (G1 segments)",
            "kind": "strokes", "color": "#eef2ff", "on": True,
            "polylines": polys,
            "note": f"{len(polys)} strokes, {mm:.2f}mm of firing travel at "
                    f"dose S{dose:g} — the laser fires only on feed moves, "
                    f"so G0 hops are dark and are not drawn"})
        notes.append("no stock simulation: a laser removes no material "
                     "(Article VI — a heightmap of this program would be a "
                     "flat sheet presented as a preview)")
    elif name == "scrub":
        polys, mm, zmin = cutting_polylines(sj, nc_path)
        tool = job.phase_tool("scrub")
        layers.append({
            "key": "scrub", "label": f"spring-tool scrub path "
                                     f"(T{tool.num} Ø{tool.diameter:g})",
            "kind": "strokes", "color": "#f0b429", "on": True,
            "polylines": polys,
            "note": f"{len(polys)} laps, {mm:.2f}mm at Z{zmin:g} of spring "
                    f"PRELOAD (not cut depth)"})
        notes.append("no stock simulation: the spring tool's commanded depth "
                     "is preload, so the kernel gives it an empty footprint "
                     "and removes nothing (Article IX exemption, stated in "
                     "kernel_py.py)")
    offset = boardmaps.machine_offset(
        boardmaps.extents(job.files["edge"], cross_check=False), job.anchor,
        job.mirror)
    mask = _gerber_layer(offset, job.files["mask"], "mask_ap",
                         "B.Mask apertures (gerber)", "#6fdc8c",
                         mirror=job.mirror)
    if mask:
        mask["note"] += " — the solderable openings: silk strokes must " \
                        "clear them and the scrub is meant to cover them. " \
                        "The gate deliberately does not bar scrub COVERAGE " \
                        "(checks.py): this layer is a picture, not a verdict."
        layers.append(mask)
    paste_p = job.files.get(
        "paste", job.gerber_dir / f"{job.stem}{pcbjob.PASTE_SUFFIX}")
    if job.side and job.side != pcbjob.SIDE_ORDER[1]:
        # ONE stencil, and it is the BACK side's (orbit SPEC's paste rule).
        # Drawing B.Paste in side A's frame would place a back-side layer
        # through the front's unmirrored transform — an overlay that lies
        # about where the pads are. Say where the stencil lives instead.
        notes.append(f"the stencil artwork is the BACK setup's "
                     f"({paste_p.name}) — it belongs to the other side's "
                     f"frame and is not drawn here")
        return {"layers": layers, "notes": notes}
    paste = _gerber_layer(offset, paste_p, "paste_ap",
                          "B.Paste apertures (gerber)", "#7aa2ff",
                          mirror=job.mirror)
    if paste:
        paste["note"] += " — the stencil source for the run sheet's " \
                         "off-machine paste/reflow steps."
        layers.append(paste)
    else:
        notes.append(f"no paste layer exported ({paste_p.name} absent) — "
                     f"the stencil step has no source artwork yet")
    return {"layers": layers, "notes": notes}


# ---------------------------------------------------------------- run sheet
def _fmt(seconds: float) -> str:
    return stagesmod.fmt_time(seconds)


def run_sheet(job: PcbJob, progs: dict[str, Path],
              est: dict[str, float], tool_order: dict[str, list[int]],
              paste: bool) -> list[dict]:
    """The bench workflow: operator steps and programs in ONE order.

    Provenance for every step: pcb-milling-workflow.md §2 (the fixture and
    the one G54 zero), §4 (run order, auto-level, the 1.5mm blank), §6
    (solder order); solder-mask-and-silkscreen.md §1-§3 (the spring tool),
    §4 (the mask workflow, tested), §5 (the laser legend, UNTESTED as a
    process — said so here as the guide says it); PCB-PLAN.md's revised
    chain (silk now precedes the pad scrub) and its stencil/reflow steps.
    Numbers come from THIS job, so the card is the job's own run sheet and
    not a boilerplate copy.
    """
    silk = job.phases["silk"]
    scrub = job.phases["scrub"]
    cut = job.phases["cutout"]
    # the card says how many tabs and, when the job steered them off the
    # default four sides, where to reach for them (pcbjob.TAB_PLACEMENTS)
    _where = pcbjob.tab_where(cut["gaps"])
    cut_where = f" ({_where})" if _where else ""

    def note(ph: str) -> str:
        return str(job.phases.get(ph, {}).get("note") or "")

    def prog(name: str, title: str, detail: str) -> dict:
        tools = tool_order.get(name, [])
        pauses = max(0, len(tools) - 1)
        d = detail
        if len(tools) > 1:
            d += (f" — {pauses} M6 pause"
                  + ("s" if pauses > 1 else "")
                  + " inside the program: "
                  + " then ".join(f"T{t}" for t in tools))
        return {"kind": "program", "program": name, "title": title,
                "detail": d, "est_s": est.get(name, 0.0),
                "file": progs[name].name if name in progs else None,
                "missing": name not in progs}

    steps: list[dict] = [
        {"kind": "setup", "title": "fixture the blank",
         "detail": f"{job.blank_w:g}x{job.blank_h:g}x{job.thickness:g} "
                   f"copper-clad, copper side UP, on FULL-coverage "
                   f"double-stick tape (a bowed blank turns every depth "
                   f"number into fiction), clamps outside the board "
                   f"rectangle. The board SW corner is G54 (0,0) and the "
                   f"blank must overhang on all four sides — the programs "
                   f"work off the board. It stays fixtured through EVERY "
                   f"step: mask and legend registration die if it moves."},
        {"kind": "setup", "title": "auto-level, then zero Z on copper",
         "detail": "probe over the board area before the first program; "
                   "Z0 = copper top. Do not re-probe Z once mask is on "
                   "top of it."},
        prog("mill", "program A — mill (isolation + copper clearing)",
             f"iso at Z{job.phases['iso']['depth']:g} with the "
             f"Ø{job.phase_tool('iso').tip_diameter:g}-tip engraver, "
             f"clearing at Z{job.phases['clear']['depth']:g}"),
        {"kind": "operator", "title": "squeegee the solder mask, cure it",
         "detail": "UV mask over the whole copper side while the board is "
                   "still in the fixture; cure with the UV lamp at the "
                   "machine. Mask guide §4, machine-tested."
                   + (f" {note('mask')}" if note("mask") else "")},
        {"kind": "operator", "title": "coat white mask for the legend",
         "detail": "thin, even coat of WHITE UV mask over the cured green — "
                   "the laser cures the legend out of this layer and the "
                   "rest wipes off. Mask guide §5: the technique is "
                   "G-code-verified but NOT yet hardware-validated; "
                   "test-fire on scrap first."},
        {"kind": "operator", "title": "fit the 455nm laser module",
         "detail": "M321 parks the spindle tool and swaps the PWM/fan "
                   "state. First use on a new setup: M323 test-fire at 1%, "
                   "jog Z until the dot is smallest (≈Z0), M324 off. The "
                   "program's own G0 Z0 is the focus law — a parked head "
                   "projects a big square and cures mask in washes."},
        prog("silk", "program B — silk legend (laser)",
             f"dose S{silk['dose']:g} at F{silk['feed']:g}, every stroke "
             f"kept ≥{silk['clearance']:g}mm clear of a solderable "
             f"aperture"),
        {"kind": "operator", "title": "wipe the uncured white off with IPA",
         "detail": "the cured legend stays. This instruction also rides in "
                   "the silk program's own header, where the operator "
                   "reads it at the machine."},
        {"kind": "operator", "title": "refit the spindle and the spring tool",
         "detail": f"T{job.phase_tool('scrub').num}, the "
                   f"Ø{job.phase_tool('scrub').diameter:g} spring mask "
                   f"removal tool. Same G54 zero as every other program — "
                   f"the board has not moved."},
        prog("scrub", "program C — scrub the mask off the pads",
             f"Z{scrub['depth']:g} of spring PRELOAD (not cut depth), "
             f"{scrub['overlap']:g}% overlap, regions deflated "
             f"{scrub['offset']:g}. If you hear buzzing on a perimeter "
             f"lap the tip is off the copper plateau: stop."
             + (f" {note('scrub')}" if note("scrub") else "")),
        prog("holes", "program D — holes and edge cut",
             f"every hole helically bored to "
             f"Z{job.phases['drills']['depth']:g} (through the "
             f"{job.thickness:g} blank into the spoilboard), then the "
             f"outline with {pcbjob.tab_count(cut['gaps'])} tabs{cut_where} "
             f"of {cut['gapsize']:g}mm"),
        {"kind": "operator", "title": "release the board, snap the tabs",
         "detail": f"{pcbjob.tab_count(cut['gaps'])} tabs{cut_where} of "
                   f"{cut['gapsize']:g}mm hold "
                   f"it; snap and file the stubs. The cut rides a tool "
                   f"radius OUTSIDE the outline ink, so the board comes "
                   f"out one Edge.Cuts line width oversize per side "
                   f"(measured and accepted 2026-07-19)."},
        {"kind": "offmachine", "title": "stencil, paste, reflow the SMD side",
         "detail": ("send the B.Paste gerber to stenchill.com, print the "
                    "stencil at 0.3-0.4mm in PLA/PETG with a 0.2mm nozzle, "
                    "squeegee paste, place, hotplate reflow."
                    if paste else
                    "NO B.Paste gerber is exported for this board, so "
                    "there is no stencil artwork — export the paste layer "
                    "before promising a stencil step.")},
        {"kind": "offmachine", "title": "THT from the front, then test",
         "detail": "solder order: reflowed SMD on the copper side first, "
                   "then through-hole parts inserted from the front and "
                   "soldered on the copper side, wires last. Then power it "
                   "and confirm it works — firmware/function is the last "
                   "verification the gate cannot do."},
    ]
    for i, s in enumerate(steps, 1):
        s["n"] = i
    return steps


def run_sheet_twosided(job: PcbJob, sides: dict[str, PcbJob],
                       progs: dict[str, dict[str, Path]],
                       est: dict[str, float],
                       tool_order: dict[str, list[int]],
                       paste: bool) -> list[dict]:
    """The bench workflow of a board that FLIPS: both setups, in ONE order.

    Same card, same step kinds and the same numbers-from-this-job rule as the
    single-sided sheet above; what is new is the sequence, and the sequence is
    not a choice. Provenance: boards/orbit/SPEC.md "Assembly / run-sheet order"
    steps 1-11 (side A phases 1-5 → ALL through-holes → the pin bores → set
    pins, flip about the axis, deburr the back, re-level → side B phases 1-5,
    with the flip gauges read after side B's iso and BEFORE its mask squeegee →
    the cutout, last of everything → off-machine), reemit._SIDE_STEPS (the same
    steps compressed into the program headers, so the card and the bytes the
    operator posts cannot disagree), pcbjob.py's grammar law that `drills`
    belongs to side 1 and `cutout` to side 2 only, and the guides the
    single-sided card cites for the mask/legend/scrub cycle it repeats twice.

    `est` and `tool_order` are keyed `<side>/<program>`, the same keys the
    sessions carry, so a step points at the session that machines it.
    """
    first, second = pcbjob.SIDE_ORDER
    cut = sides[second].phases["cutout"]
    _where = pcbjob.tab_where(cut["gaps"])
    cut_where = f" ({_where})" if _where else ""
    n_pins = len(job.pins["positions"])

    def note(side: str, ph: str) -> str:
        return str(sides[side].phases.get(ph, {}).get("note") or "")

    def prog(side: str, name: str, title: str, detail: str) -> dict:
        key = f"{side}/{name}"
        letter = "ABCDEFGH"[list(pcbjob.programs_of(sides[side])).index(name)]
        tools = tool_order.get(key, [])
        pauses = max(0, len(tools) - 1)
        d = detail
        if len(tools) > 1:
            d += (f" — {pauses} M6 pause"
                  + ("s" if pauses > 1 else "")
                  + " inside the program: "
                  + " then ".join(f"T{t}" for t in tools))
        p = progs.get(side, {}).get(name)
        return {"kind": "program", "program": key,
                "title": f"{side} program {letter} — {title}",
                "detail": d, "est_s": est.get(key, 0.0),
                "file": Path(p).name if p else None, "missing": p is None}

    def cycle(side: str) -> list[dict]:
        """The mask → legend → scrub cycle, which every setup runs (the
        operator's revised chain: silk BEFORE the pad scrub)."""
        j = sides[side]
        silk, scrub = j.phases["silk"], j.phases["scrub"]
        face = side.upper()
        out = [
            prog(side, "mill", "mill (isolation + copper clearing)",
                 f"iso at Z{j.phases['iso']['depth']:g} with the "
                 f"Ø{j.phase_tool('iso').tip_diameter:g}-tip engraver, "
                 f"clearing at Z{j.phases['clear']['depth']:g} — this side's "
                 f"own depths and feeds"),
        ]
        if side == second and job.rules.get("gauge"):
            # the registration measurement, and it has exactly one window:
            # after side 2's iso cut the rings, before the mask hides them
            out.append({
                "kind": "operator",
                "title": "read the flip gauges, write the numbers down",
                "detail": f"after side 2's iso and BEFORE the mask goes on "
                          f"(SPEC 'Assembly' step 6): read the "
                          f"{len(job.rules['gauge'])} flip gauges with a "
                          f"loupe — an even ring means a perfect flip, and "
                          f"an eccentric one measures the registration error "
                          f"this board exists to measure. Nothing downstream "
                          f"can recover it once the mask covers the copper."})
        out += [
            {"kind": "operator",
             "title": f"squeegee the solder mask on the {face}, cure it",
             "detail": f"UV mask over the whole {face} copper face while the "
                       f"board is still in the fixture; cure with the UV lamp "
                       f"at the machine. Mask guide §4, machine-tested."
                       + (f" {note(side, 'mask')}" if note(side, "mask")
                          else "")},
            {"kind": "operator", "title": "coat white mask for the legend",
             "detail": "thin, even coat of WHITE UV mask over the cured "
                       "green — the laser cures the legend out of this layer "
                       "and the rest wipes off. Mask guide §5: the technique "
                       "is G-code-verified but NOT yet hardware-validated; "
                       "test-fire on scrap first."},
            {"kind": "operator", "title": "fit the 455nm laser module",
             "detail": "M321 parks the spindle tool and swaps the PWM/fan "
                       "state. The program's own G0 Z0 is the focus law — a "
                       "parked head projects a big square and cures mask in "
                       "washes."},
            prog(side, "silk", "silk legend (laser)",
                 f"dose S{silk['dose']:g} at F{silk['feed']:g}, every stroke "
                 f"kept ≥{silk['clearance']:g}mm clear of a solderable "
                 f"aperture"
                 + (" — strokes over copper fire twice, back to back "
                    "(the 2026-08-03 dose ladder: copper heat-sinks the "
                    "cure)" if int(silk.get("copper_passes", 1)) > 1
                    else "")),
            {"kind": "operator",
             "title": "wipe the uncured white off with IPA",
             "detail": "the cured legend stays. This instruction also rides "
                       "in the silk program's own header, where the operator "
                       "reads it at the machine."},
            {"kind": "operator",
             "title": "refit the spindle and the spring tool",
             "detail": f"T{j.phase_tool('scrub').num}, the "
                       f"Ø{j.phase_tool('scrub').diameter:g} spring mask "
                       f"removal tool. Same G54 zero — the board has not "
                       f"moved within this setup."},
            prog(side, "scrub", "scrub the mask off the pads",
                 f"Z{scrub['depth']:g} of spring PRELOAD (not cut depth), "
                 f"{scrub['overlap']:g}% overlap, regions deflated "
                 f"{scrub['offset']:g}."
                 + (f" The holes are all there by now, so every hole-centred "
                    f"pad gets an ANNULAR lap, never a disc — a "
                    f"Ø{j.phase_tool('scrub').diameter:g} tip spiralling "
                    f"across a bore drops in and levers the pad off."
                    if side == second else
                    " No holes in the blank yet, so these are ordinary disc "
                    "laps.")
                 + (f" {note(side, 'scrub')}" if note(side, "scrub") else "")),
        ]
        return out

    steps: list[dict] = [
        {"kind": "setup", "title": f"fixture the blank, {first.upper()} "
                                   f"copper UP",
         "detail": f"{job.blank_w:g}x{job.blank_h:g}x{job.thickness:g} "
                   f"copper-clad on FULL-coverage double-stick tape (a bowed "
                   f"blank turns every depth number into fiction), clamps in "
                   f"the WASTE and clear of the pin holes. The board SW "
                   f"corner is G54 (0,0) and the blank must overhang on all "
                   f"four sides — the programs work off the board, and the "
                   f"{n_pins} registration pins land in the waste, outside "
                   f"the outline."},
        {"kind": "setup",
         "title": f"auto-level, then zero Z on {first.upper()} copper",
         "detail": "probe over the board area before the first program; "
                   "Z0 = this side's copper top. Do not re-probe Z once mask "
                   "is on top of it."},
    ]
    steps += cycle(first)
    steps += [
        prog(first, "holes", "every through-hole, bored from side 1",
             f"every hole helically bored to "
             f"Z{sides[first].phases['drills']['depth']:g} (through the "
             f"{job.thickness:g} blank into the spoilboard). Side 1 bores "
             f"them ALL: both artworks then reference the same physical "
             f"holes, so flip accuracy equals pin-to-hole clearance and no "
             f"via is ever bored twice from two frames"),
        prog(first, "pins", "the registration pin bores",
             f"{n_pins}x Ø{job.pins['diameter']:g} spot-faced to "
             f"Z{sides[first].phases['pinspot']['depth']:g} then pecked to "
             f"Z{sides[first].phases['pindrill']['depth']:g}, into the "
             f"spoilboard — the LAST thing cut on side 1, in the same setup, "
             f"so the pins inherit this side's zero and the flip inherits the "
             f"pins"),
        {"kind": "operator",
         "title": f"set the pins, FLIP about {job.flip_axis.upper()}, "
                  f"deburr, re-level",
         "detail": f"set the {n_pins} dowels in the bores just cut, lift the "
                   f"blank, DEBURR THE BACK by hand (scotchbrite — the "
                   f"drill's exit burr lives there and a burr under tape "
                   f"bows the blank), turn it about the "
                   f"{job.flip_axis.upper()} axis onto the pins, re-tape FULL "
                   f"coverage. Re-level and re-touch Z0 on the "
                   f"{second.upper()} copper — and confirm no probe point "
                   f"sits in a drilled hole, which would write a false low "
                   f"into the height map. NEVER re-zero XY: it is the "
                   f"registration the holes bought."},
    ]
    steps += cycle(second)
    steps += [
        prog(second, "holes", "the outline cut with tabs",
             f"the outline with {pcbjob.tab_count(cut['gaps'])} tabs"
             f"{cut_where} of {cut['gapsize']:g}mm, at "
             f"Z{cut['depth']:g} — side 2 only, and last of everything"),
        {"kind": "operator", "title": "release the board, snap the tabs",
         "detail": f"{pcbjob.tab_count(cut['gaps'])} tabs{cut_where} of "
                   f"{cut['gapsize']:g}mm hold it; snap, file the stubs and "
                   f"deburr. The cut rides a tool radius OUTSIDE the outline "
                   f"ink, so the board comes out one Edge.Cuts line width "
                   f"oversize per side (measured and accepted 2026-07-19)."},
        {"kind": "offmachine", "title": "stencil, paste, reflow the BACK",
         "detail": ("send the B.Paste gerber to stenchill.com, print the "
                    "stencil at 0.3-0.4mm in PLA/PETG with a 0.2mm nozzle, "
                    "squeegee paste, place, hotplate reflow. One stencil, "
                    "back side."
                    if paste else
                    "NO paste gerber is exported for this board, so there is "
                    "no stencil artwork — export the paste layer before "
                    "promising a stencil step.")},
        {"kind": "offmachine", "title": "wire vias — after reflow, never "
                                        "before",
         "detail": "insert, solder both faces, clip flush. A wire standing "
                   "off the back holds the board off the hotplate, so the "
                   "vias go in after the reflow, not before (SPEC 'Assembly' "
                   "step 10)."},
        {"kind": "offmachine", "title": "THT from the FRONT, then flash and "
                                        "power up",
         "detail": "through-hole parts inserted from the front with their "
                   "leads soldered on the back, every dual-solder lead "
                   "soldered on the front too; then flash and power it. "
                   "Firmware/function is the last verification the gate "
                   "cannot do."},
    ]
    for i, s in enumerate(steps, 1):
        s["n"] = i
    return steps


# ------------------------------------------------------------- the sessions
@dataclass
class PcbSession:
    """One pushable viewer session: the payload trio every other session
    pushes (meta, per-stage stock grids, the verified program bytes)."""
    name: str
    path: str
    meta: dict
    stocks: list[np.ndarray]
    program: bytes


def _laser_stage(job: PcbJob, text: str) -> tuple[list[dict], float]:
    """The silk program's one stage. NOT carve facts — there is no carve —
    so the dict says so with `overlay: True` and carries only what the bytes
    actually support."""
    polys, mm = laser_strokes(text)
    silk = job.phases["silk"]
    est = mm / max(float(silk["feed"]), 1.0) * 60.0
    return ([{"index": 0, "label": "pcb-silk (laser)", "overlay": True,
              "tool": None, "tools": [], "tool_desc": "455nm laser module",
              "moves": len(polys), "cut_mm": mm, "rapid_mm": 0.0,
              "est_s": est, "volume_mm3": 0.0, "min_z": 0.0,
              "max_feed": float(silk["feed"]),
              "dose_s": float(silk["dose"]),
              "note": "cures white mask; removes no material"}], est)


def _scrub_stage(job: PcbJob, sj: SheetJob,
                 nc_path) -> tuple[list[dict], float]:
    polys, mm, zmin = cutting_polylines(sj, nc_path)
    p = job.phases["scrub"]
    tool = job.phase_tool("scrub")
    est = mm / max(float(p["feed"]), 1.0) * 60.0
    return ([{"index": 0, "label": "pcb-scrub", "overlay": True,
              "tool": tool.num, "tools": [tool.num],
              "tool_desc": f"{tool.type} Ø{tool.diameter:g}",
              "moves": len(polys), "cut_mm": mm, "rapid_mm": 0.0,
              "est_s": est, "volume_mm3": 0.0, "min_z": zmin,
              "max_feed": float(p["feed"]),
              "note": "commanded Z is spring preload; the kernel removes "
                      "nothing (Article IX exemption)"}], est)


@dataclass
class _Facts:
    """What one SETUP's programs measured, before any session exists: the run
    sheet needs the estimates and the tool order, and every session in the
    document carries the same card."""
    carves: dict[str, simulate.CarveResult]
    stats: dict[str, list[dict]]
    est: dict[str, float]
    tool_order: dict[str, list[int]]
    texts: dict[str, str]


@dataclass
class _Setup:
    """One setup's state. A single-sided document has one; a flipped one has
    two, and `job` is a pcbjob.side_view — which is why everything below
    reads the same in both cases."""
    job: PcbJob
    sheet: SheetStock
    sj: SheetJob
    progs: dict[str, Path]
    reports: dict[str, Report]
    facts: _Facts


@dataclass
class _Doc:
    """Document-level state every session repeats: whose board this is, why
    the gate did or did not run, the one run-sheet card, the chain estimate
    and the whole program list."""
    board: str
    gate_note: str
    card: list[dict]
    chain_est: float
    programs: list[dict]


def _program_facts(job: PcbJob, sheet: SheetStock, sj: SheetJob,
                   progs: dict[str, Path],
                   reports: dict[str, Report]) -> _Facts:
    """Measure every program of ONE setup: stage stats, estimate, tool order.

    The CARVING programs serve the gate's OWN simulation when it ran (its
    Report carries the CarveResult), so the picture and the verdict can never
    drift apart; the overlay-only pair is measured from the same bytes."""
    carves: dict[str, simulate.CarveResult] = {}
    stats: dict[str, list[dict]] = {}
    est: dict[str, float] = {}
    tool_order: dict[str, list[int]] = {}
    texts: dict[str, str] = {}
    for name, path in progs.items():
        texts[name] = Path(path).read_text()
        if name in CARVING:
            # the gate already simulated this program (verify_pcb carries the
            # CarveResult on its Report since it adopted the sheet checks) —
            # serve the SAME simulation it judged rather than running a
            # second one that could drift from the verdict
            rep = reports.get(name)
            res = (rep.carve if rep is not None and rep.carve is not None
                   else carve_program(sj, sheet, path))
            outside = sheet.outside_min(res.stock)
            if outside < -1e-6:
                raise ValueError(
                    f"{path}: the program removes material outside the "
                    f"modelled sheet window ({outside:.3f}mm deep) — the "
                    f"cropped preview would hide a cut; widen the window "
                    f"or fix the toolpath")
            carves[name] = res
            stats[name] = stagesmod.stage_stats(sj, res)
            m = res.metrics
            seen: list[int] = []
            for t in m.tool_num:
                if int(t) not in seen:
                    seen.append(int(t))
            tool_order[name] = seen
        elif name == "silk":
            stats[name], _ = _laser_stage(job, texts[name])
        else:
            stats[name], _ = _scrub_stage(job, sj, path)
            tool_order[name] = [job.phase_tool("scrub").num]
        est[name] = sum(s["est_s"] for s in stats[name])
    return _Facts(carves=carves, stats=stats, est=est, tool_order=tool_order,
                  texts=texts)


def _session(doc: _Doc, st: _Setup, name: str, key: str) -> PcbSession:
    """One program's session. `key` is what the operator and the viewer call
    it — the program name on a single-sided job, `<side>/<program>` on a
    flipped one — and `name` is always the program within its own split."""
    job, sheet, sj = st.job, st.sheet, st.sj
    path = Path(st.progs[name]).resolve()
    rep = st.reports.get(name)
    chk = list(rep.checks) if rep else []
    res = st.facts.carves.get(name)
    if res is not None and rep is None:
        # the gateless path (no gerbv): the gate's report would already
        # carry the sheet checks; without it the session still shows
        # them — with ok = None below, never as a verdict
        chk += sheet_checks(job, sheet, sj, res)
    # a verdict ONLY when the gate itself ran: the sheet sim is an
    # ADDITION to the PCB gate, never a substitute for it, so a session
    # whose board maps never loaded stays UNVERIFIED however clean its
    # own simulation came out (Article I).
    ok = all(c.ok for c in chk) if rep is not None else None
    stocks = [sheet.crop(g) for g in res.stage_stocks] if res else []
    meta = {
        "kind": "pcb",
        "job": f"{doc.board} {key}",
        "board": doc.board,
        "program": key,
        "phases": list(pcbjob.programs_of(job)[name]),
        "path": str(path),
        "toml": str(job.path),
        "nc": path.name,
        "ok": ok,
        "gate": {"ran": rep is not None,
                 "verdict": ("PASS" if rep and rep.ok else "FAIL"
                             if rep else "not run"),
                 "note": doc.gate_note,
                 "sheet_sim": res is not None},
        "carves": res is not None,
        "n": sheet.n, "ppm": sheet.ppm, "half": sheet.half,
        "nx": sheet.nx, "ny": sheet.ny,
        "i_off": sheet.i_off, "j_off": sheet.j_off,
        "sheet": sheet.as_meta(),
        "sim": {"ppm": sheet.ppm, "mm_per_px": 1.0 / sheet.ppm,
                "virgin": True,
                "note": "each program simulates on a VIRGIN sheet — "
                        "carrying stock across programs needs an "
                        "initial-stock kernel parameter that does not "
                        "exist yet; more stock than reality is the "
                        "conservative reading"},
        "material": job.material["name"],
        "machine": job.machine["name"],
        "stock_size": 2 * sheet.half,
        "stock_thickness": sheet.thickness,
        "total_est_s": st.facts.est.get(name, 0.0),
        "chain_est_s": doc.chain_est,
        "stages": st.facts.stats.get(name, []),
        "tools": stagesmod.tool_cards(sj, res, st.facts.stats.get(name, []))
        if res else [],
        "checks": [{"name": c.name, "value": c.value, "limit": c.limit,
                    "ok": c.ok, "detail": c.detail} for c in chk],
        "overlay": overlay_for(job, name, st.facts.texts[name], path, sj)
        if name in OVERLAY_ONLY else None,
        "run_sheet": doc.card,
        "programs": doc.programs,
    }
    if job.side:
        # only a flipped document says which setup this is: the single-sided
        # payload must not grow a key (Board A's sessions do not move)
        meta["side"] = job.side
    body = (rep.program if rep and rep.program else st.facts.texts[name])
    return PcbSession(name=key, path=str(path), meta=meta,
                      stocks=stocks, program=body.encode())


def build(job: PcbJob,
          programs: dict[str, Path] | dict[str, dict[str, Path]] | None = None,
          gate: bool = True, ppm: float = SHEET_PPM,
          dpi: int | None = None) -> list[PcbSession]:
    """Build the viewer sessions of a [pcb] document: four for a single-sided
    job, one per program of the canonical split.

    `gate` runs checks.verify_pcb over the board maps (needs gerbv) and
    folds each program's Report into its session, so a session's PASS badge
    is the gate's verdict and nothing else. Without it — no gerbv on the box
    — the sessions still show the sheet sim, the overlays and the run sheet,
    with `ok = None` and a stated reason: a session that cannot verify says
    so instead of showing a green badge (Article I).

    A DOUBLE-SIDED document composes its two setups into ONE list here
    (build_twosided): nine sessions for orbit, named `<side>/<program>`, plus
    the `board` session that carries the document's own artwork checks. Pass
    `programs` as {side: {program: path}} for it, the shape
    flip.verify_twosided takes.
    """
    if job.twosided:
        return build_twosided(job, programs=programs, gate=gate, ppm=ppm,
                              dpi=dpi)
    sheet = sheet_stock(job, ppm=ppm)
    sj = sheet_job(job, sheet)
    progs = programs if programs is not None else program_paths(job)
    missing = [n for n in checks.PROGRAM_PHASES if n not in progs]

    reports: dict[str, Report] = {}
    gate_note = ""
    if gate and not missing:
        try:
            reports = checks.verify_pcb(job, progs, dpi=dpi)
        except FileNotFoundError as e:          # no gerbv: boardmaps says so
            gate_note = str(e)
        except (ValueError, RuntimeError) as e:
            gate_note = f"the PCB gate could not run: {e}"
    elif missing:
        gate_note = (f"programs {missing} are not on disk — generate the "
                     f"whole split before the gate can judge it")
    elif not gate:
        gate_note = "the PCB gate was not run for this session"

    facts = _program_facts(job, sheet, sj, progs, reports)
    paste = (job.gerber_dir / f"{job.stem}-B_Paste.gbr").is_file()
    card = run_sheet(job, progs, facts.est, facts.tool_order, paste)
    st = _Setup(job=job, sheet=sheet, sj=sj, progs=progs, reports=reports,
                facts=facts)
    doc = _Doc(board=job.name, gate_note=gate_note, card=card,
               chain_est=sum(facts.est.values()),
               programs=[{"name": n, "nc": Path(p).name,
                          "est_s": facts.est.get(n, 0.0)}
                         for n, p in progs.items()])
    return [_session(doc, st, name, name)
            for name in checks.PROGRAM_PHASES if name in progs]


def _board_session(job: PcbJob, doc: _Doc, rep: Report | None,
                   sheet: SheetStock) -> PcbSession:
    """The document's own session: the checks that belong to no program.

    flip.board_checks judges the ARTWORK (both coppers in one window, the
    frame, every hole's annular ring, paste against the hole schedule) — a
    verdict on the board itself, which no single program can carry and which
    would therefore be the one thing the operator never sees. It is keyed
    `<toml>#board` in twosided.py's `#side` spelling, so it joins the list
    without colliding with the document's own placeholder session."""
    path = job.path.with_name(job.path.name + "#board")
    chk = list(rep.checks) if rep else []
    meta = {
        "kind": "pcb",
        "job": f"{doc.board} board",
        "board": doc.board,
        "program": "board",
        "phases": [],
        "path": str(path),
        "toml": str(job.path),
        "nc": "",
        "ok": (all(c.ok for c in chk) if rep is not None else None),
        "gate": {"ran": rep is not None,
                 "verdict": ("PASS" if rep and rep.ok else "FAIL"
                             if rep else "not run"),
                 "note": doc.gate_note,
                 "sheet_sim": False},
        "carves": False,
        "n": sheet.n, "ppm": sheet.ppm, "half": sheet.half,
        "nx": sheet.nx, "ny": sheet.ny,
        "i_off": sheet.i_off, "j_off": sheet.j_off,
        "sheet": sheet.as_meta(),
        "sides": list(job.sides),
        "material": job.material["name"],
        "machine": job.machine["name"],
        "stock_size": 2 * sheet.half,
        "stock_thickness": sheet.thickness,
        "total_est_s": 0.0,
        "chain_est_s": doc.chain_est,
        "stages": [],
        "tools": [],
        "checks": [{"name": c.name, "value": c.value, "limit": c.limit,
                    "ok": c.ok, "detail": c.detail} for c in chk],
        "overlay": None,
        "run_sheet": doc.card,
        "programs": doc.programs,
        "note": "the DOCUMENT's artwork report: both coppers in one raster "
                "window, the derived mirror line, every hole's annular ring "
                "and the paste layer against the hole schedule. It carves "
                "nothing — there is no stock preview here because there is "
                "no program. The board rectangle drawn is the same in both "
                "setups (the flip is about its own centreline); the sheet "
                "window around it is side 1's.",
    }
    return PcbSession(name="board", path=str(path), meta=meta, stocks=[],
                      program=b"")


def build_twosided(job: PcbJob,
                   programs: dict[str, dict[str, Path]] | None = None,
                   gate: bool = True, ppm: float = SHEET_PPM,
                   dpi: int | None = None) -> list[PcbSession]:
    """The session list of a pin-and-flip [pcb] document, in MACHINING order.

    Each setup is a pcbjob.side_view — a job shaped like a single-sided one —
    so the sheet model, the stage stats, the overlays and the sessions are
    built by the same helpers the single-sided path uses, with the side's own
    artwork, its own mirror and its own phase table. What is composed here is
    only the list: the artwork report first, then side 1's five programs, then
    side 2's four, each keyed `<side>/<program>` (flip.verify_twosided's key,
    tools-cam.py's file name) and each pointing at its own .nc in out/.

    The gate is flip.verify_twosided, never four separate verify_pcb calls:
    the cross-side checks (annular scrub, tab-zone copper, the frame) exist
    precisely because a side judged alone is not judged.
    """
    sides = {side: pcbjob.side_view(job, side) for side in job.sides}
    progs = (programs if programs is not None
             else {side: program_paths(sv) for side, sv in sides.items()})
    missing = [f"{side}/{n}" for side, sv in sides.items()
               for n in pcbjob.programs_of(sv)
               if n not in progs.get(side, {})]

    reports: dict[str, Report] = {}
    gate_note = ""
    if gate and not missing:
        try:
            reports = flip.verify_twosided(job, progs, dpi=dpi, ppm=ppm)
        except FileNotFoundError as e:          # no gerbv: boardmaps says so
            gate_note = str(e)
        except (ValueError, RuntimeError) as e:
            gate_note = f"the PCB gate could not run: {e}"
    elif missing:
        gate_note = (f"programs {missing} are not on disk — generate BOTH "
                     f"setups before the gate can judge the document")
    elif not gate:
        gate_note = "the PCB gate was not run for this session"

    setups: dict[str, _Setup] = {}
    est: dict[str, float] = {}
    tool_order: dict[str, list[int]] = {}
    for side, sv in sides.items():
        sheet = sheet_stock(sv, ppm=ppm)
        sj = sheet_job(sv, sheet)
        mine = progs.get(side, {})
        sreps = {n: reports[f"{side}/{n}"] for n in mine
                 if f"{side}/{n}" in reports}
        facts = _program_facts(sv, sheet, sj, mine, sreps)
        setups[side] = _Setup(job=sv, sheet=sheet, sj=sj, progs=mine,
                              reports=sreps, facts=facts)
        est.update({f"{side}/{n}": v for n, v in facts.est.items()})
        tool_order.update({f"{side}/{n}": v
                           for n, v in facts.tool_order.items()})

    paste_p = job.gerber_dir / f"{job.stem}{pcbjob.PASTE_SUFFIX}"
    paste = "paste" in job.files or paste_p.is_file()
    card = run_sheet_twosided(job, sides, progs, est, tool_order, paste)
    doc = _Doc(board=job.name, gate_note=gate_note, card=card,
               chain_est=sum(est.values()),
               programs=[{"name": f"{side}/{n}", "nc": Path(p).name,
                          "est_s": est.get(f"{side}/{n}", 0.0)}
                         for side in job.sides
                         for n, p in progs.get(side, {}).items()])

    out = [_board_session(job, doc, reports.get("board"),
                          setups[job.sides[0]].sheet)]
    for side in job.sides:
        st = setups[side]
        out += [_session(doc, st, name, f"{side}/{name}")
                for name in pcbjob.programs_of(st.job) if name in st.progs]
    return out


def report_text(sessions: list[PcbSession]) -> str:
    """CLI/MCP text for a whole [pcb] document — one block per program,
    verdicts first, then the sheet-sim stage lines."""
    lines: list[str] = []
    ok_all = True
    for s in sessions:
        bad = [c for c in s.meta["checks"] if not c["ok"]]
        gate = s.meta["gate"]
        verdict = ("PASS" if s.meta["ok"] else "FAIL" if s.meta["ok"]
                   is False else "UNVERIFIED")
        ok_all &= s.meta["ok"] is True
        what = (f"artwork {s.name} (the whole document)" if s.name == "board"
                else f"program {s.name} ({s.meta['nc']})")
        lines.append(f"=== {what} — {verdict} "
                     f"[{len(s.meta['checks'])} checks, gate "
                     f"{gate['verdict']}]")
        if gate["note"]:
            lines.append(f"  ! {gate['note']}")
        for c in bad:
            lines.append(f"  FAIL {c['name']}: {c['value']:.3f} "
                         f"({c['limit']}) {c['detail']}")
        for st in s.meta["stages"]:
            lines.append(f"  stage {st['index'] + 1} {st['label']}: "
                         f"~{_fmt(st['est_s'])} est, "
                         f"{st['volume_mm3']:.0f}mm³ removed"
                         + (" (overlay only, nothing carved)"
                            if st.get("overlay") else ""))
    both = any(s.name == "board" for s in sessions)   # a flipped document
    lines.append("PCB VERDICT: "
                 + (("PASS — the artwork and every program of both setups "
                     "cleared" if both else "PASS — every program cleared")
                    if ok_all else
                    "NOT CLEARED — do NOT cut this board"))
    return "\n".join(lines)
