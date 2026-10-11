"""Builder D: the records routes of the fixture devserver (tests/harness/records_routes.py) against the real records,
and the four records plates driven by deep link through tests/harness/verify_records.py.

Everything runs the fixture devserver on an ephemeral loopback port. With a copied work store (ACES_CONSOLE_VM_WORK, or
the integrator's scratchpad copy on this machine) the golden, CAD, notes, drawings and ledger routes are checked against
its real records (the VM's cad-vm, cad-gold and golden-demo projects of 2026-10-10); without one the honest-absence
shapes are checked instead. The SIMLAB detail and MLflow routes replay the captures of 2026-10-11. `bundle-check` runs the
real check_bundle.py; `golden verify` runs `python -m aces golden verify`, which exits 127 where aces-engineering is not
installed (reported as 503) and returns the checks table where it is. No rig is contacted. Nothing here is test data.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

from .test_harness import HARNESS, DevServerProcess, get, post, vm_work_dir

GOLDEN = ("cad-gold", "20261010T102210Z-bc7391")
CAD = ("cad-vm", "20261010T101628Z-0ffb")
BUNDLE = "20261010T101736Z-plate"


class TestRecordsRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = DevServerProcess()
        cls.vm = vm_work_dir()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def api(self, path):
        st, headers, body = get(self.srv.url + path)
        return st, headers, json.loads(body.decode("utf-8"))

    def human(self):
        return {"Origin": self.srv.url, "X-ACES-Intent": "human", "Content-Type": "application/json"}

    # ---- golden (spec 3.9) ----
    def test_golden_runs_and_detail(self):
        st, _, r = self.api("/api/golden/runs")
        self.assertEqual(st, 200)
        self.assertEqual(r["meta"]["fixture"], "FIXTURE 2026-10-11")
        d = r["data"]
        if not self.vm:
            self.assertEqual(d["runs"], [])
            work = os.environ.get("ACES_WORK", "")
            # an existing, empty work store (makepkg's check(), the installed tests) reads "no golden runs under <work>";
            # an absent one "<work> does not exist"
            self.assertRegex(d["reason"], r"^no golden runs under " if work and os.path.isdir(work) else r"does not exist$")
            return
        self.assertIsNone(d["reason"])
        self.assertEqual(len(d["runs"]), 4)
        self.assertEqual(d["runs"][0]["run_id"], GOLDEN[1], "newest first")
        self.assertEqual({x["project"] for x in d["runs"]}, {"cad-gold", "golden-demo"})
        st, _, r = self.api(f"/api/golden/runs?project={GOLDEN[0]}")
        self.assertEqual([x["run_id"] for x in r["data"]["runs"]], [GOLDEN[1]])
        row = r["data"]["runs"][0]
        self.assertEqual((row["status"], row["verdict"], row["grade"], row["outcome"], row["exit_code"], row["wall_s"], row["has_result"]), ("done", "PASS", "verified-desktop", "pass", 0, 49.509, True))
        st, _, r = self.api(f"/api/golden/run/{GOLDEN[0]}/{GOLDEN[1]}")
        self.assertEqual(st, 200)
        d = r["data"]
        self.assertEqual(d["run"]["grade"], "verified-desktop")
        self.assertEqual(d["result"]["verdict"], "PASS")
        self.assertIsNone(d["result_absent_reason"])
        self.assertEqual(len(d["run"]["stages"]), 9)
        self.assertEqual(len(d["compute_used"]["rows"]), 15)
        self.assertEqual(len(d["report"]["provenance"]), 306)
        self.assertEqual(len(d["report"]["sections"]), 17)
        self.assertEqual(d["sweep"]["columns"][:3], ["case_id", "status", "design_status"])
        self.assertEqual(len(d["sweep"]["rows"]), 10)
        self.assertIn("winner.stl", d["winner"]["files"])
        self.assertEqual(d["winner"]["json"]["case_id"], "v001")
        # the VM's run holds five figures; the record-only copy of the same run (ui-fixtures) omitted sweep_gallery.png
        self.assertTrue({"check_levels.png", "conforming.png", "optimise_map.png", "sweep_scatter.png"} <= set(d["figures"]), d["figures"])
        self.assertEqual(d["evidence_ledgers"]["simlab"]["entry"], 4)
        self.assertEqual(d["validation"]["grade"], "verified-desktop")
        self.assertGreater(len(d["files"]), 200)
        self.assertEqual(len(d["events_tail"]), 49)
        self.assertEqual(d["events_tail"][-1]["kind"], "result")
        self.assertEqual(d["read_errors"], [])
        self.assertIn("no grade is a sign-off", d["notes"])
        # a finished run has no live event stream; an absent run and a malformed id are refused with their reason
        st, _, r = self.api(f"/api/golden/events/{GOLDEN[0]}/{GOLDEN[1]}")
        self.assertEqual((st, r["code"]), (404, "not-running"))
        st, _, r = self.api(f"/api/golden/run/{GOLDEN[0]}/20261010T000000Z-000000")
        self.assertEqual((st, r["code"]), (404, "not-found"))
        st, _, r = self.api(f"/api/golden/run/{GOLDEN[0]}/not-a-run-id")
        self.assertEqual(st, 400)
        st, _, r = self.api("/api/golden/runs?project=..")
        self.assertEqual(st, 400)

    def test_golden_file_route_rules(self):
        if not self.vm:
            self.skipTest("no copied work store on this machine")
        base = f"/api/golden/file/{GOLDEN[0]}/{GOLDEN[1]}"
        st, h, body = get(self.srv.url + base + "/report.html")
        self.assertEqual(st, 200)
        self.assertEqual(h.get("Content-Type"), "text/html; charset=utf-8")
        self.assertEqual(h.get("Content-Security-Policy"), "sandbox")
        self.assertEqual(h.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(h.get("Content-Disposition"), 'inline; filename="report.html"')
        self.assertEqual(h.get("Cache-Control"), "private, max-age=60")
        self.assertRegex(h.get("ETag", ""), r'^"[0-9a-f]{64}"$')
        self.assertIn(b"<html", body.lower())
        st2, h2, body2 = get(self.srv.url + base + "/report.html", {"If-None-Match": h["ETag"]})
        self.assertEqual((st2, body2), (304, b""))
        st, h, body = get(self.srv.url + base + "/winner/winner.stl")
        self.assertEqual((st, h.get("Content-Type"), len(body)), (200, "model/stl", 519084))
        st, h, body = get(self.srv.url + base + "/figures/sweep_scatter.png")
        self.assertEqual((st, h.get("Content-Type")), (200, "image/png"))
        self.assertEqual(body[:8], b"\x89PNG\r\n\x1a\n")
        for rel, status in (("../run.json", 400), (".hidden", 403), ("secret.env", 403), ("my-token.json", 403), ("no-such-file.json", 404), ("domain", 415)):
            st, _, body = get(self.srv.url + base + "/" + rel)
            self.assertEqual(st, status, rel)
        st, _, body = get(self.srv.url + "/api/golden/file/cad-gold/not-a-run/run.json")
        self.assertEqual(st, 400)

    def test_golden_verify_is_run_and_reported_honestly(self):
        if not self.vm:
            self.skipTest("no copied work store on this machine")
        st, r = post(self.srv.url + f"/api/golden/verify/{GOLDEN[0]}/{GOLDEN[1]}", {}, self.human())
        if st == 503:
            self.assertEqual(r["code"], "not-installed")
            self.assertEqual(r["error"], "aces-engineering not installed (exit 127)")
        else:
            self.assertEqual(st, 200)
            self.assertGreaterEqual(len(r["data"]["checks"]), 13)
            self.assertIn("ok", r["data"])
        st, r = post(self.srv.url + f"/api/golden/verify/{GOLDEN[0]}/{GOLDEN[1]}", {})
        self.assertEqual((st, r["code"]), (403, "refused"))

    # ---- CAD (spec 3.10) ----
    def test_cad_runs_detail_bundles_notes_drawings_onshape(self):
        st, _, r = self.api(f"/api/cad/runs?project={CAD[0]}")
        self.assertEqual(st, 200)
        d = r["data"]
        if not self.vm:
            self.assertEqual(d["runs"], [])
            self.assertEqual(d["note"], f"no CAD store in {CAD[0]}")
            st, _, r = self.api(f"/api/cad/bundles?project={CAD[0]}")
            self.assertEqual(r["data"]["bundles"], [])
            st, _, r = self.api(f"/api/cad/notes/{CAD[0]}")
            self.assertEqual(r["data"]["entries"], [])
            self.assertEqual(r["data"]["note"], f"no NOTES.md in {CAD[0]}")
            st, _, r = self.api(f"/api/cad/drawings/{CAD[0]}")
            self.assertEqual(r["data"]["drawings"], [])
            st, _, r = self.api(f"/api/cad/onshape/{CAD[0]}")
            self.assertEqual((r["data"]["connected"], r["data"]["project_class"]), (False, None))
            return
        self.assertEqual(d["counts"], {"total": 16, "unreadable": 0})
        self.assertEqual(d["runs"][-1]["run_id"], CAD[1])
        self.assertEqual(d["runs"][-1]["sandbox"], {"mode": "strict", "confined": True, "platform": "linux-x86_64"})
        self.assertEqual({x["status"] for x in d["runs"]}, {"ok", "error"})
        st, _, r = self.api(f"/api/cad/run/{CAD[0]}/{CAD[1]}")
        self.assertEqual(st, 200)
        rec = r["data"]["record"]
        self.assertEqual((rec["name"], rec["status"], rec["class"]), ("plate", "ok", "sovereign"))
        self.assertAlmostEqual(rec["result"]["totals"]["volume_mm3"], 11607.300918301276)
        rels = {f["rel"]: f for f in r["data"]["files"]}
        self.assertIn("run.json", rels)
        self.assertIn("drawings/plate-fa5cc45b.svg", rels)
        self.assertEqual(rels["drawings/plate-fa5cc45b.svg"]["sha256"], "e423906b462abe44b573c26825243846ea7573364e633c25a6c2c7cbc02a483f")
        st, _, r = self.api(f"/api/cad/run/{CAD[0]}/20261010T000000Z-0000")
        self.assertEqual((st, r["code"]), (404, "not-found"))
        st, _, r = self.api(f"/api/cad/bundles?project={CAD[0]}")
        bundles = {b["name"]: b for b in r["data"]["bundles"]}
        self.assertEqual(len(bundles), 4)
        b = bundles[BUNDLE]
        self.assertTrue(all(b["has"].values()))
        self.assertEqual(b["outcome_summary"]["ledger_written"], True)
        self.assertEqual((b["outcome_summary"]["ledger_n"], b["outcome_summary"]["ledger_kind"]), (6, "cad.pull"))
        self.assertEqual((b["outcome_summary"]["cad_source_recorded"], b["outcome_summary"]["cad_source_ref"]), (True, "cad/local/20261010T101736Z-plate/part.step"))
        self.assertEqual(b["source_summary"]["geometry_sha256"], "806c9be7a86c8ae60308ee75d9a910f371d5f5aae6fe6517fa5ce6aa5fd1742f")
        st, _, r = self.api(f"/api/cad/notes/{CAD[0]}")
        e = r["data"]["entries"]
        self.assertEqual(len(e), 1)
        self.assertEqual((e[0]["ts"], e[0]["tags"], e[0]["run_id"]), ("2026-10-10T10:32:20Z", ["build123d", "plate", "hole"], "20261010T103142Z-09a4"))
        self.assertTrue(e[0]["body"].startswith("For a simple centered through hole"))
        self.assertFalse(r["data"]["truncated"])
        st, _, r = self.api(f"/api/cad/drawings/{CAD[0]}")
        dr = {x["id"]: x for x in r["data"]["drawings"]}
        self.assertEqual(sorted(dr), ["agent-plate-0b866e26", "plate-fa5cc45b"])
        p = dr["plate-fa5cc45b"]
        self.assertEqual((p["run_id"], p["n_dimensions"], p["hole_table"], p["schema"]), (CAD[1], 6, None, "aces.cad.drawing/1"))
        self.assertEqual([v["name"] for v in p["views"]], ["front", "top", "right", "iso"])
        self.assertEqual(p["files"]["svg"], f"/api/cad/file/{CAD[0]}/{CAD[1]}/drawings/plate-fa5cc45b.svg")
        st, _, r = self.api(f"/api/cad/onshape/{CAD[0]}")
        self.assertEqual((r["data"]["connected"], r["data"]["project_class"]), (False, "sovereign"))
        self.assertEqual(r["data"]["note"], "this console never reads Onshape keys")

    def test_cad_files_and_bundle_check(self):
        if not self.vm:
            self.skipTest("no copied work store on this machine")
        st, h, body = get(self.srv.url + f"/api/cad/file/{CAD[0]}/{CAD[1]}/drawings/plate-fa5cc45b.svg")
        self.assertEqual((st, h.get("Content-Type"), h.get("Content-Security-Policy"), len(body)), (200, "image/svg+xml", "sandbox", 229583))
        st, h, body = get(self.srv.url + f"/api/cad/bundle/{CAD[0]}/{BUNDLE}/part.stl")
        self.assertEqual((st, h.get("Content-Type"), len(body)), (200, "model/stl", 26084))
        st, _, body = get(self.srv.url + f"/api/cad/bundle/{CAD[0]}/{BUNDLE}/part.3mf")
        self.assertEqual(st, 415, "3mf is outside the content-type allow-list")
        st, _, body = get(self.srv.url + f"/api/cad/file/{CAD[0]}/{CAD[1]}/out/p0.brep")
        self.assertEqual(st, 415, "a brep is never served by the CAD file route")
        st, _, body = get(self.srv.url + f"/api/cad/file/{CAD[0]}/{CAD[1]}/../../../aces.yaml")
        self.assertEqual(st, 400)
        st, r = post(self.srv.url + f"/api/cad/bundle-check/{CAD[0]}/{BUNDLE}", {}, self.human())
        self.assertEqual(st, 200, r)
        d = r["data"]
        self.assertEqual((d["exit"], d["all_match"]), (0, True))
        self.assertTrue(d["lines"][-1].endswith("all files match"), d["lines"])
        self.assertTrue(any(ln.startswith("ok   geometry_sha256") for ln in d["lines"]), d["lines"])
        st, r = post(self.srv.url + f"/api/cad/bundle-check/{CAD[0]}/20261010T000000Z-nothing", {}, self.human())
        self.assertEqual((st, r["code"]), (404, "not-found"))

    # ---- SIMLAB detail and MLflow (spec 3.6, 3.7) ----
    def test_sim_detail_and_mlflow_from_the_captures(self):
        st, _, r = self.api("/api/sim/experiments/aces-cal-air-03d?root=queue")
        self.assertEqual(st, 200)
        self.assertEqual(r["meta"]["fetched_utc"], "2026-10-11T00:34:41Z", "the capture time, never a fresh one")
        self.assertEqual(len(r["data"]["cases"]), 9)
        self.assertEqual(r["data"]["validation"], "pass")
        st, _, r = self.api("/api/sim/experiments/no-such-experiment")
        self.assertEqual(st, 200)
        self.assertIsNone(r["data"])
        self.assertTrue(r["meta"]["error"].startswith("not captured in the fixtures"))
        st, _, r = self.api("/api/sim/case/aces-cal-air-03d/c000?root=queue")
        d = r["data"]
        self.assertEqual((d["status"], d["mlflow_run_id"]), ("FINISHED", "54db1a96a9c0489c8d3ee776d0a9933f"))
        self.assertIsNone(d["mlflow"]["run"])
        self.assertTrue(d["mlflow"]["error"].startswith("not captured: MLflow runs/get for run 54db1a96a9c0489c8d3ee776d0a9933f"))
        st, _, r = self.api("/api/mlflow/run/97cfbb614b98494b8d3aaa63e46cfd9e")
        self.assertEqual(r["data"]["run"]["info"]["status"], "FINISHED")
        self.assertEqual(len(r["data"]["run"]["data"]["metrics"]), 17)
        self.assertEqual(r["data"]["ui_url"], "http://192.168.1.65:5000/#/experiments/11/runs/97cfbb614b98494b8d3aaa63e46cfd9e")
        st, _, r = self.api("/api/mlflow/run/54db1a96a9c0489c8d3ee776d0a9933f")
        self.assertIsNone(r["data"])
        self.assertTrue(r["meta"]["error"].startswith("not captured"))
        st, _, r = self.api("/api/sim/experiments")
        self.assertEqual(r["data"]["queue_by_rq_status"], {"finished": 255, "failed": 34, "queued": 2, "deferred": 2})
        self.assertEqual(r["data"]["queue_by_meta_state"], {"finished": 255, "failed": 23, "running": 6, "waiting_for_gpu": 5, "queued": 4})
        self.assertEqual(len(r["data"]["experiments"]), 66)
        st, _, body = get(self.srv.url + "/api/sim/file?eid=aces-cal-air-03d&root=queue&cid=c000&name=results.json")
        self.assertEqual(st, 404, "case files are not in the captures; never invented")

    # ---- ledgers (spec 3.11) ----
    def test_ledger_verify_and_simlab_chain(self):
        st, _, r = self.api("/api/ledger/verify")
        self.assertEqual(st, 200)
        if self.vm:
            self.assertEqual((r["data"]["ok"], r["data"]["n"], r["data"]["message"], r["data"]["first_bad"]), (True, 11, "chain intact", None))
        else:
            self.assertEqual((r["data"]["ok"], r["data"]["n"], r["data"]["message"]), (True, 0, "ledger empty"))
        st, _, r = self.api("/api/ledger/simlab")
        self.assertEqual(len(r["data"]["entries"]), 13)
        self.assertEqual(r["data"]["verify"], {"entries": 13, "ok": True, "problems": [], "head": "132b2ca0723654b5af0f1443e97ed82abc6ad069fc61bd2c0ac68a768d5aaddf"})
        self.assertEqual(r["data"]["note"], "rig-side chain; verified by the SIMLAB console, not by this desktop")
        self.assertEqual(r["data"]["entries"][-1]["experiment"], "golden-20261010T084920Z-b3ac99-conf")


class TestVerifyRecords(unittest.TestCase):
    """Runs the records-plates harness against a devserver; SKIP (exit 0) when no browser exists is a pass."""

    def test_verify_records(self):
        shots = os.environ.get("ACES_CONSOLE_SHOTS") or tempfile.mkdtemp(prefix="aces-console-records-")
        cmd = [sys.executable, "-I", os.path.join(HARNESS, "verify_records.py"), "--shots", shots]
        vm = vm_work_dir()
        if vm:
            cmd += ["--vm-work", vm]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        tail = "\n".join(proc.stdout.splitlines()[-40:])
        sys.stderr.write("\n--- verify_records.py ---\n" + tail + "\n" + proc.stderr[-2000:] + "\n")
        self.assertEqual(proc.returncode, 0, tail + "\n" + proc.stderr[-2000:])
        if "SKIP:" in proc.stdout:
            return
        self.assertIn("RESULT: PASS", proc.stdout)
        with open(os.path.join(shots, "records-findings.json"), encoding="utf-8") as fh:
            f = json.load(fh)
        self.assertEqual(f["fixture"], "FIXTURE 2026-10-11")
        for vp in f["viewports"]:
            self.assertEqual(vp["console_errors"], [])
            self.assertEqual(vp["page_errors"], [])
            self.assertEqual(vp["requests_failed"], [])
            self.assertEqual(sorted(vp["plates"]), ["evidence", "objects", "pipeline", "solvers"])
            for name, d in vp["plates"].items():
                self.assertTrue(d["ok"], (vp["viewport"], name, {k: v for k, v in d.items() if k not in ("colours", "text", "vocab", "overflow")}))
                self.assertTrue(d["overflow"]["ok"], (vp["viewport"], name))
                self.assertEqual(d["vocab"]["unknown"], [], (vp["viewport"], name))
                self.assertEqual(d["colours"]["n_offenders"], 0, (vp["viewport"], name))
                self.assertEqual(d["text"]["hits"], [], (vp["viewport"], name))
            self.assertIn("console:FINISHED", vp["plates"]["pipeline"]["status_words"])
            self.assertIn("rq:finished", vp["plates"]["pipeline"]["status_words"])
            self.assertIn("meta:finished", vp["plates"]["pipeline"]["status_words"])
            self.assertIn("registry:not recorded", vp["plates"]["solvers"]["status_words"])
            if vm:
                self.assertIn("cad:ok", vp["plates"]["objects"]["status_words"])
                self.assertIn("run:done", vp["plates"]["evidence"]["status_words"])
                self.assertIn("case:FINISHED", vp["plates"]["evidence"]["status_words"])
                self.assertTrue(vp["plates"]["evidence"]["grade_then_sentence"])


if __name__ == "__main__":
    unittest.main()
