"""Job configuration: one TOML file fully parameterizes a job.

Requirement 5 (see DESIGN.md): shrinking the mango coin required editing three
generators, the verifier, and hand-splicing four .nc sections. Here every
number lives in the job file and everything downstream derives from it.

Since the physics layer (2026-07-28), a job also names its stock MATERIAL and
the MACHINE, and each tool carries its full cutting geometry (flutes, flute
length, shank) — these are safety-relevant, so flutes and flute_length have
no defaults: a job that omits them does not load.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .physics import MACHINE_DEFAULTS, MATERIALS


@dataclass
class Tool:
    num: int
    type: str            # "flat" | "ball"
    diameter: float
    rpm: int
    flutes: int          # cutting edges (Spiral O = 1)
    flute_length: float  # mm of cutting flute above the tip
    shank_diameter: float

    @property
    def radius(self) -> float:
        return self.diameter / 2.0


@dataclass
class Job:
    path: Path
    name: str
    stl: Path
    out: Path
    stock_size: float          # square stock edge length
    stock_thickness: float
    model_radius: float        # coin radius; STL is centered with top at -skim
    skim: float
    floor_z: float             # relief field z (mesh bottom)
    keepout_radius: float      # fixture rule: nothing machined beyond this
    z_offset: float = 0.0      # applied to STL z (model z="relief" mode)
    clip_disc: bool = False    # relief mode: map is floor outside radius
    spoil_thickness: float = 0.0   # sacrificial board under the stock (mm);
    # pindrill ops are refused without one — a pin hole must have somewhere
    # safe to go that is not the machine bed
    material: dict = field(default_factory=dict)
    machine: dict = field(default_factory=dict)
    tools: dict[int, Tool] = field(default_factory=dict)
    ops: list[dict] = field(default_factory=list)

    @property
    def stock_half(self) -> float:
        return self.stock_size / 2.0

    def tool(self, num: int) -> Tool:
        return self.tools[num]

    def model_tris(self):
        """Load the model mesh with the job's z transform applied — the ONE
        way generators and the verifier read the model (they must agree on
        position, or the gouge check lies)."""
        from . import heightmap
        tris = heightmap.load_stl(self.stl)
        if self.z_offset:
            tris[:, :, 2] += self.z_offset
        return tris


def resolve_material(d: dict) -> dict:
    mat_cfg = d.get("material", {})
    mat_name = mat_cfg.get("name")
    if mat_name not in MATERIALS:
        raise ValueError(
            f"[material] name must be one of {sorted(MATERIALS)} — the "
            f"physics limits are per-material and have no safe default")
    return {**MATERIALS[mat_name], **{k: v for k, v in mat_cfg.items()
                                      if k != "name"}, "name": mat_name}


def resolve_machine(d: dict) -> dict:
    return {**MACHINE_DEFAULTS, **d.get("machine", {})}


def parse_tools(d: dict, machine: dict) -> dict[int, Tool]:
    tools: dict[int, Tool] = {}
    for t in d["tool"]:
        if t["type"] not in ("flat", "ball", "drill"):
            raise ValueError(
                f"tool T{t['num']}: unknown type {t['type']!r} — the "
                f"simulator models flat, ball and drill only")
        tools[t["num"]] = Tool(
            t["num"], t["type"], t["diameter"], t["rpm"],
            t["flutes"], t["flute_length"],
            t.get("shank_diameter", max(t["diameter"], 3.175)))
        if t["rpm"] > machine["spindle_max_rpm"]:
            raise ValueError(
                f"tool T{t['num']} rpm {t['rpm']} exceeds machine max "
                f"{machine['spindle_max_rpm']}")
    return tools


def resolve_model_z(model_d: dict, stl: Path) -> tuple[float, float]:
    """-> (z_offset, floor_z) for a [model] section and its mesh."""
    mode = model_d.get("z", "positioned")
    if mode == "relief":
        # z-up relief: derive the offset that puts the relief top at
        # -skim, and floor_z from the relief depth — from the TOP SURFACE
        # only. Real exports arrive with base slabs whose bottom faces
        # also carry vertices (the 2026-07-28 mango back v2: slab
        # underside at -0.5 put a vertex-min floor 1.0 too deep,
        # over-excavating the moat and floating the tabs — both caught by
        # the gate). Winding is unreliable for telling top from bottom
        # (open sheets wind arbitrarily), so: TOP = vertex max inside the
        # disc (junk sits below art by nature); FLOOR = min over coarse
        # cells of the max triangle-centroid z — bottom faces share cells
        # with the top face and lose the max, and a floor must be a flat
        # AREA, which wall slivers cannot fake. floor_z in the config
        # would be ignored — refuse the ambiguity.
        if "floor_z" in model_d:
            raise ValueError('[model] z="relief" derives floor_z — remove '
                             'the explicit floor_z')
        import numpy as np
        from . import heightmap
        radius = model_d["radius"]
        tris = heightmap.load_stl(stl)
        v = tris.reshape(-1, 3)
        selv = np.hypot(v[:, 0], v[:, 1]) <= radius + 1e-6
        if not selv.any():
            raise ValueError("no mesh vertices inside the model radius")
        ztop = float(v[selv, 2].max())
        g = 0.5
        c = tris.mean(axis=1)
        cs = c[np.hypot(c[:, 0], c[:, 1]) <= radius - g]
        if len(cs) == 0:
            raise ValueError("no mesh interior inside the model radius")
        ij = np.round((cs[:, :2] + radius) / g).astype(int)
        npx = int(2 * radius / g) + 2
        cell = np.full((npx, npx), -np.inf)
        np.maximum.at(cell, (ij[:, 1], ij[:, 0]), cs[:, 2])
        zfloor = float(cell[np.isfinite(cell)].min())
        z_offset = -(model_d["skim"] + ztop)
        return z_offset, zfloor + z_offset
    if mode == "positioned":
        return 0.0, model_d["floor_z"]
    raise ValueError(f'[model] z must be "positioned" or "relief", '
                     f'got {mode!r}')


def load(path: str | Path) -> Job:
    path = Path(path).resolve()
    with open(path, "rb") as f:
        d = tomllib.load(f)
    base = path.parent

    material = resolve_material(d)
    machine = resolve_machine(d)

    stl = (base / Path(d["job"]["stl"]).expanduser()).resolve()
    z_offset, floor_z = resolve_model_z(d["model"], stl)

    job = Job(
        path=path,
        name=d["job"]["name"],
        stl=stl,
        out=(base / d["job"]["out"]).resolve(),
        stock_size=d["stock"]["size"],
        stock_thickness=d["stock"]["thickness"],
        model_radius=d["model"]["radius"],
        skim=d["model"]["skim"],
        floor_z=floor_z,
        z_offset=z_offset,
        clip_disc=d["model"].get("z", "positioned") == "relief",
        keepout_radius=d["fixture"]["keepout_radius"],
        spoil_thickness=d.get("spoilboard", {}).get("thickness", 0.0),
        material=material,
        machine=machine,
    )
    job.tools = parse_tools(d, machine)
    job.ops = list(d["op"])
    for op in job.ops:
        if op["tool"] not in job.tools:
            raise ValueError(f"op {op.get('label', op['kind'])} uses undefined tool {op['tool']}")
    return job
