"""aces-cad-mcp over stdio for the tests: the aces-onshape Server helper, adapted (build spec section 21).

Server starts the server the way the installed aces-cad-mcp shim does (python -I -B, the package directory put first on
sys.path, aces_cad.mcp_server.main()), pumps its stdout lines into a queue and its stderr into a buffer, and keeps
every raw line it read. Notifications from the server (notifications/progress) are collected apart from replies, so
recv_reply() can wait for one id while progress notifications arrive. Not a test module.
"""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path


class Server:
    """aces-cad-mcp in a child process; one JSON-RPC line out per request."""

    def __init__(self, env: dict, cwd: Path, argv: list[str] | None = None):
        if argv is None:
            import base
            argv = base.entry_argv(*base.MCP_ARGV)
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     env=env, cwd=str(cwd))
        self.lines: queue.Queue = queue.Queue()
        self.raw: list[bytes] = []
        self.notifications: list[dict] = []
        self.pending: dict = {}
        self.stderr_chunks: list[bytes] = []
        self.n = 0
        self.threads = [threading.Thread(target=self._pump, daemon=True),
                        threading.Thread(target=self._drain, daemon=True)]
        for t in self.threads:
            t.start()

    @property
    def stderr(self) -> bytes:
        return b"".join(self.stderr_chunks)

    def _pump(self) -> None:
        for line in iter(self.proc.stdout.readline, b""):
            self.lines.put(line)
        self.lines.put(None)

    def _drain(self) -> None:
        for chunk in iter(lambda: self.proc.stderr.read(4096), b""):
            self.stderr_chunks.append(chunk)

    def write(self, data: bytes) -> None:
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def send(self, msg: dict) -> None:
        self.write(json.dumps(msg).encode("utf-8") + b"\n")

    def notify(self, method: str, params: dict | None = None) -> None:
        self.send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def recv(self, timeout: float = 60) -> dict:
        """The next line from the server, whatever it is (a reply or a notification)."""
        line = self.lines.get(timeout=timeout)
        if line is None:
            raise AssertionError("aces-cad-mcp closed stdout: " + self.stderr.decode("utf-8", "replace")[-2000:])
        self.raw.append(line)
        return json.loads(line)

    def recv_reply(self, mid, timeout: float = 600) -> dict:
        """The reply to id mid; notifications seen meanwhile go to .notifications, other replies to .pending."""
        if mid in self.pending:
            return self.pending.pop(mid)
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise AssertionError(f"no reply to id {mid!r} within {timeout} s")
            try:
                msg = self.recv(timeout=left)
            except queue.Empty:
                raise AssertionError(f"no reply to id {mid!r} within {timeout} s") from None
            if "method" in msg and "id" not in msg:
                self.notifications.append(msg)
            elif msg.get("id") == mid:
                return msg
            else:
                self.pending[msg.get("id")] = msg

    def next_id(self) -> int:
        self.n += 1
        return self.n

    def start_request(self, method: str, params: dict | None = None) -> int:
        mid = self.next_id()
        self.send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})
        return mid

    def request(self, method: str, params: dict | None = None, timeout: float = 600) -> dict:
        return self.recv_reply(self.start_request(method, params), timeout=timeout)

    def call(self, name: str, arguments: dict, timeout: float = 600) -> dict:
        reply = self.request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
        if "result" not in reply:
            raise AssertionError(f"tools/call {name} gave no result: {reply}")
        return reply["result"]

    def initialize(self, protocol: str = "2025-11-25") -> dict:
        init = self.request("initialize", {"protocolVersion": protocol, "capabilities": {},
                                           "clientInfo": {"name": "aces-cad tests (fixture)", "version": "0"}})
        self.notify("notifications/initialized")
        return init["result"]

    def close(self, timeout: float = 60) -> int:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        code = self.proc.wait(timeout=timeout)
        for t in self.threads:
            t.join(timeout=10)
        self.proc.stdout.close()
        self.proc.stderr.close()
        return code

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=30)

    def __enter__(self) -> "Server":
        return self

    def __exit__(self, *exc) -> None:
        if self.proc.poll() is None:
            try:
                self.close(timeout=30)
            except subprocess.TimeoutExpired:
                self.kill()
        for stream in (self.proc.stdout, self.proc.stderr, self.proc.stdin):
            try:
                stream.close()
            except (OSError, ValueError):
                pass


def tool_text(result: dict) -> str:
    return result["content"][0]["text"]


def images(result: dict) -> list[dict]:
    return [c for c in result.get("content", []) if c.get("type") == "image"]


if __name__ == "__main__":
    sys.exit("mcp_client.py is a test helper, not a program")
