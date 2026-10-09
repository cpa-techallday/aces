# Voxel hex-8 FEM for TPMS unit cells under internal pressure (H2 core study).
#
# Matrix-free PCG on GPU (cupy) with Jacobi preconditioner; no global assembly.
#   - one trilinear hex-8 element per solid voxel, 2x2x2 Gauss integration
#   - symmetry BCs (u_n = 0) on all 6 faces of the unit-cell box
#   - pressure traction on solid faces exposed to the pressurised void network:
#     F = -p * h^2 * o (o = unit offset solid->void), lumped h^2/4 per face node
#   - von Mises recovered at element centres
# Selftest: Lame thick-walled cylinder (plane-strain caps), inner-surface pressure.
# Study tier, NOT VERIFIED; audit M8/H3/H4 language applies.
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

# ---------------------------------------------------------------- element library
_GP = 1.0 / math.sqrt(3.0)
GAUSS = [(s * _GP, t * _GP, u * _GP) for s in (-1, 1) for t in (-1, 1) for u in (-1, 1)]
SIGN = [(s, t, u) for s in (-1, 1) for t in (-1, 1) for u in (-1, 1)]  # node signs


def shape_nat(xi, et, ze):
    """dN/d(xi,eta,zeta) for the 8 trilinear nodes, rows in SIGN order."""
    dN = np.zeros((8, 3))
    for a, (s, t, u) in enumerate(SIGN):
        dN[a, 0] = 0.125 * s * (1 + t * et) * (1 + u * ze)
        dN[a, 1] = 0.125 * t * (1 + s * xi) * (1 + u * ze)
        dN[a, 2] = 0.125 * u * (1 + s * xi) * (1 + t * et)
    return dN


def elasticity_D(E, nu):
    c = E / ((1 + nu) * (1 - 2 * nu))
    D = np.zeros((6, 6))
    D[0, 0] = D[1, 1] = D[2, 2] = c * (1 - nu)
    D[0, 1] = D[0, 2] = D[1, 0] = D[1, 2] = D[2, 0] = D[2, 1] = c * nu
    D[3, 3] = D[4, 4] = D[5, 5] = c * (1 - 2 * nu) / 2
    return D


def B_matrix(dN, h):
    """6x24 strain-displacement matrix; hex node coords are unit voxels so
    J = diag(h/2) and det J = (h/2)^3."""
    dX = dN / (h / 2.0)  # dN/dx
    B = np.zeros((6, 24))
    for a in range(8):
        B[0, 3 * a] = dX[a, 0]
        B[1, 3 * a + 1] = dX[a, 1]
        B[2, 3 * a + 2] = dX[a, 2]
        B[3, 3 * a] = dX[a, 1]
        B[3, 3 * a + 1] = dX[a, 0]
        B[4, 3 * a + 1] = dX[a, 2]
        B[4, 3 * a + 2] = dX[a, 1]
        B[5, 3 * a] = dX[a, 2]
        B[5, 3 * a + 2] = dX[a, 0]
    return B


def element_stiffness(E, nu, h=1.0):
    D = elasticity_D(E, nu)
    Ke = np.zeros((24, 24))
    detJ = (h / 2.0) ** 3
    for gp in GAUSS:
        B = B_matrix(shape_nat(*gp), h)
        Ke += B.T @ D @ B * detJ
    return Ke


# ---------------------------------------------------------------- model assembly
def assemble_voxel_model(solid: np.ndarray):
    """Active-node map and per-element DOF table for a solid voxel mask."""
    nx, ny, nz = solid.shape
    nnx, nny, nnz = nx + 1, ny + 1, nz + 1
    eidx = np.argwhere(solid)                      # (E, 3) voxel indices
    # corner node grid indices for each element
    # corner order MUST match shape_nat's SIGN order (zeta fastest, xi slowest):
    # (-1,-1,-1), (-1,-1,+1), (-1,+1,-1), ..., (+1,+1,+1)
    corners = []
    for di, dj, dk in ((0, 0, 0), (0, 0, 1), (0, 1, 0), (0, 1, 1),
                       (1, 0, 0), (1, 0, 1), (1, 1, 0), (1, 1, 1)):
        corners.append((eidx[:, 0] + di) * nny * nnz + (eidx[:, 1] + dj) * nnz
                       + (eidx[:, 2] + dk))
    nodes_of_e = np.stack(corners, axis=1)         # (E, 8) grid-node ids
    active = np.unique(nodes_of_e)
    nodemap = -np.ones(nnx * nny * nnz, dtype=np.int64)
    nodemap[active] = np.arange(active.size)
    enodes = nodemap[nodes_of_e]                   # (E, 8) active ids
    edof = np.repeat(enodes * 3, 3, axis=1)
    edof += np.tile(np.arange(3), 8)[None, :]
    return eidx, active, nodemap, edof.astype(np.int32)


def symmetry_bc_mask(shape, active: np.ndarray):
    """DOFs fixed by symmetry on all 6 box faces (u_n = 0)."""
    nx, ny, nz = shape
    nny, nnz = ny + 1, nz + 1
    fixed = np.zeros(active.size * 3, dtype=bool)
    i = active // (nny * nnz)
    j = (active // nnz) % nny
    k = active % nnz
    for arr, lim, dof in ((i, nx, 0), (j, ny, 1), (k, nz, 2)):
        m = (arr == 0) | (arr == lim)
        fixed[np.where(m)[0] * 3 + dof] = True
    return fixed


def pressure_forces(solid: np.ndarray, void_p: np.ndarray, p: float,
                    nodemap: np.ndarray, nactive: int):
    """Lumped nodal forces from pressure p acting on solid faces exposed to the
    pressurised void mask. F = -p h^2 o (o = unit offset solid -> void)."""
    F = np.zeros(nactive * 3)
    nx, ny, nz = solid.shape
    nny, nnz = ny + 1, nz + 1
    # face node local offsets (grid) for each of the 6 face directions
    face_nodes = {
        (1, 0, 0): [(1, 0, 0), (1, 1, 0), (1, 0, 1), (1, 1, 1)],
        (-1, 0, 0): [(0, 0, 0), (0, 1, 0), (0, 0, 1), (0, 1, 1)],
        (0, 1, 0): [(0, 1, 0), (1, 1, 0), (0, 1, 1), (1, 1, 1)],
        (0, -1, 0): [(0, 0, 0), (1, 0, 0), (0, 0, 1), (1, 0, 1)],
        (0, 0, 1): [(0, 0, 1), (1, 0, 1), (0, 1, 1), (1, 1, 1)],
        (0, 0, -1): [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)],
    }
    for (di, dj, dk), corners in face_nodes.items():
        shifted = np.roll(np.roll(np.roll(void_p, di, 0), dj, 1), dk, 2)
        # np.roll(void_p, +d)[i] = void_p[i-d]: 'm' is true when the void neighbour
        # is at offset -d from the solid voxel; force points away from the void = +d
        m = solid & shifted
        # kill wrap-around artefacts from np.roll
        for ax, d in enumerate((di, dj, dk)):
            if d == 1:      # neighbour at -1 -> index 0 wraps
                sl = [slice(None)] * 3; sl[ax] = 0; m[tuple(sl)] = False
            elif d == -1:   # neighbour at +1 -> last index wraps
                sl = [slice(None)] * 3; sl[ax] = -1; m[tuple(sl)] = False
        idx = np.argwhere(m)
        if idx.size == 0:
            continue
        force_dir = np.array([di, dj, dk], dtype=float) * p   # away from void at -d
        for ci, cj, ck in corners:
            gnode = ((idx[:, 0] + ci) * nny * nnz + (idx[:, 1] + cj) * nnz
                     + (idx[:, 2] + ck))
            an = nodemap[gnode]
            for dof in range(3):
                np.add.at(F, an * 3 + dof, force_dir[dof] * 0.25)
    return F


# ---------------------------------------------------------------- GPU solver
def solve_pcg(edof, Ke, F, fixed, xp):
    import cupy as cp
    E = edof.shape[0]
    ed = cp.asarray(edof)
    Ke_g = cp.asarray(Ke)
    F_g = cp.asarray(F)
    fixed_g = cp.asarray(fixed)

    # Jacobi diagonal via scatter of Ke diagonal
    ndof = F_g.shape[0]
    diag = cp.zeros(ndof)
    kd = cp.diag(Ke_g)
    cp.add.at(diag, ed.ravel(), cp.broadcast_to(kd, (E, 24)).ravel())
    diag = cp.where(diag == 0, 1.0, diag)

    def matvec(x):
        y = cp.zeros_like(x)
        xe = x[ed]                                   # (E, 24)
        ye = (xe[:, :, None] * Ke_g[None]).sum(axis=1)   # cublas-free matvec
        cp.add.at(y, ed.ravel(), ye.ravel())
        y[fixed_g] = 0.0
        return y

    def _dot(a, b_):
        return float((a * b_).sum())          # cublas-free reductions

    def _norm(a):
        return float(cp.sqrt((a * a).sum()))

    b = F_g.copy()
    b[fixed_g] = 0.0
    x = cp.zeros_like(b)
    r = b - matvec(x)
    z = r / diag
    z[fixed_g] = 0.0
    p = z.copy()
    rz = _dot(r, z)
    bnorm = _norm(b)
    it = 0
    for it in range(1, 4001):
        Ap = matvec(p)
        alpha = rz / _dot(p, Ap)
        x += alpha * p
        r -= alpha * Ap
        if _norm(r) < 1e-9 * bnorm:
            break
        z = r / diag
        z[fixed_g] = 0.0
        rz_new = _dot(r, z)
        p = z + (rz_new / rz) * p
        rz = rz_new
    return x, it


def element_vm_stress(u, edof, eidx, E, nu, h=1.0):
    """von Mises at element centres (xi=0)."""
    D = elasticity_D(E, nu)
    B0 = B_matrix(shape_nat(0, 0, 0), h)
    ue = u[edof]                                     # (E, 24)
    sig = (B0 @ ue.T).T @ D.T                        # wait: sigma = D B u
    sig = (D @ (B0 @ ue.T)).T                        # (E, 6)
    s = sig
    vm = np.sqrt(0.5 * ((s[:, 0] - s[:, 1]) ** 2 + (s[:, 1] - s[:, 2]) ** 2
                        + (s[:, 2] - s[:, 0]) ** 2 + 6 * (s[:, 3] ** 2 + s[:, 4] ** 2
                                                          + s[:, 5] ** 2)))
    return vm, sig


# ---------------------------------------------------------------- selftest: Lame
def selftest(xp, log=print):
    """Thick-walled cylinder, inner pressure p, plane-strain caps.
    Lame: sig_theta(r_i) = p (ro^2 + ri^2)/(ro^2 - ri^2); sig_r(r_i) = -p."""
    n, ri, ro, p = 56, 10.0, 24.0, 1.0
    c = (np.arange(n) + 0.5) - n / 2
    X, Y, Z = np.meshgrid(c, c, c, indexing="ij")
    R = np.sqrt(X ** 2 + Y ** 2)
    solid = (R >= ri) & (R <= ro) & (np.abs(Z) <= 6)
    voidp = (R < ri) & (np.abs(Z) <= 6)
    E, nu = 1.0, 0.3
    eidx, active, nodemap, edof = assemble_voxel_model(solid)
    fixed = np.zeros(active.size * 3, bool)
    nn = n + 1
    ii, jj, kk = active // (nn * nn), (active // nn) % nn, active % nn  # cubic selftest only
    # plane-strain caps on the SOLID's own z boundary layers
    kmin, kmax = kk.min(), kk.max()
    fixed[np.where((kk == kmin) | (kk == kmax))[0] * 3 + 2] = True
    # in-plane rigid modes: problem is self-equilibrated; PCG from x0=0 stays in
    # range(K) (consistent singular system). No pins needed.
    F = pressure_forces(solid, voidp, p, nodemap, active.size)
    Ke = element_stiffness(E, nu)
    u, iters = solve_pcg(edof, Ke, F, fixed, xp)
    u_np = np.asarray(u.get() if hasattr(u, "get") else u)
    vm, sig = element_vm_stress(u_np, edof, eidx, E, nu)
    # hoop stress at inner wall: sigma_theta = (sx y^2 + sy x^2 - 2 txy x y)/R^2
    cx = eidx[:, 0] + 0.5 - n / 2
    cy = eidx[:, 1] + 0.5 - n / 2
    r = np.sqrt(cx ** 2 + cy ** 2)
    inner = r < ri + 1.5
    st = (sig[inner, 0] * cy[inner] ** 2 + sig[inner, 1] * cx[inner] ** 2
          - 2 * sig[inner, 3] * cx[inner] * cy[inner]) / r[inner] ** 2
    lame = p * (ro ** 2 + ri ** 2) / (ro ** 2 - ri ** 2)
    got = float(np.median(st))
    err = abs(got - lame) / lame
    log(f"Lame selftest: hoop(inner) median {got:.3f} vs analytic {lame:.3f} "
        f"(err {err*100:.1f}%), PCG iters {iters}")
    ok = err < 0.15
    log("selftest " + ("PASS" if ok else "FAIL"))
    return ok


# ---------------------------------------------------------------- H2 cell run
def run_h2_cell(xp, n=48, log=print):
    from aces.cfd.periodic import voxel_mask
    ratio = 0.1646                # H2 precooler core wall ratio (t/a)
    p_H2 = 87.5e6                 # Pa, 875 bar
    E, nu = 193e9, 0.30           # LPBF 316L as-built (EOS datasheet range 180-200)
    yield_316L = 450e6            # conservative as-built yield, Pa
    cell_m = 3.115e-3

    t0 = time.time()
    vA = voxel_mask("gyroid", ratio, n, side=0)
    vB = voxel_mask("gyroid", ratio, n, side=1)
    solid = ~(vA | vB)
    log(f"cell {cell_m*1e3:.3f} mm, t/a {ratio}, n={n}, solid fraction {solid.mean():.3f}")
    eidx, active, nodemap, edof = assemble_voxel_model(solid)
    fixed = symmetry_bc_mask(solid.shape, active)
    F = pressure_forces(solid, vB, p_H2, nodemap, active.size)
    log(f"elements {len(eidx)}, active DOFs {active.size*3}, assembly {time.time()-t0:.0f}s")
    Ke = element_stiffness(E, nu)            # unit voxel; forces already carry h^2? no:
    # traction magnitude uses physical h: F = p*h^2 per face. Ke*h scales stiffness
    # linearly in h; displacement is scale-invariant, stress is not -> use physical h
    h = cell_m / n
    F_phys = F * h ** 2
    Ke_phys = element_stiffness(E, nu, h)
    u, iters = solve_pcg(edof, Ke_phys, F_phys, fixed, xp)
    u_np = np.asarray(u.get() if hasattr(u, "get") else u)
    log(f"PCG converged in {iters} iters, {time.time()-t0:.0f}s")
    vm, sig = element_vm_stress(u_np, edof, eidx, E, nu, h)
    res = {
        "n": n, "ratio": ratio, "cell_m": cell_m, "p_H2_Pa": p_H2,
        "E_Pa": E, "nu": nu, "solid_fraction": float(solid.mean()),
        "elements": int(len(eidx)), "dofs": int(active.size * 3), "pcg_iters": iters,
        "vm_max_MPa": float(vm.max() / 1e6),
        "vm_p999_MPa": float(np.quantile(vm, 0.999) / 1e6),
        "vm_p99_MPa": float(np.quantile(vm, 0.99) / 1e6),
        "vm_median_MPa": float(np.median(vm) / 1e6),
        "yield_MPa": yield_316L / 1e6,
        "util_peak": float(vm.max() / yield_316L),
        "util_p999": float(np.quantile(vm, 0.999) / yield_316L),
        "u_max_mm": float(np.abs(u_np).max() * 1e3),
    }
    log(json.dumps(res, indent=1))
    return res, solid, vm, eidx


def main():
    from aces.cfd import lbm
    xp = lbm.backend(prefer_gpu=True)
    print("backend:", xp.__name__)
    if "--selftest" in sys.argv:
        ok = selftest(xp)
        sys.exit(0 if ok else 1)
    n = 48
    for a in sys.argv:
        if a.startswith("--n="):
            n = int(a.split("=")[1])
    if not selftest(xp):
        print("selftest failed; not running the cell")
        sys.exit(1)
    res, solid, vm, eidx = run_h2_cell(xp, n=n)
    out = ROOT / "out" / "h2-fea"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / f"h2-cell-875bar-n{n}.json", "w") as fh:
        json.dump(res, fh, indent=1)
    np.save(out / f"h2-cell-vm-n{n}.npy", vm)
    print("wrote", out / f"h2-cell-875bar-n{n}.json")


if __name__ == "__main__":
    main()
