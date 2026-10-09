"""Parametric generative loop.

An optimiser (Optuna: TPE for one objective, NSGA-II for several) drives a
ParametricProblem: build(params) -> design, evaluate(design) -> metrics. Constraints are
handled with a multiplicative penalty so infeasible designs rank behind feasible ones
but still inform the sampler. The study lives in SQLite under the output directory,
so a run can be stopped and resumed, and every trial's metrics are kept as user attrs.

This is the "generative" path that produces native parametric geometry: the genome is
a set of CAD parameters (build123d or Fusion user parameters), the evaluator is a real
solver (the GPU voxel FEA here, PyNite, CalculiX, PyFR, ... on the rig), and the result
is a Pareto front of manufacturable B-rep parts rather than a mesh.
"""
from __future__ import annotations

import csv
import os
import time
from typing import Dict, List, Sequence, Tuple

import optuna

from .export import save_json

Space = Dict[str, Tuple]


class ParametricProblem:
    name: str = "problem"
    space: Space = {}
    objectives: Sequence[Tuple[str, str]] = ()          # (metric, "min" | "max")
    constraints: Sequence[Tuple[str, str, float]] = ()  # (metric, "<=" | ">=", limit)

    def build(self, params: dict):
        raise NotImplementedError

    def evaluate(self, design, params: dict) -> Dict[str, float]:
        raise NotImplementedError

    def export(self, design, params: dict, stem: str) -> List[str]:
        return []


def suggest(trial: optuna.Trial, space: Space) -> dict:
    p = {}
    for name, spec in space.items():
        kind = spec[0]
        if kind == "float":
            p[name] = trial.suggest_float(name, float(spec[1]), float(spec[2]))
        elif kind == "logfloat":
            p[name] = trial.suggest_float(name, float(spec[1]), float(spec[2]), log=True)
        elif kind == "int":
            p[name] = trial.suggest_int(name, int(spec[1]), int(spec[2]))
        elif kind == "cat":
            p[name] = trial.suggest_categorical(name, list(spec[1]))
        else:
            raise ValueError(f"unknown parameter kind {kind!r} for {name}")
    return p


def violation(metrics: dict, constraints) -> float:
    v = 0.0
    for metric, op, lim in constraints:
        val = float(metrics[metric])
        d = (val - lim) if op == "<=" else (lim - val)
        if d > 0:
            v += d / (abs(lim) if lim else 1.0)
    return v


def run_study(problem: ParametricProblem, n_trials: int, out_dir: str, seed: int = 0,
              resume: bool = True, penalty: float = 10.0, verbose: bool = True) -> optuna.Study:
    os.makedirs(out_dir, exist_ok=True)
    directions = ["minimize" if d == "min" else "maximize" for _, d in problem.objectives]
    multi = len(directions) > 1
    sampler = optuna.samplers.NSGAIISampler(seed=seed) if multi else optuna.samplers.TPESampler(seed=seed)
    storage = "sqlite:///" + os.path.abspath(os.path.join(out_dir, "study.db")).replace("\\", "/")
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(study_name=problem.name, directions=directions, sampler=sampler,
                                storage=storage, load_if_exists=resume)

    def objective(trial: optuna.Trial):
        params = suggest(trial, problem.space)
        t0 = time.time()
        design = problem.build(params)
        m = {k: float(v) for k, v in problem.evaluate(design, params).items()}
        m["eval_s"] = time.time() - t0
        viol = violation(m, problem.constraints)
        m["violation"] = viol
        for k, v in m.items():
            trial.set_user_attr(k, v)
        vals = []
        f = 1.0 + penalty * viol
        for metric, d in problem.objectives:
            v = m[metric]
            vals.append(v * f if d == "min" else v / f)
        if verbose:
            ptxt = " ".join(f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}" for k, v in params.items())
            otxt = " ".join(f"{k}={m[k]:.4g}" for k, _ in problem.objectives)
            print(f"  trial {trial.number:3d}  {ptxt}  ->  {otxt}  viol={viol:.3g}  {m['eval_s']:.1f}s", flush=True)
        return tuple(vals) if multi else vals[0]

    study.optimize(objective, n_trials=n_trials)
    write_results(problem, study, out_dir)
    return study


def write_results(problem: ParametricProblem, study: optuna.Study, out_dir: str) -> dict:
    rows = []
    for t in study.trials:
        if t.state != optuna.trial.TrialState.COMPLETE:
            continue
        rows.append({"number": t.number, **t.params, **t.user_attrs, "values": list(t.values)})
    keys = sorted({k for r in rows for k in r if k != "values"})
    with open(os.path.join(out_dir, "trials.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys + ["values"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in keys + ["values"]})
    best = sorted(study.best_trials, key=lambda t: (t.user_attrs.get("violation", 0.0), t.values[0]))
    summary = {
        "study": problem.name,
        "n_complete": len(rows),
        "objectives": [list(o) for o in problem.objectives],
        "constraints": [list(c) for c in problem.constraints],
        "pareto": [{"number": t.number, "params": t.params, "metrics": t.user_attrs, "values": list(t.values)} for t in best],
    }
    save_json(os.path.join(out_dir, "pareto.json"), summary)
    return summary


def export_pareto(problem: ParametricProblem, study: optuna.Study, out_dir: str, max_n: int = 5) -> List[str]:
    """Rebuild and export the Pareto-optimal designs, feasible ones first."""
    best = sorted(study.best_trials, key=lambda t: (t.user_attrs.get("violation", 0.0), t.values[0]))
    paths: List[str] = []
    for t in best[:max_n]:
        design = problem.build(t.params)
        paths += problem.export(design, t.params, os.path.join(out_dir, f"design_{t.number:03d}"))
    return paths
