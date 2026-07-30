"""The PCB lane's verification gate (PCB-PLAN.md WS5): named, incident-traced
checks over the ASSEMBLED BYTES of each program and the gerbv board maps.

Independence law (the whole point, Article I): nothing here reads FlatCAM's
internal state, its Tcl, or its .nc intermediates' headers. Every check takes
two inputs — the rasterized gerbers/Excellon schedule from boardmaps.py, and
the g-code of the program that would go to the machine, strict-parsed with the
same simulate.parse_line the mill gate uses. The generator and the verifier
therefore meet only at the bytes, exactly like the STL lane's mesh-vs-gcode
split. reemit.py already refuses some of this at read time; these checks prove
it again on the assembled output, because Article I verifies bytes, not intent.

The checks, each with its threshold's provenance:

  iso containment      milled centerline within ISO_CENTERLINE_TOL of the
                       copper boundary + tip radius
  iso coverage         every copper island's offset outline actually gets cut
                       (a missed island is a short waiting to happen)
  clear keep-out       clearing tool EDGE never nearer than CLEAR_KEEP_MIN to
                       kept copper
  clear opening        no clearing sample in a channel narrower than the tool
                       + CLEAR_OPENING_MARGIN (the castellation-chewing
                       incident: 0.84mm slots took an 0.8 corn)
  scrub window         spring-tool edge >= SCRUB_WINDOW_MIN inside its mask
                       aperture (the region law)
  scrub plateau margin spring-tool edge >= SCRUB_PLATEAU_MIN inside pad copper
                       OR that far clear of copper — never straddling a copper
                       edge (the peeled-trace incident)
  silk pad clearance   every FIRING stroke >= the job's clearance from a
                       solderable aperture
  silk focus move      M321 followed by exactly `G0 Z0` (the defocus incident)
  silk dose            S == the job dose and <= the emitter's ceiling
  silk dialect lint    lint_laser on the assembled bytes
  hole schedule        every Excellon hole bored at position and diameter,
                       exactly once (the displaced-drill class)
  hole bore depth      each bore reaches the configured depth in >= the
                       configured number of passes
  stray bores          no cutting sample belongs to no scheduled hole
  cutout ride band     the cut rides the outline within CUTOUT_RIDE_TOL
  cutout side          no cutting sample inside the board (a negative
                       geocutout margin flips the cut INSIDE — do not)
  cutout tab census    >= TAB_MIN_COUNT tabs of >= TAB_MATERIAL_MIN material,
                       and exactly as many as the job configured
  <phase> floor Z      program floor == the phase's declared depth (the
                       stale-repost incident)
  <phase> params       S and F words match the phase's tool and feeds
  <phase> tool         every move in the stage carries the phase's tool
  rapid depth          no rapid below the work surface (the whole blank is
                       stock, so this needs no stock model)
  dialect lint         emit.lint_program / lint_laser on the assembled bytes

MEASUREMENT HONESTY (Article IX applies to geometry too — a raster proxy must
say what it does not model):

  * Distances come from `distance_transform_edt` over gerbv rasters, i.e. from
    query pixel CENTER to nearest INK pixel CENTER. That over-reads the true
    distance to the polygon by a calibrated HALF PIXEL (measured against a
    closed-form circle at three resolutions: +0.50px at 1270/2540/5080 dpi —
    the pcb_checks suite asserts it). Every clearance subtracts that half
    pixel; every escape adds it. `BoardMaps.eps` is the number.
  * Toolpaths are sampled every SAMPLE_STEP along each cutting move and the
    distance field is bilinearly interpolated, so the sampled extremum can
    miss the true extremum by at most SAMPLE_STEP/2 (distance fields are
    Lipschitz-1). Every check adds that slop in the conservative direction.
  * What this does NOT model: cutter runout and deflection, board bow the
    auto-leveler missed, mask thickness, laser spot size, the vee kerf's
    widening with depth (the KERNEL models that; these checks work on
    centerlines), and copper etch/mill overcut. The thresholds are field bars
    that already contain those effects — they are not derived from first
    principles here.
  * The board window is padded so that off-board work (the clear phase's
    rim margin, the cutout's outside ride) lands INSIDE the raster. A sample
    outside the padded window is a refusal, never a clamp.
  * The machine transform is derived from the TIGHT Edge.Cuts extents (the
    154/124 law in boardmaps.machine_offset) even though the rasters live in
    the padded window — deriving it from a padded window silently shifts
    every stroke by the pad (found the hard way while building this).

DELIBERATELY NOT CHECKED HERE, and why:
  * scrub COVERAGE (did every pad actually get scrubbed?). The retired Fusion
    pocket was field-verified at "worst gap 0.125 vs a 0.15 tip radius" (mask
    guide §3); the FlatCAM `paint` that replaced it measures 0.37 worst gap on
    the zigbee board over the morphological opening of its own apertures.
    Either the bar or the toolpath is wrong, and no bar can be set honestly
    until Board A's scrubbed pads are inspected under the loupe. Inventing a
    threshold that the validated file happens to pass is exactly what Article
    I forbids, so this stays an open incident, not a rubber stamp.
  * the geometric stock simulation (rapids, contact, depth-vs-bed, physics).
    That needs a thin-sheet stock model the [pcb] grammar does not build yet;
    it lands with Board A (PCB-PLAN.md WS5 first paragraph).
  * double-sided: side-frame mirror consistency, via concentricity across the
    flip, and the pins law carry-over land with Board B.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy import ndimage

from ..emit import LASER_S_MAX, lint_laser, lint_program
from ..simulate import OP_MARK, GcodeError, MoveMetrics, parse_line
from ..verify import Check, Report
from . import boardmaps
from .pcbjob import PcbJob
from .reemit import _stroke_chains

# ---------------------------------------------------------------- thresholds
# Provenance for every number below is a field measurement from the
# 2026-07-19 zigbee-button V2 verification (pcbnew ground truth, recorded in
# that project's README) or a rule from ~/scratch/carvera/guides. Where the
# field verifier stated a bar, that bar is used unchanged — this gate does not
# invent numbers, and it does not relax them to fit a file.

ISO_CENTERLINE_TOL = 0.06   # field bar (verify_outputs.py 'iso containment',
#                             which measured 0.005 worst). Our raster reads
#                             0.022 on the same file at 0.01mm/px and 0.005
#                             when the worst point is re-probed at 0.001mm/px.
ISO_COVERAGE_TOL = 0.08     # field bar ('iso coverage', measured 0.048; our
#                             raster reads 0.036 on the same file)
CLEAR_KEEP_MIN = 0.02       # milling guide §3: the clearing tool must stay
#                             0.02 clear of kept copper. Field verifier used
#                             the same number as a pass/fail collide test.
CLEAR_OPENING_MARGIN = 0.10  # milling guide §3, the castellation-chewing
#                             incident: an 0.84mm slot survived a ±0.36
#                             opening filter and the 0.8 corn was dispatched
#                             down it with 0.02 total clearance. The opening
#                             threshold must exceed the tool DIAMETER with
#                             real margin; the guide's own filter is 0.9 for
#                             an 0.8 tool, i.e. dia + 0.10.
SCRUB_WINDOW_MIN = 0.05     # mask guide §3.4: the spring tool's edge stays
SCRUB_PLATEAU_MIN = 0.05    # >=0.05 inside the copper plateau ("verified
#                             -0.095 on the example board"). Our raster reads
#                             0.092 on that same file.
HOLE_POS_TOL = 0.10         # bore center vs the Excellon schedule. The
#                             displaced-drill class: a bore off its pad is a
#                             scrapped board, and 0.1 is already half a
#                             0.2mm annular ring.
HOLE_DIA_TOL = 0.06         # field bar ('holes', 10/10 bores at +-0.06)
HOLE_DEPTH_TOL = 0.02       # breakthrough is 0.2 into the spoilboard; 0.02
#                             is 4-decimal g-code rounding plus nothing
CUTOUT_RIDE_TOL = 0.05      # field bar: the V2 cutout rode outline+0.55 with
#                             a 0.50..0.60 accepted band (the Edge.Cuts line
#                             width buffers the outline +0.05/side — the board
#                             comes out one line width oversize, accepted
#                             2026-07-19)
TAB_MATERIAL_MIN = 1.0      # grammar law (pcbjob): a freed board grabs the
TAB_MIN_COUNT = 2           # cutter; >=2 tabs of >=1.0mm
CUTOUT_FINAL_TOL = 0.02     # "at final depth" band for the tab walk
ZMIN_TOL = 0.001            # 4-decimal g-code rounding
SAMPLE_STEP = 0.01          # mm along every cutting move — one pixel at the
#                             lane's declared 0.01mm/px, so sampling never
#                             out-resolves the ground truth it is compared to
CHECK_PAD_BASE = 2.0        # window pad = this + the widest off-board reach

# The canonical program split (pcbjob.py's module docstring is the law):
# A mill = iso + clear, then the operator squeegees and cures the mask;
# B silk (laser dialect); C scrub; D holes = drills + cutout.
PROGRAM_PHASES = {"mill": ("iso", "clear"), "silk": ("silk",),
                  "scrub": ("scrub",), "holes": ("drills", "cutout")}


# ----------------------------------------------------------------- board maps
@dataclass
class BoardMaps:
    """Ground truth for one board: layer rasters in ONE padded window, the
    hole schedule, and the derived machine transform. Distance fields are
    computed on demand and cached (they are large: one float64 field is 8
    bytes per pixel, ~220MB for a 55x40 board at 2540 dpi — call release()
    between programs, or pass a coarser dpi)."""
    tight: boardmaps.BoardWindow      # Edge.Cuts extents — the transform frame
    win: boardmaps.BoardWindow        # padded — the raster frame
    layers: dict[str, np.ndarray]
    holes: list[tuple[float, float, float]]
    offset: tuple[float, float]       # derived machine offset (dx, dy)
    _cache: dict[str, np.ndarray] = field(default_factory=dict)

    @property
    def eps(self) -> float:
        """The calibrated raster over-read: EDT measures to ink pixel CENTERS,
        which is half a pixel farther than the polygon edge (validated against
        a closed-form circle in tests/pcb_checks_suite.py)."""
        return 0.5 / self.win.ppmm

    def to_board(self, x, y):
        """machine frame -> gerber frame. Inverse of the mirror-x + offset
        transform boardmaps.machine_offset derives (single-sided back copper;
        the double-sided variant lands with Board B)."""
        dx, dy = self.offset
        return dx - np.asarray(x), np.asarray(y) - dy

    def dist(self, key: str) -> np.ndarray:
        """Cached distance field, mm. 'cu'/'mask'/'edge' = distance TO that
        layer's ink; 'in_cu'/'in_mask' = distance to the nearest pixel that is
        NOT that layer's ink (i.e. how deep inside the region a pixel sits)."""
        if key not in self._cache:
            name = key[3:] if key.startswith("in_") else key
            if name not in self.layers:
                raise ValueError(
                    f"these board maps carry no {name!r} raster — they were "
                    f"built without gerbv, so only the schedule/echo checks "
                    f"can run on them")
            m = self.layers[name]
            self._cache[key] = boardmaps.dist_mm(
                ~m if key.startswith("in_") else m, self.win)
        return self._cache[key]

    def release(self) -> None:
        self._cache.clear()

    def sample(self, fieldarr: np.ndarray, bx, by) -> np.ndarray:
        """Bilinear sample of a field at board coordinates. Refuses samples
        outside the padded window rather than clamping them: a toolpath that
        leaves the modeled window is unverified, not fine (Article I)."""
        i, j = self.win.world_to_px(bx, by)
        h, w = self.win.shape
        if (i.min() < 0.5 or i.max() > h - 1.5
                or j.min() < 0.5 or j.max() > w - 1.5):
            raise GcodeError(
                f"a cutting move leaves the verification window "
                f"({self.win.x0:.2f},{self.win.y0:.2f} .. "
                f"{self.win.x1:.2f},{self.win.y1:.2f}) — the board maps "
                f"cannot judge it; widen the pad or fix the toolpath")
        return ndimage.map_coordinates(fieldarr, [i - 0.5, j - 0.5], order=1)


def board_maps(job: PcbJob, dpi: int | None = None,
               pad: float | None = None) -> BoardMaps:
    """Rasterize this job's layers into ONE padded window (needs gerbv).

    The window is the Edge.Cuts extents (with boardmaps' arc cross-check)
    grown by `pad` so that the clear phase's rim margin and the cutout's
    outside ride land inside the raster. The transform stays derived from the
    TIGHT extents — see the module docstring."""
    dpi = dpi or boardmaps.DPI_DEFAULT
    tight = boardmaps.extents(job.files["edge"], dpi=dpi)
    if pad is None:
        pad = CHECK_PAD_BASE + max(float(job.phases["clear"]["margin"]),
                                   job.phase_tool("cutout").diameter)
    win = boardmaps.BoardWindow(tight.x0 - pad, tight.y0 - pad,
                                tight.x1 + pad, tight.y1 + pad, dpi)
    layers = {k: boardmaps.rasterize(job.files[k], win)
              for k in ("cu", "mask", "edge")}
    return BoardMaps(tight=tight, win=win, layers=layers,
                     holes=boardmaps.excellon(job.files["drl"]),
                     offset=boardmaps.machine_offset(tight, job.anchor))


# -------------------------------------------------------------------- samples
@dataclass
class Samples:
    """Densified cutting samples of ONE phase, machine and board frames."""
    phase: str
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    bx: np.ndarray
    by: np.ndarray
    lineno: np.ndarray
    tools: np.ndarray          # per-sample tool number
    zmin: float                # floor of the phase's cutting moves
    feeds: set                 # F words in effect on cutting moves
    rpms: set                  # S words in effect on cutting moves
    move_tools: set            # tool numbers seen on ANY move of the stage

    def __len__(self) -> int:
        return int(self.x.size)

    def at(self, k: int) -> str:
        return (f"machine ({self.x[k]:.3f},{self.y[k]:.3f}) "
                f"z{self.z[k]:.3f} line {int(self.lineno[k])}")


def _densify(x0, y0, z0, x1, y1, z1, step):
    """Sample every move at <= step along XY (Lipschitz slop = step/2).
    Returns the per-sample source-move index too, so a failure names a line."""
    L = np.hypot(x1 - x0, y1 - y0)
    n = np.maximum(1, np.ceil(L / step).astype(np.int64))
    idx = np.repeat(np.arange(L.size), n + 1)
    starts = np.concatenate([[0], np.cumsum(n + 1)[:-1]])
    k = np.arange(idx.size) - np.repeat(starts, n + 1)
    t = k / np.repeat(n, n + 1)
    return (x0[idx] + (x1[idx] - x0[idx]) * t,
            y0[idx] + (y1[idx] - y0[idx]) * t,
            z0[idx] + (z1[idx] - z0[idx]) * t, idx)


def phase_samples(m: MoveMetrics, maps: BoardMaps, phase: str,
                  step: float = SAMPLE_STEP) -> Samples:
    """Cutting samples of one stage of an assembled program.

    A move counts as CUTTING when it is a G1 whose lower end is below the
    stock surface — which deliberately includes lead-in ramps and plunges: a
    tool that is even slightly engaged is a tool that can chew a pad, and the
    conservative reading of any article wins."""
    label = f"pcb-{phase}"
    if m.stage_labels is None or label not in m.stage_labels:
        raise GcodeError(
            f"the assembled program has no '(begin operation: {label} ...)' "
            f"stage — the checks refuse to guess which moves are the "
            f"{phase} phase")
    st = m.stage_labels.index(label)
    ph = m.stage == st
    cut = ph & (m.motion == 1) & (np.minimum(m.z0, m.z1) < -1e-3)
    if not cut.any():
        raise GcodeError(f"stage {label} has no cutting move")
    x, y, z, idx = _densify(m.x0[cut], m.y0[cut], m.z0[cut],
                            m.x1[cut], m.y1[cut], m.z1[cut], step)
    bx, by = maps.to_board(x, y)
    return Samples(
        phase=phase, x=x, y=y, z=z, bx=bx, by=by,
        lineno=m.lineno[cut][idx], tools=m.tool_num[cut][idx],
        zmin=float(np.minimum(m.z0[cut], m.z1[cut]).min()),
        feeds={round(float(f), 3) for f in m.feed[cut]},
        rpms={round(float(r), 3) for r in m.rpm[cut]},
        move_tools={int(t) for t in m.tool_num[ph]})


def program_moves(job: PcbJob, path) -> tuple[MoveMetrics, str]:
    """Strict-parse an assembled MILL program into per-move arrays.

    Every LINE goes through simulate.parse_line — the gate's own Article I
    authority, unchanged: unknown tools, arcs in any spelling, G-less modal
    lines and junk words are fatal here exactly as they are for a mill job.

    Modal resolution is done here rather than by simulate.prep_moves for one
    stated reason: FlatCAM's default post writes coordinate-less feed setters
    (`G01 F500.00`, three of them before the first move of every phase file).
    A motion word with no axis word moves nothing — it sets the feed — but
    prep_moves, built for this project's own emitter which never writes one,
    reads it as a cutting move and refuses the file for having no position
    yet. Rather than relax the mill gate's resolver (Article I: never weaken
    a check to make a file pass), the PCB lane resolves modal state itself and
    treats a coordinate-less motion word as what it is. The consequence is
    recorded in DESIGN.md: an assembled [pcb] program cannot go through
    verify.verify()'s stock simulation until WS4/WS6 decides where those
    lines die.
    """
    text = Path(path).read_text()
    rapid = float(job.machine.get("rapid_feed", 3000.0))
    cur = {"X": None, "Y": None, "Z": None}
    tool = None
    feed = None
    rpm = 0.0
    rows = []
    stage_labels: list[str] = []
    cur_stage = -1
    for lineno, line in enumerate(text.splitlines(), 1):
        mk = OP_MARK.search(line)
        if mk:
            stage_labels.append(mk.group(1))
            cur_stage = len(stage_labels) - 1
        parsed = parse_line(line, lineno)
        if parsed[0] == "tool":
            if parsed[1] not in job.tools:
                raise GcodeError(
                    f"line {lineno}: M6 T{parsed[1]} but the job defines no "
                    f"such tool — refusing to judge moves blind")
            tool = parsed[1]
            continue
        if parsed[0] == "spindle":
            rpm = parsed[1]
            continue
        if parsed[0] != "move":
            continue
        motion, coords, fword, sword = parsed[1:]
        if sword is not None:
            rpm = sword
        if fword is not None:
            feed = fword
        if not coords:
            continue                      # modal feed/rpm setter, not a move
        if tool is None:
            raise GcodeError(f"line {lineno}: motion before any tool change")
        prev = dict(cur)
        cur.update(coords)
        unknown = None in prev.values() or None in cur.values()
        if motion == 1 and unknown:
            raise GcodeError(
                f"line {lineno}: cutting move before XYZ position is fully "
                f"established")
        if unknown:
            continue                      # G0 accumulating the start position
        if motion == 1 and feed is None:
            raise GcodeError(
                f"line {lineno}: cutting move with no feed rate established")
        rows.append((motion, prev["X"], prev["Y"], prev["Z"],
                     cur["X"], cur["Y"], cur["Z"], tool,
                     rapid if motion == 0 else feed, rpm, lineno, cur_stage))
    if not rows:
        raise GcodeError(f"{path}: no resolvable moves")
    a = np.array(rows, dtype=np.float64)
    stage = a[:, 11].astype(np.int32)
    if not stage_labels:
        stage_labels = ["program"]
        stage[:] = 0
    elif (stage < 0).any():
        stage_labels = ["setup"] + stage_labels
        stage += 1
    return MoveMetrics(
        motion=a[:, 0].astype(np.uint8),
        x0=a[:, 1], y0=a[:, 2], z0=a[:, 3],
        x1=a[:, 4], y1=a[:, 5], z1=a[:, 6],
        tool_num=a[:, 7].astype(np.int32),
        feed=a[:, 8], rpm=a[:, 9], lineno=a[:, 10].astype(np.uint32),
        stage=stage, stage_labels=stage_labels), text


# --------------------------------------------------------------- phase checks
def iso_checks(job: PcbJob, maps: BoardMaps, s: Samples) -> list[Check]:
    """Isolation: the vee tip must ride the copper boundary offset by its tip
    RADIUS (that offset is what isolates a gap from both sides in one pass —
    milling guide §3.2), and every copper island's offset outline must
    actually get cut. A skipped island is copper left connected: a short.
    """
    tip_r = job.phase_tool("iso").tip_diameter / 2
    slop = SAMPLE_STEP / 2
    d_cu = maps.dist("cu")
    d = maps.sample(d_cu, s.bx, s.by) - maps.eps
    err = np.abs(d - tip_r) + slop
    k = int(err.argmax())
    out = [Check("iso containment", err.max(),
                 f"<= {ISO_CENTERLINE_TOL}", err.max() <= ISO_CENTERLINE_TOL,
                 f"tip {tip_r:g} offset, worst at {s.at(k)}")]

    # Coverage: the ring of every point that stands exactly tip_r outside
    # copper IS the required centerline set (interior boundaries of a pour
    # included — FlatCAM offsets those too). Every ring pixel must have a
    # path sample near it. One band-pass over the copper distance field plus
    # one EDT of the sampled path: no per-island loop, so a board with 200
    # islands costs what a board with 20 does.
    band = 1.0 / maps.win.ppmm
    ring = (np.abs(d_cu - maps.eps - tip_r) <= band)
    pathmap = np.zeros(maps.win.shape, bool)
    i, j = maps.win.world_to_px(s.bx, s.by)
    pathmap[np.clip(np.round(i - 0.5).astype(int), 0, maps.win.shape[0] - 1),
            np.clip(np.round(j - 0.5).astype(int), 0,
                    maps.win.shape[1] - 1)] = True
    d_path = boardmaps.dist_mm(pathmap, maps.win)
    # slop: half a pixel of ink bias + a rounded path pixel (0.71px) + the
    # sampling step, rounded up to 1.5px
    gap = float(d_path[ring].max()) + 1.5 / maps.win.ppmm if ring.any() \
        else float("inf")
    detail = f"{int(ring.sum())} ring px"
    if gap > ISO_COVERAGE_TOL and ring.any():
        ii, jj = np.nonzero(ring)
        w = int(d_path[ii, jj].argmax())
        wx, wy = maps.win.px_to_world(ii[w], jj[w])
        detail += (f", worst uncut at board ({float(wx):.3f},{float(wy):.3f})"
                   f" = machine ({maps.offset[0] - float(wx):.3f},"
                   f"{float(wy) + maps.offset[1]:.3f})")
    out.append(Check("iso coverage", gap, f"<= {ISO_COVERAGE_TOL}",
                     gap <= ISO_COVERAGE_TOL, detail))
    return out


def clear_checks(job: PcbJob, maps: BoardMaps, s: Samples) -> list[Check]:
    """Copper clearing: the tool edge keeps CLEAR_KEEP_MIN off kept copper,
    AND every sample sits somewhere a disc of (tool + CLEAR_OPENING_MARGIN)
    fits — the morphological-opening rule from the castellation-chewing
    incident. The second is not implied by the first: a tool threading an
    0.86mm slot has 0.03 clearance on both sides and still chews the pads it
    passes, because runout and deflection are not in the geometry.
    """
    tool = job.phase_tool("clear")
    r = tool.radius
    slop = SAMPLE_STEP / 2
    d_cu = maps.dist("cu")
    d = maps.sample(d_cu, s.bx, s.by) - maps.eps - slop
    keep = d - r
    k = int(keep.argmin())
    out = [Check("clear keep-out", keep.min(), f">= {CLEAR_KEEP_MIN}",
                 keep.min() >= CLEAR_KEEP_MIN,
                 f"T{tool.num} d{tool.diameter:g}, worst at {s.at(k)}")]

    # A point is reachable by the tool WITH margin iff some disc of radius R
    # fits entirely in the copper-free space and covers it. The centers of
    # those discs are `core`; "covered by one" is just "within R of core", so
    # one EDT answers it — no giant binary_dilation.
    R = r + CLEAR_OPENING_MARGIN / 2      # radius of the admissible disc
    core = (d_cu - maps.eps) >= R
    reach = maps.sample(boardmaps.dist_mm(core, maps.win), s.bx, s.by) + slop
    k = int(reach.argmax())
    out.append(Check("clear opening", reach.max(), f"<= {R:g}",
                     bool(reach.max() <= R),
                     f"channel must admit a {2*R:g} disc; worst at "
                     f"{s.at(k)}"))
    return out


def scrub_checks(job: PcbJob, maps: BoardMaps, s: Samples) -> list[Check]:
    """Spring-tool pad scrub. Two independent laws, both from the peeled-trace
    incident (mask guide §2/§3.4):

      window  — the tool edge stays SCRUB_WINDOW_MIN inside its mask aperture;
                the aperture is the process's own region definition (regions =
                apertures deflated by the job's paint offset).
      plateau — the tool edge never straddles a copper EDGE: it is either
                SCRUB_PLATEAU_MIN inside copper or that far clear of it. This
                is the actual failure mechanism — the spring tip dropped off
                the pad rim into the isolation groove and levered the traces
                off. A file can satisfy the window law and still do it.
    """
    tool = job.phase_tool("scrub")
    r = tool.radius
    slop = SAMPLE_STEP / 2
    inside = maps.sample(maps.dist("in_mask"), s.bx, s.by) - maps.eps - slop
    margin = inside - r
    k = int(margin.argmin())
    out = [Check("scrub window", margin.min(), f">= {SCRUB_WINDOW_MIN}",
                 margin.min() >= SCRUB_WINDOW_MIN,
                 f"T{tool.num} d{tool.diameter:g} edge inside the mask "
                 f"aperture, worst at {s.at(k)}")]

    # |signed distance to the copper boundary|: exactly one of the two fields
    # is non-zero at any pixel, so the sum IS the unsigned distance.
    d_out = maps.sample(maps.dist("cu"), s.bx, s.by)
    d_in = maps.sample(maps.dist("in_cu"), s.bx, s.by)
    plateau = (d_out + d_in) - maps.eps - slop - r
    k = int(plateau.argmin())
    side = "inside copper" if d_in[k] > d_out[k] else "clear of copper"
    out.append(Check("scrub plateau margin", plateau.min(),
                     f">= {SCRUB_PLATEAU_MIN}",
                     plateau.min() >= SCRUB_PLATEAU_MIN,
                     f"worst {side} at {s.at(k)}"))
    return out


def hole_checks(job: PcbJob, maps: BoardMaps, s: Samples) -> list[Check]:
    """The hole schedule is ground truth (boardmaps.excellon): every hole
    bored once, at position, at diameter, to depth, and NOTHING else bored.
    The displaced-drill class covers all four failures — a bore off its pad, a
    bore that never happened, a bore of the wrong size, and a bore nobody
    asked for.

    Bore geometry: a milldrill orbits at (hole - tool)/2, so the achieved
    diameter is 2*(worst radius + tool radius) and the bore CENTER is the
    midpoint of the sample bounding box (robust for a plunge, an orbit and a
    helix alike — a centroid is not).
    """
    tool = job.phase_tool("drills")
    p = job.phases["drills"]
    depth, dpp = float(p["depth"]), float(p["dpp"])
    need_passes = int(np.ceil(abs(depth) / dpp))
    defects: list[str] = []
    bad_holes: set[tuple] = set()
    worst_dia = 0.0
    worst_pos = 0.0
    worst_depth = 0.0
    fewest = 99
    assigned = np.zeros(len(s), bool)
    for hx, hy, hd in maps.holes:
        rr = np.hypot(s.bx - hx, s.by - hy)
        cap = hd / 2 + tool.radius + HOLE_POS_TOL
        g = rr <= cap
        assigned |= g
        if not g.any():
            defects.append(f"({hx:.2f},{hy:.2f}) d{hd:g}: never bored")
            bad_holes.add((hx, hy, hd))
            continue
        dia = 2 * (float(rr[g].max()) + tool.radius)
        cx = (float(s.bx[g].min()) + float(s.bx[g].max())) / 2
        cy = (float(s.by[g].min()) + float(s.by[g].max())) / 2
        pos = float(np.hypot(cx - hx, cy - hy))
        zs = np.unique(np.round(s.z[g], 3))
        derr = abs(float(zs.min()) - depth)
        worst_dia = max(worst_dia, abs(dia - hd))
        worst_pos = max(worst_pos, pos)
        worst_depth = max(worst_depth, derr)
        fewest = min(fewest, int(zs.size))
        if abs(dia - hd) > HOLE_DIA_TOL:
            defects.append(f"({hx:.2f},{hy:.2f}) d{hd:g}: bored "
                           f"{dia:.3f}")
            bad_holes.add((hx, hy, hd))
        if pos > HOLE_POS_TOL:
            defects.append(f"({hx:.2f},{hy:.2f}) d{hd:g}: bore center off "
                           f"by {pos:.3f}")
            bad_holes.add((hx, hy, hd))
    out = [Check("hole schedule", float(len(defects)), "0 defects",
                 not defects,
                 f"{len(maps.holes) - len(bad_holes)}/{len(maps.holes)} "
                 f"bores, worst dia err {worst_dia:.3f}, worst center off "
                 f"{worst_pos:.3f}" + ("; " + "; ".join(defects[:3])
                                       if defects else ""))]
    ok_depth = worst_depth <= HOLE_DEPTH_TOL and fewest >= need_passes
    out.append(Check("hole bore depth", worst_depth,
                     f"== {depth:g} +-{HOLE_DEPTH_TOL} in >= "
                     f"{need_passes} passes", ok_depth,
                     f"fewest z levels {fewest}"))
    stray = int((~assigned).sum())
    detail = ""
    if stray:
        k = int(np.nonzero(~assigned)[0][0])
        detail = f"first at {s.at(k)}"
    out.append(Check("stray bores", float(stray), "0 samples", stray == 0,
                     detail))
    return out


def cutout_checks(job: PcbJob, maps: BoardMaps, s: Samples) -> list[Check]:
    """Edge cut: rides the outline at tool radius OUTSIDE it, never inside,
    and leaves the configured tabs.

    Ride is measured to the outline INK, which already carries the Edge.Cuts
    line width — so the nominal ride is exactly the tool radius and the board
    comes out one line width oversize per side (measured and accepted
    2026-07-19; a case cavity must allow it).

    Tabs are counted by walking the final-depth samples in outline order (the
    coin job's sever walk, generalized off the circle: order by arc length
    along the outline polyline, then measure the gaps in real XY). Material
    width = path gap - tool diameter.
    """
    tool = job.phase_tool("cutout")
    slop = SAMPLE_STEP / 2
    ride = maps.sample(maps.dist("edge"), s.bx, s.by) - maps.eps
    err = np.abs(ride - tool.radius) + slop
    k = int(err.argmax())
    out = [Check("cutout ride band", err.max(), f"<= {CUTOUT_RIDE_TOL}",
                 err.max() <= CUTOUT_RIDE_TOL,
                 f"nominal ride = tool radius {tool.radius:g} from the "
                 f"outline ink; worst at {s.at(k)}")]

    inside = (ndimage.binary_fill_holes(maps.layers["edge"])
              & ~maps.layers["edge"])
    i, j = maps.win.world_to_px(s.bx, s.by)
    hit = inside[np.clip(np.round(i - 0.5).astype(int), 0,
                         maps.win.shape[0] - 1),
                 np.clip(np.round(j - 0.5).astype(int), 0,
                         maps.win.shape[1] - 1)]
    n_in = int(hit.sum())
    detail = ""
    if n_in:
        detail = f"first at {s.at(int(np.nonzero(hit)[0][0]))}"
    out.append(Check("cutout side", float(n_in), "0 samples inside the board",
                     n_in == 0, detail))

    final = s.z <= float(job.phases["cutout"]["depth"]) + CUTOUT_FINAL_TOL
    if not final.any():
        out.append(Check("cutout tab census", 0.0, "unmeasurable", False,
                         "no sample reaches the configured depth"))
        return out
    sc = _outline_s(job, s.bx[final], s.by[final])
    # stable sort: samples whose projection clamps to the same outline vertex
    # (every corner) share an s value, and must keep their PATH order — an
    # unstable sort scrambles them into gaps that look like tabs
    order = np.argsort(sc, kind="stable")
    px_, py_ = s.bx[final][order], s.by[final][order]
    gaps = np.hypot(np.diff(np.concatenate([px_, px_[:1]])),
                    np.diff(np.concatenate([py_, py_[:1]])))
    # A path gap of g leaves g - dia of material: <= 0 means the two cuts
    # overlap and the outline is severed there (an ordering artifact at a
    # corner chord looks exactly like this and is harmless); > 0 is a bridge,
    # and a bridge thinner than the law is the hazard — it snaps in the
    # fixture and frees the board next to a spinning cutter.
    material = gaps - tool.diameter
    tabs = material[material >= TAB_MATERIAL_MIN]
    thin = material[(material > 0) & (material < TAB_MATERIAL_MIN)]
    want = int(job.phases["cutout"]["gaps"])
    ok = (len(tabs) == want and len(tabs) >= TAB_MIN_COUNT
          and len(thin) == 0)
    out.append(Check("cutout tab census", float(len(tabs)),
                     f"== {want} tabs >= {TAB_MATERIAL_MIN}", ok,
                     f"tab material {np.round(np.sort(tabs), 2)}"
                     + (f", THIN bridges {np.round(np.sort(thin), 2)}"
                        if len(thin) else "")))
    return out


def _outline_loop(job: PcbJob) -> np.ndarray:
    """Edge.Cuts draw words -> segments in true PERIMETER order.

    KiCad emits the outline in whatever order the shapes were created (the
    zigbee board draws top, left, right, bottom), so cumulative draw length is
    NOT position around the board — sorting by it interleaves opposite sides
    and invents tabs. The segments are therefore chained end-to-end into one
    closed loop, and anything that is not one closed loop refuses: a board
    with an internal cutout needs a per-loop walk, and inventing one for a
    board that does not exist yet is how a check starts lying.
    """
    segs: list[tuple] = []
    for ch in _stroke_chains(job.files["edge"]):
        for a, b in zip(ch[:-1], ch[1:]):
            if a != b:
                segs.append((a[0], a[1], b[0], b[1]))
    if not segs:
        raise GcodeError(f"{job.files['edge']}: no outline segments")

    def key(x, y):
        return (round(x, 3), round(y, 3))

    ends: dict[tuple, list[int]] = {}
    for n, (x1, y1, x2, y2) in enumerate(segs):
        ends.setdefault(key(x1, y1), []).append(n)
        ends.setdefault(key(x2, y2), []).append(n)
    if any(len(v) != 2 for v in ends.values()):
        raise GcodeError(
            f"{job.files['edge']}: the outline is not a simple closed loop "
            f"(a vertex with {sorted({len(v) for v in ends.values()})} "
            f"segments) — the cutout tab walk cannot order it")
    used = {0}
    x1, y1, x2, y2 = segs[0]
    loop = [(x1, y1, x2, y2)]
    while True:
        nxt = [n for n in ends[key(x2, y2)] if n not in used]
        if not nxt:
            break
        n = nxt[0]
        a1, b1, a2, b2 = segs[n]
        if key(a1, b1) != key(x2, y2):     # walked into its far end: flip
            a1, b1, a2, b2 = a2, b2, a1, b1
        loop.append((a1, b1, a2, b2))
        used.add(n)
        x2, y2 = a2, b2
    if len(used) != len(segs) or key(x2, y2) != key(x1, y1):
        raise GcodeError(
            f"{job.files['edge']}: the outline does not close into one loop "
            f"({len(used)} of {len(segs)} segments walked) — the cutout tab "
            f"walk cannot order it")
    return np.array(loop)


def _outline_s(job: PcbJob, bx, by) -> np.ndarray:
    """Arc-length position of each point's projection onto the Edge.Cuts
    loop — the outline's own coordinate, used for ORDERING only (gap lengths
    are measured in real XY, so a segmented corner arc cannot inflate a
    tab)."""
    a = _outline_loop(job)
    L = np.hypot(a[:, 2] - a[:, 0], a[:, 3] - a[:, 1])
    cum = np.concatenate([[0.0], np.cumsum(L)[:-1]])
    best = np.full(bx.size, np.inf)
    s = np.zeros(bx.size)
    for k in range(a.shape[0]):
        x1, y1, x2, y2 = a[k]
        dx, dy = x2 - x1, y2 - y1
        den = dx * dx + dy * dy
        t = 0.0 if den == 0 else np.clip(((bx - x1) * dx + (by - y1) * dy)
                                         / den, 0.0, 1.0)
        d = np.hypot(bx - x1 - t * dx, by - y1 - t * dy)
        take = d < best
        best = np.where(take, d, best)
        s = np.where(take, cum[k] + t * L[k], s)
    return s


def silk_checks(job: PcbJob, maps: BoardMaps, text: str) -> list[Check]:
    """Laser silk legend, on the assembled bytes. The laser fires only on FEED
    moves, so the firing set is exactly the G1 segments (each chain's first G1
    starts at its G0 landing point). Three laws, all field-derived (mask guide
    §5): clearance from solderable apertures (cured white on a pad repels
    solder), the focus move (a parked head projects a big square and cures
    mask in washes — the 2026-07-19 defocus incident), and the dose ceiling.
    """
    clearance = float(job.phases["silk"]["clearance"])
    dose = float(job.phases["silk"]["dose"])
    feed = float(job.phases["silk"]["feed"])
    lines = text.splitlines()
    problems = lint_laser(lines)
    out = [Check("silk dialect lint", float(len(problems)), "0 problems",
                 not problems, "; ".join(problems[:3]))]

    # the focus law, re-verified independently of the lint: M321 then EXACTLY
    # `G0 Z0` as the first motion
    body = [ln.split("(")[0].strip() for ln in lines]
    body = [b for b in body if b]
    focus_ok = False
    for n, b in enumerate(body):
        if "M321" in b:
            nxt = next((c for c in body[n + 1:]
                        if c.startswith(("G0", "G1", "M3"))), "")
            focus_ok = nxt.replace(" ", "") == "G0Z0"
            break
    out.append(Check("silk focus move", 1.0 if focus_ok else 0.0,
                     "M321 then exactly 'G0 Z0'", focus_ok,
                     "" if focus_ok else "the defocus incident: a parked "
                     "head cures mask in washes"))

    s_words = [float(b.split("S")[1].split()[0])
               for b in body if b.startswith("M3") and "S" in b]
    s_max = max(s_words) if s_words else 0.0
    limit = min(dose, LASER_S_MAX)
    out.append(Check("silk dose", s_max,
                     f"== {dose:g} and <= {LASER_S_MAX:g}",
                     bool(s_words) and abs(s_max - limit) <= 1e-9,
                     f"job dose S{dose:g}, ceiling S{LASER_S_MAX:g}"))

    f_words = {float(b.split("F")[1].split()[0]) for b in body if "F" in b}
    out.append(Check("silk feed echo", float(max(f_words) if f_words else 0),
                     f"== {feed:g}", f_words == {feed},
                     f"F words {sorted(f_words)}"))

    segs = []
    cur = None
    for b in body:
        if not b.startswith(("G0", "G1")) or "X" not in b:
            continue
        xy = (float(b.split("X")[1].split()[0]),
              float(b.split("Y")[1].split()[0]))
        if b.startswith("G1") and cur is not None:
            segs.append((cur[0], cur[1], xy[0], xy[1]))
        cur = xy
    if not segs:
        out.append(Check("silk pad clearance", 0.0, "unmeasurable", False,
                         "no firing move in the program"))
        return out
    a = np.array(segs)
    x, y, _, idx = _densify(a[:, 0], a[:, 1], np.zeros(len(a)),
                            a[:, 2], a[:, 3], np.zeros(len(a)), SAMPLE_STEP)
    bx, by = maps.to_board(x, y)
    d = maps.sample(maps.dist("mask"), bx, by) - maps.eps - SAMPLE_STEP / 2
    k = int(d.argmin())
    out.append(Check("silk pad clearance", float(d.min()),
                     f">= {clearance:g}", float(d.min()) >= clearance,
                     f"{len(segs)} firing segments; worst on the stroke "
                     f"({a[idx[k], 0]:.3f},{a[idx[k], 1]:.3f})->"
                     f"({a[idx[k], 2]:.3f},{a[idx[k], 3]:.3f}) at machine "
                     f"({x[k]:.3f},{y[k]:.3f})"))
    return out


PHASE_CHECKS = {"iso": iso_checks, "clear": clear_checks,
                "scrub": scrub_checks, "drills": hole_checks,
                "cutout": cutout_checks}


# ------------------------------------------------------- per-program echoes
def echo_checks(job: PcbJob, phases: tuple[str, ...],
                samples: dict[str, Samples], text: str,
                m: MoveMetrics | None = None) -> list[Check]:
    """The stale-repost incident, proven on assembled output: every program's
    floor is its phase's declared depth, and its S/F words are the phase's
    tool and feeds. reemit.read_phase refuses this at read time on the ENGINE
    file; this proves it again on the bytes that reach the machine, which is
    the only artifact the operator actually posts.

    Also the one rapid check the lane can make without a stock model: on a
    solid copper-clad blank EVERY point is material, so a rapid that DESCENDS
    below the work surface, or TRAVELS laterally below it, is a crash by
    definition (a pure Z retract out of a cut is of course fine — that is the
    same split verify.py's rapid-vs-stock check makes, and a zero-length G0 at
    depth, which FlatCAM emits before every step-down, moves nothing at all).
    verify.py normally owns this; it needs a simulated sheet the [pcb] grammar
    does not build yet, and a gate with a hole in it is worse than a slow
    one."""
    out: list[Check] = []
    if m is not None:
        rap = m.motion == 0
        lateral = (m.x0 != m.x1) | (m.y0 != m.y1)
        nz = np.minimum(m.z0, m.z1)
        descends = (m.z1 < -1e-3) & (m.z1 < m.z0 - 1e-6)
        bad = rap & (descends | (lateral & (nz < -1e-3)))
        worst = float(nz[bad].min()) if bad.any() else 0.0
        detail = f"{int(rap.sum())} rapids"
        if bad.any():
            k = int(np.nonzero(bad)[0][0])
            detail += f", first offender at line {int(m.lineno[k])}"
        out.append(Check("rapid depth", worst,
                         ">= 0 (the whole blank is stock)", not bad.any(),
                         detail))
    for ph in phases:
        s = samples[ph]
        p = job.phases[ph]
        tool = job.phase_tool(ph)
        depth = float(p["depth"])
        out.append(Check(f"{ph} floor Z", s.zmin, f"== {depth:g}",
                         abs(s.zmin - depth) <= ZMIN_TOL))
        want_f = {round(float(p["feed"]), 3), round(float(p["plunge"]), 3)}
        stray_f = sorted(s.feeds - want_f)
        stray_s = sorted(r for r in s.rpms if abs(r - tool.rpm) > 0.5)
        bad = len(stray_f) + len(stray_s)
        out.append(Check(f"{ph} params", float(bad),
                         f"F in {sorted(want_f)}, S == {tool.rpm:g}",
                         bad == 0,
                         (f"stray F {stray_f}" if stray_f else "")
                         + (f" stray S {stray_s}" if stray_s else "")))
        wrong = sorted(s.move_tools - {tool.num})
        out.append(Check(f"{ph} tool", float(len(wrong)),
                         f"T{tool.num} only", not wrong,
                         f"also saw {['T%d' % t for t in wrong]}"
                         if wrong else ""))
    problems = lint_program(text.splitlines())
    out.append(Check("dialect lint", float(len(problems)), "0 problems",
                     not problems, "; ".join(problems[:3])))
    return out


# ------------------------------------------------------------------ the gate
def verify_program(job: PcbJob, name: str, path, maps: BoardMaps) -> Report:
    """Verify ONE assembled program of a [pcb] job against the board maps."""
    if name not in PROGRAM_PHASES:
        return Report.fatal(f"unknown pcb program {name!r} — the split is "
                            f"{sorted(PROGRAM_PHASES)}")
    phases = PROGRAM_PHASES[name]
    try:
        text = Path(path).read_text()
        if "M321" in text:            # the laser family, its own dialect
            if phases != ("silk",):
                return Report.fatal(
                    f"{path}: M321 in the {name!r} program — a laser file "
                    f"cannot carry mill phases")
            return Report(silk_checks(job, maps, text), None, program=text)
        if phases == ("silk",):
            return Report.fatal(f"{path}: the silk program has no M321 — "
                                f"this is not a laser file")
        m, text = program_moves(job, path)
        samples = {ph: phase_samples(m, maps, ph) for ph in phases}
        checks: list[Check] = []
        for ph in phases:
            checks += PHASE_CHECKS[ph](job, maps, samples[ph])
        checks += echo_checks(job, phases, samples, text, m)
        return Report(checks, None, program=text)
    except GcodeError as e:
        return Report.fatal(str(e))


def verify_pcb(job: PcbJob, programs: dict[str, str | Path],
               maps: BoardMaps | None = None,
               dpi: int | None = None) -> dict[str, Report]:
    """The [pcb] gate: one Report per program, composed the way
    twosided.verify composes its two sides. A program of the canonical split
    that was not handed over is a FATAL report, never a silent gap — the six
    phases are the job (pcbjob.py), and a chain missing a program is a
    different process.

    `programs` maps program name -> the file whose BYTES go to the machine.
    """
    maps = maps or board_maps(job, dpi=dpi)
    reports: dict[str, Report] = {}
    for name in PROGRAM_PHASES:
        if name not in programs:
            reports[name] = Report.fatal(
                f"no {name!r} program was handed to the gate — phases "
                f"{PROGRAM_PHASES[name]} are unverified")
            continue
        reports[name] = verify_program(job, name, programs[name], maps)
        maps.release()
    for name in sorted(set(programs) - set(PROGRAM_PHASES)):
        reports[name] = Report.fatal(
            f"unknown pcb program {name!r} — the split is "
            f"{sorted(PROGRAM_PHASES)}")
    return reports


def report_text(reports: dict[str, Report]) -> str:
    """One text block for all four programs (CLI/MCP shape)."""
    out = []
    for name, rep in reports.items():
        out.append(f"=== program {name} ===")
        out.append(rep.text())
    ok = all(r.ok for r in reports.values())
    out.append("PCB VERDICT: " + ("PASS — every program cleared"
                                  if ok else "FAIL — do NOT cut this board"))
    return "\n".join(out)
