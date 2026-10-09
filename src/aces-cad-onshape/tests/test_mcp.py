"""Tests 34, 35 and 56 of the spec: aces-onshape-mcp over stdio, run as a child process against the fake."""
from __future__ import annotations

import contextlib
import io
import json
import queue
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

import fake_onshape
from fake_onshape import ACCESS, FIXTURE_HEADER, PLATE_CODE, SECRET, AdapterTestCase

from aces_onshape import mcp_server

TOOL_NAMES = ["onshape_whoami", "onshape_documents", "onshape_create_document", "onshape_elements",
              "onshape_featurestudio_read", "onshape_featurestudio_write", "onshape_featurescript_eval",
              "onshape_features", "onshape_add_feature", "onshape_add_custom_feature", "onshape_update_feature",
              "onshape_delete_feature", "onshape_version_create", "onshape_massproperties", "onshape_export_step",
              "onshape_export_stl", "onshape_pull"]
WRITE_TOOLS = {"onshape_create_document", "onshape_featurestudio_write", "onshape_add_feature",
               "onshape_add_custom_feature", "onshape_update_feature", "onshape_delete_feature",
               "onshape_version_create"}
PROJECT_TOOLS = WRITE_TOOLS | {"onshape_featurescript_eval", "onshape_export_step", "onshape_export_stl",
                               "onshape_pull"}
FEATURE = {"btType": "BTMFeature-134", "featureType": "fixtureFeature", "name": "Fixture feature", "namespace": "",
           "parameters": []}


class Server:
    """aces-onshape-mcp in a child process; one JSON-RPC line out per request, read with a 30 s guard."""

    def __init__(self, env: dict, cwd: Path):
        self.proc = subprocess.Popen([sys.executable, "-m", "aces_onshape.mcp_server"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=str(cwd))
        self.lines: queue.Queue = queue.Queue()
        self.raw: list[bytes] = []
        self.stderr = b""
        self.n = 0
        self.threads = [threading.Thread(target=self._pump, daemon=True),
                        threading.Thread(target=self._drain, daemon=True)]
        for t in self.threads:
            t.start()

    def _pump(self) -> None:
        for line in iter(self.proc.stdout.readline, b""):
            self.lines.put(line)
        self.lines.put(None)

    def _drain(self) -> None:
        self.stderr = self.proc.stderr.read()

    def write(self, data: bytes) -> None:
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def send(self, msg: dict) -> None:
        self.write(json.dumps(msg).encode("utf-8") + b"\n")

    def recv(self, timeout: float = 30) -> dict:
        line = self.lines.get(timeout=timeout)
        if line is None:
            raise AssertionError("aces-onshape-mcp closed stdout: " + self.stderr.decode("utf-8", "replace"))
        self.raw.append(line)
        return json.loads(line)

    def request(self, method: str, params: dict | None = None) -> dict:
        self.n += 1
        self.send({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}})
        reply = self.recv()
        if reply.get("id") != self.n:
            raise AssertionError(f"reply to request {reply.get('id')}, expected {self.n}")
        return reply

    def call(self, name: str, arguments: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments})["result"]

    def close(self) -> int:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        code = self.proc.wait(timeout=30)
        for t in self.threads:
            t.join(timeout=10)
        self.proc.stdout.close()
        self.proc.stderr.close()
        return code

    def __enter__(self) -> "Server":
        return self

    def __exit__(self, *exc) -> None:
        if self.proc.poll() is None:
            try:
                self.close()
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=30)


class McpTest(AdapterTestCase):

    def setUp(self):
        super().setUp()
        self.make_project("proj", "commercial")

    def server(self, keys: bool = True) -> Server:
        return Server(self.child_env(keys=keys), self.tmp)

    def test_34_round_trip(self):
        did, wid, ps = self.fake.seed_document()
        with self.server() as srv:
            for asked, answered in (("2025-06-18", "2025-06-18"), ("2025-11-25", "2025-11-25"),
                                    ("1999-01-01", "2025-11-25")):
                init = srv.request("initialize", {"protocolVersion": asked, "capabilities": {},
                                                  "clientInfo": {"name": "test (fixture)", "version": "0"}})["result"]
                self.assertEqual(init["protocolVersion"], answered)
            self.assertEqual(init["serverInfo"], {"name": "aces-onshape", "version": "0.1.0"})
            self.assertIn("confirm_foreign", init["instructions"])
            self.assertEqual(init["capabilities"], {"tools": {"listChanged": False}})
            srv.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            self.assertEqual(srv.request("ping")["result"], {})
            tools = srv.request("tools/list")["result"]["tools"]
            self.assertEqual([t["name"] for t in tools], TOOL_NAMES)
            by_name = {t["name"]: t for t in tools}
            for tool in tools:
                schema = tool["inputSchema"]
                self.assertEqual((schema["type"], schema["additionalProperties"]), ("object", False))
                writes = tool["description"].startswith("WRITES to Onshape:")
                self.assertEqual(writes, tool["name"] in WRITE_TOOLS, tool["name"])
                self.assertTrue(writes or tool["description"].startswith(("Read-only:",
                                                                          "Read-only in Onshape; writes local files:")))
                self.assertEqual(("project" in schema["required"]), tool["name"] in PROJECT_TOOLS, tool["name"])
                self.assertIs(tool["annotations"]["openWorldHint"], True)
            self.assertIs(by_name["onshape_featurestudio_write"]["annotations"]["idempotentHint"], False)
            self.assertIs(by_name["onshape_whoami"]["annotations"]["readOnlyHint"], True)
            who = srv.call("onshape_whoami", {})
            self.assertFalse(who["isError"])
            self.assertEqual(who["structuredContent"]["id"], self.fake.user_id)
            self.assertEqual(who["content"][0]["text"],
                             json.dumps(who["structuredContent"], ensure_ascii=False, separators=(",", ":")))
            pulled = srv.call("onshape_pull", {"ref": f"onshape://{did}/{wid}/{ps}", "project": "proj"})
            self.assertFalse(pulled["isError"], pulled)
            bundle = Path(pulled["structuredContent"]["bundle"])
            self.assertEqual(sorted(p.name for p in bundle.iterdir()),
                             ["outcome.json", "parameters.json", "part.step", "source.json"])
            self.make_project("sov", "sovereign")
            refused = srv.call("onshape_pull", {"ref": f"onshape://{did}/{wid}/{ps}", "project": "sov"})
            self.assertTrue(refused["isError"])
            self.assertEqual(refused["structuredContent"]["error"]["kind"], "class_refused")
            self.assertTrue(refused["content"][0]["text"].startswith("class_refused: "))
            unknown = srv.request("tools/call", {"name": "onshape_nope", "arguments": {}})
            self.assertEqual(unknown["error"]["code"], -32602)
            for bad in ("x" * 24, did + "\n"):
                r = srv.call("onshape_elements", {"did": bad, "wvm": "w", "wvmid": wid})
                self.assertTrue(r["isError"])
                self.assertEqual(r["structuredContent"]["error"]["kind"], "usage")
            r = srv.call("onshape_elements", {"did": did, "wvm": "w", "wvmid": wid, "extra": 1})
            self.assertEqual(r["structuredContent"]["error"]["kind"], "usage")
            self.assertEqual(srv.request("no/such/method")["error"]["code"], -32601)
            srv.write(b"this is not json\n")
            self.assertEqual(srv.recv()["error"]["code"], -32700)
            srv.write(b'[{"jsonrpc": "2.0", "id": 99, "method": "ping"}]\n')
            self.assertEqual(srv.recv()["error"]["code"], -32600)
            self.fake.inject(400, path_contains="/elements", body={"message": f"bad request (fixture) {SECRET}"})
            leaked = srv.call("onshape_elements", {"did": did, "wvm": "w", "wvmid": wid})
            self.assertTrue(leaked["isError"])
            self.assertIn("[redacted]", leaked["content"][0]["text"])
            self.assertEqual(srv.close(), 0)
        for line in srv.raw:
            for needle in (SECRET, ACCESS, fake_onshape.basic()):
                self.assertNotIn(needle.encode("utf-8"), line)

    def test_34b_hostile_lines_do_not_end_the_server(self):
        name = "Bad \ud800 name (fixture)"  # a lone surrogate: legal as the JSON escape \ud800, not encodable as UTF-8
        self.fake.seed_document(name=name)
        with self.server() as srv:
            srv.request("initialize", {"protocolVersion": "2025-11-25"})
            # 200,000 open brackets: a parse error on every platform (RecursionError where json gives up on the
            # nesting, JSONDecodeError on the unterminated array where the C stack allows the depth), and the server
            # reads on
            srv.write(b"[" * 200_000 + b"\n")
            self.assertEqual(srv.recv()["error"], {"code": -32700, "message": "Parse error"})
            self.assertEqual(srv.request("ping")["result"], {})
            # a lone surrogate in the client's id and in Onshape's text goes out as its escape
            srv.write(b'{"jsonrpc": "2.0", "id": "\\ud800", "method": "ping"}\n')
            self.assertEqual(srv.recv(), {"jsonrpc": "2.0", "id": "\ud800", "result": {}})
            found = srv.call("onshape_documents", {"query": "Bad"})
            self.assertFalse(found["isError"], found)
            self.assertEqual([d["name"] for d in found["structuredContent"]["documents"]], [name])
            self.assertEqual(srv.request("ping")["result"], {})
            # an id that is not a string, a number or null is never echoed: on Python 3.11 an array nested 990 deep
            # still decodes, and its echo then failed to encode and ended the server
            for bad_id, codes in ((b"[1]", (-32600,)), (b'{"a": 1}', (-32600,)), (b"true", (-32600,)),
                                  (b"[" * 990 + b"]" * 990, (-32600, -32700))):
                srv.write(b'{"jsonrpc": "2.0", "method": "ping", "id": ' + bad_id + b"}\n")
                reply = srv.recv()
                self.assertIsNone(reply["id"])
                self.assertIn(reply["error"]["code"], codes)
            self.assertEqual(srv.request("ping")["result"], {})
            self.assertEqual(srv.close(), 0)
        for line in srv.raw:
            self.assertTrue(line.isascii(), line[:80])

    def test_34c_a_reply_that_cannot_be_encoded_is_an_error_not_an_exit(self):
        # Two replies json cannot encode on any platform: a set (TypeError) and a circular reference (ValueError).
        # A list nested 100,000 deep was used for the second before; whether json raises RecursionError on it depends
        # on the C stack the interpreter gets (it encoded fine on the GitHub runner, whose containers run with an
        # unlimited stack), so the test now avoids depth altogether.
        circular: list = []
        circular.append(circular)
        real = mcp_server.handle

        def handle(msg):
            reply, redactor = real(msg)
            if msg.get("id") == 1:
                reply["result"] = {"not json": {1, 2}}
            elif msg.get("id") == 2:
                reply["result"] = {"circular": circular}
            return reply, redactor

        lines = b"".join(b'{"jsonrpc": "2.0", "id": %d, "method": "ping"}\n' % n for n in (1, 2, 3))
        stdin, out = io.TextIOWrapper(io.BytesIO(lines), encoding="utf-8"), io.BytesIO()
        stdout = io.TextIOWrapper(out, encoding="utf-8")
        with mock.patch.object(mcp_server, "handle", handle), mock.patch.object(sys, "stdin", stdin), \
                mock.patch.object(sys, "stdout", stdout), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(mcp_server.main(), 0)
        replies = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual([(r["id"], r.get("error", {}).get("code")) for r in replies],
                         [(1, -32603), (2, -32603), (3, None)])
        self.assertEqual(replies[2]["result"], {})
        self.assertIn("a reply could not be encoded", err.getvalue())

    def test_35_without_credentials(self):
        with self.server(keys=False) as srv:
            init = srv.request("initialize", {"protocolVersion": "2025-11-25"})["result"]
            self.assertEqual(init["serverInfo"]["name"], "aces-onshape")
            self.assertEqual(len(srv.request("tools/list")["result"]["tools"]), 17)
            who = srv.call("onshape_whoami", {})
            self.assertTrue(who["isError"])
            self.assertEqual(who["structuredContent"]["error"]["kind"], "auth")
            self.assertEqual(who["structuredContent"]["error"]["exit_code"], 3)
            self.assertEqual(srv.close(), 0)
        self.assertEqual(len(self.fake.requests), 0)

    def test_56_session_ownership(self):
        did, wid, ps = self.fake.seed_document()
        seeded = self.fake.seed_feature(did, wid, ps, FEATURE)
        with self.server() as srv:
            srv.request("initialize", {"protocolVersion": "2025-11-25"})
            base = {"did": did, "wid": wid, "project": "proj"}
            created = srv.call("onshape_featurestudio_write", dict(base, code=PLATE_CODE, name="ACES plate"))
            self.assertFalse(created["isError"], created)
            eid = created["structuredContent"]["eid"]
            rewritten = srv.call("onshape_featurestudio_write", dict(base, code=PLATE_CODE + "// v2 (fixture)\n",
                                                                      eid=eid))
            self.assertFalse(rewritten["isError"], rewritten)
            self.assertIn("// v2 (fixture)", self.fake.element(did, eid)["contents"])
            added = srv.call("onshape_add_custom_feature", dict(base, eid=ps, studio_eid=eid, feature_type="acesPlate",
                                                                parameters={"length": "60 mm"}))
            self.assertFalse(added["isError"], added)
            fid = added["structuredContent"]["feature_id"]
            replaced = srv.call("onshape_add_custom_feature", dict(base, eid=ps, studio_eid=eid,
                                                                   feature_type="acesPlate",
                                                                   parameters={"length": "70 mm"},
                                                                   replace_feature_id=fid))
            self.assertFalse(replaced["isError"], replaced)
            self.assertTrue(replaced["structuredContent"]["replaced"])
            writes = self.fake.count("POST") + self.fake.count("DELETE")
            refused = srv.call("onshape_update_feature", dict(base, eid=ps, feature_id=seeded, feature=FEATURE))
            self.assertTrue(refused["isError"])
            self.assertEqual(refused["structuredContent"]["error"]["kind"], "write_refused")
            self.assertEqual(self.fake.count("POST") + self.fake.count("DELETE"), writes)
            confirmed = srv.call("onshape_update_feature", dict(base, eid=ps, feature_id=seeded, feature=FEATURE,
                                                                confirm_foreign=True))
            self.assertFalse(confirmed["isError"], confirmed)
            no_project = srv.call("onshape_featurescript_eval", {
                "did": did, "wvm": "w", "wvmid": wid, "eid": ps,
                "script": "function(context is Context, queries is map) { return 1; }"})
            self.assertTrue(no_project["isError"])
            self.assertEqual(no_project["structuredContent"]["error"]["kind"], "project")
            self.assertEqual(srv.close(), 0)
        self.assertTrue(self.fake.element(did, eid)["contents"].startswith(FIXTURE_HEADER))


if __name__ == "__main__":
    unittest.main()
