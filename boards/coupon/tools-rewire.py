#!/usr/bin/env python3
"""Deterministic rewiring of coupon.kicad_sch: strip all broken
wires/labels/NCs, place net labels EXACTLY on pin endpoints, verify via
kicad-cli netlist export. Pin positions: simple symbols use the
konnect-verified dump; U1/U2/Q1 (rot 0, no mirror) derive from the
embedded lib_symbols as (x + px, y - py)."""
import re
import subprocess
import sys
from pathlib import Path

SCH = Path.home() / "scratch/carvera/clauderacam/boards/coupon/coupon.kicad_sch"

# ---- the authoritative netlist: (ref, pin) -> net -------------------------
NET = {}
def n(net, *pins):
    for rp in pins:
        NET[rp] = net

n("VIN", ("PAD1", "1"), ("SW1", "1"))
n("VIN_SW", ("SW1", "2"), ("D1", "2"))
n("VF", ("D1", "1"), ("C1", "1"), ("L1", "1"))
n("VCC", ("L1", "2"), ("C2", "1"), ("C3", "1"), ("R1", "1"), ("R2", "1"),
  ("R6", "1"), ("R7", "1"), ("R9", "1"), ("C7", "1"), ("C8", "1"),
  ("U1", "8"), ("U1", "4"), ("U2", "8"))
n("GND", ("PAD2", "1"), ("C1", "2"), ("LED1", "1"), ("C2", "2"),
  ("C3", "2"), ("C4", "2"), ("C5", "2"), ("C6", "2"), ("C7", "2"),
  ("C8", "2"), ("R10", "2"), ("LED4", "1"), ("U1", "1"), ("Q1", "2"),
  ("U2", "4"), ("U2", "5"))
n("TRIG_THR", ("R3", "2"), ("C4", "1"), ("R4", "2"), ("R8", "1"),
  ("U1", "2"), ("U1", "6"))
n("BLINK", ("R5", "1"), ("U1", "3"))
n("N_DISCH", ("R2", "2"), ("R3", "1"), ("S2", "1"), ("U1", "7"))
n("N_CV", ("C5", "1"), ("U1", "5"))
n("N_KICK", ("S2", "2"), ("R4", "1"))
n("N_Q1B", ("R5", "2"), ("Q1", "1"))
n("LEDK", ("LED2", "1"), ("LED3", "1"), ("Q1", "3"))
n("N_LED1", ("R1", "2"), ("LED1", "2"))
n("N_LED2A", ("R6", "2"), ("LED2", "2"))
n("N_LED3A", ("R7", "2"), ("LED3", "2"))
n("VHALF", ("R9", "2"), ("R10", "1"), ("C6", "1"), ("U2", "2"))
n("N_U2AIN", ("R8", "2"), ("U2", "3"))
n("N_U2AOUT", ("R11", "1"), ("U2", "1"))
n("N_LED4A", ("R11", "2"), ("LED4", "2"))
n("N_U2BFB", ("U2", "6"), ("U2", "7"))
n("LADDER_040", ("TP1", "1"), ("TP2", "1"))
n("LADDER_050", ("TP3", "1"), ("TP4", "1"))
n("LADDER_060", ("TP5", "1"), ("TP6", "1"))
NC = [("SW1", "3")]

# konnect-verified absolute pin positions (simple symbols)
POS = {
 ("PAD1","1"):(25.4,22.86), ("SW1","2"):(41.91,25.4),
 ("SW1","1"):(46.99,22.86), ("SW1","3"):(46.99,27.94),
 ("D1","1"):(66.04,25.4), ("D1","2"):(63.5,25.4),
 ("C1","1"):(74.93,38.354), ("C1","2"):(74.93,40.386),
 ("L1","1"):(87.63,25.4), ("L1","2"):(92.71,25.4),
 ("C2","1"):(100.33,38.354), ("C2","2"):(100.33,40.386),
 ("C3","1"):(110.49,38.354), ("C3","2"):(110.49,40.386),
 ("R1","1"):(119.38,17.78), ("R1","2"):(119.38,22.86),
 ("LED1","1"):(119.38,34.29), ("LED1","2"):(119.38,31.75),
 ("PAD2","1"):(25.4,52.07),
 ("C7","1"):(195.58,73.914), ("C7","2"):(195.58,75.946),
 ("C5","1"):(199.39,65.024), ("C5","2"):(199.39,67.056),
 ("R2","1"):(189.23,36.83), ("R2","2"):(189.23,41.91),
 ("R3","1"):(201.93,60.96), ("R3","2"):(207.01,60.96),
 ("S2","1"):(157.48,35.56), ("S2","2"):(162.56,35.56),
 ("R4","1"):(160.02,41.91), ("R4","2"):(160.02,46.99),
 ("C4","1"):(149.86,73.914), ("C4","2"):(149.86,75.946),
 ("R5","1"):(227.33,54.61), ("R5","2"):(232.41,54.61),
 ("R6","1"):(260.35,36.83), ("R6","2"):(260.35,41.91),
 ("LED2","1"):(260.35,55.88), ("LED2","2"):(260.35,53.34),
 ("R7","1"):(275.59,36.83), ("R7","2"):(275.59,41.91),
 ("LED3","1"):(275.59,55.88), ("LED3","2"):(275.59,53.34),
 ("C8","1"):(180.34,109.474), ("C8","2"):(180.34,111.506),
 ("R8","1"):(147.32,134.62), ("R8","2"):(152.4,134.62),
 ("R11","1"):(182.88,134.62), ("R11","2"):(187.96,134.62),
 ("LED4","1"):(194.31,134.62), ("LED4","2"):(196.85,134.62),
 ("R9","1"):(154.94,116.84), ("R9","2"):(154.94,121.92),
 ("R10","1"):(154.94,147.32), ("R10","2"):(154.94,152.4),
 ("C6","1"):(144.78,134.874), ("C6","2"):(144.78,136.906),
 ("TP1","1"):(30.48,196.85), ("TP2","1"):(44.45,196.85),
 ("TP3","1"):(64.77,196.85), ("TP4","1"):(80.01,196.85),
 ("TP5","1"):(100.33,196.85), ("TP6","1"):(115.57,196.85),
}

text = SCH.read_text()


def _extract_block(t, start):
    d = 0
    i = start
    while True:
        if t[i] == '(':
            d += 1
        elif t[i] == ')':
            d -= 1
            if d == 0:
                return t[start:i + 1]
        i += 1


# Q1's symbol was never embedded and its lib_id pointed at a library
# that doesn't carry it in KiCad 10 (Q_NPN_BEC moved to Transistor_BJT).
# Repoint the instance and pull the real block from the system library so
# both the netlist and our pin math can see it.
text = text.replace('(lib_id "Device:Q_NPN_BEC")',
                    '(lib_id "Transistor_BJT:Q_NPN_BEC")')
if '(symbol "Transistor_BJT:Q_NPN_BEC"' not in text:
    dev = Path("/usr/share/kicad/symbols/"
               "Transistor_BJT.kicad_sym").read_text()
    i = dev.find('(symbol "Q_NPN_BEC"')
    assert i >= 0, "Q_NPN_BEC not in Transistor_BJT"
    blk = _extract_block(dev, i)
    blk = blk.replace('(symbol "Q_NPN_BEC"',
                      '(symbol "Transistor_BJT:Q_NPN_BEC"', 1)
    ls = text.find("(lib_symbols")
    end = ls + len(_extract_block(text, ls))
    text = text[:end - 1] + "\n" + blk + "\n\t)" + text[end:]
    SCH.write_text(text)
    print("embedded Transistor_BJT:Q_NPN_BEC from the system library")

# ---- top-level block splitter ----------------------------------------------
def blocks(t):
    """yield (start, end, head) for depth-1 s-expr blocks."""
    depth = 0
    start = None
    i = 0
    while i < len(t):
        c = t[i]
        if c == '"':
            i += 1
            while i < len(t) and t[i] != '"':
                i += 2 if t[i] == '\\' else 1
        elif c == '(':
            depth += 1
            if depth == 2:
                start = i
        elif c == ')':
            if depth == 2 and start is not None:
                head = t[start + 1:start + 40].split()[0]
                yield (start, i + 1, head)
                start = None
            depth -= 1
        i += 1

# ---- lib_symbols pin extraction for U1/U2/Q1 -------------------------------
def lib_pins(libname):
    m = re.search(r'\(symbol "' + re.escape(libname) + r'"', text)
    if not m:
        sys.exit(f"lib symbol {libname} not found")
    # find its full block
    d = 0
    i = m.start()
    while True:
        if text[i] == '(':
            d += 1
        elif text[i] == ')':
            d -= 1
            if d == 0:
                break
        i += 1
    seg = text[m.start():i + 1]
    ext = re.search(r'\(extends "([^"]+)"\)', seg[:300])
    pins = dict(lib_pins(ext.group(1))) if ext else {}
    # (unit, number) -> (px, py); child pins (if any) override parent
    for sm in re.finditer(r'\(symbol "([^"]+_(\d+)_\d+)"', seg):
        unit = int(sm.group(2))
        d2 = 0
        j = sm.start()
        while True:
            if seg[j] == '(':
                d2 += 1
            elif seg[j] == ')':
                d2 -= 1
                if d2 == 0:
                    break
            j += 1
        sub = seg[sm.start():j + 1]
        for pm in re.finditer(
                r'\(pin[^(]*\(at ([-\d.]+) ([-\d.]+) [\d.]+\)'
                r'[\s\S]*?\(number "([^"]+)"', sub):
            pins[(unit, pm.group(3))] = (float(pm.group(1)),
                                         float(pm.group(2)))
    return pins

def instances():
    """(ref, lib_id, x, y, rot, unit) for placed symbols."""
    out = []
    for s, e, head in list(blocks(text)):
        if head != "symbol":
            continue
        seg = text[s:e]
        lid = re.search(r'\(lib_id "([^"]+)"\)', seg)
        at = re.search(r'\(at ([-\d.]+) ([-\d.]+) ([-\d.]+)\)', seg)
        un = re.search(r'\(unit (\d+)\)', seg)
        rf = re.search(r'\(property "Reference" "([^"]+)"', seg)
        out.append((rf.group(1), lid.group(1), float(at.group(1)),
                    float(at.group(2)), float(at.group(3)),
                    int(un.group(1)) if un else 1, s, e))
    return out

# konnect embedded derived symbols with (extends ...) — kicad-cli never
# resolves extends inside a schematic (KiCad flattens on embed). Replace
# each derived block with its parent's, renamed (top name + sub-unit
# stems) to the child.
while True:
    m = re.search(r'\(symbol "([^"]+)"\s*\(extends "([^"]+)"\)', text)
    if not m:
        break
    child, parent = m.group(1), m.group(2)
    cblk = _extract_block(text, m.start())
    pm = re.search(r'\(symbol "' + re.escape(parent) + r'"\s*\(', text)
    if not pm:
        sys.exit(f"extends parent {parent} not embedded")
    pblk = _extract_block(text, pm.start())
    cstem, pstem = child.split(":")[-1], parent.split(":")[-1]
    nblk = pblk.replace(f'(symbol "{parent}"',
                        f'(symbol "{child}"', 1)
    nblk = nblk.replace(f'"{pstem}_', f'"{cstem}_')
    text = text.replace(cblk, nblk, 1)
    print(f"flattened {child} <- {parent}")
SCH.write_text(text)

# konnect placed LM358 unit A three times — assign the real units by
# position (134.62 sits in the comparator row = unit A; 110.49 has the
# decoupling cap beside it = power unit; 160.02 = parked unit B)
U2_UNIT = {110.49: 3, 134.62: 1, 160.02: 2}
repl = []
for s, e, head in blocks(text):
    if head == "symbol" and '"Amplifier_Operational:LM358"' in text[s:e]:
        seg = text[s:e]
        at = re.search(r'\(at [-\d.]+ ([-\d.]+) [-\d.]+\)', seg)
        u = U2_UNIT[float(at.group(1))]
        # BOTH unit fields: the symbol's own and the per-project one
        # inside (instances ...) — the netlister reads the latter
        repl.append((seg, re.sub(r'\(unit \d+\)', f'(unit {u})', seg)))
for old, newseg in repl:
    text = text.replace(old, newseg, 1)
SCH.write_text(text)

inst = instances()
# EVERY pin position derives from the embedded lib geometry — the konnect
# dump reported pin bases, not endpoints (1.27mm short). Transform for
# visual-CCW rotation on y-down sheet coords, symbol space y-up:
#   0:(x+px, y-py) 90:(x-py, y-px) 180:(x-px, y+py) 270:(x+py, y+px)
POS = {}
ROT = {0: lambda px, py: (px, -py), 90: lambda px, py: (-py, -px),
       180: lambda px, py: (-px, py), 270: lambda px, py: (py, px)}
for ref, lid, x, y, rot, unit, s, e in inst:
    if lid.startswith("power:"):
        continue
    if "(mirror" in text[s:e]:
        sys.exit(f"{ref} is mirrored — transform not handled")
    lp = lib_pins(lid)
    f = ROT[int(rot) % 360]
    for (u, num), (px, py) in lp.items():
        if u == unit or u == 0:
            dx_, dy_ = f(px, py)
            POS[(ref, num)] = (round(x + dx_, 4), round(y + dy_, 4))

missing = [rp for rp in list(NET) + NC if rp not in POS]
if missing:
    sys.exit(f"missing pin positions: {missing}")

# ---- rebuild: drop wires/labels/ncs/junctions/power syms, renumber ---------
DROP = {"wire", "label", "global_label", "no_connect", "junction"}
out = []
last = 0
power_seen = 0
for s, e, head in blocks(text):
    seg = text[s:e]
    drop = head in DROP
    if head == "symbol" and '"power:' in seg[:200]:
        # keep the two power symbols, land their PIN (not anchor) exactly
        # on a real pin endpoint, and give them proper references
        power_seen += 1
        lib = "power:VCC" if "power:VCC" in seg else "power:GND"
        tgt = POS[("L1", "2")] if lib == "power:VCC" \
            else POS[("PAD2", "1")]
        (px, py), = [v for (u, _), v in lib_pins(lib).items()]
        ax, ay = round(tgt[0] - px, 4), round(tgt[1] + py, 4)
        seg = re.sub(r'\(at [-\d.]+ [-\d.]+ [-\d.]+\)',
                     f'(at {ax} {ay} 0)', seg, count=1)
        seg = re.sub(r'\(property "Reference" "[^"]*"',
                     f'(property "Reference" "#PWR0{power_seen}"', seg,
                     count=1)
        out.append(text[last:s]); out.append(seg); last = e
        continue
    if drop:
        out.append(text[last:s])
        last = e
new = "".join(out) + text[last:]

adds = []
for (ref, num), net in sorted(NET.items()):
    x, y = POS[(ref, num)]
    adds.append(f'  (label "{net}" (at {x} {y} 0) '
                f'(effects (font (size 1.27 1.27)) (justify left bottom)))')
for ref, num in NC:
    x, y = POS[(ref, num)]
    adds.append(f'  (no_connect (at {x} {y}))')
# PWR_FLAG drivers on both rails (nets fed through a passive filter and
# a wire pad have no power-out pin; this is the standard KiCad answer)
import uuid
pf = Path("/usr/share/kicad/symbols/power.kicad_sym").read_text()
pfb = _extract_block(pf, pf.find('(symbol "PWR_FLAG"'))
pfb = pfb.replace('(symbol "PWR_FLAG"', '(symbol "power:PWR_FLAG"', 1)
ls = new.find("(lib_symbols")
end = ls + len(_extract_block(new, ls))
new = new[:end - 1] + "\n" + pfb + "\n\t)" + new[end:]
m = re.search(r'\(pin[^(]*\(at ([-\d.]+) ([-\d.]+)', pfb)
fpx, fpy = float(m.group(1)), float(m.group(2))
proj = re.search(r'\(project "[^"]*"\s*\(path "[^"]*"', new).group(0)
for k, tgt in (("03", POS[("L1", "2")]), ("04", POS[("PAD2", "1")])):
    ax, ay = round(tgt[0] - fpx, 4), round(tgt[1] + fpy, 4)
    adds.append(
        f'  (symbol (lib_id "power:PWR_FLAG") (at {ax} {ay} 0) (unit 1)\n'
        f'    (in_bom no) (on_board no) (uuid "{uuid.uuid4()}")\n'
        f'    (property "Reference" "#FLG0{k}" (at {ax} {ay - 2} 0)\n'
        f'      (effects (font (size 1.27 1.27)) (hide yes)))\n'
        f'    (property "Value" "PWR_FLAG" (at {ax} {ay - 4} 0)\n'
        f'      (effects (font (size 1.27 1.27)) (hide yes)))\n'
        f'    (pin "1" (uuid "{uuid.uuid4()}"))\n'
        f'    (instances {proj} (reference "#FLG0{k}") (unit 1))))\n'
        f'  )')

new = new.rstrip()
assert new.endswith(")")
new = new[:-1] + "\n" + "\n".join(adds) + "\n)\n"
SCH.write_text(new)
print(f"rewired: {len(NET)} labeled pins, {len(NC)} NCs, "
      f"{power_seen} power symbols repositioned")
