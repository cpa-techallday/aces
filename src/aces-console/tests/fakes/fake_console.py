"""A stand-in for the SIMLAB console (the Starlette GET API at 192.168.1.65:8032, cudax simlab/console/app.py) for tests.

Replays the 2026-10-11 captures of tests/fixtures: /api/summary, /api/capacity, /api/experiments, /api/registry,
/api/evidence, /api/evidence/verify, /api/audit, /api/telemetry, /api/modes, /api/experiments/{eid}?root= (the one
captured experiment, aces-cal-air-03d; anything else answers the console's own 404 {"error": "unknown experiment"})
and /api/case/{eid}/{cid}?root= (aces-cal-air-03d/c000; else 404 {"error": "unknown case"}).

/api/file?p=<path> serves the bytes of a real local file the test maps to that console-side path (`fake.files[p] =
Path(...)`); the rig's own files are not available on the desktop, so an unmapped p answers the console's wording
"not found or outside the output roots" (404, text/plain). Every request is recorded as {method, path, query, headers}
so a test can assert which `p` the console forwarded. `down` marks paths that answer 503; `overrides` replaces a body.
"""
from __future__ import annotations

import json
import mimetypes
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .. import FIXTURES

ROUTES = {"/api/summary": "simlab-summary.json", "/api/capacity": "simlab-capacity.json", "/api/experiments": "simlab-experiments.json",
          "/api/registry": "simlab-registry.json", "/api/evidence": "simlab-evidence.json", "/api/evidence/verify": "simlab-evidence-verify.json",
          "/api/audit": "simlab-audit.json", "/api/telemetry": "simlab-telemetry.json", "/api/modes": "simlab-modes.json"}
NOT_FOUND_FILE = "not found or outside the output roots"   # app.py:356


def load_fixture(name: str, directory: Path = FIXTURES) -> Any:
    with open(directory / name, encoding="utf-8") as fh:
        return json.load(fh)


class FakeConsole:
    def __init__(self, fixtures_dir: Path = FIXTURES):
        self.fixtures_dir = fixtures_dir
        self.requests: list[dict[str, Any]] = []
        self.overrides: dict[str, Any] = {}
        self.down: set[str] = set()
        self.files: dict[str, Path] = {}
        self.lock = threading.Lock()
        self.detail = load_fixture("simlab-experiment-detail.json", fixtures_dir)
        self.case = load_fixture("simlab-case-detail.json", fixtures_dir)
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                return

            def _send(self, status: int, data: bytes, ctype: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _json(self, status: int, obj: Any) -> None:
                self._send(status, json.dumps(obj).encode("utf-8"), "application/json")

            def do_GET(self) -> None:
                path, _, qs = self.path.partition("?")
                query = urllib.parse.parse_qs(qs)
                with fake.lock:
                    fake.requests.append({"method": "GET", "path": path, "query": query,
                                          "headers": {k.lower(): v for k, v in self.headers.items()}})
                if path in fake.down:
                    self._json(503, {"error": "fake_console: route marked down by the test"})
                    return
                if path in fake.overrides:
                    self._json(200, fake.overrides[path])
                    return
                if path in ROUTES:
                    self._json(200, load_fixture(ROUTES[path], fake.fixtures_dir))
                    return
                parts = path.strip("/").split("/")
                root = (query.get("root") or [None])[0]
                if len(parts) == 3 and parts[:2] == ["api", "experiments"]:
                    if parts[2] == fake.detail["id"] and root in (None, fake.detail["root"]):
                        self._json(200, fake.detail)
                    else:
                        self._json(404, {"error": "unknown experiment"})
                    return
                if len(parts) == 4 and parts[:2] == ["api", "case"]:
                    exp = fake.case.get("experiment") or {}
                    if parts[2] == exp.get("id") and parts[3] == fake.case["case_id"] and root in (None, exp.get("root")):
                        self._json(200, fake.case)
                    else:
                        self._json(404, {"error": "unknown case"})
                    return
                if path == "/api/file":
                    p = (query.get("p") or [""])[0]
                    local = fake.files.get(p)
                    if local is None or not Path(local).is_file():
                        self._send(404, NOT_FOUND_FILE.encode("utf-8"), "text/plain; charset=utf-8")
                        return
                    ctype = mimetypes.guess_type(str(local))[0] or "application/octet-stream"
                    self._send(200, Path(local).read_bytes(), ctype)
                    return
                self._json(404, {"error": f"fake_console: no route {path}"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def requests_for(self, path: str) -> list[dict[str, Any]]:
        with self.lock:
            return [r for r in self.requests if r["path"] == path]

    def clear(self) -> None:
        with self.lock:
            self.overrides.clear()
            self.down.clear()
            self.files.clear()
            self.requests.clear()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
