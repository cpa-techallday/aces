"""Measure an arbitrary voxelised solid on ACES's own definitions.

Tier 0 proposers that emit voxel grids (XCube in the research lane today, the
clean-room learned proposer later) must be measured by the same primitives as
the analytic TPMS path — a generated shape is a suggestion until it is measured
(D5, D10). This module is that measurement layer for voxel solids.

Definitions (mirrors aces.domains.hx.tpms, adapted from periodic cell to
bounded part):

- solid_fraction: share of voxels that are solid.
- wall_min_m / wall_mean_m: local thickness (Hildebrand & Ruegsegger): at each
  solid voxel, the diameter of the largest sphere that contains the voxel and
  lies fully inside the solid. Computed exactly up to quantisation of the EDT
  levels — never from a nominal parameter.
- neck_m: per axis, the diameter of the largest sphere that can pass through
  the void phase from one face of the bounding box to the opposite face
  (bisection on percolation of the eroded void, as in tpms._neck_and_isolated
  but without the periodic tile). A blocked axis reports 0.
- isolated_void_fraction: share of the void volume fully enclosed by solid —
  enclosed pockets trap powder in AM and must be designed out.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import ndimage


@dataclass(frozen=True)
class VoxelFacts:
    proposer: str
    seed: int
    pitch_m: float
    shape: tuple[int, int, int]
    solid_fraction: float
    wall_min_m: float
    wall_mean_m: float
    neck_m: tuple[float, float, float]
    isolated_void_fraction: float
    notes: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "proposer": self.proposer,
            "seed": self.seed,
            "pitch_m": self.pitch_m,
            "shape": list(self.shape),
            "solid_fraction": self.solid_fraction,
            "wall_min_m": self.wall_min_m,
            "wall_mean_m": self.wall_mean_m,
            "neck_m": list(self.neck_m),
            "isolated_void_fraction": self.isolated_void_fraction,
            "notes": list(self.notes),
        }


def _ball(radius_vox: float) -> np.ndarray:
    """Boolean structuring element for a sphere of the given radius in voxels."""
    r = max(1, int(np.ceil(radius_vox)))
    axes = [np.arange(-r, r + 1)] * 3
    g = np.stack(np.meshgrid(*axes, indexing="ij"))
    return (g ** 2).sum(axis=0) <= radius_vox ** 2


def _local_thickness(solid: np.ndarray, pitch_m: float, max_levels: int = 48) -> np.ndarray:
    """Local thickness in metres at every solid voxel (EDT-level quantised)."""
    edt = ndimage.distance_transform_edt(solid, sampling=pitch_m)
    T = np.zeros_like(edt)
    levels = np.unique(edt[solid])
    if levels.size == 0:
        return T
    if levels.size > max_levels:
        levels = np.quantile(levels, np.linspace(0.0, 1.0, max_levels))
        levels = np.unique(levels)
    remaining = solid.copy()
    for r in levels[::-1]:  # largest spheres first
        if not remaining.any():
            break
        centres = edt >= r
        covered = ndimage.binary_dilation(centres, structure=_ball(r / pitch_m))
        newly = covered & remaining
        T[newly] = 2.0 * r
        remaining &= ~covered
    # Any voxel still unset (shouldn't happen) gets its own sphere diameter.
    T[remaining] = 2.0 * edt[remaining]
    return T


def _neck_along(void: np.ndarray, pitch_m: float, axis: int) -> float:
    """Largest sphere diameter (m) percolating the void along one axis."""
    edt = ndimage.distance_transform_edt(void, sampling=pitch_m)
    face_lo = [slice(None)] * 3
    face_hi = [slice(None)] * 3
    face_lo[axis] = 0
    face_hi[axis] = -1

    def percolates(r: float) -> bool:
        lab, _ = ndimage.label(edt >= r)
        lo = set(np.unique(lab[tuple(face_lo)])) - {0}
        hi = set(np.unique(lab[tuple(face_hi)])) - {0}
        return bool(lo & hi)

    if not percolates(1e-12):
        return 0.0
    lo, hi = 0.0, float(edt.max())
    for _ in range(16):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if percolates(mid) else (lo, mid)
    # Distances are between voxel centres; subtract one voxel to stay conservative.
    return max(0.0, 2.0 * lo - pitch_m)


def _isolated_void_fraction(void: np.ndarray) -> float:
    """Share of void voxels in pockets fully enclosed by solid."""
    if not void.any():
        return 0.0
    lab, _ = ndimage.label(void)
    border = set(np.unique(np.concatenate([
        lab[0].ravel(), lab[-1].ravel(),
        lab[:, 0].ravel(), lab[:, -1].ravel(),
        lab[:, :, 0].ravel(), lab[:, :, -1].ravel(),
    ]))) - {0}
    enclosed = ~np.isin(lab, list(border)) & (lab > 0)
    return float(np.count_nonzero(enclosed) / np.count_nonzero(void))


def measure_voxel_solid(solid: np.ndarray, pitch_m: float, proposer: str, seed: int,
                        notes: tuple[str, ...] = ()) -> VoxelFacts:
    """Measure a boolean voxel grid. pitch_m is the voxel edge length in metres."""
    if solid.ndim != 3:
        raise ValueError(f"expected a 3D grid, got shape {solid.shape}")
    if pitch_m <= 0:
        raise ValueError("pitch_m must be positive")
    solid = np.asarray(solid, dtype=bool)
    if not solid.any():
        raise ValueError("empty solid grid — nothing to measure")
    if not (~solid).any():
        raise ValueError("fully dense grid — no void phase to measure")

    T = _local_thickness(solid, pitch_m)
    wall = T[solid]
    void = ~solid
    necks = tuple(_neck_along(void, pitch_m, ax) for ax in range(3))
    return VoxelFacts(
        proposer=proposer,
        seed=seed,
        pitch_m=pitch_m,
        shape=tuple(int(s) for s in solid.shape),
        solid_fraction=float(solid.mean()),
        wall_min_m=float(wall.min()),
        wall_mean_m=float(wall.mean()),
        neck_m=(float(necks[0]), float(necks[1]), float(necks[2])),
        isolated_void_fraction=_isolated_void_fraction(void),
        notes=tuple(notes),
    )
