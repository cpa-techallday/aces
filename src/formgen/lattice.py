"""Turn a remeshed triangulated surface into a printable lattice.

Modes
-----
skin    the triangulated surface itself: every mesh edge becomes a strut
        (single-layer tessellated shell — Fusion form, faceted).
panel   Branch-style cellular panel: outer skin + inner offset skin +
        through ties + web diagonals = a doubly-triangulated space truss.
infill  experimental volumetric fill: interior Delaunay tetra edges clipped
        to the solid (requires a watertight input).
"""

from __future__ import annotations

import numpy as np
import trimesh

from .graph import LatticeGraph


def _surface_struts(g: LatticeGraph, offset: int, faces: np.ndarray, kind: str):
    edges = trimesh.geometry.faces_to_edges(faces)
    edges = np.sort(edges, axis=1)
    edges = np.unique(edges, axis=0)
    for a, b in edges:
        g.add_strut(offset + a, offset + b, kind)
    return edges


def local_thickness(mesh: trimesh.Trimesh, normals: np.ndarray) -> np.ndarray:
    """How thick is the solid under each vertex, measured along -normal.

    Panel mode offsets an inner skin inward by a fixed depth. If that depth
    exceeds the local wall thickness the inner skin punches out through the far
    face, the two skins cross, and the tessellation is nonsense -- silently.
    Measuring the real thickness lets us clamp instead.
    """
    try:
        from trimesh.proximity import thickness as _thk

        t = _thk(mesh=mesh, points=mesh.vertices, exterior=False,
                 normals=normals, method="ray")
        t = np.asarray(t, dtype=float)
        # rays that miss (open shells, grazing angles) come back inf/nan
        bad = ~np.isfinite(t) | (t <= 0)
        if bad.all():
            return None
        t[bad] = np.median(t[~bad])
        return t
    except Exception:
        return None


def build_lattice(
    mesh: trimesh.Trimesh,
    mode: str = "panel",
    thickness: float = 60.0,
    infill_cell: float | None = None,
    clamp_thickness: bool = True,
    clamp_fraction: float = 0.45,
    clamp_floor: float = 0.30,
) -> LatticeGraph:
    g = LatticeGraph.empty()
    g.meta = {"mode": mode, "thickness_mm": thickness if mode == "panel" else None}

    if mode == "skin":
        g.add_nodes(np.asarray(mesh.vertices))
        _surface_struts(g, 0, mesh.faces, "skin")

    elif mode == "panel":
        outer = np.asarray(mesh.vertices)
        normals = np.asarray(mesh.vertex_normals)

        depth = np.full(len(outer), float(thickness))
        if clamp_thickness:
            t = local_thickness(mesh, normals)
            if t is not None:
                # grazing rays report near-zero thickness; without a floor the
                # tie struts collapse to nothing and get pruned away
                cap = np.maximum(t * clamp_fraction, thickness * clamp_floor)
                clamped = cap < depth
                g.meta["thickness_measured_mm"] = {
                    "min": round(float(t.min()), 1),
                    "median": round(float(np.median(t)), 1),
                    "max": round(float(t.max()), 1),
                }
                g.meta["thickness_clamped_vertices"] = int(clamped.sum())
                g.meta["thickness_clamped_fraction"] = round(
                    float(clamped.mean()), 4)
                depth = np.minimum(depth, cap)
                g.meta["thickness_applied_mm"] = {
                    "min": round(float(depth.min()), 1),
                    "median": round(float(np.median(depth)), 1),
                    "max": round(float(depth.max()), 1),
                }
        inner = outer - normals * depth[:, None]
        o_idx = g.add_nodes(outer)
        i_idx = g.add_nodes(inner)
        edges = _surface_struts(g, 0, mesh.faces, "outer")
        _surface_struts(g, len(outer), mesh.faces, "inner")
        # through ties
        for k in range(len(outer)):
            g.add_strut(o_idx[k], i_idx[k], "tie")
        # web diagonals: one per surface edge, alternated for symmetry
        for a, b in edges:
            if (a + b) % 2 == 0:
                g.add_strut(o_idx[a], i_idx[b], "web")
            else:
                g.add_strut(o_idx[b], i_idx[a], "web")

    elif mode == "infill":
        if infill_cell is None:
            infill_cell = thickness
        if not mesh.is_watertight:
            raise ValueError("infill mode needs a watertight input solid")
        from scipy.spatial import Delaunay

        surf = np.asarray(mesh.vertices)
        lo, hi = mesh.bounds
        axes = [np.arange(l + infill_cell / 2, h, infill_cell) for l, h in zip(lo, hi)]
        grid = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)
        inside = grid[mesh.contains(grid)]
        pts = np.vstack([surf, inside])
        g.add_nodes(pts)
        tet = Delaunay(pts)
        edges = set()
        for simplex in tet.simplices:
            for a in range(4):
                for b in range(a + 1, 4):
                    i, j = sorted((simplex[a], simplex[b]))
                    edges.add((i, j))
        edges = np.array(sorted(edges))
        L = np.linalg.norm(pts[edges[:, 0]] - pts[edges[:, 1]], axis=1)
        keep = L < 1.8 * infill_cell
        mids = 0.5 * (pts[edges[:, 0]] + pts[edges[:, 1]])
        keep &= mesh.contains(mids)
        for i, j in edges[keep]:
            g.add_strut(i, j, "infill")

    else:
        raise ValueError(f"unknown mode '{mode}'")

    g.dedupe()
    return g
