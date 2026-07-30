#!/usr/bin/env python3
"""Deterministic Board A layout: rebuilds coupon.kicad_pcb from the schematic netlist.

Companion to tools-rewire.py (which built the schematic). Run with system
python3 (pcbnew, KiCad 10.0.4). Re-running regenerates the identical board.

Laws encoded here (SPEC.md + milled-board process rules):
  clearance >=0.4, track >=0.5 (0.4 ladder only inside the 'coupon_ladder'
  rule area), copper-to-edge >=0.4, THT annular >=0.6, B.Cu only, all silk
  on B.Silkscreen, GND pour min-width 0.5 with thermal reliefs.
Jumpers (Bill 2026-07-29): unplated front-side wire links are permitted;
  JP* footprints are board-only (excluded from schematic parity), drills
  from the 0.3-1.2 straight set, annular >=0.6, standard 10.16 mm span.
"""
import re, subprocess, sys, os
import pcbnew
from pcbnew import VECTOR2I, EDA_ANGLE

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "coupon.kicad_pcb")
SCH = os.path.join(HERE, "coupon.kicad_sch")
LIBROOT = "/usr/share/kicad/footprints"
OX, OY = 100.0, 100.0          # page position of board top-left corner
W, H = 55.0, 40.0

def MM(x, y):                   # board mm -> page nm vector
    return VECTOR2I(int(round((OX + x) * 1e6)), int(round((OY + y) * 1e6)))

def NM(v):                      # mm scalar -> nm
    return int(round(v * 1e6))

# ---------------------------------------------------------------- netlist
def parse_netlist():
    out = os.path.join(HERE, ".layout-net.tmp")
    subprocess.run(["kicad-cli", "sch", "export", "netlist",
                    "--format", "kicadsexpr", "-o", out, SCH],
                   check=True, capture_output=True)
    text = open(out).read()
    os.unlink(out)
    comps = {}
    body = text.split("(components")[1].split("(libparts")[0]
    for m in re.finditer(r'\(comp\s+\(ref "([^"]+)"\)(.*?)(?=\(comp\s|\Z)',
                         body, re.S):
        fp = re.search(r'\(footprint "([^"]*)"\)', m.group(2))
        val = re.search(r'\(value "([^"]*)"\)', m.group(2))
        comps[m.group(1)] = (val.group(1) if val else "",
                             fp.group(1) if fp else "")
    nets = {}                   # name -> [(ref, pin)]
    for block in re.split(r'\(net\b', text.split("(nets")[1])[1:]:
        name = re.search(r'\(name "([^"]*)"\)', block).group(1)
        nodes = re.findall(r'\(node\s+\(ref "([^"]+)"\)\s+\(pin "([^"]+)"\)',
                           block, re.S)
        if nodes:
            nets[name] = nodes
    return comps, nets

# ------------------------------------------------- custom footprint builders
def _pth(fp, num, x, y, dia, drill, npth=False):
    pad = pcbnew.PAD(fp)
    pad.SetNumber("" if npth else num)
    pad.SetAttribute(pcbnew.PAD_ATTRIB_NPTH if npth else pcbnew.PAD_ATTRIB_PTH)
    pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
    pad.SetSize(VECTOR2I(NM(dia), NM(dia)))
    pad.SetDrillSize(VECTOR2I(NM(drill), NM(drill)))
    pad.SetLayerSet(pcbnew.LSET.AllCuMask() if not npth
                    else pcbnew.LSET(pcbnew.LSET.AllCuMask()))
    pad.SetPos(VECTOR2I(NM(x), NM(y)))
    fp.Add(pad)
    return pad

def make_slide_switch(board):
    """SS-12D00-class slide SPDT, hand-mill pads: 3x PTH 2.54 pitch,
    pad 2.1 / drill 0.9 (annular 0.6). Bench-confirm flag lives in SPEC."""
    fp = pcbnew.FOOTPRINT(board)
    fp.SetFPID(pcbnew.LIB_ID("coupon", "SW_Slide_SS12D00_HandMill"))
    for i in (1, 2, 3):
        _pth(fp, str(i), 0, (i - 2) * 2.54, 2.1, 0.9)
    box = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_RECT)
    box.SetStart(VECTOR2I(NM(-2.0), NM(-4.5)))
    box.SetEnd(VECTOR2I(NM(2.0), NM(4.5)))
    box.SetLayer(pcbnew.F_Fab)
    box.SetWidth(NM(0.1))
    fp.Add(box)
    return fp

def make_m3(board):
    fp = pcbnew.FOOTPRINT(board)
    fp.SetFPID(pcbnew.LIB_ID("coupon", "MountingHole_3.4mm_NPTH"))
    _pth(fp, "", 0, 0, 3.4, 3.4, npth=True)
    fp.SetAttributes(fp.GetAttributes()
                     | pcbnew.FP_BOARD_ONLY | pcbnew.FP_EXCLUDE_FROM_BOM
                     | pcbnew.FP_EXCLUDE_FROM_POS_FILES)
    return fp

def make_jumper(board):
    """Front-side wire link: two PTH pads, 10.16 mm vertical span,
    pad 2.1 / drill 0.9 (annular 0.6). Board-only."""
    fp = pcbnew.FOOTPRINT(board)
    fp.SetFPID(pcbnew.LIB_ID("coupon", "JumperWire_P10.16mm"))
    _pth(fp, "1", 0, 0, 2.1, 0.9)
    _pth(fp, "2", 0, 10.16, 2.1, 0.9)
    ln = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_SEGMENT)
    ln.SetStart(VECTOR2I(0, 0))
    ln.SetEnd(VECTOR2I(0, NM(10.16)))
    ln.SetLayer(pcbnew.F_Fab)
    ln.SetWidth(NM(0.3))
    fp.Add(ln)
    fp.AddNetTiePadGroup("1,2")
    fp.SetAttributes(fp.GetAttributes()
                     | pcbnew.FP_BOARD_ONLY | pcbnew.FP_EXCLUDE_FROM_BOM
                     | pcbnew.FP_EXCLUDE_FROM_POS_FILES)
    return fp

# ------------------------------------------------------------- placement
# (lib, footprint, x, y, seek_pad, seek_side, flip)  -- board mm, top-left
# origin, +y down.  seek: rotate until that pad lands on that side of the
# footprint center (N/S/E/W quadrant test) AFTER flipping; None = rot 0.
P = {
 "PAD1": ("TestPoint","TestPoint_THTPad_D4.0mm_Drill2.0mm", 4.8,22.5, None,None,False),
 "PAD2": ("TestPoint","TestPoint_THTPad_D4.0mm_Drill2.0mm", 4.8,29.5, None,None,False),
 "SW1":  (None,"SW_Slide_SS12D00_HandMill",                 9.5,28.04,None,None,False),
 "D1":   ("Diode_SMD","D_SMA_Handsoldering",               15.5,28.04,"2","W",True),
 "C1":   ("Capacitor_SMD","C_0805_2012Metric_Pad1.18x1.45mm_HandSolder",19.5,32.3,"1","N",True),
 "L1":   ("Inductor_SMD","L_0805_2012Metric_Pad1.05x1.20mm_HandSolder",21.0,26.2,"1","S",True),
 "C2":   ("Capacitor_SMD","C_0603_1608Metric_Pad1.08x0.95mm_HandSolder",18.0,24.3,"1","N",True),
 "C3":   ("Capacitor_SMD","C_0805_2012Metric_Pad1.18x1.45mm_HandSolder",15.0,24.2,"1","N",True),
 "R1":   ("Resistor_SMD","R_1206_3216Metric_Pad1.30x1.75mm_HandSolder",10.5,16.3,"1","N",True),
 "LED1": ("LED_SMD","LED_0805_2012Metric_Pad1.15x1.40mm_HandSolder",10.5,20.8,"1","S",True),
 "LED2": ("LED_THT","LED_D5.0mm",                          24.0, 5.5,"1","W",False),
 "LED3": ("LED_THT","LED_D5.0mm",                          32.0, 5.5,"1","W",False),
 "Q1":   ("Package_TO_SOT_SMD","SOT-23_Handsoldering",     17.5,10.0,"3","N",True),
 "R5":   ("Resistor_SMD","R_0603_1608Metric_Pad0.98x0.95mm_HandSolder",21.5,11.9,"1","E",True),
 "R6":   ("Resistor_SMD","R_0805_2012Metric_Pad1.20x1.40mm_HandSolder",27.8,9.6,"2","N",True),
 "R7":   ("Resistor_SMD","R_1206_3216Metric_Pad1.30x1.75mm_HandSolder",35.8,9.6,"2","N",True),
 "U1":   ("Package_DIP","DIP-8_W7.62mm_Socket",            26.0,17.5,"1","NW",False),
 "C7":   ("Capacitor_SMD","C_0805_2012Metric_Pad1.18x1.45mm_HandSolder",32.7,14.4,"1","S",True),
 "R2":   ("Resistor_SMD","R_0603_1608Metric_Pad0.98x0.95mm_HandSolder",37.0,14.5,"1","N",True),
 "R3":   ("Resistor_SMD","R_0805_2012Metric_Pad1.20x1.40mm_HandSolder",38.0,20.04,"1","W",True),
 "R4":   ("Resistor_SMD","R_1206_3216Metric_Pad1.30x1.75mm_HandSolder",38.0,23.6,"1","S",True),
 "C4":   ("Capacitor_SMD","C_0805_2012Metric_Pad1.18x1.45mm_HandSolder",40.3,23.62,"1","N",True),
 "C5":   ("Capacitor_SMD","C_1206_3216Metric_Pad1.33x1.80mm_HandSolder",34.3,28.2,"1","N",True),
 "S2":   ("Button_Switch_THT","SW_PUSH_6mm",               42.5,25.7,"1","SEEK270",False),
 "R8":   ("Resistor_SMD","R_0603_1608Metric_Pad0.98x0.95mm_HandSolder",28.7,29.6,"1","N",True),
 "U2":   ("Package_SO","SOIC-8_3.9x4.9mm_P1.27mm",         28.2,33.5,"1","NE",True),
 "R9":   ("Resistor_SMD","R_0603_1608Metric_Pad0.98x0.95mm_HandSolder",23.7,33.7,"1","N",True),
 "R10":  ("Resistor_SMD","R_0805_2012Metric_Pad1.20x1.40mm_HandSolder",22.0,34.6,"1","N",True),
 "C6":   ("Capacitor_SMD","C_0805_2012Metric_Pad1.18x1.45mm_HandSolder",24.9,36.9,"1","W",True),
 "C8":   ("Capacitor_SMD","C_0805_2012Metric_Pad1.18x1.45mm_HandSolder",25.9,30.4,"1","S",True),
 "R11":  ("Resistor_SMD","R_1206_3216Metric_Pad1.30x1.75mm_HandSolder",37.3,36.45,"1","W",True),
 "LED4": ("LED_SMD","LED_0805_2012Metric_Pad1.15x1.40mm_HandSolder",39.6,37.9,"2","N",True),
 "TP1":  ("TestPoint","TestPoint_Pad_D2.0mm",44.9, 9.75,None,None,True),
 "TP2":  ("TestPoint","TestPoint_Pad_D2.0mm",53.1, 9.75,None,None,True),
 "TP3":  ("TestPoint","TestPoint_Pad_D2.0mm",44.9,15.4, None,None,True),
 "TP4":  ("TestPoint","TestPoint_Pad_D2.0mm",53.1,15.4, None,None,True),
 "TP5":  ("TestPoint","TestPoint_Pad_D2.0mm",44.9,20.45,None,None,True),
 "TP6":  ("TestPoint","TestPoint_Pad_D2.0mm",53.1,20.45,None,None,True),
}
# board-only extras: ref -> (kind, x, y, angle_deg)
EXTRA = {
 "H1": ("m3",   4.5, 4.5, 0), "H2": ("m3", 50.5, 4.5, 0),
 "H3": ("m3",   4.5,35.5, 0), "H4": ("m3", 50.5,35.5, 0),
 "JP1": ("jp1016", 42.5,11.24, 0),          # N_DISCH  -> S2 island
 "JP2": ("jp127", 19.3,19.03, "to:21.7,31.5"),          # VCC      -> SW block
 "JP3": ("jp1524", 24.1,9.86, "to:24.0,25.1"),
 "JP6": ("jp1016", 23.3,28.3, "to:21.9,18.24"),
 "JP4": ("jp1524", 29.6,12.7, "to:43.0,5.44"),
 "JP5": ("jp127", 31.5,36.0, "to:19.05,33.49"),   # VCC -> U1.4/U2 south
 "CP1": ("cp","Resistor_SMD","R_1206_3216Metric_Pad1.30x1.75mm_HandSolder",44.7,38.5),
 "CP2": ("cp","Resistor_SMD","R_0805_2012Metric_Pad1.20x1.40mm_HandSolder",49.0,38.5),
 "CP3": ("cp","Resistor_SMD","R_0603_1608Metric_Pad0.98x0.95mm_HandSolder",52.65,38.5),
}

# ---------------------------------------------------------------- routes
# (net, width, [waypoint...]) ; waypoint = (x,y) mm or "REF.PIN" (resolved
# to the placed pad center). Consecutive waypoints become track segments.
ROUTES = [
 ("/VIN",    1.0, ["PAD1.1", (6.5,24.5), (9.5,25.5)]),          # ends in SW1.1
 ("/VIN_SW", 1.0, ["SW1.2", "D1.2"]),
 ("/VF",     1.0, ["D1.1", (19.8,28.04), "C1.1"]),   # C1 col x19.5, slight diag
 ("/VF",     1.0, [(19.8,28.04), (21.0,28.04), "L1.1"]),
 ("VCC", 0.8, ["L1.2", (21.0,23.5)]),
 ("VCC", 0.8, [(8.5,23.5), (21.0,23.5)]),                       # west rail
 ("VCC", 0.8, [(8.5,14.75), (8.5,23.5)]),                       # west riser
 ("VCC", 0.6, [(8.5,14.75), "R1.1"]),
 ("VCC", 0.8, [(25.5,10.25), (35.8,10.25)]),                    # top rail (fed by JP3)
 ("VCC", 0.8, [(34.3,10.25), (34.3,17.0)]),                     # east riser -> U1.8
 ("VCC", 0.6, ["C7.1", (32.7,16.8)]),                           # C7 -> U1.8 pad
 ("VCC", 0.6, [(34.3,13.588), "R2.1"]),
 ("VCC", 0.6, ["JP3.1", (25.5,10.25)]),                         # JP3-A -> top rail
 ("VCC", 0.8, ["JP2.1", (19.3,23.5)]),                          # JP2-A -> rail
 ("VCC", 0.8, ["JP3.2", (25.5,26.05)]),                         # JP3-B -> U1.4 pad
 ("VCC", 0.8, ["JP3.2", "L1.2"]),                               # JP3-B sits on L1.2
 ("VCC", 0.8, [(22.7,31.6), (25.05,31.6)]),                     # JP2-B -> U2.8 pad
 ("VCC", 0.5, ["C8.1", (25.9,31.6)]),                           # C8 on the U2.8 track
 ("VCC", 0.6, [(23.7,31.6), (23.7,32.4)]),                      # spur -> R9.1 pad
 ("/LEDK", 0.8, [(17.5,3.6), (30.98,3.6)]),
 ("/LEDK", 0.8, [(17.5,3.6), "Q1.3"]),
 ("/LEDK", 0.8, [(22.98,3.6), "LED2.1"]),
 ("/LEDK", 0.8, [(30.98,3.6), "LED3.1"]),
 ("/N_Q1B",  0.6, ["Q1.1", "R5.2"]),
 ("/BLINK",  0.6, ["R5.1", (22.41,13.5), (23.7,14.8), (23.7,21.6), (24.8,22.58), "U1.3"]),
 ("/TRIG_THR",0.6, ["U1.2", (28.7,20.04)]),
 ("/TRIG_THR",0.6, [(28.7,20.04), "R8.1"]),                     # drop -> R8
 ("/TRIG_THR",0.6, [(28.7,22.58), (32.6,22.58)]),               # -> U1.6 pad
 ("/TRIG_THR",0.6, ["U1.6", "C4.1"]),                           # east limb y22.58
 ("/TRIG_THR",0.6, ["R3.2", (39.0,22.58)]),
 ("/TRIG_THR",0.6, ["R4.2", (38.0,22.58)]),
 ("/N_DISCH", 0.6, ["U1.7", "R3.1"]),
 ("/N_DISCH", 0.6, ["R2.2", (37.0,19.5)]),                      # drop into R3.1
 ("/N_DISCH", 0.6, ["JP1.1", (38.2,11.24), (38.2,16.6), (37.0,16.6)]),
 ("/N_DISCH", 0.6, ["JP1.2", (42.5,24.6)]),
 ("/N_DISCH", 0.6, [(42.5,25.7), (42.5,32.2)]),
 ("/N_KICK",  0.6, [(38.0,25.7), (38.0,32.2)]),
 ("/N_KICK",  0.6, ["R4.1", (38.0,25.2)]),
 ("/N_CV",    0.6, ["C5.1", (34.3,25.6)]),
 ("/N_LED1",  0.6, ["R1.2", "LED1.2"]),
 ("/N_LED2A", 0.6, ["R6.2", (27.8,7.9), (25.03,7.9), "LED2.2"]),
 ("/N_LED3A", 0.6, ["R7.2", (35.8,7.9), (33.03,7.9), "LED3.2"]),
 ("/N_LED4A", 0.6, ["R11.2", "LED4.2"]),
 ("/N_U2AOUT",0.6, ["U2.1", (35.75,31.595), (35.75,36.0)]),
 ("/N_U2AIN", 0.5, ["R8.2", (28.7,34.135), (29.75,34.135)]),
 ("/N_U2BFB", 0.5, ["U2.6", (27.45,34.135), (27.45,32.865), "U2.7"]),
 ("/VHALF", 0.6, ["R9.2", (23.7,38.35)]),                       # col through C6.1
 ("/VHALF", 0.6, ["R9.2", "R10.1"]),
 ("/VHALF", 0.6, [(23.7,38.35), (33.3,38.35), (33.3,32.865), (31.5,32.865)]),
]
# serpentines: (net, TPa, TPb, width, x0, pitch, nlegs, ytop, ybot, ymid)
SERPS = [
 ("/LADDER_040","TP1","TP2",0.4, 46.2, 0.8, 8,  8.0, 11.5,  9.75),
 ("/LADDER_050","TP3","TP4",0.5, 46.4, 1.0, 6, 13.6, 17.2, 15.4),
 ("/LADDER_060","TP5","TP6",0.6, 46.6, 1.2, 5, 19.0, 21.9, 20.45),
]

# ------------------------------------------------------------------ build
def deg(a):
    return EDA_ANGLE(a, pcbnew.DEGREES_T)

def pad_by_num(fp, num):
    for p in fp.Pads():
        if p.GetNumber() == num:
            return p
    raise KeyError(f"{fp.GetReference()} pad {num}")

def quadrant_ok(fp, padnum, want):
    pads = list(fp.Pads())
    cx = sum(q.GetPosition().x for q in pads) / len(pads)
    cy = sum(q.GetPosition().y for q in pads) / len(pads)
    p = pad_by_num(fp, padnum).GetPosition()
    x, y = p.x - cx, p.y - cy
    if want == "N": return y < 0 and abs(y) > abs(x)
    if want == "S": return y > 0 and abs(y) > abs(x)
    if want == "E": return x > 0 and abs(x) > abs(y)
    if want == "W": return x < 0 and abs(x) > abs(y)
    if want == "NW": return x < 0 and y < 0
    if want == "NE": return x > 0 and y < 0
    if want == "SE": return x > 0 and y > 0
    if want == "1E_COLS":       # S2: both '1' pads east of both '2' pads
        ones = [q.GetPosition().x for q in fp.Pads() if q.GetNumber() == "1"]
        twos = [q.GetPosition().x for q in fp.Pads() if q.GetNumber() == "2"]
        return min(ones) > max(twos)
    raise ValueError(want)

def place(board, ref, lib, name, x, y, seekpad, seekside, flip):
    if lib is None:
        fp = make_slide_switch(board)
    else:
        fp = pcbnew.FootprintLoad(f"{LIBROOT}/{lib}.pretty", name)
        assert fp, f"{lib}/{name}"
    fp.SetReference(ref)
    board.Add(fp)
    fp.SetPosition(MM(x, y))
    if flip:
        fp.Flip(fp.GetPosition(), False)
    if seekpad:
        want = "1E_COLS" if seekside == "SEEK270" else seekside
        for a in (0, 90, 180, 270):
            fp.SetOrientation(deg(a))
            if quadrant_ok(fp, seekpad, want):
                break
        else:
            raise RuntimeError(f"seek failed {ref} {seekpad}->{seekside}")
    return fp

def fixup_tht(fp):
    """THT annular >=0.6 law + SPEC drill overrides."""
    ref = fp.GetReference()
    for pad in fp.Pads():
        if pad.GetAttribute() != pcbnew.PAD_ATTRIB_PTH:
            continue
        if ref == "U1":
            pad.SetShape(pcbnew.PAD_SHAPE_OVAL)
            s = VECTOR2I(NM(2.4), NM(2.0))
            if abs(pad.GetOrientation().AsDegrees()) in (90.0, 270.0):
                s = VECTOR2I(NM(2.0), NM(2.4))
            pad.SetSize(s)
        elif ref in ("LED2", "LED3"):
            pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
            pad.SetSize(VECTOR2I(NM(2.1), NM(2.1)))
        elif ref == "S2":
            pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
            pad.SetSize(VECTOR2I(NM(2.3), NM(2.3)))
        elif ref in ("PAD1", "PAD2"):
            pad.SetDrillSize(VECTOR2I(NM(1.5), NM(1.5)))   # SPEC: 1.5 wire pad
        d = pad.GetDrillSize().x
        s = pad.GetSize(pcbnew.F_Cu)
        ann = (min(s.x, s.y) - d) / 2e6
        assert ann >= 0.599, f"annular {ref} {pad.GetNumber()} = {ann:.3f}"

def add_extras(board, nets):
    fps = {}
    for ref, spec in EXTRA.items():
        if spec[0] == "m3":
            fp = make_m3(board); fp.SetReference(ref); board.Add(fp)
            fp.SetPosition(MM(spec[1], spec[2]))
        elif spec[0].startswith("jp"):
            span = {"jp1016": 10.16, "jp127": 12.7, "jp1524": 15.24}[spec[0]]
            fp = make_jumper(board)
            if span != 10.16:
                pad_by_num(fp, "2").SetPos(VECTOR2I(0, NM(span)))
                for g in fp.GraphicalItems():
                    g.SetEnd(VECTOR2I(0, NM(span)))
                fp.SetFPID(pcbnew.LIB_ID("coupon", f"JumperWire_P{span}mm"))
            fp.SetReference(ref); fp.SetValue(f"wire {span}mm"); board.Add(fp)
            fp.SetPosition(MM(spec[1], spec[2]))
            tgt = spec[3]
            if isinstance(tgt, str) and tgt.startswith("to:"):
                tx, ty = (float(v) for v in tgt[3:].split(","))
                best = (1e18, 0.0)
                for i in range(0, 36000, 5):
                    fp.SetOrientation(deg(i / 100.0))
                    q = pad_by_num(fp, "2").GetPosition() - MM(tx, ty)
                    d2 = q.x * q.x + q.y * q.y
                    if d2 < best[0]:
                        best = (d2, i / 100.0)
                fp.SetOrientation(deg(best[1]))
                assert best[0] ** 0.5 < 5e4, f"{ref} span cannot reach target"
        else:                                   # cp: pad-ladder coupon
            _, lib, name, x, y = spec
            fp = pcbnew.FootprintLoad(f"{LIBROOT}/{lib}.pretty", name)
            fp.SetReference(ref); fp.SetValue("coupon"); board.Add(fp)
            fp.SetPosition(MM(x, y))
            fp.Flip(fp.GetPosition(), False)
            fp.SetAttributes(fp.GetAttributes() | pcbnew.FP_BOARD_ONLY
                             | pcbnew.FP_EXCLUDE_FROM_BOM)
        fps[ref] = fp
    # jumper nets + the physical wire as a netted F.Cu track (front-side
    # bare wire; F.Cu is never exported for milling -- documented exemption
    # to the no-front-tracks rule, needed so connectivity sees the link)
    for ref, net in (("JP1", "/N_DISCH"), ("JP2", "VCC"), ("JP3", "VCC"),
                     ("JP4", "GND"), ("JP5", "GND"), ("JP6", "GND")):
        for pad in fps[ref].Pads():
            pad.SetNet(nets[net])
        if net == "GND":
            for pad in fps[ref].Pads():
                pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
        pts = [pad_by_num(fps[ref], n).GetPosition() for n in ("1", "2")]
        if ref == "JP6":      # modeled dogleg: real wire may run straight if
            pts[1:1] = [MM(22.0, 26.6)]   # insulated, or bent bare per model
        for a, b in zip(pts, pts[1:]):
            w = pcbnew.PCB_TRACK(board)
            w.SetStart(a); w.SetEnd(b); w.SetWidth(NM(0.5))
            w.SetLayer(pcbnew.F_Cu); w.SetNet(nets[net]); board.Add(w)
    return fps

SILK = [   # (text, x, y, height, rot_deg)
 ("+5V",       4.8, 19.2, 1.2, 0), ("GND",  8.0, 32.9, 1.2, 0),
 ("COUPON v1",29.0,  1.6, 1.5, 0), ("2026-07", 11.5, 37.8, 1.5, 0),
 ("SILK 1.2", 10.0, 11.5, 1.2, 0), ("SILK 1.5", 31.9, 38.8, 1.5, 0),
 ("SILK 2.0", 42.4,  2.35, 2.0, 0),
 ("0.4", 44.9, 12.6, 1.0, 0), ("0.5", 44.9, 17.9, 1.0, 0),
 ("0.6", 44.9, 23.2, 1.0, 0),
]
TICKS = [((21.3,4.6),(21.3,6.4)), ((29.3,4.6),(29.3,6.4))]  # LED2/3 K side

DRU = '''(version 1)
(rule "track width floor outside the coupon ladder"
  (condition "!A.intersectsArea('coupon_ladder')")
  (constraint track_width (min 0.5mm)))
(rule "silk clear of scrubbed apertures"
  (constraint silk_clearance (min 0.3mm)))
(rule "copper to board edge"
  (constraint edge_clearance (min 0.4mm)))
'''

def rect_chain(x0, y0, x1, y1):
    ch = pcbnew.SHAPE_LINE_CHAIN()
    for x, y in ((x0,y0),(x1,y0),(x1,y1),(x0,y1)):
        ch.Append(MM(x, y))
    ch.SetClosed(True)
    return ch

def octagon_chain(cx, cy, r):
    import math
    ch = pcbnew.SHAPE_LINE_CHAIN()
    for k in range(8):
        a = math.pi / 8 + k * math.pi / 4
        ch.Append(MM(cx + r * math.cos(a) / math.cos(math.pi / 8),
                     cy + r * math.sin(a) / math.cos(math.pi / 8)))
    ch.SetClosed(True)
    return ch

def add_track(board, a, b, w, net):
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(a); t.SetEnd(b); t.SetWidth(NM(w))
    t.SetLayer(pcbnew.B_Cu); t.SetNet(net); board.Add(t)

def main():
    comps, netnodes = parse_netlist()
    board = pcbnew.CreateEmptyBoard()
    bds = board.GetDesignSettings()
    bds.m_MinClearance = NM(0.4); bds.m_TrackMinWidth = NM(0.4)
    bds.m_HoleToHoleMin = NM(0.5); bds.m_MinThroughDrill = NM(0.3)
    bds.m_CopperEdgeClearance = NM(0.4)
    nets = {}
    for name in netnodes:
        ni = pcbnew.NETINFO_ITEM(board, name); board.Add(ni); nets[name] = ni
    fps = {}
    for ref, (lib, name, x, y, sp, ss, flip) in P.items():
        fps[ref] = place(board, ref, lib, name, x, y, sp, ss, flip)
        if comps.get(ref):
            val, fpid = comps[ref]
            fps[ref].SetValue(val)          # schematic parity: value + FPID
            if ":" in fpid:
                fps[ref].SetFPID(pcbnew.LIB_ID(*fpid.split(":", 1)))
    for ref in ("PAD1","PAD2","SW1","LED2","LED3","U1","S2"):
        fixup_tht(fps[ref])
    for ref in ("TP1","TP2","TP3","TP4","TP5","TP6","PAD1","PAD2"):
        fps[ref].SetAttributes(fps[ref].GetAttributes()
                               | pcbnew.FP_EXCLUDE_FROM_BOM)
    for name, nodes in netnodes.items():
        for ref, pin in nodes:
            for pad in fps[ref].Pads():
                if pad.GetNumber() == pin:
                    pad.SetNet(nets[name])
                    if (name == "GND"
                            and pad.GetAttribute() == pcbnew.PAD_ATTRIB_SMD):
                        # milled board: solid connect SMD GND pads (less
                        # clearing, no starved thermals; THT keep reliefs)
                        pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
    fps.update(add_extras(board, nets))
    # edge
    for a, b in (((0,0),(W,0)), ((W,0),(W,H)), ((W,H),(0,H)), ((0,H),(0,0))):
        e = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
        e.SetStart(MM(*a)); e.SetEnd(MM(*b))
        e.SetLayer(pcbnew.Edge_Cuts); e.SetWidth(NM(0.1)); board.Add(e)
    # rule areas: coupon strip (no pour, named for the .kicad_dru), M3 keepouts
    ra = pcbnew.ZONE(board); ra.SetIsRuleArea(True); ra.SetZoneName("coupon_ladder")
    ra.SetDoNotAllowZoneFills(True); ra.SetDoNotAllowTracks(False)
    ra.SetDoNotAllowVias(True); ra.SetDoNotAllowPads(False)
    ra.SetLayer(pcbnew.B_Cu); ra.Outline().AddOutline(rect_chain(43.3, 6.4, 54.6, 39.6))
    board.Add(ra)
    for cx, cy in ((4.5,4.5),(50.5,4.5),(4.5,35.5),(50.5,35.5)):
        ka = pcbnew.ZONE(board); ka.SetIsRuleArea(True); ka.SetZoneName("m3_keepout")
        ka.SetDoNotAllowZoneFills(True); ka.SetDoNotAllowTracks(True)
        ka.SetDoNotAllowVias(True); ka.SetDoNotAllowPads(False)
        ka.SetLayer(pcbnew.B_Cu); ka.Outline().AddOutline(octagon_chain(cx, cy, 3.3))
        board.Add(ka)
    # routes
    def resolve(wp):
        if isinstance(wp, str):
            ref, pin = wp.split("."); return pad_by_num(fps[ref], pin).GetPosition()
        return MM(*wp)
    for netname, wdt, wps in ROUTES:
        pts = [resolve(w) for w in wps]
        for a, b in zip(pts, pts[1:]):
            add_track(board, a, b, wdt, nets[netname])
    for netname, ta, tb, wdt, x0, pitch, n, ytop, ybot, ymid in SERPS:
        pts = [pad_by_num(fps[ta], "1").GetPosition(), MM(x0, ymid)]
        y = ytop
        for k in range(n):
            x = x0 + k * pitch
            pts.append(MM(x, y)); y = ybot if y == ytop else ytop
            pts.append(MM(x, y))
        pts += [MM(x0 + (n - 1) * pitch, ymid), pad_by_num(fps[tb], "1").GetPosition()]
        # legs run ymid->ytop->ybot..., fix first pair ordering
        pts[2:2] = []
        for a, b in zip(pts, pts[1:]):
            add_track(board, a, b, wdt, nets[netname])
    ring = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_CIRCLE)
    ring.SetCenter(MM(49.7, 27.0)); ring.SetEnd(MM(52.7, 27.0))
    ring.SetWidth(NM(2.0)); ring.SetFilled(False); ring.SetLayer(pcbnew.B_Cu)
    board.Add(ring)
    # silk: board texts + ticks; refs to B.SilkS, values to fab
    for txt, x, y, h, rot in SILK:
        t = pcbnew.PCB_TEXT(board); t.SetText(txt); t.SetPosition(MM(x, y))
        t.SetLayer(pcbnew.B_SilkS); t.SetMirrored(True)
        t.SetTextSize(VECTOR2I(NM(h * 0.9), NM(h))); t.SetTextThickness(NM(h * 0.16))
        t.SetTextAngle(deg(rot)); board.Add(t)
    for a, b in TICKS:
        s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(MM(*a)); s.SetEnd(MM(*b)); s.SetWidth(NM(0.25))
        s.SetLayer(pcbnew.B_SilkS); board.Add(s)
    for ref, fp in fps.items():
        for g in [g for g in fp.GraphicalItems()
                  if g.GetLayer() in (pcbnew.B_SilkS, pcbnew.F_SilkS)]:
            fp.Remove(g)          # silk = earned labels only (laser time)
        r, v = fp.Reference(), fp.Value()
        r.SetLayer(pcbnew.B_Fab); r.SetMirrored(True)   # fab print, not laser silk
        r.SetTextSize(VECTOR2I(NM(0.9), NM(1.0))); r.SetTextThickness(NM(0.16))
        r.SetTextAngle(deg(0))
        bb = fp.GetBoundingBox(False)
        r.SetPosition(VECTOR2I(fp.GetPosition().x, bb.GetTop() - NM(0.9)))
        r.SetVisible(False)   # laser silk carries only the earned labels
        v.SetLayer(pcbnew.B_Fab); v.SetVisible(False)
    # GND pour
    z = pcbnew.ZONE(board); z.SetLayer(pcbnew.B_Cu); z.SetNet(nets["GND"])
    z.SetMinThickness(NM(0.5)); z.SetLocalClearance(NM(0.4))
    z.SetThermalReliefGap(NM(0.5)); z.SetThermalReliefSpokeWidth(NM(0.8))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
    z.Outline().AddOutline(rect_chain(0.6, 0.6, 54.4, 39.58)); board.Add(z)
    open(os.path.join(HERE, "coupon.kicad_dru"), "w").write(DRU)
    # courtyard checks -> warning: this hand-assembled milled board uses
    # deliberate same-net pad butt-joints (R4/S2, JP5/C1, JP5 beside U2's
    # lead-span courtyard); every copper clearance is checked at error level.
    pro = os.path.join(HERE, "coupon.kicad_pro")
    import json as _json
    p = _json.load(open(pro))
    rs = p.setdefault("board", {}).setdefault("design_settings", {}).setdefault("rule_severities", {})
    rs["courtyards_overlap"] = "warning"
    rs["pth_inside_courtyard"] = "warning"
    _json.dump(p, open(pro, "w"), indent=2)
    # fill needs a project-attached board: save, reload, fill, save again
    pcbnew.SaveBoard(BOARD, board)
    board2 = pcbnew.LoadBoard(BOARD)
    pcbnew.ZONE_FILLER(board2).Fill(board2.Zones())
    pcbnew.SaveBoard(BOARD, board2)
    # pad dump for route authoring / audit
    for ref in sorted(fps):
        fp = fps[ref]
        for pad in fp.Pads():
            p = pad.GetPosition()
            print(f"{ref:5s}.{pad.GetNumber() or '-':2s} ({(p.x/1e6-OX):6.3f},"
                  f"{(p.y/1e6-OY):6.3f}) net={pad.GetNetname()}")
    print("saved", BOARD)

if __name__ == "__main__":
    main()
