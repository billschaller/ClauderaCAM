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
  residual copper      carve the mill program; copper that remains farther
                       than RESIDUAL_TOL from DESIGNED copper is a sliver no
                       phase removed (the bridging-sliver incident — the gap
                       between the iso reach and the clearing floor was
                       invisible to per-phase checks)

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

THE SHEET STOCK SIMULATION (the WS5 debt, adopted into the gate 2026-07-30):
the MILL and HOLES programs additionally ride simulate.carve() on the
thin-sheet stock model defined below (thickness from the blank, XY window
DERIVED from the Edge.Cuts board window in machine frame, grown the same way
board_maps pads its raster window). That adds the checks this docstring used
to list as "deliberately not checked here": rapid-vs-stock, true tool
contact, shank clearance, depth floor vs blank + breakthrough, depth vs the
machine bed, cutting containment in the modelled sheet, and the physics
verdicts — all prefixed `sheet ` so a PCB report never confuses them with the
board-map checks, and every one an ADDITION: nothing was relaxed to make
room. WS6 built the model for the viewer; a bare verify_pcb() used to skip
it, which meant the gate a CI run or an MCP call invoked proved less than the
session a human happened to open — the gate itself must be the strictest
reader, so the sim lives here and the viewer borrows it back. Silk and scrub
stay exempt with stated reasons: the laser removes nothing and the spring
tool's kernel footprint is empty by law (Article IX exemption), so a
heightmap of either would be a flat sheet presented as a preview.

DELIBERATELY NOT CHECKED HERE, and why:
  * scrub COVERAGE (did every pad actually get scrubbed?). The retired Fusion
    pocket was field-verified at "worst gap 0.125 vs a 0.15 tip radius" (mask
    guide §3); the FlatCAM `paint` that replaced it measures 0.37 worst gap on
    the zigbee board over the morphological opening of its own apertures.
    Either the bar or the toolpath is wrong, and no bar can be set honestly
    until Board A's scrubbed pads are inspected under the loupe. Inventing a
    threshold that the validated file happens to pass is exactly what Article
    I forbids, so this stays an open incident, not a rubber stamp.
  * double-sided: side-frame mirror consistency, via concentricity across the
    flip, and the pins law carry-over land with Board B.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy import ndimage

from .. import simulate
from ..emit import LASER_S_MAX, lint_laser, lint_program
from ..physics import physics_checks
from ..simulate import OP_MARK, GcodeError, MoveMetrics, parse_line
from ..twosided import PIN_CLEAR
from ..verify import Check, Report, contact_limit
from . import boardmaps
from .pcbjob import (GAUGE_MATCH_TOL, PIN_PHASES,  # noqa: F401 (re-exported:
                     PROGRAM_PHASES,
                     PcbJob, programs_of)         # the split moved to the
#                                             grammar, checks.PROGRAM_PHASES
#                                             stays a valid name for readers)
from .pcbjob import iso_pass_plan as pcbjob_iso_plan
from .pcbjob import tab_count as pcbjob_tab_count
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

# The sheet sim runs at the mill gate's own resolution — carve_check's 12.5
# px/mm — so a [pcb] number and a mill number mean the same thing.
SHEET_PPM = 12.5

# Which programs of the split carve, and which are overlay/dialect-only. This
# is not a preference: `silk` is a laser program (simulate.parse_line refuses
# M321 by law) and `scrub` drives a tool whose kernel footprint is empty.
CARVING = ("mill", "holes", "pins")
OVERLAY_ONLY = ("silk", "scrub")

PIN_POS_TOL = 0.2           # coin lane's own bar (verify.py "drill only at pin
#                             positions", <= 0.2): the pin hole is a
#                             registration datum, and 0.2 is already a tenth of
#                             a Ø2 dowel's diameter

# Residual copper (2026-07-30, the bridging-sliver incident: the operator's
# loupe found copper ridges in the coupon's 0.5/0.6mm serpentine gaps on the
# simulated-stock render. Single-pass isolation covers a gap only up to
# ~2*(tip/2 + kerf/2) ≈ 0.46mm with the 0.2 vee, the clearing phase refuses
# regions narrower than its tool + margin, and NO check measured the copper
# that neither phase removed — the gap between the two generators was
# invisible to a gate that only checked each phase against its own contract).
RESIDUAL_CUT_MIN = 0.05     # a carve cell counts copper-REMOVED only where the
#                             mill program cut at least this deep: below the
#                             35um foil with margin, and below the vee's
#                             cone-wall grazing band, while far above any
#                             engrave depth the grammar accepts
RESIDUAL_TOL = 0.13         # remaining copper farther than this from DESIGNED
#                             copper is UNDESIGNED. Budget at the sheet sim's
#                             12.5px/mm: half a cell diagonal of carve
#                             quantization (0.057) + half a cell of footprint
#                             rounding (0.040) + the raster's half-pixel ink
#                             bias (0.005) + 0.028 spare. The real specimens
#                             sit at >=0.19 (a mid-gap sliver in a 0.5 gap is
#                             ~0.21 from either copper edge), so the band
#                             separates noise from hazard with margin both ways
RESIDUAL_EDGE_EXCL = 0.6    # the rim strip the cutout consumes: copper within
#                             this of the outline is the edge cut's territory
#                             (1.0 corn half-kerf + 0.1), not the mill's
RESIDUAL_MAX_WIDTH = 0.6    # an undesigned cluster narrower than this (max
#                             inscribed disc) is a SLIVER: thinner than the
#                             lane's own 0.5 track minimum + one sim cell, so
#                             no design could own it — it is a ridge that
#                             flakes, lifts with the mask, and bridges. The
#                             entire measured incident population (149
#                             clusters on Board A, 55 on the field board)
#                             sits at width <= 0.506
RESIDUAL_MIN_AREA = 3.0     # ... and one smaller than this (mm2) is a crumb
#                             whatever its shape (largest measured incident
#                             fragment: 2.45mm2). What the fragment law
#                             deliberately does NOT flag: a LARGE floating
#                             plane far from designed copper — that is
#                             isolation-milling practice, mechanically
#                             stable, and on this lane the generator clears
#                             it anyway (`ncc -box edge`); the check hunts
#                             fragments because fragments are what bridge


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
    mirror: str = "x"                 # which frame `offset` belongs to
    _cache: dict[str, np.ndarray] = field(default_factory=dict)

    @property
    def eps(self) -> float:
        """The calibrated raster over-read: EDT measures to ink pixel CENTERS,
        which is half a pixel farther than the polygon edge (validated against
        a closed-form circle in tests/pcb_checks_suite.py)."""
        return 0.5 / self.win.ppmm

    def to_board(self, x, y):
        """machine frame -> gerber frame: the inverse of the (mirror, offset)
        transform boardmaps.machine_offset derived. `mirror` is "x" for
        back-copper work (the single-sided lane, and side 2 of a flipped
        board) and "none" for side A's front copper — the ONE sign that
        differs between the two frames, and it is carried here rather than
        assumed."""
        return boardmaps.board_xy(self.offset, self.mirror, x, y)

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


def window_pad(job: PcbJob) -> float:
    """How far off the board the raster window must reach: the widest
    off-board work either setup does (the clear phase's rim margin, the
    cutout's outside ride) plus the base pad.

    For a DOUBLE-SIDED document this maxes over BOTH sides, so the two side
    views get pixel-identical windows from the one Edge.Cuts — which is what
    lets a board-level check compare F.Cu and B.Cu array to array. A
    single-sided job sees exactly the number it always saw."""
    tables = list(job.side_phases.values()) if job.twosided else [job.phases]
    reach = 0.0
    for phases in tables:
        reach = max(reach, float(phases["clear"]["margin"]))
        if phases.get("cutout"):
            reach = max(reach, job.tool(phases["cutout"]["tool"]).diameter)
    return CHECK_PAD_BASE + reach


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
        pad = window_pad(job)
    win = boardmaps.BoardWindow(tight.x0 - pad, tight.y0 - pad,
                                tight.x1 + pad, tight.y1 + pad, dpi)
    layers = {k: boardmaps.rasterize(job.files[k], win)
              for k in ("cu", "mask", "edge")}
    return BoardMaps(tight=tight, win=win, layers=layers,
                     holes=boardmaps.excellon(job.files["drl"]),
                     offset=boardmaps.machine_offset(tight, job.anchor,
                                                     job.mirror),
                     mirror=job.mirror)


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
    treats a coordinate-less motion word as what it is.

    Since 2026-07-30 those lines die at re-emission instead (reemit.read_phase
    folds the F onto the next motion line, so an ASSEMBLED program does ride
    prep_moves and verify()'s resolver). This tolerance stays anyway: it costs
    one branch, and it is what lets the gate judge a hand-posted interchange
    file too — the checks must never need the emitter's cooperation.
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
    # The legal band is [tip_r, top rung] of the multi-pass ladder
    # (pcbjob.iso_pass_plan — the bridging-sliver incident): pass n rides
    # dia*(0.5 + n/2) off the copper, and where opposing features' pass
    # contours merge FlatCAM rides the medial line, so any distance INSIDE
    # the band is by-construction legitimate. The gouge side keeps the
    # original field bar unchanged — a cut nearer than tip_r is cutting
    # designed copper no matter how many passes exist — and the far side
    # keeps the same bar beyond the last rung. A single-pass job has
    # top == tip_r and this is bit-for-bit the old exact-offset law.
    _, _, top = pcbjob_iso_plan(job)
    err = np.maximum.reduce([tip_r - d, d - top, np.zeros_like(d)]) + slop
    k = int(err.argmax())
    out = [Check("iso containment", err.max(),
                 f"<= {ISO_CENTERLINE_TOL}", err.max() <= ISO_CENTERLINE_TOL,
                 f"band [{tip_r:g}, {top:g}] off copper, worst at {s.at(k)}")]

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

    walk = cutout_gaps(job, s)
    if walk is None:
        out.append(Check("cutout tab census", 0.0, "unmeasurable", False,
                         "no sample reaches the configured depth"))
        return out
    px_, py_, material = walk
    tabs = material[material >= TAB_MATERIAL_MIN]
    thin = material[(material > 0) & (material < TAB_MATERIAL_MIN)]
    # the census counts what the toolpath actually left, and compares it to the
    # count the DECLARED placement leaves — it never assumes one tab per side,
    # so a job that steers its tabs ("2lr" = four, both on the left and right
    # edges) is judged by the same walk as the default four
    want = pcbjob_tab_count(job.phases["cutout"]["gaps"])
    ok = (len(tabs) == want and len(tabs) >= TAB_MIN_COUNT
          and len(thin) == 0)
    out.append(Check("cutout tab census", float(len(tabs)),
                     f"== {want} tabs >= {TAB_MATERIAL_MIN}", ok,
                     f"tab material {np.round(np.sort(tabs), 2)}"
                     + (f", THIN bridges {np.round(np.sort(thin), 2)}"
                        if len(thin) else "")))
    return out


def cutout_gaps(job: PcbJob, s: Samples):
    """The tab walk, factored out: order the final-depth cutting samples
    around the outline and measure the material each PATH gap leaves.

    -> (ordered bx, ordered by, material per gap) or None if nothing reaches
    depth. Gap k spans from ordered point k to k+1 (cyclic), so the tab census
    and the tab-zone copper keep-out (flip.py) find the same tabs from the
    same ordering — one implementation, two readers.

    A path gap of g leaves g - dia of material: <= 0 means the two cuts
    overlap and the outline is severed there (an ordering artifact at a corner
    chord looks exactly like this and is harmless); > 0 is a bridge, and a
    bridge thinner than the law is the hazard — it snaps in the fixture and
    frees the board next to a spinning cutter.
    """
    final = s.z <= float(job.phases["cutout"]["depth"]) + CUTOUT_FINAL_TOL
    if not final.any():
        return None
    sc = _outline_s(job, s.bx[final], s.by[final])
    # stable sort: samples whose projection clamps to the same outline vertex
    # (every corner) share an s value, and must keep their PATH order — an
    # unstable sort scrambles them into gaps that look like tabs
    order = np.argsort(sc, kind="stable")
    px_, py_ = s.bx[final][order], s.by[final][order]
    gaps = np.hypot(np.diff(np.concatenate([px_, px_[:1]])),
                    np.diff(np.concatenate([py_, py_[:1]])))
    return px_, py_, gaps - job.phase_tool("cutout").diameter


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


def project_to_outline(job: PcbJob, bx, by):
    """Each point's closest point ON the Edge.Cuts loop (board frame). The
    tab-zone keep-out needs it: a tab's material runs from the cut path INWARD
    to the outline, and the copper it could tear off sits by the outline, not
    by the path."""
    a = _outline_loop(job)
    bx = np.asarray(bx, dtype=float)
    by = np.asarray(by, dtype=float)
    best = np.full(bx.size, np.inf)
    ox = np.zeros(bx.size)
    oy = np.zeros(bx.size)
    for k in range(a.shape[0]):
        x1, y1, x2, y2 = a[k]
        dx, dy = x2 - x1, y2 - y1
        den = dx * dx + dy * dy
        t = 0.0 if den == 0 else np.clip(((bx - x1) * dx + (by - y1) * dy)
                                         / den, 0.0, 1.0)
        qx, qy = x1 + t * dx, y1 + t * dy
        d = np.hypot(bx - qx, by - qy)
        take = d < best
        best = np.where(take, d, best)
        ox = np.where(take, qx, ox)
        oy = np.where(take, qy, oy)
    return ox, oy


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


def pin_checks(job: PcbJob, maps: BoardMaps, s: Samples) -> list[Check]:
    """The registration-pin block, carried over from the coin lane's pins law
    (verify.py's "drill only at pin positions" / "pin hole depth error",
    DESIGN.md 2026-07-28) onto a [pcb] job's assembled bytes.

    Two phases share these checks — the spot-face and the peck — because the
    laws are the same for both: cut ONLY at the configured pin positions, and
    nowhere near the board. The depth echo is echo_checks' job (the pin
    phases are ordinary phases to it), so what is left here is position and
    the board keep-out.

    The board keep-out is the pin keep-out read from the other end: on side 1
    the pins are holes in the blank's waste and on side 2 they hold steel, so
    in neither setup may pin work reach the board. `pin_keepout_checks` says
    the same thing about every OTHER program.
    """
    p = job.phases[s.phase]
    tool = job.phase_tool(s.phase)
    pos = [(float(x), float(y)) for x, y in p["positions"]]
    off = np.min(np.stack([np.hypot(s.x - px, s.y - py) for px, py in pos]),
                 axis=0)
    worst = float(off.max())
    k = int(off.argmax())
    out = [Check(f"{s.phase} only at pin positions", worst,
                 f"<= {PIN_POS_TOL}", worst <= PIN_POS_TOL,
                 f"{len(pos)} pins, T{tool.num} d{tool.diameter:g}, worst at "
                 f"{s.at(k)}")]
    # the board box in THIS side's machine frame, from the derived transform
    bxs, bys = boardmaps.machine_xy(
        maps.offset, maps.mirror, [maps.tight.x0, maps.tight.x1],
        [maps.tight.y0, maps.tight.y1])
    x0, x1 = float(min(bxs)), float(max(bxs))
    y0, y1 = float(min(bys)), float(max(bys))
    inside = np.minimum.reduce([s.x - x0, x1 - s.x, s.y - y0, y1 - s.y]) \
        + tool.radius
    worst_in = float(inside.max())
    k = int(inside.argmax())
    out.append(Check(f"{s.phase} clear of the board", worst_in, "<= 0",
                     worst_in <= 0.0,
                     f"board box ({x0:.2f},{y0:.2f})..({x1:.2f},{y1:.2f}); "
                     f"worst at {s.at(k)}"))
    return out


def pin_keepout_checks(job: PcbJob,
                       samples: dict[str, Samples]) -> list[Check]:
    """The pins law's other half, on every program that is NOT the pin block:
    no cutting sample within pin radius + PIN_CLEAR of a pin.

    On side 2 the pins are steel and flush and a tool that crosses one breaks
    (the coin lane's "pin keep-out (side 2)"); on side 1 they are holes whose
    walls have to survive to register the flip. So the check runs on BOTH
    setups' programs — a carry-over, not a copy: PIN_CLEAR is twosided.py's
    constant, imported, not restated.
    """
    if not job.pins:
        return []
    pin_r = float(job.pins["diameter"]) / 2
    keep = pin_r + PIN_CLEAR
    worst = float("inf")
    at = ""
    for ph, s in samples.items():
        if ph in PIN_PHASES:
            continue
        for px, py in job.pins["positions"]:
            d = np.hypot(s.x - float(px), s.y - float(py)) \
                - job.phase_tool(ph).radius
            k = int(d.argmin())
            if float(d[k]) < worst:
                worst = float(d[k])
                at = f"pin ({px},{py}), {s.at(k)}"
    if worst == float("inf"):
        return []
    return [Check("pin keep-out", worst, f">= {keep}", worst >= keep,
                  f"worst {at} (tool EDGE to pin centre; pin r {pin_r:g} + "
                  f"{PIN_CLEAR} clear)")]


PHASE_CHECKS = {"iso": iso_checks, "clear": clear_checks,
                "scrub": scrub_checks, "drills": hole_checks,
                "cutout": cutout_checks,
                "pinspot": pin_checks, "pindrill": pin_checks}


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
        # The registration peck cycle's re-entry rapid descends INTO the hole
        # it just drilled, which this proxy cannot tell from a dive into
        # virgin blank — it has no stock model, that being the whole reason it
        # exists. The pin stages are therefore excluded HERE and judged by the
        # honest check instead: the pins program is a CARVING program, so
        # `sheet rapid-vs-stock` measures its rapids against the simulated
        # sheet, where a hole is a hole (ops/drill.py's own note). Strictly
        # stronger, not weaker.
        if m.stage_labels:
            pin_stages = [n for n, lb in enumerate(m.stage_labels)
                          if lb in tuple(f"pcb-{p}" for p in PIN_PHASES)]
            for n in pin_stages:
                bad &= m.stage != n
        worst = float(nz[bad].min()) if bad.any() else 0.0
        detail = f"{int(rap.sum())} rapids"
        if m.stage_labels and pin_stages:
            detail += (" (pin peck re-entries excluded — see sheet "
                       "rapid-vs-stock)")
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


# -------------------------------------------------------- sheet stock model
# Moved here from session.py (2026-07-30): WS6 defined the model so the
# VIEWER could simulate, but a bare verify_pcb() skipped it — the gate must
# be the strictest reader, so the definition and the verdicts live with the
# gate and session.py imports them back for its previews.
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


def _pin_pad(job: PcbJob, bx0: float, by0: float,
             bx1: float, by1: float) -> float:
    """How far past the board the modelled sheet must reach to hold the
    registration-pin work. The pins sit in the blank's WASTE, several
    millimetres outside the outline (orbit SPEC: 8mm), so a window sized for
    the board alone would put the pin bores outside the simulation and
    `sheet containment` would report an escape that is really a too-small
    model. Zero for a single-sided job, which has no pins."""
    if not job.pins:
        return 0.0
    r = float(job.pins["diameter"]) / 2 + PIN_CLEAR
    worst = max(max(bx0 - x, x - bx1, by0 - y, y - by1)
                for x, y in job.pins["positions"])
    return max(0.0, worst + r + 1.0)


def sheet_stock(job: PcbJob, ppm: float = SHEET_PPM) -> SheetStock:
    """DERIVE the sheet stock of a [pcb] job. No hand-typed geometry: the
    board window comes from the Edge.Cuts coordinate words and the transform
    from boardmaps.machine_offset, the same pair the templated Tcl and the
    raster checks both use.

    The arc cross-check in boardmaps.extents is board_maps' job (it runs it
    with gerbv); this window is derived without it so the sheet sim can still
    run on a box with no gerbv, where the raster checks refuse and the
    session says UNVERIFIED.
    """
    tight = boardmaps.extents(job.files["edge"], cross_check=False)
    off = boardmaps.machine_offset(tight, job.anchor, job.mirror)
    # the derived frame, from the ONE place that spells its sign out
    xs, ys = boardmaps.machine_xy(off, job.mirror,
                                 [tight.x0, tight.x1], [tight.y0, tight.y1])
    bx0, bx1 = float(min(xs)), float(max(xs))
    by0, by1 = float(min(ys)), float(max(ys))
    pad = max(window_pad(job), _pin_pad(job, bx0, by0, bx1, by1))
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
    be callable on one by accident. The PCB gate is verify_pcb; this type
    carries the stock simulation to it, it does not replace it."""
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


def carve_program(sj: SheetJob, sheet: SheetStock,
                  nc_path) -> simulate.CarveResult:
    """Simulate one MILL-dialect [pcb] program on the sheet. Same strict
    parser, same kernel, same measurements as any other job — the whole point
    of defining the sheet stock was to stop needing a special path."""
    return simulate.carve(nc_path, sj, ppm=sheet.ppm, extra_half=0.0,
                          step=0.06, check=True)


def _pin_specs(job: PcbJob, sj: SheetJob) -> list[tuple[float, float, float]]:
    """(x, y, radius) of each registration pin hole, or [] if this job has
    none. Only meaningful for the program that bores them; every other
    program's floor is judged without any exclusion at all."""
    if not job.pins or not job.has_phase("pindrill"):
        return []
    r = sj.tool(job.phases["pindrill"]["tool"]).radius
    return [(float(x), float(y), r)
            for x, y in job.phases["pindrill"]["positions"]]


def _pin_mask(sheet: SheetStock, specs) -> np.ndarray:
    """The pin discs on the carve grid, in the ONE mapping (Article IV:
    x = j/ppm - half, y = half - i/ppm — never re-derived)."""
    n, ppm, half = sheet.n, sheet.ppm, sheet.half
    yy, xx = np.mgrid[0:n, 0:n]
    xw = xx / ppm - half
    yw = half - yy / ppm
    mask = np.zeros((n, n), bool)
    for px, py, r in specs:
        mask |= np.hypot(xw - px, yw - py) <= r + 0.3   # verify.py's 0.3
    return mask


def sheet_checks(job: PcbJob, sheet: SheetStock, sj: SheetJob,
                 res: simulate.CarveResult) -> list[Check]:
    """The geometric + physics checks the PCB gate could not run before the
    sheet existed. Named with a `sheet ` prefix so a PCB report never
    confuses them with the board-map checks, and every one of them is an
    ADDITION.
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
    #
    # Registration-pin holes are the ONE legal way past that floor — through
    # the blank into the spoilboard — so they are excluded from it and made
    # to account for themselves instead, exactly as verify.py's "depth floor
    # (excl. pin holes)" does for the coin lane. The exclusion is by TOOL
    # (the twist drill), so no board phase can hide behind it.
    limit = -(sheet.thickness + 0.5)      # verify.MAX_OVERCUT, same number
    bed = -(sheet.thickness + sheet.spoil - 2.0)
    pin_specs = _pin_specs(job, sj)
    if not pin_specs:
        floor = min(res.min_cut_z, float(res.stock.min()))
        out.append(Check("sheet depth floor", floor, f">= {limit:.3f}",
                         floor >= limit,
                         f"{sheet.thickness}mm blank + 0.5 breakthrough "
                         f"allowance"))
    else:
        is_drill = np.array([sj.tool(int(t)).type == "drill"
                             for t in m.tool_num])
        cut = m.motion == 1
        nz = np.minimum(m.z0, m.z1)
        sel = cut & ~is_drill
        worst_nondrill = float(nz[sel].min()) if sel.any() else 0.0
        pinmask = _pin_mask(sheet, pin_specs)
        floor = min(worst_nondrill, float(res.stock[~pinmask].min()))
        out.append(Check("sheet depth floor (excl. pin holes)", floor,
                         f">= {limit:.3f}", floor >= limit,
                         f"{len(pin_specs)} pin holes excluded by TOOL (the "
                         f"twist drill), {sheet.thickness}mm blank + 0.5"))
        pin_floor = float(nz[cut & is_drill].min()) \
            if (cut & is_drill).any() else 0.0
        out.append(Check("sheet pin bores clear of the bed", pin_floor,
                         f">= {bed:.3f}", pin_floor >= bed,
                         f"{sheet.spoil}mm spoilboard takes the pin holes"))
        floor = min(floor, pin_floor)
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


# --------------------------------------------------------------------------
# Hand-solder / DFM design checks (the operator's 2026-07-31 render review +
# guides/pcb-dfm-notes.md). These judge the DESIGN as the gerbers deliver
# it — raster-only, net-free: pour membership comes from a flood fill of the
# copper ink itself, so a wrong netlist cannot fool them.

POUR_FRACTION = 0.25   # a copper component holding >=25% of ALL copper ink
                       # IS the pour; no routed net on a board this size
                       # comes close (measured: coupon pour 55%, VCC tree 4%)
POUR_MIN_MM2 = 50.0    # ...AND a pour is a PLANE: on a tiny board with no
                       # pour at all, a single pad flash can clear the 25%
                       # fraction (the twosided fixture: 40%); a component
                       # under 50mm2 cannot heat-sink an iron and is no pour
SPOKE_MIN = 0.40       # dfm-notes §1: the milled starved-thermal floor —
                       # drawn 0.6 spokes deliver ~0.52 after the 0.08 kerf
                       # overcut; below 0.40 the relief is decorative
SPOKE_COUNT_MIN = 2    # PCBWay/BestPCBs practice: one spoke is a fuse
SPOKE_RIM_CLEAR = 0.06 # where the moat annulus BEGINS: one raster clearance
                       # outside the pad's mask aperture, so the annulus
                       # starts in the relief and not on the pad's own rim
MOAT_REACH = 0.9       # how far out the moat is looked for at all. The moat's
                       # own outer edge is MEASURED per pad (_moat_edge); this
                       # only bounds the search
EDT_MARGIN = 1.0       # the local width transform's crop margin. A spoke
                       # width is read from copper within the moat, so a crop
                       # this far outside it holds every distance the check
                       # reads; anything wider than the margin is pour, and
                       # under-reading pour width cannot move a minimum
SOLID_FRACTION = 0.85  # a moat ring mostly copper = solid connect — the
                       # heat-sunk hand joint the review complained about
SCRUB_PAD_MIN = 0.70   # dfm-notes §9: 2·(scrub_r + window) + 2·deflate;
                       # a narrower aperture gets no scrub lap and ships
                       # under mask while every per-phase check passes
SILK_H_MIN = 1.0       # both fab houses' legend floor (JLCPCB std font 1.0)
SILK_RATIO = (1/7.5, 1/3.5)   # stroke:height band around JLCPCB's 1:6
SILK_GAP_MIN = 0.15    # JLCPCB's published inter-stroke floor, measured as
                       # TRUE ink distance. The dfm-notes aspiration of 0.3
                       # exceeds what KiCad's stroke font can deliver between
                       # glyphs (measured 0.150 at h1.5 on this board); the
                       # silk ladder's 1.0 rung is the bench gauge for
                       # whether dose bloom bridges 0.15 — if it does, that
                       # is an Article II incident and this bar rises


_EIGHT = ndimage.generate_binary_structure(2, 2)   # copper touching at a
#                        corner is one piece of metal; a rasterised diagonal
#                        edge is an artifact of the grid, not a gap


def _ring_profile(cu: np.ndarray, win, cx: float, cy: float,
                  r: float, n: int = 720) -> np.ndarray:
    """Copper occupancy around a circle, bilinear-sampled (1 = ink)."""
    th = np.linspace(0.0, 2 * np.pi, n, endpoint=False)
    i, j = win.world_to_px(cx + r * np.cos(th), cy + r * np.sin(th))
    return ndimage.map_coordinates(np.asarray(cu, dtype=np.float32),
                                   [i - 0.5, j - 0.5], order=1)


def _moat_edge(lab: np.ndarray, pad_lab: int, win, cx: float, cy: float,
               r_in: float, r_max: float, n: int = 720) -> float:
    """The radius of the POUR's inner edge around one pad — the far side of
    the thermal moat — measured off the artwork rather than declared.

    A relief keepout is a DISC, so the pour's inner edge sits at one radius.
    Walking outward from the rim circle along each of `n` bearings, the first
    copper of the pad's own component is either that edge or a neighbouring
    net's clearance that the moat leaked into. A leak can only move the
    reading OUTWARD (there is no way for it to pull copper inward), and a
    speck inside the keepout can only move it inward, so the MEDIAN over the
    bearings that start in void is the honest read — both tails are
    contamination and neither is more than a minority. Bearings that start on
    copper are spokes and are excluded: they never see the moat.

    Measured on orbit's artwork, this reads r_ap + 0.44 on all eight judged
    pads of both sides (1.678 for its Ø1 vias, 2.231 for the Ø1.5), against a
    5th-to-95th-percentile scatter of ~0.03."""
    px = 1.0 / win.ppmm
    rs = np.arange(r_in, r_max + 1e-9, px)
    th = np.linspace(0.0, 2 * np.pi, n, endpoint=False)
    i, j = win.world_to_px(cx + rs[:, None] * np.cos(th),
                           cy + rs[:, None] * np.sin(th))
    ring = lab[np.clip((i - 0.5).round().astype(int), 0, lab.shape[0] - 1),
               np.clip((j - 0.5).round().astype(int), 0,
                       lab.shape[1] - 1)] == pad_lab
    seen = ring.any(axis=0) & ~ring[0]      # a void bearing that finds copper
    if not seen.any():
        return r_max
    return float(np.median(rs[np.argmax(ring, axis=0)[seen]]))


def _bottleneck(width: np.ndarray, comp: np.ndarray, src: np.ndarray,
                dst: np.ndarray) -> float:
    """The widest channel through `comp` from `src` to `dst`: the largest w
    for which the pixels of `comp` at least w wide still join the two. For a
    bar of constant width this IS its width, and for a waisted spoke it is
    the waist — the real constriction, found without assuming where it is."""
    vals = np.unique(width[comp])
    vals = vals[vals > 0.0]
    lo, hi, best = 0, vals.size - 1, 0.0
    while lo <= hi:
        mid = (lo + hi) // 2
        sel = comp & (width >= vals[mid])
        lb, n = ndimage.label(sel, structure=_EIGHT)
        ka = np.unique(lb[sel & src])
        kb = np.unique(lb[sel & dst])
        if n and np.intersect1d(ka[ka > 0], kb[kb > 0]).size:
            best, lo = float(vals[mid]), mid + 1
        else:
            hi = mid - 1
    return best


def _moat_spokes(lab: np.ndarray, pad_lab: int, win, cx: float, cy: float,
                 r_ap: float) -> tuple[float, list[float]]:
    """The thermal spokes of one pad: -> (moat edge, spoke widths, widest
    first).

    A SPOKE is a connected copper component that CROSSES THE WHOLE MOAT
    ANNULUS, from the pad's rim clearance to the pour's inner edge. A
    component that does not span the annulus is not a spoke and contributes
    nothing — neither a passing width nor a failure. That is the 2026-08-02
    ruling (see DESIGN.md, "the spoke check measured a ring, not a spoke"):
    the retired statistic read ONE ring, where the tapering tip of a spoke
    truncated inside the moat crosses as a hair.

    Each spoke's width is the narrowest point of its crossing, measured as
    the widest channel it offers (`_bottleneck`) over a width transform of
    the real copper — 2·(EDT − eps), the lane's half-pixel convention, so a
    drawn 0.4 spoke reads 0.39–0.41 and never high. The annulus boundaries
    are radii, not copper edges, so the transform is taken on the copper
    BEFORE the annulus is cut out: a band edge can never impersonate a
    constriction."""
    px = 1.0 / win.ppmm
    r_in = r_ap + SPOKE_RIM_CLEAR
    edge = _moat_edge(lab, pad_lab, win, cx, cy, r_in, r_ap + MOAT_REACH)
    half = edge + EDT_MARGIN
    pad = int(np.ceil(half * win.ppmm)) + 2
    ic, jc = win.world_to_px(np.array([cx]), np.array([cy]))
    ci, cj = int(round(ic[0] - 0.5)), int(round(jc[0] - 0.5))
    h, w = lab.shape
    i0, i1 = max(0, ci - pad), min(h, ci + pad + 1)
    j0, j1 = max(0, cj - pad), min(w, cj + pad + 1)
    cu = lab[i0:i1, j0:j1] == pad_lab
    gi, gj = np.mgrid[i0:i1, j0:j1]
    bx, by = win.px_to_world(gi, gj)
    rr = np.hypot(bx - cx, by - cy)
    width = 2.0 * (ndimage.distance_transform_edt(cu) * px - 0.5 * px)
    band = cu & (rr >= r_in) & (rr <= edge)
    cl, nc = ndimage.label(band, structure=_EIGHT)
    if nc == 0:
        return edge, []
    idx = np.arange(1, nc + 1)
    rmin = ndimage.minimum(rr, cl, index=idx)
    rmax = ndimage.maximum(rr, cl, index=idx)
    src, dst = band & (rr <= r_in + px), band & (rr >= edge - px)
    boxes = ndimage.find_objects(cl)
    out = []
    for k in idx[(rmin <= r_in + px) & (rmax >= edge - px)]:
        s = boxes[k - 1]
        out.append(_bottleneck(width[s], cl[s] == k, src[s], dst[s]))
    return edge, sorted(out, reverse=True)


def thermal_checks(job: PcbJob, maps: BoardMaps) -> list[Check]:
    """Hole-centered pads that belong to the POUR must connect through
    thermal spokes an iron can beat (the operator's 2026-07-31 review:
    solid pour contact heat-sinks the joint; dfm-notes §1). For each
    Excellon bore whose center carries a mask aperture, the pad's copper
    component is flooded; if that component is the pour, the pad's moat
    ring (the min-copper circle between the pad edge and pad edge + 0.9)
    must be mostly void — not a solid connect — and the pad must carry at
    least SPOKE_COUNT_MIN SPOKES OF AT LEAST SPOKE_MIN, a spoke being a
    copper component that crosses the whole moat annulus (`_moat_spokes`).
    Pads on routed nets (small components) are exempt — their heat path is
    their tracks, which the review accepts.

    The verdict is that count-and-width pair, so `thermal spoke width`
    reports each pad's SPOKE_COUNT_MIN-th widest spoke: bar-passing means
    "this pad has two spokes at least this wide". A crossing narrower than
    the bar is neither a conviction nor a credit — starvation is what this
    check is for, and a hair of extra copper starves nothing — but they are
    counted into the note, because a hairline crossing is something the mill
    should be told about even when the joint is well fed."""
    cu = maps.layers["cu"]
    lab, _ = ndimage.label(cu)
    sizes = np.bincount(lab.ravel())
    total = float(cu.sum())
    cuf = cu.astype(np.float32)         # sampled ~40x per judged pad below
    px = 1.0 / maps.win.ppmm
    in_mask = maps.dist("in_mask")
    worst_frac, worst_cnt, worst_w = 0.0, 99, 9e9
    who_f = who_c = who_w = ""
    judged = skipped = hairs = 0
    for hx, hy, hd in maps.holes:
        i, j = maps.win.world_to_px(np.array([hx]), np.array([hy]))
        ii, jj = int(round(i[0] - 0.5)), int(round(j[0] - 0.5))
        h, w = maps.win.shape
        if not (0 <= ii < h and 0 <= jj < w) or in_mask[ii, jj] <= 0:
            skipped += 1
            continue                        # bare bore: M3, gauge class
        r_ap = float(in_mask[ii, jj])       # aperture radius at the center
        rmid = (hd / 2 + r_ap) / 2
        i2, j2 = maps.win.world_to_px(hx + rmid * np.cos(np.linspace(0, 6.28, 16)),
                                      hy + rmid * np.sin(np.linspace(0, 6.28, 16)))
        labs = lab[np.clip((i2 - 0.5).round().astype(int), 0, h - 1),
                   np.clip((j2 - 0.5).round().astype(int), 0, w - 1)]
        labs = labs[labs > 0]
        if labs.size == 0:
            skipped += 1
            continue
        pad_lab = int(np.bincount(labs).argmax())
        comp_mm2 = sizes[pad_lab] * px * px
        if sizes[pad_lab] < POUR_FRACTION * total or comp_mm2 < POUR_MIN_MM2:
            skipped += 1
            continue        # routed-net pad (tracks carry it) or no pour
        judged += 1
        best = (1.1, r_ap + SPOKE_RIM_CLEAR)
        for r in np.arange(r_ap + SPOKE_RIM_CLEAR, r_ap + MOAT_REACH, 0.02):
            frac = float(_ring_profile(cuf, maps.win, hx, hy, r).mean())
            if frac < best[0]:
                best = (frac, r)
        frac, rm = best
        edge, spokes = _moat_spokes(lab, pad_lab, maps.win, hx, hy, r_ap)
        runs = len(spokes)
        # the count-th widest spoke: >= the bar means the pad has that many
        # spokes at least that wide. Too few spokes reads 0.0 — starved.
        minw = spokes[SPOKE_COUNT_MIN - 1] if runs >= SPOKE_COUNT_MIN else 0.0
        hairs += sum(1 for s in spokes if s < SPOKE_MIN - 3 * px)
        tag = (f"bore ({hx:.2f},{hy:.2f}) d{hd:g} across moat "
               f"r{r_ap + SPOKE_RIM_CLEAR:.2f}..{edge:.2f}")
        if frac > worst_frac:
            worst_frac, who_f = frac, f"bore ({hx:.2f},{hy:.2f}) d{hd:g} " \
                                      f"at moat r{rm:.2f}"
        if runs < worst_cnt:
            worst_cnt, who_c = runs, tag
        if minw < worst_w:
            worst_w, who_w = minw, tag
    note = (f"{judged} pour pads judged, {skipped} exempt (bare or routed)"
            + (f", {hairs} sub-bar crossings noted" if hairs else ""))
    if judged == 0:
        return [Check("thermal spokes", 0.0, "n/a", True,
                      f"no pour-connected hole pads — {note}")]
    return [
        Check("thermal solid connect", worst_frac,
              f"<= {SOLID_FRACTION:g} of the moat ring",
              worst_frac <= SOLID_FRACTION, f"{who_f}; {note}"),
        Check("thermal spoke count", float(worst_cnt),
              f">= {SPOKE_COUNT_MIN}", worst_cnt >= SPOKE_COUNT_MIN,
              f"{who_c}; spanning the moat; {note}"),
        Check("thermal spoke width", worst_w,
              f">= {SPOKE_MIN:g}", worst_w >= SPOKE_MIN - 3 * px,
              f"{who_w}; {SPOKE_COUNT_MIN}nd widest spanning spoke, "
              f"delivered, raster tol 3px; {note}"),
    ]


def scrubbability_checks(job: PcbJob, maps: BoardMaps) -> list[Check]:
    """Every mask aperture must be wide enough for the spring tool to lap
    (dfm-notes §9: 2·(scrub_r + window) + 2·deflate = 0.70 with the lane's
    numbers). Below that FlatCAM's paint gets a marginal region — the class
    it silently skips (the ncc/clear-opening incidents) — and the pad ships
    under mask with `scrub coverage` deliberately un-barred. Judged on the
    mask raster: narrow dimension = 2 × the aperture's deepest interior
    point."""
    mask = maps.layers["mask"]
    lab, n = ndimage.label(mask)
    if n == 0:
        return [Check("pad scrubbability", 0.0, "unmeasurable", False,
                      "no mask apertures at all")]
    depth = maps.dist("in_mask")
    px = 1.0 / maps.win.ppmm
    worst, who = 9e9, ""
    for c in range(1, n + 1):
        m = lab == c
        narrow = 2 * float(depth[m].max())
        if narrow < worst:
            ii, jj = np.unravel_index(np.argmax(depth * m), depth.shape)
            bx, by = maps.win.px_to_world(np.array([ii + 0.5]),
                                          np.array([jj + 0.5]))
            worst, who = narrow, f"aperture at ({bx[0]:.2f},{by[0]:.2f})"
    return [Check("pad scrubbability", worst, f">= {SCRUB_PAD_MIN:g}",
                  worst >= SCRUB_PAD_MIN - 2 * px,
                  f"{who} of {n} apertures; raster tol 2px")]


MASK_RING_BAND = (0.05, 0.30)   # ring probed just outside the hole wall; the
                                # outer 0.30 sits inside the 0.6 annular law,
                                # so a pad's ring is always fully pad copper
MASK_RING_OPEN = 0.95           # and its aperture (== pad, expansion 0
                                # asserted board-side) must expose ~all of it


def mask_blind_checks(job: PcbJob, maps: BoardMaps) -> list[Check]:
    """Every drilled hole that carries a copper pad must open a mask
    aperture over that pad's ring. Bench incident 2026-07-30: 17 THT pads
    (JP1-7 both ends, SW1 all three) shipped with copper-only layer sets —
    pcbnew's LSET.AllCuMask() means "the set of all Cu layers", not
    "Cu + Mask", and the layout script's own mask assert SKIPPED pads that
    weren't on B.Mask, the exact defect it existed to refuse. The cured
    mask sealed all 17 pads; the scrub phase paints mask apertures, so it
    had nothing to paint there, and the operator's loupe — not the gate —
    found the sealed pads. Raster-judged so any pad spelling counts: a
    copper ring around a hole means SOLDERED, and a soldered ring the mask
    does not expose is a refusal. Two exemptions, both because nothing
    solders there: bare bores (no copper ring — the M3 mounts), and
    DECLARED flip gauges — [[rules.gauge]] entries, which the grammar
    already forces to name a real hole and carry a written reason. An
    undeclared sealed ring stays a refusal; the gauge declaration is the
    one honest way out."""
    cu, mask = maps.layers["cu"], maps.layers["mask"]
    H, W = cu.shape
    r_lo, r_hi = MASK_RING_BAND
    gauge_xy = [(float(gx), float(gy))
                for g in job.rules.get("gauge", [])
                for gx, gy in g.get("positions", [])]
    worst, who, blind, ringed = 2.0, "", [], 0
    for hx, hy, hd in maps.holes:
        if any(abs(hx - gx) <= GAUGE_MATCH_TOL
               and abs(hy - gy) <= GAUGE_MATCH_TOL for gx, gy in gauge_xy):
            continue                 # a declared, reasoned flip gauge
        i, j = maps.win.world_to_px(np.array([hx]), np.array([hy]))
        i, j = int(round(float(i[0]))), int(round(float(j[0])))
        R = int(np.ceil((hd / 2 + r_hi) * maps.win.ppmm)) + 1
        i0, i1 = max(0, i - R), min(H, i + R + 1)
        j0, j1 = max(0, j - R), min(W, j + R + 1)
        ii, jj = np.mgrid[i0:i1, j0:j1]
        d = np.hypot(ii - i, jj - j) / maps.win.ppmm
        ring = (d >= hd / 2 + r_lo) & (d <= hd / 2 + r_hi)
        if not ring.any() or cu[i0:i1, j0:j1][ring].mean() < 0.5:
            continue                       # bare bore: nothing solders here
        ringed += 1
        frac = float(mask[i0:i1, j0:j1][ring].mean())
        if frac < worst:
            worst, who = frac, f"hole Ø{hd:g} at ({hx:.2f},{hy:.2f})"
        if frac < MASK_RING_OPEN:
            blind.append(f"({hx:.2f},{hy:.2f})Ø{hd:g}")
    if ringed == 0:
        # vacuously true: nothing solders through this board's holes (an
        # SMD-only or bare-bore board), so there is no ring to seal. A
        # MISSING hole schedule cannot land here — the grammar requires
        # the Excellon and hole_checks counts the bores.
        return [Check("mask-blind pads", 1.0, f">= {MASK_RING_OPEN:g}",
                      True, "no copper-ringed holes — nothing to seal")]
    note = (f"{ringed} soldered rings; sealed: {', '.join(blind[:6])}"
            + (" ..." if len(blind) > 6 else "") if blind
            else f"{ringed} soldered rings, every one exposed; worst {who}")
    return [Check("mask-blind pads", worst, f">= {MASK_RING_OPEN:g}",
                  not blind, note)]


def silk_metric_checks(job: PcbJob, maps: BoardMaps) -> list[Check]:
    """Legend metrics on the silk ink (dfm-notes §9, JLCPCB floors): text
    height >= 1.0, stroke:height inside the 1:7.5..1:3.5 band around
    JLCPCB's 1:6, inter-glyph gap >= 0.30 (their 0.15/0.2 raised for laser
    dose bloom). Glyphs cluster into lines by bbox adjacency; a line needs
    >= 3 glyphs to be judged as text — shorter marks (polarity ticks, the
    ladder gauge bars) are graphics, not legend, and are exempt by count."""
    # the standard maps carry cu/mask/edge; silk is rasterized on demand
    silk = maps.layers.get("silk")
    if silk is None:
        silk = boardmaps.rasterize(job.files["silk"], maps.win)
    lab, n = ndimage.label(silk, structure=np.ones((3, 3), int))
    if n == 0:
        return [Check("silk legend metrics", 0.0, "n/a", True, "no silk ink")]
    depth = boardmaps.dist_mm(~silk, maps.win)
    px = 1.0 / maps.win.ppmm
    boxes = ndimage.find_objects(lab)
    glyphs = []
    for c, sl in enumerate(boxes, 1):
        if sl is None:
            continue
        gh = (sl[0].stop - sl[0].start) * px
        gw = (sl[1].stop - sl[1].start) * px
        thick = 2 * float(depth[sl][lab[sl] == c].max())
        glyphs.append((sl[1].start * px, sl[1].stop * px,
                       sl[0].start * px, sl[0].stop * px, gh, gw, thick, c))
    glyphs.sort()
    lines: list[list] = []
    for g in glyphs:
        for ln in lines:
            last = ln[-1]
            yo = min(g[3], last[3]) - max(g[2], last[2])
            if yo > 0.3 * min(g[4], last[4]) and 0 <= g[0] - last[1] <= 1.6:
                ln.append(g)
                break
        else:
            lines.append([g])
    texts = [ln for ln in lines if len(ln) >= 3]
    if not texts:
        return [Check("silk legend metrics", 0.0, "n/a", True,
                      f"{n} silk marks, none form a text line")]
    h_min, r_lo, r_hi, gap_min = 9e9, 9e9, 0.0, 9e9
    who_h = who_g = ""
    for ln in texts:
        hh = float(np.median([g[4] for g in ln]))
        tt = float(np.median([g[6] for g in ln]))
        if hh < h_min:
            h_min, who_h = hh, f"line at ({ln[0][0]:.1f},{ln[0][2]:.1f})"
        r_lo, r_hi = min(r_lo, tt / hh), max(r_hi, tt / hh)
        for a, b in zip(ln, ln[1:]):
            # TRUE ink-to-ink distance (a bbox gap understates it: a 'V'
            # after a '5' overlaps boxes while the strokes stay clear) —
            # EDT of glyph a's ink on a crop, sampled at glyph b's pixels
            x0 = max(0, int((a[1] - 0.6) / px)); x1 = int((b[0] + 0.9) / px)
            y0 = max(0, int((min(a[2], b[2]) - 0.3) / px))
            y1 = int((max(a[3], b[3]) + 0.3) / px)
            crop = lab[y0:y1, x0:x1]
            am, bm = crop == a[7], crop == b[7]
            if not am.any() or not bm.any():
                continue
            d = ndimage.distance_transform_edt(~am)[bm].min() * px
            if d < gap_min:
                gap_min, who_g = d, f"gap at ({a[1]:.1f},{a[2]:.1f})"
    return [
        Check("silk text height", h_min, f">= {SILK_H_MIN:g}",
              h_min >= SILK_H_MIN - 2 * px,
              f"{len(texts)} text lines; smallest {who_h}"),
        Check("silk stroke ratio", r_hi,
              f"{SILK_RATIO[0]:.3f} .. {SILK_RATIO[1]:.3f}",
              SILK_RATIO[0] <= r_lo and r_hi <= SILK_RATIO[1],
              f"ratios {r_lo:.3f}..{r_hi:.3f}"),
        Check("silk glyph gap", gap_min, f">= {SILK_GAP_MIN:g}",
              gap_min >= SILK_GAP_MIN - 2 * px,
              f"{who_g}; true ink distance, raster tol 2px"),
    ]


def residual_checks(job: PcbJob, maps: BoardMaps, sheet: SheetStock,
                    res: simulate.CarveResult) -> list[Check]:
    """Remaining copper vs DESIGNED copper — the bridging-sliver incident
    (2026-07-30). Carve the mill program, keep every cell the cut left
    shallower than RESIDUAL_CUT_MIN, take the cells farther than
    RESIDUAL_TOL from designed copper ink (UNDESIGNED copper), cluster them,
    and apply the FRAGMENT law: a cluster narrower than RESIDUAL_MAX_WIDTH
    or smaller than RESIDUAL_MIN_AREA is a sliver — copper no design could
    own, that flakes, lifts with the mask, and bridges.

    Division of labour: designed copper that is MISSING is `iso coverage`'s
    territory (a skipped island); copper that ENCROACHES on a gap while
    still hugging designed ink is `iso containment`'s; a LARGE floating
    plane is stated scope (see RESIDUAL_MIN_AREA) — the generator clears it
    with `ncc -box edge`, and where a job deliberately keeps a field, a
    plane is not a fragment. This check hunts what everything else is blind
    to: the ridges single-pass isolation left in every gap wider than its
    reach and narrower than the clearing tool's.

    Evaluated INSIDE the outline (edge ink flood-filled) and outside the
    cutout's rim band (RESIDUAL_EDGE_EXCL): the sheet padding is raw blank,
    and the rim strip is removed by the edge cut, not the mill program.
    """
    if "cu" not in maps.layers or "edge" not in maps.layers:
        return [Check("residual copper", 0.0, "unmeasurable", False,
                      "no cu/edge raster — these board maps were built "
                      "without gerbv")]
    stock = sheet.crop(res.stock)
    remaining = stock > -RESIDUAL_CUT_MIN
    # cell centers, machine frame (Article IV's mapping, never re-derived)
    ii, jj = np.nonzero(remaining)
    x = (jj + sheet.j_off) / sheet.ppm - sheet.half
    y = sheet.half - (ii + sheet.i_off) / sheet.ppm
    # prefilter to the board rectangle: the raster window holds it, and
    # everything outside is sheet-padding blank by construction
    eps = 0.5 / sheet.ppm
    inb = ((x > sheet.bx0 + eps) & (x < sheet.bx1 - eps)
           & (y > sheet.by0 + eps) & (y < sheet.by1 - eps))
    if not inb.any():
        return [Check("residual copper", 0.0, f"<= {RESIDUAL_TOL}", True,
                      "no remaining copper inside the board rectangle")]
    bx, by = maps.to_board(x[inb], y[inb])
    inside = maps.sample(_outline_fill(maps).astype(np.float32), bx, by) > 0.5
    d_edge = maps.sample(maps.dist("edge"), bx, by)
    d_cu = maps.sample(maps.dist("cu"), bx, by)
    keep = inside & (d_edge > RESIDUAL_EDGE_EXCL)
    if not keep.any():
        return [Check("residual copper", 0.0, f"<= {RESIDUAL_TOL}", True,
                      "no remaining copper inside the outline past the rim "
                      "band")]
    d = d_cu[keep]
    und = d > RESIDUAL_TOL
    if not und.any():
        worst = float(d.max())
        return [Check("residual copper", worst, f"<= {RESIDUAL_TOL}", True,
                      f"every remaining cell within {worst:.3f} of designed "
                      f"copper ({int(keep.sum())} cells judged)")]
    # cluster the undesigned cells and apply the FRAGMENT law: a cluster is
    # a sliver when it is too narrow or too small for any design to own it
    # (the loupe finds ridges, not pixels). Width = twice the largest
    # inscribed-disc radius, from an EDT inside the cluster mask.
    grid = np.zeros(stock.shape, bool)
    sel = np.nonzero(inb)[0][np.nonzero(keep)[0][und]]
    grid[ii[sel], jj[sel]] = True
    lab, n = ndimage.label(grid)
    idx = np.arange(1, n + 1)
    widths = 2 * ndimage.maximum(
        ndimage.distance_transform_edt(grid) / sheet.ppm, lab, idx)
    areas = ndimage.sum(grid, lab, idx) / sheet.ppm ** 2
    frag = (widths < RESIDUAL_MAX_WIDTH) | (areas < RESIDUAL_MIN_AREA)
    n_slivers = int(frag.sum())
    if n_slivers == 0:
        return [Check(
            "residual copper", 0.0, f"<= {RESIDUAL_TOL}", True,
            f"{n} undesigned region(s) totalling "
            f"{float(areas.sum()):.2f}mm2 remain, all wider than "
            f"{RESIDUAL_MAX_WIDTH} and larger than {RESIDUAL_MIN_AREA}mm2 "
            f"— floating planes, not fragments (stated scope)")]
    # worst = the sliver cell farthest from designed copper, for the report
    cell_lab = lab[ii[sel], jj[sel]]
    cell_is_sliver = np.isin(cell_lab, idx[frag])
    dd = d[und][cell_is_sliver]
    k = int(dd.argmax())
    worst = float(dd.max())
    wx = bx[keep][und][cell_is_sliver][k]
    wy = by[keep][und][cell_is_sliver][k]
    area = float(areas[frag].sum())
    detail = (f"{n_slivers} sliver(s) ({area:.2f}mm2) survive the mill "
              f"program — fragments narrower than {RESIDUAL_MAX_WIDTH} or "
              f"under {RESIDUAL_MIN_AREA}mm2; worst cell {worst:.3f} from "
              f"designed copper at board ({wx:.2f},{wy:.2f})")
    if n_slivers < n:
        detail += (f"; {n - n_slivers} wide floating region(s) "
                   f"({float(areas[~frag].sum()):.2f}mm2) not counted")
    return [Check("residual copper", worst, f"<= {RESIDUAL_TOL}",
                  False, detail)]


def _outline_fill(maps: BoardMaps) -> np.ndarray:
    """Interior of the Edge.Cuts loop (ink included), cached like the
    distance fields. binary_fill_holes is exact for any closed outline —
    rounded corners included — where a rectangle test is not."""
    if "outline_fill" not in maps._cache:
        maps._cache["outline_fill"] = ndimage.binary_fill_holes(
            maps.layers["edge"])
    return maps._cache["outline_fill"]


# ------------------------------------------------------------------ the gate
def verify_program(job: PcbJob, name: str, path, maps: BoardMaps,
                   flip=None) -> Report:
    """Verify ONE assembled program of a [pcb] job against the board maps.

    `flip` is a flip.FlipContext when this job is one SIDE of a pin-and-flip
    document: the cross-side checks that need the other side's copper or the
    board's own rules ride along with the per-phase set (flip.py owns them so
    that a single-sided report is bit-for-bit the report it always was)."""
    split = programs_of(job)
    if name not in split:
        return Report.fatal(f"unknown pcb program {name!r} — the split is "
                            f"{sorted(split)}")
    phases = split[name]
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
        checks += pin_keepout_checks(job, samples)
        if flip is not None:
            checks += flip.program_checks(job, name, maps, samples)
        return Report(checks, None, program=text)
    except GcodeError as e:
        return Report.fatal(str(e))


def verify_pcb(job: PcbJob, programs: dict[str, str | Path],
               maps: BoardMaps | None = None,
               dpi: int | None = None,
               ppm: float = SHEET_PPM, flip=None) -> dict[str, Report]:
    """The [pcb] gate: one Report per program, composed the way
    twosided.verify composes its two sides. A program of the canonical split
    that was not handed over is a FATAL report, never a silent gap — the six
    phases are the job (pcbjob.py), and a chain missing a program is a
    different process.

    The CARVING programs (mill, holes) additionally ride the sheet stock
    simulation (adopted from WS6, 2026-07-30 — the gate must be the
    strictest reader), and each Report carries its CarveResult so a viewer
    session can serve the same simulation the gate judged instead of running
    a second one.

    `programs` maps program name -> the file whose BYTES go to the machine.
    """
    maps = maps or board_maps(job, dpi=dpi)
    split = programs_of(job)
    reports: dict[str, Report] = {}
    sheet: SheetStock | None = None
    sj: SheetJob | None = None
    for name in split:
        if name not in programs:
            reports[name] = Report.fatal(
                f"no {name!r} program was handed to the gate — phases "
                f"{split[name]} are unverified")
            continue
        rep = verify_program(job, name, programs[name], maps, flip=flip)
        # design-side DFM checks ride the program that owns the feature
        # (the 2026-07-31 hand-solder review): copper thermals on mill,
        # aperture scrubbability on scrub, legend metrics on silk
        if rep.program and "cu" in maps.layers:
            if name == "mill":
                rep = Report(rep.checks + thermal_checks(job, maps),
                             rep.carve, program=rep.program)
            elif name == "scrub":
                rep = Report(rep.checks + scrubbability_checks(job, maps)
                             + mask_blind_checks(job, maps),
                             rep.carve, program=rep.program)
            elif name == "silk":
                rep = Report(rep.checks + silk_metric_checks(job, maps),
                             rep.carve, program=rep.program)
        # `rep.program` is set exactly when the bytes parsed — a fatal report
        # has nothing to simulate and simulating it would judge moves the
        # strict parse already refused
        if name in CARVING and rep.program:
            if sheet is None:
                sheet = sheet_stock(job, ppm=ppm)
                sj = sheet_job(job, sheet)
            try:
                res = carve_program(sj, sheet, programs[name])
                extra = sheet_checks(job, sheet, sj, res)
                if name == "mill":
                    # the program responsible for ALL copper removal answers
                    # for the copper that remains (the bridging-sliver
                    # incident, 2026-07-30)
                    extra += residual_checks(job, maps, sheet, res)
                rep = Report(rep.checks + extra, res, program=rep.program)
            except GcodeError as e:
                rep = Report(rep.checks + [Check(
                    "sheet simulation", 0.0, "must simulate", False,
                    str(e))], None, program=rep.program)
        reports[name] = rep
        maps.release()
    for name in sorted(set(programs) - set(split)):
        reports[name] = Report.fatal(
            f"unknown pcb program {name!r} — the split is {sorted(split)}")
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
