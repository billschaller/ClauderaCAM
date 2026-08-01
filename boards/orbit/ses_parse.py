#!/usr/bin/env python3
"""Specctra .ses (session) reader.  Standard library only.

The session grammar is plain s-expressions, so the reader is a tokenizer plus a
tree walk.  Constructs handled, all of them observed in FreeRouting 2.2.4
output or permitted by the Specctra grammar:

  (session <id> (base_design <id>) ...)
  (placement (resolution <unit> <n>) (component <img> (place <ref> x y <side> <rot>)))
  (was_is ...)                              - ignored, may be empty
  (routes (resolution ...) (parser ...) (library_out (padstack ...)) (network_out ...))
  (network_out (net <name> <wire|via>...))  - net given by ENCLOSING node
  (wire (path <layer> <width> x y x y ...) [(net <name>)] [(type protect)] ...)
  (wire (polyline_path ...))                - alias of path
  (via <padstack> x y [(net <name>)])       - net inline OR from the enclosing net
  quoted atoms ("F.Cu", "VIA_STITCH"), unquoted atoms, negative and fractional
  numbers, arbitrary whitespace/newlines, missing trailing newline, and
  duplicate (padstack ...) entries in library_out.

COORDINATE SCALE IS NOT TRUSTWORTHY.  FreeRouting 2.2.4 writes
`(resolution um 10)` into the session while emitting coordinates ten times
larger than that declaration; a DSN pin at 287000 comes back as 2870000.  So
this module returns RAW integers and offers calibrate(), which recovers the
true units-per-millimetre from the placement section against coordinates the
caller already knows.  Never divide by the declared resolution alone.
"""
from __future__ import annotations

import re
from typing import Any, Iterator

_TOK = re.compile(r'"[^"]*"|[()]|[^\s()]+')


def parse(text: str) -> list:
    """Whole file -> nested python lists; atoms stay strings (quotes removed)."""
    stack: list[list] = [[]]
    for tok in _TOK.findall(text):
        if tok == "(":
            new: list = []
            stack[-1].append(new)
            stack.append(new)
        elif tok == ")":
            if len(stack) > 1:
                stack.pop()
        else:
            stack[-1].append(tok[1:-1] if tok.startswith('"') else tok)
    return stack[0]


def head(node: Any) -> str:
    return node[0] if isinstance(node, list) and node and isinstance(node[0], str) else ""


def walk(node: Any) -> Iterator[list]:
    if isinstance(node, list):
        if node and isinstance(node[0], str):
            yield node
        for kid in node:
            yield from walk(kid)


def find(node: Any, key: str) -> Iterator[list]:
    for n in walk(node):
        if head(n) == key:
            yield n


def _num(s: str) -> float:
    return float(s)


class Session:
    """Parsed routing result, coordinates RAW (session units, y-up)."""

    def __init__(self, text: str):
        self.tree = parse(text)
        self.placement: dict[str, tuple[float, float, str]] = {}
        self.wires: list[dict] = []
        self.vias: list[dict] = []
        self.declared_res: tuple[str, float] | None = None

        for r in find(self.tree, "resolution"):
            if len(r) >= 3:
                self.declared_res = (r[1], _num(r[2]))
                break
        for comp in find(self.tree, "component"):
            for pl in find(comp, "place"):
                if len(pl) >= 5:
                    self.placement[pl[1]] = (_num(pl[2]), _num(pl[3]), pl[4])
        for out in find(self.tree, "network_out"):
            for net in out:
                if head(net) == "net":
                    self._net(net[1], net)
        # vias/wires that carry their own (net ...) outside any network_out net
        seen_w = {id(w["node"]) for w in self.wires}
        seen_v = {id(v["node"]) for v in self.vias}
        for w in find(self.tree, "wire"):
            if id(w) not in seen_w:
                self._wire(self._inline_net(w), w)
        for v in find(self.tree, "via"):
            if id(v) not in seen_v:
                self._via(self._inline_net(v), v)

    @staticmethod
    def _inline_net(node: list) -> str:
        for n in find(node, "net"):
            if len(n) >= 2 and isinstance(n[1], str):
                return n[1]
        return ""

    def _net(self, name: str, node: list) -> None:
        for kid in node:
            if head(kid) == "wire":
                self._wire(name, kid)
            elif head(kid) == "via":
                self._via(name, kid)

    def _wire(self, net: str, node: list) -> None:
        for p in node:
            if head(p) in ("path", "polyline_path") and len(p) >= 5:
                pts = [_num(v) for v in p[3:] if _isnum(v)]
                self.wires.append({
                    "net": net or self._inline_net(node),
                    "layer": p[1], "width": _num(p[2]),
                    "pts": list(zip(pts[0::2], pts[1::2])), "node": node})

    def _via(self, net: str, node: list) -> None:
        nums = [_num(v) for v in node[1:] if _isnum(v)]
        if len(nums) >= 2:
            self.vias.append({"net": net or self._inline_net(node),
                              "padstack": node[1], "x": nums[0],
                              "y": nums[1], "node": node})

    # -- scale recovery -----------------------------------------------------
    def calibrate(self, known_mm: dict[str, tuple[float, float]]) -> float:
        """units-per-mm, recovered from placement against caller-known values.

        *known_mm* maps refdes -> (x_mm, y_mm) in the caller's y-DOWN frame;
        the session is y-up.  Every non-zero coordinate must agree on one
        ratio, otherwise the session is not describing our board.
        """
        ratios = set()
        for ref, (sx, sy, _side) in self.placement.items():
            if ref not in known_mm:
                continue
            kx, ky = known_mm[ref]
            for raw, mm in ((sx, kx), (sy, -ky)):
                if abs(mm) > 1e-9:
                    ratios.add(round(raw / mm, 6))
        if not ratios:
            raise ValueError("session placement matches no known component")
        if len(ratios) > 1:
            raise ValueError(f"inconsistent session scale: {sorted(ratios)}")
        return ratios.pop()

    def geometry_mm(self, upm: float):
        """(tracks, vias) in the caller's y-DOWN millimetre frame."""
        tracks = []
        for w in self.wires:
            pts = [(x / upm, -y / upm) for x, y in w["pts"]]
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                tracks.append((w["layer"], x1, y1, x2, y2, w["width"] / upm,
                               w["net"]))
        vias = [(v["x"] / upm, -v["y"] / upm, v["net"]) for v in self.vias]
        return tracks, vias


def _isnum(s: Any) -> bool:
    if not isinstance(s, str):
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def load(path: str) -> Session:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return Session(fh.read())


if __name__ == "__main__":
    import sys
    s = load(sys.argv[1])
    print(f"declared resolution : {s.declared_res}")
    print(f"components placed   : {len(s.placement)}")
    print(f"wire paths          : {len(s.wires)}")
    print(f"vias                : {len(s.vias)}")
    for lay in sorted({w['layer'] for w in s.wires}):
        print(f"  layer {lay}: {sum(1 for w in s.wires if w['layer'] == lay)}"
              " paths")
