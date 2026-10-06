"""ARC-1 :: furan resin as the skin for an arc-lattice airframe.

Furan is furfuryl-alcohol based: an acid-catalysed bio-based thermoset made
from furfural. Nothing to do with PFA fluoropolymer.

Feedstock for THIS product is the hemicellulose (C5 / xylan) fraction of
Beema bamboo, separated by formicobio fractionation and then dehydrated with
sulphuric acid to furfural. That route matters technically, not just
commercially: a sulphuric-acid dehydration step puts residual sulphate and
free acidity in play, and residual acidity is precisely what decides whether
this resin can touch aluminium. Acid number and residual sulphate belong on
the datasheet, measured - see ACID SPEC below.

The three things that decide it here:
  1. It cures cool enough to leave 6061 in T6 - unlike PFA.
  2. It is matrix-brittle, so it gives ground exactly where a wing skin
     works hardest: in shear.
  3. It cures with strong acid, and acid plus aluminium plus thousands of
     welded crevices is a corrosion problem, not a footnote.

Run:  python scripts/furan.py
"""
from __future__ import annotations

# system: E GPa, G(+-45) GPa, cure C, service C, CTE ue/C, ILSS MPa
SYS = {
    "Epoxy / glass +-45":  (22.0,  6.0, 177.0, 140.0, 14.0, 70.0),
    "Furan / glass +-45":  (21.0,  5.0, 165.0, 200.0, 15.0, 35.0),
    "Epoxy / carbon +-45": (60.0, 30.0, 177.0, 140.0,  3.0, 75.0),
    "Furan / carbon +-45": (58.0, 24.0, 165.0, 200.0,  3.5, 38.0),
    "PFA / glass (ref)":   (15.0,  2.5, 380.0, 260.0, 60.0, 12.0),
}

FIRE = {
    "Epoxy":  (22.0,  8.0, "burns, drips, heavy smoke"),
    "Furan":  (50.0, 55.0, "chars to a carbon insulator, very low smoke"),
    "PFA":    (95.0,  2.0, "will not burn, but evolves HF above 400 C"),
}


def main():
    print("=" * 76)
    print("FURAN RESIN - bio-based, fire-safe, and acidic")
    print("=" * 76)

    print(f"\n  {'system':<24} {'E GPa':>6} {'G GPa':>6} {'cure C':>7} "
          f"{'svc C':>6} {'ILSS':>6} {'6061 after cure':>17}")
    for n, (E, G, cure, svc, cte, ilss) in SYS.items():
        if cure >= 350:
            t = "O (annealed)"
        elif cure > 190:
            t = "over-aged"
        elif 155 <= cure <= 190:
            t = "T6 + AGES IT"
        else:
            t = "T6 kept, needs age"
        print(f"  {n:<24} {E:>6.1f} {G:>6.1f} {cure:>7.0f} {svc:>6.0f} "
              f"{ilss:>6.0f} {t:>17}")

    ge = SYS["Epoxy / carbon +-45"][1]
    gf = SYS["Furan / carbon +-45"][1]
    gge = SYS["Epoxy / glass +-45"][1]
    ggf = SYS["Furan / glass +-45"][1]
    print(f"\n  Shear stiffness: furan is {gf/ge:.0%} of epoxy on carbon and "
          f"{ggf/gge:.0%} on glass.")
    print(f"  Interlaminar shear strength: {SYS['Furan / carbon +-45'][5]:.0f} "
          f"vs {SYS['Epoxy / carbon +-45'][5]:.0f} MPa - about half.")
    print("  Fibre-dominated tension is much the same; it is the MATRIX-")
    print("  dominated numbers that give ground. A wing skin carries shear,")
    print("  so budget ~20% less torsional contribution than epoxy, and be")
    print("  careful at fittings and bearing points.")

    print("\n  --- fire behaviour, where furan is in a different class ---")
    print(f"  {'resin':<10} {'LOI %':>7} {'char % @800C':>13}  behaviour")
    for n, (loi, char, note) in FIRE.items():
        print(f"  {n:<10} {loi:>7.0f} {char:>13.0f}  {note}")
    print("  LOI above 21 means it will not sustain burning in air. Furan at")
    print("  ~50 is genuinely self-extinguishing, and the 55% char is a")
    print("  carbon insulator that forms exactly where the heat is.")

    print("\n" + "=" * 76)
    print("WHAT IT BUYS")
    print("=" * 76)
    for s in [
        "Service ~200 C vs ~140 C for a 177 C epoxy. A real +60 C, and that",
        "  is precisely the direction a turbojet pushes you.",
        "The char layer makes it a genuine ablative/fire barrier around a",
        "  tailpipe - something no epoxy will do - while still curing cool",
        "  enough to leave the aluminium in T6.",
        "It cures at 150-180 C, so the co-cure trick still works: age the",
        "  6061 lattice to T6 and cure the skin in the same oven cycle.",
        "Bio-based from bamboo hemicellulose - a genuine story for a",
        "  structural aerospace part, and the product itself.",
        "Excellent chemical and fuel resistance.",
    ]:
        print(f"  + {s}")

    print("\n" + "=" * 76)
    print("WHAT IT COSTS - and the one that actually matters here")
    print("=" * 76)
    print("  ** ACID CATALYST AGAINST ALUMINIUM **")
    for s in [
        "Furan cures with strong sulfonic acids (pTSA and relatives).",
        "Residual acidity pits aluminium. Furan FRP is conventionally kept",
        "  OFF aluminium and mild steel for exactly this reason.",
        "On a 6061 lattice this is worse than on a plate structure: every",
        "  one of ~6,000 welded nodes is a crevice, and crevices are where",
        "  acidic pitting starts and where you will never inspect it.",
        "Mitigations, in order: put furan only on the 316L/Ti hot-zone",
        "  lattice; or a glass veil plus neutral barrier coat between resin",
        "  and metal; or a neutralised / latent-catalyst system with a",
        "  post-cure wash. Do not just hope.",
    ]:
        print(f"    - {s}")
    print("\n  Other real costs:")
    for s in [
        "CONDENSATION CURE releases water. Without vacuum AND pressure and a",
        "  slow ramp you get 5-10% voids against <2% for epoxy - and voids",
        "  destroy the very shear strength the skin is there to provide.",
        "Brittle matrix: low ILSS and impact tolerance. Watch bearing and",
        "  bolted joints.",
        "Dark brown/black, short pot life, and a smaller supplier base than",
        "  epoxy prepreg.",
    ]:
        print(f"    - {s}")

    print("\n" + "=" * 76)
    print("VERDICT - zone it, the same way you zone the metal")
    print("=" * 76)
    print("  COOL WING, 6061 lattice   -> EPOXY.")
    print("     The acid/aluminium interaction is not worth a 20% shear")
    print("     penalty, and the 177 C epoxy co-cure already ages the")
    print("     lattice to T6 in the same cycle.")
    print("\n  HOT ZONE, 316L/Ti lattice -> FURAN, and it is a strong choice.")
    print("     Char barrier, 200 C service, very low smoke, and the acid")
    print("     problem largely goes away on stainless. Still cures cool")
    print("     enough not to fight the metal.")
    print("\n  This mirrors the metal zoning exactly: aluminium + epoxy where")
    print("  it is cool, stainless + furan where it is hot. One process,")
    print("  one robot, two rods, two resins.")
    print("\n" + "=" * 76)
    print("PRODUCT MODE - when the RESIN is the thing being sold")
    print("=" * 76)
    print("  Everything above assumes furan is a means to an end. If the")
    print("  furan prepreg IS the product, the answer inverts: you cannot")
    print("  confine it to a hot corner, and you cannot let a corrosion")
    print("  question sit unanswered on the datasheet.")
    print("\n  1. Build the demonstrator on a 316L lattice, not 6061. It")
    print("     removes the only blocking objection, and nobody can say the")
    print("     resin ate the structure.")
    print("  2. Make the skin structural and publish the delta. See")
    print("     scripts/demo_wing.py: 2.0x torsional stiffness from 0.16 m2")
    print("     of prepreg, within 7% of epoxy/carbon.")
    print("  3. ACID SPEC - the number that unlocks aluminium later:")
    for s in ["acid number, mg KOH/g of the mixed resin",
              "residual sulphate, ppm (the H2SO4 dehydration step)",
              "pH of an aqueous extract of the cured laminate",
              "and a filiform / salt-spray coupon on 6061 with and without",
              "  a barrier veil, run to a stated duration"]:
        print(f"       - {s}")
    print("     Publish those and aluminium becomes an engineering choice")
    print("     instead of a reason for a customer to walk away.")
    print("  4. Lead with fire. LOI 50 and 55% char on a LOADED structure is")
    print("     a demonstration no epoxy prepreg can answer - burn it under")
    print("     torsion and re-rig it afterwards.")


if __name__ == "__main__":
    main()
