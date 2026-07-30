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

session.py — the viewer side (WS6). It defines the THIN-SHEET STOCK MODEL
(thickness from the blank, XY window derived from the board window) that
checks.py's docstring lists as missing, which is what lets the MILL and HOLES
programs ride simulate.carve() for real rapid/contact/depth/physics readings;
it renders the non-carving programs (laser silk, spring-tool scrub) as a 2D
overlay of what their bytes DRAW instead of a heightmap that would be a flat
sheet (Article VI); and it builds the run-sheet card. `clauderacam verify`
and `view` walk a [pcb] document through it — four sessions, one per program.

  from clauderacam.pcb import pcbjob, session
  job = pcbjob.load("boards/coupon/coupon.toml")
  for s in session.build(job):          # one per program of the split
      print(s.name, s.meta["ok"], len(s.stocks))
"""
from .checks import (PROGRAM_PHASES, board_maps, report_text,  # noqa: F401
                     verify_pcb, verify_program)
