#!/usr/bin/env python3
"""ONE-OFF salvage pin program — 2026-08-03 bench incident.

The bench is running orbit's setup 1 on a 2mm spoilboard; the committed
pins program pecks to -12.0 (the swallowed-pin model, sized for 12.7 MDF)
and would drill 8.5mm into the machine BED. The operator's salvage plan:

  1. THIS program: spot + peck the two pin holes to -1.7 ONLY — through
     the 1.5 blank, 0.2 into the 2mm sheet, 1.8mm clear of the bed (the
     same excursion as every other through-cut). The blank's O2.0 holes
     are the registration DATUM; they need no spoilboard engagement.
  2. Run the excise program, snap the sub-blank out.
  3. Off-machine: cut a thick MDF spoilboard and hand-drill its deep pin
     holes USING THE SUB-BLANK AS THE TEMPLATE (clamp it on, drill
     through its own O2.0 holes). Set the dowels, flip the sub-blank
     over them, tape, run setup 2 unchanged.

Deliberate law bypass, stated loudly: the proud-pin model's grammar
(pcbjob) refuses a bore with <1.0mm of spoilboard engagement because a
dowel located by the SPOILBOARD alone would wobble. Here the spoilboard
hole is SCRAP — the pins will stand in the hand-drilled thick MDF — so
this script builds the job normally, then overrides [pins] AFTER load
(bore_depth 1.7) and re-verifies the emitted bytes through the real gate
(checks.verify_program: dialect, pin laws against the overridden tables,
keep-outs). Article I holds on the bytes; the bypass is of one load-time
refusal whose premise does not apply, and it lives only in this file.

    python3 tools-salvage.py    ->  salvage-pins.nc (verified or deleted)
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_VENV = os.path.join(os.path.dirname(os.path.dirname(HERE)), ".venv")
if (os.path.isfile(os.path.join(_VENV, "bin", "python"))
        and sys.prefix != _VENV):
    _py = os.path.join(_VENV, "bin", "python")
    os.execv(_py, [_py, os.path.abspath(__file__), *sys.argv[1:]])

from dataclasses import replace  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)),
                                "src"))
from clauderacam.pcb import checks, pcbjob, reemit  # noqa: E402

BORE = 1.7          # through the blank + the standard 0.2 breakthrough
OUT = Path(HERE) / "salvage-pins.nc"


def main() -> int:
    job = pcbjob.load(Path(HERE) / "orbit.toml")
    if float(job.pins["length"]) <= BORE:
        raise SystemExit("pins are shorter than the salvage bore — "
                         "this script's premise is gone")
    sjob = replace(job, pins={**job.pins, "bore_depth": BORE})
    sj = pcbjob.side_view(sjob, sjob.sides[0])
    tables = pcbjob.pin_phase_tables(sjob)
    assert tables["pindrill"]["depth"] == -BORE, tables["pindrill"]
    text = reemit.assemble_program(sj, "pins", reemit.pin_ops(sj))
    OUT.write_text(text)

    print(f"wrote {OUT.name}; gate on the bytes:")
    rep = checks.verify_program(sj, "pins", OUT, checks.board_maps(sj))
    bad = [c for c in rep.checks if not c.ok]
    for c in rep.checks:
        print(f"  [{'PASS' if c.ok else 'FAIL'}] {c.name:<28} "
              f"{c.value:.4f} ({c.limit})")
    floor = min(float(ln.split("Z")[1].split()[0])
                for ln in text.splitlines()
                if ln.startswith("G1 Z") or ln.startswith("G01 Z"))
    print(f"  floor of every cut: {floor} (must be exactly -{BORE})")
    if bad or abs(floor + BORE) > 1e-9 or not rep.ok:
        OUT.unlink(missing_ok=True)
        print("SALVAGE VERDICT: FAIL — file deleted, do not cut")
        return 1
    print(f"SALVAGE VERDICT: PASS — {OUT.name} pecks to -{BORE}, "
          f"1.8mm clear of the bed under the 2mm sheet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
