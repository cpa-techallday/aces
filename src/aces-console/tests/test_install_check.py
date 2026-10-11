"""Builder D: tests/install_check.py, the script bin/install-test runs in the clean container, run here against this
source tree's server: a real `serve` child on a free loopback port, the upstreams pointed at closed loopback ports (no
rig is contacted), a ledger with three real entries written through aces.ledger.record (the count the default install
set leaves), and a random ACES_GC_TOKEN in the child's environment that must appear in no reply and no log line.
--allow-missing-modules is passed because a source tree may lack the ledgers and system modules of the backend; the
install test in the container runs the check without it.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import unittest

from aces import config as aces_config, ledger

from . import ACES_BASE, PKG_ROOT, ROOT

SCRIPT = PKG_ROOT / "tests" / "install_check.py"


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class InstallCheckTest(unittest.TestCase):
    def setUp(self):
        self.state = ROOT / "state-install-check"
        self.cfg = ROOT / "cfg-install-check"
        self.state.mkdir(parents=True, exist_ok=True)
        self.cfg.mkdir(parents=True, exist_ok=True)
        for old in (self.state / "ledger.jsonl", self.state / "console" / "server.log"):
            if old.is_file():
                old.unlink()
        # the upstreams at closed loopback ports: every proxy answers "unreachable", nothing leaves this machine
        closed = free_port()
        (self.cfg / "aces.toml").write_text(
            f'[server]\ngrid_control = "http://127.0.0.1:{closed}"\nrig_state = "http://127.0.0.1:{closed}/mcp"\n'
            f'simlab_mcp = "http://127.0.0.1:{closed}/mcp"\nsimlab_console = "http://127.0.0.1:{closed}"\nmlflow = "http://127.0.0.1:{closed}"\ntimeout_s = 1\n',
            encoding="utf-8")
        self.env = dict(os.environ)
        self.env.update({"ACES_STATE": str(self.state), "ACES_USER_CONFIG": str(self.cfg), "ACES_GC_TOKEN": secrets.token_hex(16),
                         "PYTHONPATH": os.pathsep.join([str(PKG_ROOT), str(ACES_BASE)])})
        # three real ledger entries, as the default install set leaves (classify, policy override, ...); written through
        # aces.ledger.record into the check's own state directory, restored in tearDown
        self._saved_state, self._saved_env = aces_config.STATE, os.environ.get("ACES_STATE")
        aces_config.STATE = self.state
        os.environ["ACES_STATE"] = str(self.state)
        for i in range(3):
            ledger.record("install-check.entry", {"n": i + 1, "note": "written by tests/test_install_check.py"})

    def tearDown(self):
        aces_config.STATE = self._saved_state
        if self._saved_env is None:
            os.environ.pop("ACES_STATE", None)
        else:
            os.environ["ACES_STATE"] = self._saved_env

    def test_install_check_against_this_tree(self):
        prog = (f"import sys; sys.path[:0] = {json.dumps([str(PKG_ROOT), str(ACES_BASE)])}; from aces_console.cli import main; "
                "raise SystemExit(main(sys.argv[1:]))")
        console_cmd = [sys.executable, "-I", "-c", prog]
        aces_cmd = [sys.executable, "-m", "aces"]
        port = free_port()
        proc = subprocess.run([sys.executable, "-I", str(SCRIPT), "--port", str(port), "--expect-ledger", "3", "--allow-missing-modules",
                               "--console-cmd-json", json.dumps(console_cmd), "--aces-cmd-json", json.dumps(aces_cmd)],
                              capture_output=True, text=True, timeout=180, env=self.env)
        sys.stderr.write("\n--- install_check.py ---\n" + proc.stdout[-3000:] + proc.stderr[-1500:] + "\n")
        self.assertEqual(proc.returncode, 0, proc.stdout[-2000:] + proc.stderr[-1000:])
        self.assertIn("== aces-console install check passed", proc.stdout)
        self.assertIn("ok   health.bind is 127.0.0.1", proc.stdout)
        self.assertIn("ok   ledger entries 3 == 3", proc.stdout)
        self.assertIn("ok   POST without the human headers is refused with 403 'refused'", proc.stdout)
        self.assertIn("no rig from here: data null", proc.stdout)
        self.assertNotIn(self.env["ACES_GC_TOKEN"], proc.stdout + proc.stderr)
        log = self.state / "console" / "server.log"
        self.assertTrue(log.is_file(), "the server wrote its log under $ACES_STATE/console")
        self.assertNotIn(self.env["ACES_GC_TOKEN"], log.read_text(encoding="utf-8", errors="replace"))
        ok, n, msg = ledger.verify()
        self.assertTrue(ok, msg)
        # with the system module installed the stop writes console.server.stop; without it a SIGTERM stop writes nothing
        self.assertIn(n, (3, 4))
        if n == 4:
            self.assertIn("ok   the newest ledger entry is console.server.stop", proc.stdout)
        else:
            self.assertIn("modules.system is 'not installed'", proc.stdout)
            self.assertIn("the ledger did not change on a SIGTERM stop", proc.stdout)

    def test_strict_mode_names_a_missing_module(self):
        """Without --allow-missing-modules a backend lacking the ledgers module fails, naming it (the container runs strict)."""
        prog = (f"import sys; sys.path[:0] = {json.dumps([str(PKG_ROOT), str(ACES_BASE)])}; from aces_console.cli import main; "
                "raise SystemExit(main(sys.argv[1:]))")
        probe = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path[:0] = " + json.dumps([str(PKG_ROOT), str(ACES_BASE)])
                                + "; import importlib.util as u; print(int(u.find_spec('aces_console.ledgers') is not None))"],
                               capture_output=True, text=True, timeout=60, env=self.env)
        if probe.stdout.strip() == "1":
            self.skipTest("this tree has the ledgers module; the strict path is the normal path here")
        port = free_port()
        proc = subprocess.run([sys.executable, "-I", str(SCRIPT), "--port", str(port), "--expect-ledger", "3",
                               "--console-cmd-json", json.dumps([sys.executable, "-I", "-c", prog]),
                               "--aces-cmd-json", json.dumps([sys.executable, "-m", "aces"])],
                              capture_output=True, text=True, timeout=180, env=self.env)
        self.assertEqual(proc.returncode, 1, proc.stdout[-1500:])
        self.assertIn("FAIL modules.ledgers is 'not installed'", proc.stdout)
        self.assertNotIn(self.env["ACES_GC_TOKEN"], proc.stdout + proc.stderr)

    def test_usage_errors(self):
        proc = subprocess.run([sys.executable, "-I", str(SCRIPT), "--expect-ledger", "many"], capture_output=True, text=True, timeout=60, env=self.env)
        self.assertEqual(proc.returncode, 2)
        proc = subprocess.run([sys.executable, "-I", str(SCRIPT), "--console-cmd-json", "not json"], capture_output=True, text=True, timeout=60, env=self.env)
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
