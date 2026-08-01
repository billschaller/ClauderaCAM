"""Deterministic pcb-rnd lihata (board-v8) emitter for the orbit generator.

The packaged pcb-rnd 3.1.7b carries no scripting engine, so the only reliable
way to build a board programmatically is to write its native text format.  This
module is the single audited code path that does it; `tools-board.py` owns the
geometry, this file owns the syntax.  Every construct below was round-tripped
through `pcb-rnd --gui batch ... SaveTo(LayoutAs,...)` before it was written
here (R4a lab, 2026-08-01) — nothing in this file is guessed.

Physical model this encodes (milled boards, NO plating process exists):
  hplated=1  -> a DECLARATION that the bench will put metal through this hole
                (stitched wire via, or a component lead soldered on both
                faces).  pcb-rnd's connectivity engine then models it as a
                layer bridge, and the *plated* excellon program is literally
                the bench's stitch / dual-solder drill list.
  hplated=0  -> everything else: a hole that conducts on one face only.

R3 finding (r3_probe_ring.py, reproduced in this item's gate): an unplated
padstack that is ALSO a netlist terminal must not carry copper on both faces —
pcb-rnd seeds a terminal search on every face the terminal has copper on, so
rings-both-faces + hplated=0 falsely CLOSES a net no metal bridges.  Hence
`ps_proto(..., sides=("bottom",))` for THT terminals, with the physical front
ring emitted separately by the caller as a DEAD island owned by no terminal.
"""

from __future__ import annotations

from typing import Iterable, Sequence, Tuple

MM = "mm"


# ---------------------------------------------------------------- padstacks
def ps_proto(pid: int, name: str, hdia: float, plated: bool, ringdia: float,
             sides: Sequence[str] = ("top", "bottom", "intern"),
             clearance: float = 0.0) -> str:
    """One padstack prototype: a hole plus a round ring on each named side.

    *sides* selects which copper faces carry a ring.  A THT terminal whose lead
    is soldered on the back only gets sides=("bottom",), so its front ring is
    never electrical — for that terminal, the front ring simply does not exist.

    An EMPTY *sides* makes a bare bore: a hole with no copper anywhere (orbit's
    H1–H4 mounting bores and the G1–G4 gauge holes, whose visible rings are
    dead islands, not annuli of a terminal).  MEASURED: pcb-rnd's min_ring and
    min_drill rules raise nothing on a copper-less padstack, so a bare bore is
    silent in DRC rather than a false ring violation.
    """
    shapes = "".join(f"""
       ha:ps_shape_v4 {{
        ha:ps_circ {{ x=0.0; y=0.0; dia={ringdia}{MM}; }}
        ha:layer_mask {{ copper = 1; {loc} = 1; }}
        ha:combining {{ }}
        clearance={clearance}{MM}
       }}""" for loc in sides)
    return f"""
   ha:ps_proto_v6.{pid} {{
     name = {name}
     hdia={hdia}{MM}; hplated={1 if plated else 0}; htop=0; hbottom=0;
     li:shape {{{shapes}
     }}
   }}"""


def rect_corners(w: float, h: float, rotation: float = 0.0):
    """The four corners of a w x h pad rotated by *rotation* degrees, CCW.

    Rotation lives in the SHAPE, not in a placement flag.  MEASURED why: the
    ring resistors sit at twelve different angles, and emitting their lands as
    axis-aligned rectangles at rotated centres put copper where no model said
    it was — 8 DRC net-shorts against the LED cathode rings, invisible to a
    clearance scan that believed the model.
    """
    import math
    a = math.radians(rotation)
    ca, sa = math.cos(a), math.sin(a)
    hw, hh = w / 2.0, h / 2.0
    return [(round(x * ca - y * sa, 4), round(x * sa + y * ca, 4))
            for x, y in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))]


def ps_proto_rect(pid: int, name: str, hdia: float, plated: bool,
                  w: float, h: float,
                  sides: Sequence[str] = ("bottom",),
                  clearance: float = 0.0, rotation: float = 0.0) -> str:
    """Padstack prototype with a RECTANGULAR pad (SMD land) on each named side.

    NOTE on `clearance` inside a shape: pcb-rnd stores the DOUBLE of the real
    clearance in this field for historical reasons, and it applies only when
    the padstack INSTANCE clearance is 0.  Kept at 0 here so the instance
    carries the clearance unambiguously.
    """
    corners = "\n".join(f"         {cx}{MM}; {cy}{MM};"
                        for cx, cy in rect_corners(w, h, rotation))
    pts = f"""
        li:ps_poly {{
{corners}
        }}"""
    shapes = "".join(f"""
       ha:ps_shape_v4 {{{pts}
        ha:layer_mask {{ copper = 1; {loc} = 1; }}
        ha:combining {{ }}
        clearance={clearance}{MM}
       }}""" for loc in sides)
    return f"""
   ha:ps_proto_v6.{pid} {{
     name = {name}
     hdia={hdia}{MM}; hplated={1 if plated else 0}; htop=0; hbottom=0;
     li:shape {{{shapes}
     }}
   }}"""


def ps_ref(oid: int, proto: int, x: float, y: float, name: str = "",
           term: str = "", clearance: float = 0.5,
           thermal_lids: Sequence[int] = ()) -> str:
    """A padstack instance referencing prototype *proto*.

    *thermal_lids* names the copper layer ids on which this terminal takes a
    round thermal relief instead of being cleared out of the pour (orbit: the
    GND terminals, SPEC "pours" row).  MEASURED in the R4a lab: a pour plus
    `li:N { on; round; }` closes GND galvanically ("layout is complete") and
    the ring copper survives — probing min_ring at 0.8 mm still flags the
    0.72 ring, so the `noshape` word pcb-rnd writes back is a writer artifact,
    not a deleted pad.
    """
    attrs = []
    if term:
        attrs.append(f"term = {term}; name = {term}")
    elif name:
        attrs.append(f"name = {name}")
    attr_block = f"    ha:attributes {{ {'; '.join(attrs)} }}\n" if attrs else ""
    th = "".join(f"\n     li:{lid} {{ on; round; }}" for lid in thermal_lids)
    th_block = f"    li:thermal {{{th}\n    }}\n" if thermal_lids else ""
    return f"""   ha:padstack_ref.{oid} {{
    proto = {proto}
    x = {x}{MM}; y = {y}{MM}
    rot = 0.000000
    xmirror = 0; smirror = 0
    clearance = {clearance}{MM}
{attr_block}{th_block}    ha:flags {{ clearline=1 }}
   }}"""


# ---------------------------------------------------------------- subcircuits
def uid(n: int) -> str:
    """Deterministic 24-char subcircuit uid (pcb-rnd wants 18 bytes, base64)."""
    import base64
    return base64.b64encode(b"ORB" + n.to_bytes(15, "big"),
                            altchars=b"-_").decode()


def subc(oid: int, refdes: str, pins: Sequence[Tuple[str, float, float, int]],
         protos: str, x: float, y: float, footprint: str = "ORBIT",
         on_bottom: bool = False, clearance: float = 0.4,
         thermal: dict | None = None) -> str:
    """A subcircuit (component) carrying its own padstack prototypes.

    *pins* is a sequence of (termname, x_mm, y_mm, proto_id) in BOARD lihata
    coordinates — the caller has already resolved rotation and side, so no
    mirror flag is ever set here.  That is deliberate: the KiCad rounds lost
    days to side-mirrored pin conventions, and the R3 DSN rule ("every image
    placed front at its own origin, no mirroring") only holds if the lihata
    side agrees.  *on_bottom* is metadata for the pick-and-place origin.

    *thermal* maps a terminal name to the copper LAYER IDS on which it takes a
    pour thermal instead of being cleared out.  It is a mapping and not a plain
    name list because the lid is NOT derivable from the part's side: orbit's THT
    terminals are placed `front` (that is where the body sits) while their only
    copper is the BACK ring, and a promoted dual-solder lead carries copper — and
    therefore a thermal — on BOTH faces.  MEASURED as a real break when this was
    a side-derived single lid: `pcb/30001` (the back pour) hung a rat line on
    PAD2-1, i.e. the board's only GND through-hole was isolated from the very
    plane it exists to stitch.
    """
    thermal = thermal or {}
    objs = "\n".join(
        ps_ref(oid + 10 + i, p, px, py, term=t, clearance=clearance,
               thermal_lids=tuple(thermal.get(t, ())))
        for i, (t, px, py, p) in enumerate(pins))
    side = "bottom=1" if on_bottom else "top=1"
    return f"""
   ha:subc.{oid} {{
    uid = {uid(oid)}
    ha:attributes {{ refdes = {refdes}; footprint = {footprint} }}
    ha:data {{
     li:padstack_prototypes {{{protos}
     }}
     li:objects {{
{objs}
     }}
     li:layers {{
      ha:subc-aux {{
       lid = 0
       ha:type {{ {side}; misc=1; virtual=1; }}
       li:objects {{
        ha:line.{oid + 1} {{ x1={x}{MM}; y1={y}{MM}; x2={x}{MM}; y2={y}{MM};
         thickness=0.1{MM}; clearance=0.0
         ha:flags {{ }}
         ha:attributes {{ subc-role = pnp-origin }} }}
        ha:line.{oid + 2} {{ x1={x}{MM}; y1={y}{MM}; x2={x}{MM}; y2={y}{MM};
         thickness=0.1{MM}; clearance=0.0
         ha:flags {{ }}
         ha:attributes {{ subc-role = origin }} }}
        ha:line.{oid + 3} {{ x1={x}{MM}; y1={y}{MM}; x2={x+1}{MM}; y2={y}{MM};
         thickness=0.1{MM}; clearance=0.0
         ha:attributes {{ subc-role = x }} }}
        ha:line.{oid + 4} {{ x1={x}{MM}; y1={y}{MM}; x2={x}{MM}; y2={y+1}{MM};
         thickness=0.1{MM}; clearance=0.0
         ha:attributes {{ subc-role = y }} }}
       }}
      }}
     }}
    }}
   }}"""


# ---------------------------------------------------------------- geometry
def line(oid: int, x1: float, y1: float, x2: float, y2: float,
         thickness: float = 0.6, clearance: float = 0.4,
         clearpoly: bool = True) -> str:
    """A copper/silk line.  *clearpoly* False makes it JOIN a pour it touches.

    Used both ways on orbit: signal copper clears the GND pour, while the dead
    front ring of a GND through-hole deliberately merges into the front pour
    (SPEC "vias" lever 3 — the wire pad is what stitches the front plane).
    """
    flags = "clearline=1" if clearpoly else ""
    return (f"     ha:line.{oid} {{ x1={x1}{MM}; y1={y1}{MM}; "
            f"x2={x2}{MM}; y2={y2}{MM}; thickness={thickness}{MM}; "
            f"clearance={clearance}{MM}\n      ha:flags {{ {flags} }} }}")


def polygon(oid: int, pts: Sequence[Tuple[float, float]],
            clearance: float = 0.4) -> str:
    """A filled polygon (orbit: the GND pour, one per side).

    clearpoly=1 makes padstacks and clearline objects cut themselves out of it
    at their own clearance; enforce_clearance floors that at the law even if an
    object were to arrive with a smaller one.  So pour clearance is guaranteed
    by CONSTRUCTION (the polygon is clipped), not by a check that could be
    skipped.
    """
    body = "\n".join(f"        {{ {x}{MM}; {y}{MM} }}" for x, y in pts)
    return f"""     ha:polygon.{oid} {{
      clearance={clearance}{MM}
      enforce_clearance={clearance}{MM}
      ha:flags {{ clearpoly=1 }}
      li:geometry {{
       ta:contour {{
{body}
       }}
      }}
     }}"""


# ---------------------------------------------------------------- whole board
# Layer ids and groups.  Fixed here, referenced by name everywhere else, so a
# thermal that names "layer 1" and the bottom-copper objects can never drift
# apart (Article IV's spirit: one definition, no re-derivation).
LID_TOP_CU, LID_BOT_CU, LID_OUTLINE, LID_TOP_SILK, LID_BOT_SILK = 0, 1, 2, 3, 4


def board(width: float, height: float, protos: str = "", objects: str = "",
          top: str = "", bottom: str = "", outline: str = "",
          top_silk: str = "", bottom_silk: str = "", netlist: str = "",
          track: float = 0.6, clearance: float = 0.4,
          substrate: float = 1.5) -> str:
    """Assemble a complete 2-copper-layer board with both silk layers.

    *substrate* 1.5 mm is the blank this board is milled from (SPEC process
    table: double-sided 1.5 mm FR-1/FR-4).
    """
    return f"""ha:pcb-rnd-board-v8 {{
 ha:attributes {{ {{PCB::grid::unit}}=mm }}

 li:styles {{
   ha:Signal {{ thickness = {track}{MM}; clearance = {clearance}{MM};
                text_scale = 100; via_proto = 0 }}
 }}

 ha:meta {{
   ha:size {{ thermal_scale = 0.500000
    x = {width}{MM}; y = {height}{MM} }}
   ha:grid {{ spacing = 1.0{MM}; offs_x = 0.0; offs_y = 0.0 }}
 }}

 ha:data {{
  li:padstack_prototypes {{{protos}
  }}

  li:objects {{
{objects}
  }}

  li:layers {{
   ha:top-copper {{
    lid={LID_TOP_CU}
    group=0
    ha:combining {{ }}
    color = {{#8b2323}}
    li:objects {{
{top}
    }}
   }}

   ha:bottom-copper {{
    lid={LID_BOT_CU}
    group=2
    ha:combining {{ }}
    color = {{#3a5fcd}}
    li:objects {{
{bottom}
    }}
   }}

   ha:outline {{
    lid={LID_OUTLINE}
    group=3
    ha:combining {{ }}
    li:objects {{
{outline}
    }}
   }}

   ha:top-silk {{
    lid={LID_TOP_SILK}
    group=4
    ha:combining {{ auto=1; }}
    li:objects {{
{top_silk}
    }}
   }}

   ha:bottom-silk {{
    lid={LID_BOT_SILK}
    group=5
    ha:combining {{ auto=1; }}
    li:objects {{
{bottom_silk}
    }}
   }}
  }}
 }}

 ha:layer_stack {{
  li:groups {{
   ha:0 {{ name = top_copper;     ha:type {{ copper=1; top=1; }}       li:layers {{ {LID_TOP_CU}; }} }}
   ha:1 {{ name = grp_substrate;  ha:type {{ substrate=1; intern=1; }} li:layers {{ }}
           ha:attributes {{ thickness={substrate}{MM} }} }}
   ha:2 {{ name = bottom_copper;  ha:type {{ copper=1; bottom=1; }}    li:layers {{ {LID_BOT_CU}; }} }}
   ha:3 {{ name = global_outline; ha:type {{ boundary=1; }}            li:layers {{ {LID_OUTLINE}; }}
           purpose = uroute }}
   ha:4 {{ name = top_silk;       ha:type {{ silk=1; top=1; }}         li:layers {{ {LID_TOP_SILK}; }} }}
   ha:5 {{ name = bottom_silk;    ha:type {{ silk=1; bottom=1; }}      li:layers {{ {LID_BOT_SILK}; }} }}
  }}
 }}

 ha:netlists {{
   li:input {{
{netlist}
   }}
 }}
}}
"""


def netlist_block(nets: Iterable[Tuple[str, Sequence[str]]]) -> str:
    """nets = [(netname, ["REF-TERM", ...]), ...]"""
    return "\n".join(
        f"    ha:{name} {{\n     li:conn {{ {'; '.join(conns)}; }}\n    }}"
        for name, conns in nets)
