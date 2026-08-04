#!/usr/bin/env python3
"""Board B "orbit" — gerbers-rnd/ -> out/, then the double-sided gate.

    python3 -u tools-cam.py            # generate + verify
    python3 -u tools-cam.py --verify   # verify the out/ that is already there

This is the [pcb]+[twosided] composition driven exactly the way
tests/pcb_twosided_suite.py drives it (load -> generate -> verify), on real
artwork instead of the suite's synthetic stubs:

    pcbjob.load            the grammar
    engine.run_sides       FlatCAM as a headless GEOMETRY engine, TWICE — one
                           run per setup, each in its own directory, so one
                           side's interchange can never be read as the other's
    reemit.*               every phase re-emitted through emit.py under the
                           param-match law (Article V: the engine's .nc is
                           interchange, never a post-processor's output)
    flip.verify_twosided   the gate: one artwork report + one report per
                           program per side

Nine programs come out — setup 1 (back) {mill, silk, scrub, holes=bores,
pins} and setup 2 (front) {mill, silk, scrub, holes=drills+cutout} — and
NONE of them is cuttable until this script prints PASS for it (Article I:
the verdict is on the bytes, not the intent).

The 2026-08-03 ordering law shapes the split: no pad hole exists before
both faces' scrubs, so EVERY scrub is Board A's full disc laps (the
annular-lap era and its rim mask-collar are retired — see reemit.scrub_op),
setup 1 ends with the 8 non-pad bores + the pin block, and setup 2 drills
all pad holes after its scrub, then cuts out. The front scrub covers only
the SOLDER PLAN (stitch vias + the promoted lead); the dead rings are
declared inert and keep their flood coat.

The board ships ZERO bench jumpers (MATRIX.md "JUMPER RETIREMENT" — the
2026-08-02 growth to 66x56 closed every net in copper; the declaration law
survives in tools-route.py with an empty table, so a future jumper is a
declared artifact, never an accident).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

_VENV = REPO / ".venv"
if (_VENV / "bin" / "python").is_file() and Path(sys.prefix) != _VENV:
    _py = str(_VENV / "bin" / "python")
    os.execv(_py, [_py, str(Path(__file__).resolve()), *sys.argv[1:]])

sys.path.insert(0, str(REPO / "src"))
from clauderacam.pcb import (boardmaps as bm, engine, flip,  # noqa: E402
                             pcbjob, reemit)

JOB = HERE / "orbit.toml"


def generate(job, ctx, work: Path) -> dict[str, dict[str, Path]]:
    """Both setups through the engine, re-emitted, written to out/."""
    job.out_dir.mkdir(parents=True, exist_ok=True)
    nc = engine.run_sides(job, work)
    progs: dict[str, dict[str, Path]] = {}
    for side in job.sides:
        sj = pcbjob.side_view(job, side)
        progs[side] = {}
        # side 2's scrub is annular; side 1's scrub_op IS read_phase, byte for
        # byte, so the single-sided/coupon path cannot move under it
        ops = {ph: (reemit.scrub_op(p, sj, win=ctx.tight) if ph == "scrub"
                    else reemit.read_phase(p, sj, ph))
               for ph, p in nc[side].items()}
        print(f"  {side}: " + ", ".join(f"{k} {len(v.lines)} lines"
                                        for k, v in sorted(ops.items())))
        for name, want in pcbjob.programs_of(sj).items():
            if name == "silk":
                mt = bm.rasterize(sj.files["mask"], ctx.tight)
                text = reemit.silk_program(sj, ctx.tight, mt)[0]
            elif name == "pins":
                text = reemit.assemble_program(sj, name, reemit.pin_ops(sj))
            elif name == "excise":
                text = reemit.assemble_program(
                    sj, name, [reemit.excise_ops(sj, win=ctx.tight)])
            else:
                text = reemit.assemble_program(
                    sj, name, [ops[ph] for ph in want])
            p = job.out_dir / f"{pcbjob.program_stem(sj, name)}.nc"
            p.write_text(text)
            progs[side][name] = p
    return progs


def collect(job) -> dict[str, dict[str, Path]]:
    """The out/ that is already on disk, reported as missing rather than
    invented when a program is absent."""
    progs: dict[str, dict[str, Path]] = {}
    for side in job.sides:
        sj = pcbjob.side_view(job, side)
        progs[side] = {}
        for name in pcbjob.programs_of(sj):
            p = job.out_dir / f"{pcbjob.program_stem(sj, name)}.nc"
            if p.is_file():
                progs[side][name] = p
    return progs


def main() -> int:
    job = pcbjob.load(JOB)
    print(f"grammar: {job.name} twosided={job.twosided} sides={job.sides} "
          f"anchor={job.anchor}")
    ctx = flip.context(job)
    print(f"context: mirror line x={ctx.line:.3f}  holes={len(ctx.holes)}  "
          f"board {ctx.tight.w_mm:g} x {ctx.tight.h_mm:g}")

    if "--verify" in sys.argv:
        progs = collect(job)
    else:
        if job.out_dir.exists():
            shutil.rmtree(job.out_dir)
        with tempfile.TemporaryDirectory(prefix="orbit-cam-") as td:
            print("generate (FlatCAM, one run per setup):")
            progs = generate(job, ctx, Path(td))

    n = sum(len(v) for v in progs.values())
    print(f"\nprograms: {n}")
    for side in job.sides:
        for name, p in sorted(progs[side].items()):
            print(f"  {side}/{name:<6} {p.name:<28} "
                  f"{len(p.read_text().splitlines()):>6} lines")

    print("\nverify (flip.verify_twosided — the bytes, not the intent):")
    reps = flip.verify_twosided(job, progs, ctx=ctx)
    worst = 0
    for name, rep in reps.items():
        bad = [c for c in rep.checks if not c.ok]
        print(f"  [{'PASS' if rep.ok else 'FAIL'}] {name:<14} "
              f"{len(rep.checks):>3} checks" + (f"  -> {len(bad)} FAILING"
                                                if bad else ""))
        for c in bad:
            worst += 1
            print(f"          {c.name}: {c.value:.4f} bar {c.limit}")
            print(f"          {c.detail}")
    ok = all(r.ok for r in reps.values())
    print(f"\ntotal checks: {sum(len(r.checks) for r in reps.values())}, "
          f"failing: {worst}")
    print("PCB VERDICT (double-sided): "
          + ("PASS — both setups cleared" if ok
             else "FAIL — do NOT cut this board"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
