"""ClauderaCAM MCP server (stdio). Tools: load_job, generate, verify, preview,
view. Register with:  claude mcp add clauderacam -- <repo>/.venv/bin/python -m clauderacam.mcp_server

Deliberately NO machine-upload tool: files reach the Carvera only by the
user's own hand until they decide otherwise.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP, Image

from . import emit, engine, job as jobmod, preview as previewmod, \
    stages as stagesmod, verify as verifymod
from .viewer import client as viewer

mcp = FastMCP("clauderacam")


def _job(path: str):
    return jobmod.load(path)


def _push_viewer(j, report) -> str:
    """Update the job's viewer session on whichever server is live (this
    process or a discovered one). Returns a note, or '' if no viewer."""
    pushed = viewer.push_job(j, report)
    if not pushed:
        return ""
    url, sid = pushed
    return f"viewer session updated: {url}#{sid}"


def _stage_text(j, report) -> str:
    if report.carve is None:
        return ""
    return "\n".join(stagesmod.stage_lines(stagesmod.stage_stats(j, report.carve)))


@mcp.tool()
def load_job(path: str) -> str:
    """Load a job TOML and report its parameters and derived geometry."""
    j = _job(path)
    lines = [f"job {j.name}: stl={j.stl}",
             f"stock {j.stock_size}x{j.stock_size}x{j.stock_thickness}mm, "
             f"model r={j.model_radius} (Ø{2*j.model_radius}), skim {j.skim}, "
             f"floor {j.floor_z}, fixture keep-out r>{j.keepout_radius}",
             "tools: " + ", ".join(f"T{t.num} {t.type} d{t.diameter}"
                                   for t in j.tools.values()),
             "ops: " + " → ".join(op.get("label", op["kind"])
                                  + f" (T{op['tool']})" for op in j.ops)]
    return "\n".join(lines)


@mcp.tool()
def generate(path: str) -> str:
    """Generate all toolpaths for a job and write the .nc program.
    Returns per-op stats. Always run `verify` before cutting."""
    j = _job(path)
    ops = engine.generate_ops(j)
    out = emit.write(j, ops)
    viewer.invalidate(j)  # any watching session now shows STALE
    total = sum(r.est_min for r in ops)
    lines = [f"{r.label}: T{r.tool} {len(r.lines)} moves, "
             f"{r.path_len_mm/1000:.1f}m, ~{r.est_min:.0f} min"
             for r in ops]
    lines.append(f"wrote {out}  (total ≈ {total:.0f} min + tool changes)")
    lines.append("NOT verified yet — run verify before cutting.")
    return "\n".join(lines)


@mcp.tool()
def verify(path: str) -> str:
    """Physical stock-simulation verification of the job's .nc (rapids,
    ball engagement, surface completeness, fixture keep-out). The file is
    cleared for metal only if this returns PASS. Updates the viewer if open."""
    j = _job(path)
    report = verifymod.verify(j)
    note = _push_viewer(j, report)
    st = _stage_text(j, report)
    return report.text() + (f"\n{st}" if st else "") \
        + (f"\n{note}" if note else "")


@mcp.tool()
def preview(path: str):
    """Render an as-machined hillshade preview PNG from the simulated stock."""
    j = _job(path)
    p = previewmod.render(j)
    return [f"preview written to {p}", Image(path=p)]


@mcp.tool()
def view(path: str, port: int = 8323) -> str:
    """Open (or join) this job's session in the live viewer app: starts a
    server only if none is running anywhere, verifies the job, and pushes
    its state. Returns the session URL to open in a browser."""
    j = _job(path)
    url, started = viewer.ensure_server(port, jobs_dir=j.path.parent)
    report = verifymod.verify(j)
    pushed = viewer.push_job(j, report)
    if pushed is None:
        return "viewer: verification is fatal — nothing to show:\n" \
            + report.text()
    url, sid = pushed
    st = _stage_text(j, report)
    return (f"viewer session: {url}#{sid} "
            f"({'new server' if started else 'joined running server'}; "
            f"{'PASS' if report.ok else 'FAIL'} verification live)"
            + (f"\n{st}" if st else ""))


@mcp.tool()
def sessions() -> str:
    """List open viewer sessions (job, status, verdict) on the live
    viewer server, if any."""
    rows = viewer.sessions()
    if not rows:
        return "no viewer server running (use the view tool to start one)"
    lines = []
    for s in rows:
        verdict = "STALE" if s.get("stale") else \
            {True: "PASS", False: "FAIL"}.get(s.get("ok"), "…")
        lines.append(f"{s['sid']}  {s.get('job') or s['label']:<16} "
                     f"{s['status']:<8} {verdict:<6} {s['path']}"
                     + (f"  [{s['error']}]" if s.get("error") else ""))
    return "\n".join(lines)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
