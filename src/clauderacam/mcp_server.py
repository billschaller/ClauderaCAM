"""ClauderaCAM MCP server (stdio). Tools: load_job, generate, verify, preview,
view. Register with:  claude mcp add clauderacam -- <repo>/.venv/bin/python -m clauderacam.mcp_server

Deliberately NO machine-upload tool: files reach the Carvera only by the
user's own hand until they decide otherwise.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP, Image

from . import emit, engine, job as jobmod, preview as previewmod, \
    stages as stagesmod, twosided as twomod, verify as verifymod
from .pcb import pcbjob as pcbjobmod, session as pcbsess
from .viewer import client as viewer

mcp = FastMCP("clauderacam")


def _job(path: str):
    return jobmod.load(path)


def _pcb_text(sessions, pushed) -> str:
    """One text block for a [pcb] document: the gate + sheet-sim verdicts,
    then the run sheet, then whatever viewer sessions landed."""
    lines = [pcbsess.report_text(sessions)]
    card = sessions[0].meta["run_sheet"] if sessions else []
    lines.append("run sheet:")
    for st in card:
        est = f"  ~{stagesmod.fmt_time(st['est_s'])}" if st.get("est_s") \
            else ""
        lines.append(f"  {st['n']:2d} [{st['kind']}] {st['title']}{est}")
    for name, url, sid in pushed:
        lines.append(f"viewer session {name}: {url}#{sid}")
    return "\n".join(lines)


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
    if pcbsess.is_pcb(path):
        pj = pcbjobmod.load(path)
        sheet = pcbsess.sheet_stock(pj)
        progs = pcbsess.program_paths(pj)
        return "\n".join([
            f"[pcb] job {pj.name}: gerbers={pj.gerber_dir}",
            f"blank {pj.blank_w}x{pj.blank_h}x{pj.thickness}mm, anchor "
            f"{pj.anchor}, spoilboard {pj.spoil_thickness}",
            f"board window (machine) {sheet.bx0:.3f},{sheet.by0:.3f} .. "
            f"{sheet.bx1:.3f},{sheet.by1:.3f} — DERIVED from Edge.Cuts",
            f"sheet stock {sheet.x0:.2f},{sheet.y0:.2f} .. {sheet.x1:.2f},"
            f"{sheet.y1:.2f} x {sheet.thickness}mm at {sheet.ppm} px/mm",
            "tools: " + ", ".join(f"T{t.num} {t.type} d{t.diameter}"
                                  for t in pj.tools.values()),
            "programs: " + (", ".join(f"{n} ({p.name})"
                                      for n, p in progs.items())
                            or "none on disk yet")])
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


def _twosided_verify_text(ts, reports) -> str:
    lines = []
    for side in ("front", "back"):
        rep = reports[side]
        j = ts.front if side == "front" else ts.back
        lines.append(f"=== {side} ({j.out.name}) ===")
        lines.append(rep.text())
        if rep.carve is not None:
            lines += stagesmod.stage_lines(stagesmod.stage_stats(j, rep.carve))
            pushed = viewer.push_job(j, rep)
            if pushed:
                lines.append(f"viewer session: {pushed[0]}#{pushed[1]}")
    return "\n".join(lines)


@mcp.tool()
def generate(path: str) -> str:
    """Generate all toolpaths for a job and write the .nc program(s).
    Returns per-op stats. Always run `verify` before cutting."""
    if twomod.is_twosided(path):
        ts = twomod.load(path)
        out = []
        for side, ops in twomod.generate(ts).items():
            j = ts.front if side == "front" else ts.back
            viewer.invalidate(j)
            out += [f"[{side}] {r.label}: T{r.tool} {len(r.lines)} moves, "
                    f"~{r.est_min:.0f} min" for r in ops]
            out.append(f"[{side}] wrote {j.out}")
        out.append("NOT verified yet — run verify before cutting.")
        return "\n".join(out)
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
    cleared for metal only if this returns PASS. Updates the viewer if open.
    Two-sided jobs verify both sides; the back is checked against the
    carried (flipped) front stock. [pcb] jobs verify every program of the
    six-phase split against the board maps AND the sheet stock sim."""
    if pcbsess.is_pcb(path):
        pj = pcbjobmod.load(path)
        sessions = pcbsess.build(pj)
        return _pcb_text(sessions, viewer.push_pcb(sessions))
    if twomod.is_twosided(path):
        ts = twomod.load(path)
        return _twosided_verify_text(ts, twomod.verify(ts))
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
    its state. Returns the session URL to open in a browser. Two-sided
    jobs open one session per side; [pcb] jobs one session per program of
    the split, each carrying the shared run-sheet card."""
    if pcbsess.is_pcb(path):
        pj = pcbjobmod.load(path)
        viewer.ensure_server(port, jobs_dir=pj.path.parent)
        sessions = pcbsess.build(pj)
        return _pcb_text(sessions, viewer.push_pcb(sessions))
    if twomod.is_twosided(path):
        ts = twomod.load(path)
        viewer.ensure_server(port, jobs_dir=ts.path.parent)
        return _twosided_verify_text(ts, twomod.verify(ts))
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
