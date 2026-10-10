"""STEP -> voxel FEA -> fields: the CAD-to-result path for structural checks of a part.

Stages (each may run as its own queue job; `run` executes them back to back):
  mesh  (cpu)  STEP -> tessellate (build123d/OCP) -> centre-point voxelisation on the envelope grid
               -> mask.npy, mesh.json (solid fraction, voxel volume vs B-rep volume), mask_slices.png
               A multi-solid STEP whose fused tessellation is not watertight is voxelised solid by solid and the
               masks are ORed (mesh.json "watertight": "per-solid-union(n)"): interfering parts (wing roots in a
               fuselage) tessellate open when fused, and the dilated fallback over-fills by up to 2x (the rig
               operator's patch of 2026-10-06, upstreamed in rig-golden-1.1)
  solve (gpu)  VoxelFEAEvaluator with the case's supports/loads -> compliance, u_max, VM max, mass;
               fields.npz (u per node in mm, VM per element in MPa)
  post  (cpu)  VM + |u| slice PNGs through the hot spot, fields.vtk (ParaView), case.md, results.json

Case parameters (mm, N, MPa; coordinates in the STEP's own frame):
  step_file      path relative to the spec (default: the experiment's geometry.ref)
  elem_mm        voxel size (default 1.0)
  envelope_mm    [Lx, Ly, Lz] grid extent; default = STEP bounding box + margin_mm
  origin_mm      grid origin; default = bbox min - margin_mm
  margin_mm      default 0 (the grid hugs the part)
  E, nu, rho_kg_mm3
  supports       [{"box": [x0,y0,z0,x1,y1,z1], "dofs": [0,1,2]}]      (part frame)
  loads          [{"box": [...], "force": [Fx,Fy,Fz], "axis": "x"|"y"|"z"}]  axis => consistent face traction
  vm_exclude     [[box], ...]  stress read-out ignores these zones (load patches)
  tol, maxiter, dtype
  fea_solver     "mg" (default: geometric-multigrid PCG on the solid-only operator, gendesign.mg; maxiter capped at
                 2000 by the evaluator) or "pcg" (Jacobi-PCG on the fictitious-domain operator); stage_solve passes it
                 to VoxelFEAEvaluator(solver=...) and solve.json records it as "solver". The rig operator's key
                 "solver" (the rig's step_structures.py since 2026-10-06; 14 of the rig's case files use it) is
                 honoured too; fea_solver wins when both are given.
  emin           default 1e-9: the relative stiffness of void cells (the rig operator's key, same patch)
  repair         default true: gendesign.connectivity.repair_mask drops unsupported unloaded islands and welds loaded
                 ones across a one-voxel gap before the solve (solve.json "connectivity"); false solves the mask as given
                 (the aces golden path passes false so the solved mask is the mask it hashed)
  mg_levels      default 4: the most multigrid levels when the solver is "mg"
                 Without these keys the solve is exactly what the rig has run since the operator's patch of
                 2026-10-06: mg, repair on, 4 levels, emin 1e-9 (the evaluator's own default solver is pcg).
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List

import numpy as np

STAGES = {"mesh": "cpu", "solve": "sim", "post": "cpu"}


def _box_to_grid(box, origin):
    x0, y0, z0, x1, y1, z1 = [float(v) for v in box]
    ox, oy, oz = origin
    return [x0 - ox, y0 - oy, z0 - oz, x1 - ox, y1 - oy, z1 - oz]


class Adapter:
    name = "step-structures"
    stages = STAGES

    # ---- helpers ----
    def step_path(self, case: dict, spec) -> str:
        ref = case.get("step_file") or spec.geometry.ref
        if not ref:
            raise ValueError("step-structures needs geometry.ref or case.step_file")
        return spec.resolve(ref)

    def inputs(self, case: dict, spec) -> list:
        return [self.step_path(case, spec)]

    def grid(self, case: dict, bbox: List[float]):
        h = float(case.get("elem_mm", 1.0))
        margin = float(case.get("margin_mm", 0.0))
        if "origin_mm" in case:
            origin = [float(v) for v in case["origin_mm"]]
        else:
            origin = [bbox[0] - margin, bbox[1] - margin, bbox[2] - margin]
        if "envelope_mm" in case:
            env = [float(v) for v in case["envelope_mm"]]
        else:
            env = [bbox[3] - bbox[0] + 2 * margin, bbox[4] - bbox[1] + 2 * margin, bbox[5] - bbox[2] + 2 * margin]
        # snap the envelope up to whole voxels so the part never gets clipped
        env = [np.ceil(L / h - 1e-9) * h for L in env]
        return h, origin, env

    # ---- stages ----
    def run_stage(self, stage: str, case: dict, spec, run_dir: str) -> dict:
        return getattr(self, "stage_" + stage)(case, spec, run_dir)

    def stage_mesh(self, case: dict, spec, run_dir: str) -> dict:
        import trimesh
        from build123d import import_step

        from gendesign.evaluators import voxelize_mesh
        from gendesign.export import plot_slices

        t0 = time.time()
        step = self.step_path(case, spec)
        shapes = import_step(step)
        solids = list(shapes.solids()) if hasattr(shapes, "solids") else [shapes]
        if not solids:
            raise ValueError(f"no solids in {step}")
        brep_volume = float(sum(s.volume for s in solids))
        if len(solids) > 1:
            # touching bodies (a doubler welded to a chord) become one shell, so the centre-point
            # containment test never sees coincident faces
            fused = solids[0]
            for s_ in solids[1:]:
                fused = fused.fuse(s_)
            shapes = fused.clean()
        bb = shapes.bounding_box()
        bbox = [bb.min.X, bb.min.Y, bb.min.Z, bb.max.X, bb.max.Y, bb.max.Z]
        h, origin, env = self.grid(case, bbox)
        # tessellate once (fine tolerance: the voxel test is centre-point containment)
        stl_path = os.path.join(run_dir, "tessellation.stl")
        tol = float(case.get("tessellation_mm", min(0.05, h / 10)))
        try:
            from build123d import export_stl

            export_stl(shapes, stl_path, tolerance=tol, angular_tolerance=0.1)
        except TypeError:
            export_stl(shapes, stl_path)
        tm = trimesh.load(stl_path, force="mesh")
        if not tm.is_watertight:
            # merge duplicate vertices from per-face tessellation; still not watertight -> the
            # fallback in voxelize_mesh over-fills by a layer and we say so
            tm.merge_vertices()
            tm.fix_normals()
        if not tm.is_watertight and len(solids) > 1:
            # per-solid union: interfering parts (wing roots in the fuselage, equipment
            # blocks on the deck) make the fused shape tessellate open and the dilated
            # fallback over-fills by up to 2x; each source solid is clean, so voxelise
            # each one and OR the masks - overlaps union correctly by construction
            mask = None
            n_sub = 0
            for si, s_ in enumerate(solids):
                p_ = os.path.join(run_dir, f"tess_{si}.stl")
                try:
                    export_stl(s_, p_, tolerance=tol, angular_tolerance=0.1)
                except TypeError:
                    export_stl(s_, p_)
                t_ = trimesh.load(p_, force="mesh")
                if not t_.is_watertight:
                    t_.merge_vertices()
                    t_.fix_normals()
                m_ = voxelize_mesh(t_, env, h, origin)
                mask = m_ if mask is None else (mask | m_)
                n_sub += 1
            watertight = f"per-solid-union({n_sub})"
        else:
            mask = voxelize_mesh(tm, env, h, origin)
            watertight = bool(tm.is_watertight)
        np.save(os.path.join(run_dir, "mask.npy"), mask)
        vox_volume = float(mask.sum()) * h ** 3
        info = {
            "step": step, "solids": len(solids), "brep_volume_mm3": brep_volume, "bbox_mm": bbox,
            "elem_mm": h, "origin_mm": origin, "envelope_mm": env, "shape": [int(v) for v in mask.shape],
            "cells": int(mask.size), "solid_cells": int(mask.sum()), "solid_fraction": float(mask.mean()),
            "voxel_volume_mm3": vox_volume, "volume_ratio_voxel_over_brep": vox_volume / brep_volume if brep_volume else None,
            "watertight": watertight, "tessellation_faces": int(len(tm.faces)),
            "wall_s": time.time() - t0,
        }
        with open(os.path.join(run_dir, "mesh.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2)
        plot_slices(mask.astype(float), h, os.path.join(run_dir, "mask_slices.png"),
                    f"{os.path.basename(step)}: {info['solid_cells']} solid voxels of {info['cells']} at {h} mm")
        return info

    def stage_solve(self, case: dict, spec, run_dir: str) -> dict:
        from gendesign.evaluators import VoxelFEAEvaluator
        from gendesign.fea3d import device_name, to_numpy

        t0 = time.time()
        with open(os.path.join(run_dir, "mesh.json"), "r", encoding="utf-8") as f:
            mesh = json.load(f)
        mask = np.load(os.path.join(run_dir, "mask.npy"))
        h, origin, env = mesh["elem_mm"], mesh["origin_mm"], mesh["envelope_mm"]
        dtype = case.get("dtype", spec.resources.precision)
        E = float(case.get("E", 200000.0))
        supports = [{"box": _box_to_grid(s["box"], origin), "dofs": tuple(s.get("dofs", (0, 1, 2)))} for s in case.get("supports", [])]
        loads = []
        for ld in case.get("loads", []):
            d = {"box": _box_to_grid(ld["box"], origin), "force": ld["force"], "case": ld.get("case", "default")}
            if "axis" in ld:
                d["axis"] = ld["axis"]
            loads.append(d)
        if not supports or not loads:
            raise ValueError("step-structures needs at least one support and one load")
        ev = VoxelFEAEvaluator(env, h, E=E, nu=float(case.get("nu", 0.3)), rho_kg_mm3=float(case.get("rho_kg_mm3", 7.85e-6)),
                               supports=supports, loads=loads, dtype=dtype, prefer_gpu=not case.get("cpu", False),
                               tol=float(case.get("tol", 1e-8 if dtype == "float64" else 1e-4)),
                               maxiter=int(case.get("maxiter", 20000)), warm_start=False,
                               vm_exclude_boxes=[_box_to_grid(b, origin) for b in case.get("vm_exclude", [])],
                               emin=float(case.get("emin", 1e-9)),
                               solver=str(case.get("fea_solver", case.get("solver", "mg"))),
                               repair=bool(case.get("repair", True)),
                               mg_levels=int(case.get("mg_levels", 4)))
        if tuple(ev.shape) != tuple(mask.shape):
            raise ValueError(f"grid {ev.shape} != mask {mask.shape}")
        m = ev.evaluate(mask, return_fields=True)
        fields = m.pop("fields")
        np.savez_compressed(os.path.join(run_dir, "fields.npz"),
                            u_mm=to_numpy(fields["u_mm"]).astype(np.float32),
                            vm_MPa=to_numpy(fields["vm_MPa"]).astype(np.float32))
        m.update({"device": device_name(ev.fea.xp), "dtype": dtype, "elements": int(ev.fea.ne), "dof": int(ev.fea.ndof),
                  "n_fixed_dofs": int(to_numpy(ev.fea.fixed).sum()), "wall_s": time.time() - t0})
        with open(os.path.join(run_dir, "solve.json"), "w", encoding="utf-8") as f:
            json.dump(m, f, indent=2)
        return m

    def stage_post(self, case: dict, spec, run_dir: str) -> dict:
        from ..post import case_markdown, plot_field_slices, write_vtk_structured

        t0 = time.time()
        with open(os.path.join(run_dir, "mesh.json"), "r", encoding="utf-8") as f:
            mesh = json.load(f)
        with open(os.path.join(run_dir, "solve.json"), "r", encoding="utf-8") as f:
            solve = json.load(f)
        mask = np.load(os.path.join(run_dir, "mask.npy"))
        fz = np.load(os.path.join(run_dir, "fields.npz"))
        h, origin, shape = mesh["elem_mm"], mesh["origin_mm"], tuple(mesh["shape"])
        vm = fz["vm_MPa"].reshape(shape)
        u = fz["u_mm"].reshape(-1, 3)
        umag = np.sqrt((u ** 2).sum(axis=1))
        # |u| per element = mean of its 8 nodes (node order i + (nx+1)(j + (ny+1)k))
        nx, ny, nz = shape
        un = umag.reshape(nz + 1, ny + 1, nx + 1).transpose(2, 1, 0)  # -> (nx+1, ny+1, nz+1)
        ue = 0.125 * (un[:-1, :-1, :-1] + un[1:, :-1, :-1] + un[:-1, 1:, :-1] + un[:-1, :-1, 1:] +
                      un[1:, 1:, :-1] + un[1:, :-1, 1:] + un[:-1, 1:, 1:] + un[1:, 1:, 1:])
        figs = []
        p = plot_field_slices(vm, mask, h, os.path.join(run_dir, "vm_slices.png"), "von Mises (element centres)", "MPa",
                              cmap="inferno", origin=origin, vmax=float(solve.get("vm_max_MPa") or np.nanmax(vm[mask])))
        if p:
            figs.append(p)
        p = plot_field_slices(ue, mask, h, os.path.join(run_dir, "disp_slices.png"), "displacement magnitude", "mm",
                              cmap="viridis", origin=origin)
        if p:
            figs.append(p)
        vtk = write_vtk_structured(os.path.join(run_dir, "fields.vtk"), shape, h, origin,
                                   cell_data={"solid": mask.astype(np.float32), "vm_MPa": vm.astype(np.float32),
                                              "u_mag_mm": ue.astype(np.float32)},
                                   point_data={"u_mm": u.astype(np.float32)})
        numbers = {k: solve[k] for k in ("compliance_Nmm", "umax_mm", "vm_max_MPa", "volume_mm3", "mass_kg", "solid_fraction",
                                         "pcg_iters", "pcg_relres", "converged") if k in solve}
        numbers.update({"elements": solve.get("elements"), "elem_mm": h, "volume_ratio_voxel_over_brep": mesh.get("volume_ratio_voxel_over_brep")})
        notes = [f"Geometry `{os.path.basename(mesh['step'])}`: {mesh['solids']} solid(s), B-rep volume {mesh['brep_volume_mm3']:.1f} mm³, "
                 f"voxelised at {h} mm into {mesh['solid_cells']} cells ({100 * mesh['solid_fraction']:.1f} % of the {mesh['cells']}-cell grid).",
                 f"Solve: {solve.get('device')} {solve.get('dtype')}, {int(solve.get('pcg_iters', 0))} PCG iterations, true relative residual {solve.get('pcg_relres', 0):.2e}, "
                 f"{'converged' if solve.get('converged') else 'NOT converged'}."]
        case_markdown(os.path.join(run_dir, "case.md"), f"case {case.get('case_id', 'c000')}", numbers, notes, figs)
        results = dict(numbers)
        results.update({"wall_s_mesh": mesh.get("wall_s"), "wall_s_solve": solve.get("wall_s"), "wall_s_post": time.time() - t0,
                        "device": solve.get("device"), "dof": solve.get("dof"),
                        "artifacts": figs + [vtk, os.path.join(run_dir, "mask_slices.png"), os.path.join(run_dir, "case.md"),
                                             os.path.join(run_dir, "mesh.json"), os.path.join(run_dir, "solve.json")]})
        return results

    def run(self, case: dict, spec, run_dir: str) -> dict:
        """Single-process path (`simlab run` and the no-stage fallback): mesh -> solve -> post."""
        self.stage_mesh(case, spec, run_dir)
        self.stage_solve(case, spec, run_dir)
        return self.stage_post(case, spec, run_dir)
