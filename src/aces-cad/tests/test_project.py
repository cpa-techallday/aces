"""U03 of build spec section 21: the class gate and the bridge to the aces CLI (sections 5.1 and 17, aces_cad.project).

Every policy class is allowed and recorded; a null or unknown class, a missing project, a second class line and a
project other than the bound one are refused (kind project). The class comes from `aces project show --json` (the
fake aces CLI) or, without an aces CLI, from aces.yaml.
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from base import CadTestCase

CLASSES = ("personal", "open", "commercial", "sovereign", "controlled")


class ProjectTest(CadTestCase):

    def setUp(self):
        super().setUp()
        from aces_cad import errors, project
        self.errors, self.project = errors, project

    def refused(self, kind: str, fn, *args, **kwargs) -> dict:
        with self.assertRaises(self.errors.CadError) as ctx:
            fn(*args, **kwargs)
        err = ctx.exception.to_dict()
        self.assertEqual(err["kind"], kind, err)
        self.assertEqual(err["exit_code"], 2 if kind in ("project", "config", "usage") else err["exit_code"])
        return err

    def test_u03_every_class_is_allowed_and_returned(self):
        for cls in CLASSES:
            pdir = self.make_project(f"p-{cls}", cls)
            info = self.project.gate(f"p-{cls}")
            self.assertEqual(info["name"], f"p-{cls}")
            self.assertEqual(info["class"], cls)
            self.assertEqual(info["class_source"], "aces.yaml")
            self.assertEqual(Path(info["dir"]).resolve(), pdir.resolve())
        log = self.use_fake_aces("new")
        for cls in CLASSES:
            info = self.project.gate(f"p-{cls}")
            self.assertEqual((info["class"], info["class_source"]), (cls, "aces project show"))
        calls = self.fake_aces_calls(log)
        self.assertIn(["project", "show", "--json", "p-sovereign"], [c["argv"] for c in calls])

    def test_u03_null_unknown_and_two_class_lines_are_refused(self):
        self.make_project("pnull", None)
        err = self.refused("project", self.project.gate, "pnull")
        self.assertIn("aces project classify", err["message"])
        self.make_project("pempty", None, class_line="class: ")
        self.refused("project", self.project.gate, "pempty")
        self.make_project("psecret", "secret")
        err = self.refused("project", self.project.gate, "psecret")
        self.assertIn("aces project classify", err["message"])
        self.make_project("ptwo", "open", extra_lines="class: sovereign\n")
        self.refused("project", self.project.gate, "ptwo")
        self.use_fake_aces("new")
        self.refused("project", self.project.gate, "pnull")
        self.refused("project", self.project.gate, "psecret")

    def test_u03_missing_project_and_bad_names(self):
        err = self.refused("project", self.project.gate, "nowhere")
        self.assertIn("aces project new nowhere", err["message"])
        for cls in ("personal", "sovereign", "controlled"):
            self.assertIn(cls, err["message"])
        for bad in ("../escape", "-dash", "a" * 65, "with space", "x/y"):
            self.refused("project", self.project.gate, bad)

    def test_u03_bound_project(self):
        self.make_project("a", "sovereign")
        self.make_project("b", "open")
        err = self.refused("project", self.project.gate, None)
        self.assertIn("ACES_CAD_PROJECT", err["message"])
        os.environ["ACES_CAD_PROJECT"] = "a"
        self.assertEqual(self.project.gate(None)["name"], "a")
        self.assertEqual(self.project.gate("a")["name"], "a")
        err = self.refused("project", self.project.gate, "b")
        self.assertIn("bound to project a", err["message"])

    def test_u03_aces_work_reaches_the_aces_cli(self):
        # ACES_WORK set but empty counts as unset (~/Work); the aces CLI must get aces-cad's own work dir, or the fake
        # (which reads ACES_WORK) would look relative to its working directory and the class would come from aces.yaml
        os.environ["ACES_WORK"] = ""
        home_work = Path(os.environ["HOME"]) / "Work"
        pdir = home_work / "w"
        pdir.mkdir(parents=True)
        (pdir / "aces.yaml").write_text("name: w\nclass: controlled\ncad_sources: []\n", encoding="utf-8")
        self.use_fake_aces("new")
        info = self.project.gate("w")
        self.assertEqual((info["class"], info["class_source"]), ("controlled", "aces project show"))
        self.assertEqual(Path(info["dir"]).resolve(), pdir.resolve())

    def test_u03_aces_cli_settings(self):
        os.environ["ACES_CAD_ACES_CMD"] = "{not an array}"
        self.make_project("p", "open")
        with self.assertRaises(self.errors.CadError) as ctx:
            self.project.ledger_capability()
            self.project.gate("p")
        self.assertEqual(ctx.exception.kind, "config")

    def test_u03_ledger_capability_and_record_new_and_old(self):
        self.make_project("p", "sovereign")
        cap = self.project.ledger_capability()
        self.assertEqual((cap["available"], cap["print_json"]), (False, False))
        self.assertTrue(cap["reason"])
        self.assertFalse(self.project.add_cad_source("p", "cad/local/x/part.step")["recorded"])
        log = self.use_fake_aces("new")
        cap = self.project.ledger_capability()
        self.assertEqual((cap["available"], cap["reason"], cap["print_json"]), (True, None, True))
        payload = {"source": "build123d", "tool": "aces-cad", "project": "p", "class": "sovereign",
                   "note": "fixture payload"}
        done = self.project.ledger_record("cad.pull", payload, capability=cap)
        self.assertTrue(done["written"], done)
        self.assertEqual(done["n"], 1)
        record_calls = [c for c in self.fake_aces_calls(log) if c["argv"][:2] == ["ledger", "record"]
                        and "--help" not in c["argv"]]
        self.assertEqual(len(record_calls), 1)
        self.assertEqual(record_calls[0]["argv"][:5], ["ledger", "record", "cad.pull", "--json", "-"])
        self.assertEqual(json.loads(record_calls[0]["stdin"]), payload)
        added = self.project.add_cad_source("p", "cad/local/20261010T134002Z-plate/part.step")
        self.assertEqual(added, {"recorded": True, "ref": "cad/local/20261010T134002Z-plate/part.step"})
        self.use_fake_aces("old")
        cap = self.project.ledger_capability()
        self.assertFalse(cap["available"])
        self.assertIn("ledger record", cap["reason"])
        done = self.project.ledger_record("cad.pull", payload, capability=cap)
        self.assertFalse(done["written"])
        self.assertTrue(done["reason"])
        added = self.project.add_cad_source("p", "cad/local/x/part.step")
        self.assertFalse(added["recorded"])
        self.assertIn("add-cad-source", added["reason"])

    def test_u03_run_aces_without_cli_is_none(self):
        self.assertIsNone(self.project.run_aces(["--version"]))


if __name__ == "__main__":
    unittest.main()
