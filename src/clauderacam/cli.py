"""Thin CLI over the same library the MCP server uses."""
from __future__ import annotations

import argparse
import sys
import time

from . import emit, engine, job as jobmod, preview as previewmod, verify as verifymod
from .viewer import server as viewer


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="clauderacam")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("generate", "verify", "preview", "all"):
        p = sub.add_parser(name)
        p.add_argument("job")
    pv = sub.add_parser("view")
    pv.add_argument("job")
    pv.add_argument("--port", type=int, default=8323)
    args = ap.parse_args(argv)

    j = jobmod.load(args.job)
    if args.cmd in ("generate", "all"):
        ops = engine.generate_ops(j)
        out = emit.write(j, ops)
        for r in ops:
            print(f"{r.label}: T{r.tool} {len(r.lines)} moves, "
                  f"{r.path_len_mm/1000:.1f}m, ~{r.est_min:.0f} min")
        print(f"wrote {out}")
    if args.cmd in ("verify", "all"):
        report = verifymod.verify(j)
        print(report.text())
        if not report.ok:
            return 1
    if args.cmd in ("preview", "all"):
        print("preview:", previewmod.render(j))
    if args.cmd == "view":
        url = viewer.start(args.port)
        report = verifymod.verify(j)
        viewer.push_state(
            j.name, report.carve.stock, report.carve.ppm, report.carve.half,
            [{"name": c.name, "value": c.value, "limit": c.limit, "ok": c.ok}
             for c in report.checks], report.ok)
        print(f"viewer at {url} — ctrl-c to stop")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
