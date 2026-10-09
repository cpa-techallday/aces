"""B2 unit tests: golden.optimise (candidates, the pinned margin rule, kappa, exclusion, closest infeasible, the Optuna
path, the map). The surrogate here is an analytic stand-in (test input: tv.physics as the mean, a fixed fraction as
the spread), so every expected winner can be worked out by hand.
Run: python -m unittest discover -s tests -p "test_g2_optimise.py"."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

import test_g2_compute as tc
import test_g2_verify as tv

from aces_engineering.golden import optimise


class AnalyticSur:
    """Surrogate stand-in (test input): mean = tv.physics, sd = sd_frac * mean."""

    def __init__(self, outputs, sd_frac: float = 0.0, inputs=("volfrac", "rmin_mm")):
        self.inputs, self.outputs, self.sd_frac = list(inputs), list(outputs), sd_frac

    def predict(self, X):
        X = np.atleast_2d(np.asarray(X, dtype=float))
        mu = np.array([[tv.physics(x[0], x[1])[o] for o in self.outputs] for x in X]).reshape(len(X), len(self.outputs))
        return mu, self.sd_frac * mu

    def predict_dict(self, params):
        mu, sd = self.predict([[params[k] for k in self.inputs]])
        out = {o: float(mu[0, j]) for j, o in enumerate(self.outputs)}
        out.update({o + "_std": float(sd[0, j]) for j, o in enumerate(self.outputs)})
        return out


def fit_record(eff, mape: float = 0.0) -> dict:
    return {"cv": {o: {"mape_pct": mape} for o in eff["derived"]["outputs"]}}


class _Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = tc.FakeRun(self.tmp.name)
        self.eff = tv.full_eff()
        self.outs = self.eff["derived"]["outputs"]

    def tearDown(self):
        self.tmp.cleanup()


class TestSearch(_Env):
    def test_known_feasible_region_found(self):
        rec = optimise.search(AnalyticSur(self.outs), self.eff, fit_record(self.eff), self.run, round_no=1, seed=7,
                              exclude=[])
        self.assertEqual(rec["method"], "grid")
        self.assertEqual(rec["n_candidates"], 41 * 41)
        self.assertEqual(rec["margins"], {"vm_p99_MPa": 0.10, "umax_mm": 0.10})
        # by hand: umax(v, 6) <= 0.035 * 0.9 first holds on the grid at v = 0.13 + 11 * 0.00625 = 0.19875 (rmin 6)
        self.assertEqual(rec["winner"]["params"], {"volfrac": 0.19875, "rmin_mm": 6.0})
        self.assertTrue(rec["from_feasible"])
        self.assertIsNone(rec["closest_infeasible"])
        grid = [(0.13 + 0.25 * i / 40, 6.0 + 6.0 * j / 40) for i in range(41) for j in range(41)]
        n_ok = sum(1 for v, r in grid if tv.physics(v, r)["umax_mm"] <= 0.0315 and tv.physics(v, r)["vm_p99_MPa"] <= 54.0)
        self.assertEqual(rec["n_feasible"], n_ok)
        self.assertEqual(len(rec["top"]), 5)
        self.assertEqual(rec["top"][0], rec["winner"])
        self.assertEqual(self.run.read_json("optimise/round-1.json"), rec)
        self.assertEqual(optimise.chosen(rec), rec["winner"])

    def test_margin_and_kappa_push_the_winner_into_the_feasible_side(self):
        base = optimise.search(AnalyticSur(self.outs), self.eff, fit_record(self.eff), self.run, round_no=1, seed=7,
                               exclude=[])["winner"]["params"]["volfrac"]
        sd = optimise.search(AnalyticSur(self.outs, sd_frac=0.05), self.eff, fit_record(self.eff), self.run,
                             round_no=1, seed=7, exclude=[])["winner"]["params"]["volfrac"]
        mape = optimise.search(AnalyticSur(self.outs), self.eff, fit_record(self.eff, mape=20.0), self.run,
                               round_no=1, seed=7, exclude=[])
        self.assertGreater(sd, base)
        self.assertEqual(mape["margins"]["umax_mm"], 0.20)
        self.assertGreater(mape["winner"]["params"]["volfrac"], base)
        eff = tv.full_eff()
        eff["optimise"]["cv_margin"] = False
        tol_only = optimise.search(AnalyticSur(self.outs), eff, fit_record(eff, mape=20.0), self.run, round_no=1,
                                   seed=7, exclude=[])
        self.assertEqual(tol_only["margins"]["umax_mm"], 0.10)
        self.assertEqual(tol_only["winner"]["params"]["volfrac"], base)
        eff["derived"]["kappa"] = 0.0
        k0 = optimise.search(AnalyticSur(self.outs, sd_frac=0.05), eff, fit_record(eff), self.run, round_no=1, seed=7,
                             exclude=[])
        self.assertEqual(k0["winner"]["params"]["volfrac"], base)

    def test_exclusion(self):
        first = optimise.search(AnalyticSur(self.outs), self.eff, fit_record(self.eff), self.run, round_no=1, seed=7,
                                exclude=[])
        win = first["winner"]["params"]
        again = optimise.search(AnalyticSur(self.outs), self.eff, fit_record(self.eff), self.run, round_no=2, seed=7,
                                exclude=[{"case_id": "v001", "params": win}])
        self.assertEqual(again["excluded"], ["v001"])
        self.assertEqual(again["n_excluded"], 1)  # grid spacing 1/40 > 0.02: only the verified point itself
        self.assertNotEqual(again["winner"]["params"], win)
        self.assertEqual(again["exclusion_radius"], 0.02)

    def test_closest_infeasible(self):
        self.eff["derived"]["constraints"][1]["limit"] = 0.001  # unreachable (test input)
        rec = optimise.search(AnalyticSur(self.outs), self.eff, fit_record(self.eff), self.run, round_no=1, seed=7,
                              exclude=[])
        self.assertIsNone(rec["winner"])
        self.assertFalse(rec["from_feasible"])
        ci = rec["closest_infeasible"]
        self.assertGreater(ci["violation"], 0.0)
        # brute force: least total normalised violation over the grid
        best = min((max(0.0, tv.physics(0.13 + 0.25 * i / 40, 6.0 + 6.0 * j / 40)["umax_mm"] - 0.0009) / 0.001, i, j)
                   for i in range(41) for j in range(41))
        self.assertAlmostEqual(ci["violation"], best[0])
        self.assertEqual(optimise.chosen(rec), ci)

    def test_lhs_for_four_variables(self):
        eff = tv.full_eff()
        eff["derived"]["variables"] = {"volfrac": [0.13, 0.38], "rmin_mm": [6.0, 12.0], "cell_mm": [18.0, 26.0],
                                       "wall_mm": [4.0, 6.0]}
        eff["optimise"]["candidates"] = 256
        sur = AnalyticSur(self.outs, inputs=list(eff["derived"]["variables"]))
        a = optimise.search(sur, eff, fit_record(eff), self.run, round_no=1, seed=7, exclude=[])
        b = optimise.search(sur, eff, fit_record(eff), self.run, round_no=1, seed=7, exclude=[])
        c = optimise.search(sur, eff, fit_record(eff), self.run, round_no=2, seed=7, exclude=[])
        self.assertEqual(a["method"], "lhs")
        self.assertEqual(a["n_candidates"], 256)
        self.assertEqual(a["winner"], b["winner"])
        self.assertNotEqual(a["top"], c["top"])  # seed + 1000 * round

    def test_optuna_path_deterministic(self):
        eff = tv.full_eff()
        eff["optimise"]["method"] = "optuna"
        eff["optimise"]["trials"] = 12  # test input: a short TPE study (SQLite per trial is slow on Windows)
        recs = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as td:
                run = tc.FakeRun(td)
                rec = optimise.search(AnalyticSur(self.outs), eff, fit_record(eff), run, round_no=1, seed=7, exclude=[])
                self.assertTrue(Path(run.path("optimise", "round-1-optuna", "trials.csv")).is_file())
                recs.append(rec)
        self.assertEqual(recs[0]["method"], "optuna")
        self.assertEqual(recs[0]["n_candidates"], 12)
        self.assertEqual(recs[0]["winner"], recs[1]["winner"])
        self.assertEqual(recs[0]["n_feasible"], recs[1]["n_feasible"])


class TestMap(_Env):
    def test_map_two_and_one_variable(self):
        records = {"round": 1, "fit": fit_record(self.eff),
                   "sweep": [{"case_id": "d000", "params": {"volfrac": 0.2, "rmin_mm": 8.0}, "ok": True},
                             {"case_id": "d001", "params": {"volfrac": 0.3, "rmin_mm": 10.0}, "ok": False}],
                   "verify": [{"case_id": "v001", "params": {"volfrac": 0.21, "rmin_mm": 6.2}, "ok": True}],
                   "winner": {"volfrac": 0.21, "rmin_mm": 6.2}}
        p = optimise.plot_map(AnalyticSur(self.outs), self.eff, self.run, records)
        self.assertTrue(Path(p).is_file())
        self.assertGreater(Path(p).stat().st_size, 10_000)
        eff1 = tv.full_eff()
        eff1["derived"]["variables"] = {"volfrac": [0.13, 0.38]}
        sur1 = AnalyticSur(self.outs, inputs=["volfrac"])
        sur1.predict = lambda X: AnalyticSur(self.outs).predict(np.column_stack([np.atleast_2d(X)[:, 0],
                                                                               np.full(len(np.atleast_2d(X)), 9.0)]))
        p1 = optimise.plot_map(sur1, eff1, self.run, {"round": 2, "fit": None, "winner": {"volfrac": 0.21}})
        self.assertTrue(Path(p1).is_file())
        self.assertIsNone(optimise.plot_map(None, self.eff, self.run, records))


if __name__ == "__main__":
    unittest.main()
