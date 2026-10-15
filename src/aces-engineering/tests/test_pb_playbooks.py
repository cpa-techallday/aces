"""B5 unit tests: the playbook library (aces_engineering/playbooks/*.yaml) — every playbook
parses, validates against the schema, references only known registry rows (missing rows are
valid only on declared-pending stages), and has satisfiable gates. Golden tests per design
doc section 5; stdlib unittest."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (_HERE.parent / "lib", _HERE.parent / "vendor"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.append(str(_p))

from aces_engineering.playbooks import (CLASSES, SCHEMA, SOLVERS_AVAILABLE, SOLVERS_INCOMING,  # noqa: E402
                                        solver_status)
from aces_engineering.playbooks import library, validate  # noqa: E402

# the stage skeletons the design doc pins (order is data; these are the load-bearing ids)
STAGE_SKELETONS = {
    "structure": ["baseline-fea", "simp-topopt", "threshold-sweep", "implicit-reconstruction",
                  "voxel-fea-recheck", "conforming-check", "score"],
    "flow-device": ["param-geometry", "doe-lhs", "cfd-sweep", "surrogate-fit", "pareto-search",
                    "knee-recheck", "score"],
    "heat-rejection": ["heat-load-spec", "flow-fields", "cht-doe", "surrogate-fit", "pareto-search",
                       "cht-verify", "score"],
    "em-device": ["envelope-interfaces", "em-solve-doe", "surrogate-fit", "pareto-search",
                  "em-verify", "score"],
    "rotating-machine": ["duty-point-spec", "meanline-sizing", "throughflow-2d", "blade-cfd-doe",
                         "surrogate-fit", "pareto-search", "rotordynamics-gate",
                         "full-annulus-verify", "score"],
    "rocket-motor": ["design-point", "chemistry-check", "combustion-verify", "score"],
}


class Library(unittest.TestCase):
    def test_every_class_has_a_playbook_that_loads(self):
        all_pb = library.load_all()
        self.assertEqual(set(all_pb), set(CLASSES))
        for cls, pb in all_pb.items():
            self.assertEqual(pb["schema"], SCHEMA)
            self.assertEqual(pb["class"], cls)
            self.assertEqual(pb["version"], 1)
            self.assertTrue(pb["stages"], cls)

    def test_unknown_class_raises(self):
        with self.assertRaises(library.PlaybookError):
            library.load("spaceship")

    def test_stage_skeletons_match_the_design(self):
        for cls, ids in STAGE_SKELETONS.items():
            pb = library.load(cls)
            self.assertEqual([s["id"] for s in pb["stages"]], ids, cls)
            self.assertEqual(pb["stages"][-1]["id"], "score", cls)  # every playbook ends in scoring

    def test_every_stage_is_fully_declared(self):
        for cls, pb in library.load_all().items():
            for st in pb["stages"]:
                for key in validate.STAGE_KEYS:
                    self.assertIn(key, st, f"{cls}/{st.get('id')}: missing {key}")
                self.assertTrue(st["inputs"], f"{cls}/{st['id']}")
                self.assertTrue(st["outputs"], f"{cls}/{st['id']}")
                self.assertTrue(st["evidence"], f"{cls}/{st['id']}")


class SolverReferences(unittest.TestCase):
    def test_no_missing_solver_except_declared_pending(self):
        for cls, pb in library.load_all().items():
            for st in pb["stages"]:
                status = solver_status(st["solver"])
                if status == "missing":
                    self.assertIs(st.get("pending"), True,
                                  f"{cls}/{st['id']}: solver {st['solver']!r} missing without pending: true")

    def test_registry_vocabulary_covers_every_reference(self):
        referenced = {st["solver"] for pb in library.load_all().values() for st in pb["stages"]}
        known = SOLVERS_AVAILABLE | SOLVERS_INCOMING | {"none"}
        unknown = {s for s in referenced if solver_status(s) == "missing"}
        self.assertEqual(unknown, {"pelec-combustion"})  # the one declared-pending gap
        self.assertTrue(referenced <= known | unknown)

    def test_incoming_rows_are_used_and_named(self):
        used = {st["solver"] for pb in library.load_all().values() for st in pb["stages"]}
        for incoming in ("su2-rans", "openfoam-cht", "geom-reconstruct", "mdao-run",
                         "pymoo-nsga2", "rocket-cea", "cantera-check"):
            self.assertIn(incoming, used)


class Validation(unittest.TestCase):
    def test_shipped_playbooks_have_no_errors(self):
        report = validate.validate_all()
        self.assertTrue(validate.ok(report),
                        "; ".join(f"{c}: {r['errors']}" for c, r in report.items() if r["errors"]))

    def test_incoming_and_pending_are_notes_not_errors(self):
        report = validate.validate_all()
        notes = [n for r in report.values() for n in r["notes"]]
        self.assertTrue(any("incoming" in n for n in notes))
        self.assertTrue(any("pelec-combustion" in n and "pending" in n for n in notes))

    def test_schema_errors_are_caught(self):
        pb = library.load("structure")
        bad = {**pb, "schema": "aces-playbook/0"}
        errors, _ = validate.validate_playbook(bad, name="bad")
        self.assertTrue(any("schema" in e for e in errors))

    def test_gate_satisfiability_is_checked(self):
        pb = library.load("structure")
        bad_stage = dict(pb["stages"][0])
        bad_stage["gate"] = {"metric": "no_such_metric", "op": "<=", "limit": 1.0}
        errors = validate.validate_gate(bad_stage, (pb.get("spec") or {}).get("metrics") or [])
        self.assertTrue(any("not an output" in e for e in errors))
        bad_stage["gate"] = {"metric": "vm_p99_MPa", "op": "≈", "limit": 1.0}
        errors = validate.validate_gate(bad_stage, [])
        self.assertTrue(any("gate.op" in e for e in errors))
        bad_stage["gate"] = {"metric": "vm_p99_MPa", "op": "<=", "limit": "soon"}
        errors = validate.validate_gate(bad_stage, [])
        self.assertTrue(any("gate.limit" in e for e in errors))

    def test_last_stage_must_be_score(self):
        pb = library.load("structure")
        bad = dict(pb)
        bad["stages"] = list(reversed(pb["stages"]))
        errors, _ = validate.validate_playbook(bad, name="bad")
        self.assertTrue(any("last stage" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
