"""H01-H17 of build spec section 21: hostile scripts against the sandbox (section 8), with real workers.

Rows the spec marks (strict) need both kernel guards (seccomp and Landlock) and skip with "strict-mode tests skipped:
<missing>" otherwise. Limits that only rlimits can enforce (memory, file size) skip where they are not enforced
(Windows). Every target outside the run (secret files, write targets, loopback ports) is planted by the test and
passed through the script's str parameters; nothing here can reach another machine.
"""
from __future__ import annotations

import json
import os
import secrets
import signal
import socket
import sys
import time
import unittest
from pathlib import Path

import base
import mcp_client
from base import POSIX, WORKER_ENV_KEYS, WorkerTestCase, expected


def workers_under(root: Path) -> list[int]:
    """Live processes whose working directory is under root (Linux /proc): this test's workers."""
    found = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit() or int(d.name) == os.getpid():
            continue
        try:
            cwd = os.readlink(d / "cwd")
            state = (d / "stat").read_text().rsplit(")", 1)[1].split()[0]
        except OSError:
            continue
        if cwd.startswith(str(root)) and state not in ("Z", "X"):
            found.append(int(d.name))
    return found


class HostileTest(WorkerTestCase):

    def hostile(self, script_name: str, kind, /, **kw) -> dict:
        t0 = time.monotonic()
        err = self.run_error(script_name, kind, **kw)
        err["_wall"] = time.monotonic() - t0
        return err

    def assert_no_worker_left(self) -> None:
        if sys.platform.startswith("linux"):
            self.wait_for(lambda: not workers_under(self.tmp), 5, what="every worker of this test to be gone")

    def test_h01_endless_loop_times_out(self):
        err = self.hostile("hostile_loop", "timeout", timeout_s=3)
        self.assertEqual(err["exit_code"], 4)
        self.assertEqual(err.get("limit"), "timeout_s")
        self.assertLess(err["_wall"], 8)
        self.assertIsInstance(err.get("leftover_files", []), list)
        self.assert_run_id(err["run_id"])
        self.assert_no_worker_left()

    def test_h02_sleep_times_out_on_the_wall_clock(self):
        err = self.hostile("hostile_sleep", "timeout", timeout_s=3)
        self.assertLess(err["_wall"], 8)
        self.assert_no_worker_left()

    def test_h03_memory_bombs(self):
        self.require_posix("RLIMIT_AS")
        os.environ["ACES_CAD_MEMORY_MB"] = "2048"
        for name in ("hostile_memory_list", "hostile_memory_numpy"):
            err = self.hostile(name, "memory", timeout_s=120)
            self.assertEqual(err["exit_code"], 4, name)

    def test_h04_network_is_refused(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        port = str(listener.getsockname()[1])
        try:
            for name in ("hostile_socket", "hostile_urlopen"):
                err = self.hostile(name, "sandbox", params={"port": port})
                self.assertEqual(err["exit_code"], 5, name)
            listener.settimeout(2)
            with self.assertRaises(socket.timeout, msg="the listener accepted a connection from a worker"):
                conn, _addr = listener.accept()
                conn.close()
        finally:
            listener.close()

    def test_h05_writing_outside_the_run_is_refused(self):
        target = self.tmp / f"outside-{secrets.token_hex(4)}.txt"
        err = self.hostile("hostile_write_outside", "sandbox", params={"target": str(target)})
        self.assertEqual(err["exit_code"], 5)
        self.assertFalse(target.exists())

    def planted_secret(self) -> tuple[Path, str]:
        marker = f"aces-cad-marker-{secrets.token_hex(8)}"
        cfg = Path(os.environ["ACES_USER_CONFIG"])
        cfg.mkdir(parents=True, exist_ok=True)
        path = cfg / "onshape.env"
        path.write_text(f"ONSHAPE_SECRET_KEY={marker}\n", encoding="utf-8")
        return path, marker

    def test_h06_a_planted_secret_cannot_be_read(self):
        self.require_strict()
        path, marker = self.planted_secret()
        err = self.hostile("hostile_read_secret", "sandbox", params={"secret": str(path)})
        rec = json.loads((self.runs_dir() / err["run_id"] / "run.json").read_text(encoding="utf-8"))
        self.assert_marker_absent(marker, err, rec)

    def test_h07_occt_file_io_outside_the_run(self):
        self.require_strict()
        marker = f"aces-cad-marker-{secrets.token_hex(8)}"
        secret = self.make_step(Path(os.environ["ACES_USER_CONFIG"]) / "secret.step", label=marker)
        err = self.run_error("hostile_occt_import_secret", ("script", "kernel", "sandbox", "result"),
                             params={"secret": str(secret)})
        self.assert_marker_absent(marker, err)
        target = self.tmp / f"escape-{secrets.token_hex(4)}.step"
        err = self.run_error("hostile_occt_export_outside", None, params={"target": str(target)})
        self.assertIn(err["kind"], ("script", "kernel", "sandbox"), err)
        self.assertFalse(target.exists(), "OCCT wrote outside the run")

    def test_h08_the_parent_environment_stays_private(self):
        self.require_strict()
        marker = f"aces-cad-marker-env-{secrets.token_hex(8)}"
        os.environ["ACES_CAD_TEST_ENV_MARKER"] = marker
        os.environ["ONSHAPE_ACCESS_KEY"] = marker
        err = self.hostile("hostile_proc_environ", "sandbox")
        line = [ln for ln in err.get("stdout", "").splitlines() if ln.startswith("ENV ")]
        self.assertEqual(len(line), 1, err.get("stdout", "")[:500])
        keys = set(line[0].split()[1:])
        allowed = WORKER_ENV_KEYS | set(expected("worker_env.added_by_imports"))
        self.assertEqual(keys - allowed, set(), "the worker environment holds keys outside the allowlist")
        self.assert_marker_absent(marker, err)

    def test_h09_processes_libraries_and_signals_are_refused(self):
        names = ["hostile_subprocess", "hostile_os_system", "hostile_ctypes"]
        if POSIX:
            names += ["hostile_fork", "hostile_kill_parent"]  # os.kill on Windows terminates; os.fork does not exist
        for name in names:
            err = self.hostile(name, "sandbox")
            self.assertEqual(err["exit_code"], 5, name)

    def test_h10_a_print_flood_is_cut_to_head_and_tail(self):
        out = self.run_script("hostile_print_flood")
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["stdout_truncated"])
        self.assertRegex(out["stdout"], r"\[\.\.\. \d+ bytes omitted \.\.\.\]")
        self.assertLessEqual(len(out["stdout"].encode("utf-8")), (64 << 10) + 200)

    def test_h11_file_size_and_job_directory_caps(self):
        if POSIX:
            os.environ.update({"ACES_CAD_MAX_FILE_MB": "64", "ACES_CAD_KEEP_JOBS": "1"})
            err = self.hostile("hostile_big_file", "disk", timeout_s=300)
            self.assertEqual(err["exit_code"], 4)
            self.assertEqual(err.get("limit"), "file_size")
            big = list((self.runs_dir() / err["run_id"]).glob("jobs/run-*/cwd/big.bin"))
            for f in big:
                self.assertLessEqual(f.stat().st_size, 64 << 20)
            os.environ.pop("ACES_CAD_MAX_FILE_MB")
            os.environ.pop("ACES_CAD_KEEP_JOBS")
        os.environ["ACES_CAD_MAX_RUN_MB"] = "64"
        err = self.hostile("hostile_many_files", "disk", timeout_s=300)
        self.assertEqual(err["exit_code"], 4)
        self.assertLess(err["_wall"], 60)

    def test_h12_symlinks_cannot_be_created(self):
        self.require_strict()
        err = self.hostile("hostile_symlink", "sandbox")
        self.assertEqual(err["exit_code"], 5)

    def test_h13_a_forged_summary_does_not_reach_the_pull(self):
        run = self.run_script("hostile_forge_summary")
        self.assertEqual(run["status"], "ok")
        pulled = self.op("pull", project="p", run_id=run["run_id"])
        source = json.loads((Path(pulled["bundle"]) / "source.json").read_text(encoding="utf-8"))
        self.assertEqual(len(source["result"]["parts"]), 1)
        self.assert_close(source["result"]["parts"][0]["volume_mm3"], 1000.0, 1e-6,
                          "the pull re-derives the volume in a worker that runs no agent code")
        self.assertNotEqual(source["result"]["parts"][0]["name"], "forged")

    def test_h14_a_killed_server_takes_its_worker_along(self):
        self.require_linux("PR_SET_PDEATHSIG")
        env = self.child_env()
        with mcp_client.Server(env, self.tmp) as srv:
            srv.initialize()
            srv.start_request("tools/call", {"name": "cad_run", "arguments": {
                "project": "p", "script": self.script("hostile_loop"), "timeout_s": 60}})
            workers = self.wait_for(lambda: base.children_of(srv.proc.pid), 30, what="the worker to start")
            time.sleep(1.0)
            os.kill(srv.proc.pid, signal.SIGKILL)
            srv.proc.wait(timeout=10)
            t0 = time.monotonic()
            self.wait_for(lambda: not any(base.pid_alive(w) for w in workers), 3,
                          what="the worker to die with its server (PDEATHSIG)")
            self.assertLess(time.monotonic() - t0, 3)

    def test_h15_fifty_threads(self):
        out = self.run_script("hostile_threads", timeout_s=300)
        self.assertEqual(out["status"], "ok")

    def test_h16_audit_hooks_and_tracing_are_refused(self):
        for name in ("hostile_addaudithook", "hostile_settrace"):
            err = self.hostile(name, "sandbox")
            self.assertEqual(err["exit_code"], 5, name)

    def test_h17_ansi_output_is_cleaned(self):
        out = self.run_script("hostile_ansi_occt")
        self.assertEqual(out["status"], "ok")
        for text in (out["stdout"], out["stderr"]):
            self.assertFalse(base.strip_ansi_present(text), repr(text[:300]))
            self.assertNotIn("\x1b", text)
            self.assertNotIn("\x07", text)
        self.assertIn("python stdout line (fixture)", out["stdout"])
        self.assertIn("native stdout line (fixture)", out["stdout"])
        self.assertIn("OCCT-like error line (fixture)", out["stderr"])


if __name__ == "__main__":
    unittest.main()
