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
Hand-solder rework (Bill 2026-07-31 + guides/pcb-dfm-notes.md):
  hole-centered GND pads get thermal reliefs (gap 0.4, spoke 0.6, 4 @ 45deg);
  SMD GND pads are NEVER solid-connected to the pour -- each gets a routed
  0.6 neck instead (one heat path an iron can beat); teardrops at THT
  pad-track junctions; no drawn copper turn sharper than 90deg; silk text
  >=1.0 (the DFM floor both fab houses agree on); hole = lead + 0.2..0.4
  rounded up to a bore class; mask expansion 0, asserted.
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
    # LSET.AllCuMask() is the bit-mask of all COPPER layers — the name does
    # not mean "Cu + Mask". Shipping it bare left every hand-built PTH pad
    # (JP1-7, SW1) with no solder-mask aperture: 17 pads the scrub phase
    # could never reach (bench-found 2026-07-30, Bill's loupe). Soldered
    # pads open both masks, like every library footprint's "*.Cu" "*.Mask".
    # NPTH (M3 bores) stays copper-only ON PURPOSE: a mask aperture there
    # would send the spring tool across a future bore (the side-2
    # paint-across-bores class) for a hole nothing solders to.
    # The LSET() COPY below is load-bearing: AllCuMask() hands back the
    # shared static itself, and AddLayer on it poisons every later caller
    # IN-PROCESS — measured 2026-07-30: the serializer compares pad sets
    # against that static to choose the "*.Cu" shorthand, so one mutated
    # static silently wrote ALL 35 THT pads (library ones included) as
    # copper-only while the pre-save assert read the live objects and
    # passed. Mutate a copy, never the static.
    lset = pcbnew.LSET(pcbnew.LSET.AllCuMask())
    if not npth:
        lset.AddLayer(pcbnew.F_Mask)
        lset.AddLayer(pcbnew.B_Mask)
    pad.SetLayerSet(lset)
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
    pad 2.1 / drill 0.9 (annular 0.6). The DFM hole rule band for 22 AWG
    wire (0.64) is [0.84, 1.04]; 0.9 sits in-band at the bottom. The 1.0
    class was tried (2026-07-31) and REVERTED: it forces 2.2 pads, which
    shaved three long-standing 0.44 clearances (JP2.2/C1.1, JP2.2/R10,
    JP6.2/BLINK) to 0.38-0.40 -- under the 0.4 law. Board-only."""
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
 "LED2": ("LED_THT","LED_D5.0mm",                          24.0, 6.35,"1","W",False),
 "LED3": ("LED_THT","LED_D5.0mm",                          32.0, 6.35,"1","W",False),
 # y 5.5 -> 6.35 (2026-07-31): 5mm body was 2.25 from the north tab stub;
 # the >=3.0 tab-body law wants the filed-bump zone clear (dfm-notes SS11)
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
 "R10":  ("Resistor_SMD","R_0805_2012Metric_Pad1.20x1.40mm_HandSolder",22.0,34.6,"1","S",True),
 # R10 flipped N->S (2026-07-31): puts its GND pad beside C1.2/JP5.2's GND
 # neighborhood (the W neck lands ON JP5.2 -- the pocket feed) and its
 # VHALF pad on the C6 side for a straight R10.1 -> C6.1 link
 "C6":   ("Capacitor_SMD","C_0805_2012Metric_Pad1.18x1.45mm_HandSolder",22.9,37.45,"1","E",True),
 # C6 (24.9,36.9) -> (22.9,37.45) flipped E (2026-07-31): its body was 2.18
 # from the south tab stub, and at 23.4 its pads physically overlapped
 # R10.2's. Now: VHALF pad (east) links R10.1 and the y37.85 run; GND pad
 # (west) necks into the JP5.2-fed west pocket.
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
 "JP6": ("jp127", 23.3,28.3, "to:21.3,15.76"),
 # JP6 grew 10.16 -> 12.7 (2026-07-31): its old far pad sat in the U1-moat
 # pocket -- a pocket feeding a pocket. Once SMD pads stopped solid-
 # connecting, the whole network JP6 stitches had NO copper path to main
 # GND (every tie dead-ended on an SMD pad). The far pad now lands in the
 # west-central MAIN pour, so JP6.1 becomes the true feed for the C8/U1
 # pocket blob. Wire re-checked: stays west of the U1 socket body, east of
 # JP2's wire (no different-net crossing).
 "JP4": ("jp1524", 29.6,12.7, "to:43.0,5.44"),
 "JP5": ("jp127", 31.5,36.0, "to:19.05,33.49"),   # GND drop, U2-south pockets
 # JP5.1 doubles as the C6/U2 pocket's feed: the y36.5 GND run lands on it
 "JP7": ("jp127", 41.5,35.0, "to:30.9,28.0"),
 # JP7 (2026-07-31): the pocket ringed by TRIG's east limb, N_KICK, S2's
 # pad moat and N_U2AOUT (under/east of U1) has no pour channel out --
 # S2.2's moat meets N_U2AOUT's cap exactly. JP7 bridges it to the SE
 # island (itself merged with main via the south strip). Far pad clears
 # the U1 socket lip by 1.25; wire doglegs around S2.2's front annulus.
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
 ("/VF",     1.0, ["D1.1", (19.5,28.04), "C1.1"]),   # x = C1 col: square corner
 ("/VF",     1.0, [(19.5,28.04), (21.0,28.04), "L1.1"]),
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
 ("/N_LED2A", 0.6, ["R6.2", (27.8,7.9), (26.54,7.9), "LED2.2"]),   # x = LED2.2
 ("/N_LED3A", 0.6, ["R7.2", (35.8,7.9), (34.54,7.9), "LED3.2"]),   # x = LED3.2
 # (the old 25.03/33.03 waypoints turned into the pad at an acute angle --
 #  a free-copper wedge, refused by assert_route_angles since 2026-07-31)
 ("/N_LED4A", 0.6, ["R11.2", "LED4.2"]),
 ("/N_U2AOUT",0.6, ["U2.1", (35.75,31.595), (35.75,36.0)]),
 ("/N_U2AIN", 0.5, ["R8.2", (28.7,34.135), (29.75,34.135)]),
 ("/N_U2BFB", 0.5, ["U2.6", (27.45,34.135), (27.45,32.865), "U2.7"]),
 ("/VHALF", 0.6, ["R9.2", "R10.1"]),
 ("/VHALF", 0.6, ["R10.1", (23.94,36.0), "C6.1"]),      # jog E then drop
 # (the straight diagonal passed 0.205 from C6.2's corner)
 ("/VHALF", 0.6, ["C6.1", (25.0,37.45), (25.0,37.85)]),
 ("/VHALF", 0.6, [(25.0,37.85), (33.55,37.85), (33.55,32.87), (31.4,32.87)]),
 # run at y37.85: leaves a 1.43 pour channel to the south edge (a run at
 # y38.95 was tried and SEVERED the south strip -- the SE island lost its
 # only bridge to main); col at x33.55 clears JP5.1 by 0.55 and passes
 # under it at 0.5
 # the C6/U2 pocket lost its old solid-pad stitching when SMD GND went to
 # necks -- bond U2.5/U2.4 by routed GND landing on JP5.1 (the pocket feed):
 ("GND", 0.6, ["U2.5", (25.72,36.5), (30.68,36.5), "U2.4"]),
 ("GND", 0.6, [(30.68,36.5), (31.5,36.0)]),             # stitch onto JP5.1
 # JP6.1 feeds the pockets on BOTH its sides; spokes alone left the small
 # NW islet "isolated" per DRC, so the feeds are explicit routed stubs:
 ("GND", 0.6, ["JP6.1", (21.6,29.4)]),                  # into the W pocket
 ("GND", 0.6, ["JP6.1", (22.5,26.8)]),                  # into the NW islet
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
    """THT annular >=0.6 law + SPEC drill overrides + the DFM hole rule
    (hole = lead + 0.2..0.4, rounded up toward lead + 0.3, bore classes only;
    guides/pcb-dfm-notes.md). Lead dimensions are datasheet-class and each
    THT line keeps its bench-confirm flag in SPEC.md:
      U1 socket pin ~0.52 round  -> band [0.72,0.92] -> 0.9 (was the stock
        0.8 -- which the 0.8 corn could only PLUNGE, never orbit)
      LED2/3 lead 0.45 sq (diag 0.64) -> band [0.84,1.04] -> 0.9 kept: the
        1.0 class forces a 2.2 pad and 2.54-pitch neighbors to 0.34 < the
        0.4 clearance law. 0.9 sits in-band; bench-confirm the leads.
      S2 leg 0.7x0.3 (diag 0.76) -> band [0.96,1.16] -> 1.1
      SW1 SS-12D00 pin: THIN variant 0.5x0.3 (diag 0.58) -> 0.9. The FAT
        0.8x0.4 variant (diag 0.89) would need 1.1+ and CANNOT hold both
        the 0.6 annular and 0.4 clearance laws at 2.54 pitch -- if the bench
        finds fat pins this footprint gets a staggered rework, not a bigger
        drill. Flagged in SPEC.
      PAD1/2: SPEC law, 1.5 drill."""
    ref = fp.GetReference()
    for pad in fp.Pads():
        if pad.GetAttribute() != pcbnew.PAD_ATTRIB_PTH:
            continue
        if ref == "U1":
            pad.SetShape(pcbnew.PAD_SHAPE_OVAL)
            s = VECTOR2I(NM(2.4), NM(2.1))
            if abs(pad.GetOrientation().AsDegrees()) in (90.0, 270.0):
                s = VECTOR2I(NM(2.1), NM(2.4))
            pad.SetSize(s)
            pad.SetDrillSize(VECTOR2I(NM(0.9), NM(0.9)))
        elif ref in ("LED2", "LED3"):
            pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
            pad.SetSize(VECTOR2I(NM(2.1), NM(2.1)))
            pad.SetDrillSize(VECTOR2I(NM(0.9), NM(0.9)))
        elif ref == "S2":
            pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
            pad.SetSize(VECTOR2I(NM(2.3), NM(2.3)))
            pad.SetDrillSize(VECTOR2I(NM(1.1), NM(1.1)))
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
                     ("JP4", "GND"), ("JP5", "GND"), ("JP6", "GND"),
                     ("JP7", "GND")):
        for pad in fps[ref].Pads():
            pad.SetNet(nets[net])
        if net == "GND":
            # hole-centered GND pads take the zone's thermal relief like
            # every other PTH pad (Bill 2026-07-31 -- the old FULL connect
            # here was the same heat-sink mistake as the SMD pads').
            # JP6.1 runs its spokes at 90: it feeds pockets on BOTH sides
            # (I3 east, I5 west) and the 45deg set only reached one.
            for pad in fps[ref].Pads():
                pad.SetThermalSpokeAngleDegrees(
                    90.0 if ref == "JP6" and pad.GetNumber() == "1"
                    else 45.0)
        pts = [pad_by_num(fps[ref], n).GetPosition() for n in ("1", "2")]
        # modeled doglegs: the F.Cu wire model must not cross a different
        # net's front copper (pads or other wires); the real wire may run
        # straight if insulated, or bent bare per the model
        dog = {"JP6": (21.8, 24.0), "JP7": (37.3, 34.3)}.get(ref)
        if dog:
            pts[1:1] = [MM(*dog)]
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
 ("SILK 1.0", 12.5, 36.0, 1.0, 0),   # DFM ladder rung AT both fab floors --
                                     # without it the ladder can't answer
                                     # whether 1.0 is readable (dfm-notes SS9)
 ("0.4", 44.9, 12.6, 1.0, 0), ("0.5", 44.9, 17.9, 1.0, 0),
 ("0.6", 44.9, 23.2, 1.0, 0),
]
TICKS = [((21.3,5.45),(21.3,7.25)), ((29.3,5.45),(29.3,7.25))]  # LED2/3 K side

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

def add_scrub_ring(board, layer):
    """SPEC.md's scrub-margin gauge: a 2.0 mm band annulus, OD 8 (centreline
    r 3.0), drawn from ONE definition so the copper ring and the mask aperture
    over it cannot drift apart.

    The B.Mask copy is what makes the gauge a gauge. The scrub phase paints the
    MASK apertures (deflated by the job's paint offset), so a B.Cu graphic with
    no aperture over it never gets lapped: the first live FlatCAM run measured
    this ring's closest scrub sample at 6.466 mm from a band that ends at 4.0,
    i.e. the gauge read identically on a perfect and a botched run (DESIGN.md
    2026-07-30, the gauge the process cannot touch)."""
    ring = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_CIRCLE)
    ring.SetCenter(MM(49.7, 27.0)); ring.SetEnd(MM(52.7, 27.0))
    ring.SetWidth(NM(2.0)); ring.SetFilled(False); ring.SetLayer(layer)
    board.Add(ring)

def add_track(board, a, b, w, net):
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(a); t.SetEnd(b); t.SetWidth(NM(w))
    t.SetLayer(pcbnew.B_Cu); t.SetNet(net); board.Add(t)

NECK_DIR = {                       # overrides where the outward default collides
    # (ux, uy) or (ux, uy, embed) -- embed defaults to 1.2; None = hand-routed
    ("LED1", "1"): (1, 0),         # S would kiss the VCC west rail, W the riser
    ("LED4", "1"): (1, 0),         # S runs past the pour edge toward Edge.Cuts
    ("R10", "2"): (-1, 0),         # W lands ON JP5.2's pad -- the pocket feed
    ("C2", "2"): (1, 0, 1.05),     # S ended 0.0025 from D1.1's big SMA pad;
                                   # E shortened 0.15 to clear L1.2 by 0.53
    ("C3", "2"): (-1, 0),          # S bridged toward D1.2's aperture
    ("C5", "2"): (1, 0),           # S crossed the N_U2AOUT horizontal run
    ("C6", "2"): (-1, 0),          # W into the JP5.2-fed pocket (C6 flipped E)
    ("U2", "4"): None,             # hand-routed onto JP5.1
    ("U2", "5"): None,             # hand-routed via the y36.5 run
}

def add_gnd_necks(board, gnd_smd, fps, gnd):
    """One routed 0.6 mm neck per SMD GND pad (Bill 2026-07-31, DFM SS1).

    The pad itself is ZONE_CONNECTION_NONE, so the pour pulls back 0.4 all
    around; the neck is a plain GND track from the pad center outward --
    tracks are always solid-embedded by the fill, so the result is exactly
    one 0.6 heat path: 0.4 of moat crossing plus >=0.8 embedded in pour.
    Direction defaults to "away from the footprint center along the dominant
    axis" (clears the body and its second pad); NECK_DIR overrides the two
    pads where that default lands on foreign copper."""
    for ref, pad in gnd_smd:
        fp = fps[ref]
        p, c = pad.GetPosition(), fp.GetPosition()
        dx, dy = p.x - c.x, p.y - c.y
        embed = 1.2                          # 0.4 moat + 0.8 embedded
        if (ref, pad.GetNumber()) in NECK_DIR:
            d = NECK_DIR[(ref, pad.GetNumber())]
            if d is None:
                continue           # this pad's neck is a hand-drawn route
            if len(d) == 3:
                ux, uy, embed = d
            else:
                ux, uy = d
        elif abs(dx) >= abs(dy):
            ux, uy = (1 if dx >= 0 else -1), 0
        else:
            ux, uy = 0, (1 if dy >= 0 else -1)
        s = pad.GetSize(pcbnew.B_Cu)
        half = (s.x if ux else s.y) / 2e6
        L = half + embed
        end = VECTOR2I(p.x + NM(L) * ux, p.y + NM(L) * uy)
        add_track(board, p, end, 0.6, gnd)

def add_teardrops(board, fps):
    """Teardrops at THT pad-track junctions (DFM SS12: adopted for unplated
    pads, whose only anchor is the laminate's adhesive). Drawn as two flank
    tracks from a point on the arriving track to the pad interior -- length
    <= 1.0x pad radius beyond the pad edge, per the adopted rule. Same-net
    wedges against a convex pad make no acute FREE-copper angle, so the
    >=90deg drawn-copper assertion does not apply to them (stated scope)."""
    pth = []
    for fp in fps.values():
        for pad in fp.Pads():
            if (pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH
                    and pad.GetNetname() and pad.GetSize(pcbnew.B_Cu).x >= NM(1.8)):
                pth.append(pad)
    added = 0
    for t in list(board.Tracks()):
        if t.GetLayer() != pcbnew.B_Cu:
            continue
        for pad in pth:
            c = pad.GetPosition()
            r = pad.GetSize(pcbnew.B_Cu).x / 2e6
            for eend, other in ((t.GetStart(), t.GetEnd()),
                                (t.GetEnd(), t.GetStart())):
                v = eend - c
                if (v.x * v.x + v.y * v.y) > NM(r * 0.4) ** 2:
                    continue          # this end is not on the pad center
                seg = other - eend
                slen = (seg.x * seg.x + seg.y * seg.y) ** 0.5 / 1e6
                need = r + min(r, 1.5 * t.GetWidth() / 1e6)
                if slen <= need + 0.1:
                    continue          # too short to root a teardrop in
                ax, ay = seg.x / (slen * 1e6), seg.y / (slen * 1e6)
                root = VECTOR2I(int(c.x + ax * NM(need)),
                                int(c.y + ay * NM(need)))
                for sgn in (1, -1):
                    flank = VECTOR2I(int(c.x - sgn * ay * NM(r * 0.55)),
                                     int(c.y + sgn * ax * NM(r * 0.55)))
                    tt = pcbnew.PCB_TRACK(board)
                    tt.SetStart(root); tt.SetEnd(flank)
                    tt.SetWidth(t.GetWidth()); tt.SetLayer(pcbnew.B_Cu)
                    tt.SetNet(t.GetNet()); board.Add(tt)
                added += 1
    return added

def assert_route_angles(fps):
    """No drawn copper turn sharper than 90deg (DFM SS8: the acid-trap rule
    re-derived for kerf -- an acute wedge between two cuts leaves a standing
    sliver of length 0.210/tan(theta)). Collinear reversals (the serpentine
    lead-ins) form no wedge and are allowed. Runs on the ROUTE/SERP data
    before anything is built, so a violation stops the build by name."""
    import math
    def resolve(wp):
        if isinstance(wp, str):
            ref, pin = wp.split(".")
            p = pad_by_num(fps[ref], pin).GetPosition()
            return (p.x / 1e6, p.y / 1e6)
        return (OX + wp[0], OY + wp[1])
    def audit(name, pts):
        for a, b, c in zip(pts, pts[1:], pts[2:]):
            v1 = (b[0] - a[0], b[1] - a[1]); v2 = (c[0] - b[0], c[1] - b[1])
            n1 = math.hypot(*v1); n2 = math.hypot(*v2)
            if n1 < 1e-9 or n2 < 1e-9:
                continue
            cosv = max(-1.0, min(1.0, (v1[0]*v2[0] + v1[1]*v2[1]) / (n1*n2)))
            turn = math.degrees(math.acos(cosv))
            assert turn <= 90.5 or turn >= 179.5, \
                f"acute copper wedge: {name} turns {turn:.1f}deg at {b}"
    for netname, _, wps in ROUTES:
        audit(netname, [resolve(w) for w in wps])
    for netname, ta, tb, wdt, x0, pitch, n, ytop, ybot, ymid in SERPS:
        pts = [(x0 + OX, ymid + OY)]
        y = ytop
        for k in range(n):
            x = x0 + k * pitch
            pts.append((x + OX, y + OY)); y = ybot if y == ytop else ytop
            pts.append((x + OX, y + OY))
        audit(netname, pts)

TAB_MIDPOINTS = ((27.5, 0.0), (27.5, 40.0), (0.0, 20.0), (55.0, 20.0))

def assert_tab_bodies(fps):
    """Part body >= 3.0 mm from every cutout tab (DFM SS11, adapted: JLCPCB
    keeps parts 3-5 mm off separation lines because THEIR flow snaps
    assembled panels. This lane snaps the bare board before any part
    exists, so the adapted hazard is the tab STUB -- an unfiled bump a
    nearby part body would rock on. Bodiless copper features (wire pads,
    test points, the pad ladder's empty footprints at ladder distance)
    have nothing to rock and are exempt by name-class).
    FlatCAM's `geocutout -gaps 4` puts one tab at each edge midpoint."""
    for ref, fp in fps.items():
        if ref.startswith(("TP", "PAD")):
            continue                  # bare copper, no body to rock
        bb = fp.GetCourtyard(pcbnew.B_CrtYd)
        if bb.OutlineCount() == 0:
            bb = fp.GetCourtyard(pcbnew.F_CrtYd)
        if bb.OutlineCount() == 0:
            continue                  # jumpers/M3: no body to protect
        box = bb.BBox()
        for tx, ty in TAB_MIDPOINTS:
            t = MM(tx, ty)
            dx = max(box.GetLeft() - t.x, 0, t.x - box.GetRight()) / 1e6
            dy = max(box.GetTop() - t.y, 0, t.y - box.GetBottom()) / 1e6
            d = (dx * dx + dy * dy) ** 0.5
            assert d >= 3.0, f"{ref} body {d:.2f}mm from tab at ({tx},{ty})"

def assert_masks_and_pads(board):
    """Mask expansion 0 asserted (DFM SS9 INVERTS the fab +0.05..0.10: the
    scrub tool needs the aperture to BE the pad -- expansion eats the 0.05
    plateau bar), and every solderable pad >= 0.70 narrow dimension (DFM SS9:
    below 2*(scrub_r + window) + 2*deflate = 0.70 the spring tool cannot lap
    it and it ships under mask)."""
    assert board.GetDesignSettings().m_SolderMaskExpansion == 0
    smallest = (9e9, "")
    for fp in board.Footprints():
        assert fp.GetLocalSolderMaskMargin() in (None, 0), fp.GetReference()
        for pad in fp.Pads():
            assert pad.GetLocalSolderMaskMargin() in (None, 0)
            if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                continue
            # Every solderable pad on this single-sided board MUST open
            # B.Mask. The old form of this loop SKIPPED pads that weren't
            # on B.Mask — which is exactly the defect it existed to refuse:
            # 17 mask-blind PTH pads (JP1-7, SW1) sailed through the skip
            # and shipped under solder mask (bench-found 2026-07-30).
            assert pad.IsOnLayer(pcbnew.B_Mask), \
                f"mask-blind pad {fp.GetReference()}.{pad.GetNumber()}: " \
                f"solderable but opens no B.Mask aperture"
            s = pad.GetSize(pcbnew.B_Cu)
            narrow = min(s.x, s.y) / 1e6
            if narrow < smallest[0]:
                smallest = (narrow, f"{fp.GetReference()}.{pad.GetNumber()}")
            assert narrow >= 0.70, \
                f"unscrubbable pad {fp.GetReference()}.{pad.GetNumber()} " \
                f"{narrow:.2f} < 0.70"
    return smallest

def main():
    comps, netnodes = parse_netlist()
    board = pcbnew.CreateEmptyBoard()
    bds = board.GetDesignSettings()
    bds.m_MinClearance = NM(0.4); bds.m_TrackMinWidth = NM(0.4)
    bds.m_HoleToHoleMin = NM(0.5); bds.m_MinThroughDrill = NM(0.3)
    bds.m_CopperEdgeClearance = NM(0.4)
    bds.m_HoleClearance = NM(0.4)        # DRC/SPEC agreement (dfm-notes SS5)
    bds.m_MinSilkTextHeight = NM(1.0)    # both fab houses' floor (SS9)
    bds.m_SolderMaskExpansion = 0        # asserted below: aperture == pad
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
    # U2 pad surgery: the stock SOIC-8 pad is 0.60 wide -- exactly the
    # marginal paint-region width the scrubbability floor (0.70) exists to
    # refuse (FlatCAM skips marginal regions silently; the ncc-standard and
    # clear-opening incidents are the same failure class). 0.80 wide leaves
    # a 0.47 gap at 1.27 pitch, inside the 0.4 clearance law, and is the
    # friendlier hand-solder target anyway (dfm-notes SS9).
    for pad in fps["U2"].Pads():
        s = pad.GetSize(pcbnew.B_Cu)
        lo, hi = sorted((s.x, s.y))
        pad.SetSize(VECTOR2I(NM(0.8), hi) if s.x == lo
                    else VECTOR2I(hi, NM(0.8)))
    for ref in ("TP1","TP2","TP3","TP4","TP5","TP6","PAD1","PAD2"):
        fps[ref].SetAttributes(fps[ref].GetAttributes()
                               | pcbnew.FP_EXCLUDE_FROM_BOM)
    gnd_smd = []                # (ref, pad) -> gets a routed neck, never FULL
    for name, nodes in netnodes.items():
        for ref, pin in nodes:
            for pad in fps[ref].Pads():
                if pad.GetNumber() == pin:
                    pad.SetNet(nets[name])
                    if (name == "GND"
                            and pad.GetAttribute() == pcbnew.PAD_ATTRIB_SMD):
                        # Bill 2026-07-31: solid pour contact heat-sinks the
                        # iron. Each SMD GND pad gets ZONE_CONNECTION_NONE +
                        # ONE routed 0.6 neck (add_gnd_necks) -- a single
                        # heat path hand soldering can beat. The clearing
                        # argument the old FULL connect made is void: the
                        # 0.8 corn never enters a 0.4 relief moat anyway.
                        pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_NONE)
                        gnd_smd.append((ref, pad))
                    elif (name == "GND"
                            and pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH):
                        # hole-centered pads: 4 spokes at 45deg (DFM SS1 --
                        # spokes miss the radial track corridors more often).
                        # U1.1 is the exception: the socket corner pin's 45deg
                        # diagonals die in the BLINK-column moat; 90deg finds
                        # the open pour north and the inter-column channel
                        pad.SetThermalSpokeAngleDegrees(
                            90.0 if ref == "U1" else 45.0)
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
    # M3 keep-out radius 3.3 >= 3.2 = M3 head/2 + clearance (dfm-notes SS6)
    assert 3.3 >= 3.2
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
    add_scrub_ring(board, pcbnew.B_Cu)      # its B.Mask twin goes on post-fill
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
    # GND necks + teardrops BEFORE the pour so the fill embeds them
    add_gnd_necks(board, gnd_smd, fps, nets["GND"])
    td = add_teardrops(board, fps)
    print(f"teardrops at {td} THT junctions")
    # GND pour. Thermal gap 0.4 == the board clearance law: ONE moat width
    # everywhere, so the iso pass ladder treats relief rings identically to
    # every other gap (both 0.4 and 0.5 are sliver-safe under 5-pass iso;
    # uniformity is the reason, dfm-notes SS1). Spoke 0.6 drawn -> >=0.52
    # delivered after the 0.08 kerf overcut, vs the 0.40 floor.
    z = pcbnew.ZONE(board); z.SetLayer(pcbnew.B_Cu); z.SetNet(nets["GND"])
    z.SetMinThickness(NM(0.5)); z.SetLocalClearance(NM(0.4))
    z.SetThermalReliefGap(NM(0.4)); z.SetThermalReliefSpokeWidth(NM(0.6))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
    z.Outline().AddOutline(rect_chain(0.6, 0.6, 54.4, 39.58)); board.Add(z)
    # build-time DFM assertions (dfm-notes SS8/SS11/SS9)
    assert_route_angles(fps)
    assert_tab_bodies(fps)
    smallest = assert_masks_and_pads(board)
    print(f"smallest solderable pad {smallest[0]:.3f} at {smallest[1]}")
    open(os.path.join(HERE, "coupon.kicad_dru"), "w").write(DRU)
    # fill needs a project-attached board: save, reload, fill, save again
    pcbnew.SaveBoard(BOARD, board)
    board2 = pcbnew.LoadBoard(BOARD)
    pcbnew.ZONE_FILLER(board2).Fill(board2.Zones())
    # the gauge's mask aperture goes on AFTER the pour, deliberately: a mask
    # graphic reaches no copper, but adding it before ZONE_FILLER drops one
    # COLLINEAR vertex from one GND island's outline (measured 2026-07-30:
    # the vertex sits 0.000mm off its own chord, the island's area is equal to
    # the bit, and the copper gerber's other 675 ops are untouched). Post-fill
    # the copper bytes cannot move at all, which is the honest form of "this
    # change reaches no copper" -- the mask layer does not get to rewrite B.Cu.
    add_scrub_ring(board2, pcbnew.B_Mask)
    pcbnew.SaveBoard(BOARD, board2)
    # courtyard checks -> warning: this hand-assembled milled board uses
    # deliberate same-net pad butt-joints (R4/S2, JP5/C1, JP5 beside U2's
    # lead-span courtyard); every copper clearance is checked at error level.
    # This patch must be the LAST write: SaveBoard re-serializes the project
    # file from the board's own settings, so a patch written before it is
    # silently reverted and a fresh build hands the DRC gate 7 courtyard-class
    # ERRORS (found 2026-07-30 re-running this script for the scrub-ring fix).
    pro = os.path.join(HERE, "coupon.kicad_pro")
    import json as _json
    p = _json.load(open(pro))
    rs = p.setdefault("board", {}).setdefault("design_settings", {}).setdefault("rule_severities", {})
    rs["courtyards_overlap"] = "warning"
    rs["pth_inside_courtyard"] = "warning"
    # trailing newline: KiCad's own serializer writes one, so keeping it makes
    # this rewrite a two-value diff instead of a whole-file one
    open(pro, "w").write(_json.dumps(p, indent=2) + "\n")
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
