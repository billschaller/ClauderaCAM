"""KiCad ``kicadsexpr`` netlist -> python nets -> tEDAx v1 (pcb-rnd).

The schematic (`orbit.kicad_sch`) is the ground truth for connectivity; this
module is the only thing that reads it, so the board generator never restates
a net by hand.  Verified on orbit: 26 nets / 97 connections.

    kicad-cli sch export netlist --format kicadsexpr -o orbit.net orbit.kicad_sch
    python3 kicadnet.py orbit.net > orbit.tdx

tEDAx netlist grammar consumed by pcb-rnd (io_tedax/netlist.c)::

    tEDAx v1
    begin netlist v1 <netlist-name>
        conn <netname> <refdes> <pinnum>
    end netlist

Fields are TAB separated; a field containing whitespace must be quoted.
Stdlib only.  Derived from the R3 lab's kicadnet2tedax.py.
"""

from __future__ import annotations

import sys
from typing import Dict, Iterator, List, Tuple, Union

Node = Union[str, List["Node"]]


# --------------------------------------------------------- s-expression reader
def tokenize(text: str) -> Iterator[str]:
    """Yield ``(``, ``)`` and atoms.  Handles "quoted strings" with \\ escapes."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "()":
            yield c
            i += 1
        elif c.isspace():
            i += 1
        elif c == '"':
            i += 1
            buf = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    i += 1
                buf.append(text[i])
                i += 1
            i += 1
            yield "".join(buf)
        else:
            start = i
            while i < n and not text[i].isspace() and text[i] not in '()"':
                i += 1
            yield text[start:i]


def parse(text: str) -> Node:
    """Parse the first complete s-expression in *text*."""
    stack: List[List[Node]] = []
    root: List[Node] = []
    for tok in tokenize(text):
        if tok == "(":
            new: List[Node] = []
            (stack[-1] if stack else root).append(new)
            stack.append(new)
        elif tok == ")":
            if not stack:
                raise ValueError("unbalanced ')' in netlist")
            stack.pop()
        else:
            (stack[-1] if stack else root).append(tok)
    if stack:
        raise ValueError("unbalanced '(' in netlist")
    if not root:
        raise ValueError("empty netlist")
    return root[0]


def children(node: Node, tag: str) -> Iterator[List[Node]]:
    if not isinstance(node, list):
        return
    for item in node:
        if isinstance(item, list) and item and item[0] == tag:
            yield item


def value(node: Node, tag: str) -> str:
    for sub in children(node, tag):
        if len(sub) >= 2 and isinstance(sub[1], str):
            return sub[1]
    return ""


def sanitize(name: str) -> str:
    """KiCad hierarchical names lead with '/'; pcb-rnd takes them verbatim as
    long as they carry no whitespace, so the only change is dropping that '/'."""
    return name[1:] if name.startswith("/") and len(name) > 1 else name


# ------------------------------------------------------------------- reading
def read_nets(path: str) -> Tuple[Dict[str, List[str]], dict]:
    """-> ({netname: ["REF-PIN", ...]}, stats).  Deterministic ordering.

    A one-pin net is a NO-CONNECT: it cannot express a connection and pcb-rnd
    has nothing to route, so it is recorded in stats and not emitted (orbit has
    exactly one, SW1's unused slide throw).
    """
    with open(path, "r", encoding="utf-8") as fh:
        root = parse(fh.read())
    if not isinstance(root, list) or not root or root[0] != "export":
        raise SystemExit(f"{path}: not a kicadsexpr netlist (no (export ...))")

    nets: Dict[str, List[str]] = {}
    singles: List[str] = []
    total = conns = 0
    for group in children(root, "nets"):
        for net in children(group, "net"):
            total += 1
            name = sanitize(value(net, "name"))
            nodes = list(children(net, "node"))
            if len(nodes) < 2:
                singles.append(name)
                continue
            pins = []
            for node in nodes:
                ref, pin = value(node, "ref"), value(node, "pin")
                if not ref or not pin:
                    raise SystemExit(f"net {name!r}: node missing ref/pin")
                pins.append(f"{ref}-{pin}")
            nets[name] = sorted(pins)
            conns += len(pins)
    return (dict(sorted(nets.items())),
            {"nets_in_file": total, "nets": len(nets), "conns": conns,
             "no_connect": sorted(singles)})


def quote(field: str) -> str:
    if field == "":
        return '""'
    if any(ch.isspace() for ch in field) or '"' in field:
        return '"' + field.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return field


def to_tedax(nets: Dict[str, List[str]], listname: str = "orbit") -> str:
    """tEDAx serialization of *nets* — the form pcb-rnd's LoadTedaxFrom eats."""
    out = ["tEDAx v1", f"begin netlist v1 {quote(listname)}"]
    for name in sorted(nets):
        for pid in sorted(nets[name]):
            ref, _, pin = pid.rpartition("-")
            out.append(f"\tconn {quote(name)}\t{quote(ref)}\t{quote(pin)}")
    out += ["end netlist", ""]
    return "\n".join(out)


if __name__ == "__main__":
    nets_, stats_ = read_nets(sys.argv[1])
    sys.stdout.write(to_tedax(nets_))
    print(f"{stats_['nets']} nets / {stats_['conns']} conns, "
          f"no-connect: {stats_['no_connect']}", file=sys.stderr)
