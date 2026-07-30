"""[pcb] job grammar: one TOML fully parameterizes the six-phase chain
(PCB-PLAN.md WS3). The phase ORDER is law — the operator's revised chain
from the coin-era bench notes:

  1 iso (vee)  → 2 clear (corn) → 3 mask (OPERATOR: squeegee + cure)
  → 4 silk (laser) → 5 scrub (spring tool) → 6 drills + edge cut

and the program split follows the operator steps between machine work:

  program A "mill"  = iso + clear          (before the mask goes on)
  operator          = squeegee white/green mask, UV cure, IPA prep
  program B "silk"  = laser legend         (own dialect, emit.assemble_laser)
  program C "scrub" = spring tool pad clear
  program D "holes" = milled drills + edge cut with tabs

Tools flow through the SAME crib gate as every job (Article XI —
parse_tools/inventory.match); material and machine through the same
physics tables. Phase parameters are validated here at load: a depth
through the blank without a spoilboard, a scrub outside its narrow
preload band, a silk dose beyond the ceiling — all refuse before any
geometry is generated.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..emit import LASER_DOSE_DEFAULT, LASER_FEED_DEFAULT, LASER_S_MAX
from ..job import Tool, parse_tools, resolve_machine, resolve_material

# the operator's revised chain; grammar and generators iterate THIS, never
# a config-supplied order
PHASE_ORDER = ("iso", "clear", "mask", "silk", "scrub", "drills", "cutout")

# scrub preload band: Makera stock DOC -0.2; field-tuned -0.21 (2026-07-19,
# with deflated regions + full tape); -0.25 peeled traces on a bowed blank.
SCRUB_Z_MIN, SCRUB_Z_MAX = -0.25, -0.18

GERBER_SUFFIXES = {
    "cu": "-B_Cu.gbr",
    "mask": "-B_Mask.gbr",
    "silk": "-B_Silkscreen.gbr",
    "edge": "-Edge_Cuts.gbr",
}
DRILL_SUFFIX = ".drl"


@dataclass
class PcbJob:
    path: Path
    name: str
    stem: str
    gerber_dir: Path
    out_dir: Path
    files: dict[str, Path]          # cu/mask/silk/edge/drl -> existing file
    blank_w: float
    blank_h: float
    thickness: float
    anchor: tuple[float, float]     # machine-frame LL of the BOARD
    spoil_thickness: float
    material: dict = field(default_factory=dict)
    machine: dict = field(default_factory=dict)
    tools: dict[int, Tool] = field(default_factory=dict)
    phases: dict[str, dict] = field(default_factory=dict)

    def tool(self, num: int) -> Tool:
        return self.tools[num]

    def phase_tool(self, phase: str) -> Tool:
        return self.tools[self.phases[phase]["tool"]]


def _require(d: dict, keys: tuple[str, ...], where: str):
    for k in keys:
        if k not in d:
            raise ValueError(f"[{where}] is missing {k!r}")


def load(path: str | Path) -> PcbJob:
    path = Path(path).resolve()
    with open(path, "rb") as f:
        d = tomllib.load(f)
    base = path.parent
    if "pcb" not in d:
        raise ValueError("not a [pcb] job")
    p = d["pcb"]
    _require(p, ("name", "stem", "gerbers", "out"), "pcb")
    blank = d.get("blank") or {}
    _require(blank, ("width", "height", "thickness", "anchor"), "blank")
    spoil = d.get("spoilboard", {}).get("thickness", 0.0)
    if spoil <= 0:
        raise ValueError(
            "[spoilboard] thickness is required for a pcb job — drills and "
            "the edge cut break through the blank and must have somewhere "
            "safe to go that is not the machine bed")

    gdir = (base / p["gerbers"]).resolve()
    files: dict[str, Path] = {}
    for key, suf in GERBER_SUFFIXES.items():
        f = gdir / f"{p['stem']}{suf}"
        if not f.is_file():
            raise ValueError(f"missing gerber {f} — export all four layers "
                             f"(B.Cu, B.Mask, B.Silkscreen, Edge.Cuts)")
        files[key] = f
    drl = gdir / f"{p['stem']}{DRILL_SUFFIX}"
    if not drl.is_file():
        raise ValueError(f"missing excellon {drl}")
    files["drl"] = drl

    material = resolve_material(d)
    if material["name"] not in ("fr4", "fr1"):
        raise ValueError(
            f"pcb jobs cut copper-clad board — material "
            f"{material['name']!r} has no place here")
    machine = resolve_machine(d)
    tools = parse_tools(d, machine, base)

    phases_d = d.get("phases")
    if not phases_d:
        raise ValueError("[phases] is required — the six-phase chain is "
                         "the job")
    extra = set(phases_d) - set(PHASE_ORDER)
    if extra:
        raise ValueError(f"unknown phases {sorted(extra)} — the chain is "
                         f"{PHASE_ORDER} and its order is law")
    missing = [ph for ph in PHASE_ORDER if ph not in phases_d
               and ph != "mask"]        # mask is an operator step; params
    #                                     optional (notes only)
    if missing:
        raise ValueError(f"[phases] missing {missing} — a partial chain is "
                         f"a different process; every machine phase must "
                         f"be configured (or explicitly absent in a future "
                         f"grammar rev, not silently skipped)")
    phases: dict[str, dict] = {ph: dict(phases_d.get(ph, {}))
                               for ph in PHASE_ORDER}

    job = PcbJob(
        path=path, name=p["name"], stem=p["stem"],
        gerber_dir=gdir, out_dir=(base / p["out"]).resolve(), files=files,
        blank_w=float(blank["width"]), blank_h=float(blank["height"]),
        thickness=float(blank["thickness"]),
        anchor=(float(blank["anchor"][0]), float(blank["anchor"][1])),
        spoil_thickness=float(spoil),
        material=material, machine=machine, tools=tools, phases=phases)
    _validate_phases(job)
    return job


def _validate_phases(j: PcbJob) -> None:
    t = j.thickness

    def need(ph: str, keys: tuple[str, ...]):
        _require(j.phases[ph], keys, f"phases.{ph}")

    need("iso", ("tool", "depth", "feed", "plunge"))
    need("clear", ("tool", "depth", "margin", "offset", "overlap",
                   "feed", "plunge"))
    need("silk", ("clearance",))
    need("scrub", ("tool", "depth", "overlap", "offset", "feed", "plunge"))
    need("drills", ("tool", "depth", "dpp", "feed", "plunge"))
    need("cutout", ("tool", "depth", "dpp", "gaps", "gapsize",
                    "feed", "plunge"))

    kinds = {"iso": "vee", "clear": "flat", "scrub": "scrub",
             "drills": "flat", "cutout": "flat"}
    for ph, kind in kinds.items():
        tool = j.phase_tool(ph)
        if tool.type != kind:
            raise ValueError(
                f"phases.{ph} needs a {kind} tool, got T{tool.num} "
                f"({tool.type}) — the phase physics assume the tool class")

    for ph in ("iso", "clear"):
        z = j.phases[ph]["depth"]
        if not (-0.5 <= z < 0):
            raise ValueError(
                f"phases.{ph} depth {z} outside (-0.5, 0) — surface phases "
                f"cut copper plus bow margin, never into the board")
    zs = j.phases["scrub"]["depth"]
    if not (SCRUB_Z_MIN <= zs <= SCRUB_Z_MAX):
        raise ValueError(
            f"phases.scrub depth {zs} outside [{SCRUB_Z_MIN}, "
            f"{SCRUB_Z_MAX}] — the spring preload band is law: -0.25 "
            f"peeled traces off a bowed blank, shallower than -0.18 "
            f"leaves film (field record 2026-07-19)")
    for ph in ("drills", "cutout"):
        z = j.phases[ph]["depth"]
        if z > -t:
            raise ValueError(
                f"phases.{ph} depth {z} does not break through the "
                f"{t} blank")
        if z < -(t + j.spoil_thickness - 2.0):
            raise ValueError(
                f"phases.{ph} depth {z} runs within 2mm of the machine "
                f"bed under a {j.spoil_thickness} spoilboard")
        if z < -(t + 0.5):
            raise ValueError(
                f"phases.{ph} depth {z} more than 0.5 into the "
                f"spoilboard — breakthrough is 0.2, not excavation")
    silk = j.phases["silk"]
    dose = silk.get("dose", LASER_DOSE_DEFAULT)
    if not (0 < dose <= LASER_S_MAX):
        raise ValueError(f"phases.silk dose {dose} outside "
                         f"(0, {LASER_S_MAX}]")
    silk.setdefault("dose", LASER_DOSE_DEFAULT)
    silk.setdefault("feed", LASER_FEED_DEFAULT)
    if silk["clearance"] < 0.3:
        raise ValueError(
            "phases.silk clearance below 0.3 — cured white on a pad "
            "repels solder (mask guide §5)")
    cut = j.phases["cutout"]
    if cut["gaps"] < 2 or cut["gapsize"] < 1.0:
        raise ValueError(
            "phases.cutout needs >= 2 tabs of >= 1.0 — a freed board "
            "grabs the cutter")
