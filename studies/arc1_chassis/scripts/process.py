"""ARC-1 :: rod mechanics for Nodal Arc Lattice Deposition.

Everything about the lattice geometry falls out of one question: how long can a
2.4 mm rod be before it buckles at a load lower than it can carry in tension?
Answer that and you have the cell size, and therefore the strut count, the mass
and the build time.

Run:  python scripts/process.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P = json.loads((ROOT / "cad" /
                __import__("os").environ.get("ARC1_PARAMS",
                                             "params.json")).read_text())


def section(d_mm: float) -> dict:
    """Area, second moment and radius of gyration of a solid round rod."""
    A = math.pi * (d_mm / 2.0) ** 2
    I = math.pi * d_mm ** 4 / 64.0
    r = math.sqrt(I / A)          # == d/4 exactly, kept computed as a check
    return {"A_mm2": A, "I_mm4": I, "r_mm": r}


def euler_critical_N(E_MPa: float, I_mm4: float, L_mm: float, K: float) -> float:
    return (math.pi ** 2) * E_MPa * I_mm4 / ((K * L_mm) ** 2)


def balanced_length(d_mm: float, K: float, slenderness: float) -> float:
    """Strut length at which KL/r hits the target slenderness.

    r = d/4, so KL/(d/4) = slenderness  ->  L = slenderness*d/(4K).
    """
    return slenderness * d_mm / (4.0 * K)


def johnson_critical_N(E_MPa: float, fy_MPa: float, A_mm2: float,
                       slend: float) -> float:
    """Inelastic (J.B. Johnson) column load - the real curve below the Euler
    transition, where Euler over-predicts badly."""
    Cc = math.sqrt(2.0 * math.pi ** 2 * E_MPa / fy_MPa)
    if slend >= Cc:
        return None
    return A_mm2 * fy_MPa * (1.0 - (slend ** 2) / (2.0 * Cc ** 2))


def main() -> None:
    pr, mat = P["process"], P["material"]
    d = pr["rod_diameter_mm"]
    K = pr["effective_length_factor_K"]
    E, fy, rho = mat["E_MPa"], mat["yield_MPa"], mat["density_g_cm3"]

    s = section(d)
    Cc = math.sqrt(2.0 * math.pi ** 2 * E / fy)

    print(f"=== rod section :: {d} mm {mat['name']} ===")
    print(f"  A  = {s['A_mm2']:.3f} mm2")
    print(f"  I  = {s['I_mm4']:.4f} mm4")
    print(f"  r  = {s['r_mm']:.3f} mm   (d/4 = {d/4:.3f})")
    print(f"  tension yield capacity      = {s['A_mm2']*fy:,.0f} N")
    print(f"  transition slenderness Cc   = {Cc:.1f}")

    print(f"\n=== column capacity vs strut length (K={K}) ===")
    print(f"{'L mm':>6} {'KL/r':>7} {'Pcr N':>9} {'curve':>8} "
          f"{'vs tension':>11}")
    Pt = s["A_mm2"] * fy
    for L in (30, 40, 50, 60, 70, 80, 100, 120, 150):
        slend = K * L / s["r_mm"]
        Pj = johnson_critical_N(E, fy, s["A_mm2"], slend)
        Pe = euler_critical_N(E, s["I_mm4"], L, K)
        Pcr, curve = (Pj, "Johnson") if Pj is not None else (Pe, "Euler")
        Pcr = min(Pcr, Pt)
        print(f"{L:>6} {slend:>7.1f} {Pcr:>9,.0f} {curve:>8} "
              f"{Pcr/Pt:>10.0%}")

    Lb = balanced_length(d, K, pr["slenderness_limit"])
    print(f"\n=== design rule ===")
    print(f"  target slenderness KL/r = {pr['slenderness_limit']:.0f}")
    print(f"  -> L = slenderness*d/(4K) = {Lb:.1f} mm")
    print(f"  -> L/d = {Lb/d:.1f}   <-- the whole lattice scales on this ratio")
    print(f"  params strut_len_max_mm  = {pr['strut_len_max_mm']} mm "
          f"({'MATCHES' if abs(Lb - pr['strut_len_max_mm']) < 1.0 else 'MISMATCH'})")

    # what one strut costs you
    L = pr["strut_len_max_mm"]
    vol_mm3 = s["A_mm2"] * L
    m_strut = vol_mm3 / 1000.0 * rho          # g
    ball_r = pr["node_ball_dia_mm"] / 2.0
    m_ball = (4.0 / 3.0 * math.pi * ball_r ** 3) / 1000.0 * rho
    print(f"\n=== unit costs at L = {L:.0f} mm ===")
    print(f"  strut mass      = {m_strut:.2f} g")
    print(f"  node ball mass  = {m_ball:.2f} g "
          f"({pr['node_ball_dia_mm']} mm dia)")
    print(f"  strut cycle     = {pr['rod_place_cycle_s'] + pr['arc_cycle_s']:.1f} s")

    # joint capacity
    Pj_bare = Pt * pr["node_efficiency_bare"]
    Pj_col = Pt * pr["node_efficiency_collared"]
    print(f"\n=== joint capacity (governs, not the rod) ===")
    print(f"  bare arc insertion   = {Pj_bare:,.0f} N "
          f"({pr['node_efficiency_bare']:.0%} of parent)")
    print(f"  + WAAM collar pass   = {Pj_col:,.0f} N "
          f"({pr['node_efficiency_collared']:.0%} of parent)")
    print(f"  design strut capacity (min of buckling, tension, joint):")
    slend = K * L / s["r_mm"]
    Pj_col_buck = min(johnson_critical_N(E, fy, s["A_mm2"], slend) or 1e9, Pt)
    print(f"    compression = {min(Pj_col_buck, Pj_col):,.0f} N")
    print(f"    tension     = {Pj_col:,.0f} N")

    out = ROOT / P["output"]["dir"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "process_rules.json").write_text(json.dumps({
        "section": s,
        "Cc": Cc,
        "balanced_length_mm": Lb,
        "L_over_d": Lb / d,
        "tension_capacity_N": Pt,
        "compression_capacity_N": min(Pj_col_buck, Pj_col),
        "joint_capacity_collared_N": Pj_col,
        "joint_capacity_bare_N": Pj_bare,
        "strut_mass_g": m_strut,
        "node_ball_mass_g": m_ball,
    }, indent=2))
    print(f"\nwrote {out / 'process_rules.json'}")


if __name__ == "__main__":
    main()
