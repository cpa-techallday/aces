"""B3 unit tests: golden.report's rig and conforming sections (build spec section 16): S["rig"], S["conforming"], the
check table's host column, the verdict-line fragments, figures/conforming.png, and that every provenance pointer of
the new numbers resolves. The run directory is a hand-written test fixture in the record shapes of section 14
(validation.json with the independent_check host/rig fields and the conforming and rig blocks, rig/session.json, each
rig case's rig.json, the conforming case's conforming.json); every number in it is a test input, not a measurement.
Run: python -m unittest discover -s tests -p "test_g4_report_rig.py"."""
from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

import test_g2_compute as tc

from aces_engineering import golden
from aces_engineering.golden import conforming, evidence, report

GPU_NAME = "fixture GPU (test input)"
IMAGE_ID = "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def eff() -> dict:
    """A resolved spec (test input) with two constraints and a 4 mm solve."""
    e = tc.eff_fixture(check_levels=[{"level": 1, "h_mm": 2.0, "gpu_only": False},
                                     {"level": 2, "h_mm": 1.0, "gpu_only": True}])
    e["name"] = "b3-test"
    e["description"] = "test fixture"
    e["material"] = {"name": "test", "E_MPa": 70000.0, "nu": 0.33, "rho_kg_mm3": 2.67e-6, "source": "test input"}
    return e


def check_rows(coarse: dict, fine: dict) -> list[dict]:
    rows = []
    for o, role, op, lim in (("mass_kg", "objective", None, None), ("vm_p99_MPa", "constraint", "<=", 60.0),
                             ("umax_mm", "constraint", "<=", 0.035), ("compliance_Nmm", "reported", None, None)):
        gap = (fine[o] - coarse[o]) / abs(coarse[o])
        rows.append({"output": o, "role": role, "coarse": coarse[o], "fine": fine[o], "rel_gap": gap, "tol": 0.1,
                     "agree": abs(gap) <= 0.1, "op": op, "limit": lim,
                     "meets_fine": (fine[o] <= lim) if op else None})
    return rows


VERIFY = {"mass_kg": 0.2, "vm_p99_MPa": 50.0, "umax_mm": 0.030, "compliance_Nmm": 800.0, "vm_p95_MPa": 40.0,
          "vm_max_MPa": 70.0, "mesh_volume_mm3": 74906.36704119849}                       # test input (v001)
FINE1 = {"mass_kg": 0.2, "vm_p99_MPa": 52.0, "umax_mm": 0.0315, "compliance_Nmm": 840.0}  # test input (x001, 2 mm)
FINE2 = {"mass_kg": 0.2, "vm_p99_MPa": 53.0, "umax_mm": 0.0320, "compliance_Nmm": 860.0, "vm_p95_MPa": 42.0,
         "vm_max_MPa": 120.0, "mesh_volume_mm3": 74906.36704119849}                       # test input (x002, 1 mm)
TET10 = {"mass_kg": 0.2, "mesh_volume_mm3": 74906.36704119849, "compliance_Nmm": 820.0, "umax_mm": 0.0310,
         "vm_max_MPa": 100.0, "vm_p99_MPa": 51.0, "vm_p95_MPa": 41.0, "vm_max_ip_MPa": 150.0}  # test input (m001)


def rig_case(gid: str, rc: str, exp: str, gpu, wait_s: float, run_s: float, *, state="result", design_status="ok"):
    return {"schema": golden.SCHEMA.get("rig-case", "aces-golden-rig-case/1"), "case_id": gid, "lane": "sim/pool",
            "url": "http://rig.test:8022/mcp", "experiment_id": exp, "rig_case_id": rc, "job_id": f"{exp}--{rc}--run--0a1b2c",
            "job_status": "finished" if state == "result" else "failed", "state": state, "design_status": design_status,
            "reason": None if state == "result" else "fixture: not delivered", "queue": "sim" if gpu is not None else "cpu",
            "gpu": gpu, "owner": "fixture", "gpu_name": GPU_NAME if gpu is not None else None,
            "gpu_uuid": "GPU-fixture-0001" if gpu is not None else None, "image": "aces-golden:0.1",
            "image_id": IMAGE_ID, "image_digest": IMAGE_ID + " (test fixture)", "submitted_utc": "2026-10-10T00:00:00Z",
            "started_utc": "2026-10-10T00:00:05Z", "finished_utc": "2026-10-10T00:01:00Z", "wait_s": wait_s,
            "run_s": run_s, "pulled_utc": "2026-10-10T00:01:10Z", "files": [], "records": {}, "code": {},
            "device_forced": None}


def conforming_record(status="ok", metrics=None) -> dict:
    rec = conforming._empty_record("m001", "rig", 16, 3000.0, 3_000_000)
    rec.update(status=status, message="test fixture", finished_utc="2026-10-10T00:02:00Z", wall_s=60.0,
               wall_s_mesh=10.0, wall_s_solve=45.0, wall_s_post=5.0, host="fixture-host")
    rec["source"].update(stl="v001/part.stl", sha256="0" * 64, faces=1000, vertices=502, volume_mm3=74906.36704119849,
                         area_mm2=12000.0, watertight=True, shells=1, cavities=0, bbox_mm=[0, 0, 0, 100, 100, 40])
    rec["mesh"].update(version="4.15.2", route="discrete", algorithm_3d=1, size_mm=2.0, nodes=60000, elements=37000,
                       dof=180000, dof_estimate=346441, volume_mm3=74906.36704119849, volume_rel_err=2e-16,
                       min_sicn=0.3, p01_sicn=0.55, attempts=[])
    rec["bcs"].update(window_mm=2.0, supports=[
        {"name": "foot-1", "box": [76, 64, 0, 88, 76, 0], "dofs": [0, 1, 2], "mode": "surface", "axis": "z",
         "plane_mm": 0.0, "faces": 70, "nodes": 190, "area_mm2": 146.0, "projected_mm2": 145.5, "footprint_mm2": 144.0,
         "coverage": 1.0104166666666667, "plane_offset_mm": 0.3, "overlap_nodes": 0}],
        loads=[{"name": "cup-down", "box": [40, 40, 40, 60, 60, 40], "force": [0.0, 0.0, -30000.0], "axis": "z",
                "mode": "traction", "plane_mm": 40.0, "faces": 200, "nodes": 339, "area_mm2": 400.5,
                "projected_mm2": 400.4, "footprint_mm2": 400.0, "coverage": 1.001, "plane_offset_mm": 0.02,
                "traction_MPa": [0.0, 0.0, -74.9]}])
    rec["solver"].update(version="2.23", path="/usr/local/bin/ccx", sha256="1" * 64,
                         linear_solver="Factoring the system of equations using the symmetric spooles solver",
                         card="*STATIC, SOLVER=SPOOLES", rc=0, wall_s=45.0)
    rec["equilibrium"].update(applied_N=[0.0, 0.0, -30000.0], reaction_N=[0.0, 0.0, 29999.999], rel_residual=3e-8,
                              per_support_N={"foot-1": [0.0, 0.0, 29999.999]}, rf_totals_N={"foot-1": [0.0, 0.0, 29999.999]})
    rec["metrics"] = dict(metrics if metrics is not None else TET10)
    rec["stress"].update(measure="test fixture", exclude_boxes=[], elements_total=37000, elements_included=34000,
                         volume_included_mm3=66000.0)
    rec["material"] = {"E_MPa": 70000.0, "nu": 0.33, "rho_kg_mm3": 2.67e-6}
    return rec


def build_run(root: Path, *, conf_status="ok", tet10=None, lane=True, conf_mode="auto") -> tc.FakeRun:
    """The fixture run directory (test input): a PASS with a rig level-1 and level-2 check, a conforming case m001 on
    the rig, rig/session.json and the rig cases' rig.json."""
    run = tc.FakeRun(root, run_id="20261010T000000Z-b3test")
    e = eff()
    run.write_json("inputs/spec.effective.json", e)
    run.write_json("run.json", {"schema": golden.SCHEMA["run"], "run_id": run.run_id, "project": run.project,
                                "status": "running", "stages": [{"name": "compute", "status": "done", "wall_s": 1.0}],
                                "timings": {}})
    run.write_json("v001/results.json", dict(VERIFY))
    run.write_json("x001/results.json", dict(FINE1))
    run.write_json("x002/results.json", dict(FINE2))
    rows1, rows2 = check_rows(VERIFY, FINE1), check_rows(VERIFY, FINE2)
    rig1 = {"lane": "sim/pool", "experiment": "golden-b3test-check", "rig_case": "c000", "job_id": "j0", "gpu": 2,
            "gpu_name": GPU_NAME, "gpu_uuid": "GPU-fixture-0001", "image": "aces-golden:0.1", "image_id": IMAGE_ID,
            "solver": "golden-check", "solver_validation": "pass", "validation_image_id": IMAGE_ID, "wait_s": 4.0,
            "run_s": 40.0} if lane else None
    levels = [{"level": 1, "case_id": "x001", "h_mm": 2.0, "device": "rig" if lane else "cpu", "gating": True,
               "est_s": None, "budget_s": 60.0, "ran": True, "reason": "level 1 ran", "rows": rows1, "passed": True,
               "host": "rig" if lane else "desktop", "fea_solver": "pcg", "rig": rig1, "rig_attempt": None},
              {"level": 2, "case_id": "x002", "h_mm": 1.0, "device": "rig" if lane else "cpu", "gating": False,
               "est_s": None, "budget_s": 60.0, "ran": True, "reason": "level 2 ran", "rows": rows2, "passed": True,
               "host": "rig" if lane else "desktop", "fea_solver": "mg", "rig": None, "rig_attempt": None}]
    rec = conforming_record(conf_status, tet10)
    run.write_json("m001/conforming.json", rec)
    rows, passed, reasons = conforming.compare(FINE2, rec, e)
    ran = conf_status not in conforming.NOT_RUN
    conf = {"ran": ran, "passed": passed, "status": conf_status, "case_id": "m001", "host": "rig" if lane else "desktop",
            "mode": conf_mode, "reason": reasons[0] if reasons else "the conforming check agrees", "h_mm": 2.0,
            "source_case": "v001", "reference": {"case_id": "x002", "h_mm": 1.0, "host": "rig" if lane else "desktop"},
            "record": "m001/conforming.json",
            "mesh": {k: rec["mesh"][k] for k in ("route", "elements", "nodes", "dof", "min_sicn", "volume_rel_err")},
            "solver": {"name": "CalculiX ccx", "version": "2.23", "sha256": "1" * 64, "threads": 16,
                       "linear_solver": rec["solver"]["linear_solver"]},
            "bands": conforming.bands(e), "rows": rows,
            "constraints_met": all(r["meets_conforming"] is True for r in rows if r["role"] == "constraint"),
            "rig": {"experiment": "golden-b3test-conf", "rig_case": "c000", "job_id": "j2", "image": "aces-golden:0.1",
                    "image_id": IMAGE_ID, "solver_validation": "pass", "validation_image_id": IMAGE_ID} if lane else None,
            "rig_attempt": None}
    val = {"schema": golden.SCHEMA["validation"], "tol": 0.1, "objective": {"output": "mass_kg", "sense": "min"},
           "constraints": [{"output": "vm_p99_MPa", "op": "<=", "limit": 60.0},
                           {"output": "umax_mm", "op": "<=", "limit": 0.035}],
           "resolution_mm": {"generator": 4.0, "field": 2.0, "solve": 4.0, "check": [2.0, 1.0]},
           "rounds": [], "outcome": "pass", "passed": True, "final_round": 1, "final_case": "v001",
           "winner_case": "v001", "winner_params": {"volfrac": 0.25},
           "independent_check": {"ran": True, "reason": "level 1 ran", "passed": True, "levels": levels},
           "device_parity": [], "grade": "rig-verified" if lane else "verified-desktop",
           "grade_reasons": ["test fixture"], "verdict": "PASS", "verdict_reasons": [], "scope": "test fixture",
           "conforming": conf, "rig": None}
    if lane:
        val["rig"] = {"lane": "sim/pool", "url": "http://rig.test:8022/mcp", "scope": {"sweep": False, "check": True,
                                                                                     "conforming": True},
                      "token_source": "not set", "principal": "anonymous", "policy": "full",
                      "server": {"name": "simlab", "version": "0.4.0-fixture"}, "session": "rig/session.json",
                      "session_sha256": "2" * 64, "solvers": {},
                      "experiments": [{"id": "golden-b3test-check", "stage": "check", "solver": "golden-check",
                                       "n_cases": 2, "n_finished": 2, "n_failed": 0},
                                      {"id": "golden-b3test-conf", "stage": "conf", "solver": "ccx-check",
                                       "n_cases": 1, "n_finished": 1, "n_failed": 0}],
                      "code_identity": {"ok": True}, "integrity_error": None}
        run.write_json("rig/session.json", {
            "schema": "aces-golden-rig-session/1", "lane": "sim/pool", "lane_source": "cli",
            "url": "http://rig.test:8022/mcp", "token_source": "not set", "principal": "anonymous", "policy": "full",
            "server": {"name": "simlab", "version": "0.4.0-fixture"}, "budget_s": 1800.0,
            "scope": {"sweep": False, "check": True, "conforming": True}, "uploads": [],
            "experiments": [{"id": "golden-b3test-check", "stage": "check", "kind": "mesh", "solver": "golden-check",
                             "wall_s": 95.5, "cases": {"c000": "x001", "c001": "x002"},
                             "jobs": {"c000": "j0", "c001": "j1"}},
                            {"id": "golden-b3test-conf", "stage": "conf", "kind": "conforming", "solver": "ccx-check",
                             "wall_s": 70.25, "cases": {"c000": "m001"}, "jobs": {"c000": "j2"}}],
            "events": [], "cancelled": [], "integrity_error": None})
        run.write_json("x001/rig.json", rig_case("x001", "c000", "golden-b3test-check", 2, 4.0, 40.0))
        run.write_json("x002/rig.json", rig_case("x002", "c001", "golden-b3test-check", 3, 6.5, 48.0))
        run.write_json("m001/rig.json", rig_case("m001", "c000", "golden-b3test-conf", None, 2.0, 60.0))
        run.write_json("compute.json", {"rig": {"probe": {"notes": ["test fixture: manifest 12 s old"]}}})
    run.write_json("validation.json", val)
    return run


class TestRigAndConforming(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.rd = build_run(Path(cls.tmp.name) / "run")
        cls.charts = report.charts(cls.rd)
        cls.data = report.assemble(cls.rd)
        cls.paths = report.write(cls.rd, cls.data)
        cls.md = Path(cls.paths["md"]).read_text(encoding="utf-8")
        cls.doc = cls.rd.read_json("report.json")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_rig_section(self):
        rg = self.doc["sections"]["rig"]
        self.assertNotIn("status", rg)
        self.assertEqual((rg["lane"], rg["url"], rg["principal"], rg["server_version"], rg["token_source"]),
                         ("sim/pool", "http://rig.test:8022/mcp", "anonymous", "0.4.0-fixture", "not set"))
        self.assertEqual(rg["notes"], ["test fixture: manifest 12 s old"])
        e0, e1 = rg["experiments"]
        self.assertEqual((e0["stage"], e0["solver"], e0["image_id12"], e0["cases"], e0["finished"], e0["gpus"],
                          e0["wall_s"], e0["max_wait_s"]),
                         ("check", "golden-check", "0123456789ab", 2, 2, [2, 3], 95.5, 6.5))
        self.assertEqual((e1["stage"], e1["gpus"], e1["max_wait_s"]), ("conf", [], 2.0))
        self.assertEqual([(c["case_id"], c["rig_case"], c["gpu"], c["wait_s"], c["run_s"], c["state"]) for c in rg["cases"]],
                         [("x001", "c000", 2, 4.0, 40.0, "result"), ("x002", "c001", 3, 6.5, 48.0, "result"),
                          ("m001", "c000", None, 2.0, 60.0, "result")])
        self.assertEqual((rg["cancelled"], rg["integrity_error"], rg["code_identity_ok"]), ([], None, True))
        self.assertIn("## 11. Rig", self.md)
        self.assertIn("| experiment | stage | solver | image id | cases | finished | GPUs | wall s | max queue wait s |",
                      self.md)
        self.assertIn("| golden-b3test-check | check | golden-check | 0123456789ab | 2 | 2 | 2, 3 | 95.5 | 6.5 |", self.md)
        self.assertIn("| x002 | c001 | golden-b3test-check--c001--run--0a1b2c | 3 | 6.5 | 48 | result |", self.md)
        self.assertIn("Cancelled: none. Integrity error: none.", self.md)

    def test_conforming_section(self):
        cf = self.doc["sections"]["conforming"]
        self.assertTrue(cf["ran"])
        self.assertTrue(cf["passed"])
        self.assertEqual(cf["check_status"], "ok")
        self.assertEqual((cf["mesh"]["route"], cf["mesh"]["elements"], cf["mesh"]["dof"]), ("discrete", 37000, 180000))
        self.assertEqual(cf["bcs"]["supports"][0]["coverage"], 1.0104166666666667)
        self.assertEqual(cf["solver"]["linear_solver"], "Factoring the system of equations using the symmetric spooles solver")
        self.assertEqual(cf["equilibrium"]["rel_residual"], 3e-8)
        self.assertEqual([r["output"] for r in cf["rows"]],
                         ["mass_kg", "vm_p99_MPa", "umax_mm", "compliance_Nmm", "vm_max_MPa", "vm_p95_MPa",
                          "mesh_volume_mm3"])
        self.assertEqual(cf["chart"], "figures/conforming.png")
        self.assertIn("## 10. Conforming second discretisation", self.md)
        self.assertIn("| kind | name | mode | faces | nodes | area (mm2) | projected (mm2) | footprint (mm2) | coverage | "
                      "plane offset (mm) |", self.md)
        self.assertIn("| output | role | voxel (x002, 1 mm) | tet10 | gap | band | agree | limit | meets on tet10 |",
                      self.md)
        self.assertIn("| vm_p99_MPa | constraint | 53 | 51 | -3.774 % | 15 % | yes | <= 60 | yes |", self.md)
        self.assertIn("Verdict: **agrees within its bands and meets every constraint**.", self.md)
        self.assertIn("(Factoring the system of equations using the symmetric spooles solver), 16 threads", self.md)
        self.assertIn("![conforming against voxel](figures/conforming.png)", self.md)

    def test_check_table_has_host(self):
        self.assertIn("| level | case | h (mm) | device | host | gating |", self.md)
        self.assertIn("| 1 | x001 | 2 | rig | rig | yes |", self.md)

    def test_verdict_fragments(self):
        line = self.doc["verdict_line"]
        self.assertIn(" · check 2 mm on the rig (GPU 2): max gap +5 % on umax_mm, constraints met · conforming tet10 "
                      "2 mm (ccx 2.23, rig): max gap -4.651 % on compliance_Nmm, constraints met", line)
        self.assertTrue(line.startswith("PASS · rig-verified · b3-test: volfrac 0.25 · "))

    def test_every_provenance_pointer_resolves(self):
        prov = self.doc["provenance"]
        pointers = {p["pointer"] for p in prov}
        for want in ("/sections/summary/check/gpu", "/sections/summary/conforming/h_mm",
                     "/sections/summary/conforming/max_gap/value", "/sections/conforming/h_mm",
                     "/sections/conforming/mesh/elements", "/sections/conforming/bcs/supports/0/coverage",
                     "/sections/conforming/equilibrium/rel_residual", "/sections/conforming/rows/1/rel_gap",
                     "/sections/conforming/bands/stress", "/sections/conforming/wall/wall_s_solve",
                     "/sections/conforming/metrics/vm_max_ip_MPa", "/sections/rig/budget_s",
                     "/sections/rig/experiments/0/wall_s", "/sections/rig/experiments/0/cases",
                     "/sections/rig/experiments/0/finished", "/sections/rig/experiments/0/gpus/1",
                     "/sections/rig/experiments/0/max_wait_s", "/sections/rig/cases/1/gpu", "/sections/rig/cases/2/run_s"):
            self.assertIn(want, pointers)
        ok, detail = evidence._check_report(self.rd)
        self.assertTrue(ok, detail)
        # every number placed in the two new sections is covered by a pointer
        for name in ("rig", "conforming"):
            missing = []

            def walk(v, ptr):
                if isinstance(v, dict):
                    for k, x in v.items():
                        walk(x, f"{ptr}/{k}")
                elif isinstance(v, list):
                    for i, x in enumerate(v):
                        walk(x, f"{ptr}/{i}")
                elif report._num(v) and ptr not in pointers:
                    missing.append(ptr)
            walk(self.doc["sections"][name], f"/sections/{name}")
            self.assertEqual(missing, [], name)

    def test_a_drifted_rig_number_fails_the_provenance_check(self):
        doc = copy.deepcopy(self.doc)
        doc["sections"]["rig"]["cases"][0]["wait_s"] = 4.5
        with tempfile.TemporaryDirectory() as td:
            run = build_run(Path(td) / "run")
            run.write_json("report.json", doc)
            ok, detail = evidence._check_report(run)
            self.assertFalse(ok)
            self.assertIn("/sections/rig/cases/0/wait_s", detail)

    def test_chart(self):
        self.assertIn("conforming", self.charts)
        p = Path(self.rd.path("figures", "conforming.png"))
        self.assertTrue(p.is_file())
        self.assertGreater(p.stat().st_size, 10_000)


class TestOtherOutcomes(unittest.TestCase):
    def outcome(self, **kw):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        run = build_run(Path(td.name) / "run", **kw)
        charts = report.charts(run)
        data = report.assemble(run)
        report.write(run, data)
        return run, data, charts

    def test_not_run(self):
        run, data, charts = self.outcome(conf_status="ccx-not-found", tet10={k: None for k in TET10})
        line = data["verdict_line"]
        self.assertIn(" · conforming tet10: not run (ccx-not-found: test fixture) · ", line)
        self.assertNotIn("conforming", charts)
        cf = data["sections"]["conforming"]
        self.assertFalse(cf["ran"])
        md = Path(run.path("report.md")).read_text(encoding="utf-8")
        self.assertIn("Mode `auto`; status `ccx-not-found`; case `m001` on the rig.", md)
        self.assertTrue(any(s.startswith("no conforming-mesh second discretisation") for s in
                            data["sections"]["limits"]["not_verified"]))
        ok, detail = evidence._check_report(run)
        self.assertTrue(ok, detail)

    def test_constraint_violated_on_the_tet10_mesh(self):
        bad = dict(TET10, vm_p99_MPa=61.0)
        run, data, _ = self.outcome(tet10=bad)
        self.assertIn("conforming tet10 2 mm (ccx 2.23, rig): max gap +15.09 % on vm_p99_MPa, constraints not met",
                      data["verdict_line"])
        md = Path(run.path("report.md")).read_text(encoding="utf-8")
        self.assertIn("Verdict: **violates a constraint on the conforming mesh (the grade is capped at preview-grade)**.",
                      md)

    def test_failed_solve(self):
        run, data, charts = self.outcome(conf_status="ccx-failed", tet10={k: None for k in TET10})
        self.assertIn("conforming tet10 2 mm (ccx 2.23, rig): failed (ccx-failed)", data["verdict_line"])
        self.assertNotIn("conforming", charts)
        md = Path(run.path("report.md")).read_text(encoding="utf-8")
        self.assertIn("Verdict: **failed: ccx-failed (test fixture)**.", md)

    def test_desktop_run_without_a_lane(self):
        run, data, _ = self.outcome(lane=False)
        self.assertEqual(data["sections"]["rig"]["status"], "not run: no rig lane was used (desktop only)")
        line = data["verdict_line"]
        self.assertIn(" · check 2 mm: max gap +5 % on umax_mm, constraints met · conforming tet10 2 mm (ccx 2.23, "
                      "desktop): max gap -4.651 % on compliance_Nmm, constraints met", line)
        self.assertNotIn("on the rig", line)
        ok, detail = evidence._check_report(run)
        self.assertTrue(ok, detail)


if __name__ == "__main__":
    unittest.main()
