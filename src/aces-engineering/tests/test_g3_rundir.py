"""golden.rundir: run ids (deterministic, keyed by inputs not paths), the collision suffix, register_case, run.json
lifecycle, hash_tree exclusions, run lookup and listing. Standard library only; every spec below is a test input."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from aces_engineering.golden import EXIT_USAGE, SCHEMA, GoldenError  # noqa: E402
from aces_engineering.golden.rundir import RunDir, RunNotFound, list_runs, new_run_id, work_root  # noqa: E402

NOW = datetime(2026, 10, 9, 1, 2, 3, tzinfo=timezone.utc)


def spec_input(**over) -> dict:
    """A minimal effective spec (test input): what new_run_id keys on, plus a derived block it must ignore."""
    eff = {"schema": "aces-golden-spec/1", "name": "test-part", "domain": {"source": "build123d",
           "build123d": {"ref": "pkg.mod:func", "kwargs": {}}}, "sweep": {"n": 10, "seed": 7},
           "derived": {"base_dir": "/somewhere", "domain_ref": "pkg.mod:func", "grid": None,
                       "gen_mm": 4.0, "fea_mm": 4.0, "field_h_mm": 2.0, "check_levels": [{"level": 1, "h_mm": 2.0}]}}
    eff.update(over)
    return eff


class RunDirTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="g3-rundir-"))
        self._env = dict(os.environ)
        os.environ["ACES_WORK"] = str(self.tmp / "work")

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)


class RunIdTests(RunDirTestCase):
    def test_format_and_determinism(self) -> None:
        a = new_run_id(spec_input(), 7, NOW)
        self.assertRegex(a, r"^20261009T010203Z-[0-9a-f]{6}$")
        self.assertEqual(a, new_run_id(spec_input(), 7, NOW))
        self.assertNotEqual(a, new_run_id(spec_input(), 8, NOW))
        self.assertNotEqual(a, new_run_id(spec_input(name="other-part"), 7, NOW))
        naive = datetime(2026, 10, 9, 1, 2, 3)
        self.assertEqual(a, new_run_id(spec_input(), 7, naive))  # a naive time is taken as UTC

    def test_derived_and_machine_paths_do_not_change_the_suffix(self) -> None:
        a = new_run_id(spec_input(), 7, NOW)
        other = spec_input()
        other["derived"] = {"base_dir": "/elsewhere", "overrides": {"sweep.n": 10}, "grid": {"origin_mm": [0, 0, 0]}}
        self.assertEqual(a, new_run_id(other, 7, NOW))

    def test_step_source_is_keyed_by_content(self) -> None:
        d1, d2 = self.tmp / "a", self.tmp / "b"
        d1.mkdir()
        d2.mkdir()
        (d1 / "part.step").write_bytes(b"ISO-10303-21; test input\n")
        (d2 / "renamed.step").write_bytes(b"ISO-10303-21; test input\n")
        (d2 / "changed.step").write_bytes(b"ISO-10303-21; another test input\n")

        def eff(p: Path) -> dict:
            return spec_input(domain={"source": "step", "step_file": str(p)}, derived={"domain_ref": str(p)})

        same1 = new_run_id(eff(d1 / "part.step"), 7, NOW)
        same2 = new_run_id(eff(d2 / "renamed.step"), 7, NOW)
        diff = new_run_id(eff(d2 / "changed.step"), 7, NOW)
        self.assertEqual(same1, same2)
        self.assertNotEqual(same1, diff)
        with self.assertRaises(GoldenError) as cm:
            new_run_id(eff(self.tmp / "missing.step"), 7, NOW)
        self.assertEqual(cm.exception.code, EXIT_USAGE)


class CreateTests(RunDirTestCase):
    def test_create_and_collision_suffix(self) -> None:
        r1 = RunDir.create("proj-1", spec_input(), seed=7, now=NOW)
        r2 = RunDir.create("proj-1", spec_input(), seed=7, now=NOW)
        r3 = RunDir.create("proj-1", spec_input(), seed=7, now=NOW)
        self.assertEqual(r2.run_id, r1.run_id + "-2")
        self.assertEqual(r3.run_id, r1.run_id + "-3")
        self.assertEqual(r1.root, (work_root() / "proj-1" / "golden" / r1.run_id).resolve())
        meta = json.loads((r1.root / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["schema"], SCHEMA["run"])
        self.assertEqual((meta["status"], meta["seed"], meta["project"], meta["run_id"]), ("running", 7, "proj-1", r1.run_id))
        self.assertEqual(meta["resolution"], {"gen_mm": 4.0, "fea_mm": 4.0, "field_h_mm": 2.0,
                                              "check_levels": [{"level": 1, "h_mm": 2.0}]})
        self.assertEqual(r1.exp_id, f"golden-proj-1-{r1.run_id}")
        self.assertEqual(RunDir.create("odd name!", spec_input(), seed=7, now=NOW).exp_id[:20], "golden-odd-name--202")
        self.assertNotIn(b"\r", (r1.root / "run.json").read_bytes())

    def test_work_root_default(self) -> None:
        os.environ.pop("ACES_WORK", None)
        self.assertEqual(work_root(), Path.home() / "Work")
        os.environ["ACES_WORK"] = str(self.tmp / "w2")
        self.assertEqual(work_root(), self.tmp / "w2")


class RecordTests(RunDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.run = RunDir.create("proj", spec_input(), seed=7, now=NOW)

    def test_register_case(self) -> None:
        run = self.run
        d0 = run.register_case({"case_id": "d000", "role": "sweep", "volfrac": 0.2})
        run.register_case({"case_id": "d001", "role": "sweep", "volfrac": 0.3})
        run.register_case({"case_id": "d000", "role": "sweep", "volfrac": 0.25})  # replaced in place
        self.assertEqual(d0, run.root / "d000")
        self.assertEqual([c["case_id"] for c in run.cases()], ["d000", "d001"])
        self.assertEqual(run.cases()[0]["volfrac"], 0.25)
        self.assertEqual(json.loads((d0 / "case.json").read_text(encoding="utf-8"))["volfrac"], 0.25)
        for bad in ("", "../x", "a/b", ".."):
            with self.assertRaises(ValueError):
                run.register_case({"case_id": bad})

    def test_json_text_and_paths(self) -> None:
        run = self.run
        run.write_json("sub/x.json", {"v": float("nan"), "p": Path("a/b")})
        self.assertEqual(run.read_json("sub/x.json"), {"v": None, "p": str(Path("a/b"))})
        run.write_text("t.txt", "a\r\nb\n")
        self.assertEqual((run.root / "t.txt").read_bytes(), b"a\nb\n")
        self.assertEqual(run.rel(run.path("sub", "x.json")), "sub/x.json")
        self.assertEqual(run.rel("sub/x.json"), "sub/x.json")
        outside = str(self.tmp / "elsewhere.txt")
        self.assertEqual(run.rel(outside), outside)

    def test_write_experiment(self) -> None:
        class Exp:  # stands in for simlab.spec.Experiment
            def model_dump(self, mode: str = "python") -> dict:
                return {"id": "golden-proj-x", "mode": mode}

        self.run.write_experiment(Exp())
        self.assertEqual(self.run.read_json("spec.resolved.json"), {"id": "golden-proj-x", "mode": "json"})

    def test_stage_and_finish(self) -> None:
        run = self.run
        run.t0 = time.time() - 5.0
        run.stage("compute", "running", t0=time.time() - 1.0)
        run.stage("compute", "done", "detected")
        run.stage("loop", "skipped", "not run: thin sweep")
        with self.assertRaises(ValueError):
            run.stage("sweep", "bogus")
        run.finish(status="done", outcome="thin-sweep", verdict="THIN", grade="preview-grade", exit_code=3,
                   message="only 2 of 10 designs finished: detached-load", paths={"report_md": "report.md"})
        meta = run.meta()
        st = {s["name"]: s for s in meta["stages"]}
        self.assertEqual(st["compute"]["status"], "done")
        self.assertGreaterEqual(st["compute"]["wall_s"], 0.9)
        self.assertEqual(st["compute"]["note"], "detected")
        self.assertEqual((st["loop"]["status"], st["loop"]["wall_s"]), ("skipped", None))
        self.assertEqual(meta["timings"]["compute_s"], st["compute"]["wall_s"])
        self.assertGreaterEqual(meta["timings"]["total_s"], 4.9)
        self.assertEqual((meta["status"], meta["exit_code"], meta["verdict"]), ("done", 3, "THIN"))
        self.assertIsNotNone(meta["finished_utc"])

    def test_hash_tree_excludes(self) -> None:
        run = self.run
        for rel, data in (("a.txt", b"a"), ("log.txt", b"log"), ("d000/progress.json", b"{}"), ("d000/x.tmp", b"t"),
                          ("evidence/files.sha256", b"e"), ("d000/results.json", b"{}"), ("z/log.txt", b"nested log")):
            p = run.path(*rel.split("/"))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        sha = run.hash_tree("manifest.sha256", exclude=["log.txt", "*.tmp", "progress.json", "evidence/*"])
        text = run.path("manifest.sha256").read_bytes()
        self.assertEqual(sha, hashlib.sha256(text).hexdigest())
        self.assertNotIn(b"\r", text)
        names = [ln.split(" *", 1)[1] for ln in text.decode().splitlines()]
        self.assertEqual(names, sorted(names))
        self.assertIn("a.txt", names)
        self.assertIn("d000/results.json", names)
        self.assertIn("run.json", names)
        for gone in ("log.txt", "z/log.txt", "d000/progress.json", "d000/x.tmp", "evidence/files.sha256", "manifest.sha256"):
            self.assertNotIn(gone, names)
        line = next(ln for ln in text.decode().splitlines() if ln.endswith(" *a.txt"))
        self.assertTrue(re.fullmatch(r"[0-9a-f]{64} \*a\.txt", line))
        self.assertEqual(line[:64], hashlib.sha256(b"a").hexdigest())
        run.hash_tree("only-top.sha256", exclude=lambda rel: "/" in rel)  # a callable decides on its own
        top = [ln.split(" *", 1)[1] for ln in run.path("only-top.sha256").read_text().splitlines()]
        self.assertTrue(all("/" not in n for n in top))
        self.assertIn("manifest.sha256", top)


class LookupTests(RunDirTestCase):
    def test_open_and_list(self) -> None:
        with self.assertRaises(RunNotFound):
            RunDir.open()
        a = RunDir.create("p1", spec_input(), seed=1, now=datetime(2026, 10, 9, 1, 0, 0, tzinfo=timezone.utc))
        time.sleep(0.01)
        b = RunDir.create("p2", spec_input(), seed=2, now=datetime(2026, 10, 9, 2, 0, 0, tzinfo=timezone.utc))
        runs = list_runs()
        self.assertEqual([p.parent.name for p in runs], [b.run_id, a.run_id])  # newest first
        self.assertTrue(all(p.name == "run.json" for p in runs))
        self.assertEqual([p.parent.name for p in list_runs("p1")], [a.run_id])
        self.assertEqual(RunDir.open().run_id, b.run_id)
        self.assertEqual(RunDir.open("latest", project="p1").run_id, a.run_id)
        self.assertEqual(RunDir.open(a.run_id).root, a.root)
        self.assertEqual(RunDir.open(a.run_id[:12]).root, a.root)  # 20261009T010 is a unique prefix
        self.assertEqual(RunDir.open(str(b.root)).project, "p2")
        with self.assertRaises(GoldenError) as cm:
            RunDir.open("2026")  # both match: ambiguous
        self.assertEqual(cm.exception.code, EXIT_USAGE)
        self.assertNotIsInstance(cm.exception, RunNotFound)
        with self.assertRaises(RunNotFound):
            RunDir.open("19990101")
        with self.assertRaises(RunNotFound):
            RunDir.open(str(self.tmp / "not-a-run"))
        (b.root / "run.json").write_text("{broken", encoding="utf-8")  # unreadable run.json: listed, not fatal
        self.assertEqual(len(list_runs()), 2)


if __name__ == "__main__":
    unittest.main()
