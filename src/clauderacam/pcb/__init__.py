"""The PCB lane (PCB-PLAN.md): gerbers in, verified Carvera programs out.

boardmaps.py — the verification ground truth: gerbv-rasterized layer
masks + the in-repo Excellon parser. Deliberately independent of the
FlatCAM geometry engine: the generator and the verifier read the same
source files through implementations that share no lineage, exactly
like the STL lane's mesh-vs-gcode split.
"""
