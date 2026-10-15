"""Generate a doubly-curved facade panel (BranchClad-style demo input).

2400 x 1200 mm wall panel standing in the XZ plane with sinusoidal
out-of-plane articulation (max ~250 mm), written as demo_panel.stl.
Run:  python scripts/make_demo_panel.py
"""

import os

import numpy as np
import trimesh

W, H = 2400.0, 1200.0        # mm, panel lies in X (width) / Z (height)
NX, NZ = 49, 25              # grid resolution
AMP = 125.0                  # out-of-plane amplitude (total articulation 2*AMP)


def surface() -> trimesh.Trimesh:
    x = np.linspace(0, W, NX)
    z = np.linspace(0, H, NZ)
    X, Z = np.meshgrid(x, z, indexing="ij")
    Y = (AMP * np.sin(2 * np.pi * X / W * 1.5) * np.cos(np.pi * Z / H)
         + 0.35 * AMP * np.sin(2 * np.pi * Z / H))
    V = np.stack([X, Y, Z], axis=-1).reshape(-1, 3)

    faces = []
    for i in range(NX - 1):
        for j in range(NZ - 1):
            a = i * NZ + j
            b = (i + 1) * NZ + j
            faces += [[a, b, a + 1], [b, b + 1, a + 1]]
    return trimesh.Trimesh(vertices=V, faces=np.array(faces), process=True)


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "..", "examples", "formgen", "demo_panel.stl")
    m = surface()
    m.export(os.path.abspath(out))
    print(f"wrote {os.path.abspath(out)}  extents={m.extents.round(1).tolist()} mm")
