"""Training driver for the Tier 0 reference model.

Closed-form fit (no gradient loop), but the run discipline is the same as
any future trainer: deterministic split, metrics on the held-out rows,
and a train manifest that names the dataset, the held-out sources, the
hyper-parameters and the code path — a model without provenance is not a
model (D18).
"""
from __future__ import annotations

import json
from typing import Any

import numpy as np

from . import data as tdata
from . import model as tmodel


def train(dataset_dir: str, out_dir, *, n_components: int | None = None,
          val_every: int = 5, ridge: float = 1e-3) -> dict[str, Any]:
    """Fit on train rows, score on held-out rows, write model + manifest into out_dir.

    Returns the metrics dict. Raises ValueError on any data problem.
    """
    grids, cond, manifest = tdata.load_dataset(dataset_dir)
    cond_n = tdata.normalise(cond, manifest)
    train_idx, val_idx = tdata.split(manifest, val_every=val_every)

    m = tmodel.fit(grids[train_idx], cond_n[train_idx],
                   {**manifest, "rows": [manifest["rows"][i] for i in train_idx]},
                   n_components=n_components, ridge=ridge)

    metrics: dict[str, Any] = {
        "n_train": len(train_idx), "n_val": len(val_idx),
        "components": int(m.components.shape[0]),
        "train_bce": m.bce(grids[train_idx], cond_n[train_idx]),
        "val_bce": m.bce(grids[val_idx], cond_n[val_idx]),
        "val_every": val_every, "ridge": ridge,
    }
    # conditioning regression quality on held-out rows (latent R^2)
    if len(val_idx) >= 2:
        from . import model as _m  # noqa: F401  (clarity alias)
        x = grids[val_idx].reshape(len(val_idx), -1).astype(np.float32) - m.mean_img
        z_true = x @ m.components.T
        z_pred = m.latent(cond_n[val_idx])
        ss_res = float(((z_true - z_pred) ** 2).sum())
        ss_tot = float(((z_true - z_true.mean(axis=0)) ** 2).sum()) or 1.0
        metrics["val_latent_r2"] = 1.0 - ss_res / ss_tot

    train_manifest = {
        "kind": tmodel.MODEL_KIND,
        "dataset_dir": str(dataset_dir),
        "dataset_n": manifest["n"],
        "dataset_resolution": manifest["resolution"],
        "val_every": val_every,
        "n_components_requested": n_components,
        "ridge": ridge,
        "held_out_sources": [manifest["rows"][i]["source"] for i in val_idx],
        "train_sources": [manifest["rows"][i]["source"] for i in train_idx],
        "metrics": metrics,
        "licence": "internal",
        "note": "reference floor model; any future learned proposer must beat "
                "val_bce and the evaluate.py harness to replace it (D26)",
    }
    import io
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        mp = Path(td) / "model.npz"
        m.save(mp, train_manifest)
        out_dir.write_bytes("model.npz", mp.read_bytes())
        out_dir.write_bytes("model.json", mp.with_suffix(".json").read_bytes())
    return metrics
