"""Evaluation harness for Tier 0 models — the part that outlives any model.

Metrics, all computable on a small corpus and all honest:

- ``spearman_rho``: the AirfRANS-style ranking metric. A surrogate or a
  generator is useful if it ORDERS designs correctly, even before its
  absolute numbers are tight. This is the library shortlist §9.1 adopted.
- ``sample_validity``: generated grids are measured on the SAME voxel
  measurement layer as every other proposer (voxel_measure, D5/D10):
  solid fraction vs requested wall_ratio (conditioning fidelity), neck
  per axis (printability), enclosed void fraction (powder traps, must
  be ~0). A model that emits unmeasurable geometry fails here, not at
  print time.
- ``ranking_report``: the conditioning-fidelity ranking test — samples
  across a wall_ratio sweep are ranked by requested wall_ratio and by
  measured solid fraction; Spearman-ρ near 1 means the model actually
  listens to its conditioning.
"""
from __future__ import annotations

from typing import Any

import numpy as np
from scipy import stats

from ..voxel_measure import measure_voxel_solid
from . import data as tdata
from . import model as tmodel


def spearman_rho(a: list[float] | np.ndarray, b: list[float] | np.ndarray) -> float:
    """Spearman rank correlation; refuses degenerate inputs honestly."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if len(a) != len(b) or len(a) < 3:
        raise ValueError("spearman_rho needs >= 3 paired values (D18 floor)")
    if np.all(a == a[0]) or np.all(b == b[0]):
        raise ValueError("spearman_rho is undefined for constant inputs")
    return float(stats.spearmanr(a, b).statistic)


def sample_validity(m: tmodel.PCAConditionalModel, manifest: dict[str, Any],
                    cond_raw: np.ndarray, cond_norm: np.ndarray,
                    n_per: int = 2, seed: int = 0) -> dict[str, Any]:
    """Measure n_per samples at each conditioning row on ACES definitions."""
    rows: list[dict[str, Any]] = []
    for i in range(len(cond_raw)):
        kind = tdata.kind_of(manifest, cond_raw[i])
        wr = tdata.wall_ratio_of(manifest, cond_raw[i])
        pitch = tdata.cell_m_of(manifest, cond_raw[i])
        sf_target = m.target_solid_fraction(kind, wr)
        anchors = m.kind_sf.get(kind, [[wr]])
        ws = [a[0] for a in anchors]
        # cond rows are float32, anchors come from the manifest — allow ULP noise
        tol = 1e-6 * max(1.0, max(abs(w) for w in ws))
        in_env = min(ws) - tol <= wr <= max(ws) + tol  # anchor envelope, same rule as calibration validity
        grids = m.sample(cond_norm[i], kind, wr, n_per, seed + i)
        for j, g in enumerate(grids):
            facts = measure_voxel_solid(g != 0, pitch, "tier0-pca", seed + i,
                                        notes=(f"sample {j}",))
            rows.append({
                "row": i, "kind": kind, "wall_ratio_requested": wr,
                "in_anchor_envelope": in_env,
                "solid_fraction_target": sf_target,
                "solid_fraction_measured": facts.solid_fraction,
                "neck_min_m": min(facts.neck_m),
                "isolated_void_fraction": facts.isolated_void_fraction,
            })
    sf_err = [abs(r["solid_fraction_measured"] - r["solid_fraction_target"]) for r in rows]
    return {
        "n_samples": len(rows),
        "out_of_anchor_envelope_share": float(np.mean([not r["in_anchor_envelope"] for r in rows])),
        "solid_fraction_abs_err_max": max(sf_err),
        "solid_fraction_abs_err_mean": float(np.mean(sf_err)),
        "neck_blocked_share": float(np.mean([r["neck_min_m"] <= 0 for r in rows])),
        "enclosed_void_share": float(np.mean([r["isolated_void_fraction"] > 0.01 for r in rows])),
        "rows": rows,
    }


def ranking_report(m: tmodel.PCAConditionalModel, manifest: dict[str, Any],
                   cond_raw: np.ndarray, cond_norm: np.ndarray,
                   n_per: int = 2, seed: int = 0) -> dict[str, Any]:
    """Does the model rank-order its samples by the requested wall_ratio?

    Groups samples by kind, ranks within each kind. Needs >= 3 distinct
    wall_ratios in some kind to say anything (D18 floor).
    """
    v = sample_validity(m, manifest, cond_raw, cond_norm, n_per=n_per, seed=seed)
    by_kind: dict[str, list[dict[str, Any]]] = {}
    skipped_out = 0
    for r in v["rows"]:
        # Rows outside the training anchor envelope are clamped by np.interp
        # at the edge anchor, producing tied target solid fractions — the
        # model honestly cannot extrapolate there (same envelope discipline
        # as the calibration registry), so exclude them from ranking.
        if not r["in_anchor_envelope"]:
            skipped_out += 1
            continue
        by_kind.setdefault(r["kind"], []).append(r)
    out: dict[str, Any] = {"out_of_envelope_excluded": skipped_out}
    for kind, rs in by_kind.items():
        req = [r["wall_ratio_requested"] for r in rs]
        meas = [r["solid_fraction_measured"] for r in rs]
        if len(set(req)) < 3:
            out[kind] = {"skipped": f"fewer than 3 distinct wall_ratios ({len(set(req))})"}
            continue
        out[kind] = {"spearman_rho": spearman_rho(req, meas), "n": len(rs)}
    return out


def evaluate(run_dir: str, dataset_dir: str, *, n_per: int = 2, seed: int = 0,
             val_every: int = 5) -> dict[str, Any]:
    """Full harness on a trained run: BCE on the held-out split, validity, ranking."""
    from pathlib import Path
    m = tmodel.PCAConditionalModel.load(Path(run_dir) / "model.npz")
    grids, cond, manifest = tdata.load_dataset(dataset_dir)
    cond_n = tdata.normalise(cond, manifest)
    train_idx, val_idx = tdata.split(manifest, val_every=val_every)
    report: dict[str, Any] = {
        "run_dir": str(run_dir), "dataset_dir": str(dataset_dir),
        "val_bce": m.bce(grids[val_idx], cond_n[val_idx]),
        "train_bce": m.bce(grids[train_idx], cond_n[train_idx]),
        "validity": sample_validity(m, manifest, cond, cond_n, n_per=n_per, seed=seed),
        "ranking": ranking_report(m, manifest, cond, cond_n, n_per=n_per, seed=seed),
    }
    return report
