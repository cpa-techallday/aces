"""sim.py against the fakes and the 2026-10-11 captures: the two labelled queue scales, the capacity activity labels, the
registry citability note and the "not recorded" lane, the experiment and case passthroughs with their 404 wordings, the
per-case file allow-list, the MCP client (bearer sent, never printed; sim_status without a capture), MLflow run/get,
experiments/search, the invalid runs/search capture and the 403 "Invalid Host header" shown verbatim, and every
unreachable wording. No request leaves the loopback."""
from __future__ import annotations

import hashlib
import json
import os
import secrets as pysecrets
import unittest
from unittest import mock

from aces_console import sim

from . import FIXTURES
from .fakes.fake_console import FakeConsole, load_fixture
from .fakes.fake_mlflow import CAPTURED_RUN, FakeMlflow
from .fakes.fake_simlab_mcp import FakeSimlabMcp
from .support import closed_url
from .support_b import ConsoleB

TOKEN = pysecrets.token_hex(16)
EXP, CASE, RUN_DIR = "aces-cal-air-03d", "c000", "/work/out/queue/aces-cal-air-03d/c000"


class SimRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = mock.patch.dict(os.environ, {"ACES_SIMLAB_TOKEN": TOKEN})
        cls.env.start()
        cls.fc = FakeConsole()
        cls.ml = FakeMlflow()
        cls.mcp = FakeSimlabMcp(token=TOKEN, sse=True)
        cls.c = ConsoleB(simlab_console=cls.fc.url, simlab_mcp=cls.mcp.url, mlflow=cls.ml.url, tag="sim")

    @classmethod
    def tearDownClass(cls):
        cls.c.close()
        cls.fc.close()
        cls.ml.close()
        cls.mcp.close()
        cls.env.stop()

    def setUp(self):
        self.fc.clear()
        self.ml.invalid_host = False

    def test_summary_keeps_the_two_scales_apart(self):
        status, _, body = self.c.get("/api/sim/summary")
        self.assertEqual(status, 200)
        d = body["data"]
        self.assertEqual((d["experiments"], d["cases"], d["cases_done"], d["cases_failed"]), (66, 201, 173, 11))
        self.assertEqual(d["queue_counts"], {"finished": 255, "failed": 34, "queued": 2, "deferred": 2})
        self.assertEqual(d["queue_jobs_total"], 293)
        self.assertNotEqual(d["cases"], d["queue_jobs_total"], "cases and queue jobs are two scales")
        self.assertEqual(d["mlflow"], "http://127.0.0.1:5000", "the console's own link default passes through as reported")
        self.assertEqual(d["notes"]["mlflow"], sim.MLFLOW_LINK_NOTE)
        self.assertEqual(d["mode"], "sim-lab-4")
        self.assertEqual(body["meta"]["source"], self.fc.url + "/api/summary")

    def test_capacity_activity_labels_and_queue_normalisation(self):
        status, _, body = self.c.get("/api/sim/capacity")
        self.assertEqual(status, 200)
        d = body["data"]
        by = {g["index"]: g for g in d["gpus"]}
        self.assertEqual(by[2]["activity"], "outside the queue (operator hand-run)")
        self.assertEqual(by[2]["processes"], [{"pid": 279309, "usedMemMiB": 2628, "processName": "/opt/venv/bin/python3"}])
        self.assertEqual(by[6]["activity"], "rests by mode sim-lab-4")
        for i in (4, 5, 7):
            self.assertEqual(by[i]["activity"], "not in the sim pool", i)
        for i in (0, 1, 3):
            self.assertEqual(by[i]["activity"], "idle", i)
        self.assertEqual(d["gridcontrol_queue"], {"queued": None, "running": None})
        self.assertEqual(d["gridcontrol_queue_note"], "GRID CONTROL reported -1")
        self.assertEqual(d["raw_gridcontrol_queue"], {"queued": -1, "running": -1})
        self.assertEqual(d["source"], "rig-state http://host.docker.internal:8010/mcp")
        self.assertEqual(d["errors"], [])
        self.assertEqual(d["activity_counts"], {"idle": 3, "outside the queue (operator hand-run)": 1, "not in the sim pool": 3,
                                                "rests by mode sim-lab-4": 1})
        self.assertEqual(sorted(d["modes"]), ["batch-overnight", "general-fleet-v2", "sim-lab-4", "sim-lab-8", "workstation"])

    def test_activity_rules_in_order(self):
        modes = {"m": {"rest": [6]}}
        self.assertEqual(sim.gpu_activity({"index": 1, "in_pool": True, "job": {"job_id": "j"}, "processes": [{}]}, "m", modes), "job")
        self.assertEqual(sim.gpu_activity({"index": 1, "in_pool": True, "lease": "owner", "processes": [{}]}, "m", modes), "lease")
        self.assertEqual(sim.gpu_activity({"index": 1, "in_pool": True, "processes": [{"pid": 1}]}, "m", modes), "outside the queue (operator hand-run)")
        self.assertEqual(sim.gpu_activity({"index": 6, "in_pool": False, "processes": []}, "m", modes), "rests by mode m")
        self.assertEqual(sim.gpu_activity({"index": 7, "in_pool": False, "processes": []}, "m", modes), "not in the sim pool")
        self.assertEqual(sim.gpu_activity({"index": 0, "in_pool": True, "derated": True, "processes": []}, "m", modes), "derated")
        self.assertEqual(sim.gpu_activity({"index": 0, "in_pool": True, "processes": []}, "m", modes), "idle")
        self.assertEqual(sim.normalise_queue({"queued": 3, "running": 1}), ({"queued": 3, "running": 1}, None))
        self.assertEqual(sim.normalise_queue(None), ({"queued": None, "running": None}, None))
        self.assertEqual(sim.normalise_queue({"queued": -1, "running": 2})[1], "GRID CONTROL reported -1")

    def test_experiments_queue_scales_never_merged(self):
        status, _, body = self.c.get("/api/sim/experiments")
        self.assertEqual(status, 200)
        d = body["data"]
        self.assertEqual((d["experiments_n"], d["cases_n"], d["queue_n"], d["queue_cap"], d["queue_capped"]), (66, 201, 293, 300, False))
        self.assertEqual(d["queue_by_rq_status"], {"finished": 255, "failed": 34, "queued": 2, "deferred": 2})
        self.assertEqual(d["queue_by_meta_state"], {"finished": 255, "failed": 23, "running": 6, "waiting_for_gpu": 5, "queued": 4})
        self.assertNotEqual(d["queue_by_rq_status"].get("failed"), d["queue_by_meta_state"].get("failed"), "the scales disagree in the capture")
        self.assertIsNone(d["queue_error"])
        self.assertEqual(d["queue_scales_note"], sim.SCALES_NOTE)
        self.assertEqual(len(d["experiments"]), 66)
        self.assertEqual(len(d["queue"]), 293)

    def test_registry_citability_and_lane(self):
        status, _, body = self.c.get("/api/sim/registry")
        self.assertEqual(status, 200)
        d = body["data"]
        self.assertEqual(d["n"], 14)
        self.assertEqual(d["counts"], {"pass": 12, "fail": 2, "other": 0, "citable": 3, "lane_recorded": 0})
        rows = {r["name"]: r for r in d["solvers"]}
        self.assertEqual(sorted(rows), sorted(load_fixture("simlab-registry.json")))
        for r in d["solvers"]:
            self.assertEqual((r["lane"], r["lane_recorded"]), ("not recorded", False), r["name"])
        for name in ("golden-case", "golden-check", "ccx-check"):
            self.assertTrue(rows[name]["citable"], name)
            self.assertEqual(rows[name]["citable_note"], "pass")
            self.assertTrue(rows[name]["image_id"].startswith("sha256:"))
        for name in ("gendesign-topopt", "pyfr", "aces-tpms-periodic", "surrogate-bracket-fno"):
            self.assertFalse(rows[name]["citable"], name)
            self.assertEqual(rows[name]["citable_note"], sim.CITABLE_NOTE)
            self.assertIsNone(rows[name]["image_id"])
        for name in ("surrogate-bracket-gp", "surrogate-bracket-et"):
            self.assertEqual((rows[name]["validation_word"], rows[name]["citable_note"], rows[name]["citable"]), ("fail", "fail", False))
        self.assertEqual(sum(1 for r in d["solvers"] if r["citable_note"] == sim.CITABLE_NOTE), 9)
        # a row without validation reads "not recorded", never a default
        out = sim.decorate_registry({"x": {"module": "m"}})
        self.assertEqual((out["solvers"][0]["validation_word"], out["solvers"][0]["citable_note"]), ("not recorded", "not recorded"))

    def test_experiment_and_case_passthrough_with_404_wording(self):
        status, _, body = self.c.get(f"/api/sim/experiments/{EXP}?root=queue")
        self.assertEqual(status, 200)
        self.assertEqual((body["data"]["id"], body["data"]["project"], len(body["data"]["cases"])), (EXP, "ACES-calibration", 9))
        self.assertEqual(body["meta"]["source"], f"{self.fc.url}/api/experiments/{EXP}?root=queue")
        status, _, body = self.c.get(f"/api/sim/experiment/{EXP}")
        self.assertEqual((status, body["data"]["id"]), (200, EXP))
        status, _, body = self.c.get("/api/sim/experiments/no-such?root=queue")
        self.assertEqual((status, body["error"], body["code"], body["status"]), (404, "unknown experiment", "not-found", 404))
        status, _, body = self.c.get(f"/api/sim/case/{EXP}/{CASE}?root=queue")
        self.assertEqual(status, 200)
        d = body["data"]
        self.assertEqual((d["status"], d["run_dir"], d["mlflow_run_id"]), ("FINISHED", RUN_DIR, "54db1a96a9c0489c8d3ee776d0a9933f"))
        self.assertEqual([f["name"] for f in d["files"]], ["case.json", "evidence.json", "measurement.json", "progress.json", "results.json",
                                                            "sample.json", "status.json", "worker.log"])
        self.assertIsNone(d["mlflow"]["run"])
        self.assertTrue(d["mlflow"]["error"].startswith("not available: HTTP 404"), d["mlflow"])
        self.assertEqual(d["mlflow"]["run_id"], "54db1a96a9c0489c8d3ee776d0a9933f")
        status, _, body = self.c.get(f"/api/sim/case/{EXP}/c999?root=queue")
        self.assertEqual((status, body["error"]), (404, "unknown case"))
        status, _, body = self.c.get("/api/sim/experiments/bad%20id")
        self.assertEqual(status, 400)

    def test_file_route_forwards_only_names_of_the_case_record(self):
        local = FIXTURES / "simlab-case-detail.json"
        self.fc.files[f"{RUN_DIR}/case.json"] = local
        status, hdrs, data = self.c.request("GET", f"/api/sim/file?eid={EXP}&root=queue&cid={CASE}&name=case.json")
        self.assertEqual(status, 200)
        self.assertEqual(data, local.read_bytes())
        self.assertEqual(hdrs["content-security-policy"], "sandbox")
        self.assertEqual(hdrs["x-content-type-options"], "nosniff")
        self.assertEqual(hdrs["content-disposition"], 'inline; filename="case.json"')
        self.assertTrue(hdrs["content-type"].startswith("application/json"))
        forwarded = self.fc.requests_for("/api/file")
        self.assertEqual([r["query"]["p"][0] for r in forwarded], [f"{RUN_DIR}/case.json"])
        before = len(self.fc.requests_for("/api/file"))
        status, _, body = self.c.get(f"/api/sim/file?eid={EXP}&root=queue&cid={CASE}&name=nope.txt")
        self.assertEqual((status, body["error"]), (404, "not a file of this case"))
        self.assertEqual(len(self.fc.requests_for("/api/file")), before, "no arbitrary p is ever forwarded")
        status, _, body = self.c.get(f"/api/sim/file?eid={EXP}&root=queue&cid={CASE}&name=../case.json")
        self.assertEqual(status, 400)
        status, _, body = self.c.get(f"/api/sim/file?eid={EXP}&root=queue&report=report.html")
        self.assertEqual((status, body["error"]), (404, "not a report of this experiment"))
        status, _, body = self.c.get(f"/api/sim/file?eid={EXP}&root=queue")
        self.assertEqual(status, 400)
        # a mapped file the console itself cannot find answers the console's own wording
        self.fc.files.clear()
        status, _, body = self.c.get(f"/api/sim/file?eid={EXP}&root=queue&cid={CASE}&name=case.json")
        self.assertEqual((status, body["error"]), (404, "not found or outside the output roots"))
        log = self.c.log_text()
        self.assertNotIn("p=", log)
        self.assertNotIn(RUN_DIR, log, "neither the path nor the query is logged")

    def test_evidence_verify_and_audit(self):
        status, _, body = self.c.get("/api/sim/evidence")
        self.assertEqual((status, body["data"]["n"], len(body["data"]["ledger"])), (200, 13, 13))
        self.assertEqual(body["data"]["ledger"][0]["experiment"], "halo-skeleton-v2-singlepin-mg")
        status, _, body = self.c.get("/api/sim/evidence/verify")
        self.assertEqual(body["data"], {"entries": 13, "ok": True, "problems": [],
                                        "head": "132b2ca0723654b5af0f1443e97ed82abc6ad069fc61bd2c0ac68a768d5aaddf"})
        status, _, body = self.c.get("/api/sim/audit")
        self.assertEqual((status, body["data"]["n"], body["data"]["cap"]), (200, 400, 400))
        self.assertEqual(body["data"]["audit"][-1]["tool"], "sim_whoami")

    def test_whoami_and_status_through_the_mcp_client(self):
        status, _, body = self.c.get("/api/sim/whoami")
        self.assertEqual(status, 200, body)
        d = body["data"]
        self.assertEqual((d["principal"], d["policy"], d["n_tools"], len(d["tools"])), ("anonymous", "full", 17, 17))
        self.assertEqual(d["token_source"], "ACES_SIMLAB_TOKEN")
        self.assertEqual(d["server"]["name"], "fake simlab-mcp (test stand-in)")
        self.assertEqual(d["protocol"], "2025-06-18")
        self.assertEqual(d["audit_log"], "/work/out/mcp_audit.jsonl")
        with self.mcp.lock:
            reqs = list(self.mcp.requests)
        self.assertGreaterEqual(len(reqs), 3)
        self.assertTrue(all(r["headers"].get("authorization") == f"Bearer {TOKEN}" for r in reqs), "the bearer goes on every request")
        methods = [r["body"].get("method") for r in reqs if isinstance(r["body"], dict)]
        self.assertEqual(methods[:3], ["initialize", "notifications/initialized", "tools/call"])
        self.assertTrue(all("mcp-session-id" in r["headers"] for r in reqs[1:]), "the session id follows initialize")
        self.assertNotIn(TOKEN, json.dumps(body))
        status, _, body = self.c.get(f"/api/sim/status/{EXP}")
        self.assertEqual(status, 200)
        self.assertIsNone(body["data"])
        self.assertFalse(body["meta"]["ok"])
        self.assertTrue(body["meta"]["error"].startswith("simlab-mcp rpc: JSON-RPC error -32601"), body["meta"]["error"])
        self.assertEqual(len(self.mcp.calls("sim_status")), 1)
        self.assertEqual(self.mcp.calls("sim_status")[0]["body"]["params"]["arguments"], {"experiment_id": EXP})
        self.assertNotIn(TOKEN, self.c.log_text())

    def test_mlflow_routes(self):
        status, _, body = self.c.get("/api/mlflow/experiments")
        self.assertEqual((status, body["data"]["n"]), (200, 12))
        ids = [(e["experiment_id"], e["name"]) for e in body["data"]["experiments"]]
        self.assertIn(("11", "aces-golden"), ids)
        self.assertIn(("0", "Default"), ids)
        self.assertFalse(body["data"]["page_more"])
        status, _, body = self.c.get(f"/api/mlflow/run/{CAPTURED_RUN}")
        self.assertEqual(status, 200)
        d = body["data"]
        self.assertEqual(d["run"]["info"]["experiment_id"], "11")
        self.assertEqual(d["run"]["info"]["status"], "FINISHED")
        self.assertEqual((len(d["run"]["data"]["params"]), len(d["run"]["data"]["metrics"]), len(d["run"]["data"]["tags"])), (2, 17, 17))
        self.assertEqual(d["ui_url"], f"{self.ml.url}/#/experiments/11/runs/{CAPTURED_RUN}")
        status, _, body = self.c.get("/api/mlflow/run/" + "0" * 32)
        self.assertEqual(status, 404)
        self.assertTrue(body["error"].startswith("not available: HTTP 404"))
        status, _, body = self.c.get("/api/mlflow/run/not-a-run-id")
        self.assertEqual(status, 400)
        status, _, body = self.c.get("/api/mlflow/runs?experiment_id=11&max=5")
        self.assertEqual(status, 200)
        self.assertIsNone(body["data"])
        self.assertTrue(body["meta"]["error"].startswith("not available: not JSON"), body["meta"]["error"])
        posted = self.ml.requests_for("POST", "/api/2.0/mlflow/runs/search")
        self.assertEqual(posted[-1]["body"]["experiment_ids"], ["11"])
        status, _, body = self.c.get("/api/mlflow/runs?experiment_id=x")
        self.assertEqual(status, 400)
        self.ml.invalid_host = True
        self.c.app.hub.refresh("mlflow.experiments")
        status, _, body = self.c.get("/api/mlflow/experiments")
        self.assertEqual(status, 200)
        self.assertEqual(body["meta"]["error"], "HTTP 403: Invalid Host header", "the proxy's text is shown verbatim")
        self.assertFalse(body["meta"]["ok"])
        self.assertTrue(body["meta"]["stale"], "the last good list is kept, marked stale (spec 3.1)")
        self.assertEqual(body["data"]["n"], 12)
        self.ml.invalid_host = False
        self.c.app.hub.refresh("mlflow.experiments")
        status, _, body = self.c.get("/api/mlflow/experiments")
        self.assertTrue(body["meta"]["ok"])
        self.assertFalse(body["meta"]["stale"])

    def test_unreachable_wordings(self):
        c = ConsoleB(tag="sim-down")
        try:
            status, _, body = c.get("/api/sim/summary")
            self.assertEqual(status, 200)
            self.assertIsNone(body["data"])
            self.assertTrue(body["meta"]["error"].startswith("SIMLAB console unreachable at http://127.0.0.1:"), body["meta"]["error"])
            self.assertIsNotNone(body["meta"]["next_try_s"])
            status, _, body = c.get("/api/sim/whoami")
            self.assertTrue(body["meta"]["error"].startswith("simlab-mcp network: cannot reach http://127.0.0.1:"), body["meta"]["error"])
            self.assertEqual(sim.mcp_call(c.app, "sim_whoami")[3]["token_source"], "ACES_SIMLAB_TOKEN")
            status, _, body = c.get("/api/mlflow/experiments")
            self.assertTrue(body["meta"]["error"].startswith("MLflow unreachable at http://127.0.0.1:"), body["meta"]["error"])
            status, _, body = c.get(f"/api/sim/file?eid={EXP}&root=queue&cid={CASE}&name=case.json")
            self.assertEqual((status, body["code"]), (502, "upstream"))
            self.assertTrue(body["error"].startswith("SIMLAB console unreachable at"))
        finally:
            c.close()

    def test_fixture_copies_of_the_captures(self):
        manifest = {r["file"]: r for r in json.loads((FIXTURES / "MANIFEST.json").read_text(encoding="utf-8"))}
        for name in ("simlab-summary.json", "simlab-capacity.json", "simlab-experiments.json", "simlab-registry.json", "simlab-evidence.json",
                     "simlab-evidence-verify.json", "simlab-audit.json", "simlab-case-detail.json", "simlab-experiment-detail.json",
                     "simlab-mcp-tools.json", "simlab-whoami.json", "simlab-modes.json", "mlflow-experiments.json", "mlflow-run-get.json",
                     "mlflow-runs-search.json"):
            self.assertIn(name, manifest)
            self.assertEqual(hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest(), manifest[name]["sha256"], name)
        self.assertIn("NOT VALID JSON", manifest["mlflow-runs-search.json"]["note"])
        with self.assertRaises(json.JSONDecodeError):
            json.loads((FIXTURES / "mlflow-runs-search.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
