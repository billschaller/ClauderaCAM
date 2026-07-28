"""Negative controls: prove the gate can FAIL. The 2026-07-28 adversarial
review demonstrated that a verifier stubbed to always-PASS cleared the whole
test suite — nothing anywhere asserted that a bad file gets caught. Every
case here is a distilled review finding: a fragment of dangerous G-code or a
misconfigured job that MUST be refused, with the specific check that catches
it named. If any case slips through, the suite exits nonzero.

Run: .venv/bin/python tests/negative_suite.py
"""
import sys
import tempfile
from pathlib import Path

from clauderacam import emit, engine, job as jobmod, verify

REPO = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="clauderacam-neg-"))

# base: a fully valid dome program (generated fresh so it matches the code)
base_job = jobmod.load(REPO / "jobs" / "dome.toml")
base_ops = engine.generate_ops(base_job)
base_lines = emit.assemble(base_job, base_ops).splitlines()
POST = base_lines.index("(begin postamble)")


def block(tool_num: int, *moves: str) -> list[str]:
    return ["M05", f"M6 T{tool_num}", "M3 S12000", "G4 P2", *moves]


def spliced(name: str, extra: list[str]) -> Path:
    p = TMP / f"{name}.nc"
    p.write_text("\n".join(base_lines[:POST] + extra + base_lines[POST:]) + "\n")
    return p


def edited(name: str, transform) -> Path:
    p = TMP / f"{name}.nc"
    p.write_text(transform("\n".join(base_lines) + "\n"))
    return p


results = []


def expect_check_fails(name: str, path: Path, check_substr: str):
    r = verify.verify(base_job, path)
    hit = [c for c in r.checks if check_substr in c.name and not c.ok]
    ok = (not r.ok) and bool(hit)
    results.append((name, ok,
                    f"caught by {hit[0].name} = {hit[0].value:.3f}" if hit
                    else f"NOT caught (report ok={r.ok})"))


def expect_fatal(name: str, path: Path):
    r = verify.verify(base_job, path)
    ok = (not r.ok) and r.checks and r.checks[0].name == "gcode fatal"
    results.append((name, ok, r.checks[0].detail[:80] if ok
                    else f"NOT fatal (ok={r.ok})"))


def expect_plan_refusal(name: str, mutate):
    j = jobmod.load(REPO / "jobs" / "dome.toml")
    mutate(j)
    try:
        engine.check_job_plan(j)
        results.append((name, False, "NOT refused"))
    except ValueError as e:
        results.append((name, True, str(e)[:80]))


CUTOUT_OP = {"kind": "cutout", "tool": 3, "z_start": -1.7, "z_final": -3.35,
             "ramp": 0.15, "tabs": [45], "tab_width": 2.0, "tab_top": -2.7,
             "seg": 128, "feed": 300, "plunge": 150}

# --- G-code the gate must refuse or fail -----------------------------------
expect_fatal("unknown tool skips simulation",
             spliced("unknown_tool", block(9, "G0 Z3.000", "G0 X0.000 Y13.000",
                                           "G1 Z-3.000 F100")))
expect_fatal("arc (spaced)",
             spliced("arc", block(3, "G0 Z3.000", "G2 X1.000 Y1.000 I1.000")))
expect_fatal("arc (compact, no spaces)",
             spliced("arc_compact", block(3, "G0Z3.000", "G2X1Y1I1")))
expect_fatal("G-less modal coordinate line",
             spliced("modal", block(3, "G0 Z3.000", "G0 X0.000 Y0.000",
                                    "G1 Z-0.500 F300", "X5.000")))
expect_check_fails("rapid Z-plunge into stock",
                   spliced("rapid_plunge",
                           block(3, "G0 Z3.000", "G0 X0.000 Y13.000",
                                 "G0 Z-2.500", "G0 Z3.000")),
                   "rapid-vs-stock")
expect_check_fails("cut below machine-bed depth (compact words)",
                   spliced("too_deep",
                           block(3, "G0Z3.000", "G0X0.000Y13.000",
                                 "G1Z-4.200F100", "G0 Z3.000")),
                   "depth floor")
expect_check_fails("1mm ball buried ~1mm past the finished surface",
                   spliced("buried_ball",
                           block(4, "G0 Z3.000", "G0 X2.000 Y0.000",
                                 "G1 Z-1.350 F100", "G1 X2.800 Y0.000 F300",
                                 "G0 Z3.000")),
                   "T4 ball contact")
expect_check_fails("gouge below the model surface",
                   spliced("gouge",
                           block(4, "G0 Z3.000", "G0 X-1.000 Y0.000",
                                 "G1 Z-1.000 F100", "G1 X1.000 Y0.000 F300",
                                 "G0 Z3.000")),
                   "gouge")
expect_check_fails("cutting inside the fixture keep-out",
                   spliced("keepout",
                           block(3, "G0 Z3.000", "G0 X0.000 Y15.000",
                                 "G1 Z-0.500 F100", "G1 X0.500 Y15.000 F300",
                                 "G0 Z3.000")),
                   "fixture keep-out")
expect_check_fails("dropped G4 spin-up dwells",
                   edited("no_dwell", lambda s: s.replace("G4 P2\n", "")),
                   "dialect lint")
expect_check_fails("dropped M05 before tool changes",
                   edited("no_m05", lambda s: s.replace("M05\n", "")),
                   "dialect lint")

# --- job plans the engine must refuse --------------------------------------
expect_plan_refusal("raster before rough (op order)",
                    lambda j: j.ops.insert(0, j.ops.pop(1)))
expect_plan_refusal("cutout not last",
                    lambda j: j.ops.insert(1, dict(CUTOUT_OP)))
expect_plan_refusal("rough boundary too small for ball reach",
                    lambda j: j.ops[0].__setitem__("boundary_r", 10.0))
expect_plan_refusal("op reach crosses fixture keep-out",
                    lambda j: j.ops[0].__setitem__("boundary_r", 14.0))
expect_plan_refusal("cutout that does not sever the stock",
                    lambda j: j.ops.append(dict(CUTOUT_OP, z_final=-2.0)))
expect_plan_refusal("cutout entering below the cleared field",
                    lambda j: j.ops.append(dict(CUTOUT_OP, z_start=-1.9)))

# ---------------------------------------------------------------------------
fail = False
for name, ok, note in results:
    print(f"{'CAUGHT ' if ok else 'MISSED '} {name}  [{note}]")
    if not ok:
        fail = True
print(f"\n{'NEGATIVE SUITE PASS' if not fail else 'NEGATIVE SUITE FAIL'}: "
      f"{sum(1 for _, ok, _ in results if ok)}/{len(results)} hazards caught")
sys.exit(1 if fail else 0)
