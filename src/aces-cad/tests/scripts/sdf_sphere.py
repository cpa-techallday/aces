"""A mesh result from a signed distance field (test fixture): a sphere of radius 10 mm sampled at 1 mm.

mesh_from_sdf pads the grid, runs marching cubes and returns a watertight trimesh.Trimesh.
"""
import numpy as np

from aces_cad.worker.sdf import mesh_from_sdf

PARAMS = {
    "radius": {"default": 10.0, "min": 1.0, "max": 100.0, "unit": "mm", "doc": "sphere radius"},
    "h": {"default": 1.0, "min": 0.1, "max": 5.0, "unit": "mm", "doc": "grid spacing"},
}


def build(p):
    r = p.radius
    lo = (-(r + 2.0),) * 3
    hi = (r + 2.0,) * 3
    return mesh_from_sdf(lambda x, y, z: np.sqrt(x * x + y * y + z * z) - r, lo, hi, p.h)
