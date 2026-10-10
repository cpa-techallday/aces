"""B2 unit tests: golden.verify (rounds, compare, independent check, device parity, grade, finalise) against a
deterministic fake case runner. The fake runner's "solver" is an analytic stand-in (test input, smooth and positive,
with active constraints), never presented as FEA; it writes the case records B1's runner writes (section 8.2) so the
real surrogate, optimiser and record readers run on them. Run: python -m unittest discover -s tests -p "test_g2_verify.py".
"""
from __future__ import annotations

import datetime as dt
import math
import sys
import tempfile
import types
import unittest
from unittest import mock

import test_g2_compute as tc

from aces_engineering import golden
from aces_engineering.golden import compute, optimise, verify

CASE_MODULE = "aces_engineering.golden.case"
VARS = {"volfrac": [0.13, 0.38], "rmin_mm": [6.0, 12.0]}
ALL_OUTPUTS = ["mass_kg", "vm_p99_MPa", "umax_mm", "compliance_Nmm", "vm_max_MPa"]
FAST_OUTPUTS = ["mass_kg", "vm_p99_MPa", "umax_mm"]  # objective + constraints: a 3-output GP fits in ~1 s, 5 in ~2.5 s


def full_eff(outputs=None, **derived_over) -> dict:
    """A resolved root-mount-like spec (test input) with every block golden.verify reads. Unit tests default to the
    objective and the constrained outputs only (fast fits); pass outputs=ALL_OUTPUTS for the full demo list."""
    derived_over.setdefault("outputs", list(outputs or FAST_OUTPUTS))
    eff = tc.eff_fixture(**derived_over)
    eff.update({
        "schema": "aces-golden-spec/1", "description": "B2 unit-test spec (test input, not a design case)",
        "units": "mm-N-MPa-kg", "hypothesis": "test hypothesis (test input)",
        "domain": {"source": "build123d", "keep_in": [], "keep_out": []},
        "supports": [{"name": "foot", "box": [44, 8, 0, 56, 20, 0], "dofs": [0, 1, 2]}],
        "loads": [{"name": "cup", "box": [40, 40, 40, 60, 60, 40], "force": [0, 0, -30000], "axis": "z", "case": "down"}],
        "material": {"name": "test material", "E_MPa": 70000.0, "nu": 0.33, "rho_kg_mm3": 2.67e-6,
                     "allowable_vm_MPa": 60.0, "source": "test input"},
        "objective": {"output": "mass_kg", "sense": "min"},
        "constraints": [{"output": "vm_p99_MPa", "op": "<=", "limit": "allowable"},
                        {"output": "umax_mm", "op": "<=", "limit": 0.035}],
        "generator": {"kind": "simp-organic", "variables": {k: {"lo": v[0], "hi": v[1]} for k, v in VARS.items()},
                      "fixed": {"penal": 3.0}, "iterations": 25, "change_tol": 0.01, "move": 0.2, "solver_tol": 1e-4,
                      "skin": {"upsample": 2, "sigma_vox": 1.0, "blend_mm": 5.0, "clip_blend_mm": 2.0,
                               "taubin_iters": 5, "iso": 0.5}, "infill": None},
        "resolution": {"gen_mm": 4.0, "fea_mm": 4.0, "check_factor": 0.5, "stress_exclude_mm": 6.0},
        "sweep": {"n": 10, "seed": 7},
        "surrogate": {"kind": "gp", "folds": 5, "min_points": 8, "r2_flag": 0.9, "log_outputs": "auto-positive"},
        "optimise": {"method": "auto", "candidates": 4096, "trials": 400, "kappa": 1.0, "cv_margin": True, "rounds": 3},
        "verify": {"tol": 0.10, "check_objective": True, "check": "auto", "check_budget_s": 60.0},
        "solver": {"dtype": "float64", "tol": 1e-8, "maxiter": 20000},
    })
    eff["derived"].setdefault("domain_ref", "aces_engineering.golden.demo:root_mount_domain")
    eff["derived"].setdefault("overrides", {})
    eff["derived"].setdefault("stress_exclude_mm", 6.0)
    return eff


def physics(v: float, r: float) -> dict:
    """Analytic stand-in for a solve (TEST INPUT, not FEA): mass linear in volfrac; umax and vm_p99 power laws."""
    umax = 0.0075 / v ** 0.9 * (1.0 + 0.01 * (r - 9.0))
    vm99 = 9.0 / v ** 0.95 * (1.0 - 0.01 * (r - 9.0))
    vol = 0.4 * v / 2.67e-6
    return {"mass_kg": 0.4 * v, "mass_voxel_kg": 0.41 * v, "mesh_volume_mm3": vol, "voxel_volume_mm3": 1.025 * vol,
            "solid_fraction": v, "compliance_Nmm": 1.0e4 * umax, "umax_mm": umax, "vm_max_MPa": 1.6 * vm99,
            "vm_p99_MPa": vm99, "vm_p95_MPa": 0.8 * vm99}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class FakeCaseModule(types.ModuleType):
    """Stands in for B1's golden.case: run_cases registers each case, 'solves' it with `physics` and writes
    status.json, results.json (FINISHED only), solve.json, skin.json and (optionally) evidence.json."""

    def __init__(self, *, fail_roles=(), fail_ids=(), check_bias=0.03, parity_bias=0.0, write_evidence=False):
        super().__init__(CASE_MODULE)
        self.fail_roles, self.fail_ids = set(fail_roles), set(fail_ids)
        self.check_bias, self.parity_bias = check_bias, parity_bias
        self.write_evidence = write_evidence
        self.calls: list[tuple[str, str]] = []

    def run_cases(self, run, eff, domain, experiment, cases, *, pool, progress, kind, bar=None):
        outs = []
        for case in cases:
            run.register_case(case)
            outs.append(self._solve(run, eff, case, kind, experiment))
            self.calls.append((case["case_id"], kind))
        return outs

    def _params(self, run, case):
        if case["role"] in ("sweep", "verify"):
            return {k: case[k] for k in VARS}
        src = run.read_json(f"{case['source_case']}/case.json")
        return {k: src[k] for k in VARS}

    def _solve(self, run, eff, case, kind, experiment):
        cid, role = case["case_id"], case["role"]
        p = self._params(run, case)
        started = _now()
        failed = role in self.fail_roles or cid in self.fail_ids
        m = physics(p["volfrac"], p["rmin_mm"])
        if role == "check":
            for k in ("umax_mm", "vm_p99_MPa", "vm_p95_MPa", "vm_max_MPa", "compliance_Nmm", "mass_voxel_kg"):
                m[k] *= 1.0 + self.check_bias
        if role == "parity":
            src = run.read_json(f"{case['source_case']}/results.json")
            m = {k: src[k] * (1.0 + self.parity_bias) for k in m}
        h = case["h_fea_mm"]
        cells = int(round(100 / h) * round(100 / h) * round(40 / h))
        gen = {"kind": "simp", "h_mm": 4.0, "elements": 6250, "device": "cpu/numpy", "wall_s": 0.0,
               "converged": True} if kind == "design" else None
        skin = {"status": "ok", "faces": 1000, "field_h_mm": 2.0, "wall_s": 0.0, "genus": 3,
                "islands_removed": 0} if kind == "design" else None
        diag = {"generator": gen, "skin": skin, "checks": {}}
        if skin:
            run.write_json(f"{cid}/skin.json", {"schema": golden.SCHEMA["skin"], **skin})
        run.write_json(f"{cid}/solve.json", {"device": "cpu/numpy", "elements": cells, "dof": 3 * cells, "wall_s": 0.0,
                                             "converged": 0.0 if failed else 1.0})
        if failed:
            out = {"case_id": cid, "role": role, "status": "FAILED", "design_status": "disconnected",
                   "error": "load path not connected (test input)", "results": None, "diagnostics": diag,
                   "started_utc": started, "finished_utc": _now(), "wall_s": 0.0, "stages": []}
        else:
            res = {"schema": golden.SCHEMA["case"], "case_id": cid, "role": role, "round": case.get("round"),
                   "level": case.get("level"), "source_case": case.get("source_case"),
                   "design": dict(p) if role in ("sweep", "verify") else None, **m,
                   "pcg_iters": 400.0, "pcg_relres": 1e-9, "converged": True, "elements": cells, "dof": 3 * cells,
                   "elem_mm": h, "n_fixed_dofs": 300, "device": "cpu/numpy", "dtype": "float64",
                   "backend": "step-structures.stage_solve", "lane": "desktop-cpu", "grade": golden.GRADE_PREVIEW,
                   "generator": gen, "skin": skin, "voxel": {"h_mm": h, "cells": cells, "solid_cells": int(cells * p["volfrac"]),
                                                             "voxel_volume_mm3": m["voxel_volume_mm3"],
                                                             "mesh_volume_mm3": m["mesh_volume_mm3"],
                                                             "volume_ratio_voxel_over_mesh": 1.025},
                   "wall_s_generate": 0.0, "wall_s_skin": 0.0, "wall_s_voxel": 0.0, "wall_s_solve": 0.0,
                   "wall_s_post": 0.0, "wall_s_total": 0.0, "artifacts": []}
            out = {"case_id": cid, "role": role, "status": "FINISHED", "design_status": "ok", "error": None,
                   "results": res, "diagnostics": diag, "started_utc": started, "finished_utc": _now(), "wall_s": 0.0,
                   "stages": []}
            run.write_json(f"{cid}/results.json", res)
        run.write_json(f"{cid}/status.json", {"status": out["status"], "design_status": out["design_status"],
                                              "mlflow_run_id": None, "wall_s": 0.0, "error": out["error"],
                                              "stages": [], "diagnostics": diag})
        wanted = self.write_evidence is True or (isinstance(self.write_evidence, (set, frozenset, tuple, list))
                                                 and role in self.write_evidence)
        if wanted and experiment is not None:
            from simlab.evidence import manifest

            ev = manifest(experiment, golden.clean(case), str(run.path(cid)),
                          [str(run.path("inputs", "spec.json")), str(run.path(cid, "case.json"))],
                          dt.datetime.fromisoformat(out["started_utc"]), dt.datetime.fromisoformat(out["finished_utc"]),
                          out["status"], golden.clean(out["results"]), out["error"])
            ev["stages"] = []
            golden.dump_json(run.path(cid, "evidence.json"), ev, indent=2)
        return out


class case_module:
    """Context manager: the fake at sys.modules[CASE_MODULE], whatever was there (B1's real module, when another test
    imported it) restored afterwards."""

    def __init__(self, fake: FakeCaseModule):
        self.fake = fake
        self.prev = None

    def __enter__(self) -> FakeCaseModule:
        self.prev = sys.modules.get(CASE_MODULE)
        sys.modules[CASE_MODULE] = self.fake
        return self.fake

    def __exit__(self, *exc) -> None:
        if self.prev is None:
            sys.modules.pop(CASE_MODULE, None)
        else:
            sys.modules[CASE_MODULE] = self.prev


def run_sweep(run, eff, fake: FakeCaseModule, n=10, seed=7, lo_hi=None, experiment=None):
    """Sweep cases d000.. at LHS points (test input) solved by the fake runner; writes sweep.json/sweep.csv."""
    import csv
    import io

    U = optimise.lhs_unit(2, n, seed)
    bounds = lo_hi or VARS
    cases = []
    for i, u in enumerate(U):
        c = {"case_id": f"d{i:03d}", "role": "sweep"}
        for j, k in enumerate(VARS):
            lo, hi = bounds[k]
            c[k] = round(lo + float(u[j]) * (hi - lo), 6)
        c.update({"h_gen_mm": 4.0, "h_fea_mm": 4.0, "device": "cpu", "backend": "adapter", "source_case": None,
                  "reuse_mask": False, "round": None, "level": None, "seed": seed})
        cases.append(c)
    outs = fake.run_cases(run, eff, {}, experiment, cases, pool=None, progress=golden.NullProgress(), kind="design")
    cols = ["case_id", "status", "design_status", *VARS, "mass_kg", "umax_mm", "vm_p99_MPa", "converged"]
    rows = []
    for c, o in zip(cases, outs):
        res = o.get("results") or {}
        rows.append({"case_id": c["case_id"], "status": o["status"], "design_status": o["design_status"],
                     **{k: c[k] for k in VARS}, **{k: res.get(k) for k in ("mass_kg", "umax_mm", "vm_p99_MPa",
                                                                           "converged")}})
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for r in rows:
        w.writerow(["" if r[k] is None else ("true" if r[k] is True else "false" if r[k] is False else
                                             repr(r[k]) if isinstance(r[k], float) else r[k]) for k in cols])
    p = run.write_text("sweep.csv", buf.getvalue())
    run.write_json("sweep.json", {"schema": golden.SCHEMA["sweep"],
                                  "sampler": {"kind": "lhs", "optimization": "random-cd", "seed": seed, "n": n},
                                  "variables": VARS, "outputs": eff["derived"]["outputs"], "columns": cols,
                                  "csv_sha256": golden.sha256_file(p), "n": n,
                                  "n_ok": sum(1 for r in rows if r["status"] == "FINISHED"),
                                  "rows": [dict(r, wall_s=0.0, error=None) for r in rows]})
    return outs


class _Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = tc.FakeRun(self.tmp.name)
        self.eff = full_eff()
        self.run.write_json("inputs/spec.effective.json", self.eff)

    def tearDown(self):
        if getattr(self, "_installed", None) is not None:
            self._installed.__exit__(None, None, None)
        self.tmp.cleanup()

    def install(self, fake: FakeCaseModule) -> FakeCaseModule:
        if getattr(self, "_installed", None) is None:
            self._installed = case_module(fake)
            return self._installed.__enter__()
        sys.modules[CASE_MODULE] = fake  # a second fake in the same test: the first install still restores
        return fake


class TestCompare(unittest.TestCase):
    def test_rows_roles_and_rules(self):
        eff = full_eff(outputs=ALL_OUTPUTS)
        solved = physics(0.25, 9.0)
        pred = {k: v * 1.02 for k, v in solved.items()}
        sd = {k: 0.01 * v for k, v in solved.items()}
        rows, ok, why = verify.compare(pred, sd, solved, eff)
        self.assertTrue(ok, why)
        self.assertEqual([r["role"] for r in rows], ["objective", "constraint", "constraint", "reported", "reported"])
        self.assertTrue(all(r["agree"] for r in rows))
        bad = dict(pred, vm_p99_MPa=solved["vm_p99_MPa"] * 1.2)
        rows, ok, why = verify.compare(bad, sd, solved, eff)
        self.assertFalse(ok)
        self.assertIn("exceeds the 10 % tolerance", why[0])
        over = dict(solved, umax_mm=0.04)
        rows, ok, why = verify.compare(dict(over), sd, over, eff)
        self.assertFalse(ok)
        self.assertTrue(any("does not meet <= 0.035" in w for w in why))
        obj_off = dict(pred, mass_kg=solved["mass_kg"] * 1.5)
        self.assertFalse(verify.compare(obj_off, sd, solved, eff)[1])
        eff2 = full_eff(outputs=ALL_OUTPUTS)
        eff2["verify"]["check_objective"] = False
        self.assertTrue(verify.compare(obj_off, sd, solved, eff2)[1])
        rows, ok, why = verify.compare(pred, sd, None, eff)
        self.assertFalse(ok)


class TestRounds(_Env):
    def test_pass_in_round_one_then_check_parity_and_finalise(self):
        fake = self.install(FakeCaseModule())
        run_sweep(self.run, self.eff, fake)
        plan = compute.plan(tc.detected(gpu_usable=True), self.eff)
        loop = verify.run_rounds(self.run, self.eff, {}, None, None, plan, golden.NullProgress())
        self.assertTrue(loop["passed"], loop["rounds"][-1].get("reasons"))
        self.assertEqual((loop["outcome"], loop["final_round"], loop["final_case"]), ("pass", 1, "v001"))
        r1 = loop["rounds"][0]
        self.assertTrue(r1["from_feasible"])
        self.assertEqual(r1["surrogate"], "surrogate/round-1/cv.json")
        self.assertTrue(all(row["agree"] for row in r1["rows"] if row["role"] != "reported"))
        self.assertLessEqual(self.run.read_json("v001/results.json")["umax_mm"], 0.035)
        levels = compute.plan_check(tc.detected(), self.eff, coarse_solve_s=0.5)
        check = verify.independent_check(self.run, self.eff, {}, None, loop, levels, None, golden.NullProgress())
        self.assertTrue(check["ran"])
        self.assertTrue(check["passed"])
        lv1 = check["levels"][0]
        self.assertEqual((lv1["case_id"], lv1["h_mm"], lv1["gating"]), ("x001", 2.0, True))
        self.assertAlmostEqual(next(r for r in lv1["rows"] if r["output"] == "vm_p99_MPa")["rel_gap"], 0.03)
        self.assertFalse(check["levels"][1]["ran"])
        parity = verify.device_parity(self.run, self.eff, {}, None, loop, plan, golden.NullProgress())
        self.assertEqual([p["case_id"] for p in parity], ["p001"])
        self.assertTrue(parity[0]["passed"])
        self.assertEqual(parity[0]["max_rel_diff"], 0.0)
        self.assertEqual(self.run.read_json("p001/case.json")["reuse_mask"], True)
        g = verify.grade(loop, check, self.eff)
        self.assertEqual(g[0], golden.GRADE_VERIFIED_DESKTOP)
        val = verify.finalise(self.run, self.eff, loop, check, parity, g)
        self.assertEqual((val["verdict"], val["outcome"], val["grade"], val["passed"]),
                         ("PASS", "pass", golden.GRADE_VERIFIED_DESKTOP, True))
        self.assertEqual(val["winner_case"], "v001")
        self.assertEqual(val["resolution_mm"], {"generator": 4.0, "field": 2.0, "solve": 4.0, "check": [2.0, 1.0]})
        self.assertEqual(self.run.read_json("validation.json")["verdict"], "PASS")
        self.assertEqual(verify.derive(val)[:2], ("PASS", golden.GRADE_VERIFIED_DESKTOP))
        for key in ("schema", "tol", "kappa", "cv_margin", "check_objective", "objective", "constraints",
                    "resolution_mm", "rounds", "outcome", "passed", "final_round", "final_case", "winner_case",
                    "winner_params", "independent_check", "device_parity", "conforming", "rig", "grade",
                    "grade_reasons", "verdict", "verdict_reasons", "scope"):
            self.assertIn(key, val)
        self.assertIsNone(val["rig"])  # no lane
        self.assertEqual(set(val["conforming"]), set(verify.CONFORMING_KEYS))
        self.assertFalse(val["conforming"]["ran"])
        self.assertEqual(val["grade_reasons"], ["the 2 mm check agrees with the 4 mm solve within 10 % and meets every "
                                                "constraint"])
        # the level entries carry host, fea_solver, rig and rig_attempt (section 14.3); the case dicts the fake
        # runner received carry fea_solver (pcg for verify and parity, auto and host desktop for the check)
        self.assertEqual({k: lv1[k] for k in ("host", "fea_solver", "rig", "rig_attempt")},
                         {"host": "desktop", "fea_solver": "auto", "rig": None, "rig_attempt": None})
        self.assertEqual(self.run.read_json("v001/case.json")["fea_solver"], "pcg")
        self.assertEqual(self.run.read_json("p001/case.json")["fea_solver"], "pcg")
        x1 = self.run.read_json("x001/case.json")
        self.assertEqual((x1["host"], x1["fea_solver"], x1["device"]), ("desktop", "auto", "cpu"))
        self.assertNotIn("lane", x1)

    def test_fail_then_pass_with_infill(self):
        """Scripted surrogate and search (the real ones are tested elsewhere): round 1 has no feasible candidate and
        solves the closest infeasible design as infill; round 2 finds a feasible winner that verifies."""
        fake = self.install(FakeCaseModule())
        run_sweep(self.run, self.eff, fake)
        seen_excludes = []

        def fake_fit(run, eff, *, round_no, seed):
            return object(), {"round": round_no, "n": 10, "outputs": eff["derived"]["outputs"], "cv": {}, "flags": []}

        def fake_search(sur, eff, fit, run, *, round_no, seed, exclude):
            seen_excludes.append([e["case_id"] for e in exclude])
            p = {"volfrac": 0.30, "rmin_mm": 9.0} if round_no == 1 else {"volfrac": 0.26, "rmin_mm": 9.0}
            m = physics(p["volfrac"], p["rmin_mm"])
            pt = {"params": p, "pred": {k: m[k] for k in eff["derived"]["outputs"]},
                  "sd": {k: 0.0 for k in eff["derived"]["outputs"]}}
            if round_no == 1:
                return {"round": 1, "n_candidates": 1681, "n_excluded": 0, "n_feasible": 0, "winner": None,
                        "from_feasible": False, "closest_infeasible": dict(pt, violation=0.1)}
            return {"round": 2, "n_candidates": 1681, "n_excluded": 4, "n_feasible": 900, "winner": pt,
                    "from_feasible": True, "closest_infeasible": None}

        with mock.patch("aces_engineering.golden.surrogate.fit", fake_fit), \
                mock.patch("aces_engineering.golden.optimise.search", fake_search):
            loop = verify.run_rounds(self.run, self.eff, {}, None, None, {"verify": {"device": "cpu"}},
                                     golden.NullProgress())
        self.assertEqual(seen_excludes, [[], ["v001"]])
        r1, r2 = loop["rounds"]
        self.assertFalse(r1["passed"])
        self.assertFalse(r1["from_feasible"])
        self.assertIn("infill", r1["reasons"][0])
        self.assertTrue(r2["passed"])
        self.assertEqual((loop["outcome"], loop["winner_case"]), ("pass", "v002"))

    def test_infeasible(self):
        self.eff["derived"]["constraints"][1]["limit"] = 0.0001  # unreachable umax limit (test input)
        self.eff["derived"]["rounds"] = 2
        fake = self.install(FakeCaseModule())
        run_sweep(self.run, self.eff, fake)
        loop = verify.run_rounds(self.run, self.eff, {}, None, None, {"verify": {"device": "cpu"}},
                                 golden.NullProgress())
        self.assertFalse(loop["passed"])
        self.assertEqual(loop["outcome"], "infeasible")
        self.assertEqual(len(loop["rounds"]), 2)
        self.assertTrue(all(not r["from_feasible"] for r in loop["rounds"]))
        self.assertEqual(self.run.read_json("optimise/round-2.json")["excluded"], ["v001"])
        g = verify.grade(loop, None, self.eff)
        self.assertEqual(g, (golden.GRADE_PREVIEW, ["check skipped: verification failed"]))
        val = verify.finalise(self.run, self.eff, loop, None, [], g)
        self.assertEqual(val["verdict"], "INFEASIBLE")
        self.assertEqual(val["verdict_reasons"][0], "no candidate predicted feasible with the margins (round 2)")
        self.assertEqual(val["independent_check"]["reason"], "not run: verification failed")
        self.assertEqual(verify.derive(val)[0], "INFEASIBLE")

    def test_verification_failed_when_the_verify_case_fails(self):
        self.eff["derived"]["rounds"] = 2
        fake = self.install(FakeCaseModule(fail_roles={"verify"}))
        run_sweep(self.run, self.eff, fake)
        loop = verify.run_rounds(self.run, self.eff, {}, None, None, {"verify": {"device": "cpu"}},
                                 golden.NullProgress())
        self.assertEqual(loop["outcome"], "verification-failed")
        self.assertIn("v001 did not finish (disconnected)", loop["rounds"][0]["reasons"][0])
        val = verify.finalise(self.run, self.eff, loop, None, [], verify.grade(loop, None, self.eff))
        self.assertEqual(val["verdict"], "FAIL")
        self.assertTrue(val["verdict_reasons"])

    def test_parity_disagreement_is_fail(self):
        fake = self.install(FakeCaseModule(parity_bias=1e-3))
        run_sweep(self.run, self.eff, fake)
        plan = compute.plan(tc.detected(gpu_usable=True, warp_usable=True), self.eff)
        loop = verify.run_rounds(self.run, self.eff, {}, None, None, plan, golden.NullProgress())
        self.assertTrue(loop["passed"])
        parity = verify.device_parity(self.run, self.eff, {}, None, loop, plan, golden.NullProgress())
        self.assertEqual([(p["case_id"], p["backend"], p["passed"]) for p in parity],
                         [("p001", "adapter", False), ("p002", "warp", False)])
        self.assertAlmostEqual(parity[0]["max_rel_diff"], 1e-3)
        val = verify.finalise(self.run, self.eff, loop, None, parity, None)
        self.assertEqual((val["verdict"], val["outcome"], val["passed"]), ("FAIL", "verification-failed", False))
        self.assertTrue(val["verdict_reasons"][0].startswith("device disagreement on gpu/adapter: max rel diff 0.001"))
        self.assertEqual(verify.derive(val)[0], "FAIL")


class TestCheckAndGrade(_Env):
    def _passing_loop(self):
        fake = self.install(FakeCaseModule())
        run_sweep(self.run, self.eff, fake)
        return verify.run_rounds(self.run, self.eff, {}, None, None, {"verify": {"device": "cpu"}},
                                 golden.NullProgress())

    def test_check_skip_reasons(self):
        levels = compute.plan_check(tc.detected(), full_eff(check_mode="off"), coarse_solve_s=0.5)
        failed_loop = {"passed": False, "final_case": None}
        res = verify.independent_check(self.run, self.eff, {}, None, failed_loop, levels, None, golden.NullProgress())
        self.assertEqual(res["reason"], "not run: verification failed")
        self.assertTrue(all(lv["reason"] == "not run: verification failed" for lv in res["levels"]))
        loop = self._passing_loop()
        res = verify.independent_check(self.run, self.eff, {}, None, loop, levels, None, golden.NullProgress())
        self.assertFalse(res["ran"])
        self.assertEqual(res["reason"], "not run: check disabled (--check off)")
        g, why = verify.grade(loop, res, self.eff)
        self.assertEqual((g, why), (golden.GRADE_PREVIEW, ["check skipped: check disabled (--check off)"]))
        slow = compute.plan_check(tc.detected(), self.eff, coarse_solve_s=5.0)
        res = verify.independent_check(self.run, self.eff, {}, None, loop, slow, None, golden.NullProgress())
        self.assertIn("exceeds the 60 s budget", verify.grade(loop, res, self.eff)[1][0])

    def test_grade_rules_on_gaps_and_fine_constraints(self):
        loop = self._passing_loop()
        levels = compute.plan_check(tc.detected(), self.eff, coarse_solve_s=0.5)
        self.install(FakeCaseModule(check_bias=0.12))  # the finer solve differs by 12 % (test input)
        res = verify.independent_check(self.run, self.eff, {}, None, loop, levels, None, golden.NullProgress())
        self.assertFalse(res["passed"])
        g, why = verify.grade(loop, res, self.eff)
        self.assertEqual(g, golden.GRADE_PREVIEW)
        self.assertTrue(any(w.startswith("vm_p99_MPa +12.0 % between 4 and 2 mm exceeds 10 %") for w in why), why)
        # a constraint met at 4 mm but violated at 2 mm (synthetic check record)
        lv = {"level": 1, "case_id": "x001", "h_mm": 2.0, "ran": True, "passed": False, "gating": True,
              "rows": [{"output": "vm_p99_MPa", "role": "constraint", "coarse": 58.0, "fine": 61.0,
                        "rel_gap": 3.0 / 58.0, "agree": True, "op": "<=", "limit": 60.0, "meets_fine": False}]}
        g, why = verify.grade(loop, {"ran": True, "passed": False, "levels": [lv]}, self.eff)
        self.assertEqual(why, ["vm_p99_MPa 61 exceeds 60 at 2 mm"])


class TestFinaliseThin(_Env):
    def test_thin_call_shape(self):
        self.run.write_json("sweep.json", {"n": 10, "n_ok": 2, "rows": [
            {"case_id": f"d{i:03d}", "status": "FINISHED" if i < 2 else "FAILED",
             "design_status": "ok" if i < 2 else "detached-load"} for i in range(10)]})
        val = verify.finalise(self.run, self.eff, None, None, [], None, outcome="thin-sweep")
        self.assertEqual((val["verdict"], val["outcome"], val["grade"]), ("THIN", "thin-sweep", golden.GRADE_PREVIEW))
        self.assertEqual(val["verdict_reasons"], ["only 2 of 10 designs finished: detached-load"])
        self.assertEqual(val["thin"]["need"], 8)
        self.assertEqual(val["rounds"], [])
        self.assertEqual(val["independent_check"]["reason"], "not run: thin sweep")
        self.assertEqual(verify.derive(val)[:2], ("THIN", golden.GRADE_PREVIEW))
        self.assertTrue(math.isclose(val["tol"], 0.10))


if __name__ == "__main__":
    unittest.main()
