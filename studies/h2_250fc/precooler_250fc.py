# 250 kW FC truck H70-T40 precooler: fixed-cell geometry search with the
# MEASURED Forchheimer law (no fast-tier dP). Cell fixed at the CFD-measured /
# FEA-verified geometry: gyroid 3.115 mm, t/a 0.1646, LPBF 316L.
# Duty from the fast tier (conservative: measured Nu(Re) exceeds laminar law,
# report S9). Study tier, NOT VERIFIED; simlab sign-off required (M8/H3/H4).
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aces import evidence, fluids, search
from aces.domains.hx import tpms
from aces.domains.hx.model import HXRequirements, Material, Manufacturing, Stream, TwoFluidTPMS
from aces.model import Evaluation, Param

# ---- fixed measured cell -----------------------------------------------------
CELL_M, WALL_RATIO = 0.003114903330269901, 0.1646
A_H2, B_H2 = 2414.0, 6.847e5          # measured: dpdx = A u_s + B u_s^2 (Pa/m), network A
DH_H2 = 1.2197e-3                     # m, at this cell (both networks congruent)
NU_H2 = 2.551e-7                      # m2/s, H2 87.5 MPa 293 K
RHO_H2 = 45.96
RE_FIT_MAX = 118.1                    # highest measured point (time-averaged)

HX_REQ = HXRequirements(
    hot=Stream(fluid="Hydrogen", mass_flow_kg_s=0.070, inlet_temperature_K=293.15,
               inlet_pressure_Pa=87500000.0, max_pressure_drop_Pa=1.0e6),
    cold=Stream(fluid="INCOMP::MEG[0.6]", mass_flow_kg_s=1.68, inlet_temperature_K=228.15,
                inlet_pressure_Pa=400000.0, max_pressure_drop_Pa=80000.0),
    duty_W=61000.0,
    material=Material(name="316L LPBF as-built", thermal_conductivity_W_mK=14.6,
                      density_kg_m3=7950.0,
                      source="AK Steel 316/316L product data bulletin"),
    manufacturing=Manufacturing(process="lpbf", min_wall_m=0.0004, min_channel_m=0.0005,
                                envelope_length_m=0.6, envelope_width_m=0.35,
                                envelope_height_m=0.35),
    tpms_types=["gyroid"], calibration_file=None)
CORE = TwoFluidTPMS()
H2 = fluids.state("Hydrogen", 293.15, 87.5e6)
BRINE = fluids.state("INCOMP::MEG[0.6]", 228.15, 400000.0)


class Precooler250:
    id = "hx.h2_precooler_250fc"
    version = "0.1.0-study-measured-dp"
    requirements_model = None

    def parameters(self, req):
        return [
            Param("core_len_m", "float", 0.15, 0.55, units="m"),   # H2 flow direction
            Param("core_w_m", "float", 0.12, 0.32, units="m"),
            Param("core_h_m", "float", 0.12, 0.32, units="m"),
        ]

    def evaluate(self, p, req) -> Evaluation:
        L, W, H = p["core_len_m"], p["core_w_m"], p["core_h_m"]
        warnings = ["fixed cell 3.115mm t/a 0.1646; dP from measured Forchheimer law "
                    "(verified to Re 118; H2 operating Re extrapolated)",
                    "duty from fast tier = conservative (measured Nu(Re) above laminar)",
                    "brine-side dP: H2 law rescaled by rho-nu / rho (Ergun-form, same dh); "
                    "per-fluid sweep pending",
                    "manifold/header losses not modelled here (see fullpart v3.1 for the "
                    "fused-manifold pattern); not verified: simlab sign-off required"]

        ev = CORE.evaluate({"tpms": "gyroid", "cell_m": CELL_M, "wall_ratio": WALL_RATIO,
                            "length_m": L, "width_m": W, "height_m": H}, HX_REQ)
        cm = ev.metrics

        Af = W * H
        u_h2 = HX_REQ.hot.mass_flow_kg_s / (H2.rho * Af)
        re_h2 = (u_h2 / uc_phi()) * DH_H2 / NU_H2
        dp_h2 = (A_H2 * u_h2 + B_H2 * u_h2 ** 2) * L

        u_br = HX_REQ.cold.mass_flow_kg_s / (BRINE.rho * Af)
        A_br = A_H2 * (BRINE.mu) / (RHO_H2 * NU_H2)     # A ~ rho*nu = mu
        B_br = B_H2 * BRINE.rho / RHO_H2                # B ~ rho (same dh)
        dp_br = (A_br * u_br + B_br * u_br ** 2) * L
        re_br = (u_br / uc_phi()) * DH_H2 / (BRINE.mu / BRINE.rho)

        constraints = {
            "duty": ev.constraints["duty"],
            "dp_h2": dp_h2 / 1.0e6 - 1.0,
            "dp_brine": dp_br / 80000.0 - 1.0,
            "cells_across": (4 - min(L, W, H) / CELL_M) / 4,
            "envelope": max(L / 0.55, W / 0.32, H / 0.32) - 1.0,
        }
        metrics = {"core": {"duty_W": cm["duty_W"], "effectiveness": cm["effectiveness"],
                            "ntu": cm["ntu"], "UA_W_K": cm["UA_W_K"],
                            "h2_outlet_K": cm["hot_outlet_K"], "mass_core_kg": cm["mass_kg"]},
                   "dp_h2_Pa": dp_h2, "dp_brine_Pa": dp_br,
                   "re_h2": re_h2, "re_brine": re_br,
                   "u_s_h2": u_h2, "u_s_brine": u_br,
                   "h2_outlet_C": cm["hot_outlet_K"] - 273.15}
        return Evaluation(params=dict(p),
                          objectives={"mass_kg": cm["mass_kg"],
                                      "dp_sum": dp_h2 / 1e6 + dp_br / 80000.0},
                          constraints={k: (v if math.isfinite(v) else 1e9)
                                       for k, v in constraints.items()},
                          metrics=metrics, warnings=warnings + ev.warnings,
                          uncalibrated=list(ev.uncalibrated))


_uc = tpms.unit_cell("gyroid", WALL_RATIO)
def uc_phi():
    return _uc.fluid_fraction[0]


def main():
    out = ROOT / "out" / "h2-precooler-250fc"
    out.mkdir(parents=True, exist_ok=True)
    model = Precooler250()
    t0 = time.time()
    res = search.run(model, None, trials=150, seed=0, population=24)
    print(f"search: {len(res.evaluations)} evals, {len(res.feasible)} feasible, "
          f"{len(res.pareto)} non-dominated, {time.time()-t0:.0f}s")
    with open(out / "evaluations.jsonl", "w") as fh:
        for e in res.evaluations:
            fh.write(json.dumps(e.as_dict(), default=float) + "\n")
    pareto = sorted(res.pareto, key=lambda e: e.objectives["mass_kg"])
    with open(out / "pareto.json", "w") as fh:
        json.dump([e.as_dict() for e in pareto], fh, indent=2, default=float)
    for i, e in enumerate(pareto[:8]):
        m = e.metrics
        print(i, {k: round(v, 3) for k, v in e.params.items()},
              f"mass={e.objectives['mass_kg']:.1f}kg Q={m['core']['duty_W']/1e3:.1f}kW "
              f"eff={m['core']['effectiveness']:.3f} Tout={m['h2_outlet_C']:.1f}C "
              f"dpH2={m['dp_h2_Pa']:.0f}Pa dpBr={m['dp_brine_Pa']/1e3:.1f}kPa "
              f"ReH2={m['re_h2']:.0f}")
    if pareto:
        win = pareto[0]
        with open(out / "winner-250fc.json", "w") as fh:
            json.dump({"params": win.params, "objectives": win.objectives,
                       "metrics": win.metrics, "warnings": win.warnings,
                       "uncalibrated": win.uncalibrated}, fh, indent=2, default=float)
        rec = {"type": "design_run", "project": "h2-precooler-250fc", "company": "CPA",
               "owner": "Aaron",
               "model": {"id": model.id, "version": model.version},
               "domain": "hx.two_fluid duty (fast tier) + measured Forchheimer dP",
               "uncalibrated": win.uncalibrated,
               "status": "study tier; measured-law dP; NOT VERIFIED",
               "objectives": win.objectives, "params": win.params,
               "evidence_files": ["out/h2-precooler-250fc/pareto.json",
                                  "out/h2-precooler-250fc/winner-250fc.json"]}
        w = evidence.Ledger(ROOT / "out" / "evidence" / "ledger.jsonl").append(rec)
        print("ledger record", w["record_hash"][:12])


if __name__ == "__main__":
    main()
