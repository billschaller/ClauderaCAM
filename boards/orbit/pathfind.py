#!/usr/bin/env python3
"""A LEGAL-BY-CONSTRUCTION path search across a finished board (R4b endgame).

Why this exists, measured
-------------------------
`closing_tracks` can only say four shapes: a straight run, one bend near the
midpoint, and one short escape leg at either end.  That vocabulary closes a
gap in an open pocket and cannot state a JOURNEY.  RESET's last rat is a
journey: U1-1 sits in the ring interior and the rest of the net is 25 mm away
in the south-east corner, past twelve LED lead pairs and a fully routed board.

The old ledger blamed U1-1's escape ("only lane 130-145 deg, +0.645 mm").
probe_lane.py re-measured it on the GROWN board: U1-1 has a 2.375 mm corridor
and an 8.00 mm legal 0.6 stub at bearing 75-85.  The pin was never trapped —
the vocabulary was.

Method: rasterize every different-net obstacle into an occupancy grid (a cell
is free when a track CENTRE there clears the law), A* across both faces with a
via to change face, then throw the grid away and re-verify every emitted
segment against the SAME independent oracle the gate uses.  The grid is a
SEARCH HEURISTIC ONLY; nothing it says is ever trusted into copper.  If
verification fails the path is discarded and the net stays honestly open.

Deterministic: fixed grid, fixed neighbour order, ties broken on (f, g, face,
i, j), and the caller's validator is the same pure geometry every time.
"""

from __future__ import annotations

import heapq
import math

STEP = 0.25              # grid pitch, mm — four cells to the tightest corridor
VIA_COST = 8.0           # mm-equivalent: a via is a threaded wire, not free
NB = ((1, 0), (-1, 0), (0, 1), (0, -1),
      (1, 1), (1, -1), (-1, 1), (-1, -1))


class Grid:
    """Free/blocked per face for a track of width *w* on net *net*."""

    def __init__(self, tb, others, net, w, faces=("bottom", "top"),
                 step: float = STEP, clear=None):
        self.tb, self.step, self.faces = tb, step, faces
        self.clear = tb.CLEAR if clear is None else clear
        self.nx = int(round(tb.BOARD_W / step)) + 1
        self.ny = int(round(tb.BOARD_H / step)) + 1
        self.blocked = {f: bytearray(self.nx * self.ny) for f in faces}
        self._edge_mask(w)
        margin = w / 2 + self.clear
        for n2, l2, p2, r2 in others:
            if n2 is None or n2 == net or l2 not in self.blocked:
                continue
            self._stamp(self.blocked[l2], p2, r2, margin, w)

    # -- geometry ---------------------------------------------------------
    def xy(self, i, j):
        return (i * self.step, j * self.step)

    def cell(self, x, y):
        return (min(max(int(round(x / self.step)), 0), self.nx - 1),
                min(max(int(round(y / self.step)), 0), self.ny - 1))

    def _edge_mask(self, w):
        """Copper keeps EDGE_CLEAR from the outline, so the search does too.

        The standard rounded-rect inside test: how far the point lies outside
        the corner-centre core rectangle, measured as one radius."""
        tb = self.tb
        inset = w / 2 + tb.EDGE_CLEAR
        r = max(tb.CORNER_R - inset, 0.0)
        x0, y0 = inset, inset
        x1, y1 = tb.BOARD_W - inset, tb.BOARD_H - inset
        for j in range(self.ny):
            for i in range(self.nx):
                x, y = self.xy(i, j)
                dx = max(x0 + r - x, 0.0, x - (x1 - r))
                dy = max(y0 + r - y, 0.0, y - (y1 - r))
                if not (x0 <= x <= x1 and y0 <= y <= y1
                        and dx * dx + dy * dy <= r * r):
                    for f in self.faces:
                        self.blocked[f][j * self.nx + i] = 1

    def _stamp(self, mask, pts, rad, margin, w):
        reach = rad + margin
        i0, i1 = self.cell(min(p[0] for p in pts) - reach, 0)[0], \
            self.cell(max(p[0] for p in pts) + reach, 0)[0]
        j0, j1 = self.cell(0, min(p[1] for p in pts) - reach)[1], \
            self.cell(0, max(p[1] for p in pts) + reach)[1]
        gap = self.tb.shape_gap
        for j in range(j0, j1 + 1):
            row = j * self.nx
            for i in range(i0, i1 + 1):
                if mask[row + i]:
                    continue
                if gap(([self.xy(i, j)], w / 2), (pts, rad)) < self.clear:
                    mask[row + i] = 1

    def mark_goals(self, goals):
        """-> {face: set(index)} of cells that TOUCH the target copper."""
        out = {f: set() for f in self.faces}
        gap = self.tb.shape_gap
        for faces, pts, rad in goals:
            for f in faces:
                if f not in out:
                    continue
                i0 = self.cell(min(p[0] for p in pts) - rad - self.step, 0)[0]
                i1 = self.cell(max(p[0] for p in pts) + rad + self.step, 0)[0]
                j0 = self.cell(0, min(p[1] for p in pts) - rad - self.step)[1]
                j1 = self.cell(0, max(p[1] for p in pts) + rad + self.step)[1]
                for j in range(j0, j1 + 1):
                    for i in range(i0, i1 + 1):
                        if gap(([self.xy(i, j)], 0.0), (pts, rad)) <= 0.0:
                            out[f].add(j * self.nx + i)
        return out


def distance_field(grid, goal):
    """Cost-to-go lower bound: the cheapest grid walk to the goal set when a
    cell is free if EITHER face is free and layer changes are free.

    Every real path is one of those walks with extra restrictions and a via
    bill, so this never over-states the remaining cost — which is the property
    A* needs.  The 8-neighbour metric overshoots true Euclidean by up to 8% on
    a diagonal, so the field is scaled below that; the discount costs a little
    search and keeps the answer optimal instead of merely quick.
    """
    n = grid.nx * grid.ny
    free = bytearray(n)
    masks = [grid.blocked[f] for f in grid.faces]
    for k in range(n):
        free[k] = 0 if all(m[k] for m in masks) else 1
    dist = [float("inf")] * n
    q = []
    for f in grid.faces:
        for k in goal[f]:
            if dist[k] > 0.0:
                dist[k] = 0.0
                heapq.heappush(q, (0.0, k))
    nx, ny, step = grid.nx, grid.ny, grid.step
    while q:
        d, k = heapq.heappop(q)
        if d > dist[k]:
            continue
        i, j = k % nx, k // nx
        for di, dj in NB:
            i2, j2 = i + di, j + dj
            if not (0 <= i2 < nx and 0 <= j2 < ny):
                continue
            k2 = j2 * nx + i2
            if not free[k2]:
                continue
            d2 = d + step * (1.41421356 if di and dj else 1.0)
            if d2 < dist[k2]:
                dist[k2] = d2
                heapq.heappush(q, (d2, k2))
    return [0.0 if d == float("inf") else d * 0.92 for d in dist]


def search(grid, start_pt, goals, net, via_ok, via_cost=VIA_COST,
           start_face="bottom"):
    """A* over (face, cell).  -> [(face, (x, y)), ...] or None."""
    goal = grid.mark_goals(goals)
    if not any(goal.values()):
        return None
    nx, faces = grid.nx, list(grid.faces)
    # THE HEURISTIC IS A MEASUREMENT, not a guess at one, and the first draft
    # of this file proves why it matters: an h() that read the goal objects'
    # ANCHOR POINTS over-estimated whenever a cell touched a long goal segment
    # far from either end, which is exactly what a routed net looks like.  A*
    # is only optimal under an admissible h, so that draft returned an 84.4 mm
    # detour around three sides of the board for a 27 mm gap.  The field below
    # is the cost of the same journey with BOTH faces free and no via to pay
    # for — never more than the real answer, and it knows about walls, which a
    # straight-line estimate cannot.
    field = distance_field(grid, goal)

    def h(face, k):
        return field[k]

    si, sj = grid.cell(*start_pt)
    seeds = []
    for dj in range(-6, 7):
        for di in range(-6, 7):
            i, j = si + di, sj + dj
            if not (0 <= i < nx and 0 <= j < grid.ny):
                continue
            k = j * nx + i
            if grid.blocked[start_face][k]:
                continue
            d = math.dist(start_pt, grid.xy(i, j))
            if d <= 1.5:
                seeds.append((d, k))
    if not seeds:
        return None
    open_q, best, came = [], {}, {}
    for d, k in sorted(seeds):
        st = (start_face, k)
        if st not in best or d < best[st]:
            best[st] = d
            heapq.heappush(open_q, (d + h(*st), d, start_face, k))
    seen = set()
    while open_q:
        _f, gcost, face, k = heapq.heappop(open_q)
        if (face, k) in seen:
            continue
        seen.add((face, k))
        if k in goal[face]:
            path, cur = [], (face, k)
            while cur in came:
                path.append(cur)
                cur = came[cur]
            path.append(cur)
            path.reverse()
            return [(f, grid.xy(kk % nx, kk // nx)) for f, kk in path]
        i, j = k % nx, k // nx
        for di, dj in NB:
            i2, j2 = i + di, j + dj
            if not (0 <= i2 < nx and 0 <= j2 < grid.ny):
                continue
            k2 = j2 * nx + i2
            if grid.blocked[face][k2] or (face, k2) in seen:
                continue
            c = gcost + grid.step * (1.41421356 if di and dj else 1.0)
            if c < best.get((face, k2), 1e18):
                best[(face, k2)] = c
                came[(face, k2)] = (face, k)
                heapq.heappush(open_q, (c + h(face, k2), c, face, k2))
        for f2 in faces:
            if f2 == face or grid.blocked[f2][k] or (f2, k) in seen:
                continue
            x, y = grid.xy(i, j)
            if not via_ok(x, y, net):
                continue
            c = gcost + via_cost
            if c < best.get((f2, k), 1e18):
                best[(f2, k)] = c
                came[(f2, k)] = (face, k)
                heapq.heappush(open_q, (c + h(f2, k), c, f2, k))
    return None


def simplify(runs, seg_ok):
    """Greedy line-of-sight, judged by the CALLER'S oracle, not the grid."""
    out = []
    for face, pts in runs:
        keep, i = [pts[0]], 0
        while i < len(pts) - 1:
            j = len(pts) - 1
            while j > i + 1 and not seg_ok(pts[i], pts[j], face):
                j -= 1
            keep.append(pts[j])
            i = j
        out.append((face, keep))
    return out


def to_runs(path):
    """[(face, pt), ...] -> ([(face, [pt, ...]), ...], [via pt, ...])."""
    runs, vias = [], []
    cur_face, cur = path[0][0], [path[0][1]]
    for face, pt in path[1:]:
        if face != cur_face:
            vias.append(pt)
            runs.append((cur_face, cur))
            cur_face, cur = face, [pt]
        else:
            cur.append(pt)
    runs.append((cur_face, cur))
    return [r for r in runs if len(r[1]) > 1 or len(runs) == 1], vias


def route_between(tb, others, net, start_pt, goals, w, via_ok, seg_ok,
                  step=STEP, via_cost=VIA_COST, start_face="bottom",
                  clear=None):
    """The whole journey: grid, A*, simplify, RE-VERIFY.  -> (runs, vias)|None.

    The re-verification is not belt-and-braces, it is the contract: the grid
    tests a track CENTRE at discrete points, the oracle tests the whole swept
    segment.  Only what the oracle passes may become copper.

    *clear* is the gap the search holds.  The caller passes more than the LAW
    for the same reason emit_lihata's copper does (see ROUTE_CLEAR): a journey
    grazes obstacles for its whole length, and a geometry that must pass a '<'
    test cannot sit ON the number in the test — a closure accepted at exactly
    0.400 measured 0.403 to our own scan and read as one "shorted nets: net too
    close to other net" to pcb-rnd.  The law does not move; the copper clears it.
    """
    grid = Grid(tb, others, net, w, step=step, clear=clear)
    path = search(grid, start_pt, goals, net, via_ok, via_cost, start_face)
    if path is None:
        return None
    runs, vias = to_runs(path)
    runs[0] = (runs[0][0], [start_pt] + runs[0][1])
    runs = simplify(runs, seg_ok)
    for face, pts in runs:
        for a, b in zip(pts, pts[1:]):
            if a != b and not seg_ok(a, b, face):
                return None
    for v in vias:
        if not via_ok(v[0], v[1], net):
            return None
    return runs, vias
