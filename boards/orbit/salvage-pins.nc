(clauderacam job: orbit-back)
(program E of 6 - pins [side BACK]: pcb-pinspot + pcb-pindrill)
(before: same setup, still; fit the pin drill - the registration holes are the LAST thing cut in setup 1)
(tools: T1 flat d3.175 S12000 | T9 drill d2 S3000)
(1 M6 tool-change pause inside: T1 then T9 - the spindle stops before each change)
(floors: pcb-pinspot Z-0.1 | pcb-pindrill Z-1.7)
(after: program F excise - the sub-blank perimeter, no operator step between)
G90 G94
G17
G21
G54
M05
M6 T1
M3 S12000
G4 P2
(begin operation: pcb-pinspot T1 flat d3.175)
G0 Z3.000
G0 X33.000 Y-3.600
G0 Z0.500
G1 Z-0.100 F60
G0 Z3.000
G0 Z3.000
G0 X33.000 Y59.600
G0 Z0.500
G1 Z-0.100 F60
G0 Z3.000
(finish operation: pcb-pinspot)
M05
M6 T9
M3 S3000
G4 P2
(begin operation: pcb-pindrill T9 drill d2.0)
G0 Z3.000
G0 X33.000 Y-3.600
G1 Z-0.800 F100
G0 Z3.000
G0 Z-0.300
G1 Z-1.600 F100
G0 Z3.000
G0 Z-1.100
G1 Z-1.700 F100
G0 Z3.000
G0 Z3.000
G0 X33.000 Y59.600
G1 Z-0.800 F100
G0 Z3.000
G0 Z-0.300
G1 Z-1.600 F100
G0 Z3.000
G0 Z-1.100
G1 Z-1.700 F100
G0 Z3.000
(finish operation: pcb-pindrill)
(begin postamble)
M05
G17 G90
G28
M30
