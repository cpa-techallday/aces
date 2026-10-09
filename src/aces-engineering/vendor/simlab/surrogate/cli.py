"""`python -m simlab surrogate <command>`:

  fit-tabular  --name bracket-gp --problem cases/bracket_param.py:Bracket --trials out/bracket_param_fp64/trials.csv
               [--experiment out/exp/<id>] --outputs mass_kg,compliance_Nmm,vm_max_MPa [--kind gp|et] [--out out/surrogate/<name>]
  train-fno    --name bracket-fno --dataset out/surrogate/bracket_fields/dataset.npz [--epochs 200 --batch 8 --width 24 --modes 12,8,12]
               [--problem cases/bracket_param.py:Bracket] [--backend auto|builtin|physicsnemo]
  loop         --name bracket-gp --problem cases/bracket_param.py:Bracket [--rounds 3 --candidates 400 --verify 6 --uncertain 2]
  predict      --name bracket-gp --params '{"t_base": 6, ...}'

Each fit/train writes the model under out/surrogate/<name>/ and a registry entry `surrogate-<name>`
with the validation error, so `solver: surrogate-<name>` works in any experiment.yaml.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os

import numpy as np

from .. import registry

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _register(name: str, entry: dict) -> None:
    reg = registry.load_registry()
    reg[f"surrogate-{name}"] = entry
    with open(registry.REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2)


def cmd_fit_tabular(a):
    from .dataset import load_tabular
    from .loop import load_problem
    from .tabular import TabularSurrogate

    prob = load_problem(a.problem, REPO) if a.problem else None
    inputs = a.inputs.split(",") if a.inputs else list(prob.space)
    outputs = a.outputs.split(",")
    sources = []
    if a.trials:
        sources += [{"kind": "trials_csv", "path": p} for p in a.trials.split(",")]
    if a.experiment:
        sources += [{"kind": "experiment", "exp_dir": p} for p in a.experiment.split(",")]
    if a.npz:
        sources += [{"kind": "fields_npz", "path": p} for p in a.npz.split(",")]
    X, Y, man = load_tabular(sources, inputs, outputs)
    bounds = {k: (prob.space[k][1], prob.space[k][2]) for k in inputs} if prob else None
    sur = TabularSurrogate(inputs, outputs, bounds, kind=a.kind, seed=a.seed)
    cv = sur.fit(X, Y, folds=a.folds)
    out = a.out or os.path.join(REPO, "out", "surrogate", a.name)
    sur.save(out)
    with open(os.path.join(out, "dataset_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, indent=2)
    status = "pass" if all(v["r2"] >= a.min_r2 for v in cv["per_output"].values()) else "fail"
    entry = {"module": "simlab.solvers.surrogate_predict", "physics": ["surrogate"], "precision": ["float64"], "image": "simlab-ml:0.1",
             "description": f"tabular {a.kind} surrogate of {a.problem or 'table'}: {inputs} -> {outputs}",
             "surrogate": {"kind": "tabular", "path": out, "inputs": inputs, "outputs": outputs, "problem": a.problem, "model": a.kind},
             "validation": {"status": status, "date": dt.date.today().isoformat(), "n_train": int(len(X)), "folds": cv["folds"],
                            "cv": {k: {kk: v[kk] for kk in ("rmse", "mae", "mape_pct", "r2", "y_range")} for k, v in cv["per_output"].items()},
                            "dataset": man, "note": f"K-fold error on the original scale; registry status fails when any R2 < {a.min_r2}"}}
    _register(a.name, entry)
    print(json.dumps({"name": f"surrogate-{a.name}", "n": len(X), "dropped": man["dropped"], "status": status,
                      "cv": {k: {"mape_pct": round(v["mape_pct"], 3), "r2": round(v["r2"], 4)} for k, v in cv["per_output"].items()},
                      "wall_s": round(cv["wall_s"], 1), "out": out}, indent=2))


def cmd_train_fno(a):
    from .fno import train

    out = a.out or os.path.join(REPO, "out", "surrogate", a.name)
    modes = tuple(int(v) for v in a.modes.split(","))
    m = train(a.dataset, out, epochs=a.epochs, batch=a.batch, lr=a.lr, modes=modes, width=a.width, layers=a.layers,
              val_frac=a.val_frac, seed=a.seed, backend=a.backend)
    status = "pass" if m["val"].get("vm", 1.0) <= a.max_rel_l2 else "fail"
    entry = {"module": "simlab.solvers.surrogate_predict", "physics": ["surrogate"], "precision": ["float32"], "image": "simlab-ml:0.1",
             "description": f"FNO field surrogate ({m['backend']}): design mask -> {m['history'] and list(m['scales'])} fields of the voxel FEA",
             "surrogate": {"kind": "field", "path": out, "problem": a.problem, "fields": list(m["scales"]), "backend": m["backend"]},
             "validation": {"status": status, "date": dt.date.today().isoformat(), "n_train": m["n_train"], "n_val": m["n_val"],
                            "held_out": m["val"], "train_fit": m["train"], "n_params": m["n_params"], "epochs": m["epochs"],
                            "dataset": m["dataset"], "wall_s": m["wall_s"],
                            "note": f"relative L2 over solid cells on held-out designs; status fails when vm rel-L2 > {a.max_rel_l2}"}}
    _register(a.name, entry)
    print(json.dumps({"name": f"surrogate-{a.name}", "backend": m["backend"], "n_params": m["n_params"], "val": m["val"], "train": m["train"],
                      "wall_s": round(m["wall_s"], 1), "status": status, "out": out}, indent=2))


def cmd_loop(a):
    from .loop import load_problem, run_loop
    from .tabular import TabularSurrogate

    entry = registry.get(f"surrogate-{a.name}")
    sur = TabularSurrogate.load(entry["surrogate"]["path"])
    prob = load_problem(a.problem or entry["surrogate"]["problem"], REPO)
    man_path = os.path.join(entry["surrogate"]["path"], "dataset_manifest.json")
    from .dataset import load_tabular

    with open(man_path, "r", encoding="utf-8") as f:
        man = json.load(f)
    sources = [{"kind": s["kind"], "path": s.get("path"), "exp_dir": s.get("exp_dir")} for s in man["sources"]]
    sources = [{k: v for k, v in s.items() if v is not None} for s in sources]
    X, Y, _ = load_tabular(sources, sur.inputs, sur.outputs)
    out = a.out or os.path.join(REPO, "out", "surrogate", a.name, "loop")
    res = run_loop(prob, sur, X, Y, out, rounds=a.rounds, candidates=a.candidates, verify_pareto=a.verify, verify_uncertain=a.uncertain, seed=a.seed)
    # persist the refitted surrogate and its new error
    sur.save(entry["surrogate"]["path"])
    entry["validation"]["after_loop"] = {"rounds": [{"round": r["round"], "proposal_error": r["proposal_error"], "cv_after_refit": r["cv_after_refit"],
                                                      "n_train_after": r["n_train_after"]} for r in res["rounds"]],
                                         "n_verified_front": len(res["verified_front"]), "loop_dir": out}
    _register(a.name, entry)
    print(json.dumps({"rounds": [{k: r[k] for k in ("round", "n_verified", "n_true_feasible", "proposal_error", "n_train_after")} for r in res["rounds"]],
                      "verified_front": len(res["verified_front"]), "wall_s": round(res["wall_s"], 1), "out": out}, indent=2))


def cmd_predict(a):
    from .tabular import TabularSurrogate

    entry = registry.get(f"surrogate-{a.name}")
    sur = TabularSurrogate.load(entry["surrogate"]["path"])
    print(json.dumps(sur.predict_dict(json.loads(a.params)), indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="simlab surrogate", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fit-tabular")
    f.add_argument("--name", required=True)
    f.add_argument("--problem", default=None, help="module.py:Class providing space (bounds) for input scaling")
    f.add_argument("--trials", default=None, help="comma list of Optuna trials.csv files")
    f.add_argument("--experiment", default=None, help="comma list of experiment dirs with summary.json")
    f.add_argument("--npz", default=None, help="comma list of field-dataset npz files (params + scalars)")
    f.add_argument("--inputs", default=None, help="comma list; default = problem.space keys")
    f.add_argument("--outputs", required=True)
    f.add_argument("--kind", default="gp", choices=["gp", "et"])
    f.add_argument("--folds", type=int, default=5)
    f.add_argument("--min-r2", type=float, default=0.9)
    f.add_argument("--seed", type=int, default=0)
    f.add_argument("--out", default=None)
    f.set_defaults(func=cmd_fit_tabular)
    t = sub.add_parser("train-fno")
    t.add_argument("--name", required=True)
    t.add_argument("--dataset", required=True)
    t.add_argument("--problem", default=None)
    t.add_argument("--epochs", type=int, default=200)
    t.add_argument("--batch", type=int, default=8)
    t.add_argument("--lr", type=float, default=2e-3)
    t.add_argument("--modes", default="12,8,12")
    t.add_argument("--width", type=int, default=24)
    t.add_argument("--layers", type=int, default=4)
    t.add_argument("--val-frac", type=float, default=0.2)
    t.add_argument("--backend", default="auto", choices=["auto", "builtin", "physicsnemo"])
    t.add_argument("--max-rel-l2", type=float, default=0.15)
    t.add_argument("--seed", type=int, default=0)
    t.add_argument("--out", default=None)
    t.set_defaults(func=cmd_train_fno)
    lp = sub.add_parser("loop")
    lp.add_argument("--name", required=True)
    lp.add_argument("--problem", default=None)
    lp.add_argument("--rounds", type=int, default=3)
    lp.add_argument("--candidates", type=int, default=400)
    lp.add_argument("--verify", type=int, default=6)
    lp.add_argument("--uncertain", type=int, default=2)
    lp.add_argument("--seed", type=int, default=0)
    lp.add_argument("--out", default=None)
    lp.set_defaults(func=cmd_loop)
    pr = sub.add_parser("predict")
    pr.add_argument("--name", required=True)
    pr.add_argument("--params", required=True, help="JSON dict of inputs")
    pr.set_defaults(func=cmd_predict)
    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
