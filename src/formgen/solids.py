"""Sweep the lattice into printable solid geometry (cylinders + node spheres)."""

from __future__ import annotations

import numpy as np
import trimesh


def strut_solids(
    g,
    strut_diameter: float = 8.0,
    node_diameter: float | None = None,
    sections: int = 8,
    union: bool = False,
) -> trimesh.Trimesh:
    """Return a mesh of every strut as a cylinder plus a sphere at every node.

    union=False (default) concatenates primitives — fast, fine for preview and
    for most slicers (they handle self-intersections). union=True runs a
    manifold boolean union (needs the `manifold3d` package) for a watertight
    single solid suitable for downstream CAD/FEA.
    """
    r = strut_diameter / 2.0
    nr = (node_diameter or strut_diameter * 1.25) / 2.0

    parts = []
    for i, j, _ in g.struts:
        a, b = g.nodes[i], g.nodes[j]
        vec = b - a
        length = float(np.linalg.norm(vec))
        if length < 1e-6:
            continue
        cyl = trimesh.creation.cylinder(radius=r, height=length, sections=sections)
        # align +Z cylinder to the strut vector
        transform = trimesh.geometry.align_vectors([0, 0, 1], vec / length)
        transform[:3, 3] = (a + b) / 2.0
        cyl.apply_transform(transform)
        parts.append(cyl)

    sphere = trimesh.creation.icosphere(subdivisions=1, radius=nr)
    for p in g.nodes:
        s = sphere.copy()
        s.apply_translation(p)
        parts.append(s)

    if union:
        try:
            combined = trimesh.boolean.union(parts, engine="manifold")
        except Exception:
            combined = trimesh.util.concatenate(parts)
    else:
        combined = trimesh.util.concatenate(parts)
    return combined
