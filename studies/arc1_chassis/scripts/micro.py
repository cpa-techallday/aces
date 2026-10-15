"""ARC-1 :: expand the macro frame into the actual 2.4 mm rods.

Level 2. Every macro member becomes a 3-chord lattice boom; every boom becomes
chords, battens and diagonals; every one of those is a rod the robot places and
arc-welds at both ends.

This is also where the process rules are ENFORCED rather than assumed:

  * a rod's capacity comes from ITS OWN length, not from the 60 mm nominal.
    Chords are 60 mm by construction, but a batten is R*sqrt(3) and a diagonal
    is sqrt(batten^2 + bay^2) - always the longest rod in the boom. A 40 mm
    boom has 92 mm diagonals, which hold 35% of what a 60 mm rod holds.
  * minimum included angle at a node, or the torch cannot reach the second rod
    without fouling the first.
  * node valence, or the node ball cannot swallow all the rod ends.

Run:  python scripts/micro.py
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

from boom import Boom, boom_rods, A_ROD, I_ROD, E, MAT, PR

ROOT = Path(__file__).resolve().parents[1]
P = json.loads((ROOT / "cad" /
                __import__("os").environ.get("ARC1_PARAMS",
                                             "params.json")).read_text())
OUT = ROOT / P["output"]["dir"]

K = PR["effective_length_factor_K"]
R_GYR = PR["rod_diameter_mm"] / 4.0
FY = MAT["yield_MPa"]
CC = math.sqrt(2.0 * math.pi ** 2 * E / FY)


def rod_capacity(L: float) -> float:
    """Compression capacity of a single rod of length L (Johnson/Euler)."""
    slend = K * L / R_GYR
    if slend < CC:
        Pcr = A_ROD * FY * (1.0 - slend ** 2 / (2.0 * CC ** 2))
    else:
        Pcr = math.pi ** 2 * E * I_ROD / ((K * L) ** 2)
    return min(Pcr, A_ROD * FY)


def expand(frame: dict, merge_r=None):
    """Macro frame -> micro nodes + rods, with real spatial node welding.

    Booms meeting at a macro node arrive with their own end rings at their own
    orientations, a few millimetres apart. Grid-rounding does not merge them -
    it leaves near-coincident nodes joined by near-zero rods, which is where
    the sub-1-degree weld-access angles came from. Cluster them properly: any
    nodes closer than one node-ball diameter ARE one node, because one ball of
    deposited metal is physically going to swallow them all.
    """
    from scipy.spatial import cKDTree

    merge_r = merge_r or PR["node_ball_dia_mm"]
    nodes = np.array(frame["nodes"], float)
    class_R = frame["class_R"]

    raw_pts: list[tuple] = []
    raw_rods: list[tuple] = []
    for (i, j, cls) in frame["members"]:
        pts, rods = boom_rods(nodes[i], nodes[j], class_R[cls])
        if not pts:
            continue
        off = len(raw_pts)
        raw_pts.extend(pts)
        for (a, b, kind) in rods:
            raw_rods.append((off + a, off + b, kind, cls))

    raw = np.array(raw_pts, float)
    tree = cKDTree(raw)
    groups = tree.query_ball_tree(tree, merge_r)

    label = np.full(len(raw), -1, int)
    centres = []
    for i in range(len(raw)):
        if label[i] >= 0:
            continue
        grp = [k for k in groups[i] if label[k] < 0]
        label[grp] = len(centres)
        centres.append(raw[grp].mean(axis=0))
    mnodes = np.array(centres)

    seen, out = set(), []
    for (a, b, kind, cls) in raw_rods:
        u, v = int(label[a]), int(label[b])
        if u == v:
            continue                      # collapsed into a single node ball
        k = (min(u, v), max(u, v))
        if k in seen:
            continue
        seen.add(k)
        out.append((k[0], k[1], kind, cls))
    return mnodes, out


def check(mnodes, mrods) -> dict:
    n = len(mnodes)
    L = np.array([np.linalg.norm(mnodes[a] - mnodes[b]) for a, b, _, _ in mrods])
    cap = np.array([rod_capacity(x) for x in L])

    inc = defaultdict(list)
    for idx, (a, b, _, _) in enumerate(mrods):
        inc[a].append(idx)
        inc[b].append(idx)
    valence = np.array([len(inc[i]) for i in range(n)])

    # minimum included angle between rods meeting at a node
    min_ang = np.full(n, 180.0)
    for i in range(n):
        ids = inc[i]
        if len(ids) < 2:
            continue
        dirs = []
        for idx in ids:
            a, b, _, _ = mrods[idx]
            o = b if a == i else a
            d = mnodes[o] - mnodes[i]
            nrm = np.linalg.norm(d)
            if nrm > 1e-6:
                dirs.append(d / nrm)
        if len(dirs) < 2:
            continue
        Dm = np.array(dirs)
        c = np.clip(Dm @ Dm.T, -1.0, 1.0)
        np.fill_diagonal(c, -1.0)
        min_ang[i] = math.degrees(math.acos(float(c.max())))

    ball = PR["node_ball_dia_mm"] / 2.0
    mass_rod = float(L.sum()) * A_ROD / 1000.0 * MAT["density_g_cm3"] / 1000.0
    mass_ball = n * (4 / 3 * math.pi * ball ** 3) / 1000.0 * \
        MAT["density_g_cm3"] / 1000.0

    by_kind = defaultdict(lambda: [0, 0.0])
    for (idx, (_, _, kind, _)) in enumerate(mrods):
        by_kind[kind][0] += 1
        by_kind[kind][1] += float(L[idx])

    return {
        "nodes": int(n), "rods": int(len(mrods)),
        "rod_len_mm": {"min": float(L.min()), "mean": float(L.mean()),
                       "max": float(L.max()),
                       "p95": float(np.percentile(L, 95))},
        "rod_total_m": float(L.sum() / 1000.0),
        "capacity_N": {"min": float(cap.min()), "mean": float(cap.mean()),
                       "max": float(cap.max())},
        "rods_over_60mm": int((L > 60.5).sum()),
        "rods_over_90mm": int((L > 90.0).sum()),
        "valence": {"max": int(valence.max()),
                    "mean": float(valence.mean()),
                    "over_limit": int((valence > PR["node_valence_max"]).sum())},
        "min_angle_deg": {"min": float(min_ang.min()),
                          "p05": float(np.percentile(min_ang, 5)),
                          "under_limit": int(
                              (min_ang < PR["weld_access_angle_min_deg"]).sum())},
        "mass_rod_kg": round(mass_rod, 2),
        "mass_nodes_kg": round(mass_ball, 2),
        "mass_total_kg": round(mass_rod + mass_ball, 2),
        "by_kind": {k: {"n": v[0], "m": round(v[1] / 1000.0, 1)}
                    for k, v in by_kind.items()},
        "_valence_arr": valence,
        "_minang_arr": min_ang,
        "weld_hours": round(len(mrods) * (PR["rod_place_cycle_s"] +
                                          2 * PR["arc_cycle_s"]) / 3600.0, 1),
    }


def mesh(mnodes, mrods, path, rod_dia=None, seg=6):
    """Triangulated solid of every rod and node ball, for Fusion."""
    import trimesh
    rod_dia = rod_dia or PR["rod_diameter_mm"]
    parts = []
    for (a, b, _, _) in mrods:
        p0, p1 = mnodes[a], mnodes[b]
        v = p1 - p0
        h = float(np.linalg.norm(v))
        if h < 1e-6:
            continue
        cyl = trimesh.creation.cylinder(radius=rod_dia / 2.0, height=h,
                                        sections=seg)
        cyl.apply_transform(_align(v, (p0 + p1) / 2.0))
        parts.append(cyl)
    ico = trimesh.creation.icosphere(subdivisions=0,
                                     radius=PR["node_ball_dia_mm"] / 2.0)
    for p in mnodes:
        s = ico.copy()
        s.apply_translation(p)
        parts.append(s)
    m = trimesh.util.concatenate(parts)
    m.export(path)
    return m


def _align(v, centre):
    import trimesh
    z = np.array([0.0, 0.0, 1.0])
    d = v / np.linalg.norm(v)
    T = trimesh.geometry.align_vectors(z, d)
    T[:3, 3] = centre
    return T


def main():
    frame = json.loads((OUT / "arc1_frame.json").read_text())
    mnodes, mrods = expand(frame)
    st = check(mnodes, mrods)

    print(f"micro lattice: {st['nodes']:,} nodes  {st['rods']:,} rods  "
          f"{st['rod_total_m']:,.0f} m of {PR['rod_diameter_mm']} mm rod")
    print(f"  mass  rod {st['mass_rod_kg']:.1f} kg + nodes "
          f"{st['mass_nodes_kg']:.1f} kg = {st['mass_total_kg']:.1f} kg")
    print(f"  rod length  min {st['rod_len_mm']['min']:.1f}  "
          f"mean {st['rod_len_mm']['mean']:.1f}  "
          f"p95 {st['rod_len_mm']['p95']:.1f}  "
          f"max {st['rod_len_mm']['max']:.1f} mm")
    print(f"  capacity    min {st['capacity_N']['min']:.0f} N  "
          f"mean {st['capacity_N']['mean']:.0f} N")
    print(f"  over 60 mm  {st['rods_over_60mm']:,} rods "
          f"({st['rods_over_60mm']/st['rods']:.0%})   "
          f"over 90 mm {st['rods_over_90mm']:,}")
    print(f"  valence     max {st['valence']['max']}  "
          f"mean {st['valence']['mean']:.1f}  "
          f"over limit {st['valence']['over_limit']}")
    print(f"  weld access min angle {st['min_angle_deg']['min']:.1f} deg  "
          f"({st['min_angle_deg']['under_limit']} nodes under "
          f"{PR['weld_access_angle_min_deg']:.0f})")
    print(f"  robot time  {st['weld_hours']:,.0f} h of arc cycles")
    print("  by kind: " + "  ".join(
        f"{k} {v['n']:,}/{v['m']:.0f}m" for k, v in st["by_kind"].items()))

    # Classify every node by whether the robot can actually weld it. A node
    # too crowded or too shallow for torch access is not a failure of the
    # design, it is a node that gets a machined 316L hub with drilled sockets
    # instead of a deposited ball. Knowing WHICH ones, and how many, is the
    # manufacturing answer.
    hub = (st["_valence_arr"] > PR["node_valence_max"]) | \
          (st["_minang_arr"] < PR["weld_access_angle_min_deg"])
    st["nodes_weldable"] = int((~hub).sum())
    st["nodes_need_hub"] = int(hub.sum())
    st["hub_fraction"] = round(float(hub.mean()), 4)
    print(f"  NODE BUILD SPLIT  {st['nodes_weldable']:,} robot-weldable "
          f"({1-st['hub_fraction']:.1%})   {st['nodes_need_hub']:,} need a "
          f"machined hub ({st['hub_fraction']:.1%})")

    np.save(OUT / "micro_nodes.npy", mnodes)
    np.save(OUT / "micro_node_hub.npy", hub)
    del st["_valence_arr"], st["_minang_arr"]
    (OUT / "micro_rods.json").write_text(json.dumps(
        {"rods": [[int(a), int(b), k, c] for a, b, k, c in mrods],
         "stats": st}))
    (OUT / "micro_stats.json").write_text(json.dumps(st, indent=2))
    print(f"\nwrote micro_nodes.npy / micro_rods.json / micro_stats.json")

    if "--mesh" in sys.argv:
        path = OUT / "arc1_lattice.stl"
        print("building mesh ...")
        m = mesh(mnodes, mrods, str(path))
        print(f"wrote {path}  {len(m.faces):,} triangles  "
              f"{path.stat().st_size/1e6:.1f} MB")


if __name__ == "__main__":
    main()
