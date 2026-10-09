"""aces golden: the SIMP generator (gendesign.topopt3d.run_case, OC update, FFT density filter).

The generator grows material inside the design space under the spec's supports and loads: everything outside the
design space is a passive void, keep-ins are passive solid, and `volfrac` is a fraction of the design space (run_case
receives volfrac x design_share of the box). run_case works in the grid frame (its domain starts at 0), so every box
is translated by -origin. Its own iso-surface (design.stl) is deleted: the part is the organic skin built from the
density field, not that raw mesh."""
from __future__ import annotations

import time
from pathlib import Path

from .spec import param_values

EMPTY_MESSAGE = "the SIMP density never reaches the iso level: no material survives at these parameters"


def _shift(box, origin) -> list[float]:
    o = [float(v) for v in origin]
    b = [float(v) for v in box]
    return [b[0] - o[0], b[1] - o[1], b[2] - o[2], b[3] - o[0], b[4] - o[1], b[5] - o[2]]


def simp_case(domain: dict, eff: dict, params: dict) -> dict:
    """The topopt3d case for one design, in the grid frame. Loads are nodal (add_load_box), as run_case applies them;
    the real solve applies the spec's traction loads."""
    d = eff["derived"]
    p = param_values(eff, params)
    origin = d["grid"]["origin_mm"]
    g = eff["generator"]
    loads, weights = [], {}
    for ld in eff["loads"]:
        loads.append({"case": ld["case"], "box": _shift(ld["box"], origin), "force": [float(f) for f in ld["force"]]})
        weights[ld["case"]] = 1.0
    return {
        "name": eff["name"], "domain_mm": [float(v) for v in d["grid"]["envelope_mm"]], "elem_mm": float(d["gen_mm"]),
        "material": {"E": float(eff["material"]["E_MPa"]), "nu": float(eff["material"]["nu"])},
        "volfrac": p["volfrac"] * float(domain["design_share"]), "penal": p["penal"], "rmin_mm": p["rmin_mm"],
        "iterations": int(g["iterations"]), "change_tol": float(g["change_tol"]), "move": float(g["move"]),
        "supports": [{"box": _shift(s["box"], origin), "dofs": [int(x) for x in s["dofs"]]} for s in eff["supports"]],
        "loads": loads, "weights": weights, "passive": domain["passive_grid"],
        "solver": {"tol": float(g["solver_tol"]), "maxiter": 4000},
    }


def run_simp(domain: dict, eff: dict, params: dict, out_dir: Path, *, device: str) -> dict:
    """Run SIMP into <out_dir>/generator/ (density.npy, report.json, slices.png). Returns {"status": "ok"|"empty",
    "message", "density_path", "report" (the generator record), "wall_s", "device"}. An empty design (the vendored
    density_to_stl raises after saving density.npy) is status "empty", not an exception."""
    import numpy as np
    from gendesign.topopt3d import run_case

    from .domain import design_cells

    t0 = time.time()
    gen_dir = Path(out_dir) / "generator"
    case = simp_case(domain, eff, params)
    p = param_values(eff, params)
    status, message, rep = "ok", "", None
    try:
        rep = run_case(case, str(gen_dir), dtype="float64", prefer_gpu=(device == "gpu"), iters=None, smooth=0, quiet=True)
    except ValueError as exc:
        if "never exceeds the iso level" not in str(exc) or not (gen_dir / "density.npy").is_file():
            raise
        status, message = "empty", EMPTY_MESSAGE
    stl = gen_dir / "design.stl"
    if stl.exists():
        stl.unlink()  # the raw iso-surface is not the part
    density_path = gen_dir / "density.npy"
    x = np.load(density_path)
    free = design_cells(domain, eff, x.shape)
    rho = np.asarray(x, dtype=np.float64)[free]
    if rep is not None:
        hist = rep.get("history") or []
        dev = rep.get("device")
        if device == "gpu" and str(dev).startswith("cpu"):
            raise RuntimeError("a GPU generator run was asked for but gendesign.topopt3d fell back to NumPy (no usable "
                               "CUDA device); the design is not recorded as a GPU run")
        record = {
            "iterations_run": int(rep["iterations_run"]), "converged": bool(rep["converged"]),
            "stopped_at_cap": (not bool(rep["converged"])) and int(rep["iterations_run"]) >= int(case["iterations"]),
            "final_change": float(hist[-1]["change"]) if hist else None,
            "compliance_Nmm": float(rep["final"]["compliance_Nmm"]),
            "solves_unconverged": int(rep["solves_unconverged"]),
        }
    else:
        from gendesign.fea3d import device_name, get_xp

        dev = device_name(get_xp(device == "gpu"))
        record = {"iterations_run": None, "converged": False, "stopped_at_cap": None, "final_change": None,
                  "compliance_Nmm": None, "solves_unconverged": None}
    wall = time.time() - t0
    report = {
        "kind": "simp", "h_mm": float(eff["derived"]["gen_mm"]), "elements": int(x.size),
        "volfrac_design": p["volfrac"], "volfrac_box": case["volfrac"], "rmin_mm": p["rmin_mm"], "penal": p["penal"],
        **record,
        "mnd_pct": 100.0 * 4.0 * float(np.mean(rho * (1.0 - rho))) if rho.size else None,
        "device": dev, "wall_s": wall,
    }
    return {"status": status, "message": message, "density_path": str(density_path), "report": report,
            "wall_s": wall, "device": dev}
