"""ARC-1 :: what polyurethane foam fill actually does to a rod lattice.

Three separate questions, three different answers:

  1. GLOBAL STIFFNESS   - almost nothing. E_foam/E_steel is ~0.01 %.
  2. BUCKLING           - everything. A rod embedded in foam is a column on an
                          elastic foundation, and its critical load stops
                          depending on length at all. This breaks the L/d = 25
                          rule that governs the entire design.
  3. LOCAL / FUNCTIONAL - impact absorption, and stopping a bonded carbon skin
                          from buckling locally so it can develop full shear.

Run:  python scripts/foamfill.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from boom import Boom, A_ROD, I_ROD, E, MAT, PR

ROOT = Path(__file__).resolve().parents[1]
P = json.loads((ROOT / "cad" /
                __import__("os").environ.get("ARC1_PARAMS",
                                             "params.json")).read_text())
OUT = ROOT / P["output"]["dir"]

FY = MAT["yield_MPa"]
P_YIELD = A_ROD * FY
K_EFF = PR["effective_length_factor_K"]
R_GYR = PR["rod_diameter_mm"] / 4.0

# rigid closed-cell PU: density -> Young's modulus (typical supplier data)
FOAMS = [
    (16.0,   2.5, 0.16),
    (32.0,   8.0, 0.30),
    (48.0,  16.0, 0.45),
    (64.0,  26.0, 0.70),
    (96.0,  50.0, 1.30),
    (160.0, 110.0, 2.80),
]


def p_cr_free(L):
    """Free rod: Johnson below the transition, Euler above."""
    Cc = math.sqrt(2.0 * math.pi ** 2 * E / FY)
    s = K_EFF * L / R_GYR
    if s < Cc:
        return min(A_ROD * FY * (1 - s ** 2 / (2 * Cc ** 2)), P_YIELD)
    return min(math.pi ** 2 * E * I_ROD / ((K_EFF * L) ** 2), P_YIELD)


def p_cr_foundation(k):
    """Column on a continuous elastic foundation of modulus k (N/mm per mm).
    For a long column the critical load is 2*sqrt(EI*k) - INDEPENDENT OF
    LENGTH. That is the whole point."""
    return 2.0 * math.sqrt(E * I_ROD * k)


def half_wave(k):
    """Buckle half-wavelength. The foundation model only applies if the strut
    is longer than this."""
    return math.pi * (E * I_ROD / k) ** 0.25


def main():
    print("=" * 74)
    print("1. GLOBAL STIFFNESS CONTRIBUTION - the part that does NOT work")
    print("=" * 74)
    for rho, Ef, _ in FOAMS[:4]:
        print(f"  PU {rho:>5.0f} kg/m3   E = {Ef:>5.1f} MPa   "
              f"E_foam/E_316L = {Ef/E:.4%}")
    print("  Filling the lattice adds essentially nothing to axial or bending")
    print("  stiffness. Do not fill it expecting a stiffer chassis.")

    print("\n" + "=" * 74)
    print("2. BUCKLING ON AN ELASTIC FOUNDATION - the part that does work")
    print("=" * 74)
    print(f"  rod yield capacity          {P_YIELD:>7,.0f} N")
    print(f"  free 60 mm rod (governing)  {p_cr_free(60):>7,.0f} N  "
          f"= {p_cr_free(60)/P_YIELD:.0%} of yield")
    print(f"\n  {'PU kg/m3':>9} {'E MPa':>7} {'k':>7} {'P_cr N':>9} "
          f"{'vs yield':>9} {'half-wave':>10}")
    for rho, Ef, _ in FOAMS:
        k = Ef                      # conservative: k = E_f for a fully
        pcr = p_cr_foundation(k)    # embedded cylinder (1-2 x E_f is typical)
        hw = half_wave(k)
        cap = min(pcr, P_YIELD)
        print(f"  {rho:>9.0f} {Ef:>7.1f} {k:>7.1f} {pcr:>9,.0f} "
              f"{cap/P_YIELD:>8.0%} {hw:>9.0f} mm")
    print("\n  Even 16 kg/m3 foam takes the rod past its own yield load.")
    print("  Embedded, the rod is YIELD-limited, not buckling-limited, at ANY")
    print("  length. The L/d = 25 rule stops applying.")

    print("\n" + "=" * 74)
    print("3. WHAT THAT BUYS - redesign at a longer bay")
    print("=" * 74)
    R = 34.0
    base = Boom(R, bay=60.0)
    rod_kg_60 = 34.37
    node_kg_60 = 8.05
    n_rods_60 = 16273
    print(f"  {'bay':>5} {'diag':>7} {'rod m/m':>8} {'rods':>8} "
          f"{'rod kg':>7} {'node kg':>8} {'steel kg':>9}")
    rows = []
    for bay in (60, 80, 100, 120, 150):
        b = Boom(R, bay=float(bay))
        f_rod = b.rod_length_per_m() / base.rod_length_per_m()
        f_node = 60.0 / bay
        rod_kg = rod_kg_60 * f_rod
        node_kg = node_kg_60 * f_node
        nr = int(n_rods_60 * (1.0 / 3.0 + (2.0 / 3.0) * (60.0 / bay)))
        rows.append((bay, rod_kg, node_kg, nr))
        print(f"  {bay:>5} {b.diagonal:>7.1f} "
              f"{b.rod_length_per_m()/1000:>8.2f} {nr:>8,} "
              f"{rod_kg:>7.1f} {node_kg:>8.1f} {rod_kg+node_kg:>9.1f}")

    print("\n  ...but the foam itself has mass. Boom interiors only:")
    boom_vol_L = math.pi * R ** 2 * 93_100.0 / 1e6      # 93.1 m of boom
    print(f"  boom interior volume = {boom_vol_L:.0f} L "
          f"(pi*{R:.0f}mm^2 x 93.1 m)")
    print(f"\n  {'bay':>5} {'steel':>7} {'foam@32':>8} {'total':>7} "
          f"{'vs 60mm bare':>13} {'rods':>8}")
    steel_60 = rod_kg_60 + node_kg_60
    for (bay, rod_kg, node_kg, nr) in rows:
        steel = rod_kg + node_kg
        foam = boom_vol_L * 32.0 / 1000.0
        tot = steel + foam
        print(f"  {bay:>5} {steel:>7.1f} {foam:>8.1f} {tot:>7.1f} "
              f"{tot-steel_60:>+12.1f} {nr:>8,}")

    print("\n  VERDICT: filling the booms is roughly MASS-NEUTRAL. The gain is")
    print("  not weight - it is that a 120 mm bay needs ~11,000 fewer rods:")
    r120 = [r for r in rows if r[0] == 120][0]
    saved = n_rods_60 - r120[3]
    cyc = PR["rod_place_cycle_s"] + 2 * PR["arc_cycle_s"]
    print(f"    {saved:,} fewer rods  =  {saved*cyc/3600:.0f} h less robot time")
    print(f"    {saved:,} fewer welded joints to fatigue-crack")

    print("\n" + "=" * 74)
    print("4. SELECTIVE FILL - where it is unambiguously worth it")
    print("=" * 74)
    import trimesh
    env_path = OUT / "arc1_envelope.stl"
    if not env_path.exists():  # fall back to the committed copy of the Fusion export
        env_path = ROOT / "cad" / "arc1_envelope.stl"
    env = trimesh.load(env_path)
    area_m2 = env.area / 1e6

    # nose region volume, by slicing the envelope
    nose = env.slice_plane([480, 0, 0], [-1, 0, 0], cap=True)
    nose_L = abs(nose.volume) / 1e6 if nose is not None else 0.0

    skin_t = 0.025
    skin_L = area_m2 * skin_t * 1000.0
    floor_L = 0.62 * 2.0 * 0.06 * 1000.0

    regions = [
        ("Nose / impact attenuator", nose_L, 64.0,
         "crushable structure - foam+lattice is an excellent attenuator"),
        ("Backing the bonded carbon skin (25 mm)", skin_L, 32.0,
         "stops local skin buckling so the skin develops its full shear"),
        ("Floor sandwich (60 mm)", floor_L, 32.0,
         "closed, low inspection need, adds underbody shear"),
    ]
    tot = 0.0
    for name, vol_L, rho, why in regions:
        kg = vol_L * rho / 1000.0
        tot += kg
        print(f"  {name:<40} {vol_L:>6.0f} L @ {rho:>3.0f} = {kg:>5.1f} kg")
        print(f"    {why}")
    print(f"  {'TOTAL selective fill':<40} {'':>6}        {tot:>5.1f} kg")

    print("\n" + "=" * 74)
    print("5. WHY NOT TO FILL THE WHOLE THING (yet)")
    print("=" * 74)
    for s in [
        "Buries 5,603 welded joints you cannot inspect - on a structure with",
        "  NO fatigue data. That is the wrong order to do things in.",
        "Kills the repairability argument, which was ARC-1's main advantage",
        "  over carbon in the first place.",
        "Moisture trapped against 316L in a closed cell you can never drain",
        "  is a crevice-corrosion trap.",
        "PU degrades above ~100-120 C - keep it away from brakes, motor and",
        "  inverter.",
        "PU burns and gives off HCN. Fire-retardant grade only, near a driver.",
    ]:
        print(f"  - {s}")


if __name__ == "__main__":
    main()
