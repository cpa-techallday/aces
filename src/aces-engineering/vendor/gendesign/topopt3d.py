"""3-D SIMP topology optimisation on a voxel grid.

The generative engine for structural parts: SIMP material interpolation, FFT density
filter, optimality-criteria update, several weighted load cases, passive solid and
void regions (boxes and cylinders in mm). Runs on the GPU through CuPy; the same code
runs on NumPy with --cpu.

Case file (JSON)::

    {
      "name": "cantilever3d",
      "domain_mm": [60, 20, 10],          # design box, origin at (0,0,0)
      "elem_mm": 0.5,                     # voxel size
      "material": {"E": 200000, "nu": 0.3},
      "volfrac": 0.3, "penal": 3.0, "rmin_mm": 1.2,
      "iterations": 60, "change_tol": 0.01, "move": 0.2,
      "supports": [{"box": [0,0,0, 0,20,10], "dofs": [0,1,2]}],
      "loads": [{"case": "tip", "box": [60,0,0, 60,0,10], "force": [0,-100,0]}],
      "weights": {"tip": 1.0},
      "passive": [{"box": [...], "type": "solid"},
                  {"cyl": {"axis": "z", "center": [x, y], "r": 4, "span": [z0, z1]}, "type": "void"}],
      "solver": {"tol": 1e-8, "maxiter": 5000}
    }

Outputs in --out: density.npy (nx,ny,nz), design.stl (mm), slices.png, report.json.
"""
from __future__ import annotations

from . import progress as _progress

import argparse
import json
import os
import time

import numpy as np

from .export import density_to_stl, plot_slices, save_json
from .fea3d import VoxelFEA, device_name, get_xp, to_numpy
from .filters import FFTDensityFilter


def passive_masks(fea: VoxelFEA, passive):
    xp = fea.xp
    solid = xp.zeros(fea.ne, dtype=bool)
    void = xp.zeros(fea.ne, dtype=bool)
    for p in passive or []:
        if "box" in p:
            m = fea.elems_in_box(p["box"])
        elif "cyl" in p:
            c = p["cyl"]
            m = fea.elems_in_cyl(c["axis"], c["center"], c["r"], c["span"])
        else:
            raise ValueError("passive region needs 'box' or 'cyl'")
        if p.get("type", "void") == "solid":
            solid |= m
        else:
            void |= m
    return solid, void


def oc_update(x, dc, dv, volfrac, filt, shape, solid, void, move=0.2):
    """Optimality-criteria update with bisection on the volume multiplier."""
    xp = filt.xp
    g = xp.maximum(-dc, 0.0) / dv
    lo, hi = 0.0, 1e9
    xnew = x
    xphys = None
    while (hi - lo) / (lo + hi + 1e-30) > 1e-4:
        mid = 0.5 * (lo + hi)
        xnew = xp.clip(x * xp.sqrt(g / mid), xp.maximum(x - move, 0.0), xp.minimum(x + move, 1.0))
        xnew[solid] = 1.0
        xnew[void] = 0.0
        xphys = filt.apply(xnew.reshape(shape)).ravel()
        if float(xphys.mean()) > volfrac:
            lo = mid
        else:
            hi = mid
    return xnew, xphys


def run_case(case: dict, out_dir: str, dtype: str = "float64", prefer_gpu: bool = True,
             iters: int | None = None, smooth: int = 0, quiet: bool = False) -> dict:
    t_start = time.time()
    xp = get_xp(prefer_gpu)
    Lx, Ly, Lz = [float(v) for v in case["domain_mm"]]
    h = float(case["elem_mm"])
    nx, ny, nz = [max(1, int(round(L / h))) for L in (Lx, Ly, Lz)]
    shape = (nx, ny, nz)
    mat = case.get("material", {})
    E0 = float(mat.get("E", 200000.0))
    nu = float(mat.get("nu", 0.3))
    volfrac = float(case["volfrac"])
    penal = float(case.get("penal", 3.0))
    rmin = float(case.get("rmin_mm", 1.5 * h))
    niter = int(iters or case.get("iterations", 60))
    change_tol = float(case.get("change_tol", 0.01))
    move = float(case.get("move", 0.2))
    is64 = np.dtype(dtype) == np.float64
    emin = float(case.get("emin", 1e-9 if is64 else 1e-6))
    sol = case.get("solver", {})
    tol = float(sol.get("tol", 1e-8 if is64 else 1e-5))
    maxiter = int(sol.get("maxiter", 5000))

    fea = VoxelFEA(nx, ny, nz, h, nu, dtype, prefer_gpu)
    nfix = sum(fea.fix_box(s["box"], s.get("dofs", [0, 1, 2])) for s in case["supports"])
    nload = {}
    for ld in case["loads"]:
        name = ld.get("case", "default")
        nload[name] = nload.get(name, 0) + fea.add_load_box(name, ld["box"], ld["force"])
    weights = case.get("weights", {})
    solid, void = passive_masks(fea, case.get("passive"))
    filt = FFTDensityFilter(nx, ny, nz, h, rmin, xp, dtype)

    x = xp.full(fea.ne, volfrac, dtype=fea.dtype)
    x[solid] = 1.0
    x[void] = 0.0
    xphys = filt.apply(x.reshape(shape)).ravel()
    warm = {}
    hist = []
    if not quiet:
        print(f"[topopt3d] {case.get('name', 'case')}: {nx}x{ny}x{nz} = {fea.ne:,} elements, "
              f"{fea.ndof:,} DOF, {device_name(xp)}, {dtype}, rmin {rmin} mm, volfrac {volfrac}", flush=True)
    for it in range(1, niter + 1):
        t0 = time.time()
        Ee = emin + xphys ** penal * (1.0 - emin)
        c = 0.0
        dc = xp.zeros(fea.ne, dtype=fea.dtype)
        pcg = []
        umax = 0.0
        unconverged = []
        for name, F in fea.loads.items():
            w = float(weights.get(name, 1.0))
            u, its, res = fea.solve(Ee, F, warm.get(name), tol, maxiter)
            warm[name] = u
            pcg.append(its)
            if res > tol:
                unconverged.append((name, res))
            ce = fea.element_compliance(u)
            c += w * float((Ee * ce).sum())
            dc -= w * penal * xphys ** (penal - 1.0) * (1.0 - emin) * ce
            umax = max(umax, fea.max_displacement(u) / E0)
        dv = xp.ones(fea.ne, dtype=fea.dtype)
        dc = filt.apply_T(dc.reshape(shape)).ravel()
        dv = filt.apply_T(dv.reshape(shape)).ravel()
        xnew, xphys_new = oc_update(x, dc, dv, volfrac, filt, shape, solid, void, move)
        change = float(xp.abs(xnew - x).max())
        x, xphys = xnew, xphys_new
        vol = float(xphys.mean())
        rec = {"it": it, "compliance_Nmm": c / E0, "vol": vol, "change": change,
               "pcg_iters": pcg, "umax_mm": umax, "s": round(time.time() - t0, 3),
               "unconverged": [[n, float(r)] for n, r in unconverged]}
        hist.append(rec)
        _progress.report("simp", step=it, total=niter, min_interval_s=0.0, compliance_Nmm=c / E0, vol=vol, change=change, pcg_iters=pcg, umax_mm=umax)
        if not quiet:
            print(f"  it {it:3d}  c {c / E0:.6g} N.mm  vol {vol:.3f}  change {change:.3f}  "
                  f"pcg {pcg}  umax {umax:.4g} mm  {rec['s']:.2f}s"
                  + (f"  WARNING solve not converged: {unconverged}" if unconverged else ""), flush=True)
        if change < change_tol:
            break

    xphys_np = to_numpy(xphys).reshape(shape)
    os.makedirs(out_dir, exist_ok=True)
    np.save(os.path.join(out_dir, "density.npy"), xphys_np.astype(np.float32))
    stl = density_to_stl(xphys_np, h, os.path.join(out_dir, "design.stl"), 0.5, smooth)
    png = plot_slices(xphys_np, h, os.path.join(out_dir, "slices.png"), case.get("name", ""))
    final = hist[-1]
    report = {
        "case": case,
        "grid": {"nx": nx, "ny": ny, "nz": nz, "h_mm": h, "elements": fea.ne, "dof": fea.ndof},
        "device": device_name(xp), "dtype": dtype, "emin": emin, "solver": {"tol": tol, "maxiter": maxiter},
        "supports_nodes": nfix, "load_nodes": nload,
        "passive_solid_elems": int(solid.sum()), "passive_void_elems": int(void.sum()),
        "iterations_run": len(hist), "converged": final["change"] < change_tol,
        "solves_unconverged": sum(1 for h in hist if h["unconverged"]),
        "final": {"compliance_Nmm": final["compliance_Nmm"], "vol": final["vol"],
                  "umax_mm": final["umax_mm"], "mass_kg_at_rho_7850": final["vol"] * nx * ny * nz * h ** 3 * 7.85e-6},
        "wall_s": round(time.time() - t_start, 2),
        "outputs": {"density": "density.npy", "stl": stl, "slices": png},
        "history": hist,
    }
    save_json(os.path.join(out_dir, "report.json"), report)
    if not quiet:
        print(f"[topopt3d] done in {report['wall_s']} s -> {out_dir}  (STL {stl['faces']:,} faces)", flush=True)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="3-D SIMP topology optimisation (GPU)")
    ap.add_argument("case", help="case JSON file")
    ap.add_argument("--out", default=None, help="output directory (default out/<case name>)")
    ap.add_argument("--dtype", default="float64", choices=["float32", "float64"],
                    help="float64 on the rig (GA100 FP64 is fast); float32 on the workstation")
    ap.add_argument("--cpu", action="store_true", help="force NumPy")
    ap.add_argument("--iters", type=int, default=None)
    ap.add_argument("--smooth", type=int, default=0, help="Taubin smoothing passes for the STL")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    with open(a.case, "r", encoding="utf-8") as f:
        case = json.load(f)
    out = a.out or os.path.join("out", case.get("name", os.path.splitext(os.path.basename(a.case))[0]))
    run_case(case, out, a.dtype, not a.cpu, a.iters, a.smooth, a.quiet)


if __name__ == "__main__":
    main()
