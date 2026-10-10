"""B2 end-to-end tests: the real golden path (`aces golden run --lane sim/pool`) in a child process against the in-process
fake simlab-mcp (tests/fake_simlab.py, a test fixture), section 22.3 of the rig build spec.

Every run solves a small fixture spec (a 40 x 20 x 16 mm box design space, test input) for real on this machine's CPU
(--device cpu); the fake executes each rig job with the real golden.rigside.run and ACES_GOLDEN_FORCE_DEVICE=cpu, so
every number comes from a real solve and every rig-side label says "(test fixture)". The scenarios run once in
setUpClass, four at a time, each with its own fake, scratch state (ACES_WORK, ACES_STATE, ACES_USER_CONFIG with the
fabric manifest, XDG_DATA_HOME) and poll constants patched short; the test methods assert on what they produced:

  ok (with a bearer token) -> rig-verified when gmsh and ccx are usable here, else verified-desktop with the conforming
  reason; the token appears in no output or file; validation pending or an image-id mismatch -> verified-desktop with
  the reason; never-starts with --rig-budget 2 -> sim_cancel and x101; infra-fail -> x101; design-fail -> the level
  fails, preview-grade, exit 0 (1 with --strict); corrupt-file -> exit 6; dark-after-submit with UNREACHABLE_S 2 ->
  rig-unreachable and x101; Ctrl-C (a KeyboardInterrupt raised in the child when x001 starts running) -> 130 with
  sim_cancel; --rig-sweep -> d000... on the fake and sweep.csv device rig; `aces golden verify` passes all 13 checks on
  the ok run, and an edited pulled fields.npz makes files and rig FAIL; file-vanishes (a listed file the rig will not
  hand over) and rig-exception (an exception inside the rig case) -> rig-error, x101 and exit 0, never 7, with the audit
  passing on both runs (review findings 2 and 3).

The rig is never contacted: the children import fake_simlab (the loopback-only network guard) and report anything it
blocked through a file that tearDown checks. Run: python -m unittest discover -s tests -p "test_g4_pipeline_rig.py".
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import fake_simlab

TESTS = Path(__file__).resolve().parent
ENG = TESTS.parent
ACES_BASE_SRC = TESTS.parents[1] / "aces-base"
TOKEN = "fixture-token-7d1c0b9e"  # a test input; it must never appear in any output or record
PATCH = {"POLL_S": 0.05, "BEACON_EVERY_S": 0.2}
BUILDER = ("from build123d import Align, Box\n\n\n"
           "def box(x=40.0, y=20.0, z=16.0):\n"
           "    return Box(x, y, z, align=(Align.MIN, Align.MIN, Align.MIN))\n")
SPEC = {  # test input: a 40 x 20 x 16 mm box design space fixed at x = 0 and loaded at x = 40
    "schema": "aces-golden-spec/1", "name": "rig-fixture",
    "description": "a 40 x 20 x 16 mm box design space fixed at x = 0 and loaded at x = 40 (test input)",
    "hypothesis": "test hypothesis (test input)",
    "domain": {"source": "build123d", "build123d": {"ref": "builder.py:box"}, "origin_mm": [0, 0, 0],
               "envelope_mm": [40, 20, 16]},
    "supports": [{"name": "wall", "box": [0, 0, 0, 0, 20, 16]}],
    "loads": [{"name": "tip", "box": [40, 4, 4, 40, 16, 12], "force": [0, 0, -500], "axis": "x"}],
    "material": {"name": "test", "E_MPa": 70000, "nu": 0.33, "rho_kg_mm3": 2.7e-6, "allowable_vm_MPa": 100,
                 "source": "test input"},
    "constraints": [{"output": "umax_mm", "op": "<=", "limit": 0.06}],
    "generator": {"variables": {"volfrac": {"lo": 0.6, "hi": 0.8}, "rmin_mm": {"lo": 6.0, "hi": 8.0}}, "iterations": 6},
    "resolution": {"gen_mm": 4.0, "fea_mm": 4.0, "check_factor": 0.5},
    "sweep": {"n": 8, "seed": 1},
}

CHILD = r"""
import json, os, sys
sys.path.insert(0, os.environ["G4_TESTS"])
import fake_simlab  # the loopback-only network guard
from aces_engineering.golden import rig
for k, v in json.loads(os.environ.get("G4_RIG_PATCH") or "{}").items():
    setattr(rig, k, v)
stop = os.environ.get("G4_INTERRUPT")
if stop:  # Ctrl-C, emulated: a KeyboardInterrupt when that case starts running on the fake
    real = rig.RigSession._state_line
    def _line(self, e, rc, gid, state, job):
        real(self, e, rc, gid, state, job)
        if gid == stop and state == "running":
            raise KeyboardInterrupt
    rig.RigSession._state_line = _line
from aces_engineering import golden_cli
try:
    code = golden_cli.main(json.loads(os.environ["G4_ARGV"]))
finally:
    with open(os.environ["G4_BLOCKED_FILE"], "w", encoding="utf-8") as fh:
        json.dump(fake_simlab.BLOCKED, fh)
raise SystemExit(code)
"""


def aces_command() -> list[str] | None:
    if (ACES_BASE_SRC / "aces" / "cli.py").is_file():
        return [sys.executable, "-m", "aces.cli"]
    found = shutil.which("aces")
    return [found] if found else None


class Scenario:
    """One `aces golden run` against its own fake (test fixture)."""

    def __init__(self, base: Path, name: str, *, cases=None, solvers=None, extra=(), patch=None, interrupt=None,
                 token=False):
        self.name, self.cases, self.extra = name, dict(cases or {}), list(extra)
        self.patch, self.interrupt, self.token = dict(patch or PATCH), interrupt, token
        self.solvers = solvers
        self.state = base / name
        for d in ("work", "state", "cfg", "data", "spec"):
            (self.state / d).mkdir(parents=True, exist_ok=True)
        self.fake = fake_simlab.FakeSimlab(self.state / "rigrepo", scenarios=self.cases, solvers=solvers,
                                           tokens={"aces-test": TOKEN} if token else None)
        man = {"generated": "fixture", "server": {"simlab_mcp": self.fake.url}, "health": {"simlab_mcp": "http 404"},
               "mode": {"name": "sim-lab-4"},
               "lanes": [{"id": "sim/pool", "kind": "sim", "gpus": [0, 1, 2, 3], "endpoint": self.fake.url}]}
        (self.state / "cfg" / "fabric.json").write_text(json.dumps(man), encoding="utf-8")
        (self.state / "spec" / "spec.json").write_text(json.dumps(SPEC, indent=1), encoding="utf-8")
        (self.state / "spec" / "builder.py").write_text(BUILDER, encoding="utf-8")
        self.rc: int | None = None
        self.out = self.err = ""
        self.doc: dict = {}
        self.wall_s = 0.0

    def env(self, argv: list[str]) -> dict:
        env = {k: v for k, v in os.environ.items()
               if k not in ("ACES_SIMLAB_TOKEN", "SIMLAB_MCP_TOKEN", "SIMLAB_EVIDENCE_ROOT", "ACES_SIMLAB_REGISTRY",
                            "ACES_GOLDEN_FORCE_DEVICE", "ACES_GOLDEN_DEBUG")}
        st = self.state
        env.update({"PYTHONPATH": os.pathsep.join([str(ENG / "lib"), str(ENG / "vendor"), str(ACES_BASE_SRC)]),
                    "G4_TESTS": str(TESTS), "ACES_WORK": str(st / "work"), "ACES_STATE": str(st / "state"),
                    "ACES_USER_CONFIG": str(st / "cfg"), "XDG_DATA_HOME": str(st / "data"),
                    "SIMLAB_EVIDENCE_ROOT": str(st / "data" / "evidence"), "ACES_ETC": str(ACES_BASE_SRC / "etc"),
                    "MPLBACKEND": "Agg", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
                    "G4_ARGV": json.dumps(argv), "G4_RIG_PATCH": json.dumps(self.patch),
                    "G4_BLOCKED_FILE": str(st / "blocked.json"), "G4_INTERRUPT": self.interrupt or ""})
        cmd = aces_command()
        if cmd:
            env["ACES_CMD"] = json.dumps(cmd)
        if self.token:
            env["ACES_SIMLAB_TOKEN"] = TOKEN
        return env

    def child(self, argv: list[str], timeout: float = 900) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-c", CHILD], env=self.env(argv), capture_output=True, timeout=timeout,
                              cwd=str(self.state))

    def run(self) -> "Scenario":
        argv = ["run", "--spec", str(self.state / "spec" / "spec.json"), "--project", "rig-fixture", "--new", "--lane",
                "sim/pool", "--json", "--progress", "lines", "--device", "cpu", *self.extra]
        t0 = time.monotonic()
        try:
            cp = self.child(argv)
            self.rc, self.out = cp.returncode, cp.stdout.decode("utf-8", "replace")
            self.err = cp.stderr.decode("utf-8", "replace")
            try:
                self.doc = json.loads(self.out)
            except ValueError:
                self.doc = {}
        finally:
            self.wall_s = time.monotonic() - t0
            self.fake.stop()
        return self

    @property
    def run_dir(self) -> Path:
        return Path(self.doc["run_dir"])

    def read(self, rel: str):
        return json.loads((self.run_dir / rel).read_text(encoding="utf-8"))

    def validation(self) -> dict:
        return self.read("validation.json")

    def level(self, n: int) -> dict:
        return next(lv for lv in self.validation()["independent_check"]["levels"] if lv["level"] == n)

    def blocked(self) -> list:
        p = self.state / "blocked.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []

    def label(self) -> str:
        return f"{self.name}: rc {self.rc}\n{self.err[-3000:]}"


def _conforming_usable() -> bool:
    from aces_engineering.golden import compute

    return bool(compute.conforming_available().get("usable"))


@unittest.skipUnless(aces_command(), "no aces here: neither src/aces-base nor an installed aces-base")
class PipelineOnTheFakeRig(unittest.TestCase):
    runs: dict[str, Scenario] = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="g4-pipe-"))
        other = fake_simlab.golden_solvers(image_id="sha256:0123456789abcdef0123 (test fixture)")
        plans = [
            Scenario(cls.tmp, "ok", token=True),
            Scenario(cls.tmp, "pending", solvers=fake_simlab.golden_solvers(validation="pending")),
            Scenario(cls.tmp, "mismatch", solvers=other),
            Scenario(cls.tmp, "never", cases={"x001": "never-starts"}, extra=["--rig-budget", "2"]),
            Scenario(cls.tmp, "infra", cases={"x001": "infra-fail"}),
            Scenario(cls.tmp, "dfail", cases={"x001": "design-fail"}),
            Scenario(cls.tmp, "dfail-strict", cases={"x001": "design-fail"}, extra=["--strict"]),
            Scenario(cls.tmp, "corrupt", cases={"x001": "corrupt-file"}),
            Scenario(cls.tmp, "dark", cases={"x001": "dark-after-submit"}, patch=dict(PATCH, UNREACHABLE_S=2.0)),
            Scenario(cls.tmp, "ctrlc", cases={"x001": "hang"}, interrupt="x001"),
            Scenario(cls.tmp, "sweep", extra=["--rig-sweep"]),
            Scenario(cls.tmp, "vanish", cases={"x001": "file-vanishes"}),
            Scenario(cls.tmp, "rigexc", cases={"x001": "rig-exception"}),
        ]
        with ThreadPoolExecutor(max_workers=4) as ex:
            done = list(ex.map(lambda s: s.run(), plans))
        cls.runs = {s.name: s for s in done}

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def tearDown(self) -> None:
        self.assertEqual(fake_simlab.BLOCKED, [])
        for s in self.runs.values():
            self.assertEqual(s.blocked(), [], s.name)

    def cli(self, s: Scenario, argv: list[str]) -> tuple[int, dict, str]:
        """`aces golden <argv> --json` in the scenario's state: (exit code, the one JSON document, stderr)."""
        cp = s.child(argv, timeout=600)
        return cp.returncode, json.loads(cp.stdout.decode("utf-8")), cp.stderr.decode("utf-8", "replace")

    # ---- the grades ---------------------------------------------------------------------------------------------
    def test_ok_with_a_token(self):
        s = self.runs["ok"]
        self.assertEqual(s.rc, 0, s.label())
        v = s.validation()
        lv1 = s.level(1)
        self.assertEqual((lv1["case_id"], lv1["host"], lv1["device"], lv1["passed"]), ("x001", "rig", "rig", True))
        self.assertEqual(lv1["rig"]["solver_validation"], "pass")
        self.assertEqual(lv1["rig"]["image_id"], "sha256:fake-image")
        self.assertEqual(lv1["rig"]["gpu"], fake_simlab.FAKE_JOB_GPU)
        self.assertTrue(v["rig"]["code_identity"]["ok"], v["rig"]["code_identity"])
        self.assertIsNone(v["rig"]["integrity_error"])
        self.assertEqual(v["rig"]["token_source"], "ACES_SIMLAB_TOKEN")
        self.assertEqual(v["rig"]["principal"], "aces-test")
        if _conforming_usable():
            self.assertEqual(v["grade"], "rig-verified", v["grade_reasons"])
        else:
            self.assertEqual(v["grade"], "verified-desktop")
            self.assertTrue(v["grade_reasons"][1].startswith("not rig-verified: the conforming check "),
                            v["grade_reasons"])
        self.assertEqual(s.doc["rig"]["lane"], "sim/pool")
        self.assertEqual([e["stage"] for e in s.doc["rig"]["experiments"]], ["check", "conf"])
        self.assertEqual(s.doc["independent_check"]["host"], "rig")
        # every pulled file landed under the case, and the rig's own records under <case>/rig/
        rj = s.read("x001/rig.json")
        self.assertEqual(rj["state"], "result")
        self.assertTrue(all((s.run_dir / f["local"]).is_file() for f in rj["files"]))
        for name in ("outcome.json", "files.json", "results.json", "evidence.json", "status.json", "case.json"):
            self.assertTrue((s.run_dir / "x001" / "rig" / name).is_file(), name)
        sess = s.read("rig/session.json")
        self.assertEqual(sess["schema"], "aces-golden-rig-session/1")
        self.assertEqual(sess["experiments"][0]["cases"], {"c000": "x001", "c001": "x002"})
        self.assertTrue(all("bundle_sha256" in e["rig_evidence"] for e in sess["experiments"]))
        self.assertEqual(len(s.fake.tool_calls("sim_evidence")), 2)
        self.assertTrue(all(c[2] for c in s.fake.calls if c[0] != "notifications/initialized"))  # bearer on every call

    def test_compute_used_rows(self):
        from aces_engineering import golden_cli

        s = self.runs["ok"]
        rows = s.read("compute_used.json")["rows"]
        check = next(r for r in rows if r["step"] == "check" and r["computation"].endswith("level 1"))
        self.assertEqual(check["device"], f"rig gpu {fake_simlab.FAKE_JOB_GPU} (cpu/numpy)")  # the fake forced the CPU
        self.assertEqual(check["solver"], "golden-check on the rig (step-structures stage_solve, pcg FP64)")
        self.assertEqual(check["source"], "x001/solve.json, x001/rig.json")
        self.assertTrue(check["note"].startswith("job golden-"), check["note"])
        exps = [r for r in rows if r["step"] == "rig"]
        self.assertEqual([r["computation"].split(":")[0] for r in exps],
                         [f"experiment {e['id']}" for e in s.doc["rig"]["experiments"]])
        self.assertEqual(exps[0]["device"], f"rig gpu {fake_simlab.FAKE_JOB_GPU}")
        self.assertEqual(exps[0]["source"], "rig/session.json")
        self.assertIn("rig: cpu/numpy x1 (numpy)", golden_cli.devices_used(rows))

    def test_the_token_appears_nowhere(self):
        s = self.runs["ok"]
        self.assertNotIn(TOKEN, s.out)
        self.assertNotIn(TOKEN, s.err)
        hits = []
        for p in s.state.rglob("*"):
            if p.is_file() and p.name != "fabric.json":
                try:
                    if TOKEN.encode() in p.read_bytes():
                        hits.append(str(p))
                except OSError:
                    pass
        self.assertEqual(hits, [])
        self.assertTrue((s.run_dir / "log.txt").is_file() and (s.run_dir / "events.jsonl").is_file())

    def test_validation_pending_or_another_image(self):
        for name, reason in (("pending", "golden-check validation is pending in the rig registry"),
                             ("mismatch", "golden-check was validated on image 0123456789ab, this run used fake-image")):
            with self.subTest(name):
                s = self.runs[name]
                self.assertEqual(s.rc, 0, s.label())
                v = s.validation()
                self.assertEqual(v["grade"], "verified-desktop")
                self.assertEqual(v["grade_reasons"][1], f"not rig-verified: {reason}")

    def test_never_starts_spends_the_budget_cancels_and_falls_back(self):
        s = self.runs["never"]
        self.assertEqual(s.rc, 0, s.label())
        exp = s.read("rig/session.json")["experiments"][0]["id"]
        self.assertIn(exp, s.fake.cancel_calls)
        lv1 = s.level(1)
        self.assertEqual((lv1["case_id"], lv1["host"]), ("x101", "desktop"))
        self.assertEqual((lv1["rig_attempt"]["case_id"], lv1["rig_attempt"]["design_status"]), ("x001", "rig-timeout"))
        self.assertEqual(s.read("x101/case.json")["fallback_from"], "x001")
        self.assertEqual(s.read("x001/status.json")["design_status"], "rig-timeout")
        self.assertIn("not rig-verified: the level-1 check ran on this desktop (x101: the rig did not deliver x001: "
                      "rig-timeout)", s.validation()["grade_reasons"])

    def test_infra_fail_falls_back(self):
        s = self.runs["infra"]
        self.assertEqual(s.rc, 0, s.label())
        lv1 = s.level(1)
        self.assertEqual((lv1["case_id"], lv1["rig_attempt"]["design_status"]), ("x101", "rig-error"))
        err = s.read("x001/status.json")["error"]
        self.assertIn("failed without an outcome.json", err)
        self.assertIn("docker: Error response from daemon: No such image (fixture)", err)  # the worker.log tail

    def test_design_fail_fails_the_level_without_fallback(self):
        s, strict = self.runs["dfail"], self.runs["dfail-strict"]
        self.assertEqual(s.rc, 0, s.label())
        self.assertEqual(strict.rc, 1, strict.label())
        lv1 = s.level(1)
        self.assertEqual((lv1["case_id"], lv1["host"], lv1["ran"], lv1["passed"]), ("x001", "rig", True, False))
        self.assertIsNone(lv1["rig_attempt"])
        self.assertFalse((s.run_dir / "x101").exists())
        self.assertEqual(s.validation()["grade"], "preview-grade")
        self.assertIn("fixture: forced unconverged", s.validation()["grade_reasons"][0])
        self.assertTrue(strict.doc["message"].startswith("strict: grade preview-grade ("), strict.doc["message"])

    def test_corrupt_file_is_an_integrity_error(self):
        s = self.runs["corrupt"]
        self.assertEqual(s.rc, 6, s.label())
        v = s.validation()
        self.assertIn("c000/golden/x001/mask.npy", v["rig"]["integrity_error"])
        self.assertTrue(s.doc["message"].startswith("rig integrity error: "), s.doc["message"])
        self.assertEqual(s.read("x001/status.json")["design_status"], "rig-integrity")
        self.assertFalse((s.run_dir / "x001" / "mask.npy").exists())  # nothing of the case was written
        self.assertEqual(s.level(1)["case_id"], "x101")

    def assert_falls_back_to_x101(self, s: Scenario, reason_starts: str) -> None:
        self.assertEqual(s.rc, 0, s.label())  # never exit 7: a case the rig did not deliver is not an internal error
        lv1 = s.level(1)
        self.assertEqual((lv1["case_id"], lv1["host"]), ("x101", "desktop"))
        self.assertEqual((lv1["rig_attempt"]["case_id"], lv1["rig_attempt"]["design_status"]), ("x001", "rig-error"))
        self.assertTrue(lv1["rig_attempt"]["reason"].startswith(reason_starts), lv1["rig_attempt"]["reason"])
        self.assertEqual(s.read("x101/case.json")["fallback_from"], "x001")
        self.assertEqual((s.read("x001/rig.json")["state"], s.read("x001/status.json")["design_status"]),
                         ("not-run", "rig-error"))
        v = s.validation()
        self.assertEqual(v["grade"], "verified-desktop", v["grade_reasons"])
        self.assertIn("not rig-verified: the level-1 check ran on this desktop (x101: the rig did not deliver x001: "
                      "rig-error)", v["grade_reasons"])
        rc, doc, _err = self.cli(s, ["verify", str(s.run_dir), "--json"])  # the audit reads the not-delivered records
        self.assertEqual(rc, 0, [c for c in doc["checks"] if c["ok"] is False])

    def test_a_file_the_rig_will_not_hand_over_falls_back(self):
        """Review finding 2: sim_get_file refuses a file that files.json lists (file-vanishes)."""
        self.assert_falls_back_to_x101(self.runs["vanish"], "c000/golden/x001/fields.npz could not be pulled: ")

    def test_an_internal_error_on_the_rig_falls_back(self):
        """Review finding 3: an exception inside the rig case (rig-exception) is not delivered, not a failed check."""
        self.assert_falls_back_to_x101(self.runs["rigexc"], "the rig case x001 stopped on an internal error: "
                                                            f"RuntimeError: {fake_simlab.RIG_EXCEPTION}")

    def test_lost_contact_is_rig_unreachable_with_the_fallback(self):
        s = self.runs["dark"]
        self.assertEqual(s.rc, 0, s.label())
        lv1 = s.level(1)
        self.assertEqual((lv1["case_id"], lv1["rig_attempt"]["design_status"]), ("x101", "rig-unreachable"))
        self.assertIn("lost contact with simlab-mcp at", s.read("x001/status.json")["error"])
        self.assertIn("jobs may still run on the rig: experiment golden-", s.read("x001/status.json")["error"])

    def test_ctrl_c_cancels_and_exits_130(self):
        s = self.runs["ctrlc"]
        self.assertEqual(s.rc, 130, s.label())
        self.assertEqual((s.doc["outcome"], s.doc["exit_code"]), ("interrupted", 130))
        sess = s.read("rig/session.json")
        ids = [e["id"] for e in sess["experiments"]]
        self.assertEqual(sorted(s.fake.cancel_calls), sorted(ids))
        self.assertTrue(all(c["reason"] == "interrupted" for c in sess["cancelled"]))
        self.assertIsNotNone(sess["finished_utc"])

    def test_rig_sweep(self):
        s = self.runs["sweep"]
        self.assertEqual(s.rc, 0, s.label())
        sess = s.read("rig/session.json")
        sw = sess["experiments"][0]
        self.assertEqual((sw["stage"], sw["solver"]), ("sweep", "golden-case"))
        self.assertEqual(sorted(sw["cases"].values()), [f"d{i:03d}" for i in range(8)])
        with open(s.run_dir / "sweep.csv", encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual({r["device"] for r in rows}, {"rig"})
        self.assertTrue(all(r["status"] == "FINISHED" for r in rows))
        d0 = s.read("d000/case.json")
        self.assertEqual((d0["host"], d0["lane"], d0["fea_solver"], d0["device"]), ("rig", "sim/pool", "pcg", "rig"))
        self.assertEqual(s.read("v001/case.json")["device"], "cpu")  # verification stays on this desktop

    # ---- the audit ----------------------------------------------------------------------------------------------
    def test_verify_passes_all_13_checks_and_catches_an_edited_pulled_file(self):
        s = self.runs["ok"]
        rc, doc, err = self.cli(s, ["verify", str(s.run_dir), "--json"])
        names = [c["name"] for c in doc["checks"]]
        self.assertEqual(names, ["files", "bundle", "simlab-ledger", "aces-ledger", "sweep", "surrogate", "verdict",
                                 "report", "compute", "rig", "conforming", "manifest", "resolve"])
        self.assertEqual(rc, 0, [c for c in doc["checks"] if c["ok"] is False])
        rig_check = next(c for c in doc["checks"] if c["name"] == "rig")
        self.assertTrue(rig_check["ok"], rig_check)
        self.assertIn("device forced to cpu", rig_check["detail"])
        # a copy of the run with one byte of a pulled field file changed
        copy = self.tmp / "tampered" / s.run_dir.name
        shutil.copytree(s.run_dir, copy)
        f = copy / "x001" / "fields.npz"
        data = bytearray(f.read_bytes())
        data[-1] ^= 0x01
        f.write_bytes(bytes(data))
        rc, doc, err = self.cli(s, ["verify", str(copy), "--json"])
        res = {c["name"]: c for c in doc["checks"]}
        self.assertEqual(rc, 1)
        self.assertFalse(res["files"]["ok"])
        self.assertFalse(res["rig"]["ok"])
        self.assertIn("R3 x001/fields.npz", res["rig"]["detail"])


class DevicesUsed(unittest.TestCase):
    """golden_cli.devices_used aggregates the rig tokens (section 14.6); the rows are test inputs."""

    def tearDown(self) -> None:
        self.assertEqual(fake_simlab.BLOCKED, [])

    def test_rig_tokens(self):
        from aces_engineering import golden_cli

        rows = [{"step": "solve", "device": "cpu/numpy x8", "solver": "stage_solve", "count": 8, "grade": "preview-grade"},
                {"step": "check", "device": "rig gpu 2 (NVIDIA CMP 170HX)", "count": 1, "grade": "x",
                 "solver": "golden-check on the rig (step-structures stage_solve, mg FP64)"},
                {"step": "check", "device": "rig gpu 3 (NVIDIA CMP 170HX)", "count": 1, "grade": "x",
                 "solver": "golden-check on the rig (step-structures stage_solve, pcg FP64)"},
                {"step": "rig", "device": "rig gpu 2,3", "count": 2, "grade": "used", "solver": "golden-check (img)"},
                {"step": "rig", "device": "rig cpu", "count": 1, "grade": "used", "solver": "ccx-check (img)"},
                {"step": "second-discretisation", "device": "rig cpu x16", "count": 1, "grade": "x", "solver": "gmsh"},
                {"step": "rig", "device": "rig", "count": 0, "grade": "not used", "solver": "simlab-mcp"}]
        self.assertEqual(golden_cli.devices_used(rows),
                         "cpu/numpy x8, rig: NVIDIA CMP 170HX x2 (cupy, mg), rig cpu (ccx x16)")

    def test_rows_that_ran_on_several_rig_gpus(self):
        """The device strings of the compute-used rows of run 20261010T084243Z-b3ac99 (the demo with --rig-sweep on
        the ACES Server, 2026-10-10), plus two parity rows modelled on that run: a sweep row names three rig GPUs and
        the CPU in one string, one row records a GPU without its name. Before this test the greedy regex turned
        `rig gpu 0 (NVIDIA CMP 170HX), rig gpu 2 (NVIDIA CMP 170HX)` into a GPU named `NVIDIA CMP 170HX), rig gpu 2
        (NVIDIA CMP 170HX`, and the string with the CPU at its end was dropped."""
        from aces_engineering import golden_cli

        cmp = "NVIDIA CMP 170HX"
        rows = [{"device": "cpu; gpu", "solver": "gendesign.fea3d.self_test; GPU patch test", "count": 2, "grade": "-"},
                {"device": f"rig gpu 0 ({cmp}), rig gpu 2 ({cmp}), rig gpu 3 ({cmp}), cpu/numpy",
                 "solver": "gendesign.topopt3d.run_case (OC, FFT filter)", "count": 11, "grade": "-"},
                {"device": f"rig gpu 0 ({cmp}), rig gpu 2 ({cmp}), rig gpu 3 (None), cpu",
                 "solver": "golden.organic (EDT SDF, smooth unions, Taubin)", "count": 11, "grade": "-"},
                {"device": f"rig gpu 0 ({cmp}), rig gpu 2 ({cmp})", "solver": "step-structures stage_solve, Jacobi-PCG FP64",
                 "count": 9, "grade": "preview-grade"},
                {"device": f"rig gpu 0 ({cmp})", "count": 1, "grade": "independent check -> verified-desktop",
                 "solver": "golden-check on the rig (step-structures stage_solve, pcg FP64)"},
                {"device": f"rig gpu 2 ({cmp})", "count": 1, "grade": "informational (agrees)",
                 "solver": "golden-check on the rig (step-structures stage_solve, mg FP64)"},
                {"device": "NVIDIA RTX PRO 4000 Blackwell SFF Edition", "solver": "gendesign.fea3d (cupy)", "count": 1,
                 "grade": "max rel diff 3.1e-12"},
                {"device": "NVIDIA RTX PRO 4000 Blackwell SFF Edition", "solver": "gendesign.fea3d (warp)", "count": 1,
                 "grade": "max rel diff 9.9e-12"},
                {"device": "rig gpu 0,2,3", "solver": "golden-case (aces-golden:0.1)", "count": 9, "grade": "used"},
                {"device": "rig gpu 0,2", "solver": "golden-check (aces-golden:0.1)", "count": 2, "grade": "used"},
                {"device": "rig cpu", "solver": "ccx-check (aces-golden:0.1)", "count": 1, "grade": "used"},
                {"device": "rig cpu x16", "solver": "gmsh 4.15.2 + CalculiX ccx 2.23", "count": 1,
                 "grade": "second discretisation -> agrees"}]
        self.assertEqual(golden_cli.devices_used(rows),
                         "cpu/numpy, NVIDIA RTX PRO 4000 Blackwell SFF Edition (cupy, warp), "
                         "rig: NVIDIA CMP 170HX x3 (cupy, mg), rig cpu (ccx x16)")
        # a GPU that no row names is reported as such, never under an invented name
        rows2 = [{"device": "rig gpu 5 (None)", "solver": "step-structures stage_solve, Jacobi-PCG FP64", "count": 1,
                  "grade": "preview-grade"}]
        self.assertEqual(golden_cli.devices_used(rows2), "rig gpu 5 (name not recorded)")


if __name__ == "__main__":
    unittest.main()
