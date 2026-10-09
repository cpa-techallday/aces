"""aces-base 0.1.0-3: ledger.record and its checks, `aces ledger record / verify --json / show --kind --json`, the
ledger lock under two concurrent writers, `aces project add-evidence / add-cad-source`, and the `aces golden`
passthrough (127 when aces-golden is missing, the child's exit status with ACES_GOLDEN_CMD).

Every input below is a test input, and every path is a temporary test directory: the module points ACES_STATE,
ACES_WORK, ACES_USER_CONFIG and ACES_ETC at its own scratch tree before importing aces, so it never touches the real
home or the environment of the PKGBUILD check that runs it. Standard library only.
    python -m unittest discover -s tests          (from src/aces-base)
"""
from __future__ import annotations

import contextlib
import io
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent  # src/aces-base (or $srcdir/aces-base in the package build)


def _scratch_root() -> Path:
    """One scratch tree per test run; spawned writer processes reuse the parent's through the environment, and only
    the process that made it removes it (tearDownModule)."""
    root = os.environ.get("ACES_BASE_TEST_ROOT")
    if not root:
        root = tempfile.mkdtemp(prefix="aces-base-test-")
        os.environ["ACES_BASE_TEST_ROOT"] = root
        os.environ["ACES_BASE_TEST_ROOT_OWNER"] = str(os.getpid())
    return Path(root)


ROOT = _scratch_root()
(ROOT / "etc").mkdir(parents=True, exist_ok=True)
for _name in ("aces.toml", "policy.json", "modes.json"):
    if (PKG_ROOT / "etc" / _name).is_file() and not (ROOT / "etc" / _name).exists():
        shutil.copyfile(PKG_ROOT / "etc" / _name, ROOT / "etc" / _name)
os.environ.update({"ACES_ETC": str(ROOT / "etc"), "ACES_USER_CONFIG": str(ROOT / "cfg"),
                   "ACES_STATE": str(ROOT / "state"), "ACES_WORK": str(ROOT / "work")})
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from aces import cli, config, ledger, project  # noqa: E402  (after the environment above)


def tearDownModule() -> None:
    if os.environ.get("ACES_BASE_TEST_ROOT_OWNER") == str(os.getpid()):
        shutil.rmtree(ROOT, ignore_errors=True)


def _use_state(state: Path, work: Path | None = None) -> None:
    """Point aces at a fresh state (and work) directory; config reads its environment once at import."""
    config.STATE = state
    os.environ["ACES_STATE"] = str(state)
    if work is not None:
        config.WORK = work
        os.environ["ACES_WORK"] = str(work)


def _writer(state_dir: str, tag: str, n: int) -> None:
    """Spawned process body for the concurrency test: n records of kind test.concurrent."""
    from aces import config as cfg, ledger as led

    cfg.STATE = Path(state_dir)
    for i in range(n):
        led.record("test.concurrent", {"writer": tag, "i": i})


def _run_cli(argv: list[str], stdin: bytes | None = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    saved_stdin = sys.stdin
    if stdin is not None:
        sys.stdin = io.TextIOWrapper(io.BytesIO(stdin), encoding="utf-8")
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(argv)
    finally:
        sys.stdin = saved_stdin
    return rc, out.getvalue(), err.getvalue()


class LedgerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="case-", dir=ROOT))
        _use_state(self.tmp / "state", self.tmp / "work")
        self._env = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)

    def payload_file(self, payload, name: str = "payload.json") -> str:
        p = self.tmp / name
        p.write_text(json.dumps(payload) if not isinstance(payload, str) else payload, encoding="utf-8")
        return str(p)


class RecordTests(LedgerTestCase):
    def test_record_returns_positions_and_chains(self) -> None:
        n1, e1 = ledger.record("golden.run", {"project": "p", "run_id": "r1", "verdict": "PASS"})
        n2, e2 = ledger.record("golden.run", {"project": "p", "run_id": "r2", "verdict": "FAIL"})
        ledger.append("project.new", {"name": "p"})  # aces-base's own writer still works and shares the chain
        n4, _ = ledger.record("golden.run", {"project": "p", "run_id": "r3", "verdict": "THIN"})
        self.assertEqual((n1, n2, n4), (1, 2, 4))
        self.assertEqual(e1["prev"], ledger.GENESIS)
        self.assertEqual(e2["prev"], e1["hash"])
        self.assertEqual(ledger.count(), 4)
        self.assertEqual(set(e1), {"ts", "kind", "operator", "host", "payload", "prev", "hash"})
        self.assertEqual(ledger.verify(), (True, 4, "chain intact"))
        self.assertTrue(ledger.lock_path().exists())
        raw = ledger.path().read_bytes()
        self.assertNotIn(b"\r", raw)

    def test_reserved_kinds_are_refused(self) -> None:
        for kind in ("policy.override", "project.new", "lane.test", "ledger.anything"):
            with self.assertRaises(ValueError) as cm:
                ledger.record(kind, {"x": 1})
            self.assertEqual(str(cm.exception), f"kind '{kind}' is written by aces itself; use the aces command that owns it")
        self.assertEqual(ledger.count(), 0)

    def test_malformed_kinds_are_refused(self) -> None:
        for kind in ("Bad Kind", "golden", "Golden.run", "golden..run", "1golden.run", "golden.run.", "golden.Run",
                     "golden_run.x", "a" * 62 + ".bb", "", None):
            with self.assertRaises(ValueError) as cm:
                ledger.record(kind, {"x": 1})
            self.assertIn("must be dotted lower-case words (e.g. golden.run), at most 64 characters", str(cm.exception))
        self.assertEqual(str(self._err("Bad Kind")), "kind 'Bad Kind' must be dotted lower-case words (e.g. golden.run), at most 64 characters")
        self.assertEqual(ledger.count(), 0)
        ledger.record("a" * 61 + ".bb", {"x": 1})  # exactly 64 characters: accepted
        ledger.record("golden-path.run-2.v1", {"x": 1})
        self.assertEqual(ledger.count(), 2)

    def _err(self, kind):
        try:
            ledger.record(kind, {})
        except ValueError as e:
            return e
        return None

    def test_payload_must_be_a_json_object(self) -> None:
        for payload in ([1, 2], "text", 3, None, True):
            with self.assertRaises(ValueError) as cm:
                ledger.record("golden.run", payload)
            self.assertEqual(str(cm.exception), "payload must be a JSON object")
        with self.assertRaises(ValueError):
            ledger.record("golden.run", {"x": float("nan")})

    def test_payload_size_limit(self) -> None:
        def payload_of(size: int) -> dict:
            base = len(json.dumps({"blob": ""}, separators=(",", ":")).encode("utf-8"))
            return {"blob": "x" * (size - base)}

        n, _ = ledger.record("golden.run", payload_of(ledger.MAX_PAYLOAD_BYTES))
        self.assertEqual(n, 1)
        with self.assertRaises(ValueError) as cm:
            ledger.record("golden.run", payload_of(ledger.MAX_PAYLOAD_BYTES + 1))
        self.assertEqual(str(cm.exception), "payload is 65537 bytes; the limit is 65536")
        self.assertEqual(ledger.count(), 1)


class CliTests(LedgerTestCase):
    def test_cli_record_json_and_text(self) -> None:
        f = self.payload_file({"project": "p", "run_id": "r1", "verdict": "PASS", "note": "café ·"})
        rc, out, err = _run_cli(["ledger", "record", "golden.run", "--json", f, "--print", "json"])
        self.assertEqual((rc, err), (0, ""))
        rec = json.loads(out)
        self.assertEqual(set(rec), {"recorded", "n", "kind", "ts", "hash", "prev", "path"})
        self.assertEqual((rec["recorded"], rec["n"], rec["kind"], rec["prev"]), (True, 1, "golden.run", ledger.GENESIS))
        self.assertEqual(rec["path"], str(ledger.path()))
        self.assertEqual(ledger.entries()[0]["payload"]["note"], "café ·")
        rc, out, _ = _run_cli(["ledger", "record", "golden.run", "--json", f])
        self.assertEqual(rc, 0)
        h = ledger.entries()[-1]["hash"]
        self.assertEqual(out.strip(), f"recorded golden.run as ledger entry 2 ({h[:12]}...) in {ledger.path()}")

    def test_cli_record_from_stdin(self) -> None:
        rc, out, _ = _run_cli(["ledger", "record", "golden.run", "--json", "-", "--print", "json"],
                              stdin=json.dumps({"run_id": "stdin"}).encode("utf-8"))
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["n"], 1)
        self.assertEqual(ledger.entries()[0]["payload"], {"run_id": "stdin"})

    def test_cli_record_refusals_exit_2(self) -> None:
        good = self.payload_file({"x": 1})
        cases = [
            (["ledger", "record", "policy.override", "--json", good], "aces: kind 'policy.override' is written by aces itself"),
            (["ledger", "record", "Bad Kind", "--json", good], "aces: kind 'Bad Kind' must be dotted lower-case words"),
            (["ledger", "record", "golden.run", "--json", self.payload_file("{not json", "bad.json")], "aces: "),
            (["ledger", "record", "golden.run", "--json", self.payload_file([1], "list.json")], "aces: payload must be a JSON object"),
            (["ledger", "record", "golden.run", "--json", str(self.tmp / "missing.json")], "aces: cannot read"),
        ]
        for argv, msg in cases:
            rc, out, err = _run_cli(argv)
            self.assertEqual(rc, 2, argv)
            self.assertEqual(out, "")
            self.assertTrue(err.startswith(msg), (argv, err))
        self.assertEqual(ledger.count(), 0)

    def test_cli_verify_json_and_tamper(self) -> None:
        f = self.payload_file({"run_id": "r"})
        for _ in range(3):
            self.assertEqual(_run_cli(["ledger", "record", "golden.run", "--json", f])[0], 0)
        rc, out, _ = _run_cli(["ledger", "verify", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), {"ok": True, "entries": 3, "message": "chain intact", "path": str(ledger.path())})
        lines = ledger.path().read_text(encoding="utf-8").splitlines()
        lines[1] = lines[1].replace('"run_id": "r"', '"run_id": "tampered"')
        ledger.path().write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        rc, out, _ = _run_cli(["ledger", "verify", "--json"])
        doc = json.loads(out)
        self.assertEqual((rc, doc["ok"], doc["entries"]), (1, False, 2))
        self.assertIn("entry 2", doc["message"])
        rc, out, _ = _run_cli(["ledger", "verify"])
        self.assertEqual(rc, 1)
        self.assertTrue(out.startswith("FAIL: 2 entries"))

    def test_cli_show_kind_json(self) -> None:
        f = self.payload_file({"run_id": "r"})
        _run_cli(["ledger", "record", "golden.run", "--json", f])
        ledger.append("policy.override", {"component": "c", "reason": "test", "terms": ""})
        _run_cli(["ledger", "record", "other.kind", "--json", f])
        _run_cli(["ledger", "record", "golden.run", "--json", f])
        rc, out, _ = _run_cli(["ledger", "show", "-n", "0", "--kind", "golden.run", "--json"])
        self.assertEqual(rc, 0)
        shown = json.loads(out)
        self.assertEqual([e["n"] for e in shown], [1, 4])
        self.assertTrue(all(e["kind"] == "golden.run" for e in shown))
        rc, out, _ = _run_cli(["ledger", "show", "-n", "1", "--json"])
        self.assertEqual([e["n"] for e in json.loads(out)], [4])
        rc, out, _ = _run_cli(["ledger", "show", "--kind", "policy.override"])
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertIn("policy.override", out)

    def test_concurrent_writers_keep_the_chain(self) -> None:
        ctx = multiprocessing.get_context("spawn")
        procs = [ctx.Process(target=_writer, args=(str(config.STATE), tag, 25)) for tag in ("a", "b")]
        for p in procs:
            p.start()
        for p in procs:
            p.join(120)
            self.assertEqual(p.exitcode, 0)
        ok, n, msg = ledger.verify()
        self.assertTrue(ok, msg)
        self.assertEqual(n, 50)
        es = ledger.entries()
        for tag in ("a", "b"):
            self.assertEqual(sorted(e["payload"]["i"] for e in es if e["payload"]["writer"] == tag), list(range(25)))


class ProjectTests(LedgerTestCase):
    def test_add_evidence_and_cad_source(self) -> None:
        self.assertEqual(_run_cli(["project", "new", "check-project", "--hypothesis", "test: 10 %"])[0], 0)
        ref = "golden/20261009T000000Z-abcdef bundle:0123456789abcdef PASS"
        rc, out, _ = _run_cli(["project", "add-evidence", "check-project", ref])
        self.assertEqual(rc, 0)
        self.assertIn("recorded against check-project", out)
        rc, out, _ = _run_cli(["project", "add-evidence", "check-project", ref])
        self.assertIn("already recorded", out)
        self.assertEqual(_run_cli(["project", "add-cad-source", "check-project", "cad/part.step"])[0], 0)
        self.assertEqual(_run_cli(["project", "add-component", "check-project", "aces-engineering"])[0], 0)
        data = project.show("check-project")
        self.assertEqual(data["evidence"], [ref])
        self.assertEqual(data["cad_sources"], ["cad/part.step"])
        self.assertEqual(data["components"], ["aces-engineering"])
        self.assertEqual(data["hypothesis"], "test: 10 %")
        rc, out, _ = _run_cli(["project", "show", "check-project", "--json"])
        self.assertEqual(json.loads(out)["evidence"], [ref])

    def test_add_evidence_to_a_missing_project_fails(self) -> None:
        with self.assertRaises(SystemExit):
            project.add_evidence("no-such-project", "x")


class GoldenPassthroughTests(LedgerTestCase):
    def test_missing_aces_golden_exits_127(self) -> None:
        empty = self.tmp / "empty-bin"
        empty.mkdir()
        os.environ.pop("ACES_GOLDEN_CMD", None)
        os.environ["PATH"] = str(empty)
        rc, out, err = _run_cli(["golden", "run", "--demo"])
        self.assertEqual(rc, 127)
        self.assertEqual(err.strip(), cli.GOLDEN_MISSING)
        self.assertEqual(out, "")

    def test_aces_golden_cmd_runs_and_returns_the_childs_status(self) -> None:
        os.environ["ACES_GOLDEN_CMD"] = json.dumps([sys.executable, "-c", "import sys; sys.exit(len(sys.argv))"])
        self.assertEqual(cli.main(["golden", "a", "b"]), 3)
        self.assertEqual(cli.main(["golden", "--demo", "--json", "x"]), 4)  # leading options pass through untouched
        os.environ["ACES_GOLDEN_CMD"] = "[1"
        rc, _, err = _run_cli(["golden", "run"])
        self.assertEqual(rc, 2)
        self.assertIn("ACES_GOLDEN_CMD", err)
        os.environ["ACES_GOLDEN_CMD"] = str(self.tmp / "no-such-aces-golden")
        rc, _, err = _run_cli(["golden", "run"])
        self.assertEqual(rc, 127)

    def test_ctrl_c_waits_for_the_child_and_returns_its_status(self) -> None:
        from unittest import mock

        class Child:  # a stand-in child: the console Ctrl-C reaches both processes; the child exits 130 afterwards
            waits = 0

            def wait(self):
                Child.waits += 1
                if Child.waits == 1:
                    raise KeyboardInterrupt
                return 130

        os.environ["ACES_GOLDEN_CMD"] = json.dumps([sys.executable, "-c", "pass"])
        with mock.patch.object(cli.subprocess, "Popen", return_value=Child()) as popen:
            self.assertEqual(cli.main(["golden", "run", "--demo"]), 130)
        popen.assert_called_once()
        self.assertEqual(popen.call_args[0][0][-2:], ["run", "--demo"])
        self.assertEqual(Child.waits, 2)

    def test_python_dash_m_aces(self) -> None:
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(PKG_ROOT)] + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else [])))
        out = subprocess.run([sys.executable, "-m", "aces", "--version"], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "aces 0.1.0")


if __name__ == "__main__":
    unittest.main()
