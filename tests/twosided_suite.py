"""Two-sided (pin-and-flip) suite: the mode's contract, positive and
negative.

Positive: the synthetic twoside-ref job generates and verifies PASS on
both sides, with the cross-side checks present (punch-through, sever vs
actual bottom, tab bridge, pin keep-out) and the pin machinery checked on
the front (hole depth/position, bed clearance, drill plunge).

Negatives — each a real failure mode of the mode, each must be CAUGHT:
  floating tabs   tab_top chosen below the front moat's carried floor
                  leaves the tabs attached to nothing (found on paper
                  designing the mode; the tab-bridge check is its law)
  punch-through   a back model deep enough to break into the front art
  displaced pins  drill moves not at the configured pin positions, and
                  side-2 machining crossing a (mis-configured) pin
  drill overreach hole deeper than the drill's reach-to-shank-step
  asymmetric pins pin layout that cannot land in its own holes after the
                  flip (load-time refusal)
  front cutout    a cutout on side 1 (load-time refusal: the coin must
                  stay attached until the back's tabs exist)
  tab law         hand-written tab_top, tab_bridge below the minimum, and
                  tab_bridge thicker than the coin's actual edge — all
                  load-time refusals (bench 2026-07-29: a hand-picked
                  tab_top made 0.241mm tabs that snapped too thin)

Run: .venv/bin/python tests/twosided_suite.py
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

from clauderacam import emit, engine, twosided  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {name}: {'OK' if ok else 'FAIL'}"
          + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def caught(name, fn, needle=""):
    try:
        fn()
    except ValueError as e:
        ok = needle in str(e)
        print(f"  CAUGHT  {name}  [{str(e)[:70]}]" if ok
              else f"  WRONG ERROR  {name}  [{e}]")
        if not ok:
            fails.append(name)
        return
    print(f"  MISSED  {name}")
    fails.append(name)


if not (REPO / "assets" / "generated" / "twoside_front.stl").exists():
    import runpy
    runpy.run_path(str(REPO / "assets" / "generate_references.py"),
                   run_name="__main__")

JOB = REPO / "jobs" / "twoside-ref.toml"

print("positive: generate + verify both sides")
ts = twosided.load(JOB)
check("relief floors resolve exactly (top-surface derivation)",
      abs(ts.front.floor_z + 1.15) < 1e-6
      and abs(ts.back.floor_z + 0.85) < 1e-6,
      f"front {ts.front.floor_z:.3f} back {ts.back.floor_z:.3f}")
twosided.generate(ts)
reports = twosided.verify(ts)
check("front PASS", reports["front"].ok,
      "; ".join(c.name for c in reports["front"].checks if not c.ok))
check("back PASS", reports["back"].ok,
      "; ".join(c.name for c in reports["back"].checks if not c.ok))
front_names = {c.name for c in reports["front"].checks}
back_names = {c.name for c in reports["back"].checks}
check("front has the pin machinery checks",
      {"pin hole depth error", "drill only at pin positions",
       "drill clear of machine bed", "drill plunge feed"} <= front_names)
check("back has the cross-side checks",
      {"punch-through into front side", "sever vs actual bottom",
       "tab bridge (coin held to skeleton)",
       "pin keep-out (side 2)"} <= back_names)
# tab_top is DERIVED (bench 2026-07-29: hand-picked -1.6 here would make
# 0.45 tabs; the first cut coin's 0.241 snapped too thin): -t - front
# floor + default 0.6 target = -3.2 + 1.15 + 0.6
check("tab_top derived from the carried front floor",
      ts.back.ops[-1]["tab_top"] == -1.45,
      f"tab_top {ts.back.ops[-1]['tab_top']}")
tb = next(c for c in reports["back"].checks
          if c.name == "tab bridge (coin held to skeleton)")
check("measured bridge meets the derived target",
      0.55 <= tb.value <= 0.65, f"bridge {tb.value:.3f}")

print("negative: floating tabs (tab_top below the carried floor)")
ts_bad = twosided.load(JOB)
ts_bad.back.ops[-1]["tab_top"] = -2.8
emit.write(ts_bad.back, engine.generate_ops(ts_bad.back))
rep = twosided.verify(ts_bad)["back"]
bad = {c.name for c in rep.checks if not c.ok}
check("tab bridge catches floating tabs",
      "tab bridge (coin held to skeleton)" in bad, str(bad))

print("negative: back model punches into front art")
ts_bad = twosided.load(JOB)
ts_bad.back.z_offset -= 1.4     # back floor -2.25 vs carried front at -2.05
ts_bad.back.floor_z -= 1.4
emit.write(ts_bad.back, engine.generate_ops(ts_bad.back))
rep = twosided.verify(ts_bad)["back"]
bad = {c.name for c in rep.checks if not c.ok}
check("punch-through catches it",
      "punch-through into front side" in bad, str(bad))

print("restore good back program")
ts = twosided.load(JOB)
emit.write(ts.back, engine.generate_ops(ts.back))

print("negative: pins displaced into the machined field")
ts_bad = twosided.load(JOB)
ts_bad.pins["positions"] = [[0.0, 9.0], [0.0, -9.0]]  # in the slot band
for op in ts_bad.front.ops:
    if op["kind"] == "pindrill":
        op["positions"] = [[0.0, 9.0], [0.0, -9.0]]
        # the front .nc still drills at the REAL pins (±13.5) — the gate
        # must notice the program and the config disagree
rep = twosided.verify(ts_bad)
bad_front = {c.name for c in rep["front"].checks if not c.ok}
bad_back = {c.name for c in rep["back"].checks if not c.ok}
check("drill-position check catches the front mismatch",
      "drill only at pin positions" in bad_front, str(bad_front))
check("pin keep-out catches side-2 machining over the 'pins'",
      "pin keep-out (side 2)" in bad_back, str(bad_back))

print("negative: plan refusals")
# the job's own drill is the shop's 2x12 (12 reach) making a 12.8 hole —
# legal ONLY through the 1.0 counterbore credit. Remove the counterbore
# and the same plan must be refused; the credit must also refuse a spot
# tool narrower than the drill's shank.
ts_bad = twosided.load(JOB)
for op in ts_bad.front.ops:
    if op["kind"] == "spotface":
        op["depth"] = 0.1
caught("hole deeper than the drill's reach (counterbore removed)",
       lambda: engine.check_job_plan(ts_bad.front), "reach")
ts_bad = twosided.load(JOB)
for op in ts_bad.front.ops:
    if op["kind"] == "spotface":
        op["tool"] = 3          # Ø2 flat: narrower than the 3.175 shank
caught("counterbore narrower than the drill shank",
       lambda: engine.check_job_plan(ts_bad.front), "reach")
ts_bad = twosided.load(JOB)
ts_bad.front.spoil_thickness = 0.0
ts_bad.front.ops[-1]["depth"] = 12.8
caught("pindrill without a spoilboard",
       lambda: engine.check_job_plan(ts_bad.front), "spoilboard")

text = JOB.read_text()
tmp = REPO / "out" / "twoside_bad.toml"
tmp_inv = REPO / "out" / "inventory.toml"   # the crib gate runs at load;
tmp_inv.write_text((REPO / "jobs" / "inventory.toml").read_text())
tmp.write_text(text.replace("[[0.0, 13.5], [0.0, -13.5]]",
                            "[[3.0, 13.5], [0.0, -13.5]]"))
caught("pins asymmetric under the flip",
       lambda: twosided.load(tmp), "symmetric")
tmp.write_text(text.replace("[[side.back.op]]\nkind = \"cutout\"",
                            "[[side.front.op]]\nkind = \"cutout\""))
caught("cutout on the front side",
       lambda: twosided.load(tmp), "front side must not cut out")
# the tab law (bench 2026-07-29): tab_top is derived, never hand-written;
# targets below the floor-backed minimum or above the coin's actual edge
# thickness (t - front moat - back moat = 1.2 here) refuse at load
tmp.write_text(text.replace("tab_width = 2.0",
                            "tab_width = 2.0\ntab_top = -1.6"))
caught("hand-written tab_top on a two-sided back",
       lambda: twosided.load(tmp), "derived")
tmp.write_text(text.replace("tab_width = 2.0",
                            "tab_width = 2.0\ntab_bridge = 0.3"))
caught("tab_bridge below the minimum",
       lambda: twosided.load(tmp), "below the minimum")
tmp.write_text(text.replace("tab_width = 2.0",
                            "tab_width = 2.0\ntab_bridge = 1.4"))
caught("tab_bridge thicker than the coin edge",
       lambda: twosided.load(tmp), "unreachable")
tmp.unlink()
tmp_inv.unlink()

print("TWOSIDED " + ("FAIL: " + ", ".join(fails) if fails else "PASS"))
sys.exit(1 if fails else 0)
