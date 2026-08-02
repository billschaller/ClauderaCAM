#!/usr/bin/env python3
"""Board B "orbit" — fab artwork, deterministically, from the SEALED board.

    python3 -u tools-fab.py            # regenerate + export + filter + assert
    python3 -u tools-fab.py --no-regen # export from the orbit.lht on disk

Chain: `tools-route.py` (replays the sealed route) -> pcb-rnd `-x cam` with a
USER-LEVEL cam job (`-C`, so nothing under the pcb-rnd install is edited) ->
`gerber_filter.py` -> a merged Excellon -> asserts.

WHY EACH STAGE EXISTS (all four are R2-S5 lab findings, re-measured here):

  * `-C job.conf -x cam` is the only invocation that emits the lane's exact
    filenames; the bare `-x gerber` exporter names the boundary layer with a
    SPACE in it and cannot be told otherwise.

  * pcb-rnd's own RS-274X dialect is refused by `clauderacam.pcb.boardmaps`
    (packed polylines, inline aperture selects). `gerber_filter.convert` is the
    purely syntactic fix; see that module for the two failure modes and why the
    second one is the dangerous one.

  * pcb-rnd splits the drill program into PLATED and UNPLATED files. The lane's
    drill phase consumes ALL holes, so they are merged here. The plated file is
    also kept verbatim as `stitch-drills.txt` — it IS the bench stitch list, and
    this script asserts its 29 hits against MATRIX.md coordinate-for-coordinate.

  * The export frame is Y-MIRRORED with respect to tools-board.py's board frame:
    that generator writes the board into lihata y-DOWN (`lht_y = BOARD_H - y`)
    and pcb-rnd then exports from ITS lower-left origin, so
    `export_y = BOARD_H - board_y`. Everything downstream reads the export
    frame; only the MATRIX cross-check un-flips, and it says so where it does.

WHAT THIS SCRIPT DOES NOT DO — and must not: it does not invent mask or paste
artwork. It did not invent it when the board had none (until 2026-08-02 the
pcb-rnd stackup carried copper, silk and outline groups only, `-x cam` emitted
no F.Mask/B.Mask/B.Paste at all, and this script's job was to say so and exit
non-zero), and it does not invent it now that the board has it. Synthesising a
mask HERE would mean choosing a swell rule and guessing which of 57 SMD lands,
6 ISP pads and 39 through-hole leads (a ring on each face, so 78 more rings)
are solderable — a mask that is wrong in the operator's favour is the exact
failure Article II exists for.

The gap belonged to the BOARD GENERATOR and was closed there: tools-board.py
now emits mask groups on both faces and a bottom paste group, with every
opening carried by the PADSTACK that owns the copper (so it can never drift off
its pad) and a swell of exactly zero, which is Board A's measured convention
and what the lane's raster checker already assumes. The dead front rings are
the one exception and get their openings from the same free-standing lines
their copper comes from.

So the refusal below stays armed, with its meaning inverted: an empty
`missing` list is now the expected state, and anything in it is a REGRESSION in
the stackup rather than a known gap. The asserts at the end are what make that
more than a filename check.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

# The asserts read the raster stack (numpy/scipy/gerbv), which lives in the
# repo venv. Re-exec there so `python3 -u tools-fab.py` behaves the same as
# `.venv/bin/python -u tools-fab.py` — one command, one answer.
# (compare sys.prefix, not the interpreter path: .venv/bin/python is a SYMLINK
# to the system python, so resolve() makes the two indistinguishable and the
# guard silently never fires.)
_VENV = REPO / ".venv"
if (_VENV / "bin" / "python").is_file() and Path(sys.prefix) != _VENV:
    _py = str(_VENV / "bin" / "python")
    os.execv(_py, [_py, str(Path(__file__).resolve()), *sys.argv[1:]])

sys.path.insert(0, str(HERE))
from gerber_filter import filter_file                        # noqa: E402

PCBRND = Path(os.path.expanduser("~/.clauderacam/tools/pcbrnd/pcb-rnd.sh"))
STEM = "orbit"
OUT = HERE / "gerbers-rnd"
BOARD_H = 56.0          # tools-board.BOARD_H — the Y-flip constant
BOARD_W = 66.0          # (both grown from 64 x 54 on 2026-08-02; the FRAME
#                         MISMATCH assert below is what makes this a check and
#                         not a duplicate — it reads the exported Edge_Cuts)

# The lane's filenames, and the pcb-rnd layer each is drawn from. Coordinate
# formats are the lab's: nanometer for gerber, micron for excellon.
CAM_JOB = """li:pcb-rnd-conf-v1 {
\tha:overwrite {
\t\tha:plugins {
\t\t\tha:cam {
\t\t\t\tli:jobs {
\t\t\t\t\t{gerber:clauderacam} {
\t\t\t\t\t\tdesc ClauderaCAM lane filenames, nm gerber + um excellon
\t\t\t\t\t\tplugin gerber --coord-format nanometer --all-layers
\t\t\t\t\t\twrite %base%-F_Cu.gbr=top-copper
\t\t\t\t\t\twrite %base%-B_Cu.gbr=bottom-copper
\t\t\t\t\t\twrite %base%-F_Mask.gbr=[okempty] top-mask
\t\t\t\t\t\twrite %base%-B_Mask.gbr=[okempty] bottom-mask
\t\t\t\t\t\twrite %base%-B_Paste.gbr=[okempty] bottom-paste
\t\t\t\t\t\twrite %base%-F_Silkscreen.gbr=[okempty] top-silk
\t\t\t\t\t\twrite %base%-B_Silkscreen.gbr=[okempty] bottom-silk
\t\t\t\t\t\twrite %base%-Edge_Cuts.gbr=boundary
\t\t\t\t\t\tplugin excellon --coord-format um
\t\t\t\t\t\twrite %base%-PTH.drl=[okempty] virtual(purpose=pdrill)
\t\t\t\t\t\twrite %base%-NPTH.drl=[okempty] virtual(purpose=udrill)
\t\t\t\t\t}
\t\t\t\t}
\t\t\t}
\t\t}
\t}
}
"""

# every layer a [twosided] pcbjob resolves (pcbjob.SIDE_SUFFIXES +
# SHARED_SUFFIXES + PASTE_SUFFIX + DRILL_SUFFIX)
GERBERS = ["F_Cu", "B_Cu", "F_Mask", "B_Mask", "F_Silkscreen", "B_Silkscreen",
           "B_Paste", "Edge_Cuts"]
NOISE = re.compile(r"font-symbol-file|default\.pcb|footprint library")


def sh(cmd: list[str], cwd: Path | None = None) -> str:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    out = "\n".join(l for l in (p.stdout + p.stderr).splitlines()
                    if l.strip() and not NOISE.search(l))
    if p.returncode:
        raise SystemExit(f"FAILED ({p.returncode}): {' '.join(cmd)}\n{out}")
    return out


def read_drl(path: Path) -> list[tuple[float, float, float]]:
    """pcb-rnd excellon -> [(x, y, dia)], export frame."""
    tools: dict[str, float] = {}
    cur: float | None = None
    out: list[tuple[float, float, float]] = []
    for ln in path.read_text().splitlines():
        m = re.match(r"^T(\d+)C([\d.]+)$", ln)
        if m:
            tools[m.group(1)] = float(m.group(2))
            continue
        m = re.match(r"^T(\d+)$", ln)
        if m:
            cur = tools.get(m.group(1))
            continue
        m = re.match(r"^X([-\d.]+)Y([-\d.]+)$", ln)
        if m:
            if cur is None:
                raise SystemExit(f"{path.name}: coordinate before any T select")
            out.append((float(m.group(1)), float(m.group(2)), cur))
    return out


def write_merged(holes: list[tuple[float, float, float]], dst: Path) -> None:
    """One KiCad-metric Excellon over ALL holes — the drill phase's schedule.

    boardmaps.excellon insists on `M48` / `METRIC` / `T<n>C<dia>` with DECIMAL
    coordinates, which is the KiCad dialect and not pcb-rnd's `METRIC,000.000`
    header. Tool numbers are re-issued in ascending diameter so the file is a
    pure function of the hole set (determinism, not taste).
    """
    dias = sorted({d for _, _, d in holes})
    lines = ["M48", "METRIC,TZ"]
    lines += [f"T{i}C{d:.3f}" for i, d in enumerate(dias, 1)]
    lines.append("%")
    for i, d in enumerate(dias, 1):
        lines.append(f"T{i}")
        lines += [f"X{x:.3f}Y{y:.3f}" for x, y, dd in holes if dd == d]
    lines += ["T0", "M30"]
    dst.write_text("\n".join(lines) + "\n")


def openings(path: Path) -> tuple[int, int, int]:
    """-> (apertures, flashed pads, region-filled pads) in a FILTERED gerber.

    Both spellings are counted because pcb-rnd uses both and the difference is
    not a detail: a round pad becomes an aperture flash (`D03`), a rotated
    rectangular land becomes a filled region (`G36`/`G37`). A tally that knows
    only about flashes silently reports two thirds of this board as absent —
    see check_openings.
    """
    txt = path.read_text()
    return (len(re.findall(r"^%ADD\d+", txt, re.M)),
            len(re.findall(r"D03\*", txt)),
            len(re.findall(r"^G36\*", txt, re.M)))


def check_openings(out: Path, bm, win, holes: list) -> bool:
    """SPEC's mask and paste rules, read back off the emitted artwork.

    Judged on the RASTER, not on the flash list, and the first draft of this
    function is why. Counting D03 flashes finds every circular pad — the 39
    hole-centred rings and the 6 ISP discs — and MISSES ALL 57 SMD LANDS,
    because pcb-rnd emits a rotated rectangular pad as a filled REGION and not
    as an aperture flash. That check reported "45 openings, 45/45 on copper"
    and "B_Paste 0 windows" on artwork that was in fact complete: a green
    verdict computed over two thirds of the board. The lane already owns an
    oracle with no lineage in common with the generator (gerbv, via
    boardmaps.rasterize) and it sees ink however that ink was spelled.

    Three facts, none of which a filename check can see:

      1. Every mask opening lies ON copper — THE SWELL RULE, in the only form
         a raster can state it: no mask pixel may miss the copper it exposes.
         (Copper is strictly larger, since it also carries tracks and pours.)
      2. Paste is a SUBSET of the bottom mask. A stencil window over paint is
         not a window.
      3. NO paste covers a drilled hole. SPEC states the rule as a prohibition
         — a pasted hole wicks solder and blocks the wire the operator has to
         thread — and this is the last place in the lane where it can be
         checked against the bytes that actually go to the fab.
    """
    ok = True
    ras = {}
    for lay in ("F_Cu", "B_Cu", "F_Mask", "B_Mask", "B_Paste"):
        p = out / f"{STEM}-{lay}.gbr"
        if p.is_file():
            ras[lay] = bm.rasterize(p, win).astype(bool)

    for face in ("F", "B"):
        m, c = ras.get(f"{face}_Mask"), ras.get(f"{face}_Cu")
        if m is None or c is None:
            continue
        off = int((m & ~c).sum())
        print(f"      {face}_Mask  {int(m.sum()):>9} px open, "
              f"{off} px of it NOT over copper")
        if off:
            ok = False
            print(f"      *** MASK OFF COPPER on {face}: {off} px — an "
                  f"opening wider than the pad it exposes")

    p, bmk = ras.get("B_Paste"), ras.get("B_Mask")
    if p is not None and bmk is not None:
        out_of = int((p & ~bmk).sum())
        print(f"      B_Paste {int(p.sum()):>9} px, "
              f"{out_of} px of it outside the mask")
        if out_of:
            ok = False
            print(f"      *** PASTE OUTSIDE THE MASK: {out_of} px")
        # Sample the paste raster at each hole centre. flip_line/extents put
        # the window in the EXPORT frame, which is the frame the drill file is
        # already in, so no un-flip belongs here.
        h, w = p.shape
        wet = []
        for hx, hy, hd in holes:
            j = int(round((hx - win.x0) / win.w_mm * (w - 1)))
            i = int(round((win.y1 - hy) / win.h_mm * (h - 1)))
            if 0 <= i < h and 0 <= j < w and p[i, j]:
                wet.append((hx, hy, hd))
        print(f"      B_Paste over a drilled hole: {len(wet)} of "
              f"{len(holes)} holes")
        if wet:
            ok = False
            print(f"      *** PASTE ON A DRILLED HOLE (it will wick): "
                  f"{wet[:4]}")
    if (out / f"{STEM}-F_Paste.gbr").is_file():
        ok = False
        print("      F_Paste EXISTS: orbit reflows on ONE face; a top stencil "
              "window opens onto a face no squeegee ever touches")
    return ok


def matrix_vias() -> list[tuple[str, float, float]]:
    """The via ledger MATRIX.md publishes, in the BOARD frame (y up)."""
    txt = (HERE / "MATRIX.md").read_text()
    return [(f"V{n}", float(x), float(y)) for n, x, y in
            re.findall(r"`V(\d+)\s*\(\s*([\d.]+),\s*([\d.]+)\)", txt)]


def main() -> int:
    if not PCBRND.is_file():
        raise SystemExit(f"pcb-rnd wrapper not found at {PCBRND}")
    if "--no-regen" not in sys.argv:
        print("[1/5] regenerating the sealed board (tools-route.py) ...")
        tail = sh([sys.executable, "-u", str(HERE / "tools-route.py")]).splitlines()
        print("\n".join("      " + l for l in tail[-4:]))
    board = HERE / f"{STEM}.lht"
    if not board.is_file():
        raise SystemExit(f"no {board}")

    OUT.mkdir(exist_ok=True)
    for f in OUT.iterdir():
        if f.is_file():
            f.unlink()
    with tempfile.TemporaryDirectory() as td:
        conf = Path(td) / "cam.conf"
        conf.write_text(CAM_JOB)
        raw = Path(td) / "raw"
        raw.mkdir()
        print("[2/5] pcb-rnd -x cam (user-level job, -C) ...")
        sh([str(PCBRND), "-C", str(conf), "-x", "cam", "gerber:clauderacam",
            "--outfile", STEM, str(board)], cwd=raw)
        made = sorted(p.name for p in raw.iterdir())
        print(f"      emitted {len(made)}: {', '.join(made)}")

        print("[3/5] gerber_filter (pcb-rnd dialect -> one op per line) ...")
        missing = []
        for lay in GERBERS:
            src = raw / f"{STEM}-{lay}.gbr"
            if not src.is_file():
                missing.append(lay)
                continue
            st = filter_file(src, OUT / f"{STEM}-{lay}.gbr")
            print(f"      {lay:<14} hoisted={st['selects_hoisted']:<5} "
                  f"split={st['ops_split']:<5} completed={st['coords_completed']}")

        print("[4/5] excellon: merge PTH+NPTH, keep the plated set ...")
        pth = read_drl(raw / f"{STEM}-PTH.drl")
        npth = read_drl(raw / f"{STEM}-NPTH.drl")
        write_merged(pth + npth, OUT / f"{STEM}.drl")
        (HERE / "stitch-drills.txt").write_text(
            (raw / f"{STEM}-PTH.drl").read_text())
        # Stamp WHICH board these gerbers came from, by content.  MATRIX's pour
        # census is measured off this artwork and must refuse to report on a
        # board it does not belong to — and mtime cannot answer that here: the
        # build is deterministic, so re-running it rewrites an identical
        # orbit.lht with a newer timestamp and would mark good artwork stale.
        import hashlib
        (OUT / ".source.sha256").write_text(
            hashlib.sha256(board.read_bytes()).hexdigest())
        print(f"      plated {len(pth)} + unplated {len(npth)} "
              f"= {len(pth) + len(npth)} holes -> {STEM}.drl")

    print("[5/5] asserts ...")
    ok = True

    # -- the bench stitch list IS the plated program, un-flipped to the board
    vias = matrix_vias()
    drl = {(round(x, 3), round(BOARD_H - y, 3))
           for x, y, d in pth if d == 1.0}
    mtx = {(round(x, 3), round(y, 3)) for _, x, y in vias}
    if drl == mtx and len(pth) == len(vias) + 1:
        print(f"      stitch set: {len(pth)} plated = {len(vias)} MATRIX vias "
              f"(coordinate-for-coordinate) + 1 promoted lead  OK")
    else:
        ok = False
        print(f"      STITCH MISMATCH: drl-not-MATRIX {sorted(drl - mtx)[:4]} "
              f"MATRIX-not-drl {sorted(mtx - drl)[:4]}")
    promoted = [(x, BOARD_H - y, d) for x, y, d in pth if d != 1.0]
    print(f"      promoted lead (board frame): {promoted}")

    # -- the raster stack must eat what we just wrote (gerbv ground truth)
    sys.path.insert(0, str(REPO / "src"))
    from clauderacam.pcb import boardmaps as bm                # noqa: E402
    holes = bm.excellon(OUT / f"{STEM}.drl")
    # cross_check=True is NEVER optional: without it extents silently reads a
    # pcb-rnd outline several mm small (R2-S5 measured 4mm on a tab outline).
    win = bm.extents(OUT / f"{STEM}-Edge_Cuts.gbr", cross_check=True)
    line = bm.flip_line(win, (0.0, 0.0))
    print(f"      excellon parses: {len(holes)} holes "
          f"{sorted({round(h[2], 2) for h in holes})}")
    print(f"      extents (cross_check=True): x {win.x0:g}..{win.x1:g} "
          f"y {win.y0:g}..{win.y1:g}  {win.w_mm:g} x {win.h_mm:g}")
    print(f"      flip line at anchor (0,0): x={line:g}")
    for want, got, what in ((BOARD_W, win.w_mm, "board width"),
                            (BOARD_H, win.h_mm, "board height"),
                            (BOARD_W / 2, line, "mirror line")):
        if abs(want - got) > 1e-6:
            ok = False
            print(f"      FRAME MISMATCH {what}: want {want} got {got}")
    for lay in GERBERS:
        p = OUT / f"{STEM}-{lay}.gbr"
        if p.is_file():
            n = int(bm.rasterize(p, win).sum())
            ap, fl, rg = openings(p)
            print(f"      rasterize {lay:<14} {n:>10} ink px   "
                  f"{ap:>3} apertures, {fl:>3} flashed + {rg:>3} region pads")

    # -- the mask/paste artwork exists, and says what SPEC says it should
    ok = check_openings(OUT, bm, win, holes) and ok

    if missing:
        ok = False
        print("\n      *** THE BOARD HAS LOST ITS MASK/PASTE ARTWORK ***")
        print(f"      -x cam emitted nothing for: {', '.join(missing)}")
        print("      These groups were added to orbit.lht's stackup on")
        print("      2026-08-02; emitting nothing for one means the stackup")
        print("      regressed, not that the lane has a known gap. A")
        print("      [twosided] job REFUSES to load without all three")
        print("      (pcbjob.load: 'a double-sided board is masked, lasered")
        print("      and scrubbed on BOTH faces'). The fix belongs in the")
        print("      board generator, as it did the first time. This script")
        print("      will not invent them — see the module docstring.")
    print("\n" + ("fab artwork OK" if ok else "fab artwork INCOMPLETE"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
