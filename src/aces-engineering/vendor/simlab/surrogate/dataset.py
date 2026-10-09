"""Datasets for surrogates: tabular rows from Optuna trial tables or experiment summaries, field
samples from the voxel-FEA dataset generator. Every loader returns the source hashes so the
surrogate's evidence record says exactly what it was trained on."""
from __future__ import annotations

import csv
import json
import os
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..spec import sha256_file


def _rows_from_csv(path: str) -> List[dict]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _rows_from_experiment(exp_dir: str) -> List[dict]:
    p = os.path.join(exp_dir, "summary.json")
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)["rows"]


def load_tabular(sources: Sequence[dict], inputs: Sequence[str], outputs: Sequence[str],
                 require_converged: bool = True) -> Tuple[np.ndarray, np.ndarray, dict]:
    """sources: [{"kind": "trials_csv", "path": ...} | {"kind": "experiment", "exp_dir": ...}].
    Returns X (n, len(inputs)), Y (n, len(outputs)) and a manifest with hashes and drop counts."""
    X, Y, man_src = [], [], []
    dropped = 0
    for src in sources:
        if src["kind"] == "trials_csv":
            rows = _rows_from_csv(src["path"])
            man_src.append({"kind": "trials_csv", "path": os.path.abspath(src["path"]), "sha256": sha256_file(src["path"]), "rows": len(rows)})
        elif src["kind"] == "experiment":
            rows = _rows_from_experiment(src["exp_dir"])
            sp = os.path.join(src["exp_dir"], "summary.json")
            man_src.append({"kind": "experiment", "exp_dir": os.path.abspath(src["exp_dir"]), "sha256": sha256_file(sp), "rows": len(rows)})
        elif src["kind"] == "fields_npz":
            # the field dataset generator's scalars (params + scalar outputs per converged sample)
            d = np.load(src["path"], allow_pickle=False)
            pn, sn = [str(x) for x in d["param_names"]], [str(x) for x in d["scalar_names"]]
            rows = [dict(zip(pn, map(float, d["params"][i])), **dict(zip(sn, map(float, d["scalars"][i])))) for i in range(len(d["params"]))]
            man_src.append({"kind": "fields_npz", "path": os.path.abspath(src["path"]), "sha256": sha256_file(src["path"]), "rows": len(rows)})
        else:
            raise ValueError(f"unknown source kind {src['kind']!r}")
        for r in rows:
            try:
                if require_converged and "converged" in r and float(r["converged"]) < 0.5:
                    dropped += 1
                    continue
                if any(r.get(k) in (None, "") for k in list(inputs) + list(outputs)):
                    dropped += 1
                    continue
                X.append([float(r[k]) for k in inputs])
                Y.append([float(r[k]) for k in outputs])
            except (TypeError, ValueError):
                dropped += 1
    if not X:
        raise ValueError("no usable rows")
    return (np.asarray(X, dtype=float), np.asarray(Y, dtype=float),
            {"sources": man_src, "inputs": list(inputs), "outputs": list(outputs), "n": len(X), "dropped": dropped})


def load_fields(npz_path: str) -> Dict[str, np.ndarray]:
    d = np.load(npz_path, allow_pickle=False)
    out = {k: d[k] for k in d.files}
    out["_manifest"] = {"path": os.path.abspath(npz_path), "sha256": sha256_file(npz_path), "n": int(out["mask"].shape[0]),
                        "shape": list(out["mask"].shape[1:])}
    return out


def split_indices(n: int, val_frac: float = 0.2, seed: int = 0) -> Tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    perm = rng.permutation(n)
    nv = max(1, int(round(n * val_frac)))
    return np.sort(perm[nv:]), np.sort(perm[:nv])
