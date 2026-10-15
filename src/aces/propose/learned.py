"""Dataset builder for the clean-room learned proposer (Tier 0, D26/D29).

Turns verified corpus rows (``aces corpus``) into (voxel grid, conditioning)
pairs for training. The geometry is not stored in the corpus — it is
re-derived deterministically from each row's (tpms, wall_ratio) on the same
level-set definitions as the measurement layer, so the training grids are
exactly the geometries the calibration factors were measured on.

Provenance rule: a row without a ``source`` is refused here too (D18) —
training data must be traceable to a verified run, or it does not train.

This is the scaffold: dataset first, model second. The corpus is small
(17 rows today) and grows with every calibrated campaign.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from ..domains.hx import tpms

KINDS: tuple[str, ...] = ("gyroid", "diamond", "primitive")
# Conditioning: what a user can ask for. Performance targets (h, dpdx) join
# once the corpus has enough spread to make them learnable.
NUMERIC_FIELDS: tuple[str, ...] = ("wall_ratio", "cell_m", "reynolds", "T_K")

_REQUIRED = ("tpms", "wall_ratio", "cell_m", "reynolds", "T_K", "source", "licence")


def load_corpus_rows(path: str | Path) -> list[dict[str, Any]]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"cannot read corpus {path}: {e}") from e
    rows: list[dict[str, Any]] = []
    for i, line in enumerate(text.splitlines()):
        if not line.strip():
            continue
        r = json.loads(line)
        missing = [k for k in _REQUIRED if k not in r]
        if missing:
            raise ValueError(f"corpus row {i} is missing {missing}")
        if not r["source"]:
            raise ValueError(f"corpus row {i} has no source (D18): refused")
        rows.append(r)
    if not rows:
        raise ValueError(f"no usable rows in {path}")
    return rows


def rasterize_cell(kind: str, wall_ratio: float, resolution: int) -> np.ndarray:
    """The sheet-TPMS unit cell as a boolean grid, on the domain's own level set."""
    if kind not in KINDS:
        raise ValueError(f"unknown TPMS kind {kind!r}; known: {', '.join(KINDS)}")
    a = (np.arange(resolution) + 0.5) / resolution
    x, y, z = np.meshgrid(a, a, a, indexing="ij")
    d = tpms.signed_distance(kind, x, y, z)
    return np.abs(d) <= 0.5 * wall_ratio


def conditioning(row: dict[str, Any]) -> np.ndarray:
    """One conditioning vector: one-hot kind, then the numeric spec fields."""
    onehot = np.zeros(len(KINDS), dtype=np.float32)
    onehot[KINDS.index(row["tpms"])] = 1.0
    numeric = np.array([float(row[k]) for k in NUMERIC_FIELDS], dtype=np.float32)
    # log-Reynolds: the corpus spans decades, and diffusion conditioners hate that
    numeric[2] = np.log10(max(numeric[2], 1e-12))
    return np.concatenate([onehot, numeric])


def build_dataset(rows: list[dict[str, Any]], resolution: int = 32
                  ) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """(grids uint8 (n,N,N,N), cond float32 (n,D), manifest with provenance + stats)."""
    if resolution < 8:
        raise ValueError("resolution below 8 cannot express a TPMS cell")
    grids = np.stack([rasterize_cell(r["tpms"], float(r["wall_ratio"]), resolution)
                      for r in rows]).astype(np.uint8)
    cond = np.stack([conditioning(r) for r in rows])
    stats = {
        k: {"mean": float(cond[:, len(KINDS) + i].mean()),
            "std": float(cond[:, len(KINDS) + i].std() or 1.0)}
        for i, k in enumerate(NUMERIC_FIELDS)
    }
    manifest = {
        "kind": "tier0_training_dataset",
        "resolution": resolution,
        "n": len(rows),
        "kinds": list(KINDS),
        "numeric_fields": list(NUMERIC_FIELDS),
        "reynolds_log10": True,
        "normalisation": stats,
        "rows": [{"source": r["source"], "licence": r["licence"], "tpms": r["tpms"],
                  "wall_ratio": r["wall_ratio"], "cell_m": r["cell_m"]} for r in rows],
    }
    return grids, cond, manifest


def write_dataset(grids: np.ndarray, cond: np.ndarray, manifest: dict[str, Any],
                  out_dir) -> None:
    """Write grids.npy, cond.npy and manifest.json through a safeio.OutDir."""
    import io

    buf = io.BytesIO()
    np.save(buf, grids)
    out_dir.write_bytes("grids.npy", buf.getvalue())
    buf = io.BytesIO()
    np.save(buf, cond)
    out_dir.write_bytes("cond.npy", buf.getvalue())
    out_dir.write_text("manifest.json", json.dumps(manifest, indent=2))
