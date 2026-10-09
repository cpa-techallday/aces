"""Parametric generative demo: L-bracket, minimise mass and compliance under a stress limit.

Genome: base thickness, wall thickness, gusset size, gusset thickness (mm).
Evaluator: GPU voxel FEA of the bracket inside a 60 x 40 x 60 mm envelope, bolted
through two M6 holes (washer zones fixed on the underside), 800 N pulling the top of
the wall in +x. Multi-objective NSGA-II -> Pareto front -> STL of the Pareto designs.

    .venv\\Scripts\\python cases\\bracket_param.py --trials 24 --out out\\bracket_param
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gendesign.evaluators import VoxelFEAEvaluator  # noqa: E402
from gendesign.export import mask_to_stl, save_json  # noqa: E402
from gendesign.parametric import ParametricProblem, export_pareto, run_study  # noqa: E402

ENVELOPE = (60.0, 40.0, 60.0)
HOLES = [(45.0, 10.0), (45.0, 30.0)]
HOLE_R = 3.3
WASHER_R = 6.5
LOAD_N = 800.0
LOAD_BOX = [0.0, 0.0, 60.0, 3.0, 40.0, 60.0]        # top of the wall, inside the thinnest wall (3 mm)
VM_EXCLUDE = [[0.0, 0.0, 54.0, 6.0, 40.0, 60.0]]     # ignore the load-application zone for the stress read-out


class Bracket(ParametricProblem):
    name = "lbracket_v1"
    space = {
        "t_base": ("float", 3.0, 10.0),
        "t_wall": ("float", 3.0, 10.0),
        "gusset": ("float", 0.0, 40.0),
        "gusset_t": ("float", 2.0, 12.0),
    }
    objectives = (("mass_kg", "min"), ("compliance_Nmm", "min"))
    constraints = (("vm_max_MPa", "<=", 150.0),)

    def __init__(self, h: float = 1.0, dtype: str = "float64"):
        self.h = h
        supports = [{"box": [x - WASHER_R, y - WASHER_R, 0, x + WASHER_R, y + WASHER_R, 0]} for x, y in HOLES]
        loads = [{"case": "pull", "box": LOAD_BOX, "force": [LOAD_N, 0, 0], "axis": "z"}]
        self.ev = VoxelFEAEvaluator(ENVELOPE, h, supports=supports, loads=loads, dtype=dtype,
                                    vm_exclude_boxes=VM_EXCLUDE)
        self.c = self.ev.elem_xyz()

    def build(self, p):
        x, y, z = self.c[..., 0], self.c[..., 1], self.c[..., 2]
        base = z <= p["t_base"]
        wall = x <= p["t_wall"]
        g, gt = p["gusset"], p["gusset_t"]
        gus = ((np.abs(y - 20.0) <= gt / 2) & (x >= p["t_wall"]) & (z >= p["t_base"]) &
               ((x - p["t_wall"]) + (z - p["t_base"]) <= g))
        solid = base | wall | gus
        for hx, hy in HOLES:
            solid &= ~(((x - hx) ** 2 + (y - hy) ** 2 <= HOLE_R ** 2) & (z <= p["t_base"] + 1e-9))
        return solid

    def evaluate(self, mask, p):
        return self.ev.evaluate(mask)

    def export(self, mask, p, stem):
        info = mask_to_stl(mask, self.h, stem + ".stl", smooth_iters=0)
        save_json(stem + ".json", {"params": p, "stl": info})
        return [stem + ".stl"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=24)
    ap.add_argument("--out", default="out/bracket_param")
    ap.add_argument("--h", type=float, default=1.0)
    ap.add_argument("--dtype", default="float64", help="float64 (default, accurate); float32 is indicative only")
    ap.add_argument("--export", type=int, default=4, help="number of Pareto designs to export as STL")
    a = ap.parse_args()
    prob = Bracket(a.h, a.dtype)
    study = run_study(prob, a.trials, a.out)
    paths = export_pareto(prob, study, a.out, max_n=a.export)
    print("Pareto designs exported:", paths)


if __name__ == "__main__":
    main()
