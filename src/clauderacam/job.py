"""Job configuration: one TOML file fully parameterizes a job.

Requirement 5 (see DESIGN.md): shrinking the mango coin required editing three
generators, the verifier, and hand-splicing four .nc sections. Here every
number lives in the job file and everything downstream derives from it.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Tool:
    num: int
    type: str          # "flat" | "ball"
    diameter: float
    rpm: int

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
    tools: dict[int, Tool] = field(default_factory=dict)
    ops: list[dict] = field(default_factory=list)

    @property
    def stock_half(self) -> float:
        return self.stock_size / 2.0

    def tool(self, num: int) -> Tool:
        return self.tools[num]


def load(path: str | Path) -> Job:
    path = Path(path).resolve()
    with open(path, "rb") as f:
        d = tomllib.load(f)
    base = path.parent
    job = Job(
        path=path,
        name=d["job"]["name"],
        stl=(base / d["job"]["stl"]).resolve(),
        out=(base / d["job"]["out"]).resolve(),
        stock_size=d["stock"]["size"],
        stock_thickness=d["stock"]["thickness"],
        model_radius=d["model"]["radius"],
        skim=d["model"]["skim"],
        floor_z=d["model"]["floor_z"],
        keepout_radius=d["fixture"]["keepout_radius"],
    )
    for t in d["tool"]:
        job.tools[t["num"]] = Tool(t["num"], t["type"], t["diameter"], t["rpm"])
    job.ops = list(d["op"])
    for op in job.ops:
        if op["tool"] not in job.tools:
            raise ValueError(f"op {op.get('label', op['kind'])} uses undefined tool {op['tool']}")
    return job
