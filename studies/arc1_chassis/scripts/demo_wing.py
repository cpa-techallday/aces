"""ARC-1 :: furan showcase demonstrator - a wing torsion box on an arc lattice.

If furan prepreg is the PRODUCT, the demonstrator has to prove the resin does
STRUCTURAL work and survives fire while doing it. A wing torsion box is the
right article: one number (Nm/deg), one robot station, and you can torsion-rig
it, then burn it under load and re-rig it.

Two design decisions fall straight out of making furan the product:

  1. The lattice is 316L, not 6061. Furan's acid catalyst pits aluminium, and
     every welded node is a crevice. On a showcase you cannot have the resin
     blamed for eating the structure. Stainless also detunes the rods clear
     of a turbine excitation band that aluminium sat inside.
  2. The skin is structural, not decorative. The whole point is the delta
     between bare lattice and skinned lattice.

Run:  python scripts/demo_wing.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from Pynite import FEModel3D

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out_demo"

# ---- article -----------------------------------------------------------
SPAN = 600.0          # mm - one robot station
CHORD = 300.0
THICK = 0.18          # t/c
N_RIB = 5             # ribs across the span
SPAR_X = (0.25, 0.70)  # fraction of chord

# ---- lattice -----------------------------------------------------------
ROD_D = 2.4
BOOM_R = 18.0         # smaller booms than the car - this is a wing, not a tub
BAY = 60.0
E_316, G_316, RHO_316 = 193_000.0, 77_000.0, 8.0
FY_316 = 290.0

# ---- skins to compare --------------------------------------------------
SKINS = {
    "none (bare lattice)":  (0.0, 0.0, 0.00),
    "Furan / glass +-45":   (0.75, 5_000.0, 1.42),
    "Furan / carbon +-45":  (0.75, 24_000.0, 1.18),
    "Epoxy / carbon +-45":  (0.75, 30_000.0, 1.15),
}

A_ROD = math.pi * (ROD_D / 2.0) ** 2
I_ROD = math.pi * ROD_D ** 4 / 64.0


def naca_t(xc, t):
    return 5 * t * (0.2969 * math.sqrt(xc) - 0.1260 * xc - 0.3516 * xc ** 2
                    + 0.2843 * xc ** 3 - 0.1015 * xc ** 4)


def build(skin_t, skin_G):
    """Wing box: 4 longerons (2 spars x upper/lower), ribs, and shear webs."""
    nodes, members = [], []
    key = {}

    def nid(x, y, z):
        k = (round(x, 2), round(y, 2), round(z, 2))
        if k not in key:
            key[k] = len(nodes)
            nodes.append((x, y, z))
        return key[k]

    ys = np.linspace(0.0, SPAN, N_RIB)
    corners = []       # [rib][0..3] = FU, FL, RU, RL
    for y in ys:
        c = []
        for xc in SPAR_X:
            zt = naca_t(xc, THICK) * CHORD
            for sgn in (1, -1):
                c.append(nid(xc * CHORD, y, sgn * zt))
        corners.append(c)

    # 4 longerons
    for a, b in zip(corners, corners[1:]):
        for k in range(4):
            members.append((a[k], b[k], "longeron"))
    # rib frames: front web, rear web, upper and lower skin ties + X
    for c in corners:
        members += [(c[0], c[1], "rib"), (c[2], c[3], "rib"),
                    (c[0], c[2], "rib"), (c[1], c[3], "rib"),
                    (c[0], c[3], "rib_x"), (c[1], c[2], "rib_x")]
    # bay diagonals on all four faces: front web, rear web, upper, lower
    faces = ((0, 1), (2, 3), (0, 2), (1, 3))
    for a, b in zip(corners, corners[1:]):
        for (p, q) in faces:
            members.append((a[p], b[q], "diagonal"))
    # the skin closes the upper and lower faces as shear panels
    skin_pairs = ((0, 2), (1, 3))
    skin_area = 0.0
    if skin_t > 0:
        for a, b in zip(corners, corners[1:]):
            for (p, q) in skin_pairs:
                P0, P1 = np.array(nodes[a[p]]), np.array(nodes[a[q]])
                Q0 = np.array(nodes[b[p]])
                w = float(np.linalg.norm(P1 - P0))
                l = float(np.linalg.norm(Q0 - P0))
                skin_area += w * l / 1e6
                d = math.hypot(w, l)
                A_eq = skin_t * skin_G * d ** 3 / (2.0 * w * l * E_316)
                members.append((a[p], b[q], f"skin:{A_eq:.4f}"))
                members.append((a[q], b[p], f"skin:{A_eq:.4f}"))
    return nodes, members, corners, skin_area


def solve(nodes, members, corners):
    m = FEModel3D()
    m.add_material("s", E_316, G_316, 0.27, RHO_316 * 1000.0 / 1e9)
    A_b = 3 * A_ROD
    I_b = 1.5 * A_ROD * BOOM_R ** 2 + 3 * I_ROD
    m.add_section("boom", A_b, I_b, I_b, 0.12 * 2 * I_b)
    for i, (x, y, z) in enumerate(nodes):
        m.add_node(f"N{i}", x, y, z)
    seen = set()
    for k, (i, j, cls) in enumerate(members):
        if cls.startswith("skin:"):
            A_eq = float(cls.split(":")[1])
            nm = f"sk{A_eq:.4f}"
            if nm not in seen:
                m.add_section(nm, A_eq, 1e-3, 1e-3, 1e-3)
                seen.add(nm)
            m.add_member(f"M{k}", f"N{i}", f"N{j}", "s", nm)
        else:
            m.add_member(f"M{k}", f"N{i}", f"N{j}", "s", "boom")

    for n in corners[0]:
        m.def_support(f"N{n}", True, True, True, True, True, True)

    tip = corners[-1]
    zs = [nodes[n][2] for n in tip]
    arm = max(zs) - min(zs)
    T = 20_000.0                       # N.mm
    F = T / (2.0 * arm)
    for n in tip:
        m.add_node_load(f"N{n}", "FZ", F if nodes[n][0] < CHORD * 0.5 else -F,
                        "T")
    m.add_load_combo("T", {"T": 1.0})
    m.analyze_linear(check_statics=False, sparse=True)

    xs = [nodes[n][0] for n in tip]
    xarm = max(xs) - min(xs)
    dz = [m.nodes[f"N{n}"].DZ["T"] for n in tip]
    theta = (max(dz) - min(dz)) / xarm
    return T / math.degrees(theta), arm, xarm


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"ARC-1 furan demonstrator: {SPAN:.0f} x {CHORD:.0f} mm wing box, "
          f"t/c {THICK:.0%}")
    print(f"316L lattice, {ROD_D} mm rod, {BOOM_R:.0f} mm booms, "
          f"{BAY:.0f} mm bays\n")

    print(f"  {'skin':<24} {'G GPa':>6} {'Nm/deg':>9} {'skin kg':>8} "
          f"{'total kg':>9} {'skin adds':>10}")
    base = None
    res = {}
    for name, (t, G, areal) in SKINS.items():
        nodes, members, corners, area = build(t, G)
        K, arm, xarm = solve(nodes, members, corners)
        rods = [x for x in members if not x[2].startswith("skin:")]
        L_tot = sum(float(np.linalg.norm(np.array(nodes[i]) -
                                         np.array(nodes[j])))
                    for i, j, _ in rods)
        boom = __import__("boom", fromlist=["Boom"]).Boom(BOOM_R, bay=BAY)
        m_latt = boom.mass_per_m_kg() * L_tot / 1000.0
        m_skin = area * areal
        if base is None:
            base = K
        res[name] = dict(K=K, lattice_kg=m_latt, skin_kg=m_skin,
                         skin_m2=area)
        print(f"  {name:<24} {G/1000:>6.1f} {K:>9,.0f} {m_skin:>8.3f} "
              f"{m_latt+m_skin:>9.3f} {K/base:>9.2f}x")

    fc = res["Furan / carbon +-45"]
    ec = res["Epoxy / carbon +-45"]
    print(f"\n  The skin is doing the work: a bare lattice is "
          f"{base:,.0f} Nm/deg,")
    print(f"  the furan/carbon skin takes it to {fc['K']:,.0f} - "
          f"{fc['K']/base:.1f}x - for {fc['skin_kg']:.3f} kg.")
    print(f"  Against epoxy/carbon at {ec['K']:,.0f}, furan gives up "
          f"{(1-fc['K']/ec['K'])*100:.0f}%.")
    print(f"  THAT is the number to put on the datasheet: {fc['K']/base:.1f}x "
          f"torsional")
    print(f"  stiffness from {fc['skin_m2']:.2f} m2 of prepreg, and it still")
    print(f"  chars instead of burning.")

    # rod resonance on 316L vs the turbine band
    print("\n  --- rod resonance, 316L vs 6061 (turbine band 1,667-2,500 Hz) ---")
    for label, E, rho in (("316L", 193e9, 8000.0), ("6061", 68.9e9, 2700.0)):
        L, d = BAY / 1000.0, ROD_D / 1000.0
        I = math.pi * d ** 4 / 64.0
        A = math.pi * d * d / 4.0
        f1 = (math.pi / 2.0) / L ** 2 * math.sqrt(E * I / (rho * A))
        flag = "IN BAND" if 1667 <= f1 <= 2500 else "clear"
        print(f"      {label}  {BAY:.0f} mm x {ROD_D} mm rod : "
              f"{f1:>7,.0f} Hz   {flag}")
    print("      NOTE: at the SAME diameter both alloys land in the same")
    print("      place - E/rho is nearly identical, so f1 barely moves.")
    print("      The earlier 1,890 Hz in-band case was 6061 at 3.43 mm:")
    print("      aluminium needs a FATTER rod for the same capacity, and")
    print("      f1 scales with diameter. Stainless helps by staying thin,")
    print("      not by being stiffer.")

    (OUT / "demo_wing.json").write_text(json.dumps(res, indent=2))
    nodes, members, corners, _ = build(*SKINS["Furan / carbon +-45"][:2])
    (OUT / "demo_wing_geom.json").write_text(json.dumps({
        "nodes": nodes,
        "members": [[i, j, c] for i, j, c in members if not c.startswith("skin:")],
        "corners": corners, "span": SPAN, "chord": CHORD,
        "boom_R": BOOM_R, "bay": BAY, "rod_d": ROD_D,
    }))
    print(f"\n  wrote {OUT/'demo_wing.json'} and demo_wing_geom.json")


if __name__ == "__main__":
    main()
