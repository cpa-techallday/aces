import numpy as np
from aces_cad.worker.sdf import mesh_from_sdf

PARAMS = {
    "size": {"default": 30.0, "min": 10.0, "max": 36.0, "unit": "mm", "doc": "cube edge"},
    "cell": {"default": 10.0, "min": 10.0, "max": 40.0, "unit": "mm", "doc": "gyroid period"},
    "wall": {"default": 1.2, "min": 1.0, "max": 5.0, "unit": "mm", "doc": "sheet thickness (approximate)"},
}


def build(p):
    k = 2 * np.pi / p.cell
    half = p.size / 2

    def sdf(X, Y, Z):
        g = np.sin(k * X) * np.cos(k * Y) + np.sin(k * Y) * np.cos(k * Z) + np.sin(k * Z) * np.cos(k * X)
        sheet = np.abs(g) / k - p.wall / 2           # approximate distance to the sheet
        box = np.maximum(np.maximum(np.abs(X), np.abs(Y)), np.abs(Z)) - half
        return np.maximum(sheet, box)                # the sheet, clipped to the cube

    # h: about four grid steps across the thinnest wall, and at most cell / 16
    return mesh_from_sdf(sdf, (-half,) * 3, (half,) * 3, h=min(p.cell / 16, p.wall / 4))
