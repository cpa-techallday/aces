"""A stand-in for GRID CONTROL rev B (the Node service at 192.168.1.65:3200) for tests.

Replays the 2026-10-11 captures of tests/fixtures: GET /api/status (gc-status.json), /api/modes (gc-modes.json),
/api/telemetry (gc-telemetry.json), /api/switch/status (gc-switch-status.json). Records every request as
{method, path, headers (lower-cased), body (parsed JSON or None)}; tests assert on the recorded x-gc-token header
without ever printing it.

Protected routes (POST /api/switch, POST /api/auto, GET /api/decisions) answer 401 unless the request carries
x-gc-token equal to the token the fake was given. /api/decisions has no capture and answers 404 with a note saying so.
POST /api/switch records the body and answers `switch_reply` (status `switch_status`): that reply is a test stand-in,
not a capture (GRID CONTROL's reply shape was not captured on 2026-10-11), and tests assert only the console's
behaviour. POST /api/auto answers 403 and is recorded, so a test can prove the console never calls it.

Scenarios: `apply_scenario(path, name, extra=None)` loads tests/fakes/scenarios/<name> (derived: true, derived_from,
changes), applies `changes` (and `extra`) to the fixture body and serves that for `path` until `clear(path)`.
"""
from __future__ import annotations

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .. import FIXTURES, SCENARIOS

ROUTES = {"/api/status": "gc-status.json", "/api/modes": "gc-modes.json", "/api/telemetry": "gc-telemetry.json",
          "/api/switch/status": "gc-switch-status.json"}
PROTECTED = {("GET", "/api/decisions"), ("POST", "/api/switch"), ("POST", "/api/auto")}


def load_fixture(name: str, directory: Path = FIXTURES) -> Any:
    with open(directory / name, encoding="utf-8") as fh:
        return json.load(fh)


def load_scenario(name: str) -> dict[str, Any]:
    with open(SCENARIOS / name, encoding="utf-8") as fh:
        sc = json.load(fh)
    if sc.get("derived") is not True or "derived_from" not in sc or "changes" not in sc:
        raise ValueError(f"scenario {name} must carry derived:true, derived_from and changes")
    return sc


class FakeGC:
    def __init__(self, token: str | None = None, fixtures_dir: Path = FIXTURES):
        self.token = token
        self.fixtures_dir = fixtures_dir
        self.requests: list[dict[str, Any]] = []
        self.overrides: dict[str, Any] = {}
        self.down: set[str] = set()
        self.switch_reply: Any = {"ok": True, "started": True,
                                  "note": "fake_gc test stand-in: GRID CONTROL's POST /api/switch reply was not captured"}
        self.switch_status = 200
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                return

            def _record(self, method: str, body: Any) -> None:
                with fake.lock:
                    fake.requests.append({"method": method, "path": self.path.split("?")[0],
                                          "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})

            def _send(self, status: int, obj: Any) -> None:
                data = json.dumps(obj).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _authorised(self) -> bool:
                return fake.token is not None and self.headers.get("x-gc-token") == fake.token

            def do_GET(self) -> None:
                path = self.path.split("?")[0]
                self._record("GET", None)
                if path in fake.down:
                    self._send(503, {"error": "fake_gc: route marked down by the test"})
                    return
                if ("GET", path) in PROTECTED and not self._authorised():
                    self._send(401, {"error": "unauthorized (fake_gc)"})
                    return
                if path in fake.overrides:
                    self._send(200, fake.overrides[path])
                    return
                if path == "/api/decisions":
                    self._send(404, {"error": "not captured: GRID CONTROL /api/decisions has no 2026-10-11 capture (fake_gc)"})
                    return
                name = ROUTES.get(path)
                if name is None:
                    self._send(404, {"error": f"fake_gc: no route {path}"})
                    return
                self._send(200, load_fixture(name, fake.fixtures_dir))

            def do_POST(self) -> None:
                path = self.path.split("?")[0]
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else None
                except (UnicodeDecodeError, json.JSONDecodeError):
                    body = raw.decode("utf-8", "replace")
                self._record("POST", body)
                if ("POST", path) in PROTECTED and not self._authorised():
                    self._send(401, {"error": "unauthorized (fake_gc)"})
                    return
                if path == "/api/switch":
                    self._send(fake.switch_status, fake.switch_reply)
                    return
                if path == "/api/auto":
                    self._send(403, {"error": "fake_gc: /api/auto must never be called by the console"})
                    return
                self._send(404, {"error": f"fake_gc: no route POST {path}"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    # --- scenarios -------------------------------------------------------------------------------------------------------

    def apply_scenario(self, path: str, name: str, extra: dict[str, Any] | None = None) -> Any:
        sc = load_scenario(name)
        body = copy.deepcopy(load_fixture(sc["derived_from"], self.fixtures_dir))
        for k, v in sc["changes"].items():
            body[k] = v
        for k, v in (extra or {}).items():
            body[k] = v
        with self.lock:
            self.overrides[path] = body
        return body

    def clear(self, path: str | None = None) -> None:
        with self.lock:
            if path is None:
                self.overrides.clear()
                self.down.clear()
            else:
                self.overrides.pop(path, None)
                self.down.discard(path)

    # --- inspection ------------------------------------------------------------------------------------------------------

    def requests_for(self, method: str, path: str) -> list[dict[str, Any]]:
        with self.lock:
            return [r for r in self.requests if r["method"] == method and r["path"] == path]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
