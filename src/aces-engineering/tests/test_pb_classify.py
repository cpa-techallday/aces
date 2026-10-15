"""B5 unit tests: the brief classifier (aces_engineering.playbooks.classify) — one case per
part class, the owner's canonical chassis brief, ambiguous and empty briefs, and the
determinism / no-LLM contract. stdlib unittest; every brief here is a test input."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (_HERE.parent / "lib", _HERE.parent / "vendor"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.append(str(_p))

from aces_engineering.playbooks import CLASSES, DEFAULT_CLASS  # noqa: E402
from aces_engineering.playbooks.classify import classify  # noqa: E402

CANONICAL = "optimise this chassis, 1500 kg, six-wheeled off-roader, electric drive, 50 kWh battery"


class PerClass(unittest.TestCase):
    def assertClass(self, brief: str, cls: str):  # noqa: N802 (unittest-style helper)
        r = classify(brief)
        self.assertEqual(r.cls, cls, f"{brief!r}: scores {r.scores}")
        self.assertFalse(r.low_confidence, f"{brief!r} unexpectedly low confidence: {r.evidence}")
        self.assertTrue(r.evidence)
        return r

    def test_structure(self):
        self.assertClass("optimise this suspension upright bracket, 2 kN vertical load, 250 MPa stress cap",
                         "structure")

    def test_canonical_chassis_brief(self):
        r = self.assertClass(CANONICAL, "structure")
        self.assertGreaterEqual(r.confidence, 0.8)
        self.assertTrue(any("chassis" in e for e in r.evidence))

    def test_flow_device(self):
        self.assertClass("design an intake duct for the cooling airflow, minimise the pressure drop",
                         "flow-device")

    def test_flow_device_manifold(self):
        self.assertClass("optimise this exhaust manifold for uniform mass flow", "flow-device")

    def test_heat_rejection(self):
        self.assertClass("design a cold plate to cool the inverter, 800 W heat load", "heat-rejection")

    def test_heat_rejection_hx(self):
        self.assertClass("optimise this heat exchanger, coolant water at 40 °C", "heat-rejection")

    def test_em_device(self):
        self.assertClass("design a patch antenna for 5.8 GHz with S11 below -15 dB", "em-device")

    def test_em_device_radome(self):
        self.assertClass("optimise this radome and waveguide feed for the radar band", "em-device")

    def test_rotating_machine(self):
        self.assertClass("design a centrifugal compressor impeller, 40000 rpm, pressure ratio 3",
                         "rotating-machine")

    def test_rocket_motor(self):
        self.assertClass("design a rocket motor, 5 kN thrust, LOX/RP-1, chamber pressure 100 bar",
                         "rocket-motor")

    def test_rocket_beats_flow_on_nozzle(self):
        # 'nozzle' alone leans flow-device; with rocket vocabulary the rocket class must win
        self.assertClass("optimise this rocket nozzle, 2 kN thrust, 300 s Isp", "rocket-motor")


class AmbiguousAndEmpty(unittest.TestCase):
    def test_unknown_defaults_to_structure_low_confidence(self):
        r = classify("optimise this thing please")
        self.assertEqual(r.cls, DEFAULT_CLASS)
        self.assertTrue(r.low_confidence)
        self.assertEqual(r.confidence, 0.0)
        self.assertTrue(any("no class vocabulary matched" in e for e in r.evidence))

    def test_empty_brief(self):
        r = classify("")
        self.assertEqual(r.cls, DEFAULT_CLASS)
        self.assertTrue(r.low_confidence)

    def test_tie_defaults_to_structure_low_confidence(self):
        # 'duct' (3, flow-device) vs 'chassis' (3, structure): an exact tie is a guess
        r = classify("a duct chassis")
        self.assertEqual(r.cls, DEFAULT_CLASS)
        self.assertTrue(r.low_confidence)
        self.assertEqual(r.scores["flow-device"], r.scores["structure"])

    def test_weak_match_is_low_confidence(self):
        # a single weak hint ('beam', +1) is below the confident threshold
        r = classify("optimise this beam")
        self.assertEqual(r.cls, DEFAULT_CLASS)
        self.assertTrue(r.low_confidence)


class Contract(unittest.TestCase):
    def test_deterministic(self):
        a, b = classify(CANONICAL), classify(CANONICAL)
        self.assertEqual(a.as_dict(), b.as_dict())

    def test_scores_cover_every_class(self):
        self.assertEqual(set(classify(CANONICAL).scores), set(CLASSES))

    def test_never_raises_on_text(self):
        for brief in (None, "   ", "!!! *** ---", "χ σ τ", "a" * 10000):
            r = classify(brief or "")
            self.assertIn(r.cls, CLASSES)


if __name__ == "__main__":
    unittest.main()
