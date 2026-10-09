# ACES study v3: holistic DAC full-part re-search with CFD-corrected core physics.
#
# What changed vs v2 (fullpart.py):
#   1. Air-side core dP uses the MEASURED Forchheimer law from periodic LBM on the
#      v2 winner cell (out/cfd/dacv2-gyroid-re{20,50,100}-n48, grid-checked at n=64:
#      dpdx 19.132 vs 19.166 Pa/m, 0.18%):  dpdx = A*u_s + B*u_s^2  (u_s = superficial)
#      A0=100.25 Pa s/m2, B0=1129.49 Pa s2/m3 at dh0=6.7094 mm, Air 293.15 K.
#      Scaling (Ergun-form assumption, stated): A ~ rho*nu/dh^2, B ~ rho/dh.
#      Porosity/shape variation across the search box treated as second order.
#   2. Water-side core dP: same measured law, B-network dh/phi, water properties.
#      ASSUMPTION: A/B measured on network A transfer to network B via dh scaling.
#   3. Plenum/port loss heuristic calibrated to the v2 plenum CFD (out/plenum/base):
#      heuristic 23.1 Pa vs CFD 191 Pa at the same point -> single-point multiplier 8.27.
#   4. Uniformity heuristic kept for RANKING ONLY; anchor note: heuristic 0.141 vs
#      CFD 0.885 at v2 (Forchheimer core resistance self-equalises the face flow).
#   5. Duty kept from the fast tier: measured Nu rises with Re (5.78/6.88/9.04 at
#      Re 19.5/49.8/99.7), so fast-tier laminar h UNDERSTATES air-side h at the
#      operating pore-Re (~3000) -> duty constraint here is conservative.
#
# Everything remains study tier, calibration factors 1.0 except the stated
# single-point anchors; NOT VERIFIED until simlab rig sign-off (audit M8/H3/H4).
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from aces import evidence, fluids, search
from aces.domains.hx import tpms
from aces.domains.hx.model import Evaluation

from studies.dac_fullpart.fullpart import (HX_REQ, CORE, AIR, WAT, W_FACE, H_FACE,
                                           TANK_H, RHO_AL, YIELD_AL, P_WATER,
                                           FAN_ETA, V_TANK_MIN, FullPartReq,
                                           HolisticFullPart, build_part)

# ---------------------------------------------------------- measured anchors (v2 cell, Air 293.15 K)
A0, B0 = 100.253, 1129.488          # dpdx = A0*u_s + B0*u_s^2, Pa/m (u_s superficial, m/s)
DH0 = 6.7094e-3                     # cold-network hydraulic diameter at the anchor, m
NU0 = 1.511e-5                      # air kinematic viscosity at the anchor, m2/s
RHO0 = 1.2046
RE_FIT_MAX = 100.0                  # highest verified CFD point
PLENUM_CAL = 191.0 / 23.0818        # CFD / heuristic plenum+port loss at the v2 point (1 anchor)
OP_U_S = None                       # filled per design


def forchheimer_dpdx(u_s: float, dh: float, st) -> float:
    """Measured v2-cell law scaled by Ergun-form geometry/property ratios."""
    A = A0 * (st.rho * (st.mu / st.rho)) / (RHO0 * NU0) * (DH0 / dh) ** 2
    B = B0 * (st.rho / RHO0) * (DH0 / dh)
    return A * u_s + B * u_s ** 2


class HolisticFullPartV3(HolisticFullPart):
    id = "hx.fullpart_dac"
    version = "0.3.0-study-cfd-corrected"

    def evaluate(self, p, req) -> Evaluation:
        a, ratio = p["cell_m"], p["wall_ratio"]
        Lc, Lp = p["core_len_m"], p["plenum_len_m"]
        hp, wg, tc = p["slot_h_m"], p["gallery_w_m"], p["casing_t_m"]
        Lo = 0.6 * Lp

        warnings = [
            "v3 study tier: air/water core dP from measured Forchheimer law (one cell, "
            "Air 293 K, Re<=100 verified, n=64 grid-checked); Ergun-form dh scaling assumed",
            "operating pore-Re ~3000 >> verified Re<=100: quadratic extrapolation; B assumed "
            "Re-independent (Forchheimer form); transitional correction possible",
            "water side assumes the network-A law transfers to network B via dh scaling",
            "plenum/port loss calibrated to ONE plenum-CFD anchor (x8.27 on Borda-Carnot)",
            "uniformity heuristic is ranking-only (CFD at v2: eta=0.885 vs heuristic 0.141)",
            "duty from fast tier = conservative (measured Nu(Re) rises above laminar law)",
            "not verified: simlab sign-off required",
        ]

        # ---- core fast tier (duty, masses, geometry) --------------------------
        ev = CORE.evaluate({"tpms": "gyroid", "cell_m": a, "wall_ratio": ratio,
                            "length_m": Lc, "width_m": W_FACE - 2 * wg, "height_m": H_FACE}, HX_REQ)
        cm = ev.metrics

        uc = tpms.unit_cell("gyroid", ratio)
        dh_c = uc.hydraulic_diameter_over_a(1) * a
        phi_c = uc.fluid_fraction[1]
        dh_h = uc.hydraulic_diameter_over_a(0) * a
        phi_h = uc.fluid_fraction[0]

        Af = (W_FACE - 2 * wg) * H_FACE
        # ---- air side: measured Forchheimer core + calibrated plenum ----------
        u_s_air = HX_REQ.cold.mass_flow_kg_s / (AIR.rho * Af)
        re_pore_air = (u_s_air / phi_c) * dh_c / (AIR.mu / AIR.rho)
        dp_core_air = forchheimer_dpdx(u_s_air, dh_c, AIR) * Lc

        Ap = (W_FACE - 2 * wg) * hp
        beta = Ap / Af
        v_port = HX_REQ.cold.mass_flow_kg_s / (AIR.rho * Ap)
        dp_in = 0.5 * AIR.rho * ((u_s_air) ** 2 * (1 - beta) ** 2 + 0.3 * v_port ** 2)
        dp_out = 0.5 * AIR.rho * 0.5 * (1 - beta) ** 2 * v_port ** 2
        dp_plenum = PLENUM_CAL * (dp_in + dp_out)
        dp_air_total = dp_core_air + dp_plenum

        # ---- water side: measured law, cross-flow path across Wc -------------
        Af_w = Lc * H_FACE                       # water enters through the gallery face
        u_s_wat = HX_REQ.hot.mass_flow_kg_s / (WAT.rho * Af_w)
        re_pore_wat = (u_s_wat / phi_h) * dh_h / (WAT.mu / WAT.rho)
        dp_core_wat = forchheimer_dpdx(u_s_wat, dh_h, WAT) * (W_FACE - 2 * wg)

        A_gal = max(wg - tc, 1e-3) * H_FACE
        v_gal = HX_REQ.hot.mass_flow_kg_s / (WAT.rho * A_gal)
        dp_gal = 0.05 * (Lc / max(2 * (wg - tc), 1e-3)) * 0.5 * WAT.rho * v_gal ** 2
        rp_w = 0.4 * wg
        v_wport = HX_REQ.hot.mass_flow_kg_s / (WAT.rho * math.pi * rp_w ** 2)
        dp_water_total = dp_core_wat + dp_gal

        # ---- uniformity ranking heuristic (unchanged; ranking only) ----------
        J = AIR.rho * v_port ** 2 / max(dp_core_air, 1e-6)
        eta_u = 1.0 / (1.0 + J)

        # ---- casing stress, masses (unchanged) --------------------------------
        sigma = P_WATER * wg / (2.0 * tc)
        stress_util = sigma / YIELD_AL
        L_tot = Lp + Lc + Lo
        shell_area = (2 * (W_FACE * H_FACE + W_FACE * L_tot + H_FACE * L_tot)
                      + 2 * (W_FACE * TANK_H + TANK_H * L_tot))
        m_shell = shell_area * tc * RHO_AL
        V_tank = (W_FACE - 2 * tc) * (L_tot - 2 * tc) * (TANK_H - 2 * tc) * 0.9
        m_water = WAT.rho * (V_tank + 2 * A_gal * Lc
                             + cm["hot"]["fluid_fraction"] * cm["core_volume_m3"])
        mass_total = cm["mass_kg"] + m_shell + m_water
        fan_power = dp_air_total * (HX_REQ.cold.mass_flow_kg_s / AIR.rho) / FAN_ETA

        constraints = {
            "duty": ev.constraints["duty"],
            "dp_air_total": dp_air_total / 100.0 - 1.0,
            "dp_water_total": dp_water_total / 30000.0 - 1.0,
            "min_wall": ev.constraints["min_wall"],
            "neck": ev.constraints["neck_cold"],
            "cells_across": (3 - min(Lc / a, (W_FACE - 2 * wg) / a, H_FACE / a)) / 3,
            "air_port_speed": v_port / 20.0 - 1.0,
            "water_port_speed": v_wport / 3.0 - 1.0,
            "stress": stress_util / 0.5 - 1.0,
            "tank": (V_TANK_MIN - V_tank) / V_TANK_MIN,
            "envelope": max(L_tot / 0.9, W_FACE / 1.2, (H_FACE + TANK_H) / 1.35) - 1.0,
            "slot_fits": (hp - (H_FACE - 2 * tc - 0.04)) / H_FACE,
        }
        metrics = {"core": cm, "dp_plenum_Pa": dp_plenum, "dp_core_air_Pa": dp_core_air,
                   "dp_air_total_Pa": dp_air_total, "dp_water_total_Pa": dp_water_total,
                   "uniformity_rank": eta_u, "fan_power_W": fan_power,
                   "re_pore_air": re_pore_air, "re_pore_water": re_pore_wat,
                   "u_s_air": u_s_air, "dh_cold_m": dh_c,
                   "mass_shell_kg": m_shell, "mass_water_kg": m_water,
                   "mass_total_kg": mass_total, "tank_volume_L": V_tank * 1e3,
                   "L_total_m": L_tot, "v_port_air": v_port, "v_gallery_water": v_gal,
                   "stress_gallery_MPa": sigma / 1e6}
        return Evaluation(params=dict(p),
                          objectives={"mass_total_kg": mass_total,
                                      "fan_power_W": fan_power,
                                      "maldistribution": 1.0 - eta_u},
                          constraints={k: (v if math.isfinite(v) else 1e9)
                                       for k, v in constraints.items()},
                          metrics=metrics, warnings=warnings + ev.warnings,
                          uncalibrated=list(ev.uncalibrated))


# ---------------------------------------------------------------- face-area trade study
def face_area_trade(a: float, ratio: float, Lc: float) -> list[dict]:
    """Required face area per panel to meet dP_core <= 100 Pa at 4.5 kg/s total,
    for 1..4 parallel panels (air split evenly). Study-level arithmetic."""
    uc = tpms.unit_cell("gyroid", ratio)
    dh = uc.hydraulic_diameter_over_a(1) * a
    rows = []
    for n in (1, 2, 3, 4):
        m = HX_REQ.cold.mass_flow_kg_s / n
        # solve A*u+B*u^2 = 100/Lc for u_s, then Af = m/(rho*u_s)
        A = A0 * (DH0 / dh) ** 2
        B = B0 * (DH0 / dh)
        u = (-A + math.sqrt(A * A + 4 * B * 100.0 / Lc)) / (2 * B)
        Af = m / (AIR.rho * u)
        side = math.sqrt(Af / (H_FACE / W_FACE))  # keep 1.14:1.20 aspect
        rows.append({"panels": n, "u_s": round(u, 3), "Af_per_panel_m2": round(Af, 2),
                     "face_W_m": round(side, 2), "face_H_m": round(side * H_FACE / W_FACE, 2)})
    return rows


def main():
    out = ROOT / "out" / "dac-fullpart-v3"
    out.mkdir(parents=True, exist_ok=True)
    model = HolisticFullPartV3()
    req = FullPartReq()
    t0 = time.time()
    res = search.run(model, req, trials=200, seed=0, population=32)
    print(f"search: {len(res.evaluations)} evaluations, {len(res.feasible)} feasible, "
          f"{len(res.pareto)} non-dominated, {time.time()-t0:.0f}s")

    with open(out / "evaluations.jsonl", "w") as fh:
        for e in res.evaluations:
            fh.write(json.dumps(e.as_dict(), default=float) + "\n")
    pareto = sorted(res.pareto, key=lambda e: e.objectives["mass_total_kg"])
    with open(out / "pareto.json", "w") as fh:
        json.dump([e.as_dict() for e in pareto], fh, indent=2, default=float)
    for i, e in enumerate(pareto[:8]):
        print(i, {k: round(v, 4) for k, v in e.params.items()},
              {k: round(v, 2) for k, v in e.objectives.items()},
              "dp_air", round(e.metrics["dp_air_total_Pa"], 1))

    # least-infeasible point if nothing is feasible
    if not res.feasible:
        best = min(res.evaluations, key=lambda e: max(
            max(0.0, v) for v in e.constraints.values()))
        print("NO FEASIBLE DESIGN. Least-violating point:")
        print(json.dumps(best.params, indent=1))
        print("dp_air_total", round(best.metrics["dp_air_total_Pa"], 1),
              "dp_core", round(best.metrics["dp_core_air_Pa"], 1),
              "re_pore_air", round(best.metrics["re_pore_air"], 0))
        sel, tag = best, "least-violating"
    else:
        cand = pareto[: max(3, len(pareto) // 3)]
        sel = min(cand, key=lambda e: e.objectives["maldistribution"])
        tag = "winner"
    print(tag.upper(), json.dumps(sel.params, indent=1))
    print(json.dumps({k: v for k, v in sel.metrics.items() if k != "core"}, indent=1, default=float))

    trade = face_area_trade(sel.params["cell_m"], sel.params["wall_ratio"],
                            sel.params["core_len_m"])
    print("face-area trade (dP_core=100 Pa):", json.dumps(trade))

    with open(out / "selected-v3.json", "w") as fh:
        json.dump({"tag": tag, "params": sel.params, "objectives": sel.objectives,
                   "metrics": {k: v for k, v in sel.metrics.items() if k != "core"},
                   "warnings": sel.warnings, "uncalibrated": sel.uncalibrated,
                   "face_area_trade": trade,
                   "anchors": {"A0": A0, "B0": B0, "dh0_m": DH0, "plenum_cal": PLENUM_CAL,
                               "grid_check": "dpdx n48/n64 = 19.132/19.166 Pa/m at Re 99.7"}},
                  fh, indent=2, default=float)
    rec = {"type": "design_run", "project": "dac-fullpart-holistic-v3-cfd-corrected",
           "company": "CPA", "owner": "Aaron",
           "model": {"id": model.id, "version": model.version},
           "domain": "hx.two_fluid core + measured Forchheimer dP + study manifold heuristics",
           "uncalibrated": sel.uncalibrated,
           "status": "study tier; core dP grid-checked CFD law; NOT VERIFIED",
           "objectives": sel.objectives, "params": sel.params,
           "evidence_files": ["out/dac-fullpart-v3/pareto.json",
                              "out/dac-fullpart-v3/selected-v3.json"]}
    written = evidence.Ledger(ROOT / "out" / "evidence" / "ledger.jsonl").append(rec)
    print("ledger record", written["record_hash"][:12])


if __name__ == "__main__":
    main()
