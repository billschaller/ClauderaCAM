"""Golden test: the mango job regenerated through the clauderacam library must
reproduce the exact toolpaths that cut the real coin, and the assembled .nc
must pass full verification. Run: .venv/bin/python tests/golden_mango.py"""
import sys
import time
from pathlib import Path

from clauderacam import emit, engine, job as jobmod, preview, verify

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "tests" / "golden"
REF = {"rough": "rough_custom.gcode", "semi": "semi_custom.gcode",
       "finish": "finish_custom.gcode", "cutout": "cutout_custom.gcode"}

j = jobmod.load(REPO / "jobs" / "mango.toml")

t0 = time.time()
ops = engine.generate_ops(j)
print(f"generated {len(ops)} ops in {time.time()-t0:.1f}s")

fail = False
for r in ops:
    ref = (GOLDEN / REF[r.label]).read_text()
    got = "\n".join(r.lines) + "\n"
    if got == ref:
        print(f"  {r.label}: EXACT MATCH ({len(r.lines)} moves, "
              f"{r.path_len_mm/1000:.1f}m, ~{r.est_min:.0f} min)")
    else:
        fail = True
        gl, rl = got.splitlines(), ref.splitlines()
        print(f"  {r.label}: MISMATCH gen={len(gl)} ref={len(rl)} lines")
        for i, (a, b) in enumerate(zip(gl, rl)):
            if a != b:
                print(f"    first diff line {i+1}:\n      gen: {a}\n      ref: {b}")
                break

out = emit.write(j, ops)
print(f"assembled {out}")

# the FULL program is golden too: preamble, M05/M6/M3/G4 blocks and postamble
# had zero coverage before the 2026-07-28 review — every safety line could be
# dropped with both suites green
ref_nc = (GOLDEN / "mango-brass.nc").read_text()
got_nc = Path(out).read_text()
if got_nc == ref_nc:
    print("  full program: EXACT MATCH")
else:
    fail = True
    gl, rl = got_nc.splitlines(), ref_nc.splitlines()
    print(f"  full program: MISMATCH gen={len(gl)} ref={len(rl)} lines")
    for i, (a, b) in enumerate(zip(gl, rl)):
        if a != b:
            print(f"    first diff line {i+1}:\n      gen: {a}\n      ref: {b}")
            break

report = verify.verify(j)
print(report.text())
if not report.ok:
    fail = True

p = preview.render(j)
print(f"preview {p}")

sys.exit(1 if fail else 0)
