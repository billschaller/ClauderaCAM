"""Re-emission (PCB-PLAN.md WS4, Article V): FlatCAM's per-phase .nc is
GEOMETRY INTERCHANGE, never a program. Every line is read through the
same strict parser as the gate, cross-checked against the phase's own
parameters, and re-emitted through emit.assemble() — which is what adds
the missing G4 spin-up dwell (the zigbee files genuinely lack it;
assemble_multitool retires), the M5-before-M6 law, stage markers, the
tool table, and the 128-char discipline.

Param-match law (the stale-ZMIN incident class): a phase file whose S
word, F words, or floor Z disagree with the job config is a file from
some OTHER run — refused, never trimmed to fit.

Dialect normalization belongs HERE, not in the gate (Article V): a
FlatCAM feed setter (`G01 F500.00`, a motion word with no axis word) is
folded onto the NEXT motion line, so every line an assembled [pcb]
program carries is a fully-worded move that simulate.prep_moves can
resolve. The mill gate is not relaxed to accept the interchange
dialect; the interchange is normalized to the gate's.

The silk program never touches FlatCAM at all: stroke chains come
straight from the B.Silkscreen gerber's draw words (boardmaps-style
scan), get the SAME derived mirror+offset as every layer, and are
CLIPPED — segment by segment, not chain by chain — against the mask
apertures dilated by the job's silk clearance before leaving through
emit.assemble_laser with its own dialect law.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy import ndimage

from ..emit import assemble, assemble_laser
from ..engine import OpResult, path_length
from ..ops import drill
from ..simulate import parse_line
from . import boardmaps, pcbjob
from .pcbjob import PIN_PHASES, PcbJob, programs_of

_DROP = re.compile(r"^\s*$|^\(")
_COMMENT = re.compile(r"\([^)]*\)|;.*")
# the F token exactly as the interchange file spells it — folding re-uses the
# SOURCE text, never a reformatted float, so a feed can never drift past the
# param-match law's 3-decimal comparison on its way onto the next line
_F_TOKEN = re.compile(r"[Ff]\s*([-+]?(?:\d+\.?\d*|\.\d+))")


def read_phase(nc_path: Path, job: PcbJob, phase: str) -> OpResult:
    """Strict-read one FlatCAM phase file -> an OpResult whose lines are
    ready for emit.assemble(). Refuses on: unparseable words (the strict
    parser is the same one the gate runs), spindle speed != the phase
    tool's rpm, feed words outside the phase's {feed, plunge}, or a
    floor Z that is not the phase's configured depth (param-match law).

    Feed-setter folding (the 2026-07-30 unsimulatable-program incident):
    FlatCAM's default post writes `G01 F500.00` — a motion word with no
    axis word — three or more times per phase file. It moves nothing; it
    sets the modal feed. simulate.prep_moves reads it as a cutting move
    with no position established and REFUSES the file, which is correct
    (an unmodeled move is an unverified move) and is why an assembled
    [pcb] program could not ride verify.verify()'s stock simulation. The
    fix is here, at the dialect boundary: the F word is carried onto the
    next line that actually moves, so the modal state every move sees is
    bit-identical while the assembled program contains only fully-worded
    motion lines. The F is added to `feeds` BEFORE it is folded, so the
    param-match law still validates every feed word in the file — folding
    cannot smuggle a stray feed past it.
    """
    p = job.phases[phase]
    tool = job.phase_tool(phase)
    keep: list[str] = []
    zmin = 0.0
    feeds: set[float] = set()
    rpm_seen = None
    pending_f: str | None = None      # a folded feed setter's source token
    pending_at = 0
    for lineno, raw in enumerate(open(nc_path), 1):
        parsed = parse_line(raw, lineno)   # GcodeError is fatal by design
        kind = parsed[0]
        if kind == "none":
            continue
        if kind == "tool":
            raise ValueError(f"{nc_path}:{lineno}: tool change inside a "
                             f"phase file — not geometry interchange")
        if kind == "spindle":
            rpm_seen = float(parsed[1])
            continue
        motion, coords, fword, sword = parsed[1:]
        if sword is not None:
            rpm_seen = float(sword)
        if fword is not None:
            feeds.add(round(float(fword), 3))
        if not coords:
            # a motion word with no axis word: a feed setter, not a move
            if fword is not None:
                toks = _F_TOKEN.findall(_COMMENT.sub(" ", raw))
                if not toks:                  # the parser saw an F; we must
                    raise ValueError(         # too, or we would drop it
                        f"{nc_path}:{lineno}: cannot re-read the F word of "
                        f"{raw.strip()!r} to fold it — refusing to drop it")
                pending_f = toks[-1]
                pending_at = lineno
            continue
        if "Z" in coords:
            zmin = min(zmin, coords["Z"])
        line = raw.strip()
        if not _DROP.match(line) and not re.fullmatch(
                r"G2[19]|G90|G94|M0?[35](\s+S[\d.]+)?", line):
            if fword is None and pending_f is not None:
                line = f"{line} F{pending_f}"
            pending_f = None
            keep.append(line)
    if pending_f is not None:
        raise ValueError(
            f"{nc_path}:{pending_at}: a feed setter F{pending_f} with no "
            f"motion line left to fold it onto — this file's dialect is not "
            f"the one re-emission models, and a dropped word is a word "
            f"nobody verified")
    if rpm_seen is None or abs(rpm_seen - tool.rpm) > 0.5:
        raise ValueError(
            f"{nc_path}: spindle {rpm_seen} != T{tool.num} rpm {tool.rpm} "
            f"— this file belongs to some other run (param-match law)")
    want_feeds = {round(float(p["feed"]), 3), round(float(p["plunge"]), 3)}
    stray = feeds - want_feeds
    if stray:
        raise ValueError(
            f"{nc_path}: feed words {sorted(stray)} outside the phase's "
            f"{sorted(want_feeds)} (param-match law)")
    if abs(zmin - p["depth"]) > 1e-6:
        raise ValueError(
            f"{nc_path}: floor Z {zmin} != configured depth {p['depth']} "
            f"— the stale-ZMIN incident class, refused")
    if not keep:
        raise ValueError(f"{nc_path}: no motion")
    plen = path_length(keep)
    return OpResult(label=f"pcb-{phase}", kind=phase, tool=tool.num,
                    lines=keep, path_len_mm=plen,
                    est_min=plen / max(float(p["feed"]), 1.0))


# ------------------------------------------------------- program assembly
# The operator steps BEFORE and AFTER each program of the split, as one
# header line each. The full provenance-traced card is session.run_sheet();
# these are the same steps compressed to what the operator reads inside the
# file at the machine — the WS6 review's point: a run-sheet card in a viewer
# is not a substitute for a comment in the bytes the operator posts.
_PROGRAM_STEPS = {
    "mill": ("blank on FULL tape, copper up, auto-leveled, Z0 = copper "
             "top, G54 = board SW corner",
             "squeegee + UV-cure the solder mask, coat white, then "
             "program B silk"),
    "silk": ("mask cured + white coated; fit the 455nm module, M323 "
             "test-fire on scrap first",
             "wipe uncured white off with IPA, refit spindle + spring "
             "tool, then program C scrub"),
    "scrub": ("legend wiped, spring tool fitted; same G54 zero - the "
              "board has not moved",
              "program D holes - no operator step between"),
    "holes": ("same setup; every hole helically bored, then the outline "
              "cut with tabs",
              "snap the {gaps} tabs{where} of {gapsize}mm, file the stubs; "
              "stencil/paste/reflow happens off-machine"),
}

# The same steps for a board that FLIPS (orbit SPEC.md "Assembly / run-sheet
# order"). Only the entries that differ are listed; the rest fall back to the
# single-sided text above. The FLIP itself is the `after` of side A's last
# program and the `before` of side B's first — an operator step between two
# programs, which is exactly what this header block was built to carry.
#
# A step may be a TUPLE of lines: the flip is genuinely a paragraph of
# instructions and the dialect's 128-char law is not negotiable, so it gets
# continuation comment lines rather than a truncated one.
_SIDE_STEPS = {
    ("front", "mill"): (
        ("FULL-coverage tape is mandatory - a bowed blank makes every depth "
         "number fiction",
         "FRONT copper up, auto-level over the board, Z0 = front copper, "
         "G54 = board SW corner"),
        None),
    ("front", "holes"): (
        "same setup; ALL through-holes bored from side A, so both artworks "
        "reference the same physical holes",
        "program E pins - no operator step between"),
    ("front", "pins"): (
        "same setup, still; fit the pin drill - the registration holes are "
        "the LAST thing cut on side A",
        ("FLIP: set the {pins} dowels, turn the blank about {axis} onto them, "
         "re-tape FULL coverage",
         "deburr the BACK by hand first - the drill's exit burr lives there",
         "re-level and re-touch Z0 on the back copper; NEVER re-zero XY - it "
         "is the registration the holes bought")),
    ("back", "mill"): (
        ("blank FLIPPED onto the pins, re-taped, re-leveled, Z0 = BACK "
         "copper, XY untouched",
         "confirm no auto-level probe point sat in a drilled hole - one that "
         "did wrote a false low into the height map"),
        ("READ THE FLIP GAUGES with a loupe and write the numbers down - an "
         "even ring means a perfect flip",
         "THEN squeegee + UV-cure this side's mask and coat white")),
    ("back", "scrub"): (
        "legend wiped, spring tool fitted; the holes are all there now - "
        "hole-centred pads get ANNULAR laps, never a disc",
        "program D holes - the cutout, no operator step between"),
    ("back", "holes"): (
        "same setup; the outline cut with tabs - side 2 only, and last",
        ("snap the {gaps} tabs{where} of {gapsize}mm, file the stubs, deburr",
         "off-machine: stencil + paste + hotplate reflow the BACK, THEN the "
         "wire vias, THEN the THT parts from the front")),
}


def _step_lines(tag: str, step, **fmt) -> list[str]:
    """One operator step as header comment lines. A tuple becomes a first
    line plus indented continuations, because the 128-char dialect law is not
    negotiable and a truncated instruction is worse than none."""
    parts = (step,) if isinstance(step, str) else tuple(step)
    parts = [p.format(**fmt) if "{" in p else p for p in parts]
    return [f"({tag}: {parts[0]})"] + [f"(  {p})" for p in parts[1:]]


def program_header(job: PcbJob, name: str,
                   ops: list[OpResult] | None = None) -> list[str]:
    """The run-sheet header of ONE program of the split: position in the
    chain, the operator step on each side, the tool table, and the floor
    echo — all COMMENT lines, all inside emit's own lint (the 128-char law
    included). Exists because the M6-pause instructions used to live only in
    the viewer's card and the laser banner (the 2026-07-30 WS6 review):
    the bytes the operator posts must carry their own bench context.

    On a side of a flipped board the header also names the SIDE and carries
    the flip as the operator step between the two setups' programs."""
    split = programs_of(job)
    letter = "ABCDEFGH"[list(split).index(name)]
    phases = split[name]
    side = f" [side {job.side.upper()}]" if job.side else ""
    lines = [f"(program {letter} of {len(split)} - {name}{side}: "
             + " + ".join(f"pcb-{p}" for p in phases) + ")"]
    before, after = _PROGRAM_STEPS.get(name, ("", ""))
    if job.side:
        sb, sa = _SIDE_STEPS.get((job.side, name), (None, None))
        before, after = sb or before, sa or after
    fmt = {}
    if job.has_phase("cutout"):
        c = job.phases["cutout"]
        # the operator snaps tabs, not a placement token: the note carries the
        # COUNT the placement leaves, plus WHERE to reach for them when the job
        # steered them off the default four sides (empty for a plain count, so
        # the shipped single-sided text stays byte-identical)
        # " - {where} -" not "({where})": these notes live INSIDE a G-code
        # comment, and a nested paren is unparseable — simulate.parse_line
        # refuses the whole program (caught by the gate on orbit's first
        # styled-tab emission, 2026-08-02)
        where = pcbjob.tab_where(c["gaps"])
        assert "(" not in where and ")" not in where, \
            f"tab_where text may not contain parens: {where!r}"
        fmt.update(gaps=pcbjob.tab_count(c["gaps"]),
                   where=f" - {where} -" if where else "",
                   gapsize=f"{c['gapsize']:g}")
    if job.pins:
        fmt.update(pins=len(job.pins["positions"]),
                   axis=job.flip_axis.upper())
    lines += _step_lines("before", before, **fmt)
    if name == "silk":
        lines.append("(laser: 455nm module - the head stays at the Z0 "
                     "focal plane; the only Z word is the focus move)")
    else:
        seen: list[int] = []
        for r in ops or []:
            if r.tool not in seen:
                seen.append(r.tool)
        descs = []
        for num in seen:
            t = job.tool(num)
            d = f"T{t.num} {t.type} d{t.diameter:g}"
            if t.type == "vee":
                d += f" tip {t.tip_diameter:g}"
            descs.append(d + f" S{t.rpm:g}")
        lines.append("(tools: " + " | ".join(descs) + ")")
        pauses = len(ops or []) - 1
        if pauses > 0:
            lines.append(f"({pauses} M6 tool-change pause"
                         + ("s" if pauses > 1 else "") + " inside: "
                         + " then ".join(f"T{r.tool}" for r in ops)
                         + " - the spindle stops before each change)")
        floors = " | ".join(
            f"pcb-{p} Z{job.phases[p]['depth']:g}" for p in phases)
        note = " - spring PRELOAD, not cut depth" if name == "scrub" else ""
        lines.append(f"(floors: {floors}{note})")
    lines += _step_lines("after", after, **fmt)
    return lines


def assemble_program(job: PcbJob, name: str, ops: list[OpResult]) -> str:
    """Assemble ONE program of the canonical split, with its run-sheet
    header, through emit.assemble (Article V — same parser, same lint).
    The ops must BE the split's phases for that program, in order: a
    program carrying some other phase set is a different process and
    refuses here before the gate ever has to catch it."""
    want = programs_of(job).get(name)
    got = tuple(r.kind for r in ops)
    if want is None or got != want:
        raise ValueError(
            f"program {name!r} must carry phases {want}, got {got} — the "
            f"split is the job (pcbjob.PROGRAM_PHASES / SIDE_PROGRAMS)")
    return assemble(job, ops, header=program_header(job, name, ops))


def pin_ops(job: PcbJob) -> list[OpResult]:
    """The pin block's two ops, from the SHIPPED coin-lane generators
    (ops/drill.py — spot-face then full-retract peck, emitted as plain G0/G1
    so the gate models every move). Nothing is re-derived here: the positions,
    depths and feeds are pcbjob's derived pin phase tables, and the only
    translation is the sign (a pcb phase carries a Z FLOOR, the coin
    generators take a positive depth below the surface).

    Why these ops and not FlatCAM's: a registration hole is not board artwork.
    It is not in any gerber, it goes 12mm into the spoilboard, and the burr it
    must not leave is the reason the spot-face exists at all — the coin lane
    already got all three right, and the flip accuracy of this board rests on
    that code being the same code.
    """
    if not job.has_phase("pindrill"):
        raise ValueError(f"{job.name} has no pin block — the pins live on "
                         f"side A of a [twosided] document")
    out: list[OpResult] = []
    for phase in PIN_PHASES:
        p = job.phases[phase]
        tool = job.phase_tool(phase)
        depth = -float(p["depth"])          # floor Z -> depth below the top
        if phase == "pinspot":
            lines = drill.spotface(p["positions"], depth, float(p["feed"]))
        else:
            lines = drill.pindrill(p["positions"], depth, float(p["peck"]),
                                   float(p["feed"]))
        plen = path_length(lines)
        out.append(OpResult(label=f"pcb-{phase}", kind=phase, tool=tool.num,
                            lines=lines, path_len_mm=plen,
                            est_min=plen / max(float(p["feed"]), 1.0)))
    return out


# ----------------------------------------------------------- the silk clip
# The 2026-07-30 field-legend incident: silk_strokes used to test a chain's
# VERTICES, so a long straight stroke whose endpoints were clear could still
# cross a pad mid-segment — the zigbee legend's 23.5mm box line passes 0.085
# from an aperture and the whole chain shipped. Cured white on a solderable
# pad repels solder. Strokes are therefore CLIPPED against the forbidden
# region (mask apertures dilated by the job's clearance) segment by segment:
# the clear part is kept, only what lies inside is dropped.
#
# Conservatism budget. This module measures with the EDT of the raster it is
# handed (the TIGHT window — the 154/124 transform law); checks.silk_checks
# re-measures the ASSEMBLED BYTES with its own EDT over its own PADDED
# window. Two rasters, never the same array, so every kept point is held
# SILK_EPS_PX pixels + SILK_EPS_MM clear of the clearance the job asks for:
#
#   2.0 px : the padded raster reads up to ONE pixel tighter than the tight
#            one (measured: worst 0.010mm on the zigbee board at the lane's
#            declared 0.01mm/px) + the half pixel of ink bias the check
#            subtracts from its own reading + half a pixel of spare
#   0.010mm: the check's SAMPLE_STEP/2 path-sampling slop (0.005), the
#            3-decimal coordinates assemble_laser writes (<=0.002), spare
#
# = 0.030mm of headroom at 2540 dpi. A stroke that needs that 0.03 to fit is
# a stroke on the edge of curing a pad; it goes. The independent check is the
# judge either way — this margin exists so it never has to argue.
SILK_EPS_PX = 2.0
SILK_EPS_MM = 0.010
SILK_BACKOFF = 0.01     # mm: a CLIPPED end is placed where the field reads
#                         clearance + eps + this, so Lipschitz-1 alone covers
#                         the whole gap back to the nearest proven sample
SILK_MIN_STROKE = 0.05  # mm: a survivor shorter than this is not a legend
#                         feature, it is a dot of cured mask


@dataclass
class SilkClip:
    """What the clip did to the legend, for the run-sheet. A silk legend that
    silently lost strokes is a lie — and so is one that silently lost PARTS of
    strokes, which is why clipped and dropped are counted separately."""
    strokes: list[list[tuple]] = field(default_factory=list)  # machine frame
    chains: int = 0          # stroke chains in the gerber
    clipped: int = 0         # chains that lost part of themselves
    dropped: int = 0         # chains that lost all of themselves
    kept_mm: float = 0.0
    removed_mm: float = 0.0
    clearance: float = 0.0   # guaranteed clearance of every kept point from a
    #                          mask APERTURE — the raster threshold with the
    #                          half-pixel ink bias taken back off, so this is a
    #                          polygon statement, not a raster one (Article IX)
    asked: float = 0.0       # what the job asked for

    @property
    def note(self) -> str:
        return (f"{self.chains} gerber chains -> {len(self.strokes)} strokes "
                f"({self.clipped} clipped, {self.dropped} dropped); "
                f"{self.removed_mm:.2f}mm of "
                f"{self.kept_mm + self.removed_mm:.2f}mm removed; every kept "
                f"point >= {self.clearance:.3f}mm from a mask aperture "
                f"(job asks {self.asked:.3f})")


def _runs(ok: np.ndarray) -> list[tuple[int, int]]:
    """Maximal runs of True as inclusive (first, last) index pairs."""
    idx = np.nonzero(ok)[0]
    if idx.size == 0:
        return []
    brk = np.nonzero(np.diff(idx) > 1)[0]
    starts = np.concatenate([[0], brk + 1])
    ends = np.concatenate([brk, [idx.size - 1]])
    return [(int(idx[a]), int(idx[b])) for a, b in zip(starts, ends)]


def _cross(fn, bad: float, good: float, level: float, iters: int = 30):
    """Bisect fn between a point below `level` and one at/above it; return the
    parameter on the SAFE side of the final bracket. Order-agnostic, so it
    walks a run's left end and its right end with the same code."""
    for _ in range(iters):
        mid = 0.5 * (bad + good)
        if fn(mid) >= level:
            good = mid
        else:
            bad = mid
    return good


def _mask_probe(win: boardmaps.BoardWindow, dist: np.ndarray):
    """-> probe(xs, ys) giving a LOWER BOUND (mm) on each point's distance to
    the mask ink, bilinearly interpolated the way checks.BoardMaps.sample
    does. Outside the window it is the reading at the clamped border point
    MINUS the overshoot: distance fields are Lipschitz-1, so that is a bound,
    never an over-read. Legends really do run off the Edge.Cuts extents (the
    zigbee legend clears the top edge by 0.5mm) and a plain clamp there would
    measure fiction — which is the whole class of bug this function exists to
    avoid."""
    h, w = dist.shape

    def probe(xs, ys):
        i, j = win.world_to_px(np.atleast_1d(np.asarray(xs, float)),
                               np.atleast_1d(np.asarray(ys, float)))
        ci = np.clip(i, 0.5, h - 0.5)
        cj = np.clip(j, 0.5, w - 0.5)
        over = np.hypot(i - ci, j - cj) / win.ppmm
        return ndimage.map_coordinates(dist, [ci - 0.5, cj - 0.5],
                                       order=1) - over
    return probe


def _clip_chain(pts: list[tuple], probe, need: float, step: float,
                backoff: float) -> tuple[list[list[tuple]], float]:
    """Split ONE board-frame stroke chain against the forbidden region.

    -> (pieces, removed_mm). Every point of every piece provably reads
    >= `need` from the mask ink:

      * each segment is sampled at `step` (half a pixel) and a sample counts
        as clear only at `need + step/2` — a distance field is Lipschitz-1,
        so that margin covers every point BETWEEN two clear samples;
      * a clipped end is then bisected out to where the field reads
        `need + backoff` and placed there. Because step <= backoff, the
        bisection bracket is never wider than `backoff`, so Lipschitz-1
        covers the gap from that end back to the first proven sample too.

    Pieces that end on an ORIGINAL vertex keep it exactly, so a chain with
    nothing to clip re-emits byte-identically to before this clip existed.
    """
    pieces: list[list[tuple]] = []
    cur: list[tuple] = []
    removed = 0.0

    def flush():
        nonlocal cur
        if len(cur) >= 2:
            pieces.append(cur)
        cur = []

    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        L = float(np.hypot(x1 - x0, y1 - y0))
        if L <= 0.0:
            continue                      # duplicate vertex: nothing to clip
        n = max(1, int(np.ceil(L / step)))
        t = np.linspace(0.0, 1.0, n + 1)
        f = probe(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)
        runs = _runs(f >= need + (L / n) / 2)
        if not runs:
            removed += L
            flush()
            continue

        def at(tt, x0=x0, y0=y0, x1=x1, y1=y1):
            return (x0 + (x1 - x0) * tt, y0 + (y1 - y0) * tt)

        def probe_t(tt, at=at):
            x, y = at(tt)
            return float(probe([x], [y])[0])

        kept_t = 0.0
        for a, b in runs:
            t_lo, t_hi = float(t[a]), float(t[b])
            if a > 0 and f[a] >= need + backoff:
                t_lo = _cross(probe_t, float(t[a - 1]), t_lo, need + backoff)
            if b < n and f[b] >= need + backoff:
                t_hi = _cross(probe_t, float(t[b + 1]), t_hi, need + backoff)
            kept_t += t_hi - t_lo
            if not (a == 0 and cur):
                flush()
                cur = [at(t_lo)]
            cur.append(at(t_hi))
            if b < n:
                flush()
        removed += L * max(0.0, 1.0 - kept_t)
    flush()

    out: list[list[tuple]] = []
    for pc in pieces:
        plen = sum(float(np.hypot(q[0] - p[0], q[1] - p[1]))
                   for p, q in zip(pc[:-1], pc[1:]))
        if plen < SILK_MIN_STROKE:
            removed += plen
            continue
        out.append(pc)
    return out, removed


def silk_strokes(job: PcbJob, win: boardmaps.BoardWindow,
                 mask_map: np.ndarray) -> SilkClip:
    """Stroke chains from the B.Silkscreen gerber, machine-framed with the
    SAME derived transform as every layer, CLIPPED against the mask-opening
    map: the part of a stroke nearer than the configured clearance to a
    solderable aperture is cut out and the clear part still fires (cured
    white on a pad repels solder — the 2026-07-30 field-legend incident).
    Clipped and dropped chains are both counted for the run-sheet.

    `win` is BOTH the transform frame and the measurement frame, and it must
    be the TIGHT Edge.Cuts extents (the 154/124 law; a padded window shifts
    every stroke by the pad). `mask_map` must therefore CONTAIN the mask
    layer — ink touching the window border means the layer continues outside
    it, where this module cannot measure, and that refuses.
    """
    clearance = float(job.phases["silk"]["clearance"])
    chains = _stroke_chains(job.files["silk"])
    if mask_map.any() and (mask_map[0].any() or mask_map[-1].any()
                           or mask_map[:, 0].any() or mask_map[:, -1].any()):
        raise ValueError(
            "the mask raster has ink on its window border — the layer "
            "continues outside the window, so the silk clearance cannot be "
            "measured there; hand silk_strokes a window that contains the "
            "mask layer")
    need = clearance + SILK_EPS_PX / win.ppmm + SILK_EPS_MM
    step = min(0.5 / win.ppmm, SILK_BACKOFF)   # the bracket <= backoff law
    probe = _mask_probe(win, boardmaps.dist_mm(mask_map, win))
    off = boardmaps.machine_offset(win, job.anchor, job.mirror)
    rep = SilkClip(chains=len(chains), asked=clearance,
                   clearance=need - 0.5 / win.ppmm)
    for ch in chains:
        pieces, removed = _clip_chain(ch, probe, need, step, SILK_BACKOFF)
        rep.removed_mm += removed
        if not pieces:
            rep.dropped += 1
            continue
        if removed > 0.0:
            rep.clipped += 1
        for pc in pieces:
            rep.kept_mm += sum(float(np.hypot(q[0] - p[0], q[1] - p[1]))
                               for p, q in zip(pc[:-1], pc[1:]))
            # the derived frame from the one place that owns its sign: side A's
            # legend is NOT mirrored (it is lasered front-up), side B's is
            mx, my = boardmaps.machine_xy(off, job.mirror,
                                          [p[0] for p in pc],
                                          [p[1] for p in pc])
            rep.strokes.append([(float(a), float(b)) for a, b in zip(mx, my)])
    return rep


def silk_program(job: PcbJob, win: boardmaps.BoardWindow,
                 mask_map: np.ndarray) -> tuple[str, SilkClip]:
    """-> (program text, the SilkClip report)."""
    rep = silk_strokes(job, win, mask_map)
    if not rep.strokes:
        raise ValueError("silk layer produced no strokes clear of pads — "
                         "nothing to cure is a design problem, not a "
                         "program")
    silk = job.phases["silk"]
    return assemble_laser(f"{job.name} silk", rep.strokes,
                          dose_s=silk["dose"], feed=silk["feed"],
                          header=program_header(job, "silk")), rep


def _stroke_chains(gbr: Path) -> list[list[tuple]]:
    """D02 lifts the pen, D01 draws — chains of draw segments. Flashes
    (D03) in a silk layer are refused: KiCad's line font never flashes,
    and a flashed aperture has no centerline for the laser to follow."""
    text = Path(gbr).read_text(errors="replace")
    fs = boardmaps._FS.search(text)
    mo = boardmaps._MO.search(text)
    if not fs or not mo or mo.group(1) != "MM":
        raise ValueError(f"{gbr}: needs FSLA + MM (KiCad export)")
    xdiv, ydiv = 10.0 ** int(fs.group(2)), 10.0 ** int(fs.group(4))
    chains: list[list[tuple]] = []
    cur: dict[str, float | None] = {"X": None, "Y": None}
    chain: list[tuple] = []
    for line in text.splitlines():
        op = boardmaps._OP.search(line)
        if not op:
            continue
        for axis, digits in boardmaps._COORD.findall(line):
            cur[axis] = int(digits) / (xdiv if axis == "X" else ydiv)
        if cur["X"] is None or cur["Y"] is None:
            continue
        pt = (cur["X"], cur["Y"])
        code = op.group(1)
        if code == "3":
            raise ValueError(f"{gbr}: flash in the silk layer — no "
                             f"centerline to cure; use stroke text")
        if code == "2":
            if len(chain) >= 2:
                chains.append(chain)
            chain = [pt]
        else:
            chain.append(pt)
    if len(chain) >= 2:
        chains.append(chain)
    return chains


# ------------------------------------------------- side-2 annular scrub laps
# The 2026-07-30 paint-across-bores finding (the twosided suite's tripwire,
# now flipped): FlatCAM's `paint` fills mask apertures from the mask layer
# alone and knows NOTHING about the Excellon, so on side 2 of a flipped board
# — where every hole is already drilled — it drove the 0.3 spring tip
# straight across each bore and the gate refused the program (flip.py's
# annular-scrub laws, rim margin −0.60 on the fixture). The generator's fix
# is an APERTURE-CLASS SPLIT:
#
#   * SMD apertures (no hole underneath) keep FlatCAM `paint`, fed a FILTERED
#     mask (scrub_mask below) in which every hole-centred flash became a D02
#     move — zero ink, modal state intact. Filtering an INPUT gerber is not a
#     G-code emission; Article V is untouched.
#   * hole-centred apertures get ANNULAR laps generated HERE, like the silk
#     strokes: concentric segment polylines (no arcs — Article V refuses
#     them) around the hole the Excellon declares, appended to the scrub op.
#
# A mask aperture that OVERLAPS a hole off-centre refuses: neither a disc nor
# a concentric annulus scrubs it honestly. So does a non-circle aperture or
# pad over a hole — Board B's hole-centred pads are circles, and the day an
# oval DIP pad needs laps this refusal is where that work starts.
SCRUB_LAP_CHORD = 0.01   # mm: max chord sagitta of a lap polyline — one
#                          raster pixel at the lane's 2540dpi, so the polygon
#                          and every raster reading of it agree
SCRUB_LAP_MARGIN = 0.05  # mm engineered headroom over flip.py's bars: the
#                          checks re-measure the bytes with their own EDT
#                          (raster eps + SAMPLE_STEP/2 ≈ 0.012) and a lap
#                          that needs this margin to pass is a lap on the
#                          edge of levering a pad off
SCRUB_WINDOW_MIN = 0.05  # the single-sided scrub-window bar (checks.py) —
#                          a lap must also satisfy the laws side 1 lives by
LAP_CONCENTRIC_TOL = 0.05  # "hole-centred" tolerance: pcbjob.GAUGE_MATCH_TOL
#                          and flip.CONCENTRIC_TOL's artwork budget, restated
#                          here only because both licensing modules sit above
#                          this one in the import graph


def _concentric(fl, x: float, y: float):
    """The flashes within LAP_CONCENTRIC_TOL of (x, y) — 0 or 1 expected."""
    return [f for f in fl if abs(f[0] - x) <= LAP_CONCENTRIC_TOL
            and abs(f[1] - y) <= LAP_CONCENTRIC_TOL]


def hole_apertures(job: PcbJob) -> list[dict]:
    """Side 2's hole-centred scrub set, from DESIGN numbers only (gerber
    flash text + the Excellon — no raster): one entry per drilled hole that
    carries a mask aperture, with the hole, mask and copper-pad diameters the
    lap band is computed from. Holes with no mask aperture (flip gauges, bare
    bores) are legitimately not in the scrub set and are skipped."""
    holes = boardmaps.excellon(job.files["drl"])
    mfl = boardmaps.flashes(job.files["mask"])
    cfl = boardmaps.flashes(job.files["cu"])
    out: list[dict] = []
    for hx, hy, hd in holes:
        near = _concentric(mfl, hx, hy)
        if not near:
            for fx, fy, shape, fd in mfl:
                if shape == "C" and fd is not None and \
                        float(np.hypot(fx - hx, fy - hy)) < fd / 2 + hd / 2:
                    raise ValueError(
                        f"mask aperture at ({fx:.3f},{fy:.3f}) overlaps the "
                        f"Ø{hd:g} hole at ({hx:.3f},{hy:.3f}) OFF-CENTRE — "
                        f"neither paint nor a concentric annular lap scrubs "
                        f"that honestly; move the aperture or drop it")
            continue
        if len(near) > 1:
            raise ValueError(f"{len(near)} mask flashes at the hole at "
                             f"({hx:.3f},{hy:.3f}) — one aperture per hole")
        _, _, mshape, mdia = near[0]
        if mshape != "C" or mdia is None:
            raise ValueError(
                f"mask aperture over the hole at ({hx:.3f},{hy:.3f}) is "
                f"{mshape!r}, not a circle — annular laps only know "
                f"circles, and a non-circular hole-centred aperture needs "
                f"its own generator before it can be scrubbed")
        pads = _concentric(cfl, hx, hy)
        if not pads:
            raise ValueError(
                f"the hole at ({hx:.3f},{hy:.3f}) has a mask aperture but "
                f"no copper flash in {job.files['cu'].name} — the lap band "
                f"needs the DESIGN pad diameter, and a pad drawn some other "
                f"way is a pad this generator refuses to guess at")
        _, _, pshape, pdia = pads[0]
        if pshape != "C" or pdia is None:
            raise ValueError(
                f"copper pad over the hole at ({hx:.3f},{hy:.3f}) is "
                f"{pshape!r}, not a circle — see the mask refusal above")
        out.append({"hx": hx, "hy": hy, "hole_d": hd,
                    "mask_d": float(mdia), "pad_d": float(pdia)})
    return out


def scrub_mask(job: PcbJob, work_dir: Path) -> Path:
    """The mask file `paint` should read for THIS job's scrub phase. On
    anything but side 2 of a flipped board that is the export itself; on
    side 2 it is a filtered copy beside the engine's Tcl, with every
    hole-centred flash rewritten to a D02 move so paint never laps a bore."""
    if job.side != pcbjob.SIDE_ORDER[1]:
        return job.files["mask"]
    hp = hole_apertures(job)
    centres = [(h["hx"], h["hy"]) for h in hp]

    def drop(x: float, y: float) -> bool:
        return any(abs(x - cx) <= LAP_CONCENTRIC_TOL
                   and abs(y - cy) <= LAP_CONCENTRIC_TOL
                   for cx, cy in centres)

    out = Path(work_dir) / "mask-scrub.gbr"
    n = boardmaps.rewrite_flashes(job.files["mask"], out, drop)
    if n != len(hp):
        raise ValueError(f"filtered {n} mask flashes but classified "
                         f"{len(hp)} hole-centred apertures — the two scans "
                         f"disagree about the same file")
    text = out.read_text()
    if "D03*" not in text and "D01*" not in text:
        raise ValueError(
            "side 2's mask holds ONLY hole-centred apertures — paint would "
            "run on an empty layer and FlatCAM's failure mode there is not "
            "modeled; an all-annular side-2 scrub needs its own path the "
            "day a real board asks for it")
    return out


def annular_laps(job: PcbJob,
                 win: boardmaps.BoardWindow | None = None
                 ) -> tuple[list[str], dict]:
    """Side 2's annular laps as ready-to-append scrub-op lines (machine
    frame, phase feeds, no arcs). -> (lines, stats).

    Band per hole (tool radius r, everything else a design number):
        rc_max = min(pad_r  − r − (INSIDE + MARGIN),          # stay ON copper
                     mask_r − r − max(deflate, WINDOW+MARGIN))# and IN the window
        rc_min =     hole_r + r + (RIM + MARGIN)              # stay OFF the rim
    An empty band refuses by hole — a pad with no legal lap is a design
    problem the operator must see, not a pad silently skipped (a skipped pad
    would pass the gate: scrub COVERAGE deliberately has no bar).
    One lap at the band's midpoint when the band is narrower than the
    stepover; else evenly spaced laps no farther apart than the stepover."""
    # flip.py owns the two bars but sits above this module in the import
    # graph (flip → checks → reemit), so they are imported at call time
    from .flip import SCRUB_ANNULAR_INSIDE, SCRUB_ANNULAR_RIM
    if job.side != pcbjob.SIDE_ORDER[1]:
        raise ValueError("annular laps are side 2's geometry — side 1 has "
                         "no holes yet and paint's disc laps are right")
    p = job.phases["scrub"]
    tool = job.phase_tool("scrub")
    r = tool.diameter / 2
    win = win or boardmaps.extents(job.files["edge"])
    off = boardmaps.machine_offset(win, job.anchor, job.mirror)
    step = tool.diameter * (1.0 - float(p["overlap"]) / 100.0)
    depth, feed, plunge = (float(p[k]) for k in ("depth", "feed", "plunge"))
    inside_need = SCRUB_ANNULAR_INSIDE + SCRUB_LAP_MARGIN
    rim_need = SCRUB_ANNULAR_RIM + SCRUB_LAP_MARGIN
    window_need = max(float(p["offset"]), SCRUB_WINDOW_MIN + SCRUB_LAP_MARGIN)
    lines: list[str] = []
    nlaps = 0
    pads = hole_apertures(job)
    for h in pads:
        pad_r, mask_r, hole_r = (h["pad_d"] / 2, h["mask_d"] / 2,
                                 h["hole_d"] / 2)
        rc_max = min(pad_r - r - inside_need, mask_r - r - window_need)
        rc_min = hole_r + r + rim_need
        if rc_min > rc_max + 1e-9:
            raise ValueError(
                f"no legal annular lap on the Ø{h['pad_d']:g} pad / "
                f"Ø{h['mask_d']:g} aperture over the Ø{h['hole_d']:g} hole "
                f"at ({h['hx']:.3f},{h['hy']:.3f}): the tool centre must "
                f"stay ≤{rc_max:.3f} and ≥{rc_min:.3f} — a pad this tight "
                f"cannot be scrubbed with a Ø{tool.diameter:g} tool")
        band = rc_max - rc_min
        n = 1 + int(band / step)
        radii = ([0.5 * (rc_min + rc_max)] if n == 1
                 else list(np.linspace(rc_min, rc_max, n)))
        for rc in radii:
            nseg = max(24, int(np.ceil(
                np.pi / np.arccos(1.0 - SCRUB_LAP_CHORD / rc))))
            t = np.linspace(0.0, 2.0 * np.pi, nseg + 1)
            bx = h["hx"] + rc * np.cos(t)
            by = h["hy"] + rc * np.sin(t)
            bx[-1], by[-1] = bx[0], by[0]      # closed exactly, not to eps
            mx, my = boardmaps.machine_xy(off, job.mirror, bx, by)
            lines += [f"G0 Z2.0000",
                      f"G0 X{mx[0]:.4f} Y{my[0]:.4f}",
                      f"G1 Z{depth:.4f} F{plunge:g}",
                      f"G1 X{mx[1]:.4f} Y{my[1]:.4f} F{feed:g}"]
            lines += [f"G1 X{x:.4f} Y{y:.4f}"
                      for x, y in zip(mx[2:], my[2:])]
            nlaps += 1
    lines.append("G0 Z2.0000")
    return lines, {"pads": len(pads), "laps": nlaps,
                   "margin": SCRUB_LAP_MARGIN, "chord": SCRUB_LAP_CHORD}


def scrub_op(nc_path: Path, job: PcbJob,
             win: boardmaps.BoardWindow | None = None) -> OpResult:
    """The scrub phase's ONE op: FlatCAM's paint interchange strict-read
    (read_phase, unchanged), and — on side 2 of a flipped board — the
    annular laps appended in the same op: same tool, same feeds, same floor,
    one stage marker. Anything single-sided or side-1 passes through
    byte-identical to read_phase, which is why the coupon goldens cannot
    move. This is the entry every side-2 scrub assembly must use; the paint
    file it reads must have been generated against scrub_mask()'s filtered
    input, or the gate will refuse the result exactly as it did the day the
    tripwire was written."""
    base = read_phase(nc_path, job, "scrub")
    if job.side != pcbjob.SIDE_ORDER[1]:
        return base
    lap_lines, _ = annular_laps(job, win=win)
    lines = base.lines + lap_lines
    plen = path_length(lines)
    return OpResult(label=base.label, kind=base.kind, tool=base.tool,
                    lines=lines, path_len_mm=plen,
                    est_min=plen / max(float(job.phases["scrub"]["feed"]),
                                       1.0))
