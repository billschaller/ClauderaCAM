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

The silk program never touches FlatCAM at all: stroke chains come
straight from the B.Silkscreen gerber's draw words (boardmaps-style
scan), get the SAME derived mirror+offset as every layer, are checked
against the mask-opening map for pad clearance, and leave through
emit.assemble_laser with its own dialect law.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from ..emit import assemble_laser
from ..engine import OpResult, path_length
from ..simulate import parse_line
from . import boardmaps
from .pcbjob import PcbJob

_DROP = re.compile(r"^\s*$|^\(")


def read_phase(nc_path: Path, job: PcbJob, phase: str) -> OpResult:
    """Strict-read one FlatCAM phase file -> an OpResult whose lines are
    ready for emit.assemble(). Refuses on: unparseable words (the strict
    parser is the same one the gate runs), spindle speed != the phase
    tool's rpm, feed words outside the phase's {feed, plunge}, or a
    floor Z that is not the phase's configured depth (param-match law).
    """
    p = job.phases[phase]
    tool = job.phase_tool(phase)
    keep: list[str] = []
    zmin = 0.0
    feeds: set[float] = set()
    rpm_seen = None
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
        if "Z" in coords:
            zmin = min(zmin, coords["Z"])
        line = raw.strip()
        if not _DROP.match(line) and not re.fullmatch(
                r"G2[19]|G90|G94|M0?[35](\s+S[\d.]+)?", line):
            keep.append(line)
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


def silk_strokes(job: PcbJob, win: boardmaps.BoardWindow,
                 mask_map: np.ndarray) -> tuple[list[list[tuple]], int]:
    """Stroke chains from the B.Silkscreen gerber, machine-framed with
    the SAME derived transform as every layer, each chain checked
    against the mask-opening map: a chain nearer than the configured
    clearance to any solderable pad is DROPPED (cured white on a pad
    repels solder), and the drop count is returned for the run-sheet —
    a silk legend that silently lost strokes is a lie.
    """
    clearance = float(job.phases["silk"]["clearance"])
    chains = _stroke_chains(job.files["silk"])
    dist = boardmaps.dist_mm(mask_map, win)
    dx, dy = boardmaps.machine_offset(win, job.anchor)
    kept: list[list[tuple]] = []
    dropped = 0
    for ch in chains:
        ok = True
        for x, y in ch:
            i, j = win.world_to_px(x, y)
            i, j = int(i), int(j)
            if 0 <= i < dist.shape[0] and 0 <= j < dist.shape[1] \
                    and dist[i, j] < clearance:
                ok = False
                break
        if not ok:
            dropped += 1
            continue
        kept.append([(-x + dx, y + dy) for x, y in ch])
    return kept, dropped


def silk_program(job: PcbJob, win: boardmaps.BoardWindow,
                 mask_map: np.ndarray) -> tuple[str, int]:
    """-> (program text, dropped stroke count)."""
    strokes, dropped = silk_strokes(job, win, mask_map)
    if not strokes:
        raise ValueError("silk layer produced no strokes clear of pads — "
                         "nothing to cure is a design problem, not a "
                         "program")
    silk = job.phases["silk"]
    return assemble_laser(f"{job.name} silk", strokes,
                          dose_s=silk["dose"], feed=silk["feed"]), dropped


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
