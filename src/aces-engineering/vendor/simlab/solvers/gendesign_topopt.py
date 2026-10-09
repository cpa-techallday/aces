from __future__ import annotations

import json
import os
import statistics

OVERRIDES = ("volfrac", "penal", "rmin_mm", "iterations", "change_tol", "elem_mm", "move", "emin")


class Adapter:
    name = "gendesign-topopt"

    def inputs(self, case: dict, spec) -> list:
        return [spec.resolve(case["case_file"])]

    def run(self, case: dict, spec, run_dir: str) -> dict:
        from gendesign.topopt3d import run_case

        with open(spec.resolve(case["case_file"]), "r", encoding="utf-8") as f:
            tc = json.load(f)
        for k in OVERRIDES:
            if k in case:
                tc[k] = case[k]
        if "solver" in case:
            tc.setdefault("solver", {}).update(case["solver"])
        dtype = case.get("dtype", spec.resources.precision)
        out = os.path.join(run_dir, "topopt")
        rep = run_case(tc, out, dtype=dtype, prefer_gpu=not case.get("cpu", False),
                       iters=case.get("iterations"), smooth=int(case.get("smooth", 0)), quiet=True)
        pcg = [max(h["pcg_iters"]) for h in rep["history"]]
        return {
            "compliance_Nmm": rep["final"]["compliance_Nmm"],
            "vol": rep["final"]["vol"],
            "umax_mm": rep["final"]["umax_mm"],
            "mass_kg": rep["final"]["mass_kg_at_rho_7850"],
            "iterations_run": rep["iterations_run"],
            "converged": int(bool(rep["converged"])),
            "elements": rep["grid"]["elements"],
            "dof": rep["grid"]["dof"],
            "pcg_mean": statistics.mean(pcg) if pcg else 0,
            "wall_s": rep["wall_s"],
            "device": rep["device"],
            "artifacts": [os.path.join(out, n) for n in ("design.stl", "slices.png", "report.json", "density.npy")],
        }
