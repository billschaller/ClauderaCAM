"""Stage model: the per-stage data model behind the viewer UX.

Proves, against real programs:
  - stages are recovered from the program BYTES (the golden mango program
    yields exactly its four operations, in order, with the right tools)
  - per-stage stock snapshots partition the carve: the last snapshot is
    bit-exact the final stock, consecutive snapshots only ever remove
    material, and per-stage removed volumes sum to the program total
  - stage stats are JSON-clean and the viewer payload round-trips through
    the server's push_session
  - files without markers become a single "program" stage; moves before the
    first marker become a "setup" stage

Run: .venv/bin/python tests/stage_model.py
"""
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))

from clauderacam import emit, engine, job as jobmod, simulate, stages  # noqa: E402
from clauderacam.viewer import server as viewer_server  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {name}: {'OK' if ok else 'FAIL'}" + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(name)


# --- 1. stage recovery from the golden mango bytes (parse only, no carve) --
print("stage recovery from tests/golden/mango-brass.nc")
mango = jobmod.load(REPO / "jobs" / "mango.toml")
m = simulate.prep_moves(REPO / "tests" / "golden" / "mango-brass.nc", mango,
                        mango.machine["rapid_feed"])
check("labels", m.stage_labels == ["rough", "semi", "finish", "cutout"],
      str(m.stage_labels))
tools_per_stage = [sorted({int(t) for t in m.tool_num[m.stage == s]})
                   for s in range(4)]
check("tools per stage", tools_per_stage == [[1], [2], [4], [1]],
      str(tools_per_stage))
check("every move assigned a stage", int(m.stage.min()) == 0
      and int(m.stage.max()) == 3)

# --- 2. carve + snapshots on the dome reference job (small, fast) ----------
print("stage snapshots on the dome job")
if not (REPO / "assets" / "generated" / "dome.stl").exists():
    import runpy
    runpy.run_path(str(REPO / "assets" / "generate_references.py"),
                   run_name="__main__")
dome = jobmod.load(REPO / "jobs" / "dome.toml")
emit.write(dome, engine.generate_ops(dome))
res = simulate.carve_check(dome.out, dome)
want_labels = [op.get("label", op["kind"]) for op in dome.ops]
check("labels match the job's ops", res.stage_labels == want_labels,
      str(res.stage_labels))
check("one snapshot per stage", len(res.stage_stocks) == len(res.stage_labels))
check("last snapshot is the final stock, bit-exact",
      bool((res.stage_stocks[-1] == res.stock).all()))
mono = all(bool((res.stage_stocks[s + 1] <= res.stage_stocks[s] + 1e-6).all())
           for s in range(len(res.stage_stocks) - 1))
check("stages only remove material", mono)
st = stages.stage_stats(dome, res)
vol_sum = sum(s["volume_mm3"] for s in st)
vol_all = float(res.metrics.volume.sum())
check("stage volumes partition the total",
      abs(vol_sum - vol_all) <= 1e-6 * max(vol_all, 1.0),
      f"{vol_sum:.3f} vs {vol_all:.3f}")
check("every stage has a time estimate", all(s["est_s"] > 0 for s in st))

# --- 3. viewer payload round-trips through the server ----------------------
print("viewer payload")
from clauderacam import verify as verifymod  # noqa: E402
report = verifymod.verify(dome)
meta, stocks = stages.viewer_payload(dome, report)
try:
    json.dumps(meta)
    check("meta is JSON-clean", True)
except TypeError as e:
    check("meta is JSON-clean", False, str(e))
sid = viewer_server.push_session(
    meta["path"], meta, viewer_server.encode_stocks(stocks))
sess = viewer_server._sessions[sid]
check("server serves one buffer per stage",
      len(sess.stocks) == len(res.stage_labels))
check("buffer sizes match n²",
      all(len(b) == meta["n"] ** 2 * 4 for b in sess.stocks))
try:
    json.dumps(sess.meta)
    check("pushed meta is JSON-clean", True)
except TypeError as e:
    check("pushed meta is JSON-clean", False, str(e))
check("same path joins the same session",
      viewer_server.push_session(
          meta["path"], meta, viewer_server.encode_stocks(stocks)) == sid)

# --- 4. marker-less and pre-marker moves -----------------------------------
print("degenerate programs")
scratch = REPO / "out" / "stage_model_tmp.nc"
scratch.parent.mkdir(exist_ok=True)
scratch.write_text(
    "G21\nG90 G94\nM05\nM6 T1\nM3 S12000\nG4 P2\n"
    "G0 Z3\nG0 X0 Y0\nG1 Z-0.1 F100\nG1 X1 F100\nM05\nG28\nM30\n")
mm = simulate.prep_moves(scratch, mango, 3000.0)
check("no markers -> single 'program' stage",
      mm.stage_labels == ["program"] and int(mm.stage.max()) == 0,
      str(mm.stage_labels))
scratch.write_text(
    "G21\nG90 G94\nM05\nM6 T1\nM3 S12000\nG4 P2\n"
    "G0 Z3\nG0 X0 Y0\nG1 Z-0.1 F100\n"
    "(begin operation: opA T1 flat d3.175)\n"
    "G1 X1 F100\n(finish operation: opA)\nM05\nG28\nM30\n")
mm = simulate.prep_moves(scratch, mango, 3000.0)
check("pre-marker moves -> 'setup' stage",
      mm.stage_labels == ["setup", "opA"], str(mm.stage_labels))
scratch.unlink()

print("STAGE MODEL " + ("FAIL: " + ", ".join(fails) if fails else "PASS"))
sys.exit(1 if fails else 0)
