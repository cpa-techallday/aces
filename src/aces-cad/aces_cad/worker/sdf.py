"""mesh_from_sdf: a triangle mesh from a signed-distance function (section 7.7 of the build spec).

    from aces_cad.worker.sdf import mesh_from_sdf
    mesh = mesh_from_sdf(lambda X, Y, Z: (X**2 + Y**2 + Z**2) ** 0.5 - 10, (-12, -12, -12), (12, 12, 12), 1.0)

fn(X, Y, Z) is evaluated once, vectorised, on np.meshgrid(..., indexing="ij") arrays and must be negative inside. The
grid is padded with one cell of +h (outside) so the surface closes at the box, marching cubes runs with
allow_degenerate=False, the vertices are offset by lo - h, and the mesh is inverted when its volume is negative.

A grid value exactly at the level (a planar clip face lying on a grid plane, such as max(sheet, box) with the box
faces at lo and hi) puts marching-cubes vertices exactly on grid points, and the triangles of neighbouring cells then
meet in edges shared by more than two of them: the skill's gyroid example had 2162 such values and 864 such edges, so
the run reported watertight false (open_shell) and the pull refused it. Those values are counted as outside by 1e-9 h
before marching cubes, which closed every case probed (17 gyroid runs). The run still reports watertight; nothing
is repaired after the fact. More than 256^3 grid points is refused (kind result). Meshes pull as STL only.
"""
from __future__ import annotations

from aces_cad.worker.main import WorkerError

MAX_CELLS = 256 ** 3


def mesh_from_sdf(fn, lo, hi, h, level=0.0):
    """A trimesh.Trimesh of the level set {fn = level} inside the box [lo, hi] at spacing h (mm)."""
    import math
    import numpy as np
    import trimesh
    from skimage import measure
    lo = np.asarray(lo, dtype=np.float64).reshape(3)
    hi = np.asarray(hi, dtype=np.float64).reshape(3)
    h = float(h)
    if not (math.isfinite(h) and h > 0) or not np.all(np.isfinite(lo)) or not np.all(np.isfinite(hi)):
        raise WorkerError("result", "mesh_from_sdf needs finite lo, hi and a spacing h > 0")
    if np.any(hi <= lo):
        raise WorkerError("result", "mesh_from_sdf needs hi > lo on every axis")
    n = np.ceil((hi - lo) / h - 1e-9).astype(np.int64) + 1
    cells = int(n[0]) * int(n[1]) * int(n[2])
    if cells > MAX_CELLS:
        raise WorkerError("result", f"mesh_from_sdf would sample {cells} grid points; at most {MAX_CELLS} (256^3): use "
                                    f"a larger h or a smaller box")
    axes = [lo[i] + h * np.arange(n[i]) for i in range(3)]
    X, Y, Z = np.meshgrid(*axes, indexing="ij")
    values = np.asarray(fn(X, Y, Z), dtype=np.float64)
    if values.shape != X.shape:
        raise WorkerError("result", f"the SDF returned shape {values.shape}, not the grid's {X.shape}")
    if not np.all(np.isfinite(values)):
        raise WorkerError("result", "the SDF returned NaN or infinite values")
    on_level = values == float(level)
    if on_level.any():  # exactly on the level: count as outside by a hair, or marching cubes leaves non-manifold edges
        values = np.where(on_level, float(level) + 1e-9 * h, values)
    padded = np.pad(values, 1, mode="constant", constant_values=float(level) + h)
    try:
        verts, faces, _normals, _vals = measure.marching_cubes(padded, level=float(level), spacing=(h, h, h),
                                                                 allow_degenerate=False)
    except (ValueError, RuntimeError) as e:
        raise WorkerError("result", f"marching cubes found no surface at level {level}: {e}") from None
    verts = verts + (lo - h)
    mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=True)
    if mesh.volume < 0:
        mesh.invert()
    return mesh
