"""PCB viewer sessions (WS6): a [pcb] job in front of human eyes, end to end.

Everything here runs against Board A's BLESSED assets (tests/golden_pcb) —
the same bytes the gate judges — because a viewer that shows a different
board than the gate cleared is worse than no viewer.

What must hold, and why:

  the sheet stock model (the WS5 debt)
    - the sheet window is DERIVED: Edge.Cuts extents through
      boardmaps.machine_offset (the 154/124 law), grown by the widest
      off-board reach the job configures. Board A lands at machine
      0..55 x 0..40 and its programs work outside it, so the pad is not
      decoration.
    - the crop the browser is served is LOSSLESS: no material is removed
      outside it. A program that cuts outside the modelled sheet is refused,
      not cropped — a preview that hides a cut is the Article VI failure.
    - the MILL and HOLES programs ride simulate.carve unchanged and produce
      the checks checks.py lists as "deliberately not checked here": rapids
      vs stock, true footprint contact, shank clearance, depth vs the blank
      and the spoilboard, plus the physics verdicts.
    - a laser program gets NO stock sim and the scrub program gets no stage
      snapshot: neither carves (Article VI / the Article IX scrub exemption),
      so a heightmap of either would be a flat sheet presented as a preview.

  the 2D overlay
    - silk strokes come from the program's own firing G1 segments and scrub
      laps from its cutting moves — parsed from the bytes the gate judged.
    - the gerber reference layers are labelled as gerber, default to off, and
      COUNT what they cannot draw (an overlay that silently omits pads is the
      same class of lie as a preview of the target model).

  the run sheet
    - the operator steps and the four programs in ONE order, with the mask /
      legend / scrub cycle in the operator's revised chain (silk BEFORE the
      pad scrub), every program in its place, and the job's own numbers.

  the session protocol
    - four sessions, one per program; stage lists recovered from the program
      markers; stock buffers at nx*ny; the version/409 discipline; downloads
      serving the VERIFIED bytes; and /api/open on a [pcb] TOML dissolving
      into the four program sessions.

  the verdict discipline (Article I)
    - a session shows PASS only when the PCB gate itself ran. Without gerbv
      the sheet sim, the overlays and the run sheet still work and the
      session says UNVERIFIED — it never shows a green badge it did not earn.

Run: .venv/bin/python tests/pcb_viewer_suite.py
"""
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# the discovery file must never touch the developer's real one
_tmp = tempfile.TemporaryDirectory(prefix="clauderacam-pcbview-test-")
os.environ["CLAUDERACAM_VIEWER_FILE"] = str(Path(_tmp.name) / "viewer.json")

import numpy as np  # noqa: E402

from clauderacam.pcb import boardmaps as bm  # noqa: E402
from clauderacam.pcb import checks, pcbjob, session as pcbsess  # noqa: E402
from clauderacam.viewer import client, server  # noqa: E402

GOLDEN = REPO / "tests" / "golden_pcb"
TD = Path(_tmp.name)
fails = []


def check(name, ok, detail=""):
    print(f"  {name}: {'OK' if ok else 'FAIL'}"
          + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def caught(name, fn, needle):
    try:
        fn()
    except Exception as e:
        check(name, needle in str(e), f"got: {str(e)[:100]}")
        return
    check(name, False, "no exception raised")


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def post(url, body: bytes):
    req = urllib.request.Request(url, data=body)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


if not GOLDEN.is_dir() or not (GOLDEN / "coupon.toml").is_file():
    print("SKIP: no golden-pcb assets — nothing to show a session of")
    print("PCB VIEWER PASS (skipped)")
    sys.exit(0)

HAVE_GERBV = bm.have_gerbv()
if not HAVE_GERBV:
    print("SKIP(partial): gerbv not available — the PCB GATE cannot run, so "
          "every session must come out UNVERIFIED (asserted below). The "
          "sheet sim, the overlays and the run sheet need no raster and run "
          "in full.  (install: sudo apt install gerbv)")

job = pcbjob.load(GOLDEN / "coupon.toml")
progs = pcbsess.program_paths(job)

# =========================================================== the sheet stock
print("\nsheet stock model (the WS5 debt):")
sheet = pcbsess.sheet_stock(job)
tight = bm.extents(job.files["edge"], cross_check=False)
dx, dy = bm.machine_offset(tight, job.anchor)
check("board window is DERIVED, not typed",
      abs(sheet.bx0 - (dx - tight.x1)) < 1e-9
      and abs(sheet.bx1 - (dx - tight.x0)) < 1e-9
      and abs(sheet.by0 - (tight.y0 + dy)) < 1e-9
      and abs(sheet.by1 - (tight.y1 + dy)) < 1e-9,
      f"board {sheet.bx0:g},{sheet.by0:g}..{sheet.bx1:g},{sheet.by1:g} "
      f"from Edge.Cuts {tight.x0:g},{tight.y0:g}..{tight.x1:g},{tight.y1:g} "
      f"+ offset {dx:g},{dy:g}")
check("sheet = board window + the widest off-board reach",
      abs(sheet.pad - (checks.CHECK_PAD_BASE
                       + max(job.phases["clear"]["margin"],
                             job.phase_tool("cutout").diameter))) < 1e-9
      and sheet.x0 < sheet.bx0 and sheet.x1 > sheet.bx1,
      f"pad {sheet.pad:g} -> {sheet.x0:g},{sheet.y0:g}.."
      f"{sheet.x1:g},{sheet.y1:g}")
check("sheet thickness and spoilboard come from the job",
      sheet.thickness == job.thickness and sheet.spoil == job.spoil_thickness,
      f"{sheet.thickness}mm blank over {sheet.spoil}mm spoilboard")
# the crop must be the window, in simulate.py's ONE mapping (Article IV)
wx = sheet.j_off / sheet.ppm - sheet.half
wy = sheet.half - sheet.i_off / sheet.ppm
check("crop indices invert to the window corner (one mapping, Article IV)",
      abs(wx - sheet.x0) <= 1.0 / sheet.ppm
      and abs(wy - sheet.y1) <= 1.0 / sheet.ppm,
      f"crop origin ({wx:.3f},{wy:.3f}) vs window "
      f"({sheet.x0:.3f},{sheet.y1:.3f})")
check("crop is smaller than the square grid it comes from",
      sheet.nx < sheet.n and sheet.ny < sheet.n,
      f"{sheet.nx}x{sheet.ny} of {sheet.n}x{sheet.n}")

sj = pcbsess.sheet_job(job, sheet)
res_mill = pcbsess.carve_program(sj, sheet, progs["mill"])
check("the crop is lossless (nothing carved outside the served window)",
      sheet.outside_min(res_mill.stock) == 0.0,
      f"worst outside {sheet.outside_min(res_mill.stock):.4f}")
crop = sheet.crop(res_mill.stock)
check("the crop keeps every cut",
      abs(float(crop.min()) - float(res_mill.stock.min())) < 1e-9
      and crop.shape == (sheet.ny, sheet.nx),
      f"crop min {float(crop.min()):.4f} vs grid min "
      f"{float(res_mill.stock.min()):.4f}")

print("\nsheet checks (the gate's own machinery, on a PCB program):")
sc = pcbsess.sheet_checks(job, sheet, sj, res_mill)
names = [c.name for c in sc]
for want in ("sheet rapid-vs-stock", "sheet shank clearance",
             "sheet depth floor", "sheet clear of machine bed",
             "sheet containment", "sheet cutting power",
             "sheet sustained chip per tooth", "sheet plunge feed"):
    check(f"{want} present", want in names)
check("every sheet check PASSES on the blessed mill bytes",
      all(c.ok for c in sc),
      ", ".join(f"{c.name}={c.value:.4f}" for c in sc if not c.ok))
check("the vee's true footprint contact is measured, not assumed",
      any(c.name.startswith("sheet T2 vee contact") and c.value > 0
          for c in sc),
      next((f"{c.value:.3f} ({c.limit})" for c in sc
            if c.name.startswith("sheet T2 vee")), "missing"))

print("\nNEGATIVE: a program that works outside the modelled sheet:")
ESCAPE = """(clauderacam job: coupon)
G90 G94
G17
G21
G54
M05
M6 T3
M3 S12000
G4 P2
(begin operation: pcb-clear T3 flat d0.8)
G00 Z2.0000 F500.00
G00 X-20.0000 Y20.0000 F500.00
G01 Z-0.1500 F200.00
G01 X-19.0000 Y20.0000 F500.00
G00 Z2.0000
(finish operation: pcb-clear)
(begin postamble)
M05
G17 G90
G28
M30
"""
bad_nc = TD / "escape.nc"
bad_nc.write_text(ESCAPE)
bad_res = pcbsess.carve_program(sj, sheet, bad_nc)
bad_chk = {c.name: c for c in pcbsess.sheet_checks(job, sheet, sj, bad_res)}
check("sheet containment catches it by name",
      not bad_chk["sheet containment"].ok
      and bad_chk["sheet containment"].value > 16.0,
      f"escape {bad_chk['sheet containment'].value:.2f}mm, "
      f"{bad_chk['sheet containment'].detail}")
check("and the crop notices the material it would have hidden",
      sheet.outside_min(bad_res.stock) < -0.14,
      f"{sheet.outside_min(bad_res.stock):.3f}mm deep outside the crop")
caught("build() REFUSES rather than serve a crop that hides a cut",
       lambda: pcbsess.build(job, programs={**progs, "mill": bad_nc},
                             gate=False),
       "outside the modelled sheet")

# ============================================================ the sessions
print("\nbuild: four sessions, one per program of the split "
      f"(gate={'on' if HAVE_GERBV else 'OFF — no gerbv'}):")
t0 = time.time()
sessions = pcbsess.build(job, gate=HAVE_GERBV)
print(f"  (built in {time.time() - t0:.1f}s)")
by_name = {s.name: s for s in sessions}
check("one session per program, in chain order",
      [s.name for s in sessions] == list(checks.PROGRAM_PHASES),
      str([s.name for s in sessions]))
check("each session is keyed by the .nc the operator posts",
      all(Path(s.path).name == f"coupon-{s.name}.nc" for s in sessions))
for s in sessions:
    try:
        json.dumps(s.meta)
        ok = True
        why = ""
    except TypeError as e:
        ok, why = False, str(e)
    check(f"{s.name} meta is JSON-clean", ok, why)

print("\nstage discovery from the program MARKERS (never the config):")
check("mill stages", [st["label"] for st in by_name["mill"].meta["stages"]]
      == ["pcb-iso", "pcb-clear"])
check("holes stages", [st["label"] for st in by_name["holes"].meta["stages"]]
      == ["pcb-drills", "pcb-cutout"])
check("one stock snapshot per carved stage",
      len(by_name["mill"].stocks) == 2 and len(by_name["holes"].stocks) == 2)
mill_st = by_name["mill"].meta["stages"]
check("per-stage stats are real measurements",
      all(st["moves"] > 0 and st["cut_mm"] > 0 and st["volume_mm3"] > 0
          and st["est_s"] > 0 for st in mill_st)
      and mill_st[0]["tool"] == 2 and mill_st[1]["tool"] == 3,
      "; ".join(f"{st['label']} {st['moves']} moves "
                f"{st['volume_mm3']:.1f}mm³" for st in mill_st))
check("stage volumes partition the program total",
      abs(sum(st["volume_mm3"] for st in mill_st)
          - float(res_mill.metrics.volume.sum())) < 1e-6 * 200,
      f"{sum(st['volume_mm3'] for st in mill_st):.3f}")
check("the drills stage carries the deepest cut, not the configured one",
      abs(by_name["holes"].meta["stages"][0]["min_z"]
          - job.phases["drills"]["depth"]) < 1e-3,
      str(by_name["holes"].meta["stages"][0]["min_z"]))
check("tool cards come from the shared stages.tool_cards",
      {t["num"] for t in by_name["mill"].meta["tools"]} == set(job.tools)
      and any(t["contact"] > 0 for t in by_name["mill"].meta["tools"]))

print("\nnon-carving programs get NO stock simulation (Article VI):")
for name, why in (("silk", "a laser removes no material"),
                  ("scrub", "spring preload, empty kernel footprint")):
    m = by_name[name].meta
    check(f"{name}: no stage stocks, no carve claim",
          len(by_name[name].stocks) == 0 and m["carves"] is False
          and m["stages"][0]["overlay"] is True
          and m["stages"][0]["volume_mm3"] == 0.0, why)
    check(f"{name}: the session SAYS why",
          any("no stock simulation" in n for n in m["overlay"]["notes"]),
          "; ".join(m["overlay"]["notes"])[:90])
check("the scrub stage still reports its preload Z from the bytes",
      abs(by_name["scrub"].meta["stages"][0]["min_z"]
          - job.phases["scrub"]["depth"]) < 1e-9)

print("\nverdict discipline (Article I):")
if HAVE_GERBV:
    check("every blessed program PASSES gate + sheet sim",
          all(s.meta["ok"] is True for s in sessions),
          "; ".join(f"{s.name}: " + ", ".join(c["name"]
                                              for c in s.meta["checks"]
                                              if not c["ok"])
                    for s in sessions if s.meta["ok"] is not True))
    check("the gate's checks and the sheet's are BOTH in the list",
          any(c["name"] == "iso containment"
              for c in by_name["mill"].meta["checks"])
          and any(c["name"] == "sheet rapid-vs-stock"
                  for c in by_name["mill"].meta["checks"]),
          f"{len(by_name['mill'].meta['checks'])} checks on mill")
ungated = pcbsess.build(job, gate=False)
check("without the gate a session is UNVERIFIED, never PASS",
      all(s.meta["ok"] is None and s.meta["gate"]["ran"] is False
          and s.meta["gate"]["note"] for s in ungated),
      str([s.meta["gate"]["verdict"] for s in ungated]))
check("...even when its own sheet sim came out clean",
      all(c["ok"] for c in ungated[0].meta["checks"])
      and ungated[0].meta["ok"] is None)

# ============================================================= the overlay
print("\n2D overlay (what the program DRAWS):")
silk = by_name["silk"].meta["overlay"]
lay = {L["key"]: L for L in silk["layers"]}
check("silk layer is the FIRING strokes, on by default",
      lay["silk"]["on"] is True and lay["silk"]["kind"] == "strokes"
      and len(lay["silk"]["polylines"]) > 50,
      lay["silk"]["note"][:80])
pts = [p for pl in lay["silk"]["polylines"] for p in pl]
xs = [p[0] for p in pts]
ys = [p[1] for p in pts]
check("every stroke point is inside the board window",
      min(xs) >= sheet.bx0 and max(xs) <= sheet.bx1
      and min(ys) >= sheet.by0 and max(ys) <= sheet.by1,
      f"x {min(xs):.2f}..{max(xs):.2f} y {min(ys):.2f}..{max(ys):.2f}")
# the overlay must be the BYTES, not the gerber the emitter read: count the
# firing segments the gate's own silk check counts
gate_segs = sum(len(pl) - 1 for pl in lay["silk"]["polylines"])
check("firing segment count matches the program's G1 census",
      gate_segs == sum(1 for ln in
                       (GOLDEN / "coupon-silk.nc").read_text().splitlines()
                       if ln.startswith("G1 ")),
      f"{gate_segs} segments")
scrub = {L["key"]: L for L in by_name["scrub"].meta["overlay"]["layers"]}
check("scrub layer is the cutting laps at preload depth",
      scrub["scrub"]["on"] is True
      and len(scrub["scrub"]["polylines"]) > 50
      and f"Z{job.phases['scrub']['depth']:g}" in scrub["scrub"]["note"],
      scrub["scrub"]["note"][:80])
check("the mask apertures ride along as a LABELLED gerber layer, off",
      scrub["mask_ap"]["on"] is False
      and "gerber" in scrub["mask_ap"]["label"]
      and "not a verdict" in scrub["mask_ap"]["note"],
      scrub["mask_ap"]["note"][:70])
check("Board A exports no paste layer and the overlay says so",
      "paste_ap" not in scrub
      and any("no paste layer" in n
              for n in by_name["scrub"].meta["overlay"]["notes"]))
check("carving programs carry no overlay (their preview is the stock)",
      by_name["mill"].meta["overlay"] is None
      and by_name["holes"].meta["overlay"] is None)

print("\naperture parsing (the stencil/mask reference layers):")
PASTE = """%FSLAX46Y46*%
%MOMM*%
%AMRoundRect*
4,1,4,$2,$3,$4,$5,$6,$7,$8,$9,$2,$3,0*%
%AMFreePoly0*
4,1,3,0,0,1,0,0,1,0,0*%
%ADD10C,0.600000*%
%ADD11R,1.200000X0.800000*%
%ADD12RoundRect,0.250000X0.625000X-0.400000X0.625000X0.400000X-0.625000X0.400000X-0.625000X-0.400000X0*%
%ADD13FreePoly0,0.000000*%
G01*
D10*
X5000000Y5000000D03*
D11*
X10000000Y5000000D03*
D12*
X15000000Y5000000D03*
D13*
X20000000Y5000000D03*
M02*
"""
pgbr = TD / "paste.gbr"
pgbr.write_text(PASTE)
flashes, draws, skipped = pcbsess.apertures(pgbr)
check("circle, rect and KiCad RoundRect flashes are drawn exactly",
      len(flashes) == 3
      and len(flashes[1]["poly"]) == 5
      and abs(flashes[1]["poly"][0][0] - (10.0 - 0.6)) < 1e-9
      and abs(flashes[1]["poly"][0][1] - (5.0 - 0.4)) < 1e-9
      and len(flashes[2]["poly"]) == 5,
      f"{len(flashes)} flashes")
check("an aperture the overlay cannot draw is COUNTED, never dropped",
      skipped == {"FreePoly0": 1}, str(skipped))
ARCS = """%FSLAX46Y46*%
%MOMM*%
%ADD10C,0.200000*%
G01*
D10*
X13000000Y5000000D02*
G75*
G03*
X7000000Y5000000I-3000000J0D01*
X13000000Y5000000I3000000J0D01*
M02*
"""
agbr = TD / "arcs.gbr"
agbr.write_text(ARCS)
_, adraws, askip = pcbsess.apertures(agbr)
apts = np.array([p for c in adraws for p in c])
rad = np.hypot(apts[:, 0] - 10.0, apts[:, 1] - 5.0)
check("a G75 arc draw is interpolated, not flattened to a chord",
      len(apts) > 100 and abs(float(rad.max()) - 3.0) < 1e-6
      and abs(float(rad.min()) - 3.0) < 1e-6 and not askip,
      f"{len(apts)} points, radius {float(rad.min()):.6f}.."
      f"{float(rad.max()):.6f} of 3.0")
a74 = TD / "arcs74.gbr"
a74.write_text(ARCS.replace("G75*", "G74*"))
_, _, q74 = pcbsess.apertures(a74)
check("single-quadrant (G74) arcs are REFUSED and counted, never guessed",
      q74 == {"arc draw": 2}, str(q74))

# self-consistent against the file rather than a blessed count: the golden
# set is re-blessed whenever the board changes (Article III), and the
# overlay's job is to draw EVERY flash it finds, not a remembered number
real_flashes, real_draws, real_skipped = pcbsess.apertures(job.files["mask"])
mask_txt = job.files["mask"].read_text().splitlines()
d03 = sum(1 for ln in mask_txt if "D03*" in ln)
check("Board A's real mask layer: every D03 flash is drawn or counted",
      len(real_flashes) + sum(real_skipped.values()) == d03
      and len(real_flashes) > 50,
      f"{len(real_flashes)} drawn + {sum(real_skipped.values())} counted "
      f"= {d03} flashes in the gerber")
check("DRAWN mask ink is not silently omitted (Board A's scrub-margin ring "
      "is a graphic, not a pad)",
      len(real_draws) > 0 and all(len(c) > 2 for c in real_draws),
      f"{len(real_draws)} chains of "
      f"{[len(c) for c in real_draws]} points")

# ============================================================ the run sheet
print("\nrun-sheet card (the bench workflow IS the card):")
card = by_name["mill"].meta["run_sheet"]
check("the same card rides every session",
      all(s.meta["run_sheet"] == card for s in sessions))
check("numbered, gapless, in order",
      [st["n"] for st in card] == list(range(1, len(card) + 1)))
order = [st.get("program") for st in card if st["kind"] == "program"]
check("the four programs appear once each, in chain order",
      order == list(checks.PROGRAM_PHASES), str(order))
kinds = [st["kind"] for st in card]
check("it starts at the fixture and ends off the machine",
      kinds[0] == "setup" and kinds[-1] == "offmachine"
      and "tape" in card[0]["detail"], card[0]["title"])
titles = [st["title"] for st in card]


def idx(needle):
    return next(i for i, t in enumerate(titles) if needle in t)


check("the mask/scrub cycle is in the operator's REVISED order "
      "(mask -> silk -> scrub)",
      idx("squeegee") < idx("silk legend") < idx("scrub the mask"),
      " -> ".join(titles[idx("squeegee"):idx("scrub the mask") + 1]))
check("auto-level lands before the first program",
      idx("auto-level") < idx("program A"))
check("the laser module gets fitted and unfitted around the silk program",
      idx("fit the 455nm") < idx("silk legend") < idx("refit the spindle"))
check("every operator step between programs is present",
      all(any(n in t for t in titles) for n in
          ("squeegee", "white mask", "IPA", "snap the tabs", "stencil",
           "THT")))
mill_step = next(st for st in card if st.get("program") == "mill")
check("a program step names its file, its estimate and its M6 pauses",
      mill_step["file"] == "coupon-mill.nc" and mill_step["est_s"] > 0
      and "M6 pause" in mill_step["detail"] and "T2" in mill_step["detail"],
      mill_step["detail"][-70:])
check("the run sheet carries THIS job's numbers, not boilerplate",
      any(f"S{job.phases['silk']['dose']:g}" in st["detail"] for st in card)
      and any(f"{int(job.phases['cutout']['gaps'])} tabs" in st["detail"]
              for st in card)
      and any(f"Z{job.phases['scrub']['depth']:g}" in st["detail"]
              for st in card))
check("the stencil step is honest about the missing paste artwork",
      "NO B.Paste" in next(st["detail"] for st in card
                           if "stencil" in st["title"]))

# ====================================================== the HTTP protocol
print("\nsession protocol over real HTTP:")
url = server.start(port=0, jobs_dir=GOLDEN)
found = client.discover()
check("client discovers the server", bool(found) and found["url"] == url)
pushed = client.push_pcb(sessions)
check("all four sessions land", len(pushed) == 4,
      str([p[0] for p in pushed]))
sids = {name: sid for name, _, sid in pushed}
code, resp = get(url + "api/sessions")
rows = json.loads(resp)["sessions"]
check("four ready sessions listed",
      len(rows) == 4 and all(r["status"] == "ready" for r in rows)
      and {r["job"] for r in rows} == {f"coupon {n}"
                                       for n in checks.PROGRAM_PHASES},
      str([(r["job"], r["ok"]) for r in rows]))
code, resp = get(url + f"api/session/{sids['mill']}/state")
st = json.loads(resp)
check("state carries the pcb shape", st["kind"] == "pcb"
      and st["nstages"] == 2 and st["nx"] == sheet.nx
      and st["ny"] == sheet.ny and st["program"] == "mill"
      and len(st["run_sheet"]) == len(card))
v = urllib.parse.quote(st["version"])
sizes = []
for k in range(st["nstages"]):
    code, blob = get(url + f"api/session/{sids['mill']}/stock?v={v}&stage={k}")
    sizes.append((code, len(blob)))
check("every stage buffer served at nx*ny (the cropped sheet)",
      all(c == 200 and n == sheet.nx * sheet.ny * 4 for c, n in sizes),
      str(sizes))
code, _ = get(url + f"api/session/{sids['mill']}/stock?v=bogus&stage=0")
check("stale version -> 409", code == 409, str(code))
code, _ = get(url + f"api/session/{sids['silk']}/stock?v="
              + urllib.parse.quote(
                  json.loads(get(url + f"api/session/{sids['silk']}/state")[1]
                             )["version"]) + "&stage=0")
check("an overlay-only session serves no stock -> 404", code == 404,
      str(code))
with urllib.request.urlopen(url + f"api/session/{sids['silk']}/program",
                            timeout=30) as r:
    disp = r.headers.get("Content-Disposition", "")
    got = r.read()
check("download serves the exact bytes the gate judged",
      got == (GOLDEN / "coupon-silk.nc").read_bytes()
      and "coupon-silk.nc" in disp, f"{len(got)} bytes, {disp}")
again = client.push_pcb(sessions)
check("re-pushing joins the same four sessions",
      [p[2] for p in again] == [p[2] for p in pushed])

print("\n/api/open on a [pcb] TOML (the browser path):")
code, resp = post(url + "api/open",
                  json.dumps({"path": "coupon.toml"}).encode())
check("open accepted", code == 200, str(code))
placeholder = json.loads(resp)["sid"]
deadline = time.time() + 300
while time.time() < deadline:
    rows = json.loads(get(url + "api/sessions")[1])["sessions"]
    if not any(r["sid"] == placeholder for r in rows) \
            and len(rows) == 4 and all(r["status"] == "ready" for r in rows):
        break
    time.sleep(0.5)
rows = json.loads(get(url + "api/sessions")[1])["sessions"]
check("the placeholder dissolved into the four program sessions",
      len(rows) == 4 and placeholder not in {r["sid"] for r in rows}
      and {r["job"] for r in rows} == {f"coupon {n}"
                                       for n in checks.PROGRAM_PHASES},
      str([(r["job"], r["status"], r["ok"]) for r in rows]))
if HAVE_GERBV:
    check("the browser-opened sessions carry the gate's verdict",
          all(r["ok"] is True for r in rows), str([r["ok"] for r in rows]))
else:
    check("the browser-opened sessions stay UNVERIFIED without gerbv",
          all(r["ok"] is None for r in rows), str([r["ok"] for r in rows]))

print("\nPCB VIEWER " + ("FAIL: " + ", ".join(fails) if fails else "PASS"))
sys.exit(1 if fails else 0)
