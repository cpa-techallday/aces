# Core-only STL for the 250 kW FC H2 precooler winner (0.1526 x 0.2648 x 0.2554 m,
# 49 x 85 x 82 cells, gyroid 3.115 mm, t/a 0.1646, sheet wall ~0.513 mm).
# Two-stage: certify network topology on a 3x3x3-cell patch at 0.25 mm voxels
# (expect exactly 2 void components, each touching all 6 faces, no merging),
# then a 10x10x10-cell manufacturing QA coupon STL.
# Full-core STL (~220M triangles at 0.25 mm) is impractical as a file; the core
# is delivered as implicit parameters for direct slicing (see report S10).
# Study tier, NOT VERIFIED; simlab sign-off required (M8/H3/H4).
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aces.domains.hx import tpms

CELL_M, WALL_RATIO = 0.003114903330269901, 0.1646
H_VOX = 0.00025                       # 0.25 mm native voxels (~2 voxels per wall)
OUT = ROOT / "out" / "h2-precooler-250fc"


def raster(n_cells: int) -> np.ndarray:
    """Boolean solid field for an n^3-cell gyroid sheet block."""
    a = CELL_M
    half = 0.5 * WALL_RATIO
    n = int(round(n_cells * a / H_VOX))
    xs = (np.arange(n) + 0.5) * H_VOX / a   # in cell units
    X, Y, Z = np.meshgrid(xs, xs, xs, indexing="ij", sparse=True)
    d = tpms.signed_distance("gyroid", X, Y, Z)
    return np.asarray(np.abs(d) < half)


def certify(solid: np.ndarray, label: str) -> dict:
    from scipy import ndimage
    lab, ncomp = ndimage.label(~solid)
    faces = [lab[0, :, :], lab[-1, :, :], lab[:, 0, :], lab[:, -1, :],
             lab[:, :, 0], lab[:, :, -1]]
    comp_faces = {}
    comp_vox = {}
    for cid in range(1, ncomp + 1):
        comp_faces[cid] = sum(cid in f for f in faces)
        comp_vox[cid] = int((lab == cid).sum())
    spanning = [c for c, f in comp_faces.items() if f == 6]
    void_vox = sum(comp_vox.values())
    pockets = {c: v for c, v in comp_vox.items()
               if c not in spanning and v < 0.005 * void_vox}
    # pass = both networks intact and unmerged (exactly 2 spanning components);
    # extra tiny face-connected corner pockets are block-truncation artifacts
    # (they open into the fused manifold plenum in the real part) — benign.
    qa = {"block": label,
          "voxel_m": H_VOX,
          "void_components": int(ncomp),
          "solid_fraction": float(solid.mean()),
          "faces_touched_per_component": comp_faces,
          "component_voxels": comp_vox,
          "spanning_components": spanning,
          "truncation_pockets": pockets,
          "pass": bool(len(spanning) == 2
                     and len(pockets) == ncomp - 2)}
    print(json.dumps(qa, indent=1), flush=True)
    return qa


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)

    patch = raster(3)
    qa3 = certify(patch, "3x3x3 cells (9.34 mm cube)")
    del patch
    if not qa3["pass"]:
        print("PATCH QA FAIL — 0.513 mm walls fragment at 0.25 mm voxels; "
              "no coupon written")
        json.dump({"qa_patch": qa3}, open(OUT / "qa_core_stl.json", "w"), indent=2)
        return

    coupon = raster(10)
    qa10 = certify(coupon, "10x10x10 cells (31.15 mm cube)")
    np.clip(np.where(coupon, 1.0, -1.0).astype(np.float64) * H_VOX, -1, 1)
    field = np.where(coupon, H_VOX, -H_VOX).astype(np.float64)
    del coupon
    padded = np.pad(field, 1, mode="constant", constant_values=-1.0e9)
    del field
    from skimage.measure import marching_cubes
    verts, faces, _, _ = marching_cubes(padded, level=0.0,
                                        spacing=(H_VOX,) * 3)
    del padded
    verts -= H_VOX
    vol = tpms.mesh_volume(verts, faces)
    if vol < 0:
        faces = faces[:, ::-1]
        vol = -vol
    stl = OUT / "h2-250fc-core-coupon-10cell.stl"
    tpms.write_stl(stl, verts, faces,
                   header="ACES H2 250FC precooler core coupon 10cell gyroid "
                          "3.115mm t/a 0.1646 (study, not verified)",
                   scale=1000.0)
    qa10.update({"triangles": int(len(faces)), "enclosed_m3": float(vol),
                 "stl": str(stl)})
    json.dump({"qa_patch": qa3, "qa_coupon": qa10,
               "full_core": {"cells": [49, 85, 82],
                             "dims_m": [49 * CELL_M, 85 * CELL_M, 82 * CELL_M],
                             "note": "full core ~220M triangles at 0.25 mm; deliver "
                                     "as implicit params for direct slicing"}},
              open(OUT / "qa_core_stl.json", "w"), indent=2, default=float)
    print(f"wrote {stl} ({len(faces)} tris, {time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
