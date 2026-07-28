"""Viewer sessions: one server, many jobs, every process joins.

Exercises the real HTTP protocol end to end (no browser needed):
  - discovery: the server records itself; the client health-checks the
    record (epoch match) and finds it
  - push over HTTP with the token (the cross-process collaboration path);
    pushing the same job path JOINS its session instead of forking one
  - per-session state/stock endpoints under the version/409 discipline
  - invalidate marks a session STALE
  - a push without the token is refused
  - /api/open is path-constrained to the jobs dir (no traversal), loads in
    the background, and lands on a ready PASS session
  - /api/close removes a session

Run: .venv/bin/python tests/viewer_sessions.py
"""
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# the discovery file must never touch the developer's real one
_tmp = tempfile.TemporaryDirectory(prefix="clauderacam-viewer-test-")
os.environ["CLAUDERACAM_VIEWER_FILE"] = str(Path(_tmp.name) / "viewer.json")

from clauderacam import emit, engine, job as jobmod, stages, \
    verify as verifymod  # noqa: E402
from clauderacam.viewer import client, server  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {name}: {'OK' if ok else 'FAIL'}"
          + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def get(url, expect=200):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def post(url, body: bytes, expect=200):
    req = urllib.request.Request(url, data=body)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


if not (REPO / "assets" / "generated" / "dome.stl").exists():
    import runpy
    runpy.run_path(str(REPO / "assets" / "generate_references.py"),
                   run_name="__main__")

print("server start + discovery")
url = server.start(port=0, jobs_dir=REPO / "jobs")
check("ephemeral port", url.startswith("http://localhost:"))
found = client.discover()
check("client discovers the server",
      bool(found) and found["url"] == url, str(found and found["url"]))

print("push over HTTP (the cross-process path)")
dome = jobmod.load(REPO / "jobs" / "dome.toml")
emit.write(dome, engine.generate_ops(dome))
report = verifymod.verify(dome)
meta, stocks = stages.viewer_payload(dome, report)
blobs = server.encode_stocks(stocks)
head = json.dumps({"meta": meta,
                   "stage_bytes": [len(b) for b in blobs]}).encode()
body = head + b"\n" + b"".join(blobs)
tok = urllib.parse.quote(found["token"])
code, resp = post(url + "api/push?token=" + tok, body)
check("authorized push accepted", code == 200, str(code))
sid = json.loads(resp)["sid"]
code, _ = post(url + "api/push?token=WRONG", body)
check("push without the token refused", code == 403, str(code))

code, resp = get(url + "api/sessions")
rows = json.loads(resp)["sessions"]
check("one ready session", len(rows) == 1
      and rows[0]["status"] == "ready" and rows[0]["ok"] is True, str(rows))

print("session state/stock endpoints")
code, resp = get(url + f"api/session/{sid}/state")
st = json.loads(resp)
check("state has the stage model", st["nstages"] == len(blobs)
      and st["job"] == "dome" and len(st["stages"]) == st["nstages"])
v = urllib.parse.quote(st["version"])
sizes_ok = True
for k in range(st["nstages"]):
    code, blob = get(url + f"api/session/{sid}/stock?v={v}&stage={k}")
    sizes_ok &= code == 200 and len(blob) == st["n"] ** 2 * 4
check("every stage buffer served at n²", sizes_ok)
code, _ = get(url + f"api/session/{sid}/stock?v=bogus&stage=0")
check("stale version -> 409", code == 409, str(code))
code, _ = get(url + f"api/session/{sid}/stock?v={v}&stage=99")
check("bad stage -> 404", code == 404, str(code))

print("join semantics + second session + invalidate")
code, resp = post(url + "api/push?token=" + tok, body)
check("same path joins the same session",
      json.loads(resp)["sid"] == sid)
ripple = jobmod.load(REPO / "jobs" / "ripple.toml")
emit.write(ripple, engine.generate_ops(ripple))
ripple_report = verifymod.verify(ripple)
pushed = client.push_job(ripple, ripple_report)
check("client.push_job lands a second session", pushed is not None
      and pushed[1] != sid, str(pushed))

print("program download (the verified bytes, never the live file)")
rsid = pushed[1]
req = urllib.request.Request(url + f"api/session/{rsid}/program")
with urllib.request.urlopen(req, timeout=10) as r:
    disp = r.headers.get("Content-Disposition", "")
    got = r.read()
check("download serves the exact verified bytes",
      got == ripple_report.program.encode(), f"{len(got)} bytes")
check("download names the .nc file", "ripple" in disp, disp)
original = ripple.out.read_bytes()
ripple.out.write_bytes(b"G21\nMUTATED AFTER VERIFY\n")
with urllib.request.urlopen(url + f"api/session/{rsid}/program",
                            timeout=10) as r:
    got2 = r.read()
ripple.out.write_bytes(original)
check("disk mutation cannot reach the download", got2 == got)
code, _ = get(url + f"api/session/{sid}/program")
check("programless session -> 404", code == 404, str(code))
code, resp = get(url + "api/sessions")
rows = json.loads(resp)["sessions"]
check("two sessions listed", len(rows) == 2, str(len(rows)))
code, _ = post(url + "api/invalidate?token=" + tok,
               json.dumps({"path": str(dome.path)}).encode())
code, resp = get(url + f"api/session/{sid}/state")
st = json.loads(resp)
check("invalidate marks the session STALE",
      st["stale"] is True and st["ok"] is None)

print("browser open endpoint")
code, resp = post(url + "api/open",
                  json.dumps({"path": "../CLAUDE.md"}).encode())
check("path traversal refused", code == 400, str(code))
code, resp = post(url + "api/open",
                  json.dumps({"path": "no-such-job.toml"}).encode())
check("missing file refused", code == 400, str(code))
code, resp = post(url + "api/open",
                  json.dumps({"path": "dome.toml"}).encode())
check("valid open accepted", code == 200, str(code))
osid = json.loads(resp)["sid"]
check("open joins the existing dome session", osid == sid)
deadline = time.time() + 300
status = None
while time.time() < deadline:
    code, resp = get(url + f"api/session/{osid}/state")
    status = json.loads(resp)["status"]
    if status in ("ready", "error"):
        break
    time.sleep(0.5)
code, resp = get(url + f"api/session/{osid}/state")
st = json.loads(resp)
check("background load reaches ready + un-stales", status == "ready"
      and st["ok"] is True and not st.get("stale"),
      f"status={status} err={json.loads(resp).get('error')}")

print("close")
code, resp = post(url + "api/close", json.dumps({"sid": sid}).encode())
check("close removes the session", json.loads(resp)["closed"] is True)
code, resp = get(url + "api/sessions")
check("one session left", len(json.loads(resp)["sessions"]) == 1)

code, resp = get(url + "api/files")
files = json.loads(resp)["files"]
check("files endpoint lists job tomls", "dome.toml" in files
      and all(f.endswith(".toml") for f in files))

print("VIEWER SESSIONS " + ("FAIL: " + ", ".join(fails) if fails
                            else "PASS"))
sys.exit(1 if fails else 0)
