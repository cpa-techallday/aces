"""W07-W10 of build spec section 21: cad_inspect with real build123d (sections 11.4 and 12).

Section 12 fixes each check's fields. Where a check reports per part, the tests read the part's fields through
part_entry(): a "parts" list of rows with "name" (the layout this file expects), a {name: row} mapping, or the fields
at the check's top level for a one-part result. Every expected number comes from tests/expected.json.
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from base import WorkerTestCase, expected


def part_entry(check: dict, name: str | None = None) -> dict:
    rows = check.get("parts")
    if isinstance(rows, list):
        if name is None:
            assert rows, check
            return rows[0]
        for row in rows:
            if row.get("name") == name or row.get("part") == name:
                return row
        raise AssertionError(f"no part {name} in {json.dumps(check)[:400]}")
    if isinstance(rows, dict):
        return rows[name] if name is not None else next(iter(rows.values()))
    return check


def diag(m) -> list:
    return [m[0][0], m[1][1], m[2][2]]


class InspectTest(WorkerTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    def inspect(self, run_id: str, *checks: str, **kw) -> dict:
        out = self.op("inspect", project="p", run_id=run_id, checks=list(checks), **kw)
        self.assertEqual(out["run_id"], run_id)
        self.assertRegex(out["inspect_id"], r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{4}$")
        report = Path(out["report_path"])
        self.assertTrue(report.is_file())
        self.assertEqual(report.parent, self.runs_dir() / run_id / "inspect")
        for name in checks:
            self.assertIn(name, out["checks"])
            self.assertNotIn("error", out["checks"][name], f"{name}: {out['checks'][name]}")
            self.assertTrue(out["checks"][name].get("method"), f"{name} names its method")
        return out["checks"]

    def plate(self) -> str:
        return self.run_script("aces_plate", name="plate")["run_id"]

    def test_w07_mass_and_inertia(self):
        rid = self.plate()
        mass = self.inspect(rid, "mass", material="AlSi10Mg")["mass"]
        row = part_entry(mass)
        self.assert_expected(row["mass_kg"], "aces_plate.mass_alsi10mg_kg")
        self.assert_expected(row["volume_mm3"], "aces_plate.volume_mm3")
        self.assert_expected(diag(row["inertia_mm5"]), "aces_plate.inertia_diag_mm5")
        # the plate's axes are its principal axes, so the principal moments are the diagonal (in some order)
        self.assert_close(sorted(row["principal_moments_mm5"]), sorted(expected("aces_plate.inertia_diag_mm5")), 1e-3)
        density = mass["density"]
        self.assertEqual((density["kg_m3"], density["material"]), (expected("materials.AlSi10Mg.density_kg_m3"),
                                                                   "AlSi10Mg"))
        self.assertTrue(density["source"])
        by_density = self.inspect(rid, "mass", density_kg_m3=2670)["mass"]
        self.assert_expected(part_entry(by_density)["mass_kg"], "aces_plate.mass_alsi10mg_kg")
        self.op_error("inspect", "usage", project="p", run_id=rid, checks=["mass"], material="AlSi10Mg",
                      density_kg_m3=2670)
        err = self.op_error("inspect", "usage", project="p", run_id=rid, checks=["mass"], material="Unobtainium")
        self.assertIn("AlSi10Mg", err["message"])
        cfg = Path(os.environ["ACES_USER_CONFIG"])
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "cad-materials.json").write_text(json.dumps(
            {"schema": "aces.cad.materials/1", "materials": {"Fixture steel": {"density_kg_m3": 7850,
                                                                              "source": "test fixture value"}}}),
            encoding="utf-8")
        steel = part_entry(self.inspect(rid, "mass", material="Fixture steel")["mass"])
        self.assertAlmostEqual(steel["mass_kg"], expected("aces_plate.volume_mm3") * 7850e-9, delta=1e-12)
        (cfg / "cad-materials.json").write_text(json.dumps(
            {"schema": "aces.cad.materials/1", "materials": {"Sourceless": {"density_kg_m3": 1000, "source": ""}}}),
            encoding="utf-8")
        self.op_error("inspect", ("config", "usage"), project="p", run_id=rid, checks=["mass"], material="Sourceless")

    def test_w08_validity(self):
        plate = part_entry(self.inspect(self.plate(), "validity")["validity"])
        self.assertTrue(plate["ok"], plate)
        self.assertEqual([r for r in plate["reasons"] if r != "self_intersection_unchecked"], [])
        self.assertIs(plate["brepcheck_valid"], True)
        self.assertIs(plate["closed"], True)
        self.assertIs(plate["self_intersection"], False)
        self.assertIs(plate["manifold"], True)
        self.assertIs(plate["mesh"]["watertight"], True)
        overlap = part_entry(self.inspect(self.run_script("overlap_compound")["run_id"], "validity")["validity"])
        self.assertIn("self_intersecting", overlap["reasons"])
        self.assertIn(expected("overlap_compound.bop_status"), overlap["bop_statuses"])
        self.assertFalse(overlap["ok"])
        reverse = part_entry(self.inspect(self.run_script("reversed_box")["run_id"], "validity")["validity"])
        self.assertIn("non_positive_volume", reverse["reasons"])
        self.assert_close(reverse["signed_volumes_mm3"], [expected("reversed_box.signed_volume_mm3")], 1e-6)
        bowtie_run = self.run_script("bowtie_face")
        self.assertEqual(bowtie_run["result"]["parts"][0]["valid"], False)
        bowtie = part_entry(self.inspect(bowtie_run["run_id"], "validity")["validity"])
        self.assertIn("invalid_topology", bowtie["reasons"])

    def test_w09_holes(self):
        plate = part_entry(self.inspect(self.plate(), "holes")["holes"])
        self.assertEqual(len(plate["holes"]), 1, plate)
        hole, want = plate["holes"][0], expected("aces_plate.hole")
        self.assertEqual((hole["kind"], hole["through"]), (want["kind"], want["through"]))
        self.assert_close(hole["diameter_mm"], want["diameter_mm"], 1e-6)
        self.assert_close(hole["depth_mm"], want["depth_mm"], 1e-6)
        self.assert_close(hole["entry_point_mm"], want["entry_point_mm"], 1e-6)
        self.assert_close(hole["axis_dir"], want["axis_dir"], 1e-9)
        self.assertRegex(hole["id"], r"^h[0-9]+$")
        self.assertTrue(all(f.startswith("f") for f in hole["faces"]))
        block = part_entry(self.inspect(self.run_script("holes_block")["run_id"], "holes")["holes"])
        holes = sorted(block["holes"], key=lambda h: h["entry_point_mm"][0])
        wanted = expected("holes_block.holes")
        self.assertEqual(len(holes), len(wanted), holes)
        for got, want in zip(holes, wanted):
            label = f"hole at x {want['entry_point_mm'][0]}"
            self.assertEqual((got["kind"], got["through"]), (want["kind"], want["through"]), label)
            self.assert_close(got["diameter_mm"], want["diameter_mm"], 1e-6, label)
            self.assert_close(got["depth_mm"], want["depth_mm"], 1e-6, label)
            self.assert_close(got["entry_point_mm"], want["entry_point_mm"], 1e-6, label)
            self.assert_close(got["axis_dir"], [0.0, 0.0, 1.0], 1e-9, label)
            for key in ("cbore", "csk"):
                if want[key] is None:
                    self.assertIsNone(got[key], label)
                else:
                    self.assertIsNotNone(got[key], label)
                    for k, v in want[key].items():
                        self.assert_close(got[key][k], v, 1e-6, f"{label} {key}.{k}")
        self.assertEqual(len(block["hole_groups"]), 4)
        checks = self.inspect(self.run_script("l_bracket")["run_id"], "holes", "radii")
        bracket = part_entry(checks["holes"])
        self.assertEqual(len(bracket["holes"]), expected("l_bracket.holes")["count"], "the fillet is not a hole")
        for h in bracket["holes"]:
            self.assert_close(h["diameter_mm"], expected("l_bracket.holes")["diameter_mm"], 1e-6)
            self.assertIs(h["through"], True)
        radii = part_entry(checks["radii"])
        self.assert_close(radii["min_concave_mm"], expected("l_bracket.fillet")["radius_mm"], 1e-6)

    def test_w10_thickness_topology_draft_section_measure(self):
        rid = self.plate()
        checks = self.inspect(rid, "thickness", "topology", "draft", "section", list_faces=True,
                              section={"axis": "y", "at_mm": 20})
        thick = part_entry(checks["thickness"])
        self.assert_expected(thick["min_mm"], "aces_plate.thickness_min_mm")
        self.assertIn("normal", checks["thickness"]["method"] + " " + checks["thickness"].get("note", ""))
        topo = part_entry(checks["topology"])
        self.assertEqual(topo["face_types"], {"PLANE": 6, "CYLINDER": 1})
        self.assertEqual(sum(topo["edge_types"].values()), expected("aces_plate.counts")["edges"])
        self.assertEqual([f["ref"] for f in topo["faces"]], [f"f{i}" for i in range(1, 8)])
        cyl = [f for f in topo["faces"] if f["type"] == "CYLINDER"]
        self.assertEqual(len(cyl), 1)
        self.assert_close(cyl[0]["radius_mm"], 5.0, 1e-9)
        draft = part_entry(checks["draft"])
        self.assertEqual(draft["counts"]["near_vertical"], 5, "four side faces and the hole")
        self.assertEqual(sum(draft["counts"].values()), 7)
        section = part_entry(checks["section"])
        self.assert_expected(section["area_mm2"], "aces_plate.section_y20.area_mm2")
        self.assert_expected(section["second_moments_mm4"]["I_u"], "aces_plate.section_y20.I_u_mm4")
        self.assert_expected(section["second_moments_mm4"]["I_v"], "aces_plate.section_y20.I_v_mm4")
        svgs = sorted((self.runs_dir() / rid / "inspect").glob("*-section-y-20.svg"))
        self.assertEqual(len(svgs), 1, "the section outline is written as an SVG")
        measure = self.inspect(rid, "measure", measure={"a": "plate:+z", "b": "plate:-z"})["measure"]
        self.assert_expected(measure["distance_mm"], "aces_plate.top_to_bottom_mm")
        self.assertIs(measure["parallel"], True)
        datums = self.run_script("datums_plate")["run_id"]
        measure = self.inspect(datums, "measure", measure={"a": "@top", "b": "plate:-z"})["measure"]
        self.assert_expected(measure["distance_mm"], "aces_plate.top_to_bottom_mm")
        self.assertIs(measure["parallel"], True)
        missing = self.op("inspect", project="p", run_id=datums, checks=["measure"],
                          measure={"a": "plate:f99", "b": "plate:-z"})["checks"]["measure"]
        self.assertIn("error", missing, "a check that raises is recorded and the call still succeeds")

    def test_w10_interference_and_design_space(self):
        pin = self.inspect(self.run_script("assembly_pin")["run_id"], "interference")["interference"]
        pair = pin["pairs"][0]
        self.assertEqual(pair["status"], "clear")
        self.assert_expected(pair["clearance_mm"], "assembly_pin.clearance_mm")
        self.assertTrue(pin["ok"])
        overlap = self.inspect(self.run_script("assembly_overlap")["run_id"], "interference")["interference"]
        pair = overlap["pairs"][0]
        self.assertEqual(pair["status"], "interfere")
        self.assert_expected(pair["intersection_mm3"], "assembly_overlap.intersection_mm3")
        self.assertFalse(overlap["ok"])
        single = self.inspect(self.plate(), "interference")["interference"]
        self.assertEqual(single.get("skipped"), "one part")
        disc = self.inspect(self.run_script("disc_domain")["run_id"], "design_space")["design_space"]
        self.assertTrue(disc["ok"], disc)
        self.assertEqual(disc["cells"], expected("disc_domain.design_space_gen4")["cells"])
        self.assertEqual(disc["void_box_limit"], 2000)
        busy = self.inspect(self.run_script("perforated_block")["run_id"], "design_space", gen_mm=0.5)["design_space"]
        want = expected("perforated_block.design_space_gen0.5")
        self.assertFalse(busy["ok"])
        self.assertEqual(busy["cells"], want["cells"])
        self.assertGreater(busy["void_boxes_upper_bound"], want["void_boxes_more_than"])


if __name__ == "__main__":
    unittest.main()
