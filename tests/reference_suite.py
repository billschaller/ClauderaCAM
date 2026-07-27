"""Reference suite: generate the synthetic reference shapes, then run every
reference job end-to-end (generate -> emit -> verify -> preview). Each job is
designed to stress a different hazard; all must PASS honestly.

Run: .venv/bin/python tests/reference_suite.py
"""
import runpy
import sys
import time
from pathlib import Path

from clauderacam import emit, engine, job as jobmod, preview, verify

REPO = Path(__file__).resolve().parents[1]
JOBS = ["terraces", "dome", "ripple", "pocketfield"]

print("== generating reference shapes ==")
runpy.run_path(str(REPO / "assets" / "generate_references.py"),
               run_name="__main__")

failed = []
for name in JOBS:
    print(f"\n== {name} ==")
    t0 = time.time()
    j = jobmod.load(REPO / "jobs" / f"{name}.toml")
    ops = engine.generate_ops(j)
    emit.write(j, ops)
    for r in ops:
        print(f"  {r.label}: T{r.tool} {len(r.lines)} moves, "
              f"{r.path_len_mm/1000:.1f}m, ~{r.est_min:.0f} min")
    report = verify.verify(j)
    print("  " + report.text().replace("\n", "\n  "))
    preview.render(j)
    print(f"  ({time.time()-t0:.1f}s)")
    if not report.ok:
        failed.append(name)

print()
if failed:
    print(f"SUITE FAIL: {', '.join(failed)}")
    sys.exit(1)
print(f"SUITE PASS: all {len(JOBS)} reference jobs verified")
