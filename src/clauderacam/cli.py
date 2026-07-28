"""Thin CLI over the same library the MCP server uses."""
from __future__ import annotations

import argparse
import sys
import time

from . import emit, engine, job as jobmod, preview as previewmod, \
    stages as stagesmod, verify as verifymod
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


if __name__ == "__main__":
    sys.exit(main())
