"""Calibration of the radar fast tier from EM-sim and bench measurements.

Measurement rows come from two sources, both recorded with provenance:

- ``simlab:<run-id>`` — the ``aces-tile-em`` solver (Palace FEM or gprMax FDTD on the
  rig): S-parameters of the T/R tile, RF distribution board (board 9) or antenna
  plate, from the exported STEP/stackup, at the operating band.
- ``test:<id>`` — bench VNA / noise-figure measurements of built hardware
  (THRU coupon sweep, tile loopback), once hardware exists.

A row is a dict::

    {"kind": "tile_insertion_loss_db" | "rx_nf_db" | "array_gain_db",
     "frequency_hz": float, "value": float, "source": str,
     "model_value": float  # what the fast tier predicted for the same case}

``fit`` turns the rows into multiplicative registry factors (measured/model in
linear units, geometric mean per key), so an EM-sim result directly corrects the
cascade the design model ranks with — the same loop ``aces calibrate`` runs for
the HX domain.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Iterable

from ...calibration import Factor, Registry
from .model import KEY_GAIN, KEY_NF, KEY_TX_LOSS

ROW_KIND_TO_KEY = {
    "tile_insertion_loss_db": KEY_TX_LOSS,
    "rx_nf_db": KEY_NF,
    "array_gain_db": KEY_GAIN,
}

REQUIRED_FIELDS = {"kind", "frequency_hz", "value", "source", "model_value"}


def read_rows(path: str | Path) -> list[dict]:
    """Read measurement rows from JSON (list of dicts) or CSV with the row fields."""
    p = Path(path)
    if p.suffix.lower() == ".json":
        rows = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError(f"{p}: expected a JSON list of measurement rows")
    else:
        with p.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        for r in rows:
            for k in ("frequency_hz", "value", "model_value"):
                r[k] = float(r[k])
    for i, r in enumerate(rows):
        missing = REQUIRED_FIELDS - set(r)
        if missing:
            raise ValueError(f"row {i} missing fields: {sorted(missing)}")
        if r["kind"] not in ROW_KIND_TO_KEY:
            raise ValueError(f"row {i}: unknown kind {r['kind']!r}; choose from {sorted(ROW_KIND_TO_KEY)}")
    return rows


def collect_from_experiment(exp_dir: str | Path) -> list[dict]:
    """Gather measurement rows from a finished simlab experiment (``measurement.json`` per case)."""
    rows = []
    exp = Path(exp_dir)
    for case_dir in sorted(exp.iterdir()):
        m = case_dir / "measurement.json"
        if m.is_file():
            row = json.loads(m.read_text(encoding="utf-8"))
            row.setdefault("source", f"simlab:{case_dir.name}")
            rows.append(row)
    return rows


def fit(rows: Iterable[dict], registry: Registry, fitted_on: str) -> list[Factor]:
    """Fit one factor per row kind into ``registry`` (geometric mean of measured/model).

    The factor is multiplicative on the linear quantity the model uses (loss, noise
    factor, gain), consistent with the ``hx`` domain's factor convention.  Rows
    whose model prediction is zero or negative are rejected — a factor against no
    baseline is meaningless.
    """
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        buckets.setdefault(ROW_KIND_TO_KEY[r["kind"]], []).append(r)

    factors: list[Factor] = []
    for key, rs in sorted(buckets.items()):
        log_ratios = []
        freqs = []
        sources = set()
        for r in rs:
            mv = float(r["model_value"])
            if mv <= 0:
                raise ValueError(f"{key}: model_value must be > 0 to form a ratio; got {mv}")
            log_ratios.append(math.log10(float(r["value"]) / mv))
            freqs.append(float(r["frequency_hz"]))
            sources.add(str(r["source"]))
        factor_lin = 10.0 ** (sum(log_ratios) / len(log_ratios))
        fac = Factor(
            key=key,
            value=factor_lin,
            sources=tuple(sorted(sources)),
            valid_range={"frequency_hz": (min(freqs), max(freqs))},
            fitted_on=fitted_on,
            basis=f"geometric mean of measured/model over {len(rs)} rows ({key})",
        )
        registry.set(fac)
        factors.append(fac)
    return factors
