"""The flip's own gate: what a DOUBLE-SIDED [pcb] job has to prove that a
single-sided one cannot (PCB-PLAN.md WS5 double-sided list + the four checks
boards/orbit/SPEC.md asks WS5 to add).

Why this is a module and not more of checks.py: every check here needs
something a one-sided report does not have — the OTHER side's copper, the
board's own mirror line, or the design rules a board that flips declares. Kept
apart, a single-sided report is bit-for-bit the report it always was (the
coupon goldens prove it), and the double-sided set can be read in one screen.

WHAT LIVES HERE, and what each threshold's provenance is:

  BOARD level (gerbers + the hole schedule; no program involved)
    side frame mirror law      the two side frames come from ONE Edge.Cuts and
                              mirror about one line — the falsified-and-fixed
                              WS2 law (`mirror -axis X` NEGATES X), asserted
                              closed-loop in boardmaps.flip_line
    both-side annular ring     >= the JOB's declared ring on every hole-centred
                              pad in F.Cu AND B.Cu (orbit SPEC "THT annular Δ",
                              0.7 there), with named [[rules.gauge]] exceptions
    via/hole concentricity     the hole sits centred in its pad on both sides
                              (CONCENTRIC_TOL) — the half of the flip budget
                              the artwork controls
    paste clear of the holes   no B.Paste aperture intersects any hole (orbit
                              SPEC "paste Δ": paste in a via wicks and blocks
                              the wire)

  PROGRAM level (the assembled bytes of one side's programs)
    annular scrub inside copper       side-2 hole-centred pads: tool edge
    annular scrub clear of the rim    >= 0.15 inside copper AND >= 0.20 outside
                                      the hole rim (orbit SPEC "scrub Δ NEW")
    tab-zone copper keep-out          no copper within 1.0 of a cutout tab, on
                                      EITHER side (orbit SPEC "tab-zone NEW")

  The pins-law carry-over is checks.pin_checks / checks.pin_keepout_checks: it
  is not cross-side, so it lives with the phases it judges.

WHAT DOES NOT CARRY OVER FROM THE COIN'S TWO-SIDED GATE, stated rather than
silently skipped. twosided.py's cross-side checks (punch-through, sever vs the
actual bottom, tab bridge) all rest on a carried BOTTOM FIELD — the flipped
side-1 stock — because a coin's two faces are carved reliefs that can meet in
the middle. A PCB's two faces are 0.15mm of copper on a 1.5mm laminate: side 2
cannot punch into side 1's art, there is no moat to thin the tab slot, and the
tab bridge is the full board thickness by construction. What replaces them is
the drill-once law (the grammar refuses a second drilling pass, so the holes
cannot disagree) and the registration checks above.

MEASUREMENT HONESTY (Article IX, the same posture as checks.py):
  * The ring walk reads a raster radially outward from the hole rim and stops
    at RING_PROBE. Beyond that stop it reports "capped": a hole in a pour has
    no discrete pad and no meaningful ring, and pretending to measure one would
    be worse than saying so. Tracks leaving a pad cap out too, which is why the
    walk is radial and not a distance transform — a 0.6 track on a Ø2.4 pad is
    NEARER to the rim than the pad's own boundary, so an in-copper distance
    field reads 0.3 where the annular ring is really 0.7.
  * Concentricity is measured as half the ring's spread over the non-capped
    angles, which assumes hole-centred pads are ROUND. Every hole-centred pad
    on the boards this lane has specified is (orbit: Ø2.4 vias, Ø1.6 gauges,
    Ø3.6 wire pads); a deliberately oval one would read as eccentric, and that
    is a refusal to be argued with evidence, not a number to loosen here.
  * Distances carry the same calibrated half-pixel over-read every check in
    the lane does (BoardMaps.eps), always in the conservative direction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..verify import Check, Report
from . import boardmaps, checks, pcbjob
from .checks import BoardMaps, Samples
from .pcbjob import PcbJob

# --------------------------------------------------------------- thresholds
RING_PROBE = 1.5        # mm past the hole rim the radial walk looks before it
#                         calls the ring "capped". Every annulus any board in
#                         this lane specifies is smaller (orbit's widest is
#                         PAD±'s 1.05), so a walk that reaches the cap has
#                         found more copper than any rule asks for — and a hole
#                         in a solid pour has no pad boundary to find at all.
RING_RAYS = 72          # 5° steps: a 0.05mm chord at the rim of a Ø1.0 hole,
#                         i.e. finer than the 0.01mm/px raster can resolve a
#                         boundary anyway
CONCENTRIC_TOL = 0.05   # mm of pad eccentricity allowed in the ARTWORK. The
#                         flip itself contributes an estimated 0.02-0.04
#                         (DESIGN.md: pin-to-hole clearance, still UNMEASURED
#                         until orbit's gauges are read), and the two errors
#                         add. The artwork's half of that budget is the half
#                         software can hold near zero, so it is held there.
SCRUB_ANNULAR_INSIDE = 0.15   # orbit SPEC "scrub Δ NEW": on side 2 the holes
SCRUB_ANNULAR_RIM = 0.20      # are already drilled, and a 0.3 spring tip
#                               spiralling across a Ø1.0 hole drops in and
#                               levers the pad off the laminate. Ø2.4 pad +
#                               Ø1.0 hole leaves exactly one legal 0.3-wide
#                               lap at r≈0.9, which is these two numbers.
TAB_KEEPOUT = 1.0       # orbit SPEC "tab-zone copper NEW": tabs are snapped by
#                         hand, and a tab that bridges copper tears it off the
#                         laminate
GAUGE_MATCH_TOL = pcbjob.GAUGE_MATCH_TOL   # the grammar already refused a
#                         gauge that names no hole; this is the same tolerance
#                         used to look the exception back up
PASTE_HOLE_MIN = 0.0    # orbit SPEC "paste Δ": an aperture may not INTERSECT a
#                         hole. Zero is the law; the raster eps is applied in
#                         the conservative direction, so a marginal case fails.


# ------------------------------------------------------------ the flip context
@dataclass
class FlipContext:
    """Ground truth for a whole double-sided document: BOTH sides' artwork in
    ONE shared raster window, the single hole schedule, and the derived mirror
    line. One window is not an optimization — it is what makes an F.Cu-vs-B.Cu
    comparison an array comparison instead of a resampling argument."""
    job: PcbJob                          # the DOCUMENT, never a side view
    tight: boardmaps.BoardWindow         # Edge.Cuts extents: transform frame
    win: boardmaps.BoardWindow           # padded: the raster frame
    cu: dict[str, np.ndarray]            # side -> that side's copper
    mask: dict[str, np.ndarray]
    edge: np.ndarray
    paste: np.ndarray | None
    holes: list[tuple[float, float, float]]
    line: float                          # machine-frame X of the mirror line
    _rings: dict = field(default_factory=dict)
    _dist: dict = field(default_factory=dict)

    @property
    def eps(self) -> float:
        return 0.5 / self.win.ppmm

    @property
    def annular(self) -> float:
        return float(self.job.rules["annular"])

    def offset(self, side: str) -> tuple[float, float]:
        return boardmaps.machine_offset(self.tight, self.job.anchor,
                                        self.mirror(side))

    @staticmethod
    def mirror(side: str) -> str:
        return "none" if side == pcbjob.SIDE_ORDER[0] else "x"

    def maps(self, side: str) -> BoardMaps:
        """A BoardMaps for one side, sharing this context's rasters — so the
        per-phase checks (iso, clear, scrub, cutout) run unchanged on a side
        of a flipped board, in the same window the board checks used."""
        return BoardMaps(tight=self.tight, win=self.win,
                         layers={"cu": self.cu[side], "mask": self.mask[side],
                                 "edge": self.edge},
                         holes=self.holes, offset=self.offset(side),
                         mirror=self.mirror(side))

    def dist(self, key: str, m: np.ndarray) -> np.ndarray:
        if key not in self._dist:
            self._dist[key] = boardmaps.dist_mm(m, self.win)
        return self._dist[key]

    def required_ring(self, hx: float, hy: float) -> tuple[float, str]:
        """The annular ring this hole must have, and what names it: the job's
        declared value, or a [[rules.gauge]] exception that covers it."""
        for g in self.job.rules.get("gauge", []):
            for gx, gy in g["positions"]:
                if abs(hx - float(gx)) <= GAUGE_MATCH_TOL and \
                        abs(hy - float(gy)) <= GAUGE_MATCH_TOL:
                    return float(g["annular"]), g["name"]
        return self.annular, ""

    def rings(self, side: str):
        """Per-hole ring walk on one side (cached). -> list of dicts, one per
        hole in schedule order."""
        if side not in self._rings:
            self._rings[side] = [_ring_walk(self, self.cu[side], *h)
                                 for h in self.holes]
        return self._rings[side]

    def program_checks(self, job: PcbJob, name: str, maps: BoardMaps,
                       samples: dict[str, Samples]) -> list[Check]:
        """The cross-side checks that belong to ONE program of ONE side.
        checks.verify_program calls this when it was handed a flip context."""
        out: list[Check] = []
        if job.side == pcbjob.SIDE_ORDER[1] and "scrub" in samples:
            out += annular_scrub_checks(self, job, maps, samples["scrub"])
        if "cutout" in samples:
            out += tab_zone_checks(self, job, samples["cutout"])
        return out


def context(job: PcbJob, dpi: int | None = None,
            pad: float | None = None) -> FlipContext:
    """Rasterize a double-sided document's SIX layers (+ paste) into one
    window and derive the mirror line. Needs gerbv, like every raster read in
    the lane."""
    if not job.twosided:
        raise ValueError(f"{job.name} is single-sided — there is no flip to "
                         f"check")
    dpi = dpi or boardmaps.DPI_DEFAULT
    tight = boardmaps.extents(job.files["edge"], dpi=dpi)
    pad = checks.window_pad(job) if pad is None else pad
    win = boardmaps.BoardWindow(tight.x0 - pad, tight.y0 - pad,
                                tight.x1 + pad, tight.y1 + pad, dpi)
    cu, mask = {}, {}
    for side in job.sides:
        cu[side] = boardmaps.rasterize(job.files[f"{side}_cu"], win)
        mask[side] = boardmaps.rasterize(job.files[f"{side}_mask"], win)
    paste = None
    if "paste" in job.files:
        paste = boardmaps.rasterize(job.files["paste"], win)
    return FlipContext(
        job=job, tight=tight, win=win, cu=cu, mask=mask,
        edge=boardmaps.rasterize(job.files["edge"], win), paste=paste,
        holes=boardmaps.excellon(job.files["drl"]),
        line=boardmaps.flip_line(tight, job.anchor))


# --------------------------------------------------------------- the ring walk
def _ring_walk(ctx: FlipContext, cu: np.ndarray, hx: float, hy: float,
               hd: float) -> dict:
    """Walk outward from ONE hole's rim, at RING_RAYS angles, until the copper
    ends or the walk hits RING_PROBE.

    -> {"pad": is this a hole-centred pad at all, "rim_frac": how much of the
    rim is copper, "ring": min annular ring over the angles, "spread": max-min
    over the non-capped angles, "capped": how many rays ran to the cap}.

    Radial by construction — see the module docstring on why a distance field
    is the wrong operator for an annular ring.
    """
    r_h = hd / 2.0
    step = 0.5 / ctx.win.ppmm
    rs = np.arange(r_h, r_h + RING_PROBE + step, step)
    ang = np.linspace(0.0, 2 * np.pi, RING_RAYS, endpoint=False)
    xs = hx + np.cos(ang)[:, None] * rs[None, :]
    ys = hy + np.sin(ang)[:, None] * rs[None, :]
    i, j = ctx.win.world_to_px(xs, ys)
    h, w = ctx.win.shape
    ii = np.clip(np.round(i - 0.5).astype(int), 0, h - 1)
    jj = np.clip(np.round(j - 0.5).astype(int), 0, w - 1)
    ink = cu[ii, jj]
    # a walk that leaves the window is not measured, it is refused: an annular
    # ring outside the raster is an unmeasured ring
    if (i < 0.5).any() or (i > h - 1.5).any() or (j < 0.5).any() \
            or (j > w - 1.5).any():
        raise ValueError(
            f"hole ({hx:.2f},{hy:.2f}) sits within {RING_PROBE}mm of the "
            f"raster window edge — its annular ring cannot be measured; the "
            f"window pad is too small for this board")
    rim_frac = float(ink[:, 0].mean())
    # first non-copper index along each ray (rs.size == "ran to the cap")
    first = np.where(ink.all(axis=1), rs.size,
                     np.argmin(ink, axis=1))
    capped = first == rs.size
    ring = np.where(capped, RING_PROBE, rs[np.minimum(first, rs.size - 1)]
                    - r_h)
    free = ring[~capped]
    return {"pad": rim_frac >= 0.9, "rim_frac": rim_frac,
            "ring": float(ring.min()) - ctx.eps,
            "spread": float(free.max() - free.min()) if free.size else 0.0,
            "capped": int(capped.sum())}


# ------------------------------------------------------------- board-level set
def frame_checks(ctx: FlipContext) -> list[Check]:
    """The two side frames derive from ONE Edge.Cuts and mirror about ONE line.

    boardmaps.flip_line already refuses a pair of transforms whose average is
    not constant (that is the closed-loop assert on the WS2 mirror law); this
    check states the same fact as a measured number the report carries, and
    adds the statement the plan words as "B.Cu frame == mirrored F.Cu frame":
    the MACHINE-frame box of the board is identical under both frames, so the
    board occupies the same physical rectangle in both setups.
    """
    a, b = pcbjob.SIDE_ORDER
    corners_x = [ctx.tight.x0, ctx.tight.x1]
    corners_y = [ctx.tight.y0, ctx.tight.y1]
    ax, ay = boardmaps.machine_xy(ctx.offset(a), ctx.mirror(a),
                                  corners_x, corners_y)
    bx, by = boardmaps.machine_xy(ctx.offset(b), ctx.mirror(b),
                                  corners_x, corners_y)
    box_a = tuple(float(v) for v in (min(ax), min(ay), max(ax), max(ay)))
    box_b = tuple(float(v) for v in (min(bx), min(by), max(bx), max(by)))
    worst_box = float(max(abs(p - q) for p, q in zip(box_a, box_b)))
    out = [Check("side frame board box", worst_box, "<= 1e-06",
                 worst_box <= 1e-6,
                 f"{a} {tuple(round(v, 3) for v in box_a)} vs {b} "
                 f"{tuple(round(v, 3) for v in box_b)} — one Edge.Cuts, two "
                 f"derived transforms")]

    # the involution itself, on the features that matter: every hole's two
    # machine positions must straddle the mirror line
    if ctx.holes:
        hx = np.array([h[0] for h in ctx.holes])
        hy = np.array([h[1] for h in ctx.holes])
        mxa, mya = boardmaps.machine_xy(ctx.offset(a), ctx.mirror(a), hx, hy)
        mxb, myb = boardmaps.machine_xy(ctx.offset(b), ctx.mirror(b), hx, hy)
        dev = np.abs((mxa + mxb) / 2.0 - ctx.line)
        worst = float(max(dev.max(), np.abs(mya - myb).max()))
        k = int(dev.argmax())
        out.append(Check("side frame mirror law", worst, "<= 1e-06",
                         worst <= 1e-6,
                         f"{len(ctx.holes)} holes; mirror line x="
                         f"{ctx.line:.3f}; worst at board "
                         f"({hx[k]:.2f},{hy[k]:.2f}) -> machine "
                         f"{mxa[k]:.3f}/{mxb[k]:.3f}"))
    return out


def annular_checks(ctx: FlipContext) -> list[Check]:
    """Both-side annular ring, and concentricity across the flip.

    A hole counts as a hole-centred PAD if EITHER side's copper covers its rim
    — that is deliberate and it is the whole point of the check: the failure
    orbit SPEC names ("a good back pad over a shaved front pad") includes the
    case where one side's pad is missing outright, and a per-side census would
    quietly reclassify that hole as a bare bore on the bad side. Holes bare on
    BOTH sides (mounting bores, copper keep-out by design) are excluded and
    counted, so the exclusion is visible in the detail rather than silent.
    """
    a, b = pcbjob.SIDE_ORDER
    ra, rb = ctx.rings(a), ctx.rings(b)
    worst = float("inf")
    worst_at = ""
    bare = 0
    pads = 0
    gauges: dict[str, int] = {}
    ecc_worst = 0.0
    ecc_at = ""
    ecc_capped = 0
    for (hx, hy, hd), wa, wb in zip(ctx.holes, ra, rb):
        if not (wa["pad"] or wb["pad"]):
            bare += 1
            continue
        pads += 1
        need, gname = ctx.required_ring(hx, hy)
        if gname:
            gauges[gname] = gauges.get(gname, 0) + 1
        for side, w in ((a, wa), (b, wb)):
            slack = w["ring"] - need
            if slack < worst:
                worst = slack
                worst_at = (f"{side} ring {w['ring']:.3f} vs {need:g} at "
                            f"({hx:.2f},{hy:.2f}) d{hd:g}"
                            + (f" [{gname}]" if gname else "")
                            + ("" if w["pad"] else " — NO PAD on this side, "
                               "rim copper "
                               f"{w['rim_frac'] * 100:.0f}%"))
            ecc = w["spread"] / 2.0
            if w["capped"] < RING_RAYS and ecc > ecc_worst:
                ecc_worst = ecc
                ecc_at = (f"{side} pad at ({hx:.2f},{hy:.2f}), ring spread "
                          f"{w['spread']:.3f} over {RING_RAYS - w['capped']} "
                          f"free rays")
            if w["capped"] == RING_RAYS:
                ecc_capped += 1
    if not pads:
        return [Check("both-side annular ring", 0.0, "unmeasurable", False,
                      f"no hole in the schedule has copper at its rim on "
                      f"either side ({bare} bare bores) — either the copper "
                      f"layers or the drill file belong to another board")]
    detail = (f"{pads} hole-centred pads x2 sides, job asks "
              f"{ctx.annular:g}; {bare} bare bores excluded")
    if gauges:
        detail += "; named exceptions " + ", ".join(
            f"{n} x{c}" for n, c in sorted(gauges.items()))
    return [
        Check("both-side annular ring", worst, ">= 0 (ring - required)",
              worst >= 0.0, detail + f"; worst {worst_at}"),
        Check("via/hole concentricity across the flip", ecc_worst,
              f"<= {CONCENTRIC_TOL}", ecc_worst <= CONCENTRIC_TOL,
              (f"worst {ecc_at}" if ecc_at else "no measurable pad boundary")
              + (f"; {ecc_capped} pad-sides sit in continuous copper (pour) "
                 f"and have no boundary to centre on" if ecc_capped else "")),
    ]


def paste_checks(ctx: FlipContext) -> list[Check]:
    """No B.Paste aperture intersects any hole in the schedule (orbit SPEC
    "paste Δ"): a via, a THT pad or an ISP pad with paste in it wicks solder
    into the hole, which blocks the wire that was supposed to go there — and a
    via is stitched AFTER reflow, so nobody finds out until it is too late."""
    if ctx.paste is None:
        return [Check("paste clear of the hole schedule", 0.0,
                      "unmeasurable", False,
                      "no paste layer in this document — the grammar requires "
                      "one for a double-sided board")]
    if not ctx.paste.any():
        return [Check("paste clear of the hole schedule", float("inf"),
                      f"> {PASTE_HOLE_MIN}", True,
                      "the paste layer is empty (no stencil apertures)")]
    d = ctx.dist("paste", ctx.paste)
    worst = float("inf")
    at = ""
    for hx, hy, hd in ctx.holes:
        i, j = ctx.win.world_to_px(hx, hy)
        h, w = ctx.win.shape
        ii = int(np.clip(round(float(i) - 0.5), 0, h - 1))
        jj = int(np.clip(round(float(j) - 0.5), 0, w - 1))
        clear = float(d[ii, jj]) - ctx.eps - hd / 2.0
        if clear < worst:
            worst = clear
            at = f"hole ({hx:.2f},{hy:.2f}) d{hd:g}, paste {clear:.3f} clear"
    return [Check("paste clear of the hole schedule", worst,
                  f"> {PASTE_HOLE_MIN}", worst > PASTE_HOLE_MIN,
                  f"{len(ctx.holes)} holes vs the B.Paste apertures; "
                  f"worst {at}")]


def board_checks(ctx: FlipContext) -> list[Check]:
    """Everything the ARTWORK has to prove before either side is machined.
    These read no g-code: they are facts about the gerbers and the schedule,
    and they belong to the document rather than to any program."""
    return frame_checks(ctx) + annular_checks(ctx) + paste_checks(ctx)


# ----------------------------------------------------------- program-level set
def annular_scrub_checks(ctx: FlipContext, job: PcbJob, maps: BoardMaps,
                         s: Samples) -> list[Check]:
    """Side 2's scrub, over hole-centred pads (orbit SPEC "scrub Δ NEW").

    On side 1 the holes do not exist yet and a disc lap over a pad is right.
    On side 2 they all do, and a 0.3 spring tip spiralling across a Ø1.0 hole
    drops in and levers the pad off the laminate. The two laws below are the
    annular lap: stay ON copper, and stay OFF the rim.

    The second one is the reason this check exists at all — `scrub plateau
    margin` cannot express it. The copper gerber draws a pad as a SOLID disc
    (the hole lives only in the Excellon), so a lap straight across the hole
    centre reads as deeply inside copper and passes every single-sided scrub
    law there is.
    """
    tool = job.phase_tool("scrub")
    r = tool.radius
    slop = checks.SAMPLE_STEP / 2
    pads = [(hx, hy, hd) for (hx, hy, hd), wa, wb
            in zip(ctx.holes, ctx.rings(pcbjob.SIDE_ORDER[0]),
                   ctx.rings(pcbjob.SIDE_ORDER[1])) if wa["pad"] or wb["pad"]]
    if not pads:
        return []
    dh = np.min(np.stack([np.hypot(s.bx - hx, s.by - hy)
                          for hx, hy, _ in pads]), axis=0)
    which = np.argmin(np.stack([np.hypot(s.bx - hx, s.by - hy)
                                for hx, hy, _ in pads]), axis=0)
    rim = np.array([pads[k][2] / 2.0 for k in which])
    on_pad = dh <= rim + RING_PROBE
    if not on_pad.any():
        return [Check("annular scrub laps", 0.0, "unmeasurable", False,
                      f"the side-2 scrub never comes near any of the "
                      f"{len(pads)} hole-centred pads — either the program or "
                      f"the mask apertures belong to another board")]
    inside = maps.sample(maps.dist("in_cu"), s.bx[on_pad], s.by[on_pad]) \
        - maps.eps - slop - r
    k = int(inside.argmin())
    idx = np.nonzero(on_pad)[0]
    out = [Check("annular scrub inside copper", float(inside.min()),
                 f">= {SCRUB_ANNULAR_INSIDE}",
                 float(inside.min()) >= SCRUB_ANNULAR_INSIDE,
                 f"{int(on_pad.sum())} laps on {len(pads)} hole-centred pads; "
                 f"worst at {s.at(int(idx[k]))}")]
    clear = dh[on_pad] - rim[on_pad] - r - slop
    k = int(clear.argmin())
    out.append(Check("annular scrub clear of the hole rim",
                     float(clear.min()), f">= {SCRUB_ANNULAR_RIM}",
                     float(clear.min()) >= SCRUB_ANNULAR_RIM,
                     f"tool EDGE to the drilled rim; worst at "
                     f"{s.at(int(idx[k]))}"))
    return out


def tab_zone_checks(ctx: FlipContext, job: PcbJob,
                    s: Samples) -> list[Check]:
    """No copper within TAB_KEEPOUT of a cutout tab, on EITHER side (orbit
    SPEC "tab-zone copper NEW"): the tabs are snapped by hand and a tab that
    bridges copper tears it off the laminate.

    The tabs come from checks.cutout_gaps — the same ordering the tab census
    uses, so the two checks always agree about which gaps are tabs. Each tab is
    measured twice: at the cut path (where the tab's material ends) and at its
    projection onto the OUTLINE (where the fracture actually happens, and
    where the copper is). The outline projection is the conservative half and
    it is the one that catches a pour that runs to the board edge.
    """
    walk = checks.cutout_gaps(job, s)
    if walk is None:
        return []
    px_, py_, material = walk
    tabs = np.nonzero(material >= checks.TAB_MATERIAL_MIN)[0]
    if tabs.size == 0:
        return []          # the tab census owns "there are no tabs"
    xs: list[float] = []
    ys: list[float] = []
    for k in tabs:
        k2 = (k + 1) % px_.size
        n = max(2, int(np.ceil(material[k] / 0.05)))
        t = np.linspace(0.0, 1.0, n)
        xs += list(px_[k] + (px_[k2] - px_[k]) * t)
        ys += list(py_[k] + (py_[k2] - py_[k]) * t)
    ox, oy = checks.project_to_outline(job, xs, ys)
    qx = np.concatenate([np.asarray(xs, float), ox])
    qy = np.concatenate([np.asarray(ys, float), oy])
    out: list[Check] = []
    worst = float("inf")
    at = ""
    for side in job.sides:
        d = ctx.dist(f"cu-{side}", ctx.cu[side])
        i, j = ctx.win.world_to_px(qx, qy)
        h, w = ctx.win.shape
        ii = np.clip(np.round(i - 0.5).astype(int), 0, h - 1)
        jj = np.clip(np.round(j - 0.5).astype(int), 0, w - 1)
        v = d[ii, jj] - ctx.eps
        k = int(v.argmin())
        if float(v[k]) < worst:
            worst = float(v[k])
            at = (f"{side} copper {worst:.3f} from the tab zone at board "
                  f"({qx[k]:.2f},{qy[k]:.2f})")
    out.append(Check("tab-zone copper keep-out", worst, f">= {TAB_KEEPOUT}",
                     worst >= TAB_KEEPOUT,
                     f"{tabs.size} tabs, both sides, measured at the cut path "
                     f"AND its outline projection; worst {at}"))
    return out


# -------------------------------------------------------------------- the gate
def verify_twosided(job: PcbJob, programs: dict[str, dict[str, Path]],
                    ctx: FlipContext | None = None,
                    dpi: int | None = None,
                    ppm: float = checks.SHEET_PPM) -> dict[str, Report]:
    """The double-sided [pcb] gate: one "board" report for the artwork, then
    one report per program per side, keyed "<side>/<program>".

    Composed the way twosided.verify composes its two sides, and the way
    checks.verify_pcb composes its four programs — one Report type all the way
    down, so a flipped board prints, serializes and is judged by exactly the
    same machinery as a coin.

    `programs` is {side: {program: path}}. A side that was not handed over is
    a fatal report per missing program, never a silent gap.
    """
    ctx = ctx or context(job, dpi=dpi)
    out: dict[str, Report] = {"board": Report(board_checks(ctx), None)}
    for side in job.sides:
        sj = pcbjob.side_view(job, side)
        reps = checks.verify_pcb(sj, programs.get(side, {}),
                                 maps=ctx.maps(side), ppm=ppm, flip=ctx)
        for name, rep in reps.items():
            out[f"{side}/{name}"] = rep
    return out


def report_text(reports: dict[str, Report]) -> str:
    """One text block for the whole document (CLI/MCP shape)."""
    lines = []
    for name, rep in reports.items():
        lines.append(f"=== {'artwork' if name == 'board' else 'program'} "
                     f"{name} ===")
        lines.append(rep.text())
    ok = all(r.ok for r in reports.values())
    lines.append("PCB VERDICT (double-sided): "
                 + ("PASS — both setups cleared" if ok
                    else "FAIL — do NOT cut this board"))
    return "\n".join(lines)
