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

import pathfind                                        # noqa: E402
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

# FreeRouting must be BOUNDED on this board, and the bound is MEASURED, not a
# precaution.  Once the LED anodes stopped being layer bridges (2026-08-01
# ruling) the router improves monotonically for eight passes — 19 unrouted at
# #1, 18, 12, 13, 9, 13, 8, and 8 at #8, which is the same figure the bridged
# board finished with — and then WEDGES in pass #9 and never returns.  Left
# unbounded it burns an hour and writes nothing at all (our own subprocess
# timeout is what killed it), and because -do only writes on completion, a
# wedged run costs the whole session.
#
# Everything else was tried first and none of it helps: the dead rings as
# keepouts vs. as visible copper (23 unrouted either way, wedge in pass #2-3),
# cheap vias and cheap ripup, the router's own JOB_TIMEOUT (only honoured
# BETWEEN passes, so it never fires inside the wedge), disabling the route
# optimizer, and -oit.  Only stopping at the last good pass returns a session.
#
# ...and then the wedge went away, so the bound went OFF (0 = no limit).
# Pushing the ring resistors out to the ceiling the cathode ring allows
# (RES_OUTER, 2026-08-02) widened the interior annulus by 0.23-0.43 mm, and on
# that board FreeRouting ran to its OWN stopping rule in 19 passes and 5
# unrouted — it never wedged at all.  The bound was kept as a named constant
# rather than deleted, "because the wedge is a property of the geometry and the
# next change to this board can bring it back".
#
# IT CAME BACK, one working day later, exactly as predicted — and the bound is
# STILL OFF, because the fix was to move the GEOMETRY, not to truncate the
# router.  Recording that, because truncation was tried first and it is a trap.
#
# The SCRUB growth (tools-board.SCRUB_RING, 2026-08-02) wedged FreeRouting at
# pass #4-#6 in every pitch arrangement that merely satisfied the clearance
# law.  Bounding the passes at the last one that COMPLETES does return a
# session, exactly as the paragraph above promises — but that session was 11
# connections short, and a route that short is not a route.  MEASURED on it:
# the closer spends its whole 12-closure budget (38 segments) and the gate
# still reads 3 rat lines, an "overlapping holes" DRC hit, and a pour cutout
# 0.137 mm INSIDE a mount keep-out, which is the plane-killer band.  A bound
# keeps the PIPELINE running; it does not keep the BOARD correct, and the
# difference is invisible unless you look at what the closer had to invent.
#
# So the wedge was treated as what this file already calls it — a property of
# the geometry — and the LED anode radius was swept until FreeRouting converged
# on its own (full table at tools-board.LEAD_R_OUT; convergence is NOT
# monotonic in the pitch, it switches back on at 18.59 and improves after).  At
# LEAD_R_OUT 18.67 the router runs to its OWN stopping rule in 30 passes and 4
# unrouted, matching the baseline exactly.  No truncation, no residue.
#
# The signature is worth writing down for the next person to hit it: every
# healthy pass on this board finishes in under 2.5 seconds, so a pass still
# running after ten is not slow, it is wedged, and no amount of waiting helps
# (-do only writes on completion, so a wedged run costs the whole session).
# Probe with ONE unbounded run and read the pass log.  If a bound is ever
# genuinely needed, set it to the last pass that completes AND look at what the
# closer is left holding before believing the result.
#
# The subprocess timeout below is the other half of that lesson.  It used to
# be an hour, which is how a wedged run cost a full hour AND the session file
# (-do only writes on completion, so a killed run leaves nothing).  Five
# minutes is far past this board's 25 s and fails loudly instead.
ROUTER_MAX_PASSES = "0"        # 0 = no limit (FreeRouting's own encoding)


def router_env() -> dict:
    env = dict(os.environ)
    env["FREEROUTING__ROUTER__MAX_PASSES"] = ROUTER_MAX_PASSES
    return env

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

# ---------------------------------------------------------------------------
# DECLARED BENCH JUMPERS — the [[rules.gauge]] pattern, applied to connectivity
# ---------------------------------------------------------------------------
# A flip gauge is copper this board deliberately builds OUTSIDE a law (0.35
# annulus against a 0.70 ring law) and gets away with it by DECLARING the
# exception: the number is named, the reason is written down, the count is
# fixed, and the gate checks the declaration rather than waiving the law.  A
# bench jumper is the same shape of thing for connectivity — a connection the
# COPPER does not make and a WIRE does — so it is declared the same way.
#
# THE MIGRATION FINDING, which is what earns this exception.  After the scrub
# growth the board sits exactly ONE routable connection short, and which
# connection is short is a property of the SEED SET, not of any one net.
# MEASURED, four seed sets, each rerouted from scratch:
#
#     seeds                                  vias  residual open connection
#     (C2-1)                                  24   GND   Q2-2/C3-2 cell
#     (C2-1, Q2-2)                            25   RESET U1-1 -> TP5-1
#     (C2-1, Q2-2, U1-1)                      31   VCC   rail -> C3-1
#     (C2-1, Q2-2, U1-1, U1-2, TP1-1, C3-1)   28   L0    see below
#
# Every seed closed the connection it was aimed at and the deficit MOVED.  That
# is not a defect in any of those nets; it is one global shortfall wearing a
# different name each time, and chasing it with more seeds is a treadmill.
#
# So the shortfall is SPENT DELIBERATELY, on the pair of terminals that is
# easiest to solder — because the one thing this board gets to choose is WHERE
# the wire goes, and a jumper between two bare rings is a different object from
# a jumper between two SOIC pins.  The route is SEALED against its DSN, so the
# residual is stable: the same board comes back every build, and this
# declaration keeps meaning what it says.
#
# WHY THESE TWO.  The four artwork fixes of 2026-08-02 (the pour's 1.10 setback
# and the mount/gauge moves it forced, U1's widened lands) changed the routing
# problem enough that the shortfall grew from one connection to two, and a
# seventh seed aimed at closing one of them WEDGED the router (recorded at
# tools-board.SEEDED_ESCAPES).  So the board spends two wires instead of one.
#
# Each lands on a pair chosen by SOLDERABILITY first and length second, and on
# this route both come out entirely bare — no SOIC pin, no 0603 land anywhere:
#
#   L2  piece 0 = LED4/8/11-2 rings + S2-1/1B rings + R7-2 land
#       piece 1 = TP3-1 alone       -> LED11-2 ring to TP3-1 pad, 24.50 mm
#   L3  piece 0 = LED6/10/12-2 rings + R5/R9/R11-2 lands
#       piece 1 = S1-1 + S1-1B      -> LED12-2 ring to S1-1B ring, 27.41 mm
#
# An LED anode ring is soldered on the BACK — exactly where the operator
# already puts an iron for that lead, with the body out of the way on the front
# — and S1's leg rings and the ISP pads are bare by construction.  The SMD
# alternatives were shorter in one case and are not taken: this declaration is
# spending ease-of-assembly, not millimetres.
#
# Both wires lie flat on the BACK face for their whole run and cross no front
# component.  22 AWG solid, the same wire the stitched vias use (Board A
# shipped seven of these).
# EMPTY ON THE GROWN BOARD (66 x 56, 2026-08-02), and that is the growth roll's
# second dividend.  Everything above is the record of the 64 x 54 board, which
# was one — then two — routable connections short; this board is short of
# NOTHING.  pcb-rnd on the final layout: 0 rat lines, `layout is complete`, 0
# clipper failures.
#
# The two wires retired here were L2 (LED11-2 -> TP3-1, 24.50 mm) and L3
# (LED12-2 -> S1-1B, 27.41 mm).  They are not deleted quietly: a stale
# declaration is exactly the hazard jumper_audit's first question exists to
# catch ("is every declared jumper still NEEDED"), and leaving these two in
# place would have made the gate say STALE rather than let them rot.
#
# WHAT PAID FOR THEM was not the outline directly but the room it bought the
# router: two more escape seeds (tools-board.SEEDED_ESCAPES, U1-3 and TP5-1),
# the seventh of which is the one that WEDGED FreeRouting on the old board and
# routes it in 54 s on this one.  The full migration table is at that constant.
# Net bench cost: 23 threaded wire vias and one dual-solder lead, against the
# old board's 23 vias + 1 lead + 2 jumper wires.
#
# THE MECHANISM STAYS ARMED.  This is a declaration, not a repair, and the next
# change to this board can put a connection back out of reach — in which case
# the entry goes back here, named, with its two BARE terminals and its reason,
# and the gate goes back to judging the declaration instead of the law.
DECLARED_JUMPERS = ()

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
        capture_output=True, text=True, timeout=300, env=router_env())
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
        "freerouting": "2.2.4 -mt 1 -de orbit-route.dsn -do orbit-route.ses"
                       " FREEROUTING__ROUTER__MAX_PASSES=" + ROUTER_MAX_PASSES,
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


BOT = frozenset(("bottom",))
BOTH = frozenset(("top", "bottom"))
RAIL_NETS = {"VCC", "VBAT", "VSW"}
MAX_CLOSURES = 12

# Nets whose copper WELDS into a pour (emit_lihata: clearpoly False for GND)
# and are therefore already joined by metal net_copper cannot see.
#
# This set is what stops the long-haul tier from inventing copper, and it cost
# RESET a corridor to learn.  net_copper models pads, tracks and vias — never
# the fill — so U1-4 has ALWAYS looked like a separate GND island to
# closing_tracks.  Tiers 1-2 could not reach it and quietly gave up, which was
# right: gate G measures that terminal reaching the network through the fill.
# The long-haul tier CAN reach it, and did — five segments across the ring
# interior, one of them along y 33.0, one millimetre over U1-1's land.  It
# closed nothing that was open and walled in the pin this whole exercise is
# about.  A conductor the board already has is not a rat line.
POUR_WELDED_NETS = {"GND"}


def net_copper(net: str, parts: list, tracks: list, vias: list,
               promoted: set) -> list:
    """Every piece of *net*'s copper as (faces, points, radius), board frame.

    `faces` is a SET because a wire via and a promoted lead are ONE conductor on
    two faces — that is the entire reason both exist — so they bridge the faces
    here the way the operator's solder bridges them on the bench.  An
    UNPROMOTED through lead does not bridge: its front ring is dead copper
    belonging to no net (the R3 finding), so it contributes its back ring only.
    Getting that distinction wrong would let this code believe in connections
    the board does not have, which is the exact failure the plating model
    exists to prevent.
    """
    H, objs = TB.BOARD_H, []
    for part in parts:
        for p in part.pins:
            if p.net != net:
                continue
            if p.kind == "rect":
                objs.append((BOT, p.corners(), 0.0))
            elif p.kind == "circ":
                objs.append((BOT, [(p.x, p.y)], p.shape[1] / 2))
            else:
                objs.append((BOTH if p.pid in promoted else BOT,
                             [(p.x, p.y)], p.shape[2] / 2))
    for t in tracks:
        if t[6] == net:
            objs.append((frozenset((t[0],)),
                         [(t[1], H - t[2]), (t[3], H - t[4])], t[5] / 2))
    for lay, n, w, pts in TB.fixed_tracks() + TB.board_only_tracks():
        if n == net:
            for a, b in zip(pts, pts[1:]):
                objs.append((frozenset((lay,)), [a, b], w / 2))
    for vx, vy, vn in vias:
        if vn == net:
            objs.append((BOTH, [(vx, H - vy)], TB.RING_VIA / 2))
    return objs


def components(objs: list) -> list:
    """Union-find over copper that TOUCHES (gap <= 0) on a shared face."""
    up = list(range(len(objs)))

    def find(i):
        while up[i] != i:
            up[i] = up[up[i]]
            i = up[i]
        return i

    for i, (fa, pa, ra) in enumerate(objs):
        for j in range(i + 1, len(objs)):
            fb, pb, rb = objs[j]
            if (fa & fb) and TB.shape_gap((pa, ra), (pb, rb)) <= 0.0:
                up[find(i)] = find(j)
    groups: dict = {}
    for i in range(len(objs)):
        groups.setdefault(find(i), []).append(i)
    return [groups[k] for k in sorted(groups, key=lambda k: min(groups[k]))]


def anchor(obj, toward) -> tuple:
    """A point ON *obj* to land a track on, nearest *toward*."""
    faces, pts, r = obj
    if len(pts) == 1:
        return pts[0]
    if len(pts) == 2:
        (ax, ay), (bx, by) = pts
        dx, dy = bx - ax, by - ay
        n = dx * dx + dy * dy
        t = 0.0 if n == 0 else max(0.0, min(
            1.0, ((toward[0] - ax) * dx + (toward[1] - ay) * dy) / n))
        return (TB.q(ax + t * dx), TB.q(ay + t * dy))
    return (TB.q(sum(p[0] for p in pts) / len(pts)),
            TB.q(sum(p[1] for p in pts) / len(pts)))


def closing_tracks(parts: list, tracks: list, vias: list,
                   promoted: set) -> list:
    """-> the copper that FINISHES what the router leaves open, LIHATA frame.

    R4b's endgame lever, and the operator sanctioned it in as many words: the
    autorouter is a tool, not an authority.  When it stops with a net lying in
    two pieces, the board data is ours to finish — and the galvanic gate, not
    this function, is what decides whether the finish is real.

    The method is measurement, not cleverness.  For every net: build its copper,
    union-find the pieces that actually TOUCH, and while more than one piece
    remains, offer the shortest paths between the two nearest pieces to the same
    independent clearance oracle the gate uses.  A path that cannot make 0.40 mm
    against every other net is never drawn; if nothing legal exists the net
    stays open and pcb-rnd says so, which is the honest outcome — a closure that
    only exists because a check was skipped is worth nothing.

    Deterministic by construction: nets in sorted order, components keyed by
    their lowest object index, candidate paths generated in a fixed sequence and
    chosen by (length, -clearance, coordinates).  Power nets get RAIL width
    because they carry the battery.
    """
    route = {"tracks": [t[:6] for t in tracks], "vias": vias,
             "track_nets": [t[6] for t in tracks], "promoted": promoted}
    others = TB.copper_objects(parts, route)

    # THE M3 COPPER KEEP-OUTS, AS OBSTACLES THE CLOSER CAN SEE.
    #
    # MEASURED 2026-08-02, and it was a real hole in this stage rather than a
    # tidy-up.  The ROUTER is told about these keep-outs — emit_dsn writes one
    # per mount at ROUTE_MOUNT_KEEPOUT_R — but the CLOSER was not, so a closing
    # track could be laid straight through a mount bore.  The gate DETECTS that
    # (pour_hole_scan, the plane-killer check: a copper line whose pour cutout
    # grazes a punched hole makes pcb-rnd's boolean fail and SILENTLY DISCARD
    # the whole pour), but detection after the fact only converts a bad board
    # into a failed build.  It fired for real on the scrub-growth board: a
    # closure landed 0.245 mm inside H4's keep-out, and an earlier attempt put
    # one at 0.137.  Both were legal by every clearance test this function ran,
    # because no test knew the hole was there.
    #
    # The radius is ROUTE_MOUNT_KEEPOUT_R - CLEAR, chosen so that this stage's
    # own `clears(...) >= CLEAR` reproduces pour_hole_scan's arithmetic exactly:
    #
    #   dist >= w/2 + CLEAR + (MOUNT_KEEPOUT_R + COPPER_CLEAR
    #                          + POUR_HOLE_MARGIN - CLEAR)
    #        == w/2 + COPPER_CLEAR + MOUNT_KEEPOUT_R + POUR_HOLE_MARGIN
    #
    # which IS the scan's safe side.  Tiers that demand more (COPPER_CLEAR,
    # ROUTE_CLEAR) are simply stricter, and pathfind's Grid stamps the same
    # list, so tier 3's journeys inherit it for free.  One obstacle list, one
    # law, every tier — rather than a new check bolted onto each.
    MOUNT_KEEPOUT = "__mount_keepout"
    others = others + [(MOUNT_KEEPOUT, lay, [(mx, my)],
                        TB.ROUTE_MOUNT_KEEPOUT_R - TB.CLEAR)
                       for mx, my in TB.MOUNTS.values()
                       for lay in ("top", "bottom")]
    out, new_vias, H = [], [], TB.BOARD_H
    unclosed: list = []

    def nearby(pa, pb, layer, net, pad=7.0):
        """The only copper a path between pa and pb can possibly hit.  A spatial
        prefilter, not an approximation: everything outside this box is further
        than the box margin from every candidate, and the candidates never leave
        it."""
        x0, x1 = min(pa[0], pb[0]) - pad, max(pa[0], pb[0]) + pad
        y0, y1 = min(pa[1], pb[1]) - pad, max(pa[1], pb[1]) + pad
        keep = []
        for n2, l2, p2, r2 in others:
            if l2 != layer or n2 == net or n2 is None:
                continue
            xs = [p[0] for p in p2]
            ys = [p[1] for p in p2]
            if (max(xs) + r2 < x0 or min(xs) - r2 > x1
                    or max(ys) + r2 < y0 or min(ys) - r2 > y1):
                continue
            keep.append((p2, r2))
        return keep

    def clears(pts, near, w):
        worst = 99.0
        for a, b in zip(pts, pts[1:]):
            for p2, r2 in near:
                g = TB.shape_gap(([a, b], w / 2), (p2, r2))
                if g < worst:
                    worst = g
                    if worst < TB.CLEAR:
                        return worst
        return worst

    # ---- THE UN-MILLABLE SLIVER RULE (2026-08-02) ------------------------
    # A closure may run as close to its OWN net's copper as it likes and as far
    # as it likes, but it may not park in the narrow band between: a channel
    # too thin for the isolation bit to cut, yet too wide to be no channel at
    # all.  MEASURED, and this rule is written from the incident:
    #
    #   The closer put an L0 closure (5.75,20.00)->(6.50,25.25) w0.6 alongside
    #   FreeRouting's own L0 trace at x 5.60 w0.62.  Centres 0.804 apart, half
    #   widths 0.61, so 0.194 mm of bare laminate between two traces of the
    #   SAME net.  Every clearance oracle passed it — correctly, because
    #   same-net copper has no clearance law — and the double-sided gate
    #   refused the board anyway on `iso coverage` 0.0850 against a 0.08 bar,
    #   worst uncut at board (6.005,24.575).
    #
    #   The mechanism is FlatCAM's, not ours: the isolation centreline is the
    #   ring standing tip_r outside copper, and when two such rings MERGE the
    #   engine rides the medial line and cuts one pass where two were needed.
    #   The rings merge exactly when the channel is narrower than 2*tip_r, i.e.
    #   the tip diameter.  orbit.toml's iso tool is the 30-degree vee with
    #   tip_diameter 0.2, so 0.194 is 0.006 inside the merge condition.
    #
    # Electrically the sliver is harmless — same net, and the unmilled laminate
    # simply leaves the two traces joined, which they already were.  What it
    # costs is a gate that can no longer certify the isolation pass covered the
    # board, and that is not a certificate this lane hands out on trust.
    ISO_TIP_DIA = 0.2          # orbit.toml [[tool]] T2 tip_diameter
    # 0.22 = the tip diameter plus a tenth of itself.  0.30 was tried first and
    # is MEASURED to be too wide a veto: closures on this board routinely run
    # alongside their own net at 0.2-0.3, and banning that band outright cost a
    # whole net's worth of closures (three bench jumpers instead of two, 19
    # wire vias instead of 23).  The bar belongs just above the merge
    # condition, which is where the physics is, not wherever feels safe.
    SLIVER_MIN = ISO_TIP_DIA * 1.1     # clear the merge condition, not tie it

    def same_near(pa, pb, layer, net, pad=7.0):
        """Same-net copper on this face near the run — the objects `nearby`
        deliberately drops, kept here because they bind a different law."""
        x0, x1 = min(pa[0], pb[0]) - pad, max(pa[0], pb[0]) + pad
        y0, y1 = min(pa[1], pb[1]) - pad, max(pa[1], pb[1]) + pad
        keep = []
        for n2, l2, p2, r2 in others:
            if l2 != layer or n2 != net:
                continue
            xs = [p[0] for p in p2]
            ys = [p[1] for p in p2]
            if (max(xs) + r2 < x0 or min(xs) - r2 > x1
                    or max(ys) + r2 < y0 or min(ys) - r2 > y1):
                continue
            keep.append((p2, r2))
        return keep

    # A run LEAVING its own net's copper must cross the band — that is
    # unavoidable geometry, and a transverse crossing is harmless because the
    # channel it makes is a few tenths long and the bit never has to enter it.
    # What ruins the isolation pass is DWELLING in the band: running nearly
    # parallel to the neighbour for millimetres, which is exactly what the L0
    # closure does (it leaves the trace overlapping at y 20 and creeps out to
    # 0.194 by y 24.6).  So the sliver test measures LENGTH inside the band,
    # not distance at a point — the first version measured the per-object
    # minimum, which is <= 0 for any run that touches, and that is why five
    # rebuilds in a row came back byte-identical.
    SLIVER_RUN_MAX = 1.0       # mm of near-parallel travel we will tolerate

    def sliver_len(pts, p2, r2, w, step=0.05):
        """How far *pts* travels while inside the un-millable band of one
        same-net object."""
        total = 0.0
        for a, b in zip(pts, pts[1:]):
            d = math.dist(a, b)
            n = max(1, int(d / step))
            for i in range(n):
                m0 = (a[0] + (b[0] - a[0]) * i / n,
                      a[1] + (b[1] - a[1]) * i / n)
                m1 = (a[0] + (b[0] - a[0]) * (i + 1) / n,
                      a[1] + (b[1] - a[1]) * (i + 1) / n)
                g = TB.shape_gap(([m0, m1], w / 2), (p2, r2))
                if 0.0 < g < SLIVER_MIN:
                    total += d / n
        return total

    def sliver_ok(pts, near_same, w):
        """True unless some SAME-NET object sits in the un-millable band.

        The test is PER OBJECT and on that object's CLOSEST approach, and both
        halves of that matter:

          * per object, not over the whole set — a closure ends ON its net's
            copper by construction, so the set minimum is always <= 0 and a
            set-wide test would pass everything, including the 0.194 sliver.
          * closest approach, not every segment — an object the run TOUCHES is
            merged with it and cannot form a channel with it, so the anchor a
            closure lands on must not veto the run just because a later
            segment passes 0.15 away from that same pad.  Judging every
            (segment, object) pair instead cost three nets' worth of closures
            on 2026-08-02 and turned two bench jumpers into three.
        """
        for p2, r2 in near_same:
            if sliver_len(pts, p2, r2, w) > SLIVER_RUN_MAX:
                return False
        return True

    def _ride_point(p2, toward):
        """A point ON the CENTRELINE of a same-net object, nearest *toward*.

        The centreline, not the surface: a bend placed there is inside the
        object's copper by its whole radius, so the two runs merge instead of
        leaving a channel.  Discs give their centre, tracks the projection of
        *toward* onto the segment, polygonal lands their centroid."""
        if len(p2) == 1:
            return p2[0]
        if len(p2) == 2:
            (ax, ay), (bx, by) = p2
            vx, vy = bx - ax, by - ay
            den = vx * vx + vy * vy
            if den <= 0:
                return p2[0]
            s = ((toward[0] - ax) * vx + (toward[1] - ay) * vy) / den
            s = max(0.0, min(1.0, s))
            return (ax + vx * s, ay + vy * s)
        return (sum(x for x, _ in p2) / len(p2),
                sum(y for _, y in p2) / len(p2))

    def _snap_run(run, lay, net, w):
        """Ride the net's own copper rather than leave an un-millable channel.

        Returns *run* unchanged when it already leaves none, or when no
        interior vertex can be moved onto same-net copper without breaking the
        clearance law against some OTHER net — riding one trace moves the run
        nearer whatever bounded its original placement, so that is measured,
        never assumed."""
        if len(run) < 3:
            return run
        near_same = same_near(run[0], run[-1], lay, net)
        if sliver_ok(run, near_same, w):
            return run
        for p2, r2 in near_same:
            if sliver_len(run, p2, r2, w) <= SLIVER_RUN_MAX:
                continue
            for k in range(1, len(run) - 1):
                q = _ride_point(p2, run[k])
                cand = list(run)
                cand[k] = (TB.q(q[0]), TB.q(q[1]))
                if (clears(cand, nearby(cand[0], cand[-1], lay, net), w)
                        >= TB.CLEAR
                        and sliver_ok(cand, near_same, w)):
                    return cand
        return run

    def path_ok(pts, layer, net, w, clear=TB.CLEAR):
        """Both laws at once: foreign copper clears, own copper does not
        leave an un-millable sliver."""
        return (clears(pts, nearby(pts[0], pts[-1], layer, net), w) >= clear
                and sliver_ok(pts, same_near(pts[0], pts[-1], layer, net), w))

    bodies = [(p.x, p.y, 1.5 if p.kind == "rect" else 2.0)
              for part in parts for p in part.pins]

    def via_ok(x, y, net, clear=TB.CLEAR):
        """SPEC "Via geometry": 0.4 to any other copper on BOTH faces, 1.5 clear
        of an SMD body, 2.0 of a THT body, 3.0 from the board edge — every one
        of them a rule about whether a human can thread and solder a wire
        there, which is what a via on this board actually is."""
        if not (3.0 < x < TB.BOARD_W - 3.0 and 3.0 < y < TB.BOARD_H - 3.0):
            return False
        for bx, by, keep in bodies:
            if math.hypot(bx - x, by - y) < keep + TB.RING_VIA / 2:
                return False
        # A via is a PADSTACK, and POUR_HOLE_MARGIN was measured to bind LINES
        # only ("PADSTACK cutouts are unaffected — S2's own rings straddle the
        # same hole harmlessly", tools-board.POUR_HOLE_MARGIN).  So a via gets
        # the plain keep-out rule that seeded_vias already applies, not the
        # inflated line rule, and the inflated obstacle is skipped here rather
        # than costing legal via positions it has no measured claim on.
        for mx, my in TB.MOUNTS.values():
            if (math.hypot(mx - x, my - y) - TB.MOUNT_KEEPOUT_R
                    - TB.RING_VIA / 2) < clear:
                return False
        for n2, _l2, p2, r2 in others:
            if n2 in (None, net, MOUNT_KEEPOUT):
                continue
            if TB.shape_gap(([(x, y)], TB.RING_VIA / 2), (p2, r2)) < clear:
                return False
        return True

    def long_haul(net, w, objs, comps):
        """TIER 3 — a JOURNEY, when the local vocabulary has nothing to say.

        The three shapes above (straight, one bend, one escape leg, optionally
        across a via pair with a STRAIGHT crossing) all describe a gap in an
        open pocket.  RESET's last rat is not a gap: U1-1 sits in the ring
        interior and the rest of the net is 25 mm away in the south-east, past
        twelve LED lead pairs and a board FreeRouting has already filled.  No
        two-segment shape can state that, so for years of this file's history
        the residue was blamed on the pin — "U1-1's only lane is 130-145 deg,
        +0.645 mm".

        probe_lane.py re-measured that lane on the GROWN board: U1-1 holds a
        2.375 mm corridor and an 8.00 mm legal 0.6 stub at bearing 75-85.  The
        pin was never trapped.  What was missing was a way to SAY a journey, so
        pathfind.py searches for one and this hands it the same oracles every
        other closure answers to.  It runs LAST, only where tiers 1 and 2 have
        already failed, so no closure that worked before can change.
        """
        head = set(comps[0])
        goals = [objs[k] for k in range(len(objs)) if k not in head]
        if not goals:
            return None
        _g, i, gj = min(((TB.shape_gap((objs[i][1], objs[i][2]),
                                       (g[1], g[2])), i, k)
                         for i in head for k, g in enumerate(goals)),
                        key=lambda t: (t[0], t[1], t[2]))
        pa = anchor(objs[i], anchor(goals[gj], objs[i][1][0]))
        face = sorted(objs[i][0])[0]

        # A journey BUYS margin, and tiers 1-2 keep the law unchanged.  Tens of
        # millimetres threaded through the gaps a finished board has left graze
        # something almost everywhere they go, and the first one built at
        # exactly the law drew a pcb-rnd "shorted nets" on a 0.403 mm gap our
        # own scan called legal.  So the search asks for the ROUTED-copper
        # margin first and settles for the STATIC-copper margin, never less:
        # COPPER_CLEAR is the floor this file already proves an emitted line
        # needs before pcb-rnd's pour clipping stops reading it as a short
        # (21 net-short violations before that margin existed).  The LAW is
        # still 0.400; what changes is that the copper clears it instead of
        # tying it.
        # THREE rungs, not two, and the new one is the strictest: ask first
        # for a journey that also leaves no un-millable sliver against this
        # net's own copper, and only then fall back to the two clearance rungs
        # that have always been here.  PREFER, never veto — a tier-3 journey is
        # the closer's last resort, so a sliver rule that could refuse one
        # would cost the connection outright, which is how the hard veto turned
        # two bench jumpers into three.  Same shape as the tier-1/2 ranking:
        # take the millable route when one exists, take the sliver when the
        # alternative is an open net, and let the double-sided gate measure
        # whatever survives.
        for clear, no_sliver in ((TB.ROUTE_CLEAR, True),
                                 (TB.ROUTE_CLEAR, False),
                                 (TB.COPPER_CLEAR, False)):
            def seg_ok(a, b, f, _c=clear, _ns=no_sliver):
                if clears([a, b], nearby(a, b, f, net), w) < _c:
                    return False
                return (not _ns) or sliver_ok([a, b],
                                              same_near(a, b, f, net), w)

            def via_ok3(x, y, n, _c=clear):
                return via_ok(x, y, n, _c)

            got = pathfind.route_between(TB, others, net, pa, goals, w,
                                         via_ok3, seg_ok, start_face=face,
                                         clear=clear)
            if got is not None:
                return got
        return None

    # 0.25 steps, not 0.5.  The sliver RANKING can only choose a millable route
    # when the candidate set contains one, and at 0.5 it often does not: the
    # L0 closure beside FreeRouting's own trace sat 0.194 from it, 0.026 short
    # of the tip-diameter band, and no bend on a half-millimetre lattice
    # expressed a nudge that small.  Halving the step doubles the lattice in
    # each axis and is the cheapest way to give the ranking something to pick.
    off = [round(-5.0 + 0.25 * k, 3) for k in range(41)]
    vgrid = [round(-6.0 + 0.5 * k, 3) for k in range(25)]
    for net in sorted({p.net for part in parts for p in part.pins} - {None}):
        w = TB.RAIL if net in RAIL_NETS else TB.TRACK
        for _ in range(MAX_CLOSURES):
            objs = net_copper(net, parts, tracks + out,
                              vias + new_vias, promoted)
            comps = components(objs)
            if len(comps) < 2:
                break
            # EVERY cross-component pair that shares a face, nearest first: the
            # closest pieces are often the ones a dense corner has walled off,
            # and a pair 2 mm further apart can have an empty lane between it.
            pairs = sorted(
                (round(TB.shape_gap((objs[i][1], objs[i][2]),
                                    (objs[j][1], objs[j][2])), 3), i, j)
                for ci, ga in enumerate(comps) for gb in comps[ci + 1:]
                for i in ga for j in gb if objs[i][0] & objs[j][0])
            drawn = None
            for _d, i, j in pairs[:24]:
                face = sorted(objs[i][0] & objs[j][0])[0]
                pa = anchor(objs[i], anchor(objs[j], objs[i][1][0]))
                pb = anchor(objs[j], pa)
                near = nearby(pa, pb, face, net)
                mid = ((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2)
                # Three shapes, and the third one is the lesson of U1-1:
                # a pin buried in a pin field escapes on a SHORT LEG of its own
                # bearing first (U1-1's only lane is 130-145 deg, +0.645 mm)
                # and only then heads for the far piece.  Bends offered only
                # near the MIDPOINT cannot express that, so the first leg went
                # straight back into the traffic every time and the closure was
                # reported impossible when it was merely mis-shaped.
                escape = [(round(r * math.cos(math.radians(a)), 3),
                           round(r * math.sin(math.radians(a)), 3))
                          for r in (2.0, 3.0, 4.0) for a in range(0, 360, 10)]
                cands = [[pa, pb]] + [
                    [pa, (TB.q(mid[0] + dx), TB.q(mid[1] + dy)), pb]
                    for dx in off for dy in off] + [
                    [pa, (TB.q(pa[0] + dx), TB.q(pa[1] + dy)), pb]
                    for dx, dy in escape] + [
                    [pa, (TB.q(pb[0] + dx), TB.q(pb[1] + dy)), pb]
                    for dx, dy in escape]
                # SNAP CANDIDATES — the other half of the sliver rule.
                #
                # The ranking above prefers a route that leaves no un-millable
                # channel, but preferring only works when the lattice contains
                # one, and for the L0 closure beside FreeRouting's own trace it
                # never did: FOUR reroutes (hard veto, ranking, a 0.25 lattice,
                # a tier-3 sliver rung) all came back byte-identical with the
                # same 0.194 mm channel at board (6.005,24.575) — 0.006 inside
                # the tip-diameter merge condition.  No amount of nudging finds
                # a gap that is wide enough when the corridor is that tight.
                #
                # So take the OTHER exit the rule always allowed: stop trying
                # to clear the neighbour and RIDE it.  A bend placed on the
                # centreline of the net's own copper makes the two runs one
                # solid outline, and a channel that does not exist cannot be
                # too narrow to mill.  Same net, so the overlap is electrically
                # identity — it joins conductors that were already joined —
                # and every snapped candidate still has to satisfy the
                # clearance oracle against OTHER nets before the ranking will
                # look at it, because riding one trace moves the run nearer
                # whatever bounded its original placement.
                for _p2, _r2 in same_near(pa, pb, face, net):
                    q = _ride_point(_p2, mid)
                    if q is not None and math.dist(q, mid) <= 4.0:
                        cands.append([pa, (TB.q(q[0]), TB.q(q[1])), pb])
                ok = []
                for pts in cands:
                    g = clears(pts, near, w)
                    if g >= TB.CLEAR:
                        ln = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
                        # PREFER, do not veto.  A hard sliver veto was measured
                        # to cost a whole net's closures (three bench jumpers
                        # instead of two) at every band width tried, because
                        # closures on this board routinely pass their own net
                        # at 0.2-0.3.  Ranking keeps every closure the board
                        # needs and still takes the millable route whenever one
                        # exists; a sliver that survives means no alternative
                        # did, and the double-sided gate still measures it.
                        sl = 0 if sliver_ok(
                            pts, same_near(pts[0], pts[-1], face, net),
                            w) else 1
                        ok.append((sl, round(ln, 3), -round(g, 3), pts))
                if ok:
                    ok.sort()
                    drawn = (face, ok[0][3], None)
                    break
            if drawn is None:
                # No lane on the face the two pieces share.  SPEND A VIA PAIR
                # and cross on the other one — the operator's 2026-08-01 ruling
                # says a wire via costs about what a jumper wire costs, and the
                # FRONT of this board is mostly bare pour where the BACK is
                # solid traffic.  Each via still has to be a hole a human can
                # thread (via_ok), and both stubs and the crossing are measured
                # like any other copper.
                for _d, i, j in pairs[:40]:
                    face = sorted(objs[i][0] & objs[j][0])[0]
                    over = "top" if face == "bottom" else "bottom"
                    pa = anchor(objs[i], anchor(objs[j], objs[i][1][0]))
                    pb = anchor(objs[j], pa)
                    va = [(TB.q(pa[0] + dx), TB.q(pa[1] + dy))
                          for dx in vgrid for dy in vgrid]
                    vb = [(TB.q(pb[0] + dx), TB.q(pb[1] + dy))
                          for dx in vgrid for dy in vgrid]
                    va = [v for v in va if via_ok(*v, net)
                          and clears([pa, v], nearby(pa, v, face, net), w)
                          >= TB.CLEAR][:40]
                    vb = [v for v in vb if via_ok(*v, net)
                          and clears([pb, v], nearby(pb, v, face, net), w)
                          >= TB.CLEAR][:40]
                    hop = []
                    for a in va:
                        for b2 in vb:
                            g = clears([a, b2], nearby(a, b2, over, net), w)
                            if g >= TB.CLEAR:
                                hop.append((round(math.dist(a, b2), 3),
                                            -round(g, 3), a, b2))
                    if hop:
                        hop.sort()
                        _l, _g, a, b2 = hop[0]
                        drawn = (face, [pa, a], (over, a, b2, pb))
                        break
            if drawn is None:
                journey = (None if net in POUR_WELDED_NETS
                           else long_haul(net, w, objs, comps))
                if journey is None:
                    # GND arrives here on EVERY iteration by construction —
                    # the line above never offers it a journey, because its
                    # copper is WELDED into the pour and net_copper cannot see
                    # the pour (POUR_WELDED_NETS).  Its leftover "pieces" are
                    # joined by metal; reporting them would be crying wolf on
                    # the one net that is fine, and the first version of this
                    # reporter did exactly that.
                    if net in POUR_WELDED_NETS:
                        break
                    # NOTHING LEGAL EXISTS for this pair.  Say so, by name.
                    #
                    # This used to `break` in silence and leave the gate to
                    # report a bare "N rat lines" — which tells you a board is
                    # broken but not WHERE, and 2026-08-02 was a day spent
                    # guessing at geometry because of it.  A closer that gives
                    # up is making a statement about a specific pair of copper
                    # pieces on a specific face, and that statement is the most
                    # useful thing this stage knows.
                    unclosed.append(
                        (net, len(comps),
                         tuple(round(v, 2) for v in objs[i][1][0]),
                         tuple(round(v, 2) for v in objs[j][1][0]),
                         sorted(objs[i][0] & objs[j][0])))
                    break                   # nothing legal: let the gate speak
                runs, hop_vias = journey
            else:
                face, pts, hop = drawn
                runs, hop_vias = [(face, pts)], []
                if hop:
                    over, a, b2, pb = hop
                    runs += [(over, [a, b2]), (face, [b2, pb])]
                    hop_vias = [a, b2]
            for v in hop_vias:
                new_vias.append((TB.q(v[0]), TB.q(H - v[1]), net))
                for lay in ("top", "bottom"):
                    others.append((net, lay, [v], TB.RING_VIA / 2))
            # SNAP, whichever tier drew it.  The candidate-level snap above
            # only reaches tiers 1-2, and the closure that started all of this
            # comes from tier 3's pathfinder — measured: four reroutes, always
            # the same 0.194 mm channel at board (6.005,24.575).  So the snap
            # runs once more HERE, on the chosen runs, where every tier's
            # output passes.  Anchors never move (they are what the closure
            # connects); only interior vertices ride onto the net's own
            # centreline, and only when that both removes the sliver AND still
            # clears every other net.
            runs = [(lay, _snap_run(run, lay, net, w)) for lay, run in runs]
            for lay, run in runs:
                for a, b in zip(run, run[1:]):
                    if a == b:
                        continue
                    out.append((lay, TB.q(a[0]), TB.q(H - a[1]),
                                TB.q(b[0]), TB.q(H - b[1]), w, net))
                    # Every closure joins the obstacle set IMMEDIATELY.
                    # Measured the hard way: without this, closure #2 is
                    # checked against the board as it was before closure #1,
                    # and pcb-rnd reads the result as a SHORT between two nets
                    # we drew ourselves.
                    others.append((net, lay, [a, b], w / 2))
    return out, new_vias, unclosed


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
        # The dead front rings were handed to the router as protected wires on
        # private pseudo-nets (tools-board.dead_net), so it echoes them back
        # like any other protected wire.  They are ALREADY on the board as
        # dead-island copper — dead_front_rings() emits every one of them — so
        # taking them from the session too would stack a second disc of copper
        # on the same spot and hand DRC a self-overlap.  Same reasoning as the
        # `fixed` filter below, different origin.
        if net.startswith("__dead_"):
            echoed += 1
            continue
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
    # The 2026-08-01 class boundary, checked rather than assumed.  It is
    # already true by construction — the LEDs carry dual=False, so they can
    # neither reach `promoted` nor own a hplated=1 prototype to be emitted on —
    # and that is exactly why it is worth the line: the failure mode is a
    # future edit quietly handing the class back, and the symptom would be an
    # assembly card asking the bench for ten joints under an LED flange.
    bad = sorted(p for p in promoted if p.startswith("LED"))
    if bad:
        raise SystemExit(
            f"refusing to emit: {bad} promoted to a front-face joint — the "
            f"operator ruled on 2026-08-01 that LED leads may not be layer "
            f"bridges (a via is jumper-class; an under-flange joint is not)")

    # ... and now the copper that finishes what the router left open.  AFTER
    # promotion, deliberately: a closing track may not conjure a dual-solder
    # joint into existence, so it is drawn on copper the bench is already
    # committed to.  Before everything else, so the ledger, the stitch list,
    # the clearance scan and pcb-rnd all see exactly one board.
    closing, closing_vias, unclosed = closing_tracks(parts, tracks, vias,
                                                     promoted)
    tracks.extend(closing)
    vias.extend(closing_vias)

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
        "closing_tracks": [list(t) for t in closing],
        # what the closer could NOT do, by name — see closing_tracks
        "unclosed": [list(u) for u in unclosed],
        "stitch_set": [list(p) for p in stitch],
    }


# ---------------------------------------------------------------------------
# 1+4. BUILD
# ---------------------------------------------------------------------------
def build_routed(force_route: bool = False) -> dict:
    b = TB.build(out_lht=UNROUTED_LHT)                 # generator + DSN
    seal, ran = route(force_route)
    m = merge(b["parts"], SES)
    # Drop the stitches the copper does not need.  Cached so the normal build
    # is one pass and still byte-deterministic.
    #
    # The key is the MERGED COPPER, not the session digest, and that correction
    # cost a live GND net to find: a via is redundant only relative to the
    # copper around it, and the closing tracks are part of that copper.  Keyed
    # on the session alone, a cache computed when closing_tracks was weaker
    # went on deleting a via that the NEW closures had come to depend on, and
    # pcb-rnd reported GND in two pieces on a board whose seal looked current.
    mkey = hashlib.sha256(json.dumps(
        {"tracks": m["tracks"], "vias": m["vias"], "promoted": m["promoted"],
         "prune_policy": 2},
        sort_keys=True).encode()).hexdigest()
    if seal.get("pruned_for") != mkey:
        seal["redundant_vias"] = [list(v) for v in prune_vias(b, m)]
        seal["pruned_for"] = mkey
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
    final = {"tracks": m["tracks"], "vias": m["vias"],
             "track_nets": m["track_nets"], "promoted": set(m["promoted"])}
    rb = TB.build(route=final, out_lht=FINAL_LHT)
    # THE SHIPPED SILK IS THE ROUTED BOARD'S SILK, and taking it from the
    # unrouted build is how three labels came to sit on via rings while every
    # check agreed they did not (2026-08-02).  A via is a mask aperture on
    # both faces and it does not exist until this function has run, so the
    # labels, the legend, the MATRIX census and the gate below all read the
    # placement made against THIS board rather than the one R4a emitted.
    b["labels"], b["unplaced"], b["placed"] = (rb["labels"], rb["unplaced"],
                                               rb["placed"])
    b["route"], b["silk_drops"] = final, {k: list(v) for k, v
                                          in TB.SILK_DROPS.items()}
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


def open_nets(parts: list, route: dict, m: dict) -> list[tuple]:
    """-> [(net, n_pieces), ...] for every net whose copper is in >1 piece.

    GND is excluded on the same grounds closing_tracks excludes it: its copper
    WELDS into the pours, which net_copper cannot see, so its "pieces" are
    joined by metal.  pcb-rnd is the oracle for GND, and gate A reads it.
    """
    tracks = [tuple(t) + (n,) for t, n in zip(m["tracks"], m["track_nets"])]
    vias = [tuple(v) for v in m["vias"]]
    promoted = set(m["promoted"])
    out = []
    for net in sorted({p.net for part in parts for p in part.pins} - {None}):
        if net in POUR_WELDED_NETS:
            continue
        comps = components(net_copper(net, parts, tracks, vias, promoted))
        if len(comps) > 1:
            out.append((net, len(comps)))
    return out


def jumper_audit(parts: list, route: dict, m: dict) -> tuple[list, list]:
    """-> (satisfied, undeclared).  The [[rules.gauge]] check for connectivity.

    A declaration is only worth something if it is CHECKED, so this asks two
    questions the count alone cannot:

      * is every declared jumper still NEEDED — do its two named terminals
        really sit in different pieces of their net?  A stale declaration that
        names a connection the copper now makes would otherwise sit here
        forever, quietly licensing a future break on that net.
      * is every open net DECLARED?  Anything else is an undeclared open and
        must fail, which is the whole point of writing the exception down
        instead of loosening the law.
    """
    tracks = [tuple(t) + (n,) for t, n in zip(m["tracks"], m["track_nets"])]
    vias = [tuple(v) for v in m["vias"]]
    promoted = set(m["promoted"])
    by_pid = {p.pid: p for part in parts for p in part.pins}
    satisfied = []
    for j in DECLARED_JUMPERS:
        objs = net_copper(j["net"], parts, tracks, vias, promoted)
        comps = components(objs)
        where = {}
        for ci, g in enumerate(comps):
            for k in g:
                for pid in (j["from"], j["to"]):
                    p = by_pid[pid]
                    if any(math.hypot(p.x - a, p.y - b) < 1e-6
                           for a, b in [objs[k][1][0]]):
                        where[pid] = ci
        ok = (where.get(j["from"]) is not None
              and where.get(j["to"]) is not None
              and where[j["from"]] != where[j["to"]])
        satisfied.append((j["net"], j["from"], j["to"], ok, len(comps)))
    declared_nets = {j["net"] for j in DECLARED_JUMPERS}
    undeclared = [(n, k) for n, k in open_nets(parts, route, m)
                  if n not in declared_nets]
    return satisfied, undeclared


def rat_lines(board: str) -> list[tuple]:
    """-> [(a, b, anchor_a, anchor_b), ...] for every rat pcb-rnd draws.

    A COUNT cannot be checked against a declaration — "one rat line" is true of
    a board whose one open connection is the declared jumper and equally true
    of a board whose one open connection is something nobody has ever seen.
    AddRats writes real `ha:rat` objects into the layout, so the honest way to
    know WHICH connection is open is to let pcb-rnd draw them and read the
    bytes back.  Coordinates are the lihata frame (y-down), as saved.
    """
    out = os.path.join(HERE, ".rats-out.lht")
    if os.path.exists(out):
        os.unlink(out)
    TB.pcb_rnd("AddRats(AllRats)\nSaveTo(LayoutAs, %s)\n" % out, board)
    if not os.path.exists(out):
        return []
    txt = open(out, encoding="utf-8").read()
    os.unlink(out)
    rows = []
    for m in re.finditer(
            r"ha:rat\.\d+ \{\s*x1=([\d.eE+-]+)mm; y1=([\d.eE+-]+)mm; "
            r"lgrp1=\d+; anchor1=(\S+); x2=([\d.eE+-]+)mm; y2=([\d.eE+-]+)mm; "
            r"lgrp2=\d+; anchor2=([^;]+);", txt):
        rows.append(((round(float(m.group(1)), 3), round(float(m.group(2)), 3)),
                     (round(float(m.group(4)), 3), round(float(m.group(5)), 3)),
                     m.group(3), m.group(6)))
    return sorted(rows)


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
    # The pre-seeded crossings are named as their own class, because they are
    # not the router's arithmetic — each one is the operator's ruling in
    # copper, standing exactly where an under-flange anode joint used to.
    seeds = {(TB.q(sx), TB.q(TB.BOARD_H - sy)): pid
             for pid, sx, sy, _n in TB.seeded_vias(parts or ())}
    rows = []
    for i, (x, y, net) in enumerate(sorted(m["vias"],
                                           key=lambda v: (v[2], v[0]))):
        seed = seeds.get((TB.q(x), TB.q(y)))
        if seed:
            rows.append(f"V{i + 1:<2d} ({x:6.3f}, {y:6.3f})  {net:6s}  "
                        f"pre-seeded crossing for {net} beside "
                        f"{seed.split('-')[0]} (replaces the forbidden anode "
                        f"bridge)")
            continue
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

    # A via that JOINS this net's copper on BOTH faces is never a candidate,
    # whatever the rat count says.  MEASURED 2026-08-02: the VCC via at
    # (25.707, 29.897) was the only conductor between a bottom track ending
    # there and a top track starting there, and dropping it did not raise the
    # rat count — because VCC stays connected by another path entirely — so the
    # rat-count oracle called it redundant.  What it left behind was two traces
    # meeting at a point on opposite faces with nothing between them, and
    # pcb-rnd's OTHER check convicted it: "broken net: insufficient overlap".
    # Connectivity is not the only thing a via does; it is also the metal that
    # makes a layer change exist on the milled board.
    joins = set()
    for x, y, net in m["vias"]:
        faces = {t[0] for t, n in zip(m["tracks"], m["track_nets"])
                 if n == net and ((TB.q(t[1]), TB.q(t[2])) == (TB.q(x), TB.q(y))
                                  or (TB.q(t[3]), TB.q(t[4])) == (TB.q(x), TB.q(y)))}
        if len(faces) > 1:
            joins.add((x, y, net))
    base, keep, drop = rats(m["vias"]), list(m["vias"]), []
    for v in list(m["vias"]):
        if tuple(v) in joins:
            continue
        trial = [x for x in keep if x != v]
        if rats(trial) <= base:
            keep, _ = trial, drop.append(v)
    os.unlink(probe)
    return drop


MATRIX = os.path.join(HERE, "MATRIX.md")
GERBERS = os.path.join(HERE, "gerbers-rnd")


def pour_census(parts: list, m: dict) -> list[str]:
    """Every copper region on both faces, classified, from the RASTER.

    Returns MATRIX lines, or a loud placeholder when the artwork has not been
    exported yet — never a guess.  The classification is by MATCHING known
    geometry (a dead ring's centre, a gauge's centre, a track's endpoint),
    not by area, because a Ø2.50 dead ring and a small pour fragment are the
    same size and only one of them is a defect.
    """
    need = ["orbit-F_Cu.gbr", "orbit-B_Cu.gbr", "orbit-Edge_Cuts.gbr"]
    if not all(os.path.exists(os.path.join(GERBERS, f)) for f in need):
        return ["- **not measured**: run `tools-fab.py`, then re-run the gate "
                "so this section is written from real artwork."]
    # Staleness is judged by CONTENT, not mtime: this build is deterministic,
    # so a rebuild rewrites a byte-identical orbit.lht with a newer timestamp
    # and an mtime test would call good artwork stale (it did).
    stamp = os.path.join(GERBERS, ".source.sha256")
    want = hashlib.sha256(open(FINAL_LHT, "rb").read()).hexdigest()
    if not os.path.exists(stamp) or open(stamp).read().strip() != want:
        return ["- **stale**: these gerbers were exported from a different "
                "orbit.lht; run `tools-fab.py`, then re-run the gate."]
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)),
                                        "src"))
        from clauderacam.pcb import boardmaps as bm
        import numpy as np
        from scipy import ndimage
    except Exception as e:                                   # noqa: BLE001
        return [f"- **not measured**: {e}"]

    win = bm.extents(os.path.join(GERBERS, "orbit-Edge_Cuts.gbr"),
                     cross_check=True)
    out = []
    for face, gbr in (("FRONT", "orbit-F_Cu.gbr"), ("BACK", "orbit-B_Cu.gbr")):
        A = bm.rasterize(os.path.join(GERBERS, gbr), win).astype(bool)
        h, w = A.shape
        # EIGHT-CONNECTED, and it is the fix for the census's own contradiction
        # (2026-08-02): this labeller was 4-connected while the rest of the
        # lane has been 8-connected since checks._EIGHT — "copper touching at a
        # corner is one piece of metal; a rasterised diagonal edge is an
        # artifact of the grid, not a gap".  Two laws for what counts as ONE
        # piece of metal is one law too many, and the census had the wrong one:
        # MEASURED on this artwork, 4-connectivity split SEVEN single-pixel
        # specks off the back GND plane's own diagonal edges — regions 21, 22,
        # 34, 35, 44, 49 and 50, each exactly 1 px = 0.000100 mm2, each
        # touching the plane at a corner, two of them (34/35, 49/50) diagonal
        # PAIRS of each other — and reported them as unexplained copper.  They
        # are the plane.  Under this structure the back census reads 45 regions
        # instead of 52 and NOTHING is unexplained; no copper changed.
        lab, n = ndimage.label(A, structure=np.ones((3, 3), int))
        area = ndimage.sum(A, lab, range(1, n + 1))
        pxmm2 = (w - 1) * (h - 1) / (win.w_mm * win.h_mm)

        def at(bx, by):
            return int(lab[int(round((win.y1 - by) / win.h_mm * (h - 1))),
                           int(round((bx - win.x0) / win.w_mm * (w - 1)))])

        pad2 = [p for part in parts if part.ref == "PAD2" for p in part.pins][0]
        plane = at(pad2.x, pad2.y)
        live = {plane: ["PAD2-1, the promoted GND lead"]}
        for vx, vy, vnet in m["vias"]:
            if vnet == "GND":
                live.setdefault(at(vx, TB.BOARD_H - vy), []).append(
                    f"GND wire via ({vx:.2f},{TB.BOARD_H - vy:.2f})")
        dead = {}
        if face == "FRONT":
            for pid, x, y, dia, _j in TB.dead_front_rings(parts):
                if pid not in m["promoted"]:
                    dead[at(x, y)] = f"dead front ring {pid}"
        for ref, (gx, gy) in TB.GAUGES.items():
            dead.setdefault(at(gx, gy), f"flip gauge {ref}")
        sig = {}
        for lay, x1, y1, x2, y2, _w in m["tracks"]:
            if (lay == "top") == (face == "FRONT"):
                sig.setdefault(at(x1, TB.BOARD_H - y1), 0)
                sig[at(x1, TB.BOARD_H - y1)] += 1
        pads = {}
        for part in parts:
            for p in part.pins:
                if face == "BACK":
                    pads.setdefault(at(p.x, p.y), []).append(p.pid)
        rest = [k for k in range(1, n + 1)
                if k not in live and k not in dead and k not in sig
                and k not in pads]
        # THE ARTIFACT BAR, and it is a guard rather than an excuse.  One
        # raster pixel at the census resolution is 1/pxmm2 = 0.000100 mm2 at
        # 100 px/mm; the smallest copper this PROCESS can leave behind is
        # bounded from below by the isolation bit that cuts around it (0.30 mm
        # wide, so ~0.09 mm2 = 900 px for anything the mill could actually
        # produce, and the sliver rule in checks.residual_checks refuses
        # fragments far bigger than that).  A region at or under one pixel is
        # therefore not copper at all — it is the rasteriser's report of a
        # polygon boundary, four orders of magnitude below the smallest real
        # feature.  It is counted and named as an ARTIFACT, never folded into a
        # class it is not and never called unexplained.  With the connectivity
        # fixed this bar catches nothing on this artwork (both faces read 0),
        # which is the honest state: the specks it was written for were the
        # plane all along.
        art = [k for k in rest if area[k - 1] / pxmm2 <= 1.0 / pxmm2]
        unex = [k for k in rest if k not in art]
        out.append(f"- **{face}: {n} regions.** GND plane is region {plane} at "
                   f"{area[plane - 1] / pxmm2:.1f} mm2, live through "
                   f"{'; '.join(live[plane])}.")
        out.append(f"  - {len(dead)} region(s) dead BY DESIGN "
                   f"(THT front rings own no net — the R3 finding — and the "
                   f"four flip gauges are read with a loupe, never soldered).")
        out.append(f"  - {len(sig)} region(s) are other nets' routed copper; "
                   f"{len(pads)} carry component lands.")
        # THE COUNT AND THE LIST ARE THE SAME OBJECT.  They were not: the count
        # was len(unex) and the list was unex[:6], so the back census published
        # "7 unexplained region(s): [21, 22, 34, 35, 44, 49]" — six names for
        # seven regions, and the seventh (region 50) was invisible to anyone
        # trying to investigate it.  A census that truncates its own evidence
        # is a census nobody can check.
        out.append(f"  - {len(art)} raster artifact(s) "
                   f"(<= 1 px = {1 / pxmm2:.6f} mm2, below any copper this "
                   f"process can leave)"
                   + (f": {art}." if art else "."))
        out.append(f"  - **{len(unex)} unexplained region(s)**"
                   + (f": {unex} — INVESTIGATE." if unex else "."))
    return out


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

    if DECLARED_JUMPERS:
        L += ["", "## BENCH JUMPERS — wires the COPPER does not make", "",
              f"**{len(DECLARED_JUMPERS)} jumper(s).**  Same 22 AWG solid wire "
              "as the stitched vias (Board A shipped seven of these).  This is "
              "not a repair and not an oversight: after the side-2 scrub growth "
              "the board is exactly one routable connection short, and WHICH "
              "connection is short moves with the seed set (four sets measured, "
              "four different nets).  So the shortfall was spent deliberately, "
              "on the pair of terminals that is easiest to solder.", ""]
        for j in DECLARED_JUMPERS:
            L += [f"- **{j['net']}** — solder a {j['length_mm']:.1f} mm wire "
                  f"from **{j['from']}** ({j['from_xy'][0]:.3f}, "
                  f"{j['from_xy'][1]:.3f}) to **{j['to']}** "
                  f"({j['to_xy'][0]:.3f}, {j['to_xy'][1]:.3f}).",
                  f"  Both pads are on the **{j['face']}** face, so the wire "
                  f"lies flat there for its whole run and crosses no front "
                  f"component.  `{j['from']}` is {j['from_note']}; "
                  f"`{j['to']}` is {j['to_note']}.  Neither end is an SMD "
                  f"land.",
                  f"  Until it is soldered, `{j['net']}` is OPEN — the board "
                  f"is not finished without it."]

    prom = [p for p in m["promoted"]]
    L += ["", "## DUAL-SOLDER LEADS — the front-side bench work list", "",
          f"**{len(prom)} leads** must be soldered on the FRONT face as well "
          "as the back.  Each one is real copper the routed board depends on: "
          "there is no plating on a milled board, so an unsoldered lead here "
          "is an open circuit, not a cosmetic miss.", "",
          "> **Seat every LED FLUSH and solder it on the BACK only.** No LED "
          "lead is a layer bridge on this board. Operator ruling, 2026-08-01: "
          "*\"seating an LED 1.5mm proud for an under-flange front joint is "
          "EXTREMELY annoying by hand — ten promoted LED anodes is "
          "unacceptable. A bare wire via (open access, both faces) is "
          "jumper-class and fine. Therefore: LED leads may no longer be layer "
          "bridges.\"* The ten front joints this board used to ask for are "
          "PRE-SEEDED CROSSINGS in the via ledger above — same layer change, "
          "made with a threaded wire in open board instead of an iron tip "
          "under an LED flange.", "",
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
    L += ["", "## Pour census — every copper region and what makes it live", "",
          "MEASURED on the exported gerbers with gerbv (the rasterizer that "
          "shares no code with this generator), not asserted from the model.  "
          "A region with no conductor is not automatically a defect: on a "
          "milled board with no plating, a THT lead's FRONT ring belongs to no "
          "net by construction (the R3 finding), and the flip gauges are "
          "deliberately dead copper you read with a loupe.  What matters is "
          "that every region is either LIVE or dead ON PURPOSE.", ""]
    for line in pour_census(parts, m):
        L += [line]

    rows = TB.silk_seats(parts, b["labels"], b.get("route"))
    flags = TB.silk_flags(rows)
    worst = min(rows, key=lambda r: r["feature_gap"])
    drops = b.get("silk_drops", {"front": [], "back": []})
    L += ["", "## Silk: what is printed, and what is not", "",
          f"Measured under the CORRECTED keep-out law (2026-08-02): every "
          f"mask aperture on that side — solderable or dead, including all "
          f"{len(m['vias'])} wire-via rings, which are apertures on BOTH "
          f"faces — plus the bare-copper flip gauges, which have no aperture "
          f"at all, plus the bores. {len(rows)} texts measured against "
          f"{rows[0]['n_features']}/{rows[-1]['n_features']} features per "
          f"side: **{len(flags)} flags**, tightest ink-to-copper "
          f"{worst['feature_gap']:.3f} mm on `{worst['item']}` against the "
          f"0.30 law (+{100 * (worst['feature_gap'] / 0.30 - 1):.0f}%).", "",
          "Silk is this board's congestion canary, not decoration: a label "
          "that cannot be seated cleanly means the copper under it is too "
          "tight, and the answer is to un-compress the copper. The drops "
          "below are therefore split into the two kinds that matter — a "
          "REDUNDANT label the board does not need, and a label the copper "
          "left nowhere to put, which is an un-compression request.", ""]
    for ref in b["unplaced"]:
        fn = TB.ISP_LABEL.get(ref)
        alt = {"PAD1": "+", "PAD2": "-"}.get(ref)
        if fn:
            L += [f"- **{ref}** (redundant) — the ISP block names this pad "
                  f"`{fn}`, or would: see the un-compression request below. "
                  f"`{ref}` is a second name for the same hole either way."]
        elif alt:
            L += [f"- **{ref}** (redundant) — the bottom strip prints `{alt}` "
                  f"at this pad, which is the thing the bench actually has to "
                  f"read before wiring a battery. `{ref}` adds no information "
                  f"and its seat is contested by the pad's own legend."]
        else:
            L += [f"- **{ref}** — NO legal seat under the corrected law and "
                  f"no functional legend covers it. THIS IS AN "
                  f"UN-COMPRESSION REQUEST: the copper around {ref} leaves "
                  f"nowhere to put a label that both clears every aperture "
                  f"and unambiguously names {ref}."]
    for side in ("front", "back"):
        for name, owner, why in drops[side]:
            L += [f"- **{name}** ({side} legend, owner {owner}) — {why}. "
                  f"UN-COMPRESSION REQUEST: this one is FUNCTIONAL and its "
                  f"absence costs the bench information."]
    if any(drops[s] for s in drops):
        by_ref = {p.ref: p for p in parts}
        L += ["", "### ISP block — READ THIS, the legend is incomplete", "",
              "Four of the six ISP names have no legal seat while R4b's "
              "crossings stand where they stand (full reasoning in "
              "`tools-board.back_legend`). Until that copper is "
              "un-compressed the block is read from the SQUARE TICK, which "
              "marks pin 1, and from this table. Board frame, back side, "
              "viewed with the BACK up (x mirrors):", "",
              "| pad | name | board (x, y) | printed on silk |",
              "|:----|:-----|:-------------|:----------------|"]
        printed = {nm.replace("ISP ", "")
                   for nm, _o, _s, _h in b["labels"]["back_items"]
                   if nm.startswith("ISP ")}
        for ref, txt in TB.ISP_LABEL.items():
            pin = by_ref[ref].pins[0]
            L += [f"| {ref} | **{txt}** | ({pin.x:.2f}, {pin.y:.2f}) | "
                  f"{'yes' if txt in printed else '**NO — read this table**'} |"]

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


# The pins the negative control lies about.
#
# BZ1-1 is a THT lead the bench can only reach on the BACK — the buzzer's body
# sits on the front, over it — so it is exactly the kind of hole a careless
# model would call "through".
#
# LED8-2 is the 2026-08-01 class boundary itself.  Until that ruling an LED
# anode WAS promotable, and ten of them shipped as layer bridges; a control
# that only ever lied about BZ1-1 would not notice the class quietly coming
# back.  The boundary a project has just moved is the one worth a control.
FANTASIES = ("BZ1-1", "LED8-2")


def neg_paths(pin: str) -> tuple:
    tag = pin.replace("-", "_")
    return tuple(os.path.join(HERE, f".neg-{tag}{ext}")
                 for ext in (".dsn", ".ses", ".lht", ".seal.json"))


def negative_control(b: dict, FANTASY: str) -> tuple:
    """R3's gate D: describe ONE unsolderable lead to the router as a through
    pin, let it use the bridge, then build the board the BENCH can actually
    make — which does not have it — and let pcb-rnd condemn the result.

    Promotion is never the router's decision.  This proves the refusal is worth
    something: without it the board would ship claiming a connection that only
    exists in the DSN.
    """
    NEG_DSN, NEG_SES, NEG_LHT, NEG_SEAL = neg_paths(FANTASY)
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
                       capture_output=True, text=True, timeout=300,
                       env=router_env())
        with open(NEG_SEAL, "w", encoding="utf-8") as fh:
            json.dump({"dsn_sha256": digest(NEG_DSN)}, fh, indent=1)
    m2 = merge(b["parts"], NEG_SES)
    # The board is emitted as PHYSICAL TRUTH: the lied-about lead keeps its
    # back-only ring, because that is the hole the operator will actually hold.
    TB.build(route={"tracks": m2["tracks"], "track_nets": m2["track_nets"],
                    "vias": m2["vias"], "promoted": set(m2["promoted"])},
             out_lht=NEG_LHT)
    rats, complete, _clip = galvanic(NEG_LHT)
    # The condemnation must be about WHAT is open, not how many things are.
    # Counting stopped working the moment the board carried a declared jumper:
    # a fantasy board that happens to reroute into the same total is not
    # innocent, it just has the same arithmetic.  The audit names nets.
    _sat, undecl = jumper_audit(b["parts"], None, m2)

    return m2["fantasy_bridges"], rats, complete, undecl


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
    # The law is unchanged for everything that is not written down: copper must
    # close every connection.  What the declaration buys is EXACTLY the jumpers
    # named in DECLARED_JUMPERS and nothing else, so the arithmetic below is a
    # tightening, not a waiver — with an empty declaration this is the old
    # `rats == 0 and complete` check character for character.
    njump = len(DECLARED_JUMPERS)
    chk(f"open connections == declared bench jumpers ({njump})", rats, njump)
    # pcb-rnd announces SUCCESS with the sentence "The layout is complete and
    # has no shorted nets." — which contains the very word this check greps
    # for.  MEASURED 2026-08-02, and the board-growth roll is what exposed it:
    # orbit had never before been COMPLETE with zero rat lines.  Every previous
    # checkpoint carried at least one declared bench jumper, so pcb-rnd printed
    # a rat-line tally and never reached the congratulation — and the first
    # board that closed entirely in copper was convicted by its own clean bill
    # of health.  A check that fires only on the good boards is worse than no
    # check, and this is the whole of the defect: a substring test against a
    # sentence that asserts the opposite.
    #
    # The fix removes exactly that ONE fixed sentence and applies the SAME test
    # to what is left, so every other spelling of the word stays fatal.  PROVEN
    # rather than asserted — with R14 shoved until its SND_B land OVERLAPS
    # Q2's GND land, pcb-rnd writes
    #     W: SHORT: net "GND" is shorted to "SND_B" at terminal R14-2
    # and the stripped test still convicts.  Note which control that is: the
    # clearance control at tools-board.perturb stops at a 0.15 mm GAP, which is
    # illegal copper but not metal contact, so pcb-rnd correctly reports no
    # short and it cannot prove anything about this check.  The control for a
    # SHORT has to actually short.
    SHORT_OK = "The layout is complete and has no shorted nets."
    chk("no shorted nets", "shorted" not in TB.pcb_rnd(
        "AddRats(AllRats)\n", FINAL_LHT).replace(SHORT_OK, "").lower(), True)
    if njump == 0:
        chk("every net complete", complete, True)
    else:
        sat, undeclared = jumper_audit(parts, route, m)
        # NB: not `b` — that is the build dict this whole function reads, and
        # shadowing it here made the fantasy controls 200 lines later explode
        # on a string.
        for jnet, jfrom, jto, ok, npieces in sat:
            print(f"    declared jumper {jnet}: {jfrom} <-> {jto} — "
                  f"{'joins 2 pieces, still needed' if ok else 'STALE'} "
                  f"({npieces} pieces)")
        chk("every declared jumper still joins two pieces of its net",
            [s[3] for s in sat], [True] * njump)
        for net, k in undeclared:
            print(f"    UNDECLARED OPEN: {net} in {k} pieces")
        chk("no net is open except the declared jumpers", undeclared, [])

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

    print("### D. NEGATIVE CONTROLS — a fantasy bridge must NOT ship ###")
    named, bearings = [], []
    for pin in FANTASIES:
        print(f"    the DSN is corrupted to call {pin} an ordinary through "
              f"pin; the bench cannot reach that lead on the front.")
        fant, nrats, ncomplete, nundecl = negative_control(b, pin)
        # WORSE THAN HONEST is the whole verdict, and it is measured against
        # the declaration rather than against a raw count: an undeclared open
        # on any net, or more open connections than the board declares wires
        # for.  Either one means the corrupted DSN produced a board the bench
        # cannot make.
        worse = bool(nundecl) or nrats > len(DECLARED_JUMPERS)
        print(f"      merge names: {fant} — pcb-rnd on that board: {nrats} "
              f"rat lines, complete={ncomplete}  (honest: {rats}, {complete}); "
              f"undeclared opens {nundecl} -> "
              f"{'WORSE than honest' if worse else 'no worse on this roll'}")
        named.append(fant)
        bearings.append(worse)
    chk("the merge names every fantasy bridge and refuses to promote it",
        named, [[p] for p in FANTASIES])
    # WHY THIS IS NOT "each board is condemned" ANY MORE.  That test assumed a
    # corrupted DSN always costs a connection, and MEASURED 2026-08-02 it does
    # not: the BZ1-1 roll came back 0 rat lines and COMPLETE against an honest
    # board carrying 2 declared jumpers, because the router had found real
    # copper for everything and the front stub it left on that lead was
    # redundant.  Demanding a conviction there is demanding that pcb-rnd
    # convict a board with nothing wrong with it, which is how a check starts
    # lying.  (A "was the bridge load-bearing" proxy was built first, comparing
    # the physical board against the board the DSN claimed; it was removed
    # because it answered a different question and mislabelled both rolls.)
    #
    # What still has to be true every time is the REFUSAL — checked above, and
    # it never relaxes — plus this: the corruption must be shown to COST
    # something somewhere, or the section is passing on router luck.  If a
    # future roll makes every fantasy harmless, this fails loudly and the fix
    # is a fantasy pin the router cannot avoid leaning on, not a softer bar.
    chk("a board built on a fantasy bridge is measurably worse than the "
        "honest board (at least one)", any(bearings), True)

    # -- the DECLARED-JUMPER law's own control ------------------------------
    # A declaration that cannot fail is a waiver wearing a checklist.  So cut
    # one routed segment out of the FINAL merge — a break the declaration says
    # nothing about — and prove the audit names the net and refuses.  The cut
    # is chosen deterministically: the longest bottom segment on a net that is
    # NOT the declared one, so it is guaranteed to open something new.
    cand = sorted(
        ((round(math.hypot(t[3] - t[1], t[4] - t[2]), 3), i)
         for i, (t, n) in enumerate(zip(m["tracks"], m["track_nets"]))
         if t[0] == "bottom" and n not in
         ({j["net"] for j in DECLARED_JUMPERS} | POUR_WELDED_NETS | {None})),
        reverse=True)
    if cand:
        cut = cand[0][1]
        hurt = {k: (list(v) if isinstance(v, list) else v) for k, v in m.items()}
        hurt["tracks"] = [t for i, t in enumerate(m["tracks"]) if i != cut]
        hurt["track_nets"] = [n for i, n in enumerate(m["track_nets"])
                              if i != cut]
        broke = m["track_nets"][cut]
        _sat, undecl = jumper_audit(parts, route, hurt)
        print(f"    control: the longest {broke} segment is cut from the "
              f"merge; the audit reports {undecl}")
        chk("an UNDECLARED open is named and refused",
            bool(undecl) and all(n != DECLARED_JUMPERS[0]["net"]
                                 for n, _k in undecl) if DECLARED_JUMPERS
            else bool(undecl), True)

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

    print("### G2. SILK ON THE BOARD THAT SHIPS ###")
    # R4a's gate measures the UNROUTED board's legend, which is the board
    # nobody machines.  23 wire vias later the apertures are different, and
    # 2026-08-02 is the day that difference put three labels on via rings with
    # every check passing.  The audit runs HERE, on the final placement.
    srows = TB.silk_seats(parts, b["labels"], route)
    sflags = TB.silk_flags(srows)
    for side in ("front", "back"):
        sub = [r for r in srows if r["side"] == side]
        wf = min(sub, key=lambda r: r["feature_gap"])
        wt = min(sub, key=lambda r: r["text_gap"] - r["text_bar"])
        wa = max((r for r in sub if r["ratio"] is not None),
                 key=lambda r: r["ratio"])
        print(f"    {side}: {len(sub)} texts vs {sub[0]['n_features']} "
              f"features — ink {wf['feature_gap']:.3f} ({wf['item']} -> "
              f"{wf['feature']}), text {wt['text_gap']:.3f}/"
              f"{wt['text_bar']:.2f} ({wt['item']} -> {wt['text']}), "
              f"attribution {wa['ratio']:.2f} ({wa['item']})")
    for f in sflags[:12]:
        print(f"      FLAG {f}")
    chk("crowding audit on the ROUTED board: zero flags", len(sflags), 0)
    for name, hurt in TB.silk_controls(parts, b["labels"]):
        got = TB.silk_flags(TB.silk_seats(parts, hurt, route))
        chk(f"the audit convicts {name.split('|')[0]}",
            any(name.split("|")[1] in g for g in got), True)
    print(f"    {b['placed']}/{TB.N_LABELS} ref labels placed; dropped "
          f"{b['unplaced']}")
    for side in ("front", "back"):
        for nm, owner, why in b.get("silk_drops", {}).get(side, ()):
            print(f"    LEGEND DROP {side}/{nm} (owner {owner}): {why}")

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
    # This USED to assert lost > rats — "the pour is a real conductor, not
    # decoration".  On the seeded board that is FALSE and the falsity is an
    # improvement: with 28 wire vias the router closes GND entirely in copper,
    # so no terminal depends on the fill and deleting it changes nothing.
    #
    # The incident this check was written for (2026-08-01: a clipper failure
    # silently DISCARDED the back plane and eleven SMD GND terminals came open
    # while DRC stayed clean) is still guarded, and by two sharper checks than
    # this one ever was: gate A refuses any clipper failure outright, and the
    # jumper audit refuses any undeclared open on any net including GND.  What
    # remains worth asserting here is the hazard that a REDUNDANT pour could
    # still hide — a plane that is FLOATING, i.e. a slab of copper attached to
    # nothing, which is a real antenna and a real short risk on a handled
    # board.  Deleting copper can never IMPROVE connectivity, so a floating
    # plane shows up as lost < rats; a live-or-redundant one cannot.
    chk("deleting the back pour never improves connectivity (it is not "
        "floating copper masking an open)", lost >= rats, True)
    role = ("LOAD-BEARING" if lost > rats
            else "REDUNDANT — GND closes in copper alone")
    print(f"    the back plane is {role}; either is legal, floating is not")
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
    for net, npieces, pa, pb, faces in mm.get("unclosed", ()):
        print(f"  UNCLOSED {net}: {npieces} pieces, no legal path "
              f"{pa} <-> {pb} on {faces}")
    if mm["fantasy_bridges"]:
        print(f"  FANTASY BRIDGES (never promoted): {mm['fantasy_bridges']}")
    sys.exit(gate(bb) if "--gate" in sys.argv else 0)
