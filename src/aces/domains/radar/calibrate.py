"""Calibration of the radar fast tier from EM-sim and bench measurements (D18, as ``hx.calibrate``).

Measurement rows come from two sources, both recorded with provenance:

- ``simlab:<run-id>`` — the ``aces-tile-em`` solver (Palace FEM or gprMax FDTD on the
  rig): S-parameters of the T/R tile, RF distribution board (board 9) or antenna
  plate, from the exported STEP/stackup, at the operating band.
- ``test:<id>`` — bench VNA / noise-figure measurements of built hardware
  (THRU coupon sweep, tile loopback), once hardware exists.

A row is a dict (JSON list, or CSV with these columns)::

    {"kind": "tile_insertion_loss_db" | "rx_nf_db" | "array_gain_db",
     "frequency_hz": float, "value": float, "source": str,
     "model_value": float,   # what the fast tier predicted for the same case, in the same dB unit
     "held_out": bool}       # optional: kept out of the fit and used to check it

Every value is in dB.  A registry factor multiplies the LINEAR quantity the model uses (loss,
noise factor, gain), so a row's ratio is 10 ** ((value - model_value) / 10), never value / model_value.

Rules (D18, D20, and the held-out check of WORKFLOW steps 20-26):
  * provenance: every row names a ``simlab:`` or ``test:`` source;
  * one factor per kind: the geometric mean of the per-condition ratios, where a condition is a
    frequency (to 6 significant figures) and repeated rows of a condition are averaged first, so
    repeats cannot make a fit look better supported than it is;
  * fewer than ``min_points`` distinct conditions, or a sample sd of ln(ratio) above
    ``max_scatter``, is refused;
  * held-out rows at conditions the fit never saw check the factor: each must be predicted within
    ``holdout_tol`` (linear fraction; ROADMAP gate 1 uses 10 %), and fewer than ``min_held_out`` of
    them refuses the factor.
"""
from __future__ import annotations

import csv
import datetime as _dt
import json
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ... import safeio
from ...calibration import Factor, Registry
from .model import KEY_GAIN, KEY_NF, KEY_TX_LOSS

ROW_KIND_TO_KEY = {
    "tile_insertion_loss_db": KEY_TX_LOSS,
    "rx_nf_db": KEY_NF,
    "array_gain_db": KEY_GAIN,
}

REQUIRED_FIELDS = {"kind", "frequency_hz", "value", "source", "model_value"}
SOURCE_PREFIXES = ("simlab:", "test:")
MAX_FILE_BYTES = 20_000_000


def _truthy(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y")
    return bool(v)


def check_rows(rows: list[dict]) -> list[dict]:
    """Validate rows (fields, kinds, finite numbers, provenance); return them with numbers as floats."""
    out, problems = [], []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            problems.append(f"row {i + 1}: not an object")
            continue
        missing = REQUIRED_FIELDS - set(r)
        if missing:
            problems.append(f"row {i + 1}: missing fields {sorted(missing)}")
            continue
        if r["kind"] not in ROW_KIND_TO_KEY:
            problems.append(f"row {i + 1}: unknown kind {r['kind']!r}; choose from {sorted(ROW_KIND_TO_KEY)}")
            continue
        try:
            nums = {k: float(r[k]) for k in ("frequency_hz", "value", "model_value")}
        except (TypeError, ValueError):
            problems.append(f"row {i + 1}: frequency_hz, value and model_value must be numbers")
            continue
        if not all(math.isfinite(v) for v in nums.values()) or nums["frequency_hz"] <= 0:
            problems.append(f"row {i + 1}: numbers must be finite and frequency_hz > 0")
            continue
        src = str(r["source"] or "").strip()
        prefix = next((p for p in SOURCE_PREFIXES if src.startswith(p)), None)
        if prefix is None or len(src) == len(prefix):
            problems.append(f"row {i + 1}: source {src!r} must name a simlab run (simlab:<id>) or a test (test:<id>)")
            continue
        out.append({**r, **nums, "source": src, "held_out": _truthy(r.get("held_out", False))})
    if problems:
        raise ValueError("measurement rows are invalid:\n  " + "\n  ".join(problems))
    if not out:
        raise ValueError("no measurement rows")
    return out


def read_rows(path: str | Path) -> list[dict]:
    """Read measurement rows from JSON (list of dicts) or CSV with the row fields."""
    p = Path(path)
    text = safeio.read_text_limited(p, MAX_FILE_BYTES)
    if p.suffix.lower() == ".json":
        rows = json.loads(text)
        if not isinstance(rows, list):
            raise ValueError(f"{p}: expected a JSON list of measurement rows")
    else:
        rows = list(csv.DictReader(text.splitlines()))
    return check_rows(rows)


def collect_from_experiment(exp_dir: str | Path) -> tuple[list[dict], list[str]]:
    """Rows from a finished simlab experiment: (rows, skipped cases with the reason).

    As ``aces cfd-collect``: only FINISHED cases with an ``evidence.json`` count, and the source is the run
    folder (``simlab:<experiment>:<case>``) whatever the row says.
    """
    rows, skipped = [], []
    exp = Path(exp_dir)
    for case_dir in sorted(p for p in exp.iterdir() if p.is_dir() and not safeio.is_link(p)):
        status_p, meas_p = case_dir / "status.json", case_dir / "measurement.json"
        status = (json.loads(safeio.read_text_limited(status_p, 1_000_000)).get("status")
                  if status_p.is_file() else None)
        if status != "FINISHED":
            skipped.append(f"{case_dir.name}: status {status}")
            continue
        if not meas_p.is_file():
            skipped.append(f"{case_dir.name}: no measurement.json")
            continue
        if not (case_dir / "evidence.json").is_file():
            skipped.append(f"{case_dir.name}: no evidence.json; not citable")
            continue
        row = json.loads(safeio.read_text_limited(meas_p, 1_000_000))
        row["source"] = f"simlab:{exp.name}:{case_dir.name}"
        try:
            rows += check_rows([row])
        except ValueError as e:
            skipped.append(f"{case_dir.name}: {str(e).splitlines()[-1].strip()}")
    return rows, skipped


def _ratio(r: dict) -> float:
    """measured / model on the linear quantity the factor multiplies (both values are dB)."""
    return 10.0 ** ((r["value"] - r["model_value"]) / 10.0)


def _condition(r: dict) -> float:
    return float(f"{r['frequency_hz']:.6g}")


def fit(
    rows: Iterable[dict],
    *,
    min_points: int = 3,
    max_scatter: float = 0.15,
    min_held_out: int = 1,
    holdout_tol: float = 0.10,
    fitted_on: str | None = None,
) -> tuple[list[Factor], list[dict[str, Any]], list[str]]:
    """Return (factors, per-key summary rows, refusals).  Rows must have passed ``check_rows``."""
    if min_points < 2:
        raise ValueError("min_points must be at least 2: one condition cannot show whether a constant factor holds")
    if min_held_out < 0 or not holdout_tol > 0:
        raise ValueError("min_held_out >= 0 and holdout_tol > 0 required")
    today = fitted_on or _dt.datetime.now(_dt.UTC).date().isoformat()
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        buckets.setdefault(ROW_KIND_TO_KEY[r["kind"]], []).append(r)

    factors: list[Factor] = []
    summary: list[dict[str, Any]] = []
    refused: list[str] = []
    for key, rs in sorted(buckets.items()):
        use = [r for r in rs if not r["held_out"]]
        groups: dict[float, list[dict]] = {}
        for r in use:
            groups.setdefault(_condition(r), []).append(r)
        conditions = len(groups)
        row: dict[str, Any] = {"key": key, "points": len(use), "conditions": conditions}
        if conditions < min_points:
            refused.append(f"{key}: {conditions} distinct condition(s) ({len(use)} fit row(s)), need at least "
                           f"{min_points}")
            summary.append({**row, "status": "refused: too few conditions"})
            continue
        logs = [sum(math.log(_ratio(r)) for r in g) / len(g) for g in groups.values()]
        mean = sum(logs) / len(logs)
        sd = math.sqrt(sum((x - mean) ** 2 for x in logs) / (len(logs) - 1))
        value = math.exp(mean)
        freqs = [r["frequency_hz"] for r in use]
        row.update({"value": value, "scatter_sd_log": sd, "frequency_hz": (min(freqs), max(freqs))})
        if sd > max_scatter:
            refused.append(f"{key}: ratios scatter {sd:.3f} (sd of ln) > {max_scatter:g}; a constant cannot "
                           "represent them. Narrow the band or add conditions.")
            summary.append({**row, "status": "refused: scatter"})
            continue

        checks, not_independent = [], []
        for r in rs:
            if not r["held_out"]:
                continue
            if _condition(r) in groups:
                not_independent.append(r["source"])
                continue
            predicted_db = r["model_value"] + 10.0 * math.log10(value)
            err = 10.0 ** ((r["value"] - predicted_db) / 10.0) - 1.0
            checks.append({"source": r["source"], "frequency_hz": r["frequency_hz"], "measured_db": r["value"],
                           "predicted_db": predicted_db, "error": err, "ok": abs(err) <= holdout_tol})
        row.update({"held_out": checks, "held_out_not_independent": not_independent})
        bad = [c for c in checks if not c["ok"]]
        if bad:
            refused.append(f"{key}: {len(bad)} of {len(checks)} held-out row(s) miss by more than "
                           f"{holdout_tol:.0%} (worst {max(abs(c['error']) for c in bad):.1%})")
            summary.append({**row, "status": "refused: held-out check"})
            continue
        if len(checks) < min_held_out:
            extra = f" ({len(not_independent)} at fitted conditions do not count)" if not_independent else ""
            refused.append(f"{key}: {len(checks)} held-out row(s) at unseen conditions{extra}, need at least "
                           f"{min_held_out}")
            summary.append({**row, "status": "refused: too few held-out rows"})
            continue

        sources = tuple(sorted({r["source"] for r in use}))
        basis = (f"multiplies the linear quantity; geometric mean of 10^((measured - model)/10 dB) over "
                 f"{len(use)} row(s) at {conditions} condition(s); {len(checks)} held-out row(s) within "
                 f"{holdout_tol:.0%}")
        fac = Factor(key=key, value=value, sources=sources, valid_range={"frequency_hz": (min(freqs), max(freqs))},
                     fitted_on=today, basis=basis)
        factors.append(fac)
        summary.append({**row, "status": "fitted"})
    return factors, summary, refused


def update_registry(path: str | Path, factors: list[Factor]) -> Registry:
    """Add ``factors`` to the registry at ``path`` (created if missing), written through ``aces.safeio``."""
    p = Path(path)
    if safeio.is_link(p):
        raise safeio.UnsafePathError(f"{p} is a link; refusing to write through it")
    reg = Registry.load(p) if p.exists() else Registry(path=p)
    for f in factors:
        reg.set(f)
    data = {k: {kk: vv for kk, vv in v.items() if kk != "key"} for k, v in reg.as_dict().items()}
    safeio.write_text(p, json.dumps(data, indent=2, sort_keys=True), overwrite=True)
    reg.path = p
    return reg
