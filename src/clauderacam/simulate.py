"""Sequential stock simulation — the physical ground truth for verification
and previews.

Structure (since the physics layer):
  parse_line()  strict G-code parsing — Article I: the gate refuses what it
                cannot model (unknown tools, arcs in any spelling, G-less
                modal lines, junk words are all fatal)
  prep_moves()  resolves modal state (position, tool, feed, rpm) into flat
                per-move arrays
  kernel        the measurement loop (kernel.py dispatches to the Rust
                extension when built, else the pure-Python reference in
                kernel_py.py — both must agree, see tests/kernel_parity.py):
                carves the stock and measures, per move: removed volume,
                max footprint contact vs the MOVE-START snapshot, engagement
                fraction, shank clearance; plus rapid-vs-stock and depth.

TRUE ENGAGEMENT: contact is the max over the tool footprint of
(stock − cutting surface) against a move-start snapshot — per-sample
pre-carve measurement is not enough because a plunge carves its own
footprint incrementally. The pre-2026-07-28 center-column metric reported
its own ~0.06mm sampling step on plunges and could essentially never fail.

Coordinate convention (requirement 4): carve maps world->pixel via
i = round((half - y) * ppm), j = round((x + half) * ppm). Any analysis mask
MUST invert with x = j/ppm - half, y = half - i/ppm — never re-derive
centers from (n-1)/2.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from . import kernel
from .job import Job

SAFE_Z = 3.0

WORD = re.compile(r"([A-Za-z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")
COMMENT = re.compile(r"\([^)]*\)|;.*")
# Stage markers: emit.assemble() writes one before every operation's moves.
# The stage model is recovered from these — from the program BYTES, never the
# job config (Article VI: previews describe the file the machine will run).
OP_MARK = re.compile(r"\(begin operation: (.+) T(\d+) (\S+) d([0-9.]+)\)")
# Modeled or provably motion-free words. Everything else is fatal.
IGNORED_G = {4, 17, 21, 28, 54, 90, 94}   # dwell, XY plane, mm, park, WCS1, abs, feed/min
IGNORED_M = {0, 1, 2, 3, 5, 7, 8, 9, 30}  # stops, spindle, coolant, end


class GcodeError(ValueError):
    """Raised when a file contains anything the simulator cannot fully model.
    Fatal by design: an unmodeled move is an unverified move."""


def parse_line(line: str, lineno: int):
    """-> ('none',) | ('spindle', rpm) | ('tool', n)
        | ('move', 0|1, {axis: value}, feed|None, rpm|None)"""
    body = COMMENT.sub(" ", line).strip()
    if not body:
        return ("none",)
    words = WORD.findall(body)
    if WORD.sub("", body).strip():
        raise GcodeError(
            f"line {lineno}: unparseable text in {line.strip()!r} — "
            f"the gate refuses what it cannot model")
    motion = None
    coords: dict[str, float] = {}
    m6 = False
    tsel = None
    fword = None
    sword = None
    for L, V in words:
        L = L.upper()
        v = float(V)
        if L == "G":
            g = int(round(v))
            if abs(v - g) > 1e-9:
                raise GcodeError(f"line {lineno}: non-integer word G{V}")
            if g in (0, 1):
                if motion is not None and motion != g:
                    raise GcodeError(f"line {lineno}: conflicting motion words")
                motion = g
            elif g in IGNORED_G:
                pass
            else:
                raise GcodeError(
                    f"line {lineno}: unsupported G{g} in {line.strip()!r} — "
                    f"arcs, cutter comp and machine-coordinate moves cannot "
                    f"be simulated")
        elif L == "M":
            m = int(round(v))
            if m == 6:
                m6 = True
            elif m not in IGNORED_M:
                raise GcodeError(f"line {lineno}: unsupported M{m}")
        elif L == "T":
            tsel = int(round(v))
        elif L == "F":
            fword = v
        elif L == "S":
            sword = v
        elif L in "XYZ":
            coords[L] = v
        elif L == "P":
            pass
        else:
            raise GcodeError(
                f"line {lineno}: unsupported word {L}{V} "
                f"(I/J/K/R arc parameters etc. cannot be simulated)")
    if m6:
        if tsel is None:
            raise GcodeError(f"line {lineno}: M6 without a T word")
        if motion is not None or coords:
            raise GcodeError(f"line {lineno}: motion mixed with tool change")
        return ("tool", tsel)
    if coords and motion is None:
        raise GcodeError(
            f"line {lineno}: coordinates without G0/G1 in {line.strip()!r} — "
            f"modal G-less lines are rejected by the Carvera and by this gate")
    if motion is not None:
        return ("move", motion, coords, fword, sword)
    if sword is not None:
        return ("spindle", sword)
    return ("none",)


@dataclass
class Contact:
    max: float = 0.0
    at: tuple | None = None
    samples: int = 0


@dataclass
class MoveMetrics:
    """Flat per-move arrays from prep + kernel. Index = move order."""
    motion: np.ndarray      # u8: 0 rapid / 1 cut
    x0: np.ndarray
    y0: np.ndarray
    z0: np.ndarray
    x1: np.ndarray
    y1: np.ndarray
    z1: np.ndarray
    tool_num: np.ndarray    # actual tool numbers
    feed: np.ndarray        # mm/min (rapids: machine rapid rate)
    rpm: np.ndarray
    lineno: np.ndarray
    stage: np.ndarray = None        # i32: stage index per move
    stage_labels: list = None       # stage index -> label (from OP_MARK)
    # filled by the kernel:
    volume: np.ndarray = None       # mm³ removed by this move
    contact: np.ndarray = None      # max footprint contact vs move start
    contact_x: np.ndarray = None
    contact_y: np.ndarray = None
    contact_samples: np.ndarray = None
    efrac: np.ndarray = None        # max engagement fraction over samples
    shank_over: np.ndarray = None   # max stock above the shank bottom

    @property
    def time_s(self) -> np.ndarray:
        L = np.maximum(np.hypot(np.hypot(self.x1 - self.x0,
                                         self.y1 - self.y0),
                                self.z1 - self.z0), 0.0)
        return L / np.maximum(self.feed, 1.0) * 60.0


@dataclass
class CarveResult:
    stock: np.ndarray
    ppm: float
    half: float
    worst_rapid: float = 0.0
    rapid_at: tuple | None = None
    contact: dict[int, Contact] = field(default_factory=dict)  # ALL tools
    min_cut_z: float = 0.0
    metrics: MoveMetrics | None = None
    shank_worst: float = 0.0
    shank_at: tuple | None = None
    # per-stage previews: stage_stocks[s] is the stock grid AFTER stage s
    # finished (same shape/convention as .stock; the last one equals it)
    stage_labels: list[str] = field(default_factory=list)
    stage_stocks: list[np.ndarray] = field(default_factory=list)


def prep_moves(nc_path, job: Job, rapid_feed: float) -> MoveMetrics:
    """Strict-parse the file and resolve all modal state into flat arrays."""
    cur = {"X": None, "Y": None, "Z": None}
    tool = None
    feed = None
    rpm = 0.0
    rows = []
    stage_labels: list[str] = []
    cur_stage = -1
    for lineno, line in enumerate(open(nc_path), 1):
        mk = OP_MARK.search(line)
        if mk:
            stage_labels.append(mk.group(1))
            cur_stage = len(stage_labels) - 1
        parsed = parse_line(line, lineno)
        if parsed[0] == "tool":
            t = parsed[1]
            if t not in job.tools:
                raise GcodeError(
                    f"line {lineno}: M6 T{t} but tool {t} is not defined in "
                    f"the job — refusing to simulate moves blind")
            tool = t
            continue
        if parsed[0] == "spindle":
            rpm = parsed[1]
            continue
        if parsed[0] != "move":
            continue
        motion, coords, fword, sword = parsed[1], parsed[2], parsed[3], parsed[4]
        if sword is not None:
            rpm = sword
        if fword is not None:
            feed = fword
        if tool is None:
            raise GcodeError(
                f"line {lineno}: motion before any tool change — cannot "
                f"attribute the move to a tool")
        prev = dict(cur)
        cur.update(coords)
        unknown = None in prev.values() or None in cur.values()
        if motion == 1 and unknown:
            raise GcodeError(
                f"line {lineno}: cutting move before XYZ position is fully "
                f"established")
        if unknown:
            continue  # G0 accumulating the initial position
        if motion == 1 and feed is None:
            raise GcodeError(
                f"line {lineno}: cutting move with no feed rate established")
        rows.append((motion, prev["X"], prev["Y"], prev["Z"],
                     cur["X"], cur["Y"], cur["Z"], tool,
                     rapid_feed if motion == 0 else feed, rpm, lineno,
                     cur_stage))
    if not rows:
        raise GcodeError("file contains no resolvable moves")
    a = np.array(rows, dtype=np.float64)
    col = lambda i: np.ascontiguousarray(a[:, i])  # noqa: E731 — kernels need contiguous
    stage = col(11).astype(np.int32)
    if not stage_labels:
        stage_labels = ["program"]     # hand-written file with no markers
        stage[:] = 0
    elif (stage < 0).any():
        stage_labels = ["setup"] + stage_labels  # moves before the first op
        stage += 1
    return MoveMetrics(
        motion=col(0).astype(np.uint8),
        x0=col(1), y0=col(2), z0=col(3),
        x1=col(4), y1=col(5), z1=col(6),
        tool_num=col(7).astype(np.int32),
        feed=col(8), rpm=col(9),
        lineno=col(10).astype(np.uint32),
        stage=stage, stage_labels=stage_labels)


def carve(nc_path, job: Job, *, ppm: float = 12.5, extra_half: float = 3.0,
          step: float = 0.06, check: bool = True) -> CarveResult:
    half = job.stock_half + extra_half
    n = int(half * 2 * ppm)
    m = prep_moves(nc_path, job, job.machine["rapid_feed"])

    tool_nums = sorted(job.tools)
    idx_of = {t: i for i, t in enumerate(tool_nums)}
    tools = job.tools
    dia = np.array([tools[t].diameter for t in tool_nums])
    ball = np.array([1 if tools[t].type == "ball" else 0 for t in tool_nums],
                    dtype=np.uint8)
    flute_len = np.array([tools[t].flute_length for t in tool_nums])
    shank_d = np.array([tools[t].shank_diameter for t in tool_nums])
    tool_idx = np.array([idx_of[int(t)] for t in m.tool_num], dtype=np.uint16)

    # per-stage stock snapshots: after the LAST move of each stage. Stage
    # last-move indices are strictly increasing (each move has one stage),
    # so this is already sorted the way the kernels require.
    nstages = len(m.stage_labels)
    last = np.full(nstages, -1, dtype=np.int64)
    for s in range(nstages):
        idx = np.nonzero(m.stage == s)[0]
        if idx.size:
            last[s] = idx[-1]
    snap_after = np.ascontiguousarray(last[last >= 0])

    out = kernel.measure(
        n=n, ppm=ppm, half=half, step=step, check=check,
        dia=dia, ball=ball, flute_len=flute_len, shank_d=shank_d,
        motion=m.motion, x0=m.x0, y0=m.y0, z0=m.z0,
        x1=m.x1, y1=m.y1, z1=m.z1, tool_idx=tool_idx,
        snap_after=snap_after)

    m.volume = out["volume"]
    m.contact = out["contact"]
    m.contact_x = out["contact_x"]
    m.contact_y = out["contact_y"]
    m.contact_samples = out["contact_samples"]
    m.efrac = out["efrac"]
    m.shank_over = out["shank_over"]

    snap_of = dict(zip((int(v) for v in snap_after), out["snapshots"]))
    stage_stocks = []
    prev = np.zeros((n, n), np.float32)  # uncut stock (top plane z=0)
    for s in range(nstages):
        if last[s] >= 0:
            prev = snap_of[int(last[s])]
        stage_stocks.append(prev)        # empty stage: unchanged stock

    res = CarveResult(out["stock"], ppm, half,
                      worst_rapid=float(out["worst_rapid"]),
                      min_cut_z=float(out["min_cut_z"]),
                      metrics=m,
                      stage_labels=list(m.stage_labels),
                      stage_stocks=stage_stocks)
    if out["worst_rapid"] > 0:
        res.rapid_at = (float(round(out["rapid_x"], 2)),
                        float(round(out["rapid_y"], 2)),
                        float(round(out["rapid_z"], 2)))
    for t in tool_nums:
        sel = m.tool_num == t
        c = Contact()
        if sel.any():
            c.samples = int(m.contact_samples[sel].sum())
            k = int(np.argmax(np.where(sel, m.contact, -1)))
            if m.contact[k] > 1e-6:
                c.max = float(m.contact[k])
                c.at = (float(round(m.contact_x[k], 1)),
                        float(round(m.contact_y[k], 1)))
        res.contact[t] = c
    if check:
        k = int(np.argmax(m.shank_over))
        if m.shank_over[k] > 1e-6:
            res.shank_worst = float(m.shank_over[k])
            res.shank_at = (float(round((m.x0[k] + m.x1[k]) / 2, 1)),
                            float(round((m.y0[k] + m.y1[k]) / 2, 1)),
                            int(m.lineno[k]))
    return res


def carve_check(nc_path, job: Job, *, ppm: float = 12.5,
                extra_half: float = 3.0) -> CarveResult:
    return carve(nc_path, job, ppm=ppm, extra_half=extra_half,
                 step=0.06, check=True)


def carve_fast(nc_path, job: Job, *, ppm: float = 20.0,
               extra_half: float = 1.0) -> CarveResult:
    """Higher-resolution carve for previews. Same strict parser — the
    preview must refuse what the gate refuses (Article VI)."""
    return carve(nc_path, job, ppm=ppm, extra_half=extra_half,
                 step=0.04, check=False)
