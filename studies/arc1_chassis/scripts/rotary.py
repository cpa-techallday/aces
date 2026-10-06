"""ARC-1 :: 13B rotary vs the electric powertrain.

Three questions that actually decide it:
  1. Does it FIT the measured engine bay? (packing.py gives the real void)
  2. What does the whole propulsion system weigh, energy included?
  3. What does a rotary do to a chassis with 5,603 fatigue-critical welds,
     a bonded epoxy skin and polyurethane foam in it?

Engine figures are typical published values for a built unit and carry real
uncertainty - weigh and measure the actual engine before trusting them.

Run:  python scripts/rotary.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P = json.loads((ROOT / "cad" /
                __import__("os").environ.get("ARC1_PARAMS",
                                             "params.json")).read_text())
OUT = ROOT / P["output"]["dir"]

# --- engines: L x W x H mm (bare core), dressed mass kg, peak kW ---------
ENGINES = {
    "13B-REW twin turbo": dict(lwh=(380, 400, 450), bare=135, dressed=165,
                               kW=206, note="stock FD; 250-300 kW when built"),
    "13B-MSP Renesis NA": dict(lwh=(370, 390, 440), bare=120, dressed=145,
                               kW=175, note="RX-8, NA, lighter, thirstier"),
    "13B 6-port NA":      dict(lwh=(370, 390, 430), bare=115, dressed=140,
                               kW=120, note="simplest, cheapest"),
}

# --- what hangs off each architecture -----------------------------------
ROTARY_SYSTEM = [
    ("Gearbox + clutch + flywheel", 40.0),
    ("Cooling: radiator, 2x oil cooler, plumbing, coolant", 20.0),
    ("Exhaust incl. heat shielding", 14.0),
    ("Intake, intercooler, piping (turbo)", 14.0),
    ("ECU, ignition, injectors, loom", 8.0),
    ("Engine mounts / rear subframe", 8.0),
    ("Fuel tank, pumps, lines (dry)", 8.0),
]
EV_SYSTEM = [
    ("Emrax 268 motor", 20.3),
    ("Inverter", 8.0),
    ("Reduction gearbox + LSD", 12.0),
    ("Driveshafts", 4.0),
    ("Cooling: rad, pump, lines, coolant", 8.0),
]

PETROL_KG_PER_L = 0.745
PETROL_KWH_PER_L = 9.5          # lower heating value
ROTARY_EFF = 0.24               # brake thermal efficiency, rotary, on song
EV_EFF = 0.90                   # battery -> wheel
PACK_KG_PER_KWH = 100.0 / 15.0  # from budget.py: 15 kWh = 100 kg


def fit_check(lwh, box):
    """Any axis permutation that fits the measured clear box."""
    a, b, c = lwh
    perms = [(a, b, c), (a, c, b), (b, a, c), (b, c, a), (c, a, b), (c, b, a)]
    return any(p[0] <= box[0] and p[1] <= box[1] and p[2] <= box[2]
               for p in perms)


def main():
    pack = json.loads((OUT / "packaging.json").read_text())
    bay = pack["engine / battery"]["box_mm"]
    print(f"measured engine-bay clear box: "
          f"{bay[0]:.0f} x {bay[1]:.0f} x {bay[2]:.0f} mm "
          f"({pack['engine / battery']['free_L']} L free)\n")

    print("=" * 72)
    print("1. DOES IT FIT?")
    print("=" * 72)
    for name, e in ENGINES.items():
        l, w, h = e["lwh"]
        ok = fit_check(e["lwh"], bay)
        short = max(0, min(l, w, h) - min(bay))
        print(f"  [{'FITS' if ok else ' NO '}] {name:<22} "
          f"{l}x{w}x{h} mm core   {e['dressed']} kg   {e['kW']} kW")
    need = max(ENGINES["13B-REW twin turbo"]["lwh"])
    print(f"\n  The bay is {bay[2]:.0f} mm tall. A 13B core is ~450 mm tall")
    print(f"  before manifolds. NOTHING in the 13B family fits as drawn.")
    print(f"  Bay must grow ~{450-bay[2]:.0f} mm in height and ~"
          f"{max(0,400-bay[1]):.0f} mm in width; with manifolds and the oil")
    print(f"  pan, budget a 550 x 500 x 550 mm bay.")

    print("\n" + "=" * 72)
    print("2. SYSTEM MASS, ENERGY INCLUDED")
    print("=" * 72)
    rot_fixed = ENGINES["13B-REW twin turbo"]["dressed"] + \
        sum(v for _, v in ROTARY_SYSTEM)
    ev_fixed = sum(v for _, v in EV_SYSTEM)
    print(f"  rotary fixed mass  {rot_fixed:>6.1f} kg "
          f"(engine {ENGINES['13B-REW twin turbo']['dressed']} + "
          f"{sum(v for _, v in ROTARY_SYSTEM):.0f} systems)")
    print(f"  EV fixed mass      {ev_fixed:>6.1f} kg")
    print(f"  delta              {rot_fixed-ev_fixed:>+6.1f} kg before any "
          f"energy is carried\n")

    print(f"  {'useful kWh':>11} {'petrol L':>9} {'rotary kg':>10} "
          f"{'pack kg':>8} {'EV kg':>8} {'lighter':>9}")
    for useful in (5, 10, 15, 20, 30, 40, 60):
        litres = useful / (PETROL_KWH_PER_L * ROTARY_EFF)
        rot = rot_fixed + litres * PETROL_KG_PER_L
        ev = ev_fixed + (useful / EV_EFF) * PACK_KG_PER_KWH
        print(f"  {useful:>11.0f} {litres:>9.1f} {rot:>10.1f} "
              f"{(useful/EV_EFF)*PACK_KG_PER_KWH:>8.1f} {ev:>8.1f} "
              f"{'rotary' if rot < ev else 'EV':>9}")

    # crossover
    a = PACK_KG_PER_KWH / EV_EFF
    b = PETROL_KG_PER_L / (PETROL_KWH_PER_L * ROTARY_EFF)
    cross = (rot_fixed - ev_fixed) / (a - b)
    print(f"\n  CROSSOVER at {cross:.0f} kWh useful energy.")
    print(f"  Below that the EV is lighter; above it the rotary is.")
    print(f"  A 20 min sprint at ~80 kW average needs ~27 kWh - right on it.")

    print("\n" + "=" * 72)
    print("3. WHAT IT DOES TO *THIS* CHASSIS")
    print("=" * 72)
    print("  GOOD - and specific to a welded lattice:")
    print("    * A rotary has no reciprocating mass. Primary and secondary")
    print("      shake are essentially nil; a 4-cylinder's 2nd-order vertical")
    print("      shake is the classic killer of welded tube frames. With 5,603")
    print("      fatigue-critical nodes and NO fatigue data, the smoothest")
    print("      available prime mover is worth real money here.")
    print("    * 316L does not care about heat. A carbon monocoque next to a")
    print("      rotary exhaust needs serious shielding; the lattice does not.")
    print("      This is one of the few places the metal structure genuinely")
    print("      beats carbon.")
    print("    * Compact and low - good CG, and it suits a mid-mounted bay.")
    print("\n  BAD - and it lands on the parts we just added:")
    print("    * Rotary EGT is ~900-1000 C. Epoxy Tg is ~120 C and PU foam")
    print("      degrades from ~110 C. The bonded skin (5b) and the foam fill")
    print("      (5c) must be kept out of the exhaust bay entirely, or get a")
    print("      shield plus an air gap. That is a hard packaging constraint,")
    print("      not a detail.")
    print("    * Heat rejection to coolant AND oil is high - two oil coolers")
    print("      is normal. That is frontal area, which costs you the aero.")
    print("    * Apex seals are a consumable at sustained track power.")
    print("    * Thirst: at 24% BTE you carry more fuel for the same work.")

    print("\n" + "=" * 72)
    print("4. VERDICT")
    print("=" * 72)
    print(f"  Power density: 13B-REW {206/165:.2f} kW/kg dressed vs")
    print(f"                 Emrax 268 {200/20.3:.2f} kW/kg - the motor is")
    print(f"                 {(200/20.3)/(206/165):.0f}x better, but you must "
          f"carry the battery.")
    print("  For a SPRINT car (<25 kWh): the EV is lighter and simpler, and")
    print("    it keeps the foam and the bonded skin viable everywhere.")
    print("  For ENDURANCE or a long track day (>30 kWh): the rotary wins on")
    print("    mass, and its smoothness is a genuine gift to a welded lattice.")
    print("  Either way the rear of the chassis must be redesigned - the bay")
    print("    as drawn is 216 mm tall and a 13B needs ~450.")


if __name__ == "__main__":
    main()
