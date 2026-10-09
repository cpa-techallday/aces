"""B1 unit tests: the design space (build + prepare) on a 40 x 20 x 16 mm build123d box at 4 mm, the passive void-box
encoding, keep-ins that always win, patch keep-ins and stress-exclusion zones. stdlib unittest; test inputs only."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (_HERE.parent / "lib", _HERE.parent / "vendor"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.append(str(_p))

import numpy as np  # noqa: E402

from aces_engineering import golden  # noqa: E402
from aces_engineering.golden import domain, spec  # noqa: E402

BUILDER = (
    "from build123d import Align, Box\n\n\n"
    "def box(x=40.0, y=20.0, z=16.0):\n"
    "    return Box(x, y, z, align=(Align.MIN, Align.MIN, Align.MIN))\n"
)


def box_spec(envelope=(48, 24, 16), keep_in=(), patch=None) -> dict:
    """A test input: a box design space from a builder file, supported at x = 0, loaded at x = 40."""
    return {
        "schema": "aces-golden-spec/1", "name": "test-box",
        "domain": {"source": "build123d", "build123d": {"ref": "builder.py:box", "kwargs": {"z": 16.0}},
                   "origin_mm": [0, 0, 0], "envelope_mm": list(envelope), "keep_in": list(keep_in),
                   "patch_keep_in_mm": patch},
        "supports": [{"name": "wall", "box": [0, 0, 0, 0, 20, 16]}],
        "loads": [{"name": "tip", "box": [40, 4, 4, 40, 16, 12], "force": [0, 0, -500], "axis": "x"}],
        "material": {"name": "test", "E_MPa": 70000, "nu": 0.33, "rho_kg_mm3": 2.7e-6, "allowable_vm_MPa": 100,
                     "source": "test input"},
        "constraints": [{"output": "vm_p99_MPa", "op": "<=", "limit": "allowable"}],
        "generator": {"variables": {"volfrac": {"lo": 0.3, "hi": 0.5}, "rmin_mm": {"lo": 6.0, "hi": 8.0}}, "iterations": 6},
        "resolution": {"gen_mm": 4.0, "fea_mm": 4.0, "check_factor": 0.5},
        "sweep": {"n": 2, "seed": 1},
    }


class DomainBox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        root = Path(cls.td.name)
        (root / "spec").mkdir()
        (root / "spec" / "builder.py").write_text(BUILDER, encoding="utf-8")
        keep = [{"name": "lug", "box": [36, 0, 0, 48, 8, 16]}]   # sticks out of the design space on purpose
        cls.eff = spec.resolve(box_spec(keep_in=keep), base_dir=root / "spec", overrides={}, gpu_usable=False)
        cls.run_root = root / "run"
        cls.inputs = domain.build(cls.eff, cls.run_root / "inputs", root / "spec")
        cls.dom = domain.prepare(cls.eff, cls.run_root / "inputs" / "domain.step", cls.run_root / "domain")

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def test_inputs_record(self):
        rec = golden.read_json(self.run_root / "inputs" / "domain.json")
        self.assertEqual(rec, self.inputs)
        self.assertEqual(rec["schema"], "aces-golden-inputs/1")
        self.assertEqual((rec["source"], rec["ref"], rec["kwargs"], rec["solids"]), ("build123d", "builder.py:box", {"z": 16.0}, 1))
        self.assertAlmostEqual(rec["brep_volume_mm3"], 40 * 20 * 16, places=3)
        np.testing.assert_allclose(rec["bbox_mm"], [0, 0, 0, 40, 20, 16], atol=1e-6)
        self.assertEqual(rec["step_sha256"], golden.sha256_file(self.run_root / "inputs" / "domain.step"))
        self.assertTrue(rec["ref_file"].endswith("builder.py"))
        self.assertEqual(len(rec["ref_file_sha256"]), 64)

    def test_stage_mesh_stats(self):
        st = self.dom["stats"]
        self.assertEqual((st["cells"], st["solid_cells"]), (12 * 6 * 4, 10 * 5 * 4))
        self.assertAlmostEqual(st["volume_ratio_voxel_over_brep"], 1.0, places=6)
        self.assertTrue(st["watertight"])
        self.assertAlmostEqual(self.dom["design_share"], 200 / 288, places=9)
        self.assertEqual(self.dom["mask_sha256"], golden.sha256_file(self.run_root / "domain" / "mask.npy"))
        self.assertIsNone(self.dom["stats_fea"])
        on_disk = golden.read_json(self.run_root / "domain" / "domain.json")
        self.assertEqual(on_disk["schema"], "aces-golden-domain/1")
        self.assertEqual(golden.canonical_json(on_disk), golden.canonical_json(self.dom))

    def test_sdf_field(self):
        self.assertEqual(self.dom["sdf_field_path"], "domain/sdf_field.npy")
        sdf = np.load(self.run_root / self.dom["sdf_field_path"])
        self.assertEqual(sdf.shape, (24, 12, 8))
        self.assertEqual(sdf.dtype, np.float32)
        self.assertLess(float(sdf[5, 5, 4]), 0.0)    # (11, 11, 9) mm: inside the box
        self.assertGreater(float(sdf[22, 3, 4]), 0.0)  # (45, 7, 9) mm: outside it

    def test_void_boxes_and_keep_ins_win(self):
        from gendesign.fea3d import VoxelFEA

        mask = np.load(self.run_root / "domain" / "mask.npy")
        fea = VoxelFEA(12, 6, 4, 4.0, prefer_gpu=False)
        void = np.zeros(fea.ne, dtype=bool)
        solid = np.zeros(fea.ne, dtype=bool)
        for p in self.dom["passive_grid"]:
            sel = fea.elems_in_box(p["box"]) if "box" in p else fea.elems_in_cyl(**{k: p["cyl"][k] for k in ("axis", "center", "r", "span")})
            if p["type"] == "void":
                void |= sel
            else:
                solid |= sel
        keep = domain.region_mask(self.eff["domain"]["keep_in"] + self.dom["patch_keep_ins"], mask.shape, 4.0, [0, 0, 0])
        np.testing.assert_array_equal(void.reshape(mask.shape), ~mask & ~keep)
        np.testing.assert_array_equal(solid.reshape(mask.shape), keep)
        self.assertTrue(keep[9:12, 0:2, :].all())
        self.assertFalse((void.reshape(mask.shape) & keep).any())
        self.assertEqual(self.dom["n_void_boxes"], sum(1 for p in self.dom["passive_grid"] if p["type"] == "void"))

    def test_patches_and_stress_zones(self):
        t = 2 * 4.0  # patch_keep_in_mm defaults to 2 x gen_mm
        self.assertEqual(self.dom["patch_keep_ins"][0]["box"], [0.0, 0.0, 0.0, t, 20.0, 16.0])   # on the envelope face
        self.assertEqual(self.dom["patch_keep_ins"][1]["box"], [40 - t, 4.0, 4.0, 40 + t, 16.0, 12.0])  # interior plane
        # stress_exclude_mm defaults to 1.5 x fea_mm = 6, rounded up to the 4 mm stress lattice: 8 mm at every level
        s = 8.0
        self.assertEqual((self.dom["stress_exclude_effective_mm"], self.dom["stress_exclude_lattice_mm"]), (s, 4.0))
        self.assertEqual(self.dom["stress_exclude"], [[0.0, 0.0, 0.0, s, 20.0, 16.0], [40 - s, 4.0, 4.0, 48.0, 16.0, 12.0]])
        self.assertEqual(self.dom["load_patches"], [{"name": "tip", "box": [40.0, 4.0, 4.0, 40.0, 16.0, 12.0], "axis": "x", "plane": 40.0}])

    def test_stress_zones_are_the_same_physical_zone_at_every_level(self):
        from aces_engineering.golden import fea

        env = [48.0, 24.0, 16.0]

        def excluded_mm3(h):
            shape = [int(round(L / h)) for L in env]
            m = np.zeros(shape, dtype=bool)
            for b in self.dom["stress_exclude"]:
                m |= fea.box_elems(b, [0.0, 0.0, 0.0], shape, h)
            return float(m.sum()) * h ** 3

        self.assertEqual(excluded_mm3(4.0), 8 * 20 * 16 + 16 * 12 * 8)  # the boxes themselves, whole cells
        self.assertEqual(excluded_mm3(2.0), excluded_mm3(4.0))   # level 1 (check_factor 0.5)
        self.assertEqual(excluded_mm3(1.0), excluded_mm3(4.0))   # level 2
        self.assertEqual(self.dom["support_patches"], [{"name": "wall", "box": [0.0, 0.0, 0.0, 0.0, 20.0, 16.0]}])


class Encoding(unittest.TestCase):
    def test_random_mask_round_trip(self):
        from gendesign.fea3d import VoxelFEA

        rng = np.random.default_rng(3)
        shape, h = (7, 5, 9), 2.5
        void = rng.random(shape) < 0.45
        boxes = domain.void_boxes(void, h)
        fea = VoxelFEA(*shape, h, prefer_gpu=False)
        back = np.zeros(fea.ne, dtype=bool)
        for b in boxes:
            back |= fea.elems_in_box(b)
        np.testing.assert_array_equal(back.reshape(shape), void)

    def test_region_mask_matches_vendored_cylinder_test(self):
        from gendesign.fea3d import VoxelFEA

        cyl = {"name": "", "box": None, "cyl": {"axis": "y", "center": [7.0, 5.0], "r": 4.1, "span": [1.0, 9.0]}, "round_mm": 0.0}
        origin = [1.0, -2.0, 0.5]
        m = domain.region_mask([cyl], (8, 6, 5), 2.0, origin)
        fea = VoxelFEA(8, 6, 5, 2.0, prefer_gpu=False)
        g = domain.to_grid(cyl, origin)["cyl"]
        np.testing.assert_array_equal(m.reshape(-1), fea.elems_in_cyl(g["axis"], g["center"], g["r"], g["span"]))

    def test_too_many_void_boxes_is_usage(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "builder.py").write_text(BUILDER, encoding="utf-8")
            raw = box_spec(envelope=(48, 24, 16))
            raw["domain"]["build123d"]["kwargs"] = {"z": 16.0}
            eff = spec.resolve(raw, base_dir=root, overrides={}, gpu_usable=False)
            domain.build(eff, root / "run" / "inputs", root)
            old = domain.MAX_VOID_BOXES
            domain.MAX_VOID_BOXES = 3
            try:
                with self.assertRaises(golden.GoldenError) as cm:
                    domain.prepare(eff, root / "run" / "inputs" / "domain.step", root / "run" / "domain")
            finally:
                domain.MAX_VOID_BOXES = old
            self.assertEqual(cm.exception.code, golden.EXIT_USAGE)
            self.assertIn("design space too complex for the v0 passive-box encoding at 4 mm", cm.exception.message)


if __name__ == "__main__":
    unittest.main()
