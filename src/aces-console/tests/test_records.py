"""records.py on a work store assembled from the committed real records (tests/support_b.build_store): the three
projects with their counts, the two record-only golden runs (cad-gold verified-desktop, golden-demo rig-verified), the
run detail with every piece and the honest absences of a partial copy, the file and events routes, the verify action
(exit 127 here, the real VM record through the fake aces-golden), the CAD run without run.json, the real pull bundle
and the real check_bundle.py, NOTES.md, the drawing record and the Onshape card. Nothing leaves the loopback."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

from aces import config as aces_config

from aces_console import records

from . import CAD_RECORDS, FIXTURES
from .support import ledger_entries
from .support_b import BUNDLE, CAD_PROJECT, CAD_RUN, DRAWING, GOLDEN_RUNS, ConsoleB, build_store, copy_golden_run, new_project

GOLD_RUN = GOLDEN_RUNS["cad-gold"]
DEMO_RUN = GOLDEN_RUNS["golden-demo"]
FAKE_GOLDEN = Path(__file__).resolve().parent / "fakes" / "fake_golden.py"


class RecordsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = ConsoleB(tag="records")
        cls.work = build_store("records")

    @classmethod
    def tearDownClass(cls):
        cls.c.close()

    # --- projects (3.8) --------------------------------------------------------------------------------------------------

    def test_projects_with_counts(self):
        self.c.app.hub.refresh("projects")
        status, _, body = self.c.get("/api/projects")
        self.assertEqual(status, 200)
        d = body["data"]
        self.assertEqual([p["name"] for p in d["projects"]], ["cad-gold", "cad-vm", "golden-demo"])
        self.assertIsNone(d["reason"])
        self.assertEqual(d["root"], str(self.work))
        by = {p["name"]: p for p in d["projects"]}
        self.assertEqual(by["cad-gold"]["class"], "sovereign")
        self.assertEqual(by["cad-gold"]["counts"], {"golden_runs": 1, "cad_runs": 0, "bundles": 0, "ledger_entries": 1})
        self.assertEqual(by["cad-gold"]["onshape_gate"], "refused for this class")
        self.assertEqual(by["cad-vm"]["counts"], {"golden_runs": 0, "cad_runs": 1, "bundles": 1, "ledger_entries": 1})
        self.assertEqual(by["golden-demo"]["class"], "personal")
        self.assertEqual(by["golden-demo"]["counts"], {"golden_runs": 1, "cad_runs": 0, "bundles": 0, "ledger_entries": 1})
        self.assertEqual(by["golden-demo"]["onshape_gate"], "allowed")
        self.assertEqual(by["golden-demo"]["class_source"], "aces.yaml")
        self.assertTrue(by["golden-demo"]["path"].endswith("aces.yaml"))
        status, _, body = self.c.get("/api/projects/cad-gold")
        self.assertEqual(status, 200)
        p = body["data"]
        self.assertEqual([e["kind"] for e in p["ledger"]], ["project.new"])
        self.assertEqual(p["ledger"][0]["payload"]["name"], "cad-gold")
        self.assertEqual([r["run_id"] for r in p["golden_runs"]], [GOLD_RUN])
        self.assertEqual((p["cad_runs"], p["bundles"]), ([], []))
        status, _, body = self.c.get("/api/projects/no-such-project")
        self.assertEqual((status, body["code"]), (404, "not-found"))
        self.assertTrue(body["error"].startswith("no project at "))
        status, _, body = self.c.get("/api/projects/..")
        self.assertEqual(status, 400)

    def test_projects_without_a_work_store(self):
        saved = aces_config.WORK
        try:
            aces_config.WORK = self.work / "no-such-store"
            okf, data, err = records.fetch_projects(self.c.app)
            self.assertTrue(okf)
            self.assertEqual(data["projects"], [])
            self.assertEqual(data["reason"], f"{aces_config.WORK} does not exist")
            self.assertEqual(records.golden_rows(None)["reason"], f"no golden runs under {aces_config.WORK}")
        finally:
            aces_config.WORK = saved

    # --- golden (3.9) -----------------------------------------------------------------------------------------------------

    def test_golden_runs_newest_first(self):
        self.c.app.hub.refresh("golden.runs")
        status, _, body = self.c.get("/api/golden/runs")
        self.assertEqual(status, 200)
        rows = [r for r in body["data"]["runs"] if r["project"] in ("cad-gold", "golden-demo")]
        self.assertEqual([(r["project"], r["run_id"]) for r in rows], [("cad-gold", GOLD_RUN), ("golden-demo", DEMO_RUN)])
        gold, demo = rows
        self.assertEqual((gold["status"], gold["verdict"], gold["grade"], gold["wall_s"], gold["has_result"], gold["n_stages"]),
                         ("done", "PASS", "verified-desktop", 49.509, True, 9))
        self.assertEqual((demo["status"], demo["verdict"], demo["grade"], demo["wall_s"], demo["exit_code"]),
                         ("done", "PASS", "rig-verified", 83.806, 0))
        self.assertTrue(all(r["status_known"] for r in rows))
        self.assertEqual(gold["notes"], ["no grade is a sign-off", "desktop grades are not rig results"])
        status, _, body = self.c.get("/api/golden/runs?project=golden-demo")
        self.assertEqual([r["run_id"] for r in body["data"]["runs"]], [DEMO_RUN])
        status, _, body = self.c.get("/api/golden/runs?project=cad-vm")
        self.assertEqual(body["data"]["runs"], [])
        self.assertEqual(body["data"]["reason"], f"no golden runs under {self.work / 'cad-vm' / 'golden'}")
        status, _, body = self.c.get("/api/golden/runs?project=../x")
        self.assertEqual(status, 400)

    def test_golden_run_detail(self):
        status, _, body = self.c.get(f"/api/golden/run/cad-gold/{GOLD_RUN}")
        self.assertEqual(status, 200)
        d = body["data"]
        self.assertEqual((d["status"], d["grade"], d["verdict"], d["n_stages"]), ("done", "verified-desktop", "PASS", 9))
        self.assertEqual([s["name"] for s in d["stages"]], ["compute", "inputs", "domain", "sweep", "loop", "check", "export", "evidence", "report"])
        self.assertEqual(d["run"]["timings"]["total_s"], 49.509)
        self.assertEqual(d["result_source"], "result.json")
        self.assertIsNone(d["result_absent_reason"])
        self.assertEqual(d["result"]["verdict"], "PASS")
        self.assertEqual(d["compute_used_rows"], 15)
        self.assertEqual(d["compute_used"]["schema"], "aces-golden-compute-used/1")
        self.assertEqual(d["provenance_n"], 306)
        self.assertEqual(d["report_sections"], 17)
        self.assertEqual(d["validation"]["schema"], "aces-golden-validation/1")
        self.assertEqual(d["sweep"]["n"], 10)
        self.assertIn("case_id", d["sweep"]["columns"])
        self.assertEqual(d["sweep"]["rows"][0]["case_id"], "d000")
        self.assertEqual(sorted(f["name"] for f in d["winner"]["files"]), ["density_slices.png", "disp_slices.png", "vm_slices.png", "winner.json"])
        self.assertEqual(d["winner"]["json"]["schema"], "aces-golden-winner/1")
        self.assertEqual(d["figures"], ["check_levels.png", "conforming.png", "optimise_map.png", "sweep_scatter.png"])
        self.assertEqual(d["evidence_ledgers"]["schema"], "aces-golden-ledgers/1")
        self.assertIsNone(d["candidate"])
        self.assertEqual(d["absent"], [])
        self.assertEqual(d["read_errors"], [])
        self.assertEqual(len(d["events_tail"]), 49)
        self.assertEqual(d["events_tail"][-1]["kind"], "result")
        self.assertEqual(d["report_html"], "report.html")
        self.assertIsNone(d["stl"], "winner.stl is one of the omitted binaries")
        self.assertEqual(len(d["case_dirs"]), 13)
        self.assertGreaterEqual(len(d["files"]), 170)
        self.assertEqual(d["notes"], ["no grade is a sign-off", "desktop grades are not rig results"])
        status, _, body = self.c.get(f"/api/golden/run/golden-demo/{DEMO_RUN}")
        self.assertEqual((status, body["data"]["grade"]), (200, "rig-verified"))
        self.assertIsNotNone(body["data"]["result"]["rig"])
        status, _, body = self.c.get(f"/api/golden/run/cad-gold/{DEMO_RUN}")
        self.assertEqual((status, body["code"]), (404, "not-found"))
        status, _, body = self.c.get("/api/golden/run/cad-gold/not-a-run")
        self.assertEqual(status, 400)

    def test_golden_partial_copy_reports_absences_not_errors(self):
        project = "cad-gold-partial-copy"
        new_project(project, "sovereign")
        run_dir = self.work / project / "golden" / GOLD_RUN
        run_dir.mkdir(parents=True)
        shutil.copyfile(FIXTURES / "golden" / "cad-gold" / "golden" / GOLD_RUN / "run.json", run_dir / "run.json")
        try:
            status, _, body = self.c.get(f"/api/golden/run/{project}/{GOLD_RUN}")
            self.assertEqual(status, 200)
            d = body["data"]
            self.assertEqual(d["result_source"], "run.json")
            self.assertEqual(d["result_absent_reason"], "result.json not written: this run ended with status done")
            self.assertEqual(d["result"]["run_id"], GOLD_RUN)
            for rel in ("result.json", "validation.json", "compute_used.json", "report.json", "sweep.csv", "events.jsonl", "evidence/",
                        "winner/", "figures/"):
                self.assertIn(rel, d["absent"], rel)
            self.assertEqual(d["read_errors"], [])
            self.assertIsNone(d["events_tail"])
            self.assertIsNone(d["report_html"])
            self.assertEqual(d["n_stages"], 9)
            status, _, body = self.c.get(f"/api/golden/events/{project}/{GOLD_RUN}")
            self.assertEqual((status, body["error"]), (404, "events.jsonl absent"))
            row = records.golden_rows(project)["runs"][0]
            self.assertEqual((row["has_result"], row["status"], row["unreadable"]), (False, "done", None))
        finally:
            shutil.rmtree(self.work / project, ignore_errors=True)
            self.c.app.hub.refresh("golden.runs")
            self.c.app.hub.refresh("projects")

    def test_golden_file_route(self):
        base = f"/api/golden/file/cad-gold/{GOLD_RUN}/"
        status, hdrs, data = self.c.request("GET", base + "report.html")
        self.assertEqual(status, 200)
        self.assertEqual(hdrs["content-type"], "text/html; charset=utf-8")
        self.assertEqual(hdrs["content-security-policy"], "sandbox")
        self.assertEqual(hdrs["x-content-type-options"], "nosniff")
        self.assertEqual(hdrs["content-disposition"], 'inline; filename="report.html"')
        self.assertEqual(hdrs["cache-control"], "private, max-age=60")
        etag = hdrs["etag"]
        self.assertEqual(etag, '"' + hashlib.sha256(data).hexdigest() + '"')
        status, hdrs, data = self.c.request("GET", base + "report.html", headers={"If-None-Match": etag})
        self.assertEqual((status, data), (304, b""))
        status, hdrs, data = self.c.request("GET", base + "evidence/bundle.json")
        self.assertEqual((status, hdrs["content-type"]), (200, "application/json"))
        self.assertEqual(json.loads(data)["schema"], "simlab-evidence-bundle/1")
        status, hdrs, _ = self.c.request("GET", base + "d000/thumb.png")
        self.assertEqual((status, hdrs["content-type"]), (200, "image/png"))
        status, hdrs, _ = self.c.request("GET", base + "manifest.sha256")
        self.assertEqual((status, hdrs["content-type"]), (200, "text/plain; charset=utf-8"))
        status, _, body = self.c.get(base + "../run.json")
        self.assertEqual(status, 400)
        status, _, body = self.c.get(base + ".hidden")
        self.assertEqual((status, body["code"]), (403, "refused"))
        status, _, body = self.c.get(base + "winner/keys.env")
        self.assertEqual(status, 403)
        self.assertTrue(body["error"].startswith("refusing to read "))
        status, _, body = self.c.get(base + "nope.txt")
        self.assertEqual((status, body["code"]), (404, "not-found"))
        status, _, body = self.c.get(base + "winner/winner.stl")
        self.assertEqual(status, 404, "the binary was omitted from the record-only copy")

    def test_golden_events(self):
        status, _, body = self.c.get(f"/api/golden/events/cad-gold/{GOLD_RUN}?after=0")
        self.assertEqual(status, 200)
        d = body["data"]
        self.assertEqual((d["n_total"], len(d["lines"]), d["eof"], d["status"], d["next_after"]), (49, 49, True, "done", 49))
        self.assertEqual(set(d["lines"][0]) >= {"t", "ts", "kind", "stage", "msg"}, True, d["lines"][0])
        self.assertEqual(d["lines"][-1]["kind"], "result")
        status, _, body = self.c.get(f"/api/golden/events/cad-gold/{GOLD_RUN}?after=45")
        self.assertEqual((len(body["data"]["lines"]), body["data"]["after"]), (4, 45))
        status, _, body = self.c.get(f"/api/golden/events/cad-gold/{GOLD_RUN}?after=abc")
        self.assertEqual(status, 400)

    def test_golden_verify(self):
        path = f"/api/golden/verify/cad-gold/{GOLD_RUN}"
        before = len(ledger_entries())
        status, _, body = self.c.post(path, {}, human=False)
        self.assertEqual((status, body["code"]), (403, "refused"))
        with mock.patch.dict(os.environ):
            os.environ.pop("ACES_GOLDEN_CMD", None)
            status, _, body = self.c.post(path, {})
        self.assertEqual((status, body["code"]), (503, "not-installed"), body)
        self.assertTrue(body["error"].startswith("aces-engineering not installed (exit 127)"), body["error"])
        with mock.patch.dict(os.environ, {"ACES_GOLDEN_CMD": json.dumps([sys.executable, str(FAKE_GOLDEN)])}):
            status, _, body = self.c.post(path, {})
        self.assertEqual(status, 200, body)
        d = body["data"]
        self.assertTrue(d["ok"])
        self.assertEqual((d["exit"], len(d["checks"]), d["counts"]), (0, 13, {"ok": 11, "failed": 0, "not_run": 2}))
        by = {c["name"]: c for c in d["checks"]}
        self.assertEqual((by["rig"]["ok"], by["rig"]["detail"]), (None, "not run: no rig work in this run"))
        self.assertEqual((by["resolve"]["ok"], by["resolve"]["detail"]), (None, "not run (pass --resolve to re-solve the winner on this CPU)"))
        self.assertEqual(by["aces-ledger"]["detail"], "aces ledger verifies (10 entries); entry 10 is golden.run for this bundle")
        self.assertEqual(d["command"], ["aces", "golden", "verify", str(self.work / "cad-gold" / "golden" / GOLD_RUN), "--json", "--no-model"])
        self.assertFalse(d["ledgered"])
        self.assertEqual(len(ledger_entries()), before, "a verify is a read")
        status, _, body = self.c.post(f"/api/golden/verify/cad-gold/{DEMO_RUN}", {})
        self.assertEqual(status, 404)
        st = records._state(self.c.app)
        with st.lock:
            st.verifying.add(str(self.work / "cad-gold" / "golden" / GOLD_RUN))
        try:
            status, _, body = self.c.post(path, {})
            self.assertEqual((status, body["code"]), (409, "busy"))
        finally:
            with st.lock:
                st.verifying.clear()

    # --- CAD (3.10) --------------------------------------------------------------------------------------------------------

    def test_cad_runs(self):
        status, _, body = self.c.get(f"/api/cad/runs?project={CAD_PROJECT}")
        self.assertEqual(status, 200)
        d = body["data"]
        self.assertEqual(d["counts"], {"total": 1, "unreadable": 1})
        self.assertEqual(d["runs"][0]["run_id"], CAD_RUN)
        self.assertEqual((d["runs"][0]["status"], d["runs"][0]["unreadable"]), ("unreadable", "run.json missing"))
        self.assertIsNone(d["note"])
        status, _, body = self.c.get("/api/cad/runs?project=golden-demo")
        self.assertEqual((body["data"]["runs"], body["data"]["note"]), ([], "no CAD store in golden-demo"))
        status, _, body = self.c.get("/api/cad/runs?project=bad%20name")
        self.assertEqual(status, 400)
        status, _, body = self.c.get("/api/cad/runs")
        self.assertEqual(status, 400)
        rec = json.loads((CAD_RECORDS / "records" / "run-plate.json").read_text(encoding="utf-8"))
        row = records.summarize_record(rec)
        self.assertEqual((row["status"], row["name"], row["kind"], row["valid"], row["volume_mm3"]), ("ok", "plate", "solid", True, 11607.300918301276))

    def test_cad_run_detail_and_files(self):
        status, _, body = self.c.get(f"/api/cad/run/{CAD_PROJECT}/{CAD_RUN}")
        self.assertEqual(status, 200)
        d = body["data"]
        self.assertIsNone(d["record"])
        self.assertEqual(d["unreadable"], "run.json missing")
        self.assertEqual(sorted(f["rel"] for f in d["files"]),
                         [f"drawings/{DRAWING}.dxf", f"drawings/{DRAWING}.json", f"drawings/{DRAWING}.png", f"drawings/{DRAWING}.svg"])
        svg = next(f for f in d["files"] if f["rel"].endswith(".svg"))
        self.assertEqual(svg["sha256"], hashlib.sha256((CAD_RECORDS / "drawing" / f"{DRAWING}.svg").read_bytes()).hexdigest())
        self.assertEqual((svg["kind"], svg["content_type"]), ("svg", "image/svg+xml"))
        self.assertEqual([x["id"] for x in d["drawings"]], [DRAWING])
        self.assertEqual(d["subdirs"], ["drawings"])
        base = f"/api/cad/file/{CAD_PROJECT}/{CAD_RUN}/drawings/{DRAWING}"
        status, hdrs, data = self.c.request("GET", base + ".svg")
        self.assertEqual((status, hdrs["content-type"], hdrs["content-security-policy"]), (200, "image/svg+xml", "sandbox"))
        self.assertEqual(data, (CAD_RECORDS / "drawing" / f"{DRAWING}.svg").read_bytes())
        status, hdrs, _ = self.c.request("GET", base + ".png")
        self.assertEqual((status, hdrs["content-type"]), (200, "image/png"))
        status, hdrs, _ = self.c.request("GET", base + ".dxf")
        self.assertEqual((status, hdrs["content-type"]), (200, "image/vnd.dxf"))
        status, _, body = self.c.get(f"/api/cad/run/{CAD_PROJECT}/20261010T000000Z-0000")
        self.assertEqual((status, body["error"]), (404, f"no run 20261010T000000Z-0000 in {CAD_PROJECT}"))
        status, _, body = self.c.get(f"/api/cad/run/{CAD_PROJECT}/latest")
        self.assertEqual(status, 400)

    def test_bundles_and_the_real_check(self):
        status, _, body = self.c.get(f"/api/cad/bundles?project={CAD_PROJECT}")
        self.assertEqual(status, 200)
        d = body["data"]
        self.assertEqual(d["n"], 1)
        b = d["bundles"][0]
        self.assertEqual(b["name"], BUNDLE)
        self.assertEqual(b["has"], {"source": True, "outcome": True, "parameters": True, "part_step": True, "part_stl": True,
                                    "part_3mf": True, "source_py": True})
        o = b["outcome_summary"]
        self.assertEqual((o["ledger_written"], o["ledger_n"], o["ledger_kind"], o["cad_source_recorded"], o["cad_source_ref"]),
                         (True, 6, "cad.pull", True, "cad/local/20261010T101736Z-plate/part.step"))
        self.assertEqual(b["source_summary"]["run_id"], CAD_RUN)
        self.assertEqual(b["source_summary"]["geometry_sha256"], "806c9be7a86c8ae60308ee75d9a910f371d5f5aae6fe6517fa5ce6aa5fd1742f")
        status, _, body = self.c.get("/api/cad/bundles?project=golden-demo")
        self.assertEqual((body["data"]["bundles"], body["data"]["note"]), ([], "no bundles in golden-demo"))
        status, hdrs, data = self.c.request("GET", f"/api/cad/bundle/{CAD_PROJECT}/{BUNDLE}/source.json")
        self.assertEqual((status, hdrs["content-type"]), (200, "application/json"))
        self.assertEqual(json.loads(data)["schema"], "aces.cad.local.source/1")
        status, hdrs, _ = self.c.request("GET", f"/api/cad/bundle/{CAD_PROJECT}/{BUNDLE}/part.step")
        self.assertEqual((status, hdrs["content-type"]), (200, "application/step"))
        status, _, body = self.c.get(f"/api/cad/bundle/{CAD_PROJECT}/20261010T000000Z-nope/source.json")
        self.assertEqual(status, 404)
        before = len(ledger_entries())
        status, _, body = self.c.post(f"/api/cad/bundle-check/{CAD_PROJECT}/{BUNDLE}", {})
        self.assertEqual(status, 200, body)
        d = body["data"]
        self.assertEqual((d["exit"], d["all_match"], d["name"]), (0, True, BUNDLE))
        self.assertIn(d["checker"]["source"], ("installed", "source-tree"))
        self.assertIn("ok   part.step  806c9be7a86c  18903 bytes", d["lines"])
        self.assertTrue(d["lines"][-1].endswith(": all files match"))
        self.assertFalse(d["ledgered"])
        self.assertEqual(len(ledger_entries()), before)
        status, _, body = self.c.post(f"/api/cad/bundle-check/{CAD_PROJECT}/20261010T000000Z-nope", {})
        self.assertEqual(status, 404)
        with mock.patch.object(records, "CHECK_BUNDLE_INSTALLED", Path("/nonexistent/check_bundle.py")), \
                mock.patch.object(records, "CHECK_BUNDLE_SOURCE", Path("/nonexistent/src/check_bundle.py")):
            status, _, body = self.c.post(f"/api/cad/bundle-check/{CAD_PROJECT}/{BUNDLE}", {})
        self.assertEqual((status, body["code"]), (503, "not-installed"))
        self.assertTrue(body["error"].startswith("aces-cad not installed"))

    def test_notes(self):
        status, _, body = self.c.get(f"/api/cad/notes/{CAD_PROJECT}")
        self.assertEqual(status, 200)
        d = body["data"]
        self.assertEqual((d["n"], d["truncated"], d["note"]), (1, False, None))
        e = d["entries"][0]
        self.assertEqual((e["ts"], e["tags"], e["run_id"]), ("2026-10-10T10:32:20Z", ["build123d", "plate", "hole"], "20261010T103142Z-09a4"))
        self.assertTrue(e["body"].startswith("For a simple centered through hole in a build123d plate"))
        self.assertTrue(d["preamble"].startswith("# CAD notes for cad-vm"))
        status, _, body = self.c.get("/api/cad/notes/golden-demo")
        self.assertEqual((body["data"]["entries"], body["data"]["note"]), ([], "no NOTES.md in golden-demo"))
        pre, entries = records.parse_notes("# head\n\n## 2026-10-10T10:00:00Z\n\nbody only\n\n## 2026-10-10T11:00:00Z · run 20261010T103142Z-09a4\n\nx\n")
        self.assertEqual([(e["ts"], e["tags"], e["run_id"], e["body"]) for e in entries],
                         [("2026-10-10T10:00:00Z", [], None, "body only"), ("2026-10-10T11:00:00Z", [], "20261010T103142Z-09a4", "x")])

    def test_drawings(self):
        status, _, body = self.c.get(f"/api/cad/drawings/{CAD_PROJECT}")
        self.assertEqual(status, 200)
        d = body["data"]
        self.assertEqual(d["n"], 1)
        x = d["drawings"][0]
        self.assertEqual((x["id"], x["run_id"], x["schema"]), (DRAWING, CAD_RUN, "aces.cad.drawing/1"))
        self.assertEqual(x["views"], ["front", "top", "right", "iso"])
        self.assertEqual((x["n_dimensions"], x["hole_table"], x["projection"], x["units"]), (6, None, "third-angle", "mm"))
        self.assertEqual(x["files"], {ext: f"drawings/{DRAWING}.{ext}" for ext in ("dxf", "svg", "png", "json")})
        self.assertEqual(x["file_route"], f"/api/cad/file/{CAD_PROJECT}/{CAD_RUN}/")
        status, _, body = self.c.get("/api/cad/drawings/golden-demo")
        self.assertEqual((body["data"]["drawings"], body["data"]["note"]), ([], "no CAD store in golden-demo"))

    def test_onshape_card(self):
        status, _, body = self.c.get(f"/api/cad/onshape/{CAD_PROJECT}")
        self.assertEqual(status, 200)
        d = body["data"]
        self.assertEqual((d["connected"], d["project_class"], d["onshape_gate"]), (False, "sovereign", "refused for this class"))
        self.assertEqual(d["note"], "this console never reads Onshape keys")
        self.assertFalse(d["local"]["present"])
        status, _, body = self.c.get("/api/cad/onshape/golden-demo")
        self.assertEqual((body["data"]["project_class"], body["data"]["onshape_gate"]), ("personal", "allowed"))
        status, _, body = self.c.get("/api/cad/onshape/no-such-project")
        self.assertEqual((status, body["data"]["project_class"], body["data"]["onshape_gate"]), (200, None, "refused for this class"))
        self.assertTrue(body["data"]["class_error"].startswith("no project at "))

    def test_golden_fixture_manifest(self):
        m = json.loads((FIXTURES / "golden" / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual({r["run_id"] for r in m["runs"]}, {GOLD_RUN, DEMO_RUN})
        for r in m["runs"]:
            run = FIXTURES / r["dir"]
            listed = {}
            for line in (run / "manifest.sha256").read_text(encoding="utf-8").splitlines():
                h, _, rel = line.partition(" *")
                listed[rel.strip()] = h.strip()
            present = {p.relative_to(run).as_posix(): p for p in run.rglob("*") if p.is_file()}
            self.assertEqual(len(present), r["files_present"])
            matching = sum(1 for rel, p in present.items() if rel in listed and hashlib.sha256(p.read_bytes()).hexdigest() == listed[rel])
            self.assertEqual(matching, r["files_matching_manifest"])
            self.assertEqual(sorted(set(listed) - set(present)), r["omitted"])
            self.assertTrue(all(Path(x).suffix in (".npz", ".npy", ".stl", ".joblib", ".vtk", ".step", ".gz", ".sta", ".cvg", ".png", ".yaml")
                                for x in r["omitted"]), r["omitted"])
            self.assertEqual(hashlib.sha256((run / "manifest.sha256").read_bytes()).hexdigest(), r["manifest_sha256_file"])
        ledger_row = m["files"][0]
        self.assertEqual(hashlib.sha256((FIXTURES / ledger_row["file"]).read_bytes()).hexdigest(), ledger_row["sha256"])
        self.assertEqual(ledger_row["entries"], 4)


if __name__ == "__main__":
    unittest.main()
