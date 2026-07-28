"""Localhost viewer server: one process, many SESSIONS, everyone joins.

A session is one open job document (the job TOML is the document format).
Sessions are keyed by the resolved job path — opening a file that is
already open JOINS its session, which is the collaboration model: Claude
regenerates and verifies through the MCP while a human watches the same
session in a browser; the browser polls and reflects every push within a
second.

Because the MCP server, the CLI and a human's browser live in different
processes, the viewer server is standalone: any process discovers a
running server through a discovery file (~/.clauderacam/viewer.json,
health-checked against the server's random epoch) and pushes state over
HTTP instead of spawning a second server. Pushes require the token from
the discovery file; the browser's only mutations are opening a job file
(path-constrained to the jobs dir) and closing a session — view state
only, nothing that touches a job or a machine (Article VII).

Protocol hardening carried over from the single-job viewer (2026-07-28
review): random per-process epoch in every version, /stock requires the
version it was asked for (409 on mismatch), stale marking on regenerate,
port fallback.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

STATIC = Path(__file__).parent / "static"
MIME = {".js": "text/javascript", ".html": "text/html", ".css": "text/css"}

DISCOVERY_FILE = Path(
    os.environ.get("CLAUDERACAM_VIEWER_FILE",
                   Path.home() / ".clauderacam" / "viewer.json"))

_EPOCH = secrets.token_hex(4)
_TOKEN = secrets.token_hex(16)
_lock = threading.Lock()
_counter = 0
_sessions: dict[str, "Session"] = {}
_server: ThreadingHTTPServer | None = None
_port: int | None = None
_jobs_dir: Path | None = None
_discovery_path: Path | None = None


@dataclass
class Session:
    sid: str
    path: str                 # resolved job TOML path — the join key
    label: str
    status: str = "loading"   # loading | ready | error
    error: str = ""
    meta: dict | None = None
    stocks: list[bytes] = field(default_factory=list)
    version: str | None = None
    updated: float = 0.0

    def summary(self) -> dict:
        m = self.meta or {}
        return {"sid": self.sid, "path": self.path, "label": self.label,
                "status": self.status, "error": self.error,
                "version": self.version, "updated": self.updated,
                "ok": m.get("ok"), "stale": m.get("stale"),
                "nstages": m.get("nstages"), "job": m.get("job")}


def _sid_for(path: str) -> str:
    return hashlib.sha1(path.encode()).hexdigest()[:8]


def _bump() -> str:
    global _counter
    _counter += 1
    return f"{_EPOCH}:{_counter}"


def encode_stocks(stocks: list[np.ndarray]) -> list[bytes]:
    return [np.ascontiguousarray(s.astype("<f4")).tobytes() for s in stocks]


def push_session(path: str, meta: dict, stocks: list[bytes]) -> str:
    """Create or update (JOIN) the session for a job path. stocks are the
    already-encoded little-endian f32 stage grids. Returns the sid."""
    path = str(Path(path).resolve())
    sid = _sid_for(path)
    with _lock:
        v = _bump()
        s = _sessions.get(sid) or Session(sid=sid, path=path,
                                          label=meta.get("job") or
                                          Path(path).stem)
        s.label = meta.get("job") or s.label
        s.status, s.error = "ready", ""
        s.meta = {**meta, "path": path, "version": v, "stale": False,
                  "nstages": len(stocks)}
        s.stocks = stocks
        s.version = v
        s.updated = time.time()
        _sessions[sid] = s
    return sid


def invalidate_session(path: str) -> None:
    """The job's .nc was regenerated: whatever this session shows is no
    longer a verdict about any existing file."""
    path = str(Path(path).resolve())
    with _lock:
        s = _sessions.get(_sid_for(path))
        if s is None or s.meta is None:
            return
        v = _bump()
        s.meta = {**s.meta, "version": v, "stale": True, "ok": None}
        s.version = v
        s.updated = time.time()


def error_session(path: str, msg: str) -> str:
    path = str(Path(path).resolve())
    sid = _sid_for(path)
    with _lock:
        s = _sessions.get(sid) or Session(sid=sid, path=path,
                                          label=Path(path).stem)
        s.status, s.error = "error", msg
        s.updated = time.time()
        _sessions[sid] = s
    return sid


def close_session(sid: str) -> bool:
    with _lock:
        return _sessions.pop(sid, None) is not None


def list_sessions() -> list[dict]:
    with _lock:
        return [s.summary() for s in
                sorted(_sessions.values(), key=lambda s: s.updated)]


def _load_in_background(path: Path) -> str:
    """Open (or rejoin) a session for a job file and verify it off-thread.
    Never generates or edits anything — a missing .nc is reported, not
    fixed: toolpath changes are Claude's to make, not the browser's."""
    path = path.resolve()
    sid = _sid_for(str(path))
    with _lock:
        s = _sessions.get(sid) or Session(sid=sid, path=str(path),
                                          label=path.stem)
        s.status = "loading"
        s.error = ""
        s.updated = time.time()
        _sessions[sid] = s

    def work():
        try:
            from .. import job as jobmod, stages, verify as verifymod
            j = jobmod.load(path)
            if not j.out.exists():
                error_session(str(path),
                              f"no toolpaths ({j.out.name} missing) — "
                              f"ask Claude to generate")
                return
            report = verifymod.verify(j)
            if report.carve is None:
                error_session(str(path), "fatal: " +
                              (report.checks[0].detail or "unparseable"))
                return
            meta, stocks = stages.viewer_payload(j, report)
            push_session(str(path), meta, encode_stocks(stocks))
        except Exception as e:  # surfaced in the session row, not lost
            error_session(str(path), str(e))

    threading.Thread(target=work, daemon=True).start()
    return sid


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

    def _json(self, obj, code=200):
        self._send(code, "application/json", json.dumps(obj).encode())

    def _authed(self, parsed) -> bool:
        tok = (parse_qs(parsed.query).get("token") or [None])[0]
        return tok == _TOKEN

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self._send(200, "text/html", (STATIC / "index.html").read_bytes())
        elif path == "/api/ping":
            self._json({"app": "clauderacam-viewer", "epoch": _EPOCH,
                        "pid": os.getpid()})
        elif path == "/api/sessions":
            self._json({"sessions": list_sessions()})
        elif path == "/api/files":
            root = _jobs_dir
            files = sorted(p.name for p in root.glob("*.toml")) if root else []
            self._json({"dir": str(root) if root else None, "files": files})
        elif path.startswith("/api/session/"):
            parts = path.split("/")  # '', 'api', 'session', sid, what
            if len(parts) != 5:
                self._send(404, "text/plain", b"not found")
                return
            sid, what = parts[3], parts[4]
            with _lock:
                s = _sessions.get(sid)
                if s is None:
                    self._send(404, "text/plain", b"no such session")
                    return
                if what == "state":
                    body = json.dumps({**(s.meta or {}), "sid": s.sid,
                                       "status": s.status,
                                       "error": s.error}).encode()
                    self._send(200, "application/json", body)
                    return
                if what == "stock":
                    q = parse_qs(parsed.query)
                    want = (q.get("v") or [None])[0]
                    stage = (q.get("stage") or [None])[0]
                    if want != s.version:
                        self._send(409, "text/plain", b"version changed")
                        return
                    try:
                        idx = len(s.stocks) - 1 if stage is None \
                            else int(stage)
                        body = s.stocks[idx]
                    except (ValueError, IndexError):
                        self._send(404, "text/plain", b"no such stage")
                        return
                    self._send(200, "application/octet-stream", body)
                    return
            self._send(404, "text/plain", b"not found")
        elif path.startswith("/static/"):
            f = STATIC / Path(path).name
            if f.is_file():
                self._send(200, MIME.get(f.suffix, "application/octet-stream"),
                           f.read_bytes())
            else:
                self._send(404, "text/plain", b"not found")
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length else b""

        if path == "/api/open":
            # browser-reachable: path strictly constrained to the jobs dir
            try:
                name = json.loads(body or b"{}").get("path", "")
            except json.JSONDecodeError:
                self._json({"error": "bad json"}, 400)
                return
            root = _jobs_dir
            if root is None:
                self._json({"error": "no jobs dir configured"}, 400)
                return
            cand = (root / name).resolve()
            if cand.parent != root.resolve() or cand.suffix != ".toml" \
                    or not cand.is_file():
                self._json({"error": "path outside the jobs dir"}, 400)
                return
            sid = _load_in_background(cand)
            self._json({"sid": sid})
        elif path == "/api/close":
            try:
                sid = json.loads(body or b"{}").get("sid", "")
            except json.JSONDecodeError:
                self._json({"error": "bad json"}, 400)
                return
            self._json({"closed": close_session(sid)})
        elif path == "/api/push":
            # process-to-process: requires the discovery-file token
            if not self._authed(parsed):
                self._send(403, "text/plain", b"bad token")
                return
            try:
                head, _, blob = body.partition(b"\n")
                h = json.loads(head)
                meta, lens = h["meta"], h["stage_bytes"]
                stocks, off = [], 0
                for ln in lens:
                    stocks.append(blob[off:off + ln])
                    off += ln
                if off != len(blob) or any(len(s) != ln for s, ln
                                           in zip(stocks, lens)):
                    raise ValueError("stock byte count mismatch")
                sid = push_session(meta["path"], meta, stocks)
            except (KeyError, ValueError, json.JSONDecodeError) as e:
                self._json({"error": str(e)}, 400)
                return
            with _lock:
                v = _sessions[sid].version
            self._json({"sid": sid, "version": v})
        elif path == "/api/invalidate":
            if not self._authed(parsed):
                self._send(403, "text/plain", b"bad token")
                return
            try:
                p = json.loads(body or b"{}")["path"]
            except (KeyError, json.JSONDecodeError):
                self._json({"error": "bad json"}, 400)
                return
            invalidate_session(p)
            self._json({"ok": True})
        else:
            self._send(404, "text/plain", b"not found")


def _write_discovery():
    global _discovery_path
    _discovery_path = DISCOVERY_FILE
    _discovery_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _discovery_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({
        "port": _port, "token": _TOKEN, "epoch": _EPOCH,
        "pid": os.getpid(), "jobs_dir": str(_jobs_dir) if _jobs_dir else None,
    }))
    tmp.chmod(0o600)
    tmp.replace(_discovery_path)


def start(port: int = 8323, jobs_dir: str | Path | None = None,
          discovery: bool = True) -> str:
    """Start (or reuse) the in-process server; returns its URL. Tries
    nearby ports if the requested one is held by another process. Writes
    the discovery file so OTHER processes join this server over HTTP
    instead of starting their own (see viewer/client.py)."""
    global _server, _port, _jobs_dir
    if jobs_dir is not None:
        _jobs_dir = Path(jobs_dir).resolve()
    elif _jobs_dir is None:
        guess = Path.cwd() / "jobs"
        _jobs_dir = guess.resolve() if guess.is_dir() else Path.cwd().resolve()
    if _server is not None:
        return f"http://localhost:{_port}/"
    last_err = None
    for p in ([port] if port == 0 else range(port, port + 10)):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), Handler)
        except OSError as e:
            last_err = e
            continue
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        _server, _port = srv, srv.server_address[1]
        if discovery:
            _write_discovery()
        return f"http://localhost:{_port}/"
    raise OSError(f"no free port in {port}..{port + 9}: {last_err}")


def running() -> bool:
    return _server is not None


def current_port() -> int | None:
    return _port


def token() -> str:
    return _TOKEN
