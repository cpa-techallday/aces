"""Reference Tier-0 proposer: parametric TPMS perturbation for the hx domain.

Not a learned model — it samples the calibrated design space (and just past its
edges) so the full Tier-0 contract runs end to end before any diffusion model
plugs in. Every sample is exactly measurable by aces.domains.hx.tpms, which is
the rule for every proposer: only propose what the measurement layer can measure.

A learned proposer (latent diffusion over voxel cells) registers against the
same contract and is trained from `aces corpus` output; see docs/TIER0.md.
"""
from __future__ import annotations

import random
from typing import Any, Iterable

from ..domains.hx import tpms
from .contract import Proposal

KINDS = ("gyroid", "diamond", "primitive")
# The calibration campaign covered 0.08-0.16; samples outside it are allowed but flagged.
CALIBRATED_WALL_RATIO = (0.08, 0.16)


class TpmsParametricProposer:
    name = "tpms-parametric"
    licence = "internal"
    domain = "hx"

    def propose(self, req: dict[str, Any], n: int, seed: int) -> Iterable[Proposal]:
        if n < 1:
            raise ValueError("n must be >= 1")
        rng = random.Random(seed)
        kinds = tuple(req.get("kinds") or KINDS)
        for k in kinds:
            if k not in KINDS:
                raise ValueError(f"unknown TPMS kind {k!r}; known: {', '.join(KINDS)}")
        cell_m = float(req.get("cell_m", 0.004))
        lo, hi = req.get("wall_ratio_range", CALIBRATED_WALL_RATIO)
        if not 0.0 < float(lo) < float(hi) < 0.6:
            raise ValueError("wall_ratio_range must sit inside (0, 0.6)")
        for i in range(n):
            kind = kinds[rng.randrange(len(kinds))]
            # jitter cell size +-15 % — still printable-scale, still measurable
            cell = cell_m * rng.uniform(0.85, 1.15)
            ratio = round(rng.uniform(float(lo), float(hi)), 4)
            notes = []
            if not CALIBRATED_WALL_RATIO[0] <= ratio <= CALIBRATED_WALL_RATIO[1]:
                notes.append("wall_ratio outside the calibrated range 0.08-0.16")
            if kind == "primitive":
                notes.append("primitive has no calibrated factors yet (campaign cases exceeded the Mach limit)")
            yield Proposal(
                proposer=self.name,
                domain=self.domain,
                seed=seed * 1_000_003 + i,
                params={"tpms": kind, "wall_ratio": ratio, "cell_m": round(cell, 6)},
                notes=tuple(notes),
            )


def measure(p: Proposal) -> dict[str, Any]:
    """Measured geometry facts for a TPMS proposal, on the domain's own definitions."""
    if p.domain != "hx":
        raise ValueError(f"measure() here covers hx proposals, not {p.domain!r}")
    kind, ratio = p.params["tpms"], float(p.params["wall_ratio"])
    cell = tpms.unit_cell(kind, ratio)
    man = tpms.measure_manufacturability(kind, ratio)
    return {
        "proposer": p.proposer,
        "seed": p.seed,
        "params": p.params,
        "solid_fraction": cell.solid_fraction,
        "hydraulic_diameter_over_a": cell.hydraulic_diameter_over_a(0),
        "wall_min_over_a": man.wall_min_over_a,
        "neck_over_a": list(man.neck_over_a),
        "isolated_fraction": list(man.isolated_fraction),
        "notes": list(p.notes),
    }
