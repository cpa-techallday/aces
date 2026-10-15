"""ARC-1 :: aluminium lattice wing + PFA prepreg skin - does it work?

The conflict: PFA is a fluorothermoplastic that consolidates at 350-400 C.
6061 anneals at ~415 C and over-ages from ~200 C. Consolidating a PFA skin
onto a 6061 lattice puts the whole structure through a heat cycle that takes
it towards O temper - and unlike the weld HAZ, this one is NOT survivable,
because a fully annealed strut stops being buckling-governed and becomes
yield-governed, where aluminium's advantage disappears.

Run:  python scripts/wing_skin.py
"""
from __future__ import annotations

import math

K = 0.8
TARGET_P = 992.0
TARGET_L = 60.0

# 6061 tempers: yield MPa (E is 68.9 GPa in every one of them)
TEMPERS = {
    "T6 (as supplied)":      276.0,
    "T6 + weld HAZ":         125.0,
    "T4 (over-aged)":        145.0,
    "O (fully annealed)":     55.0,
}
E_AL = 68.9e3
RHO_AL = 2.70

# skin systems: E GPa, G GPa (in-plane shear, the one that matters for a
# torsion box), CTE ue/C, process temp C, areal kg/m2 at 0.5 mm
SKINS = {
    "PFA neat film":        (0.6,  0.2, 140.0, 380.0, 1.09),
    "PFA / glass fabric":   (15.0, 2.5, 60.0,  380.0, 0.95),
    "PEI (ULTEM) / glass":  (22.0, 4.0, 30.0,  320.0, 0.90),
    "Epoxy / glass +-45":   (22.0, 6.0, 14.0,  177.0, 0.95),
    "Epoxy / carbon +-45":  (60.0, 30.0, 3.0,  177.0, 0.78),
}


def cc(E, fy):
    return math.sqrt(2.0 * math.pi ** 2 * E / fy)


def capacity(d, L, E, fy):
    A = math.pi * d * d / 4.0
    I = math.pi * d ** 4 / 64.0
    s = K * L / (d / 4.0)
    if s < cc(E, fy):
        P = A * fy * (1.0 - s * s / (2.0 * cc(E, fy) ** 2))
    else:
        P = math.pi ** 2 * E * I / ((K * L) ** 2)
    return min(P, A * fy)


def size_for(P, L, E, fy):
    lo, hi = 0.3, 40.0
    for _ in range(90):
        mid = 0.5 * (lo + hi)
        if capacity(mid, L, E, fy) < P:
            lo = mid
        else:
            hi = mid
    return hi


def main():
    print("=" * 76)
    print("1. WHAT THE SKIN CURE DOES TO THE LATTICE")
    print("=" * 76)
    print("  6061 ages at 160-177 C, over-ages from ~200 C, anneals at ~415 C.")
    print("  E is 68.9 GPa in EVERY temper - only yield moves.\n")
    print(f"  {'temper':<22} {'fy MPa':>7} {'dia mm':>7} {'slend':>7} "
          f"{'mass g':>7} {'vs 316L':>9} {'governed by':>13}")
    ref_316 = 2.171
    for name, fy in TEMPERS.items():
        d = size_for(TARGET_P, TARGET_L, E_AL, fy)
        A = math.pi * d * d / 4.0
        m = A * TARGET_L * RHO_AL / 1000.0
        s = K * TARGET_L / (d / 4.0)
        gov = "buckling" if s > 0.75 * cc(E_AL, fy) else "YIELD"
        print(f"  {name:<22} {fy:>7.0f} {d:>7.2f} {s:>7.1f} {m:>7.3f} "
              f"{m/ref_316:>8.2f}x {gov:>13}")

    print("\n  This is the whole problem. In T6 or even weld-HAZ temper the")
    print("  strut is BUCKLING-governed, so E carries it and aluminium wins.")
    print("  Annealed, it flips to YIELD-governed - and a yield-governed")
    print("  aluminium strut is heavier than the stainless one it replaced.")
    print("  A 380 C PFA consolidation is an annealing cycle in all but name.")

    print("\n" + "=" * 76)
    print("2. SKIN SYSTEMS - stiffness, cure temp, and CTE against 6061")
    print("=" * 76)
    print(f"  {'system':<24} {'E GPa':>6} {'G GPa':>6} {'cure C':>7} "
          f"{'CTE':>6} {'dCTE vs Al':>11} {'temper after':>14}")
    for name, (E, G, cte, tp, areal) in SKINS.items():
        if tp >= 350:
            after = "O (annealed)"
        elif tp >= 250:
            after = "over-aged"
        elif tp <= 190:
            after = "T6 RETAINED"
        else:
            after = "partial"
        print(f"  {name:<24} {E:>6.1f} {G:>6.1f} {tp:>7.0f} {cte:>6.0f} "
              f"{cte-23.6:>+10.0f} {after:>14}")

    print("\n  Differential growth over a 1.5 m wing, cure -> -20 C ambient:")
    for name, (E, G, cte, tp, areal) in SKINS.items():
        dT = tp - (-20.0)
        dL = (cte - 23.6) * 1e-6 * 1500.0 * dT
        print(f"    {name:<24} {dL:>+7.1f} mm  (dT = {dT:.0f} C)")

    print("\n" + "=" * 76)
    print("3. SHEAR STIFFNESS - what a wing skin is actually for")
    print("=" * 76)
    print("  A wing skin's job is carrying torsion and shear, so G is the")
    print("  number, not E. Relative to epoxy/carbon +-45:\n")
    gref = SKINS["Epoxy / carbon +-45"][1]
    for name, (E, G, cte, tp, areal) in SKINS.items():
        bar = "#" * max(1, int(38 * G / gref))
        print(f"    {name:<24} G={G:>5.1f} GPa  {G/gref:>5.1%}  {bar}")
    print("\n  Neat PFA has essentially no shear stiffness - it is a COVER,")
    print("  not a structure. PFA/glass is ~8% of carbon/epoxy. If you want")
    print("  the skin to carry load, PFA is the wrong family.")

    print("\n" + "=" * 76)
    print("4. BONDING - the one that usually decides it")
    print("=" * 76)
    for s in [
        "PFA surface energy is ~18-20 mN/m. It is used AS a release film",
        "  precisely because nothing sticks to it. Bonding it to aluminium",
        "  needs sodium-naphthalenide etch, plasma, or a tie layer.",
        "An unbonded skin contributes ZERO structural stiffness. On the car,",
        "  the bonded carbon skin was worth +73% torsion; an unbonded one",
        "  would have been worth nothing.",
        "PFA also needs 350-400 C AND pressure to consolidate - press or",
        "  autoclave. That gives back the no-tooling advantage that made the",
        "  lattice attractive in the first place.",
    ]:
        print(f"  - {s}")

    print("\n" + "=" * 76)
    print("5. THE ELEGANT ALTERNATIVE")
    print("=" * 76)
    print("  6061-T6 artificial ageing is 160 C/18 h or 177 C/8 h.")
    print("  177 C is also the standard aerospace epoxy prepreg cure.")
    print("  So: solution-treat and quench the bare lattice, lay up the skin,")
    print("  then CO-CURE THE SKIN AND AGE THE LATTICE IN ONE OVEN CYCLE.")
    print("  You get full T6 temper and a bonded structural skin from a")
    print("  single operation, with no autoclave.")
    print("\n  If PFA is wanted for chemical, thermal or RF reasons, use it as")
    print("  a thin non-structural liner or surface film OVER an epoxy")
    print("  structural skin - not as the structural skin itself.")

    turbojet()


def turbojet():
    print("\n" + "=" * 76)
    print("6. TURBOJET PROPULSION - two things it changes")
    print("=" * 76)

    # --- temperature zoning -------------------------------------------
    print("  (a) TEMPERATURE ZONING. 6061 retains roughly:")
    retain = {20: 1.00, 100: 0.95, 150: 0.85, 200: 0.60,
              250: 0.40, 300: 0.20, 350: 0.10}
    for T, f in retain.items():
        bar = "#" * int(34 * f)
        print(f"      {T:>4} C : {f:>5.0%} of room-temperature yield  {bar}")
    print("      A small turbine runs 550-750 C EGT; skin near the exhaust")
    print("      sees 200-400 C. Aluminium is finished there.")
    print("\n      The arc-lattice process does not care - it is the same")
    print("      robot and the same arc, just a different rod. ZONE IT:")
    print("        cool wing / fuselage    -> 6061 or 5083")
    print("        engine bay, tailpipe    -> 316L or Ti-6Al-4V")
    print("        transition              -> stainless, bolted or bi-metal")
    print("      THIS is where PFA earns its place: as the skin over the hot")
    print("      zone (260 C continuous) where epoxy cannot go. Not as the")
    print("      structural skin of the cool wing.")

    # --- rod resonance -------------------------------------------------
    print("\n  (b) ROD RESONANCE - the one that would bite hardest.")
    print("      A small turbojet spins 100,000-150,000 rpm = 1,667-2,500 Hz.")
    print("      First bending mode of one pinned 6061 rod:\n")
    E, rho = 68.9e9, 2700.0
    print(f"      {'L mm':>6} {'d mm':>6} {'f1 Hz':>9}   {'vs turbine band':>18}")
    for L_mm, d_mm in ((40, 3.43), (50, 3.43), (60, 3.43), (60, 4.87),
                       (80, 3.43), (100, 3.43)):
        L, d = L_mm / 1000.0, d_mm / 1000.0
        I = math.pi * d ** 4 / 64.0
        A = math.pi * d * d / 4.0
        f1 = (math.pi / 2.0) / L ** 2 * math.sqrt(E * I / (rho * A))
        if 1667 <= f1 <= 2500:
            flag = "** IN BAND **"
        elif f1 < 1667:
            flag = "below"
        else:
            flag = "above"
        print(f"      {L_mm:>6} {d_mm:>6.2f} {f1:>9,.0f}   {flag:>18}")
    print("\n      A 60 mm x 3.43 mm rod sits INSIDE the turbine's operating")
    print("      band. At 2,000 Hz you accumulate 10^7 cycles in 1.4 hours -")
    print("      an entire high-cycle fatigue life in one afternoon's flying,")
    print("      and aluminium has no endurance limit to stop at.")
    print("\n      Fixes, in order of preference:")
    print("        1. Detune: shorten the rods (40 mm -> ~4,250 Hz, clear")
    print("           above the band) or fatten them.")
    print("        2. Damp: foam fill also raises f1 and adds loss - but keep")
    print("           PU away from the hot zone (degrades ~110 C).")
    print("        3. Isolate the turbine on soft mounts so the excitation")
    print("           never reaches the lattice at full amplitude.")
    print("      Do a modal survey of a real module before flying this.")



if __name__ == "__main__":
    main()
