"""aces-simlab remote: a small MCP client (streamable HTTP, JSON-RPC 2.0) for the rig's simlab and rig-state servers.

  aces-simlab remote info  [--server simlab|rig-state] [--url URL]     server name, version, protocol, instructions
  aces-simlab remote tools [--server ...]                              the tools the server exposes to this caller
  aces-simlab remote call TOOL ['{"arg": ...}'] [--server ...] [--json]

URLs come from the aces config (server.simlab_mcp, server.rig_state); --url overrides. The simlab bearer token comes
from simlab_token(): $ACES_SIMLAB_TOKEN, else $SIMLAB_MCP_TOKEN, else ACES_SIMLAB_TOKEN= or SIMLAB_MCP_TOKEN= in
$ACES_USER_CONFIG/simlab.env (default ~/.config/aces/simlab.env). The token is only ever sent as Authorization: Bearer;
nothing here prints, logs or raises it. Standard library only: the mcp SDK is not in this venv because it pulls in the
server stack (uvicorn, starlette). Calls reach the rig: sim_submit and sim_sweep queue real work, and every call is
audited there.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__, server_url

PROTOCOL = "2025-06-18"
SERVERS = {"simlab": "simlab_mcp", "rig-state": "rig_state"}
TOKEN_ENVS = ("ACES_SIMLAB_TOKEN", "SIMLAB_MCP_TOKEN")
TOKEN_FILE = "simlab.env"


class MCPError(RuntimeError):
    """A failed MCP request. `kind` is "http" (the server answered with an HTTP error; `status` is the code),
    "network" (no answer: refused, reset, timed out), "rpc" (a JSON-RPC error object) or "protocol" (an answer that is
    not a JSON-RPC reply to this request). The message never contains the bearer token."""

    def __init__(self, message: str, *, kind: str = "protocol", status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


def user_config_dir() -> Path:
    return Path(os.environ.get("ACES_USER_CONFIG") or (Path.home() / ".config" / "aces"))


def _env_file_token(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    found: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if " #" in value and not value.startswith(("'", '"')):
            value = value.split(" #", 1)[0].rstrip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key in TOKEN_ENVS and value:
            found.setdefault(key, value)
    for key in TOKEN_ENVS:
        if key in found:
            return found[key]
    return None


def simlab_token(*, warn=None) -> tuple[str | None, str]:
    """(token or None, source): $ACES_SIMLAB_TOKEN, then $SIMLAB_MCP_TOKEN, then $ACES_USER_CONFIG/simlab.env (KEY=VALUE
    lines, # comments, optional quotes). source is "ACES_SIMLAB_TOKEN", "SIMLAB_MCP_TOKEN", "simlab.env" or "not set".
    On POSIX a group- or world-readable simlab.env gets one warning through `warn` (default: stderr); the warning names
    the file, never the token."""
    for key in TOKEN_ENVS:
        value = (os.environ.get(key) or "").strip()
        if value:
            return value, key
    path = user_config_dir() / TOKEN_FILE
    if path.is_file():
        token = _env_file_token(path)
        if token:
            if os.name == "posix":
                try:
                    mode = path.stat().st_mode
                except OSError:
                    mode = 0
                if mode & (stat.S_IRGRP | stat.S_IROTH):
                    text = (f"warning: {path} is readable by other users (mode {stat.S_IMODE(mode):o}); "
                            f"chmod 600 {path}")
                    if warn is not None:
                        warn(text)
                    else:
                        print(text, file=sys.stderr)
            return token, TOKEN_FILE
    return None, "not set"


class Client:
    def __init__(self, url: str, token: str | None = None, timeout: float = 60.0):
        self.url, self.timeout = url, timeout
        self._token = token
        self.session: str | None = None
        self.protocol: str | None = None
        self._id = 0

    @property
    def token(self) -> str | None:
        return self._token

    def __repr__(self) -> str:  # never the token
        return f"Client({self.url!r}, token {'set' if self._token else 'not set'})"

    def _post(self, payload: dict):
        hdrs = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self._token:
            hdrs["Authorization"] = f"Bearer {self._token}"
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
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # a reset while reading the error body
                detail = ""
            raise MCPError(f"HTTP {exc.code} from {self.url}: {detail}", kind="http", status=int(exc.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise MCPError(f"cannot reach {self.url}: {getattr(exc, 'reason', exc)}", kind="network") from None
        return None if "id" not in payload else self._result(body, payload["id"])

    @staticmethod
    def _result(body: str, rid: int):
        text = body.strip()
        if text.startswith(("{", "[")):
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise MCPError(f"the reply is not JSON ({exc.msg})", kind="protocol") from None
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
                    raise MCPError(f"JSON-RPC error {err.get('code')}: {err.get('message')}", kind="rpc")
                return msg.get("result")
        raise MCPError(f"no JSON-RPC response for request {rid} in the reply ({len(text)} bytes)", kind="protocol")

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
    token, source = simlab_token() if a.server == "simlab" else (None, "not set")
    client = Client(url, token, a.timeout)
    try:
        info = client.initialize()
        if a.action == "info":
            srv = info.get("serverInfo") or {}
            print(f"{srv.get('name')} {srv.get('version')} at {url} (protocol {client.protocol}, "
                  f"token {'sent from ' + source if token else 'not set'})")
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
