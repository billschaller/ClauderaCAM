"""NEGATIVE CONTROL for the unplated-hole gate (Article III).

A gate nobody has watched fail is a gate nobody should trust. The 2026-07-28
review made that law: `tests/negative_suite.py` exists because an always-PASS
verifier clears every positive test in the project.

This is the same idea for orbit's connectivity claim. It solders a FANTASY
onto a scratch copy of the routed board -- an F.Cu stub track running out of
SW1's centre blade, the one lead an iron provably cannot reach, because the
slide switch's body sits flat on top of that ring -- and then demands TWO
things:

  1. the UNPLATED gate CATCHES it: with SW1's front ring deleted (it is
     BACK_ONLY), the stub is copper that connects to nothing, so kicad-cli
     drc exits nonzero and the unconnected count RISES. Not merely "the gate
     was already failing" -- strictly more open items than the same board
     without the stub, so the control discriminates.
  2. stock KiCad, judging the SAME stub with its PLATED-barrel assumption,
     MISSES it: the count does not move, because KiCad believes the front
     ring reaches the back ring through a barrel this process never drills.

(2) is the point of the whole exercise. It is the measurement that says the
unplated model is not paranoia: without it the tools would sign off on a
connection that is a hole and some air.

Run standalone, or as part of every build -- tools-layout.py's main() calls
it through negative_control() and turns a nonzero exit into a gate failure.

    python3 tools-unplated-negative.py     # 0 = the gate has teeth
"""
import importlib.util
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))

spec = importlib.util.spec_from_file_location(
    "orbit_layout", os.path.join(HERE, "tools-layout.py"))
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)                       # constants + gate only

# SW1 pin 1 is the west blade (/VSW, out to Q1's drain). Its ring is
# BACK_ONLY -- the slide switch's body lies flat on it -- and, unlike pin 2,
# nothing has been routed onto its front ring, so the stub is a NEW island
# and the control measures the stub instead of re-measuring copper the
# router already put there. (Pin 2 was tried first and moved nothing: /VBAT
# is ALREADY routed across the front through SW1.2, which is precisely the
# fantasy joint this build exists to expose.)
STUB_REF, STUB_PAD = "SW1", "1"
# 1.2 mm, toward the ring. Long enough to be unmistakable copper, short
# enough to touch nothing else: measured 0 DRC violations under BOTH models,
# so the only thing this control can move is the connectivity count.
STUB_LEN = 1.2


def add_front_stub(board):
    """Solder the fantasy: a short F.Cu track leaving a SW1 blade's FRONT
    ring on the pad's own net -- exactly what an autorouter that believes in
    plated barrels draws, and exactly what an iron can never reach."""
    fp = {f.GetReference(): f for f in board.Footprints()}[STUB_REF]
    pad = L.pad_by_num(fp, STUB_PAD)
    assert pad.IsOnLayer(pcbnew.F_Cu), \
        f"{STUB_REF}.{STUB_PAD} has no front ring on the real board -- the " \
        "control cannot stub a pad that is not there"
    assert L.unplated_class(fp, pad) == "back", \
        f"{STUB_REF}.{STUB_PAD} is no longer BACK_ONLY; this control only " \
        "means something on a lead an iron cannot reach"
    t = pcbnew.PCB_TRACK(board)
    t.SetLayer(pcbnew.F_Cu)
    t.SetWidth(L.NM(L.TRACK_SIG))
    t.SetStart(pad.GetPosition())
    t.SetEnd(pad.GetPosition() - pcbnew.VECTOR2I(0, L.NM(STUB_LEN)))
    t.SetNet(pad.GetNet())
    board.Add(t)


def plated(mutate=None, label="plated"):
    """kicad drc on a scratch copy with NO front ring removed: stock KiCad's
    barrel-believing verdict, the one this whole exercise distrusts."""
    import shutil
    import tempfile
    d = tempfile.mkdtemp(prefix=f"orbit-{label}-")
    board = pcbnew.LoadBoard(L.BOARD)
    if mutate:
        mutate(board)
    tmp = os.path.join(d, "orbit.kicad_pcb")
    pcbnew.SaveBoard(tmp, board)
    for ext in (".kicad_pro", ".kicad_dru"):
        src = os.path.join(HERE, "orbit" + ext)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(d, "orbit" + ext))
    return L._kicad_drc(tmp, label)[:3]              # rc, violations, open


def main():
    # The dual-solder list is computed ONCE, on the clean board, and then
    # FORCED into the stubbed run. A control that let the greedy re-solve
    # would be measuring two different models, not one model's reaction.
    clean_rc, clean_open, clean_vio, dual = L.unplated_drc(label="neg-clean")
    stub_rc, stub_open, stub_vio, _ = L.unplated_drc(
        dual=dual, mutate=add_front_stub, label="neg-stub")
    p_clean_rc, p_clean_vio, p_clean_open = plated(label="neg-plated-clean")
    p_stub_rc, p_stub_vio, p_stub_open = plated(add_front_stub,
                                                "neg-plated-stub")

    print(f"unplated clean : exit {clean_rc}, {clean_open} open")
    print(f"unplated + stub: exit {stub_rc}, {stub_open} open")
    print(f"PLATED   clean : exit {p_clean_rc}, {p_clean_open} open")
    print(f"PLATED   + stub: exit {p_stub_rc}, {p_stub_open} open")
    ok = True
    if stub_rc == 0:
        print("FAIL: the unplated gate exited 0 on a fantasy front joint")
        ok = False
    if stub_open <= clean_open:
        print(f"FAIL: the stub did not raise the unplated open count "
              f"({clean_open} -> {stub_open}); the gate does not discriminate,"
              " it is merely already failing")
        ok = False
    if (stub_vio, p_stub_vio) != (clean_vio, p_clean_vio):
        # If the stub also trips a geometric rule, the open-count comparison
        # is no longer isolating connectivity and the control is measuring
        # two things at once. Shorten STUB_LEN or re-aim it.
        print(f"FAIL: the stub changed the violation count too "
              f"(unplated {clean_vio}->{stub_vio}, plated {p_clean_vio}->"
              f"{p_stub_vio}); this control must move connectivity ONLY")
        ok = False
    if p_stub_open != p_clean_open:
        # Not a failure of THIS gate, but the demonstration is gone and the
        # reviewer must be told, not quietly reassured.
        print(f"FAIL: the plated model also moved ({p_clean_open} -> "
              f"{p_stub_open}); this control no longer isolates the barrel "
              "lie, so it no longer proves what it claims to prove")
        ok = False
    print(f"NEGATIVE CONTROL PASS: an F.Cu joint on {STUB_REF}.{STUB_PAD} -- a "
          f"lead the switch body sits on -- opens {stub_open - clean_open} "
          "more connection(s) under the unplated model, while stock KiCad's "
          "plated model sees nothing wrong with it at all." if ok else
          "NEGATIVE CONTROL FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
