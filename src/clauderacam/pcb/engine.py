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

from . import boardmaps
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


def render_tcl(job: PcbJob, win: boardmaps.BoardWindow,
               work_dir: Path) -> str:
    """The one Tcl this lane ever runs: templated, transform DERIVED."""
    dx, dy = boardmaps.machine_offset(win, job.anchor)
    ph = job.phases
    iso_t, clear_t = job.phase_tool("iso"), job.phase_tool("clear")
    scrub_t = job.phase_tool("scrub")
    drill_t, cut_t = job.phase_tool("drills"), job.phase_tool("cutout")
    L: list[str] = [
        f"# clauderacam pcb engine — templated from {job.path.name}; DO "
        f"NOT hand-edit",
        f"# board window {win.x0:.3f},{win.y0:.3f} .. "
        f"{win.x1:.3f},{win.y1:.3f}; derived offset {dx:.3f},{dy:.3f}",
        f"set OUT {work_dir}",
    ]
    for name in ("cu", "mask", "edge"):
        L.append(f"open_gerber {job.files[name]} -outname {name}")
    L.append(f"open_excellon {job.files['drl']} -outname drl")
    for name in ("cu", "mask", "edge", "drl"):
        L.append(f"mirror {name} -axis X -origin 0,0")
    for name in ("cu", "mask", "edge", "drl"):
        L.append(f"offset {name} -x {dx:.6g} -y {dy:.6g}")

    def cnc(geo, tool, z, feed, plunge, out, dpp=None):
        extra = f" -dpp {dpp:.6g}" if dpp is not None else ""
        return (f"cncjob {geo} -dia {tool.diameter:.6g} -z_cut {z:.6g}"
                f"{extra} -z_move 2.0 -feedrate {feed:.6g} "
                f"-feedrate_z {plunge:.6g} -spindlespeed {tool.rpm} "
                f"-pp default -outname {out}")

    p = ph["iso"]
    L += [f"isolate cu -dia {iso_t.tip_diameter:.6g} -passes 1 -overlap 0 "
          f"-combine 1 -outname iso_geo",
          cnc("iso_geo", iso_t, p["depth"], p["feed"], p["plunge"],
              "iso_cnc"),
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
    p = ph["drills"]
    L += [f"milldrills drl -milled_dias all "
          f"-tooldia {drill_t.diameter:.6g} -diatol 5 -outname drl_geo",
          cnc("drl_geo", drill_t, p["depth"], p["feed"], p["plunge"],
              "drl_cnc", dpp=p["dpp"]),
          f"write_gcode drl_cnc $OUT/{PHASE_NC['drills']}"]
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
    """Template, run, sentinel-poll, kill. -> {phase: nc_path}."""
    pf = preflight(job)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    win = boardmaps.extents(job.files["edge"])
    tcl = work_dir / "engine.tcl"
    tcl.write_text(render_tcl(job, win, work_dir))
    log = work_dir / "engine.log"
    expected = {ph: work_dir / nc for ph, nc in PHASE_NC.items()}
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
