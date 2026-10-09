"""B1 unit tests: SDF primitives, smooth min/max, the gyroid sheet, the zero clamp, and skin() on synthetic densities.
stdlib unittest; every density and spec here is a test input."""
from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (_HERE.parent / "lib", _HERE.parent / "vendor"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.append(str(_p))

import numpy as np  # noqa: E402

from aces_engineering.golden import organic, spec  # noqa: E402


def P(*pts):
    return np.asarray(pts, dtype=np.float32)


def bar_spec(infill: bool = False) -> dict:
    """A test input: an 80 x 40 x 40 mm envelope, a support patch at x = 0, a traction load at x = 80, a keep-in box at
    the load end."""
    gen = {"kind": "simp-organic", "variables": {"volfrac": {"lo": 0.2, "hi": 0.4}, "rmin_mm": {"lo": 6.0, "hi": 8.0}},
           "fixed": {"penal": 3.0}, "iterations": 10,
           "skin": {"upsample": 2, "sigma_vox": 1.0, "blend_mm": 3.0, "clip_blend_mm": 2.0, "taubin_iters": 3, "iso": 0.5}}
    res = {"gen_mm": 4.0, "fea_mm": 4.0, "check_factor": 0.5}
    if infill:
        gen["fixed"].update(shell_mm=4.0, cell_mm=16.0, wall_mm=4.0)
        gen["infill"] = {"kind": "gyroid", "blend_mm": 1.0}
        gen["skin"]["upsample"] = 4
        res["fea_mm"] = 2.0
    return {
        "schema": "aces-golden-spec/1", "name": "test-bar",
        "domain": {"source": "step", "step_file": "unused.step", "origin_mm": [0, 0, 0], "envelope_mm": [80, 40, 40],
                   "keep_in": [{"name": "boss", "box": [68, 12, 12, 80, 28, 28]}], "patch_keep_in_mm": 0},
        "supports": [{"name": "wall", "box": [0, 16, 16, 0, 24, 24]}],
        "loads": [{"name": "tip", "box": [80, 16, 16, 80, 24, 24], "force": [0, 0, -100], "axis": "x"}],
        "material": {"name": "test", "E_MPa": 70000, "nu": 0.3, "rho_kg_mm3": 2.7e-6, "source": "test input"},
        "constraints": [{"output": "umax_mm", "op": "<=", "limit": 1.0}],
        "generator": gen, "resolution": res,
    }


def domain_of(eff) -> dict:
    return {"keep_in": eff["domain"]["keep_in"], "patch_keep_ins": [], "keep_out": [], "sdf_field_path": None}


class Primitives(unittest.TestCase):
    def test_box_signs_and_distances(self):
        box = [0, 0, 0, 10, 4, 2]
        d = organic.sdf_box(P([5, 2, 1], [12, 2, 1], [13, 7, 1], [5, 2, 2]), box)
        self.assertEqual(d.dtype, np.float32)
        np.testing.assert_allclose(d, [-1.0, 2.0, math.hypot(3, 3), 0.0], atol=1e-5)

    def test_rounded_box_keeps_outer_dimensions(self):
        box = [0, 0, 0, 10, 10, 10]
        d = organic.sdf_box(P([10, 5, 5], [5, 5, 10], [10, 10, 10], [11, 11, 11]), box, round_mm=2.0)
        np.testing.assert_allclose(d[:2], [0.0, 0.0], atol=1e-5)
        corner = math.sqrt(3) * 2.0 - 2.0  # the rounded corner sits inside the sharp one
        np.testing.assert_allclose(d[2], corner, atol=1e-5)
        np.testing.assert_allclose(d[3], math.sqrt(3) * 3.0 - 2.0, atol=1e-5)

    def test_cylinder_signs_and_distances(self):
        d = organic.sdf_cyl(P([8, 0, 5], [0, 0, 5], [0, 0, 12], [3, 0, 9]), "z", (0, 0), 5.0, (0, 10))
        np.testing.assert_allclose(d, [3.0, -5.0, 2.0, -1.0], atol=1e-5)
        d = organic.sdf_cyl(P([5, 0, 5], [5, 0, 10], [0, 0, 10]), "z", (0, 0), 5.0, (0, 10), round_mm=1.0)
        np.testing.assert_allclose(d, [0.0, math.sqrt(2) - 1.0, 0.0], atol=1e-5)
        dx = organic.sdf_cyl(P([5, 3, 0], [5, 0, 0]), "x", (0, 0), 2.0, (0, 10))
        np.testing.assert_allclose(dx, [1.0, -2.0], atol=1e-5)

    def test_region_dispatch(self):
        r = {"name": "", "box": None, "cyl": {"axis": "y", "center": [0, 0], "r": 3.0, "span": [0, 4]}, "round_mm": 0.0}
        np.testing.assert_allclose(organic.sdf_region(P([0, 2, 0], [5, 2, 0]), r), [-2.0, 2.0], atol=1e-5)

    def test_smooth_min_and_max(self):
        a = np.linspace(-3, 3, 61, dtype=np.float32)
        b = np.float32(0.5) * np.ones_like(a)
        s = organic.smin(a, b, 1.0)
        self.assertEqual(s.dtype, np.float32)
        self.assertTrue(np.all(s <= np.minimum(a, b) + 1e-7))
        far = np.abs(a - b) >= 1.0
        np.testing.assert_array_equal(s[far], np.minimum(a, b)[far])
        m = organic.smax(a, b, 1.0)
        self.assertTrue(np.all(m >= np.maximum(a, b) - 1e-7))
        np.testing.assert_array_equal(organic.smin(a, b, 0.0), np.minimum(a, b))

    def test_gyroid_sheet_zero_set_and_distance(self):
        cell, wall = 20.0, 2.0
        # G = 0 at the origin and at (cell/2, 0, 0): the sheet's mid-surface, so the SDF is -wall/2 there
        d = organic.gyroid_sheet_sdf(P([0, 0, 0], [10, 0, 0], [0, 10, 0]), cell, wall, (0, 0, 0))
        np.testing.assert_allclose(d, [-1.0, -1.0, -1.0], atol=1e-4)
        # stepping 0.1 mm along the surface normal at the origin moves the distance by about 0.1 mm
        n = np.array([1.0, 1.0, 1.0]) / math.sqrt(3)
        d1 = organic.gyroid_sheet_sdf(P(list(0.1 * n)), cell, wall, (0, 0, 0))
        self.assertAlmostEqual(float(d1[0]), 0.1 - 1.0, delta=0.01)

    def test_zero_clamp(self):
        hf = 2.0
        sd = np.array([0.0, 1e-9, -1e-6, -0.5, 0.3, 2e-4], dtype=np.float32)
        out = organic.zero_clamp(sd, hf)
        self.assertEqual(out.dtype, np.float32)
        self.assertFalse(np.any(np.abs(out) < np.float32(1e-4 * hf)))
        np.testing.assert_array_equal(out[[3, 4, 5]], sd[[3, 4, 5]])


class Skin(unittest.TestCase):
    def test_bar_skin_is_one_watertight_solid_and_islands_go(self):
        eff = spec.resolve(bar_spec(), base_dir=Path(tempfile.gettempdir()), overrides={}, gpu_usable=False)
        x = np.zeros((20, 10, 10), dtype=np.float32)
        x[:, 4:6, 4:6] = 1.0          # a bar from the support to the load
        x[2:4, 7:9, 7:9] = 1.0        # a detached blob that touches nothing
        mesh, info = organic.skin(x, domain_of(eff), eff, {"volfrac": 0.3, "rmin_mm": 7.0})
        self.assertEqual(info["status"], "ok", info["message"])
        self.assertTrue(info["watertight"])
        self.assertEqual((info["solids"], info["cavities"]), (1, 0))
        self.assertGreater(info["mesh_volume_mm3"], 0.0)
        self.assertGreaterEqual(info["islands_removed"], 1)
        self.assertGreater(info["removed_volume_mm3"], 0.0)
        self.assertEqual(info["schema"], "aces-golden-skin/1")
        self.assertAlmostEqual(float(mesh.volume), info["mesh_volume_mm3"], places=6)
        # the keep-in boss is preserved: points well inside it are inside the part
        g = np.stack(np.meshgrid(np.linspace(70, 78, 5), np.linspace(14, 26, 5), np.linspace(14, 26, 5), indexing="ij"), -1)
        self.assertTrue(bool(np.all(mesh.contains(g.reshape(-1, 3)))))
        # and nothing survives near the removed blob
        self.assertFalse(bool(np.any(mesh.contains(np.array([[12.0, 32.0, 32.0]])))))

    def test_empty_density(self):
        eff = spec.resolve(bar_spec(), base_dir=Path(tempfile.gettempdir()), overrides={}, gpu_usable=False)
        mesh, info = organic.skin(np.zeros((20, 10, 10), dtype=np.float32), domain_of(eff), eff, {"volfrac": 0.3, "rmin_mm": 7.0})
        self.assertIsNone(mesh)
        self.assertEqual(info["status"], "empty")

    def test_gyroid_block_reports_cavities(self):
        eff = spec.resolve(bar_spec(infill=True), base_dir=Path(tempfile.gettempdir()), overrides={}, gpu_usable=False)
        x = np.zeros((20, 10, 10), dtype=np.float32)
        x[0:18, 1:9, 1:9] = 1.0       # a solid block grown from the support towards the load
        mesh, info = organic.skin(x, domain_of(eff), eff, {"volfrac": 0.3, "rmin_mm": 7.0})
        self.assertTrue(info["watertight"], info["message"])
        self.assertEqual(info["infill"], {"kind": "gyroid", "cell_mm": 16.0, "wall_mm": 4.0, "shell_mm": 4.0, "blend_mm": 1.0})
        self.assertGreaterEqual(info["cavities"], 1)
        self.assertGreater(info["cavity_volume_mm3"], 0.0)
        self.assertGreater(info["genus"], 0)


if __name__ == "__main__":
    unittest.main()
