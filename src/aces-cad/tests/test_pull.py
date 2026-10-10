"""W13 of build spec section 21: cad_pull and the CadSource bundle (section 15) with real build123d.

The bundle's file list, every sha256 against source.json (also through tests/check_bundle.py), source.json left
unchanged by the outcome rewrite, the cad.pull ledger payload and add-cad-source through the fake aces CLI (new mode)
and their absence with an aces-base that lacks them (old mode), the STEP re-imported with the run's volume, a stable
geometry_sha256, mesh and sketch pulls, failed, invalid and tampered runs.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import base
from base import WorkerTestCase, expected

LEDGER_KEYS = {"source", "tool", "tool_version", "ref", "project", "class", "bundle", "run_id", "name", "kind", "valid",
               "exported_at", "script_sha256", "params_sha256", "geometry_sha256", "files", "versions"}
STEP_VOLUME = """
import json, sys
from build123d import import_step
shape = import_step(sys.argv[1])
print(json.dumps({"volume": shape.volume, "solids": len(shape.solids())}))
"""


def sha_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class PullTest(WorkerTestCase):

    def pull(self, run_id: str, **kw) -> dict:
        out = self.op("pull", project="p", run_id=run_id, **kw)
        bundle = Path(out["bundle"])
        self.assertTrue(bundle.is_dir())
        self.assertEqual(bundle.parent, Path(os.environ["ACES_WORK"]) / "p" / "cad" / "local")
        self.assertEqual([p.name for p in bundle.parent.iterdir() if p.name.endswith(".partial")], [])
        return out

    def check_bundle(self, pull: dict) -> subprocess.CompletedProcess:
        path = self.tmp / "pull.json"
        path.write_text(json.dumps(pull), encoding="utf-8")
        return subprocess.run([sys.executable, str(base.CHECK_BUNDLE), str(path)], capture_output=True, timeout=120)

    def test_w13_the_bundle_and_the_ledger(self):
        log = self.use_fake_aces("new")
        text = self.script("aces_plate")
        run = self.run_script("aces_plate", name="plate")
        out = self.pull(run["run_id"])
        bundle = Path(out["bundle"])
        self.assertRegex(bundle.name, r"^[0-9]{8}T[0-9]{6}Z-plate(-[0-9]+)?$")
        self.assertEqual(sorted(p.name for p in bundle.iterdir()),
                         ["outcome.json", "parameters.json", "part.3mf", "part.step", "part.stl", "source.json",
                          "source.py"])
        done = self.check_bundle(out)
        self.assertEqual(done.returncode, 0, done.stdout.decode() + done.stderr.decode())
        source = json.loads((bundle / "source.json").read_text(encoding="utf-8"))
        self.assertEqual((source["schema"], source["source"], source["tool"]),
                         ("aces.cad.local.source/1", "build123d", {"name": "aces-cad", "version": "0.1.0"}))
        self.assertEqual(source["ref"], f"aces-cad://p/runs/{run['run_id']}")
        self.assertEqual((source["run_id"], source["name"], source["kind"]), (run["run_id"], "plate", "solid"))
        self.assertEqual(source["script_sha256"], hashlib.sha256(text.encode("utf-8")).hexdigest())
        self.assertEqual(source["params_sha256"], json.loads((bundle / "parameters.json").read_text())["params_sha256"])
        self.assertEqual(source["geometry_sha256"], sha_file(bundle / "part.step"))
        self.assertEqual(out["geometry_sha256"], source["geometry_sha256"])
        for name, info in source["files"].items():
            self.assertEqual(info["sha256"], sha_file(bundle / name), name)
            self.assertEqual(info["bytes"], (bundle / name).stat().st_size, name)
        self.assertEqual(sorted(source["files"]), ["parameters.json", "part.3mf", "part.step", "part.stl",
                                                   "source.py"])
        part = source["result"]["parts"][0]
        self.assert_expected(part["volume_mm3"], "aces_plate.volume_mm3")
        self.assertIs(part["valid"], True)
        self.assertEqual(source["result"]["derived_by"], "export worker (no agent code)")
        self.assertEqual(source["export"]["step_header_time"], "2000-01-01T00:00:00")
        self.assertIs(source["export"]["3mf_reproducible"], False)
        self.assertEqual(source["ledger"], {"kind": "cad.pull", "available": True, "reason": None})
        self.assertEqual((source["project"]["name"], source["project"]["class"]), ("p", "sovereign"))
        self.assertIs(source["allow_invalid"], False)
        self.assertEqual((bundle / "source.py").read_text(encoding="utf-8"), text)
        params = json.loads((bundle / "parameters.json").read_text(encoding="utf-8"))
        self.assertEqual(params["schema"], "aces.cad.local.parameters/1")
        self.assertEqual((params["params"], params["entry"]), (run["params"], "build"))
        outcome = json.loads((bundle / "outcome.json").read_text(encoding="utf-8"))
        self.assertEqual(outcome["files"]["source.json"]["sha256"], sha_file(bundle / "source.json"),
                         "outcome.json holds the sha256 of source.json, which the rewrite left unchanged")
        self.assertTrue(outcome["ledger"]["written"])
        self.assertEqual(outcome["ledger"]["kind"], "cad.pull")
        self.assertEqual(outcome["cad_source"], {"recorded": True, "ref": f"cad/local/{bundle.name}/part.step"})
        self.assertIn("updated_at", outcome)
        self.assertEqual(out["ledger"]["written"], True)
        self.assertEqual(out["cad_source"]["ref"], f"cad/local/{bundle.name}/part.step")
        calls = self.fake_aces_calls(log)
        records = [c for c in calls if c["argv"][:3] == ["ledger", "record", "cad.pull"]]
        self.assertEqual(len(records), 1)
        payload = json.loads(records[0]["stdin"])
        self.assertEqual(set(payload), LEDGER_KEYS)
        self.assertEqual((payload["class"], payload["project"], payload["tool"]), ("sovereign", "p", "aces-cad"))
        self.assertEqual(payload["geometry_sha256"], source["geometry_sha256"])
        self.assertEqual(payload["files"]["source.json"], sha_file(bundle / "source.json"))
        self.assertLess(len(records[0]["stdin"].encode("utf-8")), 65536)
        self.assertIn(["project", "add-cad-source", "p", f"cad/local/{bundle.name}/part.step"],
                      [c["argv"] for c in calls])
        golden = out["golden"]
        self.assertIs(golden["applicable"], True)
        self.assertEqual(golden["next"], "init")
        self.assertEqual(golden["init"], f"aces golden init p --step {bundle / 'part.step'}")
        reimported = self.worker_python_json(STEP_VOLUME, str(bundle / "part.step"))
        self.assertEqual(reimported["solids"], 1)
        self.assert_expected(reimported["volume"], "aces_plate.volume_mm3")
        again = self.pull(run["run_id"])
        self.assertNotEqual(again["bundle"], out["bundle"])
        self.assertEqual(again["geometry_sha256"], out["geometry_sha256"], "two pulls of one run")
        rerun = self.run_script("aces_plate", name="plate")
        self.assertEqual(self.pull(rerun["run_id"])["geometry_sha256"], out["geometry_sha256"],
                         "two runs of one script and parameters")

    def test_w13_without_ledger_record(self):
        self.use_fake_aces("old")
        out = self.pull(self.run_script("aces_plate", name="plate")["run_id"])
        bundle = Path(out["bundle"])
        self.assertFalse(out["ledger"]["written"])
        self.assertTrue(out["ledger"]["reason"])
        self.assertFalse(out["cad_source"]["recorded"])
        source = json.loads((bundle / "source.json").read_text(encoding="utf-8"))
        self.assertIs(source["ledger"]["available"], False)
        self.assertTrue(source["ledger"]["reason"])
        outcome = json.loads((bundle / "outcome.json").read_text(encoding="utf-8"))
        self.assertFalse(outcome["ledger"]["written"])
        self.assertEqual(self.check_bundle(out).returncode, 0)

    def test_w13_mesh_sketch_failed_invalid_and_tampered_runs(self):
        mesh = self.pull(self.run_script("sdf_sphere")["run_id"], formats=["step", "stl"])
        mbundle = Path(mesh["bundle"])
        self.assertTrue((mbundle / "part.stl").is_file())
        self.assertFalse((mbundle / "part.step").exists())
        self.assertTrue(any("pretend STEP" in w for w in mesh["warnings"]), mesh["warnings"])
        self.assertEqual(mesh["geometry_sha256"], sha_file(mbundle / "part.stl"))
        self.assertEqual(self.check_bundle(mesh).returncode, 0)
        sketch = self.pull(self.run_script("sketch_plate")["run_id"])
        sfiles = sorted(p.name for p in Path(sketch["bundle"]).iterdir())
        self.assertIn("part.step", sfiles)
        self.assertNotIn("part.stl", sfiles)
        self.assertNotIn("part.3mf", sfiles)
        failed = self.run_error("name_error", "script")
        self.op_error("pull", "usage", project="p", run_id=failed["run_id"])
        invalid = self.run_script("reversed_box")
        self.assertIs(invalid["result"]["parts"][0]["valid"], False)
        err = self.op_error("pull", "result", project="p", run_id=invalid["run_id"])
        self.assertIn("allow_invalid", err["message"])
        forced = self.pull(invalid["run_id"], allow_invalid=True)
        self.assertIs(forced["valid"], False)
        source = json.loads((Path(forced["bundle"]) / "source.json").read_text(encoding="utf-8"))
        self.assertIs(source["allow_invalid"], True)
        self.assertIs(source["result"]["parts"][0]["valid"], False)
        plate = self.run_script("aces_plate", name="plate")
        script = Path(plate["run_dir"]) / "script.py"
        script.write_text(script.read_text(encoding="utf-8") + "# tampered after the run (fixture)\n",
                          encoding="utf-8", newline="\n")
        self.op_error("pull", "integrity", project="p", run_id=plate["run_id"])


class CheckBundleTest(base.CadTestCase):
    """check_bundle.py itself, on a hand-made bundle (a test fixture, not aces-cad output)."""

    def make_bundle(self) -> tuple[Path, dict]:
        bundle = self.tmp / "work" / "p" / "cad" / "local" / "20261010T134002Z-plate"
        bundle.mkdir(parents=True)
        files = {}
        for name, data in (("part.step", b"ISO-10303-21; fixture\n"), ("parameters.json", b"{}\n"),
                           ("source.py", b"print(1)\n")):
            (bundle / name).write_bytes(data)
            files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        source = {"schema": "aces.cad.local.source/1", "kind": "solid", "files": files,
                  "geometry_sha256": files["part.step"]["sha256"]}
        (bundle / "source.json").write_text(json.dumps(source), encoding="utf-8")
        outcome = {"files": {"source.json": {"sha256": sha_file(bundle / "source.json")}}}
        (bundle / "outcome.json").write_text(json.dumps(outcome), encoding="utf-8")
        return bundle, {"bundle": str(bundle), "geometry_sha256": source["geometry_sha256"], "files": files}

    def run_check(self, arg) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(base.CHECK_BUNDLE), str(arg)], capture_output=True, timeout=60)

    def test_check_bundle_accepts_and_rejects(self):
        bundle, pull = self.make_bundle()
        path = self.tmp / "pull.json"
        path.write_text(json.dumps(pull), encoding="utf-8")
        self.assertEqual(self.run_check(path).returncode, 0)
        self.assertEqual(self.run_check(bundle).returncode, 0)
        (bundle / "source.py").write_bytes(b"print(2)\n")
        done = self.run_check(path)
        self.assertEqual(done.returncode, 1)
        self.assertIn(b"source.py", done.stdout)
        (bundle / "source.py").write_bytes(b"print(1)\n")
        (bundle / "outcome.json").write_text(json.dumps({"files": {"source.json": {"sha256": "0" * 64}}}),
                                             encoding="utf-8")
        self.assertEqual(self.run_check(path).returncode, 1)
        self.assertEqual(self.run_check(self.tmp / "missing.json").returncode, 2)


if __name__ == "__main__":
    unittest.main()
