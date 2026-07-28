"""Localhost viewer app: a threaded HTTP server holding the latest simulated
stock. The frontend (three.js) polls /api/state and refetches the heightfield
when the version changes, so re-running verify/view updates the open tab live.

Protocol hardening from the 2026-07-28 review:
  - versions carry a random per-process epoch, so a tab that watched a dead
    server never mistakes the new process's push for one it already has
  - /api/stock requires the version it was asked for (?v=...) and answers
    409 if a push landed between the state fetch and the stock fetch, so the
    client can never pair meta from one push with the stock of another
  - start() falls back through nearby ports if the requested one is taken by
    another process (previously the new session silently could not update
    the tab, which kept showing the OLD process's PASS verdict)
  - push_invalidate() marks the current view STALE when the .nc is
    regenerated, so the tab never claims a verdict for bytes that no longer
    exist
"""
from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

STATIC = Path(__file__).parent / "static"
MIME = {".js": "text/javascript", ".html": "text/html", ".css": "text/css"}

_EPOCH = secrets.token_hex(4)
_state_lock = threading.Lock()
_counter = 0
_state = {"version": None, "meta": {"job": None, "checks": []}, "stocks": []}
_server: ThreadingHTTPServer | None = None
_port: int | None = None


def _bump() -> str:
    global _counter
    _counter += 1
    return f"{_EPOCH}:{_counter}"


def push_state(meta: dict, stocks: list[np.ndarray]) -> None:
    """meta: the JSON-clean payload built by stages.viewer_payload (job card,
    stage list, tools, checks). stocks: one grid per stage, cumulative —
    stocks[s] is the stock AFTER stage s; stocks[-1] is the final part."""
    with _state_lock:
        v = _bump()
        _state["version"] = v
        _state["meta"] = {**meta, "version": v, "stale": False,
                          "nstages": len(stocks)}
        _state["stocks"] = [
            np.ascontiguousarray(s.astype("<f4")).tobytes() for s in stocks]


def push_invalidate(job_name: str) -> None:
    """The job's .nc was regenerated: whatever the tab shows is no longer a
    verdict about any existing file."""
    with _state_lock:
        if not _state["meta"].get("n"):
            return
        v = _bump()
        _state["version"] = v
        _state["meta"] = {**_state["meta"], "version": v, "stale": True,
                          "ok": None, "job": job_name}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep stdio clean (MCP runs on stdio!)
        pass

    def _send(self, code, ctype, body):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client went away mid-transfer; not our problem

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self._send(200, "text/html", (STATIC / "index.html").read_bytes())
        elif path == "/api/state":
            with _state_lock:
                body = json.dumps(_state["meta"]).encode()
            self._send(200, "application/json", body)
        elif path == "/api/stock":
            q = parse_qs(parsed.query)
            want = (q.get("v") or [None])[0]
            stage = (q.get("stage") or [None])[0]
            with _state_lock:
                if want != _state["version"]:
                    self._send(409, "text/plain", b"version changed")
                    return
                stocks = _state["stocks"]
                try:
                    idx = len(stocks) - 1 if stage is None else int(stage)
                    body = stocks[idx]
                except (ValueError, IndexError):
                    self._send(404, "text/plain", b"no such stage")
                    return
            self._send(200, "application/octet-stream", body)
        elif path.startswith("/static/"):
            f = STATIC / Path(path).name
            if f.is_file():
                self._send(200, MIME.get(f.suffix, "application/octet-stream"),
                           f.read_bytes())
            else:
                self._send(404, "text/plain", b"not found")
        else:
            self._send(404, "text/plain", b"not found")


def start(port: int = 8323) -> str:
    """Start (or reuse) the viewer server; returns its URL. Tries nearby
    ports if the requested one is held by another process."""
    global _server, _port
    if _server is not None:
        return f"http://localhost:{_port}/"
    last_err = None
    for p in range(port, port + 10):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), Handler)
        except OSError as e:
            last_err = e
            continue
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        _server, _port = srv, p
        return f"http://localhost:{p}/"
    raise OSError(f"no free port in {port}..{port + 9}: {last_err}")


def running() -> bool:
    return _server is not None


def current_port() -> int | None:
    return _port
