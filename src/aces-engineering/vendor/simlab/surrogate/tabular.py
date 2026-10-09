"""Tabular surrogates: design parameters -> scalar outputs, with a predictive spread.

kind "gp": one Gaussian process per output (Matern 5/2 with ARD + white noise) on inputs scaled to
the design box and log-transformed positive outputs; posterior std is the uncertainty.
kind "et": extra-trees ensemble; the spread over trees is the uncertainty.

The cross-validated error (K-fold, on the original output scale) is what goes into the registry:
a surrogate without a recorded error is not citable.
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional, Sequence

import numpy as np


class TabularSurrogate:
    def __init__(self, inputs: Sequence[str], outputs: Sequence[str], bounds: Optional[Dict[str, Sequence[float]]] = None,
                 kind: str = "gp", log_outputs: str = "auto", seed: int = 0):
        self.inputs, self.outputs = list(inputs), list(outputs)
        self.bounds = {k: [float(v[0]), float(v[1])] for k, v in (bounds or {}).items()}
        self.kind = kind
        self.log_outputs = log_outputs
        self.seed = seed
        self.models: List[object] = []
        self._log: List[bool] = []
        self._ymean: List[float] = []
        self._ystd: List[float] = []
        self.cv: dict = {}
        self.n_train = 0

    # ---- scaling ----
    def _scale_x(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=float)
        if not self.bounds:
            return X
        lo = np.array([self.bounds[k][0] for k in self.inputs])
        hi = np.array([self.bounds[k][1] for k in self.inputs])
        return (X - lo) / np.maximum(hi - lo, 1e-12)

    def _fwd_y(self, y: np.ndarray, j: int) -> np.ndarray:
        y = np.log10(y) if self._log[j] else y
        return (y - self._ymean[j]) / self._ystd[j]

    def _inv_y(self, z: np.ndarray, j: int) -> np.ndarray:
        y = z * self._ystd[j] + self._ymean[j]
        return 10.0 ** y if self._log[j] else y

    def _make(self):
        if self.kind == "gp":
            from sklearn.gaussian_process import GaussianProcessRegressor
            from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel

            d = len(self.inputs)
            k = ConstantKernel(1.0, (1e-2, 1e2)) * Matern(length_scale=np.full(d, 0.5), length_scale_bounds=(1e-2, 1e2), nu=2.5) \
                + WhiteKernel(1e-4, (1e-8, 1e-1))
            return GaussianProcessRegressor(kernel=k, normalize_y=False, n_restarts_optimizer=3, random_state=self.seed)
        if self.kind == "et":
            from sklearn.ensemble import ExtraTreesRegressor

            return ExtraTreesRegressor(n_estimators=400, min_samples_leaf=2, random_state=self.seed, n_jobs=-1)
        raise ValueError(f"unknown surrogate kind {self.kind!r}")

    # ---- fit / predict ----
    def _fit_scalers(self, Y: np.ndarray):
        self._log, self._ymean, self._ystd = [], [], []
        for j in range(Y.shape[1]):
            y = Y[:, j]
            use_log = (self.log_outputs == "always") or (self.log_outputs == "auto" and np.all(y > 0) and (y.max() / max(y.min(), 1e-300) > 20))
            self._log.append(bool(use_log))
            yt = np.log10(y) if use_log else y
            self._ymean.append(float(yt.mean()))
            self._ystd.append(float(yt.std() + 1e-12))

    def _predict_one(self, model, Xs: np.ndarray):
        if self.kind == "gp":
            mu, sd = model.predict(Xs, return_std=True)
            return mu, sd
        per_tree = np.stack([t.predict(Xs) for t in model.estimators_], axis=0)
        return per_tree.mean(axis=0), per_tree.std(axis=0)

    def fit(self, X: np.ndarray, Y: np.ndarray, folds: int = 5) -> dict:
        from sklearn.model_selection import KFold

        X, Y = np.asarray(X, dtype=float), np.asarray(Y, dtype=float)
        self.n_train = int(len(X))
        self._fit_scalers(Y)
        Xs = self._scale_x(X)
        t0 = time.time()
        # cross-validated error on the original scale
        kf = KFold(n_splits=min(folds, len(X)), shuffle=True, random_state=self.seed)
        pred = np.zeros_like(Y)
        for tr, te in kf.split(Xs):
            for j in range(Y.shape[1]):
                m = self._make()
                m.fit(Xs[tr], self._fwd_y(Y[tr, j], j))
                mu, _ = self._predict_one(m, Xs[te])
                pred[te, j] = self._inv_y(mu, j)
        cv = {}
        for j, name in enumerate(self.outputs):
            y, p = Y[:, j], pred[:, j]
            err = p - y
            cv[name] = {"rmse": float(np.sqrt(np.mean(err ** 2))), "mae": float(np.mean(np.abs(err))),
                        "rel_rmse": float(np.sqrt(np.mean(err ** 2)) / (np.std(y) + 1e-300)),
                        "mape_pct": float(100 * np.mean(np.abs(err) / np.maximum(np.abs(y), 1e-300))),
                        "r2": float(1 - np.sum(err ** 2) / max(np.sum((y - y.mean()) ** 2), 1e-300)),
                        "y_range": [float(y.min()), float(y.max())]}
        # final fit on everything
        self.models = []
        for j in range(Y.shape[1]):
            m = self._make()
            m.fit(Xs, self._fwd_y(Y[:, j], j))
            self.models.append(m)
        self.cv = {"folds": int(kf.get_n_splits()), "n": self.n_train, "per_output": cv, "wall_s": time.time() - t0, "kind": self.kind,
                   "log_outputs": dict(zip(self.outputs, self._log))}
        return self.cv

    def predict(self, X: np.ndarray):
        Xs = self._scale_x(np.atleast_2d(np.asarray(X, dtype=float)))
        mu = np.zeros((len(Xs), len(self.outputs)))
        sd = np.zeros_like(mu)
        for j, m in enumerate(self.models):
            z, s = self._predict_one(m, Xs)
            mu[:, j] = self._inv_y(z, j)
            # spread mapped through the inverse transform (one-sided, symmetric in the transformed space)
            hi = self._inv_y(z + s, j)
            lo = self._inv_y(z - s, j)
            sd[:, j] = 0.5 * np.abs(hi - lo)
        return mu, sd

    def predict_dict(self, params: dict) -> Dict[str, float]:
        x = [[float(params[k]) for k in self.inputs]]
        mu, sd = self.predict(x)
        out = {k: float(mu[0, j]) for j, k in enumerate(self.outputs)}
        out.update({k + "_std": float(sd[0, j]) for j, k in enumerate(self.outputs)})
        return out

    # ---- persistence ----
    def save(self, dir_: str) -> str:
        import joblib

        os.makedirs(dir_, exist_ok=True)
        joblib.dump({"inputs": self.inputs, "outputs": self.outputs, "bounds": self.bounds, "kind": self.kind,
                     "log": self._log, "ymean": self._ymean, "ystd": self._ystd, "models": self.models,
                     "cv": self.cv, "n_train": self.n_train, "seed": self.seed}, os.path.join(dir_, "model.joblib"))
        with open(os.path.join(dir_, "model.json"), "w", encoding="utf-8") as f:
            json.dump({"inputs": self.inputs, "outputs": self.outputs, "bounds": self.bounds, "kind": self.kind,
                       "cv": self.cv, "n_train": self.n_train}, f, indent=2)
        return dir_

    @classmethod
    def load(cls, dir_: str) -> "TabularSurrogate":
        import joblib

        d = joblib.load(os.path.join(dir_, "model.joblib"))
        s = cls(d["inputs"], d["outputs"], d["bounds"], d["kind"], seed=d.get("seed", 0))
        s._log, s._ymean, s._ystd, s.models, s.cv, s.n_train = d["log"], d["ymean"], d["ystd"], d["models"], d["cv"], d["n_train"]
        return s
