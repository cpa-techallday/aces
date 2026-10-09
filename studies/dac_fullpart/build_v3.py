# Build the v3 selected full part + QA (sections, shell probe, network connectivity).
# Study tier; geometry generation reuses studies/dac_fullpart/fullpart.build_part.
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from studies.dac_fullpart.fullpart import build_part
from studies.dac_fullpart.fullpart_v3 import ROOT as _R  # same ROOT
from aces.domains.hx.tpms import mesh_volume, write_stl

OUT = ROOT / "out" / "dac-fullpart-v3"
PARAMS = json.load(open(OUT / "selected-v3.json"))["params"]


def main():
    verts, faces, info, (solid, xs, ys, zs) = build_part(PARAMS, keep_field=True)
    vol = mesh_volume(verts, faces)
    print(f"mesh: {len(faces)} triangles, enclosed {vol:.4f} m3")
    if vol < 0:
        faces = faces[:, ::-1]
    stl = OUT / "dac-fullpart-v3.stl"
    write_stl(stl, verts, faces,
              header="ACES holistic DAC full part v3 CFD-corrected (study, not verified)",
              scale=1000.0)

    S = solid > 0  # solid mask

    # ---- shell probe: min wall thickness between each outer face and nearest void,
    # sampled on a coarse lattice (fraction of probes with wall < casing_t).
    tc = PARAMS["casing_t_m"]
    h = info["voxel_m"]
    probe = {}
    faces_axis = {"+x": (0, -1), "-x": (0, 0), "+y": (1, -1), "-y": (1, 0),
                  "+z": (2, -1), "-z": (2, 0)}
    # distance transform-lite: for each boundary voxel layer, march inward until solid
    rng = np.random.default_rng(0)
    thin = {k: 0 for k in faces_axis}
    tot = {k: 0 for k in faces_axis}
    n_probe = 4000
    for name, (ax, end) in faces_axis.items():
        oa = [i for i in range(3) if i != ax]
        for _ in range(n_probe):
            i = rng.integers(2, S.shape[oa[0]] - 2)
            j = rng.integers(2, S.shape[oa[1]] - 2)
            idx = [0, 0, 0]
            idx[oa[0]], idx[oa[1]] = i, j
            depth = 0
            if end == -1:
                line = S[tuple(idx[:ax] + [slice(None)] + idx[ax + 1:])][::-1]
            else:
                line = S[tuple(idx[:ax] + [slice(None)] + idx[ax + 1:])]
            # find first solid from outside
            hit = np.argmax(line)
            if not line[hit]:
                continue
            depth_in_solid = np.argmin(line[hit:hit + 400])
            wall = depth_in_solid * h
            tot[name] += 1
            if wall < tc - h:
                thin[name] += 1
        probe[f"{name} shell thin fraction"] = thin[name] / max(tot[name], 1)

    # ---- core solid fraction
    Lp = PARAMS["plenum_len_m"]
    a = PARAMS["cell_m"]
    Lc, Wc, Hc = info["core_active"]
    x0, y0, z0 = info["core_origin"]
    trim = min(2 * a, 0.2 * min(Lc, Wc, Hc))  # v3 core is only ~2.7 cells long
    ix = (xs >= x0 + trim) & (xs <= x0 + Lc - trim)
    iy = (ys >= y0 + trim) & (ys <= y0 + Wc - trim)
    iz = (zs >= z0 + trim) & (zs <= z0 + Hc - trim)
    core = S[np.ix_(ix, iy, iz)]
    probe["core solid fraction"] = float(core.mean())

    # ---- network connectivity at the air faces: label void components in the core,
    # check the air network touches both air faces and NOT the gallery walls.
    from scipy import ndimage
    void = ~core
    lab, n = ndimage.label(void)
    face_in = lab[0, :, :]
    face_out = lab[-1, :, :]
    gal_lo = lab[:, 0, :]
    gal_hi = lab[:, -1, :]
    ids_in = set(np.unique(face_in)) - {0}
    ids_out = set(np.unique(face_out)) - {0}
    ids_gal = (set(np.unique(gal_lo)) | set(np.unique(gal_hi))) - {0}
    air_net = ids_in & ids_out
    probe["void components in core"] = int(n)
    probe["air network connects both air faces"] = bool(air_net)
    probe["air network leaks to gallery walls"] = bool(ids_in & ids_gal)
    probe["gallery network reaches both galleries"] = bool(
        (set(np.unique(gal_lo)) - {0}) & (set(np.unique(gal_hi)) - {0}))

    with open(OUT / "qa_v3.json", "w") as fh:
        json.dump({"params": PARAMS, "mesh": info, "volume_m3": vol, "probe": probe},
                  fh, indent=2, default=float)
    print(json.dumps(probe, indent=1))

    # ---- QA section images
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    mid = [S.shape[0] // 2, S.shape[1] // 2, S.shape[2] // 2]
    fig, axs = plt.subplots(1, 3, figsize=(18, 6))
    for ax, (k, name) in zip(axs, enumerate("xyz")):
        sl = np.take(S, mid[k], axis=k)
        ax.imshow(sl.T, origin="lower", cmap="gray_r")
        ax.set_title(f"mid-{name} section")
        ax.axis("off")
    fig.savefig(OUT / "qa_sections_v3.png", bbox_inches="tight", dpi=110)
    print("wrote", stl, OUT / "qa_v3.json", OUT / "qa_sections_v3.png")


if __name__ == "__main__":
    main()
