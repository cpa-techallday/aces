"""Voxel (8-node hexahedral) linear-elastic FEA on a structured grid.

Matrix-free element-by-element K.u with a Jacobi-preconditioned conjugate gradient
solver. Runs on the GPU through CuPy when a CUDA device is present and falls back to
NumPy otherwise, with the same code path. Designed for SIMP topology optimisation and
for evaluating voxelised parametric parts.

Conventions
-----------
* Units: mm, N, MPa.
* Grid: nx * ny * nz cube elements of side h. Element (i, j, k) has flat index
  e = (i * ny + j) * nz + k  (C order of a (nx, ny, nz) array), so density fields are
  plain (nx, ny, nz) arrays and `.ravel()` matches the element order.
* Node n has grid position i = n % (nx+1), j = (n // (nx+1)) % (ny+1), k = n // ((nx+1)(ny+1))
  and coordinates (i, j, k) * h. DOFs are 3n, 3n+1, 3n+2 for x, y, z.
* Stiffness is assembled with E = 1; callers pass per-element relative moduli Ee
  (e.g. Emin + x^p (1 - Emin)). Real displacements are u_rel / E0 and real stresses are
  element_stress_vm(u_rel, Ee).
"""
from __future__ import annotations

from . import progress as _progress

import math
from typing import Dict, Iterable, Optional, Tuple

import numpy as np

try:  # CuPy is optional: the kernel runs on NumPy without it
    import cupy as cp
    import cupyx
    _HAVE_CUPY = True
except Exception:  # pragma: no cover
    cp = None
    cupyx = None
    _HAVE_CUPY = False


# ----------------------------------------------------------------------------- backend

def have_gpu() -> bool:
    if not _HAVE_CUPY:
        return False
    try:
        return cp.cuda.runtime.getDeviceCount() > 0
    except Exception:
        return False


def get_xp(prefer_gpu: bool = True):
    """Return cupy when a GPU is usable and preferred, else numpy."""
    return cp if (prefer_gpu and have_gpu()) else np


def device_name(xp) -> str:
    if xp is np:
        return "cpu/numpy"
    props = cp.cuda.runtime.getDeviceProperties(cp.cuda.Device().id)
    name = props["name"]
    return name.decode() if isinstance(name, bytes) else str(name)


def to_numpy(a):
    if _HAVE_CUPY and isinstance(a, cp.ndarray):
        return cp.asnumpy(a)
    return np.asarray(a)


def scatter_add(xp, out, idx, vals):
    if xp is np:
        np.add.at(out, idx, vals)
    else:
        cupyx.scatter_add(out, idx, vals)


# ----------------------------------------------------------------------------- element

_XI = np.array([[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
                [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]], dtype=float)


def iso_D(E: float, nu: float) -> np.ndarray:
    """Isotropic elasticity matrix, Voigt order xx yy zz xy yz xz, engineering shear."""
    lam = E * nu / ((1 + nu) * (1 - 2 * nu))
    mu = E / (2 * (1 + nu))
    D = np.zeros((6, 6))
    D[:3, :3] = lam
    D[0, 0] += 2 * mu
    D[1, 1] += 2 * mu
    D[2, 2] += 2 * mu
    D[3, 3] = D[4, 4] = D[5, 5] = mu
    return D


def _dN(xi: float, eta: float, zeta: float, h: float) -> np.ndarray:
    dN = np.zeros((8, 3))
    for i in range(8):
        a, b, c = _XI[i]
        dN[i, 0] = 0.125 * a * (1 + eta * b) * (1 + zeta * c)
        dN[i, 1] = 0.125 * (1 + xi * a) * b * (1 + zeta * c)
        dN[i, 2] = 0.125 * (1 + xi * a) * (1 + eta * b) * c
    return dN * (2.0 / h)  # d/dxi -> d/dx for a cube of side h


def _B(dN: np.ndarray) -> np.ndarray:
    B = np.zeros((6, 24))
    for i in range(8):
        c = 3 * i
        B[0, c] = dN[i, 0]
        B[1, c + 1] = dN[i, 1]
        B[2, c + 2] = dN[i, 2]
        B[3, c] = dN[i, 1]
        B[3, c + 1] = dN[i, 0]
        B[4, c + 1] = dN[i, 2]
        B[4, c + 2] = dN[i, 1]
        B[5, c] = dN[i, 2]
        B[5, c + 2] = dN[i, 0]
    return B


def hex8_ke(nu: float, h: float) -> np.ndarray:
    """24x24 stiffness matrix of a cube element of side h with E = 1 (2x2x2 Gauss)."""
    D = iso_D(1.0, nu)
    g = 1.0 / math.sqrt(3.0)
    detJ = (h / 2.0) ** 3
    KE = np.zeros((24, 24))
    for xi in (-g, g):
        for eta in (-g, g):
            for zeta in (-g, g):
                B = _B(_dN(xi, eta, zeta, h))
                KE += B.T @ D @ B * detJ
    return KE


def hex8_bc(h: float) -> np.ndarray:
    """Strain-displacement matrix at the element centre (6x24)."""
    return _B(_dN(0.0, 0.0, 0.0, h))


# ----------------------------------------------------------------------------- grid model

class VoxelFEA:
    """Structured hex grid with supports, load cases and a matrix-free PCG solver."""

    def __init__(self, nx: int, ny: int, nz: int, h: float, nu: float = 0.3,
                 dtype: str = "float64", prefer_gpu: bool = True):
        self.xp = get_xp(prefer_gpu)
        xp = self.xp
        self.nx, self.ny, self.nz, self.h = int(nx), int(ny), int(nz), float(h)
        self.nu = float(nu)
        self.dtype = np.dtype(dtype)
        self.ne = self.nx * self.ny * self.nz
        self.nn = (self.nx + 1) * (self.ny + 1) * (self.nz + 1)
        self.ndof = 3 * self.nn
        self.KE = xp.asarray(hex8_ke(self.nu, self.h), dtype=self.dtype)
        self.KEdiag = xp.diag(self.KE).copy()
        self.Bc = xp.asarray(hex8_bc(self.h), dtype=self.dtype)
        self.D1 = xp.asarray(iso_D(1.0, self.nu), dtype=self.dtype)
        self.edof = self._build_edof()
        self.fixed = xp.zeros(self.ndof, dtype=bool)
        self.loads: Dict[str, "xp.ndarray"] = {}

    # -- topology ------------------------------------------------------------------
    def _build_edof(self):
        xp = self.xp
        nx, ny, nz = self.nx, self.ny, self.nz
        i, j, k = xp.meshgrid(xp.arange(nx), xp.arange(ny), xp.arange(nz), indexing="ij")
        i, j, k = i.ravel(), j.ravel(), k.ravel()

        def nid(a, b, c):
            return a + (nx + 1) * (b + (ny + 1) * c)

        nodes = xp.stack([nid(i, j, k), nid(i + 1, j, k), nid(i + 1, j + 1, k), nid(i, j + 1, k),
                          nid(i, j, k + 1), nid(i + 1, j, k + 1), nid(i + 1, j + 1, k + 1), nid(i, j + 1, k + 1)],
                         axis=1)
        edof = (3 * nodes[:, :, None] + xp.arange(3)[None, None, :]).reshape(self.ne, 24)
        return edof.astype(xp.int32 if self.ndof < 2 ** 31 else xp.int64)

    def node_xyz(self):
        xp = self.xp
        n = xp.arange(self.nn)
        i = n % (self.nx + 1)
        j = (n // (self.nx + 1)) % (self.ny + 1)
        k = n // ((self.nx + 1) * (self.ny + 1))
        return xp.stack([i, j, k], axis=1).astype(self.dtype) * self.h

    def elem_xyz(self):
        xp = self.xp
        e = xp.arange(self.ne)
        k = e % self.nz
        j = (e // self.nz) % self.ny
        i = e // (self.nz * self.ny)
        return (xp.stack([i, j, k], axis=1).astype(self.dtype) + 0.5) * self.h

    # -- region selectors (mm) -----------------------------------------------------
    def nodes_in_box(self, box, tol: float = 1e-6):
        p = self.node_xyz()
        x0, y0, z0, x1, y1, z1 = [float(v) for v in box]
        return ((p[:, 0] >= x0 - tol) & (p[:, 0] <= x1 + tol) & (p[:, 1] >= y0 - tol) &
                (p[:, 1] <= y1 + tol) & (p[:, 2] >= z0 - tol) & (p[:, 2] <= z1 + tol))

    def elems_in_box(self, box):
        p = self.elem_xyz()
        x0, y0, z0, x1, y1, z1 = [float(v) for v in box]
        return ((p[:, 0] >= x0) & (p[:, 0] <= x1) & (p[:, 1] >= y0) & (p[:, 1] <= y1) &
                (p[:, 2] >= z0) & (p[:, 2] <= z1))

    def elems_in_cyl(self, axis: str, center, r: float, span):
        """Elements whose centre lies inside a cylinder along `axis` ('x'|'y'|'z')."""
        p = self.elem_xyz()
        ax = {"x": 0, "y": 1, "z": 2}[axis]
        others = [a for a in (0, 1, 2) if a != ax]
        d2 = (p[:, others[0]] - float(center[0])) ** 2 + (p[:, others[1]] - float(center[1])) ** 2
        return (d2 <= float(r) ** 2) & (p[:, ax] >= float(span[0])) & (p[:, ax] <= float(span[1]))

    # -- boundary conditions -------------------------------------------------------
    def fix_box(self, box, dofs: Iterable[int] = (0, 1, 2)) -> int:
        xp = self.xp
        idx = xp.nonzero(self.nodes_in_box(box))[0]
        if int(idx.size) == 0:
            raise ValueError(f"no nodes inside support box {box}")
        for d in dofs:
            self.fixed[3 * idx + int(d)] = True
        return int(idx.size)

    def fix_mask(self, node_mask, dofs: Iterable[int] = (0, 1, 2)) -> int:
        xp = self.xp
        idx = xp.nonzero(node_mask)[0]
        for d in dofs:
            self.fixed[3 * idx + int(d)] = True
        return int(idx.size)

    def add_load_box(self, case: str, box, force) -> int:
        """Spread a total force vector (N) evenly over the nodes inside the box."""
        xp = self.xp
        idx = xp.nonzero(self.nodes_in_box(box))[0]
        n = int(idx.size)
        if n == 0:
            raise ValueError(f"no nodes inside load box {box}")
        F = self.loads.setdefault(case, xp.zeros(self.ndof, dtype=self.dtype))
        for d, fd in enumerate(force):
            if float(fd) != 0.0:
                F[3 * idx + d] += self.dtype.type(float(fd) / n)
        return n

    def add_traction_box(self, case: str, box, force, axis: str) -> int:
        """Consistent nodal loads for a uniform traction on a grid-aligned face.

        `box` is a planar region on a face normal to `axis` ('x'|'y'|'z'); the total
        force (N) is split equally over the element faces inside it and each element face
        hands a quarter to its four nodes, so corner and edge nodes get the H8-consistent
        share. Returns the number of element faces loaded.
        """
        xp = self.xp
        ax = {"x": 0, "y": 1, "z": 2}[axis]
        x0, y0, z0, x1, y1, z1 = [float(v) for v in box]
        lo = (x0, y0, z0)
        hi = (x1, y1, z1)
        plane = lo[ax]
        p = self.elem_xyz()
        others = [a for a in (0, 1, 2) if a != ax]
        inplane = ((p[:, others[0]] >= lo[others[0]]) & (p[:, others[0]] <= hi[others[0]]) &
                   (p[:, others[1]] >= lo[others[1]]) & (p[:, others[1]] <= hi[others[1]]))
        touching = xp.abs(xp.abs(p[:, ax] - plane) - 0.5 * self.h) < 1e-6 * self.h
        elems = xp.nonzero(inplane & touching)[0]
        nf = int(elems.size)
        if nf == 0:
            raise ValueError(f"no element faces on the {axis}={plane} plane inside {box}")
        enodes = self.edof[elems][:, 0::3] // 3          # (nf, 8) node ids of each element
        ncoord = self.node_xyz()[enodes.ravel()][:, ax].reshape(nf, 8)
        onface = xp.abs(ncoord - plane) < 1e-6 * self.h   # the 4 nodes of each element on the plane
        F = self.loads.setdefault(case, xp.zeros(self.ndof, dtype=self.dtype))
        share = self.dtype.type(1.0 / (4.0 * nf))
        nodes = enodes[onface]                             # (4 nf,) with repeats for shared nodes
        for d, fd in enumerate(force):
            if float(fd) != 0.0:
                scatter_add(xp, F, 3 * nodes + d, xp.full(nodes.shape, float(fd) * share, dtype=self.dtype))
        return nf

    # -- operators -----------------------------------------------------------------
    def apply_K(self, u, Ee):
        """Matrix-free K(Ee) . u with identity rows at fixed DOFs."""
        xp = self.xp
        fe = (u[self.edof] @ self.KE) * Ee[:, None]
        out = xp.zeros(self.ndof, dtype=self.dtype)
        scatter_add(xp, out, self.edof.ravel(), fe.ravel())
        out[self.fixed] = u[self.fixed]
        return out

    def diag_K(self, Ee):
        xp = self.xp
        diag = xp.zeros(self.ndof, dtype=self.dtype)
        scatter_add(xp, diag, self.edof.ravel(), (Ee[:, None] * self.KEdiag[None, :]).ravel())
        diag[self.fixed] = 1.0
        return diag

    def solve(self, Ee, F, u0=None, tol: float = 1e-8, maxiter: int = 5000,
              replace_every: int = 100) -> Tuple["xp.ndarray", int, float]:
        """Jacobi-PCG for K(Ee) u = F. Returns (u, iterations, TRUE relative residual).

        The recurrence residual of plain CG drifts from the true residual b - K u in finite
        precision (measured: it claimed 1e-9 while the true residual was 0.6 in float32 on a
        slender beam). Every `replace_every` iterations the residual is recomputed from u, and
        convergence is only accepted when the true residual passes the tolerance.
        """
        xp = self.xp
        diag = self.diag_K(Ee)
        b = F.copy()
        b[self.fixed] = 0.0
        u = u0.copy() if u0 is not None else xp.zeros(self.ndof, dtype=self.dtype)
        u[self.fixed] = 0.0
        bnorm = float(xp.sqrt(b @ b))
        if bnorm == 0.0:
            return u, 0, 0.0
        r = b - self.apply_K(u, Ee)
        z = r / diag
        p = z.copy()
        rz = float(r @ z)
        rn = float(xp.sqrt(r @ r))
        it = 0
        while it < maxiter:
            if rn <= tol * bnorm:
                r = b - self.apply_K(u, Ee)          # verify with the true residual
                rn = float(xp.sqrt(r @ r))
                if rn <= tol * bnorm:
                    break
                z = r / diag                          # not really converged: restart the recurrence
                p = z.copy()
                rz = float(r @ z)
            Ap = self.apply_K(p, Ee)
            pAp = float(p @ Ap)
            if pAp <= 0.0:
                break
            alpha = rz / pAp
            u += alpha * p
            it += 1
            if it % replace_every == 0:
                r = b - self.apply_K(u, Ee)          # residual replacement
                if _progress.enabled():
                    _progress.report("pcg", step=it, total=maxiter, relres=float(xp.sqrt(r @ r)) / bnorm, tol=tol, dof=int(self.ndof))
            else:
                r -= alpha * Ap
            z = r / diag
            rz_new = float(r @ z)
            p = z + (rz_new / rz) * p
            rz = rz_new
            rn = float(xp.sqrt(r @ r))
        return u, it, rn / bnorm

    # -- post-processing -----------------------------------------------------------
    def element_compliance(self, u):
        """Per-element u_e^T KE u_e (multiply by Ee for strain energy * 2)."""
        xp = self.xp
        ue = u[self.edof]
        return xp.einsum("ij,ij->i", ue @ self.KE, ue)

    def element_stress_vm(self, u, Ee):
        """Element-centre von Mises stress (MPa) for relative solution u and moduli Ee (E0 units)."""
        xp = self.xp
        ue = u[self.edof]
        eps = ue @ self.Bc.T                 # (ne, 6) strains
        sig = (eps @ self.D1.T) * Ee[:, None]  # (ne, 6) stresses, E0-scaled in Ee
        sxx, syy, szz, sxy, syz, sxz = (sig[:, i] for i in range(6))
        return xp.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
                       + 3.0 * (sxy ** 2 + syz ** 2 + sxz ** 2))

    def max_displacement(self, u):
        xp = self.xp
        return float(xp.sqrt((u.reshape(-1, 3) ** 2).sum(axis=1)).max())


def self_test(verbose: bool = True) -> bool:
    """Element checks: symmetry, rigid-body null space, and a uniaxial patch test."""
    KE = hex8_ke(0.3, 2.0)
    sym = np.allclose(KE, KE.T, atol=1e-12)
    rigid = np.zeros(24)
    rigid[0::3] = 1.0
    null = np.allclose(KE @ rigid, 0.0, atol=1e-12)
    ok = sym and null
    if verbose:
        print(f"KE symmetric: {sym}  rigid-body null space: {null}")
    # uniaxial bar: 10 x 2 x 2 elements, fixed x=0 face, 100 N along x on x=L face
    fea = VoxelFEA(10, 2, 2, 1.0, nu=0.3, dtype="float64", prefer_gpu=False)
    fea.fix_box([0, 0, 0, 0, 2, 2], dofs=(0,))
    fea.fix_box([0, 0, 0, 10, 0, 2], dofs=(1,))  # symmetry y
    fea.fix_box([0, 0, 0, 10, 2, 0], dofs=(2,))  # symmetry z
    nf = fea.add_traction_box("x", [10, 0, 0, 10, 2, 2], [100.0, 0, 0], axis="x")
    Ee = np.ones(fea.ne)
    u, it, res = fea.solve(Ee, fea.loads["x"], tol=1e-12)
    E0 = 200000.0
    ux = u[0::3] / E0
    tip = ux[fea.nodes_in_box([10, 0, 0, 10, 2, 2])]
    exact = 100.0 * 10.0 / (4.0 * E0)  # F L / (A E), A = 2x2 mm^2
    bar_ok = bool(np.allclose(tip, exact, rtol=1e-6))
    vm = fea.element_stress_vm(u, Ee)
    stress_ok = bool(np.allclose(vm, 100.0 / 4.0, rtol=1e-6))  # uniform 25 MPa
    if verbose:
        print(f"bar: {nf} loaded faces, tip displacement {tip.min():.6e}..{tip.max():.6e} mm vs exact {exact:.6e} mm "
              f"({it} PCG iters): {'OK' if bar_ok else 'FAIL'}; von Mises {vm.min():.4f}..{vm.max():.4f} MPa vs 25: "
              f"{'OK' if stress_ok else 'FAIL'}")
    return ok and bar_ok and stress_ok


if __name__ == "__main__":
    raise SystemExit(0 if self_test() else 1)
