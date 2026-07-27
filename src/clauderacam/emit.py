"""G-code program assembly for the Carvera (grbl-mode Smoothieware).

Requirement 6 (DESIGN.md): ClauderaCAM owns emission end to end — no external
post-processor to second-guess. Dialect rules enforced here: G4 P is SECONDS,
every line <= 128 chars, M5 before every M6, `M6 Tn` (integer) semi-auto tool
change, G4 P2 spin-up dwell after M3, proven G28 park footer.
"""
from __future__ import annotations

from .engine import OpResult
from .job import Job

MAX_LINE = 128


def assemble(job: Job, ops: list[OpResult]) -> str:
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

    for i, line in enumerate(out, 1):
        if len(line) > MAX_LINE:
            raise ValueError(f"line {i} exceeds {MAX_LINE} chars: {line[:40]}...")
        if line.startswith(("G2 ", "G3 ", "G02", "G03")):
            raise ValueError(f"arc emitted at line {i}; simulator can't verify arcs")
    return "\n".join(out) + "\n"


def write(job: Job, ops: list[OpResult]) -> str:
    text = assemble(job, ops)
    job.out.parent.mkdir(parents=True, exist_ok=True)
    job.out.write_text(text)
    return str(job.out)
