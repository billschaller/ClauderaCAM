#!/usr/bin/env python3
"""Deterministic Board B ("orbit") schematic builder: writes orbit.kicad_sch
from scratch, then re-reads the bytes it wrote and proves them against the
contract in boards/orbit/SPEC.md.

This script IS the schematic's source of truth.  orbit.kicad_sch is a build
artifact; hand-edits are forbidden and will be overwritten on the next run.
Re-running produces a byte-identical file (all UUIDs are uuid5 of a fixed
namespace and a stable key).

Method inherited from Board A (boards/coupon/tools-rewire.py): no wires and
no junctions anywhere.  Every net is declared once in NET below and realised
as a `label` placed EXACTLY on the pin's connection point, derived from the
embedded lib_symbols geometry as (x + px, y - py) for an unrotated,
unmirrored placement.  Board A learned this the hard way: the pin dump from
the MCP validator reported pin bases, not endpoints, and every net silently
fell 1.27 mm short.  Geometry comes from the library, never from a table
typed by hand, and the asserts at the bottom re-derive it from the emitted
file.

SPEC sections served, block by block:
  RING          -> "The ring - 12 LEDs, 4 lines, explicit matrix"
  LINES         -> "Pin budget - the whole design is this table"
  buttons       -> "Buttons share the charlieplex lines, deliberately"
  buzzer cell   -> "Buzzer cell (BZ1)"
  power entry   -> "Power entry"
  ISP pads      -> "ISP - bare pads, no connector"
  PARTS/footprints -> "BOM - every line traced to a crib file" (Article XI)

Not in this file, on purpose, because Board A's convention puts them in the
LAYOUT script and not the schematic: G1-G4 flip gauges, H1-H4 M3 bores and
V1-V6 wire vias.  They are copper artifacts with no net (SPEC "Deliberate
exceptions", "Wire vias & pin-and-flip registration"), exactly like Board A's
JP* links and MountingHole footprints.

Run:  python3 tools-schematic.py          # build + self-verify
      kicad-cli sch erc --severity-error --exit-code-violations orbit.kicad_sch
"""
import os
import re
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
SCH = os.path.join(HERE, "orbit.kicad_sch")
SYMROOT = "/usr/share/kicad/symbols"
NS = uuid.UUID("6f2f4e2c-0b1a-5c3d-9e77-0b0d0b0d0b0d")   # fixed: determinism


def uid(key):
    return str(uuid.uuid5(NS, key))


# --------------------------------------------------------------- s-expression
def sexp(text):
    """Parse s-expression text into nested lists.  Atoms keep their raw
    source spelling (quotes included) so a parse/dump round trip is lossless."""
    out, stack, i, n = [], None, 0, len(text)
    stack = [out]
    while i < n:
        c = text[i]
        if c == "(":
            node = []
            stack[-1].append(node)
            stack.append(node)
            i += 1
        elif c == ")":
            stack.pop()
            i += 1
        elif c == '"':
            j = i + 1
            while text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            stack[-1].append(text[i:j + 1])
            i = j + 1
        elif c.isspace():
            i += 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            stack[-1].append(text[i:j])
            i = j
    return out


def dump(node, ind=0):
    pad = "\t" * ind
    if isinstance(node, str):
        return pad + node
    if all(isinstance(c, str) for c in node):
        return pad + "(" + " ".join(node) + ")"
    head = node[0] if isinstance(node[0], str) else None
    parts = [pad + "(" + head] if head else [pad + "("]
    for c in node[1:] if head else node:
        parts.append(dump(c, ind + 1))
    parts[-1] += ")"
    return "\n".join(parts)


def unq(tok):
    return tok[1:-1] if tok.startswith('"') else tok


def kids(node, head):
    return [c for c in node[1:]
            if isinstance(c, list) and c and c[0] == head]


def kid(node, head):
    got = kids(node, head)
    return got[0] if got else None


# ------------------------------------------------------- library symbol load
_LIBCACHE = {}


def _lib(libname):
    if libname not in _LIBCACHE:
        path = os.path.join(SYMROOT, libname + ".kicad_sym")
        _LIBCACHE[libname] = sexp(open(path).read())[0]
    return _LIBCACHE[libname]


def _raw_symbol(libname, name):
    for c in _lib(libname)[1:]:
        if isinstance(c, list) and c[0] == "symbol" and unq(c[1]) == name:
            return c
    sys.exit(f"symbol {libname}:{name} not found")


def _rename_units(node, old, new):
    """Rename the `<stem>_<unit>_<style>` sub-symbol blocks of a copied parent
    to the child's stem.  KiCad flattens derived symbols when it embeds them;
    kicad-cli never resolves (extends ...) INSIDE a schematic (Board A hit this
    with Q_NPN_BEC), so the embedded copy must already be flat."""
    if not isinstance(node, list):
        return node
    out = []
    for i, c in enumerate(node):
        if (i == 1 and node[0] == "symbol" and isinstance(c, str)
                and c.startswith('"' + old + "_")):
            out.append('"' + new + unq(c)[len(old):] + '"')
        else:
            out.append(_rename_units(c, old, new))
    return out


def lib_symbol(libname, name):
    """Flattened symbol block: parent geometry, child properties."""
    node = _raw_symbol(libname, name)
    ext = kid(node, "extends")
    if ext is None:
        return [c for c in node]
    flat = lib_symbol(libname, unq(ext[1]))
    flat = _rename_units(flat, unq(ext[1]), name)
    out = [flat[0], '"' + name + '"']
    childprops = {unq(p[1]): p for p in kids(node, "property")}
    seen = set()
    for c in flat[2:]:
        if isinstance(c, list) and c[0] == "property":
            key = unq(c[1])
            seen.add(key)
            out.append(childprops.get(key, c))
        elif isinstance(c, list) and c[0] == "extends":
            continue
        else:
            out.append(c)
    for key, p in childprops.items():
        if key not in seen:
            out.append(p)
    return out


def embed(lib_id):
    """The lib_symbols entry: flattened, renamed to the full lib_id."""
    libname, name = lib_id.split(":")
    blk = lib_symbol(libname, name)
    return [blk[0], '"' + lib_id + '"'] + blk[2:]


def lib_pins(lib_id):
    """(unit, number) -> (px, py) connection points, from the embedded block."""
    blk = embed(lib_id)
    pins = {}

    def walk(node, unit):
        for c in node[1:]:
            if not isinstance(c, list):
                continue
            if c[0] == "symbol":
                m = re.match(r'"[^"]*_(\d+)_\d+"', c[1])
                walk(c, int(m.group(1)) if m else unit)
            elif c[0] == "pin":
                at = kid(c, "at")
                num = kid(c, "number")
                pins[(unit, unq(num[1]))] = (float(at[1]), float(at[2]))
    walk(blk, 1)
    return pins


# ============================================================== THE CONTRACT
# SPEC "BOM - every line traced to a crib file".  Footprint spellings follow
# Board A's convention: a stock KiCad library footprint wherever one matches
# the crib's measured geometry (the layout script reworks pad/drill sizes to
# the milled-board annular law, exactly as boards/coupon/tools-layout.py's
# fixup_tht does), and an `orbit:` name where the crib states a geometry no
# stock footprint provides - those three are built by the layout script, the
# way Board A built coupon:SW_Slide_SS12D00_HandMill.
R_FP = {
    "0603": "Resistor_SMD:R_0603_1608Metric_Pad0.98x0.95mm_HandSolder",
    "0805": "Resistor_SMD:R_0805_2012Metric_Pad1.20x1.40mm_HandSolder",
    "1206": "Resistor_SMD:R_1206_3216Metric_Pad1.30x1.75mm_HandSolder",
}
C_FP = {
    "0603": "Capacitor_SMD:C_0603_1608Metric_Pad1.08x0.95mm_HandSolder",
    "0805": "Capacitor_SMD:C_0805_2012Metric_Pad1.18x1.45mm_HandSolder",
    "1206": "Capacitor_SMD:C_1206_3216Metric_Pad1.33x1.80mm_HandSolder",
}
SOT23 = "Package_TO_SOT_SMD:SOT-23_Handsoldering"
# parts/attiny85.toml: the -SU marking is the EIAJ 8S2 WIDE body (5.3 mm,
# lead span 8.0, pitch 1.27).  Package_SO:SOIC-8_3.9x4.9mm_P1.27mm is the
# JEDEC narrow body and would leave the gull-wings barely on the pads - a
# reflow failure.  Asserted at the bottom of this file.
SOIC8W = "Package_SO:SOIC-8_5.3x5.3mm_P1.27mm"
CRIB = {"1206": "parts/egscst-1206.toml", "0805": "parts/egscst-0805.toml",
        "0603": "parts/egscst-0603.toml", "smd": "parts/kokiso-smd.toml",
        "tht": "parts/tht-bins.toml", "mcu": "parts/attiny85.toml",
        "none": "copper artifact - no crib part"}

# SPEC "The ring": (position, anode line, cathode line, R ref, R package).
# Position 1 is the marker at 12 o'clock; the marker is silk, not a part, so
# all twelve LEDs are one part and one value.  The three package sizes are the
# controlled reflow-wetting comparison, not an accident.
RING = [
    (1,  "L0", "L1", "R1",  "1206"),
    (2,  "L1", "L0", "R2",  "0805"),
    (3,  "L0", "L2", "R3",  "0603"),
    (4,  "L2", "L0", "R4",  "1206"),
    (5,  "L0", "L3", "R5",  "0805"),
    (6,  "L3", "L0", "R6",  "0603"),
    (7,  "L1", "L2", "R7",  "1206"),
    (8,  "L2", "L1", "R8",  "0805"),
    (9,  "L1", "L3", "R9",  "0603"),
    (10, "L3", "L1", "R10", "1206"),
    (11, "L2", "L3", "R11", "0805"),
    (12, "L3", "L2", "R12", "0603"),
]

# SPEC "Pin budget": the charlieplex line -> ATtiny85 pin map.  RESET stays
# RESET; there is no sixth I/O, which is why the buttons share lines.
LINES = {"L0": ("U1", "5"), "L1": ("U1", "6"),
         "L2": ("U1", "7"), "L3": ("U1", "2")}

# ref -> (lib_id, value, footprint, crib key, place x, place y)
PARTS = {
    "U1":  ("MCU_Microchip_ATtiny:ATtiny85-20S", "ATTINY85-20SU", SOIC8W,
            "mcu", 60.0, 110.0),
    "Q1":  ("Transistor_FET:AO3401A", "AO3401", SOT23, "smd", 115.0, 40.0),
    "Q2":  ("Transistor_BJT:Q_NPN_BEC", "MMBT2222A", SOT23, "smd", 65.0, 180.0),
    "D1":  ("Diode:BAV99", "BAV99", SOT23, "smd", 105.0, 180.0),
    "R13": ("Device:R", "10k", R_FP["0603"], "0603", 140.0, 110.0),
    "R14": ("Device:R", "2.2k", R_FP["0805"], "0805", 30.0, 180.0),
    "R15": ("Device:R", "4.7k", R_FP["1206"], "1206", 265.0, 180.0),
    "R16": ("Device:R", "4.7k", R_FP["0603"], "0603", 335.0, 180.0),
    "C1":  ("Device:C", "10uF", C_FP["0805"], "0805", 155.0, 40.0),
    "C2":  ("Device:C", "100nF", C_FP["0805"], "0805", 110.0, 110.0),
    "C3":  ("Device:C", "1uF", C_FP["0603"], "0603", 180.0, 180.0),
    "C4":  ("Device:C", "10nF", C_FP["1206"], "1206", 170.0, 110.0),
    "S1":  ("Switch:SW_Push", "CATCH", "Button_Switch_THT:SW_PUSH_6mm",
            "tht", 230.0, 180.0),
    "S2":  ("Switch:SW_Push", "START", "Button_Switch_THT:SW_PUSH_6mm",
            "tht", 300.0, 180.0),
    "SW1": ("Switch:SW_SPDT", "ON/OFF", "orbit:SW_Slide_SPDT_P4.86mm_D1.8mm",
            "tht", 70.0, 40.0),
    "BZ1": ("Device:Buzzer", "CYT1036", "Buzzer_Beeper:Buzzer_12x9.5RM7.6",
            "tht", 145.0, 180.0),
    "PAD1": ("Connector:TestPoint", "+5V",
             "orbit:WirePad_D3.6mm_Drill1.5mm", "none", 30.0, 40.0),
    "PAD2": ("Connector:TestPoint", "GND",
             "orbit:WirePad_D3.6mm_Drill1.5mm", "none", 190.0, 40.0),
}
for pos, hi, lo, rref, pkg in RING:
    col, row = (pos - 1) % 6, (pos - 1) // 6
    x, y = 40.0 + col * 90.0, 250.0 + row * 65.0
    # SPEC "LED1-LED12 | 5 mm THT red ... 2.54 pitch confirmed"
    PARTS[f"LED{pos}"] = ("Device:LED", "LED_5MM_RED", "LED_THT:LED_D5.0mm",
                          "tht", x, y)
    PARTS[rref] = ("Device:R", "560R", R_FP[pkg], pkg, x + 35.0, y)
# SPEC "ISP - bare pads, no connector": 2x3 AVR grid, MISO/VCC SCK/MOSI RST/GND.
ISP = [("TP1", "MISO"), ("TP2", "VCC"), ("TP3", "SCK"),
       ("TP4", "MOSI"), ("TP5", "RST"), ("TP6", "GND")]
for i, (ref, sig) in enumerate(ISP):
    PARTS[ref] = ("Connector:TestPoint", sig, "orbit:ISP_Pad_D1.8mm",
                  "none", 210.0 + i * 25.0, 110.0)


# KiCad's connection grid is 1.27 mm and every library pin offset used here is
# already a multiple of it, so snapping the PLACEMENTS puts every connection
# point on-grid.  Off-grid pins are how a schematic that netlists correctly
# today grows a silent open the moment someone drags a wire in the GUI; ERC
# calls it `endpoint_off_grid` and it is a real warning, not a cosmetic one.
G = 1.27


def snap(v):
    return round(round(v / G) * G, 4)


PARTS = {r: v[:4] + (snap(v[4]), snap(v[5])) for r, v in PARTS.items()}

# Power-symbol / PWR_FLAG set.  The power symbols give VCC and GND their
# global net names; PWR_FLAG is the power_out driver those rails need (they
# arrive through a wire pad and a P-FET, neither of which has a power-out
# pin).  Board A's answer, unchanged.
POWER = {
    "#PWR01": ("power:VCC", "VCC", 230.0, 40.0),
    "#PWR02": ("power:GND", "GND", 290.0, 40.0),
    "#FLG01": ("power:PWR_FLAG", "PWR_FLAG", 260.0, 40.0),
    "#FLG02": ("power:PWR_FLAG", "PWR_FLAG", 320.0, 40.0),
}
POWER = {r: v[:2] + (snap(v[2]), snap(v[3])) for r, v in POWER.items()}

# ----------------------------------------------------------------- the nets
NET = {}


def n(net, *pins):
    for rp in pins:
        assert rp not in NET, f"{rp} already on {NET[rp]}"
        NET[rp] = net


# SPEC "Power entry": PAD+ -> SW1 (slide SPDT as on/off) -> Q1 AO3401 reverse
# guard -> VCC.  KiCad's Switch:SW_SPDT numbers the COMMON blade pin 2 (symbol
# pin name "B"); pins 1 and 3 are the throws.  PAD+ feeds the common, throw A
# feeds the FET, throw C is unused and carries an explicit no-connect.
# Q1 is the zigbee-button configuration: gate to GND, source to the load
# (VCC), drain to the switched input - the body diode blocks on reversed
# leads and the channel shorts it out at ~10 mV the right way round.
n("VBAT", ("PAD1", "1"), ("SW1", "2"))
n("VSW", ("SW1", "1"), ("Q1", "3"))
NC = [("SW1", "3")]

# C1 is the bulk sitting on the guarded rail, not on the raw input: a bulk cap
# ahead of the reverse guard is a cap the guard does not protect and a rail
# that has no bulk.  SPEC's "C1 at the entry" is a placement statement
# (layout: "Q1 + C1 on the back beneath the bottom edge strip"), not a net.
n("VCC", ("Q1", "2"), ("C1", "1"), ("C2", "1"), ("C3", "1"), ("R13", "1"),
  ("U1", "8"), ("BZ1", "1"), ("D1", "2"), ("TP2", "1"),
  ("#PWR01", "1"), ("#FLG01", "1"))
n("GND", ("PAD2", "1"), ("Q1", "1"), ("Q2", "2"), ("D1", "1"),
  ("C1", "2"), ("C2", "2"), ("C3", "2"), ("C4", "2"), ("U1", "4"),
  ("R15", "2"), ("R16", "2"), ("TP6", "1"),
  ("#PWR02", "1"), ("#FLG02", "1"))

# SPEC "1 PB5 / RESET": ISP only, R13 10k pull-up, C4 10nF filter.  RSTDISBL
# is never set, so this pin has to stay reachable.
n("RESET", ("U1", "1"), ("R13", "2"), ("C4", "1"), ("TP5", "1"))

# SPEC "Buzzer cell (BZ1)": PB4 -> R14 2.2k -> Q2 MMBT2222A base; emitter GND;
# collector sinks BZ1 from VCC; D1 BAV99 clamps the collector to both rails.
# KiCad's Diode:BAV99 draws the series pair as pin1 -A|K- pin3 -A|K- pin2, so
# pin 3 is the common node.  GND on pin 1 and VCC on pin 2 puts the two
# forward paths where a coil needs them: GND -> collector on undershoot,
# collector -> VCC for the flyback kick.  The polarity is not optional; the
# clamp is the mandatory flyback for a magnetic element.
n("SND", ("U1", "3"), ("R14", "1"))
n("SND_B", ("R14", "2"), ("Q2", "1"))
n("SND_C", ("Q2", "3"), ("BZ1", "2"), ("D1", "3"))

# SPEC "Pin budget" + "ISP": three ISP lines are charlieplex lines, deliberately.
n("L0", LINES["L0"], ("TP4", "1"))
n("L1", LINES["L1"], ("TP1", "1"))
n("L2", LINES["L2"], ("TP3", "1"), ("S2", "1"))
n("L3", LINES["L3"], ("S1", "1"))

# SPEC "Buttons share the charlieplex lines, deliberately": line -> button ->
# 4.7k -> GND, read in the display blanking window.  NOTHING else may hang off
# a charlieplex line - in particular no debounce capacitor, which would smear
# every LED slot.  Debounce is firmware.
n("S1_R", ("S1", "2"), ("R15", "1"))
n("S2_R", ("S2", "2"), ("R16", "1"))

# SPEC "The ring": one resistor per LED (not per line), so brightness is
# independent of how many LEDs share a scan slot.  Topology is
# anode line - LED - R - cathode line.
for pos, hi, lo, rref, pkg in RING:
    n(hi, (f"LED{pos}", "2"))           # pin 2 = anode
    n(f"LED{pos}_K", (f"LED{pos}", "1"), (rref, "1"))   # pin 1 = cathode
    n(lo, (rref, "2"))


# ------------------------------------------------------------------- emitter
def pin_xy(ref):
    """(number -> (x, y)) sheet connection points for a ref, rotation 0."""
    lid = PARTS[ref][0] if ref in PARTS else POWER[ref][0]
    x, y = (PARTS[ref][4], PARTS[ref][5]) if ref in PARTS \
        else (POWER[ref][2], POWER[ref][3])
    out = {}
    for (unit, num), (px, py) in lib_pins(lid).items():
        if unit in (0, 1):
            out[num] = (round(x + px, 4), round(y - py, 4))
    return out


def prop(name, value, x, y, hide=False, ind=2):
    eff = ["effects", ["font", ["size", "1.27", "1.27"]]]
    if hide:
        eff.append(["hide", "yes"])
    return dump(["property", f'"{name}"', f'"{value}"',
                 ["at", f"{x}", f"{y}", "0"], eff], ind)


def sym_block(ref, lib_id, value, fp, crib, x, y, in_bom, on_board):
    body = [
        f'\t\t(lib_id "{lib_id}")',
        f"\t\t(at {x} {y} 0)",
        "\t\t(unit 1)",
        "\t\t(exclude_from_sim no)",
        f"\t\t(in_bom {in_bom})",
        f"\t\t(on_board {on_board})",
        "\t\t(dnp no)",
        f'\t\t(uuid "{uid("sym:" + ref)}")',
        prop("Reference", ref, x, round(y - 5.08, 4), ref.startswith("#")),
        prop("Value", value, x, round(y + 5.08, 4), ref.startswith("#")),
    ]
    if fp is not None:
        body.append(prop("Footprint", fp, x, y, True))
        body.append(prop("Datasheet", "", x, y, True))
        body.append(prop("Crib", CRIB[crib], x, y, True))
    for num in sorted(pin_xy(ref)):
        body.append(f'\t\t(pin "{num}" (uuid "{uid(ref + ".pin." + num)}"))')
    body.append('\t\t(instances\n\t\t\t(project ""\n\t\t\t\t(path "/"\n'
                f'\t\t\t\t\t(reference "{ref}")\n\t\t\t\t\t(unit 1))))')
    return "\t(symbol\n" + "\n".join(body) + ")"


def build():
    lib_ids = sorted({v[0] for v in PARTS.values()}
                     | {v[0] for v in POWER.values()})
    out = ["(kicad_sch",
           "\t(version 20250610)",
           '\t(generator "tools-schematic.py")',
           '\t(generator_version "10.0")',
           '\t(paper "A2")',
           '\t(title_block\n\t\t(title "ORBIT v1 - Board B (chase-the-light)")'
           '\n\t\t(company "ClauderaCAM PCB lane")'
           '\n\t\t(comment 1 "generated by boards/orbit/tools-schematic.py '
           '- do not hand-edit"))',
           "\t(lib_symbols"]
    for lid in lib_ids:
        out.append(dump(embed(lid), 2))
    out.append("\t)")

    for ref in sorted(PARTS):
        lid, value, fp, crib, x, y = PARTS[ref]
        out.append(sym_block(ref, lid, value, fp, crib, x, y,
                             "no" if ref.startswith("TP") else "yes", "yes"))
    for ref in sorted(POWER):
        lid, value, x, y = POWER[ref]
        out.append(sym_block(ref, lid, value, None, None, x, y, "no", "no"))

    seen = {}
    for (ref, num), net in sorted(NET.items()):
        xy = pin_xy(ref)[num]
        assert xy not in seen, f"pin collision {ref}.{num} vs {seen[xy]} @{xy}"
        seen[xy] = f"{ref}.{num}"
        out.append(f'\t(label "{net}" (at {xy[0]} {xy[1]} 0) '
                   "(effects (font (size 1.27 1.27)) "
                   "(justify left bottom)))")
    for ref, num in NC:
        xy = pin_xy(ref)[num]
        assert xy not in seen, f"no-connect collides with {seen.get(xy)}"
        seen[xy] = f"{ref}.{num}"
        out.append(f"\t(no_connect (at {xy[0]} {xy[1]}))")

    open(SCH, "w").write("\n".join(out) + "\n)\n")
    return len(seen)


# ================================================================== VERIFY
# Everything below re-reads the FILE, never the tables above, and compares it
# to SPEC.md's own words.  Article I's habit applied to a schematic: the bytes
# that would go to the layout are what gets checked.
def reread():
    root = sexp(open(SCH).read())[0]
    syms, labels, ncs = {}, [], []
    for c in root[1:]:
        if not isinstance(c, list):
            continue
        if c[0] == "symbol" and kid(c, "lib_id") is not None:
            props = {unq(p[1]): unq(p[2]) for p in kids(c, "property")}
            at = kid(c, "at")
            syms[props["Reference"]] = {
                "lib_id": unq(kid(c, "lib_id")[1]),
                "value": props.get("Value", ""),
                "fp": props.get("Footprint"),
                "crib": props.get("Crib"),
                "at": (float(at[1]), float(at[2])),
            }
        elif c[0] == "label":
            at = kid(c, "at")
            labels.append((unq(c[1]), (float(at[1]), float(at[2]))))
        elif c[0] == "no_connect":
            at = kid(c, "at")
            ncs.append((float(at[1]), float(at[2])))
    lib = {}
    for c in kid(root, "lib_symbols")[1:]:
        lib[unq(c[1])] = c
    return syms, labels, ncs, lib


def file_pins(syms, lib):
    """(ref, num) -> (x, y) rebuilt from the emitted lib_symbols geometry."""
    out = {}
    for ref, s in syms.items():
        blk = lib[s["lib_id"]]
        x, y = s["at"]

        def walk(node, unit):
            for c in node[1:]:
                if not isinstance(c, list):
                    continue
                if c[0] == "symbol":
                    m = re.match(r'"[^"]*_(\d+)_\d+"', c[1])
                    walk(c, int(m.group(1)) if m else unit)
                elif c[0] == "pin" and unit in (0, 1):
                    at, num = kid(c, "at"), kid(c, "number")
                    out[(ref, unq(num[1]))] = (round(x + float(at[1]), 4),
                                               round(y - float(at[2]), 4))
        walk(blk, 1)
    return out


def ring_contract():
    """The matrix table's own invariants, asserted against what SPEC SAYS
    rather than against the table that says it.

    A check that reads only the table it is checking cannot fail: corrupt the
    table and the comparison quietly follows it.  Two mutation tests proved
    exactly that here (a degenerate L2/L2 row and a swapped resistor package
    both sailed through file-vs-table agreement), so the structural claims
    SPEC makes in prose are spelled out as literals below.
    """
    assert [p for p, *_ in RING] == list(range(1, 13))
    pairs = [(hi, lo) for _, hi, lo, _, _ in RING]
    assert all(hi != lo for hi, lo in pairs), "an LED across ONE line never lights"
    lines = ("L0", "L1", "L2", "L3")
    # SPEC: "Charlieplexing 4 lines gives exactly 4x3 = 12 ordered pairs, so
    # the ring is fully populated with no spare positions"
    assert set(pairs) == {(a, b) for a in lines for b in lines if a != b}
    # SPEC: "six sectors of two adjacent positions, each sector being one
    # antiparallel pair on one line pair (1,2 = L0/L1; 3,4 = L0/L2; ...)"
    for s, (a, b) in enumerate([("L0", "L1"), ("L0", "L2"), ("L0", "L3"),
                                ("L1", "L2"), ("L1", "L3"), ("L2", "L3")]):
        assert pairs[2 * s] == (a, b) and pairs[2 * s + 1] == (b, a), \
            f"sector {s + 1} is not the antiparallel pair {a}/{b}"
    # SPEC BOM: R1,R4,R7,R10 = 1206 | R2,R5,R8,R11 = 0805 | R3,R6,R9,R12 = 0603
    got = {}
    for _, _, _, rref, pkg in RING:
        got.setdefault(pkg, []).append(rref)
    assert got == {"1206": ["R1", "R4", "R7", "R10"],
                   "0805": ["R2", "R5", "R8", "R11"],
                   "0603": ["R3", "R6", "R9", "R12"]}, got
    # SPEC "Pin budget - the whole design is this table"
    assert LINES == {"L0": ("U1", "5"), "L1": ("U1", "6"),
                     "L2": ("U1", "7"), "L3": ("U1", "2")}
    # a package label and its footprint must not be able to disagree
    for pkg, fp in R_FP.items():
        assert f"R_{pkg}_" in fp and "HandSolder" in fp, fp
    for pkg, fp in C_FP.items():
        assert f"C_{pkg}_" in fp and "HandSolder" in fp, fp


def verify():
    ring_contract()
    syms, labels, ncs, lib = reread()
    pins = file_pins(syms, lib)
    at_xy = {}
    for net, xy in labels:
        at_xy.setdefault(xy, []).append(net)
    for xy, nets in at_xy.items():
        assert len(nets) == 1, f"{len(nets)} labels stacked at {xy}: {nets}"

    net_of, members = {}, {}
    for rp, xy in pins.items():
        if xy in at_xy:
            net_of[rp] = at_xy[xy][0]
            members.setdefault(at_xy[xy][0], set()).add(rp)
    unlabelled = sorted(rp for rp in pins
                        if rp not in net_of and pins[rp] not in ncs)
    assert not unlabelled, f"pins with neither label nor no-connect: {unlabelled}"
    assert len({v for v in pins.values()}) == len(pins), "coincident pins"
    off = sorted(rp for rp, (x, y) in pins.items()
                 if abs(x / G - round(x / G)) > 1e-6
                 or abs(y / G - round(y / G)) > 1e-6)
    assert not off, f"pins off the 1.27 mm connection grid: {off}"

    def net(ref, num):
        assert (ref, num) in net_of, f"{ref}.{num} unconnected"
        return net_of[(ref, num)]

    # --- SPEC "Pin budget": the four charlieplex lines land on these pins
    for line, (ref, num) in LINES.items():
        assert net(ref, num) == line, f"{line} not on {ref}.{num}"
    assert net("U1", "8") == "VCC" and net("U1", "4") == "GND"
    assert net("U1", "1") == "RESET" and net("U1", "3") == "SND"

    # --- SPEC "The ring": the 12-entry matrix, verbatim, plus per-LED R
    leds = sorted(r for r in syms if r.startswith("LED"))
    assert len(leds) == 12, leds
    for pos, hi, lo, rref, pkg in RING:
        led = f"LED{pos}"
        assert net(led, "2") == hi, f"{led} anode on {net(led, '2')} not {hi}"
        assert net(rref, "2") == lo, f"{rref} not returned to {lo}"
        priv = net(led, "1")
        assert priv == net(rref, "1"), f"{led}-{rref} not in series"
        assert members[priv] == {(led, "1"), (rref, "1")}, \
            f"{priv} is not a private LED-R node: {sorted(members[priv])}"
        assert syms[rref]["value"] == "560R", syms[rref]["value"]
        assert syms[rref]["fp"] == R_FP[pkg], syms[rref]["fp"]
        assert syms[rref]["crib"] == CRIB[pkg]
        assert syms[led]["fp"] == "LED_THT:LED_D5.0mm"
        assert syms[led]["crib"] == CRIB["tht"]
    # every line pair is used exactly once in each direction (4x3 = 12)
    assert len({(hi, lo) for _, hi, lo, _, _ in RING}) == 12
    assert {pkg for _, _, _, _, pkg in RING} == {"0603", "0805", "1206"}

    # --- SPEC "Buttons share the charlieplex lines, deliberately"
    for sw, rr, line in (("S1", "R15", "L3"), ("S2", "R16", "L2")):
        assert net(sw, "1") == line, f"{sw} not on {line}"
        mid = net(sw, "2")
        assert members[mid] == {(sw, "2"), (rr, "1")}, sorted(members[mid])
        assert net(rr, "2") == "GND"
        assert syms[rr]["value"] == "4.7k", syms[rr]["value"]
    # "no debounce capacitor": no C pin may touch a matrix line or a button node
    caps = {r for r in syms if r.startswith("C")}
    for line in list(LINES) + ["S1_R", "S2_R"]:
        touching = {r for r, _ in members[line]} & caps
        assert not touching, f"capacitor {touching} on matrix node {line}"

    # --- SPEC "Buzzer cell (BZ1)"
    assert members["SND"] == {("U1", "3"), ("R14", "1")}
    assert members["SND_B"] == {("R14", "2"), ("Q2", "1")}
    assert syms["R14"]["value"] == "2.2k" and syms["R14"]["fp"] == R_FP["0805"]
    assert syms["Q2"]["value"] == "MMBT2222A"
    assert syms["Q2"]["lib_id"] == "Transistor_BJT:Q_NPN_BEC"  # 1=B 2=E 3=C
    assert net("Q2", "2") == "GND", "Q2 emitter must be GND"
    assert members["SND_C"] == {("Q2", "3"), ("BZ1", "2"), ("D1", "3")}
    assert net("BZ1", "1") == "VCC", "BZ1 '+' pin sits on VCC"
    assert syms["D1"]["value"] == "BAV99" and syms["D1"]["fp"] == SOT23
    assert net("D1", "1") == "GND" and net("D1", "2") == "VCC"
    assert syms["C3"]["value"] == "1uF" and syms["C3"]["fp"] == C_FP["0603"]
    assert net("C3", "1") == "VCC" and net("C3", "2") == "GND"

    # --- SPEC "Power entry"
    assert members["VBAT"] == {("PAD1", "1"), ("SW1", "2")}, "PAD+ -> SW1 common"
    assert members["VSW"] == {("SW1", "1"), ("Q1", "3")}, "throw -> Q1 drain"
    assert pins[("SW1", "3")] in ncs, "unused SPDT throw needs a no-connect"
    assert ("SW1", "3") not in net_of
    assert syms["Q1"]["value"] == "AO3401"
    assert syms["Q1"]["lib_id"] == "Transistor_FET:AO3401A"   # 1=G 2=S 3=D
    assert net("Q1", "2") == "VCC", "Q1 source feeds the load"
    assert net("Q1", "1") == "GND", "Q1 gate to GND (zigbee configuration)"
    assert net("PAD2", "1") == "GND"
    assert syms["C1"]["value"] == "10uF" and syms["C1"]["fp"] == C_FP["0805"]
    assert net("C1", "1") == "VCC" and net("C1", "2") == "GND"
    assert syms["C2"]["value"] == "100nF" and syms["C2"]["fp"] == C_FP["0805"]
    assert net("C2", "1") == "VCC" and net("C2", "2") == "GND"

    # --- SPEC "1 PB5 / RESET" + the removable C4
    assert members["RESET"] == {("U1", "1"), ("R13", "2"), ("C4", "1"),
                                ("TP5", "1")}
    assert syms["R13"]["value"] == "10k" and syms["R13"]["fp"] == R_FP["0603"]
    assert net("R13", "1") == "VCC", "R13 is a pull-up"
    assert syms["C4"]["value"] == "10nF" and syms["C4"]["fp"] == C_FP["1206"]
    assert net("C4", "2") == "GND"

    # --- SPEC "ISP - bare pads, no connector": MISO/VCC SCK/MOSI RST/GND
    for ref, want in (("TP1", "L1"), ("TP2", "VCC"), ("TP3", "L2"),
                      ("TP4", "L0"), ("TP5", "RESET"), ("TP6", "GND")):
        assert net(ref, "1") == want, f"{ref} on {net(ref, '1')} not {want}"
        assert syms[ref]["fp"] == "orbit:ISP_Pad_D1.8mm"

    # --- SPEC BOM: U1 is the WIDE body.  parts/attiny85.toml exists to stop
    # exactly this substitution; a 3.9 mm narrow SOIC-8 is a reflow failure.
    assert syms["U1"]["fp"] == SOIC8W, syms["U1"]["fp"]
    assert "5.3x5.3" in syms["U1"]["fp"] and "3.9x4.9" not in syms["U1"]["fp"]
    assert syms["U1"]["value"] == "ATTINY85-20SU"
    assert syms["U1"]["crib"] == "parts/attiny85.toml"

    # --- Article XI: every placed symbol carries a footprint and a crib trace
    root = os.path.dirname(os.path.dirname(HERE))
    for ref, s in syms.items():
        if ref.startswith("#"):
            continue
        assert s["fp"], f"{ref} has no footprint field"
        assert s["crib"], f"{ref} has no crib trace"
        if s["crib"].startswith("parts/"):
            assert os.path.exists(os.path.join(root, s["crib"])), s["crib"]
    assert len(syms) == len(PARTS) + len(POWER)

    return syms, net_of, members


def verify_netlist():
    """Ground truth: kicad-cli's own netlister must reproduce NET exactly."""
    out = os.path.join(HERE, ".sch-net.tmp")
    subprocess.run(["kicad-cli", "sch", "export", "netlist",
                    "--format", "kicadsexpr", "-o", out, SCH],
                   check=True, capture_output=True)
    text = open(out).read()
    os.unlink(out)
    got, orphan = {}, set()
    for block in re.split(r"\(net\b", text.split("(nets")[1])[1:]:
        name = re.search(r'\(name "([^"]*)"\)', block).group(1).lstrip("/")
        nodes = re.findall(r'\(node\s+\(ref "([^"]+)"\)\s+\(pin "([^"]+)"\)',
                           block, re.S)
        if not nodes:
            continue
        # kicad-cli mints an "unconnected-(...)" net per no-connect pin; the
        # set of those must be EXACTLY the declared NC list, no more.
        if name.startswith("unconnected-"):
            orphan |= set(nodes)
        else:
            got[name] = {rp for rp in nodes if not rp[0].startswith("#")}
    assert orphan == set(NC), f"stray unconnected pins: {orphan ^ set(NC)}"
    want = {}
    for rp, net in NET.items():
        if not rp[0].startswith("#"):
            want.setdefault(net, set()).add(rp)
    assert got == want, "netlist != contract: " + repr(
        {k: (got.get(k), want.get(k)) for k in set(got) ^ set(want)
         or {k for k in want if got.get(k) != want[k]}})
    npins = sum(len(v) for v in got.values())
    return len(got), npins


if __name__ == "__main__":
    nlabels = build()
    syms, net_of, members = verify()
    nets, npins = verify_netlist()
    print(f"orbit.kicad_sch: {len(syms)} symbols, {nets} nets, "
          f"{npins} connected pins ({nlabels} labels + no-connects), "
          f"{len(RING)} ring positions verified against SPEC")
