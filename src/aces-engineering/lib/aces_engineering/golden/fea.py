"""aces golden: the real solve of a computed part on the voxel FEA (H8, fictitious domain, Jacobi-PCG, FP64).

solve_mesh() voxelises the part's STL on the design grid (or reuses another case's mask, so a device cross-check solves
the identical mask), checks that the loads and supports act on material and that every loaded body reaches a support,
solves through the step-structures adapter's stage_solve (CPU NumPy or CuPy) or through the same evaluator with the
Warp apply_K, and derives the metrics: mass from the mesh volume (the deliverable is the mesh), vm_p99/vm_p95 over the
solid elements outside the boundary-condition zones (vm_max is recorded but is resolution-sensitive).

The linear solver is chosen per solve (`solver`): "pcg" (Jacobi-PCG; sweep, verify and parity solves), "mg"
(geometric-multigrid PCG on the solid-only operator, gendesign.mg) or "auto" (the independent check: multigrid for a
one-component mask of at least MG_MIN_CELLS cells when gendesign.mg is importable, else PCG). An MG solve that stops above
the tolerance is solved again with PCG, and both attempts are recorded. Every solve asks for repair=False: the golden
path never lets the evaluator edit the mask it was given (the rig's patched evaluator repairs masks by default).

Ordering, pinned by the vendored code: element arrays such as fields.npz["vm_MPa"] are the C-order ravel of the
(nx, ny, nz) mask; node arrays such as u_mm are x-fastest, node i + (nx+1)*(j + (ny+1)*k)."""
from __future__ import annotations

import importlib.util
import inspect
import math
import re
import shutil
import time
from pathlib import Path

from . import METRICS, dump_json, read_json, sha256_file, utc_now, write_text

AXES = "xyz"
COVERAGE_MIN = 0.98
BACKENDS = {"adapter": "step-structures.stage_solve", "warp": "warp-apply_K"}
FEA_SOLVERS = ("pcg", "mg", "auto")
MG_MIN_CELLS = 200_000            # "auto" uses multigrid from this many cells (one solid component, gendesign.mg present)
MG_LEVELS = 4


def lane_of(device: str, backend: str) -> str:
    """desktop-cpu, desktop-gpu or desktop-warp: where a desktop solve ran. A rig job carries its own lane ("rig-gpu",
    set by golden.rigside), which case._results prefers."""
    if backend == "warp":
        return "desktop-warp"
    return "desktop-cpu" if device == "cpu" else "desktop-gpu"


def mg_available() -> bool:
    """gendesign.mg can be imported here (looked up with find_spec, not imported)."""
    try:
        return importlib.util.find_spec("gendesign.mg") is not None
    except (ImportError, ValueError):
        return False


def resolve_solver(requested: str, cells: int, components: int) -> tuple[str, str]:
    """(solver, reason) for a solve over `cells` grid cells whose mask has `components` solid components. "auto" is
    multigrid when gendesign.mg is importable, cells >= MG_MIN_CELLS and there is exactly one component, else PCG; an
    explicit "mg" without the module or with more than one component falls back to PCG; "pcg" is PCG."""
    req = str(requested or "pcg")
    if req not in FEA_SOLVERS:
        raise ValueError(f"unknown fea solver {req!r}: pcg, mg or auto")
    if req == "pcg":
        return "pcg", "pcg requested"
    have = mg_available()
    n = int(components)
    if req == "mg":
        if not have:
            return "pcg", "mg requested; gendesign.mg is not importable here: Jacobi-PCG"
        if n != 1:
            return "pcg", f"mg requested; {n} solid components (multigrid needs one): Jacobi-PCG"
        return "mg", "mg requested"
    if not have:
        return "pcg", "auto: Jacobi-PCG (gendesign.mg is not importable here)"
    if n != 1:
        return "pcg", f"auto: Jacobi-PCG ({n} solid components; multigrid needs one)"
    if int(cells) < MG_MIN_CELLS:
        return "pcg", f"auto: Jacobi-PCG ({int(cells):,} cells < {MG_MIN_CELLS:,})"
    return "mg", (f"auto: geometric multigrid (gendesign.mg) for a one-component mask of {int(cells):,} cells "
                  f"(>= {MG_MIN_CELLS:,})")


def _evaluator_kwargs(cls, **wanted) -> dict:
    """The keyword arguments of `wanted` that `cls` (VoxelFEAEvaluator) accepts: the rig's patched evaluator takes
    solver, repair and mg_levels; the older vendored snapshot does not, and must keep working."""
    try:
        params = inspect.signature(cls).parameters
    except (TypeError, ValueError):
        return {}
    return {k: v for k, v in wanted.items() if k in params}


class _Stage:
    """One stage record (voxel, solve, post) in the vendored worker's shape; ended FAILED when its body raises."""

    def __init__(self, stages: list, name: str, device: str):
        self.rec = {"stage": name, "status": "RUNNING", "started_utc": utc_now(), "finished_utc": None, "wall_s": None,
                    "device": device}
        self.t0 = time.time()
        stages.append(self.rec)

    def end(self, status: str = "FINISHED") -> float:
        if self.rec["finished_utc"] is None:
            self.rec["status"] = status
            self.rec["finished_utc"] = utc_now()
            self.rec["wall_s"] = time.time() - self.t0
        return self.rec["wall_s"]

    def __enter__(self) -> "_Stage":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.end("FAILED" if exc_type is not None else "FINISHED")
        return False


# ------------------------------------------------------------------------------------------- node and element sets

def _along(v, axis: int):
    shape = [1, 1, 1]
    shape[axis] = -1
    return v.reshape(shape)


def _grid_box(box, origin) -> list[float]:
    return [float(box[a]) - float(origin[a % 3]) for a in range(6)]


def box_nodes(box, origin, shape, h: float):
    """Nodes inside a part-frame box (tolerance 1e-6 mm), as VoxelFEA.nodes_in_box selects them: (nx+1, ny+1, nz+1)."""
    import numpy as np

    b = _grid_box(box, origin)
    m = []
    for a, n in enumerate(shape):
        g = np.arange(int(n) + 1, dtype=np.float64) * float(h)
        m.append((g >= b[a] - 1e-6) & (g <= b[a + 3] + 1e-6))
    return _along(m[0], 0) & _along(m[1], 1) & _along(m[2], 2)


def box_elems(box, origin, shape, h: float):
    """Elements whose centres lie inside a part-frame box, as VoxelFEA.elems_in_box selects them: (nx, ny, nz)."""
    import numpy as np

    b = _grid_box(box, origin)
    m = []
    for a, n in enumerate(shape):
        c = (np.arange(int(n), dtype=np.float64) + 0.5) * float(h)
        m.append((c >= b[a]) & (c <= b[a + 3]))
    return _along(m[0], 0) & _along(m[1], 1) & _along(m[2], 2)


def traction_elems(box, axis: str, origin, shape, h: float):
    """The elements VoxelFEA.add_traction_box loads: centres inside the in-plane extent, at h/2 from the plane."""
    import numpy as np

    a = AXES.index(axis)
    b = _grid_box(box, origin)
    plane = b[a]
    m = []
    for i, n in enumerate(shape):
        c = (np.arange(int(n), dtype=np.float64) + 0.5) * float(h)
        if i == a:
            m.append(np.abs(np.abs(c - plane) - 0.5 * float(h)) < 1e-6 * float(h))
        else:
            m.append((c >= b[i]) & (c <= b[i + 3]))
    return _along(m[0], 0) & _along(m[1], 1) & _along(m[2], 2)


def node_touches(mask):
    """(nx+1, ny+1, nz+1): nodes with at least one solid element among the eight around them."""
    import numpy as np

    p = np.pad(np.asarray(mask, dtype=bool), 1)
    out = np.zeros(tuple(n - 1 for n in p.shape), dtype=bool)
    for di in (0, 1):
        for dj in (0, 1):
            for dk in (0, 1):
                out |= p[di:di + out.shape[0], dj:dj + out.shape[1], dk:dk + out.shape[2]]
    return out


def elems_touching(node_mask):
    """(nx, ny, nz): elements with at least one of their eight corner nodes in node_mask."""
    import numpy as np

    n = np.asarray(node_mask, dtype=bool)
    out = np.zeros(tuple(s - 1 for s in n.shape), dtype=bool)
    for di in (0, 1):
        for dj in (0, 1):
            for dk in (0, 1):
                out |= n[di:di + out.shape[0], dj:dj + out.shape[1], dk:dk + out.shape[2]]
    return out


def structural_checks(mask, h: float, origin, eff: dict) -> tuple[dict, str, str]:
    """Before solving: every load acts on material (coverage >= 0.98), every support touches material, and every body
    that carries load reaches a support. Returns (checks, status, message); status "ok" or the failing design status."""
    import numpy as np
    import scipy.ndimage as nd

    mask = np.asarray(mask, dtype=bool)
    shape = mask.shape
    ns = node_touches(mask)
    load_cov: dict[str, float] = {}
    sup_cov: dict[str, float] = {}
    loaded = np.zeros(shape, dtype=bool)
    fixed = np.zeros(tuple(n + 1 for n in shape), dtype=bool)
    for i, ld in enumerate(eff["loads"]):
        name = ld.get("name") or f"load{i + 1}"
        if name in load_cov:
            name = f"{name}#{i + 1}"
        if ld.get("axis"):
            e = traction_elems(ld["box"], ld["axis"], origin, shape, h)
            load_cov[name] = float(mask[e].mean()) if e.any() else 0.0
            loaded |= e
        else:
            nb = box_nodes(ld["box"], origin, shape, h)
            load_cov[name] = float(ns[nb].mean()) if nb.any() else 0.0
            loaded |= elems_touching(nb)
    for i, s in enumerate(eff["supports"]):
        name = s.get("name") or f"support{i + 1}"
        if name in sup_cov:
            name = f"{name}#{i + 1}"
        nb = box_nodes(s["box"], origin, shape, h)
        sup_cov[name] = float(ns[nb].mean()) if nb.any() else 0.0
        fixed |= nb
    lab, ncomp = nd.label(mask)
    comps_loaded = set(int(c) for c in np.unique(lab[loaded & mask])) - {0}
    comps_fixed = set(int(c) for c in np.unique(lab[elems_touching(fixed) & mask])) - {0}
    connected = bool(comps_loaded) and comps_loaded <= comps_fixed
    checks = {"load_coverage": load_cov, "support_coverage": sup_cov, "load_path_connected": connected,
              "solid_components": int(ncomp)}
    low = [f"{k} {v:.3f}" for k, v in load_cov.items() if v < COVERAGE_MIN]
    if low:
        return checks, "detached-load", (f"load patch coverage below {COVERAGE_MIN:g} at {h:g} mm: {', '.join(low)} "
                                         "(the load would act on void)")
    bare = [k for k, v in sup_cov.items() if v == 0.0]
    if bare:
        return checks, "detached-support", f"no material at the support patch at {h:g} mm: {', '.join(bare)}"
    if not connected:
        return checks, "disconnected", (f"a loaded body does not reach any support at {h:g} mm "
                                        f"({ncomp} solid components)")
    return checks, "ok", ""


# ------------------------------------------------------------------------------------------------------------ solves

def _exp_id(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", name) or "case"


def _solve_adapter(fcase: dict, case_dir: Path) -> dict:
    from simlab.solvers.step_structures import Adapter
    from simlab.spec import Experiment

    exp = Experiment(id=_exp_id(case_dir.name), physics="structures", solver="step-structures", params=fcase,
                     resources={"precision": "float64"})
    return Adapter().stage_solve(fcase, exp, str(case_dir))


def _solve_warp(fcase: dict, mask, h: float, origin, env, case_dir: Path) -> dict:
    """The adapter's solve with the Warp K product: VoxelFEAEvaluator built with exactly stage_solve's arguments, then
    ev.fea.apply_K = WarpApplyK(ev.fea). Needs a usable CuPy GPU and warp-lang (imported here, never at module level).
    solver="pcg" and repair=False are passed only when the evaluator has those parameters (the patched one)."""
    import numpy as np
    from gendesign.evaluators import VoxelFEAEvaluator
    from gendesign.fea3d import device_name, to_numpy
    from gendesign.warp_fea import WarpApplyK
    from simlab.solvers.step_structures import _box_to_grid

    t0 = time.time()
    supports = [{"box": _box_to_grid(s["box"], origin), "dofs": tuple(s.get("dofs", (0, 1, 2)))} for s in fcase["supports"]]
    loads = []
    for ld in fcase["loads"]:
        e = {"box": _box_to_grid(ld["box"], origin), "force": ld["force"], "case": ld.get("case", "default")}
        if "axis" in ld:
            e["axis"] = ld["axis"]
        loads.append(e)
    extra = _evaluator_kwargs(VoxelFEAEvaluator, solver="pcg", repair=False)
    ev = VoxelFEAEvaluator(env, h, E=float(fcase["E"]), nu=float(fcase["nu"]), rho_kg_mm3=float(fcase["rho_kg_mm3"]),
                           supports=supports, loads=loads, dtype=fcase["dtype"], prefer_gpu=True,
                           tol=float(fcase["tol"]), maxiter=int(fcase["maxiter"]), warm_start=False,
                           vm_exclude_boxes=[_box_to_grid(b, origin) for b in fcase["vm_exclude"]], **extra)
    if ev.fea.xp is np:
        raise RuntimeError("the Warp backend needs a usable CuPy GPU (gendesign.fea3d fell back to NumPy)")
    if tuple(ev.shape) != tuple(mask.shape):
        raise ValueError(f"grid {ev.shape} != mask {mask.shape}")
    ev.fea.apply_K = WarpApplyK(ev.fea)
    m = ev.evaluate(mask, return_fields=True)
    fields = m.pop("fields")
    np.savez_compressed(str(case_dir / "fields.npz"), u_mm=to_numpy(fields["u_mm"]).astype(np.float32),
                        vm_MPa=to_numpy(fields["vm_MPa"]).astype(np.float32))
    m.update({"device": device_name(ev.fea.xp), "dtype": fcase["dtype"], "elements": int(ev.fea.ne),
              "dof": int(ev.fea.ndof), "n_fixed_dofs": int(to_numpy(ev.fea.fixed).sum()), "wall_s": time.time() - t0,
              "backend": BACKENDS["warp"]})
    dump_json(case_dir / "solve.json", m, indent=2)
    return m


def _post(case_dir: Path, mask, h: float, origin, sj: dict, res: dict, lane: str, exclude_mm: float) -> list[str]:
    """vm and |u| slices, fields.vtk and case.md, as the adapter's stage_post computes the fields (not called)."""
    import numpy as np
    from simlab.post import case_markdown, plot_field_slices, write_vtk_structured

    nx, ny, nz = mask.shape
    fz = np.load(case_dir / "fields.npz")
    vm = np.asarray(fz["vm_MPa"]).reshape(mask.shape)
    u = np.asarray(fz["u_mm"]).reshape(-1, 3)
    umag = np.sqrt((u.astype(np.float64) ** 2).sum(axis=1))
    un = umag.reshape(nz + 1, ny + 1, nx + 1).transpose(2, 1, 0)
    ue = 0.125 * (un[:-1, :-1, :-1] + un[1:, :-1, :-1] + un[:-1, 1:, :-1] + un[:-1, :-1, 1:]
                  + un[1:, 1:, :-1] + un[1:, :-1, 1:] + un[:-1, 1:, 1:] + un[1:, 1:, 1:])
    figs = []
    vmax = float(sj.get("vm_max_MPa") or np.nanmax(np.where(mask, vm, np.nan)))
    p = plot_field_slices(vm, mask, h, str(case_dir / "vm_slices.png"), "von Mises (element centres), preview-grade", "MPa",
                          cmap="inferno", origin=origin, vmax=vmax)
    if p:
        figs.append(p)
    p = plot_field_slices(ue, mask, h, str(case_dir / "disp_slices.png"), "displacement magnitude, preview-grade", "mm",
                          cmap="viridis", origin=origin)
    if p:
        figs.append(p)
    vtk = write_vtk_structured(str(case_dir / "fields.vtk"), mask.shape, h, origin,
                               cell_data={"solid": mask.astype(np.float32), "vm_MPa": vm.astype(np.float32),
                                          "u_mag_mm": ue.astype(np.float32)},
                               point_data={"u_mm": u.astype(np.float32)})
    numbers = {k: res[k] for k in ("mass_kg", "mesh_volume_mm3", "voxel_volume_mm3", "compliance_Nmm", "umax_mm",
                                   "vm_p99_MPa", "vm_p95_MPa", "vm_max_MPa", "pcg_iters", "pcg_relres", "elements")}
    numbers["elem_mm"] = h
    notes = [f"Geometry `part.stl`: organic skin (marching cubes of an implicit field), mesh volume "
             f"{res['mesh_volume_mm3']:.1f} mm³; mass {res['mass_kg']:.6g} kg from the mesh volume. Voxelised at {h:g} mm "
             f"into {int(mask.sum())} solid cells of {mask.size} (voxel/mesh volume "
             f"{res['voxel_volume_mm3'] / res['mesh_volume_mm3']:.4f}).",
             f"Solve: {sj.get('device')} float64 on lane {lane}, {res['pcg_iters']} "
             f"{'multigrid-PCG' if res.get('fea_solver') == 'mg' else 'PCG'} iterations, true relative residual "
             f"{res['pcg_relres']:.2e}, {'converged' if res['converged'] else 'NOT converged'}. Constrained stress is vm_p99 "
             f"over the solid elements outside the boundary-condition zones (+-{exclude_mm:g} mm); vm_max is recorded but "
             "is resolution-sensitive.",
             "preview-grade: a voxel solve on the rig is evidence, not sign-off." if str(lane).startswith("rig")
             else "preview-grade: a desktop voxel solve is evidence, not sign-off."]
    md = case_dir / "case.md"
    case_markdown(str(md), f"case {case_dir.name} · preview-grade", numbers, notes, figs)
    write_text(md, md.read_text(encoding="utf-8"))  # LF line endings
    return figs + [vtk, str(md)]


def _voxel_and_check(out: dict, st: _Stage, mesh_path, eff: dict, case_dir: Path, h_mm: float, origin, env,
                     reuse_mask_from):
    """Steps 1 and 2 of solve_mesh: the mask (voxelised or reused) and the structural checks. Returns (mask, h, mesh
    record), or None after setting out["status"] when the part cannot be solved."""
    import numpy as np

    if reuse_mask_from is not None:
        src = Path(reuse_mask_from)
        shutil.copyfile(src / "mask.npy", case_dir / "mask.npy")
        shutil.copyfile(src / "mesh.json", case_dir / "mesh.json")
        mask = np.load(case_dir / "mask.npy").astype(bool)
        mj = read_json(case_dir / "mesh.json")
        h = float(mj["elem_mm"])
        if abs(h - float(h_mm)) > 1e-9:
            raise ValueError(f"the reused mask is at {h:g} mm, the case asks for {float(h_mm):g} mm")
    else:
        import trimesh
        from gendesign.evaluators import voxelize_mesh

        h = float(h_mm)
        m = trimesh.load(str(mesh_path), force="mesh")
        if not m.is_watertight:
            out["wall_s_voxel"] = st.end("FAILED")
            out.update(status="not-watertight", message=f"{Path(mesh_path).name} is not watertight: refused "
                       "(the voxel fallback over-fills open meshes)")
            return None
        mask = np.asarray(voxelize_mesh(m, env, h, origin), dtype=bool)
        mesh_vol = float(m.volume)
        vox_vol = float(mask.sum()) * h ** 3
        mj = {"elem_mm": h, "origin_mm": origin, "envelope_mm": env, "shape": [int(n) for n in mask.shape],
              "cells": int(mask.size), "solid_cells": int(mask.sum()), "solid_fraction": float(mask.mean()),
              "mesh_volume_mm3": mesh_vol, "voxel_volume_mm3": vox_vol,
              "volume_ratio_voxel_over_mesh": vox_vol / mesh_vol if mesh_vol > 0 else None, "watertight": True,
              "source": "mesh", "mesh_path": str(Path(mesh_path).resolve())}
        np.save(case_dir / "mask.npy", mask)
        dump_json(case_dir / "mesh.json", mj)
    out["voxel"] = {"h_mm": h, "cells": int(mj["cells"]), "solid_cells": int(mj["solid_cells"]),
                    "voxel_volume_mm3": float(mj["voxel_volume_mm3"]), "mesh_volume_mm3": mj["mesh_volume_mm3"],
                    "volume_ratio_voxel_over_mesh": mj["volume_ratio_voxel_over_mesh"],
                    "mask_sha256": sha256_file(case_dir / "mask.npy")}
    if int(mj["solid_cells"]) == 0:
        out["wall_s_voxel"] = st.end("FAILED")
        out.update(status="empty", message=f"no solid cell at {h:g} mm")
        return None
    checks, status, message = structural_checks(mask, h, origin, eff)
    out["checks"] = checks
    out["wall_s_voxel"] = st.end()
    if status != "ok":
        out.update(status=status, message=message)
        return None
    return mask, h, mj


def _attempt(sj: dict, solver: str) -> dict:
    return {"solver": solver, "iters": int(sj.get("pcg_iters") or 0), "relres": float(sj["pcg_relres"]),
            "wall_s": float(sj["wall_s"]) if isinstance(sj.get("wall_s"), (int, float)) else None}


def solve_mesh(mesh_path: Path, domain: dict, eff: dict, case_dir: Path, *, h_mm: float, device: str,
               backend: str = "adapter", post: bool = False, reuse_mask_from: Path | None = None,
               solver: str = "pcg", lane: str | None = None) -> dict:
    """Voxelise (or reuse a mask), check, solve and measure one part. Returns {"status": "ok"|design status, "message",
    "results": {metrics}|None, "voxel", "checks", "stress", "wall_s_voxel", "wall_s_solve", "wall_s_post",
    "artifacts", "stages"}. Writes mask.npy, mesh.json, solve.json, fields.npz (+ vm_slices.png, disp_slices.png,
    fields.vtk, case.md with post) in case_dir. A mesh that is not watertight is refused, never voxelised.

    `solver` is pcg, mg or auto (resolve_solver, after the structural checks); the results carry fea_solver (what
    solved), fea_solver_reason, mg_levels when multigrid ran, and fea_solver_attempts when an MG solve that stopped above
    the tolerance was solved again with PCG. `lane` only labels case.md (default lane_of(device, backend))."""
    import numpy as np

    if backend not in BACKENDS:
        raise ValueError(f"unknown backend {backend!r}: adapter or warp")
    if str(solver or "pcg") not in FEA_SOLVERS:
        raise ValueError(f"unknown fea solver {solver!r}: pcg, mg or auto")
    case_dir = Path(case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)
    d = eff["derived"]
    origin = [float(v) for v in d["grid"]["origin_mm"]]
    env = [float(v) for v in d["grid"]["envelope_mm"]]
    mat, sol = eff["material"], eff["solver"]
    stages: list[dict] = []
    out = {"status": "ok", "message": "", "results": None, "voxel": None, "checks": None, "stress": None,
           "wall_s_voxel": None, "wall_s_solve": None, "wall_s_post": None, "artifacts": [], "stages": stages}

    # 1-2. mask and structural checks
    with _Stage(stages, "voxel", "cpu") as st:
        got = _voxel_and_check(out, st, mesh_path, eff, case_dir, h_mm, origin, env, reuse_mask_from)
    if got is None:
        return out
    mask, h, mj = got

    # 3. solve (the linear solver is resolved after the structural checks: the component count is known)
    if backend == "warp":
        fea_solver = "pcg"
        reason = "pcg requested" if str(solver or "pcg") == "pcg" else "the Warp backend is Jacobi-PCG with the Warp K product"
    else:
        fea_solver, reason = resolve_solver(solver, int(mask.size), int((out.get("checks") or {}).get("solid_components") or 0))
    loads = []
    for ld in eff["loads"]:
        e = {"box": [float(v) for v in ld["box"]], "force": [float(f) for f in ld["force"]], "case": ld["case"]}
        if ld.get("axis"):
            e["axis"] = ld["axis"]
        loads.append(e)
    fcase = {"E": float(mat["E_MPa"]), "nu": float(mat["nu"]), "rho_kg_mm3": float(mat["rho_kg_mm3"]),
             "supports": [{"box": [float(v) for v in s["box"]], "dofs": [int(x) for x in s["dofs"]]} for s in eff["supports"]],
             "loads": loads, "vm_exclude": [[float(v) for v in b] for b in domain["stress_exclude"]], "dtype": "float64",
             "tol": float(sol["tol"]), "maxiter": int(sol["maxiter"]), "cpu": device == "cpu",
             "fea_solver": fea_solver, "repair": False, "mg_levels": MG_LEVELS}
    attempts: list[dict] = []
    with _Stage(stages, "solve", "cpu" if device == "cpu" else "gpu") as st:
        if backend == "warp":
            sj = _solve_warp(fcase, mask, h, origin, env, case_dir)
        else:
            sj = _solve_adapter(fcase, case_dir)
        if device == "gpu" and str(sj.get("device", "")).startswith("cpu"):
            raise RuntimeError("a GPU solve was asked for but gendesign.fea3d fell back to NumPy (no usable CUDA "
                               "device); the result is not recorded as a GPU solve")
        ran = str(sj.get("solver") or "pcg")  # the evaluator records what solved; an evaluator without the key is PCG
        if fea_solver == "mg" and ran != "mg":
            reason += "; the step-structures adapter in this snapshot does not pass fea_solver: Jacobi-PCG solved"
        if ran == "mg" and float(sj["pcg_relres"]) > float(sol["tol"]):
            first = _attempt(sj, "mg")
            fcase["fea_solver"] = "pcg"
            sj = _solve_adapter(fcase, case_dir)
            ran = str(sj.get("solver") or "pcg")
            attempts = [first, _attempt(sj, ran)]
            reason += (f"; multigrid stopped at relative residual {first['relres']:.3e} > tol {float(sol['tol']):g} after "
                       f"{first['iters']} iterations: the same mask was solved again with Jacobi-PCG")
    out["wall_s_solve"] = st.rec["wall_s"]

    # 4. metrics
    fz = np.load(case_dir / "fields.npz")
    vm = np.asarray(fz["vm_MPa"], dtype=np.float64).reshape(-1)
    excl = np.zeros(mask.shape, dtype=bool)
    for b in domain["stress_exclude"]:
        excl |= box_elems(b, origin, mask.shape, h)
    vals = vm[(mask & ~excl).reshape(-1)]
    p99 = float(np.percentile(vals, 99)) if vals.size else float("nan")
    p95 = float(np.percentile(vals, 95)) if vals.size else float("nan")
    rho = float(mat["rho_kg_mm3"])
    mesh_vol = float(mj["mesh_volume_mm3"]) if mj.get("mesh_volume_mm3") is not None else float("nan")
    relres = float(sj["pcg_relres"])
    res = {"mass_kg": mesh_vol * rho, "mass_voxel_kg": float(sj["mass_kg"]), "mesh_volume_mm3": mesh_vol,
           "voxel_volume_mm3": float(sj["volume_mm3"]), "solid_fraction": float(sj["solid_fraction"]),
           "compliance_Nmm": float(sj["compliance_Nmm"]), "umax_mm": float(sj["umax_mm"]),
           "vm_max_MPa": float(sj["vm_max_MPa"]), "vm_p99_MPa": p99, "vm_p95_MPa": p95,
           "pcg_iters": int(sj["pcg_iters"]), "pcg_relres": relres, "converged": bool(relres <= float(sol["tol"])),
           "elements": int(sj["elements"]), "dof": int(sj["dof"]), "elem_mm": h, "n_fixed_dofs": int(sj["n_fixed_dofs"]),
           "device": str(sj["device"]), "fea_solver": ran, "fea_solver_reason": reason}
    if ran == "mg":
        res["mg_levels"] = int(sj.get("mg_levels") or MG_LEVELS)
    if attempts:
        res["fea_solver_attempts"] = attempts
    out["results"] = res
    # the zones' width as domain.prepare snapped it to the stress lattice (the spec value when the record predates that)
    exclude_mm = float(domain.get("stress_exclude_effective_mm", d["stress_exclude_mm"]))
    out["stress"] = {"measure": "vm_p99", "exclude_mm": exclude_mm,
                     "exclude_boxes": [[float(v) for v in b] for b in domain["stress_exclude"]], "n_elements": int(vals.size)}
    if not res["converged"]:
        out.update(status="unconverged", message=f"PCG stopped at relative residual {relres:.3e} > tol {float(sol['tol']):g} "
                   f"after {res['pcg_iters']} iterations")
    elif not all(isinstance(res[k], float) and math.isfinite(res[k]) for k in METRICS):
        bad = [k for k in METRICS if not (isinstance(res[k], float) and math.isfinite(res[k]))]
        out.update(status="non-finite", message=f"non-finite metric(s): {', '.join(bad)}")

    # 5. post
    arts = [str(case_dir / n) for n in ("mask.npy", "mesh.json", "solve.json", "fields.npz")]
    if post and out["status"] == "ok":
        with _Stage(stages, "post", "cpu") as st:
            arts += _post(case_dir, mask, h, origin, sj, res, lane or lane_of(device, backend), exclude_mm)
        out["wall_s_post"] = st.rec["wall_s"]
    out["artifacts"] = arts
    return out
