"""Solid cantilever on the voxel FEA kernel against Euler-Bernoulli and Timoshenko tip deflection.

Fixed root face, consistent uniform traction on the free end face, square section b x h,
length L. Timoshenko: delta = P L^3 / (3 E I) + P L / (kappa G A), kappa = 10(1+nu)/(12+11nu)
for a rectangle. The FEA clamps every DOF of the root face, which the beam theories do not,
so a small stiffening offset is expected and reported, not hidden.
"""
from __future__ import annotations

import time

import numpy as np


class Adapter:
    name = "gendesign-beam-check"

    def inputs(self, case: dict, spec) -> list:
        return []

    def run(self, case: dict, spec, run_dir: str) -> dict:
        from gendesign.fea3d import VoxelFEA, device_name

        L = float(case.get("L_mm", 100.0))
        b = float(case.get("b_mm", 10.0))
        h = float(case.get("h_mm", 10.0))
        P = float(case.get("P_N", 100.0))
        E = float(case.get("E", 200000.0))
        nu = float(case.get("nu", 0.3))
        e = float(case.get("elem_mm", 0.5))
        dtype = case.get("dtype", spec.resources.precision)
        # FP64 Jacobi-PCG bottoms out near a true relative residual of 1e-9 on this problem
        # (measured), so 1e-8 is the tightest tolerance that terminates; float32 cannot get
        # below ~0.4 and is kept only as an explicit, flagged option.
        tol = float(case.get("tol", 1e-8 if dtype == "float64" else 1e-3))
        nx, ny, nz = [max(1, int(round(v / e))) for v in (L, b, h)]
        t0 = time.time()
        fea = VoxelFEA(nx, ny, nz, e, nu, dtype, prefer_gpu=not case.get("cpu", False))
        fea.fix_box([0, 0, 0, 0, b, h])
        fea.add_traction_box("tip", [L, 0, 0, L, b, h], [0, 0, -P], axis="x")
        Ee = fea.xp.ones(fea.ne, dtype=fea.dtype)
        u, its, res = fea.solve(Ee, fea.loads["tip"], tol=tol, maxiter=int(case.get("maxiter", 20000)))
        uz = u[2::3] / E
        tipmask = fea.nodes_in_box([L, 0, 0, L, b, h])
        delta_fea = float(-fea.xp.asarray(uz)[tipmask].mean())
        I = b * h ** 3 / 12.0
        A = b * h
        G = E / (2 * (1 + nu))
        kappa = 10 * (1 + nu) / (12 + 11 * nu)
        d_eb = P * L ** 3 / (3 * E * I)
        d_timo = d_eb + P * L / (kappa * G * A)
        return {
            "converged": int(res <= tol),
            "delta_fea_mm": delta_fea,
            "delta_eb_mm": d_eb,
            "delta_timo_mm": d_timo,
            "err_vs_eb_pct": 100 * (delta_fea - d_eb) / d_eb,
            "err_vs_timo_pct": 100 * (delta_fea - d_timo) / d_timo,
            "elements": fea.ne,
            "dof": fea.ndof,
            "pcg_iters": its,
            "pcg_relres": float(res),
            "wall_s": time.time() - t0,
            "device": device_name(fea.xp),
            "artifacts": [],
        }
