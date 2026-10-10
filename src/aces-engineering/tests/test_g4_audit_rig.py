"""B2 tests: the audit's rig and conforming checks (golden.evidence._check_rig and _check_conforming, section 15 of the
rig build spec) on a synthetic run tree: one check experiment of the GC-1 bar (test input) on the in-process fake
simlab-mcp (tests/fake_simlab.py, a test fixture), pulled by the real client, with validation.json's rig block from the
session. Each failure path of R1-R9 is a copy of that tree with one thing changed; the conforming check reports INFO
when nothing ran or nothing was recorded, fails on a missing record, and (only with golden.conforming, gmsh and ccx
here) recomputes the tet10 metrics. The audit never contacts the rig. Run: python -m unittest discover -s tests -p
"test_g4_audit_rig.py"."""
from __future__ import annotations

import copy
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fake_simlab
import test_g4_rig_client as tc

from aces_engineering import golden
from aces_engineering.golden import evidence, rig, verify
from aces_engineering.golden.rundir import RunDir


class AuditRig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="g4-audit-"))
        env = {k: v for k, v in os.environ.items() if k not in ("ACES_SIMLAB_TOKEN", "SIMLAB_MCP_TOKEN")}
        (cls.tmp / "etc").mkdir()
        env.update({"ACES_USER_CONFIG": str(cls.tmp / "cfg"), "ACES_ETC": str(cls.tmp / "etc"),
                    "ACES_WORK": str(cls.tmp / "work"), "XDG_DATA_HOME": str(cls.tmp / "data"),
                    "ACES_STATE": str(cls.tmp / "state")})
        cls._env = mock.patch.dict(os.environ, env, clear=True)
        cls._env.start()
        cls._consts = mock.patch.multiple(rig, POLL_S=0.02, BEACON_EVERY_S=0.1)
        cls._consts.start()
        cls.fake = fake_simlab.FakeSimlab(cls.tmp / "rigrepo")
        run, sess, res = tc.gc1_check_run(cls.tmp, cls.fake)
        assert res["x001"]["state"] == "result", res["x001"].get("reason")
        run.write_json("validation.json", {"rig": sess.validation_block(),
                                           "conforming": verify.conforming_not_run("not run: no conforming check")})
        cls.base = Path(run.root)
        cls.exp = run.read_json("rig/session.json")["experiments"][0]
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls.fake.stop()
        cls._consts.stop()
        cls._env.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def tearDown(self):
        self.assertEqual(fake_simlab.BLOCKED, [])

    # ---- helpers ------------------------------------------------------------------------------------------------
    def copy(self) -> RunDir:
        type(self).n += 1
        dst = self.tmp / "copies" / f"c{self.n:02d}" / self.base.name
        shutil.copytree(self.base, dst)
        return RunDir._from_dir(dst)

    @staticmethod
    def edit_json(run: RunDir, rel: str, mutate) -> None:
        doc = run.read_json(rel)
        mutate(doc)
        golden.dump_json(run.path(*rel.split("/")), doc)

    def rewrite_record(self, run: RunDir, name: str, mutate) -> None:
        """Change a pulled rig record and re-record its hash in x001/rig.json (and the byte copy of evidence.json), so
        only the check under test sees the change."""
        self.edit_json(run, f"x001/rig/{name}", mutate)
        if name == "evidence.json":
            shutil.copyfile(run.path("x001", "rig", "evidence.json"), run.path("x001", "evidence.json"))
        self.edit_json(run, "x001/rig.json",
                       lambda rj: rj["records"].update({name: golden.sha256_file(run.path("x001", "rig", name))}))

    def check(self, run: RunDir) -> tuple:
        return evidence._check_rig(run)

    def fails(self, run: RunDir, words: str) -> None:
        ok, detail = self.check(run)
        self.assertIs(ok, False, detail)
        self.assertIn(words, detail)

    # ---- the clean tree and R1-R9 -------------------------------------------------------------------------------
    def test_clean_tree_passes(self):
        ok, detail = self.check(RunDir._from_dir(self.base))
        self.assertIs(ok, True, detail)
        self.assertTrue(detail.startswith("1 experiment(s), 1 rig case(s), "), detail)
        self.assertIn("1 case(s) re-derived from their raw files", detail)
        self.assertIn("x001 device forced to cpu (a test fake)", detail)

    def test_no_rig_work(self):
        run = self.copy()
        run.path("rig", "session.json").unlink()
        self.assertEqual(self.check(run), (None, "not run: no rig work in this run"))

    def test_r1_the_experiment_document(self):
        run = self.copy()
        spec = run.path("rig", "specs", f"{self.exp['id']}.yaml")
        spec.write_bytes(spec.read_bytes() + b"# edited (test input)\n")
        self.fails(run, f"R1 rig/specs/{self.exp['id']}.yaml does not hash to the recorded spec_sha256")
        run = self.copy()
        self.rewrite_record(run, "evidence.json", lambda ev: ev["experiment"].update(spec_sha256="0" * 64))
        self.fails(run, "R1 x001/rig/evidence.json names spec 000000000000")

    def test_r2_the_uploads(self):
        import warnings

        run = self.copy()
        p = run.path("b001", "part.stl")
        p.write_bytes(p.read_bytes() + b"\n")
        with warnings.catch_warnings():  # trimesh warns while it re-derives R6 from the edited STL
            warnings.simplefilter("ignore", RuntimeWarning)
            self.fails(run, f"R2 upload golden/{run.run_id}/b001.part.stl: b001/part.stl does not hash to the "
                            "recorded sha256")

    def test_r3_files_and_records(self):
        run = self.copy()
        p = run.path("x001", "mask.npy")
        p.write_bytes(p.read_bytes()[:-1] + bytes([p.read_bytes()[-1] ^ 1]))
        self.fails(run, "R3 x001/mask.npy does not hash as x001/rig.json and x001/rig/files.json say")
        run = self.copy()
        run.path("x001", "solve.json").unlink()
        self.fails(run, "R3 x001/solve.json is missing")
        run = self.copy()
        self.edit_json(run, "x001/rig/status.json", lambda st: st.update(status="FAILED"))
        self.fails(run, "R3 x001/rig/status.json does not hash as x001/rig.json records")
        run = self.copy()
        self.edit_json(run, "x001/rig/files.json", lambda fd: fd["files"][0].update(sha256="0" * 64))
        self.fails(run, "does not hash as x001/rig.json and x001/rig/files.json say")

    def test_r4_evidence(self):
        run = self.copy()
        self.edit_json(run, "x001/evidence.json", lambda ev: ev.update(status="FAILED"))
        self.fails(run, "R4 x001/evidence.json is not a byte copy of x001/rig/evidence.json")
        run = self.copy()
        self.rewrite_record(run, "evidence.json", lambda ev: ev["inputs_sha256"].update({"b001.part.stl": "1" * 64}))
        self.fails(run, "R4 x001: the rig evidence hashes input b001.part.stl as 111111111111, the experiment sent ")
        run = self.copy()
        self.edit_json(run, "x001/case.json", lambda c: c.update(seed=8))
        self.fails(run, "R4 x001: the rig evidence's case.golden_case differs from x001/case.json")

    def test_r5_results(self):
        run = self.copy()
        self.edit_json(run, "x001/results.json", lambda r: r.update(compliance_Nmm=r["compliance_Nmm"] * 1.5))
        self.fails(run, "R5 x001/results.json differs from x001/rig/outcome.json results")

    def test_r6_rederivation(self):
        for key, words in (("vm_p99_MPa", "R6 x001: vm_p99_MPa re-derived"), ("umax_mm", "R6 x001: umax_mm re-derived"),
                           ("mass_kg", "R6 x001: mass_kg re-derived"),
                           ("mesh_volume_mm3", "R6 x001: mesh_volume_mm3 re-derived")):
            with self.subTest(key):
                run = self.copy()
                for rel in ("x001/results.json",):
                    self.edit_json(run, rel, lambda r: r.update({key: r[key] * 1.01}))
                self.rewrite_record(run, "outcome.json", lambda o: o["results"].update({key: o["results"][key] * 1.01}))
                self.fails(run, words)
                ok, detail = self.check(run)
                self.assertNotIn("R5", detail)  # the results agree with the outcome: only the re-derivation fails
        run = self.copy()
        self.edit_json(run, "x001/results.json", lambda r: r["voxel"].update(mask_sha256="2" * 64))
        self.rewrite_record(run, "outcome.json", lambda o: o["results"]["voxel"].update(mask_sha256="2" * 64))
        self.fails(run, "R6 x001: mask.npy sha256 differs from results.voxel.mask_sha256")

    def test_r7_image_gpu_and_pool(self):
        cases = (({"image": "other-image:9"}, "R7 x001 ran on image other-image:9, the registry recorded aces-golden:0.1"),
                 ({"image_id": None}, "R7 x001 has no image_id"),
                 ({"gpu_uuid": None}, "R7 x001: the rig evidence lists no GPU with a uuid"),
                 ({"gpu": 9}, "R7 x001 ran on GPU 9, outside the recorded pool [0, 1, 2, 3]"))
        for change, words in cases:
            with self.subTest(change):
                run = self.copy()
                self.edit_json(run, "x001/rig.json", lambda rj: rj.update(change))
                self.fails(run, words)

    def test_r8_code_identity(self):
        run = self.copy()
        self.edit_json(run, "validation.json", lambda v: v["rig"]["code_identity"].update(ok=False))
        self.fails(run, "R8 code identity re-derives as ok=True, validation.json says False")
        run = self.copy()  # a rig module that is not this desktop's: re-derived not ok while validation says ok
        mods = run.read_json("validation.json")["rig"]["code_identity"]["desktop_modules"]
        first = sorted(mods)[0]
        self.edit_json(run, "validation.json",
                       lambda v: v["rig"]["code_identity"]["desktop_modules"].update({first: "3" * 64}))
        self.fails(run, "R8 code identity re-derives as ok=False, validation.json says True")

    def test_r9_the_validation_block(self):
        for key, value in (("lane", "sim/other"), ("session_sha256", "4" * 64), ("principal", "someone-else"),
                           ("experiments", []), ("integrity_error", "x (test input)")):
            with self.subTest(key):
                run = self.copy()
                self.edit_json(run, "validation.json", lambda v: v["rig"].update({key: value}))
                self.fails(run, f"R9 validation.rig.{key} does not re-derive from rig/session.json")
        run = self.copy()
        self.edit_json(run, "validation.json", lambda v: v.update(rig=None))
        self.fails(run, "R9 rig/session.json has experiments but validation.json has no rig block")

    # ---- the conforming check -----------------------------------------------------------------------------------
    def test_conforming_info_and_missing_record(self):
        run = self.copy()
        ok, detail = evidence._check_conforming(run)
        self.assertEqual((ok, detail), (None, "not run: no conforming check"))
        failed = verify.conforming_not_run("not run: x")
        failed.update(ran=True, passed=False, status="error", case_id="m001", host="rig",
                      reason="error: ImportError (test input)")
        self.edit_json(run, "validation.json", lambda v: v.update(conforming=copy.deepcopy(failed)))
        ok, detail = evidence._check_conforming(run)
        self.assertIsNone(ok)
        self.assertEqual(detail, "not re-derived: m001 wrote no conforming.json (error: ImportError (test input))")
        self.edit_json(run, "validation.json", lambda v: v["conforming"].update(record="m001/conforming.json"))
        ok, detail = evidence._check_conforming(run)
        self.assertIs(ok, False)
        self.assertEqual(detail, "m001/conforming.json is missing")
        golden.dump_json(run.path("m001", "conforming.json"), {"status": "error", "files": {}, "metrics": None})
        ok, detail = evidence._check_conforming(run)
        try:
            from aces_engineering.golden import conforming  # noqa: F401
        except ImportError:
            self.assertIs(ok, False)
            self.assertTrue(detail.startswith("golden.conforming is not importable here"), detail)
        else:
            self.assertIs(ok, True, detail)  # a record without metrics or files: nothing to recompute, rows []

    def test_conforming_recompute(self):
        try:
            from aces_engineering.golden import compute, conforming  # noqa: F401
        except ImportError as exc:
            self.skipTest(f"golden.conforming (B3) is not in this tree ({exc}); the integrator runs this after merging")
        if not compute.conforming_available().get("usable"):
            self.skipTest("gmsh and CalculiX ccx are not usable here")
        from aces_engineering.golden import case as case_mod

        run = self.copy()
        eff, dom = run.read_json("inputs/spec.effective.json"), run.read_json("domain/domain.json")
        plan = {"mode": "local", "host": "desktop", "h_mm": 1.0, "threads": 2, "max_dof": 1_500_000, "timeout_s": 900.0}
        c = verify.conforming_case(plan, "b001", 7, case_id="m001", host="desktop")
        oc = case_mod.run_conforming_case(run, eff, dom, None, c)
        check = {"levels": [{"level": 1, "case_id": "x001", "h_mm": 1.0, "ran": True, "host": "rig"}]}
        blk = verify._conforming_from(run, eff, check, c, oc, mode="local", rig_blk=None, attempt=None)
        self.edit_json(run, "validation.json", lambda v: v.update(conforming=golden.clean(blk)))
        ok, detail = evidence._check_conforming(run)
        self.assertIs(ok, True, detail)
        self.edit_json(run, "m001/conforming.json", lambda r: r["metrics"].update(mass_kg=r["metrics"]["mass_kg"] * 2))
        ok, detail = evidence._check_conforming(run)
        self.assertIs(ok, False)
        self.assertIn("m001: mass_kg recomputes to", detail)


if __name__ == "__main__":
    unittest.main()
