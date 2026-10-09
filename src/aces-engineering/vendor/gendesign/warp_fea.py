"""NVIDIA Warp kernels for the voxel FEA and a differentiable rod-lattice parameterisation.

Two things the CuPy kernel cannot give cheaply:

1. `apply_K` as a single fused kernel (gather 24 dofs, 24x24 matvec, scatter-add) instead of the
   gather / einsum / scatter_add chain: the matrix-free product is the whole cost of the PCG, so
   this is where the generative loops speed up. Zero-copy with CuPy through the CUDA array
   interface, so `VoxelFEA.solve` can use it unchanged.

2. Differentiable geometry: `rod_density` builds a smooth voxel density from rod segments
   (centre points, radii) and `wp.Tape` returns d(density)/d(radius, endpoints). Chained with the
   analytic compliance sensitivity dc/dE_e = -u_e^T k_e u_e (the self-adjoint case) that gives
   dc/d(rod parameters): the gradient a lattice-chassis or formgen loop needs, checked against
   finite differences in `self_test`.
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

try:
    import warp as wp

    HAVE_WARP = True
except Exception:  # pragma: no cover
    wp = None
    HAVE_WARP = False

if HAVE_WARP:
    wp.config.quiet = True
    wp.init()

    @wp.kernel
    def _apply_k(edof: wp.array2d(dtype=wp.int32), ke: wp.array2d(dtype=wp.float64), ee: wp.array(dtype=wp.float64),
                 u: wp.array(dtype=wp.float64), out: wp.array(dtype=wp.float64)):
        e = wp.tid()
        s = ee[e]
        for a in range(24):
            acc = wp.float64(0.0)
            for b in range(24):
                acc += ke[a, b] * u[edof[e, b]]
            wp.atomic_add(out, edof[e, a], s * acc)

    @wp.kernel
    def _elem_compliance(edof: wp.array2d(dtype=wp.int32), ke: wp.array2d(dtype=wp.float64), u: wp.array(dtype=wp.float64),
                         out: wp.array(dtype=wp.float64)):
        e = wp.tid()
        acc = wp.float64(0.0)
        for a in range(24):
            ua = u[edof[e, a]]
            row = wp.float64(0.0)
            for b in range(24):
                row += ke[a, b] * u[edof[e, b]]
            acc += ua * row
        out[e] = acc

    # Two kernels on purpose: Warp's reverse mode gives wrong adjoints when a loop-carried
    # accumulator feeds a nonlinearity (min / clamp / exp) after a dynamic loop (measured: the
    # gradient ignored the clamp, 65 % error). A linear accumulation kernel followed by a
    # loop-free clamp kernel is exact (checked against finite differences in self_test).
    @wp.kernel
    def _rod_sum(centres: wp.array(dtype=wp.vec3), p0: wp.array(dtype=wp.vec3), p1: wp.array(dtype=wp.vec3),
                 radius: wp.array(dtype=wp.float32), sharpness: wp.float32, acc: wp.array(dtype=wp.float32)):
        i = wp.tid()
        c = centres[i]
        s = wp.float32(0.0)
        for r in range(p0.shape[0]):
            a = p0[r]
            b = p1[r]
            ab = b - a
            t = wp.dot(c - a, ab) / wp.max(wp.dot(ab, ab), wp.float32(1e-12))
            t = wp.clamp(t, wp.float32(0.0), wp.float32(1.0))
            q = a + ab * t
            d = wp.length(c - q)
            s += wp.float32(1.0) / (wp.float32(1.0) + wp.exp(sharpness * (d - radius[r])))
        acc[i] = s

    @wp.kernel
    def _rod_clamp(acc: wp.array(dtype=wp.float32), rho_min: wp.float32, rho: wp.array(dtype=wp.float32)):
        i = wp.tid()
        rho[i] = rho_min + (wp.float32(1.0) - rho_min) * wp.min(acc[i], wp.float32(1.0))


class WarpApplyK:
    """Drop-in matrix-free K product on Warp, sharing the device arrays of a gendesign VoxelFEA."""

    def __init__(self, fea):
        if not HAVE_WARP:
            raise RuntimeError("warp-lang is not installed")
        import cupy as cp

        if fea.xp is not cp:
            raise RuntimeError("WarpApplyK needs the CuPy (GPU) VoxelFEA")
        self.fea = fea
        self.dev = "cuda"
        self.edof = wp.array(cp.asnumpy(cp.asarray(fea.edof, dtype=cp.int32)), dtype=wp.int32, device=self.dev)
        self.ke = wp.array(np.asarray(cp.asnumpy(fea.KE), dtype=np.float64), dtype=wp.float64, device=self.dev)
        self.ne, self.ndof = int(fea.ne), int(fea.ndof)

    def __call__(self, u, Ee):
        import cupy as cp

        u64 = cp.ascontiguousarray(cp.asarray(u, dtype=cp.float64))
        e64 = cp.ascontiguousarray(cp.asarray(Ee, dtype=cp.float64))
        out = cp.zeros(self.ndof, dtype=cp.float64)
        # CuPy arrays expose __cuda_array_interface__: wrapped zero-copy
        wp.launch(_apply_k, dim=self.ne, inputs=[self.edof, self.ke, wp.array(e64, dtype=wp.float64, copy=False),
                                                 wp.array(u64, dtype=wp.float64, copy=False), wp.array(out, dtype=wp.float64, copy=False)], device=self.dev)
        out[self.fea.fixed] = 0.0
        return out.astype(self.fea.dtype, copy=False)

    def element_compliance(self, u):
        import cupy as cp

        u64 = cp.ascontiguousarray(cp.asarray(u, dtype=cp.float64))
        out = cp.zeros(self.ne, dtype=cp.float64)
        wp.launch(_elem_compliance, dim=self.ne, inputs=[self.edof, self.ke, wp.array(u64, dtype=wp.float64, copy=False),
                                                         wp.array(out, dtype=wp.float64, copy=False)], device=self.dev)
        return out


class RodLattice:
    """Smooth voxel density from rod segments; gradients by wp.Tape."""

    def __init__(self, centres_mm: np.ndarray, sharpness: float = 4.0, rho_min: float = 1e-3, device: str = "cuda"):
        if not HAVE_WARP:
            raise RuntimeError("warp-lang is not installed")
        self.dev = device
        self.centres = wp.array(np.asarray(centres_mm, dtype=np.float32), dtype=wp.vec3, device=device)
        self.n = int(len(centres_mm))
        self.sharpness, self.rho_min = float(sharpness), float(rho_min)

    def density(self, p0: np.ndarray, p1: np.ndarray, radius: np.ndarray, grad_out: Optional[np.ndarray] = None
                ) -> Tuple[np.ndarray, Optional[dict]]:
        """rho (n,) and, when grad_out (n,) = d(objective)/d(rho) is given, the chained gradients
        d(objective)/d(radius), d/d(p0), d/d(p1)."""
        a0 = wp.array(np.asarray(p0, dtype=np.float32), dtype=wp.vec3, device=self.dev, requires_grad=grad_out is not None)
        a1 = wp.array(np.asarray(p1, dtype=np.float32), dtype=wp.vec3, device=self.dev, requires_grad=grad_out is not None)
        ar = wp.array(np.asarray(radius, dtype=np.float32), dtype=wp.float32, device=self.dev, requires_grad=grad_out is not None)
        rho = wp.zeros(self.n, dtype=wp.float32, device=self.dev, requires_grad=grad_out is not None)
        acc = wp.zeros(self.n, dtype=wp.float32, device=self.dev, requires_grad=grad_out is not None)
        if grad_out is None:
            wp.launch(_rod_sum, dim=self.n, inputs=[self.centres, a0, a1, ar, self.sharpness, acc], device=self.dev)
            wp.launch(_rod_clamp, dim=self.n, inputs=[acc, self.rho_min, rho], device=self.dev)
            return rho.numpy(), None
        tape = wp.Tape()
        with tape:
            wp.launch(_rod_sum, dim=self.n, inputs=[self.centres, a0, a1, ar, self.sharpness, acc], device=self.dev)
            wp.launch(_rod_clamp, dim=self.n, inputs=[acc, self.rho_min, rho], device=self.dev)
        rho.grad.assign(wp.array(np.asarray(grad_out, dtype=np.float32), dtype=wp.float32, device=self.dev))
        tape.backward()
        g = {"radius": ar.grad.numpy().copy(), "p0": a0.grad.numpy().copy(), "p1": a1.grad.numpy().copy()}
        tape.zero()
        return rho.numpy(), g


def compliance_and_grad_wrt_rods(fea, lattice: RodLattice, p0, p1, radius, F, penal: float = 1.0, tol: float = 1e-8,
                                 maxiter: int = 8000, apply=None):
    """c(rods) = F.u with K(rho(rods)) u = F, and dc/d(radius), dc/d(p0), dc/d(p1) by the chain
    dc/drho_e = -penal * rho_e^(penal-1) * u_e^T k_e u_e  (self-adjoint), then wp.Tape for drho/d(rods)."""
    import cupy as cp

    rho, _ = lattice.density(p0, p1, radius)
    rho_d = cp.asarray(rho, dtype=fea.dtype)
    Ee = rho_d ** penal
    u, its, res = fea.solve(Ee, F, tol=tol, maxiter=maxiter)
    c = float(F @ u)
    ec = (apply.element_compliance(u) if apply is not None else fea.element_compliance(u))
    dc_drho = -penal * (rho_d ** (penal - 1.0)) * cp.asarray(ec, dtype=fea.dtype)
    _, g = lattice.density(p0, p1, radius, grad_out=cp.asnumpy(dc_drho))
    return c, g, {"pcg_iters": its, "relres": float(res), "solid_fraction": float(rho.mean())}


def self_test(verbose: bool = True) -> dict:
    """1) Warp apply_K == CuPy apply_K; 2) element compliance agreement; 3) rod-parameter gradient vs
    central finite differences on a small cantilever lattice."""
    import time

    import cupy as cp

    from .fea3d import VoxelFEA

    out = {}
    nx, ny, nz, h = 24, 8, 8, 1.0
    fea = VoxelFEA(nx, ny, nz, h, 0.3, "float64", prefer_gpu=True)
    fea.fix_box([0, 0, 0, 0, ny * h, nz * h])
    fea.add_traction_box("tip", [nx * h, 0, 0, nx * h, ny * h, nz * h], [0, 0, -100.0], axis="x")
    F = fea.loads["tip"]
    rng = cp.random.default_rng(0)
    Ee = 0.1 + 0.9 * rng.random(fea.ne, dtype=cp.float64)
    u = rng.random(fea.ndof, dtype=cp.float64)
    u[fea.fixed] = 0.0
    app = WarpApplyK(fea)
    app(u, Ee)  # warm-up: module compile is not part of the timing
    fea.apply_K(u, Ee)
    cp.cuda.Stream.null.synchronize()
    t0 = time.time()
    for _ in range(3):
        kw = app(u, Ee)
    cp.cuda.Stream.null.synchronize()
    t_w = (time.time() - t0) / 3
    t0 = time.time()
    for _ in range(3):
        kc = fea.apply_K(u, Ee)
    cp.cuda.Stream.null.synchronize()
    t_c = (time.time() - t0) / 3
    out["apply_K_max_abs_diff"] = float(cp.abs(kw - kc).max())
    out["apply_K_rel_diff"] = float(cp.abs(kw - kc).max() / cp.abs(kc).max())
    out["apply_K_warp_ms"] = 1e3 * t_w
    out["apply_K_cupy_ms"] = 1e3 * t_c
    ec_w = app.element_compliance(u)
    ec_c = fea.element_compliance(u)
    out["elem_compliance_rel_diff"] = float(cp.abs(ec_w - ec_c).max() / cp.abs(ec_c).max())
    # rod lattice gradient check
    centres = cp.asnumpy(fea.elem_xyz())
    lat = RodLattice(centres, sharpness=3.0, rho_min=1e-3)
    p0 = np.array([[0.0, 2.0, 2.0], [0.0, 6.0, 6.0], [0.0, 2.0, 6.0], [0.0, 6.0, 2.0]], dtype=np.float32)
    p1 = np.array([[24.0, 4.0, 4.0], [24.0, 4.0, 4.0], [24.0, 4.0, 4.0], [24.0, 4.0, 4.0]], dtype=np.float32)
    r = np.array([1.6, 1.6, 1.6, 1.6], dtype=np.float32)
    c0, g, info = compliance_and_grad_wrt_rods(fea, lat, p0, p1, r, F, penal=1.0, apply=app)
    eps = 2e-2
    fd = []
    for k in range(len(r)):
        rp, rm = r.copy(), r.copy()
        rp[k] += eps
        rm[k] -= eps
        cp_, _, _ = compliance_and_grad_wrt_rods(fea, lat, p0, p1, rp, F, penal=1.0, apply=app)
        cm_, _, _ = compliance_and_grad_wrt_rods(fea, lat, p0, p1, rm, F, penal=1.0, apply=app)
        fd.append((cp_ - cm_) / (2 * eps))
    fd = np.array(fd)
    out["compliance"] = c0
    out["grad_radius_warp"] = g["radius"].tolist()
    out["grad_radius_fd"] = fd.tolist()
    out["grad_radius_rel_err"] = float(np.abs(g["radius"] - fd).max() / max(np.abs(fd).max(), 1e-12))
    out["pcg_iters"] = info["pcg_iters"]
    if verbose:
        print(f"apply_K: warp {out['apply_K_warp_ms']:.2f} ms vs cupy {out['apply_K_cupy_ms']:.2f} ms, rel diff {out['apply_K_rel_diff']:.2e}; "
              f"elem compliance rel diff {out['elem_compliance_rel_diff']:.2e}")
        print(f"rod gradient: warp {np.round(g['radius'], 4)} vs FD {np.round(fd, 4)} -> rel err {out['grad_radius_rel_err']:.2e} (c = {c0:.5g}, {info['pcg_iters']} its)")
    return out


if __name__ == "__main__":
    self_test()
