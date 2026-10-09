"""Outputs: density -> STL via marching cubes, binary STL writer, reports, slice plots."""
from __future__ import annotations

import json
import os
from typing import Optional

import numpy as np


def write_binary_stl(path: str, verts: np.ndarray, faces: np.ndarray, name: bytes = b"gendesign") -> None:
    tri = np.asarray(verts, dtype=np.float64)[np.asarray(faces)]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    n = n / np.where(ln == 0, 1.0, ln)
    rec = np.zeros(len(faces), dtype=[("n", np.float32, 3), ("v", np.float32, (3, 3)), ("attr", np.uint16)])
    rec["n"] = n
    rec["v"] = tri
    with open(path, "wb") as f:
        f.write(name.ljust(80, b"\0"))
        f.write(np.uint32(len(faces)).tobytes())
        f.write(rec.tobytes())


def density_to_mesh(xphys: np.ndarray, h: float, level: float = 0.5, smooth_iters: int = 0):
    """Iso-surface of a (nx, ny, nz) density field in mm. Returns (verts, faces)."""
    from skimage import measure

    vol = np.pad(np.asarray(xphys, dtype=np.float32), 1, constant_values=0.0)
    if vol.max() <= level:
        raise ValueError("density field never exceeds the iso level; nothing to export")
    verts, faces, _normals, _vals = measure.marching_cubes(vol, level=level, spacing=(h, h, h))
    verts = verts - 0.5 * h  # padded index p <-> element p-1 whose centre is (p-0.5) h
    if smooth_iters > 0:
        try:
            import trimesh

            m = trimesh.Trimesh(verts, faces, process=False)
            trimesh.smoothing.filter_taubin(m, iterations=int(smooth_iters))
            verts, faces = np.asarray(m.vertices), np.asarray(m.faces)
        except Exception as exc:  # smoothing is optional
            print(f"[export] smoothing skipped: {exc}")
    return verts, faces


def density_to_stl(xphys: np.ndarray, h: float, path: str, level: float = 0.5, smooth_iters: int = 0) -> dict:
    verts, faces = density_to_mesh(xphys, h, level, smooth_iters)
    write_binary_stl(path, verts, faces)
    return {"path": path, "vertices": int(len(verts)), "faces": int(len(faces))}


def mask_to_stl(mask: np.ndarray, h: float, path: str, smooth_iters: int = 0) -> dict:
    return density_to_stl(np.asarray(mask, dtype=np.float32), h, path, 0.5, smooth_iters)


def save_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, default=_json_default)


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def plot_slices(xphys: np.ndarray, h: float, path: str, title: str = "") -> Optional[str]:
    """Three mid-plane slices of the density field as a PNG (needs matplotlib)."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    nx, ny, nz = xphys.shape
    fig, axs = plt.subplots(1, 3, figsize=(13, 4))
    panels = [
        (xphys[:, :, nz // 2].T, f"z = {(nz // 2 + 0.5) * h:.1f} mm", "x [mm]", "y [mm]", (nx, ny)),
        (xphys[:, ny // 2, :].T, f"y = {(ny // 2 + 0.5) * h:.1f} mm", "x [mm]", "z [mm]", (nx, nz)),
        (xphys[nx // 2, :, :].T, f"x = {(nx // 2 + 0.5) * h:.1f} mm", "y [mm]", "z [mm]", (ny, nz)),
    ]
    for ax, (img, ttl, xl, yl, (w, d)) in zip(axs, panels):
        ax.imshow(img, origin="lower", cmap="gray_r", vmin=0, vmax=1, extent=(0, w * h, 0, d * h))
        ax.set_title(ttl)
        ax.set_xlabel(xl)
        ax.set_ylabel(yl)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path
