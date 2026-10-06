"""ARC-1 :: structural analysis of the macro space frame.

Two separate models, because Pynite supports are a property of the model, not
of the load combination, and a chassis is tested two different ways:

  RIG    rear pickups clamped, equal/opposite couple across the front track.
         This IS a cantilever - that is what a torsion rig is. Gives Nm/deg.

  ROAD   the car standing on its four lower pickups, carrying distributed
         inertial mass. Gives the member forces you actually size to.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from Pynite import FEModel3D

from boom import Boom, E, G, MAT

ROOT = Path(__file__).resolve().parents[1]
P = json.loads((ROOT / "cad" /
                __import__("os").environ.get("ARC1_PARAMS",
                                             "params.json")).read_text())
OUT = ROOT / P["output"]["dir"]
G_ACC = 9.81


def J_for(R: float) -> float:
    cal = json.loads((OUT / "boom_calibration.json").read_text())
    xs = np.array(sorted(float(k) for k in cal))
    ys = np.array([cal[str(int(x))]["J_eq_mm4"] for x in xs])
    return float(np.interp(R, xs, ys))


def pickup_nodes(f) -> set:
    """Frame.member stores (min,max) and pickup nodes are created last, so the
    pickup end of a 'pickup' member is always the higher index."""
    return {j for (_, j, cls) in f.members if cls == "pickup"}


def _model(f, class_R):
    m = FEModel3D()
    m.add_material("steel", E, G, MAT["poisson"],
                   MAT["density_g_cm3"] * 1000.0 / 1e9)
    secs = {}
    for cls, R in class_R.items():
        if isinstance(R, dict):
            # explicit section - used for the carbon skin's equivalent
            # diagonals, which are not booms and have no boom mass
            m.add_section(cls, R["A"], R["I"], R["I"], R["J"])
            secs[cls] = None
            continue
        b = Boom(R)
        m.add_section(cls, b.A, b.I, b.I, J_for(R))
        secs[cls] = b
    for i, (x, y, z) in enumerate(f.nodes):
        m.add_node(f"N{i}", x, y, z)
    for k, (i, j, cls) in enumerate(f.members):
        m.add_member(f"M{k}", f"N{i}", f"N{j}", "steel", cls)
    # Pickup links are left moment-connected on purpose. Idealising them as
    # pin-jointed two-force members is more elegant but leaves the pickup
    # node's rotational DOF unrestrained, and Pynite returns ~1e14 N rather
    # than reporting the singularity. The rigid model is also the conservative
    # one: the moment it shows is real work the bracket has to do, and that
    # bracket is the generatively-designed part (see rocker.py).
    return m, secs


def node_masses(f, secs) -> np.ndarray:
    """Sprung mass lumped onto frame nodes, kg."""
    veh, ck = P["vehicle"], P["cockpit"]
    nm = np.zeros(len(f.nodes))
    for mem in f.members:
        if secs[mem[2]] is None:
            continue
        kg = secs[mem[2]].mass_per_m_kg() * f.length(mem) / 1000.0
        nm[mem[0]] += kg / 2.0
        nm[mem[1]] += kg / 2.0

    def spread(kg, pick):
        idx = [i for i, p in enumerate(f.nodes) if pick(p)]
        if not idx:
            raise RuntimeError(f"no nodes to carry {kg} kg")
        for i in idx:
            nm[i] += kg / len(idx)

    spread(veh["driver_mass_kg"],
           lambda p: ck["x_front"] <= p[0] <= ck["x_rear"] and p[2] < 320)
    spread(70.0, lambda p: 1900 <= p[0] <= 2450)     # powertrain / battery
    spread(45.0, lambda p: True)                     # fluids, bodywork, misc
    return nm


def run_road(f, hard, class_R):
    m, secs = _model(f, class_R)
    nm = node_masses(f, secs)
    lc = P["load_cases"]

    cases = {
        "BUMP":   (0.0, 0.0, -lc["bump_3g"]["g"]),
        "BRAKE":  (-lc["brake_1p5g"]["g"], 0.0, -1.0),
        "CORNER": (0.0, lc["cornering_2g"]["g"], -1.0),
    }
    for cname, (gx, gy, gz) in cases.items():
        for i in range(len(f.nodes)):
            if nm[i] <= 0:
                continue
            w = nm[i] * G_ACC
            for d, gg in (("FX", gx), ("FY", gy), ("FZ", gz)):
                if gg:
                    m.add_node_load(f"N{i}", d, gg * w, cname)

    # All four lower pickups react in all three directions. A suspension is a
    # redundant system - every tyre carries vertical, lateral AND longitudinal
    # load - and the FEA distributes the reactions by stiffness. Restraining
    # fewer directions does not make it more honest, it makes it a mechanism:
    # reacting lateral load at the front pair alone put 227 kN into a link
    # that only ever sees 4.7 kN of applied load.
    c = [hard["FLA_L_F"], hard["FLA_L_F_M"],
         hard["RLA_L_F"], hard["RLA_L_F_M"]]
    for n in c:
        m.def_support(f"N{n}", True, True, True, False, False, False)

    for k in cases:
        m.add_load_combo(k, {k: 1.0})
    m.analyze_linear(check_statics=False, sparse=True)
    return m, secs, nm, list(cases)


def run_rig(f, hard, class_R):
    m, _ = _model(f, class_R)
    for key in ("RLA_L_F", "RLA_L_F_M", "RLA_L_R", "RLA_L_R_M"):
        m.def_support(f"N{hard[key]}", True, True, True, False, False, False)

    fl, fr = hard["FLA_L_F"], hard["FLA_L_F_M"]
    arm = abs(f.nodes[fl][1] - f.nodes[fr][1])
    T_Nm = P["load_cases"]["torsion"]["torque_Nm"]
    F = T_Nm * 1000.0 / arm
    m.add_node_load(f"N{fl}", "FZ", F, "TOR")
    m.add_node_load(f"N{fr}", "FZ", -F, "TOR")
    m.add_load_combo("TOR", {"TOR": 1.0})
    m.analyze_linear(check_statics=False, sparse=True)

    dzl = m.nodes[f"N{fl}"].DZ["TOR"]
    dzr = m.nodes[f"N{fr}"].DZ["TOR"]
    theta = math.atan2(abs(dzl - dzr), arm)
    return m, T_Nm / math.degrees(theta), arm, max(abs(dzl), abs(dzr))


def utilisation(models, f, class_R):
    """Worst chord force in every boom, over every case in every model.

    A boom's chords carry the axial load AND the bending: for 3 chords on
    radius R the bending part is M/(1.5R).
    """
    rules = json.loads((OUT / "process_rules.json").read_text())
    Pc = rules["compression_capacity_N"]
    Pt = rules["joint_capacity_collared_N"]
    worst = []
    for k, (_, _, cls) in enumerate(f.members):
        R = class_R[cls]
        if isinstance(R, dict):
            continue          # skin panel, not a rod boom - no chord check
        ub, best = 0.0, None
        for m, combos in models:
            mm = m.members[f"M{k}"]
            for cb in combos:
                nmax, nmin = mm.max_axial(cb), mm.min_axial(cb)
                N = max(abs(nmax), abs(nmin))
                Mr = math.hypot(
                    max(abs(mm.max_moment("My", cb)), abs(mm.min_moment("My", cb))),
                    max(abs(mm.max_moment("Mz", cb)), abs(mm.min_moment("Mz", cb))))
                chord = N / 3.0 + Mr / (1.5 * R)
                cap = Pc if nmin < -1e-6 else Pt
                u = chord / cap
                if u > ub:
                    ub, best = u, (cb, N, Mr, chord)
        worst.append((ub, k, cls, best))
    worst.sort(reverse=True)
    return worst, Pc, Pt


def analyse(f, hard, class_R, verbose=True) -> dict:
    road, secs, nm, road_cases = run_road(f, hard, class_R)
    rig, K, arm, tip = run_rig(f, hard, class_R)

    mass, by_cls = 0.0, {}
    for mem in f.members:
        if secs[mem[2]] is None:
            continue
        L = f.length(mem) / 1000.0
        kg = secs[mem[2]].mass_per_m_kg() * L
        mass += kg
        d = by_cls.setdefault(mem[2], {"n": 0, "m": 0.0, "kg": 0.0})
        d["n"] += 1
        d["m"] += L
        d["kg"] += kg

    worst, Pc, Pt = utilisation(
        [(road, road_cases), (rig, ["TOR"])], f, class_R)

    res = {
        "nodes": len(f.nodes),
        "members": len(f.members),
        "boom_length_m": round(sum(f.length(x) for x in f.members) / 1000.0, 1),
        "frame_mass_kg": round(mass, 2),
        "sprung_mass_kg": round(float(nm.sum()), 1),
        "by_class": {k: {"n": v["n"], "len_m": round(v["m"], 1),
                         "kg": round(v["kg"], 2)} for k, v in by_cls.items()},
        "torsion_Nm_per_deg": round(K, 0),
        "torsion_arm_mm": round(arm, 1),
        "torsion_tip_mm": round(tip, 2),
        "capacity_comp_N": round(Pc, 0),
        "capacity_tens_N": round(Pt, 0),
        "max_utilisation": round(worst[0][0], 3),
        "over_unity": int(sum(1 for w in worst if w[0] > 1.0)),
        "worst": [{"u": round(w[0], 3), "class": w[2], "case": w[3][0],
                   "N": round(w[3][1], 0), "M_Nmm": round(w[3][2], 0),
                   "chord_N": round(w[3][3], 0)} for w in worst[:12]],
        "util_by_class": {},
    }
    for u, _, cls, _ in worst:
        d = res["util_by_class"].setdefault(cls, 0.0)
        res["util_by_class"][cls] = max(d, round(u, 2))

    if verbose:
        print(f"nodes {res['nodes']}   members {res['members']}   "
              f"boom {res['boom_length_m']} m")
        print(f"FRAME MASS   {res['frame_mass_kg']:.1f} kg     "
              f"(sprung total modelled {res['sprung_mass_kg']:.0f} kg)")
        for k, v in sorted(res["by_class"].items(), key=lambda kv: -kv[1]["kg"]):
            print(f"   {k:<11} {v['n']:>4} mbr {v['len_m']:>6.1f} m "
                  f"{v['kg']:>6.2f} kg   u_max={res['util_by_class'].get(k,0):.2f}")
        print(f"TORSION      {res['torsion_Nm_per_deg']:,.0f} Nm/deg "
              f"(arm {arm:.0f} mm, tip {tip:.2f} mm @ "
              f"{P['load_cases']['torsion']['torque_Nm']:.0f} Nm)")
        print(f"CAPACITY     {Pc:.0f} N comp / {Pt:.0f} N tens per chord")
        print(f"UTILISATION  max {res['max_utilisation']:.2f}   "
              f"({res['over_unity']} of {len(f.members)} members over 1.0)")
        for w in res["worst"][:8]:
            print(f"    u={w['u']:<6.2f} {w['class']:<11} {w['case']:<7} "
                  f"N={w['N']:>8,.0f}  M={w['M_Nmm']:>9,.0f}  "
                  f"chord={w['chord_N']:>7,.0f} N")
    return res
