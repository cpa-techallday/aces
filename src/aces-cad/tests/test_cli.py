"""C01-C03 of build spec section 21: the aces-cad CLI (section 18), and the installed shims (section 22.2).

C01 reaches every exit code without build123d: params needs no project and no worker, and the fake worker supplies
the worker-side kinds (sandbox_unavailable 3, timeout 4, sandbox 5, crash 1). C02 checks the doctor JSON, C03 runs
`aces-cad selftest --quick` with a real worker.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import base
from base import POSIX, CadTestCase, WorkerTestCase

SHIM_LIB = "/opt/aces/cad/lib"


class CliTest(CadTestCase):

    def setUp(self):
        super().setUp()
        self.make_project("p", "sovereign")
        self.plate = str(base.SCRIPTS_DIR / "aces_plate.py")

    def assert_json_error(self, code: int, doc: dict, kind: str, exit_code: int) -> None:
        self.assertEqual(code, exit_code, doc)
        self.assertEqual(sorted(doc), ["error"], "a failure is exactly one document {\"error\": {...}}")
        self.assertEqual((doc["error"]["kind"], doc["error"]["exit_code"]), (kind, exit_code))
        self.assertTrue(doc["error"]["message"])

    def test_c01_version_and_params_without_a_project(self):
        code, out, err = self.cli("--version")
        self.assertEqual((code, out.strip()), (0, "aces-cad 0.1.0"), err)
        code, doc = self.cli_json("params", self.plate)
        self.assertEqual(code, 0)
        self.assertEqual(sorted(doc["params_declared"]), ["hole_d", "length", "thickness", "width"])
        self.assertEqual(doc["entry"], "build")
        code, out, err = self.cli("params", "-", stdin=self.script("gen_step_plate"))
        self.assertEqual(code, 0, err)
        self.assertIn("gen_step", out)
        os.environ["ACES_CAD_PROJECT"] = "p"
        code, doc = self.cli_json("params", self.plate)
        self.assertEqual(code, 0)

    def test_c01_exit_codes_without_a_worker(self):
        code, doc = self.cli_json("params", str(base.SCRIPTS_DIR / "syntax_error.py"))
        self.assert_json_error(code, doc, "script", 1)
        self.assertEqual(doc["error"]["line"], base.expected("errors.syntax_error")["line"])
        code, doc = self.cli_json("params", str(base.SCRIPTS_DIR / "params_not_literal.py"))
        self.assert_json_error(code, doc, "params", 2)
        code, doc = self.cli_json("run", "--project", "p")
        self.assert_json_error(code, doc, "usage", 2)
        code, doc = self.cli_json("run", self.plate, "--project", "nowhere")
        self.assert_json_error(code, doc, "project", 2)
        code, doc = self.cli_json("run", self.plate, "--project", "p", "--param", "lenght=70")
        self.assert_json_error(code, doc, "params", 2)
        code, out, err = self.cli("no-such-verb")
        self.assertEqual(code, 2)
        code, out, err = self.cli("run", self.plate, "--project", "nowhere")
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.startswith("aces-cad: project: "), err)
        self.assertNotIn("Traceback", err)

    def test_c01_exit_codes_from_the_worker(self):
        cases = ((("error", "sandbox_unavailable"), "sandbox_unavailable", 3),
                 (("error", "sandbox"), "sandbox", 5),
                 (("error", "memory"), "memory", 4),
                 (("segv",), "crash", 1))
        for fake, kind, exit_code in cases:
            self.use_fake_worker(*fake)
            code, doc = self.cli_json("run", self.plate, "--project", "p")
            self.assert_json_error(code, doc, kind, exit_code)
            self.assert_run_id(doc["error"]["run_id"])
        self.use_fake_worker("slow", "600")
        code, doc = self.cli_json("run", self.plate, "--project", "p", "--timeout", "2")
        self.assert_json_error(code, doc, "timeout", 4)
        self.use_fake_worker("ok")
        code, doc = self.cli_json("runs", "--project", "p")
        self.assertEqual(code, 0)
        self.assertEqual(doc["count"], 5)
        self.assertTrue(all(r["status"] == "error" for r in doc["runs"]))

    def test_c02_doctor_json_with_the_fake_worker(self):
        self.use_fake_worker("ok")
        os.environ["ACES_CAD_SANDBOX"] = "permissive"
        code, doc = self.cli_json("doctor")
        self.assertIn(code, (0, 3))
        self.assertEqual(code == 0, doc["verdict"] == "ready")
        for key in ("verdict", "mode", "worker", "sandbox", "checks", "aces", "cache_dir"):
            self.assertIn(key, doc)
        self.assertEqual(doc["mode"], "permissive")
        self.assertEqual(sorted(doc["worker"])[:3], ["ok", "python", "versions"])
        self.assertEqual(sorted(doc["aces"]), ["add_cad_source", "cli", "ledger_record"])
        for check in doc["checks"]:
            self.assertEqual(sorted(check), ["check", "expect", "got", "pass"])
        os.environ["ACES_CAD_SANDBOX"] = "strict"
        code, doc = self.cli_json("doctor")
        self.assertEqual((code, doc["verdict"]), (3, "not-ready"), "the fake worker has no kernel guards")

    def test_c04_the_installed_shims(self):
        for name, call in (("aces-cad", "from aces_cad.cli import main; sys.exit(main(sys.argv[1:]))"),
                           ("aces-cad-mcp", "from aces_cad.mcp_server import main; sys.exit(main())")):
            path = base.PKG_ROOT / "packaging" / name
            if not path.is_file():
                installed = Path("/usr/bin") / name
                if not installed.is_file():
                    self.skipTest("neither the source shims nor the installed ones are here")
                path = installed
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("#!/bin/sh\n"), name)
            self.assertIn(f"exec /usr/bin/python3 -I -B -c 'import sys; sys.path.insert(0, \"{SHIM_LIB}\"); "
                          f"{call}' \"$@\"\n", text, name)
            if not POSIX or not shutil.which("sh"):
                continue
            root = base.lib_root()
            if root is None:
                self.skipTest("the aces_cad package is not importable here")
            local = self.tmp / name
            local.write_text(text.replace("/usr/bin/python3", sys.executable).replace(SHIM_LIB, str(root)),
                             encoding="utf-8", newline="\n")
            os.chmod(local, 0o755)
            if name == "aces-cad":
                done = subprocess.run([str(local), "--version"], capture_output=True, timeout=120,
                                      env=self.child_env())
                self.assertEqual((done.returncode, done.stdout.decode().strip()), (0, "aces-cad 0.1.0"),
                                 done.stderr.decode())
            else:
                done = subprocess.run([str(local)], input=b'{"jsonrpc": "2.0", "id": 1, "method": "ping"}\n',
                                      capture_output=True, timeout=120, env=self.child_env())
                self.assertEqual(done.returncode, 0, done.stderr.decode())
                self.assertEqual(json.loads(done.stdout.decode().splitlines()[0]),
                                 {"jsonrpc": "2.0", "id": 1, "result": {}})


class CliWorkerTest(WorkerTestCase):

    def test_c01_a_real_run_and_its_human_line(self):
        code, out, err = self.cli("run", str(base.SCRIPTS_DIR / "aces_plate.py"), "--project", "p", "--name", "plate")
        self.assertEqual(code, 0, err)
        self.assertRegex(out, r"run [0-9]{8}T[0-9]{6}Z-[0-9a-f]{4} ok")
        self.assertIn("11607.30", out)
        code, doc = self.cli_json("run", str(base.SCRIPTS_DIR / "aces_plate.py"), "--project", "p",
                                  "--param", "length=70")
        self.assertEqual(code, 0)
        self.assert_expected(doc["result"]["parts"][0]["volume_mm3"], "aces_plate.length70_volume_mm3")
        code, doc = self.cli_json("run", str(base.SCRIPTS_DIR / "name_error.py"), "--project", "p")
        self.assertEqual((code, doc["error"]["kind"]), (1, "script"))

    def test_c02_doctor_json_with_the_real_worker(self):
        code, doc = self.cli_json("doctor")
        self.assertEqual(code == 0, doc["verdict"] == "ready", doc)
        self.assertEqual(code, 0 if doc["verdict"] == "ready" else 3)
        self.assertIs(doc["worker"]["ok"], True, doc)
        self.assertIn("build123d", json.dumps(doc["worker"]["versions"]).lower())
        if self.mode == "strict":
            self.assertEqual(doc["verdict"], "ready", doc)
            self.assertTrue(all(c["pass"] is True for c in doc["checks"] if c["pass"] != "skipped"), doc["checks"])

    def test_c03_selftest_quick(self):
        done = self.cli_process("selftest", "--quick", timeout=900)
        out = done.stdout.decode("utf-8", "replace")
        self.assertEqual(done.returncode, 0, out[-3000:] + done.stderr.decode("utf-8", "replace")[-2000:])
        self.assertNotIn("FAIL", out)
        self.assertIn("PASS", out)


if __name__ == "__main__":
    unittest.main()
