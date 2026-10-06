"""ARC-1 :: rod-by-rod strength check (added by ACES, 2026-10-06).

frame.py / fea.py check each macro member as one boom: the worst chord force
N/3 + M/(1.5R) against the capacity of a NOMINAL 60 mm rod. Two things fall
outside that check:

  * the lacing. Battens and diagonals carry the boom's shear and torsion, and
    the diagonals are the longest rods in the chassis (84 mm in a 34 mm boom),
    so they hold the least;
  * the bending-compression chord of a member in net tension, which fea.py
    checks against the tension capacity.

This script maps the frame FEA forces of every member, in every load case, onto
the individual rods that micro.py builds for it, and checks each rod against
the capacity of ITS OWN length.

Rod forces in a 3-chord boom of circumradius R (bay s, batten b = R*sqrt(3),
diagonal d = hypot(b, s), enclosed area A = 3*sqrt(3)/4 * R^2):

  chord     N/3 +- M/(1.5 R)                 (M = resultant bending moment)
  face shear S = (2/3) V + T b / (2 A)       (V = resultant shear, T = torque)
            (2/3)V is the largest share of V any one face takes when three faces
            at 120 deg resist it with no net twist; T b / 2A is the Bredt shear
            flow times the face width.
  diagonal  S d / b                          (its transverse component carries S)
  batten    S                                (each ring passes the face shear on)

Forces use each member's extreme N, M, V and T along its length, combined as if
they acted at the same section and in the worst direction: conservative. The
sign of lacing forces is not resolved, so every batten and diagonal is checked
in compression. Chords are checked in both senses.

Capacities (from params, as process.py):
  compression  min(Johnson/Euler of the rod's own length, collared joint)
  tension      collared joint (node_efficiency_collared x A x fy)

Run:  python scripts/rodcheck.py     (after process.py, boom.py, frame.py --dump)
Writes out/rod_check.json.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict

import numpy as np

from boom import A_ROD, BAY, PR, boom_rods
from fea import _CSIGN, run_rig, run_road
from frame import CLASS_R, OUT, build
from micro import FY, rod_capacity

ENC = 3.0 * math.sqrt(3.0) / 4.0  # enclosed area / R^2 of the chord triangle


def member_demands(models, f, class_R) -> dict[int, dict]:
    """Extreme forces per member over every case: tension-positive N, M, V, T."""
    out = {}
    for k, (_, _, cls) in enumerate(f.members):
        R = class_R[cls]
        if isinstance(R, dict):
            continue  # skin panel equivalent, not a rod boom
        worst = {"nt_max": 0.0, "nc_max": 0.0, "M": 0.0, "V": 0.0, "T": 0.0,
                 "chord_c": (0.0, None), "chord_t": (0.0, None), "face": (0.0, None)}
        for m, combos in models:
            mm = m.members[f"M{k}"]
            for cb in combos:
                n1, n2 = mm.max_axial(cb), mm.min_axial(cb)
                nt = [-_CSIGN * n1, -_CSIGN * n2]  # tension positive
                M = math.hypot(max(abs(mm.max_moment("My", cb)), abs(mm.min_moment("My", cb))),
                               max(abs(mm.max_moment("Mz", cb)), abs(mm.min_moment("Mz", cb))))
                V = math.hypot(max(abs(mm.max_shear("Fy", cb)), abs(mm.min_shear("Fy", cb))),
                               max(abs(mm.max_shear("Fz", cb)), abs(mm.min_shear("Fz", cb))))
                T = max(abs(mm.max_torque(cb)), abs(mm.min_torque(cb)))
                bend = M / (1.5 * R)
                cc = max(0.0, -min(nt) / 3.0 + bend)  # compressed chord
                ct = max(0.0, max(nt) / 3.0 + bend)   # tensioned chord
                b = R * math.sqrt(3.0)
                face = (2.0 / 3.0) * V + T * b / (2.0 * ENC * R * R)
                for key, val in (("chord_c", cc), ("chord_t", ct), ("face", face)):
                    if val > worst[key][0]:
                        worst[key] = (val, cb)
                worst["nt_max"] = max(worst["nt_max"], max(nt))
                worst["nc_max"] = max(worst["nc_max"], -min(nt))
                worst["M"], worst["V"], worst["T"] = max(worst["M"], M), max(worst["V"], V), max(worst["T"], T)
        out[k] = worst
    return out


def expand_tracked(f, class_R, merge_r=None):
    """micro.expand, keeping which member(s) each rod came from."""
    from scipy.spatial import cKDTree

    merge_r = merge_r or PR["node_ball_dia_mm"]
    nodes = np.array(f.nodes, float)
    raw_pts, raw_rods = [], []
    for k, (i, j, cls) in enumerate(f.members):
        R = class_R[cls]
        if isinstance(R, dict):
            continue
        pts, rods = boom_rods(nodes[i], nodes[j], R)
        if not pts:
            continue
        L = float(np.linalg.norm(nodes[j] - nodes[i]))
        step = L / max(1, int(math.ceil(L / BAY - 1e-9)))
        off = len(raw_pts)
        raw_pts.extend(pts)
        for a, b, kind in rods:
            raw_rods.append((off + a, off + b, kind, cls, k, R, step))
    raw = np.array(raw_pts, float)
    tree = cKDTree(raw)
    groups = tree.query_ball_tree(tree, merge_r)
    label = np.full(len(raw), -1, int)
    centres = []
    for i in range(len(raw)):
        if label[i] >= 0:
            continue
        grp = [q for q in groups[i] if label[q] < 0]
        label[grp] = len(centres)
        centres.append(raw[grp].mean(axis=0))
    mnodes = np.array(centres)
    rods: dict[tuple, dict] = {}
    order = []
    for a, b, kind, cls, k, R, step in raw_rods:
        u, v = int(label[a]), int(label[b])
        if u == v:
            continue
        key = (min(u, v), max(u, v))
        if key not in rods:
            rods[key] = {"kind": kind, "cls": cls, "members": []}
            order.append(key)
        rods[key]["members"].append((k, kind, R, step))
    return mnodes, [(key, rods[key]) for key in order]


def check(f, class_R, demands, mnodes, rods) -> dict:
    t_cap = PR["node_efficiency_collared"] * A_ROD * FY
    j_cap = t_cap  # the collared joint also limits compression
    per_rod = []
    for (a, b), r in rods:
        L = float(np.linalg.norm(mnodes[a] - mnodes[b]))
        c_cap = min(rod_capacity(L), j_cap)
        comp = tens = 0.0
        gov = None
        for k, kind, R, step in r["members"]:
            d = demands.get(k)
            if d is None:
                continue
            bt = R * math.sqrt(3.0)
            if kind == "chord":
                c, t = d["chord_c"][0], d["chord_t"][0]
                case = d["chord_c"][1] if c / c_cap >= t / t_cap else d["chord_t"][1]
            elif kind == "diagonal":
                c = t = d["face"][0] * math.hypot(bt, step) / bt
                case = d["face"][1]
            else:  # batten
                c = t = d["face"][0]
                case = d["face"][1]
            if max(c / c_cap, t / t_cap) > max(comp / c_cap, tens / t_cap):
                gov = (k, case)
            comp, tens = max(comp, c), max(tens, t)
        u = max(comp / c_cap, tens / t_cap)
        per_rod.append((u, L, r["kind"], r["cls"], comp, tens, c_cap, gov))

    u = np.array([p[0] for p in per_rod])
    by_kind = defaultdict(lambda: {"n": 0, "u_max": 0.0, "over": 0})
    by_cls = defaultdict(lambda: {"n": 0, "u_max": 0.0, "over": 0})
    for p in per_rod:
        for tab, key in ((by_kind, p[2]), (by_cls, p[3])):
            e = tab[key]
            e["n"] += 1
            e["u_max"] = max(e["u_max"], round(p[0], 3))
            e["over"] += int(p[0] > 1.0)
    order = np.argsort(-u)
    worst = []
    for i in order[:15]:
        uu, L, kind, cls, comp, tens, c_cap, gov = per_rod[i]
        worst.append({"u": round(uu, 3), "kind": kind, "class": cls, "length_mm": round(L, 1),
                      "compression_N": round(comp, 0), "tension_N": round(tens, 0),
                      "compression_capacity_N": round(c_cap, 0), "tension_capacity_N": round(t_cap, 0),
                      "member": None if gov is None else gov[0], "case": None if gov is None else gov[1]})
    over_members = sorted({per_rod[i][7][0] for i in range(len(per_rod)) if per_rod[i][0] > 1.0 and per_rod[i][7]})
    return {
        "method": "frame FEA member extremes mapped onto boom rods; capacities from each rod's own length",
        "rods": len(per_rod),
        "max_utilisation": round(float(u.max()), 3),
        "over_unity": int((u > 1.0).sum()),
        "members_with_rods_over_unity": len(over_members),
        "tension_capacity_N": round(t_cap, 0),
        "by_kind": dict(by_kind),
        "by_class": dict(by_cls),
        "worst": worst,
        "assumptions": [
            "member extreme N, M, V and T combined as if coincident (conservative)",
            "lacing force sign not resolved: battens and diagonals checked in compression",
            "face shear (2/3)V + T b / 2A for a 3-chord boom; diagonal S d/b; batten S",
            "pickup and other links are rod booms as micro.py builds them",
        ],
    }


def main():
    f, hard = build()
    road, _, _, road_cases = run_road(f, hard, CLASS_R)
    rig = run_rig(f, hard, CLASS_R)[0]
    demands = member_demands([(road, road_cases), (rig, ["TOR"])], f, CLASS_R)
    mnodes, rods = expand_tracked(f, CLASS_R)

    mr = OUT / "micro_rods.json"
    if mr.exists():  # same lattice as micro.py, or the map is meaningless
        n_micro = len(json.loads(mr.read_text())["rods"])
        if n_micro != len(rods):
            raise SystemExit(f"rod count {len(rods)} differs from micro.py's {n_micro}; rerun micro.py first")

    res = check(f, CLASS_R, demands, mnodes, rods)
    print(f"rod check: {res['rods']:,} rods  max utilisation {res['max_utilisation']:.2f}  "
          f"{res['over_unity']:,} over 1.0 in {res['members_with_rods_over_unity']} member(s)")
    for k, v in sorted(res["by_kind"].items()):
        print(f"   {k:<9} {v['n']:>6,} rods  u_max {v['u_max']:.2f}  over {v['over']:,}")
    for k, v in sorted(res["by_class"].items(), key=lambda kv: -kv[1]["u_max"]):
        print(f"   {k:<11} {v['n']:>6,} rods  u_max {v['u_max']:.2f}  over {v['over']:,}")
    for w in res["worst"][:8]:
        print(f"   u={w['u']:<6.2f} {w['kind']:<8} {w['class']:<11} L={w['length_mm']:>5.1f}  "
              f"C={w['compression_N']:>6,.0f}/{w['compression_capacity_N']:,.0f} N  "
              f"T={w['tension_N']:>6,.0f}/{w['tension_capacity_N']:,.0f} N  {w['case']}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "rod_check.json").write_text(json.dumps(res, indent=2))
    print(f"\nwrote {OUT / 'rod_check.json'}")


if __name__ == "__main__":
    main()
