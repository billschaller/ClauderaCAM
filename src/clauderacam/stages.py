"""Per-stage job model — the UX data model behind the viewer and reports.

A STAGE is one logical operation of a program, recovered from the program's
own `(begin operation: ...)` markers by the strict parser (simulate.OP_MARK)
— derived from the .nc BYTES, never from the job config (Article VI: what
the viewer describes is the file the machine will run). A hand-written
program without markers is a single stage named "program".

Each stage carries three kinds of numbers, kept honest per the working
rules ("time estimates are estimates; verification results are facts"):
  facts     — moves, path lengths, removed volume, max footprint contact,
              max engagement fraction, deepest cut (all kernel-measured)
  verdicts  — peak windowed chip load and cutting power vs the material
              limits (model-based; see physics.py's honesty contract)
  estimates — machining time from commanded feed rates (excludes tool
              changes and dwells; always labeled as an estimate)
"""
from __future__ import annotations

import numpy as np

from .physics import windowed_load_power
from .simulate import CarveResult
from .verify import Report, contact_limit


def fmt_time(seconds: float) -> str:
    s = int(round(seconds))
    if s >= 3600:
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def stage_stats(job, res: CarveResult) -> list[dict]:
    """One dict per stage, JSON-clean (plain floats/ints/strs only)."""
    m = res.metrics
    wload, wpower = windowed_load_power(job, m)
    L = np.hypot(np.hypot(m.x1 - m.x0, m.y1 - m.y0), m.z1 - m.z0)
    mat = job.material
    stats: list[dict] = []
    for s, label in enumerate(res.stage_labels):
        sel = m.stage == s
        cut = sel & (m.motion == 1)
        tools_used = sorted({int(t) for t in m.tool_num[sel]}) if sel.any() \
            else []
        tnum = tools_used[0] if tools_used else None
        tool = job.tool(tnum) if tnum is not None else None
        chip_limit = mat["a_tooth_max_frac"] * tool.diameter ** 2 \
            if tool else 0.0
        peak_ratio = float(wload[sel].max()) if sel.any() else 0.0
        stats.append({
            "index": s, "label": label,
            "tool": tnum, "tools": tools_used,
            "tool_desc": f"{tool.type} Ø{tool.diameter:g}" if tool else "—",
            "moves": int(sel.sum()),
            "cut_mm": float(L[cut].sum()),
            "rapid_mm": float(L[sel & (m.motion == 0)].sum()),
            "est_s": float(m.time_s[sel].sum()),
            "volume_mm3": float(m.volume[sel].sum()),
            "max_contact": float(m.contact[sel].max()) if sel.any() else 0.0,
            "contact_limit": float(contact_limit(tool)) if tool else 0.0,
            "max_efrac": float(m.efrac[sel].max()) if sel.any() else 0.0,
            "min_z": float(np.minimum(m.z0, m.z1)[cut].min())
            if cut.any() else 0.0,
            "max_feed": float(m.feed[cut].max()) if cut.any() else 0.0,
            "peak_chip_mm3": float(peak_ratio * chip_limit),
            "chip_limit_mm3": float(chip_limit),
            "peak_power_w": float(wpower[sel].max()) if sel.any() else 0.0,
            "power_limit_w": float(job.machine["spindle_cut_w"]),
        })
    return stats


def stage_lines(stats: list[dict]) -> list[str]:
    """Compact per-stage text for MCP/CLI reporting."""
    out = []
    for st in stats:
        out.append(
            f"stage {st['index'] + 1}/{len(stats)} {st['label']}: "
            f"T{st['tool']} {st['tool_desc']}, ~{fmt_time(st['est_s'])} est, "
            f"{st['volume_mm3']:.0f}mm³ removed, "
            f"contact {st['max_contact']:.2f}/{st['contact_limit']:g}mm, "
            f"peak {st['peak_power_w']:.0f}W")
    return out


def viewer_payload(job, report: Report) -> tuple[dict, list[np.ndarray]]:
    """Everything the viewer app shows: job card, stage list, tool card,
    checks — plus the per-stage stock grids to serve. report.carve must be
    present (a fatal-parse report has nothing to show)."""
    res = report.carve
    stats = stage_stats(job, res)
    tools = []
    for t in sorted(job.tools):
        tool = job.tools[t]
        c = res.contact.get(t)
        tools.append({
            "num": t, "type": tool.type, "diameter": tool.diameter,
            "rpm": tool.rpm, "flutes": tool.flutes,
            "flute_length": tool.flute_length,
            "shank": tool.shank_diameter,
            "stages": [st["index"] for st in stats if t in st["tools"]],
            "contact": float(c.max) if c else 0.0,
            "contact_limit": float(contact_limit(tool)),
        })
    meta = {
        "job": job.name, "nc": job.out.name, "ok": report.ok,
        "n": int(res.stock.shape[0]), "ppm": res.ppm, "half": res.half,
        "material": job.material["name"], "machine": job.machine["name"],
        "stock_size": job.stock_size,
        "stock_thickness": job.stock_thickness,
        "model_d": 2 * job.model_radius,
        "total_est_s": sum(st["est_s"] for st in stats),
        "stages": stats, "tools": tools,
        "checks": [{"name": c.name, "value": c.value, "limit": c.limit,
                    "ok": c.ok, "detail": c.detail} for c in report.checks],
    }
    return meta, res.stage_stocks
