"""Localhost viewer app: a threaded HTTP server holding the latest simulated
stock. The frontend (three.js) polls /api/state and refetches the heightfield
when the version changes, so re-running verify/view updates the open tab live.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

STATIC = Path(__file__).parent / "static"
MIME = {".js": "text/javascript", ".html": "text/html", ".css": "text/css"}

_state_lock = threading.Lock()
_state = {"version": 0, "meta": {"job": None, "checks": []}, "stock": b""}
_server: ThreadingHTTPServer | None = None
_port: int | None = None


def push_state(job_name: str, stock: np.ndarray, ppm: float, half: float,
               checks: list[dict], verdict: bool | None) -> None:
    with _state_lock:
        _state["version"] += 1
        _state["meta"] = {
            "version": _state["version"], "job": job_name,
            "n": int(stock.shape[0]), "ppm": ppm, "half": half,
            "checks": checks, "ok": verdict,
        }
        _state["stock"] = np.ascontiguousarray(
            stock.astype("<f4")).tobytes()


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
        path = self.path.split("?")[0]
        if path == "/":
            self._send(200, "text/html",
                       (STATIC / "index.html").read_bytes())
        elif path == "/api/state":
            with _state_lock:
                body = json.dumps(_state["meta"]).encode()
            self._send(200, "application/json", body)
        elif path == "/api/stock":
            with _state_lock:
                body = _state["stock"]
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
    """Start (or reuse) the viewer server; returns its URL."""
    global _server, _port
    if _server is not None:
        return f"http://localhost:{_port}/"
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _server = srv
    _port = port
    return f"http://localhost:{port}/"


def running() -> bool:
    return _server is not None
