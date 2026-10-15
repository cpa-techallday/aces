"""Triply periodic minimal surface (TPMS) sheet cores, computed numerically.

A sheet TPMS wall of thickness t splits a periodic cell of size a into two
interpenetrating fluid networks, A and B: the basis of two-fluid printed heat
exchangers.  Geometry is the solution here, not a guess: porosity, wetted area,
hydraulic diameter, minimum wall, channel neck and isolated pockets are measured on
the level set, never taken from a published table.

Level sets (X = 2*pi*x/a, etc.):
  gyroid     sin X cos Y + sin Y cos Z + sin Z cos X
  diamond    sin X sin Y sin Z + sin X cos Y cos Z + cos X sin Y cos Z + cos X cos Y sin Z
  primitive  cos X + cos Y + cos Z

The signed distance to the mid-surface is approximated to first order by
f / |grad f|, so the nominal wall ratio t/a is exact only near flat parts of the
surface.  That is why manufacturing checks use the *measured* minimum wall and
channel neck (``manufacturability``), never the nominal values.
"""
from __future__ import annotations

import json
import math
import struct
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy import ndimage
from skimage.measure import marching_cubes, mesh_surface_area

KINDS = ("gyroid", "diamond", "primitive")
TWO_PI = 2.0 * math.pi
TABLE_PATH = Path(__file__).with_name("data") / "tpms_manufacturability.json"


def _field_grad(kind: str, X: np.ndarray, Y: np.ndarray, Z: np.ndarray):
    sx, cx, sy, cy, sz, cz = np.sin(X), np.cos(X), np.sin(Y), np.cos(Y), np.sin(Z), np.cos(Z)
    if kind == "gyroid":
        f = sx * cy + sy * cz + sz * cx
        gx = cx * cy - sz * sx
        gy = -sx * sy + cy * cz
        gz = -sy * sz + cz * cx
    elif kind == "diamond":
        f = sx * sy * sz + sx * cy * cz + cx * sy * cz + cx * cy * sz
        gx = cx * sy * sz + cx * cy * cz - sx * sy * cz - sx * cy * sz
        gy = sx * cy * sz - sx * sy * cz + cx * cy * cz - cx * sy * sz
        gz = sx * sy * cz - sx * cy * sz - cx * sy * sz + cx * cy * cz
    elif kind == "primitive":
        f = cx + cy + cz
        gx, gy, gz = -sx, -sy, -sz
    else:
        raise ValueError(f"unknown TPMS kind {kind!r}; choose from {KINDS}")
    return f, gx, gy, gz


def signed_distance(kind: str, x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    """Approximate signed distance to the mid-surface, in units of the cell size a."""
    f, gx, gy, gz = _field_grad(kind, TWO_PI * x, TWO_PI * y, TWO_PI * z)
    g = np.sqrt(gx * gx + gy * gy + gz * gz)
    return f / (TWO_PI * np.maximum(g, 1e-12))


def _unit_normal(kind: str, p: np.ndarray) -> np.ndarray:
    _, gx, gy, gz = _field_grad(kind, TWO_PI * p[:, 0], TWO_PI * p[:, 1], TWO_PI * p[:, 2])
    g = np.stack([gx, gy, gz], axis=1)
    return g / np.maximum(np.linalg.norm(g, axis=1, keepdims=True), 1e-12)


def _grid(n_cells: int, N: int):
    s = np.linspace(0.0, float(n_cells), n_cells * N + 1)
    return np.meshgrid(s, s, s, indexing="ij")


def _centres(N: int):
    c = (np.arange(N) + 0.5) / N
    return np.meshgrid(c, c, c, indexing="ij")


# ----------------------------------------------------------------------------- unit cell

@dataclass(frozen=True)
class UnitCell:
    kind: str
    ratio: float  # nominal t / a
    resolution: int
    solid_fraction: float
    fluid_fraction: tuple[float, float]  # (A, B)
    area_density_a: tuple[float, float]  # wetted area per unit volume, times a (dimensionless)

    def hydraulic_diameter_over_a(self, side: int) -> float:
        ad = self.area_density_a[side]
        return 4.0 * self.fluid_fraction[side] / ad if ad > 0 else 0.0


def _area(field: np.ndarray, level: float, h: float) -> float:
    if not (field.min() < level < field.max()):
        return 0.0
    verts, faces, _, _ = marching_cubes(field, level=level, spacing=(h, h, h))
    return float(mesh_surface_area(verts, faces))


def _ramp(x: np.ndarray) -> np.ndarray:
    return np.clip(x + 0.5, 0.0, 1.0)


@lru_cache(maxsize=4096)
def _unit_cell(kind: str, ratio: float, N: int) -> UnitCell:
    if not 0.0 < ratio < 1.0:
        raise ValueError(f"wall/cell ratio must be in (0, 1), got {ratio}")
    if 0.5 * ratio * N < 1.0:
        raise ValueError(f"resolution {N} too coarse for wall ratio {ratio}: need at least 2 voxels per wall")
    h = 1.0 / N
    half = 0.5 * ratio
    dc = signed_distance(kind, *_centres(N))
    # Volume fractions with a one-voxel linear ramp across each interface (a first-order
    # volume-of-fluid estimate): far less staircase noise than counting voxel centres.
    fa = float(np.mean(_ramp((dc - half) / h)))
    fb = float(np.mean(_ramp((-dc - half) / h)))
    solid = 1.0 - fa - fb
    d = signed_distance(kind, *_grid(1, N))
    return UnitCell(kind, ratio, N, solid, (fa, fb), (_area(d, half, h), _area(d, -half, h)))


def unit_cell(kind: str, ratio: float, resolution: int = 64) -> UnitCell:
    """Measured unit-cell geometry.  Resolution is raised so every wall spans >= 3 voxels."""
    ratio = round(float(ratio), 4)
    return _unit_cell(kind, ratio, max(int(resolution), math.ceil(3.0 / ratio)))


@lru_cache(maxsize=32)
def mid_surface_area(kind: str, resolution: int = 96) -> float:
    """Area of the zero level set inside one unit cell (a = 1).

    solid_fraction / mid_surface_area is the mean wall thickness actually present,
    which is what wall conduction uses.
    """
    return _area(signed_distance(kind, *_grid(1, resolution)), 0.0, 1.0 / resolution)


# ----------------------------------------------------------------------------- manufacturability

@dataclass(frozen=True)
class Manufacturability:
    kind: str
    ratio: float
    wall_min_over_a: float  # thinnest wall, measured along the mid-surface normal
    wall_mean_over_a: float  # area-weighted mean of the same measurement
    neck_over_a: tuple[float, float]  # largest sphere that can pass through network A / B
    isolated_fraction: tuple[float, float]  # share of each fluid volume not connected to its network
    resolution: int


def _wall_thickness(kind: str, ratio: float, N: int = 96) -> tuple[float, float]:
    """Local wall thickness at every mid-surface vertex, by bisection along the normal."""
    verts, faces, _, _ = marching_cubes(signed_distance(kind, *_grid(1, N)), level=0.0, spacing=(1.0 / N,) * 3)
    n = _unit_normal(kind, verts)
    half = 0.5 * ratio

    def sd(s: np.ndarray, sign: float) -> np.ndarray:
        p = verts + sign * s[:, None] * n
        return sign * signed_distance(kind, p[:, 0], p[:, 1], p[:, 2])

    def reach(sign: float) -> np.ndarray:
        # March out in small steps to bracket the FIRST crossing of the offset surface
        # (further out the normal can enter a neighbouring sheet), then bisect.
        step = ratio / 20.0
        lo = np.zeros(len(verts))
        hi = np.full(len(verts), np.nan)
        for k in range(1, 61):
            s = np.full(len(verts), k * step)
            crossed = np.isnan(hi) & (sd(s, sign) >= half)
            hi = np.where(crossed, s, hi)
            lo = np.where(np.isnan(hi), s, lo)
            if not np.isnan(hi).any():
                break
        found = ~np.isnan(hi)
        hi = np.where(found, hi, lo)
        for _ in range(30):
            mid = 0.5 * (lo + hi)
            inside = sd(mid, sign) < half
            lo = np.where(inside, mid, lo)
            hi = np.where(inside, hi, mid)
        return np.where(found, hi, np.inf)

    t = reach(+1.0) + reach(-1.0)
    tri = verts[faces]
    w = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    vw = np.zeros(len(verts))
    np.add.at(vw, faces.ravel(), np.repeat(w / 3.0, 3))
    finite = np.isfinite(t)
    return float(t[finite].min()), float(np.sum(t[finite] * vw[finite]) / np.sum(vw[finite]))


def _neck_and_isolated(mask_cell: np.ndarray, N: int) -> tuple[float, float]:
    """Neck diameter (cell units) for flow along x, and isolated volume fraction."""
    tile = np.tile(mask_cell, (3, 3, 3))
    pad = N // 4
    edt = ndimage.distance_transform_edt(np.pad(tile, pad, mode="wrap"), sampling=1.0 / N)
    edt = edt[pad:-pad, pad:-pad, pad:-pad]

    def percolates(r: float) -> bool:
        lab, _ = ndimage.label(edt >= r)
        left = set(np.unique(lab[0])) - {0}
        right = set(np.unique(lab[-1])) - {0}
        return bool(left & right)

    lo, hi = 0.0, float(edt.max())
    if not percolates(1e-9):
        return 0.0, 1.0
    for _ in range(14):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if percolates(mid) else (lo, mid)
    # Distances are measured between voxel centres; subtract one voxel to stay conservative.
    neck = max(0.0, 2.0 * lo - 1.0 / N)

    lab, _ = ndimage.label(tile)
    border = set(np.unique(np.concatenate([lab[0].ravel(), lab[-1].ravel(), lab[:, 0].ravel(),
                                           lab[:, -1].ravel(), lab[:, :, 0].ravel(), lab[:, :, -1].ravel()])))
    centre = (slice(N, 2 * N),) * 3
    c_lab = lab[centre]
    fluid = np.count_nonzero(c_lab)
    isolated = np.count_nonzero(~np.isin(c_lab, list(border)) & (c_lab > 0))
    return neck, (isolated / fluid if fluid else 1.0)


def measure_manufacturability(kind: str, ratio: float, N: int = 48) -> Manufacturability:
    wall_min, wall_mean = _wall_thickness(kind, ratio)
    d = signed_distance(kind, *_centres(N))
    half = 0.5 * ratio
    na, ia = _neck_and_isolated(d > half, N)
    nb, ib = _neck_and_isolated(d < -half, N)
    return Manufacturability(kind, ratio, wall_min, wall_mean, (na, nb), (ia, ib), N)


@lru_cache(maxsize=1)
def _table() -> dict:
    if not TABLE_PATH.exists():
        return {}
    return json.loads(TABLE_PATH.read_text())


def manufacturability(kind: str, ratio: float) -> Manufacturability:
    """Interpolated from the precomputed table when available, otherwise measured live."""
    rows = _table().get("kinds", {}).get(kind)
    if rows:
        rs = [r["ratio"] for r in rows]
        if rs[0] <= ratio <= rs[-1]:
            i = max(0, min(len(rs) - 2, int(np.searchsorted(rs, ratio) - 1)))
            r0, r1 = rows[i], rows[i + 1]
            w = (ratio - r0["ratio"]) / (r1["ratio"] - r0["ratio"])

            def lerp(key, j=None):
                a, b = (r0[key], r1[key]) if j is None else (r0[key][j], r1[key][j])
                return (1 - w) * a + w * b

            return Manufacturability(kind, ratio, lerp("wall_min_over_a"), lerp("wall_mean_over_a"),
                                     (lerp("neck_over_a", 0), lerp("neck_over_a", 1)),
                                     (max(r0["isolated_fraction"][0], r1["isolated_fraction"][0]),
                                      max(r0["isolated_fraction"][1], r1["isolated_fraction"][1])),
                                     r0["resolution"])
    return _measured(kind, round(float(ratio), 3))


@lru_cache(maxsize=512)
def _measured(kind: str, ratio: float) -> Manufacturability:
    return measure_manufacturability(kind, ratio)


def max_neck_over_a(kinds: tuple[str, ...]) -> float | None:
    rows = [r for k in kinds for r in _table().get("kinds", {}).get(k, [])]
    return max(max(r["neck_over_a"]) for r in rows) if rows else None


def build_table(ratios: list[float], kinds: tuple[str, ...] = KINDS, N: int = 48) -> dict:
    import datetime as _dt

    out = {"generated_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
           "method": "aces.domains.hx.tpms.measure_manufacturability", "resolution": N, "kinds": {}}
    for k in kinds:
        out["kinds"][k] = [asdict(measure_manufacturability(k, r, N)) for r in ratios]
    return out


# ----------------------------------------------------------------------------- export

def sample_mesh(kind: str, cell_m: float, wall_m: float, n_cells: int = 3, region: str = "solid", resolution: int = 32):
    """Closed triangle mesh of an n x n x n block, in metres, capped exactly on the block faces.

    region: "solid" (the printed wall), "fluid_a" or "fluid_b" (CFD domains).
    """
    ratio = wall_m / cell_m
    d = signed_distance(kind, *_grid(n_cells, resolution))
    half = 0.5 * ratio
    if region == "solid":
        inside = half - np.abs(d)
    elif region == "fluid_a":
        inside = d - half
    elif region == "fluid_b":
        inside = -d - half
    else:
        raise ValueError("region must be 'solid', 'fluid_a' or 'fluid_b'")
    # Clip (f/|grad f| blows up where the gradient vanishes) and pad with a huge negative
    # value so the closing faces sit on the boundary node planes, not a voxel outside.
    inside = np.clip(inside, -1.0, 1.0)
    padded = np.pad(inside, 1, mode="constant", constant_values=-1.0e9)
    h = cell_m / resolution
    verts, faces, _, _ = marching_cubes(padded, level=0.0, spacing=(h, h, h))
    verts -= h  # undo the padding offset
    verts = np.clip(verts, 0.0, n_cells * cell_m)
    if mesh_volume(verts, faces) < 0:  # enforce outward orientation from the enclosed volume
        faces = faces[:, ::-1]
    return verts, faces


def mesh_volume(verts: np.ndarray, faces: np.ndarray) -> float:
    tri = verts[faces]
    return float(np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])).sum() / 6.0)


def write_stl(path: str | Path, verts: np.ndarray, faces: np.ndarray, header: str = "ACES TPMS sample",
              scale: float = 1.0, *, out=None) -> Path:
    """Binary STL.  Coordinates are written in metres x scale (scale=1000 for millimetres).

    Written through :mod:`aces.safeio` (audit M1): a new file only, never through a link.  With ``out`` (an open
    :class:`aces.safeio.OutDir`), ``path`` is a file name inside that folder.
    """
    from ... import safeio

    tri = (verts[faces] * scale).astype(np.float32)
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    norm = np.linalg.norm(n, axis=1, keepdims=True)
    n = (n / np.where(norm == 0, 1, norm)).astype(np.float32)
    rec = np.zeros(len(faces), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"] = n
    rec["v"] = tri
    data = header.encode()[:80].ljust(80, b" ") + struct.pack("<I", len(faces)) + rec.tobytes()
    if out is not None:
        return out.write_bytes(str(path), data)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return safeio.write_bytes(p, data)
