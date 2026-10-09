from __future__ import annotations

import importlib
import os
import sys


class Adapter:
    name = "gendesign-parametric"

    def inputs(self, case: dict, spec) -> list:
        mod = case["problem"].split(":")[0]
        p = spec.resolve(mod.replace(".", "/") + ".py")
        return [p] if os.path.isfile(p) else []

    def run(self, case: dict, spec, run_dir: str) -> dict:
        from gendesign.parametric import export_pareto, run_study

        mod_name, cls_name = case["problem"].split(":")
        base = spec.base_dir
        for p in (base, os.path.join(base, "cases")):
            if p not in sys.path:
                sys.path.insert(0, p)
        cls = getattr(importlib.import_module(mod_name.split(".")[-1]), cls_name)
        kwargs = dict(case.get("problem_kwargs", {}))
        kwargs.setdefault("dtype", case.get("dtype", "float32" if spec.resources.precision == "float32" else "float64"))
        prob = cls(**kwargs)
        out = os.path.join(run_dir, "study")
        study = run_study(prob, int(case.get("trials", 24)), out, seed=int(case.get("seed", 0)), verbose=False)
        paths = export_pareto(prob, study, out, max_n=int(case.get("export", 4)))
        trials = [t for t in study.trials if t.values is not None]
        feas = [t for t in trials if t.user_attrs.get("violation", 0.0) == 0.0]
        best = sorted(study.best_trials, key=lambda t: (t.user_attrs.get("violation", 0.0), t.values[0]))
        m = {"n_trials": len(trials), "n_feasible": len(feas), "n_pareto": len(study.best_trials),
             "wall_s_mean_eval": sum(t.user_attrs.get("eval_s", 0) for t in trials) / max(1, len(trials)),
             "artifacts": paths + [os.path.join(out, n) for n in ("pareto.json", "trials.csv")]}
        if best:
            for k, v in best[0].user_attrs.items():
                if isinstance(v, (int, float)):
                    m["best_" + k] = v
            m["best_trial"] = best[0].number
        return m
