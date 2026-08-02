#!/usr/bin/env python3
"""pcb-rnd RS-274X -> the one-op-per-line dialect boardmaps.py insists on.

GRADUATED from the R2-S5 lab probe (scratchpad/pcbrnd-lab/s5_filter.py), which
measured the two ways pcb-rnd's own dialect defeats `clauderacam.pcb.boardmaps`.
pcb-rnd packs a whole polyline onto one text line and puts the aperture select
inline with it:

    G54D11*X10000000Y12000000D02*Y20000000D01*

Two independent failures follow, and the lab isolated them one at a time:

  1. `boardmaps._flash_scan` REFUSES a line carrying more than one draw/flash op
     (">1 draw/flash op on one line").  Loud, so it cannot ship a wrong answer.

  2. Its aperture tracker only fires on a line that is EXACTLY `[G54]Dnn*`, so
     with the select glued to the first op every flash comes back with shape ""
     and diameter None.  The lab's `--keep-select-inline` half-fix exists purely
     to demonstrate this second failure alone: it splits the ops, `flashes()`
     stops raising, and every flash SILENTLY loses its aperture.  That is the
     dangerous one, and it is why this filter hoists selects rather than merely
     unpacking lines.

  3. `_scan_coords` does not refuse either, but keeps only the LAST modal
     position on each line, silently dropping every pen-up start point that
     shares a line with a draw.  On a boundary layer that reads as an outline
     several mm smaller than the board (the lab measured a tab outline whose
     apex, which starts both of its segments and is therefore never last,
     vanished entirely).

This filter is purely SYNTACTIC and loses nothing:
  * every `[G54]Dnn*` aperture select goes on its own line
  * every `<coords>D0n*` op goes on its own line
  * every op is written with BOTH coordinate words, resolved from the modal
    state, so no consumer has to carry modality across lines
Header/extension lines (G04 comments, %..% parameters, M02*) pass through
byte-identical, so the format spec, apertures and polarity are untouched.

Usage: gerber_filter.py IN.gbr OUT.gbr
       from gerber_filter import convert
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# The op alternative MUST come first: `D02*` would otherwise be eaten by the
# aperture-select branch and re-emitted as a select for aperture 2.
TOK = re.compile(r"((?:[XY][-+]?\d+)*)D0([123])\*"
                 r"|(?:G54)?D(\d+)\*")
COORD = re.compile(r"([XY])([-+]?\d+)")


def convert(text: str) -> tuple[str, dict]:
    """-> (one-op-per-line text, statistics).

    Raises if an op is reached with no resolved modal position: an op whose
    coordinate cannot be determined is not something to guess at.
    """
    out: list[str] = []
    stat = {"lines_in": 0, "lines_out": 0, "selects_hoisted": 0,
            "ops_split": 0, "coords_completed": 0}
    cur = {"X": None, "Y": None}
    for raw in text.splitlines():
        stat["lines_in"] += 1
        line = raw.rstrip("\r")
        if not line or line.startswith("%") or line.startswith("G04"):
            out.append(line)
            continue
        pieces, pos, n_tok = [], 0, 0
        for m in TOK.finditer(line):
            if m.start() > pos:                    # unmatched residue
                pieces.append(line[pos:m.start()])
            pos = m.end()
            n_tok += 1
            if m.group(3) is not None:             # aperture select
                if m.start() != 0 or m.end() != len(line):
                    stat["selects_hoisted"] += 1
                pieces.append(f"G54D{m.group(3)}*")
                continue
            words = COORD.findall(m.group(1) or "")
            for axis, digits in words:
                cur[axis] = digits
            if cur["X"] is None or cur["Y"] is None:
                raise ValueError(f"op with no resolved position: {line!r}")
            if len(words) < 2:
                stat["coords_completed"] += 1
            pieces.append(f"X{cur['X']}Y{cur['Y']}D0{m.group(2)}*")
        if pos < len(line):
            pieces.append(line[pos:])
        if not pieces:
            pieces = [line]
        if n_tok > 1:
            stat["ops_split"] += 1
        out.extend(p for p in pieces if p)
    stat["lines_out"] = len(out)
    return "\n".join(out) + "\n", stat


def filter_file(src: Path, dst: Path) -> dict:
    text, stat = convert(Path(src).read_text())
    Path(dst).write_text(text)
    return stat


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    stat = filter_file(src, dst)
    print(f"  {src.name} -> {dst.name}: "
          + " ".join(f"{k}={v}" for k, v in stat.items()))


if __name__ == "__main__":
    main()
