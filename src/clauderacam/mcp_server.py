"""ClauderaCAM MCP server (stdio). Tools: load_job, generate, verify, preview,
view. Register with:  claude mcp add clauderacam -- <repo>/.venv/bin/python -m clauderacam.mcp_server

Deliberately NO machine-upload tool: files reach the Carvera only by the
user's own hand until they decide otherwise.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP, Image

from . import emit, engine, job as jobmod, preview as previewmod, verify as verifymod
from .viewer import server as viewer

mcp = FastMCP("clauderacam")


def _job(path: str):
    return jobmod.load(path)


def _push_viewer(j, report):
    if viewer.running():
        viewer.push_state(
            j.name, report.carve.stock, report.carve.ppm, report.carve.half,
            [{"name": c.name, "value": c.value, "limit": c.limit, "ok": c.ok}
             for c in report.checks],
            report.ok)


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
    _push_viewer(j, report)
    return report.text()


@mcp.tool()
def preview(path: str):
    """Render an as-machined hillshade preview PNG from the simulated stock."""
    j = _job(path)
    p = previewmod.render(j)
    return [f"preview written to {p}", Image(path=p)]


@mcp.tool()
def view(path: str, port: int = 8323) -> str:
    """Start (or update) the live 3D viewer app on localhost and load this
    job's simulated stock into it. Returns the URL to open in a browser."""
    j = _job(path)
    url = viewer.start(port)
    report = verifymod.verify(j)
    _push_viewer(j, report)
    return (f"viewer: {url}  (orbit with mouse; panel shows the "
            f"{'PASS' if report.ok else 'FAIL'} verification live)")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
