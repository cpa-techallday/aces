"""The surrogate of aces golden: a tabular GP (or extra-trees) fitted on the real solves, with its K-fold error.

Training rows are read from the case records on disk: round 1 fits the FINISHED sweep designs, every later round also
the FINISHED verify designs of the earlier rounds (a verify solve that did not pass is still a real solve, so it
teaches the next round). Inputs are the spec's design variables in spec order, outputs are `derived.outputs`.

The fit is the vendored `simlab.surrogate.tabular.TabularSurrogate`, run with BLAS pinned to one thread and every
warning captured and counted (the GP raises sklearn ConvergenceWarnings on small training sets; the count goes into
the record, nothing is printed). Outputs are fitted in log space when every training value is positive (policy
`auto-positive`): every metric golden constrains is positive, and the log keeps the GP's predictions positive too.

Each round leaves `surrogate/round-K/`: model.joblib and model.json (the vendored save), cv.json (the K-fold error per
output, the log policy, the warning count, hashes) and data.csv (exactly the rows it was fitted on).
"""
from __future__ import annotations

import csv
import io
import math
import time
import warnings
from collections import Counter
from pathlib import Path

from . import EXIT_THIN, EXIT_USAGE, SCHEMA, GoldenError, clean, sha256_bytes, sha256_file


def variables(eff: dict) -> list[str]:
    return list(((eff.get("derived") or {}).get("variables") or {}).keys())


def outputs(eff: dict) -> list[str]:
    return list((eff.get("derived") or {}).get("outputs") or [])


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def _case_list(run) -> list[dict]:
    try:
        cases = run.cases()
    except Exception:
        cases = None
    if cases is None:
        cases = run.read_json("cases.json", []) or []
    return [c for c in cases if isinstance(c, dict) and c.get("case_id")]


def finished_results(run, case_id: str) -> dict | None:
    """A case's results.json when the case FINISHED (B1 writes results.json for FINISHED cases only; status.json
    must not say otherwise and the solve must have converged), else None."""
    res = run.read_json(f"{case_id}/results.json")
    if not isinstance(res, dict):
        return None
    st = run.read_json(f"{case_id}/status.json")
    if isinstance(st, dict) and st.get("status") not in (None, "FINISHED"):
        return None
    if res.get("converged") is False:
        return None
    return res


def case_params(case: dict, results: dict | None, names: list[str]) -> dict | None:
    """The design variables of a case: top-level keys of the case dict (section 8.1), else results.json "design"."""
    out = {}
    design = (results or {}).get("design") or {}
    for v in names:
        val = case.get(v, design.get(v))
        if not _finite(val):
            return None
        out[v] = float(val)
    return out


def training_rows(run, eff: dict, *, include_verify: bool):
    """(case_ids, X, Y, skipped): the FINISHED sweep cases (plus the FINISHED verify cases when include_verify) with
    every output finite, as numpy arrays in spec order; `skipped` lists the case ids left out."""
    import numpy as np

    names, outs = variables(eff), outputs(eff)
    ids, xs, ys, skipped = [], [], [], []
    for case in _case_list(run):
        role = case.get("role")
        if role != "sweep" and not (include_verify and role == "verify"):
            continue
        cid = case["case_id"]
        res = finished_results(run, cid)
        params = case_params(case, res, names) if res is not None else None
        y = [res.get(o) for o in outs] if res is not None else []
        if res is None or params is None or not all(_finite(v) for v in y):
            skipped.append(cid)
            continue
        ids.append(cid)
        xs.append([params[v] for v in names])
        ys.append([float(v) for v in y])
    x_arr = np.asarray(xs, dtype=float).reshape(len(xs), len(names))
    y_arr = np.asarray(ys, dtype=float).reshape(len(ys), len(outs))
    return ids, x_arr, y_arr, skipped


def _data_csv(ids, X, Y, names, outs) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["case_id", *names, *outs])
    for i, cid in enumerate(ids):
        w.writerow([cid, *[repr(float(v)) for v in X[i]], *[repr(float(v)) for v in Y[i]]])
    return buf.getvalue()


def log_policy(policy: str, Y) -> str:
    """The vendored `log_outputs` value for a spec policy: auto-positive -> "always" when every training value of every
    output is > 0, else "auto"; auto -> "auto"; never -> "never" (the vendored class logs nothing then)."""
    import numpy as np

    if policy == "auto-positive":
        return "always" if Y.size and bool(np.all(Y > 0)) else "auto"
    if policy == "auto":
        return "auto"
    return "never"


def fit(run, eff: dict, *, round_no: int, seed: int):
    """Fit round `round_no` on the records on disk; save surrogate/round-K/ and return (surrogate, cv record)."""
    import numpy as np
    from threadpoolctl import threadpool_limits

    from simlab.surrogate.tabular import TabularSurrogate

    d = eff.get("derived") or {}
    scfg = eff.get("surrogate") or {}
    names, outs = variables(eff), outputs(eff)
    if not names:
        raise GoldenError(EXIT_USAGE, "the spec has no design variables: there is nothing to fit a surrogate over",
                          stage="loop")
    ids, X, Y, skipped = training_rows(run, eff, include_verify=round_no > 1)
    n = len(ids)
    if n < 3:
        raise GoldenError(EXIT_THIN, f"round {round_no}: only {n} finished solves to fit the surrogate on (need 3)",
                          stage="loop")
    policy = scfg.get("log_outputs") or "auto-positive"
    applied = log_policy(policy, Y)
    kind = scfg.get("kind") or "gp"
    folds = int(scfg.get("folds") or 5)
    r2_flag = float(scfg.get("r2_flag") if scfg.get("r2_flag") is not None else 0.9)
    bounds = {k: [float(v[0]), float(v[1])] for k, v in (d.get("variables") or {}).items()}
    sur = TabularSurrogate(names, outs, bounds, kind=kind, log_outputs=applied, seed=int(seed))
    t0 = time.perf_counter()
    with threadpool_limits(1), warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cv = sur.fit(X, Y, folds=min(folds, n))
    wall = time.perf_counter() - t0
    kinds = Counter(getattr(w.category, "__name__", "Warning") for w in caught)
    rel = f"surrogate/round-{round_no}"
    out_dir = Path(run.path("surrogate", f"round-{round_no}"))
    out_dir.mkdir(parents=True, exist_ok=True)
    sur.save(str(out_dir))
    data = _data_csv(ids, X, Y, names, outs)
    run.write_text(f"{rel}/data.csv", data)
    per = cv.get("per_output") or {}
    cv_out = {o: {k: per.get(o, {}).get(k) for k in ("rmse", "mae", "rel_rmse", "mape_pct", "r2", "y_range")}
              for o in outs}
    flags = [f"{o} R2 {cv_out[o]['r2']:.3g} < {r2_flag:g}" for o in outs
             if _finite(cv_out[o].get("r2")) and cv_out[o]["r2"] < r2_flag]
    flags += [f"{o} R2 not finite" for o in outs if not _finite(cv_out[o].get("r2"))]
    record = {
        "schema": SCHEMA["surrogate"], "round": int(round_no), "kind": kind, "folds": int(cv.get("folds") or min(folds, n)),
        "n": n, "inputs": names, "outputs": outs,
        "log_outputs": {"policy": policy, "applied": applied,
                        "per_output": {o: bool(v) for o, v in (cv.get("log_outputs") or {}).items()}},
        "training_cases": ids, "skipped_cases": skipped,
        "data_sha256": sha256_bytes(data.encode("utf-8")),
        "cv": cv_out, "status": "weak" if flags else "pass", "flags": flags, "r2_flag": r2_flag,
        "gp_warnings": int(sum(kinds.values())), "gp_warning_kinds": dict(sorted(kinds.items())),
        "model_sha256": sha256_file(out_dir / "model.joblib"),
        "seed": int(seed), "wall_s": round(wall, 4),
    }
    record = clean(record)
    run.write_json(f"{rel}/cv.json", record)
    return sur, record


def load(model_dir: Path):
    """A saved round's surrogate (joblib is pickle: load only run directories you trust)."""
    from simlab.surrogate.tabular import TabularSurrogate

    return TabularSurrogate.load(str(model_dir))


def predict(sur, params: list[dict]):
    """(mu, sd) per point as lists of {output: value} dicts, for parameter dicts in any key order."""
    import numpy as np

    if not params:
        return [], []
    X = np.asarray([[float(p[v]) for v in sur.inputs] for p in params], dtype=float)
    mu, sd = sur.predict(X)
    mus = [{o: float(mu[i, j]) for j, o in enumerate(sur.outputs)} for i in range(len(params))]
    sds = [{o: float(sd[i, j]) for j, o in enumerate(sur.outputs)} for i in range(len(params))]
    return mus, sds
