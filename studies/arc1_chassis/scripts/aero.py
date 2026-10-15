"""ARC-1 :: aerodynamic package - wings, intakes, diffuser, airbox.

Generates real NACA 4-digit sections (inverted for downforce), lays out the
planforms, estimates the load each surface makes, and emits a generic list of
"loft jobs" and "plate jobs" that fusion_aero.py builds without needing to know
anything about aerodynamics.

The downforce numbers matter structurally, not just on paper: they come back
into the chassis FEA as an AERO load case at the wing mounts, because a rear
wing making 400 N on a 650 mm pylon is a real moment into the rear bulkhead.

Run:  python scripts/aero.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
P = json.loads((ROOT / "cad" /
                __import__("os").environ.get("ARC1_PARAMS",
                                             "params.json")).read_text())
OUT = ROOT / P["output"]["dir"]

RHO = 1.225          # kg/m3
V_REF = 150.0 / 3.6  # m/s design speed


# ------------------------------------------------------------------ foil
def naca4(m_pct, p_tenth, t_pct, n=50):
    """NACA 4-digit section, closed loop, unit chord, x 0->1.

    Deliberately BLUNT trailing edge (the -0.1015 coefficient, not -0.1036).
    A cusped TE closes to a zero-included-angle point, and Fusion's modeller
    refuses to loft through it - 'LOFT_NO_TOOLBODY'. Real race wings are
    blunt-tailed anyway: you cannot mould or machine a knife edge.
    """
    m, p, t = m_pct / 100.0, p_tenth / 10.0, t_pct / 100.0
    beta = np.linspace(0.0, math.pi, n)
    x = 0.5 * (1 - np.cos(beta))                 # cosine clustering at the LE
    yt = 5 * t * (0.2969 * np.sqrt(x) - 0.1260 * x - 0.3516 * x ** 2
                  + 0.2843 * x ** 3 - 0.1015 * x ** 4)
    yc = np.where(x < p,
                  m / max(p ** 2, 1e-9) * (2 * p * x - x ** 2),
                  m / max((1 - p) ** 2, 1e-9) * ((1 - 2 * p) + 2 * p * x - x ** 2))
    dyc = np.where(x < p,
                   2 * m / max(p ** 2, 1e-9) * (p - x),
                   2 * m / max((1 - p) ** 2, 1e-9) * (p - x))
    th = np.arctan(dyc)
    xu, yu = x - yt * np.sin(th), yc + yt * np.cos(th)
    xl, yl = x + yt * np.sin(th), yc - yt * np.cos(th)
    up = np.column_stack([xu, yu])                    # LE -> TE
    lo = np.column_stack([xl, yl])[::-1]              # TE -> LE
    # keep the lower TE point (blunt edge), drop the duplicated LE point
    return np.vstack([up, lo[:-1]])


def element(chord, aoa_deg, le_x, le_z, y, invert=True, foil=(6, 4, 12)):
    """One wing element section at spanwise station y, in world mm."""
    pts = naca4(*foil)
    if invert:
        pts = pts * np.array([1.0, -1.0])
    a = math.radians(-aoa_deg if invert else aoa_deg)
    ca, sa = math.cos(a), math.sin(a)
    rot = np.column_stack([pts[:, 0] * ca - pts[:, 1] * sa,
                           pts[:, 0] * sa + pts[:, 1] * ca])
    rot *= chord
    return [(le_x + px, y, le_z + pz) for px, pz in rot]


def wing(name, elements, span, stations=None, taper=1.0):
    """Multi-element wing -> loft jobs, one per element."""
    stations = stations or [-span, -span * 0.55, 0.0, span * 0.55, span]
    jobs = []
    for ei, e in enumerate(elements):
        secs = []
        for y in stations:
            f = 1.0 - (1.0 - taper) * abs(y) / span
            secs.append(element(e["chord"] * f, e["aoa"],
                                e["x"] + e["chord"] * (1 - f) * 0.5,
                                e["z"], y))
        jobs.append({"name": f"{name}_el{ei+1}", "sections": secs})
    return jobs


def rrect(cx, cy, w, h, r, n=8):
    """Rounded rectangle in a local 2D frame."""
    r = min(r, w / 2 * 0.95, h / 2 * 0.95)
    pts = []
    for (sx, sy, a0) in ((1, 1, 0.0), (-1, 1, math.pi / 2),
                         (-1, -1, math.pi), (1, -1, 3 * math.pi / 2)):
        ccx, ccy = cx + sx * (w / 2 - r), cy + sy * (h / 2 - r)
        for k in range(n + 1):
            a = a0 + (math.pi / 2) * k / n
            pts.append((ccx + r * math.cos(a), ccy + r * math.sin(a)))
    return pts


def downforce(area_m2, cl, v=V_REF):
    return 0.5 * RHO * v * v * area_m2 * cl


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    lofts, plates, loads = [], [], {}
    q = 0.5 * RHO * V_REF ** 2

    # ---------------- front wing -------------------------------------
    FW_SPAN = 700.0
    fw = wing("FRONT_WING", [
        {"chord": 250.0, "aoa": 3.0,  "x": 10.0,  "z": 105.0},
        {"chord": 150.0, "aoa": 22.0, "x": 232.0, "z": 150.0},
    ], FW_SPAN, taper=0.80)
    lofts += fw
    fw_area = (0.250 + 0.150) * 0.9 * (2 * FW_SPAN / 1000.0)
    loads["front_wing_N"] = downforce(fw_area, 2.05)

    for s in (1, -1):
        plates.append({"name": f"FW_ENDPLATE_{'R' if s>0 else 'L'}",
                       "plane": "xz", "offset": s * FW_SPAN,
                       "thick": 6.0, "sym": True,
                       "poly": rrect(180.0, 135.0, 420.0, 210.0, 45.0)})

    # ---------------- rear wing + pylons -----------------------------
    RW_SPAN = 500.0
    rw = wing("REAR_WING", [
        {"chord": 280.0, "aoa": 5.0,  "x": 2455.0, "z": 800.0},
        {"chord": 180.0, "aoa": 27.0, "x": 2700.0, "z": 862.0},
    ], RW_SPAN, taper=1.0)
    lofts += rw
    rw_area = (0.280 + 0.180) * (2 * RW_SPAN / 1000.0)
    loads["rear_wing_N"] = downforce(rw_area, 2.45)

    for s in (1, -1):
        plates.append({"name": f"RW_ENDPLATE_{'R' if s>0 else 'L'}",
                       "plane": "xz", "offset": s * RW_SPAN,
                       "thick": 8.0, "sym": True,
                       "poly": rrect(2600.0, 840.0, 520.0, 320.0, 50.0)})
    for s in (1, -1):
        plates.append({"name": f"RW_PYLON_{'R' if s>0 else 'L'}",
                       "plane": "xz", "offset": s * 190.0,
                       "thick": 14.0, "sym": True,
                       "poly": [(2430, 360), (2560, 360), (2585, 800),
                                (2505, 800), (2480, 430), (2430, 430)]})

    # ---------------- rear diffuser ----------------------------------
    # underfloor ramp: flat to x_start, then expanding to the exit
    dif = []
    for (x, z, hw) in ((2020.0,  42.0, 300.0), (2180.0,  58.0, 300.0),
                       (2350.0, 100.0, 295.0), (2520.0, 158.0, 288.0),
                       (2700.0, 215.0, 280.0)):
        sec = []
        for (u, v) in rrect(0.0, 0.0, 2 * hw, 26.0, 11.0, n=5):
            sec.append((x, u, z + v))
        dif.append(sec)
    lofts.append({"name": "DIFFUSER", "sections": dif})
    # diffuser strakes
    for s in (1, -1):
        for yy in (150.0, 275.0):
            plates.append({"name": f"DIF_STRAKE_{'R' if s>0 else 'L'}_{int(yy)}",
                           "plane": "xz", "offset": s * yy,
                           "thick": 5.0, "sym": True,
                           "poly": [(2100, 40), (2700, 200), (2700, 268),
                                    (2100, 108)]})
    dif_area = (2 * 0.29) * 0.68
    loads["diffuser_N"] = downforce(dif_area, 1.85)

    # ---------------- sidepod intakes / radiator ducts ----------------
    for s in (1, -1):
        secs = []
        for (x, yc, zc, w, h) in ((1430.0, 330.0, 205.0, 150.0, 210.0),
                                  (1560.0, 352.0, 210.0, 165.0, 225.0),
                                  (1760.0, 350.0, 215.0, 175.0, 230.0),
                                  (1960.0, 315.0, 220.0, 140.0, 190.0),
                                  (2080.0, 285.0, 225.0,  95.0, 140.0)):
            sec = [(x, s * (yc + u), zc + v)
                   for (u, v) in rrect(0.0, 0.0, w, h, 38.0, n=6)]
            secs.append(sec)
        lofts.append({"name": f"SIDEPOD_{'R' if s>0 else 'L'}",
                      "sections": secs})
    loads["sidepod_drag_N"] = 0.5 * RHO * V_REF ** 2 * (2 * 0.037) * 0.55

    # ---------------- engine airbox ----------------------------------
    box = []
    for (x, zc, w, h) in ((1655.0, 1035.0, 190.0, 150.0),
                          (1760.0, 1010.0, 230.0, 175.0),
                          (1900.0,  930.0, 265.0, 200.0),
                          (2010.0,  830.0, 250.0, 185.0)):
        box.append([(x, u, zc + v)
                    for (u, v) in rrect(0.0, 0.0, w, h, 55.0, n=6)])
    lofts.append({"name": "AIRBOX", "sections": box})

    # ---------------- totals ------------------------------------------
    total = loads["front_wing_N"] + loads["rear_wing_N"] + loads["diffuser_N"]
    veh = P["vehicle"]
    W = (veh["target_mass_kg"] + veh["driver_mass_kg"]) * 9.81
    bal = (loads["front_wing_N"] + 0.35 * loads["diffuser_N"]) / total

    print(f"AERO PACKAGE at {V_REF*3.6:.0f} km/h   (q = {q:.0f} Pa)")
    print(f"  front wing  2-element, {2*FW_SPAN:.0f} mm span : "
          f"{loads['front_wing_N']:>6,.0f} N")
    print(f"  rear wing   2-element, {2*RW_SPAN:.0f} mm span : "
          f"{loads['rear_wing_N']:>6,.0f} N")
    print(f"  diffuser    {2*280:.0f} mm wide, 680 mm long   : "
          f"{loads['diffuser_N']:>6,.0f} N")
    print(f"  TOTAL DOWNFORCE                        : {total:>6,.0f} N")
    print(f"  = {total/W:.2f} x vehicle weight, "
          f"aero balance {bal:.0%} front")
    for v_kmh in (60, 100, 150, 200):
        v = v_kmh / 3.6
        print(f"     {v_kmh:>3} km/h : {total*(v/V_REF)**2:>6,.0f} N "
              f"({total*(v/V_REF)**2/W:>4.2f} g)")

    res = {"lofts": lofts, "plates": plates, "loads": loads,
           "total_downforce_N": total, "v_ref_ms": V_REF,
           "balance_front": bal}
    (OUT / "aero.json").write_text(json.dumps(res))
    print(f"\n{len(lofts)} loft jobs, {len(plates)} plate jobs "
          f"-> {OUT/'aero.json'}")


if __name__ == "__main__":
    main()
