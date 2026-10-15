"""ARC-1 :: would the arc-lattice method work for a UAV airframe in aluminium?

The car answer was "aluminium and titanium barely help", because that used
E/rho - the right metric when geometry is FIXED. For a strut you are free to
re-size, the buckling-critical metric is different:

    m  ~  rho / sqrt(E)          (Euler-governed, diameter free to change)

and on that measure aluminium beats steel by 1.8x. The HAZ softening that
rules aluminium out of strength-critical weldments largely does NOT bite here,
because buckling depends on E, and E is indifferent to temper.

Run:  python scripts/uav_alloy.py
"""
from __future__ import annotations

import math

# name: E GPa, rho g/cm3, yield as-welded MPa, note
MATS = {
    "316L stainless":   (193.0, 8.00, 290.0, "austenitic - welding does not soften it"),
    "6061 (welded HAZ)": (68.9, 2.70, 125.0, "T6 -> ~T4/O at every node"),
    "6061-T6 + PWHT":   (68.9, 2.70, 276.0, "solution treat + age AFTER welding"),
    "5083 (welded)":    (70.3, 2.66, 125.0, "non-heat-treatable, predictable"),
    "Ti-6Al-4V":       (114.0, 4.43, 750.0, "needs a chamber"),
    "CFRP UD (ref)":   (135.0, 1.60, 600.0, "not weldable - benchmark only"),
}

K = 0.8          # effective length factor
TARGET_P = 992.0  # N - the rev A strut capacity, so we compare like for like
TARGET_L = 60.0   # mm


def cc(E_MPa, fy):
    return math.sqrt(2.0 * math.pi ** 2 * E_MPa / fy)


def capacity(d, L, E_MPa, fy):
    """Johnson / Euler column capacity of a solid round rod."""
    A = math.pi * d * d / 4.0
    I = math.pi * d ** 4 / 64.0
    s = K * L / (d / 4.0)
    if s < cc(E_MPa, fy):
        P = A * fy * (1.0 - s * s / (2.0 * cc(E_MPa, fy) ** 2))
    else:
        P = math.pi ** 2 * E_MPa * I / ((K * L) ** 2)
    return min(P, A * fy)


def size_for(P, L, E_MPa, fy):
    """Smallest rod diameter that carries P over length L."""
    lo, hi = 0.3, 30.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if capacity(mid, L, E_MPa, fy) < P:
            lo = mid
        else:
            hi = mid
    return hi


def main():
    print("=" * 76)
    print("1. THE RIGHT METRIC")
    print("=" * 76)
    print("  For a strut whose DIAMETER is fixed, what matters is E/rho.")
    print("  For a strut you are free to re-size, it is sqrt(E)/rho.")
    print("  Every structural metal has nearly the same E/rho - which is why")
    print("  titanium gained only 7% on the car. sqrt(E)/rho is different.\n")
    print(f"  {'material':<20} {'E/rho':>8} {'sqrt(E)/rho':>12} {'vs 316L':>9}")
    base = math.sqrt(193.0) / 8.00
    for n, (E, rho, fy, _) in MATS.items():
        print(f"  {n:<20} {E/rho:>8.1f} {math.sqrt(E)/rho:>12.3f} "
              f"{math.sqrt(E)/rho/base:>8.2f}x")

    print("\n" + "=" * 76)
    print(f"2. RE-SIZED STRUT - same {TARGET_P:.0f} N over the same "
          f"{TARGET_L:.0f} mm")
    print("=" * 76)
    print(f"  {'material':<20} {'dia mm':>7} {'L/d':>6} {'slend':>7} "
          f"{'area':>7} {'mass g':>7} {'vs 316L':>9}")
    ref = None
    rows = {}
    for n, (E, rho, fy, _) in MATS.items():
        d = size_for(TARGET_P, TARGET_L, E * 1000.0, fy)
        A = math.pi * d * d / 4.0
        m = A * TARGET_L * rho / 1000.0
        s = K * TARGET_L / (d / 4.0)
        if ref is None:
            ref = m
        rows[n] = (d, m)
        print(f"  {n:<20} {d:>7.2f} {TARGET_L/d:>6.1f} {s:>7.1f} "
              f"{A:>7.2f} {m:>7.3f} {m/ref:>8.2f}x")
    print("\n  Aluminium needs a FATTER rod (lower E) but it is so much less")
    print("  dense that the strut still comes out lighter. Note L/d is no")
    print("  longer 25 - that number was specific to 2.4 mm 316L.")

    print("\n" + "=" * 76)
    print("3. DOES THE WELD-SOFTENED HAZ MATTER?")
    print("=" * 76)
    haz = rows["6061 (welded HAZ)"][1]
    pwht = rows["6061-T6 + PWHT"][1]
    print(f"  6061 welded HAZ : {haz:.3f} g/strut")
    print(f"  6061 + post-weld heat treat : {pwht:.3f} g/strut "
          f"({(1-pwht/haz)*100:.0f}% lighter)")
    print("  With 60 mm struts and a ~25 mm HAZ either end, EVERY strut is")
    print("  entirely heat-affected. There is no parent material anywhere in")
    print("  the lattice. In a strength-critical weldment that is fatal.")
    print("  Here it is survivable, because buckling depends on E and E does")
    print("  not care about temper - only the short-column (Johnson) end of")
    print("  the curve loses out, worth the ~%.0f%% above." % ((1-pwht/haz)*100))
    print("\n  At UAV scale, post-weld solution-treat + age is actually")
    print("  practical (a 1 m airframe fits a furnace; a 2.6 m car chassis")
    print("  would distort on the quench). That is a real option here that")
    print("  did not exist for the chassis.")

    print("\n" + "=" * 76)
    print("4. A REPRESENTATIVE UAV TAIL BOOM - 1.2 m, same bending stiffness")
    print("=" * 76)
    # reference: 30 x 1 mm filament-wound CFRP tube
    od, wall, E_cf, rho_cf = 30.0, 1.0, 100.0e3, 1.60
    I_ref = math.pi * (od ** 4 - (od - 2 * wall) ** 4) / 64.0
    EI_ref = E_cf * I_ref
    A_cf = math.pi * (od ** 2 - (od - 2 * wall) ** 2) / 4.0
    m_cf = A_cf * rho_cf / 1000.0
    print(f"  CFRP tube {od:.0f} x {wall:.0f} mm : I = {I_ref:,.0f} mm4  "
          f"EI = {EI_ref:.3g}  {m_cf:.3f} kg/m")

    print(f"\n  {'lattice boom':<26} {'R mm':>6} {'chord d':>8} "
          f"{'rod m/m':>8} {'kg/m':>7} {'vs CFRP':>9}")
    for name in ("316L stainless", "6061 (welded HAZ)", "6061-T6 + PWHT"):
        E, rho, fy, _ = MATS[name]
        for R in (25.0, 40.0):
            # 3 chords on radius R: I = 1.5*A*R^2  -> A from the EI target
            A_need = EI_ref / (E * 1000.0) / (1.5 * R * R)
            d = math.sqrt(4.0 * A_need / math.pi)
            bay = 25.0 * d if name == "316L stainless" else 25.0 * d
            batten = R * math.sqrt(3.0)
            diag = math.hypot(batten, bay)
            rod_m = 3.0 + (1000.0 / bay) * 3.0 * (batten + diag) / 1000.0
            kg = rod_m * 1000.0 * A_need * rho / 1e6
            print(f"  {name:<26} {R:>6.0f} {d:>8.2f} {rod_m:>8.2f} "
                  f"{kg:>7.3f} {kg/m_cf:>8.2f}x")

    print("\n" + "=" * 76)
    print("5. THE PROCESS REALITY - aluminium is harder to stud weld")
    print("=" * 76)
    for s in [
        "Al2O3 oxide melts at 2072 C; the aluminium under it melts at 660 C.",
        "  The oxide MUST be disrupted - argon shielding is mandatory, where",
        "  steel CD stud welding can often be done in open air.",
        "Aluminium conducts heat ~4x faster than steel, so the arc energy",
        "  drains away - higher current, tighter timing window.",
        "Thermal expansion is ~2x steel's: a growing lattice distorts more,",
        "  and the build sequence has to manage it.",
        "CD stud welding of aluminium IS commercial, but mostly at <=8 mm.",
        "  A 3-4 mm rod is inside that range - this is not exotic.",
        "Fatigue: aluminium has no endurance limit. A UAV with prop or engine",
        "  excitation and thousands of welded nodes needs a real fatigue",
        "  spectrum, not a static check. This is the main risk, not strength.",
    ]:
        print(f"  - {s}")


if __name__ == "__main__":
    main()
