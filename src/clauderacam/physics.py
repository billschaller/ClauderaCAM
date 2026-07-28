"""Cutting-physics layer: materials, machine, and the checks computed from
the kernel's per-move measurements.

HONESTY CONTRACT (Article II applies to every number here): the geometric
checks in verify.py are facts about the simulation; the checks in this file
are MODEL-based verdicts. Each limit states its provenance — published
machining data, the Carvera Air spec sheet, or the two metal-validated jobs
(the aluminum coin and the brass mango coin). Limits marked PROVISIONAL are
anchored to those two data points plus handbook ranges and should tighten or
relax only with new metal evidence, written into DESIGN.md.

What is modeled (and what is not):
  - chip load / tooth overload:   a_tooth = removed volume per cutting edge
    per revolution, limited proportionally to d² (tooth cross-section).
  - rubbing: feed-per-tooth below the material's minimum while actually
    removing material — the tool burnishes instead of cutting: heat + work
    hardening + dulling.
  - spindle power / stall: P = specific_energy × MRR against the spindle's
    available cutting power.
  - sustained heat (PROXY): rolling-window mean cutting power. No coolant on
    the Air; heat leaves through chips and air. This does NOT model
    temperature — it bounds sustained energy input.
  - chip evacuation / gumming (PROXY): chip volume produced during a
    CONTINUOUS enclosed cut (engagement fraction ≥ ENCLOSED_EFRAC with real
    removal). Gummy materials (6061) weld chips to the tool when they cannot
    escape; the limit bounds how much chip a single enclosed stretch may
    produce. It does NOT model chip shape or airflow.
  - shank/holder clearance is NOT here — it is a geometric fact measured by
    the kernel and checked in verify.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Carvera Air spec: 200W spindle, 0-13000 RPM (makera.com product page).
# spindle_cut_w is the power we allow the CUT to demand — 50% of rated,
# covering transmission loss and leaving headroom before stall. PROVISIONAL.
MACHINE_DEFAULTS = {
    "name": "Carvera Air",
    "spindle_rated_w": 200.0,
    "spindle_cut_w": 100.0,
    "spindle_max_rpm": 13000,
    "max_feed": 3000.0,      # mm/min, firmware XY limit territory
    "rapid_feed": 3000.0,    # used for time estimates of G0 moves
}

# Material table. specific_energy in J/mm³ (unit cutting power): measured
# ~0.52 J/mm³ for Al 2014 at medium speed (ScienceDirect/PMC refs in
# DESIGN.md); micro-milling size effect raises effective values, so we use
# 1.0 for 6061 and 2.2 for brass (handbook ratio brass ≈ 2× aluminum).
# Validated anchors (brass mango coin, measured 2026-07-28, all PASS):
#   sustained a_tooth 0.043 mm³ on d=3.175; windowed power 19 W;
#   enclosed cutout run ~326 mm³ continuous; plunges 150-200 mm/min.
MATERIALS = {
    "brass": {
        "specific_energy": 2.2,      # J/mm³, PROVISIONAL
        "fz_min_frac": 0.0015,       # min feed/tooth as fraction of d
        # sustained (0.25s prorated window) anchor: the validated mango job
        # measures 0.043 mm³/tooth on d=3.175 descending the art's rim taper
        # -> frac 0.00427; limit 0.0055 = 29% above validated, ~2.8x below
        # the slam class (~0.156 sustained) the check exists to catch
        "a_tooth_max_frac": 0.0055,
        "plunge_feed_max": 400.0,    # mm/min straight-down, validated 150-200
        "sustained_w": 80.0,         # rolling-window mean power cap, PROXY
        "heat_window_s": 10.0,
        "enclosed_chip_mm3": 2500.0,  # brass chips free-cutting; anchor 900
    },
    "aluminum-6061": {
        "specific_energy": 1.0,
        "fz_min_frac": 0.002,        # 6061 work-hardens when rubbed
        "a_tooth_max_frac": 0.003,
        "plunge_feed_max": 300.0,
        "sustained_w": 60.0,
        "heat_window_s": 10.0,
        "enclosed_chip_mm3": 600.0,  # gummy: chips weld if they can't escape
    },
}

ENCLOSED_EFRAC = 0.4     # engagement fraction that counts as an enclosed cut
REMOVAL_FLOOR = 0.05     # mm³/s below which a move is not "really cutting"

# Overload and stall are SUSTAINED phenomena: the validated jobs contain
# ~40ms terrain-descent pulses that spike instantaneous chip load to the
# same order as a genuine full-width slam (both ~0.16 mm³/tooth on d=3.175),
# but the pulse ends within 0.2mm of travel while a slam persists — and the
# spindle's rotor inertia absorbs the pulse. Chip load and cutting power are
# therefore evaluated as sliding-window averages over contiguous cutting
# runs; windows never span retracts or tool changes.
#
# KNOWN LIMIT: metrics are per-move, so removal concentrated INSIDE one long
# uniform move is smeared across that move's duration. Generator output
# breaks moves wherever terrain changes (the simplifier keeps deviation
# under 5-10µm), so concentration implies segmentation there — but a
# hand-written single long move through varying terrain under-reads. The
# geometric contact checks (per-sample) still see it.
LOAD_WINDOW_S = 0.25


@dataclass
class PhysCheck:
    name: str
    value: float
    limit: str
    ok: bool
    detail: str = ""


def windowed_load_power(job, m):
    """Per-move sustained chip load and cutting power, the LOAD_WINDOW_S
    prorated sliding window ending at each cutting move (windows never span
    retracts or tool changes — see the sustained-phenomena note above).

    Returns (load_ratio, power_w): load_ratio[k] is the windowed chip per
    tooth as a FRACTION of that move's limit (a_tooth_max_frac · d²), so 1.0
    is the line; power_w[k] is the windowed mean cutting power in watts.
    Non-cutting moves are 0. physics_checks() takes the maxima; the stage
    model (stages.py) takes per-stage maxima of the same arrays."""
    mat = job.material
    nmoves = len(m.volume)
    load_ratio = np.zeros(nmoves)
    power_w = np.zeros(nmoves)
    cutting = (m.motion == 1)
    t_s = m.time_s
    dia = np.array([job.tool(int(t)).diameter for t in m.tool_num])
    flutes = np.array([job.tool(int(t)).flutes for t in m.tool_num])
    rpm = np.maximum(m.rpm, 1.0)
    tooth_rate = t_s * rpm / 60.0 * flutes        # tooth-passes per move
    energy = mat["specific_energy"] * m.volume    # J per move
    k = 0
    while k < nmoves:
        if not cutting[k]:
            k += 1
            continue
        k1 = k
        while k1 < nmoves and cutting[k1]:
            k1 += 1
        rt = t_s[k:k1]
        ct = np.cumsum(rt)
        cv = np.concatenate([[0.0], np.cumsum(m.volume[k:k1])])
        ctp = np.concatenate([[0.0], np.cumsum(tooth_rate[k:k1])])
        ce = np.concatenate([[0.0], np.cumsum(energy[k:k1])])
        for j1 in range(k1 - k):
            t_end = ct[j1]
            t_start = max(0.0, t_end - LOAD_WINDOW_S)
            idx = int(np.searchsorted(ct, t_start, side="right"))
            # full moves idx+1..j1, plus the tail fraction of move idx
            frac = 0.0
            if idx <= j1 and rt[idx] > 0:
                frac = (ct[idx] - t_start) / rt[idx]
            dv = cv[j1 + 1] - cv[idx + 1] + frac * m.volume[k + idx]
            dtp = ctp[j1 + 1] - ctp[idx + 1] + frac * tooth_rate[k + idx]
            de = ce[j1 + 1] - ce[idx + 1] + frac * energy[k + idx]
            dts = t_end - t_start
            if dtp > 0:
                load_ratio[k + j1] = dv / dtp / (
                    mat["a_tooth_max_frac"] * dia[k + j1] ** 2)
            if dts > 0:
                power_w[k + j1] = de / dts
        k = k1
    return load_ratio, power_w


def physics_checks(job, metrics) -> list[PhysCheck]:
    """metrics: the kernel's per-move arrays (see simulate.MoveMetrics)."""
    mat = job.material
    mach = job.machine
    checks: list[PhysCheck] = []
    m = metrics
    nmoves = len(m.volume)
    cutting = (m.motion == 1)
    t_s = m.time_s
    with np.errstate(divide="ignore", invalid="ignore"):
        mrr = np.where(t_s > 0, m.volume / t_s, 0.0)          # mm³/s
    dz = m.z1 - m.z0
    lat = np.hypot(m.x1 - m.x0, m.y1 - m.y0)
    # a plunge is an AXIAL descent (zero surface speed at a ball's center);
    # steep in-row contour segments have lateral motion and are governed by
    # the contact and chip-load checks instead
    plungey = cutting & (dz < 0) & (lat < 0.02)
    removing = cutting & (mrr > REMOVAL_FLOOR)

    dia = np.array([job.tool(int(t)).diameter for t in m.tool_num])
    flutes = np.array([job.tool(int(t)).flutes for t in m.tool_num])
    rpm = np.maximum(m.rpm, 1.0)

    # --- feed & speed sanity (machine spec, hard) --------------------------
    worst_feed = float(m.feed[cutting].max()) if cutting.any() else 0.0
    checks.append(PhysCheck("max commanded feed", worst_feed,
                            f"<= {mach['max_feed']:g} mm/min",
                            worst_feed <= mach["max_feed"]))

    # --- sustained tooth overload + sustained power (windowed over runs) ---
    # Moves are internally uniform-rate, so boundary moves enter the window
    # FRACTIONALLY (prorated) — whole-move windows degenerate to a single
    # 30ms move when its neighbors are long, defeating the smoothing.
    # (Computation shared with the per-stage model — windowed_load_power.)
    wload, wpower = windowed_load_power(job, m)
    worst_load = float(wload.max()) if nmoves else 0.0
    load_i = int(np.argmax(wload)) if nmoves else 0
    worst_pwr = float(wpower.max()) if nmoves else 0.0
    pwr_i = int(np.argmax(wpower)) if nmoves else 0
    i = load_i
    a_val = worst_load * mat["a_tooth_max_frac"] * dia[i] ** 2 if nmoves else 0
    checks.append(PhysCheck(
        "sustained chip per tooth", float(a_val),
        f"<= {mat['a_tooth_max_frac']}*d² mm³ over {LOAD_WINDOW_S}s",
        worst_load <= 1.0,
        f"T{int(m.tool_num[i])} line {int(m.lineno[i])}" if nmoves else ""))

    # --- rubbing -----------------------------------------------------------
    fz = m.feed / (rpm * flutes)
    rub = removing & ~plungey & (fz < mat["fz_min_frac"] * dia)
    n_rub = int(rub.sum())
    detail = ""
    if n_rub:
        i = int(np.argmax(rub))
        detail = (f"T{int(m.tool_num[i])} line {int(m.lineno[i])} "
                  f"fz={fz[i]:.4f} < {mat['fz_min_frac'] * dia[i]:.4f}")
    checks.append(PhysCheck("rubbing (feed/tooth too low)", float(n_rub),
                            "0 moves", n_rub == 0, detail))

    # --- plunge rate -------------------------------------------------------
    worst_pl = float(m.feed[plungey].max()) if plungey.any() else 0.0
    checks.append(PhysCheck("plunge feed", worst_pl,
                            f"<= {mat['plunge_feed_max']:g} mm/min",
                            worst_pl <= mat["plunge_feed_max"]))

    # --- spindle power / stall (windowed, computed above) ------------------
    checks.append(PhysCheck(
        "cutting power", float(worst_pwr),
        f"<= {mach['spindle_cut_w']:g} W over {LOAD_WINDOW_S}s",
        worst_pwr <= mach["spindle_cut_w"],
        f"T{int(m.tool_num[pwr_i])} line {int(m.lineno[pwr_i])}"
        if nmoves else ""))
    power = mat["specific_energy"] * mrr   # per-move, for the heat window

    # --- sustained heat proxy: rolling-window mean power -------------------
    win = mat["heat_window_s"]
    worst_avg = 0.0
    if nmoves:
        energy = power * t_s
        ct, ce = np.cumsum(t_s), np.cumsum(energy)
        j0 = 0
        for j1 in range(nmoves):
            while ct[j1] - (ct[j0 - 1] if j0 else 0.0) > win and j0 < j1:
                j0 += 1
            dt = ct[j1] - (ct[j0 - 1] if j0 else 0.0)
            if dt >= win * 0.5:
                de = ce[j1] - (ce[j0 - 1] if j0 else 0.0)
                worst_avg = max(worst_avg, de / dt)
    checks.append(PhysCheck(f"sustained power ({win:g}s window)", worst_avg,
                            f"<= {mat['sustained_w']:g} W",
                            worst_avg <= mat["sustained_w"]))

    # --- gumming proxy: chip volume in one continuous enclosed stretch -----
    worst_run, run = 0.0, 0.0
    for k in range(nmoves):
        if cutting[k] and m.efrac[k] >= ENCLOSED_EFRAC and mrr[k] > REMOVAL_FLOOR:
            run += float(m.volume[k])
            worst_run = max(worst_run, run)
        else:
            run = 0.0
    checks.append(PhysCheck(
        "enclosed chip volume (gumming proxy)", worst_run,
        f"<= {mat['enclosed_chip_mm3']:g} mm³",
        worst_run <= mat["enclosed_chip_mm3"]))

    return checks
