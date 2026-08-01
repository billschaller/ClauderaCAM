#!/usr/bin/env python3
"""Board B "orbit" — ROUTE and CLOSE (R4b), the pcb-rnd-native pipeline.

ONE deterministic entry point.  `python3 tools-route.py` runs, in order:

  1. GENERATE   tools-board.py's model -> orbit-unrouted.lht + orbit-route.dsn
  2. ROUTE      FreeRouting solves the DSN -> orbit-route.ses, SEALED with the
                digest of the DSN it answers.  FreeRouting 2.2.4 is
                deterministic per byte-exact input, so a matching seal means
                the committed session IS what the router would say again: the
                build REPLAYS it instead of burning a minute proving it.
                Change the board and the digest changes and it reroutes.
  3. MERGE      the session's wires become lihata tracks, its vias become
                WIRE_VIA_STITCHED instances (hplated=1), and every dual-solder
                lead the routed FRONT copper actually lands on is PROMOTED to
                the declared hplated=1 prototype.  Nothing else is ever
                promoted: an undeclared pin the router bridged is a FANTASY
                BRIDGE, named here and left for pcb-rnd to condemn.
  4. EMIT       the SAME generator re-emits the FINAL orbit.lht with that
                copper folded in, so the routed board cannot drift from the
                board the router was shown.
  5. GATES      --gate runs all seven; exit 1 if any fails.

Every promotion is a physical bench joint (solder the lead on the reflow face
too), and every via is a hand-stitched wire.  That is why this file counts them
out loud and why MATRIX.md carries the list to the bench.

Usage:
    python3 tools-route.py            # build the final routed board
    python3 tools-route.py --gate     # build, then judge it
    python3 tools-route.py --reroute  # force FreeRouting even if the seal fits
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ses_parse                                       # noqa: E402


def _load(name: str, path: str):
    """Import a hyphenated sibling script as a module."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


TB = _load("tools_board", os.path.join(HERE, "tools-board.py"))
L = TB.L

JAVA = os.path.expanduser(
    "~/.clauderacam/tools/freerouting/jdk-25.0.4+7-jre/bin/java")
JAR = os.path.expanduser(
    "~/.clauderacam/tools/freerouting/freerouting-2.2.4.jar")

DSN = TB.OUT_DSN
FINAL_LHT = TB.OUT_LHT
UNROUTED_LHT = os.path.join(HERE, "orbit-unrouted.lht")
SES = os.path.join(HERE, "orbit-route.ses")
SEAL = os.path.join(HERE, "orbit-route.seal.json")
RLOG = os.path.join(HERE, "orbit-route.log")
MERGE_JSON = os.path.join(HERE, "orbit-route.merge.json")

LAYER = {"F.Cu": "top", "B.Cu": "bottom"}

# The bench's mandatory joint.  PAD2-1 is the board's ONLY GND through-hole, so
# it is the only conductor that can make the FRONT pour live; without it the
# front plane is a disc of metal attached to nothing.  Promoted whether or not
# the router happens to land front copper on it (SPEC "vias" lever 3).
MANDATORY = {"PAD2-1"}

VIA_BUDGET = 6                           # SPEC "Wire vias" planning number
# The hard ceiling was REPEALED by operator ruling 2026-08-01: "there's
# no need to think of vias as having a hard ceiling ... not much more
# annoying than measuring, cutting, and soldering jumper wires." Vias
# are a minimized, LEDGERED bench cost — reported, never gated.


# ---------------------------------------------------------------------------
# 2. ROUTE — sealed against the DSN it answers
# ---------------------------------------------------------------------------
def digest(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def route(force: bool = False) -> tuple[dict, bool]:
    """-> (seal, did_run).  Replays the committed session when it still fits."""
    dsn_sha = digest(DSN)
    seal = {}
    if os.path.exists(SEAL):
        with open(SEAL, encoding="utf-8") as fh:
            seal = json.load(fh)
    fits = (not force and seal.get("dsn_sha256") == dsn_sha
            and os.path.exists(SES) and digest(SES) == seal.get("ses_sha256"))
    if fits:
        return seal, False

    r = subprocess.run(
        [JAVA, "-Djava.awt.headless=true", "-jar", JAR,
         "-de", DSN, "-do", SES, "-mt", "1"],
        capture_output=True, text=True, timeout=3600)
    log = r.stdout + r.stderr
    with open(RLOG, "w", encoding="utf-8") as fh:
        fh.write(log)
    if not os.path.exists(SES):
        raise SystemExit("FreeRouting wrote no session — see orbit-route.log")
    m = re.search(r"started with (\d+) unrouted nets", log)
    n = re.search(r"final score: [\d.]+ \((\d+) unrouted\)", log)
    open_pairs = re.findall(r"^\s+- (\S+)\s+->\s+(\S+)\s*$", log, re.M)
    seal = {
        "dsn_sha256": dsn_sha,
        "ses_sha256": digest(SES),
        "connections": int(m.group(1)) if m else -1,
        "router_unrouted": int(n.group(1)) if n else -1,
        "router_open_pairs": ["%s -> %s" % p for p in open_pairs],
        "freerouting": "2.2.4 -mt 1 -de orbit-route.dsn -do orbit-route.ses",
    }
    with open(SEAL, "w", encoding="utf-8") as fh:
        json.dump(seal, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return seal, True


# ---------------------------------------------------------------------------
# 3. MERGE — session -> board copper
# ---------------------------------------------------------------------------
def fixed_keys() -> set:
    """The pre-placed protected tracks, keyed for identity.

    FreeRouting echoes every `(type protect)` wire back into the session.  The
    generator already emits those four runs as physical truth, so re-adding
    them from the session would double the copper on the tightest geometry on
    the board."""
    out = set()
    for layer, _net, width, pts in TB.fixed_tracks():
        for a, b in zip(pts, pts[1:]):
            ka, kb = TB.lht_xy(*a), TB.lht_xy(*b)
            out.add((layer, round(width, 3)) + (ka + kb if ka <= kb
                                                else kb + ka))
    return out


def merge(parts: list, ses_path: str) -> dict:
    s = ses_parse.load(ses_path)
    # Scale is recovered from PLACEMENT, never from (resolution ...): the
    # session declares `um 10` and then writes coordinates ten times larger.
    known = {p.ref: TB.lht_xy(p.pins[0].x, p.pins[0].y) for p in parts}
    upm = s.calibrate(known)
    raw_tracks, raw_vias = s.geometry_mm(upm)          # already y-DOWN (lihata)

    unknown = sorted({lay for lay, *_ in raw_tracks} - set(LAYER))
    if unknown:
        raise SystemExit(f"session routes on unknown layers {unknown}")

    fixed, tracks, echoed = fixed_keys(), [], 0
    for lay, x1, y1, x2, y2, w, net in raw_tracks:
        t = (LAYER[lay], TB.q(x1), TB.q(y1), TB.q(x2), TB.q(y2), round(w, 3))
        a, b = (t[1], t[2]), (t[3], t[4])
        if a == b:
            continue                                   # zero-length artefact
        key = (t[0], t[5]) + (a + b if a <= b else b + a)
        if key in fixed:
            echoed += 1
            continue
        tracks.append(t + (net,))
    vias = [(TB.q(x), TB.q(y), net) for x, y, net in raw_vias]

    # --- which dual-solder leads did the routed FRONT copper actually use? ---
    by_pid = {p.pid: p for part in parts for p in part.pins}
    tops = [t for t in tracks if t[0] == "top"]
    hits: dict[str, int] = {}
    for pid, p in by_pid.items():
        if p.kind != "tht":
            continue
        lx, ly = TB.lht_xy(p.x, p.y)
        r = p.shape[2] / 2.0            # inside the ring == landed on the pad
        n = sum(1 for _l, x1, y1, x2, y2, _w, net in tops
                if net == p.net and TB.pt_seg(lx, ly, x1, y1, x2, y2) <= r)
        if n:
            hits[pid] = n
    promoted = {pid for pid in hits if by_pid[pid].dual} | MANDATORY
    fantasy = sorted(pid for pid in hits if not by_pid[pid].dual)

    stitch = sorted([(TB.q(x), TB.q(y)) for x, y, _n in vias] +
                    [TB.lht_xy(by_pid[pid].x, by_pid[pid].y)
                     for pid in promoted])
    return {
        "units_per_mm": upm,
        "declared_resolution": list(s.declared_res or []),
        "echoed_protected": echoed,
        "tracks": [t[:6] for t in tracks],
        "track_nets": [t[6] for t in tracks],
        "top_segments": len(tops),
        "bottom_segments": len(tracks) - len(tops),
        "vias": vias,
        "promoted": sorted(promoted),
        "front_copper_on_leads": sorted(hits),
        "fantasy_bridges": fantasy,
        "stitch_set": [list(p) for p in stitch],
    }


# ---------------------------------------------------------------------------
# 1+4. BUILD
# ---------------------------------------------------------------------------
def build_routed(force_route: bool = False) -> dict:
    b = TB.build(out_lht=UNROUTED_LHT)                 # generator + DSN
    seal, ran = route(force_route)
    m = merge(b["parts"], SES)
    # Drop the stitches the copper does not need.  Cached against the session
    # digest so the normal build is one pass and still byte-deterministic.
    if seal.get("pruned_for") != seal.get("ses_sha256"):
        seal["redundant_vias"] = [list(v) for v in prune_vias(b, m)]
        seal["pruned_for"] = seal["ses_sha256"]
        with open(SEAL, "w", encoding="utf-8") as fh:
            json.dump(seal, fh, indent=1, sort_keys=True)
            fh.write("\n")
    drop = {tuple(v) for v in seal.get("redundant_vias", ())}
    if drop:
        m["vias"] = [v for v in m["vias"] if tuple(v) not in drop]
        by_pid = {p.pid: p for part in b["parts"] for p in part.pins}
        m["stitch_set"] = sorted(
            [[x, y] for x, y, _n in m["vias"]] +
            [list(TB.lht_xy(by_pid[p].x, by_pid[p].y)) for p in m["promoted"]])
    TB.build(route={"tracks": m["tracks"], "vias": m["vias"],
                    "track_nets": m["track_nets"],
                    "promoted": set(m["promoted"])}, out_lht=FINAL_LHT)
    with open(MERGE_JSON, "w", encoding="utf-8") as fh:
        json.dump({k: v for k, v in m.items() if k != "track_nets"},
                  fh, indent=1, sort_keys=True)
        fh.write("\n")
    b["seal"], b["merge"], b["rerouted"] = seal, m, ran
    return b


# ---------------------------------------------------------------------------
# 5. GATES
# ---------------------------------------------------------------------------
def galvanic(board: str) -> tuple[int, bool, int]:
    """-> (rat lines, complete, polygon-clipper failures).

    The clipper count is part of the verdict and not a curiosity: a failed
    subtraction makes pcb-rnd DISCARD the pour silently, so a board that clips
    badly is one whose connectivity answer cannot be trusted either way
    (measured at tools-board.POUR_HOLE_MARGIN)."""
    out = TB.pcb_rnd("AddRats(AllRats)\n", board)
    m = re.search(r"(\d+) rat line", out)
    return (int(m.group(1)) if m else 0,
            "layout is complete" in out,
            out.count("Error while clipping"))


def excellon(board: str, tag: str) -> tuple[list, list]:
    """-> (plated hits, unplated hits) as (x, y) mm, excellon frame."""
    out = {}
    for kind in ("plated", "unplated"):
        out[kind] = os.path.join(HERE, f".{tag}-{kind}.cnc")
    subprocess.run([TB.PCB_RND, "-x", "excellon",
                    "--filename-plated", out["plated"],
                    "--filename-unplated", out["unplated"],
                    "--coord-format", "um", board],
                   capture_output=True, text=True)
    hits = {}
    for kind, path in out.items():
        txt = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
        hits[kind] = sorted((round(float(a), 3), round(float(b), 3)) for a, b in
                            re.findall(r"^X([\d.-]+)Y([\d.-]+)", txt, re.M))
        if os.path.exists(path):
            os.unlink(path)
    return hits["plated"], hits["unplated"]


def via_ledger(m: dict, parts: list | None = None) -> list[str]:
    """One justification line per wire via: the net it serves and the nearest
    terminal on that net, so the operator has a landmark to find it by.  A via
    is a WIRE the operator threads and solders on BOTH faces, so every line
    here is a bench instruction, not a statistic."""
    pins = [(p.pid, p.net) + TB.lht_xy(p.x, p.y)
            for part in (parts or ()) for p in part.pins]
    rows = []
    for i, (x, y, net) in enumerate(sorted(m["vias"],
                                           key=lambda v: (v[2], v[0]))):
        same = sorted((round(math.hypot(px - x, py - y), 2), pid)
                      for pid, pnet, px, py in pins if pnet == net)
        where = f"nearest {net} terminal {same[0][1]} at {same[0][0]} mm" \
            if same else "no terminal on this net"
        rows.append(f"V{i + 1:<2d} ({x:6.3f}, {y:6.3f})  {net:6s}  "
                    f"carries {net} between the faces; {where}")
    return rows


def prune_vias(b: dict, m: dict) -> list:
    """Wire vias the routed copper does not actually need.

    FreeRouting leaves stitches behind that carry no connection.  Each one
    would cost the bench a threaded wire, so every via must earn its place:
    drop it, ask pcb-rnd, and keep it only if a net comes open.  pcb-rnd is
    deterministic, so this verdict is cached in the seal and replayed."""
    probe = os.path.join(HERE, ".prune.lht")

    def rats(vias):
        TB.build(route={"tracks": m["tracks"], "track_nets": m["track_nets"],
                        "vias": vias, "promoted": set(m["promoted"])},
                 out_lht=probe)
        return galvanic(probe)[0]

    base, keep, drop = rats(m["vias"]), list(m["vias"]), []
    for v in list(m["vias"]):
        trial = [x for x in keep if x != v]
        if rats(trial) <= base:
            keep, _ = trial, drop.append(v)
    os.unlink(probe)
    return drop


MATRIX = os.path.join(HERE, "MATRIX.md")


def render(board: str) -> list[str]:
    """Photo-mode PNGs of the FINAL board, one per face.

    Article VI's habit: the picture is rendered FROM the emitted file, never
    from the model that produced it, so it can disagree with the generator and
    say so.  The back view is flipped left-right because that is how the board
    is held when the back is soldered.
    """
    out = []
    for side, flip in (("front", []), ("back", ["--photo-flip-x"])):
        png = os.path.join(HERE, f"render-rnd-{side}.png")
        subprocess.run([TB.PCB_RND, "-x", "png", "--outfile", png,
                        "--dpi", "600", "--photo-mode"] + flip + [board],
                       capture_output=True, text=True)
        out.append(png if os.path.exists(png) else f"{png} MISSING")
    return out


def write_matrix(b: dict) -> None:
    """Regenerate MATRIX.md — the bench's copy of what this board expects.

    Everything here is DERIVED from the same model the copper came from, so the
    assembly card cannot drift from the board the way a hand-kept table would.
    """
    m, parts = b["merge"], b["parts"]
    net_of = {p.pid: p.net for part in parts for p in part.pins}
    by_ref = {p.ref: p for p in parts}
    L = ["# Board B \"orbit\" — assembly matrix (R4b)", "",
         f"Generated by `tools-route.py` from `orbit.lht` / `orbit-route.ses` "
         f"(session sealed to DSN `{b['seal']['dsn_sha256'][:12]}`).", "",
         "## Charlieplex ring", "",
         "Ring positions run CLOCKWISE from 12 o'clock.  The position->LED "
         "permutation is the SPEC DEFAULT matrix, inherited unchanged from "
         "R4a: SPEC Decision 5 grants R4b the freedom to re-permute, and R4b "
         "measured that no permutation removes the crossing problem — the six "
         "line-pairs are the edges of K4, and four vertices cannot each own a "
         "contiguous arc of a 6-cycle, so six two-track corridors are forced "
         "whatever the order.  The firmware table follows this column.", "",
         "| pos | angle | LED | anode line | cathode line | resistor | pkg |",
         "|----:|------:|:----|:-----------|:-------------|:---------|:----|"]
    for pos in range(1, 13):
        led = TB.POS_LED[pos]
        L.append(f"| {pos} | {TB.pos_angle(pos) % 360:.0f}° | LED{led} | "
                 f"{net_of.get(f'LED{led}-2', '?')} | "
                 f"{net_of.get(f'R{led}-2', '?')} | R{led} | "
                 f"{TB.RES_PKG[led]} |")
    L += ["", "## Wire vias — hand-stitched, one threaded wire each", "",
          f"Ø{TB.HOLE_VIA} hole, Ø{TB.RING_VIA} ring, `WIRE_VIA_STITCHED`, "
          f"hplated=1.  SPEC planning budget {VIA_BUDGET}, no hard ceiling "
          f"(operator ruling 2026-08-01: a via ~ a jumper wire); "
          f"this board spends **{len(m['vias'])}**.", ""]
    L += [f"- `{r}`" for r in via_ledger(m, parts)] or ["- none"]
    if b["seal"].get("redundant_vias"):
        L += ["", f"{len(b['seal']['redundant_vias'])} further via(s) the "
              "router left behind were removed after pcb-rnd confirmed they "
              "carry no connection."]

    prom = [p for p in m["promoted"]]
    L += ["", "## DUAL-SOLDER LEADS — the front-side bench work list", "",
          f"**{len(prom)} leads** must be soldered on the FRONT face as well "
          "as the back.  Each one is real copper the routed board depends on: "
          "there is no plating on a milled board, so an unsoldered lead here "
          "is an open circuit, not a cosmetic miss.", "",
          "> **Seat every LED proud.** A lead can only be soldered on the "
          "front if the LED body stands off the board far enough to get an "
          "iron tip and solder onto the ring. Seat the bodies ~1.5 mm proud "
          "(a scrap of 1.5 mm stock under each body works), solder the BACK "
          "first, then the front rings listed here.", "",
          "| lead | net | what the front joint carries |",
          "|:-----|:----|:-----------------------------|"]
    for pid in sorted(prom):
        net = net_of.get(pid, "?")
        why = ("the ONLY GND through-hole on the board — without this joint "
               "the entire FRONT pour is dead copper" if pid == "PAD2-1"
               else f"layer bridge: routed FRONT copper on {net} reaches the "
                    f"back through this lead")
        L.append(f"| {pid} | {net} | {why} |")
    p1, p2 = by_ref["PAD1"].pins[0], by_ref["PAD2"].pins[0]
    L += ["", "## Power pads — READ BEFORE WIRING", "",
          f"**PAD1 is `+` and is the RIGHT-HAND pad (x {p1.x}); PAD2 is `-` "
          f"and is the LEFT-HAND pad (x {p2.x}).** This TRANSPOSES the layout "
          "note in SPEC, and the silk `+`/`-` legend follows the copper, not "
          "the note. The transposition is forced, not cosmetic: the VBAT "
          "corridor runs rightward from the `+` pad to SW1 and needs "
          f"{TB.RING_SW1 / 2 + TB.CLEAR + TB.RAIL / 2:.2f} mm of vertical room "
          "under SW1's blade, which the strip only has on that side.", "",
          f"Reversing them puts the battery backwards across {by_ref['Q1'].ref}"
          " and C1.", ""]
    with open(MATRIX, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


# The pin the negative control lies about.  BZ1-1 is a THT lead the bench can
# only reach on the BACK — the buzzer's body sits on the front, over it — so it
# is exactly the kind of hole a careless model would call "through".
FANTASY = "BZ1-1"
NEG_DSN = os.path.join(HERE, ".neg-route.dsn")
NEG_SES = os.path.join(HERE, ".neg-route.ses")
NEG_LHT = os.path.join(HERE, ".neg-route.lht")
NEG_SEAL = os.path.join(HERE, ".neg-route.seal.json")


def negative_control(b: dict, honest_rats: int) -> tuple:
    """R3's gate D: describe ONE unsolderable lead to the router as a through
    pin, let it use the bridge, then build the board the BENCH can actually
    make — which does not have it — and let pcb-rnd condemn the result.

    Promotion is never the router's decision.  This proves the refusal is worth
    something: without it the board would ship claiming a connection that only
    exists in the DSN.
    """
    keep = TB.FANTASY_PIN
    try:
        TB.FANTASY_PIN = FANTASY
        with open(NEG_DSN, "w", encoding="utf-8") as fh:
            fh.write(TB.emit_dsn(b["parts"], b["nets"]))
    finally:
        TB.FANTASY_PIN = keep
    seal = {}
    if os.path.exists(NEG_SEAL):
        with open(NEG_SEAL, encoding="utf-8") as fh:
            seal = json.load(fh)
    if not (seal.get("dsn_sha256") == digest(NEG_DSN) and os.path.exists(NEG_SES)):
        subprocess.run([JAVA, "-Djava.awt.headless=true", "-jar", JAR,
                        "-de", NEG_DSN, "-do", NEG_SES, "-mt", "1"],
                       capture_output=True, text=True, timeout=3600)
        with open(NEG_SEAL, "w", encoding="utf-8") as fh:
            json.dump({"dsn_sha256": digest(NEG_DSN)}, fh, indent=1)
    m2 = merge(b["parts"], NEG_SES)
    # The board is emitted as PHYSICAL TRUTH: BZ1-1 keeps its back-only ring,
    # because that is the hole the operator will actually hold.
    TB.build(route={"tracks": m2["tracks"], "track_nets": m2["track_nets"],
                    "vias": m2["vias"], "promoted": set(m2["promoted"])},
             out_lht=NEG_LHT)
    rats, complete, _clip = galvanic(NEG_LHT)
    return m2["fantasy_bridges"], rats, complete


def gate(b: dict) -> int:
    m, seal = b["merge"], b["seal"]
    parts, nets = b["parts"], b["nets"]
    npass = nfail = 0
    route = {"tracks": m["tracks"], "track_nets": m["track_nets"],
             "vias": m["vias"], "promoted": set(m["promoted"])}

    def chk(label, got, want):
        nonlocal npass, nfail
        if got == want:
            npass += 1
            print(f"  [PASS] {label} ({got})")
        else:
            nfail += 1
            print(f"  [FAIL] {label} (got {got!r}, want {want!r})")

    print("### A. GALVANIC — pcb-rnd is the oracle ###")
    rats, complete, clip = galvanic(FINAL_LHT)
    print(f"    {seal['connections']} connections offered to the router, "
          f"{seal['router_unrouted']} it admits open")
    print(f"    pcb-rnd on the FINAL board: {rats} rat lines, "
          f"complete={complete}, clipper failures={clip}")
    chk("the pour survives every polygon subtraction", clip, 0)
    chk("every net complete, no shorted nets", complete and rats == 0, True)

    print("### B. THE LAWS — pcb-rnd DRC and an independent oracle ###")
    viol = [ln for ln in TB.run_drc(FINAL_LHT).splitlines()
            if re.match(r"^\d+: ", ln)]
    for v in viol[:10]:
        print(f"    {v}")
    chk("clearance 0.4 / ring 0.7 / drill 1.0 all clean", len(viol), 0)
    worst, bad = TB.clearance_scan(parts, route)
    for x in bad[:8]:
        print(f"    {x}")
    print(f"    tightest different-net gap in the ROUTED board: {worst:.3f} mm")
    chk("independent scan: every different-net gap >= 0.40", len(bad), 0)
    pworst, pbad = TB.pour_hole_scan(parts, route)
    print(f"    closest any copper LINE's pour cutout comes to a pour hole: "
          f"{pworst:+.3f} (band {TB.POUR_HOLE_DEEP:+.2f}..{TB.POUR_HOLE_MARGIN:+.2f})")
    chk("no line grazes a pour hole (the plane-killer)", len(pbad), 0)

    print("### C. THE PLATED PROGRAM IS THE STITCH LIST ###")
    plated, unplated = excellon(FINAL_LHT, "gate")
    want = sorted((round(x, 3), round(TB.BOARD_H - y, 3))
                  for x, y in m["stitch_set"])
    print(f"    plated hits {len(plated)}, stitch set {len(want)} "
          f"(= {len(m['vias'])} wire via + {len(m['promoted'])} dual-solder)")
    if plated != want:
        print(f"      plated: {plated[:6]}\n      want  : {want[:6]}")
    chk("plated excellon == the stitch set, coordinate for coordinate",
        plated == want, True)
    # Everything the operator drills that is NOT stitched: the THT leads left
    # back-only, plus the bare bores (4x M3 mount, 4x flip gauge), which carry
    # no copper on either face and so can never be plated.
    tht = sum(1 for p in parts for pin in p.pins if pin.kind == "tht")
    bores = len(TB.MOUNTS) + len(TB.GAUGES)
    chk("unplated program holds every remaining THT lead and bare bore",
        len(unplated), tht - len(m["promoted"]) + bores)

    print("### D. NEGATIVE CONTROL — a fantasy bridge must NOT ship ###")
    print(f"    the DSN is corrupted to call {FANTASY} an ordinary through "
          f"pin; the bench can only reach that lead on the back.")
    fant, nrats, ncomplete = negative_control(b, rats)
    print(f"    merge names: {fant}")
    print(f"    pcb-rnd on the corrupted board: {nrats} rat lines, "
          f"complete={ncomplete}  (honest board: {rats}, {complete})")
    chk("the merge names the fantasy bridge and refuses to promote it",
        fant, [FANTASY])
    chk("pcb-rnd independently condemns the board built on it",
        (not ncomplete) and nrats > rats, True)

    print("### E. VIA LEDGER ###")
    for row in via_ledger(m, parts):
        print(f"    {row}")
    if seal.get("redundant_vias"):
        print(f"    (pruned {len(seal['redundant_vias'])} via(s) that carried "
              f"no connection: {seal['redundant_vias']})")
    chk(f"every wire via ledgered ({len(m['vias'])} total; no ceiling — "
        f"operator ruling 2026-08-01)",
        len(via_ledger(m, parts)) == len(m["vias"]), True)
    if len(m["vias"]) > VIA_BUDGET:
        print(f"    NOTE: {len(m['vias'])} vias vs SPEC's planning budget of "
              f"{VIA_BUDGET}; each is one threaded-wire bench joint pair, "
              f"cost-class of a jumper wire (ruling 2026-08-01).")

    print("### G. POURS ###")
    gnd_prom = sorted(p for p in m["promoted"]
                      if {pin.pid: pin for part in parts
                          for pin in part.pins}[p].net == "GND")
    chk("the front pour's only through-hole conductor is PAD2-1",
        gnd_prom, ["PAD2-1"])
    # The front pour carries no TRAFFIC — every GND terminal but PAD2-1 is a
    # back-side land — so no rat-line differential can show PAD2-1 doing work,
    # and a probe that demoted it and expected more rats was asking pcb-rnd a
    # question it cannot answer.  What must be true instead is MEMBERSHIP: the
    # front plane has to be soldered to the GND network through that one lead.
    # That is a fact about the emitted bytes, so it is read from them.
    txt = open(FINAL_LHT, encoding="utf-8").read()
    pad2 = [p for p in parts if p.ref == "PAD2"][0]
    blk = re.search(r"ha:padstack_ref\.%d \{(.*?)\n   \}" % (pad2.oid + 10),
                    txt, re.S)
    body = blk.group(1) if blk else ""
    chk("PAD2-1 is emitted on the stitched (hplated=1) prototype",
        bool(re.search(r"proto = 1\b", body)), True)
    chk("...and thermals into BOTH pours, which is what makes the front live",
        bool(re.search(r"li:0 \{ on; round; \}", body))
        and bool(re.search(r"li:1 \{ on; round; \}", body)), True)
    # Back pour: how much of GND actually leans on the fill?
    probe = os.path.join(HERE, ".pour.lht")
    nop = txt.replace(re.search(r"     ha:polygon\.30001 \{.*?\n     \}",
                                txt, re.S).group(0), "")
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write(nop)
    lost = galvanic(probe)[0]
    print(f"    deleting the BACK pour: {rats} rat lines -> {lost}"
          f"  ({lost - rats} GND terminal(s) reach the network only through "
          f"the fill)")
    chk("the back pour is a real conductor, not decoration", lost > rats, True)
    os.unlink(probe)

    print("### F. DETERMINISM ###")
    sums = []
    for _ in range(2):
        for f in (FINAL_LHT, UNROUTED_LHT):
            if os.path.exists(f):
                os.unlink(f)
        build_routed()
        sums.append(hashlib.md5(open(FINAL_LHT, "rb").read()).hexdigest())
    chk("two clean rebuilds give one byte-identical orbit.lht",
        len(set(sums)), 1)

    print("### OUTPUTS ###")
    write_matrix(b)
    print(f"    {MATRIX}")
    for p in render(FINAL_LHT):
        print(f"    {p}")

    print(f"\n### {npass}/{npass + nfail} checks passed, {nfail} failed ###")
    return 1 if nfail else 0


if __name__ == "__main__":
    bb = build_routed("--reroute" in sys.argv)
    mm = bb["merge"]
    print(f"orbit.lht          {os.path.getsize(FINAL_LHT):7d} bytes  (FINAL)")
    print(f"orbit-route.ses    {os.path.getsize(SES):7d} bytes  "
          f"{'ROUTED' if bb['rerouted'] else 'replayed from seal'}")
    print(f"  scale {mm['units_per_mm']:.0f} units/mm (declared "
          f"{mm['declared_resolution']}) — placement-calibrated")
    print(f"  {mm['top_segments']} front + {mm['bottom_segments']} back "
          f"segments, {mm['echoed_protected']} protected echoes dropped")
    print(f"  {len(mm['vias'])} wire vias, "
          f"{len(mm['promoted'])} promoted leads: "
          f"{' '.join(mm['promoted'])}")
    if mm["fantasy_bridges"]:
        print(f"  FANTASY BRIDGES (never promoted): {mm['fantasy_bridges']}")
    sys.exit(gate(bb) if "--gate" in sys.argv else 0)
