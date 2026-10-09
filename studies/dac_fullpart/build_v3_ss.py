# v3 full-part build with 3x supersampled core rasterisation (volume-fraction
# anti-aliasing) at 1.5 mm base voxels. Fixes the sub-voxel wall pinhole failure
# found in the naive 3 mm build (air/water network short-circuit).
# Study tier; geometry reuses the layout of studies/dac_fullpart/fullpart.build_part.
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from aces.domains.hx import tpms
from aces.domains.hx.tpms import mesh_volume, write_stl
from studies.dac_fullpart.fullpart import W_FACE, H_FACE, TANK_H

import sys as _s
TAG = _s.argv[1] if len(_s.argv) > 1 and not _s.argv[1].startswith("--") else "v3"
if TAG != "v3": _s.argv.pop(1)
OUT = ROOT / "out" / f"dac-fullpart-{TAG}"
P = json.load(open(OUT / f"selected-{TAG}.json"))["params"]
H_VOX = 0.0015
SS = 3  # supersampling factor in the core region


def main():
    t0 = time.time()
    FIELD = OUT / "v3_field.npy"
    if "--from-field" in sys.argv:
        qa_and_mesh(np.load(FIELD), t0)
        return
    a, ratio = P["cell_m"], P["wall_ratio"]
    Lc, Lp = P["core_len_m"], P["plenum_len_m"]
    hp, wg, tc = P["slot_h_m"], P["gallery_w_m"], P["casing_t_m"]
    Lo = 0.6 * Lp
    nx = max(3, int(round(Lc / a)))
    ny = max(3, int(round((W_FACE - 2 * wg) / a)))
    nz = max(3, int(round(H_FACE / a)))
    Lc, Wc, Hc = nx * a, ny * a, nz * a
    yc0 = (W_FACE - Wc) / 2
    zc0 = (H_FACE - Hc) / 2
    L_tot = Lp + Lc + Lo
    h = H_VOX
    m_ = tc + 2 * h
    x0, x1 = -m_, L_tot + m_
    y0, y1 = -m_, W_FACE + m_
    z0, z1 = -TANK_H - m_, H_FACE + m_
    xs = np.arange(x0, x1 + h, h, dtype=np.float64)
    ys = np.arange(y0, y1 + h, h, dtype=np.float64)
    zs = np.arange(z0, z1 + h, h, dtype=np.float64)
    print(f"grid ({len(xs)}, {len(ys)}, {len(zs)}) = {len(xs)*len(ys)*len(zs)/1e6:.0f} M voxels")

    # ---- coarse casing field (hard boxes, features >= tc >> h) ---------------
    def build_coarse():
        X, Y, Z = np.meshgrid(xs, ys, zs, indexing="ij", sparse=True)
        def box(xa, xb, ya, yb, za, zb):
            return np.minimum(np.minimum(X - xa, xb - X),
                              np.minimum(np.minimum(Y - ya, yb - Y),
                                         np.minimum(Z - za, zb - Z)))
        cham = 0.03
        d_out = (np.maximum(x0 - X, 0) + np.maximum(X - x1, 0)
                 + np.maximum(y0 - Y, 0) + np.maximum(Y - y1, 0)
                 + np.maximum(z0 - Z, 0) + np.maximum(Z - z1, 0))
        solid = np.minimum(box(x0, x1, y0, y1, z0, z1), cham - d_out).astype(np.float32)
        voids = [
            box(x0 + tc, Lp, yc0 + tc, yc0 + Wc - tc, zc0 + tc, zc0 + Hc - tc),
            box(Lp + Lc, x1 - tc, yc0 + tc, yc0 + Wc - tc, zc0 + tc, zc0 + Hc - tc),
            box(x0 + tc, x1 - tc, y0 + tc, yc0 - tc, zc0 + tc, zc0 + Hc - tc),
            box(x0 + tc, x1 - tc, yc0 + Wc + tc, y1 - tc, zc0 + tc, zc0 + Hc - tc),
            box(x0 + tc, L_tot / 2 - tc / 2, y0 + tc, y1 - tc, z0 + tc, -tc),
            box(L_tot / 2 + tc / 2, x1 - tc, y0 + tc, y1 - tc, z0 + tc, -tc),
        ]
        ymid, zmid = W_FACE / 2, zc0 + Hc / 2
        voids += [
            box(x0 - h, x0 + tc, yc0 + tc, yc0 + Wc - tc, zmid - hp / 2, zmid + hp / 2),
            box(x1 - tc, x1 + h, yc0 + tc, yc0 + Wc - tc, zmid - hp / 2, zmid + hp / 2),
        ]
        V = voids[0]
        for v in voids[1:]:
            V = np.maximum(V, v)
        rp_w = 0.4 * wg
        yg_m = 0.5 * (y0 + tc + yc0 - tc)
        yg_p = 0.5 * (yc0 + Wc + tc + y1 - tc)
        for (xc, yg, sgn) in ((x1, yg_m, +1.0), (x0, yg_p, -1.0)):
            cyl = rp_w ** 2 - ((Y - yg) ** 2 + (Z - zmid) ** 2)
            reach = np.minimum(sgn * (X - xc) + h + tc, 2 * tc + 2 * h)
            V = np.maximum(V, np.minimum(cyl, reach))
        for xp, rr in ((L_tot / 4, 0.03), (3 * L_tot / 4, 0.03)):
            cyl = rr ** 2 - ((X - xp) ** 2 + (Z - (-TANK_H / 2)) ** 2)
            reach = np.minimum(Y - (y0 - h), (y0 + tc + 0.01) - Y)
            V = np.maximum(V, np.minimum(cyl, reach))
        solid = np.minimum(solid, -V)
        del V
        for xe in (x0, x1):
            boss = box(xe - 0.02, xe + tc, yc0 + tc - 0.02, yc0 + Wc - tc + 0.02,
                       zmid - hp / 2 - 0.02, zmid + hp / 2 + 0.02)
            hole = box(xe - 0.03, xe + tc + h, yc0 + tc, yc0 + Wc - tc,
                       zmid - hp / 2, zmid + hp / 2)
            solid = np.maximum(solid, np.minimum(boss, -hole))
        for fx in (0.05, L_tot - 0.05):
            for fy in (0.05, W_FACE - 0.05):
                solid = np.maximum(solid, box(fx - 0.03, fx + 0.03, fy - 0.03,
                                              fy + 0.03, z0 - 0.02, z0))
        return solid

    solid = build_coarse()
    print(f"coarse casing field done {time.time()-t0:.0f}s")

    # ---- supersampled core (volume fraction of sheet|seals, SS^3 sub-samples) --
    ix0 = int(np.searchsorted(xs, Lp))
    ix1 = int(np.searchsorted(xs, Lp + Lc)) + 1
    iy0 = int(np.searchsorted(ys, yc0))
    iy1 = int(np.searchsorted(ys, yc0 + Wc)) + 1
    iz0 = int(np.searchsorted(zs, zc0))
    iz1 = int(np.searchsorted(zs, zc0 + Hc)) + 1
    half = 0.5 * ratio
    slab = a
    off = (np.arange(SS) + 0.5) / SS - 0.5  # sub-voxel offsets in units of h
    core_vf = np.zeros((ix1 - ix0, iy1 - iy0, iz1 - iz0), np.float32)
    ys_c = ys[iy0:iy1]
    zs_c = zs[iz0:iz1]
    for ixsl in range(ix0, ix1):
        acc = np.zeros((SS, iy1 - iy0, iz1 - iz0), np.float32)
        for si in range(SS):
            xq = xs[ixsl] + off[si] * h
            for sj in range(SS):
                yq = ys_c + off[sj] * h
                for sk in range(SS):
                    zq = zs_c + off[sk] * h
                    Yq, Zq = np.meshgrid(yq, zq, indexing="ij")
                    d = tpms.signed_distance("gyroid", (xq - Lp) / a,
                                             (Yq - yc0) / a, (Zq - zc0) / a)
                    dy = np.minimum(Yq - yc0, yc0 + Wc - Yq)
                    half_g = np.minimum(0.45, half * (1.0 + 3.0 *
                                        np.clip(1.0 - dy / (3.0 * a), 0.0, 1.0)))
                    sheet = half_g - np.abs(d)
                    fA = (xq < Lp + slab) or (xq > Lp + Lc - slab)
                    sealA = (half - d) if fA else np.full_like(d, -1.0)
                    fB = (Yq < yc0 + slab) | (Yq > yc0 + Wc - slab)
                    sealB = np.where(fB, d + half, -1.0)
                    sealZ = 1.0  # z faces of the core box are closed by casing walls
                    fld = np.maximum(np.maximum(sheet, sealA), sealB)
                    acc[si] += (fld > 0)
                    del d, sheet, sealA, sealB, fld, Yq, Zq
        core_vf[ixsl - ix0] = acc.mean(axis=0) / (SS * SS)
        if (ixsl - ix0) % 8 == 0:
            print(f"  core slab {ixsl-ix0+1}/{ix1-ix0} {time.time()-t0:.0f}s")
    # NOTE sealZ: full-part box walls close z faces; inside the core box we keep the
    # gyroid; z closure comes from the casing (matches build_part's sealZ=1.0 solid).
    solid_core = core_vf - 0.5  # pseudo-signed field, positive = solid (vf > 0.5)
    del core_vf
    solid[ix0:ix1, iy0:iy1, iz0:iz1] = solid_core
    del solid_core
    print(f"supersampled core merged {time.time()-t0:.0f}s")
    np.save(FIELD, solid)
    print("field checkpoint saved")
    if "--field-only" in sys.argv:
        return
    qa_and_mesh(solid, t0, meta=dict(ix0=ix0, ix1=ix1, iy0=iy0, iy1=iy1,
                                     iz0=iz0, iz1=iz1, a=a, Lc=Lc, Wc=Wc, Hc=Hc))


def qa_and_mesh(solid, t0, meta=None):
    if meta is None:  # resumed from checkpoint: recompute indices
        a, ratio = P["cell_m"], P["wall_ratio"]
        Lc, Lp = P["core_len_m"], P["plenum_len_m"]
        Lo = 0.6 * Lp
        nx = max(3, int(round(Lc / a)))
        ny = max(3, int(round((W_FACE - 2 * P["gallery_w_m"]) / a)))
        nz = max(3, int(round(H_FACE / a)))
        Lc, Wc, Hc = nx * a, ny * a, nz * a
        yc0 = (W_FACE - Wc) / 2
        zc0 = (H_FACE - Hc) / 2
        xs = np.arange(-(P["casing_t_m"] + 2 * H_VOX),
                       Lp + Lc + Lo + P["casing_t_m"] + 2 * H_VOX + H_VOX, H_VOX)
        ys = np.arange(-(P["casing_t_m"] + 2 * H_VOX),
                       W_FACE + P["casing_t_m"] + 2 * H_VOX + H_VOX, H_VOX)
        zs = np.arange(-TANK_H - (P["casing_t_m"] + 2 * H_VOX),
                       H_FACE + P["casing_t_m"] + 2 * H_VOX + H_VOX, H_VOX)
        ix0 = int(np.searchsorted(xs, Lp)); ix1 = int(np.searchsorted(xs, Lp + Lc)) + 1
        iy0 = int(np.searchsorted(ys, yc0)); iy1 = int(np.searchsorted(ys, yc0 + Wc)) + 1
        iz0 = int(np.searchsorted(zs, zc0)); iz1 = int(np.searchsorted(zs, zc0 + Hc)) + 1
    else:
        a = meta["a"]; Lc, Wc, Hc = meta["Lc"], meta["Wc"], meta["Hc"]
        ix0, ix1 = meta["ix0"], meta["ix1"]
        iy0, iy1 = meta["iy0"], meta["iy1"]
        iz0, iz1 = meta["iz0"], meta["iz1"]
    h = H_VOX

    # ---- QA on the rasterised field BEFORE meshing ----------------------------
    from scipy import ndimage
    trim = int(round(min(2 * a, 0.2 * min(Lc, Wc, Hc)) / h))
    core = solid[ix0 + trim:ix1 - trim, iy0 + trim:iy1 - trim, iz0 + trim:iz1 - trim] > 0
    lab, ncomp = ndimage.label(~core)
    ids_in = set(np.unique(lab[0])) - {0}
    ids_out = set(np.unique(lab[-1])) - {0}
    ids_gal = (set(np.unique(lab[:, 0])) | set(np.unique(lab[:, -1]))) - {0}
    qa = {
        "core void components": int(ncomp),
        "core solid fraction": float(core.mean()),
        "air network connects both air faces": bool(ids_in & ids_out),
        "air network leaks to gallery walls": bool(ids_in & ids_gal),
        "gallery network reaches both galleries": bool(
            (set(np.unique(lab[:, 0])) - {0}) & (set(np.unique(lab[:, -1])) - {0})),
    }
    print(json.dumps(qa, indent=1))
    if qa["air network leaks to gallery walls"] or not qa["air network connects both air faces"]:
        print("QA FAIL: network topology wrong; STL NOT written")
        json.dump({"params": P, "qa": qa}, open(OUT / "qa_v3_ss.json", "w"), indent=2)
        return
    del lab, core

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
    stl = OUT / "dac-fullpart-v3.stl"
    write_stl(stl, verts, faces,
              header="ACES DAC full part v3 supersampled-core (study, not verified)",
              scale=1000.0)
    json.dump({"params": P, "qa": qa, "volume_m3": vol, "triangles": len(faces),
               "voxel_m": h, "supersample": SS},
              open(OUT / "qa_v3_ss.json", "w"), indent=2, default=float)
    print("wrote", stl)


if __name__ == "__main__":
    main()
