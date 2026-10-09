"""B2 unit tests: golden.compute (detection never raises, rig-lane fixtures, plan and plan_check rules, the
compute-used table). Every manifest, detection snapshot and record below is a labelled test input; nothing here is
presented as a measured result. Run: python -m unittest discover -s tests -p "test_g2_compute.py"."""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from aces_engineering import golden
from aces_engineering.golden import compute


class FakeRun:
    """A 15-line RunDirLike over a temp directory (test fixture)."""

    def __init__(self, root, run_id="20261009T000000Z-b2test", project="b2-test"):
        self.root, self.run_id, self.project = Path(root), run_id, project
        self.exp_id = f"golden-{project}-{run_id}"

    def path(self, *parts):
        return self.root.joinpath(*parts)

    def rel(self, p):
        return Path(p).resolve().relative_to(self.root.resolve()).as_posix()

    def case_dir(self, cid):
        d = self.root / cid
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_json(self, rel, obj):
        return golden.dump_json(self.root / rel, obj)

    def read_json(self, rel, default=None):
        return golden.read_json(self.root / rel, default)

    def write_text(self, rel, text):
        return golden.write_text(self.root / rel, text)

    def register_case(self, case):
        cases = [c for c in self.cases() if c["case_id"] != case["case_id"]] + [case]
        self.write_json("cases.json", cases)
        golden.dump_json(self.case_dir(case["case_id"]) / "case.json", case)
        return self.case_dir(case["case_id"])

    def cases(self):
        return list(self.read_json("cases.json", []) or [])


def eff_fixture(**derived_over) -> dict:
    """A resolved spec (test input) with the root-mount grid: 100 x 100 x 40 mm, 4 mm generator and solve."""
    d = {"gen_mm": 4.0, "fea_mm": 4.0, "field_h_mm": 2.0, "check_factor": 0.5, "n": 10, "seed": 7,
         "check_mode": "auto", "check_budget_s": 60.0, "tol": 0.10, "rounds": 3, "kappa": 1.0,
         "variables": {"volfrac": [0.13, 0.38], "rmin_mm": [6.0, 12.0]}, "fixed": {"penal": 3.0},
         "outputs": ["mass_kg", "vm_p99_MPa", "umax_mm", "compliance_Nmm", "vm_max_MPa"],
         "objective": {"output": "mass_kg", "sense": "min"},
         "constraints": [{"output": "vm_p99_MPa", "op": "<=", "limit": 60.0},
                         {"output": "umax_mm", "op": "<=", "limit": 0.035}],
         "check_levels": [{"level": 1, "h_mm": 2.0, "gpu_only": False}],
         "grid": {"origin_mm": [0, 0, 0], "envelope_mm": [100, 100, 40], "derived_from": "spec"}}
    d.update(derived_over)
    return {"name": "b2-test", "derived": d, "generator": {"skin": {"upsample": 2}}}


def detected(*, gpu_usable=False, free_mib=20000, warp_usable=False, usable_cpus=8, mem_gb=32.0) -> dict:
    """A detection snapshot (test input) in the shape detect() returns."""
    return {"schema": golden.SCHEMA["compute"], "host": "test-host", "platform": "test-platform", "python": "3.x",
            "cpu": {"logical": usable_cpus, "usable": usable_cpus, "model": "test cpu", "mem_total_gb": 64.0,
                    "mem_available_gb": mem_gb, "numpy": "test", "blas": [], "blas_threads_per_solve": 1},
            "kernel_selftest": {"passed": True, "wall_s": 0.01, "source": "gendesign.fea3d.self_test"},
            "gpu": {"cupy": "importable" if gpu_usable else "not available (ImportError)", "present": gpu_usable,
                    "usable": gpu_usable, "reason": "test gpu" if gpu_usable else "no CUDA device (test)",
                    "name": "Test GPU" if gpu_usable else None, "cuda_runtime": "12.9" if gpu_usable else None,
                    "mem_free_mib": free_mib if gpu_usable else None, "mem_total_mib": 24000 if gpu_usable else None,
                    "patch_test": {"passed": True, "tip_rel_err": 1e-14, "wall_s": 0.1} if gpu_usable else None},
            "warp": {"importable": warp_usable, "usable": warp_usable, "version": "1.0-test" if warp_usable else None,
                     "cuda_devices": 1 if warp_usable else None, "reason": "test"},
            "torch": {"installed": False, "use": "not used", "reason": compute.FNO_REASON},
            "physicsnemo": {"installed": False, "use": "not used", "reason": compute.FNO_REASON},
            "conforming_mesh": {"ccx": None, "code_aster": None, "use": "not used", "reason": compute.CONFORMING_REASON},
            "rig": {"manifest": {"path": None, "present": False}, "simlab_mcp": {"url": None, "health": None},
                    "lanes": [{"id": None, "kind": "sim", "gpus": None, "endpoint": None, "status": "no-manifest",
                               "use": "not used", "reason": "no fabric manifest: run aces fabric refresh; "
                               + compute.NOT_WIRED}]}}


class TestDetect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self.tmp.name) / "cfg"
        self.cfg.mkdir()
        etc = Path(self.tmp.name) / "etc"
        etc.mkdir()
        self.env = mock.patch.dict(os.environ, {"ACES_USER_CONFIG": str(self.cfg), "ACES_ETC": str(etc)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_detect_cpu_never_raises_and_has_every_section(self):
        det = compute.detect(device="cpu")
        for key in ("schema", "detected_utc", "host", "platform", "python", "cpu", "kernel_selftest", "gpu", "warp",
                    "torch", "physicsnemo", "conforming_mesh", "rig"):
            self.assertIn(key, det)
        self.assertEqual(det["gpu"]["reason"], "excluded by --device cpu")
        self.assertFalse(det["gpu"]["usable"])
        self.assertEqual(det["warp"]["reason"], "excluded by --device cpu")
        self.assertTrue(det["kernel_selftest"]["passed"], det["kernel_selftest"])
        self.assertEqual(det["torch"]["use"], "not used")
        json.dumps(golden.clean(det), allow_nan=False)

    def test_detect_survives_failing_probes(self):
        boom = mock.Mock(side_effect=RuntimeError("probe exploded (test)"))
        with mock.patch.object(compute, "_cpu", boom), mock.patch.object(compute, "_kernel_selftest", boom), \
                mock.patch.object(compute, "_rig", boom), mock.patch.object(compute.shutil, "which", boom):
            det = compute.detect(device="cpu")
        self.assertIn("error", det["cpu"])
        self.assertIn("error", det["kernel_selftest"])
        self.assertEqual(det["rig"]["lanes"][0]["use"], "not used")
        self.assertIn("not wired", det["rig"]["lanes"][0]["reason"])
        self.assertIn("lookup failed", det["conforming_mesh"]["reason"])

    def _manifest(self, *, health, endpoint="http://rig.test:8022/mcp", age_s=0.0):
        m = {"generated": "2026-10-09T00:00:00+00:00", "server": {"simlab_mcp": "http://rig.test:8022/mcp"},
             "health": {"simlab_mcp": health}, "mode": {"name": "sim-lab-4"},
             "lanes": [{"id": "sim/pool", "kind": "sim", "gpus": [0, 1, 2, 3], "endpoint": endpoint},
                       {"id": "model/x", "kind": "model", "gpus": [7], "endpoint": "http://rig.test:8018/v1"}]}
        p = self.cfg / "fabric.json"
        p.write_text(json.dumps(m), encoding="utf-8")
        if age_s:
            t = time.time() - age_s
            os.utime(p, (t, t))

    def test_rig_lane_fixtures_all_say_not_wired(self):
        cases = {
            "absent": (None, "no-manifest"),
            "available": (dict(health="http 404"), "available"),
            "unreachable": (dict(health="unreachable: timed out"), "unreachable"),
            "stale": (dict(health="http 404", age_s=3600), "stale-manifest"),
            "no-endpoint": (dict(health="http 404", endpoint=None), "no-endpoint"),
        }
        for label, (kw, status) in cases.items():
            with self.subTest(label):
                (self.cfg / "fabric.json").unlink(missing_ok=True)
                if kw is not None:
                    self._manifest(**kw)
                rig = compute.detect(device="cpu")["rig"]
                self.assertEqual(len(rig["lanes"]), 1)
                lane = rig["lanes"][0]
                self.assertEqual(lane["status"], status)
                self.assertEqual(lane["use"], "not used")
                self.assertIn("not wired yet", lane["reason"])
                if kw is None:
                    self.assertIn("no fabric manifest: run aces fabric refresh", lane["reason"])
                    self.assertFalse(rig["manifest"]["present"])
                else:
                    self.assertEqual(lane["id"], "sim/pool")
                    self.assertEqual(rig["manifest"]["mode"], "sim-lab-4")

    def test_lane_refusal_texts(self):
        msg = compute.lane_refusal("sim/pool")
        self.assertIn("rig submission through lane 'sim/pool' is not wired into the golden path yet (v0)", msg)
        self.assertIn("not in the fabric manifest", msg)
        self.assertIn("nothing was submitted", msg)
        self._manifest(health="http 404")
        self.assertIn("the lane is available in the fabric manifest", compute.lane_refusal("sim/pool"))
        self.assertIn("the lane is not in the fabric manifest", compute.lane_refusal("sim/other"))
        self._manifest(health="http 404", age_s=3600)
        self.assertIn("the lane is in a stale manifest", compute.lane_refusal("sim/pool"))


class TestPlan(unittest.TestCase):
    def test_plan_needs_the_grid(self):
        with self.assertRaises(ValueError):
            compute.plan(detected(), eff_fixture(grid=None))

    def test_cpu_only_host(self):
        p = compute.plan(detected(usable_cpus=4), eff_fixture())
        self.assertEqual(p["sweep"]["device"], "cpu")
        self.assertEqual(p["verify"]["device"], "cpu")
        self.assertEqual(p["sweep"]["jobs"], 4)  # min(usable 4, n 10, floor(32 / 0.6))
        self.assertEqual(p["parity"], [])
        self.assertFalse(p["rig"]["submit"])
        self.assertTrue(any("parity" in n for n in p["notes"]))

    def test_jobs_rule_and_override(self):
        self.assertEqual(compute.plan(detected(usable_cpus=24, mem_gb=1.3), eff_fixture())["sweep"]["jobs"], 2)
        self.assertEqual(compute.plan(detected(usable_cpus=24), eff_fixture(n=3))["sweep"]["jobs"], 3)
        self.assertEqual(compute.plan(detected(), eff_fixture(), jobs="5")["sweep"]["jobs"], 5)
        with self.assertRaises(golden.GoldenError) as cm:
            compute.plan(detected(), eff_fixture(), jobs="zero")
        self.assertEqual(cm.exception.code, golden.EXIT_USAGE)

    def test_gpu_small_grid_sweeps_on_cpu_with_parity(self):
        p = compute.plan(detected(gpu_usable=True, warp_usable=True), eff_fixture())
        self.assertEqual(p["sweep"]["device"], "cpu")  # 6,250 cells <= 40,000: the demo sweeps on the CPU everywhere
        self.assertEqual([(x["case_id"], x["device"], x["backend"]) for x in p["parity"]],
                         [("p001", "gpu", "adapter"), ("p002", "gpu", "warp")])

    def test_gpu_large_grid_sweeps_on_gpu(self):
        eff = eff_fixture(gen_mm=2.0, fea_mm=2.0, field_h_mm=1.0)  # 50,000 cells per solve
        p = compute.plan(detected(gpu_usable=True), eff)
        self.assertEqual(p["sweep"]["device"], "gpu")
        self.assertEqual(p["sweep"]["jobs"], 1)
        self.assertEqual(p["parity"], [{"case_id": "p001", "device": "cpu", "backend": "adapter"}])
        tight = compute.plan(detected(gpu_usable=True, free_mib=20), eff)  # 50,000 x 700 B > 20 MiB
        self.assertEqual(tight["sweep"]["device"], "cpu")
        self.assertIn("GPU has 20 MiB free; this solve needs about", tight["sweep"]["reason"])

    def test_device_gpu_without_gpu_is_usage(self):
        with self.assertRaises(golden.GoldenError) as cm:
            compute.plan(detected(gpu_usable=False), eff_fixture(), device="gpu")
        self.assertEqual(cm.exception.code, golden.EXIT_USAGE)
        self.assertIn("no usable CUDA GPU here", cm.exception.message)
        with self.assertRaises(golden.GoldenError) as cm:
            compute.plan(detected(gpu_usable=True, free_mib=1), eff_fixture(), device="gpu")
        self.assertIn("GPU has 1 MiB free; this solve needs about", cm.exception.message)

    def test_device_cpu_plans_no_gpu_work(self):
        p = compute.plan(detected(gpu_usable=True, warp_usable=True), eff_fixture(), device="cpu")
        self.assertEqual(p["sweep"]["device"], "cpu")
        self.assertEqual(p["parity"], [])


class TestPlanCheck(unittest.TestCase):
    def test_budget_arithmetic(self):
        levels = compute.plan_check(detected(), eff_fixture(), coarse_solve_s=0.5)
        l1, l2 = levels
        self.assertEqual((l1["level"], l1["h_mm"], l1["gating"]), (1, 2.0, True))
        self.assertAlmostEqual(l1["est_s"], 0.5 * (4.0 / 2.0) ** 4)  # 8 s
        self.assertTrue(l1["run"])
        self.assertEqual(l1["device"], "cpu")
        self.assertFalse(l2["run"])
        self.assertFalse(l2["gating"])
        self.assertEqual(l2["reason"], "GPU-only extra level: no usable GPU")
        slow = compute.plan_check(detected(), eff_fixture(), coarse_solve_s=5.0)[0]
        self.assertFalse(slow["run"])
        self.assertEqual(slow["reason"], "no usable GPU and the CPU estimate 80.0 s exceeds the 60 s budget")
        forced = compute.plan_check(detected(), eff_fixture(check_mode="force"), coarse_solve_s=5.0)[0]
        self.assertTrue(forced["run"])
        off = compute.plan_check(detected(), eff_fixture(check_mode="off"), coarse_solve_s=0.5)
        self.assertTrue(all(not lv["run"] and lv["reason"] == "check disabled (--check off)" for lv in off))

    def test_gpu_runs_both_levels(self):
        l1, l2 = compute.plan_check(detected(gpu_usable=True), eff_fixture(), coarse_solve_s=5.0)
        self.assertEqual((l1["device"], l1["run"]), ("gpu", True))
        self.assertEqual((l2["device"], l2["run"], l2["h_mm"]), ("gpu", True, 1.0))
        self.assertEqual(l2["reason"], "extra level because a GPU is present")
        cpu_only = compute.plan_check(detected(gpu_usable=True), eff_fixture(), coarse_solve_s=0.5, device="cpu")
        self.assertEqual(cpu_only[0]["device"], "cpu")
        self.assertFalse(cpu_only[1]["run"])
        # over budget with the GPU excluded by the flag: the reason names the flag, not an unusable GPU
        slow = compute.plan_check(detected(gpu_usable=True), eff_fixture(), coarse_solve_s=5.0, device="cpu")
        self.assertEqual(slow[0]["reason"], "--device cpu and the CPU estimate 80.0 s exceeds the 60 s budget")
        self.assertEqual(slow[1]["reason"], "GPU-only extra level: --device cpu")

    def test_cell_limit(self):
        big = eff_fixture(grid={"origin_mm": [0, 0, 0], "envelope_mm": [400, 400, 400], "derived_from": "spec"})
        l1 = compute.plan_check(detected(gpu_usable=True), big, coarse_solve_s=0.5)[0]
        self.assertFalse(l1["run"])
        self.assertIn("exceed the 2,000,000-cell limit", l1["reason"])


class TestSummaryAndLog(unittest.TestCase):
    def test_summary_lines(self):
        det = detected(gpu_usable=True, warp_usable=True)
        lines = compute.summary_lines(det, None)
        self.assertTrue(lines[0].startswith("cpu: test cpu"))
        self.assertFalse(any(ln.startswith("plan:") for ln in lines))
        with_plan = compute.summary_lines(det, compute.plan(det, eff_fixture()))
        self.assertTrue(any(ln.startswith("plan: sweep on cpu") for ln in with_plan))

    def test_compute_log_rows_rederive(self):
        with tempfile.TemporaryDirectory() as td:
            run = FakeRun(td)
            eff = eff_fixture()
            run.write_json("inputs/spec.effective.json", eff)
            det = detected()
            pl = compute.plan(det, eff, jobs=2)
            run.write_json("compute.json", {**det, "plan": pl})
            run.write_json("domain/mesh.json", {"elem_mm": 4.0, "cells": 6250, "solid_cells": 4890, "wall_s": 0.25,
                                                "volume_ratio_voxel_over_brep": 0.996, "watertight": True})
            for i in range(3):
                cid = f"d{i:03d}"
                run.register_case({"case_id": cid, "role": "sweep", "volfrac": 0.2 + 0.01 * i, "rmin_mm": 8.0})
                run.write_json(f"{cid}/status.json", {"status": "FINISHED", "diagnostics": {
                    "generator": {"device": "cpu/numpy", "wall_s": 1.0 + i, "elements": 6250, "h_mm": 4.0}}})
                run.write_json(f"{cid}/skin.json", {"status": "ok", "faces": 1000 + i, "wall_s": 0.5, "field_h_mm": 2.0})
                run.write_json(f"{cid}/solve.json", {"device": "cpu/numpy", "elements": 6250, "dof": 21000,
                                                     "wall_s": 0.4, "converged": 1.0})
            log = compute.ComputeLog().from_run(run, det, pl)
            rows = log.rows()
            steps = [r["step"] for r in rows]
            for step in ("kernel", "domain", "generate", "skin", "solve", "surrogate", "optimise", "verify", "check",
                         "parity", "rig", "warp-lattice", "fno", "second-discretisation"):
                self.assertIn(step, steps)
            gen = next(r for r in rows if r["step"] == "generate")
            self.assertEqual(gen["count"], 3)
            self.assertEqual(gen["wall_s_sum"], 6.0)
            self.assertIsNone(gen["wall_s"])  # concurrent in 2 workers: only the sum is honest
            self.assertEqual(gen["device"], "cpu/numpy x2")
            surr = next(r for r in rows if r["step"] == "surrogate")
            self.assertTrue(surr["note"].startswith("not run"))
            self.assertTrue(all(set(r) == set(compute.COLUMNS) for r in rows))
            log.write(run)
            self.assertEqual(compute.rederive_problems(run), [])
            doc = run.read_json("compute_used.json")
            doc["rows"][3]["wall_s_sum"] = 99.0  # tamper (test)
            run.write_json("compute_used.json", doc)
            self.assertTrue(compute.rederive_problems(run))
            self.assertIn("| step |", log.markdown())


if __name__ == "__main__":
    unittest.main()
