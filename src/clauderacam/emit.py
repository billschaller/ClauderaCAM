"""G-code program assembly for the Carvera (grbl-mode Smoothieware).

Requirement 6 (DESIGN.md): ClauderaCAM owns emission end to end — no external
post-processor to second-guess. Dialect rules: G4 P is SECONDS, every line
<= 128 chars, M5 before every M6, `M6 Tn` (integer) semi-auto tool change,
G4 P2 spin-up dwell after M3, proven G28 park footer.

Two layers of defense (both added/hardened after the 2026-07-28 review):
  - assemble() runs every emitted line through the SAME strict parser the
    verification gate uses (simulate.parse_line) — the emitter cannot
    produce what the gate would refuse, and there is no second, weaker
    arc check to bypass with compact syntax.
  - lint_program() checks the spindle-state dialect invariants the
    geometric simulator cannot see (M5-before-M6, dwell-after-M3, S>0,
    units/absolute preamble, line length, M30). verify() runs it on the
    program text, so dropping a safety line fails the gate, not just a
    code review.
"""
from __future__ import annotations

import re

from .engine import OpResult
from .job import Job

MAX_LINE = 128

_M6 = re.compile(r"\bM0?6\b")
_M3 = re.compile(r"\bM0?3\b")
_M5 = re.compile(r"\bM0?5\b")
_S_WORD = re.compile(r"\bS(\d+(?:\.\d*)?)")
_G4 = re.compile(r"\bG0?4\b")
_MOTION = re.compile(r"\bG0?[01]\b")
_G21 = re.compile(r"\bG21\b")
_G90 = re.compile(r"\bG90\b")
_M30 = re.compile(r"\bM30\b")
_COMMENT = re.compile(r"\([^)]*\)|;.*")


def lint_program(lines: list[str]) -> list[str]:
    """Dialect lint for a full program. Returns a list of problems (empty ==
    clean). These are the invariants a purely geometric simulation is blind
    to — the adversarial review demonstrated that every one of them could be
    dropped while both the golden test and the simulator stayed green."""
    problems: list[str] = []
    spindle = "unknown"       # unknown | off | on
    seen_g21 = seen_g90 = seen_motion = seen_m30 = False
    want_dwell_after = None   # line number of an M3 awaiting its G4
    for lineno, raw in enumerate(lines, 1):
        if len(raw) > MAX_LINE:
            problems.append(f"line {lineno} exceeds {MAX_LINE} chars")
        body = _COMMENT.sub(" ", raw).strip()
        if not body:
            continue
        if want_dwell_after is not None:
            if _G4.search(body):
                want_dwell_after = None
            else:
                problems.append(
                    f"line {want_dwell_after}: M3 not followed by a G4 "
                    f"spin-up dwell")
                want_dwell_after = None
        if _G21.search(body):
            seen_g21 = True
        if _G90.search(body):
            seen_g90 = True
        if _M30.search(body):
            seen_m30 = True
        if _MOTION.search(body) and not seen_motion:
            seen_motion = True
            if not seen_g21:
                problems.append(
                    f"line {lineno}: motion before G21 (units not pinned)")
            if not seen_g90:
                problems.append(
                    f"line {lineno}: motion before G90 (absolute mode not "
                    f"pinned)")
        if _M6.search(body):
            if spindle != "off":
                problems.append(
                    f"line {lineno}: M6 without a preceding M5 — tool change "
                    f"with the spindle not provably stopped")
        if _M3.search(body):
            spindle = "on"
            m = _S_WORD.search(body)
            if not m or float(m.group(1)) <= 0:
                problems.append(f"line {lineno}: M3 without a positive S word")
            want_dwell_after = lineno
        elif _M5.search(body):
            spindle = "off"
    if want_dwell_after is not None:
        problems.append(f"line {want_dwell_after}: M3 not followed by a G4 "
                        f"spin-up dwell")
    if not seen_m30:
        problems.append("program has no M30 end-of-program")
    return problems


# ---------------------------------------------------------------- laser silk
# The Air's 455nm module cures white UV mask as a plotter (solder-mask guide
# §5). Laser programs are their OWN dialect with their own law: the mill gate
# (simulate.parse_line) refuses M321 on sight, so a laser file can never slip
# through the carving pipeline — and this lint refuses anything spindle-like,
# so a carving file can never pass as a laser job.
LASER_S_MAX = 0.30         # hard ceiling = the MakeraCAM tutorial's own top
#                            end (20-30%). The 2026-07-19 defocus incident
#                            proved a DEFOCUSED 20% beam cures mask in washes,
#                            so focused 20% is already past the cure
#                            threshold; the ceiling exists to catch a
#                            fat-fingered dose, not to bless 0.30.
LASER_DOSE_DEFAULT = 0.03  # field-validated 2026-07-19 (S0.03 / F100)
LASER_FEED_DEFAULT = 100.0

_M321 = re.compile(r"\bM321\b")
_Z_WORD = re.compile(r"\bZ([-+]?\d*\.?\d+)")
_T_WORD = re.compile(r"\bT\d")
_LASER_WORDS = re.compile(
    r"\bG0?(?:[01]\b|17\b|21\b|54\b|90\b|94\b)|\bM(?:321|0?3|0?5|30)\b"
    r"|[XYZF][-+]?\d*\.?\d+|S\d*\.?\d+")


def lint_laser(lines: list[str], s_max: float = LASER_S_MAX) -> list[str]:
    """Dialect lint for a LASER program. The law (solder-mask guide §5, all
    field-derived): exactly one M321 before anything moves; the first motion
    after it is exactly `G0 Z0` (the focus law — a parked head projects a
    big square and cures mask in washes); no other Z word in the file; one
    M3 with 0 < S <= s_max (no G4 dwell — spin-up is a SPINDLE rule, the
    laser has none); no tool changes; M5 before M30. The laser fires only
    during feed moves, so armed G0 travel is dark by firmware design."""
    problems: list[str] = []
    seen_m321 = seen_m3 = seen_m5 = seen_m30 = seen_motion = False
    seen_g21 = seen_g90 = False
    focus_pending = False    # after M321, before its G0 Z0
    for lineno, raw in enumerate(lines, 1):
        if len(raw) > MAX_LINE:
            problems.append(f"line {lineno} exceeds {MAX_LINE} chars")
        body = _COMMENT.sub(" ", raw).strip()
        if not body:
            continue
        if _LASER_WORDS.sub("", body).replace(" ", ""):
            problems.append(
                f"line {lineno}: {raw.strip()!r} contains words outside "
                f"the laser dialect")
            continue
        if _T_WORD.search(body) or _M6.search(body):
            problems.append(f"line {lineno}: tool change in a laser program")
        if _G21.search(body):
            seen_g21 = True
        if _G90.search(body):
            seen_g90 = True
        if _M321.search(body):
            if seen_m321:
                problems.append(f"line {lineno}: second M321")
            if seen_motion:
                problems.append(f"line {lineno}: M321 after motion")
            seen_m321 = True
            focus_pending = True
            continue
        zm = _Z_WORD.search(body)
        motion = _MOTION.search(body) is not None
        if focus_pending:
            if motion or _M3.search(body):
                if body.replace(" ", "") == "G0Z0":
                    focus_pending = False
                    seen_motion = True
                    continue
                problems.append(
                    f"line {lineno}: first motion after M321 must be "
                    f"exactly 'G0 Z0' (focus law) — got {raw.strip()!r}")
                focus_pending = False
        elif zm is not None:
            problems.append(
                f"line {lineno}: Z word outside the M321 focus move — the "
                f"head stays at the focal plane")
        if motion:
            seen_motion = True
            if not seen_m321:
                problems.append(f"line {lineno}: motion before M321 — the "
                                f"head is still a spindle here")
            if not (seen_g21 and seen_g90):
                problems.append(f"line {lineno}: motion before G21/G90")
        if _M3.search(body):
            if not seen_m321:
                problems.append(f"line {lineno}: M3 before M321 arms the "
                                f"SPINDLE, not the laser")
            if seen_m3:
                problems.append(f"line {lineno}: second M3")
            seen_m3 = True
            sm = _S_WORD.search(body)
            s = float(sm.group(1)) if sm else 0.0
            if not sm or s <= 0:
                problems.append(f"line {lineno}: M3 without a positive S")
            elif s > s_max:
                problems.append(
                    f"line {lineno}: dose S{s:g} exceeds the ceiling "
                    f"{s_max:g} — char/vaporize territory")
        elif _M5.search(body):
            seen_m5 = True
        if _M30.search(body):
            seen_m30 = True
    if not seen_m321:
        problems.append("no M321 — this is not a laser program")
    if focus_pending:
        problems.append("M321 never followed by its 'G0 Z0' focus move")
    if not seen_m3:
        problems.append("laser never armed (no M3 S)")
    if not seen_m5:
        problems.append("laser never disarmed (no M5)")
    if not seen_m30:
        problems.append("program has no M30 end-of-program")
    return problems


def assemble_laser(name: str, strokes: list[list[tuple]], *,
                   dose_s: float = LASER_DOSE_DEFAULT,
                   feed: float = LASER_FEED_DEFAULT,
                   s_max: float = LASER_S_MAX,
                   header: list[str] | None = None) -> str:
    """Emit a silk-legend laser program: one G0 hop + G1 chain per stroke.
    Refuses to emit outside its own law, same posture as assemble().
    `header` = optional COMMENT lines after the banner (run-sheet context),
    linted with everything else; None emits byte-identically to before."""
    if dose_s <= 0 or dose_s > s_max:
        raise ValueError(
            f"laser dose S{dose_s:g} outside (0, {s_max:g}] — the "
            f"field-validated dose is S{LASER_DOSE_DEFAULT}; bracket UP on "
            f"scrap, not in the config")
    out = [f"(clauderacam laser: {name})",
           f"(silk legend: dose S{dose_s:g} F{feed:g}; cures white mask, "
           f"wipe uncured with IPA)",
           *(header or []),
           "G90 G94", "G17", "G21", "G54",
           "M321",
           "G0 Z0",
           "(focus law: Z0 = focal plane after M321)",
           f"M3 S{dose_s:g}"]
    for pts in strokes:
        if len(pts) < 2:
            raise ValueError("laser stroke needs at least 2 points")
        x, y = pts[0]
        out.append(f"G0 X{x:.3f} Y{y:.3f}")
        x, y = pts[1]
        out.append(f"G1 X{x:.3f} Y{y:.3f} F{feed:g}")
        for x, y in pts[2:]:
            out.append(f"G1 X{x:.3f} Y{y:.3f}")
    out += ["M5", "M30"]
    problems = lint_laser(out, s_max=s_max)
    if problems:
        raise ValueError("emitted laser program fails its own dialect "
                         "lint: " + "; ".join(problems[:3]))
    return "\n".join(out) + "\n"


def assemble(job: Job, ops: list[OpResult],
             header: list[str] | None = None) -> str:
    """`header` is an optional block of COMMENT lines placed right after the
    job banner — the [pcb] lane uses it for the run-sheet context, tool table
    and floor echo the operator reads at the machine (reemit.program_header
    composes it). Every header line goes through the same strict parse and
    lint as the rest of the program, and header=None emits byte-identically
    to before the parameter existed — the [job] goldens prove it."""
    from .simulate import parse_line  # deferred: emit is imported by verify

    out: list[str] = []
    out += [f"(clauderacam job: {job.name})"]
    out += header or []
    out += ["G90 G94", "G17", "G21", "G54"]
    for r in ops:
        tool = job.tool(r.tool)
        out += ["M05",
                f"M6 T{tool.num}",
                f"M3 S{tool.rpm}",
                "G4 P2",
                f"(begin operation: {r.label} T{tool.num} "
                f"{tool.type} d{tool.diameter})"]
        out += r.lines
        out.append(f"(finish operation: {r.label})")
    out += ["(begin postamble)", "M05", "G17 G90", "G28", "M30"]

    # every line must survive the SAME parser the verification gate uses
    for i, line in enumerate(out, 1):
        parse_line(line, i)  # raises GcodeError on anything unmodelable
    problems = lint_program(out)
    if problems:
        raise ValueError("emitted program fails its own dialect lint: "
                         + "; ".join(problems[:3]))
    return "\n".join(out) + "\n"


def write(job: Job, ops: list[OpResult]) -> str:
    text = assemble(job, ops)
    job.out.parent.mkdir(parents=True, exist_ok=True)
    job.out.write_text(text)
    return str(job.out)
