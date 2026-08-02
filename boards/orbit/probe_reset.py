#!/usr/bin/env python3
"""DIAGNOSTIC — does a LEGAL journey exist from U1-1 to the rest of RESET?

Emits no copper.  It asks pathfind.py the question closing_tracks' four-shape
vocabulary cannot ask, and prints the answer as measured geometry: the runs,
the vias, and the tightest gap each segment holds against every other net.

Usage:  python3 probe_reset.py [NET ...]      (default RESET)
"""

from __future__ import annotations

import importlib.util
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pathfind                                           # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


TR = _load("tools_route", os.path.join(HERE, "tools-route.py"))
TB = TR.TB


def oracles(parts, others, net, w):
    """The SAME rules closing_tracks applies, as callables."""
    bodies = [(p.x, p.y, 1.5 if p.kind == "rect" else 2.0)
              for part in parts for p in part.pins]

    def via_ok(x, y, n):
        if not (3.0 < x < TB.BOARD_W - 3.0 and 3.0 < y < TB.BOARD_H - 3.0):
            return False
        for bx, by, keep in bodies:
            if math.hypot(bx - x, by - y) < keep + TB.RING_VIA / 2:
                return False
        for n2, _l2, p2, r2 in others:
            if n2 in (None, n):
                continue
            if TB.shape_gap(([(x, y)], TB.RING_VIA / 2), (p2, r2)) < TB.CLEAR:
                return False
        return True

    def gap_of(a, b, face):
        worst = 99.0
        for n2, l2, p2, r2 in others:
            if l2 != face or n2 in (None, net):
                continue
            g = TB.shape_gap(([a, b], w / 2), (p2, r2))
            worst = min(worst, g)
        return worst

    return via_ok, gap_of


def main(nets, via_costs=(pathfind.VIA_COST,)):
    b = TR.build_routed(False)
    m = b["merge"]
    parts = b["parts"]
    route = {"tracks": m["tracks"], "track_nets": m["track_nets"],
             "vias": m["vias"], "promoted": set(m["promoted"])}
    others = TB.copper_objects(parts, route)
    tracks = [tuple(t) + (n,) for t, n in zip(m["tracks"], m["track_nets"])]
    promoted = set(m["promoted"])
    for net in nets:
        objs = TR.net_copper(net, parts, tracks, m["vias"], promoted)
        comps = TR.components(objs)
        print(f"\n=== {net}: {len(objs)} pieces of copper in "
              f"{len(comps)} component(s) ===")
        if len(comps) < 2:
            print("    already closed")
            continue
        w = TB.RAIL if net in TR.RAIL_NETS else TB.TRACK
        via_ok, gap_of = oracles(parts, others, net, w)

        def seg_ok(a, bpt, face):
            return gap_of(a, bpt, face) >= TB.CLEAR

        head = comps[0]
        rest = [objs[i] for c in comps[1:] for i in c]
        start = objs[head[0]]
        start_pt = TR.anchor(start, rest[0][1][0])
        face = sorted(start[0])[0]
        print(f"    from {start_pt} on {face} to {len(rest)} target pieces")
        for vc in via_costs:
            got = pathfind.route_between(TB, others, net, start_pt, rest, w,
                                         via_ok, seg_ok, start_face=face,
                                         via_cost=vc)
            if got is None:
                print(f"    via_cost {vc}: NO LEGAL PATH")
                continue
            runs, vias = got
            total = 0.0
            for f, pts in runs:
                for p, qq in zip(pts, pts[1:]):
                    d = math.dist(p, qq)
                    total += d
                    if len(via_costs) == 1:
                        print(f"    {f:6s} ({p[0]:6.2f},{p[1]:6.2f}) -> "
                              f"({qq[0]:6.2f},{qq[1]:6.2f})  {d:5.2f} mm  "
                              f"gap {gap_of(p, qq, f):.3f}")
            print(f"    via_cost {vc}: TOTAL {total:.2f} mm of {w} track, "
                  f"{len(vias)} via(s): "
                  f"{[(round(v[0], 3), round(v[1], 3)) for v in vias]}")


if __name__ == "__main__":
    args = sys.argv[1:]
    costs = [float(a.split("=")[1]) for a in args if a.startswith("--via-cost")]
    main([a for a in args if not a.startswith("--")] or ["RESET"],
         costs or (pathfind.VIA_COST,))
