"""Connectivity repair for voxelised assemblies.

Voxelising a multi-body STEP can sever thin attachments (a fin root thinner than one
voxel, face-touching solids). Under the old fictitious-domain solve such an island sat
on emin~1e-9 stiffness; with a load on it the system is so ill-conditioned that PCG
never converges and displacements are garbage (measured: 41 m on the HALO-X skeleton
launch case). With the solid-only compressed operator the same island is exactly
singular. Either way it must be fixed in the mask, not the solver.

repair_mask() labels 6-connected components of the solid mask and classifies each:
  * supported  - contains a support node          -> keep
  * free       - no support, carries no load       -> drop (reports volume dropped)
  * loaded     - no support, carries load          -> try to weld: add the 1-voxel gap
                 band between the component and the supported body (binary dilation
                 intersection). If no bridge exists the case is physically broken:
                 raise with the component volume and centroid so the spec can be fixed.
Everything is reported in the returned dict so results.json carries an audit trail.
"""
from __future__ import annotations

from typing import Dict, Tuple

import numpy as np

from .fea3d import to_numpy

_STRUCT6 = np.zeros((3, 3, 3), dtype=bool)
_STRUCT6[1, 1, :] = True
_STRUCT6[1, :, 1] = True
_STRUCT6[:, 1, 1] = True


def _elem_flag_from_nodes(node_flag: np.ndarray, nx: int, ny: int, nz: int) -> np.ndarray:
    """Element flag = any of its 8 corner nodes flagged. node_flag is (nx+1,ny+1,nz+1)."""
    g = node_flag
    out = np.zeros((nx, ny, nz), dtype=bool)
    for di in (0, 1):
        for dj in (0, 1):
            for dk in (0, 1):
                out |= g[di:di + nx, dj:dj + ny, dk:dk + nz]
    return out


def repair_mask(mask: np.ndarray, fea, weld: bool = True) -> Tuple[np.ndarray, Dict]:
    import scipy.ndimage as ndi

    nx, ny, nz = mask.shape
    report: Dict = {"components": 1, "dropped_unsupported_unloaded": [],
                    "welded": [], "kept": True}
    lab, n = ndi.label(mask, structure=_STRUCT6)
    report["components"] = int(n)
    if n <= 1:
        return mask, report

    fixed_flat = to_numpy(fea.fixed).reshape(-1, 3).any(axis=1)
    fixed = fixed_flat.reshape(nz + 1, ny + 1, nx + 1).transpose(2, 1, 0)
    elem_supported = _elem_flag_from_nodes(fixed, nx, ny, nz)
    load_nodes = np.zeros((nx + 1, ny + 1, nz + 1), dtype=bool)
    for F in fea.loads.values():
        lf = (np.abs(to_numpy(F)).reshape(-1, 3) > 0).any(axis=1)
        load_nodes |= lf.reshape(nz + 1, ny + 1, nx + 1).transpose(2, 1, 0)
    elem_loaded = _elem_flag_from_nodes(load_nodes, nx, ny, nz)

    comp_supported = ndi.maximum(elem_supported.astype(np.int8), lab, index=np.arange(1, n + 1)) > 0
    comp_loaded = ndi.maximum(elem_loaded.astype(np.int8), lab, index=np.arange(1, n + 1)) > 0
    comp_vox = np.bincount(lab.ravel(), minlength=n + 1)[1:]

    out = mask.copy()
    supported_union = mask & np.isin(lab, np.flatnonzero(comp_supported) + 1)
    h3 = float(fea.h) ** 3

    for cid in range(1, n + 1):
        if comp_supported[cid - 1]:
            continue
        comp = lab == cid
        vol_mm3 = float(comp_vox[cid - 1]) * h3
        if not comp_loaded[cid - 1]:
            out &= ~comp
            report["dropped_unsupported_unloaded"].append(
                {"component": cid, "voxels": int(comp_vox[cid - 1]), "volume_mm3": vol_mm3})
            continue
        # loaded island: try to weld it to the supported body across a <=1 voxel gap
        if not weld:
            raise RuntimeError(
                f"loaded component {cid} ({int(comp_vox[cid - 1])} voxels, "
                f"{vol_mm3:.0f} mm^3) is disconnected from the supports and welding "
                f"is disabled; the solve would be singular")
        bridge = (ndi.binary_dilation(comp, structure=_STRUCT6)
                  & ndi.binary_dilation(supported_union, structure=_STRUCT6)
                  & ~out)
        if not bridge.any():
            cen = ndi.center_of_mass(comp)
            cen_mm = [(float(c) + 0.5) * float(fea.h) for c in cen]
            raise RuntimeError(
                f"loaded component {cid} ({int(comp_vox[cid - 1])} voxels, "
                f"{vol_mm3:.0f} mm^3, centroid ~{[round(c,1) for c in cen_mm]} mm in "
                f"grid coords) is disconnected from the supports by more than one "
                f"voxel; fix the CAD join or the mesh resolution")
        out |= bridge
        supported_union |= comp | bridge
        report["welded"].append({"component": cid, "voxels": int(comp_vox[cid - 1]),
                                 "volume_mm3": vol_mm3,
                                 "bridge_voxels": int(bridge.sum())})

    # verify: relabel after welding; any remaining loaded island is fatal
    lab2, n2 = ndi.label(out, structure=_STRUCT6)
    if n2 > 1:
        comp_supported2 = ndi.maximum(elem_supported.astype(np.int8), lab2,
                                      index=np.arange(1, n2 + 1)) > 0
        comp_loaded2 = ndi.maximum(elem_loaded.astype(np.int8), lab2,
                                   index=np.arange(1, n2 + 1)) > 0
        bad = [i + 1 for i in range(n2) if comp_loaded2[i] and not comp_supported2[i]]
        if bad:
            raise RuntimeError(
                f"loaded components {bad} still disconnected after welding")
        comp_vox2 = np.bincount(lab2.ravel(), minlength=n2 + 1)[1:]
        for i in range(n2):
            if not comp_supported2[i] and not comp_loaded2[i]:
                out &= lab2 != (i + 1)
                report["dropped_unsupported_unloaded"].append(
                    {"component": i + 1, "voxels": int(comp_vox2[i]),
                     "volume_mm3": float(comp_vox2[i]) * h3})
    report["final_components"] = int(ndi.label(out, structure=_STRUCT6)[1])
    return out, report
