"""ARC-1 :: where a furan-skinned arc lattice is genuinely the right answer.

Not a brainstorm. The property stack this combination actually has is narrow
and specific, and it rules out more applications than it opens. Work out what
it is good at FIRST, then the uses follow.

Run:  python scripts/applications.py
"""
from __future__ import annotations

import math

RHO_316 = 8.0e-3        # g/mm3
ROD_D = 2.4
A_ROD = math.pi * (ROD_D / 2.0) ** 2
C_AIR = 343_000.0       # mm/s


def panel_areal_mass(cell_mm, depth_mm):
    """Rod mass per m2 of a two-skin lattice sandwich panel.

    Edges per unit area for a triangulated skin of edge L is 3.46/L, so a
    sandwich is 2 skins + ties + web diagonals.
    """
    L = cell_mm
    per_m2 = 2.0 * (3.46 / (L / 1000.0))          # both skins, m of rod per m2
    verts = 1.0 / (0.866 * (L / 1000.0) ** 2)     # vertices per m2
    per_m2 += verts * (depth_mm / 1000.0)          # through ties
    diag = math.hypot(L, depth_mm) / 1000.0
    per_m2 += (3.46 / (L / 1000.0)) * (diag / (L / 1000.0))   # web diagonals
    return per_m2 * 1000.0 * A_ROD * RHO_316 / 1000.0          # kg/m2


def main():
    print("=" * 76)
    print("1. WHAT IT IS ACTUALLY GOOD AT - and the thing it is NOT")
    print("=" * 76)
    print("  A lattice puts material at a radius. That pays when there is")
    print("  VOLUME to exploit. In a thin panel there is none, and the")
    print("  lattice loses badly to honeycomb:\n")
    print(f"  {'cell':>6} {'depth':>7} {'kg/m2':>8}   reference")
    refs = [(30, 30), (40, 40), (60, 60), (60, 100)]
    for cell, depth in refs:
        print(f"  {cell:>5} mm {depth:>5} mm {panel_areal_mass(cell, depth):>8.1f}")
    print(f"  {'':>6} {'':>7} {'':>8}   Nomex honeycomb + 2 skins ~ 2-4 kg/m2")
    print(f"  {'':>6} {'':>7} {'':>8}   1 mm aluminium sheet      ~ 2.7 kg/m2")
    print("\n  So: NOT flat panels. Not cargo liners, not rail interiors, not")
    print("  a battery LID. Every one of those is honeycomb's home ground and")
    print("  the lattice is 4-10x heavier there.")
    print("\n  It wins where there is 3D VOLUME and GEOMETRIC COMPLEXITY, and")
    print("  where the open interior is doing a second job.")

    print("\n" + "=" * 76)
    print("2. THE PROPERTY STACK NOTHING ELSE HAS")
    print("=" * 76)
    for s in [
        "A metal skeleton that does not burn, under a skin that CHARS to 55%",
        "  carbon. Load path survives the fire; the surface becomes its own",
        "  insulation. No epoxy composite can do this, and no honeycomb",
        "  panel gives you the 3D freedom.",
        "~95% open interior, addressable cell by cell, with the cell SIZE",
        "  free to vary spatially. Honeycomb has one depth per panel.",
        "No moulds. Geometry costs nothing, so every part can be different.",
        "Electrically continuous metal - EMI, lightning path, bonding.",
        "LOI 50, very low smoke and toxicity, 200 C service, bio-based.",
        "Repairable in place by the same robot that built it.",
    ]:
        print(f"  * {s}")

    print("\n" + "=" * 76)
    print("3. THE USES THAT FOLLOW")
    print("=" * 76)

    print("\n  [A] GRADED-DEPTH ACOUSTIC LINER  - the strongest novel case")
    print("      A quarter-wave resonator absorbs at f = c/4d. Honeycomb is")
    print("      stuck at one depth per panel, so a liner is single- or")
    print("      double-degree-of-freedom and narrowband. A lattice cell")
    print("      depth can vary continuously across the part:\n")
    print(f"        {'cell depth':>11} {'peak absorption':>17}")
    for d in (20, 30, 45, 60, 80, 100, 150):
        print(f"        {d:>8} mm {C_AIR/(4.0*d):>14,.0f} Hz")
    print("\n      Grading 20-150 mm covers ~570-4,290 Hz in ONE part - the")
    print("      whole turbomachinery tone range, broadband, and it is a fire")
    print("      zone so the furan skin is doing two jobs at once. Perforate")
    print("      the skin as the facing sheet and the lattice is the septum.")
    print("      Nacelle and duct liners are a real, specified market.")

    print("\n  [B] BATTERY / CELL ENCLOSURE AS A 3D SPACE FRAME")
    print("      Not as a panel - as the module frame itself. Cells nest in")
    print("      the interstices, the lattice is simultaneously the crush")
    print("      structure, the cell retention and the fire barrier, and the")
    print("      charring skin is what stops a runaway propagating out.")
    print("      Regulation is the driver: no-fire-no-explosion requirements")
    print("      are tightening, and the usual answers are heavy steel or")
    print("      expensive mica/intumescent layers over a resin that burns.")

    print("\n  [C] TURBOJET NACELLE, FIREWALL AND TAILPIPE SHROUD")
    print("      Designated fire zone, 200-400 C skin, complex 3D shape,")
    print("      low volume. Everything the stack is for, and it is the part")
    print("      of your own UAV that epoxy cannot do.")

    print("\n  [D] IN-SITU REINFORCEMENT AND REPAIR")
    print("      The robot grows a lattice ONTO an existing structure and you")
    print("      wrap it. No mould, no removal, no hot work beyond the arc.")
    print("      Strengthening plant, ship structure, bridge members - where")
    print("      furan's chemical resistance is an asset rather than a")
    print("      footnote. This exploits the PROCESS, not just the material.")

    print("\n  [E] PYROLYSED CARBON SKIN ON A REFRACTORY LATTICE")
    print("      Furan is a classic glassy-carbon precursor. Pyrolyse the")
    print("      skin deliberately and you get a carbon matrix on a metal")
    print("      skeleton without a months-long CVI cycle. 316L will not")
    print("      survive 800-1000 C pyrolysis - this needs Inconel or 310S -")
    print("      but the process is the same robot and the same arc. That is")
    print("      a research programme, not a product, and it is the most")
    print("      genuinely novel thing on this list.")

    space()

    print("\n" + "=" * 76)
    print("5. WHAT TO LEAD WITH")
    print("=" * 76)
    print("  Three horizons, and they reinforce each other rather than")
    print("  competing for the same effort.\n")
    print("  NOW - [A] the graded-depth acoustic liner. It is the only item")
    print("    here where the lattice does something honeycomb PHYSICALLY")
    print("    CANNOT, it lands in a market that already specifies fire")
    print("    performance, and it is testable in an afternoon.")
    print("    Article: 300 x 300 mm coupon, depth graded 20 -> 100 mm,")
    print("    perforated furan facing, impedance tube against an equal-mass")
    print("    honeycomb liner.")
    print("\n  NEXT - [vi] harsh environment on Earth. Same logic as space,")
    print("    available this year, and it pays for the space work. Ship rod")
    print("    and a robot instead of structure and moulds.")
    print("\n  THE REASON TO DO ANY OF IT - space. The 67:1 packing ratio is")
    print("    the strongest argument the whole process has, and it is the")
    print("    only one that cannot be answered by 'just use honeycomb'. You")
    print("    cannot launch a mould. You can launch rod.")
    print("\n  The through-line: every application above is somewhere you")
    print("  cannot bring tooling to the part. That is the actual product -")
    print("  not the lattice, and not the resin, but structure without")
    print("  tooling, in places tooling cannot go.")




def space():
    print("\n" + "=" * 76)
    print("4. SPACE AND HARSH-ENVIRONMENT CONSTRUCTION")
    print("=" * 76)

    print("\n  [i] THE LAUNCH ARGUMENT - feedstock packs 66:1")
    env_L = 353.0
    metal_L = 42.4 / 8.0
    print(f"      ARC-1 chassis: {env_L:.0f} L of deployed structure, but the")
    print(f"      rod it is made from is only {metal_L:.1f} L of solid metal.")
    print(f"      Packing ratio {env_L/metal_L:.0f}:1.")
    print("      You cannot ship a mould, a press or an autoclave. You CAN")
    print("      ship rod on a spool, and rod is the densest, dullest, most")
    print("      damage-tolerant cargo there is. This is the single strongest")
    print("      argument for the whole process, and it only exists in space.")

    print("\n  [ii] THE PROCESS GETS SIMPLER IN VACUUM, NOT HARDER")
    for s in [
        "Capacitor-discharge stud welding makes its own metal-vapour plasma",
        "  from the electrode. It does not need a shielding gas - and vacuum",
        "  is a better shield than argon. One of very few joining processes",
        "  that should work in vacuum essentially unmodified.",
        "No oxide, no porosity from entrained air, no gas logistics.",
        "On MARS it is easier still: 6 mbar of CO2 is a shielding gas.",
        "The lattice needs no support structure while building - it is",
        "  self-supporting from the first node, and in microgravity even the",
        "  16% of rods that needed a fixture on Earth do not.",
    ]:
        print(f"      * {s}")

    print("\n  [iii] LATTICE AS REGOLITH FORMWORK - shielding for free")
    print("      GCR shielding wants 20-100 g/cm2. You are not launching")
    print("      that. You launch the FORMWORK and fill it with what is")
    print("      already there:\n")
    print(f"      {'depth':>8} {'lattice kg/m2':>14} {'regolith g/cm2':>16} "
          f"{'shielding':>12}")
    for depth in (200, 350, 500, 800):
        lat = panel_areal_mass(80.0, float(depth))
        reg = 1.7 * (depth / 10.0)          # g/cm3 * cm  -> g/cm2
        if reg < 20:
            q = "thin"
        elif reg < 50:
            q = "useful"
        else:
            q = "GOOD"
        print(f"      {depth:>5} mm {lat:>14.1f} {reg:>16.0f} {q:>12}")
    print("\n      A 500 mm lattice shell filled with regolith is ~85 g/cm2 -")
    print("      solid GCR protection - and 98% of that mass never left the")
    print("      ground you are standing on. The lattice is formwork,")
    print("      reinforcement and micrometeoroid standoff in one.")
    print("      (It is NOT the pressure vessel. That is still a bladder.)")

    print("\n  [iv] WHAT FURAN SPECIFICALLY BRINGS UP THERE")
    for s in [
        "FLAMMABILITY. NASA-STD-6001 in an oxygen-enriched sealed volume is",
        "  unforgiving, and LOI 50 with very low smoke and toxicity is a",
        "  qualification advantage, not a marketing line. Crew habitat",
        "  interiors are where epoxy struggles hardest.",
        "It is a glassy-carbon precursor. Pyrolyse the skin and you get a",
        "  carbon surface that is atomic-oxygen resistant and radiation-",
        "  hard - which is exactly what LEO destroys polymers for.",
        "200 C service covers most non-propulsive thermal cases.",
    ]:
        print(f"      + {s}")

    print("\n  [v] AND WHAT WILL BITE - be honest about these")
    dT = 270.0
    for L_m in (2.0, 10.0):
        d = (16.0 - 3.5) * 1e-6 * L_m * 1000.0 * dT
        print(f"      - THERMAL CYCLING: LEO runs -150 to +120 C, ~16 cycles")
        print(f"        a day. Over {L_m:.0f} m that is {d:.1f} mm of CTE")
        print(f"        differential between a 316L lattice and a furan skin,")
        print(f"        every orbit. A lattice is compliant enough to take")
        print(f"        more than a monocoque, but the BOND is not.")
        break
    for s in [
        "ATOMIC OXYGEN eats polymers in LEO. The metal does not care; the",
        "  skin does. Coat it, or pyrolyse it to carbon, or fly it beyond LEO.",
        "OUTGASSING: furan cures by condensation and releases water. TML/CVCM",
        "  limits are tight near optics. Vacuum-bake and measure before you",
        "  promise anything.",
        "FATIGUE AGAIN: 16 thermal cycles a day is ~6,000 a year into every",
        "  one of those welded nodes. This is the same unquantified risk as",
        "  the chassis, with a worse duty cycle.",
        "LUNAR DUST is abrasive and electrostatic, and it will find the",
        "  robot's joints long before it troubles the structure.",
    ]:
        print(f"      - {s}")

    print("\n  [vi] HARSH ENVIRONMENT ON EARTH - the same argument, today")
    print("      Antarctic, offshore, mining, disaster response, remote")
    print("      defence. The logic is identical and you can test it this")
    print("      year: ship rod and a robot instead of structure and moulds,")
    print("      build on site, repair in place with the same machine.")
    print("      Furan's chemical resistance and fire rating are worth more")
    print("      on a rig or in a mine than they are in a wing.")


if __name__ == "__main__":
    main()
