"""Differentiable lattice design: rod radii (and optionally endpoints) of a rod lattice inside a
voxel envelope are optimised for compliance under a volume cap, with gradients from Warp
(d rho / d rods by wp.Tape) chained to the voxel FEA's analytic compliance sensitivity.

    python -m gendesign.lattice_opt --out out/lattice_opt [--iters 40] [--rods 8] [--volfrac 0.25]

This is the gradient path the lattice-chassis and formgen loops need: every rod parameter gets
a true derivative, the optimiser is projected gradient descent on the radii with a volume
projection, and every step logs compliance, volume fraction and the gradient check residual
of the first step so the record shows the gradients were right, not just that the number went down.
"""
from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np


def cantilever_lattice(nx: int, ny: int, nz: int, h: float, n_rods: int, seed: int = 0):
    """Rods from the clamped face (x = 0) to a tip node, plus diagonals; random initial radii."""
    rng = np.random.default_rng(seed)
    Lx, Ly, Lz = nx * h, ny * h, nz * h
    tip = np.array([Lx, Ly / 2, Lz / 2])
    p0, p1 = [], []
    ys = np.linspace(0.15 * Ly, 0.85 * Ly, 2)
    zs = np.linspace(0.15 * Lz, 0.85 * Lz, 2)
    for y in ys:
        for z in zs:
            p0.append([0.0, y, z])
            p1.append(tip)
    # cross braces between the four root points at mid-length
    mid = Lx / 2
    for (y, z), (y2, z2) in (((ys[0], zs[0]), (ys[1], zs[1])), ((ys[1], zs[0]), (ys[0], zs[1]))):
        p0.append([0.0, y, z])
        p1.append([mid, y2, z2])
        p0.append([mid, y2, z2])
        p1.append([Lx, y, z])
    p0, p1 = np.array(p0, dtype=np.float32), np.array(p1, dtype=np.float32)
    p0, p1 = p0[:n_rods], p1[:n_rods]
    r = (0.08 * min(Ly, Lz) * (1 + 0.3 * rng.random(len(p0)))).astype(np.float32)
    return p0, p1, r


def main(argv=None):
    import cupy as cp

    from .fea3d import VoxelFEA, device_name
    from .warp_fea import RodLattice, WarpApplyK, compliance_and_grad_wrt_rods

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out/lattice_opt")
    ap.add_argument("--iters", type=int, default=40)
    ap.add_argument("--rods", type=int, default=8)
    ap.add_argument("--volfrac", type=float, default=0.25)
    ap.add_argument("--grid", default="48,16,16")
    ap.add_argument("--step", type=float, default=0.15, help="radius step per iteration in mm (normalised gradient)")
    ap.add_argument("--sharpness", type=float, default=3.0)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    nx, ny, nz = [int(v) for v in a.grid.split(",")]
    h = 1.0
    fea = VoxelFEA(nx, ny, nz, h, 0.3, "float64", prefer_gpu=True)
    fea.fix_box([0, 0, 0, 0, ny * h, nz * h])
    fea.add_traction_box("tip", [nx * h, 0, 0, nx * h, ny * h, nz * h], [0, 0, -100.0], axis="x")
    F = fea.loads["tip"]
    app = WarpApplyK(fea)
    lat = RodLattice(cp.asnumpy(fea.elem_xyz()), sharpness=a.sharpness, rho_min=1e-3)
    p0, p1, r = cantilever_lattice(nx, ny, nz, h, a.rods)
    rmin, rmax = 0.6, 0.45 * min(ny, nz) * h
    t0 = time.time()
    hist = []
    for it in range(a.iters):
        c, g, info = compliance_and_grad_wrt_rods(fea, lat, p0, p1, r, F, penal=1.0, apply=app)
        gr = g["radius"]
        if it == 0:  # one finite-difference check on the record
            k = int(np.argmax(np.abs(gr)))
            eps = 1e-2
            rp, rm = r.copy(), r.copy()
            rp[k] += eps
            rm[k] -= eps
            fd = (compliance_and_grad_wrt_rods(fea, lat, p0, p1, rp, F, 1.0, apply=app)[0] -
                  compliance_and_grad_wrt_rods(fea, lat, p0, p1, rm, F, 1.0, apply=app)[0]) / (2 * eps)
            grad_check = {"rod": k, "warp": float(gr[k]), "fd": float(fd), "rel_err": float(abs(gr[k] - fd) / max(abs(fd), 1e-12))}
        vol = info["solid_fraction"]
        hist.append({"iter": it, "compliance": c, "volfrac": vol, "pcg_iters": info["pcg_iters"], "radii": r.tolist(), "wall_s": time.time() - t0})
        print(f"[lattice] it {it:3d} c = {c:.6g}  vol {vol:.4f}  its {info['pcg_iters']}  r = {np.round(r, 3)}", flush=True)
        # projected gradient step: descend compliance, then scale radii to meet the volume cap
        step = -a.step * gr / (np.abs(gr).max() + 1e-30)
        r = np.clip(r + step.astype(np.float32), rmin, rmax).astype(np.float32)
        # volume projection by bisection on a common radius scale factor
        for _ in range(12):
            rho, _ = lat.density(p0, p1, r)
            v = float(rho.mean())
            if v <= a.volfrac * 1.01:
                break
            r = np.clip(r * np.float32(0.97), rmin, rmax).astype(np.float32)
    c, g, info = compliance_and_grad_wrt_rods(fea, lat, p0, p1, r, F, penal=1.0, apply=app)
    hist.append({"iter": a.iters, "compliance": c, "volfrac": info["solid_fraction"], "pcg_iters": info["pcg_iters"], "radii": r.tolist(), "wall_s": time.time() - t0})
    rho, _ = lat.density(p0, p1, r)
    np.save(os.path.join(a.out, "density.npy"), rho.reshape(nx, ny, nz))
    try:
        from .export import density_to_stl, plot_slices

        density_to_stl(rho.reshape(nx, ny, nz), h, os.path.join(a.out, "lattice.stl"), level=0.5)
        plot_slices(rho.reshape(nx, ny, nz), h, os.path.join(a.out, "slices.png"), "rod lattice density (final)")
    except Exception as exc:
        print("export skipped:", exc)
    rep = {"grid": [nx, ny, nz], "rods": len(r), "volfrac_cap": a.volfrac, "iters": a.iters, "device": device_name(fea.xp),
           "grad_check_iter0": grad_check, "compliance_start": hist[0]["compliance"], "compliance_end": hist[-1]["compliance"],
           "volfrac_start": hist[0]["volfrac"], "volfrac_end": hist[-1]["volfrac"], "radii_start": hist[0]["radii"], "radii_end": hist[-1]["radii"],
           "p0": p0.tolist(), "p1": p1.tolist(), "history": hist, "wall_s": time.time() - t0}
    with open(os.path.join(a.out, "report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2)
    print(json.dumps({k: rep[k] for k in ("compliance_start", "compliance_end", "volfrac_start", "volfrac_end", "grad_check_iter0", "wall_s")}, indent=2))


if __name__ == "__main__":
    main()
