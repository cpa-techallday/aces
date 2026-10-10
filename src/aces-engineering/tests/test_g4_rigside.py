"""B2 tests: golden.rigside, the body of the rig solvers (section 8.2 of the rig build spec), run in this process the way
the simlab worker runs it, with ACES_GOLDEN_FORCE_DEVICE=cpu.

The GC-1 bar of section 20.2 (10 x 2 x 2 mm at h 1 mm, symmetry supports, 100 N x traction, E 200000, nu 0.3, rho
7.85e-6; test input) goes through rigside.run as a golden-check case and must meet its closed forms within 1e-6
relative: tip u_x = F L / (A E), umax at the free corner (with the lateral contraction), vm = F / A, compliance F u,
mass = volume x rho. Then the outcome.json, files.json and code_digest keys; an uploaded STL that does not match the
desktop's sha256 (and a path that leaves the spec store) gives a rig-integrity outcome and raises; the conforming kind
without ccx gives NOT_RUN (skipped, with the reason, while golden.conforming is not in this tree); --selfcheck.
Run: python -m unittest discover -s tests -p "test_g4_rigside.py"."""
from __future__ import annotations

import contextlib
import io
import json
import math
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fake_simlab

from aces_engineering import golden
from aces_engineering.golden import rigside, spec as spec_mod

E, NU, RHO, F, L, B = 200000.0, 0.3, 7.85e-6, 100.0, 10.0, 2.0
SIGMA = F / (B * B)                      # 25 MPa
TIP_UX = SIGMA * L / E                   # 1.25e-3 mm
LATERAL = NU * SIGMA * B / E             # 7.5e-5 mm at y = 2 (and z = 2)
UMAX = math.sqrt(TIP_UX ** 2 + 2 * LATERAL ** 2)
GC1 = {  # test input: the GC-1 bar
    "schema": "aces-golden-spec/1", "name": "gc-1",
    "domain": {"source": "step", "step_file": "unused.step", "origin_mm": [0, 0, 0], "envelope_mm": [L, B, B]},
    "supports": [{"name": "sym-x", "box": [0, 0, 0, 0, B, B], "dofs": [0]},
                 {"name": "sym-y", "box": [0, 0, 0, L, 0, B], "dofs": [1]},
                 {"name": "sym-z", "box": [0, 0, 0, L, B, 0], "dofs": [2]}],
    "loads": [{"name": "pull", "box": [L, 0, 0, L, B, B], "force": [F, 0, 0], "axis": "x"}],
    "material": {"name": "gc-1 steel", "E_MPa": E, "nu": NU, "rho_kg_mm3": RHO, "source": "test input"},
    "constraints": [{"output": "umax_mm", "op": "<=", "limit": 1.0}],
    "generator": {"variables": {"volfrac": {"lo": 0.3, "hi": 0.5}, "rmin_mm": {"lo": 1.5, "hi": 2.0}}},
    "resolution": {"gen_mm": 1.0, "fea_mm": 1.0, "check_factor": 0.5},
    "solver": {"dtype": "float64", "tol": 1e-12, "maxiter": 20000},
}
ENV = {"ACES_GOLDEN_FORCE_DEVICE": "cpu", "SIMLAB_IMAGE": "aces-golden:0.1", "SIMLAB_IMAGE_DIGEST": fake_simlab.FAKE_IMAGE}


def box_stl(path: Path, lx: float, ly: float, lz: float) -> None:
    import trimesh

    m = trimesh.creation.box(extents=[lx, ly, lz])
    m.apply_translation([lx / 2, ly / 2, lz / 2])
    m.export(str(path))


class _Store:
    """A spec store laid out like the rig's (cases/uploaded/<principal>/golden/<run>/<file>) with one experiment."""

    def __init__(self, root: Path, *, kind="mesh", solver="golden-check", gcase=None, stl_case="b001", run_id="gc1"):
        self.root = root
        self.store = root / "store"
        d = self.store / "golden" / run_id
        d.mkdir(parents=True, exist_ok=True)
        eff = spec_mod.resolve(GC1, base_dir=root, overrides={}, gpu_usable=False)
        golden.dump_json(d / "spec.effective.json", eff)
        golden.dump_json(d / "domain.json", {"stress_exclude": [], "stress_exclude_effective_mm": 0.0})
        box_stl(d / f"{stl_case}.part.stl", L, B, B)
        files = {}
        for key, name in (("eff", "spec.effective.json"), ("domain", "domain.json"), ("stl", f"{stl_case}.part.stl")):
            p = d / name
            files[key] = {"path": f"golden/{run_id}/{name}", "sha256": golden.sha256_file(p), "bytes": p.stat().st_size}
        files["stl"]["case_id"] = stl_case
        self.files = files
        self.gcase = gcase or {"case_id": "x001", "role": "check", "h_gen_mm": None, "h_fea_mm": 1.0, "device": "rig",
                               "backend": "adapter", "source_case": stl_case, "reuse_mask": False, "round": None,
                               "level": 1, "seed": 7, "host": "rig", "lane": "sim/test", "fea_solver": "pcg"}
        self.doc = {"id": f"golden-{run_id}-check", "project": "gc", "owner": "test", "physics": "structures",
                    "solver": solver, "description": "GC-1 bar (test input)",
                    "geometry": {"source": "mesh", "ref": files["stl"]["path"]},
                    "params": {"golden": {"schema": "aces-golden-rig/1", "kind": kind, "stage": "check",
                                          "run_id": run_id, "project": "gc", "golden_version": golden.VERSION,
                                          "golden_compute_sha256": "0" * 64, "desktop_host": "test", "lane": "sim/test",
                                          "files": files}},
                    "sweep": {"golden_case": [self.gcase]}, "outputs": [], "evidence": True,
                    "resources": {"vram_gb": 1.0, "cpu_threads": 4, "wall_h": 0.5, "precision": "float64"}}
        self.write()

    def write(self) -> None:
        from aces_engineering.golden.rig import spec_text

        (self.store / f"{self.doc['id']}.yaml").write_text(spec_text(self.doc), encoding="utf-8")

    def spec(self):
        from simlab.spec import load

        return load(str(self.store / f"{self.doc['id']}.yaml"))

    def run(self, solver="golden-check", extra_env=None):
        spec = self.spec()
        case = spec.cases()[0]
        run_dir = self.root / "queue" / spec.id / case["case_id"]
        run_dir.mkdir(parents=True, exist_ok=True)
        with mock.patch.dict(os.environ, {**ENV, **(extra_env or {})}):
            return case, run_dir, rigside.run(case, spec, str(run_dir), solver)


class _Guarded(unittest.TestCase):
    def tearDown(self):
        self.assertEqual(fake_simlab.BLOCKED, [])


class GC1Bar(_Guarded):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="g4-rigside-"))
        cls.st = _Store(cls.tmp)
        cls.case, cls.run_dir, cls.res = cls.st.run()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def rel(self, got, want):
        self.assertLessEqual(abs(got - want) / abs(want), 1e-6, (got, want))

    def test_closed_forms(self):
        import numpy as np

        r = self.res
        self.rel(r["compliance_Nmm"], F * TIP_UX)          # 0.125
        self.rel(r["umax_mm"], UMAX)                       # 1.2544919e-3
        self.rel(r["vm_max_MPa"], SIGMA)                   # 25
        self.rel(r["vm_p99_MPa"], SIGMA)
        self.rel(r["mass_kg"], L * B * B * RHO)            # 3.14e-4
        u = np.load(self.run_dir / "golden" / "x001" / "fields.npz")["u_mm"].reshape(-1, 3).astype(np.float64)
        nx, ny, nz = int(L), int(B), int(B)
        tip = [i + (nx + 1) * (j + (ny + 1) * k) for k in range(nz + 1) for j in range(ny + 1) for i in (nx,)]
        self.rel(float(u[tip, 0].mean()), TIP_UX)          # mean over the x = 10 nodes
        self.assertEqual((r["fea_solver"], r["lane"], r["device"]), ("pcg", "rig-gpu", "cpu/numpy"))
        self.assertTrue(r["converged"])

    def test_outcome_files_and_code(self):
        out = json.loads((self.run_dir / "outcome.json").read_text(encoding="utf-8"))
        self.assertEqual(out["schema"], rigside.OUTCOME_SCHEMA)
        for k in ("case_id", "role", "status", "design_status", "error", "results", "diagnostics", "started_utc",
                  "finished_utc", "wall_s", "stages", "rig"):
            self.assertIn(k, out)
        self.assertEqual((out["case_id"], out["status"], out["design_status"]), ("x001", "FINISHED", "ok"))
        rig = out["rig"]
        self.assertEqual(set(rig), {"experiment", "rig_case", "golden_case", "solver", "image", "image_digest",
                                    "device_forced", "code", "desktop_golden_compute_sha256"})
        self.assertEqual((rig["experiment"], rig["rig_case"], rig["golden_case"], rig["solver"]),
                         ("golden-gc1-check", "c000", "x001", "golden-check"))
        self.assertEqual((rig["image"], rig["image_digest"], rig["device_forced"]),
                         ("aces-golden:0.1", fake_simlab.FAKE_IMAGE, "cpu"))
        code = rig["code"]
        self.assertEqual(set(code), {"golden_compute_sha256", "modules", "simlab_from", "gendesign_from",
                                     "aces_engineering_from", "python"})
        from aces_engineering.golden.evidence import compute_digest

        self.assertEqual(code["golden_compute_sha256"], compute_digest()["golden_compute_sha256"])
        self.assertTrue(code["modules"])
        self.assertTrue(all(k.split("/")[0] in ("simlab", "gendesign") and len(v) == 64
                            for k, v in code["modules"].items()))
        self.assertIn("gendesign/fea3d.py", code["modules"])
        self.assertEqual(self.res["rig"], rig)
        fd = json.loads((self.run_dir / "files.json").read_text(encoding="utf-8"))
        self.assertEqual({k: fd[k] for k in ("schema", "experiment_id", "rig_case_id", "golden_case_id", "root")},
                         {"schema": rigside.FILES_SCHEMA, "experiment_id": "golden-gc1-check", "rig_case_id": "c000",
                          "golden_case_id": "x001", "root": "golden/x001"})
        paths = [f["path"] for f in fd["files"]]
        self.assertEqual(paths, sorted(paths))
        self.assertIn("golden/x001/fields.npz", paths)
        self.assertNotIn("golden/x001/progress.json", paths)
        for f in fd["files"]:
            p = self.run_dir / f["path"]
            self.assertEqual((f["bytes"], f["sha256"]), (p.stat().st_size, golden.sha256_file(p)), f["path"])
        self.assertTrue(all(str(a).startswith(str(self.run_dir / "golden" / "x001")) for a in self.res["artifacts"]))


class Refusals(_Guarded):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="g4-rigside-"))

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_input_mismatch_is_rig_integrity_and_raises(self):
        st = _Store(self.tmp)
        stl = st.store / "golden" / "gc1" / "b001.part.stl"
        with open(stl, "ab") as fh:
            fh.write(b"\nfixture: rewritten after the upload\n")
        with self.assertRaises(rigside.InputError):
            st.run()
        rd = self.tmp / "queue" / "golden-gc1-check" / "c000"
        out = json.loads((rd / "outcome.json").read_text(encoding="utf-8"))
        self.assertEqual((out["status"], out["design_status"]), ("FAILED", "rig-integrity"))
        self.assertTrue(out["error"].startswith("input integrity: golden/gc1/b001.part.stl: sha256 "), out["error"])
        self.assertIn("sent by the desktop", out["error"])
        self.assertIn(st.files["stl"]["sha256"], out["error"])
        self.assertTrue((rd / "files.json").is_file())

    def test_a_path_outside_the_store_is_refused(self):
        st = _Store(self.tmp)
        outside = self.tmp / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        st.doc["params"]["golden"]["files"]["domain"] = {"path": "../outside.json",
                                                         "sha256": golden.sha256_file(outside), "bytes": 2}
        st.write()
        with self.assertRaises(rigside.InputError) as cm:
            st.run()
        self.assertIn("resolves outside the spec store", str(cm.exception))

    def test_requests_that_are_not_golden_cases(self):
        st = _Store(self.tmp)
        spec = st.spec()
        case = spec.cases()[0]
        rd = self.tmp / "rd"
        with self.assertRaises(rigside.InputError):
            rigside.run(case, spec, str(rd), "golden-case")      # a mesh case sent to the design solver
        with self.assertRaises(rigside.InputError):
            rigside.run(dict(case, golden_case=None), spec, str(rd), "golden-check")
        with self.assertRaises(rigside.InputError):
            rigside.run(case, spec, str(rd), "step-structures")

    def test_conforming_without_ccx_is_not_run(self):
        try:
            from aces_engineering.golden import conforming  # noqa: F401
        except ImportError as exc:
            self.skipTest(f"golden.conforming (B3) is not in this tree ({exc}); the integrator runs this after merging")
        gcase = {"case_id": "m001", "role": "conforming", "source_case": "b001", "level": 1, "h_mm": 1.0,
                 "device": "rig", "host": "rig", "lane": "sim/test", "threads": 2, "max_dof": 3000000,
                 "timeout_s": 60.0, "seed": 7}
        st = _Store(self.tmp, kind="conforming", solver="ccx-check", gcase=gcase)
        with self.assertRaises(RuntimeError):
            st.run("ccx-check", extra_env={"ACES_CCX": str(self.tmp / "no-ccx-here")})
        rd = self.tmp / "queue" / "golden-gc1-check" / "c000"
        out = json.loads((rd / "outcome.json").read_text(encoding="utf-8"))
        self.assertEqual(out["status"], "NOT_RUN")
        self.assertEqual(out["design_status"], "ccx-not-found")


class WorkerRelativeRunDir(_Guarded):
    """The simlab worker runs `python3 -m simlab.worker <spec> out/<exp>/c000/case.json out/<exp>/c000` with cwd /work,
    so the adapter gets a run directory relative to its cwd. rigside makes it absolute: the results then name
    absolute rig paths (section 8.2) and the conforming mesher child, which runs in the case directory, finds its
    files. With the relative path every ccx-check case was mesh-failed on the integration stand-in."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="g4-rigside-rel-"))
        self.cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self.cwd)
        super().tearDown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_relative(self, st: _Store, solver: str):
        spec = st.spec()
        case = spec.cases()[0]
        rel = Path("queue") / spec.id / case["case_id"]
        (self.tmp / rel).mkdir(parents=True, exist_ok=True)
        os.chdir(self.tmp)
        with mock.patch.dict(os.environ, ENV):
            res = rigside.run(case, spec, rel.as_posix(), solver)
        return self.tmp / rel, res

    def test_mesh_case(self):
        rd, res = self.run_relative(_Store(self.tmp), "golden-check")
        self.assertTrue(res["artifacts"])
        case_dir = str((rd / "golden" / "x001").resolve())
        for a in res["artifacts"]:
            self.assertTrue(os.path.isabs(a), a)
            self.assertTrue(Path(a).is_file(), a)
            self.assertTrue(str(Path(a).resolve()).startswith(case_dir), a)
        fd = json.loads((rd / "files.json").read_text(encoding="utf-8"))
        self.assertIn("golden/x001/fields.npz", [f["path"] for f in fd["files"]])

    def test_conforming_case(self):
        try:
            from aces_engineering.golden import conforming
        except ImportError as exc:
            self.skipTest(f"golden.conforming (B3) is not in this tree ({exc})")
        av = conforming.available()
        if not av.get("usable"):
            self.skipTest(f"gmsh and CalculiX ccx are not usable here ({av.get('reason')})")
        gcase = {"case_id": "m001", "role": "conforming", "source_case": "b001", "level": 1, "h_mm": 1.0,
                 "device": "rig", "host": "rig", "lane": "sim/test", "threads": 1, "max_dof": 3000000,
                 "timeout_s": 600.0, "seed": 7}
        rd, res = self.run_relative(_Store(self.tmp, kind="conforming", solver="ccx-check", gcase=gcase), "ccx-check")
        out = json.loads((rd / "outcome.json").read_text(encoding="utf-8"))
        self.assertEqual(out["status"], "FINISHED", out.get("error"))
        self.assertEqual(res["status"], "ok")
        self.assertTrue(all(os.path.isabs(a) for a in res["artifacts"]), res["artifacts"])


class SelfCheck(_Guarded):
    def test_selfcheck_prints_one_document(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = rigside.main(["--selfcheck"])
        doc = json.loads(buf.getvalue())
        self.assertEqual(set(doc), {"ok", "python", "numpy", "scipy", "trimesh", "embreex", "skimage", "gmsh", "cupy",
                                    "ccx", "golden_compute_sha256", "problems"})
        self.assertEqual(code, 0 if doc["ok"] else 1)
        self.assertEqual(doc["ok"], not doc["problems"])
        self.assertRegex(doc["golden_compute_sha256"], r"^[0-9a-f]{64}$")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(rigside.main([]), 2)


if __name__ == "__main__":
    unittest.main()
