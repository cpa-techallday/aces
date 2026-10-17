"""Bridge to simlab, the CUDA-X simulation runtime on the 8-GPU rig.

ACES does not run high-fidelity solvers itself.  It writes simlab experiment
files for the shortlist and reads results back.  The envelope below mirrors the
fields used by the HERMES/HALO-X campaign specs (2026-10-06).  simlab's pydantic
schema is the authority: always run ``simlab validate`` (MCP ``sim_validate``)
before ``sim_submit``.

House rules carried over from simlab:
  - precision defaults to float64 (FP32 PCG falsely converges on stiff problems);
  - every case declares ``vram_gb`` and anything over 60 GB is refused;
  - simlab's company vocabulary uses ``carbon`` for Carbon Project Australia.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .spec import COMPANY

SIMLAB_COMPANY = {COMPANY: "carbon"}
MAX_VRAM_GB = 60


def experiment(
    *,
    exp_id: str,
    project: str,
    owner: str,
    rd_activity: str,
    physics: str,
    solver: str,
    description: str,
    hypothesis: str,
    expected: str,
    geometry_ref: str | None,
    params: dict[str, Any],
    outputs: list[str],
    vram_gb: float,
    precision: str = "float64",
    sweep: dict[str, Any] | None = None,
    system: str = "",
    kind: str = "verification",
    geometry_units: str = "m",
    aces_record: str | None = None,
) -> dict[str, Any]:
    if not 0 <= vram_gb <= MAX_VRAM_GB:
        raise ValueError(f"vram_gb must be in [0, {MAX_VRAM_GB}], got {vram_gb} "
                         "(0 declares a CPU-only case: it runs on the simlab cpu queue, no GPU lease)")
    if precision not in ("float64", "float32"):
        raise ValueError(f"precision must be float64 or float32, got {precision!r}")
    exp: dict[str, Any] = {
        "id": exp_id,
        "project": project,
        "owner": owner,
        "physics": physics,
        "solver": solver,
        "description": description,
        "hypothesis": hypothesis,
        "expected": expected,
        "rd_activity": rd_activity,
        "company": SIMLAB_COMPANY[COMPANY],
        "params": params,
        "outputs": outputs,
        "resources": {"precision": precision, "vram_gb": vram_gb},
        "tags": {"kind": kind, "system": system, "origin": "aces"},
    }
    if aces_record:
        exp["tags"]["aces_record"] = aces_record  # evidence record of the design run being verified
    if geometry_ref:
        exp["geometry"] = {"source": Path(geometry_ref).suffix.lstrip(".") or "file", "ref": geometry_ref,
                           "units": geometry_units}
    if sweep:
        exp["sweep"] = sweep
    return exp


def write(exp: dict[str, Any], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(exp, sort_keys=False, width=110))
    return p
