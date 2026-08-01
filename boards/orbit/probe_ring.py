#!/usr/bin/env python3
"""PRECONDITION PROBE: does an unplated padstack that is also a NETLIST
TERMINAL leak connectivity between its two faces?

This is the measurement the whole plating model rests on, so orbit's gate
re-runs it on every build rather than citing it.  Chain:

    J-A (top SMD) --top track--> P (unplated hole) --bottom track--> K-C

The netlist says J-A, P-1 and K-C are one net.  On a milled board there is no
metal in that hole, so J-A must NOT reach K-C.

  mode both  : P's padstack carries rings on BOTH faces, hplated=0, and P-1 is
               a terminal.  pcb-rnd seeds a terminal search on every face the
               terminal has copper on, so this falsely CLOSES the net and the
               board reports "layout is complete" over an open circuit.
  mode split : P's padstack has copper on the BOTTOM face only and the physical
               front ring is a separate DEAD island belonging to no terminal.
               One rat line survives — the honest answer.

`split` is how every through-hole terminal in orbit.lht is encoded.
Derived from the R3 lab's r3_probe_ring.py.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lihata as L                                          # noqa: E402

HOLE, RING, PAD = 1.0, 2.44, 2.0
XJ, XP, XK, Y = 10.0, 20.0, 30.0, 10.0


def board(mode: str) -> str:
    sides = ("top", "bottom") if mode == "both" else ("bottom",)
    objs = [
        L.subc(1000, "J", pins=[("A", XJ, Y, 0)],
               protos=L.ps_proto(0, "SMDT", 0.0, False, PAD, sides=("top",)),
               x=XJ, y=Y),
        L.subc(2000, "P", pins=[("1", XP, Y, 0)],
               protos=L.ps_proto(0, "THT", HOLE, False, RING, sides=sides),
               x=XP, y=Y),
        L.subc(3000, "K", pins=[("C", XK, Y, 0)],
               protos=L.ps_proto(0, "SMDB", 0.0, False, PAD, sides=("bottom",)),
               x=XK, y=Y, on_bottom=True),
    ]
    top = [L.line(300, XJ, Y, XP, Y)]
    if mode == "split":                     # dead front ring, owned by nobody
        top.append(L.line(301, XP, Y, XP, Y, thickness=RING, clearance=0.0))
    bot = [L.line(400, XP, Y, XK, Y)]
    return L.board(40.0, 20.0, objects="\n".join(objs), top="\n".join(top),
                   bottom="\n".join(bot),
                   netlist=L.netlist_block([("N1", ["J-A", "P-1", "K-C"])]))


if __name__ == "__main__":
    sys.stdout.write(board(sys.argv[1] if len(sys.argv) > 1 else "both"))
