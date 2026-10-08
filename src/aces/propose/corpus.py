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


def build_corpus(measurements_path: str | Path, *, boundary: str = "uniform_heat_flux") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    for m in hxcal.load(measurements_path):
        if not m.source:
            skipped.append(getattr(m, "source", None) or "(no source)")
            continue
        try:
            p = hxcal.reduce(m, boundary)
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
            "reynolds": p.re,
            "dpdx_Pa_m": m.dpdx_Pa_m,
            "h_W_m2K": m.h_W_m2K,
            "thermal_boundary": boundary,
            "source": m.source,
            "licence": "internal-verified",
        })
    return rows, skipped


def write_jsonl(rows: list[dict[str, Any]], out: str | Path) -> None:
    text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
    Path(out).write_text(text, encoding="utf-8")
