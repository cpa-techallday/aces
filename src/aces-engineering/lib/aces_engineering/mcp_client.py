"""aces-simlab remote: a small MCP client (streamable HTTP, JSON-RPC 2.0) for the rig's simlab and rig-state servers.

  aces-simlab remote info  [--server simlab|rig-state] [--url URL]     server name, version, protocol, instructions
  aces-simlab remote tools [--server ...]                              the tools the server exposes to this caller
  aces-simlab remote call TOOL ['{"arg": ...}'] [--server ...] [--json]

URLs come from the aces config (server.simlab_mcp, server.rig_state); --url overrides. The simlab bearer token is read
from $SIMLAB_MCP_TOKEN. Standard library only: the mcp SDK is not in this venv because it pulls in the server stack
(uvicorn, starlette). Calls reach the rig: sim_submit and sim_sweep queue real work, and every call is audited there.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from . import __version__, server_url

PROTOCOL = "2025-06-18"
SERVERS = {"simlab": "simlab_mcp", "rig-state": "rig_state"}


class MCPError(RuntimeError):
    pass


class Client:
    def __init__(self, url: str, token: str | None = None, timeout: float = 60.0):
        self.url, self.token, self.timeout = url, token, timeout
        self.session: str | None = None
        self.protocol: str | None = None
        self._id = 0

    def _post(self, payload: dict):
        hdrs = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.token:
            hdrs["Authorization"] = f"Bearer {self.token}"
        if self.session:
            hdrs["Mcp-Session-Id"] = self.session
        if self.protocol:
            hdrs["MCP-Protocol-Version"] = self.protocol
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode("utf-8"), headers=hdrs, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (LAN endpoint from config)
                self.session = resp.headers.get("Mcp-Session-Id") or self.session
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise MCPError(f"HTTP {exc.code} from {self.url}: {exc.read().decode('utf-8', 'replace')[:300]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise MCPError(f"cannot reach {self.url}: {getattr(exc, 'reason', exc)}") from exc
        return None if "id" not in payload else self._result(body, payload["id"])

    @staticmethod
    def _result(body: str, rid: int):
        text = body.strip()
        if text.startswith(("{", "[")):
            data = json.loads(text)
            msgs = data if isinstance(data, list) else [data]
        else:  # text/event-stream: one JSON-RPC message per data: line
            msgs = []
            for line in text.splitlines():
                if line.startswith("data:"):
                    try:
                        msgs.append(json.loads(line[5:].strip()))
                    except json.JSONDecodeError:
                        continue
        for msg in msgs:
            if isinstance(msg, dict) and msg.get("id") == rid:
                if "error" in msg:
                    err = msg["error"] or {}
                    raise MCPError(f"JSON-RPC error {err.get('code')}: {err.get('message')}")
                return msg.get("result")
        raise MCPError(f"no JSON-RPC response for request {rid} in the reply ({len(text)} bytes)")

    def request(self, method: str, params: dict | None = None):
        self._id += 1
        return self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})

    def initialize(self) -> dict:
        res = self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                          "clientInfo": {"name": "aces-simlab", "version": __version__}}) or {}
        self.protocol = res.get("protocolVersion") or PROTOCOL
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return res

    def tools(self) -> list:
        return (self.request("tools/list") or {}).get("tools", [])

    def call(self, name: str, arguments: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments}) or {}


def render(result: dict) -> str:
    if result.get("structuredContent") is not None:
        return json.dumps(result["structuredContent"], indent=2, default=str)
    texts = [b.get("text", "") for b in result.get("content") or [] if b.get("type") == "text"]
    return "\n".join(texts) if texts else json.dumps(result, indent=2, default=str)


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(prog="aces-simlab remote", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["info", "tools", "call"])
    ap.add_argument("tool", nargs="?")
    ap.add_argument("arguments", nargs="?", default="{}", help="JSON object of tool arguments")
    ap.add_argument("--server", choices=sorted(SERVERS), default="simlab")
    ap.add_argument("--url", default=None, help="endpoint override (default: from the aces config)")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--json", action="store_true", help="print the raw JSON-RPC result")
    a = ap.parse_args(argv)
    url = a.url or server_url(SERVERS[a.server])
    if not url:
        print(f"aces-simlab remote: no URL for server.{SERVERS[a.server]} in the aces config; pass --url", file=sys.stderr)
        return 2
    token = os.environ.get("SIMLAB_MCP_TOKEN") if a.server == "simlab" else None
    client = Client(url, token, a.timeout)
    try:
        info = client.initialize()
        if a.action == "info":
            srv = info.get("serverInfo") or {}
            print(f"{srv.get('name')} {srv.get('version')} at {url} (protocol {client.protocol}, "
                  f"token {'sent' if token else 'not set'})")
            if info.get("instructions"):
                print(info["instructions"])
        elif a.action == "tools":
            for t in client.tools():
                first = ((t.get("description") or "").splitlines() or [""])[0]
                print(f"{t.get('name', '?'):24s} {first[:100]}")
        else:
            if not a.tool:
                ap.error("call needs a tool name")
            res = client.call(a.tool, json.loads(a.arguments))
            print(json.dumps(res, indent=2, default=str) if a.json else render(res))
            return 1 if res.get("isError") else 0
    except (MCPError, json.JSONDecodeError) as exc:
        print(f"aces-simlab remote: {exc}", file=sys.stderr)
        return 1
    return 0
