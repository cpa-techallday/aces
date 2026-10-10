"""U06 of build spec section 21: the parent side of the sandbox (section 8.1 and 8.8) with the fake worker.

sandbox.new_job and sandbox.run_worker are called directly, with ACES_CAD_WORKER_CMD pointing at tests/fake_worker.py:
the environment allowlist, the log and job-directory caps, the wall clock killing the whole process group (a
grandchild included), malformed, symlinked and oversized results, a SIGSEGV, cancellation, and the cleaning of the
output (ANSI escapes, control characters, Fontconfig noise, invalid UTF-8, head and tail with the omission marker).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import unittest
from pathlib import Path

import base
from base import POSIX, WORKER_ENV_KEYS, WORKER_ENV_KEYS_WINDOWS, CadTestCase

JOB_KEYS = ["args", "cache_dir", "cache_rw", "job_dir", "job_id", "lib_dir", "limits", "op", "parent_pid", "ro_dirs",
            "sandbox", "schema"]


class SandboxUnitTest(CadTestCase):

    def setUp(self):
        super().setUp()
        from aces_cad import errors, sandbox, store
        self.errors, self.sandbox, self.store = errors, sandbox, store
        self.base = self.tmp / "jobs-base"
        self.base.mkdir()

    def limits(self, timeout_s: int = 60):
        return self.sandbox.Limits(timeout_s=timeout_s, memory_mb=4096, threads=2, max_file_mb=1024, nice=10)

    def run_fake(self, mode: str, *args: str, op: str = "env", timeout_s: int = 60, cache_rw: bool = False,
                 cancel=None, progress=None):
        self.use_fake_worker(mode, *args)
        job_dir = self.sandbox.new_job(self.base, op)
        t0 = time.monotonic()
        outcome = self.sandbox.run_worker(op, {}, job_dir=job_dir, ro_dirs=[], cache_rw=cache_rw,
                                          limits=self.limits(timeout_s), agent_code=False, cancel=cancel,
                                          progress=progress)
        return outcome, time.monotonic() - t0

    def assert_error(self, outcome, kind: str, exit_code: int) -> dict:
        self.assertEqual(outcome.status, "error", outcome)
        self.assertEqual(outcome.error["kind"], kind, outcome.error)
        with self.assertRaises(self.errors.CadError) as ctx:
            self.sandbox.raise_for(outcome)
        self.assertEqual((ctx.exception.kind, ctx.exception.exit_code), (kind, exit_code))
        return outcome.error

    @staticmethod
    def fake_pid(outcome) -> int:
        m = re.search(r"FAKE_WORKER pid (\d+)", outcome.stdout)
        assert m, outcome.stdout[:300]
        return int(m.group(1))

    def test_u06_new_job_layout(self):
        job = Path(self.sandbox.new_job(self.base, "env"))
        self.assertRegex(job.name, r"^env-[0-9a-f]{8}$")
        self.assertEqual(job.parent, self.base / "jobs")
        for sub in ("cwd/home", "cwd/tmp", "out", "log"):
            self.assertTrue((job / sub).is_dir(), sub)
            if POSIX:
                self.assertEqual((job / sub).stat().st_mode & 0o777, 0o700, sub)
        self.sandbox.finish_job(job)
        self.assertFalse(job.exists(), "finish_job removes the job directory")
        os.environ["ACES_CAD_KEEP_JOBS"] = "1"
        kept = Path(self.sandbox.new_job(self.base, "env"))
        self.sandbox.finish_job(kept)
        self.assertTrue(kept.exists(), "ACES_CAD_KEEP_JOBS=1 keeps it")

    def test_u06_environment_allowlist(self):
        os.environ.update({"ONSHAPE_SECRET_KEY": "aces-cad-marker-onshape-0001", "SSH_AUTH_SOCK": "/tmp/fixture.sock",
                           "DISPLAY": ":99", "WAYLAND_DISPLAY": "wayland-fixture", "HTTP_PROXY": "http://proxy.invalid",
                           "DBUS_SESSION_BUS_ADDRESS": "unix:path=/fixture", "PYTHONPATH": "/fixture/pythonpath",
                           "ACES_CAD_PROJECT": "p"})
        self.make_project("p", "open")
        outcome, _wall = self.run_fake("ok")
        self.assertEqual(outcome.status, "ok", outcome.error)
        env = outcome.result["env"]
        allowed = WORKER_ENV_KEYS | (WORKER_ENV_KEYS_WINDOWS if not POSIX else set())
        self.assertEqual(set(env) - allowed, set(), "keys outside the section 8.1 allowlist reached the worker")
        self.assertEqual(WORKER_ENV_KEYS - set(env), set(), "allowlisted keys are missing")
        self.assertNotIn("aces-cad-marker-onshape-0001", json.dumps(outcome.result))
        job = outcome.job_dir
        self.assertEqual(env["HOME"], str(job / "cwd" / "home"))
        self.assertEqual(env["TMPDIR"], str(job / "cwd" / "tmp"))
        if not POSIX:
            self.assertEqual(env.get("USERPROFILE"), str(job / "cwd" / "home"), "never the real profile")
            self.assertEqual((env.get("TEMP"), env.get("TMP")), (env["TMPDIR"], env["TMPDIR"]))
        self.assertEqual(env["PATH"], "/usr/bin:/bin")
        self.assertEqual((env["LANG"], env["LC_ALL"], env["TZ"], env["MPLBACKEND"]), ("C.UTF-8", "C.UTF-8", "UTC",
                                                                                      "Agg"))
        for var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
                    "VECLIB_MAXIMUM_THREADS"):
            self.assertEqual(env[var], "2", var)
        self.assertEqual((env["MALLOC_ARENA_MAX"], env["NO_COLOR"]), ("2", "1"))
        self.assertEqual(env["XDG_CACHE_HOME"], str(job / "cwd" / "home" / ".cache"))
        self.assertEqual(env["MPLCONFIGDIR"], str(job / "cwd" / "home" / ".mpl"))
        self.assertEqual(Path(outcome.result["cwd"]).resolve(), (job / "cwd").resolve())
        self.assertEqual(outcome.result["job_keys"], JOB_KEYS)
        if POSIX:
            self.assertEqual(outcome.result["pgid"], outcome.result["pid"], "the worker leads its own process group")
            self.assertEqual(outcome.result["sid"], outcome.result["pid"], "start_new_session")
        cached, _wall = self.run_fake("ok", cache_rw=True)
        cache = Path(os.environ["ACES_CAD_CACHE"])
        self.assertEqual(Path(cached.result["env"]["XDG_CACHE_HOME"]), cache)
        self.assertEqual(Path(cached.result["env"]["MPLCONFIGDIR"]), cache / "mpl")

    def test_u06_stdout_and_stderr_are_files_with_caps(self):
        outcome, _wall = self.run_fake("print_flood", "200000")
        self.assertEqual(outcome.status, "ok", outcome.error)
        self.assertTrue(outcome.stdout_truncated)
        self.assertRegex(outcome.stdout, r"\[\.\.\. \d+ bytes omitted \.\.\.\]")
        self.assertLessEqual(len(outcome.stdout.encode("utf-8")), (16 << 10) + (48 << 10) + 200)
        self.assertFalse(outcome.stderr_truncated, "20 KB of stderr fits head + tail")
        log = Path(outcome.job_dir) / "log" / "stdout.txt"
        if log.exists():
            self.assertGreaterEqual(log.stat().st_size, 200000, "stdout went to a file, not a pipe")
        os.environ["ACES_CAD_MAX_LOG_MB"] = "1"
        # a flood that keeps going (a pause after every MiB, about 1 s for 50 MB): an unpaced 50 MB write can finish
        # between two of the parent's 100 ms checks on Linux, and a finished job is not killed
        flood, wall = self.run_fake("print_flood", "50000000", "0.02", timeout_s=120)
        err = self.assert_error(flood, "disk", 4)
        self.assertIn("stdout", err["message"])
        self.assertLess(wall, 60)

    def test_u06_timeout_kills_the_group_with_its_grandchild(self):
        outcome, wall = self.run_fake("grandchild_sleep", timeout_s=2)
        err = self.assert_error(outcome, "timeout", 4)
        self.assertEqual(err.get("limit"), "timeout_s")
        self.assertLess(wall, 2 + 5)
        m = re.search(r"GRANDCHILD (\d+)", outcome.stdout)
        self.assertIsNotNone(m, outcome.stdout[:300])
        grandchild = int(m.group(1))
        worker = self.fake_pid(outcome)
        self.wait_for(lambda: not base.pid_alive(grandchild), 5, what="the grandchild to be gone")
        self.assertFalse(base.pid_alive(worker))
        if sys_linux():
            self.assertEqual(base.pgid_members(worker), [], "no member of the worker's process group is left")
        self.assertIsInstance(outcome.leftover_files, list)

    def test_u06_results_that_are_missing_malformed_or_oversized_are_a_crash(self):
        for mode in ("no_result", "bad_json", "oversize_result"):
            outcome, _wall = self.run_fake(mode)
            err = self.assert_error(outcome, "crash", 1)
            self.assertNotIn("Traceback", err["message"], mode)

    def test_u06_a_symlinked_result_is_never_followed(self):
        self.require_posix("a symlinked result.json")
        outcome, _wall = self.run_fake("symlink_result")
        self.assert_error(outcome, "crash", 1)
        self.assertIsNone(outcome.result)
        self.assertNotIn("followed", json.dumps(outcome.error))

    def test_u06_segv_is_a_crash_with_the_signal(self):
        outcome, _wall = self.run_fake("segv")
        err = self.assert_error(outcome, "crash", 1)
        if POSIX:
            self.assertEqual(outcome.signal, "SIGSEGV")
            self.assertIn("SIGSEGV", err["message"])

    def test_u06_cancel(self):
        cancel = threading.Event()
        timer = threading.Timer(1.0, cancel.set)
        timer.start()
        try:
            outcome, wall = self.run_fake("slow", "600", cancel=cancel, timeout_s=120)
        finally:
            timer.cancel()
        self.assert_error(outcome, "cancelled", 130)
        self.assertLess(wall, 8)
        worker = self.fake_pid(outcome)
        self.wait_for(lambda: not base.pid_alive(worker), 5, what="the cancelled worker to be gone")

    def test_u06_progress_every_five_seconds(self):
        seen: list[str] = []
        outcome, _wall = self.run_fake("slow", "6", progress=seen.append, timeout_s=60)
        self.assertEqual(outcome.status, "ok", outcome.error)
        self.assertGreaterEqual(len(seen), 1, "progress is called every 50 ticks while a job runs")

    def test_u06_output_cleaning(self):
        outcome, _wall = self.run_fake("noise")
        self.assertEqual(outcome.status, "ok", outcome.error)
        for text in (outcome.stdout, outcome.stderr):
            self.assertIsNone(re.search(r"\x1b|[\x00-\x08\x0b-\x1f\x7f]", text), repr(text[:200]))
            self.assertNotIn("Fontconfig", text)
            json.dumps(text, ensure_ascii=False).encode("utf-8")  # no lone surrogate survives
        self.assertIn("plain stdout line (fixture)", outcome.stdout)
        self.assertIn("red stdout (fixture)", outcome.stdout)
        self.assertIn("�", outcome.stdout, "invalid UTF-8 is decoded with replacement")
        self.assertIn("tab\tkept (fixture)", outcome.stderr)

    def test_u06_error_kinds_from_the_worker(self):
        for kind, code in (("sandbox_unavailable", 3), ("sandbox", 5), ("memory", 4), ("script", 1), ("kernel", 1),
                           ("result", 1), ("disk", 4)):
            outcome, _wall = self.run_fake("error", kind)
            self.assert_error(outcome, kind, code)

    def test_u06_hard_linked_output_is_not_promoted(self):
        outcome, _wall = self.run_fake("hardlink_out", op="run")
        self.assertEqual(outcome.status, "ok", outcome.error)
        with self.assertRaises(self.errors.CadError) as ctx:
            self.store.promote(outcome.out_dir, ["parts.json", "p0.brep"], self.tmp / "dest",
                               max_file_bytes=1 << 20, max_total_bytes=1 << 20)
        self.assertEqual(ctx.exception.kind, "integrity")
        self.assertFalse((self.tmp / "dest" / "p0.brep").exists(), "nothing is promoted")


def sys_linux() -> bool:
    import sys
    return sys.platform.startswith("linux")


if __name__ == "__main__":
    unittest.main()
