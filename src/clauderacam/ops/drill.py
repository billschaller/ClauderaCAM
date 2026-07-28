"""Registration-pin hole ops for pin-and-flip two-sided jobs.

Two ops, two tools, standard practice order (spot-face THEN drill):

  spotface — a shallow plunge with a flat end mill at each pin position.
    Exists for a physical reason: the twist drill's entry burr sits on the
    face that gets flipped DOWN onto the spoilboard — a 0.05mm burr ring
    rocks the stock and becomes Z registration error. The spot-face recess
    keeps the burr below the resting plane.

  pindrill — full-retract peck cycle with a twist drill, emitted as plain
    G0/G1 so the verification gate models every move (Article I — no
    canned cycles the simulator cannot see). Each peck: rapid down to just
    above the current floor, feed one peck deeper, rapid full retract for
    chip clearance. The re-entry rapid descends INTO the hole — legal
    against the rapid-vs-stock check precisely because the hole was carved
    by the same tool footprint the check measures with.

Holes drilled through the stock into the spoilboard in the SAME setup as
the side-1 art are coaxial by construction — that is the entire accuracy
story of pin-and-flip registration.
"""
from __future__ import annotations

SAFE_Z = 3.0
REENTRY_GAP = 0.5   # rapid re-entry stops this far above the current floor


def spotface(positions, depth: float, feed: float,
             step: float = 0.4) -> list[str]:
    """Counterbore tool-diameter spots `depth` below the stock top at each
    pin. Plunges are STEPPED (≤ step per move): a single full-depth plunge
    takes the whole depth as one move-start bite, which the flat contact
    limit rightly refuses at counterbore depths. Deep spot-faces exist to
    buy a short drill extra reach — see engine.check_job_plan's
    counterbore credit."""
    lines: list[str] = []
    for x, y in positions:
        lines.append(f"G0 Z{SAFE_Z:.3f}")
        lines.append(f"G0 X{x:.3f} Y{y:.3f}")
        lines.append("G0 Z0.500")
        z = 0.0
        while z > -depth + 1e-9:
            z = max(z - step, -depth)
            lines.append(f"G1 Z{z:.3f} F{feed:g}")
        lines.append(f"G0 Z{SAFE_Z:.3f}")
    return lines


def pindrill(positions, depth: float, peck: float, feed: float) -> list[str]:
    """Peck-drill to `depth` below the stock top at each pin position."""
    if peck <= 0:
        raise ValueError("peck must be positive")
    lines: list[str] = []
    for x, y in positions:
        lines.append(f"G0 Z{SAFE_Z:.3f}")
        lines.append(f"G0 X{x:.3f} Y{y:.3f}")
        z = 0.0
        while z > -depth + 1e-9:
            if z < 0:  # re-enter the existing hole quickly, then cut
                lines.append(f"G0 Z{z + REENTRY_GAP:.3f}")
            z = max(z - peck, -depth)
            lines.append(f"G1 Z{z:.3f} F{feed:g}")
            lines.append(f"G0 Z{SAFE_Z:.3f}")  # full retract: chips out
    return lines
