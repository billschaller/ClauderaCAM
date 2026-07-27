"""Spiral-ramped circular cutout with holding tabs.

Faithful port of mango-brass/make_cutout.py. Full circle around the model at
tool-centerline radius rc, ramping `ramp` mm/rev to z_final plus one spring
pass, lifting to tab_top over each tab arc. Tabs at 0.5mm tall x 2mm wide
proved right for brass (1mm x 3mm was "thick as heck").
"""
from __future__ import annotations

import math


def generate(*, rc: float, z_start: float, z_final: float, ramp: float,
             tab_top: float, tab_width: float, tool_dia: float,
             tab_centers_deg: list[float], seg: int,
             feed: float, plunge_feed: float, safe_z: float = 3.0) -> list[str]:
    tab_arc = (tab_width + tool_dia) / rc
    tab_centers = [math.radians(a) for a in tab_centers_deg]

    def in_tab(theta):
        t = theta % (2 * math.pi)
        return any(abs((t - c + math.pi) % (2 * math.pi) - math.pi) < tab_arc / 2
                   for c in tab_centers)

    lines: list[str] = []
    emit = lines.append
    emit(f"G0 Z{safe_z:.3f}")
    emit(f"G0 X{rc:.3f} Y0.000")
    emit(f"G1 Z{z_start:.3f} F{plunge_feed:.1f}")

    total_drop = z_start - z_final
    revs = math.ceil(total_drop / ramp) + 1
    first = True
    for i in range(revs * seg + 1):
        theta = 2 * math.pi * i / seg
        z = max(z_final, z_start - ramp * theta / (2 * math.pi))
        if z < tab_top and in_tab(theta):
            z = tab_top
        x = rc * math.cos(theta)
        y = rc * math.sin(theta)
        f = f" F{feed:.1f}" if first else ""
        emit(f"G1 X{x:.3f} Y{y:.3f} Z{z:.3f}{f}")
        first = False

    emit(f"G0 Z{safe_z:.3f}")
    return lines
