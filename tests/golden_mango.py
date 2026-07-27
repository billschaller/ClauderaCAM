"""Golden test: the mango job regenerated through the clauderacam library must
reproduce the exact toolpaths that cut the real coin, and the assembled .nc
must pass full verification. Run: .venv/bin/python tests/golden_mango.py"""
import sys
import time
from pathlib import Path

from clauderacam import emit, engine, job as jobmod, preview, verify

REPO = Path(__file__).resolve().parents[1]
GOLDEN = Path("/home/dad/scratch/carvera/mango-brass")
REF = {"rough": "rough_custom.gcode", "semi": "semi_custom.gcode",
       "finish": "finish_custom.gcode", "cutout": "cutout_custom.gcode"}

# the golden assets (the coin STL and its cut toolpaths) are the maintainer's
# local files and are deliberately not distributed with the repo
if not GOLDEN.is_dir() or not all((GOLDEN / f).is_file() for f in REF.values()):
    print("SKIP: golden reference assets not present on this machine")
    sys.exit(0)

j = jobmod.load(REPO / "jobs" / "mango.toml")
if not j.stl.is_file():
    print(f"SKIP: golden STL not present ({j.stl})")
    sys.exit(0)

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

report = verify.verify(j)
print(report.text())
if not report.ok:
    fail = True

p = preview.render(j)
print(f"preview {p}")

sys.exit(1 if fail else 0)
