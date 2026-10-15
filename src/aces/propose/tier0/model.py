"""The Tier 0 reference model: a PCA-conditional Gaussian geometry sampler.

Clean-room baseline (licence "internal", D26). Geometry lives in a PCA
latent space fitted on the training grids; a ridge map takes the
normalised conditioning vector to a latent mean, and residuals around it
set the sampling noise. Sampling draws a latent point, maps it back to
voxel space, and binarises at the quantile that matches the corpus-mean
solid fraction for the requested kind and wall_ratio neighbourhood —
so samples respect the measured solid-fraction trend by construction.

This is the floor any future learned proposer (latent diffusion, D26)
must beat on the evaluate.py harness. It is NOT shipped as a proposer:
it cannot register (its samples are training-harness artefacts, not
proposals), and it never sees a customer.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

MODEL_KIND = "tier0_pca_conditional"


def _bce(target: np.ndarray, recon: np.ndarray) -> float:
    p = np.clip(recon, 1e-6, 1 - 1e-6)
    return float(-(target * np.log(p) + (1 - target) * np.log(1 - p)).mean())


class PCAConditionalModel:
    def __init__(self, mean_img: np.ndarray, components: np.ndarray, cond_w: np.ndarray,
                 noise_std: np.ndarray, kind_sf: dict[str, list[list[float]]],
                 shape: tuple[int, int, int]):
        self.mean_img = mean_img            # (V,)
        self.components = components        # (k, V)
        self.cond_w = cond_w                # (D+1, k) ridge map (with bias row)
        self.noise_std = noise_std          # (k,)
        # per-kind [[wall_ratio, solid_fraction], ...] anchor points from the corpus
        self.kind_sf = kind_sf
        self.shape = shape

    # -- core maps ---------------------------------------------------------
    def latent(self, cond_norm: np.ndarray) -> np.ndarray:
        """Conditioning -> latent mean. cond_norm (n, D) -> (n, k)."""
        a = np.concatenate([cond_norm, np.ones((len(cond_norm), 1), np.float32)], axis=1)
        return a @ self.cond_w

    def decode(self, z: np.ndarray) -> np.ndarray:
        """Latent -> voxel-space reconstruction in (0,1)-ish range. (n,k) -> (n,V)."""
        return self.mean_img + z @ self.components

    def reconstruct(self, grids: np.ndarray, cond_norm: np.ndarray) -> np.ndarray:
        x = grids.reshape(len(grids), -1).astype(np.float32) - self.mean_img
        z = x @ self.components.T
        return self.decode(z)

    def bce(self, grids: np.ndarray, cond_norm: np.ndarray) -> float:
        return _bce(grids.reshape(len(grids), -1).astype(np.float32),
                    self.reconstruct(grids, cond_norm))

    # -- sampling ----------------------------------------------------------
    def target_solid_fraction(self, kind: str, wall_ratio: float) -> float:
        """Corpus-mean solid fraction for (kind, wall_ratio), linear in the anchors."""
        anchors = self.kind_sf.get(kind)
        if not anchors:
            raise ValueError(f"model has no solid-fraction anchors for kind {kind!r}")
        xs = np.array([a[0] for a in anchors])
        ys = np.array([a[1] for a in anchors])
        if len(anchors) == 1:
            return float(ys[0])
        return float(np.interp(wall_ratio, xs, ys))

    def sample(self, cond_norm: np.ndarray, kind: str, wall_ratio: float,
               n: int, seed: int) -> np.ndarray:
        """n binarised grids (n, *shape) uint8 at one conditioning point."""
        rng = np.random.default_rng(seed)
        z = self.latent(cond_norm.reshape(1, -1))[0]
        draws = z + rng.normal(0.0, self.noise_std, size=(n, len(z))).astype(np.float32)
        x = self.decode(draws)
        sf = self.target_solid_fraction(kind, wall_ratio)
        q = np.clip(1.0 - sf, 0.0, 1.0)
        # one threshold per draw, matched to that draw's own distribution
        thr = np.quantile(x, q, axis=1, keepdims=True)
        return (x >= thr).astype(np.uint8).reshape(n, *self.shape)

    # -- persistence -------------------------------------------------------
    def save(self, path: str | Path, train_manifest: dict[str, Any]) -> None:
        np.savez_compressed(
            path, mean_img=self.mean_img, components=self.components,
            cond_w=self.cond_w, noise_std=self.noise_std,
            kind_sf=np.frombuffer(json.dumps(self.kind_sf).encode(), dtype=np.uint8),
            shape=np.array(self.shape))
        meta = Path(path).with_suffix(".json")
        meta.write_text(json.dumps(train_manifest, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "PCAConditionalModel":
        z = np.load(path)
        kind_sf = json.loads(z["kind_sf"].tobytes().decode())
        return cls(z["mean_img"], z["components"], z["cond_w"], z["noise_std"],
                   kind_sf, tuple(int(v) for v in z["shape"]))


def fit(grids: np.ndarray, cond_norm: np.ndarray, manifest: dict[str, Any],
        n_components: int | None = None, ridge: float = 1e-3) -> PCAConditionalModel:
    """Closed-form fit. Deterministic — same dataset, same model."""
    n = len(grids)
    if n < 3:
        raise ValueError("fewer than 3 rows cannot ground a latent space (D18 floor)")
    k = min(n_components or (n - 1), n - 1)
    x = grids.reshape(n, -1).astype(np.float32)
    mean_img = x.mean(axis=0)
    xc = x - mean_img
    _, _, vt = np.linalg.svd(xc, full_matrices=False)
    components = vt[:k]
    z = xc @ components.T
    a = np.concatenate([cond_norm, np.ones((n, 1), np.float32)], axis=1)
    cond_w = np.linalg.solve(a.T @ a + ridge * np.eye(a.shape[1]), a.T @ z)
    resid = z - a @ cond_w
    noise_std = resid.std(axis=0) + 1e-6
    # solid-fraction anchors per kind from the corpus itself (D18: measured, not nominal)
    kind_sf: dict[str, list[list[float]]] = {}
    rows = manifest["rows"]
    for i, r in enumerate(rows):
        kind_sf.setdefault(r["tpms"], []).append([float(r["wall_ratio"]), float(grids[i].mean())])
    for anchors in kind_sf.values():
        anchors.sort()
    return PCAConditionalModel(mean_img, components, cond_w, noise_std, kind_sf,
                               tuple(int(v) for v in grids.shape[1:]))
