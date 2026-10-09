"""B1 unit tests: the golden spec (aces-golden-spec/1), its guard and geometry rules, grids, demos and the template.
stdlib unittest; every spec here is a test input built from the demo, not a result."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (_HERE.parent / "lib", _HERE.parent / "vendor"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.append(str(_p))

from aces_engineering import golden  # noqa: E402
from aces_engineering.golden import demo, spec  # noqa: E402

BASE = Path(tempfile.gettempdir())


def resolve(raw, overrides=None, gpu=False):
    return spec.resolve(raw, base_dir=BASE, overrides=overrides or {}, gpu_usable=gpu)


def joined(msgs) -> str:
    return "\n".join(msgs)


class DemoSpecs(unittest.TestCase):
    def test_root_mount_validates_and_passes_every_rule(self):
        eff = resolve(demo.spec("root-mount"))
        self.assertEqual(spec.guard(eff), [])
        self.assertEqual(spec.check_geometry(eff), [])
        d = eff["derived"]
        self.assertEqual(eff["schema"], "aces-golden-spec/1")
        self.assertEqual((d["gen_mm"], d["fea_mm"], d["field_h_mm"]), (4.0, 4.0, 2.0))
        self.assertEqual(d["check_levels"], [{"level": 1, "h_mm": 2.0, "gpu_only": False}])
        self.assertEqual(d["variables"], {"volfrac": [0.13, 0.38], "rmin_mm": [6.0, 12.0]})
        self.assertEqual(d["fixed"], {"penal": 3.0})
        self.assertEqual(d["outputs"], ["mass_kg", "vm_p99_MPa", "umax_mm", "compliance_Nmm", "vm_max_MPa"])
        self.assertEqual(d["constraints"][0], {"output": "vm_p99_MPa", "op": "<=", "limit": 60.0})
        self.assertEqual(d["grid"], {"origin_mm": [0.0, 0.0, 0.0], "envelope_mm": [100.0, 100.0, 40.0], "derived_from": "spec"})
        self.assertEqual(d["domain_ref"], "aces_engineering.golden.demo:root_mount_domain")
        self.assertEqual((d["stress_exclude_mm"], d["patch_keep_in_mm"]), (6.0, 0.0))
        self.assertEqual(set(d), {"gen_mm", "fea_mm", "field_h_mm", "check_factor", "check_levels", "stress_exclude_mm",
                                  "patch_keep_in_mm", "variables", "fixed", "outputs", "constraints", "objective", "seed",
                                  "n", "tol", "rounds", "kappa", "check_mode", "check_budget_s", "grid", "domain_ref",
                                  "base_dir", "overrides"})

    def test_bone_validates_and_gpu_lists_level_two(self):
        eff = resolve(demo.spec("bone"), gpu=True)
        self.assertEqual(spec.guard(eff), [])
        self.assertEqual(spec.check_geometry(eff), [])
        self.assertEqual(eff["derived"]["field_h_mm"], 1.0)
        self.assertEqual([lv["h_mm"] for lv in eff["derived"]["check_levels"]], [1.0, 0.5])
        self.assertEqual(eff["derived"]["fixed"], {"penal": 3.0, "shell_mm": 4.0})
        self.assertEqual(eff["derived"]["constraints"], [{"output": "vm_p99_MPa", "op": "<=", "limit": 75.0},
                                                         {"output": "umax_mm", "op": "<=", "limit": 0.042}])
        self.assertIn("75 MPa", eff["material"]["source"])
        self.assertIn("vm_p99 <= 75 MPa and umax <= 0.042 mm", eff["hypothesis"])

    def test_demo_specs_are_fresh_copies_and_unknown_demo_is_usage(self):
        a = demo.spec()
        a["name"] = "changed"
        self.assertEqual(demo.spec()["name"], "root-mount")
        with self.assertRaises(golden.GoldenError) as cm:
            demo.spec("nope")
        self.assertEqual(cm.exception.code, golden.EXIT_USAGE)

    def test_overrides_land_in_derived(self):
        eff = resolve(demo.spec(), {"sweep.n": 4, "verify.tol": 0.05, "resolution.fea_mm": 2.0})
        d = eff["derived"]
        self.assertEqual(d["overrides"], {"sweep.n": 4, "verify.tol": 0.05, "resolution.fea_mm": 2.0})
        self.assertEqual((d["n"], d["tol"], d["fea_mm"]), (4, 0.05, 2.0))
        self.assertEqual(d["check_levels"][0]["h_mm"], 1.0)

    def test_template_fails_naming_the_first_missing_field(self):
        t = demo.template("../cad/Bracket v2.step")
        self.assertEqual(t["domain"]["step_file"], "../cad/Bracket v2.step")
        self.assertEqual(t["name"], "bracket-v2")
        with self.assertRaises(spec.SpecError) as cm:
            resolve(t)
        self.assertIn("spec error at supports", str(cm.exception))

    def test_json_schema_loads(self):
        js = spec.json_schema()
        self.assertIn("schema", js["properties"])
        json.loads(json.dumps(js))


class Validation(unittest.TestCase):
    def bad(self, mutate, where):
        raw = demo.spec()
        mutate(raw)
        with self.assertRaises(spec.SpecError) as cm:
            resolve(raw)
        self.assertIn(where, str(cm.exception))
        return str(cm.exception)

    def test_unknown_key_refused(self):
        self.bad(lambda r: r["sweep"].update(seeds=3), "spec error at sweep")

    def test_unsourced_material_refused(self):
        msg = self.bad(lambda r: r["material"].update(source="   "), "material.source")
        self.assertIn("say where", msg)

    def test_allowable_rules(self):
        self.bad(lambda r: r["constraints"].append({"output": "umax_mm", "op": ">=", "limit": "allowable"}),
                 "constraints[2].limit")
        self.bad(lambda r: r["material"].update(allowable_vm_MPa=None), "needs material.allowable_vm_MPa")

    def test_duplicate_constraint_refused(self):
        self.bad(lambda r: r["constraints"].append({"output": "umax_mm", "op": "<=", "limit": 0.05}), "constraints[2]")

    def test_variable_rules(self):
        self.bad(lambda r: r["generator"]["fixed"].update(volfrac=0.2), "both variables and fixed")
        self.bad(lambda r: r["generator"]["variables"].pop("rmin_mm"), "rmin_mm must be in variables or fixed")
        self.bad(lambda r: r["generator"]["fixed"].update(cell_mm=20.0), "cell_mm needs an infill")
        def gyroid_without_wall(r):
            r["generator"]["infill"] = {"kind": "gyroid"}
            r["generator"]["fixed"].update(shell_mm=4.0, cell_mm=20.0)
        self.bad(gyroid_without_wall, "needs wall_mm")

    def test_box_and_region_rules(self):
        self.bad(lambda r: r["supports"][0].update(box=[88, 64, 0, 76, 76, 0]), "supports[0].box")
        self.bad(lambda r: r["loads"][0].update(box=[40, 40, 38, 60, 60, 40]), "planar on z")
        self.bad(lambda r: r["domain"]["keep_in"][0].update(round_mm=4.0), "round_mm 4")
        self.bad(lambda r: r["supports"][0].update(dofs=[0, 0]), "dofs")
        self.bad(lambda r: r["loads"][0].update(force=[0, 0, 0]), "force is zero")

    def test_domain_source_rules(self):
        self.bad(lambda r: r["domain"].update(source="step"), "step_file is required")
        self.bad(lambda r: r["domain"].pop("envelope_mm"), "give both origin_mm and envelope_mm")

    def test_one_load_case(self):
        side = {"name": "cup-side", "box": [40, 40, 40, 60, 60, 40], "force": [17640, 0, 0], "axis": "z", "case": "side"}
        msg = self.bad(lambda r: r["loads"].append(side), "spec error at loads[1].case")
        self.assertIn("v0 solves one load case; run one spec per case (loads[0] is case 'down', loads[1] is case 'side')", msg)
        raw = demo.spec()
        raw["loads"].append(dict(side, case="down"))
        self.assertEqual(len(resolve(raw)["loads"]), 2)  # two loads in one case are one load case

    def test_seed_range(self):
        self.bad(lambda r: r["sweep"].update(seed=-1), "spec error at sweep.seed")
        self.bad(lambda r: r["sweep"].update(seed=2**32), "spec error at sweep.seed")
        self.assertEqual(resolve(demo.spec(), {"sweep.seed": 2**32 - 1})["derived"]["seed"], 2**32 - 1)


class Guard(unittest.TestCase):
    def guard(self, mutate, demo_name="root-mount", overrides=None):
        raw = demo.spec(demo_name)
        mutate(raw)
        return joined(spec.guard(resolve(raw, overrides)))

    def test_rmin_under_one_and_a_half_cells(self):
        g = self.guard(lambda r: r["generator"]["variables"]["rmin_mm"].update(lo=5.0))
        self.assertIn("rmin_mm 5 mm is under 1.5 x gen_mm = 6 mm", g)

    def test_field_spacing_coarser_than_the_solve(self):
        g = self.guard(lambda r: r["generator"]["skin"].update(upsample=1), "bone")
        self.assertIn("skin field spacing 4 mm", g)
        self.assertIn("raise generator.skin.upsample to 2", g)

    def test_gyroid_rules(self):
        g = self.guard(lambda r: r["generator"]["variables"]["cell_mm"].update(lo=15.0), "bone")
        self.assertIn("gyroid cell_mm 15 mm is under 3 x wall_mm 6 mm = 18 mm", g)
        g = self.guard(lambda r: r["generator"]["variables"]["wall_mm"].update(lo=3.0, hi=6.0), "bone")
        self.assertIn("gyroid wall_mm 3 mm is under 2 x fea_mm = 4 mm", g)
        g = self.guard(lambda r: r["generator"]["fixed"].update(shell_mm=3.0), "bone")
        self.assertIn("shell_mm 3 mm is under 2 x fea_mm = 4 mm", g)
        g = self.guard(lambda r: None, "bone", {"generator.skin.upsample": 1, "resolution.fea_mm": 4.0})
        self.assertIn("gyroid wall_mm 4 mm is under 3 x the skin field spacing = 12 mm", g)

    def test_volfrac_and_penal_ranges(self):
        g = self.guard(lambda r: r["generator"]["variables"]["volfrac"].update(lo=0.01))
        self.assertIn("volfrac 0.01..0.38 must lie inside (0.02, 0.9)", g)
        g = self.guard(lambda r: r["generator"]["fixed"].update(penal=6.0))
        self.assertIn("penal 6..6 must lie inside [1, 5]", g)


class Geometry(unittest.TestCase):
    def geo(self, mutate):
        raw = demo.spec()
        mutate(raw)
        return joined(spec.check_geometry(resolve(raw)))

    def test_off_grid_traction_plane(self):
        def move(r):
            r["loads"][0]["box"] = [40, 40, 37, 60, 60, 37]
            r["domain"]["keep_in"][6]["cyl"]["span"] = [30, 40]
        self.assertIn("load 'cup-down': plane z=37 is not on the 4 mm grid (origin 0): use z=36 or z=40", self.geo(move))

    def test_traction_edges_off_the_fea_lattice(self):
        g = self.geo(lambda r: r["loads"][0].update(box=[41, 40, 40, 59, 60, 40]))
        self.assertIn("load 'cup-down': traction edges x 41..59 are not on the 4 mm grid that gen_mm 4, fea_mm 4 and the "
                      "level-1 check 2 mm share (origin 0, 0, 0), so each resolution would load a different patch: use "
                      "the box [40, 40, 40, 60, 60, 40]", g)

    def test_support_edges_off_the_lattice(self):
        g = self.geo(lambda r: r["supports"][0].update(box=[77, 64, 0, 88, 76, 0]))
        self.assertIn("support 'foot-1': box edges x 77..88 are not on the 4 mm grid that gen_mm 4, fea_mm 4 and the "
                      "level-1 check 2 mm share (origin 0, 0, 0), so each resolution would fix a different patch: use "
                      "the box [76, 64, 0, 88, 76, 0]", g)

    def test_every_resolution_fixes_the_same_feet(self):
        # --res 5 with gen_mm 4: the feet (12 mm, on the 4 mm grid) would fix 6 or 9 nodes at 5 mm and 25 at 2.5 mm
        eff = resolve(demo.spec(), {"resolution.fea_mm": 5.0})
        g = joined(spec.check_geometry(eff))
        self.assertIn("support 'foot-1': box edges x 76..88, y 64..76 are not on the 20 mm grid that gen_mm 4, fea_mm 5 "
                      "and the level-1 check 2.5 mm share (origin 0, 0, 0), so each resolution would fix a different "
                      "patch: use the box [80, 60, 0, 100, 80, 0]", g)
        self.assertEqual(sum(ln.startswith("support ") for ln in spec.check_geometry(eff)), 6)
        self.assertNotIn("cup-down", g)  # 40..60 and z=40 are on the 20 mm grid
        self.assertEqual(spec.check_geometry(resolve(demo.spec(), {"resolution.fea_mm": 2.0}, gpu=True)), [])

    def test_lattice_is_relative_to_the_origin_and_ties_move_outward(self):
        # the root mount centred on (0, 0), as a user STEP whose derived origin is -52: the feet sit half a cell off
        def centre(r):
            r["domain"].update(origin_mm=[-52, -52, 0], envelope_mm=[104, 104, 40])
            for k in r["domain"]["keep_in"]:
                k["cyl"]["center"] = [k["cyl"]["center"][0] - 50, k["cyl"]["center"][1] - 50]
            for s in r["supports"]:
                s["box"] = [s["box"][0] - 50, s["box"][1] - 50, 0, s["box"][3] - 50, s["box"][4] - 50, 0]
            r["loads"][0]["box"] = [-12, -12, 40, 12, 12, 40]
        g = self.geo(centre)
        self.assertIn("support 'foot-1': box edges x 26..38, y 14..26 are not on the 4 mm grid that gen_mm 4, fea_mm 4 "
                      "and the level-1 check 2 mm share (origin -52, -52, 0), so each resolution would fix a different "
                      "patch: use the box [24, 12, 0, 40, 28, 0]", g)
        self.assertNotIn("cup-down", g)  # -12..12 and z=40 are on the grid from -52

    def test_bc_lattice_includes_the_planned_check_levels(self):
        eff = resolve(demo.spec(), {"resolution.check_factor": 0.3})
        q, hs = spec.bc_lattice(eff)
        self.assertAlmostEqual(q, 12.0)
        self.assertEqual([w for _, w in hs], ["gen_mm", "fea_mm", "the level-1 check"])
        q2, hs2 = spec.bc_lattice(resolve(demo.spec(), {"resolution.check_factor": 0.3}, gpu=True))
        self.assertAlmostEqual(q2, 36.0)  # level 2 at 0.36 mm, planned because a GPU is usable
        self.assertEqual(hs2[-1][1], "the level-2 check")
        self.assertEqual(spec.stress_lattice(resolve(demo.spec(), gpu=True)), 4.0)
        self.assertAlmostEqual(spec.stress_lattice(eff), 12.0)

    def test_traction_plane_must_be_an_envelope_face(self):
        def lower(r):
            r["loads"][0]["box"] = [40, 40, 36, 60, 60, 36]
            r["domain"]["keep_in"][6]["cyl"]["span"] = [30, 40]
        g = self.geo(lower)
        self.assertIn("load 'cup-down': traction plane z=36 is inside the envelope (its z faces are 0 and 40); a traction "
                      "loads the element faces on both sides of an interior plane", g)
        self.assertNotIn("not on the", g)  # z=36 is on the grid: only the interior plane is refused
        def nodal(r):
            lower(r)
            r["loads"][0].pop("axis")
        self.assertEqual(self.geo(nodal), "")

    def test_bc_not_on_a_keep_in(self):
        g = self.geo(lambda r: r["domain"]["keep_in"].pop(6))
        self.assertIn("load 'cup-down' is not on preserved material: add a keep-in around it", g)
        g = self.geo(lambda r: r["domain"]["keep_in"].pop(0))
        self.assertIn("support 'foot-1' is not on preserved material", g)

    def test_envelope_rule_and_inside_rule(self):
        g = self.geo(lambda r: r["domain"].update(envelope_mm=[102, 100, 40]))
        self.assertIn("domain.envelope_mm x = 102 mm is not a whole number of 4 mm cells (gen_mm): use 100 or 104", g)
        g = self.geo(lambda r: r["supports"][0].update(box=[96, 64, 0, 104, 76, 0]))
        self.assertIn("support 'foot-1' box", g)
        self.assertIn("is not inside the envelope", g)
        g = self.geo(lambda r: r["domain"]["keep_in"][1]["cyl"].update(center=[50, 95]))
        self.assertIn("keep-in 'foot-2'", g)

    def test_cell_limit(self):
        raw = demo.spec()
        eff = resolve(raw, {"resolution.fea_mm": 0.25, "resolution.gen_mm": 0.5})
        self.assertIn("over the v0 limit of 2,000,000", joined(spec.check_geometry(eff)))
        eff = resolve(raw, {"resolution.fea_mm": 1.0, "resolution.gen_mm": 2.0})
        self.assertEqual(spec.check_geometry(eff), [])
        warn = spec.geometry_warnings(eff, gpu_usable=False)
        self.assertEqual(len(warn), 1)
        self.assertTrue(warn[0].startswith("400,000 cells at fea_mm 1 mm on a CPU-only host"))
        self.assertEqual(spec.geometry_warnings(eff, gpu_usable=True), [])


class Grids(unittest.TestCase):
    def test_grid_quantum(self):
        self.assertEqual(spec.grid_quantum([4, 2, 2]), 4.0)
        self.assertEqual(spec.grid_quantum([5, 2.5, 2, 1]), 10.0)
        self.assertAlmostEqual(spec.grid_quantum([0.4, 0.3]), 1.2)
        with self.assertRaises(golden.GoldenError) as cm:
            spec.grid_quantum([997, 991])
        self.assertEqual(cm.exception.code, golden.EXIT_USAGE)

    def test_derive_grid_from_a_step_bbox(self):
        raw = demo.template("part.step")
        raw.update(supports=demo.spec()["supports"][:1], loads=demo.spec()["loads"], material=demo.spec()["material"])
        raw["domain"]["patch_keep_in_mm"] = 8.0
        raw["domain"]["margin_mm"] = 2.0
        eff = resolve(raw)
        self.assertIsNone(eff["derived"]["grid"])
        g = spec.derive_grid([0.3, -1.2, 0.0, 39.7, 20.5, 15.9], eff)
        self.assertEqual(g, {"origin_mm": [-4.0, -4.0, -4.0], "envelope_mm": [48.0, 28.0, 24.0], "derived_from": "step-bbox"})
        eff["derived"]["grid"] = g
        self.assertTrue(all(abs(L / 4 - round(L / 4)) < 1e-9 for L in g["envelope_mm"]))

    def test_param_values(self):
        eff = resolve(demo.spec())
        self.assertEqual(spec.param_values(eff, {"volfrac": 0.2, "rmin_mm": 8, "case_id": "d000"}),
                         {"penal": 3.0, "volfrac": 0.2, "rmin_mm": 8.0})
        with self.assertRaises(ValueError):
            spec.param_values(eff, {"volfrac": 0.2})


class LoadFile(unittest.TestCase):
    def test_load_and_step_override(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "spec.json"
            golden.dump_json(p, demo.spec())
            raw, data, base = spec.load(p)
            self.assertEqual(raw, demo.spec())
            self.assertEqual(data, p.read_bytes())
            self.assertEqual(base, Path(td).resolve())
            step = Path(td) / "part.step"
            raw2, _, _ = spec.load(p, step=step)
            self.assertEqual(raw2["domain"]["source"], "step")
            self.assertEqual(raw2["domain"]["step_file"], str(step.resolve()))
            self.assertNotIn("build123d", raw2["domain"])
            (Path(td) / "bad.json").write_text("{\"schema\": ", encoding="utf-8")
            with self.assertRaises(spec.SpecError) as cm:
                spec.load(Path(td) / "bad.json")
            self.assertIn("not valid JSON", str(cm.exception))

    def test_step_ref_resolves_against_the_spec_directory(self):
        raw = demo.spec()
        raw["domain"] = {"source": "step", "step_file": "../cad/part.step", "origin_mm": [0, 0, 0],
                         "envelope_mm": [100, 100, 40], "keep_in": raw["domain"]["keep_in"], "patch_keep_in_mm": 0}
        with tempfile.TemporaryDirectory() as td:
            eff = spec.resolve(raw, base_dir=Path(td) / "golden", overrides={}, gpu_usable=False)
            self.assertEqual(eff["derived"]["domain_ref"], str((Path(td) / "cad" / "part.step").resolve()))


if __name__ == "__main__":
    unittest.main()
