"""ARC-1 :: full vehicle mass and cost budget, carbon-wrapped EV.

Costs are indicative AUD, ex-GST, for a one-off built in-house with an owned
robot cell. Treat +/-30% as the honest band, and get real quotes for the three
line items that dominate: motor, inverter, cells.

Run:  python scripts/budget.py            # 15 kWh track pack
      python scripts/budget.py --kwh 8    # sprint pack
"""
from __future__ import annotations

import argparse

# --- battery cell, the unit everything scales from ------------------
CELL = {"name": "Molicel P45B 21700", "Wh": 16.2, "g": 70.0, "aud": 10.0}
PACK_CELL_FRACTION = 0.65      # cells as a fraction of finished pack mass


def pack(kwh):
    n = int(round(kwh * 1000.0 / CELL["Wh"]))
    cell_kg = n * CELL["g"] / 1000.0
    return {
        "cells": n,
        "cell_kg": cell_kg,
        "mass_kg": cell_kg / PACK_CELL_FRACTION,
        "cell_aud": n * CELL["aud"],
    }


def budget(kwh):
    pk = pack(kwh)

    MASS = {
        "Structure": [
            ("Arc-lattice chassis (rods + node balls)", 42.4),
            ("318 machined hub nodes @16 mm", 5.0),
            ("Spherical bearings + housings, 16 pickups", 1.9),
            ("Module joint flanges + bolts, 3 joints", 2.4),
            ("Carbon wrap - structural skin 2.89 m2", 4.9),
            ("Carbon wrap - remaining shell 2.04 m2", 3.5),
        ],
        "Aero": [
            ("Front + rear wings, endplates, pylons", 6.0),
            ("Diffuser + strakes", 3.0),
            ("Sidepod ducts + airbox", 4.0),
        ],
        "Suspension": [
            ("Rockers x4 (generative, 7075)", 0.9),
            ("Wishbones + pushrods, 4 corners", 6.0),
            ("Uprights x4", 10.0),
            ("Hubs, bearings, driveshaft joints", 8.0),
            ("Dampers + springs x4", 4.8),
            ("Anti-roll bars f/r", 2.0),
        ],
        "Wheels + brakes": [
            ("Wheels 13 in x4", 14.0),
            ("Tyres x4", 22.0),
            ("Discs, calipers, pedal box, lines", 14.0),
        ],
        "Powertrain": [
            ("Emrax 268 motor", 20.3),
            ("Inverter", 8.0),
            ("Reduction gearbox + LSD", 12.0),
            ("Driveshafts", 4.0),
            ("Cooling: rad, pump, lines, coolant", 8.0),
        ],
        "Energy": [
            (f"HV pack {kwh:g} kWh ({pk['cells']} cells)", pk["mass_kg"]),
        ],
        "Systems": [
            ("VCU, loom, sensors, dash, LV batt, DC-DC", 10.0),
            ("Seat, belts, padding, fire system", 6.0),
            ("Fasteners, plumbing, misc", 4.0),
        ],
    }

    COST = {
        "Chassis": [
            ("316L TIG rod 2.4 mm, 34.4 kg @ $30/kg", 1030),
            ("316L MIG wire for node balls, 8 kg", 200),
            ("318 machined hub nodes @ $25 batch", 7950),
            ("Shielding gas, 47 h build", 500),
            ("Fixtures + build platform", 2000),
            ("Welding consumables", 500),
            ("Robot cell time 47 h (owned asset, notional $60/h)", 2820),
        ],
        "Carbon": [
            ("Twill 200 gsm, ~26 m2 cloth", 1150),
            ("Epoxy resin + hardener, 15 kg", 450),
            ("Film adhesive / bonding paste", 300),
            ("Bagging consumables", 400),
            ("Aero plugs + moulds (DIY, 7 parts)", 6000),
            ("Aero carbon + resin", 2500),
        ],
        "Suspension": [
            ("Uprights x4, machined billet", 1600),
            ("Wishbones + pushrods", 1200),
            ("Rod ends / spherical bearings x40", 1400),
            ("Rockers x4, machined 7075", 1000),
            ("Dampers x4", 4800),
            ("Springs + ARBs", 800),
            ("Hubs, bearings, CVs", 2000),
        ],
        "Wheels + brakes": [
            ("Wheels x4", 2400),
            ("Slicks, 1 set", 1400),
            ("Brakes: discs, calipers, pedal box, lines", 3500),
        ],
        "Powertrain": [
            ("Emrax 268 MV", 15000),
            ("Inverter (Cascadia / Bamocar class)", 7500),
            ("Reduction gearbox + LSD", 6000),
            ("Driveshafts + CVs", 1500),
            ("Cooling system", 1200),
        ],
        "Energy": [
            (f"{pk['cells']} x {CELL['name']} @ ${CELL['aud']:.0f}",
             pk["cell_aud"]),
            ("BMS (Orion / Ewert class)", 3000),
            ("Busbars, nickel, laser welding, holders", 1400),
            ("Contactors, fuses, HV cable, connectors", 2500),
            ("Fire-rated enclosure", 1500),
            ("Cooling plates + TIM", 800),
            ("IMD + HV interlock", 1200),
        ],
        "Systems": [
            ("VCU, dash, loom, sensors", 4500),
            ("Seat, belts, fire system", 2000),
            ("Fasteners, plumbing, misc", 1500),
        ],
    }
    return MASS, COST, pk


def show(title, data, unit, fmt):
    print(f"\n{'='*72}\n{title}\n{'='*72}")
    grand = 0.0
    subs = []
    for cat, items in data.items():
        sub = sum(v for _, v in items)
        grand += sub
        subs.append((cat, sub))
        print(f"\n  {cat}")
        for name, v in items:
            print(f"    {name:<52} {v:>{fmt}}")
        print(f"    {'-'*52} {'-'*8}")
        print(f"    {'subtotal':<52} {sub:>{fmt}}")
    print(f"\n{'-'*72}")
    for cat, sub in sorted(subs, key=lambda kv: -kv[1]):
        bar = "#" * int(38 * sub / grand)
        print(f"  {cat:<18} {sub:>{fmt}}  {sub/grand:>5.1%}  {bar}")
    print(f"{'-'*72}\n  {'TOTAL':<18} {grand:>{fmt}} {unit}")
    return grand


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kwh", type=float, default=15.0)
    a = ap.parse_args()

    MASS, COST, pk = budget(a.kwh)
    m = show(f"MASS BUDGET - carbon-wrapped EV, {a.kwh:g} kWh", MASS,
             "kg", "8.1f")
    c = show(f"COST BUDGET - AUD ex-GST, one-off, {a.kwh:g} kWh", COST,
             "AUD", "8,.0f")

    print(f"\n{'='*72}\nSUMMARY\n{'='*72}")
    print(f"  Dry mass, no driver          {m:>8.0f} kg")
    print(f"  With 80 kg driver            {m+80:>8.0f} kg")
    print(f"  Pack {a.kwh:g} kWh = {pk['cells']} cells  "
          f"{pk['mass_kg']:.0f} kg  "
          f"({a.kwh*1000/pk['mass_kg']:.0f} Wh/kg at pack level)")
    print(f"  Build cost                   {c:>8,.0f} AUD")
    print(f"  Torsional stiffness          {3944:>8,} Nm/deg (carbon-wrapped)")
    struct = sum(v for _, v in MASS["Structure"])
    print(f"  Structure only               {struct:>8.1f} kg  "
          f"-> {3944/struct:.0f} Nm/deg per kg")
    print(f"  Power to weight (200 kW)     "
          f"{200.0/((m+80)/1000.0):>8.0f} kW/tonne")
    print(f"\n  Confidence: +/-30%. Get real quotes for motor, inverter and "
          f"cells -\n  they are {(15000+7500+pk['cell_aud'])/c:.0%} of the "
          f"build cost between them.")


if __name__ == "__main__":
    main()
