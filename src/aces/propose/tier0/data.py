"""Dataset loading and deterministic splitting for the Tier 0 pipeline.

Reads a dataset folder written by ``aces propose-dataset`` (grids.npy,
cond.npy, manifest.json) and applies the manifest's own normalisation —
the same numbers the dataset was built with, never re-fitted on load,
so a model trained on v2 can still read v3 identically until retrained.

Split rule: deterministic stratification by TPMS kind — rows are sorted
by (kind, wall_ratio, source) and every k-th row goes to validation.
No RNG, so two people evaluating the same run see the same split; the
sources held out are recorded in the train manifest (provenance, D18).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from .. import learned


def load_dataset(dataset_dir: str | Path) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    d = Path(dataset_dir)
    if not d.is_dir():
        raise ValueError(f"dataset folder {d} does not exist")
    for name in ("grids.npy", "cond.npy", "manifest.json"):
        if not (d / name).is_file():
            raise ValueError(f"dataset folder {d} is missing {name}")
    grids = np.load(d / "grids.npy")
    cond = np.load(d / "cond.npy")
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("kind") != "tier0_training_dataset":
        raise ValueError(f"{d}/manifest.json is not a tier0_training_dataset")
    if len(grids) != manifest["n"] or len(cond) != manifest["n"]:
        raise ValueError(f"dataset folder {d}: arrays disagree with manifest n={manifest['n']}")
    return grids, cond, manifest


def normalise(cond: np.ndarray, manifest: dict[str, Any]) -> np.ndarray:
    """Z-score the numeric block with the manifest's build-time stats."""
    stats = manifest["normalisation"]
    fields = manifest["numeric_fields"]
    nk = len(manifest["kinds"])
    out = cond.astype(np.float32).copy()
    for i, f in enumerate(fields):
        out[:, nk + i] = (out[:, nk + i] - stats[f]["mean"]) / stats[f]["std"]
    return out


def split(manifest: dict[str, Any], val_every: int = 5) -> tuple[list[int], list[int]]:
    """Deterministic stratified split: every val_every-th row per kind to validation.

    Returns (train_idx, val_idx). val_every must be >= 3 — a validation row
    thinner than that says nothing (same spirit as the D18 3-point floor).
    """
    if val_every < 3:
        raise ValueError("val_every below 3 leaves too little validation to mean anything")
    rows = manifest["rows"]
    train_idx: list[int] = []
    val_idx: list[int] = []
    for kind in manifest["kinds"]:
        order = sorted((i for i, r in enumerate(rows) if r["tpms"] == kind),
                       key=lambda i: (rows[i]["wall_ratio"], rows[i]["source"]))
        for rank, i in enumerate(order):
            (val_idx if rank % val_every == 0 else train_idx).append(i)
    if not val_idx:
        raise ValueError("split produced no validation rows; corpus too small")
    return sorted(train_idx), sorted(val_idx)


def kind_of(manifest: dict[str, Any], cond_row: np.ndarray) -> str:
    """The TPMS kind from a conditioning row's one-hot block."""
    nk = len(manifest["kinds"])
    return manifest["kinds"][int(np.argmax(cond_row[:nk]))]


def wall_ratio_of(manifest: dict[str, Any], cond_row: np.ndarray) -> float:
    """Requested wall_ratio from a raw (unnormalised) conditioning row."""
    nk = len(manifest["kinds"])
    return float(cond_row[nk + manifest["numeric_fields"].index("wall_ratio")])


def cell_m_of(manifest: dict[str, Any], cond_row: np.ndarray) -> float:
    nk = len(manifest["kinds"])
    return float(cond_row[nk + manifest["numeric_fields"].index("cell_m")])


def reynolds_of(manifest: dict[str, Any], cond_row: np.ndarray) -> float:
    nk = len(manifest["kinds"])
    v = float(cond_row[nk + manifest["numeric_fields"].index("reynolds")])
    return 10.0**v if manifest.get("reynolds_log10") else v
