"""FlatCAM as a headless GEOMETRY ENGINE (PCB-PLAN.md WS3): Tcl is
templated from the [pcb] TOML — never hand-written — and the per-phase
.nc output is geometry interchange, parsed and re-emitted through
emit.py (Article V). FlatCAM is an external binary of an optional lane,
like the Rust kernel: configured path, pinned commit, refused when it
drifts.

Engine discipline, each rule traced:
  - PINNED COMMIT: billschaller/flatcam @ 16e635a carries the three
    headless-Tcl fixes (WS0). A different checkout is a different
    generator and its output is unblessed — refuse, don't warn.
  - CIRCLE STEPS: the two keys that differ from factory defaults
    (geometry_circle_steps / gerber_circle_steps = 64) are asserted
    AND written back before every run — the setup heals itself instead
    of living in one dotfile (DESIGN.md WS0 entry). Arc fidelity
    matters because the emitter refuses real arcs (Article V): FlatCAM
    must segment finely enough to hold tolerance.
  - SENTINEL-POLL-KILL: the fork's restricted Tcl has no exit/quit.
    The script's LAST action writes the sentinel token into a file
    beside the phase outputs; the runner polls for that file, then
    kills the process group. A missing sentinel inside the timeout is
    a FAILED run even if every output exists.

    The sentinel is a FILE and not a line of stdout because stdout does
    not work (2026-07-30, the unreachable-sentinel incident — the first
    live run on Board A). FlatCAM's shell evaluates the shellfile in a
    `tkinter.Tcl()` interpreter whose `stdout` channel is not the
    process's fd 1: `puts "ALL-PHASES-DONE"` followed by an explicit
    `flush stdout` produces NOTHING in a redirected log, while in the
    same script a Tcl `open`/`puts $fh`/`close` writes its file and the
    following `open_gerber` logs its own execution — so the script ran
    and the token was simply discarded. The operator's field run set
    shows the same hole: its run.log contains zero occurrences of the
    sentinel its Tcl `puts`-es, and that runner polled for OUTPUT FILES
    instead. A sentinel nobody can observe is not a gate, it is a
    600-second timeout on every successful run.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

from . import boardmaps, pcbjob, reemit
from .pcbjob import PcbJob

PINNED_COMMIT = "16e635abd411d49f69012c0d63317c53b0e39724"
FLATCAM_DIR_DEFAULT = Path.home() / "scratch" / "carvera" / "flatcam"
CIRCLE_KEYS = {"geometry_circle_steps": 64, "gerber_circle_steps": 64}
SENTINEL = "ALL-PHASES-DONE"
SENTINEL_FILE = "ALL-PHASES-DONE.txt"   # written by the Tcl, polled by run()

# phase -> engine output file stem; the numbers are the CHAIN positions
# (mask=3 and silk=4 are not FlatCAM's — mask is the operator, silk is
# emit.assemble_laser from the silkscreen gerber)
PHASE_NC = {"iso": "fc-1-iso.nc", "clear": "fc-2-clear.nc",
            "scrub": "fc-5-scrub.nc", "drills": "fc-6a-drills.nc",
            "cutout": "fc-6b-cutout.nc"}


def engine_phases(job: PcbJob) -> tuple[str, ...]:
    """The phases FlatCAM generates for THIS job (or side view): the chain's
    machine phases minus mask (the operator) and silk (the laser, straight
    from the gerber). A single-sided job has all five; side A of a flipped
    board has no cutout and side B no drills, because the grammar says so —
    the engine reads the phase table rather than being told twice."""
    return tuple(ph for ph in PHASE_NC if job.has_phase(ph))


def render_tcl(job: PcbJob, win: boardmaps.BoardWindow,
               work_dir: Path, mask_path: Path | None = None) -> str:
    """The one Tcl this lane ever runs: templated, transform DERIVED.

    A double-sided document runs this TWICE — once per side view, each with
    its own work dir. The two differ in exactly three derived ways: which
    copper/mask artwork is opened, whether the `mirror` line is emitted at
    all (side A is machined front-up and needs none), and the offset the
    transform derives from that mirror. The drills are bored in SIDE A's
    setup only, so the Excellon is opened only where a `drills` phase exists.

    `mask_path` overrides the mask artwork `paint` reads (the ONLY consumer
    of the mask object in this script): on side 2 run() hands in
    reemit.scrub_mask()'s filtered copy, in which every hole-centred flash
    is a D02 move, so paint never drives the spring tip across a bore (the
    2026-07-30 paint-across-bores finding; the hole-centred pads get
    in-repo annular laps at assembly instead).
    """
    dx, dy = boardmaps.machine_offset(win, job.anchor, job.mirror)
    ph = job.phases
    gen = engine_phases(job)
    iso_t, clear_t = job.phase_tool("iso"), job.phase_tool("clear")
    scrub_t = job.phase_tool("scrub")
    side = f" side {job.side}" if job.side else ""
    L: list[str] = [
        f"# clauderacam pcb engine — templated from {job.path.name}{side}; "
        f"DO NOT hand-edit",
        f"# board window {win.x0:.3f},{win.y0:.3f} .. "
        f"{win.x1:.3f},{win.y1:.3f}; derived offset {dx:.3f},{dy:.3f}"
        + (f"; mirror {job.mirror}" if job.side else ""),
        f"set OUT {work_dir}",
    ]
    sources = {"cu": job.files["cu"],
               "mask": mask_path or job.files["mask"],
               "edge": job.files["edge"]}
    objs = ["cu", "mask", "edge"]
    for name in objs:
        L.append(f"open_gerber {sources[name]} -outname {name}")
    if "drills" in gen:
        L.append(f"open_excellon {job.files['drl']} -outname drl")
        objs.append("drl")
    if job.mirror == "x":
        # `mirror -axis X -origin 0,0` NEGATES X — the WS2 law, falsified and
        # fixed; boardmaps.machine_offset's offset is the other half of it and
        # boardmaps.flip_line asserts the pair closes.
        for name in objs:
            L.append(f"mirror {name} -axis X -origin 0,0")
    for name in objs:
        L.append(f"offset {name} -x {dx:.6g} -y {dy:.6g}")

    def cnc(geo, tool, z, feed, plunge, out, dpp=None, dia=None):
        """`dia` overrides the tool's shank diameter for the CUTTING width the
        phase actually uses — a vee's tip, not its cone (2026-07-30, the first
        live run: the iso phase's cncjob got the vee's full 3.175 while
        `isolate` correctly got the 0.2 tip, so fc-1-iso.nc's header printed
        "TOOL DIAMETER: 3.175". It reaches no geometry and re-emission drops
        the header, but a lying header is still a lie in a file an operator
        can open — the assembled mill program is byte-identical either way,
        which is the proof this fix is geometry-free)."""
        extra = f" -dpp {dpp:.6g}" if dpp is not None else ""
        d = tool.diameter if dia is None else dia
        return (f"cncjob {geo} -dia {d:.6g} -z_cut {z:.6g}"
                f"{extra} -z_move 2.0 -feedrate {feed:.6g} "
                f"-feedrate_z {plunge:.6g} -spindlespeed {tool.rpm} "
                f"-pp default -outname {out}")

    p = ph["iso"]
    L += [f"isolate cu -dia {iso_t.tip_diameter:.6g} -passes 1 -overlap 0 "
          f"-combine 1 -outname iso_geo",
          cnc("iso_geo", iso_t, p["depth"], p["feed"], p["plunge"],
              "iso_cnc", dia=iso_t.tip_diameter),
          f"write_gcode iso_cnc $OUT/{PHASE_NC['iso']}"]
    p = ph["clear"]
    L += [f"ncc cu -tooldia {clear_t.diameter:.6g} "
          f"-overlap {p['overlap']:.6g} -margin {p['margin']:.6g} "
          f"-offset {p['offset']:.6g} -method standard -connect 1 "
          f"-contour 1 -all -outname clear_geo",
          cnc("clear_geo", clear_t, p["depth"], p["feed"], p["plunge"],
              "clear_cnc"),
          f"write_gcode clear_cnc $OUT/{PHASE_NC['clear']}"]
    p = ph["scrub"]
    L += [f"paint mask -tooldia {scrub_t.diameter:.6g} "
          f"-overlap {p['overlap']:.6g} -offset {p['offset']:.6g} "
          f"-method lines -connect 1 -contour 1 -all -outname scrub_geo",
          cnc("scrub_geo", scrub_t, p["depth"], p["feed"], p["plunge"],
              "scrub_cnc"),
          f"write_gcode scrub_cnc $OUT/{PHASE_NC['scrub']}"]
    if "drills" in gen:
        drill_t = job.phase_tool("drills")
        p = ph["drills"]
        L += [f"milldrills drl -milled_dias all "
              f"-tooldia {drill_t.diameter:.6g} -diatol 5 -outname drl_geo",
              cnc("drl_geo", drill_t, p["depth"], p["feed"], p["plunge"],
                  "drl_cnc", dpp=p["dpp"]),
              f"write_gcode drl_cnc $OUT/{PHASE_NC['drills']}"]
    if "cutout" in gen:
        cut_t = job.phase_tool("cutout")
        p = ph["cutout"]
        L += [f"geocutout edge -dia {cut_t.diameter:.6g} -margin 0 "
              f"-gapsize {p['gapsize']:.6g} -gaps {p['gaps']} "
              f"-outname cut_geo",
              cnc("cut_geo", cut_t, p["depth"], p["feed"], p["plunge"],
                  "cut_cnc", dpp=p["dpp"]),
              f"write_gcode cut_cnc $OUT/{PHASE_NC['cutout']}"]
    # the sentinel, written LAST and only if every write_gcode above
    # returned — see the module docstring for why it is a file
    L += [f"set fh [open $OUT/{SENTINEL_FILE} w]",
          f'puts $fh "{SENTINEL}"',
          "close $fh"]
    return "\n".join(L) + "\n"


def _flatcam_cfg(job: PcbJob) -> dict:
    cfg = {"dir": str(FLATCAM_DIR_DEFAULT), "pin": PINNED_COMMIT,
           "timeout_s": 600}
    cfg.update(job.machine.get("flatcam", {}))
    return cfg


def preflight(job: PcbJob) -> dict:
    """Refuse a drifted engine BEFORE it generates anything."""
    cfg = _flatcam_cfg(job)
    fdir = Path(cfg["dir"]).expanduser()
    py = fdir / ".venv" / "bin" / "python"
    if not (fdir / "flatcam.py").is_file() or not py.is_file():
        raise RuntimeError(
            f"no FlatCAM checkout at {fdir} (flatcam.py + .venv/bin/"
            f"python) — clone billschaller/flatcam@{cfg['pin'][:7]}")
    head = subprocess.run(["git", "-C", str(fdir), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    if head != cfg["pin"]:
        raise RuntimeError(
            f"FlatCAM checkout is {head[:12]}, not the pinned "
            f"{cfg['pin'][:12]} — a different engine's output is "
            f"unblessed (DESIGN.md WS0); checkout the pin or change the "
            f"pin with a re-blessing")
    conf = Path.home() / ".FlatCAM" / "current_defaults_Unstable.FlatConfig"
    if not conf.is_file():
        raise RuntimeError(
            f"{conf} missing — run FlatCAM once so it writes its defaults, "
            f"then the runner maintains the two circle-steps keys itself")
    d = json.loads(conf.read_text())
    if any(d.get(k) != v for k, v in CIRCLE_KEYS.items()):
        d.update(CIRCLE_KEYS)
        conf.write_text(json.dumps(d, indent=2))
    return {"cfg": cfg, "python": str(py), "dir": str(fdir)}


def run(job: PcbJob, work_dir: Path) -> dict[str, Path]:
    """Template, run, sentinel-poll, kill. -> {phase: nc_path}.

    `work_dir` is resolved to an ABSOLUTE path here, not left to the caller:
    the subprocess runs with cwd = the FlatCAM checkout (that is how its
    `flatcam.py` finds its own package), so a relative `set OUT` in the
    templated Tcl would write every phase file beside flatcam.py and the
    sentinel poll would then time out looking in the caller's directory. The
    first live-run driver worked around this by resolving before the call;
    the resolution belongs to the function that knows about the cwd switch.
    """
    pf = preflight(job)
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    win = boardmaps.extents(job.files["edge"])
    # side 2 paints a FILTERED mask (hole-centred flashes -> D02 moves);
    # everywhere else this is the export itself, byte-for-byte the same Tcl
    # as before the split existed
    mask_path = reemit.scrub_mask(job, work_dir)
    tcl = work_dir / "engine.tcl"
    tcl.write_text(render_tcl(job, win, work_dir, mask_path=mask_path))
    log = work_dir / "engine.log"
    expected = {ph: work_dir / PHASE_NC[ph] for ph in engine_phases(job)}
    done = work_dir / SENTINEL_FILE
    for stale in (done, *expected.values()):
        # a previous run's artifact must never be mistaken for this one's
        stale.unlink(missing_ok=True)
    with open(log, "w") as lf:
        proc = subprocess.Popen(
            [pf["python"], "flatcam.py", "--headless=1",
             f"--shellfile={tcl}"],
            cwd=pf["dir"], stdout=lf, stderr=subprocess.STDOUT,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
            start_new_session=True)
        try:
            deadline = time.time() + pf["cfg"]["timeout_s"]
            while time.time() < deadline:
                if done.is_file() and SENTINEL in done.read_text(
                        errors="replace"):
                    break
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"FlatCAM exited before the sentinel — see {log}")
                time.sleep(1.0)
            else:
                raise RuntimeError(
                    f"FlatCAM did not reach {SENTINEL} within "
                    f"{pf['cfg']['timeout_s']}s — see {log}")
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    missing = [str(p) for p in expected.values() if not p.is_file()]
    if missing:
        raise RuntimeError(
            f"sentinel reached but outputs missing: {missing} — see {log}")
    return expected


def run_sides(job: PcbJob, work_dir: Path) -> dict[str, dict[str, Path]]:
    """A double-sided document is TWO engine runs, one per setup, each in its
    own subdirectory — same phase file names, different frames, no chance of
    one side's interchange being read as the other's. -> {side: {phase: nc}}.
    """
    if not job.twosided:
        raise ValueError(f"{job.name} is a single-sided document — run() it")
    work_dir = Path(work_dir).resolve()
    return {side: run(pcbjob.side_view(job, side), work_dir / side)
            for side in job.sides}
