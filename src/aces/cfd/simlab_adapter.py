"""simlab solver adapter for ACES calibration samples (solver id ``aces-tpms-periodic``).

simlab's worker imports ``module.Adapter`` from its registry entry, calls
``run(case, spec, run_dir)`` and stores the returned numbers in ``results.json``.  This
adapter runs one periodic TPMS sample (``aces.cfd.periodic``) per case and also writes
``measurement.json``: the row ``aces calibrate`` reads, or nothing when the case is not a
valid measurement.  The row carries ``boundary: uniform_heat_flux`` (h is H1).  ``aces
cfd-collect`` gathers those rows from a finished experiment.

Case parameters (from the experiment's params / sweep):
  tpms, wall_ratio, cell_m, fluid, T_K, p_Pa, re      the sample
  n (default 64), thermal (default true), cpu (force numpy)
  max_steps (default 2,000,000): the limit for EACH flow solve.  A case runs up to 8 flow
      solves (the force is iterated to the target Re) and one thermal solve of up to
      4 x max_steps, so one case can take up to 24,000,000 steps in all.
  validate: "square_duct" | "plane_channel"            the registry validation cases instead

Validation cases compare against exact fully developed laminar results (Shah & London
1978): square duct f Re = 56.91, Nu_H1 = 3.608; plane channel f Re = 96, Nu_H1 = 8.235.
"""
from __future__ import annotations

import json
import os
import time

import numpy as np

from ..safeio import OutDir
from . import lbm, periodic

EXACT = {"square_duct": (56.91, 3.608), "plane_channel": (96.0, 8.235)}


def _validate(kind: str, n: int, xp) -> dict:
    if kind == "square_duct":
        fl = np.zeros((2, n + 2, n + 2), bool)
        fl[:, 1:-1, 1:-1] = True
        dh, area = float(n), 4.0 * n * 2
    elif kind == "plane_channel":
        fl = np.ones((2, 2, n + 2), bool)
        fl[:, :, 0] = fl[:, :, -1] = False
        dh, area = 2.0 * n, 2.0 * 2 * 2
    else:
        raise ValueError(f"unknown validation case {kind!r}; choose from {sorted(EXACT)}")
    nu, g = 0.2, 1e-6
    fr, _ = lbm.solve_flow(fl, nu, g, xp=xp, check_every=100)
    u = fr.u_interstitial
    f_re = (g * dh / (0.5 * u * u)) * (u * dh / nu)
    sc = lbm.solve_scalar(fl, fr.u, alpha=nu / 0.7, xp=xp, check_every=100)
    nu_h1 = lbm.nusselt_h1(fl, fr, sc, area, dh)
    f_ex, nu_ex = EXACT[kind]
    return {"validation_case": kind, "n": n, "fRe": f_re, "fRe_exact": f_ex, "fRe_err_pct": 100 * (f_re - f_ex) / f_ex,
            "Nu_H1": nu_h1, "Nu_H1_exact": nu_ex, "Nu_err_pct": 100 * (nu_h1 - nu_ex) / nu_ex,
            "converged": int(fr.converged and sc.converged)}


class Adapter:
    name = "aces-tpms-periodic"

    def inputs(self, case: dict, spec) -> list:
        return []  # geometry is generated from the case parameters; nothing on disk to hash

    def run(self, case: dict, spec, run_dir: str) -> dict:
        precision = getattr(getattr(spec, "resources", None), "precision", "float64")
        if precision != "float64":
            raise ValueError("aces-tpms-periodic runs in float64 only (simlab verification rule)")
        want_cpu = bool(case.get("cpu", False))
        xp = lbm.backend(prefer_gpu=not want_cpu)
        if xp is np and not want_cpu:
            raise RuntimeError("no CUDA device visible to CuPy; refusing to run a rig case on the CPU "
                               "(set cpu: true in the case to force it)")
        device = "cuda" if xp is not np else "cpu"
        t0 = time.time()
        if case.get("validate"):
            out = _validate(case["validate"], int(case.get("n", 24)), xp)
            out.update({"wall_s": time.time() - t0, "device": device, "artifacts": []})
            return out
        exp_id = getattr(spec, "id", "local")
        s = periodic.Sample(
            tpms=case["tpms"], wall_ratio=float(case["wall_ratio"]), cell_m=float(case["cell_m"]),
            fluid=case["fluid"], T_K=float(case["T_K"]), p_Pa=float(case["p_Pa"]), re_target=float(case["re"]),
            n=int(case.get("n", 64)), thermal=bool(case.get("thermal", True)),
            u_lattice=float(case.get("u_lattice", 0.025)),
            source=f"simlab:{exp_id}:{case.get('case_id', 'c000')}")
        r = periodic.run(s, xp=xp, max_steps=int(case.get("max_steps", 2_000_000)))
        os.makedirs(run_dir, exist_ok=True)
        art = []
        with OutDir(run_dir) as out:  # never writes through a link planted in the run folder
            out.write_text("sample.json", json.dumps(periodic.as_dict(r), indent=2, default=float), overwrite=True)
            art.append(os.path.join(run_dir, "sample.json"))
            if r.measurement is not None:
                out.write_text("measurement.json", json.dumps(r.measurement, indent=2), overwrite=True)
                art.append(os.path.join(run_dir, "measurement.json"))
        d = r.diagnostics
        m = r.measurement or {}
        return {
            "converged": int(r.measurement is not None),
            "re_achieved": d.get("re_achieved"),
            "u_lattice": s.u_lattice,
            "dpdx_Pa_m": m.get("dpdx_Pa_m"),
            "mass_flux_kg_m2s": m.get("mass_flux_kg_m2s"),
            "h_W_m2K": m.get("h_W_m2K"),
            "boundary": periodic.BOUNDARY,
            "nusselt_h1": d.get("nusselt_h1"),
            "mach": d.get("mach"),
            "grid_peclet": d.get("grid_peclet"),
            "phi_voxel": d.get("phi_voxel"),
            "flow_steps": d.get("flow_steps"),
            "thermal_steps": d.get("thermal_steps"),
            "problems": "; ".join(r.problems),
            "notes": "; ".join(r.notes),
            "wall_s": time.time() - t0,
            "device": device,
            "artifacts": art,
        }
