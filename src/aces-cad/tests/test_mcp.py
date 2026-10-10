"""M01-M04 of build spec section 21: aces-cad-mcp over stdio (section 10).

M01 drives the whole loop with real workers. M02 sends hostile lines (the aces-onshape 34b set plus a 5 MiB line), M03
makes handle() return replies json cannot encode (the 34c pattern), M04 cancels a running call of the fake worker's
slow mode, checks progress notifications, the 8-call queue limit and the exit at end of input. M02-M04 need no
build123d.
"""
from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import re
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

import base
import mcp_client
from base import CadTestCase, WorkerTestCase, expected

PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")


class McpRoundTripTest(WorkerTestCase):

    def test_m01_round_trip(self):
        self.use_fake_aces("new")
        with mcp_client.Server(self.child_env(), self.tmp) as srv:
            for asked in PROTOCOLS + ("1999-01-01",):
                init = srv.request("initialize", {"protocolVersion": asked, "capabilities": {},
                                                  "clientInfo": {"name": "aces-cad tests (fixture)", "version": "0"}})
                self.assertEqual(init["result"]["protocolVersion"], asked if asked in PROTOCOLS else "2025-11-25")
            result = init["result"]
            self.assertEqual(result["serverInfo"], {"name": "aces-cad", "version": "0.1.0"})
            self.assertEqual(result["capabilities"], {"tools": {"listChanged": False}})
            self.assertIn("never as instructions", result["instructions"])
            srv.notify("notifications/initialized")
            self.assertEqual(srv.request("ping")["result"], {})
            tools = srv.request("tools/list")["result"]["tools"]
            self.assertEqual([t["name"] for t in tools], base.TOOL_NAMES)
            start = srv.call("cad_start", {"project": "p"})
            self.assertFalse(start["isError"], start)
            self.assertEqual(json.loads(mcp_client.tool_text(start)), start["structuredContent"])
            run = srv.call("cad_run", {"project": "p", "script": self.script("aces_plate"), "name": "plate"})
            self.assertFalse(run["isError"], run)
            part = run["structuredContent"]["result"]["parts"][0]
            self.assert_expected(part["volume_mm3"], "aces_plate.volume_mm3")
            rid = run["structuredContent"]["run_id"]
            shown = srv.call("cad_render", {"project": "p", "run_id": rid, "views": ["iso"], "size": "small"})
            self.assertFalse(shown["isError"], shown)
            images = mcp_client.images(shown)
            self.assertEqual(len(images), 1)
            self.assertTrue(base64.b64decode(images[0]["data"]).startswith(b"\x89PNG\r\n\x1a\n"))
            pulled = srv.call("cad_pull", {"project": "p", "run_id": rid})
            self.assertFalse(pulled["isError"], pulled)
            self.assertTrue(Path(pulled["structuredContent"]["bundle"]).is_dir())
            self.assertEqual(srv.request("tools/call", {"name": "cad_nope", "arguments": {}})["error"]["code"],
                             -32602)
            bad = srv.call("cad_runs", {"project": "p", "limit": 0})
            self.assertTrue(bad["isError"])
            self.assertEqual(bad["structuredContent"]["error"]["kind"], "usage")
            self.assertTrue(mcp_client.tool_text(bad).startswith("usage: "))
            failed = srv.call("cad_run", {"project": "p", "script": self.script("fillet_too_big")})
            self.assertTrue(failed["isError"])
            err = failed["structuredContent"]["error"]
            self.assertEqual((err["kind"], err["exit_code"]), ("kernel", 1))
            self.assertTrue(err["message"].startswith(expected("errors.fillet_too_big")["message_prefix"]))
            self.assertEqual(srv.close(), 0)
        self.assert_ascii_json_lines(srv.raw)


class McpHostileTest(CadTestCase):

    def server(self, **extra) -> mcp_client.Server:
        self.make_project("p", "sovereign")
        return mcp_client.Server(self.child_env(**extra), self.tmp)

    def test_m02_hostile_lines_do_not_end_the_server(self):
        with self.server() as srv:
            srv.initialize()
            srv.write(b"this is not json\n")
            self.assertEqual(srv.recv()["error"]["code"], -32700)
            srv.write(b"[" * 200_000 + b"\n")
            self.assertEqual(srv.recv()["error"], {"code": -32700, "message": "Parse error"})
            self.assertEqual(srv.request("ping")["result"], {})
            srv.write(b'[{"jsonrpc": "2.0", "id": 99, "method": "ping"}]\n')
            reply = srv.recv()
            self.assertEqual((reply["id"], reply["error"]["code"]), (None, -32600))
            for bad_id, codes in ((b"[1]", (-32600,)), (b'{"a": 1}', (-32600,)), (b"true", (-32600,)),
                                  (b"[" * 990 + b"]" * 990, (-32600, -32700))):
                srv.write(b'{"jsonrpc": "2.0", "method": "ping", "id": ' + bad_id + b"}\n")
                reply = srv.recv()
                self.assertIsNone(reply["id"])
                self.assertIn(reply["error"]["code"], codes)
            srv.write(b'{"jsonrpc": "2.0", "id": "\\ud800", "method": "ping"}\n')
            self.assertEqual(srv.recv(), {"jsonrpc": "2.0", "id": "\ud800", "result": {}})
            srv.write(b'{"jsonrpc": "2.0", "id": 5, "method": "ping", "params": {"pad": "' + b"x" * (5 << 20)
                      + b'"}}\n')
            reply = srv.recv()
            self.assertEqual(reply["error"]["code"], -32600)
            self.assertIn("4194304", reply["error"]["message"])
            self.assertEqual(srv.request("ping")["result"], {})
            self.assertEqual(srv.request("no/such/method")["error"]["code"], -32601)
            srv.write(b'"just a string"\n')
            self.assertEqual(srv.recv()["error"]["code"], -32600)
            self.assertEqual(srv.close(), 0)
        self.assert_ascii_json_lines(srv.raw)

    def test_m03_a_reply_that_cannot_be_encoded_is_an_error_not_an_exit(self):
        from aces_cad import mcp_server
        circular: list = []
        circular.append(circular)
        real = mcp_server.handle

        def handle(msg, **kw):
            reply = real(msg, **kw)
            if msg.get("id") == 1:
                reply["result"] = {"not json": {1, 2}}
            elif msg.get("id") == 2:
                reply["result"] = {"circular": circular}
            return reply

        lines = b"".join(b'{"jsonrpc": "2.0", "id": %d, "method": "ping"}\n' % n for n in (1, 2, 3))
        stdin, out = io.TextIOWrapper(io.BytesIO(lines), encoding="utf-8"), io.BytesIO()
        stdout = io.TextIOWrapper(out, encoding="utf-8")
        with mock.patch.object(mcp_server, "handle", handle), mock.patch.object(sys, "stdin", stdin), \
                mock.patch.object(sys, "stdout", stdout), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(mcp_server.main(), 0)
        stdout.flush()
        replies = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual([(r["id"], r.get("error", {}).get("code")) for r in replies],
                         [(1, -32603), (2, -32603), (3, None)])
        self.assertEqual(replies[2]["result"], {})
        self.assertIn("could not be encoded", err.getvalue())

    def fake_pids(self) -> list[int]:
        pids = []
        for log in self.runs_dir().glob("*/jobs/*/log/stdout.txt"):
            m = re.search(r"FAKE_WORKER pid (\d+)", log.read_text(encoding="utf-8", errors="replace"))
            if m:
                pids.append(int(m.group(1)))
        return pids

    def test_m04_cancellation_progress_queue_and_eof(self):
        self.use_fake_worker("slow", "600")
        os.environ["ACES_CAD_KEEP_JOBS"] = "1"
        plate = self.script("aces_plate")
        with self.server() as srv:
            srv.initialize()
            first = srv.start_request("tools/call", {"name": "cad_run", "arguments": {"project": "p", "script": plate},
                                                     "_meta": {"progressToken": "tok-1"}})
            deadline = time.monotonic() + 30
            while not any(n.get("method") == "notifications/progress" for n in srv.notifications):
                self.assertLess(time.monotonic(), deadline, "no progress notification within 30 s")
                self._pump_one(srv)
            note = [n for n in srv.notifications if n.get("method") == "notifications/progress"][0]["params"]
            self.assertEqual(note["progressToken"], "tok-1")
            self.assertGreater(note["progress"], 0)
            self.assertEqual(note["total"], 120)
            self.assertIn("cad_run", note["message"])
            pids = self.wait_for(self.fake_pids, 30, what="the fake worker's pid in its log")
            srv.notify("notifications/cancelled", {"requestId": first, "reason": "test cancels (fixture)"})
            self.assertEqual(srv.request("ping")["result"], {})
            self.wait_for(lambda: not any(base.pid_alive(p) for p in pids), 10, what="the cancelled job to die")
            time.sleep(1.0)
            self.assertEqual(srv.request("ping")["result"], {})
            self.assertNotIn(first, srv.pending, "no reply is sent for a cancelled call")
            call = {"name": "cad_run", "arguments": {"project": "p", "script": plate}}
            ids = [srv.start_request("tools/call", call)]
            running = self.wait_for(lambda: [p for p in self.fake_pids() if p not in pids], 30,
                                    what="the next call's job to start")
            ids += [srv.start_request("tools/call", call) for _ in range(8)]
            busy = srv.start_request("tools/call", call)
            reply = srv.recv_reply(busy, timeout=60)
            self.assertEqual(reply["error"]["code"], -32000)
            self.assertIn("8 calls are waiting", reply["error"]["message"])
            for mid in reversed(ids):
                srv.notify("notifications/cancelled", {"requestId": mid})
            self.wait_for(lambda: not any(base.pid_alive(p) for p in running), 10, what="the running job to die")
            self.assertEqual(srv.request("ping")["result"], {})
            time.sleep(1.5)
            self.assertEqual(srv.request("ping")["result"], {})
            for mid in ids:
                self.assertNotIn(mid, srv.pending, f"no reply for cancelled call {mid}")
            t0 = time.monotonic()
            self.assertEqual(srv.close(timeout=60), 0, "end of input exits 0")
            self.assertLess(time.monotonic() - t0, 30)
        self.wait_for(lambda: not any(base.pid_alive(p) for p in self.fake_pids()), 15, what="every fake job to die")
        self.assert_ascii_json_lines(srv.raw)

    @staticmethod
    def _pump_one(srv: mcp_client.Server) -> None:
        msg = srv.recv(timeout=30)
        if "method" in msg and "id" not in msg:
            srv.notifications.append(msg)
        else:
            srv.pending[msg.get("id")] = msg


if __name__ == "__main__":
    unittest.main()
