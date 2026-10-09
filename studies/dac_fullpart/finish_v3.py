# Finish the v3 build from the checkpoint field: rethreshold core at vf>0.25,
# QA, mesh, write STL, measure the AS-RASTERISED network porosity/dh and report
# the resulting operating dP correction. Study tier, NOT VERIFIED.
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from aces.domains.hx.tpms import mesh_volume, write_stl
from studies.dac_fullpart.fullpart import W_FACE, H_FACE, TANK_H
from studies.dac_fullpart.fullpart_v3 import A0, B0, DH0, AIR, HX_REQ

TAG = sys.argv[1] if len(sys.argv) > 1 else "v3"
OUT = ROOT / "out" / f"dac-fullpart-{TAG}"
P = json.load(open(OUT / f"selected-{TAG}.json"))["params"]
H_VOX = 0.0015
THR = float(sys.argv[2]) if len(sys.argv) > 2 else 0.25  # vf threshold; thick walls: use 0.5 (volume-correct)


def main():
    t0 = time.time()
    solid = np.load(OUT / "v3_field.npy")
    a = P["cell_m"]; h = H_VOX
    Lp, Lc = P["plenum_len_m"], 3 * a
    wg = P["gallery_w_m"]
    ny = max(3, int(round((W_FACE - 2 * wg) / a)))
    nz = max(3, int(round(H_FACE / a)))
    Wc, Hc = ny * a, nz * a
    yc0, zc0 = (W_FACE - Wc) / 2, (H_FACE - Hc) / 2
    m_ = P["casing_t_m"] + 2 * h
    xs = np.arange(-m_, Lp + Lc + 0.6 * P["plenum_len_m"] + m_ + h, h)
    ys = np.arange(-m_, W_FACE + m_ + h, h)
    zs = np.arange(-TANK_H - m_, H_FACE + m_ + h, h)
    s = lambda arr, v0, v1: (int(np.searchsorted(arr, v0)), int(np.searchsorted(arr, v1)) + 1)
    ix0, ix1 = s(xs, Lp, Lp + Lc)
    iy0, iy1 = s(ys, yc0, yc0 + Wc)
    iz0, iz1 = s(zs, zc0, zc0 + Hc)

    # rethreshold: stored core value = vf - 0.5 -> shift to vf - THR
    core_sl = np.s_[ix0:ix1, iy0:iy1, iz0:iz1]
    solid[core_sl] += (0.5 - THR)

    from scipy import ndimage
    t2 = int(0.2 * min(Lc, Wc, Hc) / h)
    pr = solid[ix0 + t2:ix1 - t2, iy0 + t2:iy1 - t2, iz0 + t2:iz1 - t2] > 0
    lab, ncomp = ndimage.label(~pr)
    ids_in = set(np.unique(lab[0])) - {0}
    ids_out = set(np.unique(lab[-1])) - {0}
    ids_gal = (set(np.unique(lab[:, 0])) | set(np.unique(lab[:, -1]))) - {0}
    air_ids = ids_in & ids_out
    qa = {
        "vf_threshold": THR,
        "core solid fraction": float(pr.mean()),
        "void components": int(ncomp),
        "air network connects both air faces": bool(air_ids),
        "air network leaks to gallery walls": bool(ids_in & ids_gal),
        "gallery network reaches both galleries": bool(
            (set(np.unique(lab[:, 0])) - {0}) & (set(np.unique(lab[:, -1])) - {0})),
    }
    print(json.dumps(qa, indent=1))
    if qa["air network leaks to gallery walls"] or not qa["air network connects both air faces"]:
        print("QA FAIL; STL not written")
        return

    # ---- as-rasterised network porosity/dh: deep interior window, exact face count
    tx = int(1.05 * a / h); ty = int(3.3 * a / h); tz = int(1.05 * a / h)
    deep = solid[ix0 + tx:ix1 - tx, iy0 + ty:iy1 - ty, iz0 + tz:iz1 - tz] > 0
    lab2, _ = ndimage.label(~deep)
    sizes = np.bincount(lab2.ravel()); sizes[0] = 0
    air_id = int(np.argmax(sizes))
    air_mask = lab2 == air_id

    def iface(vm, sm):
        c = 0
        c += int((vm[:-1] & sm[1:]).sum()) + int((vm[1:] & sm[:-1]).sum())
        c += int((vm[:, :-1] & sm[:, 1:]).sum()) + int((vm[:, 1:] & sm[:, :-1]).sum())
        c += int((vm[:, :, :-1] & sm[:, :, 1:]).sum()) + int((vm[:, :, 1:] & sm[:, :, :-1]).sum())
        return c

    Vv = deep.size * h ** 3
    phi_vox = float(air_mask.sum() / deep.size)
    av_vox = iface(air_mask, deep) * h * h / Vv
    dh_vox = 4 * phi_vox / av_vox
    print(f"as-rasterised air network: phi={phi_vox:.4f} dh={dh_vox*1e3:.3f} mm "
          f"(continuum dh={0.5435*a*1e3:.2f} mm)")
    del lab, pr, lab2, deep, air_mask

    # operating dP with as-rasterised dh (Ergun-form scaling of the measured law)
    Af = (W_FACE - 2 * wg) * H_FACE
    u_s = HX_REQ.cold.mass_flow_kg_s / (AIR.rho * Af)
    dpdx = A0 * (DH0 / dh_vox) ** 2 * u_s + B0 * (DH0 / dh_vox) * u_s ** 2
    dp_core_printed = dpdx * Lc
    print(f"printed-core operating dP ~ {dp_core_printed:.0f} Pa "
          f"(fast-tier Forchheimer model value)")

    # ---- mesh ----------------------------------------------------------------
    np.clip(solid / h, -1.0, 1.0, out=solid)
    padded = np.pad(solid, 1, mode="constant", constant_values=-1.0e9)
    del solid
    from skimage.measure import marching_cubes
    verts, faces, _, _ = marching_cubes(padded, level=0.0, spacing=(h, h, h))
    del padded
    verts -= h
    vol = mesh_volume(verts, faces)
    print(f"mesh: {len(faces)} triangles, enclosed {vol:.4f} m3, {time.time()-t0:.0f}s")
    if vol < 0:
        faces = faces[:, ::-1]
    np.save(OUT / f"{TAG}_faces.npy", faces)
    np.save(OUT / f"{TAG}_verts.npy", verts)
    stl = OUT / f"dac-fullpart-{TAG}.stl"
    write_stl(stl, verts, faces,
              header=f"ACES DAC full part {TAG} supersampled-core vf0.25 (study, not verified)",
              scale=1000.0)
    json.dump({"params": P, "qa": qa, "volume_m3": vol, "triangles": int(len(faces)),
               "voxel_m": h, "supersample": 3,
               "as_rasterised": {"phi_air": phi_vox, "dh_air_m": dh_vox,
                                 "dp_core_printed_Pa": dp_core_printed,
                                 "u_s_air": u_s}},
              open(OUT / "qa_v3_ss.json", "w"), indent=2, default=float)
    print("wrote", stl, f"{time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
