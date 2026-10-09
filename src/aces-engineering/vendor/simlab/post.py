"""Post-processing helpers shared by the staged adapters: VTK writers (no VTK dependency), field
slice plots, and the per-case Markdown stub the report builder picks up."""
from __future__ import annotations

import os
from typing import Dict, Optional, Sequence

import numpy as np


def write_vtk_structured(path: str, shape: Sequence[int], h: float, origin: Sequence[float] = (0.0, 0.0, 0.0),
                         cell_data: Optional[Dict[str, np.ndarray]] = None,
                         point_data: Optional[Dict[str, np.ndarray]] = None) -> str:
    """Legacy VTK STRUCTURED_POINTS (binary, big-endian) on the voxel grid.

    Element arrays use the solver's C-order (e = (i*ny + j)*nz + k); VTK wants i fastest, so
    (nx, ny, nz) arrays are transposed to (nz, ny, nx) before flattening. Node arrays use
    n = i + (nx+1)(j + (ny+1)k), which is already VTK point order.
    """
    nx, ny, nz = [int(v) for v in shape]
    cell_data = cell_data or {}
    point_data = point_data or {}
    with open(path, "wb") as f:
        f.write(b"# vtk DataFile Version 3.0\nsimlab voxel field\nBINARY\nDATASET STRUCTURED_POINTS\n")
        f.write(f"DIMENSIONS {nx + 1} {ny + 1} {nz + 1}\n".encode())
        f.write(f"ORIGIN {origin[0]} {origin[1]} {origin[2]}\nSPACING {h} {h} {h}\n".encode())
        if cell_data:
            f.write(f"CELL_DATA {nx * ny * nz}\n".encode())
            for name, arr in cell_data.items():
                a = np.asarray(arr)
                if a.ndim == 1:
                    a = a.reshape(nx, ny, nz)
                a = np.ascontiguousarray(a.transpose(2, 1, 0)).reshape(-1)
                f.write(f"SCALARS {name} float 1\nLOOKUP_TABLE default\n".encode())
                f.write(a.astype(">f4").tobytes())
                f.write(b"\n")
        if point_data:
            f.write(f"POINT_DATA {(nx + 1) * (ny + 1) * (nz + 1)}\n".encode())
            for name, arr in point_data.items():
                a = np.asarray(arr)
                if a.ndim == 2 and a.shape[1] == 3:
                    f.write(f"VECTORS {name} float\n".encode())
                    f.write(np.ascontiguousarray(a).astype(">f4").tobytes())
                else:
                    f.write(f"SCALARS {name} float 1\nLOOKUP_TABLE default\n".encode())
                    f.write(np.ascontiguousarray(a.reshape(-1)).astype(">f4").tobytes())
                f.write(b"\n")
    return path


def plot_field_slices(field: np.ndarray, mask: Optional[np.ndarray], h: float, path: str, title: str,
                      units: str, cmap: str = "viridis", origin=(0.0, 0.0, 0.0), vmax: Optional[float] = None,
                      planes: Optional[dict] = None) -> Optional[str]:
    """Three slices of an element field through the location of its maximum (masked cells blank)."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    f = np.asarray(field, dtype=float)
    nx, ny, nz = f.shape
    if mask is not None:
        f = np.where(np.asarray(mask, dtype=bool), f, np.nan)
    if planes is None:
        with np.errstate(invalid="ignore"):
            idx = np.unravel_index(np.nanargmax(np.nan_to_num(f, nan=-np.inf)), f.shape)
        planes = {"x": int(idx[0]), "y": int(idx[1]), "z": int(idx[2])}
    ox, oy, oz = origin
    vmax = vmax if vmax is not None else float(np.nanmax(f)) if np.isfinite(np.nanmax(f)) else 1.0
    fig, axs = plt.subplots(1, 3, figsize=(14, 4.2))
    panels = [
        (f[:, :, planes["z"]].T, f"z = {oz + (planes['z'] + 0.5) * h:.1f} mm", "x [mm]", "y [mm]", (ox, ox + nx * h, oy, oy + ny * h)),
        (f[:, planes["y"], :].T, f"y = {oy + (planes['y'] + 0.5) * h:.1f} mm", "x [mm]", "z [mm]", (ox, ox + nx * h, oz, oz + nz * h)),
        (f[planes["x"], :, :].T, f"x = {ox + (planes['x'] + 0.5) * h:.1f} mm", "y [mm]", "z [mm]", (oy, oy + ny * h, oz, oz + nz * h)),
    ]
    im = None
    for ax, (img, ttl, xl, yl, ext) in zip(axs, panels):
        im = ax.imshow(img, origin="lower", cmap=cmap, vmin=0, vmax=vmax, extent=ext, interpolation="nearest")
        ax.set_title(ttl)
        ax.set_xlabel(xl)
        ax.set_ylabel(yl)
        ax.set_aspect("equal")
    if im is not None:
        cb = fig.colorbar(im, ax=axs, shrink=0.85)
        cb.set_label(units)
    fig.suptitle(title)
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return path


def case_markdown(path: str, title: str, numbers: Dict[str, float], notes: Sequence[str] = (),
                  figures: Sequence[str] = ()) -> str:
    lines = [f"### {title}", "", "| quantity | value |", "|---|---|"]
    for k, v in numbers.items():
        lines.append(f"| {k} | {v:.6g} |" if isinstance(v, (int, float)) else f"| {k} | {v} |")
    for n in notes:
        lines.append(f"\n{n}")
    for fig in figures:
        lines.append(f"\n![{os.path.basename(fig)}]({os.path.basename(fig)})")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path
