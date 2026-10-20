"""Training-corpus builder for future learned proposers.

Turns verified measurement rows (simlab calibration campaigns, later rig tests)
into JSONL training rows with full provenance. Every row names its source run —
a corpus row without provenance is refused, the same rule as the calibration
registry (D18).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..domains.hx import calibrate as hxcal


def build_corpus(
    measurements_path: str | Path, *, boundary: str | None = None
) -> tuple[list[dict[str, Any]], list[str]]:
    measurements = hxcal.load(measurements_path)
    hxcal.thermal_boundary(measurements, boundary)
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    for m in measurements:
        if not m.source:
            skipped.append(getattr(m, "source", None) or "(no source)")
            continue
        if not m.evidence_sha256:
            skipped.append(f"{m.source}: no evidence_sha256; not citable")
            continue
        row_boundary, no_boundary = hxcal.thermal_boundary([m], boundary)
        if m.h_W_m2K is not None and row_boundary is None:
            skipped.append(f"{m.source}: {no_boundary}")
            continue
        try:
            p = hxcal.reduce(m, row_boundary or "uniform_wall_temperature")
        except Exception as e:
            skipped.append(f"{m.source}: {e}")
            continue
        rows.append({
            "kind": "hx_cell_measurement",
            "tpms": m.tpms,
            "wall_ratio": m.wall_ratio,
            "cell_m": m.cell_m,
            "fluid": m.fluid,
            "T_K": m.T_K,
            "p_Pa": m.p_Pa,
            "mass_flux_kg_m2s": m.mass_flux_kg_m2s,
            "reynolds": p.re,
            "dpdx_Pa_m": m.dpdx_Pa_m,
            "h_W_m2K": m.h_W_m2K,
            "thermal_boundary": row_boundary,
            "evidence_sha256": m.evidence_sha256,
            "source": m.source,
            "licence": "internal-verified",
        })
    return rows, skipped


def write_jsonl(rows: list[dict[str, Any]], out: str | Path) -> None:
    text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
    Path(out).write_text(text, encoding="utf-8")
