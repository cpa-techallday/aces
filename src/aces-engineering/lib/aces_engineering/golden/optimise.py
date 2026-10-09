"""The optimiser of aces golden: search the surrogate for the design to verify on the real solver.

Candidates live in the normalised design box [0, 1]^d: a full grid for d <= 3 (41 points per axis for d <= 2, 21 for
d = 3), a Latin hypercube for d >= 4 (`optimise.candidates` points, seed + 1000 * round), or the completed trials of an
Optuna TPE study on the surrogate (method optuna). Every candidate is predicted by the surrogate (mean mu, spread sd).

Feasibility (pinned): for every constraint c with margin m_c = max(tol, CV MAPE_c / 100) (or tol without cv_margin),
  `<=`:  mu_c + kappa * sd_c <= limit_c * (1 - m_c)        `>=`:  mu_c - kappa * sd_c >= limit_c * (1 + m_c)
Every limit and output is positive, so a point that was feasible under this rule and whose solve agrees with the
prediction within tol meets its constraint on the real solve. Candidates within normalised distance 0.02 of a design
already verified are dropped (a design can never verify itself). The winner is the best predicted objective over the
feasible candidates (ties to the first in candidate order); with none feasible, the closest infeasible candidate (least
total normalised violation) is returned for an infill solve instead.
"""
from __future__ import annotations

import gc
import itertools
import math
import time
import warnings
from pathlib import Path

from . import SCHEMA, clean

EXCLUSION_RADIUS = 0.02
GRID_POINTS = {1: 41, 2: 41, 3: 21}
MAP_GRID = 121


def _cfg(eff: dict) -> dict:
    d = eff.get("derived") or {}
    o = eff.get("optimise") or {}
    return {
        "names": list((d.get("variables") or {}).keys()),
        "bounds": {k: (float(v[0]), float(v[1])) for k, v in (d.get("variables") or {}).items()},
        "objective": d.get("objective") or {"output": "mass_kg", "sense": "min"},
        "constraints": list(d.get("constraints") or []),
        "tol": float(d.get("tol") if d.get("tol") is not None else 0.10),
        "kappa": float(d.get("kappa") if d.get("kappa") is not None else 1.0),
        "method": o.get("method") or "auto",
        "n_lhs": int(o.get("candidates") or 4096),
        "trials": int(o.get("trials") or 400),
        "cv_margin": bool(o.get("cv_margin", True)),
    }


def margins(eff: dict, fit_record: dict | None) -> dict:
    """m_c = max(tol, CV MAPE_c / 100) per constrained output (tol alone without cv_margin)."""
    c = _cfg(eff)
    cv = (fit_record or {}).get("cv") or {}
    out = {}
    for con in c["constraints"]:
        o = con["output"]
        mape = (cv.get(o) or {}).get("mape_pct")
        m = c["tol"]
        if c["cv_margin"] and isinstance(mape, (int, float)) and math.isfinite(float(mape)):
            m = max(m, float(mape) / 100.0)
        out[o] = m
    return out


def lhs_unit(d: int, n: int, seed: int):
    """Latin hypercube in [0, 1]^d with the golden.sweep.sample semantics (random-cd optimisation, numpy Generator)."""
    import numpy as np
    from scipy.stats import qmc

    try:
        sampler = qmc.LatinHypercube(d=d, optimization="random-cd", rng=np.random.default_rng(seed))
    except TypeError:  # SciPy < 1.15 names the generator `seed`
        sampler = qmc.LatinHypercube(d=d, optimization="random-cd", seed=np.random.default_rng(seed))
    return sampler.random(n)


def grid_unit(d: int):
    import numpy as np

    k = GRID_POINTS[d]
    axis = np.linspace(0.0, 1.0, k)
    return np.asarray(list(itertools.product(*([axis] * d))), dtype=float).reshape(-1, d)


def _scale(U, c: dict):
    import numpy as np

    lo = np.array([c["bounds"][v][0] for v in c["names"]])
    hi = np.array([c["bounds"][v][1] for v in c["names"]])
    return np.round(lo + np.asarray(U, dtype=float) * (hi - lo), 6)


def _unit(X, c: dict):
    import numpy as np

    lo = np.array([c["bounds"][v][0] for v in c["names"]])
    hi = np.array([c["bounds"][v][1] for v in c["names"]])
    return (np.asarray(X, dtype=float) - lo) / np.maximum(hi - lo, 1e-300)


def feasibility(mu, sd, outputs: list[str], c: dict, m: dict):
    """(feasible mask, total normalised violation) per candidate under the pinned rule."""
    import numpy as np

    n = mu.shape[0]
    feas = np.ones(n, dtype=bool)
    viol = np.zeros(n, dtype=float)
    for con in c["constraints"]:
        j = outputs.index(con["output"])
        lim = float(con["limit"])
        mc = m[con["output"]]
        if con["op"] == "<=":
            lhs = mu[:, j] + c["kappa"] * sd[:, j]
            bound = lim * (1.0 - mc)
            feas &= lhs <= bound
            viol += np.maximum(0.0, lhs - bound) / lim
        else:
            lhs = mu[:, j] - c["kappa"] * sd[:, j]
            bound = lim * (1.0 + mc)
            feas &= lhs >= bound
            viol += np.maximum(0.0, bound - lhs) / lim
    return feas, viol


def _optuna_candidates(sur, c: dict, m: dict, run, round_no: int, seed: int):
    """Completed trials of a TPE study on the surrogate (objective and margin-adjusted constraints)."""
    import numpy as np
    import optuna

    from gendesign.parametric import ParametricProblem, run_study
    from simlab.surrogate.loop import SurrogateProblem

    class _GoldenProblem(ParametricProblem):
        pass

    prob = _GoldenProblem()
    prob.name = f"golden-round-{round_no}"
    prob.space = {v: ("float", c["bounds"][v][0], c["bounds"][v][1]) for v in c["names"]}
    prob.objectives = [(c["objective"]["output"], c["objective"]["sense"])]
    prob.constraints = [(con["output"], con["op"], float(con["limit"]) * (1.0 - m[con["output"]]) if con["op"] == "<="
                         else float(con["limit"]) * (1.0 + m[con["output"]])) for con in c["constraints"]]
    out_dir = Path(run.path("optimise", f"round-{round_no}-optuna"))
    out_dir.mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        study = run_study(SurrogateProblem(prob, sur), c["trials"], str(out_dir), seed=int(seed) + int(round_no),
                          verbose=False)
    done = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    X = np.asarray([[float(t.params[v]) for v in c["names"]] for t in done], dtype=float).reshape(-1, len(c["names"]))
    del study
    gc.collect()  # release the SQLite handle on study.db (Windows keeps open files locked)
    return np.round(X, 6)


def search(sur, eff: dict, fit_record: dict, run, *, round_no: int, seed: int, exclude: list[dict]) -> dict:
    """Search round `round_no`; writes optimise/round-K.json and returns it.

    `exclude` holds the designs already verified, as {"case_id", "params"} dicts (a bare params dict also works)."""
    import numpy as np
    from threadpoolctl import threadpool_limits

    t0 = time.perf_counter()
    c = _cfg(eff)
    names = c["names"]
    d = len(names)
    outs = list(sur.outputs)
    m = margins(eff, fit_record)
    method = c["method"]
    if method == "auto":
        method = "grid" if d <= 3 else "lhs"
    with threadpool_limits(1):
        if method == "grid":
            X = _scale(grid_unit(d), c)
        elif method == "lhs":
            X = _scale(lhs_unit(d, c["n_lhs"], int(seed) + 1000 * int(round_no)), c)
        elif method == "optuna":
            X = _optuna_candidates(sur, c, m, run, round_no, seed)
        else:
            raise ValueError(f"unknown optimise.method {method!r}")
        n_all = int(X.shape[0])
        keep = np.ones(n_all, dtype=bool)
        excluded_ids = []
        U = _unit(X, c)
        for item in exclude or []:
            params = item.get("params") if isinstance(item, dict) and "params" in item else item
            if not isinstance(params, dict):
                continue
            u = _unit(np.asarray([[float(params[v]) for v in names]]), c)[0]
            keep &= np.linalg.norm(U - u, axis=1) > EXCLUSION_RADIUS
            if isinstance(item, dict) and item.get("case_id"):
                excluded_ids.append(item["case_id"])
        Xk = X[keep]
        if Xk.shape[0]:
            mu, sd = sur.predict(Xk)
        else:
            mu = np.zeros((0, len(outs)))
            sd = np.zeros((0, len(outs)))
    feas, viol = feasibility(mu, sd, outs, c, m)
    j_obj = outs.index(c["objective"]["output"])
    sign = 1.0 if c["objective"]["sense"] == "min" else -1.0

    def point(i: int, with_violation: bool = False) -> dict:
        p = {"params": {v: float(Xk[i, k]) for k, v in enumerate(names)},
             "pred": {o: float(mu[i, j]) for j, o in enumerate(outs)},
             "sd": {o: float(sd[i, j]) for j, o in enumerate(outs)}}
        if with_violation:
            p["violation"] = float(viol[i])
        return p

    winner = closest = None
    top = []
    fi = np.nonzero(feas)[0]
    if fi.size:
        order = fi[np.argsort(sign * mu[fi, j_obj], kind="stable")]
        winner = point(int(order[0]))
        top = [point(int(i)) for i in order[:5]]
    elif Xk.shape[0]:
        closest = point(int(np.argmin(viol)), with_violation=True)
    record = {
        "schema": SCHEMA["optimise"], "round": int(round_no), "method": method,
        "n_candidates": n_all, "n_excluded": int(n_all - int(keep.sum())), "n_feasible": int(fi.size),
        "kappa": c["kappa"], "tol": c["tol"], "cv_margin": c["cv_margin"], "margins": m,
        "objective": c["objective"], "constraints": c["constraints"],
        "exclusion_radius": EXCLUSION_RADIUS, "excluded": excluded_ids,
        "winner": winner, "from_feasible": winner is not None, "closest_infeasible": closest, "top": top,
        "seed": int(seed), "wall_s": round(time.perf_counter() - t0, 4),
    }
    record = clean(record)
    run.write_json(f"optimise/round-{round_no}.json", record)
    return record


def chosen(record: dict) -> dict | None:
    """The point a round verifies: the winner, else the closest infeasible candidate (an infill solve)."""
    return record.get("winner") or record.get("closest_infeasible")


def plot_map(sur, eff: dict, run, records: dict) -> "Path | None":
    """figures/optimise_map.png. d = 2: predicted objective contours over the design box, the region the margin rule
    calls infeasible hatched, sweep designs (failed ones hollow), verify designs as stars, the verified winner ringed.
    d = 1 or d >= 3: the objective (mean +- sd) along each variable through the winner, infeasible spans hatched.

    `records` = {"round": k, "fit": cv record of that round, "sweep": [{"case_id", "params", "ok"}],
                 "verify": [{"case_id", "params", "ok", "passed"}], "winner": params or None}."""
    if sur is None:
        return None
    import numpy as np

    from .report import CHART, chart_axes, chart_figure, chart_legend, save_chart

    c = _cfg(eff)
    names, outs = c["names"], list(sur.outputs)
    if not names:
        return None
    m = margins(eff, records.get("fit"))
    j_obj = outs.index(c["objective"]["output"])
    obj = c["objective"]["output"]
    path = Path(run.path("figures", "optimise_map.png"))
    sweep = records.get("sweep") or []
    verify = records.get("verify") or []
    win = records.get("winner")
    if len(names) == 2:
        (xa, xb) = names
        ga = np.linspace(*c["bounds"][xa], MAP_GRID)
        gb = np.linspace(*c["bounds"][xb], MAP_GRID)
        A, B = np.meshgrid(ga, gb, indexing="xy")
        P = np.column_stack([A.ravel(), B.ravel()])
        mu, sd = sur.predict(P)
        feas, _ = feasibility(mu, sd, outs, c, m)
        Z = mu[:, j_obj].reshape(A.shape)
        F = feas.reshape(A.shape)
        fig, ax = chart_figure(1, 1, size=(8.2, 6.4))
        ax = ax[0]
        from matplotlib.colors import LinearSegmentedColormap

        cmap = LinearSegmentedColormap.from_list("sky", CHART["ramp"])
        cs = ax.contourf(A, B, Z, levels=14, cmap=cmap)
        ax.contour(A, B, Z, levels=14, colors=[CHART["bg"]], linewidths=0.4)
        if (~F).any():
            ax.contourf(A, B, (~F).astype(float), levels=[0.5, 1.5], colors="none", hatches=["////"])
            ax.contour(A, B, F.astype(float), levels=[0.5], colors=[CHART["text"]], linewidths=1.2)
        cb = fig.colorbar(cs, ax=ax, pad=0.02)
        cb.set_label(f"predicted {obj}", color=CHART["muted"], fontsize=9, family="monospace")
        cb.ax.tick_params(colors=CHART["muted"], labelsize=8)
        cb.outline.set_edgecolor(CHART["grid"])
        ok = [s for s in sweep if s.get("ok") and s.get("params")]
        bad = [s for s in sweep if not s.get("ok") and s.get("params")]
        if ok:
            ax.scatter([s["params"][xa] for s in ok], [s["params"][xb] for s in ok], s=46, c=CHART["series"][0],
                       edgecolors=CHART["bg"], linewidths=1.5, zorder=4, label=f"sweep designs ({len(ok)})")
        if bad:
            ax.scatter([s["params"][xa] for s in bad], [s["params"][xb] for s in bad], s=46, facecolors="none",
                       edgecolors=CHART["text"], linewidths=1.4, zorder=4, label=f"failed designs ({len(bad)})")
        vs = [v for v in verify if v.get("params")]
        if vs:
            ax.scatter([v["params"][xa] for v in vs], [v["params"][xb] for v in vs], s=190, marker="*",
                       c=CHART["series"][1], edgecolors=CHART["bg"], linewidths=1.0, zorder=5,
                       label=f"verify solves ({len(vs)})")
            for v in vs:
                ax.annotate(v["case_id"], (v["params"][xa], v["params"][xb]), xytext=(7, 6), textcoords="offset points",
                            color=CHART["text"], fontsize=8, family="monospace")
        if win:
            ax.scatter([win[xa]], [win[xb]], s=420, facecolors="none", edgecolors=CHART["series"][2], linewidths=2.0,
                       zorder=6, label="verified winner")
        chart_axes(ax, xa, xb)
        ax.set_xlim(*c["bounds"][xa])
        ax.set_ylim(*c["bounds"][xb])
        mtxt = ", ".join(f"{o} {100 * v:.1f} %" for o, v in m.items())
        fig.suptitle(f"surrogate round {records.get('round')}: predicted {obj} (contours)", color=CHART["text"],
                     fontsize=10, x=0.01, ha="left")
        ax.set_title(f"hatched: infeasible under the margin rule, kappa {c['kappa']:g}; margins {mtxt}",
                     color=CHART["muted"], fontsize=8, loc="left", family="monospace")
        chart_legend(ax, markerscale=0.6)
        return save_chart(fig, path)
    # one or more panels: objective along each variable through the winner (or the box centre)
    base = dict(win) if win else {v: 0.5 * (c["bounds"][v][0] + c["bounds"][v][1]) for v in names}
    k = len(names)
    cols = min(3, k)
    rows = int(math.ceil(k / cols))
    fig, axes = chart_figure(rows, cols, size=(4.6 * cols, 3.6 * rows))
    for i, v in enumerate(names):
        ax = axes[i]
        g = np.linspace(*c["bounds"][v], MAP_GRID)
        P = np.asarray([[base[w] if w != v else x for w in names] for x in g])
        mu, sd = sur.predict(P)
        feas, _ = feasibility(mu, sd, outs, c, m)
        y, s = mu[:, j_obj], sd[:, j_obj]
        ax.fill_between(g, y - s, y + s, color=CHART["series"][0], alpha=0.15, linewidth=0)
        ax.plot(g, y, color=CHART["series"][0], linewidth=2.0, label=f"predicted {obj} (mean +- sd)")
        lo_y, hi_y = float(np.min(y - s)), float(np.max(y + s))
        if (~feas).any():
            ax.fill_between(g, lo_y, hi_y, where=~feas, facecolor="none", edgecolor=CHART["muted"], hatch="////",
                            linewidth=0, label="infeasible (margin rule)")
        if v in base and win:
            ax.axvline(base[v], color=CHART["series"][2], linewidth=1.5, label="verified winner")
        chart_axes(ax, v, f"predicted {obj}")
        if i == 0:
            chart_legend(ax)
    for j in range(k, len(axes)):
        axes[j].set_visible(False)
    fig.suptitle(f"surrogate round {records.get('round')}: {obj} along each variable through "
                 f"{'the verified winner' if win else 'the box centre'}", color=CHART["text"], fontsize=10, x=0.01,
                 ha="left")
    return save_chart(fig, path)
