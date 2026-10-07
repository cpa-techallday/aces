"""Multi-objective design search (NSGA-II via Optuna, MIT licence).

Returns every evaluated design plus the non-dominated feasible set.  Search runs
on the fast tier only; nothing it returns is final until the shortlist has been
verified on a registered solver ("surrogate ranks, solver signs off").
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import optuna
from pydantic import BaseModel

from .model import DesignModel, Evaluation, Param

# Optuna >= 5 records constraints on the trial; older releases use constraints_func.
_NATIVE_CONSTRAINTS = hasattr(optuna.trial.Trial, "set_constraint")


@dataclass
class SearchResult:
    evaluations: list[Evaluation]
    pareto: list[Evaluation]
    objective_names: list[str]
    constraint_names: list[str]

    @property
    def distinct(self) -> list[Evaluation]:
        return unique(self.evaluations)

    @property
    def feasible(self) -> list[Evaluation]:
        return [e for e in self.distinct if e.feasible]


def _apply_bounds(params: list[Param], overrides: dict[str, tuple[float, float]]) -> list[Param]:
    """The model's parameters with ``search.bounds`` applied.

    An override may only narrow a parameter: the model's own range is where its correlations and geometry
    tables are valid, and outside it the cost can explode (a wall ratio of 0.012 took 2.5 GB, audit M5).
    """
    known = {p.name for p in params}
    unknown = set(overrides) - known
    if unknown:
        raise ValueError(f"search.bounds names unknown parameters: {sorted(unknown)}; known: {sorted(known)}")
    out = []
    for p in params:
        if p.name in overrides:
            lo, hi = overrides[p.name]
            if p.kind == "categorical":
                raise ValueError(f"search.bounds[{p.name}]: {p.name} is categorical; it has no numeric bounds")
            if not (math.isfinite(lo) and math.isfinite(hi)) or not lo < hi:
                raise ValueError(f"search.bounds[{p.name}] must be finite with low < high, got {lo}, {hi}")
            if lo < p.low or hi > p.high:
                raise ValueError(f"search.bounds[{p.name}] = [{lo:g}, {hi:g}] goes outside the model's valid range "
                                 f"[{p.low:g}, {p.high:g}] {p.units}".rstrip() + "; bounds may only narrow it")
            p = Param(p.name, p.kind, lo, hi, p.choices, p.log, p.units)
        out.append(p)
    return out


def check_bounds(params: list[Param], overrides: dict[str, tuple[float, float]]) -> None:
    """ValueError if ``search.bounds`` names an unknown parameter or leaves the model's range."""
    _apply_bounds(params, overrides)


def _suggest(trial: optuna.Trial, p: Param) -> Any:
    if p.kind == "categorical":
        return trial.suggest_categorical(p.name, list(p.choices))
    return trial.suggest_float(p.name, p.low, p.high, log=p.log)


def dominates(a: Evaluation, b: Evaluation) -> bool:
    keys = a.objectives.keys()
    no_worse = all(a.objectives[k] <= b.objectives[k] for k in keys)
    better = any(a.objectives[k] < b.objectives[k] for k in keys)
    return no_worse and better


def _key(e: Evaluation) -> tuple:
    return tuple(sorted((k, round(v, 12) if isinstance(v, float) else v) for k, v in e.params.items()))


def unique(evals: list[Evaluation]) -> list[Evaluation]:
    """Drop repeat evaluations of the same parameter set (NSGA-II resamples)."""
    seen, out = set(), []
    for e in evals:
        k = _key(e)
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out


def pareto_front(evals: list[Evaluation]) -> list[Evaluation]:
    feas = unique([e for e in evals if e.feasible])
    return [e for e in feas if not any(dominates(o, e) for o in feas if o is not e)]


def run(
    model: DesignModel,
    req: BaseModel,
    *,
    trials: int = 200,
    seed: int = 0,
    population: int = 40,
    bounds: dict[str, tuple[float, float]] | None = None,
) -> SearchResult:
    params = _apply_bounds(model.parameters(req), bounds or {})
    evaluations: list[Evaluation] = []
    names: dict[str, list[str]] = {}

    def objective(trial: optuna.Trial):
        values = {p.name: _suggest(trial, p) for p in params}
        ev = model.evaluate(values, req)
        # A non-finite constraint would abort Optuna; treat it as a hard violation instead.
        bad = [k for k, v in ev.constraints.items() if not math.isfinite(v)]
        for k in bad:
            ev.constraints[k] = 1e9
            ev.warnings.append(f"constraint {k} was not finite; design marked infeasible")
        if not names:
            names["obj"] = list(ev.objectives)
            names["con"] = list(ev.constraints)
        if _NATIVE_CONSTRAINTS:
            for k in names["con"]:
                trial.set_constraint(k, ev.constraints[k])
        else:
            trial.set_user_attr("constraints", [ev.constraints[k] for k in names["con"]])
        evaluations.append(ev)
        return tuple(ev.objectives[k] for k in names["obj"])

    def constraints(trial: optuna.FrozenTrial):
        return trial.user_attrs["constraints"]

    # Objective count is fixed by the model; probe it once without consuming a trial.
    probe = model.evaluate({p.name: (p.choices[0] if p.kind == "categorical" else 0.5 * (p.low + p.high)) for p in params}, req)
    directions = ["minimize"] * len(probe.objectives)

    logging.getLogger("optuna").setLevel(logging.WARNING)
    kwargs = {} if _NATIVE_CONSTRAINTS else {"constraints_func": constraints}
    sampler = optuna.samplers.NSGAIISampler(population_size=population, seed=seed, **kwargs)
    study = optuna.create_study(directions=directions, sampler=sampler)
    study.optimize(objective, n_trials=trials, show_progress_bar=False)

    return SearchResult(evaluations, pareto_front(evaluations), names.get("obj", []), names.get("con", []))
