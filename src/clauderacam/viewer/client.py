"""Viewer client: how ANY process (MCP server, CLI, a future tool) gets a
job in front of human eyes without spawning a second viewer.

Discovery: server.start() records {port, token, epoch, pid} in
~/.clauderacam/viewer.json. discover() health-checks that record against
the live server's /api/ping (epoch must match — a recycled port from a
dead process never impersonates a running viewer). If a server is found,
pushes go to it over HTTP with the token; if not, the caller can start
one in-process (which then writes its own discovery record for everyone
else to join).

This is what makes the viewer collaborative: Claude's MCP process and a
human's `clauderacam view` land in the SAME server, as sessions keyed by
job path, and every push shows up in every watching browser within a
poll tick.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from . import server


def discover() -> dict | None:
    """Return {url, token, port, ...} for a live viewer server, else None."""
    try:
        info = json.loads(server.DISCOVERY_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    url = f"http://localhost:{info.get('port')}/"
    try:
        with urllib.request.urlopen(url + "api/ping", timeout=1.0) as r:
            ping = json.loads(r.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None
    if ping.get("app") != "clauderacam-viewer" \
            or ping.get("epoch") != info.get("epoch"):
        return None  # stale record: some other process owns that port now
    return {**info, "url": url}


def ensure_server(port: int = 8323, jobs_dir=None) -> tuple[str, bool]:
    """(url, started_here). Joins a discovered server when one is live;
    otherwise starts one in this process."""
    if server.running():
        return f"http://localhost:{server.current_port()}/", True
    found = discover()
    if found:
        return found["url"], False
    return server.start(port, jobs_dir=jobs_dir), True


def push_job(job, report) -> tuple[str, str] | None:
    """Push a verified job into the viewer as a session (joining any
    existing session for the same file). Returns (url, sid), or None when
    no server is running anywhere — pushing never launches one."""
    from .. import stages
    if report.carve is None:
        return None
    meta, stocks = stages.viewer_payload(job, report)
    blobs = server.encode_stocks(stocks)
    program = report.program.encode()
    if server.running():
        sid = server.push_session(meta["path"], meta, blobs, program)
        return f"http://localhost:{server.current_port()}/", sid
    found = discover()
    if not found:
        return None
    head = json.dumps({"meta": meta,
                       "stage_bytes": [len(b) for b in blobs],
                       "program_bytes": len(program)}).encode()
    body = head + b"\n" + b"".join(blobs) + program
    req = urllib.request.Request(
        found["url"] + "api/push?token=" +
        urllib.parse.quote(found["token"]),
        data=body, headers={"Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=30) as r:
        sid = json.loads(r.read())["sid"]
    return found["url"], sid


def invalidate(job) -> None:
    """Mark the job's session STALE (its .nc was regenerated). No-op when
    no server is running."""
    if server.running():
        server.invalidate_session(str(job.path))
        return
    found = discover()
    if not found:
        return
    req = urllib.request.Request(
        found["url"] + "api/invalidate?token=" +
        urllib.parse.quote(found["token"]),
        data=json.dumps({"path": str(job.path)}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except (urllib.error.URLError, OSError):
        pass  # a dead viewer has nothing to go stale


def sessions() -> list[dict]:
    """List sessions on whichever server is live (in-process or remote)."""
    if server.running():
        return server.list_sessions()
    found = discover()
    if not found:
        return []
    with urllib.request.urlopen(found["url"] + "api/sessions",
                                timeout=5) as r:
        return json.loads(r.read())["sessions"]
