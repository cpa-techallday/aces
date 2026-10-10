"""Geometric multigrid solver for the voxel FEA (feasd.py VoxelFEA grids).

Two compounding wins over the plain Jacobi-PCG path:

1. **Active-DOF compression.** The fictitious-domain approach keeps every void element
   in the system with Ee = emin (~1e-9). That makes K enormously ill-conditioned
   (cond ~ 1e9 even before geometry), which is why the big cases stalled at 20000 PCG
   iterations. Here the operator is assembled over *solid elements only* and every node
   touched only by void is removed from the system. Solid fraction on the HALO-X
   skeleton was 1.9 %, so 103 M dof collapse to ~2 M, and the void stiffness contrast
   disappears entirely.

2. **Geometric multigrid V-cycle preconditioner.** Solid-only elasticity on slender
   voxel structures still has condition number ~ (L/h)^2; PCG needs ~sqrt(kappa)
   iterations. A V(2,2)-cycle with damped-Jacobi smoothing and rediscretised coarse
   operators (same hex8 element, h doubled per level) makes iteration count nearly
   mesh-independent.

Transfers are trilinear prolongation / full-weighting restriction between compressed
node sets, precomputed as (n_active_fine, 8) gather indices + weights. Coarse fixed
DOFs are propagated with a 3x3x3 max-pool so coarse levels never unpick a support.

Everything runs through the same numpy/cupy dual path as fea3d.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from .fea3d import hex8_ke, scatter_add, to_numpy


# ----------------------------------------------------------------------------- level build

class _Level:
    """One multigrid level: compressed active-DOF system on a structured grid."""

    def __init__(self, fea, nx, ny, nz, h, Ee_grid, fixed_grid, xp):
        self.nx, self.ny, self.nz, self.h = int(nx), int(ny), int(nz), float(h)
        self.xp = xp
        self.dtype = fea.dtype
        Nx, Ny, Nz = self.nx + 1, self.ny + 1, self.nz + 1
        self.nshape = (Nx, Ny, Nz)
        # active elements: any stiffness
        elem_active = Ee_grid > 0.0
        self.ne = nx * ny * nz
        self.elem_active = xp.asarray(elem_active.ravel())
        self.Ee = xp.asarray(Ee_grid.ravel()[np.asarray(elem_active.ravel())],
                             dtype=self.dtype)
        # edof for active elements, full node ids first
        edof_full = fea_edof(nx, ny, nz)                      # (ne, 8) node ids, numpy int64
        enodes = edof_full[np.asarray(elem_active.ravel())]   # (ne_a, 8)
        # active nodes: touched by an active element
        node_active = np.zeros(Nx * Ny * Nz, dtype=bool)
        node_active[np.unique(enodes)] = True
        self.node_active = node_active
        self.nmap = np.full(Nx * Ny * Nz, -1, dtype=np.int64)
        self.nmap[node_active] = np.arange(int(node_active.sum()))
        self.nact = int(node_active.sum())
        self.ndof = 3 * self.nact
        # compressed edof (ne_a, 24), dof-major per node like fea3d.hex8_ke
        nmap_nodes = self.nmap[enodes]                        # (ne_a, 8)
        self.edof = xp.asarray(
            (3 * nmap_nodes[:, :, None] + np.arange(3)[None, None, :]).reshape(-1, 24),
            dtype=xp.int32 if self.ndof < 2 ** 31 else xp.int64)
        # fixed DOFs intersected with active set
        # fixed_grid is spatial [i, j, k, d] with i = x index (first axis).
        # Node id n = i + Nx*(j + Ny*k): flatten spatial grid to [n, d] via transpose.
        fg = np.asarray(fixed_grid, dtype=bool)
        if fg.shape != (Nx, Ny, Nz, 3):
            raise ValueError("fixed grid shape mismatch")
        fx = fg.transpose(2, 1, 0, 3).reshape(-1, 3)  # [n, d] with n = i + Nx*(j + Ny*k)
        self.fixed = xp.asarray(fx[node_active].reshape(-1))
        # stiffness for this voxel size
        self.KE = xp.asarray(hex8_ke(fea.nu, self.h), dtype=self.dtype)
        self.KEdiag = xp.diag(hex8_ke(fea.nu, self.h)).astype(self.dtype)
        self.diag = self._diag_K()

    def _diag_K(self):
        xp = self.xp
        diag = xp.zeros(self.ndof, dtype=self.dtype)
        scatter_add(xp, diag, self.edof.ravel(),
                    (self.Ee[:, None] * self.KEdiag[None, :]).ravel())
        diag[self.fixed] = 1.0
        return diag

    def apply_K(self, u):
        xp = self.xp
        fe = (u[self.edof] @ self.KE) * self.Ee[:, None]
        out = xp.zeros(self.ndof, dtype=self.dtype)
        scatter_add(xp, out, self.edof.ravel(), fe.ravel())
        out[self.fixed] = u[self.fixed]
        return out


def fea_edof(nx, ny, nz):
    """(ne, 8) element->node ids, matching fea3d's nid ordering."""
    i, j, k = np.meshgrid(np.arange(nx), np.arange(ny), np.arange(nz), indexing="ij")
    i, j, k = i.ravel(), j.ravel(), k.ravel()

    def nid(a, b, c):
        return a + (nx + 1) * (b + (ny + 1) * c)

    return np.stack([nid(i, j, k), nid(i + 1, j, k), nid(i + 1, j + 1, k), nid(i, j + 1, k),
                     nid(i, j, k + 1), nid(i + 1, j, k + 1), nid(i + 1, j + 1, k + 1),
                     nid(i, j + 1, k + 1)], axis=1).astype(np.int64)


def coarsen_Ee(Ee_grid):
    """Mean of the 2x2x2 children; pads one void slice on odd axes."""
    nx, ny, nz = Ee_grid.shape
    px, py, pz = nx % 2, ny % 2, nz % 2
    if px or py or pz:
        Ee_grid = np.pad(Ee_grid, ((0, px), (0, py), (0, pz)))
    nx2, ny2, nz2 = Ee_grid.shape[0] // 2, Ee_grid.shape[1] // 2, Ee_grid.shape[2] // 2
    c = Ee_grid.reshape(nx2, 2, ny2, 2, nz2, 2).mean(axis=(1, 3, 5))
    return np.ascontiguousarray(c)


def coarsen_fixed(fixed_grid, ncx, ncy, ncz):
    """Coarse fixed node = any fixed fine node in its 3x3x3 neighbourhood
    (sampled at even indices after a max-pool), per dof."""
    Nx, Ny, Nz, _ = fixed_grid.shape
    pad = np.pad(fixed_grid, ((1, 1), (1, 1), (1, 1), (0, 0)))
    pooled = np.zeros_like(fixed_grid)
    for di in range(3):
        for dj in range(3):
            for dk in range(3):
                pooled |= pad[di:di + Nx, dj:dj + Ny, dk:dk + Nz]
    # coarse node (I,J,K) <- fine node (2I,2J,2K); fine grid may be one node longer
    out = np.zeros((ncx + 1, ncy + 1, ncz + 1, 3), dtype=bool)
    sx = min(2 * ncx + 1, Nx)
    sy = min(2 * ncy + 1, Ny)
    sz = min(2 * ncz + 1, Nz)
    out[: (sx + 1) // 2, : (sy + 1) // 2, : (sz + 1) // 2] = pooled[0:sx:2, 0:sy:2, 0:sz:2]
    return out


def build_transfer(fine: _Level, coarse: _Level, xp):
    """Trilinear prolongation from coarse compressed nodes to fine compressed nodes.

    Returns (idx, w): (nact_fine, 8) compact coarse node ids and weights.
    """
    Nfx, Nfy, Nfz = fine.nshape
    ncx, ncy, ncz = coarse.nx, coarse.ny, coarse.nz
    fnode = np.flatnonzero(fine.node_active)
    fi = fnode % Nfx
    fj = (fnode // Nfx) % Nfy
    fk = fnode // (Nfx * Nfy)
    ci = np.minimum(fi // 2, ncx - 1)
    cj = np.minimum(fj // 2, ncy - 1)
    ck = np.minimum(fk // 2, ncz - 1)
    tx = np.clip((fi - 2 * ci) / 2.0, 0.0, 1.0)
    ty = np.clip((fj - 2 * cj) / 2.0, 0.0, 1.0)
    tz = np.clip((fk - 2 * ck) / 2.0, 0.0, 1.0)
    Ncx, Ncy = coarse.nshape[0], coarse.nshape[1]
    idx = np.zeros((fine.nact, 8), dtype=np.int64)
    w = np.zeros((fine.nact, 8), dtype=np.float64)
    n = 0
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                gx = ci + dx
                gy = cj + dy
                gz = ck + dz
                full = gx + Ncx * (gy + Ncy * gz)
                idx[:, n] = coarse.nmap[full]
                w[:, n] = (tx if dx else 1 - tx) * (ty if dy else 1 - ty) * (tz if dz else 1 - tz)
                n += 1
    # zero-weight corners may sit on inactive coarse nodes; point them at the primary
    # corner (always active) so the gather is safe — their weight is zero anyway
    primary = idx[:, 0].copy()
    for n in range(8):
        dead = (idx[:, n] < 0)
        idx[dead, n] = primary[dead]
    assert (idx[w > 1e-14] >= 0).all(), "positive-weight corner on inactive coarse node"
    return (xp.asarray(idx, dtype=xp.int32 if coarse.nact < 2 ** 31 else xp.int64),
            xp.asarray(w, dtype=fine.dtype))


# ----------------------------------------------------------------------------- solver

class MGSolver:
    """V-cycle preconditioned CG over a hierarchy of compressed voxel levels."""

    def __init__(self, fea, Ee_full_grid, max_levels: int = 4, min_dim: int = 6,
                 omega: float = None, smooth: int = 2, coarse_iters: int = 300,
                 coarse_tol: float = 1e-2):
        xp = fea.xp
        self.xp = xp
        self.dtype = fea.dtype
        self.smooth = int(smooth)
        self.coarse_iters, self.coarse_tol = int(coarse_iters), float(coarse_tol)
        fixed_flat = np.asarray(to_numpy(fea.fixed)).reshape(-1, 3)
        # node id n = i + (nx+1)*(j + (ny+1)*k): reshape puts k first, then transpose
        fixed_grid = np.stack([
            fixed_flat[:, d].reshape(fea.nz + 1, fea.ny + 1, fea.nx + 1).transpose(2, 1, 0)
            for d in range(3)], axis=-1)
        self.levels: List[_Level] = []
        self.transfers: List[Tuple[object, object]] = []
        Ee = np.asarray(Ee_full_grid, dtype=float)
        fx = fixed_grid
        nx, ny, nz = fea.nx, fea.ny, fea.nz
        h = fea.h
        while True:
            lvl = _Level(fea, nx, ny, nz, h, Ee, fx, xp)
            self.levels.append(lvl)
            if min(nx, ny, nz) <= min_dim or len(self.levels) >= max_levels:
                break
            Ee_c = coarsen_Ee(Ee)
            nxc, nyc, nzc = Ee_c.shape
            fx_c = coarsen_fixed(fx, nxc, nyc, nzc)
            Ee, fx = Ee_c, fx_c
            nx, ny, nz, h = nxc, nyc, nzc, h * 2.0
        # transfers between consecutive levels
        self.transfers = [build_transfer(self.levels[l], self.levels[l + 1], xp)
                          for l in range(len(self.levels) - 1)]
        # per-level Jacobi damping: omega = 1.8 / lambda_max(D^-1 K) by power iteration
        # (hex8 elasticity has lambda_max ~ 3.1, so a fixed 2/3 omega AMPLIFIES the top
        # modes; measured on the 64x8x8 beam test)
        self.omegas = []
        for lvl in self.levels:
            if omega is not None:
                self.omegas.append(float(omega))
                continue
            v = xp.asarray(np.random.default_rng(0).standard_normal(lvl.ndof),
                           dtype=self.dtype)
            v = xp_where_fixed_zero(xp, v, lvl.fixed)
            v = v / float(xp.sqrt(v @ v))
            lam = 0.0
            for _ in range(20):
                w = lvl.apply_K(v) / lvl.diag
                w = xp_where_fixed_zero(xp, w, lvl.fixed)
                nrm = float(xp.sqrt(w @ w))
                if nrm == 0.0:
                    break
                v = w / nrm
                lam = nrm
            self.omegas.append(min(1.8 / lam, 1.0) if lam > 0 else 0.5)

    # -- transfer ops --------------------------------------------------------------
    def prolong(self, l, uc):
        """Coarse compressed (level l+1) -> fine compressed (level l)."""
        xp = self.xp
        idx, w = self.transfers[l]
        out = xp.zeros(self.levels[l].ndof, dtype=self.dtype)
        ucn = uc.reshape(-1, 3)
        node_ids = xp.arange(self.levels[l].nact)
        for d in range(3):
            out[3 * node_ids + d] = (ucn[idx, d] * w).sum(axis=1)
        return out

    def restrict(self, l, rf):
        """Fine compressed (level l) residual -> coarse compressed (level l+1)."""
        xp = self.xp
        idx, w = self.transfers[l]
        out = xp.zeros(self.levels[l + 1].ndof, dtype=self.dtype)
        rfn = rf.reshape(-1, 3)
        for d in range(3):
            contrib = rfn[:, d][:, None] * w
            scatter_add(xp, out, (3 * idx + d).ravel(), contrib.ravel())
        return out

    # -- smoothing -----------------------------------------------------------------
    def _jacobi(self, lvl: _Level, b, x, sweeps, omega):
        for _ in range(sweeps):
            r = b - lvl.apply_K(x)
            x = x + omega * r / lvl.diag
            x = xp_where_fixed_zero(self.xp, x, lvl.fixed)
        return x

    def _vcycle(self, l, b, x):
        lvl = self.levels[l]
        b = xp_where_fixed_zero(self.xp, b, lvl.fixed)  # corrections never move supports
        x = self._jacobi(lvl, b, x, self.smooth, self.omegas[l])
        if l < len(self.levels) - 1:
            r = b - lvl.apply_K(x)
            rc = self.restrict(l, r)
            ec = self.xp.zeros(self.levels[l + 1].ndof, dtype=self.dtype)
            ec = self._vcycle(l + 1, rc, ec)
            x = x + self.prolong(l, ec)
        else:
            x = self._coarse_solve(lvl, b, x)
            return xp_where_fixed_zero(self.xp, x, lvl.fixed)
        x = self._jacobi(lvl, b, x, self.smooth, self.omegas[l])
        return x

    def _coarse_solve(self, lvl: _Level, b, x):
        """Plain Jacobi-PCG on the coarsest compressed level.

        Runs a FIXED number of iterations with no early exit: the preconditioner
        must be the same linear operator every call, otherwise the outer CG
        recurrence loses orthogonality and diverges after first converging
        (observed: relres 5e-9 at iter 21 -> 6e15 at iter 300 on the void beam).
        """
        xp = self.xp
        b = xp_where_fixed_zero(xp, b, lvl.fixed)
        r = b - lvl.apply_K(x)
        r = xp_where_fixed_zero(xp, r, lvl.fixed)
        z = r / lvl.diag
        p = z.copy()
        rz = float(r @ z)
        for _ in range(self.coarse_iters):
            Ap = lvl.apply_K(p)
            pAp = float(p @ Ap)
            if pAp <= 0.0:
                break
            alpha = rz / pAp
            x = x + alpha * p
            r = r - alpha * Ap
            z = r / lvl.diag
            z = xp_where_fixed_zero(xp, z, lvl.fixed)
            rz_new = float(r @ z)
            if rz_new <= 1e-30 * abs(rz) if rz else rz_new == 0.0:
                rz = rz_new
                break
            p = z + (rz_new / rz) * p
            rz = rz_new
        return xp_where_fixed_zero(xp, x, lvl.fixed)

    def precondition(self, r):
        z = self.xp.zeros(self.levels[0].ndof, dtype=self.dtype)
        return self._vcycle(0, r, z)

    # -- outer CG ------------------------------------------------------------------
    def solve(self, b_full, tol: float = 1e-8, maxiter: int = 200,
              progress=None) -> Tuple[object, int, float, Dict]:
        """MG-PCG on the compressed system. b_full is the FULL-length load vector
        (fea.ndof). Returns (u_full, iterations, true_relres, info)."""
        xp = self.xp
        lvl0 = self.levels[0]
        act = xp.asarray(lvl0.node_active)
        b3 = b_full.reshape(-1, 3)[act].reshape(-1).copy()
        b3[lvl0.fixed] = 0.0
        bnorm = float(xp.sqrt(b3 @ b3))
        info = {"levels": [{"nx": l.nx, "ny": l.ny, "nz": l.nz, "h": l.h,
                            "active_dof": int(l.ndof)} for l in self.levels]}
        if bnorm == 0.0:
            return xp.zeros_like(b_full), 0, 0.0, info
        x = xp.zeros(lvl0.ndof, dtype=self.dtype)
        r = b3 - lvl0.apply_K(x)
        z = self.precondition(r)
        p = z.copy()
        rz = float(r @ z)
        rn = float(xp.sqrt(r @ r))
        it = 0
        # finite-precision CG can lose orthogonality and diverge once it has
        # converged; keep the best iterate and stop if we drift away from it
        x_best, rn_best, diverging = x.copy(), rn, 0
        while it < maxiter and rn > tol * bnorm:
            Ap = lvl0.apply_K(p)
            pAp = float(p @ Ap)
            if pAp <= 0.0:
                break
            alpha = rz / pAp
            x = x + alpha * p
            it += 1
            r = b3 - lvl0.apply_K(x)          # true residual every iteration
            rn = float(xp.sqrt(r @ r))
            if progress:
                progress(it, rn / bnorm)
            if rn < rn_best:
                x_best, rn_best, diverging = x.copy(), rn, 0
            else:
                diverging += 1
            if rn <= tol * bnorm:
                break
            if rn > 100.0 * rn_best and diverging >= 5:
                # recurrence has destabilised past the best iterate: stop
                x = x_best
                rn = rn_best
                break
            z = self.precondition(r)
            rz_new = float(r @ z)
            p = z + (rz_new / rz) * p
            rz = rz_new
        if rn > rn_best:                       # ended mid-drift: return best
            x, rn = x_best, rn_best
        # expand back to the full grid
        u_full = xp.zeros_like(b_full)
        u_full.reshape(-1, 3)[act] = x.reshape(-1, 3)
        return u_full, it, rn / bnorm, info


def xp_where_fixed_zero(xp, x, fixed):
    x = x.copy()
    x[fixed] = 0.0
    return x


def solve_mg(fea, Ee_solid, F, tol: float = 1e-8, maxiter: int = 200,
             max_levels: int = 4, progress=None):
    """Solve K u = F on the solid-only compressed system with MG-PCG.

    fea: VoxelFEA (grid, supports). Ee_solid: full-length per-element stiffness with
    EXACTLY 0 in void (e.g. mask.astype(float)); void elements are dropped, not
    softened. F: full-length load vector. Returns (u_full, iters, true_relres, info).
    """
    Ee_grid = np.asarray(to_numpy(Ee_solid)).reshape(fea.nx, fea.ny, fea.nz)
    mg = MGSolver(fea, Ee_grid, max_levels=max_levels)
    return mg.solve(F, tol=tol, maxiter=maxiter, progress=progress)
