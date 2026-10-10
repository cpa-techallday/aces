"""W01-W06 of build spec section 21: cad_run with real build123d in the worker (sections 7 and 11.2).

Every expected number comes from tests/expected.json (analytic, or measured on 2026-10-10 on Windows and Arch).
"""
from __future__ import annotations

import hashlib
import json
import os
import unittest
from pathlib import Path

from base import POSIX, WorkerTestCase, expected


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class WorkerRunTest(WorkerTestCase):

    def assert_plate_part(self, part: dict) -> None:
        self.assertEqual(part["kind"], "solid")
        self.assert_expected(part["volume_mm3"], "aces_plate.volume_mm3")
        self.assert_expected(part["area_mm2"], "aces_plate.area_mm2")
        self.assert_expected(part["bbox_mm"], "aces_plate.bbox_mm")
        self.assert_close(part["size_mm"], [60.0, 40.0, 5.0], 1e-6)
        self.assert_expected(part["centroid_mm"], "aces_plate.centroid_mm")
        self.assertIs(part["valid"], True)
        self.assertEqual(part["reasons"], [])
        self.assertEqual(part["counts"], expected("aces_plate.counts"))

    def test_w01_aces_plate_defaults(self):
        text = self.script("aces_plate")
        out = self.run_script("aces_plate", name="plate")
        self.assert_run_id(out["run_id"])
        self.assertEqual((out["status"], out["name"], out["project"], out["entry"]), ("ok", "plate", "p", "build"))
        self.assertIsNone(out["from_run"])
        self.assertEqual(out["script"], {"source": "inline", "path": None, "sha256": sha(text),
                                         "bytes": len(text.encode("utf-8"))})
        self.assertEqual(out["params"], {"length": 60.0, "width": 40.0, "thickness": 5.0, "hole_d": 10.0})
        self.assertEqual(out["params_overridden"], [])
        self.assertEqual(out["params_declared"]["hole_d"], {"type": "float", "default": 10.0, "min": 1.0, "max": 30.0,
                                                            "unit": "mm", "doc": "centre hole diameter"})
        result = out["result"]
        self.assertEqual(result["kind"], "solid")
        self.assertEqual(len(result["parts"]), 1)
        self.assert_plate_part(result["parts"][0])
        self.assertEqual(result["datums"], [])
        self.assertEqual(result["totals"]["parts"], 1)
        self.assert_expected(result["totals"]["volume_mm3"], "aces_plate.volume_mm3")
        self.assertEqual(out["problems"], [])
        self.assertIn("wall", out["timings_s"])
        self.assertEqual(out["sandbox"]["mode"], self.mode)
        if self.mode == "strict":
            self.assertIs(out["sandbox"]["confined"], True)
            self.assertIs(out["sandbox"]["seccomp"]["active"], True)
            self.assertIs(out["sandbox"]["landlock"]["active"], True)
        run_dir = Path(out["run_dir"])
        self.assertEqual(run_dir, self.runs_dir() / out["run_id"])
        self.assertEqual((run_dir / "script.py").read_text(encoding="utf-8"), text)
        rec = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual((rec["schema"], rec["status"], rec["class"]), ("aces.cad.run/1", "ok", "sovereign"))
        self.assertEqual(rec["tool"], {"name": "aces-cad", "version": "0.1.0"})
        self.assertEqual(rec["params_sha256"], hashlib.sha256(json.dumps(out["params"], sort_keys=True,
                                                                         separators=(",", ":")).encode()).hexdigest())
        self.assertEqual(sorted(p.name for p in (run_dir / "out").iterdir()), ["p0.brep", "parts.json"])
        jobs = run_dir / "jobs"
        self.assertEqual(sorted(jobs.iterdir()) if jobs.exists() else [], [], "the job directory is removed")
        if POSIX:
            self.assertEqual(run_dir.stat().st_mode & 0o777, 0o700)

    def test_w02_overrides_and_from_run(self):
        first = self.run_script("aces_plate", name="plate", params={"length": 70})
        self.assertEqual(first["params"]["length"], 70.0)
        self.assertEqual(first["params_overridden"], ["length"])
        self.assert_expected(first["result"]["parts"][0]["volume_mm3"], "aces_plate.length70_volume_mm3")
        second = self.op("run", project="p", from_run=first["run_id"], params={"width": 50})
        self.assertEqual(second["script"]["source"], "from_run")
        self.assertEqual(second["from_run"], first["run_id"])
        self.assertEqual(second["name"], "plate", "the name defaults to the source run's name")
        self.assertEqual((second["params"]["length"], second["params"]["width"]), (70.0, 50.0))
        self.assertEqual(second["params_overridden"], ["length", "width"])
        self.assert_expected(second["result"]["parts"][0]["volume_mm3"], "aces_plate.length70_width50_volume_mm3")
        third = self.op("run", project="p", from_run="latest", params={"length": 60})
        self.assertEqual(third["from_run"], second["run_id"])
        self.assertEqual((third["params"]["length"], third["params"]["width"]), (60.0, 50.0))

    def test_w03_two_hole_plate_bracket_loft_and_gen_step(self):
        two = self.run_script("two_hole_plate")["result"]["parts"][0]
        self.assert_expected(two["volume_mm3"], "two_hole_plate.volume_mm3")
        self.assert_expected(two["bbox_mm"], "two_hole_plate.bbox_mm")
        self.assertEqual(two["counts"], expected("two_hole_plate.counts"))
        self.op_error("run", "params", project="p", script=self.script("two_hole_plate"), params={"x": 1.0})
        bracket = self.run_script("l_bracket")["result"]["parts"][0]
        self.assert_expected(bracket["volume_mm3"], "l_bracket.volume_mm3")
        self.assert_expected(bracket["bbox_mm"], "l_bracket.bbox_mm")
        self.assertIs(bracket["valid"], True)
        loft = self.run_script("loft_blend")["result"]["parts"][0]
        self.assert_expected(loft["volume_mm3"], "loft_blend.volume_mm3")
        self.assertEqual(loft["counts"]["faces"], expected("loft_blend.faces"))
        gen = self.run_script("gen_step_plate")
        self.assertEqual(gen["entry"], "gen_step")
        self.assert_expected(gen["result"]["parts"][0]["volume_mm3"], "aces_plate.volume_mm3")
        self.assertTrue(any("gen_step envelope" in w for w in gen["warnings"]), gen["warnings"])

    def test_w04_assembly_sketch_and_mesh(self):
        asm = self.run_script("assembly_pin")["result"]
        self.assertEqual(asm["kind"], "assembly")
        self.assertEqual(sorted(p["name"] for p in asm["parts"]), ["base", "pin"])
        self.assertEqual(asm["totals"]["parts"], 2)
        sketch = self.run_script("sketch_plate")["result"]
        self.assertEqual(sketch["kind"], "sketch")
        part = sketch["parts"][0]
        self.assertEqual(part["kind"], "sketch")
        self.assertIsNone(part["volume_mm3"])
        self.assert_expected(part["area_mm2"], "sketch_plate.area_mm2")
        mesh = self.run_script("sdf_sphere")["result"]
        self.assertEqual(mesh["kind"], "mesh")
        mpart = mesh["parts"][0]
        self.assertIs(mpart["watertight"], True)
        self.assert_expected(mpart["volume_mm3"], "sdf_sphere.exact_volume_mm3")
        self.assertEqual(sorted(mpart["counts"]), ["faces", "vertices"])
        datums = self.run_script("datums_plate")["result"]
        self.assertEqual(datums["kind"], "solid")
        self.assertEqual([d["name"] for d in datums["datums"]], ["@top"])

    def test_w04b_grid_values_on_the_level_do_not_open_a_mesh(self):
        """mesh_from_sdf counts grid values exactly on the level as outside (review finding 3). The skill's first gyroid
        example (h = cell / 16, its clip-box faces on grid planes) had 864 edges shared by more than two triangles and
        was not watertight; it is closed now."""
        text = self.script("skill_gyroid")
        coarse = text.replace("h=min(p.cell / 16, p.wall / 4)", "h=p.cell / 16")
        self.assertNotEqual(coarse, text)
        part = self.op("run", project="p", script=coarse, name="gyroid-coarse")["result"]["parts"][0]
        self.assertIs(part["watertight"], True, part)
        self.assertIs(part["valid"], True, part)

    def test_w05_errors(self):
        runs = self.runs_dir()
        err = self.run_error("syntax_error", "script")
        self.assertEqual(err["line"], expected("errors.syntax_error")["line"])
        self.assertIn("offset", err)
        self.assertNotIn("run_id", err, "a syntax error spawns nothing and makes no run")
        self.assertFalse(runs.exists() and any(runs.iterdir()), "no run directory for a script that does not parse")
        for name in ("params_not_literal", "params_bad_key"):
            self.run_error(name, "params")
        self.assertFalse(runs.exists() and any(runs.iterdir()), "no run directory for bad PARAMS")
        self.op_error("run", "params", project="p", script=self.script("aces_plate"), params={"lenght": 70.0})
        err = self.run_error("name_error", "script")
        self.assert_run_id(err["run_id"])
        self.assertIn("undefined_part_name", err["message"])
        self.assertEqual(err["exception"], "NameError")
        self.assertTrue(err["frames"], err)
        for frame in err["frames"]:
            self.assertEqual(sorted(frame), ["code", "function", "line"])
            self.assertIn(frame["function"], ("<module>", "build", "shape_of"), "frames of script.py only")
        self.assertIn("shape_of", [f["function"] for f in err["frames"]])
        self.assertLessEqual(len(err["frames"]), 8)
        rec = json.loads((runs / err["run_id"] / "run.json").read_text(encoding="utf-8"))
        self.assertEqual((rec["status"], rec["error"]["kind"]), ("error", "script"))
        err = self.run_error("fillet_too_big", "kernel")
        self.assertTrue(err["message"].startswith(expected("errors.fillet_too_big")["message_prefix"]), err)
        self.assertIn("radius is too large", err["hint"])
        self.assertTrue(err.get("raised_in"), err)
        err = self.run_error("zero_extrude", "kernel")
        self.assertEqual(err["exception"], expected("errors.zero_extrude")["exception"])
        self.assertIn(expected("errors.zero_extrude")["message"], err["message"])
        self.assertIn("zero length", err["hint"])
        err = self.run_error("empty_boolean", "result")
        self.assertIn("the result is empty", err["message"])
        err = self.run_error("returns_builder", "result")
        self.assertIn("return p.part", err.get("hint", "") + err["message"])
        err = self.run_error("returns_none", "result")
        self.assertIn("None", err["message"], "the message names the type")
        err = self.run_error("returns_shell", "result")
        self.assertIn("open shell", err["message"])
        for e in (err,):
            self.assertEqual(e["exit_code"], 1)

    def test_w06_script_path_and_inputs(self):
        pdir = Path(os.environ["ACES_WORK"]) / "p"
        src = pdir / "cad" / "src"
        src.mkdir(parents=True)
        (src / "plate.py").write_text(self.script("aces_plate"), encoding="utf-8", newline="\n")
        out = self.op("run", project="p", script_path="cad/src/plate.py", name="plate")
        self.assertEqual(out["script"]["source"], "path")
        self.assertEqual(out["script"]["path"], "cad/src/plate.py")
        self.assert_expected(out["result"]["parts"][0]["volume_mm3"], "aces_plate.volume_mm3")
        vendor = self.make_step(pdir / "cad" / "vendor" / "part.step", label="vendor-part", size=10.0)
        vendor_sha = hashlib.sha256(vendor.read_bytes()).hexdigest()
        (src / "import_input.py").write_text(self.script("import_input"), encoding="utf-8", newline="\n")
        imported = self.op("run", project="p", script_path="cad/src/import_input.py",
                           inputs={"vendor": "cad/vendor/part.step"})
        self.assertAlmostEqual(imported["result"]["totals"]["volume_mm3"], 1000.0, delta=1e-6)
        self.assertEqual(imported["inputs"]["vendor"], {"path": "cad/vendor/part.step", "file": "inputs/vendor.step",
                                                        "sha256": vendor_sha, "bytes": vendor.stat().st_size})
        copy = Path(imported["run_dir"]) / "inputs" / "vendor.step"
        self.assertEqual(hashlib.sha256(copy.read_bytes()).hexdigest(), vendor_sha)
        again = self.op("run", project="p", from_run=imported["run_id"])
        self.assertEqual(again["inputs"]["vendor"]["sha256"], vendor_sha, "from_run reuses the run's inputs")
        self.assertAlmostEqual(again["result"]["totals"]["volume_mm3"], 1000.0, delta=1e-6)
        self.op_error("run", "usage", project="p", script_path="../outside.py")
        self.op_error("run", "usage", project="p", script_path="cad/src/plate.py",
                      inputs={"vendor": "cad/vendor/part.exe"})
        self.op_error("run", ("usage", "not_found"), project="p", script_path="cad/src/missing.py")
        try:
            os.symlink(src / "plate.py", src / "link.py")
            os.symlink(vendor, pdir / "cad" / "vendor" / "link.step")
        except (OSError, NotImplementedError) as e:
            self.skipTest(f"cannot create symlinks here ({e})")
        self.op_error("run", "usage", project="p", script_path="cad/src/link.py")
        self.op_error("run", "usage", project="p", script_path="cad/src/import_input.py",
                      inputs={"vendor": "cad/vendor/link.step"})


if __name__ == "__main__":
    unittest.main()
