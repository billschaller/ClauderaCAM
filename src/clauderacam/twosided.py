"""Pin-and-flip two-sided jobs: one document, two setups, one registration.

Physical model (designed 2026-07-28 with the machinist, decisions on
record in DESIGN.md):
  - stock on an MDF spoilboard; SIDE 1 (front) machines the front art plus
    a minimal MOAT only — the surrounding ring keeps full thickness so
    that, flipped, it sits dead flat on the spoilboard and the side-2
    cutout slots into supported material (facing the whole field on side 1
    would leave the flipped ring an unsupported membrane)
  - side 1 ends by SPOT-FACING (burrs below the resting plane) and
    PECK-DRILLING the registration holes through the stock into the
    spoilboard — same setup as the art, so holes are coaxial by
    construction and flip accuracy equals pin-to-hole clearance
  - the stock is FLIPPED about the configured axis onto the pins; pin
    positions must be symmetric under that flip so they land back in the
    same spoilboard holes
  - SIDE 2 (back) machines the back art and moat, then the CUTOUT WITH
    TABS severs the coin (tabs snap by hand — the machine never frees the
    part next to a spinning cutter)

Verification carries across the flip (Article I: the bytes are verified
against what will actually be there): the back side is checked against a
BOTTOM FIELD B(x,y) = -thickness - S1(flip(x,y)) — the mirror of side 1's
simulated stock — enabling the checks that make this mode trustworthy:
punch-through (back never cuts into front art), sever against the ACTUAL
material bottom, the TAB BRIDGE (each tab is a continuous ledge of
material from coin to skeleton — the front moat overrun thins the slot
band, and a tab_top chosen blind can leave tabs floating in air), and
pin keep-out (the pins are steel and flush; no tool crosses them).
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import ndimage

from . import emit, engine, verify as verifymod
from .job import Job, parse_tools, resolve_machine, resolve_material, \
    resolve_model_z
from .verify import Check, Report

PIN_CLEAR = 1.0        # no side-2 machining within pin_r + this (steel!)
BRIDGE_MIN = 0.10      # a tab must carry at least this material thickness
PUNCH_TOL = 0.05       # resample/quantization allowance for punch-through
SEVER_TOL = 0.05


@dataclass
class TwoSided:
    path: Path
    name: str
    flip_axis: str          # "x" or "y": the axis the stock rotates ABOUT
    pins: dict
    front: Job
    back: Job


def is_twosided(path: str | Path) -> bool:
    try:
        with open(path, "rb") as f:
            return "twosided" in tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return False


def _flip_xy(x: float, y: float, axis: str) -> tuple[float, float]:
    """Where a stock feature at (x, y) lands after the physical flip."""
    return (x, -y) if axis == "x" else (-x, y)


def load(path: str | Path) -> TwoSided:
    path = Path(path).resolve()
    with open(path, "rb") as f:
        d = tomllib.load(f)
    base = path.parent
    ts_d = d["twosided"]
    pins = dict(d["pins"])
    flip_axis = ts_d.get("flip_axis", "y")
    if flip_axis not in ("x", "y"):
        raise ValueError(f"flip_axis must be 'x' or 'y', got {flip_axis!r}")

    material = resolve_material(d)
    machine = resolve_machine(d)
    tools = parse_tools(d, machine, base)
    spoil = d.get("spoilboard", {}).get("thickness", 0.0)

    # pins must be symmetric under the flip, or the flipped stock cannot
    # land back in the holes it drilled
    pos = [tuple(p) for p in pins["positions"]]
    flipped = {(round(fx, 3), round(fy, 3))
               for fx, fy in (_flip_xy(x, y, flip_axis) for x, y in pos)}
    if flipped != {(round(x, 3), round(y, 3)) for x, y in pos}:
        raise ValueError(
            f"pin positions {pos} are not symmetric under a flip about "
            f"the {flip_axis} axis — the flipped stock would not land in "
            f"its own holes")
    if len(pos) < 2:
        raise ValueError("two pins minimum: one pin leaves rotation free")

    def side_job(side: str, stl_key: str, out_key: str,
                 ops: list[dict]) -> Job:
        stl = (base / Path(ts_d[stl_key]).expanduser()).resolve()
        z_offset, floor_z = resolve_model_z(d["model"], stl)
        j = Job(
            # distinct identity per side: the viewer sessions and stale
            # marking key on job.path, and the two sides are two documents
            # to a watcher even though they share one TOML
            path=path.with_name(path.name + f"#{side}"),
            name=f"{d['job']['name']}-{side}",
            stl=stl,
            out=(base / d["job"][out_key]).resolve(),
            stock_size=d["stock"]["size"],
            stock_thickness=d["stock"]["thickness"],
            model_radius=d["model"]["radius"],
            skim=d["model"]["skim"],
            floor_z=floor_z,
            z_offset=z_offset,
            clip_disc=d["model"].get("z", "positioned") == "relief",
            keepout_radius=d["fixture"]["keepout_radius"],
            spoil_thickness=spoil,
            material=material,
            machine=machine,
        )
        j.tools = dict(tools)
        j.ops = ops
        return j

    front_ops = [dict(op) for op in d["side"]["front"]["op"]]
    back_ops = [dict(op) for op in d["side"]["back"]["op"]]
    if any(op["kind"] == "cutout" for op in front_ops):
        raise ValueError(
            "front side must not cut out — the cutout runs on side 2, "
            "into ring material the front left at full thickness")
    if back_ops[-1]["kind"] != "cutout":
        raise ValueError("back side must end with the cutout (tabs are "
                         "the only thing holding the coin after it)")
    if any(op["kind"] in ("spotface", "pindrill")
           for op in front_ops + back_ops):
        raise ValueError("pin ops are generated from [pins] — do not "
                         "write spotface/pindrill ops by hand")

    # side 1 ends with the registration holes: spot-face then peck-drill.
    # depth = pin length + seat (pin sits recessed, never proud) + drill
    # tip allowance (the sim hole is flat-bottomed; the real point cone
    # needs this much extra to give the pin its full-diameter depth).
    depth = pins["length"] + pins.get("seat_extra", 0.2) \
        + pins.get("tip_allowance", 0.6)
    front_ops = front_ops + [
        {"kind": "spotface", "label": "spotface pins",
         "tool": pins["spot_tool"],
         "positions": [list(p) for p in pos],
         "depth": pins.get("spot_depth", 0.1),
         "feed": pins.get("spot_feed", 100)},
        {"kind": "pindrill", "label": "drill pins",
         "tool": pins["drill_tool"],
         "positions": [list(p) for p in pos],
         "depth": depth,
         "peck": pins.get("peck", 0.8),
         "feed": pins.get("feed", 120)},
    ]

    ts = TwoSided(path=path, name=d["job"]["name"], flip_axis=flip_axis,
                  pins=pins,
                  front=side_job("front", "front_stl", "out_front",
                                 front_ops),
                  back=side_job("back", "back_stl", "out_back", back_ops))

    # pins must clear both sides' machining (the hole walls survive side 1;
    # the steel pins survive side 2)
    drill_r = tools[pins["drill_tool"]].diameter / 2
    for x, y in pos:
        rp = float(np.hypot(x, y))
        for j in (ts.front, ts.back):
            worst = max((engine.op_reach(j, op) for op in j.ops
                         if op["kind"] not in ("spotface", "pindrill")),
                        default=0.0)
            if rp - drill_r - PIN_CLEAR < worst:
                raise ValueError(
                    f"pin at ({x},{y}) r={rp:.2f} is within {PIN_CLEAR}mm "
                    f"of {j.name}'s machining reach {worst:.2f}")
    return ts


def generate(ts: TwoSided) -> dict[str, list]:
    """Generate and write both sides' programs. Returns per-side OpResults."""
    out = {}
    for side, j in (("front", ts.front), ("back", ts.back)):
        ops = engine.generate_ops(j)
        emit.write(j, ops)
        out[side] = ops
    return out


def bottom_field(front_stock: np.ndarray, ppm: float, half: float,
                 thickness: float, flip_axis: str) -> np.ndarray:
    """The flipped stock's bottom surface on the back-side carve grid.

    A point of side-1 stock at (x, y, z) lands after the flip at
    (flip(x, y), -thickness - z), so the new bottom under back-grid pixel
    (x', y') is -thickness - S1(flip(x', y')). Resampled by explicit world
    coordinates through the carve mapping (Article IV: never re-derive a
    grid center; the grid is NOT symmetric under j -> n-j).
    """
    n = front_stock.shape[0]
    yy, xx = np.mgrid[0:n, 0:n]
    xw = xx / ppm - half           # back-grid world coords
    yw = half - yy / ppm
    if flip_axis == "x":
        sx, sy = xw, -yw           # source point on side 1
    else:
        sx, sy = -xw, yw
    i1 = np.clip(np.round((half - sy) * ppm).astype(int), 0, n - 1)
    j1 = np.clip(np.round((sx + half) * ppm).astype(int), 0, n - 1)
    return (-thickness - front_stock[i1, j1]).astype(np.float32)


def verify(ts: TwoSided) -> dict[str, Report]:
    """Verify both sides. The back report carries the cross-side checks
    against the bottom field; a fatal front verification makes the back
    unverifiable (there is nothing true to carry over)."""
    front_rep = verifymod.verify(ts.front)
    if front_rep.carve is None:
        return {"front": front_rep,
                "back": Report.fatal("front side failed to parse — the "
                                     "back cannot be verified against an "
                                     "unknown bottom")}
    back_rep = verifymod.verify(ts.back)
    if back_rep.carve is None:
        return {"front": front_rep, "back": back_rep}

    res = back_rep.carve
    S2, ppm, half = res.stock, res.ppm, res.half
    n = S2.shape[0]
    t = ts.back.stock_thickness
    B = bottom_field(front_rep.carve.stock, ppm, half, t, ts.flip_axis)
    # lower envelope: at a steep front-art wall one resampled pixel
    # legitimately spans wall top and bottom (same incident class as the
    # gouge check's false positive — see verify.py)
    B_lo = ndimage.minimum_filter(B, size=3, mode="nearest")

    yy, xx = np.mgrid[0:n, 0:n]
    xw = xx / ppm - half
    yw = half - yy / ppm
    rr = np.hypot(xw, yw)

    cut = ts.back.ops[-1]
    tool = ts.back.tool(cut["tool"])
    rc = ts.back.model_radius + tool.radius
    slot = (rr > rc - tool.radius - 0.3) & (rr < rc + tool.radius + 0.3)

    pin_r = ts.pins["diameter"] / 2
    pinmask = np.zeros_like(slot)
    for x, y in ts.pins["positions"]:
        fx, fy = _flip_xy(x, y, ts.flip_axis)
        pinmask |= np.hypot(xw - fx, yw - fy) <= pin_r + PIN_CLEAR

    checks = back_rep.checks

    # punch-through: outside the slot, the back may never cut through the
    # material the front left standing
    zone = ~slot & ~pinmask & (rr < ts.back.stock_half)
    worst = float((B_lo[zone] - S2[zone]).max())
    checks.append(Check("punch-through into front side", worst,
                        f"<= {PUNCH_TOL}", worst <= PUNCH_TOL))

    # sever against the ACTUAL bottom (the front moat thinned the inner
    # slot band; -thickness alone is not the truth there)
    tab_arc = (cut["tab_width"] + tool.diameter) / rc
    seg_arc = 2 * np.pi / cut["seg"]
    halfw = tab_arc / 2 + 0.5 / rc + 2 * seg_arc
    theta = np.arctan2(yw, xw)
    in_tab = np.zeros_like(theta, dtype=bool)
    for a_deg in cut["tabs"]:
        a = np.radians(a_deg)
        in_tab |= np.abs((theta - a + np.pi) % (2 * np.pi) - np.pi) < halfw
    ringc = (np.abs(rr - rc) < 0.25) & ~in_tab
    sever = float((S2[ringc] - B[ringc]).max())
    checks.append(Check("sever vs actual bottom", sever,
                        f"<= {-SEVER_TOL}", sever <= -SEVER_TOL))

    # TAB BRIDGE: walk each tab radially across the slot; the material
    # ledge (S2 - B) must be continuously thick enough to carry the coin.
    # This is the check that catches tabs floating in air over the front
    # moat's overrun band — found on paper 2026-07-28, made law here.
    worst_bridge = np.inf
    r0 = ts.back.model_radius - 0.5
    r1 = rc + tool.radius + 0.5
    for a_deg in cut["tabs"]:
        a = np.radians(a_deg)
        rs = np.arange(r0, r1, 0.5 / ppm)
        px = rs * np.cos(a)
        py = rs * np.sin(a)
        ii = np.clip(np.round((half - py) * ppm).astype(int), 0, n - 1)
        jj = np.clip(np.round((px + half) * ppm).astype(int), 0, n - 1)
        h = S2[ii, jj].astype(np.float64) - B[ii, jj]
        worst_bridge = min(worst_bridge, float(h.min()))
    checks.append(Check("tab bridge (coin held to skeleton)",
                        worst_bridge, f">= {BRIDGE_MIN}",
                        worst_bridge >= BRIDGE_MIN,
                        f"{len(cut['tabs'])} tabs walked radially"))

    # pins are steel and flush-recessed: nothing machined over them
    pin_cut = float((-S2[pinmask]).max()) if pinmask.any() else 0.0
    checks.append(Check("pin keep-out (side 2)", pin_cut, "must be 0",
                        pin_cut <= 1e-3))

    return {"front": front_rep, "back": back_rep}
