"""golden.acescli against the real aces command (src/aces-base from this repository, else an installed aces) in a
temporary state: availability, projects, evidence and CAD references, ledger record / verify / find, refusals, and the
ACES_CMD convention. Skipped when neither aces is available. Every project and payload is a test input."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
ACES_BASE_SRC = Path(__file__).resolve().parents[2] / "aces-base"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from aces_engineering.golden.acescli import AcesCli, AcesCliError  # noqa: E402


def aces_command() -> tuple[list[str] | None, str | None]:
    """(argv0, PYTHONPATH entry): the repository's aces-base run by this interpreter, else `aces` on PATH."""
    if (ACES_BASE_SRC / "aces" / "cli.py").is_file():
        return [sys.executable, "-m", "aces.cli"], str(ACES_BASE_SRC)
    found = shutil.which("aces")
    return ([found], None) if found else (None, None)


class AcesCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.argv0, cls.pypath = aces_command()
        if cls.argv0 is None:
            raise unittest.SkipTest("no aces here: neither src/aces-base nor an installed aces-base")

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="g3-acescli-"))
        self._env = dict(os.environ)
        os.environ.update({"ACES_STATE": str(self.tmp / "state"), "ACES_WORK": str(self.tmp / "work"),
                           "ACES_USER_CONFIG": str(self.tmp / "cfg"), "ACES_CMD": json.dumps(self.argv0)})
        if self.pypath:
            os.environ["PYTHONPATH"] = os.pathsep.join([self.pypath] + ([self._env["PYTHONPATH"]] if self._env.get("PYTHONPATH") else []))
            os.environ["ACES_ETC"] = str(ACES_BASE_SRC / "etc")
        self.aces = AcesCli()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def payload(self, data) -> Path:
        p = self.tmp / "payload.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_probes(self) -> None:
        self.assertTrue(self.aces.available(), self.aces.problem)
        self.assertTrue(self.aces.supports_record())
        self.assertRegex(self.aces.version() or "", r"^\d+\.\d+\.\d+$")

    def test_projects_and_references(self) -> None:
        a = self.aces
        self.assertIsNone(a.project_show("g3-proj"))
        a.project_new("g3-proj", hypothesis="the organic part meets vm_p99 <= 60 MPa (test input) · 10 %")
        info = a.project_show("g3-proj")
        self.assertEqual(info["name"], "g3-proj")
        self.assertEqual(info["hypothesis"], "the organic part meets vm_p99 <= 60 MPa (test input) · 10 %")
        a.add_component("g3-proj", "aces-engineering")
        a.add_evidence("g3-proj", "golden/20261009T000000Z-abcdef bundle:0123456789abcdef PASS")
        a.add_evidence("g3-proj", "golden/20261009T000000Z-abcdef bundle:0123456789abcdef PASS")
        a.add_cad_source("g3-proj", "cad/part.step")
        info = a.project_show("g3-proj")
        self.assertEqual(info["components"], ["aces-engineering"])
        self.assertEqual(info["evidence"], ["golden/20261009T000000Z-abcdef bundle:0123456789abcdef PASS"])
        self.assertEqual(info["cad_sources"], ["cad/part.step"])
        with self.assertRaises(AcesCliError) as cm:
            a.project_new("g3-proj")
        self.assertIn("already exists", str(cm.exception))
        with self.assertRaises(AcesCliError):
            a.add_evidence("no-such-project", "x")

    def test_ledger(self) -> None:
        a = self.aces
        a.project_new("g3-proj")  # entry 1 (project.new)
        rec = a.ledger_record("golden.run", self.payload({"project": "g3-proj", "run_id": "r1", "verdict": "PASS"}))
        self.assertEqual(set(rec), {"recorded", "n", "kind", "ts", "hash", "prev", "path"})
        self.assertEqual((rec["n"], rec["kind"], rec["recorded"]), (2, "golden.run", True))
        v = a.ledger_verify()
        self.assertEqual((v["ok"], v["entries"], v["message"]), (True, 2, "chain intact"))
        found = a.ledger_find("golden.run")
        self.assertEqual([(e["n"], e["hash"]) for e in found], [(2, rec["hash"])])
        self.assertEqual(found[0]["payload"]["run_id"], "r1")
        self.assertEqual(a.ledger_find("other.kind"), [])
        with self.assertRaises(AcesCliError) as cm:
            a.ledger_record("policy.override", self.payload({"x": 1}))
        self.assertIn("is written by aces itself", str(cm.exception))
        with self.assertRaises(AcesCliError) as cm:
            a.ledger_record("golden.run", self.payload([1, 2]))
        self.assertIn("payload must be a JSON object", str(cm.exception))
        ledger = Path(rec["path"])  # a broken chain is reported, not raised
        lines = ledger.read_text(encoding="utf-8").splitlines()
        lines[1] = lines[1].replace('"r1"', '"r2"')
        ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
        v = a.ledger_verify()
        self.assertFalse(v["ok"])
        self.assertIn("entry 2", v["message"])

    def test_unavailable_commands(self) -> None:
        missing = AcesCli([str(self.tmp / "no-such-aces")])
        self.assertFalse(missing.available())
        self.assertIn("not found", missing.problem)
        self.assertFalse(missing.supports_record())
        self.assertIsNone(missing.version())
        with self.assertRaises(AcesCliError):
            missing.project_show("x")
        os.environ["ACES_CMD"] = "[not json"
        bad = AcesCli()
        self.assertFalse(bad.available())
        self.assertIn("ACES_CMD", bad.problem)
        os.environ["ACES_CMD"] = json.dumps([])
        self.assertFalse(AcesCli().available())

    def test_aces_cmd_as_one_path(self) -> None:
        if self.pypath:
            self.skipTest("the repository's aces-base runs through the interpreter (a JSON array), not one path")
        os.environ["ACES_CMD"] = self.argv0[0]
        self.assertTrue(AcesCli().available())


if __name__ == "__main__":
    unittest.main()
