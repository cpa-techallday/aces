"""B1 unit tests: the Latin hypercube sampler, the sweep tables, and a spawn pool of 2 workers producing bit-identical
designs to the inline path (BLAS pinned to one thread both ways). stdlib unittest; test inputs only."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (_HERE.parent / "lib", _HERE.parent / "vendor", _HERE):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.append(str(_p))

from aces_engineering import golden  # noqa: E402
from aces_engineering.golden import case as gcase, sweep  # noqa: E402

from test_g1_case import case_dict, make_run  # noqa: E402


def _square(job):
    return job * job


class Sampler(unittest.TestCase):
    def test_deterministic_in_bounds_and_stratified(self):
        v = {"volfrac": [0.13, 0.38], "rmin_mm": [6.0, 12.0], "cell_mm": [18.0, 26.0]}
        a = sweep.sample(v, 9, 5)
        self.assertEqual(a, sweep.sample(v, 9, 5))
        self.assertNotEqual(a, sweep.sample(v, 9, 6))
        self.assertEqual([list(p) for p in a], [list(v)] * 9)
        for k, (lo, hi) in v.items():
            vals = [p[k] for p in a]
            self.assertTrue(all(lo <= x <= hi for x in vals))
            self.assertTrue(all(x == round(x, 6) for x in vals))
            strata = sorted(min(8, int((x - lo) / (hi - lo) * 9)) for x in vals)
            self.assertEqual(strata, list(range(9)))  # one sample per stratum: a Latin hypercube


class Pool(unittest.TestCase):
    def test_map_order_and_on_done(self):
        seen = []
        with sweep.CasePool(2) as pool:
            out = pool.map(_square, [3, 1, 2], on_done=lambda i, r: seen.append((i, r)))
        self.assertEqual(out, [9, 1, 4])
        self.assertEqual(sorted(seen), [(0, 9), (1, 1), (2, 4)])

    def test_pool_of_two_matches_inline_bit_for_bit(self):
        points = [{"volfrac": 0.61, "rmin_mm": 6.2}, {"volfrac": 0.72, "rmin_mm": 7.5}]
        with tempfile.TemporaryDirectory() as td:
            outs, csvs, stls = [], [], []
            for name, jobs in (("inline", 0), ("pool", 2)):
                run, eff, dom, exp = make_run(Path(td) / name)
                cases = [case_dict(f"d{i:03d}", "sweep", eff, **p) for i, p in enumerate(points)]
                if jobs:
                    with sweep.CasePool(jobs) as pool:
                        ocs = gcase.run_cases(run, eff, dom, exp, cases, pool=pool, progress=golden.NullProgress(), kind="design")
                else:
                    ocs = gcase.run_cases(run, eff, dom, exp, cases, pool=None, progress=golden.NullProgress(), kind="design")
                table = sweep.write_table(run, eff, ocs)
                outs.append(ocs)
                csvs.append(run.path("sweep.csv").read_bytes())
                stls.append([run.path(c["case_id"], "part.stl").read_bytes() for c in cases])
                self.assertEqual(table["csv_sha256"], golden.sha256_bytes(csvs[-1]))
                self.assertEqual(golden.read_json(run.path("sweep.json"))["csv_sha256"], table["csv_sha256"])
            for a, b in zip(*outs):
                self.assertEqual((a["status"], b["status"]), ("FINISHED", "FINISHED"), (a["error"], b["error"]))
                for k in golden.METRICS + ("pcg_iters", "pcg_relres"):
                    self.assertEqual(a["results"][k], b["results"][k], k)
            self.assertEqual(stls[0], stls[1])
            self.assertEqual(csvs[0], csvs[1])
            text = csvs[0].decode("utf-8")
            self.assertNotIn("\r", text)
            head = text.splitlines()[0].split(",")
            self.assertEqual(head[:5], ["case_id", "status", "design_status", "volfrac", "rmin_mm"])
            self.assertEqual(head[-3:], ["device", "h_fea_mm", "grade"])
            self.assertIn(",true,", text)


if __name__ == "__main__":
    unittest.main()
