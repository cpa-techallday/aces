"""B5 unit tests: the planner (aces_engineering.playbooks.plan.plan_from_brief) and the
`aces golden plan` CLI — the canonical chassis brief, unit/envelope extraction, conservative
defaults, blocked-stage behaviour on a missing solver, and the plan document's shape.
stdlib unittest; every brief here is a test input, nothing is a result of the golden path."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (_HERE.parent / "lib", _HERE.parent / "vendor"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.append(str(_p))

from aces_engineering import golden_cli  # noqa: E402
from aces_engineering.playbooks import PLAN_SCHEMA, solver_status  # noqa: E402
from aces_engineering.playbooks.plan import extract_parameters, plan_from_brief  # noqa: E402

CANONICAL = "optimise this chassis, 1500 kg, six-wheeled off-roader, electric drive, 50 kWh battery"


class CanonicalBrief(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = plan_from_brief(CANONICAL)

    def test_structure_class_with_confidence(self):
        p = self.plan
        self.assertEqual(p["class"], "structure")
        self.assertFalse(p["low_confidence"])
        self.assertGreaterEqual(p["confidence"], 0.8)
        self.assertTrue(any("chassis" in e for e in p["class_evidence"]))

    def test_mass_and_energy_extracted(self):
        params = self.plan["parameters"]
        self.assertEqual(params["mass_kg"]["value"], 1500.0)
        self.assertEqual(params["energy_Wh"]["value"], 50000.0)
        self.assertEqual(params["wheels"]["value"], 6)
        # the extracted mass lands on the playbook's mass target
        self.assertEqual(self.plan["spec"]["targets"]["mass_kg"], 1500.0)

    def test_envelope_falls_back_to_defaults_with_a_note(self):
        # the brief gives no dimensions: nothing is invented — the default stands, noted
        self.assertNotIn("envelope_mm", self.plan["parameters"])
        env = self.plan["spec"]["defaults"]["envelope_mm"]
        self.assertEqual(env["value"], [500.0, 500.0, 300.0])
        self.assertIn("give dimensions", env["note"])

    def test_stage_graph_and_schema(self):
        p = self.plan
        self.assertEqual(p["schema"], PLAN_SCHEMA)
        self.assertTrue(p["plan_id"].startswith("plan-structure-"))
        self.assertEqual([s["id"] for s in p["stages"]],
                         ["baseline-fea", "simp-topopt", "threshold-sweep", "implicit-reconstruction",
                          "voxel-fea-recheck", "conforming-check", "score"])
        for s in p["stages"]:
            self.assertIn(s["solver_status"], ("available", "incoming", "missing"))
            self.assertEqual(s["parameters"]["mass_kg"], 1500.0)
        # geom-reconstruct is incoming: named as such, not blocked
        recon = next(s for s in p["stages"] if s["id"] == "implicit-reconstruction")
        self.assertEqual((recon["solver"], recon["solver_status"]), ("geom-reconstruct", "incoming"))

    def test_nothing_blocked_and_json_serialisable(self):
        self.assertEqual(self.plan["blocked"], [])
        json.dumps(self.plan)  # the whole document must round-trip through json

    def test_deterministic_plan_id(self):
        self.assertEqual(plan_from_brief(CANONICAL)["plan_id"], self.plan["plan_id"])


class ParameterExtraction(unittest.TestCase):
    def test_units_convert_to_canonical(self):
        p = extract_parameters("2 kN load, 250 MPa cap, 100 bar chamber, 5.8 GHz, 40000 rpm, 0.5 t")
        self.assertEqual(p["force_N"]["value"], 2000.0)
        self.assertEqual(p["pressure_Pa"]["value"], 250e6)  # first pressure wins
        self.assertEqual(p["frequency_Hz"]["value"], 5.8e9)
        self.assertEqual(p["speed_rpm"]["value"], 40000.0)
        self.assertEqual(p["mass_kg"]["value"], 500.0)

    def test_envelope_triple(self):
        p = extract_parameters("envelope 1200 x 800 x 400 mm")
        self.assertEqual(p["envelope_mm"]["value"], [1200.0, 800.0, 400.0])
        p = extract_parameters("within 0.4 m x 30 cm x 200 mm overall")
        self.assertEqual(p["envelope_mm"]["value"], [400.0, 300.0, 200.0])

    def test_materials_and_no_invention(self):
        p = extract_parameters("a titanium bracket")
        self.assertEqual(p["materials"]["value"], ["titanium"])
        self.assertEqual(extract_parameters("optimise this thing"), {})

    def test_targets_alias_per_class(self):
        rocket = plan_from_brief("design a rocket motor, 5 kN thrust, chamber pressure 100 bar")
        self.assertEqual(rocket["class"], "rocket-motor")
        self.assertEqual(rocket["spec"]["targets"]["thrust_N"], 5000.0)
        self.assertEqual(rocket["spec"]["targets"]["chamber_pressure_Pa"], 100e5)
        heat = plan_from_brief("design a cold plate to cool the inverter, 800 W heat load")
        self.assertEqual(heat["class"], "heat-rejection")
        self.assertEqual(heat["spec"]["targets"]["heat_load_W"], 800.0)


class BlockedStages(unittest.TestCase):
    def test_combustion_verify_is_available_since_the_pelec_image_landed(self):
        # pelec-combustion is registered in cudax-sim-runtime (simlab/solvers.d/pelec.json,
        # aces-pelec:0.1 Sod GPU-validated 2026-10-15): the stage runs, nothing is blocked
        p = plan_from_brief("design a rocket motor, 5 kN thrust, LOX/RP-1")
        self.assertEqual(p["blocked"], [])
        stage = next(s for s in p["stages"] if s["id"] == "combustion-verify")
        self.assertEqual((stage["solver"], stage["solver_status"]), ("pelec-combustion", "available"))
        self.assertFalse(stage["pending"])
        self.assertFalse(any("blocked" in w for w in p["warnings"]))
        # the design-point and chemistry stages are still incoming rows, named as such
        self.assertEqual([s["solver_status"] for s in p["stages"][:2]], ["incoming", "incoming"])

    def test_missing_solver_still_blocks(self):
        # the blocked path itself: a solver outside the vocabulary is missing, and a plan
        # stage referencing one is named in blocked, never skipped (pinned via solver_status)
        self.assertEqual(solver_status("no-such-solver"), "missing")
        self.assertEqual(solver_status("pelec-combustion"), "available")

    def test_low_confidence_warns(self):
        p = plan_from_brief("optimise this thing please")
        self.assertEqual(p["class"], "structure")
        self.assertTrue(p["low_confidence"])
        self.assertTrue(any("low confidence" in w for w in p["warnings"]))


class GoldenPlanCli(unittest.TestCase):
    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = golden_cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_json_document(self):
        code, out, _ = self.run_cli(["plan", "--json", CANONICAL])
        self.assertEqual(code, 0)
        doc = json.loads(out)  # exactly one JSON document
        self.assertEqual(doc["class"], "structure")
        self.assertEqual(doc["parameters"]["mass_kg"]["value"], 1500.0)
        self.assertEqual(doc["schema"], PLAN_SCHEMA)

    def test_human_output_and_attach(self):
        code, out, _ = self.run_cli(["plan", CANONICAL, "--attach", "cad/chassis.step"])
        self.assertEqual(code, 0)
        self.assertIn("class    structure", out)
        self.assertIn("cad/chassis.step", out)
        self.assertIn("implicit-reconstruction", out)
        self.assertIn("[incoming solver]", out)

    def test_empty_brief_is_usage(self):
        code, _, err = self.run_cli(["plan", "   "])
        self.assertEqual(code, 2)
        self.assertIn("needs a brief", err)

    def test_combustion_verify_shown_unblocked(self):
        code, out, _ = self.run_cli(["plan", "design a rocket motor, 5 kN thrust"])
        self.assertEqual(code, 0)
        self.assertIn("combustion-verify", out)
        self.assertIn("pelec-combustion", out)
        self.assertNotIn("[BLOCKED: solver missing]", out)

    def test_help_lists_plan(self):
        code, out, _ = self.run_cli([])
        self.assertEqual(code, 0)
        self.assertIn("plan", out)


if __name__ == "__main__":
    unittest.main()
