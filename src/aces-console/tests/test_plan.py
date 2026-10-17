"""plan.py: the brief-driven Plan routes (design 2026-10-15 §3). The planner is stubbed at the module boundary
(_import_planner, aces_command): the module path, the CLI fallback, the planner's own failures and the honest
planner-unavailable answer are all exercised against a real in-process console. No planner output is fabricated by
the console: every plan these tests read back came from the stub the test installed."""
from __future__ import annotations

import json
import sys
import unittest
from unittest import mock

from aces_console import plan as plan_mod

from . import FIXTURES
from .support import Console

BRIEF = "a 1500 kg six-wheeled off-roader with a load-bearing tubular chassis"

PLAN = {
    "plan_id": "plan-test-0001",
    "class": "structure",
    "confidence": 0.83,
    "class_evidence": ["chassis", "load-bearing"],
    "stages": [
        {"id": "baseline", "name": "baseline FEA", "solver": "feflow", "purpose": "constraint physics first",
         "params": {"mesh_mm": 8}, "gate": {"metric": "max_stress", "max": 250}, "evidence": ["results.json"]},
        {"id": "topopt", "name": "SIMP topopt", "solver": "gendesign", "purpose": "objective physics, cheap",
         "params": {"vol_frac": 0.3}, "gate": "compliance within 5 % of baseline", "evidence": []},
        {"id": "verify", "name": "voxel re-check", "solver": "voxelfea", "purpose": "independent verification",
         "params": {}, "gate": "second discretisation agrees", "evidence": ["evidence.json"]},
    ],
    "spec": {"metrics": [{"name": "mass_kg", "value": 41.2, "unit": "kg"}, {"name": "max_stress", "value": 210, "unit": "MPa"}],
             "targets": {"mass_kg": {"max": 45}, "max_stress": {"max": 250}}},
    "blocked": [{"stage": "verify", "gap": "voxelfea image not built"}],
    "warnings": ["envelope not parsed from the brief"],
}


def module_planner(doc, rec=None):
    def fn(brief, attachments=None):
        if rec is not None:
            rec.append((brief, attachments))
        return json.loads(json.dumps(doc))          # a copy: the console must not share the stub's dict
    return fn


class PlanRouteTest(unittest.TestCase):
    """One console whose upstreams are all closed ports; the planner boundary is stubbed per test."""

    @classmethod
    def setUpClass(cls):
        cls.c = Console(tag="plan")

    @classmethod
    def tearDownClass(cls):
        cls.c.close()

    def test_status_shape(self):
        status, _, body = self.c.get("/api/plan/status")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        d = body["data"]
        self.assertEqual(set(d["planner"]), {"available", "mode", "detail"})
        self.assertIn(d["planner"]["mode"], (None, "module", "cli"))
        self.assertEqual(set(d["launch"]), {"available", "mode", "detail"})
        self.assertTrue(d["launch"]["available"])  # the harness configures server.simlab_mcp (a closed port)
        self.assertEqual(d["launch"]["mode"], "simlab-mcp sim_submit")
        self.assertIn("simlab-mcp", d["launch"]["detail"])
        self.assertIsInstance(d["plans_stored"], int)

    def test_post_guards_apply(self):
        status, _, body = self.c.post("/api/plan", {"brief": BRIEF}, human=False)
        self.assertEqual((status, body["code"]), (403, "refused"))

    def test_bad_briefs(self):
        for payload in ({}, {"brief": ""}, {"brief": "   "}, {"brief": 42}, {"brief": "x" * 9000}):
            status, _, body = self.c.post("/api/plan", payload)
            self.assertEqual((status, body["code"]), (400, "bad-request"), payload.keys())
        for atts in ("a-path-not-a-list", ["ok", 3], ["x" * 301], ["a"] * 33):
            status, _, body = self.c.post("/api/plan", {"brief": BRIEF, "attachments": atts})
            self.assertEqual((status, body["code"]), (400, "bad-request"), str(atts)[:40])

    def test_planner_unavailable_is_honest(self):
        with mock.patch.object(plan_mod, "_import_planner", return_value=(None, "aces_engineering.playbooks")), \
             mock.patch.object(plan_mod, "aces_command", return_value=None):
            status, _, body = self.c.post("/api/plan", {"brief": BRIEF})
        self.assertEqual(status, 503)
        self.assertEqual(body["code"], "planner-unavailable")
        self.assertIn("aces_engineering.playbooks", body["error"])
        self.assertIn("aces CLI not found", body["error"])

    def test_module_planner_round_trip(self):
        rec = []
        with mock.patch.object(plan_mod, "_import_planner", return_value=(module_planner(PLAN, rec), None)):
            status, _, body = self.c.post("/api/plan", {"brief": BRIEF, "attachments": ["run-20261010T102210Z-bc7391"]})
        self.assertEqual(rec, [(BRIEF, ["run-20261010T102210Z-bc7391"])], "the planner got the brief and the attachment refs")
        self.assertEqual(status, 200)
        self.assertEqual(body["meta"]["source"], "planner (module)")
        d = body["data"]
        self.assertEqual(d["plan_id"], "plan-test-0001")
        self.assertEqual(d["class"], "structure")
        self.assertEqual(d["confidence"], 0.83)
        self.assertEqual(d["brief"], BRIEF)
        self.assertEqual(d["attachments"], ["run-20261010T102210Z-bc7391"])
        self.assertEqual([s["id"] for s in d["stages"]], ["baseline", "topopt", "verify"])
        blocked = {s["id"]: (s["blocked"], s["gap"]) for s in d["stages"]}
        self.assertEqual(blocked["verify"], (True, "voxelfea image not built"))
        self.assertEqual(blocked["baseline"], (False, None))
        self.assertEqual(d["spec"]["targets"]["mass_kg"], {"max": 45})
        # the refetch returns the same stored plan
        status, _, again = self.c.get("/api/plan/plan-test-0001")
        self.assertEqual(status, 200)
        self.assertEqual(again["data"]["plan_id"], "plan-test-0001")
        self.assertEqual(again["data"]["stages"][2]["solver"], "voxelfea")

    def test_get_unknown_plan(self):
        status, _, body = self.c.get("/api/plan/plan-never-made")
        self.assertEqual((status, body["code"]), (404, "not-found"))
        self.assertIn("in memory", body["error"])

    def test_planner_exception_is_a_502_not_a_console_bug(self):
        def boom(brief, attachments=None):
            raise RuntimeError("classifier table missing")
        with mock.patch.object(plan_mod, "_import_planner", return_value=(boom, None)):
            status, _, body = self.c.post("/api/plan", {"brief": BRIEF})
        self.assertEqual(status, 502)
        self.assertEqual(body["code"], "planner-failed")
        self.assertIn("classifier table missing", body["error"])

    def test_planner_junk_answer(self):
        with mock.patch.object(plan_mod, "_import_planner", return_value=(lambda brief, attachments=None: [1, 2], None)):
            status, _, body = self.c.post("/api/plan", {"brief": BRIEF})
        self.assertEqual((status, body["code"]), (502, "planner-failed"))
        self.assertIn("list", body["error"])

    def test_normalisation_fills_the_contract(self):
        bare = {"stages": [{"name": "only a name"}], "spec": {}}
        with mock.patch.object(plan_mod, "_import_planner", return_value=(module_planner(bare), None)):
            status, _, body = self.c.post("/api/plan", {"brief": BRIEF})
        self.assertEqual(status, 200)
        d = body["data"]
        self.assertTrue(d["plan_id"].startswith("plan-"), d["plan_id"])
        self.assertIsNone(d["class"])
        self.assertIsNone(d["confidence"])
        self.assertEqual(d["stages"][0]["id"], "stage-1")
        self.assertFalse(d["stages"][0]["blocked"])
        self.assertEqual(d["spec"], {"metrics": [], "targets": {}})
        self.assertEqual(d["blocked"], [])
        self.assertEqual(d["warnings"], [])

    def test_cli_fallback(self):
        doc = json.dumps(PLAN)
        cmd = [sys.executable, "-I", "-c", f"import sys; sys.stdout.write({doc!r})"]
        with mock.patch.object(plan_mod, "_import_planner", return_value=(None, "aces_engineering.playbooks")), \
             mock.patch.object(plan_mod, "aces_command", return_value=cmd):
            status, _, body = self.c.post("/api/plan", {"brief": BRIEF})
        self.assertEqual(status, 200)
        self.assertEqual(body["meta"]["source"], "planner (cli)")
        self.assertEqual(body["data"]["plan_id"], "plan-test-0001")

    def test_cli_failure_carries_the_tail(self):
        cmd = [sys.executable, "-I", "-c", "import sys; sys.stderr.write('golden: no verb plan\\n'); sys.exit(2)"]
        with mock.patch.object(plan_mod, "_import_planner", return_value=(None, "aces_engineering.playbooks")), \
             mock.patch.object(plan_mod, "aces_command", return_value=cmd):
            status, _, body = self.c.post("/api/plan", {"brief": BRIEF})
        self.assertEqual(status, 503)
        self.assertEqual(body["code"], "planner-unavailable")
        self.assertIn("no verb plan", body["error"])

    def test_launch_unknown_plan_is_404(self):
        status, _, body = self.c.post("/api/plan/plan-never-made/launch", {})
        self.assertEqual((status, body["code"]), (404, "not-found"))

    def test_launch_unreachable_rig_is_honest(self):
        """The harness points simlab_mcp at a closed port: the registry fetch fails and the answer is 503."""
        with mock.patch.object(plan_mod, "_import_planner", return_value=(module_planner(PLAN), None)):
            self.c.post("/api/plan", {"brief": BRIEF})
        status, _, body = self.c.post("/api/plan/plan-test-0001/launch", {})
        self.assertEqual((status, body["code"]), (503, "launch-unavailable"))
        self.assertIn("simlab-mcp", body["error"])


class PlanLaunchTest(unittest.TestCase):
    """POST /api/plan/{id}/launch against a stubbed simlab-mcp boundary (sim.mcp_call): the registry lookup,
    sim_put_spec, sim_validate and sim_submit are scripted; what the console sent and returned is asserted."""

    REGISTRY = {"feflow": {"physics": ["structures"], "stages": {"mesh": "cpu", "solve": "cpu"}},
                "gendesign": {"physics": ["structures"], "stages": {"solve": "sim"}, "queue": "sim"}}

    @classmethod
    def setUpClass(cls):
        cls.c = Console(tag="plan-launch")

    @classmethod
    def tearDownClass(cls):
        cls.c.close()

    def _make_plan(self):
        with mock.patch.object(plan_mod, "_import_planner", return_value=(module_planner(PLAN), None)):
            status, _, body = self.c.post("/api/plan", {"brief": BRIEF})
        self.assertEqual(status, 200)
        return body["data"]["plan_id"]

    def _fake_mcp(self, rec, *, submit_fail=None, validate_fail=None, registry=None):
        def fake(app, name, arguments=None):
            rec.append((name, arguments))
            if name == "sim_list_solvers":
                return True, registry if registry is not None else self.REGISTRY, None, {}
            if name == "sim_put_spec":
                return True, {"spec_path": "cases/uploaded/aces-console/x.yaml", "warnings": []}, None, {}
            if name == "sim_validate":
                if validate_fail:
                    return False, None, validate_fail, {}
                return True, {"id": "x", "n_cases": 1}, None, {}
            if name == "sim_submit":
                if submit_fail:
                    return False, None, submit_fail, {}
                return True, {"experiment": "plan-test-0001-baseline", "queue": "cpu",
                              "jobs": ["plan-test-0001-baseline--c000--run--abc123"], "n_cases": 1}, None, {}
            raise AssertionError(f"unexpected tool {name}")
        return fake

    def test_first_runnable_stage_submits_rest_answer_why(self):
        plan_id = self._make_plan()
        rec = []
        with mock.patch.object(plan_mod.sim, "mcp_call", self._fake_mcp(rec)):
            status, _, body = self.c.post(f"/api/plan/{plan_id}/launch", {})
        self.assertEqual(status, 200, body)
        self.assertEqual([n for n, _ in rec], ["sim_list_solvers", "sim_put_spec", "sim_validate", "sim_submit"])
        spec = json.loads(rec[1][1]["content"])      # the spec body is JSON (valid YAML), stdlib-only
        self.assertEqual(spec["solver"], "feflow")
        self.assertEqual(spec["physics"], "structures")   # taken from the rig registry, not invented
        self.assertEqual(spec["resources"]["vram_gb"], 0.0)   # cpu queue (gyroid SF-9 / antenna SF-2)
        self.assertEqual(spec["params"], {"mesh_mm": 8})
        self.assertEqual(spec["tags"]["plan_id"], plan_id)
        self.assertEqual(rec[3][1]["spec_path"], "cases/uploaded/aces-console/x.yaml")
        d = body["data"]
        self.assertEqual(d["launched"]["experiment"], "plan-test-0001-baseline")
        self.assertEqual(d["launched"]["jobs"], ["plan-test-0001-baseline--c000--run--abc123"])
        by_id = {s["id"]: s for s in d["stages"]}
        self.assertTrue(by_id["baseline"]["launched"])
        self.assertEqual(by_id["topopt"]["code"], "deferred")
        self.assertEqual(by_id["verify"]["code"], "stage-blocked")
        self.assertIn("voxelfea image not built", by_id["verify"]["reason"])
        self.assertEqual(list(by_id), ["baseline", "topopt", "verify"])   # plan order kept

    def test_unregistered_solver_submits_nothing(self):
        plan_id = self._make_plan()
        rec = []
        with mock.patch.object(plan_mod.sim, "mcp_call", self._fake_mcp(rec, registry={})):
            status, _, body = self.c.post(f"/api/plan/{plan_id}/launch", {})
        self.assertEqual(status, 200)
        self.assertEqual([n for n, _ in rec], ["sim_list_solvers"])
        d = body["data"]
        self.assertIsNone(d["launched"])
        base = next(s for s in d["stages"] if s["id"] == "baseline")
        self.assertEqual(base["code"], "solver-unregistered")
        self.assertIn("feflow", base["reason"])

    def test_gpu_lane_solver_is_refused_not_submitted_unleased(self):
        """gendesign's solve stage is on the GPU (sim) queue: the console does not size VRAM, so it refuses."""
        gpu_plan = dict(PLAN, stages=[PLAN["stages"][1]], blocked=[])
        with mock.patch.object(plan_mod, "_import_planner", return_value=(module_planner(gpu_plan), None)):
            status, _, body = self.c.post("/api/plan", {"brief": BRIEF})
        plan_id = body["data"]["plan_id"]
        rec = []
        with mock.patch.object(plan_mod.sim, "mcp_call", self._fake_mcp(rec)):
            status, _, body = self.c.post(f"/api/plan/{plan_id}/launch", {})
        self.assertEqual(status, 200)
        self.assertEqual([n for n, _ in rec], ["sim_list_solvers"])   # no put/validate/submit
        st = body["data"]["stages"][0]
        self.assertEqual(st["code"], "gpu-lane-unsized")
        self.assertIn("GPU", st["reason"])

    def test_validate_failure_surfaces_the_rigs_words(self):
        plan_id = self._make_plan()
        rec = []
        with mock.patch.object(plan_mod.sim, "mcp_call",
                               self._fake_mcp(rec, validate_fail="simlab-mcp tool error: solver feflow refuses mesh_mm")):
            status, _, body = self.c.post(f"/api/plan/{plan_id}/launch", {})
        self.assertEqual(status, 200)
        self.assertEqual([n for n, _ in rec], ["sim_list_solvers", "sim_put_spec", "sim_validate"])
        st = next(s for s in body["data"]["stages"] if s["id"] == "baseline")
        self.assertEqual(st["code"], "validate-failed")
        self.assertIn("refuses mesh_mm", st["reason"])

    def test_submit_failure_surfaces(self):
        plan_id = self._make_plan()
        rec = []
        with mock.patch.object(plan_mod.sim, "mcp_call",
                               self._fake_mcp(rec, submit_fail="simlab-mcp network: cannot reach x")):
            status, _, body = self.c.post(f"/api/plan/{plan_id}/launch", {})
        self.assertEqual(status, 200)
        self.assertEqual([n for n, _ in rec], ["sim_list_solvers", "sim_put_spec", "sim_validate", "sim_submit"])
        d = body["data"]
        self.assertIsNone(d["launched"])
        st = next(s for s in d["stages"] if s["id"] == "baseline")
        self.assertEqual(st["code"], "submit-failed")
        self.assertIn("cannot reach", st["reason"])

    def test_no_runnable_stage_says_so(self):
        blocked_plan = dict(PLAN, stages=[PLAN["stages"][2]], blocked=PLAN["blocked"])
        with mock.patch.object(plan_mod, "_import_planner", return_value=(module_planner(blocked_plan), None)):
            status, _, body = self.c.post("/api/plan", {"brief": BRIEF})
        plan_id = body["data"]["plan_id"]
        status, _, body = self.c.post(f"/api/plan/{plan_id}/launch", {})
        self.assertEqual(status, 200)
        d = body["data"]
        self.assertIsNone(d["launched"])
        self.assertIn("no runnable stage", d["note"])


class PlanFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = Console(fixtures=FIXTURES, gc="http://127.0.0.1:1", rigstate="http://127.0.0.1:2",
                        simlab_console="http://127.0.0.1:3", tag="plan-fixture")

    @classmethod
    def tearDownClass(cls):
        cls.c.close()

    def test_plan_write_routes_refused_in_fixture_mode(self):
        status, _, body = self.c.post("/api/plan", {"brief": BRIEF})
        self.assertEqual((status, body["code"]), (503, "fixture"))
        status, _, body = self.c.post("/api/plan/plan-test-0001/launch", {})
        self.assertEqual((status, body["code"]), (503, "fixture"))

    def test_status_still_answers(self):
        status, _, body = self.c.get("/api/plan/status")
        self.assertEqual(status, 200)
        self.assertIn("available", body["data"]["planner"])


if __name__ == "__main__":
    unittest.main()
