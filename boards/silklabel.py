"""Automatic silkscreen reference-label placement (board-side tooling).

Bill 2026-07-30: "lots of unlabelled parts and the positioning isn't
great … without claude having to manually position them (I don't want
that)." This module IS that: layout scripts hand it rectangles, it hands
back deterministic label positions, and nobody ever hand-tunes a label
coordinate again. Pure geometry (mm, y-down board frame), no pcbnew — the
glue that measures real text bboxes and builds obstacle rects lives in the
board's tools-layout.py, so this file is importable and testable anywhere.

The contract with the CAM gate, encoded as candidate REJECTION here so the
generator cannot emit a label the gate would eat or refuse:
  * reemit.silk_strokes clips every stroke that comes within `clearance`
    (job: 0.30) of a mask aperture — a label bbox must stay CLIP_KEEPOUT
    clear of every aperture or the laser legend arrives pre-chewed.
  * checks.silk_metric_checks floors text height at 1.0 (fab-house floor,
    dfm-notes §9) — callers pass the measured bbox of 1.0-high text.
  * labels must not sit on other silk (unreadable) or off the board.

Greedy, most-constrained-first: parts with the fewest surviving candidate
slots claim theirs before roomy parts eat the space. Each placed label
becomes an obstacle for the rest. A part with NO surviving slot is
reported unplaced, never forced — a missing label is a bench note, an
overlapping one is misinformation.
"""

from __future__ import annotations

from dataclasses import dataclass

# keep-clear margins (mm), each traced to the law it protects
CLIP_KEEPOUT = 0.40   # silk-clip clearance 0.30 + raster/kerf slack 0.10:
                      # a label inside this band gets clipped mid-glyph
SILK_GAP = 0.20       # between a label and any existing silk ink
EDGE_MARGIN = 1.00    # cutout rides the outline; legend near the rim chips
LABEL_GAP = 0.30      # between two placed labels

# --- the two laws a CALLER may raise, 2026-08-02 -------------------------
# Both defaults are the numbers this module shipped with, so a caller that
# passes neither gets byte-identical placements (Board A's coupon legend is
# exactly that caller). Orbit raises both, and the incident is in its
# tools-board.label_parts: the front render came back reading "PAD1ON" —
# a ref label and the ON legend fused into one word by a 0.30/0.20 gap that
# is smaller than the 0.425 gap between two glyphs of the SAME word, so the
# eye groups them wrongly. Text separation must beat intra-word spacing.
#
# ATTRIBUTION (operator, 2026-08-02): "if a human can't tell what board
# feature a label would refer to, the label shouldn't exist or the board
# should be decompressed." A label is a claim about ONE feature; a seat
# equidistant between its own part and a neighbour makes that claim
# unreadable, and an unreadable label is misinformation, not decoration.
# Expressed as a ratio because it is a RELATIVE judgement — the eye binds a
# label to the nearest thing, so the owner must be decisively nearest.

# candidate slots around a part's courtyard, in preference order: reading
# convention first (above, then below), then the sides, then corners
_SLOTS = ("N", "S", "E", "W", "NE", "NW", "SE", "SW")
_GAPS = (0.30, 0.55, 0.90, 1.40, 2.00, 2.80)   # courtyard -> label gap,
                             # nearest first; the far tiers exist for the
                             # dense middle of a 2.54-pitch field, where
                             # the nearest open silk is a part-and-a-half
                             # away (Board A measured 29/48 without them)


@dataclass(frozen=True)
class Rect:
    """Axis-aligned rect, y-down board mm. x1 > x0, y1 > y0."""
    x0: float
    y0: float
    x1: float
    y1: float

    def inflate(self, d: float) -> "Rect":
        return Rect(self.x0 - d, self.y0 - d, self.x1 + d, self.y1 + d)

    def overlaps(self, o: "Rect") -> bool:
        return not (self.x1 <= o.x0 or o.x1 <= self.x0
                    or self.y1 <= o.y0 or o.y1 <= self.y0)

    def overlap_area(self, o: "Rect") -> float:
        w = min(self.x1, o.x1) - max(self.x0, o.x0)
        h = min(self.y1, o.y1) - max(self.y0, o.y0)
        return w * h if (w > 0 and h > 0) else 0.0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass(frozen=True)
class Part:
    ref: str
    body: Rect            # courtyard (fallback: pads+graphics bbox)
    wh: tuple[float, float]      # measured label bbox at rot 0
    wh90: tuple[float, float]    # measured label bbox at rot 90


@dataclass(frozen=True)
class Placement:
    ref: str
    x: float             # label bbox CENTER, board mm (y-down)
    y: float
    rot: int             # 0 or 90
    slot: str


_SLIDES = (0.0, -0.5, 0.5, -1.0, 1.0)   # slide along the slot edge, in
                                        # body-half-extents; centred first


def _candidates(p: Part, board: Rect, gaps=_GAPS):
    """Every slot x gap x rotation x slide as (rect, rot, slot, rank).
    Slides shift the label ALONG its side (N/S: in x, E/W: in y) — in a
    dense field the open silk is usually off-centre of the part, between
    two neighbours, and a centred-only ring never finds it."""
    out = []
    rank = 0
    b = p.body
    for gap in gaps:
        for slot in _SLOTS:
            for rot, (w, h) in (((0, p.wh)), (90, p.wh90)):
                if slot == "N":
                    cx, cy, axis = b.cx, b.y0 - gap - h / 2, "x"
                elif slot == "S":
                    cx, cy, axis = b.cx, b.y1 + gap + h / 2, "x"
                elif slot == "E":
                    cx, cy, axis = b.x1 + gap + w / 2, b.cy, "y"
                elif slot == "W":
                    cx, cy, axis = b.x0 - gap - w / 2, b.cy, "y"
                elif slot == "NE":
                    cx, cy, axis = b.x1 + gap + w / 2, b.y0 - gap - h / 2, ""
                elif slot == "NW":
                    cx, cy, axis = b.x0 - gap - w / 2, b.y0 - gap - h / 2, ""
                elif slot == "SE":
                    cx, cy, axis = b.x1 + gap + w / 2, b.y1 + gap + h / 2, ""
                else:
                    cx, cy, axis = b.x0 - gap - w / 2, b.y1 + gap + h / 2, ""
                half = ((b.x1 - b.x0) if axis == "x"
                        else (b.y1 - b.y0)) / 2
                for k in (_SLIDES if axis else (0.0,)):
                    sx = cx + (k * half if axis == "x" else 0.0)
                    sy = cy + (k * half if axis == "y" else 0.0)
                    r = Rect(sx - w / 2, sy - h / 2, sx + w / 2, sy + h / 2)
                    if (r.x0 < board.x0 + EDGE_MARGIN
                            or r.x1 > board.x1 - EDGE_MARGIN
                            or r.y0 < board.y0 + EDGE_MARGIN
                            or r.y1 > board.y1 - EDGE_MARGIN):
                        rank += 1
                        continue
                    out.append((r, rot, slot, rank))
                    rank += 1
    return out


BODY_PENALTY = 12.0   # score per mm² of neighbour-courtyard overlap: a
                      # slot fully clear always beats one on a body, but a
                      # corner-brush beats exile to the far side of the board


def _valid(rect: Rect, apertures, silk, hard):
    """Hard laws only: apertures (the clip eats it), existing silk
    (unreadable), declared keep-clear zones (misleading)."""
    if any(rect.overlaps(a) for a in apertures):
        return False
    if any(rect.overlaps(s) for s in silk):
        return False
    if any(rect.overlaps(h) for h in hard):
        return False
    return True


def place_labels(parts: list[Part], board: Rect,
                 apertures: list[Rect], silk: list[Rect],
                 bodies_extra: list[Rect] = (),
                 label_gap: float = LABEL_GAP,
                 silk_gap: float = SILK_GAP,
                 attribution=None,
                 attribution_max: float = 0.5,
                 gaps=_GAPS,
                 ) -> tuple[list[Placement], list[str]]:
    """-> (placements, unplaced refs). Deterministic for identical input.

    Inflation happens HERE, one place, so every caller gets the same law:
    apertures grow CLIP_KEEPOUT, silk grows `silk_gap`. Hard rejections:
    apertures, silk, `bodies_extra` (declared keep-clear zones — gauge
    fields, washer shadows), the board-edge margin, and already-placed
    labels. Part courtyards are a SOFT penalty (BODY_PENALTY per mm²):
    in a 2.54-pitch field a hard courtyard rule strands most labels
    (measured 12/48 on Board A), while a corner-brush over a neighbour
    is still legible — the score prefers clear slots whenever one exists.

    `label_gap` / `silk_gap` are the ink-to-ink separations between this
    label and, respectively, another label and pre-existing legend ink.
    Their defaults are what this module has always used; a caller whose
    legend must never fuse two texts into one word raises them (orbit
    passes 0.50 for both — see the module header).

    `gaps` are the courtyard-to-label standoff tiers to try, nearest
    first. A caller that ALSO passes `attribution` may safely offer far
    tiers: what makes a distant label dangerous is that it stops obviously
    belonging to its part, and that is precisely what the attribution rule
    now refuses. Without `attribution`, keep the default.

    `attribution` is the operator's readability law, off unless supplied:
    a callable (rect, ref) -> ratio, where the ratio is the candidate's
    gap to its OWN part's copper over its gap to the nearest OTHER
    feature. A candidate scoring above `attribution_max` is REJECTED
    outright — it is a label the bench cannot bind to a feature, and the
    resolution order is re-seat (here), un-compress the board, or drop
    the label with written reasoning. Never ship the ambiguous seat.
    """
    apert = [a.inflate(CLIP_KEEPOUT) for a in apertures]
    silk_i = [s.inflate(silk_gap) for s in silk]
    hard = list(bodies_extra)
    bodies = {p.ref: p.body for p in parts}

    import os
    debug = bool(os.environ.get("SILKLABEL_DEBUG"))
    cands: dict[str, list] = {}
    for p in parts:
        ok = []
        why = {"aperture": 0, "silk": 0, "zone": 0, "edge": 0, "ambiguous": 0}
        n_all = 0
        for rect, rot, slot, rank in _candidates(p, board, gaps):
            n_all += 1
            if any(rect.overlaps(a) for a in apert):
                why["aperture"] += 1
                continue
            if any(rect.overlaps(s) for s in silk_i):
                why["silk"] += 1
                continue
            if any(rect.overlaps(h) for h in hard):
                why["zone"] += 1
                continue
            if (attribution is not None
                    and attribution(rect, p.ref) > attribution_max):
                why["ambiguous"] += 1
                continue
            crowd = sum(rect.overlap_area(b)
                        for ref, b in bodies.items() if ref != p.ref)
            ok.append((rank + BODY_PENALTY * crowd, rect, rot, slot))
        ok.sort(key=lambda c: c[0])
        cands[p.ref] = ok
        if debug and not ok:
            print(f"  [silklabel] {p.ref}: 0/{n_all} in-board candidates "
                  f"— rejected by {why}")

    claims: dict[str, tuple] = {}   # ref -> (score, rect, rot, slot)

    def best_free(ref):
        for score, rect, rot, slot in cands[ref]:
            if all(not rect.overlaps(c[1].inflate(label_gap))
                   for r2, c in claims.items() if r2 != ref):
                return (score, rect, rot, slot)
        return None

    # round 1: most constrained first; ties broken by ref for determinism
    for p in sorted(parts, key=lambda q: (len(cands[q.ref]), q.ref)):
        got = best_free(p.ref)
        if got is not None:
            claims[p.ref] = got
    # refinement sweeps: each part re-seats to its best slot given the
    # others' current claims — a move frees space a greedy-orphaned part
    # then takes on the next sweep. Fixed order, bounded sweeps: still
    # deterministic, and it converges (scores only ever improve).
    for _ in range(3):
        moved = False
        for p in sorted(parts, key=lambda q: q.ref):
            cur = claims.pop(p.ref, None)
            got = best_free(p.ref)
            take = got if (got is not None
                           and (cur is None or got[0] <= cur[0])) else cur
            if take is not None:
                claims[p.ref] = take
            if take is not cur:
                moved = True
        if not moved:
            break
    # depth-1 eviction for the still-homeless: find a candidate blocked by
    # exactly ONE label whose owner has another free slot; move the owner,
    # take the spot. Repeated to fixpoint (a successful eviction can free
    # the chain the next one needs); fixed order — deterministic, and
    # bounded because each round must house someone new to continue.
    for _ in range(len(parts)):
      housed = len(claims)
      for p in sorted(parts, key=lambda q: q.ref):
        if p.ref in claims:
            continue
        done = False
        for score, rect, rot, slot in cands[p.ref]:
            if done:
                break
            blockers = [r2 for r2, c in claims.items()
                        if rect.overlaps(c[1].inflate(label_gap))]
            if len(blockers) != 1:
                continue
            victim = blockers[0]
            saved = claims.pop(victim)
            claims[p.ref] = (score, rect, rot, slot)
            alt = best_free(victim)
            if alt is not None:
                claims[victim] = alt
                done = True
            else:                        # undo: eviction must not orphan
                del claims[p.ref]
                claims[victim] = saved
      if len(claims) == housed:
          break

    placed = [Placement(ref, c[1].cx, c[1].cy, c[2], c[3])
              for ref, c in claims.items()]
    placed.sort(key=lambda pl: pl.ref)
    unplaced = sorted(p.ref for p in parts if p.ref not in claims)
    return placed, unplaced


if __name__ == "__main__":
    # self-test: two neighbours must not collide; a part boxed in by
    # apertures on every side stays honestly unplaced
    board = Rect(0, 0, 30, 20)
    a = Part("R1", Rect(5, 8, 7, 12), (2.7, 1.2), (1.2, 2.7))
    b = Part("R2", Rect(8, 8, 10, 12), (2.7, 1.2), (1.2, 2.7))
    c = Part("U9", Rect(20, 8, 24, 12), (2.7, 1.2), (1.2, 2.7))
    ring = [Rect(17, 5, 27, 8), Rect(17, 12, 27, 15),     # N + S walls
            Rect(17, 5, 20, 15), Rect(24, 5, 27, 15)]     # W + E walls
    pl, un = place_labels([a, b, c], board, apertures=ring, silk=[])
    assert {p.ref for p in pl} == {"R1", "R2"} and un == ["U9"], (pl, un)
    r1 = next(p for p in pl if p.ref == "R1")
    r2 = next(p for p in pl if p.ref == "R2")
    lr1 = Rect(r1.x - 1.35, r1.y - 0.6, r1.x + 1.35, r1.y + 0.6)
    lr2 = Rect(r2.x - 1.35, r2.y - 0.6, r2.x + 1.35, r2.y + 0.6)
    assert not lr1.overlaps(lr2), (r1, r2)
    # determinism: same input, same answer
    assert place_labels([a, b, c], board, apertures=ring, silk=[]) == (pl, un)
    # the caller-raised laws, with their negative controls. A rule that can
    # never reject is not a rule (Article III), so each is proved to bite:
    #   * attribution: a scorer that calls every seat ambiguous houses nobody
    #   * attribution: a scorer that calls every seat decisive changes nothing
    #   * a text gap wider than the board strands every label
    assert place_labels([a, b, c], board, apertures=ring, silk=[],
                        attribution=lambda r, ref: 1.0) == ([], ["R1", "R2",
                                                                "U9"])
    assert place_labels([a, b, c], board, apertures=ring, silk=[],
                        attribution=lambda r, ref: 0.0) == (pl, un)
    assert place_labels([a, b], board, apertures=[], silk=[Rect(0, 0, 30, 20)],
                        silk_gap=0.0)[1] == ["R1", "R2"]
    print("silklabel self-test OK:", pl, "unplaced:", un)
