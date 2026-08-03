(clauderacam laser: testfire-ladder-s050)
(silk legend: dose S0.05 F100; cures white mask, wipe uncured with IPA)
(TEST FIRE rung 7 of 8: dose S0.05 at F100, y = 19.50)
(one stroke over copper, one over the cleared window. The tick beside this y names the rung after the IPA wipe)
(run the 8 rungs back to back - M321 is a no-op once laser mode is on. M322 exits when you are done)
G90 G94
G17
G21
G54
M321
G0 Z0
(focus law: Z0 = focal plane after M321)
M3 S0.05
G0 X2.000 Y19.500
G1 X16.000 Y19.500 F100
G0 X27.000 Y19.500
G1 X41.000 Y19.500 F100
M5
M30
