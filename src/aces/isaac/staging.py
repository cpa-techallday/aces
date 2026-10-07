"""Stage CAD geometry (STL) as USD for the Isaac Sim tier.

Isaac Sim consumes USD; ACES and KiCad/Fusion produce STEP/STL.  This module is the
bridge: it parses binary or ASCII STL with numpy and writes a USD ASCII (.usda)
mesh prim, with real extents and counts — no pxr dependency, no fabricated metadata.
The output opens in Isaac Sim (pxr reads .usda natively) and in any USD viewer.

Staging writes a new file next to nothing: the source mesh is read-only, and the
target is a fresh artifact in the run/assets directory.
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np


def read_stl(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """Read an STL file, return (vertices (n,3) float64 deduped, faces (m,3) int32)."""
    p = Path(path)
    data = p.read_bytes()
    if len(data) < 84:
        raise ValueError(f"{p}: too small to be a binary STL")
    n_tri = struct.unpack_from("<I", data, 80)[0]
    if 84 + n_tri * 50 == len(data):  # binary
        arr = np.frombuffer(data[84:], dtype=np.dtype([
            ("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2"),
        ]), count=n_tri)
        tris = arr["v"].astype(np.float64).reshape(-1, 3)
    else:  # ascii
        pts = []
        for line in data.decode("utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) == 4 and parts[0] == "vertex":
                pts.append([float(parts[1]), float(parts[2]), float(parts[3])])
        tris = np.asarray(pts, dtype=np.float64)
        if tris.size == 0 or tris.shape[0] % 3:
            raise ValueError(f"{p}: malformed ASCII STL ({tris.shape[0]} vertices)")
    # dedupe vertices (STL stores every triangle's corners independently)
    verts, faces = np.unique(tris, axis=0, return_inverse=True)
    return verts, faces.reshape(-1, 3).astype(np.int64)


def write_usda(verts: np.ndarray, faces: np.ndarray, out: str | Path, prim_name: str = "mesh",
               meters_per_unit: float = 0.001) -> Path:
    """Write vertices/faces as a single Mesh prim in USD ASCII.

    ``meters_per_unit`` defaults to 0.001 because KiCad/Fusion STEP→STL exports from
    this project tree are in millimetres; the prim is scaled so the USD stage is in
    metres (Isaac Sim's physics expects metres).
    """
    o = Path(out)
    n_v, n_f = len(verts), len(faces)
    lo, hi = verts.min(axis=0), verts.max(axis=0)

    def _fmt_vec3(v) -> str:
        return f"({v[0]:.6f}, {v[1]:.6f}, {v[2]:.6f})"

    pts = ", ".join(_fmt_vec3(v) for v in verts)
    counts = ", ".join(["3"] * n_f)
    idx = ", ".join(str(i) for i in faces.reshape(-1))

    text = f"""#usda 1.0
(
    defaultPrim = "{prim_name}"
    metersPerUnit = 1
    upAxis = "Z"
    doc = "staged from STL by aces.isaac.staging; source units mm, scaled to metres"
)

def Xform "{prim_name}" (
    doc = "STL staged mesh; uniform scale {meters_per_unit} converts source mm to metres"
)
{{
    float3 xformOp:scale = ({meters_per_unit}, {meters_per_unit}, {meters_per_unit})
    uniform token[] xformOpOrder = ["xformOp:scale"]

    def Mesh "geometry" (
        doc = "{n_f} triangles, {n_v} unique vertices; source extents mm "
              "[{lo[0]:.1f}..{hi[0]:.1f}, {lo[1]:.1f}..{hi[1]:.1f}, {lo[2]:.1f}..{hi[2]:.1f}]"
    )
    {{
        point3f[] points = [{pts}]
        int[] faceVertexCounts = [{counts}]
        int[] faceVertexIndices = [{idx}]
        float3[] extent = [{_fmt_vec3(lo)}, {_fmt_vec3(hi)}]
    }}
}}
"""
    o.write_text(text, encoding="utf-8")
    return o


def stage_stl(src: str | Path, out: str | Path, prim_name: str = "mesh",
              meters_per_unit: float = 0.001) -> dict:
    """Convert one STL to .usda and return real stats about the conversion."""
    verts, faces = read_stl(src)
    path = write_usda(verts, faces, out, prim_name, meters_per_unit)
    lo, hi = verts.min(axis=0) * meters_per_unit, verts.max(axis=0) * meters_per_unit
    return {
        "source": str(src),
        "output": str(path),
        "vertices": int(len(verts)),
        "triangles": int(len(faces)),
        "extent_m": [[round(float(x), 4) for x in lo], [round(float(x), 4) for x in hi]],
        "size_m": [round(float(x), 4) for x in (hi - lo)],
    }
