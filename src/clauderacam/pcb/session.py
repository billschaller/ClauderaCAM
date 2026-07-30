"""[pcb] viewer sessions (PCB-PLAN.md WS6): a PCB job in front of human eyes.

A PCB job is FOUR sessions, one per program of the canonical split — the
same shape twosided.py uses for its two sides (sessions are keyed by the
artifact the operator posts, and a [pcb] document posts four files). Each
session carries what every other session carries: a stage list recovered
from the program's own `(begin operation: ...)` markers, per-stage stats,
the gate's checks, and the verified bytes to download.

Two things are new here, and both exist because half this chain does not
carve:

  THE SHEET STOCK MODEL (the WS5 debt, paid).  `verify.verify()` needs a
  target mesh and a square coin blank; a PCB has neither. What the
  geometric machinery actually needs is smaller: a stock plane, a grid,
  and a tool table. `sheet_stock()` defines it — thickness from the
  blank, XY window DERIVED from the Edge.Cuts board window in machine
  frame (boardmaps.machine_offset, the 154/124 law) grown by the widest
  off-board reach the job configures, exactly the way checks.board_maps
  pads its raster window. With that, the MILL and HOLES programs ride
  simulate.carve() unchanged: real per-move volume, contact, engagement,
  shank clearance, rapid-vs-stock and depth-vs-spoilboard on the actual
  bytes — the checks checks.py's docstring lists as "deliberately not
  checked here ... needs a thin-sheet stock model the [pcb] grammar does
  not build yet". They are ADDED to what the PCB gate already proves;
  nothing is relaxed to make room for them.

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
from ..physics import physics_checks
from ..verify import Check, Report, contact_limit
from . import boardmaps, checks
from .pcbjob import PcbJob

# The sheet sim runs at the gate's own resolution — carve_check's 12.5
# px/mm — so a [pcb] number and a mill number mean the same thing.
SHEET_PPM = 12.5
# Window pad: the same rule checks.board_maps uses for its raster window
# (CHECK_PAD_BASE + the widest off-board reach the job configures). Board A
# measured: mill spans -0.400..55.400 / -0.580..40.400 and holes
# -0.550..55.550 / -0.550..40.550 on a 55x40 board, so the reach is real.
SHEET_PAD_BASE = checks.CHECK_PAD_BASE

# Which programs of the split carve, and which are overlay-only. This is not
# a preference: `silk` is a laser program (simulate.parse_line refuses M321
# by law) and `scrub` drives a tool whose kernel footprint is empty.
CARVING = ("mill", "holes")
OVERLAY_ONLY = ("silk", "scrub")

_LASER_XY = re.compile(r"^(G0?[01])\b")


def is_pcb(path) -> bool:
    """Is this TOML a [pcb] document? (viewer/server.py dispatches on it the
    same way it dispatches on twosided.is_twosided)."""
    try:
        with open(path, "rb") as f:
            return "pcb" in tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return False


# --------------------------------------------------------------- sheet stock
@dataclass(frozen=True)
class SheetStock:
    """The thin-sheet stock model of a [pcb] job, in MACHINE frame.

    (x0,y0)..(x1,y1) is the modelled sheet: the board window grown by `pad`.
    (bx0,by0)..(bx1,by1) is the board itself. `half`/`n`/`ppm` are the carve
    grid (Article IV's square, centred on the machine origin); i_off/j_off
    and nx/ny are the crop of that grid down to the modelled sheet."""
    x0: float
    y0: float
    x1: float
    y1: float
    bx0: float
    by0: float
    bx1: float
    by1: float
    pad: float
    thickness: float
    spoil: float
    ppm: float
    half: float
    n: int
    i_off: int
    j_off: int
    nx: int
    ny: int

    def crop(self, grid: np.ndarray) -> np.ndarray:
        return grid[self.i_off:self.i_off + self.ny,
                    self.j_off:self.j_off + self.nx]

    def outside_min(self, grid: np.ndarray) -> float:
        """Lowest stock value anywhere OUTSIDE the crop. 0.0 means the crop
        holds every cut the program made — which is what makes serving the
        crop instead of the whole grid honest rather than convenient."""
        i1, j1 = self.i_off + self.ny, self.j_off + self.nx
        bands = [grid[:self.i_off, :], grid[i1:, :],
                 grid[self.i_off:i1, :self.j_off], grid[self.i_off:i1, j1:]]
        return min([float(b.min()) for b in bands if b.size] or [0.0])

    def as_meta(self) -> dict:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1,
                "board": [self.bx0, self.by0, self.bx1, self.by1],
                "pad": self.pad, "thickness": self.thickness,
                "spoil": self.spoil}


def sheet_stock(job: PcbJob, ppm: float = SHEET_PPM) -> SheetStock:
    """DERIVE the sheet stock of a [pcb] job. No hand-typed geometry: the
    board window comes from the Edge.Cuts coordinate words and the transform
    from boardmaps.machine_offset, the same pair the templated Tcl and the
    gate both use.

    The arc cross-check in boardmaps.extents is the GATE's job (board_maps
    runs it with gerbv); this window is derived without it so a session can
    still show the sheet on a box with no gerbv, where the gate itself
    refuses to run and the session says so.
    """
    tight = boardmaps.extents(job.files["edge"], cross_check=False)
    dx, dy = boardmaps.machine_offset(tight, job.anchor)
    # single-sided back copper: mirror x then offset (checks.BoardMaps.to_board
    # is the inverse of exactly this)
    bx0, bx1 = dx - tight.x1, dx - tight.x0
    by0, by1 = tight.y0 + dy, tight.y1 + dy
    pad = SHEET_PAD_BASE + max(float(job.phases["clear"]["margin"]),
                               job.phase_tool("cutout").diameter)
    x0, y0, x1, y1 = bx0 - pad, by0 - pad, bx1 + pad, by1 + pad
    half = max(abs(x0), abs(x1), abs(y0), abs(y1))
    n = int(half * 2 * ppm)
    # the crop, in the ONE mapping (simulate.py's docstring, Article IV):
    # i = round((half - y) * ppm), j = round((x + half) * ppm)
    i_off = max(0, int(round((half - y1) * ppm)))
    j_off = max(0, int(round((x0 + half) * ppm)))
    i_hi = min(n, int(round((half - y0) * ppm)) + 1)
    j_hi = min(n, int(round((x1 + half) * ppm)) + 1)
    return SheetStock(x0=x0, y0=y0, x1=x1, y1=y1, bx0=bx0, by0=by0,
                      bx1=bx1, by1=by1, pad=pad, thickness=job.thickness,
                      spoil=job.spoil_thickness, ppm=ppm, half=half, n=n,
                      i_off=i_off, j_off=j_off,
                      nx=j_hi - j_off, ny=i_hi - i_off)


@dataclass
class SheetJob:
    """A [pcb] job wearing just enough of Job's shape for the simulation
    machinery: simulate.carve needs stock_half + tools + machine, and
    stages.stage_stats / physics.physics_checks need material + tool().

    Deliberately NOT a Job: there is no model mesh, no keep-out disc and no
    skim plane on a PCB, so verify.verify() (which needs all three) must not
    be callable on one by accident. The PCB gate is checks.verify_pcb; this
    type carries the stock simulation to it, it does not replace it."""
    path: Path
    name: str
    out: Path
    stock_size: float
    stock_thickness: float
    spoil_thickness: float
    material: dict
    machine: dict
    tools: dict = field(default_factory=dict)

    @property
    def stock_half(self) -> float:
        return self.stock_size / 2.0

    def tool(self, num):
        return self.tools[num]


def sheet_job(job: PcbJob, sheet: SheetStock) -> SheetJob:
    return SheetJob(path=job.path, name=job.name, out=job.out_dir,
                    stock_size=2 * sheet.half,
                    stock_thickness=sheet.thickness,
                    spoil_thickness=sheet.spoil, material=job.material,
                    machine=job.machine, tools=job.tools)


def program_paths(job: PcbJob) -> dict[str, Path]:
    """{program: path} for the canonical split. The engine writes into
    `out_dir`; a blessed asset set (tests/golden_pcb) keeps the programs
    beside the TOML — try both, and report what is missing rather than
    inventing a path."""
    out: dict[str, Path] = {}
    for name in checks.PROGRAM_PHASES:
        for cand in (job.out_dir / f"{job.stem}-{name}.nc",
                     job.path.parent / f"{job.stem}-{name}.nc"):
            if cand.is_file():
                out[name] = cand
                break
    return out


# ------------------------------------------------------------- the sheet sim
def carve_program(sj: SheetJob, sheet: SheetStock,
                  nc_path) -> simulate.CarveResult:
    """Simulate one MILL-dialect [pcb] program on the sheet. Same strict
    parser, same kernel, same measurements as any other job — the whole point
    of defining the sheet stock was to stop needing a special path."""
    return simulate.carve(nc_path, sj, ppm=sheet.ppm, extra_half=0.0,
                          step=0.06, check=True)


def sheet_checks(job: PcbJob, sheet: SheetStock, sj: SheetJob,
                 res: simulate.CarveResult) -> list[Check]:
    """The geometric + physics checks the PCB gate could not run before the
    sheet existed (checks.py's "deliberately not checked here" list). Named
    with a `sheet ` prefix so a PCB report never confuses them with the
    board-map checks, and every one of them is an ADDITION.
    """
    m = res.metrics
    out: list[Check] = [
        Check("sheet rapid-vs-stock", res.worst_rapid, "must be 0",
              res.worst_rapid <= 1e-4,
              f"at {res.rapid_at}" if res.rapid_at else
              "no rapid touches remaining stock"),
    ]
    for t in sorted(res.contact):
        if not (m.tool_num == t).any():
            continue                     # a tool this program never selects
        tool = sj.tool(t)
        limit = contact_limit(tool)
        c = res.contact[t]
        out.append(Check(
            f"sheet T{t} {tool.type} contact", c.max, f"< {limit:g}",
            c.max < limit,
            f"at {c.at}, {c.samples} contact samples" if c.at
            else "no engagement measured (empty footprint by law)"
            if tool.type == "scrub" else ""))
    out.append(Check("sheet shank clearance", res.shank_worst, "must be 0",
                     res.shank_worst <= 1e-6,
                     f"at {res.shank_at[:2]}, line {res.shank_at[2]}"
                     if res.shank_at else ""))
    # depth: the blank plus the guide's breakthrough, never near the bed. The
    # grammar already refuses a configured depth outside that band; this
    # measures the SIMULATED floor of the bytes (the two disagree exactly
    # when a program is not the program the config describes).
    floor = min(res.min_cut_z, float(res.stock.min()))
    limit = -(sheet.thickness + 0.5)      # verify.MAX_OVERCUT, same number
    out.append(Check("sheet depth floor", floor, f">= {limit:.3f}",
                     floor >= limit,
                     f"{sheet.thickness}mm blank + 0.5 breakthrough "
                     f"allowance"))
    bed = -(sheet.thickness + sheet.spoil - 2.0)
    out.append(Check("sheet clear of machine bed", floor, f">= {bed:.3f}",
                     floor >= bed,
                     f"{sheet.spoil}mm spoilboard under the blank"))
    # containment: every cutting move inside the modelled sheet. The crop the
    # viewer is served is only honest if nothing was cut outside it.
    esc = 0.0
    lines: list[int] = []
    cut = m.motion == 1
    if cut.any():
        for xs, ys in ((m.x0[cut], m.y0[cut]), (m.x1[cut], m.y1[cut])):
            e = np.maximum.reduce([sheet.x0 - xs, xs - sheet.x1,
                                   sheet.y0 - ys, ys - sheet.y1])
            k = int(e.argmax())
            if float(e[k]) > esc:
                esc = float(e[k])
                lines = [int(m.lineno[cut][k])]
    out.append(Check("sheet containment", max(esc, 0.0), "<= 0",
                     esc <= 0.0,
                     f"worst at line {lines[0]}" if lines and esc > 0 else
                     f"window {sheet.x0:.2f},{sheet.y0:.2f} .. "
                     f"{sheet.x1:.2f},{sheet.y1:.2f}"))
    for pc in physics_checks(sj, m):
        out.append(Check(f"sheet {pc.name}", pc.value, pc.limit, pc.ok,
                         pc.detail))
    return out


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
                  label: str, color: str, on: bool = False) -> dict | None:
    """One aperture layer, transformed into machine frame with the SAME
    derived mirror+offset every other layer gets."""
    if not path.is_file():
        return None
    flashes, draws, skipped = apertures(path)
    dx, dy = offset

    def frame(pts):
        return [[dx - p[0], p[1] + dy] for p in pts]

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
        boardmaps.extents(job.files["edge"], cross_check=False), job.anchor)
    mask = _gerber_layer(offset, job.files["mask"], "mask_ap",
                         "B.Mask apertures (gerber)", "#6fdc8c")
    if mask:
        mask["note"] += " — the solderable openings: silk strokes must " \
                        "clear them and the scrub is meant to cover them. " \
                        "The gate deliberately does not bar scrub COVERAGE " \
                        "(checks.py): this layer is a picture, not a verdict."
        layers.append(mask)
    paste_p = job.gerber_dir / f"{job.stem}-B_Paste.gbr"
    paste = _gerber_layer(offset, paste_p, "paste_ap",
                          "B.Paste apertures (gerber)", "#7aa2ff")
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
             f"outline with {int(cut['gaps'])} tabs of "
             f"{cut['gapsize']:g}mm"),
        {"kind": "operator", "title": "release the board, snap the tabs",
         "detail": f"{int(cut['gaps'])} tabs of {cut['gapsize']:g}mm hold "
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


def build(job: PcbJob, programs: dict[str, Path] | None = None,
          gate: bool = True, ppm: float = SHEET_PPM,
          dpi: int | None = None) -> list[PcbSession]:
    """Build the four viewer sessions of a [pcb] job.

    `gate` runs checks.verify_pcb over the board maps (needs gerbv) and
    folds each program's Report into its session, so a session's PASS badge
    is the gate's verdict and nothing else. Without it — no gerbv on the box
    — the sessions still show the sheet sim, the overlays and the run sheet,
    with `ok = None` and a stated reason: a session that cannot verify says
    so instead of showing a green badge (Article I).
    """
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

    # per-program stage stats first: the run sheet needs the estimates and
    # the tool order, and every session carries the same card.
    carves: dict[str, simulate.CarveResult] = {}
    stats: dict[str, list[dict]] = {}
    est: dict[str, float] = {}
    tool_order: dict[str, list[int]] = {}
    texts: dict[str, str] = {}
    for name, path in progs.items():
        texts[name] = Path(path).read_text()
        if name in CARVING:
            res = carve_program(sj, sheet, path)
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

    paste = (job.gerber_dir / f"{job.stem}-B_Paste.gbr").is_file()
    card = run_sheet(job, progs, est, tool_order, paste)

    out: list[PcbSession] = []
    for name in checks.PROGRAM_PHASES:
        if name not in progs:
            continue
        path = Path(progs[name]).resolve()
        rep = reports.get(name)
        chk = list(rep.checks) if rep else []
        res = carves.get(name)
        if res is not None:
            chk += sheet_checks(job, sheet, sj, res)
        # a verdict ONLY when the gate itself ran: the sheet sim is an
        # ADDITION to the PCB gate, never a substitute for it, so a session
        # whose board maps never loaded stays UNVERIFIED however clean its
        # own simulation came out (Article I).
        ok = all(c.ok for c in chk) if rep is not None else None
        stocks = [sheet.crop(g) for g in res.stage_stocks] if res else []
        meta = {
            "kind": "pcb",
            "job": f"{job.name} {name}",
            "board": job.name,
            "program": name,
            "phases": list(checks.PROGRAM_PHASES[name]),
            "path": str(path),
            "toml": str(job.path),
            "nc": path.name,
            "ok": ok,
            "gate": {"ran": rep is not None,
                     "verdict": ("PASS" if rep and rep.ok else "FAIL"
                                 if rep else "not run"),
                     "note": gate_note,
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
            "total_est_s": est.get(name, 0.0),
            "chain_est_s": sum(est.values()),
            "stages": stats.get(name, []),
            "tools": stagesmod.tool_cards(sj, res, stats.get(name, []))
            if res else [],
            "checks": [{"name": c.name, "value": c.value, "limit": c.limit,
                        "ok": c.ok, "detail": c.detail} for c in chk],
            "overlay": overlay_for(job, name, texts[name], path, sj)
            if name in OVERLAY_ONLY else None,
            "run_sheet": card,
            "programs": [{"name": n, "nc": Path(p).name,
                          "est_s": est.get(n, 0.0)}
                         for n, p in progs.items()],
        }
        body = (rep.program if rep and rep.program else texts[name])
        out.append(PcbSession(name=name, path=str(path), meta=meta,
                              stocks=stocks, program=body.encode()))
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
        lines.append(f"=== program {s.name} ({s.meta['nc']}) — {verdict} "
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
    lines.append("PCB VERDICT: " + ("PASS — every program cleared"
                                    if ok_all else
                                    "NOT CLEARED — do NOT cut this board"))
    return "\n".join(lines)
