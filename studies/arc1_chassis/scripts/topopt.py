"""ARC-1 :: 2D SIMP topology optimiser.

Fusion's Generative Design workspace has no API surface - you cannot create or
run a study from a script - so this is the generative engine. It is the real
thing: SIMP material interpolation, density filtering, optimality-criteria
update, multiple load cases, passive solid/void regions.

A rocker is a plate: planar topology optimisation is not a simplification here,
it is how the part is actually made. Out-of-plane cases are handled by the
plate thickness, checked separately.

Reusable: give it a domain, hardpoints, loads and supports in millimetres.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import spsolve


def _KE(nu: float) -> np.ndarray:
    k = np.array([0.5 - nu / 6, 0.125 + nu / 8, -0.25 - nu / 12,
                  -0.125 + 3 * nu / 8, -0.25 + nu / 12, -0.125 - nu / 8,
                  nu / 6, 0.125 - 3 * nu / 8])
    idx = [[0, 1, 2, 3, 4, 5, 6, 7],
           [1, 0, 7, 6, 5, 4, 3, 2],
           [2, 7, 0, 5, 6, 3, 4, 1],
           [3, 6, 5, 0, 7, 2, 1, 4],
           [4, 5, 6, 7, 0, 1, 2, 3],
           [5, 4, 3, 2, 1, 0, 7, 6],
           [6, 3, 4, 1, 2, 7, 0, 5],
           [7, 2, 1, 4, 3, 6, 5, 0]]
    return k[np.array(idx)] / (1 - nu ** 2)


class Domain:
    """Rectangular design space meshed with square Q4 elements.

    Physical (x, y) in mm with y UP. Element (i, j): i along x, j down from
    the top, matching the classic topopt indexing.
    """

    def __init__(self, x0, y0, x1, y1, elem_mm=1.5):
        self.x0, self.y0 = float(x0), float(y0)
        self.nelx = max(4, int(round((x1 - x0) / elem_mm)))
        self.nely = max(4, int(round((y1 - y0) / elem_mm)))
        self.h = elem_mm
        self.ndof = 2 * (self.nelx + 1) * (self.nely + 1)

    # --- indexing -----------------------------------------------------
    def node(self, i, j) -> int:
        return i * (self.nely + 1) + j

    def ij_of(self, x, y):
        i = (x - self.x0) / self.h
        j = self.nely - (y - self.y0) / self.h
        return i, j

    def nearest_node(self, x, y) -> int:
        i, j = self.ij_of(x, y)
        i = int(np.clip(round(i), 0, self.nelx))
        j = int(np.clip(round(j), 0, self.nely))
        return self.node(i, j)

    def nodes_in_disc(self, x, y, r) -> list[int]:
        return self.nodes_in_annulus(x, y, 0.0, r)

    def nodes_in_annulus(self, x, y, r_in, r_out) -> list[int]:
        """Nodes in a ring. Loads and supports belong on the BOSS, never on
        the bore: a force applied to nodes inside a passive-void bearing bore
        is being applied to nothing, and the compliance it produces swamps
        the objective and freezes the optimiser."""
        out = []
        ic, jc = self.ij_of(x, y)
        ro, ri = r_out / self.h, r_in / self.h
        for i in range(max(0, int(ic - ro - 1)), min(self.nelx, int(ic + ro + 1)) + 1):
            for j in range(max(0, int(jc - ro - 1)), min(self.nely, int(jc + ro + 1)) + 1):
                d2 = (i - ic) ** 2 + (j - jc) ** 2
                if ri * ri <= d2 <= ro * ro:
                    out.append(self.node(i, j))
        return out

    def elem_centres(self):
        i = np.arange(self.nelx) + 0.5
        j = np.arange(self.nely) + 0.5
        X = self.x0 + i * self.h
        Y = self.y0 + (self.nely - j) * self.h
        return np.meshgrid(X, Y, indexing="ij")

    def elem_mask_disc(self, x, y, r) -> np.ndarray:
        X, Y = self.elem_centres()
        return ((X - x) ** 2 + (Y - y) ** 2) <= r * r


def _edofMat(d: Domain) -> np.ndarray:
    ed = np.zeros((d.nelx * d.nely, 8), dtype=int)
    for i in range(d.nelx):
        for j in range(d.nely):
            e = i * d.nely + j
            n1 = d.node(i, j)
            n2 = d.node(i + 1, j)
            ed[e] = [2 * n1, 2 * n1 + 1, 2 * n2, 2 * n2 + 1,
                     2 * n2 + 2, 2 * n2 + 3, 2 * n1 + 2, 2 * n1 + 3]
    return ed


def _filter(d: Domain, rmin: float):
    nel = d.nelx * d.nely
    rr = rmin / d.h
    iH, jH, sH = [], [], []
    span = int(math.ceil(rr))
    for i in range(d.nelx):
        for j in range(d.nely):
            e1 = i * d.nely + j
            for i2 in range(max(i - span, 0), min(i + span + 1, d.nelx)):
                for j2 in range(max(j - span, 0), min(j + span + 1, d.nely)):
                    e2 = i2 * d.nely + j2
                    fac = rr - math.sqrt((i - i2) ** 2 + (j - j2) ** 2)
                    if fac > 0:
                        iH.append(e1)
                        jH.append(e2)
                        sH.append(fac)
    H = coo_matrix((sH, (iH, jH)), shape=(nel, nel)).tocsr()
    return H, np.asarray(H.sum(1)).ravel()


def optimise(d: Domain, load_cases, fixed_dofs, volfrac=0.4, penal=3.0,
             rmin=None, iters=80, passive_solid=None, passive_void=None,
             nu=0.3, verbose=True):
    """load_cases: list of {dof: value} force dictionaries."""
    nel = d.nelx * d.nely
    rmin = rmin or 2.6 * d.h
    KE = _KE(nu)
    ed = _edofMat(d)
    H, Hs = _filter(d, rmin)

    iK = np.kron(ed, np.ones((8, 1))).flatten().astype(int)
    jK = np.kron(ed, np.ones((1, 8))).flatten().astype(int)

    ps = np.zeros(nel, bool) if passive_solid is None else passive_solid.ravel()
    pv = np.zeros(nel, bool) if passive_void is None else passive_void.ravel()
    ps = ps & ~pv

    x = np.full(nel, volfrac)
    x[ps] = 1.0
    x[pv] = 0.0
    xPhys = x.copy()

    free = np.setdiff1d(np.arange(d.ndof), np.asarray(sorted(set(fixed_dofs))))
    F = np.zeros((d.ndof, len(load_cases)))
    for c, lc in enumerate(load_cases):
        for dof, v in lc.items():
            F[dof, c] += v

    Emin, E0 = 1e-9, 1.0
    for it in range(iters):
        sK = ((KE.flatten()[np.newaxis]).T *
              (Emin + xPhys ** penal * (E0 - Emin))).flatten(order="F")
        K = coo_matrix((sK, (iK, jK)), shape=(d.ndof, d.ndof)).tocsc()
        K = K[free, :][:, free]

        U = np.zeros((d.ndof, F.shape[1]))
        U[free, :] = spsolve(K, F[free, :]).reshape(len(free), -1)

        ce = np.zeros(nel)
        obj = 0.0
        for c in range(F.shape[1]):
            Ue = U[:, c][ed]
            cc = np.einsum("ij,jk,ik->i", Ue, KE, Ue)
            ce += cc
            obj += float(((Emin + xPhys ** penal * (E0 - Emin)) * cc).sum())
        dc = -penal * xPhys ** (penal - 1) * (E0 - Emin) * ce
        dv = np.ones(nel)

        dc = H @ (dc / Hs)
        dv = H @ (dv / Hs)

        l1, l2, move = 0.0, 1e9, 0.2
        while (l2 - l1) / max(l1 + l2, 1e-12) > 1e-4:
            lmid = 0.5 * (l1 + l2)
            xnew = np.maximum(0.0, np.maximum(
                x - move, np.minimum(1.0, np.minimum(
                    x + move, x * np.sqrt(np.maximum(-dc / dv / lmid, 0.0))))))
            xnew[ps] = 1.0
            xnew[pv] = 0.0
            xPhys = (H @ xnew) / Hs
            xPhys[ps] = 1.0
            xPhys[pv] = 0.0
            if xPhys.mean() > volfrac:
                l1 = lmid
            else:
                l2 = lmid
        change = float(np.abs(xnew - x).max())
        x = xnew
        if verbose and (it % 10 == 0 or it == iters - 1):
            print(f"  it {it:>3}  compliance {obj:>12.4g}  "
                  f"vol {xPhys.mean():.3f}  change {change:.4f}")
        if change < 0.005:
            if verbose:
                print(f"  converged at iteration {it}")
            break

    return xPhys.reshape(d.nelx, d.nely)


def contours(d: Domain, dens: np.ndarray, level=0.5):
    """Marching-squares outline of the optimised shape, in mm."""
    from skimage import measure
    field = np.pad(dens, 1, mode="constant", constant_values=0.0)
    polys = []
    for c in measure.find_contours(field, level):
        i = c[:, 0] - 1.0
        j = c[:, 1] - 1.0
        X = d.x0 + i * d.h
        Y = d.y0 + (d.nely - j) * d.h
        polys.append(np.column_stack([X, Y]))
    return polys
