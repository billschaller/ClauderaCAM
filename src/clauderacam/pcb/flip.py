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
  * Concentricity is measured on the SHORT SIDE of the ring only — the low
    quantiles of the exit radius — and deliberately ignores late exits, which
    are what attached tracks produce. See _short_side_ecc: the statistic is a
    measured eccentricity in mm, it assumes hole-centred pads are ROUND, and
    it assumes more than 40% of the ring's perimeter is exposed copper-to-void
    rather than buried under attached copper. Every hole-centred pad on the
    boards this lane has specified is round (orbit: Ø2.4 vias, Ø1.6 gauges,
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
#                         UNCHANGED by the 2026-08-02 rewrite of the statistic
#                         underneath it: the old proxy and the new one are both
#                         in mm of eccentricity, so the physical bar did not
#                         move — only the thing that can actually measure it.
SHORT_Q = (0.10, 0.40)  # the two low quantiles of exit radius _short_side_ecc
#                         reads. Reading nothing above the 40th percentile is
#                         the whole point: late exits are TRACKS. The pair
#                         costs coverage (see _short_side_ecc) and buys the
#                         only region of the ring an attached track cannot
#                         reach.
SHORT_GAIN = float(np.cos(np.pi * SHORT_Q[0]) - np.cos(np.pi * SHORT_Q[1]))
#                         0.6421: d(Q_hi - Q_lo)/d(eccentricity) for a round
#                         pad. The statistic's own gain, and therefore the
#                         factor its raster uncertainty is multiplied by.
SHORT_FREE_MIN = 0.5    # a pad-side whose rays mostly run to the cap (a hole
#                         in a pour) has no boundary to centre on. Below this
#                         fraction of free rays the pad-side is reported
#                         unmeasurable and counted, never quietly measured.
SCRUB_ANNULAR_RIM = 0.20      # a 0.3 spring tip crossing an open bore drops
#                               in and levers the pad off (the 2026-07-30
#                               incident). Under the 2026-08-03 ordering law
#                               the only holes that exist at ANY scrub are
#                               the setup-1 bores, and this is the tool-edge
#                               clearance bar scrub_plan_checks holds every
#                               lap to against them. (Its former companion
#                               SCRUB_ANNULAR_INSIDE died with the annular
#                               lap generator.)
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
        # mirror is a FACE property (front-up needs none, back-up flips) —
        # never an order property; the 2026-08-03 ordering law made the
        # distinction load-bearing
        return "none" if side == "front" else "x"

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
        if "scrub" in samples:
            out += scrub_plan_checks(self, job, maps, samples["scrub"])
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
def _short_side_ecc(r_exit: np.ndarray, step: float, eps: float) -> float:
    """How far the pad's centre sits from the HOLE's centre, in mm, measured
    from the SHORT SIDE of the annular ring only (2026-08-02, Board B).

    WHAT IT MEASURES. A round pad of radius R whose centre sits `e` from the
    hole centre puts its boundary, along the ray at angle t, at the exit radius
    r solving |r*u - c| = R, i.e.

        r^2 - 2 r (u.c) = R^2 - e^2          <- the same constant on every ray

    Over the whole circle of rays that makes the exit radius a sinusoid about
    R, so the p-th quantile of the exit radius belongs to the known direction
    u_p = -cos(pi*p) — and evaluating the constant above at TWO quantiles and
    subtracting eliminates R outright:

        e = (Q_lo^2 - Q_hi^2) / (2 (Q_lo*u_lo - Q_hi*u_hi))

    That is exact (no small-e linearisation) and it needs no declared pad
    diameter: the artwork's own short side states both R and e.

    WHAT IT DELIBERATELY IGNORES: everything above the 40th percentile of exit
    radius. That is where attached copper lives. Copper is only ever ADDED by a
    track, so a track can only move a ray's first copper->void crossing OUTWARD
    — never inward. This is exactly the property the retired proxy lacked: it
    read max-min, so one track set the max and the pad's eccentricity never
    entered the number (2026-08-02: 62 of 71 Board B pads "orbited" at 0.3925
    on artwork that is mirror-law-perfect, and its own 0.25 displaced-pad
    control scored 0.2525 — indistinguishable from one centred pad with one
    0.6mm track).

    WHY A REAL ECCENTRICITY CANNOT HIDE IN THE IGNORED REGION. A displaced pad
    ALWAYS squeezes the ring on one side: the exit radius at the squeeze is
    R - e, below R, and no arrangement of added copper can raise the *rank* of
    a ray without removing rays from BELOW the quantiles being read. Removing
    rays from the bottom moves Q_hi up faster than Q_lo (dQ/dp = e*pi*sin(pi*p)
    is 2.99e at p=0.4 against 0.97e at p=0.1), so a track lying on the squeezed
    side INFLATES the reading rather than masking it. The corner case is a
    track attached exactly on the squeezed side: the ring edge under it is
    pushed out, but the track only buries the rays within
    2*asin(t/2R) of its own direction — 29 degrees for a 0.6mm track on a Ø2.4
    via pad, 44 on a Ø1.6 gauge pad — and the adjacent off-track rays on that
    same side still exit early, at R - e*cos(22 deg) = R - 0.93e in the widest
    case. Measured over 382320 synthetic pad/hole/track/displacement/sub-pixel
    configurations: no pad whose true eccentricity reaches CONCENTRIC_TOL ever
    reads below it (a true 0.05 reads >= 0.0535 in the worst case), and a truly
    centred pad with up to four 0.6mm tracks never reads above 0.0234.

    THE COVERAGE ASSUMPTION, quantified. Reading the lowest 40% tolerates 60%
    (216 degrees) of the ring's perimeter buried under attached copper: seven
    0.6mm tracks on a Ø2.4 pad, four on a Ø1.6 pad. Past that the quantiles
    themselves sit in buried copper and the statistic OVER-reads, which fails
    the check — the correct refusal, since a pad with under 40% of its boundary
    exposed has no short side to measure. It cannot silently pass.

    THE RASTER ALLOWANCE. Each exit radius is located to within the walk's
    radial sample step plus the raster's own half pixel; a difference of two
    such radii carries at most their sum, and 1/SHORT_GAIN turns that into
    0.0156mm of eccentricity here. It is added, i.e. carried in the
    CONSERVATIVE direction (for an eccentricity, conservative is larger), the
    same posture BoardMaps.eps has everywhere else in the lane. This is what
    makes the bar hard at 0.05 rather than soft.
    """
    q_lo, q_hi = np.quantile(r_exit, SHORT_Q)
    u_lo, u_hi = (-np.cos(np.pi * p) for p in SHORT_Q)
    den = 2.0 * (q_lo * u_lo - q_hi * u_hi)
    if den > -1e-9:
        # a round pad gives den ~= -1.28*R, bounded away from zero. Anything
        # else is not a hole-centred round pad, and is refused rather than
        # divided by.
        return float(RING_PROBE)
    e = float((q_lo * q_lo - q_hi * q_hi) / den)
    return e + (step + eps) / SHORT_GAIN


def _ring_walk(ctx: FlipContext, cu: np.ndarray, hx: float, hy: float,
               hd: float) -> dict:
    """Walk outward from ONE hole's rim, at RING_RAYS angles, until the copper
    ends or the walk hits RING_PROBE.

    -> {"pad": is this a hole-centred pad at all, "rim_frac": how much of the
    rim is copper, "ring": min annular ring over the angles, "ecc": the
    short-side eccentricity in mm (_short_side_ecc), "free": how many rays
    found a boundary, "capped": how many ran to the cap, "spread": the RETIRED
    max-min proxy, kept because it is the witness the twosided suite prints to
    show the two apart on a pad with tracks — no check reads it}.

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
    # the eccentricity statistic reads ABSOLUTE exit radii (the chord relation
    # in _short_side_ecc is about the hole centre), and a capped ray is
    # recorded at the cap: it is known to exit at LEAST that late, which is all
    # the short side ever needs to know about it.
    return {"pad": rim_frac >= 0.9, "rim_frac": rim_frac,
            "ring": float(ring.min()) - ctx.eps,
            "ecc": _short_side_ecc(ring + r_h, step, ctx.eps),
            "free": int((~capped).sum()),
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
            if w["free"] >= SHORT_FREE_MIN * RING_RAYS:
                if w["ecc"] > ecc_worst:
                    ecc_worst = w["ecc"]
                    ecc_at = (f"{side} pad at ({hx:.2f},{hy:.2f}) sits "
                              f"{w['ecc']:.4f} off its hole, short side of "
                              f"{w['free']} free rays")
            else:
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
              (f"short-side eccentricity, late exits (tracks) ignored; worst "
               f"{ecc_at}" if ecc_at else "no measurable pad boundary")
              + (f"; {ecc_capped} pad-sides have under "
                 f"{SHORT_FREE_MIN:.0%} of their rays free (continuous "
                 f"copper) and no short side to centre on" if ecc_capped
                 else "")),
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
        # count-shaped value, never inf: bare `Infinity` in the session
        # JSON kills the viewer client (the back/scrub incident, 2026-08-03)
        return [Check("paste clear of the hole schedule", 0.0,
                      "0 apertures to judge", True,
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
def scrub_plan_checks(ctx: FlipContext, job: PcbJob, maps: BoardMaps,
                      s: Samples) -> list[Check]:
    """The ordering law's convictions on EVERY scrub program (operator
    ruling 2026-08-03: a pad is never drilled before it is scrubbed, and
    every area the bench expects to solder is always scrubbed).

    1. `scrub clear of existing holes` — the only holes that exist when a
       setup scrubs are the ones its own chain already cut: setup 1 none,
       setup 2 the non-pad bores. A 0.3 spring tip crossing an open bore
       drops in and levers the copper off — the 2026-07-30
       paint-across-bores incident. The GENERATOR can no longer produce
       that geometry (the grammar's chains put every pad hole after both
       scrubs), but the conviction stays until the incident is impossible
       in the bytes, not just the intent (Articles I and II).
    2. `solder plan scrubbed` — every mask aperture except the declared
       inert ones takes cutting path. On this process the flood coat is
       only ever opened by the scrub: an aperture the scrub misses is a
       pad the bench cannot solder, whatever the artwork promises.
    3. `inert stays under mask` — no cutting sample inside a declared
       inert aperture. An unscrubbed opening keeps its coat; that is the
       protective finish dead copper wants, and a lap there would strip it
       for nothing.
    """
    from scipy import ndimage
    from . import reemit
    tool = job.phase_tool("scrub")
    r = tool.radius
    slop = checks.SAMPLE_STEP / 2
    out: list[Check] = []

    role = pcbjob.role_of(job)
    existing = ([] if role == "first"
                else boardmaps.excellon(job.files["bores_drl"]))
    if existing:
        dh = np.min(np.stack([np.hypot(s.bx - hx, s.by - hy)
                              for hx, hy, _ in existing]), axis=0)
        which = np.argmin(np.stack([np.hypot(s.bx - hx, s.by - hy)
                                    for hx, hy, _ in existing]), axis=0)
        rim = np.array([existing[k][2] / 2.0 for k in which])
        clear = dh - rim - r - slop
        k = int(clear.argmin())
        out.append(Check(
            "scrub clear of existing holes", float(clear.min()),
            f">= {SCRUB_ANNULAR_RIM}",
            float(clear.min()) >= SCRUB_ANNULAR_RIM,
            f"tool EDGE to the nearest of the {len(existing)} bores this "
            f"setup inherits; worst at {s.at(k)}"))
    else:
        # value is the COUNT of holes existing at this scrub — never inf:
        # Python's json writes inf as bare `Infinity`, which the browser's
        # JSON.parse refuses, and the whole viewer session dies client-side
        # (found by the operator on back/scrub, 2026-08-03)
        out.append(Check(
            "scrub clear of existing holes", 0.0, "0 holes exist yet",
            True, "setup 1 scrubs a blank with zero holes — the ordering "
                  "law's whole point"))

    # mask regions vs cutting samples, 8-connected like every copper census
    lbl, nreg = ndimage.label(maps.layers["mask"], structure=checks._EIGHT)
    if nreg == 0:
        return out + [Check("solder plan scrubbed", 0.0, "unmeasurable",
                            False, "no mask apertures on this side at all")]
    si, sj = ctx.win.world_to_px(s.bx, s.by)
    si = np.clip(np.round(si).astype(int), 0, lbl.shape[0] - 1)
    sj = np.clip(np.round(sj).astype(int), 0, lbl.shape[1] - 1)
    hit = np.zeros(nreg + 1, bool)
    hit[lbl[si, sj]] = True
    hit[0] = False

    inert_lbls: set[int] = set()
    stale = []
    for ix, iy, why in reemit.inert_apertures(job):
        ii, jj = ctx.win.world_to_px(np.array([ix]), np.array([iy]))
        v = int(lbl[int(np.clip(round(float(ii[0])), 0, lbl.shape[0] - 1)),
                    int(np.clip(round(float(jj[0])), 0, lbl.shape[1] - 1))])
        if v == 0:
            stale.append((ix, iy))
        else:
            inert_lbls.add(v)
    if stale:
        out.append(Check("inert list names apertures", float(len(stale)),
                         "0 stale", False,
                         f"inert entries on NO mask ink: {stale[:4]} — the "
                         f"list drifted from the artwork"))

    live = [k for k in range(1, nreg + 1) if k not in inert_lbls]
    missed = [k for k in live if not hit[k]]
    cent = ndimage.center_of_mass(np.ones_like(lbl), lbl, missed[:4]) \
        if missed else []
    where = [(round(float(ctx.win.px_to_world(i, j)[0]), 2),
              round(float(ctx.win.px_to_world(i, j)[1]), 2))
             for i, j in cent]
    out.append(Check(
        "solder plan scrubbed", float(len(missed)), "0 apertures missed",
        not missed,
        f"{len(live)} solderable apertures, {len(inert_lbls)} declared "
        f"inert" + (f"; UNSCRUBBED near {where}" if missed else "")))

    wrong = sorted(k for k in inert_lbls if hit[k])
    out.append(Check(
        "inert stays under mask", float(len(wrong)), "0 inert scrubbed",
        not wrong,
        "an unscrubbed opening keeps its flood coat — scrubbing dead "
        "copper strips its finish for nothing"))
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
