"""B2 tests: the desktop's rig client (golden.rig, sections 9 and 10 of the rig build spec) and the simlab token
(aces_engineering.mcp_client) against the in-process fake simlab-mcp (tests/fake_simlab.py, a test fixture).

Token precedence and the simlab.env file; the token never appears in exception text, reprs or the remote CLI's output
(test_g4_pipeline_rig checks stdout, stderr, log.txt, events.jsonl and every run file of a whole run); reply
unwrapping; HTTP 401; upload chunking with file_chunk_max=4096, the idempotent existed, put_spec "already exists"
recovery; download chunking; RigIntegrityError on corrupt-file and file-changes after one retry; every refusal R1-R12
(R11 skipped when only the conforming check goes to the rig); the config-lane warning and fallback; the experiment id
format and length; YAML byte determinism, no anchors or aliases, parse-back to a simlab Experiment with c000 -> x001;
and a whole check experiment (the GC-1 bar of section 20.2, solved for real by the fake's worker) whose case dicts
equal the desktop's case.json. Every manifest, registry row and capacity here is a labelled test fixture.
Run: python -m unittest discover -s tests -p "test_g4_rig_client.py"."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fake_simlab
import test_g4_rigside as tr

from aces_engineering import golden, mcp_client
from aces_engineering.golden import pipeline, rig, spec as spec_mod, verify
from aces_engineering.golden.rundir import RunDir

TOKEN = "fixture-token-3f9a2c1d"  # a test input; it must never appear in any error text or output


def write_manifest(cfg: Path, url: str | None, *, lanes=None, server_url=None) -> None:
    man = {"generated": "fixture", "server": {"simlab_mcp": server_url}, "health": {}, "mode": {"name": "sim-lab-4"},
           "lanes": lanes if lanes is not None else [{"id": "sim/pool", "kind": "sim", "gpus": [0, 1, 2, 3],
                                                     "endpoint": url}]}
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "fabric.json").write_text(json.dumps(man), encoding="utf-8")


class Recorder:
    """A progress stand-in that keeps what the pipeline said (test helper)."""

    def __init__(self):
        self.lines, self.notes = [], []

    def line(self, text):
        self.lines.append(str(text))

    def note(self, text, level="info"):
        self.notes.append((level, str(text)))

    def event(self, *args, **kwargs):
        pass


class _Env(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="g4-client-"))
        self.cfg, self.etc = self.tmp / "cfg", self.tmp / "etc"
        self.cfg.mkdir()
        self.etc.mkdir()
        env = {k: v for k, v in os.environ.items() if k not in ("ACES_SIMLAB_TOKEN", "SIMLAB_MCP_TOKEN")}
        env.update({"ACES_USER_CONFIG": str(self.cfg), "ACES_ETC": str(self.etc), "ACES_WORK": str(self.tmp / "work"),
                    "XDG_DATA_HOME": str(self.tmp / "data"), "ACES_STATE": str(self.tmp / "state")})
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()
        self._consts = mock.patch.multiple(rig, POLL_S=0.02, BEACON_EVERY_S=0.1)
        self._consts.start()
        self.fakes: list[fake_simlab.FakeSimlab] = []

    def tearDown(self):
        for f in self.fakes:
            f.stop()
        self._consts.stop()
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.assertEqual(fake_simlab.BLOCKED, [])

    def fake(self, **kw) -> fake_simlab.FakeSimlab:
        f = fake_simlab.FakeSimlab(self.tmp / f"rig{len(self.fakes)}", **kw)
        self.fakes.append(f)
        return f

    def probe(self, **kw):
        args = {"check_mode": "auto", "conforming_mode": "auto", "rig_sweep": False}
        args.update(kw)
        return rig.probe("sim/pool", "cli", **args)


# ------------------------------------------------------------------------------------------------------------- token

class Token(_Env):
    def test_precedence(self):
        self.assertEqual(mcp_client.simlab_token(), (None, "not set"))
        (self.cfg / "simlab.env").write_text("# simlab token (test input)\nexport SIMLAB_MCP_TOKEN='from-file-b'\n"
                                             "ACES_SIMLAB_TOKEN=\"from-file-a\"  \n", encoding="utf-8")
        self.assertEqual(mcp_client.simlab_token(), ("from-file-a", "simlab.env"))
        with mock.patch.dict(os.environ, {"SIMLAB_MCP_TOKEN": "from-env-b"}):
            self.assertEqual(mcp_client.simlab_token(), ("from-env-b", "SIMLAB_MCP_TOKEN"))
            with mock.patch.dict(os.environ, {"ACES_SIMLAB_TOKEN": "from-env-a"}):
                self.assertEqual(mcp_client.simlab_token(), ("from-env-a", "ACES_SIMLAB_TOKEN"))
        (self.cfg / "simlab.env").write_text("SIMLAB_MCP_TOKEN=only-b # comment\n", encoding="utf-8")
        self.assertEqual(mcp_client.simlab_token(), ("only-b", "simlab.env"))

    @unittest.skipIf(os.name != "posix", "file modes are POSIX")
    def test_a_readable_simlab_env_is_warned_about(self):
        p = self.cfg / "simlab.env"
        p.write_text(f"ACES_SIMLAB_TOKEN={TOKEN}\n", encoding="utf-8")
        os.chmod(p, 0o644)
        said = []
        self.assertEqual(mcp_client.simlab_token(warn=said.append), (TOKEN, "simlab.env"))
        self.assertEqual(len(said), 1)
        self.assertIn(str(p), said[0])
        self.assertNotIn(TOKEN, said[0])
        os.chmod(p, 0o600)
        said.clear()
        mcp_client.simlab_token(warn=said.append)
        self.assertEqual(said, [])

    def test_the_token_is_only_a_bearer_header(self):
        f = self.fake(tokens={"aces-test": TOKEN})
        write_manifest(self.cfg, f.url)
        # no token: 401, refused as R6, and nothing in the record names a token
        rec, client = self.probe()
        self.assertIsNone(client)
        self.assertEqual(rec["refusal"], f"lane 'sim/pool' cannot be used: simlab-mcp at {f.url} refused the request "
                                         f"(HTTP 401): set ACES_SIMLAB_TOKEN or put ACES_SIMLAB_TOKEN=... in {self.cfg}"
                                         "/simlab.env; nothing was submitted")
        self.assertEqual(rec["token_source"], "not set")
        # a wrong token: RigError("HTTP 401"), and no token text anywhere in the error or the reprs
        c = rig.RigClient(f.url, TOKEN + "-wrong")
        with self.assertRaises(rig.RigError) as cm:
            c.initialize()
        self.assertEqual(str(cm.exception), "HTTP 401")
        self.assertNotIn(TOKEN, repr(c) + repr(c._client))
        # the right token: principal from the token's name, the bearer on every call, the token in no reply
        with mock.patch.dict(os.environ, {"ACES_SIMLAB_TOKEN": TOKEN}):
            rec, client = self.probe()
        self.assertTrue(rec["ok"], rec["refusal"])
        self.assertEqual((rec["principal"], rec["token_source"]), ("aces-test", "ACES_SIMLAB_TOKEN"))
        self.assertNotIn(TOKEN, json.dumps(rec))
        self.assertTrue(all(has_auth for name, _args, has_auth in f.calls[-6:]))
        # the remote CLI: "token sent from <source>", never the token
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"ACES_SIMLAB_TOKEN": TOKEN}), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            self.assertEqual(mcp_client.main(["info", "--url", f.url]), 0)
            self.assertEqual(mcp_client.main(["call", "sim_whoami", "--url", f.url]), 0)
        self.assertIn("token sent from ACES_SIMLAB_TOKEN", out.getvalue())
        self.assertIn('"principal": "aces-test"', out.getvalue())
        self.assertNotIn(TOKEN, out.getvalue() + err.getvalue())
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"ACES_SIMLAB_TOKEN": TOKEN + "-wrong"}), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            self.assertEqual(mcp_client.main(["info", "--url", f.url]), 1)
        self.assertIn("HTTP 401", err.getvalue())
        self.assertNotIn(TOKEN, err.getvalue())


# ------------------------------------------------------------------------------------------------------- transport

class Transport(_Env):
    def test_the_fake_tiers(self):
        """The fake's tool tiers are simlab-mcp 0.4.0's (section 5.2): the vendored snapshot's tiers, plus the spec
        store tools (sim_list_specs read, sim_put_spec submit) and the file tools (sim_get_file read, sim_put_file
        submit): 17 tools under policy full."""
        vendored = fake_simlab.vendored_tiers()
        if vendored is None:
            self.skipTest("the vendored simlab/mcp_server.py has no TIERS literal to compare with")
        added = {k: fake_simlab.TIERS[k] - vendored.get(k, set()) for k in fake_simlab.TIERS}
        self.assertTrue(all(vendored[k] <= fake_simlab.TIERS[k] for k in vendored))
        if "sim_get_file" in vendored.get("read", set()):
            # the vendored snapshot is simlab-mcp 0.4.0 itself (sync.sh --commit rig-golden-1): the fake's tiers are
            # exactly its tiers
            self.assertEqual({k: set(v) for k, v in fake_simlab.TIERS.items()},
                             {k: set(vendored.get(k, set())) for k in fake_simlab.TIERS})
        else:
            # an older snapshot (28d92a8): the fake adds the spec store and file tools of 0.4.0 to its tiers
            self.assertTrue(added["read"] <= {"sim_list_specs", "sim_get_file"} and "sim_get_file" in added["read"])
            self.assertTrue(added["submit"] <= {"sim_put_spec", "sim_put_file"} and "sim_put_file" in added["submit"])
        self.assertEqual(added["full"], set())
        self.assertEqual(len(fake_simlab.allowed_tools("full")), 17)
        self.assertEqual(len(fake_simlab.allowed_tools("read")), 12)
        f = self.fake(policy="submit")
        c = rig.RigClient(f.url, None)
        c.initialize()
        self.assertEqual(set(c.tools()), fake_simlab.allowed_tools("submit"))

    def test_reply_unwrapping(self):
        u = rig.RigClient.unwrap
        with self.assertRaises(rig.RigError) as cm:
            u({"content": [{"type": "text", "text": "Error executing tool x: boom (test input)"}], "isError": True})
        self.assertEqual(str(cm.exception), "Error executing tool x: boom (test input)")
        self.assertEqual(u({"structuredContent": {"result": [1, 2]}, "content": []}), [1, 2])
        self.assertEqual(u({"structuredContent": {"a": 1, "result": 2}}), {"a": 1, "result": 2})
        self.assertEqual(u({"content": [{"type": "text", "text": '{"b"'}, {"type": "text", "text": ": 3}"}]}), {"b": 3})
        self.assertEqual(u({"content": [{"type": "text", "text": "plain words"}]}), "plain words")

    def test_upload_chunks_existed_and_spec_recovery(self):
        f = self.fake(file_chunk_max=4096)
        c = rig.RigClient(f.url, None)
        c.initialize()
        data = bytes(range(256)) * 41  # 10,496 bytes (test input)
        with mock.patch.object(rig, "CHUNK_BYTES", 4096):
            res = c.put_file("golden/run-a/part.part.stl", data)
            again = c.put_file("golden/run-a/part.part.stl", data)
        puts = f.tool_calls("sim_put_file")
        self.assertEqual([p[1]["offset"] for p in puts[:3]], [0, 4096, 8192])
        self.assertTrue(res["complete"] and not res["existed"])
        self.assertTrue(again["existed"])
        stored = f.store_root() / "anonymous" / "golden" / "run-a" / "part.part.stl"
        self.assertEqual(stored.read_bytes(), data)
        self.assertTrue(all(len(p[1]["content_b64"]) <= 16 for p in puts))  # the fake keeps 16 characters of each chunk
        # a chunk larger than the server takes is an error, retried once from offset 0
        with self.assertRaises(rig.RigError):
            c.put_file("golden/run-a/big.bin", b"x" * 5000)  # CHUNK_BYTES is 2 MiB again: one 5,000-byte chunk
        self.assertEqual([p[1]["offset"] for p in f.tool_calls("sim_put_file")[-2:]], [0, 0])
        # put_spec: "already exists" with the same bytes is success; other bytes are an error
        doc = {"id": "golden-run-a-check", "physics": "structures", "solver": "golden-check", "sweep": {},
               "resources": {"vram_gb": 1.0}}
        text = rig.spec_text(doc)
        first = c.put_spec(text, "golden-run-a-check.yaml", "anonymous")
        second = c.put_spec(text, "golden-run-a-check.yaml", "anonymous")
        self.assertEqual(first["sha256"], hashlib.sha256(text.encode("utf-8")).hexdigest())
        self.assertEqual((second["existed"], second["sha256"]), (True, first["sha256"]))
        with self.assertRaises(rig.RigError) as cm:
            c.put_spec(rig.spec_text(dict(doc, description="other bytes (test input)")), "golden-run-a-check.yaml",
                       "anonymous")
        self.assertIn("already exists on the rig with sha256", str(cm.exception))

    def _exp_with_file(self, f, scenario="ok") -> tuple[str, bytes]:
        """An experiment in the fake's queue holding c000/golden/x001/mask.npy (test fixture)."""
        exp_dir = f.queue_root() / "golden-run-b-check"
        p = exp_dir / "c000" / "golden" / "x001" / "mask.npy"
        p.parent.mkdir(parents=True)
        data = bytes((i * 7) % 251 for i in range(9000))
        p.write_bytes(data)
        f.exps["golden-run-b-check"] = {"id": "golden-run-b-check", "exp_dir": str(exp_dir), "jobs": [
            {"rc": "c000", "scenario": scenario}]}
        return "golden-run-b-check", data

    def test_download_chunks_and_integrity(self):
        f = self.fake(file_chunk_max=4096)
        c = rig.RigClient(f.url, None)
        eid, data = self._exp_with_file(f)
        with mock.patch.object(rig, "CHUNK_BYTES", 4096):
            got, sha, chunks = c.get_file(eid, "c000/golden/x001/mask.npy",
                                          expect_sha256=hashlib.sha256(data).hexdigest())
        self.assertEqual((got, chunks), (data, 3))
        for scenario, words in (("corrupt-file", "the pulled bytes hash to"),
                                ("file-changes", "changed between chunks")):
            with self.subTest(scenario):
                f2 = self.fake(file_chunk_max=4096)
                c2 = rig.RigClient(f2.url, None)
                eid, _ = self._exp_with_file(f2, scenario)
                with mock.patch.object(rig, "CHUNK_BYTES", 4096), self.assertRaises(rig.RigIntegrityError) as cm:
                    c2.get_file(eid, "c000/golden/x001/mask.npy")
                self.assertIn("c000/golden/x001/mask.npy", str(cm.exception))
                self.assertIn(words, str(cm.exception))
                offsets = [x[1]["offset"] for x in f2.tool_calls("sim_get_file")]
                self.assertEqual(offsets.count(0), 2)  # the whole file pulled again once

    def test_read_calls_retry_then_give_up(self):
        f = self.fake()
        c = rig.RigClient(f.url, None)
        c.initialize()
        f.go_dark()  # every later request gets a connection reset
        sent = []
        real = c._client.request
        c._client.request = lambda method, params=None: (sent.append((params or {}).get("name")), real(method, params))[1]
        with mock.patch.object(rig, "UNREACHABLE_S", 1.5), self.assertRaises(rig.RigUnreachable):
            c.call("sim_whoami", {})
        self.assertGreaterEqual(sent.count("sim_whoami"), 2)  # a read call is retried before it gives up
        with self.assertRaises(rig.RigUnreachable):
            c.call("sim_submit", {"spec_path": "x"}, retry=False)
        self.assertEqual(sent.count("sim_submit"), 1)  # never retried blindly


# -------------------------------------------------------------------------------------------------------- refusals

class Refusals(_Env):
    def test_r1_to_r4_from_the_manifest(self):
        rec, _ = self.probe()
        self.assertEqual(rec["refusal"], "lane 'sim/pool' cannot be used: there is no fabric manifest (aces fabric "
                                         "refresh writes it); nothing was submitted")
        write_manifest(self.cfg, "http://127.0.0.1:9/mcp", lanes=[
            {"id": "sim/other", "kind": "sim", "endpoint": "http://127.0.0.1:9/mcp"},
            {"id": "model/x", "kind": "model", "endpoint": "http://127.0.0.1:9/v1"}])
        self.assertIn("it is not in the fabric manifest (sim lanes: sim/other)", self.probe()[0]["refusal"])
        rec, _ = rig.probe("model/x", "cli")
        self.assertEqual(rec["refusal"], "lane 'model/x' cannot be used: it is a model lane, not a simulation lane; "
                                         "nothing was submitted")
        write_manifest(self.cfg, None)
        self.assertIn("the fabric manifest gives it no simlab-mcp endpoint", self.probe()[0]["refusal"])
        write_manifest(self.cfg, None, server_url="http://127.0.0.1:9/mcp")  # the server URL serves the lane
        self.assertIn("simlab-mcp at http://127.0.0.1:9/mcp did not answer", self.probe()[0]["refusal"])

    def test_r5_to_r11_from_the_server(self):
        dead = self.fake()
        url = dead.url
        dead.stop()
        write_manifest(self.cfg, url)
        rec, client = self.probe()
        self.assertTrue(rec["refusal"].startswith(f"lane 'sim/pool' cannot be used: simlab-mcp at {url} did not "
                                                  "answer ("), rec["refusal"])
        self.assertIsNone(client)
        cases = {
            "R7": (dict(policy="read"), {}, "(policy read) does not offer sim_put_file, sim_put_spec, sim_submit: "
                                            "deploy simlab 0.4.0 on the rig (cudax-sim-runtime docs/GOLDEN_RIG.md)"),
            "R8": (dict(solvers={"golden-case": fake_simlab.golden_row("design")}), {},
                   "the rig registry has no golden-check, ccx-check"),
            "R9": (dict(solvers=fake_simlab.golden_solvers(compute_sha256=None)), {},
                   "the rig registry does not say which golden code aces-golden:0.1 carries (golden-check "
                   "golden.compute_sha256 is null): build, ship and record the image (docs/GOLDEN_RIG.md)"),
            "R10": (dict(solvers=fake_simlab.golden_solvers(compute_sha256="a" * 64)), {},
                    "the rig's aces-golden image carries other golden code (rig aaaaaaaaaaaa, here "),
            "R11": (dict(capacity=dict(fake_simlab.FAKE_CAPACITY, pool=[], available=[])), {},
                    "the rig's mode sim-lab-4 releases no simulation GPU (pool []); mode switching is a human act in "
                    "GRID CONTROL"),
            "R12": (dict(), dict(check_mode="off", conforming_mode="off"),
                    "--lane sends the independent check and the conforming check to the rig, and both are off here "
                    "(--check off, --conforming off)"),
        }
        for label, (fake_kw, probe_kw, words) in cases.items():
            with self.subTest(label):
                f = self.fake(**fake_kw)
                write_manifest(self.cfg, f.url)
                n_before = len(f.calls)
                rec, client = self.probe(**probe_kw)
                self.assertFalse(rec["ok"])
                self.assertIsNone(client)
                self.assertIn(words, rec["refusal"])
                self.assertTrue(rec["refusal"].endswith("; nothing was submitted"))
                self.assertFalse([x for x in f.calls if x[0] in ("sim_put_file", "sim_put_spec", "sim_submit")])
                if label == "R12":
                    self.assertEqual(len(f.calls), n_before)  # R12 is decided before the server is contacted
        # R11 is skipped when only the conforming check goes to the rig
        f = self.fake(capacity=dict(fake_simlab.FAKE_CAPACITY, pool=[], available=[]))
        write_manifest(self.cfg, f.url)
        rec, client = self.probe(check_mode="off", conforming_mode="auto")
        self.assertTrue(rec["ok"], rec["refusal"])
        self.assertEqual(set(rec), {"ok", "lane", "lane_source", "url", "manifest", "server", "principal", "policy",
                                    "token_source", "tools", "missing_optional", "solvers", "capacity", "notes",
                                    "refusal", "probed_utc", "wall_s"})
        self.assertEqual(rec["server"]["version"], "0.4.0-fake")
        self.assertEqual(len(rec["tools"]), 17)

    def test_policy_submit_notes_the_missing_cancel(self):
        f = self.fake(policy="submit")
        write_manifest(self.cfg, f.url)
        rec, client = self.probe()
        self.assertTrue(rec["ok"], rec["refusal"])
        self.assertEqual(rec["missing_optional"], ["sim_cancel"])
        self.assertTrue(any(n.startswith("sim_cancel not offered (policy submit)") for n in rec["notes"]))

    def test_config_lane_warning_and_fallback(self):
        (self.etc / "aces.toml").write_text('[golden]\nlane = "sim/pool"\n', encoding="utf-8")
        self.assertEqual(rig.choose_lane(None), ("sim/pool", "config"))
        self.assertEqual(rig.choose_lane("none"), (None, "cli"))
        p = pipeline._Pipeline.__new__(pipeline._Pipeline)
        p.opts = pipeline.Options(argv=[])
        p.raw = {"verify": {"check": "auto"}}
        p.progress = Recorder()
        p.probe, p.session = None, None
        p._preflight_rig()  # no fabric manifest: the config lane is refused, the run continues on this desktop
        self.assertIsNone(p.session)
        self.assertEqual((p.probe["ok"], p.probe["lane_source"]), (False, "config"))
        self.assertEqual(p.progress.notes, [("warning", "rig lane sim/pool (from aces.toml) unavailable: there is no "
                                                        "fabric manifest (aces fabric refresh writes it); computing on "
                                                        "this desktop")])
        # the same lane given with --lane is a usage refusal
        p.opts = pipeline.Options(lane="sim/pool", argv=["run", "--lane", "sim/pool"])
        with self.assertRaises(golden.GoldenError) as cm:
            p._preflight_rig()
        self.assertEqual(cm.exception.code, golden.EXIT_USAGE)
        self.assertTrue(cm.exception.message.startswith("lane 'sim/pool' cannot be used: there is no fabric manifest"))


# ------------------------------------------------------------------------------------------------- experiment docs

class Documents(_Env):
    def test_experiment_ids(self):
        rid = "20261010T025257Z-ceea48"
        for stage in ("sweep", "check", "conf"):
            eid = rig.experiment_id(rid, stage)
            self.assertEqual(eid, f"golden-{rid}-{stage}")
            self.assertLessEqual(len(eid), 41)
        with self.assertRaises(rig.RigError):
            rig.experiment_id(rid + "-2-and-much-longer", "check")
        with self.assertRaises(ValueError):
            rig.experiment_id(rid, "verify")

    def test_yaml_bytes(self):
        from simlab.spec import Experiment
        import yaml

        shared = {"path": "golden/r/spec.effective.json", "sha256": "0" * 64, "bytes": 1}
        case = {"case_id": "x001", "role": "check", "h_fea_mm": 2.0, "source_case": "v001", "lane": "sim/pool"}
        doc = {"id": "golden-r-check", "project": "p", "owner": "o", "physics": "structures", "solver": "golden-check",
               "description": "test input: ünïcode kept", "params": {"golden": {"files": {"eff": shared, "again": shared}}},
               "sweep": {"golden_case": [case]}, "resources": {"vram_gb": 1.0}}
        a, b = rig.spec_text(doc), rig.spec_text(json.loads(json.dumps(doc)))
        self.assertEqual(a.encode("utf-8"), b.encode("utf-8"))
        self.assertNotIn("&id", a)
        self.assertNotIn("*id", a)
        self.assertIn("ünïcode", a)
        self.assertEqual(list(yaml.safe_load(a))[:5], ["id", "project", "owner", "physics", "solver"])
        exp = Experiment(**yaml.safe_load(a))
        cases = exp.cases()
        self.assertEqual([(c["case_id"], c["golden_case"]["case_id"]) for c in cases], [("c000", "x001")])
        self.assertEqual(cases[0]["golden_case"], case)
        with self.assertRaises(rig.RigError):
            rig.spec_text(dict(doc, description="x" * (257 * 1024)))


def gc1_check_run(root: Path, fake: fake_simlab.FakeSimlab, *, budget_s: float = 120.0):
    """A run directory with one check experiment on the fake: the GC-1 bar (test input) uploaded as b001/part.stl and
    re-voxelised as x001 at 1 mm, solved for real by the fake's worker. Returns (run, session, results)."""
    write_manifest(Path(os.environ["ACES_USER_CONFIG"]), fake.url)
    (root / "unused.step").write_text("ISO-10303-21; placeholder design space (test input)\n", encoding="utf-8")
    eff = spec_mod.resolve(tr.GC1, base_dir=root, overrides={}, gpu_usable=False)
    run = RunDir.create("gc-test", eff, seed=7)
    run.write_json("inputs/spec.effective.json", eff)
    run.write_json("domain/domain.json", {"stress_exclude": [], "stress_exclude_effective_mm": 0.0})
    run.path("b001").mkdir(parents=True, exist_ok=True)
    tr.box_stl(run.path("b001", "part.stl"), tr.L, tr.B, tr.B)
    rec, client = rig.probe("sim/pool", "cli", check_mode="auto", conforming_mode="off")
    if not rec["ok"]:
        raise AssertionError(rec["refusal"])
    sess = rig.RigSession(rec, client, budget_s=budget_s, scope={"sweep": False, "check": True, "conforming": False},
                          progress=golden.NullProgress())
    sess.bind(run, eff, run.read_json("domain/domain.json"), None, operator="g4-test", host="g4-host")
    case = verify.check_case({"level": 1, "h_mm": 1.0, "device": "rig", "host": "rig"}, "b001", 7, lane="sim/pool")
    sess.submit("check", [case], source_case="b001")
    res = sess.collect("check")
    sess.close()
    return run, sess, res


class WholeExperiment(_Env):
    def test_gc1_check_on_the_fake(self):
        f = self.fake()
        run, sess, res = gc1_check_run(self.tmp, f)
        r = res["x001"]
        self.assertEqual((r["state"], r["outcome"]["status"]), ("result", "FINISHED"), r.get("reason"))
        doc = run.read_json("rig/session.json")
        e = doc["experiments"][0]
        self.assertEqual((e["id"], e["stage"], e["kind"], e["solver"]),
                         (f"golden-{run.run_id}-check", "check", "mesh", "golden-check"))
        self.assertEqual(e["cases"], {"c000": "x001"})
        self.assertTrue(e["jobs"]["c000"].startswith(f"{e['id']}--c000--run--"))
        self.assertEqual(e["lease_wait_s"], 120)
        raw = run.path("rig", "specs", f"{e['id']}.yaml").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), e["spec_sha256"])
        from simlab.spec import load

        stored = load(str(f.repo / e["spec_path"]))
        self.assertEqual(stored.spec_sha256, e["spec_sha256"])
        self.assertEqual(stored.cases()[0]["golden_case"], run.read_json("x001/case.json"))
        rig_case = json.loads(run.path("x001", "rig", "case.json").read_text(encoding="utf-8"))
        self.assertEqual(rig_case["golden_case"], run.read_json("x001/case.json"))
        self.assertEqual([u["name"] for u in doc["uploads"]],
                         [f"golden/{run.run_id}/spec.effective.json", f"golden/{run.run_id}/domain.json",
                          f"golden/{run.run_id}/b001.part.stl"])
        self.assertEqual(e["rig_evidence"], {"bundle_sha256": f"fake-{e['id']} (test fixture)",
                                             "ledger_entry_sha256": e["rig_evidence"]["ledger_entry_sha256"]})
        rj = run.read_json("x001/rig.json")
        self.assertEqual(set(rj), {"schema", "case_id", "lane", "url", "experiment_id", "rig_case_id", "job_id",
                                   "job_status", "state", "design_status", "reason", "queue", "gpu", "owner", "gpu_name",
                                   "gpu_uuid", "image", "image_id", "image_digest", "submitted_utc", "started_utc",
                                   "finished_utc", "wait_s", "run_s", "pulled_utc", "files", "records", "code",
                                   "device_forced"})
        self.assertEqual((rj["gpu"], rj["gpu_name"], rj["image_id"], rj["device_forced"]),
                         (fake_simlab.FAKE_JOB_GPU, fake_simlab.FAKE_GPU["name"], "sha256:fake-image", "cpu"))
        self.assertEqual(run.read_json("x001/status.json")["rig"]["rig_case_id"], "c000")
        self.assertEqual(run.path("x001", "evidence.json").read_bytes(), run.path("x001", "rig", "evidence.json")
                         .read_bytes())
        res_local = run.read_json("x001/results.json")
        self.assertTrue(all(str(a).startswith(str(run.path("x001"))) for a in res_local["artifacts"]))
        tr_rel = abs(res_local["compliance_Nmm"] - tr.F * tr.TIP_UX) / (tr.F * tr.TIP_UX)
        self.assertLessEqual(tr_rel, 1e-6)  # the closed form, through the fake's worker and the pull
        blk = sess.validation_block()
        self.assertTrue(blk["code_identity"]["ok"], blk["code_identity"])
        self.assertEqual(blk["experiments"], [{"id": e["id"], "stage": "check", "solver": "golden-check", "n_cases": 1,
                                               "n_finished": 1, "n_failed": 0}])
        level = sess.level_block("x001")
        self.assertEqual((level["solver_validation"], level["validation_image_id"], level["image_id"]),
                         ("pass", fake_simlab.FAKE_IMAGE, "sha256:fake-image"))

    def test_submission_failure_marks_every_case_not_delivered(self):
        f = self.fake(policy="full")
        write_manifest(self.cfg, f.url)
        (self.tmp / "unused.step").write_text("ISO-10303-21; placeholder design space (test input)\n",
                                              encoding="utf-8")
        eff = spec_mod.resolve(tr.GC1, base_dir=self.tmp, overrides={}, gpu_usable=False)
        run = RunDir.create("gc-test", eff, seed=7)
        run.write_json("inputs/spec.effective.json", eff)
        run.write_json("domain/domain.json", {"stress_exclude": []})
        rec, client = rig.probe("sim/pool", "cli", conforming_mode="off")
        sess = rig.RigSession(rec, client, budget_s=60.0, scope={"check": True}, progress=golden.NullProgress())
        sess.bind(run, eff, {}, None, operator="g4-test", host="g4-host")
        case = verify.check_case({"level": 1, "h_mm": 1.0, "device": "rig", "host": "rig"}, "b001", 7, lane="sim/pool")
        sess.submit("check", [case], source_case="b001")  # b001/part.stl does not exist (test input)
        r = sess.collect("check")["x001"]
        self.assertEqual((r["state"], r["design_status"]), ("not-run", "rig-error"))
        self.assertTrue(r["reason"].startswith("submission failed: "), r["reason"])
        self.assertFalse(f.tool_calls("sim_submit"))


if __name__ == "__main__":
    unittest.main()
