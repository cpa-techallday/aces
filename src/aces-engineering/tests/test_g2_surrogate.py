"""B2 unit tests: golden.surrogate (training rows from the case records, the K-fold record, the log policy, captured
warnings, save/load). The table is a synthetic smooth function (test input), never presented as solver output.
Run: python -m unittest discover -s tests -p "test_g2_surrogate.py"."""
from __future__ import annotations

import contextlib
import csv
import io
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

import numpy as np

import test_g2_compute as tc
import test_g2_verify as tv

from aces_engineering import golden
from aces_engineering.golden import surrogate


def write_case(run, cid: str, role: str, v: float, r: float, *, finished: bool = True, extra: dict | None = None):
    """One case's records as B1 writes them (test input values from tv.physics)."""
    run.register_case({"case_id": cid, "role": role, "volfrac": v, "rmin_mm": r})
    m = tv.physics(v, r)
    m.update(extra or {})
    run.write_json(f"{cid}/status.json", {"status": "FINISHED" if finished else "FAILED",
                                          "design_status": "ok" if finished else "detached-load"})
    if finished:
        run.write_json(f"{cid}/results.json", {"case_id": cid, "role": role, "design": {"volfrac": v, "rmin_mm": r},
                                               "converged": True, **m})


class _Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = tc.FakeRun(self.tmp.name)
        self.eff = tv.full_eff()
        U = np.linspace(0.0, 1.0, 12)
        for i, u in enumerate(U):
            v = 0.13 + 0.25 * u
            r = 6.0 + 6.0 * ((i * 7) % 12) / 11.0
            write_case(self.run, f"d{i:03d}", "sweep", round(v, 6), round(r, 6))
        write_case(self.run, "d012", "sweep", 0.2, 9.0, finished=False)

    def tearDown(self):
        self.tmp.cleanup()


class TestTrainingRows(_Env):
    def test_rows_skip_failed_and_add_verify_on_request(self):
        ids, X, Y, skipped = surrogate.training_rows(self.run, self.eff, include_verify=False)
        self.assertEqual(len(ids), 12)
        self.assertEqual(skipped, ["d012"])
        self.assertEqual(X.shape, (12, 2))
        self.assertEqual(Y.shape, (12, len(self.eff["derived"]["outputs"])))
        write_case(self.run, "v001", "verify", 0.25, 8.0)
        ids2, *_ = surrogate.training_rows(self.run, self.eff, include_verify=True)
        self.assertEqual(ids2[-1], "v001")
        ids3, *_ = surrogate.training_rows(self.run, self.eff, include_verify=False)
        self.assertNotIn("v001", ids3)

    def test_too_few_points_is_thin(self):
        with tempfile.TemporaryDirectory() as td:
            run = tc.FakeRun(td)
            write_case(run, "d000", "sweep", 0.2, 8.0)
            write_case(run, "d001", "sweep", 0.3, 9.0)
            with self.assertRaises(golden.GoldenError) as cm:
                surrogate.fit(run, self.eff, round_no=1, seed=7)
            self.assertEqual(cm.exception.code, golden.EXIT_THIN)


class TestFit(_Env):
    def test_record_files_and_log_policy(self):
        sur, rec = surrogate.fit(self.run, self.eff, round_no=1, seed=7)
        d = Path(self.run.path("surrogate", "round-1"))
        for name in ("model.joblib", "model.json", "cv.json", "data.csv"):
            self.assertTrue((d / name).is_file(), name)
        for key in ("schema", "round", "kind", "folds", "n", "inputs", "outputs", "log_outputs", "training_cases",
                    "data_sha256", "cv", "status", "flags", "gp_warnings", "model_sha256", "wall_s"):
            self.assertIn(key, rec)
        self.assertEqual(rec["schema"], golden.SCHEMA["surrogate"])
        self.assertEqual((rec["n"], rec["folds"]), (12, 5))
        self.assertEqual(rec["log_outputs"]["policy"], "auto-positive")
        self.assertEqual(rec["log_outputs"]["applied"], "always")
        self.assertTrue(all(rec["log_outputs"]["per_output"].values()))
        for o in rec["outputs"]:
            self.assertEqual(set(rec["cv"][o]), {"rmse", "mae", "rel_rmse", "mape_pct", "r2", "y_range"})
        self.assertEqual(rec["data_sha256"], golden.sha256_file(d / "data.csv"))
        self.assertEqual(rec["model_sha256"], golden.sha256_file(d / "model.joblib"))
        self.assertEqual(self.run.read_json("surrogate/round-1/cv.json"), rec)
        with open(d / "data.csv", encoding="utf-8", newline="") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(rows[0], ["case_id", "volfrac", "rmin_mm", *self.eff["derived"]["outputs"]])
        self.assertEqual(len(rows), 13)
        self.assertNotIn(b"\r\n", (d / "data.csv").read_bytes())
        self.assertIsInstance(rec["gp_warnings"], int)

    def test_policies(self):
        Y_pos = np.array([[1.0, 2.0], [3.0, 4.0]])
        Y_neg = np.array([[1.0, -2.0], [3.0, 4.0]])
        self.assertEqual(surrogate.log_policy("auto-positive", Y_pos), "always")
        self.assertEqual(surrogate.log_policy("auto-positive", Y_neg), "auto")
        self.assertEqual(surrogate.log_policy("auto", Y_pos), "auto")
        self.assertEqual(surrogate.log_policy("never", Y_pos), "never")
        eff = tv.full_eff()
        eff["surrogate"]["log_outputs"] = "never"
        _, rec = surrogate.fit(self.run, eff, round_no=1, seed=7)
        self.assertEqual(rec["log_outputs"]["applied"], "never")
        self.assertFalse(any(rec["log_outputs"]["per_output"].values()))

    def test_weak_flag(self):
        eff = tv.full_eff()
        eff["surrogate"]["r2_flag"] = 1.01  # no fit reaches R2 1.01 (test input): every output is flagged
        _, rec = surrogate.fit(self.run, eff, round_no=1, seed=7)
        self.assertEqual(rec["status"], "weak")
        self.assertEqual(len(rec["flags"]), len(rec["outputs"]))
        self.assertRegex(rec["flags"][0], r"^mass_kg R2 [-0-9.e]+ < 1.01$")

    def test_warnings_counted_not_printed(self):
        from simlab.surrogate.tabular import TabularSurrogate

        original = TabularSurrogate.fit

        def noisy_fit(sur, X, Y, folds=5):
            warnings.warn("test warning one (test input)", UserWarning)
            warnings.warn("test warning two (test input)", RuntimeWarning)
            return original(sur, X, Y, folds=folds)

        err = io.StringIO()
        with mock.patch.object(TabularSurrogate, "fit", noisy_fit), contextlib.redirect_stderr(err):
            _, rec = surrogate.fit(self.run, self.eff, round_no=1, seed=7)
        self.assertGreaterEqual(rec["gp_warnings"], 2)
        self.assertGreaterEqual(rec["gp_warning_kinds"].get("UserWarning", 0), 1)
        self.assertGreaterEqual(rec["gp_warning_kinds"].get("RuntimeWarning", 0), 1)
        self.assertEqual(err.getvalue(), "")

    def test_save_load_identical(self):
        sur, _ = surrogate.fit(self.run, self.eff, round_no=1, seed=7)
        again = surrogate.load(Path(self.run.path("surrogate", "round-1")))
        X = np.array([[0.2, 7.5], [0.31, 11.0], [0.13, 6.0]])
        mu1, sd1 = sur.predict(X)
        mu2, sd2 = again.predict(X)
        self.assertTrue(np.array_equal(mu1, mu2))
        self.assertTrue(np.array_equal(sd1, sd2))
        mus, sds = surrogate.predict(again, [{"rmin_mm": 7.5, "volfrac": 0.2}])
        one, _ = sur.predict(X[:1])
        self.assertEqual(mus[0]["mass_kg"], float(one[0, 0]))  # same batch shape: bit-identical
        # a point predicted alone vs inside a batch differs only in BLAS summation order (why the audit uses 1e-9)
        self.assertLess(abs(mus[0]["mass_kg"] - float(mu1[0, 0])), 1e-9 * abs(float(mu1[0, 0])))


if __name__ == "__main__":
    unittest.main()
