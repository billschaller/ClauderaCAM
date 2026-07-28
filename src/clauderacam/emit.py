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


def assemble(job: Job, ops: list[OpResult]) -> str:
    from .simulate import parse_line  # deferred: emit is imported by verify

    out: list[str] = []
    out += [f"(clauderacam job: {job.name})",
            "G90 G94", "G17", "G21", "G54"]
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
