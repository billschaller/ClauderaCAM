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

DOUBLE-SIDED (`[pcb]` + `[twosided]`, PCB-PLAN WS3/WS8, boards/orbit/SPEC.md).
A board that flips needs the chain TWICE, so the phase tables go per side and
the document grows a pin block:

  [phases.front.iso] ... side A's own depths, feeds, dose, clearance
  [phases.back.iso]  ... side B's own, independently
  [pins]             ... the shipped pins law, PCB numbers filled in

  side A = FRONT  phases 1-5, then ALL through-holes, then the PIN BLOCK
  ---- the operator sets the pins, flips the blank, re-tapes, re-levels ----
  side B = BACK   phases 1-5, then the CUTOUT with tabs

SIDE A = FRONT is orbit SPEC.md's decision, with physical reasons: the
drill's exit burr lands on the back, which is deburred and machined
afterwards anyway (a burr on the reflow side would be baked under paste);
the back is the last face machined and therefore never tape-mounted, so the
stencil/paste/hotplate side stays free of adhesive residue; and the front
(hand-soldered, robust) is the face that takes the tape.

Two consequences are grammar-level law, not conventions:
  * `cutout` belongs to side 2 ONLY. On side 1 the board must stay attached
    (the same law twosided.py enforces on the coin: "front side must not cut
    out"), and the tabs only exist once.
  * `drills` belongs to side 1 ONLY. Every through-hole is bored from the
    front in side A's setup, so both artworks reference the same physical
    holes and flip accuracy equals pin-to-hole clearance. Boring the same
    hole twice from two frames is how a via becomes a slot.

The pin block itself is DERIVED from `[pins]` and never written by hand —
the coin lane's rule, restated here for the same reason (twosided.py: "pin
ops are generated from [pins]").
"""
from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..emit import LASER_DOSE_DEFAULT, LASER_FEED_DEFAULT, LASER_S_MAX
from ..job import Tool, parse_tools, resolve_machine, resolve_material
from ..twosided import PIN_CLEAR, flip_xy
from . import boardmaps

# the operator's revised chain; grammar and generators iterate THIS, never
# a config-supplied order
PHASE_ORDER = ("iso", "clear", "mask", "silk", "scrub", "drills", "cutout")

# The two setups of a pin-and-flip board, in run order. Named the way
# twosided.py names the coin's two sides, so a viewer session, a program file
# and a report all say "front"/"back" and mean the same setup.
SIDE_ORDER = ("front", "back")

# Which of the chain's phases each side carries. `drills` on side 1 only and
# `cutout` on side 2 only are LAW (see the module docstring); the other five
# run twice with independent parameters.
SIDE_CHAIN = {
    "front": ("iso", "clear", "mask", "silk", "scrub", "drills"),
    "back": ("iso", "clear", "mask", "silk", "scrub", "cutout"),
}

# The registration-pin pseudo-phases: spot-face then peck, exactly the coin
# lane's two ops (ops/drill.py). They are DERIVED from [pins] — a config that
# writes [phases.front.pinspot] by hand is refused, the same way
# twosided.load refuses hand-written spotface/pindrill ops.
PIN_PHASES = ("pinspot", "pindrill")

# The canonical program split (the module docstring above is the law). It
# lives HERE because the split is a fact about the job, not about any one
# consumer: the gate (checks.py) iterates it, re-emission (reemit.py) writes
# its letters into program headers, and the viewer (session.py) keys its
# sessions by it — one definition, three readers.
PROGRAM_PHASES = {"mill": ("iso", "clear"), "silk": ("silk",),
                  "scrub": ("scrub",), "holes": ("drills", "cutout")}

# The same split, per side of a flipped board. The names are the single-sided
# names so every reader (header letters, session keys, run sheet) carries
# across unchanged; only the phase CONTENT differs, plus side A's fifth
# program. The pin block is its own program because it is its own tool, its
# own depth law (12mm into the spoilboard, which no board phase may reach)
# and the last thing that happens before the operator touches the blank.
SIDE_PROGRAMS = {
    "front": {"mill": ("iso", "clear"), "silk": ("silk",),
              "scrub": ("scrub",), "holes": ("drills",),
              "pins": PIN_PHASES},
    "back": {"mill": ("iso", "clear"), "silk": ("silk",),
             "scrub": ("scrub",), "holes": ("cutout",)},
}

# scrub preload band: Makera stock DOC -0.2; field-tuned -0.21 (2026-07-19,
# with deflated regions + full tape); -0.25 peeled traces on a bowed blank.
SCRUB_Z_MIN, SCRUB_Z_MAX = -0.25, -0.18

# Per-side artwork. A KiCad export names the copper face in the file, so the
# side a layer belongs to is read off the suffix and never guessed.
SIDE_SUFFIXES = {
    "front": {"cu": "-F_Cu.gbr", "mask": "-F_Mask.gbr",
              "silk": "-F_Silkscreen.gbr"},
    "back": {"cu": "-B_Cu.gbr", "mask": "-B_Mask.gbr",
             "silk": "-B_Silkscreen.gbr"},
}
# ONE outline and ONE hole schedule for the whole document: both side frames
# derive from this Edge.Cuts, and every hole is bored once (see the docstring).
SHARED_SUFFIXES = {"edge": "-Edge_Cuts.gbr"}
# The single-sided lane is the BACK-copper board it has always been.
GERBER_SUFFIXES = {**SIDE_SUFFIXES["back"], **SHARED_SUFFIXES}
PASTE_SUFFIX = "-B_Paste.gbr"     # one stencil, back side (orbit SPEC paste Δ)
DRILL_SUFFIX = ".drl"

# a [[rules.gauge]] position must land on a real hole this close (mm)
GAUGE_MATCH_TOL = 0.05

# ------------------------------------------------------ cutout tab PLACEMENT
# FlatCAM's geocutout reads `-gaps` as either a COUNT or a PLACEMENT STYLE. The
# pinned fork (tclCommands/TclCommandGeoCutout.py) guards with
#   str(gaps).lower() in ['none','lr','tb','2lr','2tb','4','8']
# and then subtracts one gap rectangle per named band in geo_init.
#
# WHY the grammar carries the styles, and not just int(): the tab-zone law
# (flip.tab_zone_checks — no copper within TAB_KEEPOUT of any tab, on BOTH
# faces, because a snapped tab that bridges copper tears it off the laminate)
# judges wherever the tabs ACTUALLY land. Tab placement is a manufacturing free
# variable, not a constant of the process. Board B "orbit" has a legal
# bottom-edge copper spine 0.5 from the edge: a bottom tab can never clear the
# keep-out there, while its left and right edges are bare. Steering the tabs to
# the clear edges is the correct physical response — the law is untouched and
# simply judges the placement the job declares. Coercing int() locked every
# board to all-four-sides, which left only one other way out of that board:
# relaxing TAB_KEEPOUT, which Article II forbids.
#
# token -> (tabs it cuts, where they land). The COUNT is what the tab census
# expects to find in the toolpath and what the operator note tells the human to
# snap; the WORDS are what that note says about where to reach. An empty
# placement means the plain count, whose note text predates this table and
# stays byte-identical (the coupon goldens ship `gaps = 4`).
TAB_PLACEMENTS = {
    "lr": (2, "left/right edges"),
    "tb": (2, "top/bottom edges"),
    "2lr": (4, "two each on the left/right edges"),
    "2tb": (4, "two each on the top/bottom edges"),
    "4": (4, ""),
    "8": (8, ""),
}

# 'none' is deliberately NOT in the table above. FlatCAM accepts it; it means
# no gap rectangles, i.e. the outline cut clean through — the freed board this
# grammar has always refused.
#
# Nor is any other integer. geo_init's branches are `gaps_u == 8`, `== 4`, and
# the four names: a bare 2, 3, 5, 6 ... passes FlatCAM's own guard for none of
# them, subtracts nothing, and severs the outline in SILENCE. Only 4 and 8 are
# counts the engine can actually cut, so only 4 and 8 are counts this grammar
# accepts.
TAB_TOKENS = tuple(TAB_PLACEMENTS)


def _tab_spec(gaps) -> tuple[str, int, str]:
    """(canonical token, tab count, placement words) for a configured `gaps`.

    Accepts an int (4 or 8) or one of TAB_PLACEMENTS' strings, case-
    insensitively — everything else refuses, because everything else is a
    cutout with no tabs in it (see the table above).
    """
    if isinstance(gaps, bool) or not isinstance(gaps, (int, str)):
        key = None
    else:
        key = str(gaps).strip().lower()
    if key not in TAB_PLACEMENTS:
        raise ValueError(
            f"phases.cutout gaps {gaps!r} is not a tab placement geocutout "
            f"can cut — use a count (4 or 8) or a placement "
            f"('lr', 'tb', '2lr', '2tb'); any other value subtracts no gap "
            f"rectangle at all and the outline is cut clean through — a "
            f"freed board grabs the cutter")
    n, where = TAB_PLACEMENTS[key]
    return key, n, where


def tab_count(gaps) -> int:
    """How many tabs `gaps` leaves: what the census counts and the human
    snaps."""
    return _tab_spec(gaps)[1]


def tab_where(gaps) -> str:
    """Where those tabs land, in operator words — "" for a plain count."""
    return _tab_spec(gaps)[2]


def geocutout_gaps(gaps) -> str:
    """The `-gaps` word for FlatCAM's geocutout — UPPERCASE for the styles.

    Case is load-bearing and the pinned fork does not say so: its guard tests
    `str(gaps).lower()` against the accepted set, but geo_init then compares
    the raw value against 'LR' / 'TB' / '2LR' / '2TB'. A lowercase `-gaps lr`
    therefore CLEARS validation, matches no branch, subtracts no rectangle, and
    writes a cutout with zero tabs — silently, which is the worst kind. The
    token is normalized here, in the one place that renders the script, so the
    emitted line can only mean what the job declared.
    """
    return _tab_spec(gaps)[0].upper()


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
    # --- double-sided (empty/one-sided defaults keep every existing reader) --
    sides: tuple[str, ...] = ()     # () = single-sided document
    side: str = ""                  # non-empty only on a SIDE VIEW
    mirror: str = "x"               # boardmaps.machine_offset's flag
    side_phases: dict[str, dict] = field(default_factory=dict)
    pins: dict = field(default_factory=dict)
    flip_axis: str = "y"
    rules: dict = field(default_factory=dict)

    @property
    def twosided(self) -> bool:
        return bool(self.sides)

    def tool(self, num: int) -> Tool:
        return self.tools[num]

    def phase_tool(self, phase: str) -> Tool:
        return self.tools[self.phases[phase]["tool"]]

    def has_phase(self, phase: str) -> bool:
        return bool(self.phases.get(phase))


def programs_of(job: PcbJob) -> dict[str, tuple[str, ...]]:
    """The program split THIS job (or side view) is made of. One function so
    the gate, the re-emitter and the viewer never disagree about how many
    programs a document has or what is in them."""
    if job.side:
        return SIDE_PROGRAMS[job.side]
    return PROGRAM_PHASES


def program_stem(job: PcbJob, name: str) -> str:
    """The file stem of one program. `<job>-<program>` for a single-sided
    document; a side view's own name already carries the side (twosided.py's
    `f"{name}-{side}"`), so the same rule yields `orbit-front-mill` — one
    convention, and the sides sort next to each other."""
    return f"{job.name}-{name}"


def iso_pass_plan(job: PcbJob) -> tuple[int, float, float]:
    """(passes, overlap_percent, top_offset_mm) of the multi-pass isolation —
    ONE definition, two readers: engine.render_tcl emits it, checks.iso_checks
    judges against it (the bridging-sliver incident, 2026-07-30: single-pass
    isolation clears a gap only up to ~2*(tip/2 + kerf/2) ≈ 0.46mm with the
    0.2 vee, the clearing tool cannot enter anything narrower than itself
    plus margin, and the copper in between belonged to no phase).

    The ladder: FlatCAM's GerberObject.isolate puts pass n's centerline at
    dia*(0.5 + n*(1 - overlap)) off the copper edge. Counting only the TIP
    width as cut (the vee cone's flare with depth is margin, not a
    dependency), 50% overlap steps the rungs by tip/2 — the widest spacing
    at which consecutive rungs' cuts stay contiguous — and a gap g is fully
    cleared when some rung sits within tip/2 below g/2, i.e. when
    tip*(passes + 1) >= g. The ladder must reach G, the narrowest channel
    the CLEARING phase guarantees (its tool + its own offset each side +
    the castellation real margin), so passes = ceil(G/tip - 1). In gaps too
    narrow for a rung the opposing buffers merge and FlatCAM emits no pass
    there — multi-pass never gouges the far side.
    """
    tip = job.phase_tool("iso").tip_diameter
    clear_t = job.phase_tool("clear")
    G = clear_t.diameter + 2 * float(job.phases["clear"]["offset"]) + 0.2
    passes = max(1, math.ceil(G / tip - 1 - 1e-9))
    return passes, 50.0, tip * (0.5 + 0.5 * (passes - 1))


def side_view(job: PcbJob, side: str) -> PcbJob:
    """One setup of a double-sided document, shaped like a single-sided job.

    This is twosided.py's move: rather than teach every consumer about sides,
    hand each of them a job whose `phases`, `files` and `mirror` are that
    side's — so board_maps, sheet_stock, render_tcl, read_phase, the gate and
    the run sheet all work unchanged. The identity carries the side the way
    the coin lane's does (`path#side`, `name-side`), because the viewer keys
    sessions on job.path and the two setups are two documents to a watcher.
    """
    if side not in job.sides:
        raise ValueError(f"{job.name} has no side {side!r} — sides are "
                         f"{list(job.sides)}")
    files = {k: job.files[f"{side}_{k}"] for k in SIDE_SUFFIXES[side]}
    files["edge"] = job.files["edge"]
    files["drl"] = job.files["drl"]
    if "paste" in job.files:
        files["paste"] = job.files["paste"]
    phases = dict(job.side_phases[side])
    if side == SIDE_ORDER[0]:
        # the pin block rides side A: derived from [pins], between the board
        # holes and the flip (PCB-PLAN WS3 sequence, orbit SPEC step 3)
        phases.update(pin_phase_tables(job))
    return replace(
        job,
        path=job.path.with_name(job.path.name + f"#{side}"),
        name=f"{job.name}-{side}",
        files=files,
        phases=phases,
        side=side,
        # F.Cu machined front-up needs no mirror; B.Cu machined back-up does
        # (boardmaps.machine_offset's law). This one line is the whole
        # difference between the two frames.
        mirror="none" if side == "front" else "x",
    )


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
    twoside = "twosided" in d
    files: dict[str, Path] = {}

    def artwork(key: str, suf: str, why: str) -> None:
        f = gdir / f"{p['stem']}{suf}"
        if not f.is_file():
            raise ValueError(f"missing gerber {f} — {why}")
        files[key] = f

    if twoside:
        for side in SIDE_ORDER:
            for key, suf in SIDE_SUFFIXES[side].items():
                artwork(f"{side}_{key}", suf,
                        "a double-sided board is masked, lasered and "
                        "scrubbed on BOTH faces (orbit SPEC.md), so all six "
                        "copper/mask/silk layers must be exported")
        for key, suf in SHARED_SUFFIXES.items():
            artwork(key, suf, "one outline for the whole document — both "
                              "side frames derive from it")
        artwork("paste", PASTE_SUFFIX,
                "one stencil, back side (orbit SPEC.md paste rule) — the "
                "paste layer is a first-class output and the gate checks it "
                "against the hole schedule")
    else:
        for key, suf in GERBER_SUFFIXES.items():
            artwork(key, suf, "export all four layers (B.Cu, B.Mask, "
                              "B.Silkscreen, Edge.Cuts)")
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
    sides: tuple[str, ...] = ()
    side_phases: dict[str, dict] = {}
    if twoside:
        sides = SIDE_ORDER
        extra = set(phases_d) - set(SIDE_ORDER)
        if extra:
            raise ValueError(
                f"[phases] carries {sorted(extra)} but this is a "
                f"[twosided] document — a flipped board needs the chain "
                f"TWICE, so its phase tables are [phases.front.<phase>] and "
                f"[phases.back.<phase>] (each side has its own depths, its "
                f"own feeds and its own dose)")
        for side in SIDE_ORDER:
            if side not in phases_d:
                raise ValueError(
                    f"[phases.{side}] is missing — both setups of a flipped "
                    f"board are machine work and neither is inferred from "
                    f"the other")
            side_phases[side] = _side_table(phases_d[side], side)
        phases = {}
    else:
        phases = _side_table(phases_d, None)

    ts = dict(d.get("twosided") or {})
    flip_axis = ts.get("flip_axis", "y")
    if twoside and flip_axis not in ("x", "y"):
        raise ValueError(f"flip_axis must be 'x' or 'y', got {flip_axis!r}")

    job = PcbJob(
        path=path, name=p["name"], stem=p["stem"],
        gerber_dir=gdir, out_dir=(base / p["out"]).resolve(), files=files,
        blank_w=float(blank["width"]), blank_h=float(blank["height"]),
        thickness=float(blank["thickness"]),
        anchor=(float(blank["anchor"][0]), float(blank["anchor"][1])),
        spoil_thickness=float(spoil),
        material=material, machine=machine, tools=tools, phases=phases,
        sides=sides, side_phases=side_phases, flip_axis=flip_axis,
        pins=dict(d.get("pins") or {}), rules=dict(d.get("rules") or {}))
    if twoside:
        _validate_twosided(job)
        for side in SIDE_ORDER:
            _validate_phases(side_view(job, side))
    else:
        _validate_phases(job)
    return job


def _side_table(phases_d: dict, side: str | None) -> dict[str, dict]:
    """One side's (or a single-sided job's) phase table, order-checked.

    `side=None` is the single-sided chain: all seven phases. A side of a
    flipped board carries SIDE_CHAIN[side] — and naming a phase that belongs
    to the other setup is refused by name, because a second cutout or a
    second drilling pass is a different (and destructive) process, not a
    configuration preference."""
    chain = PHASE_ORDER if side is None else SIDE_CHAIN[side]
    where = "phases" if side is None else f"phases.{side}"
    extra = set(phases_d) - set(chain)
    if extra:
        wrong = sorted(extra & set(PHASE_ORDER))
        pins = sorted(extra & set(PIN_PHASES))
        if pins:
            raise ValueError(
                f"[{where}] carries {pins} — the pin block is DERIVED from "
                f"[pins] (spot-face then peck, the coin lane's law); do not "
                f"write pin phases by hand")
        if wrong and side is not None:
            other = [s for s in SIDE_ORDER if s != side][0]
            raise ValueError(
                f"[{where}] carries {wrong}, which belongs to the {other} "
                f"setup only: every through-hole is bored once from side A "
                f"(so both artworks reference the same physical holes) and "
                f"the cutout runs on side 2 (the board must stay attached "
                f"until its tabs exist)")
        raise ValueError(f"unknown phases {sorted(extra)} — the chain is "
                         f"{chain} and its order is law")
    missing = [ph for ph in chain if ph not in phases_d
               and ph != "mask"]        # mask is an operator step; params
    #                                     optional (notes only)
    if missing:
        raise ValueError(f"[{where}] missing {missing} — a partial chain is "
                         f"a different process; every machine phase must "
                         f"be configured (or explicitly absent in a future "
                         f"grammar rev, not silently skipped)")
    return {ph: dict(phases_d.get(ph, {})) for ph in chain}


def _validate_phases(j: PcbJob) -> None:
    t = j.thickness

    def need(ph: str, keys: tuple[str, ...]):
        _require(j.phases[ph], keys, f"phases.{ph}")

    need("iso", ("tool", "depth", "feed", "plunge"))
    need("clear", ("tool", "depth", "margin", "offset", "overlap",
                   "feed", "plunge"))
    need("silk", ("clearance",))
    need("scrub", ("tool", "depth", "overlap", "offset", "feed", "plunge"))
    if j.has_phase("drills"):
        need("drills", ("tool", "depth", "dpp", "feed", "plunge"))
    if j.has_phase("cutout"):
        need("cutout", ("tool", "depth", "dpp", "gaps", "gapsize",
                        "feed", "plunge"))

    kinds = {"iso": "vee", "clear": "flat", "scrub": "scrub",
             "drills": "flat", "cutout": "flat"}
    for ph, kind in kinds.items():
        if not j.has_phase(ph):
            continue
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
        if not j.has_phase(ph):
            continue
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
    if j.has_phase("cutout"):
        cut = j.phases["cutout"]
        # _tab_spec is the refusal: it accepts only the placements geocutout
        # can actually cut, and every one of them leaves >= 2 tabs
        if tab_count(cut["gaps"]) < 2 or cut["gapsize"] < 1.0:
            raise ValueError(
                "phases.cutout needs >= 2 tabs of >= 1.0 — a freed board "
                "grabs the cutter")


# ------------------------------------------------------ the flip and its pins
def _validate_twosided(j: PcbJob) -> None:
    """The pins law, PCB numbers filled in (DESIGN.md 2026-07-28/29,
    boards/orbit/SPEC.md "Pin-and-flip registration").

    Every refusal below is the coin lane's, restated rather than called: the
    shipped `engine.check_job_plan` is welded to a Job's disc geometry (model
    radius, fixture keep-out, op list) and a PCB has none of those, so the
    RULES cross over and the shapes do not. Numbers are identical on purpose —
    2mm bed clearance, the counterbore reach credit, PIN_CLEAR.
    """
    pins = j.pins
    if not pins:
        raise ValueError(
            "[pins] is required for a [twosided] pcb job — the flip is only "
            "as good as its registration, and the pin geometry is what the "
            "gate checks it against")
    _require(pins, ("diameter", "length", "positions", "spot_tool",
                    "drill_tool"), "pins")
    pos = [(float(x), float(y)) for x, y in pins["positions"]]
    if len(pos) < 2:
        raise ValueError("two pins minimum: one pin leaves rotation free")

    # symmetry about the BOARD's own mirror line, derived from the one
    # Edge.Cuts — not about the machine origin (the coin's frame). Without
    # gerbv: extents() reads coordinate words, the raster cross-check is the
    # gate's job later.
    win = boardmaps.extents(j.files["edge"], cross_check=False)
    line = boardmaps.flip_line(win, j.anchor)
    flipped = {(round(fx, 3), round(fy, 3))
               for fx, fy in (flip_xy(x, y, j.flip_axis, line)
                              for x, y in pos)}
    if flipped != {(round(x, 3), round(y, 3)) for x, y in pos}:
        raise ValueError(
            f"pin positions {pos} are not symmetric under a flip about the "
            f"{j.flip_axis} axis through the board's mirror line "
            f"x={line:.3f} (derived from Edge.Cuts + the anchor) — the "
            f"flipped blank would not land in its own holes")

    drill = j.tool(pins["drill_tool"])
    spot = j.tool(pins["spot_tool"])
    if drill.type != "drill":
        raise ValueError(
            f"pins.drill_tool T{drill.num} is a {drill.type} — pecking a "
            f"{pins['length']}mm hole is a twist drill's job")
    if spot.diameter + 1e-9 < drill.shank_diameter:
        raise ValueError(
            f"pins.spot_tool T{spot.num} d{spot.diameter:g} is narrower than "
            f"the drill's {drill.shank_diameter} shank — the counterbore "
            f"cannot buy the drill any reach it does not already have")
    for knob, default in (("seat_extra", 0.2), ("tip_allowance", 0.6)):
        if float(pins.get(knob, default)) < 0:
            raise ValueError(
                f"pins.{knob} {pins[knob]} is negative — the hole can only "
                f"be drilled deeper than the pin, never shallower; declare 0 "
                f"and accept the pin seating on the drill cone instead "
                f"(orbit decision Q11)")
    depth = pin_depth(j)
    if depth <= j.thickness:
        raise ValueError(
            f"pin depth {depth} does not pass through the {j.thickness} "
            f"blank — a blind hole cannot register a flip")
    if depth > j.thickness + j.spoil_thickness - 2.0:
        raise ValueError(
            f"pin depth {depth} comes within 2mm of the machine bed (blank "
            f"{j.thickness} + spoilboard {j.spoil_thickness})")
    spot_depth = float(pins.get("spot_depth", 0.1))
    if depth > drill.flute_length + spot_depth:
        raise ValueError(
            f"pin depth {depth} exceeds T{drill.num}'s {drill.flute_length}mm "
            f"reach plus the {spot_depth}mm counterbore — the shank would "
            f"enter the hole")

    # the pins must fit in the blank the job declares. The blank's POSITION
    # is not in the grammar (a single-sided job never needed one), so this is
    # a span test, not a containment test — and the program header carries the
    # operator's own confirmation. Stated as the gap it is.
    pin_r = float(pins["diameter"]) / 2
    for axis, n, size in ((0, "width", j.blank_w), (1, "height", j.blank_h)):
        span = max(pt[axis] for pt in pos) - min(pt[axis] for pt in pos) \
            + 2 * (pin_r + PIN_CLEAR)
        if span > size:
            raise ValueError(
                f"the pin span in {n} is {span:.1f}mm but the blank is only "
                f"{size}mm — the pins do not fit in this blank")

    # nothing is machined over a pin: the coin lane's keep-out, on the PCB's
    # rectangular envelope instead of its disc. The envelope is the board box
    # grown by the widest off-board reach of either setup (the clear phase's
    # rim margin, the cutout's outside ride).
    reach = 0.0
    for phases in j.side_phases.values():
        reach = max(reach, float(phases["clear"]["margin"]))
        if phases.get("cutout"):
            reach = max(reach, j.tool(phases["cutout"]["tool"]).diameter)
    ax, ay = j.anchor
    bx0, by0 = ax - reach, ay - reach
    bx1, by1 = ax + win.w_mm + reach, ay + win.h_mm + reach
    keep = pin_r + PIN_CLEAR
    for x, y in pos:
        inx = bx0 - keep < x < bx1 + keep
        iny = by0 - keep < y < by1 + keep
        if inx and iny:
            raise ValueError(
                f"pin at ({x},{y}) is inside the machined envelope "
                f"({bx0:.1f},{by0:.1f})..({bx1:.1f},{by1:.1f}) grown by "
                f"{keep}mm — the pins are steel and the finished board must "
                f"carry no pin holes; move them into the blank's waste")

    if float(j.rules.get("annular", 0.0)) <= 0:
        raise ValueError(
            "[rules] annular is required for a double-sided board — every "
            "hole-centred pad must be solderable on BOTH faces, and the gate "
            "will not invent the ring width this board was designed to "
            "(orbit SPEC.md sets 0.7; its four flip gauges are the named "
            "exception, declared as [[rules.gauge]])")
    gauges = j.rules.get("gauge", [])
    if gauges:
        # Gauge positions are in the GERBER frame (they name pads in the
        # artwork, which is where the designer reads them off) — unlike
        # [pins].positions, which are machine-frame because a pin is not on
        # the board. A frame mix-up would silently exempt nothing, so every
        # declared gauge must land on a real hole in the schedule.
        holes = boardmaps.excellon(j.files["drl"])
        for g in gauges:
            _require(g, ("name", "annular", "positions"), "rules.gauge")
            if not g.get("reason"):
                raise ValueError(
                    f"gauge {g['name']!r} has no `reason` — an exception to "
                    f"the annular rule is a decision, and an undocumented "
                    f"decision is indistinguishable from a mistake")
            for gx, gy in g["positions"]:
                if not any(abs(hx - float(gx)) <= GAUGE_MATCH_TOL
                           and abs(hy - float(gy)) <= GAUGE_MATCH_TOL
                           for hx, hy, _ in holes):
                    raise ValueError(
                        f"gauge {g['name']!r} names ({gx},{gy}) but no hole "
                        f"in {j.files['drl'].name} is there — a named "
                        f"annular exception that matches no hole exempts "
                        f"nothing (gauge positions are in the GERBER frame, "
                        f"like the Excellon; [pins] positions are machine "
                        f"frame)")


def pin_depth(j: PcbJob) -> float:
    """Pin hole depth below Z0, the coin lane's formula: pin length + the
    seat that keeps the pin from standing proud + the drill-tip allowance
    (the simulated hole is flat-bottomed; the real point cone needs this
    much extra to give the pin its full-diameter depth).

    Both terms are DECLARABLE (seat_extra default 0.2, tip_allowance default
    0.6; negative refuses in _validate_twosided). Provenance: orbit decision
    Q11 (2026-07-31) — a Ø2x12 dowel on a 1.5 blank over 12.7 spoilboard
    derives 12.8 with the defaults, but the bed allows only 12.2; the job
    declares both 0 for a 12.0 hole and accepts the pin seating on the real
    tip cone (~0.6 proud of the blank, harmless: the pin keep-out excludes
    every machining path). A declared depth that still reaches the bed is
    refused by the same bed check as before — the knob buys honesty, not
    reach."""
    p = j.pins
    return float(p["length"]) + float(p.get("seat_extra", 0.2)) \
        + float(p.get("tip_allowance", 0.6))


def pin_phase_tables(j: PcbJob) -> dict[str, dict]:
    """The two pin pseudo-phases, DERIVED from [pins].

    Depths are signed like every other pcb phase (a Z floor, negative down)
    so the echo checks read them the same way. `plunge` repeats `feed`
    because these ops have exactly one feed word — the coin lane's
    spotface/pindrill emit `G1 Z.. F<feed>` and nothing else — and the echo
    check compares against the SET {feed, plunge}."""
    p = j.pins
    spot_feed = float(p.get("spot_feed", 100))
    feed = float(p.get("feed", 120))
    pos = [[float(x), float(y)] for x, y in p["positions"]]
    return {
        "pinspot": {"tool": p["spot_tool"], "positions": pos,
                    "depth": -float(p.get("spot_depth", 0.1)),
                    "feed": spot_feed, "plunge": spot_feed},
        "pindrill": {"tool": p["drill_tool"], "positions": pos,
                     "depth": -pin_depth(j), "peck": float(p.get("peck", 0.8)),
                     "feed": feed, "plunge": feed},
    }
