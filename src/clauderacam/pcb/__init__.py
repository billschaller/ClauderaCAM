"""The PCB lane (PCB-PLAN.md): gerbers in, verified Carvera programs out.

The lane is four modules, in dependency order:

boardmaps.py — the verification ground truth: gerbv-rasterized layer masks
plus the in-repo Excellon parser. Deliberately independent of the FlatCAM
geometry engine: the generator and the verifier read the same source files
through implementations that share no lineage, exactly like the STL lane's
mesh-vs-gcode split.

pcbjob.py — the `[pcb]` grammar. One TOML parameterizes the six-phase chain
and its phase order is law; tools go through the same Article XI crib gate as
every job.

engine.py / reemit.py — FlatCAM as a headless GEOMETRY engine (pinned commit,
templated Tcl, sentinel-poll-kill) whose per-phase .nc is interchange only,
re-emitted through emit.py (Article V) under the param-match law.

checks.py — the gate. `verify_pcb(job, programs)` returns one Report per
assembled program, composed the way twosided.verify composes its two sides:
the same Check/Report types the mill gate uses, so a PCB report prints and
serializes like every other report. It judges the BYTES of each program
against the board maps and never touches the engine's state (Article I).

  from clauderacam.pcb import checks, pcbjob
  job = pcbjob.load("boards/coupon/coupon.toml")
  reports = checks.verify_pcb(job, {"mill": ..., "silk": ..., "scrub": ...,
                                    "holes": ...})
  print(checks.report_text(reports))

Still to wire (WS6): the CLI/MCP `[pcb]` subcommand and the viewer session —
a pcb job has no stock simulation yet, so it cannot ride the Job path that
`clauderacam verify` walks.
"""
from .checks import (PROGRAM_PHASES, board_maps, report_text,  # noqa: F401
                     verify_pcb, verify_program)
