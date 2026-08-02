#!/usr/bin/env python3
"""DIAGNOSTIC — what BOUNDS a pin's escape lane, measured, by bearing.

Nothing here emits copper or changes a gate.  It answers one question the
R4b ledger kept asserting without evidence: U1-1's "only lane is 130-145 deg,
+0.645 mm" says HOW WIDE the lane is and never says WHAT WALLS IT.  Until the
two walls are named, the choice between moving a part, rotating U1, and giving
up is a guess.

Method: the SAME geometry the gate's independent clearance oracle uses
(TB.shape_gap over TB.copper_objects), relabelled so every obstacle can be
named.  The object list is rebuilt here in copper_objects' own iteration order
and cross-checked against it object for object, so a relabelling that drifts
out of step is a hard error rather than a mislabelled answer.

Usage:  python3 probe_lane.py [PID ...]      (default U1-1)
"""

from __future__ import annotations

import importlib.util
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


TR = _load("tools_route", os.path.join(HERE, "tools-route.py"))
TB = TR.TB


def labeled_objects(parts, route):
    """(label, net, layer, points, radius) — TB.copper_objects with names.

    The order below IS copper_objects' order; assert_parity() proves it.
    """
    out = []
    anon = 0
    promoted = set((route or {}).get("promoted", ()))
    for part in parts:
        for p in part.pins:
            # netless copper carries a private pseudo-net, exactly as the
            # oracle gives it one (tools-board.copper_objects)
            net = p.net if p.net is not None else f"__nc_{p.pid}"
            if p.kind == "rect":
                out.append((f"{p.pid} land", net, "bottom", p.corners(), 0.0))
            elif p.kind == "circ":
                out.append((f"{p.pid} pad", net, "bottom", [(p.x, p.y)],
                            p.shape[1] / 2))
            else:
                r = p.shape[2] / 2
                out.append((f"{p.pid} ring", net, "bottom", [(p.x, p.y)], r))
                # a dead front ring is on no net until a human solders it
                out.append((f"{p.pid} front ring",
                            net if p.pid in promoted
                            else f"__dead_{p.pid}", "top", [(p.x, p.y)], r))
    for ref, (gx, gy) in TB.GAUGES.items():
        for layer in ("top", "bottom"):
            anon += 1
            out.append((f"gauge {ref}", f"__gauge{anon}", layer,
                        [(gx, gy)], TB.RING_GAUGE / 2))
    for layer, net, width, pts in TB.fixed_tracks() + TB.board_only_tracks():
        for a, b in zip(pts, pts[1:]):
            out.append((f"fixed {net} track", net, layer, [a, b], width / 2))
    r = route or {}
    for i, (lay, x1, y1, x2, y2, w) in enumerate(r.get("tracks", ())):
        net = r.get("track_nets", [None] * (i + 1))[i]
        out.append((f"routed {net} seg{i}", net, lay,
                    [(x1, TB.BOARD_H - y1), (x2, TB.BOARD_H - y2)], w / 2))
    for i, (vx, vy, vnet) in enumerate(r.get("vias", ())):
        for lay in ("top", "bottom"):
            out.append((f"via V{i + 1}", vnet, lay,
                        [(vx, TB.BOARD_H - vy)], TB.RING_VIA / 2))
    return out


def assert_parity(parts, route):
    """The relabelled list must BE the oracle's list, object for object."""
    mine = labeled_objects(parts, route)
    theirs = TB.copper_objects(parts, route)
    if len(mine) != len(theirs):
        raise SystemExit(f"probe out of step: {len(mine)} vs {len(theirs)}")
    for k, ((_lab, na, la, pa, ra), (nb, lb, pb, rb)) in enumerate(
            zip(mine, theirs)):
        same_net = na == nb or (str(na).startswith("__gauge")
                                and str(nb).startswith("__gauge"))
        if not (same_net and la == lb and pa == pb and ra == rb):
            raise SystemExit(f"probe out of step at object {k}: "
                             f"{mine[k][:3]} vs {theirs[k][:3]}")
    return mine


def centroid(pts):
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def scan_bearing(pa, objs, net, face, deg, width, reach=8.0, step=0.25):
    """-> (max legal stub length, [(gap, label, net, side), ...] at that length).

    A stub is legal while a *width*-wide track from *pa* along *deg* holds
    CLEAR against every different-net object on *face*.  The blocking objects
    are reported with the side they sit on (L/R of the outbound ray), which is
    what turns "the lane is 0.645" into "these two things are 0.645 apart".
    """
    dx, dy = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    best, blockers = 0.0, []
    n = int(reach / step)
    for k in range(1, n + 1):
        ell = step * k
        end = (pa[0] + dx * ell, pa[1] + dy * ell)
        rows = []
        for lab, onet, olay, pts, orad in objs:
            if olay != face or onet == net or onet is None:
                continue
            g = TB.shape_gap(([pa, end], width / 2), (pts, orad))
            if g < TB.CLEAR + 0.6:
                cx, cy = centroid(pts)
                side = "L" if (dx * (cy - pa[1]) - dy * (cx - pa[0])) > 0 else "R"
                rows.append((round(g, 3), lab, onet, side))
        rows.sort()
        if rows and rows[0][0] < TB.CLEAR:
            return best, blockers
        best, blockers = ell, rows[:4]
    return best, blockers


def lane_width(pa, objs, net, face, deg, reach=4.0):
    """The free corridor width across a zero-width ray of length *reach*:
    nearest obstacle to the LEFT + nearest to the RIGHT, and their names."""
    dx, dy = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    end = (pa[0] + dx * reach, pa[1] + dy * reach)
    sides = {"L": (99.0, "-", "-"), "R": (99.0, "-", "-")}
    for lab, onet, olay, pts, orad in objs:
        if olay != face or onet == net or onet is None:
            continue
        g = TB.shape_gap(([pa, end], 0.0), (pts, orad))
        cx, cy = centroid(pts)
        side = "L" if (dx * (cy - pa[1]) - dy * (cx - pa[0])) > 0 else "R"
        if g < sides[side][0]:
            sides[side] = (round(g, 3), lab, onet)
    return sides


def report(pid, b, objs, width=None):
    by_pid = {p.pid: p for part in b["parts"] for p in part.pins}
    pin = by_pid[pid]
    pa = (pin.x, pin.y)
    face = "bottom"
    width = width or TB.TRACK
    print(f"\n=== {pid} ({pin.net}) at ({pin.x}, {pin.y}), {face}, "
          f"track {width} ===")
    print(f"    needs a corridor of {width + 2 * TB.CLEAR:.2f} mm "
          f"(track {width} + {TB.CLEAR} clearance each side)")
    print("  bearing  stub  corridor   left wall                       "
          "  right wall")
    rows = []
    for deg in range(0, 360, 5):
        stub, blk = scan_bearing(pa, objs, pin.net, face, deg, width)
        s = lane_width(pa, objs, pin.net, face, deg)
        lane = s["L"][0] + s["R"][0]
        rows.append((deg, stub, lane, s, blk))
    for deg, stub, lane, s, blk in rows:
        star = " <<<" if stub >= 2.0 else ""
        print(f"  {deg:5d}  {stub:4.2f}  {min(lane, 99):7.3f}   "
              f"{s['L'][1][:22]:22s} {s['L'][0]:6.3f}   "
              f"{s['R'][1][:22]:22s} {s['R'][0]:6.3f}{star}")
    best = max(rows, key=lambda r: (r[1], r[2]))
    print(f"  BEST bearing {best[0]}: stub {best[1]:.2f} mm, "
          f"corridor {best[2]:.3f} mm")
    print("  nearest different-net copper to this pad, any bearing:")
    near = sorted((round(TB.shape_gap((pin.corners() if pin.kind == "rect"
                                       else [pa], 0.0), (pts, orad)), 3),
                   lab, onet)
                  for lab, onet, olay, pts, orad in objs
                  if olay == face and onet not in (None, pin.net))[:10]
    for g, lab, onet in near:
        print(f"    {g:7.3f}  {lab:28s} {onet}")


if __name__ == "__main__":
    b = TR.build_routed(False)
    m = b["merge"]
    route = {"tracks": m["tracks"], "track_nets": m["track_nets"],
             "vias": m["vias"], "promoted": set(m["promoted"])}
    objs = assert_parity(b["parts"], route)
    print(f"{len(objs)} copper objects, parity with the gate's oracle OK")
    for pid in (sys.argv[1:] or ["U1-1"]):
        report(pid, b, objs)
