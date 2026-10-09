"""`surrogate-*` solvers: evaluate a registered surrogate for a case, through the same
experiment.yaml / queue / MCP path as a real solver, with the surrogate's recorded error attached.

Registry entry (written by `simlab surrogate fit-tabular` / `train-fno`):
  "surrogate-<name>": {"module": "simlab.solvers.surrogate_predict", "physics": ["surrogate"],
                       "surrogate": {"kind": "tabular", "path": "...", "inputs": [...], "outputs": [...],
                                     "problem": "cases/bracket_param.py:Bracket" (field kind only)},
                       "validation": {...cv / held-out metrics...}}
"""
from __future__ import annotations

import json
import os

from .. import registry


class Adapter:
    name = "surrogate"

    def inputs(self, case: dict, spec) -> list:
        entry = registry.get(spec.solver)
        p = entry["surrogate"]["path"]
        files = [os.path.join(p, n) for n in ("model.joblib", "model.pt", "model.json", "metrics.json")]
        return [f for f in files if os.path.exists(f)]

    def run(self, case: dict, spec, run_dir: str) -> dict:
        entry = registry.get(spec.solver)
        sur = entry["surrogate"]
        val = entry.get("validation", {})
        if sur["kind"] == "tabular":
            from ..surrogate.tabular import TabularSurrogate

            model = TabularSurrogate.load(sur["path"])
            params = {k: float(case[k]) for k in model.inputs}
            res = model.predict_dict(params)
            res.update({"surrogate": spec.solver, "n_train": model.n_train, "artifacts": []})
            for k, v in (model.cv.get("per_output") or {}).items():
                res[f"cv_mape_pct_{k}"] = v["mape_pct"]
            return res
        if sur["kind"] == "field":
            import numpy as np

            from ..surrogate.fno import FieldSurrogate
            from ..surrogate.loop import load_problem

            prob = load_problem(sur["problem"], spec.base_dir if hasattr(spec, "base_dir") else os.getcwd(), **sur.get("problem_kwargs", {}))
            params = {k: float(case[k]) for k in prob.space}
            mask = prob.build(params)
            fs = FieldSurrogate(sur["path"])
            fields = fs.predict(mask)
            res = fs.summarise(mask)
            np.savez_compressed(os.path.join(run_dir, "fields_pred.npz"), **{k: v.astype(np.float32) for k, v in fields.items()}, mask=mask.astype(np.uint8))
            res.update({"surrogate": spec.solver, "val_rel_l2_vm": (fs.metrics.get("val") or {}).get("vm"),
                        "val_vm_max_mape_pct": (fs.metrics.get("val") or {}).get("vm_max_mape_pct"),
                        "artifacts": [os.path.join(run_dir, "fields_pred.npz")]})
            try:
                from ..post import plot_field_slices

                shape = mask.shape
                p = plot_field_slices(fields["vm"].reshape(shape), mask, 1.0, os.path.join(run_dir, "vm_pred_slices.png"),
                                      "FNO-predicted von Mises", "MPa", cmap="inferno")
                if p:
                    res["artifacts"].append(p)
            except Exception:
                pass
            return res
        raise ValueError(f"unknown surrogate kind {sur['kind']!r}")
