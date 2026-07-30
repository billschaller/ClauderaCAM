"""Thin CLI over the same library the MCP server uses."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import emit, engine, job as jobmod, preview as previewmod, \
    stages as stagesmod, twosided as twomod, verify as verifymod
from .pcb import pcbjob as pcbjobmod, session as pcbsess
from .viewer import client as viewer


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="clauderacam")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("generate", "verify", "preview", "all"):
        p = sub.add_parser(name)
        p.add_argument("job")
    pv = sub.add_parser("view")
    pv.add_argument("job")
    pv.add_argument("--port", type=int, default=8323)
    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int, default=8323)
    sv.add_argument("--jobs-dir", default="jobs")
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        from .viewer import server as viewer_server
        url = viewer_server.start(args.port, jobs_dir=args.jobs_dir)
        print(f"viewer server at {url} — open job files from the browser "
              f"or push with `clauderacam view <job>` / the MCP tools; "
              f"ctrl-c to stop")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        return 0

    if pcbsess.is_pcb(args.job):
        return run_pcb(args)
    if twomod.is_twosided(args.job):
        return run_twosided(args)

    j = jobmod.load(args.job)
    if args.cmd in ("generate", "all"):
        ops = engine.generate_ops(j)
        out = emit.write(j, ops)
        viewer.invalidate(j)  # any watching session now shows STALE
        for r in ops:
            print(f"{r.label}: T{r.tool} {len(r.lines)} moves, "
                  f"{r.path_len_mm/1000:.1f}m, ~{r.est_min:.0f} min")
        print(f"wrote {out}")
    if args.cmd in ("verify", "all"):
        report = verifymod.verify(j)
        print(report.text())
        if report.carve is not None:
            for line in stagesmod.stage_lines(
                    stagesmod.stage_stats(j, report.carve)):
                print(line)
        pushed = viewer.push_job(j, report)
        if pushed:
            print(f"viewer session updated: {pushed[0]}#{pushed[1]}")
        if not report.ok:
            return 1
    if args.cmd in ("preview", "all"):
        print("preview:", previewmod.render(j))
    if args.cmd == "view":
        url, started = viewer.ensure_server(args.port,
                                            jobs_dir=j.path.parent)
        report = verifymod.verify(j)
        pushed = viewer.push_job(j, report)
        if pushed is None:
            print(report.text())
            return 1
        print(f"viewer session: {pushed[0]}#{pushed[1]}"
              + ("" if started else "  (joined the running server)"))
        if started:
            print("serving — ctrl-c to stop")
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                pass
    return 0


def run_pcb(args) -> int:
    """verify/view for a [pcb] document: every program of the split through
    the board-map gate AND the sheet stock sim, one viewer session each,
    plus the run sheet. `generate` is deliberately absent: the phase
    geometry comes from the FlatCAM engine (pcb/engine.py), which is an
    optional external binary and not something a CLI verb should launch
    behind the operator's back."""
    if args.cmd in ("generate", "all", "preview"):
        print(f"`{args.cmd}` is not wired for [pcb] jobs: phase geometry "
              f"comes from the FlatCAM engine (clauderacam.pcb.engine) and "
              f"the programs are re-emitted from it. Run the engine, then "
              f"`verify` or `view`.")
        return 2
    pj = pcbjobmod.load(args.job)
    if args.cmd == "view":
        viewer.ensure_server(args.port, jobs_dir=Path(args.job).parent)
    sessions = pcbsess.build(pj)
    print(pcbsess.report_text(sessions))
    for st in sessions[0].meta["run_sheet"]:
        est = f"  ~{stagesmod.fmt_time(st['est_s'])}" if st.get("est_s") \
            else ""
        print(f"  {st['n']:2d} [{st['kind']}] {st['title']}{est}")
    for name, url, sid in viewer.push_pcb(sessions):
        print(f"viewer session {name}: {url}#{sid}")
    rc = 0 if all(s.meta["ok"] is True for s in sessions) else 1
    if args.cmd == "view" and viewer.server.running():
        print("serving — ctrl-c to stop")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    return rc


def run_twosided(args) -> int:
    """generate/verify/preview/all/view for a pin-and-flip job: both sides,
    every side through the same gate, back verified against the carried
    bottom field."""
    ts = twomod.load(args.job)
    if args.cmd in ("generate", "all"):
        for side, ops in twomod.generate(ts).items():
            j = ts.front if side == "front" else ts.back
            viewer.invalidate(j)
            for r in ops:
                print(f"[{side}] {r.label}: T{r.tool} {len(r.lines)} moves, "
                      f"{r.path_len_mm/1000:.1f}m, ~{r.est_min:.0f} min")
            print(f"[{side}] wrote {j.out}")
    rc = 0
    reports = None
    if args.cmd in ("verify", "all", "view"):
        reports = twomod.verify(ts)
        for side in ("front", "back"):
            rep = reports[side]
            j = ts.front if side == "front" else ts.back
            print(f"=== {side} ({j.out.name}) ===")
            print(rep.text())
            if rep.carve is not None:
                for line in stagesmod.stage_lines(
                        stagesmod.stage_stats(j, rep.carve)):
                    print(line)
            if not rep.ok:
                rc = 1
    if args.cmd in ("preview", "all"):
        for j in (ts.front, ts.back):
            print("preview:", previewmod.render(j))
    if args.cmd == "view":
        url, started = viewer.ensure_server(args.port,
                                            jobs_dir=Path(args.job).parent)
        for side in ("front", "back"):
            j = ts.front if side == "front" else ts.back
            pushed = viewer.push_job(j, reports[side])
            if pushed:
                print(f"[{side}] session: {pushed[0]}#{pushed[1]}")
        if started:
            print("serving — ctrl-c to stop")
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                pass
    if args.cmd in ("verify", "all") and reports:
        # sessions update live if a viewer is running anywhere
        for side in ("front", "back"):
            j = ts.front if side == "front" else ts.back
            viewer.push_job(j, reports[side])
    return rc


if __name__ == "__main__":
    sys.exit(main())
