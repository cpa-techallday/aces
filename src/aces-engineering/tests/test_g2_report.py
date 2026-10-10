"""B2 unit tests: golden.report (assemble, the verdict-line formats, report.md / .html / .json, provenance, charts,
empty states). The recorded run is the synthetic one of test_g2_evidence (test-input solves through the real
surrogate, optimiser, validation, evidence and report code). Run: python -m unittest discover -s tests -p "test_g2_report.py".
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_g2_compute as tc
import test_g2_evidence as te

from aces_engineering import golden
from aces_engineering.golden import evidence, report


class TestRecordedRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.env = mock.patch.dict(os.environ, {"SIMLAB_EVIDENCE_ROOT": str(base / "evidence-root"),
                                               "ACES_USER_CONFIG": str(base / "cfg"), "ACES_ETC": str(base / "etc")})
        cls.env.start()
        (base / "run").mkdir()
        cls.aces = te.FakeAces(base / "state")
        cls.rd, cls.eff, cls.val = te.build_run(base / "run", aces=cls.aces, charts=True)
        cls.doc = cls.rd.read_json("report.json")
        cls.md = Path(cls.rd.path("report.md")).read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.tmp.cleanup()

    def test_sections_and_files(self):
        titles = ["Key numbers", "Inputs and provenance", "Design-space voxelisation", "Compute used", "Generative sweep",
                  "Surrogate", "Optimisation", "Verification", "Independent check and device parity",
                  "Conforming second discretisation", "Rig", "Winner", "Evidence", "Environment",
                  "Limits and reproduction"]
        at = [self.md.index(f"## {n}. {title}") for n, title in enumerate(titles, start=1)]
        self.assertEqual(at, sorted(at))
        self.assertTrue(self.md.startswith(f"# aces golden: b2-test ({self.rd.run_id})"))
        self.assertIn(f"**{self.doc['verdict_line']}**", self.md)
        self.assertNotIn("\r", self.md)
        html = Path(self.rd.path("report.html")).read_text(encoding="utf-8")
        self.assertIn("<table>", html)
        self.assertEqual(self.doc["schema"], golden.SCHEMA["report"])
        for key in ("run_id", "verdict_line", "verdict", "outcome", "grade", "sections", "provenance"):
            self.assertIn(key, self.doc)
        for key in ("title", "summary", "key_numbers", "inputs", "voxel", "compute", "sweep", "surrogate", "optimise",
                    "verification", "check", "conforming", "rig", "winner", "evidence", "environment", "limits"):
            self.assertIn(key, self.doc["sections"])
        # this recorded run (golden 0.2.0 records: finalise writes a not-run conforming block, and no lane was used)
        # says so instead of inventing either
        conf = self.doc["sections"]["conforming"]
        self.assertFalse(conf["ran"])
        self.assertTrue(str(conf["reason"]).startswith("not run: "))
        self.assertEqual(conf["rows"], [])
        self.assertEqual(self.doc["sections"]["rig"]["status"], "not run: no rig lane was used (desktop only)")
        self.assertIn("| level | case | h (mm) | device | host |", self.md)
        self.assertIn("conforming tet10: not run (no conforming check)", self.doc["verdict_line"])
        self.assertIn("Mode `-`; status `-`; case `-` on -.", self.md)
        self.assertIn("no rig run (desktop only)", self.doc["sections"]["limits"]["not_verified"])
        for fig in ("sweep_scatter.png", "optimise_map.png", "check_levels.png"):
            p = Path(self.rd.path("figures", fig))
            self.assertTrue(p.is_file(), fig)
            self.assertGreater(p.stat().st_size, 10_000)
            self.assertIn(f"figures/{fig}", self.md)
        self.assertFalse(Path(self.rd.path("figures", "conforming.png")).exists())   # no conforming check ran
        self.assertIn("Material source: test input", self.md)
        self.assertIn(golden.sha256_file(self.rd.path("inputs", "spec.json")), self.md)

    def test_golden_01_validation_reads_not_run(self):
        """A validation.json written by golden 0.1 has no conforming block: section 10 says not run, never invents it."""
        val = {k: v for k, v in self.val.items() if k not in ("conforming", "rig")}
        sec = report._conforming_section(report._Reader(self.rd), report._Prov(), val, {})
        self.assertEqual(sec, {"status": "not run: this run's validation record has no conforming check "
                                         "(golden code before 0.2.0)"})

    def test_verdict_line_pass_format(self):
        line = self.doc["verdict_line"]
        pat = (r"^PASS · (preview-grade|verified-desktop) · b2-test: volfrac \S+ rmin_mm \S+ · mass \S+ kg · "
               r"vm_p99_MPa \S+ <= 60 · umax_mm \S+ <= 0\.035 · predicted within 10 % \(max \S+ % on \S+\) · "
               r"check 2 mm: .+ · bundle [0-9a-f]{12} simlab #1 · aces #1 · (stages )?\S+ s$")
        self.assertRegex(line, pat)
        # written before the run finishes, the wall time is the stage sum and is labelled so (never read as wall_s)
        basis = self.doc["sections"]["summary"]["wall_basis"]
        key = next(p["key"] for p in self.doc["provenance"] if p["pointer"] == "/sections/summary/wall_s")
        self.assertEqual(basis, "total" if key == "timings.total_s" else "stages")
        self.assertEqual(" · stages " in line, basis == "stages")
        led = self.rd.read_json("evidence/ledgers.json")
        self.assertIn(f"bundle {led['simlab']['sha256'][:12]} simlab #1", line)
        data = report.assemble(self.rd)
        self.assertNotIn("simlab #", report.verdict_line(data, evidence=False))
        self.assertTrue(report.verdict_line(data, evidence=False).startswith("PASS · "))

    def test_every_provenance_pointer_resolves(self):
        prov = self.doc["provenance"]
        self.assertGreater(len(prov), 50)
        pointers = {p["pointer"] for p in prov}
        for want in ("/sections/summary/mass_kg", "/sections/summary/tol", "/sections/summary/simlab_entry",
                     "/sections/summary/aces_entry", "/sections/summary/wall_s", "/sections/summary/check/h_mm"):
            self.assertIn(want, pointers)
        self.assertTrue(any(p.startswith("/sections/compute/rows/") for p in pointers))
        self.assertTrue(any(p.startswith("/sections/verification/rounds/0/rows/") for p in pointers))
        self.assertTrue(any(p.startswith("/sections/check/independent_check/levels/") for p in pointers))
        self.assertTrue(any(p.startswith("/sections/evidence/simlab/") for p in pointers))
        cache = {}
        for p in prov:
            have = evidence.resolve_pointer(self.doc, p["pointer"])
            src = cache.setdefault(p["source"], self.rd.read_json(p["source"]))
            self.assertEqual(have, evidence.resolve_key(src, p["key"]), p)
        ok, detail = evidence._check_report(self.rd)
        self.assertTrue(ok, detail)

    def test_a_drifted_number_fails_the_provenance_check(self):
        with tempfile.TemporaryDirectory() as td:
            import shutil

            dst = Path(td) / "copy"
            shutil.copytree(self.rd.root, dst)
            copy = tc.FakeRun(dst, run_id=self.rd.run_id)
            doc = copy.read_json("report.json")
            doc["sections"]["summary"]["mass_kg"] *= 1.01
            copy.write_json("report.json", doc)
            ok, detail = evidence._check_report(copy)
            self.assertFalse(ok)
            self.assertIn("/sections/summary/mass_kg", detail)


class TestFormats(unittest.TestCase):
    def _data(self, summary: dict) -> dict:
        return {"sections": {"summary": dict({"name": "b2-test", "bundle_sha256": "a" * 64, "simlab_entry": 3,
                                              "aces_entry": 7, "wall_s": 61.234}, **summary)}}

    def test_fail_infeasible_thin_lines(self):
        fail = self._data({"verdict": "FAIL", "rounds": 3, "reasons": ["vm_p99_MPa predicted 52 vs solved 61: 17.3 % "
                                                                     "exceeds the 10 % tolerance"]})
        self.assertEqual(report.verdict_line(fail), "FAIL · b2-test: vm_p99_MPa predicted 52 vs solved 61: 17.3 % "
                         "exceeds the 10 % tolerance after 3 round(s) · bundle aaaaaaaaaaaa simlab #3 · aces #7 · 61.23 s")
        inf = self._data({"verdict": "INFEASIBLE", "rounds": 3})
        self.assertEqual(report.verdict_line(inf), "INFEASIBLE · b2-test: no candidate predicted feasible with the "
                         "margins (round 3) · bundle aaaaaaaaaaaa simlab #3 · aces #7 · 61.23 s")
        thin = self._data({"verdict": "THIN", "thin": {"n_ok": 2, "n": 10, "need": 8}})
        self.assertEqual(report.verdict_line(thin), "THIN · b2-test: 2 of 10 designs finished (need 8) · bundle "
                         "aaaaaaaaaaaa simlab #3 · aces #7 · 61.23 s")
        self.assertEqual(report.verdict_line(thin, evidence=False), "THIN · b2-test: 2 of 10 designs finished (need 8)"
                         " · 61.23 s")
        no_aces = self._data({"verdict": "THIN", "thin": {"n_ok": 2, "n": 10, "need": 8}, "aces_entry": None,
                              "aces_error": "aces: refused (test input)"})
        self.assertIn("aces not recorded (aces: refused (test input))", report.verdict_line(no_aces))

    def test_wall_sum_matches_its_provenance_bit_for_bit(self):
        # eight stages of 0.1 s: plain addition gives 0.7999999999999999, the compensated sum() of Python 3.12+ gives 0.8
        stages = [{"name": n, "status": "done", "wall_s": 0.1} for n in report.WALL_STAGES]
        wall, key = report._wall({"stages": stages})
        self.assertEqual(key, "sum:stages.wall_s:" + ",".join(report.WALL_STAGES))
        self.assertTrue(evidence._same(wall, evidence.resolve_key({"stages": stages}, key)))

    def test_wall_time_label(self):
        thin = self._data({"verdict": "THIN", "thin": {"n_ok": 2, "n": 10, "need": 8}, "wall_basis": "stages"})
        self.assertTrue(report.verdict_line(thin).endswith(" · aces #7 · stages 61.23 s"))
        thin["sections"]["summary"]["wall_basis"] = "total"
        self.assertTrue(report.verdict_line(thin).endswith(" · aces #7 · 61.23 s"))

    def test_g4_formatting(self):
        self.assertEqual(report.g4(0.0123456), "0.01235")
        self.assertEqual(report.g4(60.0), "60")
        self.assertEqual(report.g4(None), "-")
        self.assertEqual(report.g4(float("nan")), "-")


class TestEmptyStates(unittest.TestCase):
    def test_usage_error_run_reports_what_did_not_run(self):
        with tempfile.TemporaryDirectory() as td:
            run = tc.FakeRun(td)
            run.write_json("run.json", {"schema": golden.SCHEMA["run"], "run_id": run.run_id, "project": run.project,
                                        "status": "error", "outcome": "usage", "demo": None,
                                        "message": "spec error at material.source: field required (test input)",
                                        "stages": [{"name": "compute", "status": "done", "wall_s": 1.5, "note": ""},
                                                   {"name": "inputs", "status": "failed", "wall_s": 0.1,
                                                    "note": "spec error at material.source (test input)"},
                                                   {"name": "sweep", "status": "skipped", "wall_s": None,
                                                    "note": "not run: the run stopped at inputs"}],
                                        "timings": {}})
            data = report.assemble(run)
            s = data["sections"]
            self.assertTrue(s["sweep"]["status"].startswith("not run: the run stopped at inputs"))
            self.assertTrue(s["inputs"]["status"].startswith("not run: spec error at material.source"))
            self.assertTrue(s["verification"]["status"].startswith("not run"))
            self.assertTrue(s["evidence"]["status"].startswith("not run"))
            self.assertTrue(s["winner"]["status"].startswith("not run"))
            self.assertTrue(s["conforming"]["status"].startswith("not run"))
            self.assertTrue(s["rig"]["status"].startswith("not run"))
            self.assertIsNone(data["verdict"])
            self.assertTrue(data["verdict_line"].startswith("USAGE · golden: spec error at material.source"))
            paths = report.write(run, data)
            md = Path(paths["md"]).read_text(encoding="utf-8")
            self.assertIn("not run: the run stopped at inputs", md)
            self.assertTrue(Path(paths["html"]).is_file())
            self.assertEqual(run.read_json("report.json")["provenance"],
                             [{"pointer": "/sections/summary/wall_s", "source": "run.json",
                               "key": "sum:stages.wall_s:compute,inputs"}])
            self.assertEqual(report.charts(run), {})


if __name__ == "__main__":
    unittest.main()
