"""Load geometry from Fusion 360 exports (and anything else trimesh reads).

Fusion 360 'Form' (T-spline) bodies should be exported as STEP (exact) or
STL/OBJ (already tessellated). STEP is converted with cascadio (OpenCASCADE).
All geometry is normalised to millimetres.
"""

from __future__ import annotations

import os
import tempfile

import trimesh

STEP_EXT = {".step", ".stp"}


def _scene_to_mesh(scene: "trimesh.Scene", name: str | None) -> trimesh.Trimesh:
    if name:
        matches = [g for k, g in scene.geometry.items() if name.lower() in k.lower()]
        if not matches:
            raise ValueError(
                f"no geometry matching '{name}'; available: {list(scene.geometry)}"
            )
        picked = matches
    else:
        picked = list(scene.geometry.values())
    return trimesh.util.concatenate(picked)


def load_mesh(
    path: str,
    name: str | None = None,
    scale: float | None = None,
    tol_linear: float = 1.0,
    tol_angular: float = 0.5,
) -> trimesh.Trimesh:
    """Load *path* and return a single Trimesh in millimetres.

    name        pick a sub-body from multi-body files (substring match)
    scale       explicit unit multiplier (overrides auto mm heuristic)
    tol_linear  STEP tessellation chord tolerance (model units, mm)
    tol_angular STEP tessellation angular tolerance (radians)
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in STEP_EXT:
        import cascadio

        with tempfile.NamedTemporaryFile(suffix=".glb", delete=False) as f:
            tmp = f.name
        cascadio.step_to_glb(path, tmp, tol_linear=tol_linear, tol_angular=tol_angular)
        loaded = trimesh.load(tmp)
        os.unlink(tmp)
        if scale is None:
            scale = 1000.0  # cascadio emits GLB in metres; STEP was mm
    else:
        loaded = trimesh.load(path)

    mesh = _scene_to_mesh(loaded, name) if isinstance(loaded, trimesh.Scene) else loaded
    if not isinstance(mesh, trimesh.Trimesh):
        raise TypeError(f"unsupported geometry type: {type(mesh)}")

    if scale and scale != 1.0:
        mesh.apply_scale(scale)

    # drop degenerate faces / merge duplicate vertices
    mesh.merge_vertices()
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.remove_unreferenced_vertices()
    return mesh
