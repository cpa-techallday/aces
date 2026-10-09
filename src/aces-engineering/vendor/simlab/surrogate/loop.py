"""Surrogate-driven design loop with verification by the real solver.

Each round: NSGA-II explores the design box on the tabular surrogate (hundreds of trials in
seconds), the predicted-feasible Pareto designs plus the most uncertain designs are verified with
the real evaluator (the GPU voxel FEA), the surrogate's error on those proposals is recorded, the
verified points join the training set, and the surrogate is refitted. The output is a verified
front and an honest error history, not a surrogate's opinion of itself.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import time
from typing import Dict, List, Optional

import numpy as np

from gendesign.parametric import ParametricProblem, run_study

from .tabular import TabularSurrogate


def load_problem(ref: str, base_dir: str, **kwargs) -> ParametricProblem:
    mod_name, cls_name = ref.split(":")
    for p in (base_dir, os.path.join(base_dir, "cases")):
        if p not in sys.path:
            sys.path.insert(0, p)
    cls = getattr(importlib.import_module(mod_name.split("/")[-1].replace(".py", "").split(".")[-1]), cls_name)
    return cls(**kwargs)


class SurrogateProblem(ParametricProblem):
    """Same space/objectives/constraints as the real problem, evaluated by the surrogate."""

    def __init__(self, real: ParametricProblem, sur: TabularSurrogate):
        self.name = real.name + "-surrogate"
        self.space = real.space
        self.objectives = real.objectives
        self.constraints = real.constraints
        self.sur = sur

    def build(self, params: dict):
        return params

    def evaluate(self, design, params: dict) -> Dict[str, float]:
        return self.sur.predict_dict(params)


def _feasible(m: dict, constraints) -> bool:
    for k, op, lim in constraints:
        v = m.get(k)
        if v is None:
            return False
        if op == "<=" and v > lim:
            return False
        if op == ">=" and v < lim:
            return False
    return True


def _pareto(points: np.ndarray) -> np.ndarray:
    """indices of non-dominated rows (all objectives minimised)."""
    n = len(points)
    keep = np.ones(n, dtype=bool)
    for i in range(n):
        if not keep[i]:
            continue
        dom = np.all(points <= points[i], axis=1) & np.any(points < points[i], axis=1)
        if dom.any():
            keep[i] = False
    return np.nonzero(keep)[0]


def run_loop(problem: ParametricProblem, sur: TabularSurrogate, X: np.ndarray, Y: np.ndarray, out_dir: str,
             rounds: int = 3, candidates: int = 400, verify_pareto: int = 6, verify_uncertain: int = 2, seed: int = 0) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    keys = list(problem.space)
    obj_names = [o[0] for o in problem.objectives]
    obj_sign = np.array([1.0 if o[1] == "min" else -1.0 for o in problem.objectives])
    X, Y = np.asarray(X, dtype=float), np.asarray(Y, dtype=float)
    history = []
    verified_all: List[dict] = []
    t0 = time.time()
    for r in range(rounds):
        rd = os.path.join(out_dir, f"round{r}")
        sp = SurrogateProblem(problem, sur)
        study = run_study(sp, candidates, rd, seed=seed + r, verbose=False)
        trials = [t for t in study.trials if t.values is not None]
        # predicted-feasible Pareto designs (by surrogate mean) + most uncertain designs
        P = np.array([[t.params[k] for k in keys] for t in trials])
        mu, sd = sur.predict(P)
        preds = [dict(zip(sur.outputs, mu[i])) for i in range(len(trials))]
        feas = np.array([_feasible(m, problem.constraints) for m in preds])
        objs = np.array([[m[o] for o in obj_names] for m in preds]) * obj_sign
        cand = []
        if feas.any():
            fi = np.nonzero(feas)[0]
            pf = fi[_pareto(objs[fi])]
            order = np.argsort(objs[pf, 0])
            pick = pf[order][:: max(1, len(pf) // max(1, verify_pareto))][:verify_pareto]
            cand.extend(int(i) for i in pick)
        unc = np.argsort(-sd.max(axis=1))
        for i in unc:
            if len(cand) >= verify_pareto + verify_uncertain:
                break
            if int(i) not in cand:
                cand.append(int(i))
        # verify with the real solver
        verified = []
        for i in cand:
            p = dict(zip(keys, P[i].tolist()))
            tv = time.time()
            m_true = problem.evaluate(problem.build(p), p)
            m_pred = preds[i]
            errs = {o: {"pred": float(m_pred[o]), "true": float(m_true[o]),
                        "err_pct": float(100 * (m_pred[o] - m_true[o]) / max(abs(m_true[o]), 1e-12)),
                        "pred_std": float(sd[i, sur.outputs.index(o)])} for o in sur.outputs if o in m_true}
            verified.append({"params": p, "pred": {k: float(v) for k, v in m_pred.items()}, "true": {k: float(v) for k, v in m_true.items() if isinstance(v, (int, float))},
                             "errors": errs, "feasible_true": _feasible(m_true, problem.constraints), "eval_s": time.time() - tv,
                             "converged": bool(m_true.get("converged", 1.0))})
            if m_true.get("converged", 1.0):
                X = np.vstack([X, P[i]])
                Y = np.vstack([Y, [m_true[o] for o in sur.outputs]])
        verified_all.extend(verified)
        summary = {o: {"mape_pct": float(np.mean([abs(v["errors"][o]["err_pct"]) for v in verified if o in v["errors"]])),
                       "max_abs_err_pct": float(np.max([abs(v["errors"][o]["err_pct"]) for v in verified if o in v["errors"]]))}
                   for o in sur.outputs}
        # refit on the enlarged set
        cv = sur.fit(X, Y)
        rec = {"round": r, "surrogate_trials": len(trials), "n_pred_feasible": int(feas.sum()), "n_verified": len(verified),
               "n_true_feasible": int(sum(v["feasible_true"] for v in verified)), "proposal_error": summary,
               "cv_after_refit": {k: {"mape_pct": v["mape_pct"], "r2": v["r2"]} for k, v in cv["per_output"].items()},
               "n_train_after": int(len(X)), "wall_s": time.time() - t0}
        history.append(rec)
        print(f"[loop] round {r}: {len(trials)} surrogate trials, {int(feas.sum())} predicted feasible, verified {len(verified)} "
              f"({rec['n_true_feasible']} truly feasible); proposal error {json.dumps({k: round(v['mape_pct'], 2) for k, v in summary.items()})}; "
              f"train set now {len(X)}", flush=True)
    # verified front over everything verified (true values, feasible only)
    vf = [v for v in verified_all if v["feasible_true"] and v["converged"]]
    front = []
    if vf:
        objs = np.array([[v["true"][o] for o in obj_names] for v in vf]) * obj_sign
        for i in _pareto(objs):
            front.append(vf[i])
    out = {"problem": problem.name, "rounds": history, "verified": verified_all, "verified_front": front,
           "n_train_final": int(len(X)), "wall_s": time.time() - t0}
    with open(os.path.join(out_dir, "loop.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=str)
    np.savez(os.path.join(out_dir, "train_set_final.npz"), X=X, Y=Y, inputs=np.array(sur.inputs), outputs=np.array(sur.outputs))
    _write_md(out, obj_names, sur.outputs, os.path.join(out_dir, "loop.md"))
    return out


def _write_md(out: dict, obj_names, outputs, path: str) -> None:
    lines = [f"# Surrogate loop: {out['problem']}", "", "| round | surrogate trials | predicted feasible | verified | truly feasible | " +
             " | ".join(f"{o} MAPE %" for o in outputs) + " | train set |", "|---|---|---|---|---|" + "---|" * len(outputs) + "---|"]
    for r in out["rounds"]:
        lines.append(f"| {r['round']} | {r['surrogate_trials']} | {r['n_pred_feasible']} | {r['n_verified']} | {r['n_true_feasible']} | " +
                     " | ".join(f"{r['proposal_error'][o]['mape_pct']:.2f}" for o in outputs) + f" | {r['n_train_after']} |")
    lines += ["", "## Verified front (true solver values, feasible)", "", "| " + " | ".join(obj_names) + " | params |", "|" + "---|" * (len(obj_names) + 1)]
    for v in sorted(out["verified_front"], key=lambda v: v["true"][obj_names[0]]):
        lines.append("| " + " | ".join(f"{v['true'][o]:.5g}" for o in obj_names) + " | " + ", ".join(f"{k} {x:.3g}" for k, x in v["params"].items()) + " |")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
