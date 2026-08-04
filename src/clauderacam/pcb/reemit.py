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
# keyed by SETUP ROLE, not face: which face runs first is the job's own
# [twosided] `first` (the 2026-08-03 ordering law), and these instructions
# are about the SEQUENCE — {first}/{second} format to the face names.
_SIDE_STEPS = {
    ("first", "mill"): (
        ("FULL-coverage tape is mandatory - a bowed blank makes every depth "
         "number fiction",
         "{first} copper up on the virgin blank, auto-level over the board, "
         "Z0 = {first} copper, G54 = board SW corner"),
        None),
    ("first", "scrub"): (
        ("legend wiped, spring tool fitted; ZERO holes exist",
         "every solderable pad takes FULL disc laps, bare to its future "
         "rim"),
        "program D holes - the non-pad bores, no operator step between"),
    ("first", "holes"): (
        "same setup; ONLY the non-pad bores - flip gauges + mounts. Every "
        "PAD hole waits for setup 2, after BOTH scrubs",
        "program E pins - no operator step between"),
    ("first", "pins"): (
        "same setup, still; fit the pin drill - the registration holes are "
        "the LAST thing cut in setup 1",
        ("FLIP: set the {pins} dowels, turn the blank about {axis} onto "
         "them, re-tape FULL coverage",
         "deburr the bores' exits on the still-raw {second} face first - "
         "it is bare copper and machines next",
         "re-level and re-touch Z0 on the {second} copper; NEVER re-zero "
         "XY - it is the registration the pins bought")),
    ("first", "excise"): (
        "same setup, still; the sub-blank perimeter with tabs - the full "
        "blank stays put, nothing comes free yet",
        ("SNAP the sub-blank out of the stock, then FLIP: set the {pins} "
         "dowels, turn the SUB-BLANK about {axis} onto them",
         "deburr the bores' exits on the still-raw {second} face first - "
         "it is bare copper and machines next",
         "tape the sub-blank FULL coverage - it is the only workholding "
         "setup 2 has besides the pins",
         "re-level and re-touch Z0 on the {second} copper; NEVER re-zero "
         "XY - it is the registration the pins bought")),
    ("second", "mill"): (
        ("blank FLIPPED onto the pins, re-taped, re-leveled, Z0 = {second} "
         "copper, XY untouched",
         "confirm no auto-level probe point sat in a bore - one that did "
         "wrote a false low into the height map"),
        ("READ THE FLIP GAUGES with a loupe and write the numbers down - "
         "an even ring means a perfect flip",
         "THEN squeegee + UV-cure this side's mask and coat white")),
    ("second", "scrub"): (
        ("legend wiped, spring tool fitted; still no pad holes",
         "the laps are this face's SOLDER PLAN - declared inert rings "
         "keep their coat",
         "setup 1's gauge + mount bores ARE open under the tool; the "
         "laps stay clear of them and the gate proves it"),
        "program D holes - every pad hole then the cutout, no operator "
        "step between"),
    ("second", "holes"): (
        "same setup; EVERY pad hole - both faces' solder plans are "
        "scrubbed by now - then the outline cut with tabs, last",
        ("snap the {gaps} tabs{where} of {gapsize}mm, file the stubs, "
         "deburr the edge",
         "chase the pad-hole exits on the {first} pads GENTLY and IPA the "
         "tape residue off that face",
         "off-machine: stencil + paste + hotplate reflow the BACK, THEN "
         "the wire vias, THEN the THT parts from the front")),
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
    fmt = {}
    if job.side:
        sb, sa = _SIDE_STEPS.get((pcbjob.role_of(job), name), (None, None))
        before, after = sb or before, sa or after
        if (name == "pins" and pcbjob.role_of(job) == "first"
                and job.has_phase("excise")):
            # the flip rides the LAST setup-1 program; with an excise cut
            # configured that is program F, not the pins
            after = "program F excise - the sub-blank perimeter, no " \
                    "operator step between"
        fmt.update(first=job.sides[0].upper(), second=job.sides[1].upper())
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
            f"split is the job (pcbjob.PROGRAM_PHASES / ROLE_PROGRAMS)")
    return assemble(job, ops, header=program_header(job, name, ops))


def excise_ops(job: PcbJob,
               win: boardmaps.BoardWindow | None = None) -> OpResult:
    """The sub-blank excise cut (operator request 2026-08-03): a tabbed
    rectangle around board + pin footprint, cut LAST in setup 1 so the
    operator snaps a registered sub-blank out of stock that will not fit
    the workholding once flipped. Native emission, like the pin block — a
    rectangle with tab gaps is four polyline chains and needs no external
    engine; every line goes through emit.assemble's parser (Article V).

    Geometry: the derived pcbjob.excise_rect, path riding tool-radius
    OUTSIDE it so the sub-blank keeps its declared size; corners are sharp
    polyline corners (the overcut lands in waste). Tabs follow the
    cutout's law and precedent: the "2lr" placement puts two on each of
    the LEFT and RIGHT edges at the quarter points, the gaps exist at
    EVERY depth pass (full-height tabs, more conservative than the bar),
    and the gap in path length is gapsize + one tool diameter so the
    MATERIAL left is exactly gapsize."""
    p = job.phases["excise"]
    tool = job.phase_tool("excise")
    r = tool.diameter / 2
    win = win or boardmaps.extents(job.files["edge"], cross_check=False)
    x0, y0, x1, y1 = pcbjob.excise_rect(job)
    px0, py0, px1, py1 = x0 - r, y0 - r, x1 + r, y1 + r
    depth, dpp = float(p["depth"]), float(p["dpp"])
    feed, plunge = float(p["feed"]), float(p["plunge"])
    gh = (float(p["gapsize"]) + tool.diameter) / 2   # half a PATH gap
    if pcbjob.tab_count(p["gaps"]) != 4 or "lr" not in str(p["gaps"]).lower():
        raise ValueError(
            f"excise gaps {p['gaps']!r}: this generator implements the "
            f"'2lr' placement (two tabs per left/right edge, the cutout's "
            f"own steering precedent) — another placement is new work, "
            f"not a silent variation")
    yq1 = py0 + (py1 - py0) * 0.25
    yq3 = py0 + (py1 - py0) * 0.75
    chains = [
        [(px1, yq1 + gh), (px1, yq3 - gh)],                    # right mid
        [(px1, yq3 + gh), (px1, py1), (px0, py1),
         (px0, yq3 + gh)],                                     # top U
        [(px0, yq3 - gh), (px0, yq1 + gh)],                    # left mid
        [(px0, yq1 - gh), (px0, py0), (px1, py0),
         (px1, yq1 - gh)],                                     # bottom U
    ]
    off = boardmaps.machine_offset(win, job.anchor, job.mirror)
    zs = [round(-dpp * k, 4) for k in range(1, int(abs(depth) / dpp) + 1)]
    if not zs or abs(zs[-1] - depth) > 1e-9:
        zs.append(depth)
    lines: list[str] = []
    for z in zs:
        for ch in chains:
            mx, my = boardmaps.machine_xy(off, job.mirror,
                                          [q[0] for q in ch],
                                          [q[1] for q in ch])
            lines += [f"G0 Z2.0000",
                      f"G0 X{mx[0]:.4f} Y{my[0]:.4f}",
                      f"G1 Z{z:.4f} F{plunge:g}",
                      f"G1 X{mx[1]:.4f} Y{my[1]:.4f} F{feed:g}"]
            lines += [f"G1 X{a:.4f} Y{b:.4f}"
                      for a, b in zip(mx[2:], my[2:])]
    lines.append("G0 Z2.0000")
    plen = path_length(lines)
    return OpResult(label="pcb-excise", kind="excise", tool=tool.num,
                    lines=lines, path_len_mm=plen,
                    est_min=plen / max(feed, 1.0))


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
    on_copper: list[bool] = field(default_factory=list)  # parallel to
    #                          strokes: True = the stroke's ink lies mostly
    #                          over copper (the heat-sink substrate) — the
    #                          2026-08-03 test-fire ladder measured that a
    #                          dose crisp on bare fiberglass rubs off copper
    #                          after one pass, so these strokes fire twice
    #                          when phases.silk.copper_passes says so
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
                 mask_map: np.ndarray,
                 cu_dist: np.ndarray | None = None) -> SilkClip:
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
    # substrate classification (the 2026-08-03 dose ladder): a stroke is
    # "over copper" when the MAJORITY of its sampled centreline sits on
    # copper ink — majority, not any-touch, because the ladder also showed
    # the fiberglass sweet spot is ONE pass and a boundary-crossing stroke
    # should not drag its whole length into the second pass. cu_probe reads
    # the copper distance field; 0 (within half a pixel) means inside ink.
    cu_probe = (None if cu_dist is None else _mask_probe(win, cu_dist))
    on_cu_bar = 0.5 / win.ppmm

    def over_copper(pc) -> bool:
        if cu_probe is None:
            return False
        xs, ys = [], []
        for p, q in zip(pc[:-1], pc[1:]):
            n = max(2, int(np.hypot(q[0] - p[0], q[1] - p[1]) / 0.2) + 1)
            for t in np.linspace(0.0, 1.0, n):
                xs.append(p[0] + (q[0] - p[0]) * t)
                ys.append(p[1] + (q[1] - p[1]) * t)
        return float(np.mean(cu_probe(xs, ys) <= on_cu_bar)) >= 0.5

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
            rep.on_copper.append(over_copper(pc))
    return rep


def silk_program(job: PcbJob, win: boardmaps.BoardWindow,
                 mask_map: np.ndarray) -> tuple[str, SilkClip]:
    """-> (program text, the SilkClip report).

    phases.silk.copper_passes = 2 fires every mostly-over-copper stroke
    TWICE, back to back (2026-08-03 dose ladder: at the dose that reads
    crisp on bare fiberglass in one pass, a line over copper wipes off with
    the IPA — the copper under the coat is a heat sink). Pass count is
    GEOMETRY, not power, so the program still carries exactly one M3 S and
    the laser law holds; absent or 1, this emits byte-identically to
    before the knob existed.
    """
    silk = job.phases["silk"]
    cpasses = int(silk.get("copper_passes", 1))
    cu_dist = None
    if cpasses > 1:
        cu_map = boardmaps.rasterize(job.files["cu"], win)
        cu_dist = boardmaps.dist_mm(cu_map, win)
    rep = silk_strokes(job, win, mask_map, cu_dist=cu_dist)
    if not rep.strokes:
        raise ValueError("silk layer produced no strokes clear of pads — "
                         "nothing to cure is a design problem, not a "
                         "program")
    strokes: list[list[tuple]] = []
    for s, oc in zip(rep.strokes, rep.on_copper):
        strokes += [s] * (cpasses if oc else 1)
    header = program_header(job, "silk")
    if cpasses > 1:
        n_cu = sum(rep.on_copper)
        header = header + [
            f"(dose ladder 2026-08-03: {n_cu} strokes over copper fire "
            f"{cpasses}x back to back, {len(rep.strokes) - n_cu} over "
            f"fiberglass fire once)"]
    return assemble_laser(f"{job.name} silk", strokes,
                          dose_s=silk["dose"], feed=silk["feed"],
                          header=header), rep


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


# --------------------------------------------------- hole-centred apertures
# HISTORICAL NOTE (2026-07-30 → 2026-08-03): the paint-across-bores finding
# — FlatCAM's `paint` knows nothing about the Excellon and drove the spring
# tip across open bores — was first fixed with an APERTURE-CLASS SPLIT
# (hole-centred flashes filtered to D02, annular ring laps generated here).
# The 2026-08-03 ordering law made that machinery unnecessary: no scrub runs
# after a pad hole exists any more, so every solderable aperture takes
# paint's full disc and the annular generator was deleted. The incident's
# CONVICTION lives on in flip.scrub_plan_checks ("scrub clear of existing
# holes", keyed to the setup-1 bores). hole_apertures() below survives as a
# pure artwork READER: its three refusals (off-centre aperture over a hole,
# non-circle aperture, pad drawn some other way) are design-sanity laws the
# suite still enforces.
LAP_CONCENTRIC_TOL = 0.05  # "hole-centred" tolerance: pcbjob.GAUGE_MATCH_TOL
#                          and flip.CONCENTRIC_TOL's artwork budget, restated
#                          here only because both licensing modules sit above
#                          this one in the import graph


def _concentric(fl, x: float, y: float):
    """The flashes within LAP_CONCENTRIC_TOL of (x, y) — 0 or 1 expected."""
    return [f for f in fl if abs(f[0] - x) <= LAP_CONCENTRIC_TOL
            and abs(f[1] - y) <= LAP_CONCENTRIC_TOL]


def hole_apertures(job: PcbJob) -> list[dict]:
    """The hole-centred apertures, from DESIGN numbers only (gerber flash
    text + the Excellon — no raster): one entry per scheduled hole that
    carries a mask aperture, with the hole, mask and copper-pad diameters.
    Since the 2026-08-03 ordering law this feeds NO generator (see the
    historical note above) — it is an artwork reader whose refusals are the
    point. Holes with no mask aperture (flip gauges, bare bores) are
    legitimately absent and skipped."""
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


def inert_apertures(job: PcbJob) -> list[tuple[float, float, str]]:
    """The scrub phase's declared INERT apertures — mask openings the bench
    will never solder (a milled board's dead front rings), listed by the
    board generator in the file [phases.<side>.scrub] `inert` names, one
    `x,y  # reason` per line in the GERBER frame. The 2026-08-03 ordering
    law: the scrub set is the SOLDER PLAN, and the physical opening on this
    process is made by the scrub alone — an inert aperture left unscrubbed
    simply keeps its flood-coat mask, which is the protective finish dead
    copper wants. Artwork stays uniform-open (the mask-blind gate's
    checkability law, tools-board.py); this list is the machining subset."""
    p = job.phases.get("scrub") or {}
    src = p.get("inert")
    if not src:
        return []
    f = Path(src)
    if not f.is_absolute():
        f = job.path.parent / f
    if not f.is_file():
        raise ValueError(f"phases.{job.side}.scrub inert names {f} and it "
                         f"does not exist — an inert list is board truth, "
                         f"exported by the board generator, never typed")
    out = []
    for ln in f.read_text().splitlines():
        body, _, why = ln.partition("#")
        body = body.strip()
        if not body:
            continue
        x, y = (float(v) for v in body.replace(",", " ").split()[:2])
        out.append((x, y, why.strip()))
    return out


def scrub_mask(job: PcbJob, work_dir: Path) -> Path:
    """The mask file `paint` should read for THIS job's scrub phase: the
    export itself, unless the phase declares INERT apertures — then a
    filtered copy beside the engine's Tcl, with each inert flash rewritten
    to a D02 move so paint never cleans a pad nobody solders. (The
    2026-07-30 hole-centred filtering is GONE with the ordering law: no
    hole exists at scrub time any more, so paint covers every hole-centred
    pad fully — that full bare disc is the law's whole point.)"""
    inert = inert_apertures(job)
    if not inert:
        return job.files["mask"]
    hits = [0] * len(inert)

    def drop(x: float, y: float) -> bool:
        for i, (cx, cy, _) in enumerate(inert):
            if (abs(x - cx) <= LAP_CONCENTRIC_TOL
                    and abs(y - cy) <= LAP_CONCENTRIC_TOL):
                hits[i] += 1
                return True
        return False

    out = Path(work_dir) / "mask-scrub.gbr"
    n = boardmaps.rewrite_flashes(job.files["mask"], out, drop)
    missed = [(x, y, why) for (x, y, why), h in zip(inert, hits) if h == 0]
    if missed:
        raise ValueError(
            f"inert entries with NO matching mask flash: "
            f"{[(round(x, 3), round(y, 3)) for x, y, _ in missed[:4]]} — "
            f"the inert list has drifted from the artwork; regenerate both "
            f"from the same board build")
    if n != sum(hits):
        raise ValueError(f"rewrote {n} flashes but matched {sum(hits)} "
                         f"inert entries — the two scans disagree")
    return out


def scrub_op(nc_path: Path, job: PcbJob,
             win: boardmaps.BoardWindow | None = None) -> OpResult:
    """The scrub phase's ONE op: FlatCAM's paint interchange strict-read
    (read_phase), byte-identical to it in every case.

    Until 2026-08-03 this function appended ANNULAR laps on side 2 of a
    flipped board — the spring tip orbiting each already-drilled bore at a
    rim margin, because a 0.3 tip crossing an open hole drops in and levers
    the pad off. The ordering law retired that geometry from the GENERATOR:
    no scrub runs after a pad hole exists any more, so every solderable pad
    takes paint's full disc laps and the bench solders to bare copper all
    the way to what later becomes the drilled rim (the annular band left a
    0.20 cured-mask collar exactly where the joint wets — the operator
    caught it on the viewer). The paint-across-open-bores CONVICTION lives
    on in the gate (Article II: the check stays until the incident is
    impossible; the grammar's chains are what make it impossible), keyed to
    the holes that DO exist at each setup's scrub — setup 1 none, setup 2
    the corner bores."""
    return read_phase(nc_path, job, "scrub")
