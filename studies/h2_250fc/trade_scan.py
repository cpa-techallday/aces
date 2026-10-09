# Trade scan for the 250 kW FC H2 precooler after the brine-viscosity finding
# (mu_MEG60 @ 228 K = 0.245 Pa s, 8x the initial assumption -> in-envelope
# infeasible at 1.68 kg/s brine with an 80 kPa brine dP budget).
# Scan axes (each relaxation documented, owner to confirm):
#   m_brine : 0.6..1.68 kg/s   (T40 5 K cold-end pinch forced 1.68; duty is
#                               Cmin-limited by H2 so brine flow can fall)
#   dp_brine budget : 80/150/300 kPa (station skids realistically allow a few
#                               hundred kPa on the chiller loop)
#   duty / H2 outlet : 61.0 kW -> 233.15 K (T40), 55.9 kW -> 238.15 K,
#                               50.8 kW -> 243.15 K (T30-class fallback)
# Same model class as precooler_250fc.py: fixed CFD-measured/FEA-verified cell,
# measured Forchheimer dP law, fast-tier duty (conservative).
# Study tier, NOT VERIFIED; simlab sign-off required (M8/H3/H4).
from __future__ import annotations

import itertools
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aces import fluids, search
from aces.domains.hx import tpms
from aces.domains.hx.model import HXRequirements, Material, Manufacturing, Stream, TwoFluidTPMS
from aces.model import Evaluation, Param
import math

CELL_M, WALL_RATIO = 0.003114903330269901, 0.1646
A_H2, B_H2 = 2414.0, 6.847e5
DH_H2 = 1.2197e-3
NU_H2 = 2.551e-7
RHO_H2 = 45.96

CORE = TwoFluidTPMS()
H2 = fluids.state("Hydrogen", 293.15, 87.5e6)
BRINE = fluids.state("INCOMP::MEG[0.6]", 228.15, 400000.0)
_UC = tpms.unit_cell("gyroid", WALL_RATIO)
PHI = _UC.fluid_fraction[0]

M_BRINE = [0.6, 0.8, 1.0, 1.2, 1.4, 1.68]
DP_BUDGET = [80e3, 150e3, 300e3]
DUTY = [61000.0, 55900.0, 50800.0]          # 233.15 / 238.15 / 243.15 K outlet
DUTY_LABEL = {61000.0: "T40 233.15K", 55900.0: "238.15K", 50800.0: "243.15K"}


def make_model(m_br, dp_budget, duty):
    req = HXRequirements(
        hot=Stream(fluid="Hydrogen", mass_flow_kg_s=0.070, inlet_temperature_K=293.15,
                   inlet_pressure_Pa=87500000.0, max_pressure_drop_Pa=1.0e6),
        cold=Stream(fluid="INCOMP::MEG[0.6]", mass_flow_kg_s=m_br,
                    inlet_temperature_K=228.15, inlet_pressure_Pa=400000.0,
                    max_pressure_drop_Pa=dp_budget),
        duty_W=duty,
        material=Material(name="316L LPBF as-built", thermal_conductivity_W_mK=14.6,
                          density_kg_m3=7950.0,
                          source="AK Steel 316/316L product data bulletin"),
        manufacturing=Manufacturing(process="lpbf", min_wall_m=0.0004,
                                    min_channel_m=0.0005, envelope_length_m=0.6,
                                    envelope_width_m=0.35, envelope_height_m=0.35),
        tpms_types=["gyroid"], calibration_file=None)

    class M:
        id = "hx.h2_precooler_250fc"
        version = "0.1.0-study-measured-dp-tradescan"
        requirements_model = None

        def parameters(self, _req):
            return [Param("core_len_m", "float", 0.15, 0.55, units="m"),
                    Param("core_w_m", "float", 0.12, 0.32, units="m"),
                    Param("core_h_m", "float", 0.12, 0.32, units="m")]

        def evaluate(self, p, _req):
            L, W, H = p["core_len_m"], p["core_w_m"], p["core_h_m"]
            ev = CORE.evaluate({"tpms": "gyroid", "cell_m": CELL_M,
                                "wall_ratio": WALL_RATIO, "length_m": L,
                                "width_m": W, "height_m": H}, req)
            cm = ev.metrics
            Af = W * H
            u_h2 = req.hot.mass_flow_kg_s / (H2.rho * Af)
            dp_h2 = (A_H2 * u_h2 + B_H2 * u_h2 ** 2) * L
            re_h2 = (u_h2 / PHI) * DH_H2 / NU_H2
            u_br = m_br / (BRINE.rho * Af)
            A_br = A_H2 * BRINE.mu / (RHO_H2 * NU_H2)
            B_br = B_H2 * BRINE.rho / RHO_H2
            dp_br = (A_br * u_br + B_br * u_br ** 2) * L
            re_br = (u_br / PHI) * DH_H2 / (BRINE.mu / BRINE.rho)
            constraints = {
                "duty": ev.constraints["duty"],
                "dp_h2": dp_h2 / 1.0e6 - 1.0,
                "dp_brine": dp_br / dp_budget - 1.0,
                "cells_across": (4 - min(L, W, H) / CELL_M) / 4,
                "envelope": max(L / 0.55, W / 0.32, H / 0.32) - 1.0,
            }
            metrics = {"duty_W": cm["duty_W"], "effectiveness": cm["effectiveness"],
                       "ntu": cm["ntu"], "UA_W_K": cm["UA_W_K"],
                       "h2_outlet_K": cm["hot_outlet_K"], "mass_kg": cm["mass_kg"],
                       "dp_h2_Pa": dp_h2, "dp_brine_Pa": dp_br,
                       "re_h2": re_h2, "re_brine": re_br}
            return Evaluation(params=dict(p),
                              objectives={"mass_kg": cm["mass_kg"],
                                          "dp_sum": dp_h2 / 1e6 + dp_br / dp_budget},
                              constraints={k: (v if math.isfinite(v) else 1e9)
                                           for k, v in constraints.items()},
                              metrics=metrics, warnings=list(ev.warnings),
                              uncalibrated=list(ev.uncalibrated))
    return M()


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "a"
    out = ROOT / "out" / "h2-precooler-250fc"
    out.mkdir(parents=True, exist_ok=True)
    combos = list(itertools.product(M_BRINE, DP_BUDGET, DUTY))
    half = (len(combos) + 1) // 2
    chunk = combos[:half] if tag == "a" else combos[half:]
    rows = []
    t0 = time.time()
    for m_br, dp_b, duty in chunk:
        res = search.run(make_model(m_br, dp_b, duty), None, trials=150, seed=0,
                         population=24)
        feas = sorted(res.feasible, key=lambda e: e.objectives["mass_kg"])
        row = {"m_brine": m_br, "dp_budget_kPa": dp_b / 1e3, "duty_W": duty,
               "outlet": DUTY_LABEL[duty], "n_feasible": len(feas)}
        if feas:
            w = feas[0]
            row.update({"params": w.params,
                        "metrics": {k: round(v, 4) for k, v in w.metrics.items()}})
        rows.append(row)
        print(f"mbr={m_br:<5} dp={dp_b/1e3:>5.0f}kPa duty={duty/1e3:>4.1f}kW "
              f"feas={len(feas):>3} " +
              (f"mass={feas[0].objectives['mass_kg']:.1f}kg "
               f"Tout={feas[0].metrics['h2_outlet_K']-273.15:.1f}C "
               f"dpBr={feas[0].metrics['dp_brine_Pa']/1e3:.0f}kPa" if feas else ""),
              flush=True)
    path = out / f"trade_scan_{tag}.json"
    with open(path, "w") as fh:
        json.dump(rows, fh, indent=2, default=float)
    print(f"wrote {path} ({time.time()-t0:.0f}s, {len(chunk)} scenarios)")


if __name__ == "__main__":
    main()
