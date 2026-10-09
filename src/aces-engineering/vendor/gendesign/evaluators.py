"""Evaluators for parametric designs.

VoxelFEAEvaluator: embed any solid (a boolean voxel mask) in a fixed design envelope
with fixed supports and loads, and return compliance, peak displacement, an element-
centre von Mises estimate, volume and mass. The void is modelled as a very soft
material (fictitious-domain approach), so every design in a study shares one grid, one
set of boundary conditions and one warm-started GPU solver.

voxelize_mesh / build123d_to_mask turn a mesh or a build123d part into that mask, so
the same evaluator scores STEP-grade parametric geometry.
"""
from __future__ import annotations

import os
import tempfile
from typing import Dict, Optional, Sequence

import numpy as np

from .fea3d import VoxelFEA


class VoxelFEAEvaluator:
    def __init__(self, envelope_mm: Sequence[float], h: float, E: float = 200000.0, nu: float = 0.3,
                 rho_kg_mm3: float = 7.85e-6, supports: Sequence[dict] = (), loads: Sequence[dict] = (),
                 dtype: str = "float64", prefer_gpu: bool = True, emin: float = 1e-9,
                 tol: float = 1e-8, maxiter: int = 8000, warm_start: bool = True,
                 vm_exclude_boxes: Sequence[Sequence[float]] = ()):
        self.h = float(h)
        self.nx, self.ny, self.nz = [max(1, int(round(float(L) / h))) for L in envelope_mm]
        self.shape = (self.nx, self.ny, self.nz)
        self.E, self.rho, self.emin, self.tol, self.maxiter = float(E), float(rho_kg_mm3), float(emin), float(tol), int(maxiter)
        self.warm_start = warm_start
        self.fea = VoxelFEA(self.nx, self.ny, self.nz, self.h, nu, dtype, prefer_gpu)
        for s in supports:
            self.fea.fix_box(s["box"], s.get("dofs", (0, 1, 2)))
        for ld in loads:
            if "axis" in ld:
                self.fea.add_traction_box(ld.get("case", "default"), ld["box"], ld["force"], ld["axis"])
            else:
                self.fea.add_load_box(ld.get("case", "default"), ld["box"], ld["force"])
        xp = self.fea.xp
        self._vm_keep = xp.ones(self.fea.ne, dtype=bool)
        for b in vm_exclude_boxes:  # stress read-out ignores load-application zones
            self._vm_keep &= ~self.fea.elems_in_box(b)
        self._warm: Dict[str, object] = {}

    def elem_xyz(self) -> np.ndarray:
        """Element-centre coordinates as a (nx, ny, nz, 3) NumPy array, for building masks."""
        i, j, k = np.meshgrid(np.arange(self.nx), np.arange(self.ny), np.arange(self.nz), indexing="ij")
        return (np.stack([i, j, k], axis=-1) + 0.5) * self.h

    def evaluate(self, mask: np.ndarray, return_fields: bool = False) -> Dict[str, float]:
        """Score a boolean mask. With return_fields, the result also carries 'fields':
        u_mm (nodal displacement, (nn, 3), physical mm) and vm_MPa (element von Mises) of the
        worst load case, for post-processing."""
        xp = self.fea.xp
        m = xp.asarray(np.asarray(mask, dtype=bool).reshape(-1))
        if int(m.shape[0]) != self.fea.ne:
            raise ValueError(f"mask has {int(m.shape[0])} cells, grid has {self.fea.ne}")
        x = m.astype(self.fea.dtype)
        Ee = self.emin + x * (1.0 - self.emin)
        c = 0.0
        umax = 0.0
        vm = 0.0
        its = []
        worst_res = 0.0
        fields = None
        for name, F in self.fea.loads.items():
            u0 = self._warm.get(name) if self.warm_start else None
            u, it, res = self.fea.solve(Ee, F, u0, self.tol, self.maxiter)
            self._warm[name] = u
            its.append(it)
            worst_res = max(worst_res, float(res))
            c += float(F @ u) / self.E
            umax = max(umax, self.fea.max_displacement(u) / self.E)
            s_all = self.fea.element_stress_vm(u, Ee)
            s = s_all[m & self._vm_keep]
            vm_case = float(s.max()) if int(s.size) else 0.0
            if return_fields and (fields is None or vm_case >= vm):
                fields = {"load_case": name, "u_mm": (u / self.E).reshape(-1, 3), "vm_MPa": s_all * xp.asarray(m, dtype=s_all.dtype)}
            vm = max(vm, vm_case)
        nsolid = int(m.sum())
        vol = nsolid * self.h ** 3
        out = {"compliance_Nmm": c, "umax_mm": umax, "vm_max_MPa": vm, "volume_mm3": vol,
               "mass_kg": vol * self.rho, "solid_fraction": nsolid / self.fea.ne,
               "pcg_iters": float(max(its)) if its else 0.0,
               "pcg_relres": worst_res, "converged": float(worst_res <= self.tol)}
        if return_fields:
            out["fields"] = fields
        return out


def voxelize_mesh(mesh, envelope_mm: Sequence[float], h: float, origin=(0.0, 0.0, 0.0)) -> np.ndarray:
    """Boolean (nx, ny, nz) occupancy of a closed mesh (path or trimesh.Trimesh) on the envelope grid."""
    import trimesh

    if isinstance(mesh, (str, os.PathLike)):
        mesh = trimesh.load(mesh, force="mesh")
    nx, ny, nz = [max(1, int(round(float(L) / h))) for L in envelope_mm]
    if mesh.is_watertight:
        # centre-point containment: 99.95 % agreement with the analytic mask on the bracket
        # check and ~0.1 s for 144k cells; voxelized().fill() over-fills by a surface layer.
        i, j, k = np.meshgrid(np.arange(nx), np.arange(ny), np.arange(nz), indexing="ij")
        centres = (np.stack([i, j, k], axis=-1).reshape(-1, 3) + 0.5) * h + np.asarray(origin, dtype=float)
        return np.asarray(mesh.contains(centres), dtype=bool).reshape(nx, ny, nz)
    # fallback for open meshes: surface-dilated occupancy (about one voxel thicker everywhere)
    vg = mesh.voxelized(pitch=h).fill()
    pts = np.asarray(vg.points)
    idx = np.floor((pts - np.asarray(origin, dtype=float)) / h).astype(int)
    grid = np.zeros((nx, ny, nz), dtype=bool)
    ok = (idx >= 0).all(axis=1) & (idx[:, 0] < nx) & (idx[:, 1] < ny) & (idx[:, 2] < nz)
    grid[idx[ok, 0], idx[ok, 1], idx[ok, 2]] = True
    return grid


def build123d_to_mask(part, envelope_mm: Sequence[float], h: float, origin=(0.0, 0.0, 0.0),
                      tolerance: float = 0.05) -> np.ndarray:
    """Voxelise a build123d Part (or Solid/Compound) onto the envelope grid."""
    from build123d import export_stl

    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "part.stl")
        try:
            export_stl(part, p, tolerance=tolerance, angular_tolerance=0.1)
        except TypeError:
            export_stl(part, p)
        return voxelize_mesh(p, envelope_mm, h, origin)


def build123d_to_step(part, path: str) -> str:
    from build123d import export_step

    export_step(part, path)
    return path
