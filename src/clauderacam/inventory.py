"""The tool crib (Article XI): jobs may only use tools the shop holds.

Born 2026-07-28: a job config carried an invented 14mm drill reach — the
operator went hunting for a bit that does not exist. The durable rule:
tool GEOMETRY comes from the inventory file, never from a config's
imagination. A job tool must match an inventory entry on type, diameter,
shank and flute count; may claim AT MOST the entry's reach (claiming
less is conservative both ways: the shank-crash bound tightens, and the
simulated shank sits lower than the real one); and the entry must have
quantity on hand.

The inventory file is the job directory's `inventory.toml` (override per
job with [machine] inventory = "path"). No inventory file, no job.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

EPS = 1e-3


def load(path: str | Path) -> list[dict]:
    path = Path(path)
    if not path.is_file():
        raise ValueError(
            f"no tool inventory at {path} — jobs may only use tools the "
            f"shop holds (Article XI); create the inventory file or point "
            f"[machine] inventory at it")
    with open(path, "rb") as f:
        d = tomllib.load(f)
    tools = d.get("tool", [])
    if not tools:
        raise ValueError(f"tool inventory {path} lists no tools")
    for i, t in enumerate(tools):
        req = ("type", "diameter", "shank_diameter", "flutes",
               "flute_length", "qty")
        if t.get("type") == "vee":
            # the cone is the tool: an entry without tip and angle cannot
            # back a vee job
            req += ("tip_diameter", "included_angle_deg")
        for k in req:
            if k not in t:
                raise ValueError(
                    f"inventory {path} entry {i + 1} "
                    f"({t.get('label', '?')}) is missing {k!r} — an entry "
                    f"without full geometry cannot gate anything")
    return tools


def resolve_path(d: dict, job_dir: Path) -> Path:
    return (job_dir / d.get("machine", {}).get("inventory",
                                               "inventory.toml")).resolve()


def match(tool, inv: list[dict], inv_path) -> dict:
    """Find the inventory entry backing a job Tool, or refuse with the
    nearest candidates spelled out."""
    near = []
    for rec in inv:
        if rec["type"] != tool.type \
                or abs(rec["diameter"] - tool.diameter) > EPS:
            continue
        near.append(rec)
        if abs(rec["shank_diameter"] - tool.shank_diameter) > EPS:
            continue
        if rec["flutes"] != tool.flutes:
            continue
        if tool.flute_length > rec["flute_length"] + EPS:
            continue
        if tool.type == "vee":
            # a vee is its cone: tip and angle must match the entry exactly
            if abs(rec["tip_diameter"] - tool.tip_diameter) > EPS:
                continue
            if abs(rec["included_angle_deg"]
                   - tool.included_angle_deg) > EPS:
                continue
        if rec["qty"] < 1:
            continue
        return rec
    detail = "; ".join(
        f"{r['label']}: {r['flutes']}F reach {r['flute_length']} "
        f"shank {r['shank_diameter']} qty {r['qty']}" for r in near) \
        or "no entry of that type and diameter at all"
    raise ValueError(
        f"tool T{tool.num} ({tool.type} Ø{tool.diameter}, {tool.flutes}F, "
        f"reach {tool.flute_length}, shank Ø{tool.shank_diameter}) is not "
        f"in the inventory ({inv_path}) — nearest: {detail}. The shop "
        f"cannot cut with a tool it does not hold (Article XI)")
