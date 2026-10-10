"""B1 unit tests: the real solve of a part (golden.fea.solve_mesh) and the structural checks. A trimesh box solved on
the CPU; masks with the load patch removed or split in two. stdlib unittest; test inputs only."""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
for _p in (_HERE.parent / "lib", _HERE.parent / "vendor"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.append(str(_p))

import numpy as np  # noqa: E402

from aces_engineering import golden  # noqa: E402
from aces_engineering.golden import domain, fea, spec  # noqa: E402


def bar_spec() -> dict:
    """A test input: a 40 x 20 x 16 mm envelope, fixed at x = 0, a traction load on part of the x = 40 face."""
    return {
        "schema": "aces-golden-spec/1", "name": "test-fea",
        "domain": {"source": "step", "step_file": "unused.step", "origin_mm": [0, 0, 0], "envelope_mm": [40, 20, 16]},
        "supports": [{"name": "wall", "box": [0, 0, 0, 0, 20, 16]}],
        "loads": [{"name": "tip", "box": [40, 4, 4, 40, 16, 12], "force": [0, 0, -500], "axis": "x"}],
        "material": {"name": "test", "E_MPa": 70000, "nu": 0.33, "rho_kg_mm3": 2.7e-6, "source": "test input"},
        "constraints": [{"output": "umax_mm", "op": "<=", "limit": 1.0}],
        "generator": {"variables": {"volfrac": {"lo": 0.3, "hi": 0.5}, "rmin_mm": {"lo": 6.0, "hi": 8.0}}},
        "resolution": {"gen_mm": 4.0, "fea_mm": 4.0, "check_factor": 0.5},
    }


def gpu_tests_enabled() -> str | None:
    """GPU tests are opt-in (GOLDEN_GPU_TESTS=1) so a unit run never takes VRAM from another workload."""
    if os.environ.get("GOLDEN_GPU_TESTS") != "1":
        return "GPU tests are opt-in: set GOLDEN_GPU_TESTS=1"
    if importlib.util.find_spec("cupy") is None or importlib.util.find_spec("warp") is None:
        return "cupy and warp-lang are not both installed"
    from gendesign.fea3d import have_gpu

    return None if have_gpu() else "no CUDA GPU"


class SolveBox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import trimesh

        cls.td = tempfile.TemporaryDirectory()
        cls.root = Path(cls.td.name)
        cls.eff = spec.resolve(bar_spec(), base_dir=cls.root, overrides={}, gpu_usable=False)
        g = cls.eff["derived"]["grid"]
        cls.dom = {"stress_exclude": domain.stress_exclude(cls.eff, g["origin_mm"], g["envelope_mm"])}
        m = trimesh.creation.box(extents=[40, 20, 16])
        m.apply_translation([20, 10, 8])
        cls.stl = cls.root / "part.stl"
        m.export(str(cls.stl))
        cls.out = fea.solve_mesh(cls.stl, cls.dom, cls.eff, cls.root / "c000", h_mm=4.0, device="cpu", post=True)

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def test_converged_and_metrics(self):
        import trimesh

        out = self.out
        self.assertEqual(out["status"], "ok", out["message"])
        r = out["results"]
        self.assertTrue(r["converged"])
        self.assertLessEqual(r["pcg_relres"], 1e-8)
        vol = float(trimesh.load(str(self.stl), force="mesh").volume)
        self.assertEqual(r["mesh_volume_mm3"], vol)
        self.assertEqual(r["mass_kg"], vol * 2.7e-6)
        self.assertAlmostEqual(r["mass_voxel_kg"], 40 * 20 * 16 * 2.7e-6, places=12)
        self.assertEqual((r["elements"], r["elem_mm"], r["device"]), (200, 4.0, "cpu/numpy"))
        for k in golden.METRICS:
            self.assertTrue(np.isfinite(r[k]), k)
        self.assertLessEqual(r["vm_p95_MPa"], r["vm_p99_MPa"])
        self.assertLessEqual(r["vm_p99_MPa"], float(np.load(self.root / "c000" / "fields.npz")["vm_MPa"].max()) + 1e-9)
        self.assertEqual(out["checks"]["load_coverage"], {"tip": 1.0})
        self.assertEqual(out["checks"]["support_coverage"], {"wall": 1.0})
        self.assertTrue(out["checks"]["load_path_connected"])
        # excluded: x centres 2 and 6 at the wall (all 5 x 4), x 34 and 38 under the load patch (3 x 2)
        self.assertEqual(out["stress"]["n_elements"], 200 - 2 * 5 * 4 - 2 * 3 * 2)

    def test_solver_fields(self):
        r = self.out["results"]
        self.assertEqual((r["fea_solver"], r["fea_solver_reason"]), ("pcg", "pcg requested"))
        self.assertNotIn("mg_levels", r)
        self.assertNotIn("fea_solver_attempts", r)

    def test_auto_on_a_small_mask_is_pcg_and_identical(self):
        out = fea.solve_mesh(self.stl, self.dom, self.eff, self.root / "x001", h_mm=4.0, device="cpu", solver="auto")
        r = out["results"]
        self.assertEqual(r["fea_solver"], "pcg")
        if fea.mg_available():
            self.assertEqual(r["fea_solver_reason"], "auto: Jacobi-PCG (200 cells < 200,000)")
        else:
            self.assertEqual(r["fea_solver_reason"], "auto: Jacobi-PCG (gendesign.mg is not importable here)")
        for k in ("compliance_Nmm", "umax_mm", "vm_max_MPa", "vm_p99_MPa"):
            self.assertEqual(r[k], self.out["results"][k], k)
        with self.assertRaises(ValueError):
            fea.solve_mesh(self.stl, self.dom, self.eff, self.root / "x002", h_mm=4.0, device="cpu", solver="cg")

    def test_mg_that_stops_above_tol_is_solved_again_with_pcg(self):
        """The fallback path, emulated: resolve_solver says mg and the first solve reports solver mg with relative
        residual 1.0 (a test input standing in for a multigrid solve that stopped above tol); the second solve is the
        real PCG. Needs no gendesign.mg."""
        real = fea._solve_adapter
        calls = []

        def fake_adapter(fcase, case_dir):
            calls.append(fcase["fea_solver"])
            sj = real(dict(fcase, fea_solver="pcg"), case_dir)
            if len(calls) == 1:
                sj = dict(sj, solver="mg", pcg_relres=1.0, mg_levels=4)
            return sj

        with mock.patch.object(fea, "resolve_solver", return_value=("mg", "mg requested (test input)")), \
                mock.patch.object(fea, "_solve_adapter", fake_adapter):
            out = fea.solve_mesh(self.stl, self.dom, self.eff, self.root / "m-fallback", h_mm=4.0, device="cpu",
                                 solver="mg")
        r = out["results"]
        self.assertEqual(calls, ["mg", "pcg"])
        self.assertEqual(out["status"], "ok", out["message"])
        self.assertEqual(r["fea_solver"], "pcg")
        self.assertEqual([a["solver"] for a in r["fea_solver_attempts"]], ["mg", "pcg"])
        self.assertEqual(r["fea_solver_attempts"][0]["relres"], 1.0)
        self.assertIn("multigrid stopped at relative residual 1.000e+00", r["fea_solver_reason"])
        self.assertNotIn("mg_levels", r)
        self.assertEqual(r["compliance_Nmm"], self.out["results"]["compliance_Nmm"])

    def test_files_and_post(self):
        d = self.root / "c000"
        for name in ("mask.npy", "mesh.json", "solve.json", "fields.npz", "vm_slices.png", "disp_slices.png", "fields.vtk", "case.md"):
            self.assertTrue((d / name).is_file(), name)
        mj = golden.read_json(d / "mesh.json")
        self.assertEqual((mj["source"], mj["cells"], mj["solid_cells"], mj["watertight"]), ("mesh", 200, 200, True))
        md = (d / "case.md").read_bytes()
        self.assertNotIn(b"\r\n", md)
        self.assertIn(b"mesh volume", md)
        self.assertIn(b"preview-grade", md)
        self.assertNotIn(b"B-rep volume", md)
        self.assertEqual(self.out["voxel"]["mask_sha256"], golden.sha256_file(d / "mask.npy"))

    def test_reused_mask_is_bit_identical_on_the_same_device(self):
        again = fea.solve_mesh(self.stl, self.dom, self.eff, self.root / "p001", h_mm=4.0, device="cpu",
                               reuse_mask_from=self.root / "c000")
        self.assertEqual(again["status"], "ok")
        for k in golden.METRICS:
            self.assertEqual(again["results"][k], self.out["results"][k], k)

    def test_open_mesh_is_refused(self):
        import trimesh

        box = trimesh.creation.box(extents=[40, 20, 16])
        box.apply_translation([20, 10, 8])
        m = trimesh.Trimesh(box.vertices, box.faces[1:], process=False)  # one triangle short: an open mesh
        p = self.root / "open.stl"
        m.export(str(p))
        out = fea.solve_mesh(p, self.dom, self.eff, self.root / "c001", h_mm=4.0, device="cpu")
        self.assertEqual(out["status"], "not-watertight")
        self.assertFalse((self.root / "c001" / "mask.npy").exists())

    @unittest.skipIf(gpu_tests_enabled() is not None, gpu_tests_enabled() or "")
    def test_warp_and_cupy_parity(self):
        cu = fea.solve_mesh(self.stl, self.dom, self.eff, self.root / "p002", h_mm=4.0, device="gpu",
                            reuse_mask_from=self.root / "c000")
        wp = fea.solve_mesh(self.stl, self.dom, self.eff, self.root / "p003", h_mm=4.0, device="gpu", backend="warp",
                            reuse_mask_from=self.root / "c000")
        for o in ("compliance_Nmm", "umax_mm", "vm_max_MPa", "vm_p99_MPa", "mass_voxel_kg"):
            ref = self.out["results"][o]
            self.assertLessEqual(abs(cu["results"][o] - ref) / abs(ref), 1e-6, o)
            self.assertLessEqual(abs(wp["results"][o] - ref) / abs(ref), 1e-6, o)


class SolverChoice(unittest.TestCase):
    """fea.resolve_solver (section 11): auto is multigrid only with gendesign.mg, >= MG_MIN_CELLS cells and one solid
    component; an explicit mg without the module or with more than one component falls back to PCG."""

    def test_with_the_module(self):
        with mock.patch.object(fea, "mg_available", return_value=True):
            self.assertEqual(fea.resolve_solver("auto", 10 ** 6, 1)[0], "mg")
            self.assertEqual(fea.resolve_solver("auto", fea.MG_MIN_CELLS, 1)[0], "mg")
            self.assertEqual(fea.resolve_solver("auto", fea.MG_MIN_CELLS - 1, 1),
                             ("pcg", "auto: Jacobi-PCG (199,999 cells < 200,000)"))
            self.assertEqual(fea.resolve_solver("auto", 10 ** 6, 2),
                             ("pcg", "auto: Jacobi-PCG (2 solid components; multigrid needs one)"))
            self.assertEqual(fea.resolve_solver("mg", 10, 1), ("mg", "mg requested"))
            self.assertEqual(fea.resolve_solver("mg", 10, 3)[0], "pcg")
        self.assertEqual((fea.MG_MIN_CELLS, fea.MG_LEVELS, fea.FEA_SOLVERS), (200_000, 4, ("pcg", "mg", "auto")))

    def test_without_the_module(self):
        with mock.patch.object(fea, "mg_available", return_value=False):
            self.assertEqual(fea.resolve_solver("auto", 10 ** 6, 1),
                             ("pcg", "auto: Jacobi-PCG (gendesign.mg is not importable here)"))
            self.assertEqual(fea.resolve_solver("mg", 10 ** 6, 1),
                             ("pcg", "mg requested; gendesign.mg is not importable here: Jacobi-PCG"))
        self.assertEqual(fea.resolve_solver("pcg", 10 ** 6, 1), ("pcg", "pcg requested"))
        with self.assertRaises(ValueError):
            fea.resolve_solver("cg", 10, 1)

    def test_evaluator_kwargs_follow_the_signature(self):
        class Patched:  # the rig's patched evaluator (test input)
            def __init__(self, fea, E=1.0, solver="pcg", repair=True, mg_levels=4):
                pass

        class Old:  # the vendored snapshot's evaluator (test input)
            def __init__(self, fea, E=1.0):
                pass

        self.assertEqual(fea._evaluator_kwargs(Patched, solver="pcg", repair=False), {"solver": "pcg", "repair": False})
        self.assertEqual(fea._evaluator_kwargs(Old, solver="pcg", repair=False), {})
        from gendesign.evaluators import VoxelFEAEvaluator

        self.assertIsInstance(fea._evaluator_kwargs(VoxelFEAEvaluator, solver="pcg", repair=False), dict)


class Checks(unittest.TestCase):
    def setUp(self):
        self.eff = spec.resolve(bar_spec(), base_dir=Path(tempfile.gettempdir()), overrides={}, gpu_usable=False)

    def test_full_box_passes(self):
        checks, status, _ = fea.structural_checks(np.ones((10, 5, 4), dtype=bool), 4.0, [0, 0, 0], self.eff)
        self.assertEqual(status, "ok")
        self.assertEqual(checks["solid_components"], 1)

    def test_load_patch_removed_is_detached_load(self):
        mask = np.ones((10, 5, 4), dtype=bool)
        mask[9, :, :] = False
        checks, status, msg = fea.structural_checks(mask, 4.0, [0, 0, 0], self.eff)
        self.assertEqual(status, "detached-load")
        self.assertEqual(checks["load_coverage"]["tip"], 0.0)
        self.assertIn("tip 0.000", msg)

    def test_split_mask_is_disconnected(self):
        mask = np.ones((10, 5, 4), dtype=bool)
        mask[5, :, :] = False
        checks, status, _ = fea.structural_checks(mask, 4.0, [0, 0, 0], self.eff)
        self.assertEqual(status, "disconnected")
        self.assertEqual(checks["solid_components"], 2)
        self.assertFalse(checks["load_path_connected"])

    def test_support_without_material_is_detached_support(self):
        mask = np.ones((10, 5, 4), dtype=bool)
        mask[0:2, :, :] = False
        _, status, _ = fea.structural_checks(mask, 4.0, [0, 0, 0], self.eff)
        self.assertEqual(status, "detached-support")

    def test_node_and_element_sets_match_the_vendored_selectors(self):
        from gendesign.fea3d import VoxelFEA

        f = VoxelFEA(10, 5, 4, 4.0, prefer_gpu=False)
        box = [40, 4, 4, 40, 16, 12]
        mine = fea.traction_elems(box, "x", [0, 0, 0], (10, 5, 4), 4.0).reshape(-1)
        n = f.add_traction_box("t", box, [0, 0, -1.0], axis="x")
        self.assertEqual(int(mine.sum()), n)
        nodes = fea.box_nodes([0, 0, 0, 0, 20, 16], [0, 0, 0], (10, 5, 4), 4.0)
        ref = f.nodes_in_box([0, 0, 0, 0, 20, 16]).reshape(5, 6, 11).transpose(2, 1, 0)
        np.testing.assert_array_equal(nodes, ref)


if __name__ == "__main__":
    unittest.main()
