"""A stand-in for the rig-state MCP server (192.168.1.65:8010/mcp) for tests.

Stateless streamable HTTP, JSON-RPC 2.0 over POST (cudax docs/UI-INTEGRATION.md section 3; the aces.fabric.rig_tool
shape, src/aces-base/aces/fabric.py:54-69). Replays tests/fixtures/rigstate-tools.json for tools/list and
rigstate-snapshot.json for tools/call rig_snapshot, with the reply id rewritten to the request id; any other tool
answers a JSON-RPC -32601 error. With `sse=True` the reply is text/event-stream ("event: message" + one "data:" line),
as the live server answers; otherwise application/json. Every request is recorded.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .. import FIXTURES


class FakeRigState:
    def __init__(self, sse: bool = False, fixtures_dir: Path = FIXTURES, delay_s: float = 0.0, fail_status: int | None = None):
        self.sse = sse
        self.fixtures_dir = fixtures_dir
        self.delay_s = delay_s
        self.fail_status = fail_status
        self.requests: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                return

            def _reply(self, status: int, obj: Any) -> None:
                if fake.sse:
                    data = ("event: message\ndata: " + json.dumps(obj) + "\n\n").encode("utf-8")
                    ctype = "text/event-stream"
                else:
                    data = json.dumps(obj).encode("utf-8")
                    ctype = "application/json"
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else None
                except (UnicodeDecodeError, json.JSONDecodeError):
                    body = None
                with fake.lock:
                    fake.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                          "body": body})
                if fake.delay_s:
                    time.sleep(fake.delay_s)
                if fake.fail_status:
                    self._reply(fake.fail_status, {"error": "fake_rigstate: failure requested by the test"})
                    return
                rid = body.get("id") if isinstance(body, dict) else None
                method = body.get("method") if isinstance(body, dict) else None
                if method == "tools/list":
                    obj = _load(fake.fixtures_dir / "rigstate-tools.json")
                elif method == "tools/call" and ((body.get("params") or {}).get("name") == "rig_snapshot"):
                    obj = _load(fake.fixtures_dir / "rigstate-snapshot.json")
                else:
                    name = (body.get("params") or {}).get("name") if isinstance(body, dict) else None
                    obj = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601,
                                                                   "message": f"fake_rigstate: tool {name or method} is not replayed"}}
                    self._reply(200, obj)
                    return
                obj = dict(obj, id=rid)
                self._reply(200, obj)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)


def _load(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
