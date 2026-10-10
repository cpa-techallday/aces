"""B1 unit tests: case jobs end to end on a tiny design space (a 40 x 20 x 16 mm box at 4 mm, 6 SIMP iterations):
every record written, evidence in the simlab shape, verify post files, a finer check and a same-mask cross-check,
and designs that fail without raising. stdlib unittest, a local fake run directory and golden.NullProgress."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (_HERE.parent / "lib", _HERE.parent / "vendor"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.append(str(_p))

from aces_engineering import golden  # noqa: E402
from aces_engineering.golden import case as gcase, domain, spec  # noqa: E402

BUILDER = (
    "from build123d import Align, Box\n\n\n"
    "def box(x=40.0, y=20.0, z=16.0):\n"
    "    return Box(x, y, z, align=(Align.MIN, Align.MIN, Align.MIN))\n"
)


def tiny_spec() -> dict:
    """A test input: the design space is the whole 40 x 20 x 16 mm envelope, fixed at x = 0, loaded at x = 40."""
    return {
        "schema": "aces-golden-spec/1", "name": "test-case",
        "domain": {"source": "build123d", "build123d": {"ref": "builder.py:box"}, "origin_mm": [0, 0, 0],
                   "envelope_mm": [40, 20, 16]},
        "supports": [{"name": "wall", "box": [0, 0, 0, 0, 20, 16]}],
        "loads": [{"name": "tip", "box": [40, 4, 4, 40, 16, 12], "force": [0, 0, -500], "axis": "x"}],
        "material": {"name": "test", "E_MPa": 70000, "nu": 0.33, "rho_kg_mm3": 2.7e-6, "allowable_vm_MPa": 100,
                     "source": "test input"},
        "constraints": [{"output": "vm_p99_MPa", "op": "<=", "limit": "allowable"}],
        "generator": {"variables": {"volfrac": {"lo": 0.6, "hi": 0.8}, "rmin_mm": {"lo": 6.0, "hi": 8.0}}, "iterations": 6},
        "resolution": {"gen_mm": 4.0, "fea_mm": 4.0, "check_factor": 0.5},
        "sweep": {"n": 2, "seed": 1},
    }


class FakeRun:
    """A 15-line stand-in for golden.rundir.RunDir (RunDirLike) for B1's unit tests."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.run_id, self.project, self.exp_id = "test-run", "test", "golden-test-test-run"
        self._cases = []

    def path(self, *parts):
        return self.root.joinpath(*parts)

    def rel(self, p):
        return Path(p).resolve().relative_to(self.root.resolve()).as_posix()

    def case_dir(self, cid):
        (self.root / cid).mkdir(parents=True, exist_ok=True)
        return self.root / cid

    def write_json(self, rel, obj):
        return golden.dump_json(self.root / rel, obj)

    def read_json(self, rel, default=None):
        return golden.read_json(self.root / rel, default)

    def write_text(self, rel, text):
        return golden.write_text(self.root / rel, text)

    def register_case(self, case):
        self._cases.append(dict(case))
        golden.dump_json(self.root / "cases.json", self._cases)
        return golden.dump_json(self.case_dir(case["case_id"]) / "case.json", case)

    def cases(self):
        return list(self._cases)


def make_run(root: Path):
    """Spec, domain and experiment for a fake run under root (shared by the case, sweep and render tests)."""
    from simlab.spec import Experiment

    (root / "spec").mkdir(parents=True, exist_ok=True)
    (root / "spec" / "builder.py").write_text(BUILDER, encoding="utf-8")
    raw = tiny_spec()
    eff = spec.resolve(raw, base_dir=root / "spec", overrides={}, gpu_usable=False)
    run = FakeRun(root / "run")
    golden.dump_json(run.path("inputs", "spec.json"), raw)
    domain.build(eff, run.path("inputs"), root / "spec")
    golden.dump_json(run.path("inputs", "spec.effective.json"), eff)
    dom = domain.prepare(eff, run.path("inputs", "domain.step"), run.path("domain"))
    exp = Experiment(id=run.exp_id, project=run.project, owner="test", physics="structures", solver="step-structures",
                     spec_path=str(run.path("inputs", "spec.effective.json")),
                     spec_sha256=golden.sha256_file(run.path("inputs", "spec.effective.json")))
    return run, eff, dom, exp


def case_dict(case_id, role, eff, **kw):
    d = eff["derived"]
    c = {"case_id": case_id, "role": role, "h_gen_mm": d["gen_mm"], "h_fea_mm": d["fea_mm"], "device": "cpu",
         "backend": "adapter", "source_case": None, "reuse_mask": False, "round": None, "level": None, "seed": d["seed"]}
    c.update(kw)
    return c


RESULT_KEYS = {"schema", "case_id", "role", "round", "level", "source_case", "design", "mass_kg", "mass_voxel_kg",
               "mesh_volume_mm3", "voxel_volume_mm3", "solid_fraction", "compliance_Nmm", "umax_mm", "vm_max_MPa",
               "vm_p99_MPa", "vm_p95_MPa", "pcg_iters", "pcg_relres", "converged", "elements", "dof", "elem_mm",
               "n_fixed_dofs", "device", "dtype", "backend", "lane", "grade", "generator", "skin", "voxel", "checks",
               "stress", "wall_s_generate", "wall_s_skin", "wall_s_voxel", "wall_s_solve", "wall_s_post", "wall_s_total",
               "artifacts", "fea_solver", "fea_solver_reason"}


class Cases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        cls.rd, cls.eff, cls.dom, cls.exp = make_run(Path(cls.td.name))
        p = golden.NullProgress()
        cls.sweep_oc, cls.verify_oc = gcase.run_cases(
            cls.rd, cls.eff, cls.dom, cls.exp,
            [case_dict("d000", "sweep", cls.eff, volfrac=0.62, rmin_mm=6.5),
             case_dict("v001", "verify", cls.eff, volfrac=0.66, rmin_mm=7.0, round=1)],
            pool=None, progress=p, kind="design")
        cls.mesh_ocs = gcase.run_cases(
            cls.rd, cls.eff, cls.dom, cls.exp,
            [case_dict("x001", "check", cls.eff, h_gen_mm=None, h_fea_mm=2.0, source_case="v001", level=1),
             case_dict("p001", "parity", cls.eff, h_gen_mm=None, source_case="v001", reuse_mask=True)],
            pool=None, progress=p, kind="mesh")

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def test_sweep_design_writes_every_record(self):
        oc = self.sweep_oc
        self.assertEqual((oc["status"], oc["design_status"]), ("FINISHED", "ok"), oc["error"])
        d = self.rd.path("d000")
        for name in ("case.json", "progress.json", "generator/density.npy", "generator/report.json", "generator/slices.png",
                     "part.stl", "skin.json", "mask.npy", "mesh.json", "solve.json", "fields.npz", "thumb.png",
                     "results.json", "status.json", "evidence.json"):
            self.assertTrue((d / name).is_file(), name)
        self.assertFalse((d / "generator" / "design.stl").exists())
        self.assertFalse((d / "vm_slices.png").exists())
        res = golden.read_json(d / "results.json")
        self.assertEqual(set(res), RESULT_KEYS)
        self.assertEqual((res["fea_solver"], res["fea_solver_reason"]), ("pcg", "pcg requested"))
        self.assertEqual((res["schema"], res["grade"], res["lane"], res["backend"]),
                         ("aces-golden-case/1", "preview-grade", "desktop-cpu", "step-structures.stage_solve"))
        self.assertEqual(res["design"], {"volfrac": 0.62, "rmin_mm": 6.5})
        self.assertNotIn(str(d / "progress.json"), res["artifacts"])
        self.assertIn(str((d / "part.stl").resolve()), res["artifacts"])
        st = golden.read_json(d / "status.json")
        self.assertEqual(set(st), {"status", "design_status", "mlflow_run_id", "wall_s", "error", "stages", "diagnostics"})
        self.assertEqual(st["stages"], ["generate", "skin", "voxel", "solve", "thumb"])
        self.assertEqual(golden.read_json(d / "skin.json")["schema"], "aces-golden-skin/1")

    def test_evidence_is_a_simlab_manifest(self):
        ev = golden.read_json(self.rd.path("d000", "evidence.json"))
        self.assertEqual(ev["schema"], "simlab-evidence/1")
        self.assertEqual(set(ev["inputs_sha256"]), {"spec.json", "spec.effective.json", "domain.step", "case.json", "part.stl"})
        self.assertEqual(ev["inputs_sha256"]["part.stl"], golden.sha256_file(self.rd.path("d000", "part.stl")))
        self.assertEqual(ev["status"], "FINISHED")
        self.assertEqual([s["stage"] for s in ev["stages"]], ["generate", "skin", "voxel", "solve", "thumb"])
        self.assertEqual(ev["results"]["vm_p99_MPa"], golden.read_json(self.rd.path("d000", "results.json"))["vm_p99_MPa"])
        self.assertNotIn(b"\r\n", self.rd.path("d000", "evidence.json").read_bytes())
        cases = golden.read_json(self.rd.path("cases.json"))
        self.assertEqual([c["case_id"] for c in cases], ["d000", "v001", "x001", "p001"])

    def test_verify_gets_post_files(self):
        self.assertEqual(self.verify_oc["status"], "FINISHED", self.verify_oc["error"])
        d = self.rd.path("v001")
        for name in ("vm_slices.png", "disp_slices.png", "fields.vtk", "case.md"):
            self.assertTrue((d / name).is_file(), name)
        self.assertFalse((d / "thumb.png").exists())

    def test_check_and_same_mask_cross_check(self):
        x, p = self.mesh_ocs
        self.assertEqual(x["status"], "FINISHED", x["error"])
        xr = golden.read_json(self.rd.path("x001", "results.json"))
        self.assertEqual((xr["elem_mm"], xr["elements"], xr["design"], xr["generator"]), (2.0, 20 * 10 * 8, None, None))
        self.assertEqual(xr["mass_kg"], self.verify_oc["results"]["mass_kg"])  # same part.stl, same mesh volume
        self.assertTrue(self.rd.path("x001", "case.md").is_file())
        self.assertEqual(p["status"], "FINISHED", p["error"])
        for k in golden.METRICS:
            self.assertEqual(p["results"][k], self.verify_oc["results"][k], k)
        ev = golden.read_json(self.rd.path("p001", "evidence.json"))
        self.assertEqual(ev["inputs_sha256"]["part.stl"], golden.sha256_file(self.rd.path("v001", "part.stl")))

    def test_impossible_design_fails_without_raising(self):
        oc = gcase.run_design_case(gcase.make_job(self.rd, self.eff, self.dom,
                                                  case_dict("d901", "sweep", self.eff, volfrac=0.0005, rmin_mm=6.0)))
        self.assertEqual(oc["status"], "FAILED")
        self.assertIn(oc["design_status"], golden.DESIGN_STATUSES)
        self.assertNotEqual(oc["design_status"], "ok")
        self.assertIsNone(oc["results"])
        broken = dict(self.dom)
        broken.pop("passive_grid")
        oc = gcase.run_design_case(gcase.make_job(self.rd, self.eff, broken,
                                                  case_dict("d902", "sweep", self.eff, volfrac=0.62, rmin_mm=6.5)))
        self.assertEqual((oc["status"], oc["design_status"]), ("FAILED", "error"))
        self.assertIn("Traceback", oc["error"])
        gcase.write_records(self.rd, case_dict("d902", "sweep", self.eff, volfrac=0.62, rmin_mm=6.5), oc, self.exp)
        self.assertFalse(self.rd.path("d902", "results.json").exists())
        self.assertEqual(golden.read_json(self.rd.path("d902", "evidence.json"))["status"], "FAILED")

    def test_progress_rows(self):
        rows = []

        class Bar(golden.NullProgress):
            def done(self, row):
                rows.append(row)

        bar = Bar()
        gcase.run_cases(self.rd, self.eff, self.dom, self.exp,
                        [case_dict("d003", "sweep", self.eff, volfrac=0.64, rmin_mm=6.0)],
                        pool=None, progress=golden.NullProgress(), kind="design", bar=bar)
        self.assertEqual(set(rows[0]), {"case_id", "role", "status", "design_status", "params", "metrics", "wall_s", "device", "error"})
        self.assertEqual(set(rows[0]["metrics"]), {"mass_kg", "umax_mm", "vm_p99_MPa", "vm_max_MPa", "compliance_Nmm"})
        line = gcase.row_line(rows[0])
        self.assertTrue(line.startswith("d003  volfrac 0.64 rmin_mm 6 -> mass "), line)


if __name__ == "__main__":
    unittest.main()
