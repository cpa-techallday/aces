"""Retriangulate a mesh so edges approximate a target strut length.

Strategy (pure-pip, no CGAL): loop-subdivide while edges are too long, then
quadric-decimate to the face count implied by the target edge length. Quadric
decimation on a densely subdivided surface yields near-uniform triangles on
smooth regions — good enough for lattice struts, and it never fails to import.
For production-grade isotropic remeshing swap in pymeshlab's
meshing_isotropic_explicit_remeshing (drop-in point marked below).
"""

from __future__ import annotations

import numpy as np
import trimesh


def _mean_edge(mesh: trimesh.Trimesh) -> float:
    return float(mesh.edges_unique_length.mean())


def target_face_count(mesh: trimesh.Trimesh, edge: float) -> int:
    """Faces needed to cover the surface with equilateral triangles of side *edge*."""
    tri_area = (np.sqrt(3.0) / 4.0) * edge * edge
    return max(int(mesh.area / tri_area), 16)


def remesh_to_edge_length(
    mesh: trimesh.Trimesh,
    edge: float,
    max_subdiv: int = 6,
    max_faces: int = 400_000,
    max_edge_factor: float = 1.6,
) -> trimesh.Trimesh:
    """Return a retriangulated copy whose edges approximate *edge* (mm).

    max_edge_factor caps the longest edge at edge * factor, so flat regions
    cannot survive as giant triangles.
    """
    work = mesh.copy()

    # 1. subdivide until we are comfortably denser than the target
    goal = target_face_count(mesh, edge)
    for _ in range(max_subdiv):
        if len(work.faces) >= min(4 * goal, max_faces):
            break
        if _mean_edge(work) <= 0.75 * edge:
            break
        work = work.subdivide()

    # 2. decimate down to the implied count (uniformising pass)
    if len(work.faces) > goal:
        try:
            import fast_simplification

            v, f = fast_simplification.simplify(
                work.vertices.astype(np.float32),
                work.faces.astype(np.int32),
                target_count=goal,
            )
            work = trimesh.Trimesh(vertices=v, faces=f, process=True)
        except Exception:
            work = work.simplify_quadric_decimation(face_count=goal)

    # 3. cap the long edges. Quadric decimation is driven by *error*, and a
    # large flat region has none -- so a 14 m slab face collapses to a couple
    # of enormous triangles and yields struts nothing could print. Subdividing
    # back down to a size ceiling fixes the flats while leaving the curved
    # regions (already near-uniform) untouched.
    ceiling = edge * max_edge_factor
    if len(work.edges_unique_length) and work.edges_unique_length.max() > ceiling:
        try:
            work = work.subdivide_to_size(ceiling, max_iter=12)
        except Exception:
            pass

    work.merge_vertices()
    work.update_faces(work.nondegenerate_faces())
    work.remove_unreferenced_vertices()
    return work
