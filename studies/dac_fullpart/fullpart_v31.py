# v3.1: v3 re-search with a mesh/print fidelity constraint on minimum wall.
#
# Finding from build_v3_ss/finish_v3: at 1.5 mm base voxels with 3x supersampling
# (0.5 mm effective), walls below ~1.2 mm rasterise into fragmented islands:
# porosity collapses (0.46 -> 0.37) and measured dp rises ~13x. The low-dP corner
# of the design space is only real if the wall can actually be manufactured and
# meshed. t_min ~ ratio * cell (measured: wall_min_over_a ~ 0.99 * ratio).
# Constraint: t_min >= 1.2 mm. wall_ratio range widened to 0.12.
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from aces import evidence, search
from aces.domains.hx import tpms
from aces.model import Param

from studies.dac_fullpart.fullpart import FullPartReq
from studies.dac_fullpart.fullpart_v3 import HolisticFullPartV3, face_area_trade

T_MIN_FIDELITY = 1.2e-3  # m; walls thinner than this fragmented at 0.5 mm effective voxels


class HolisticFullPartV31(HolisticFullPartV3):
    version = "0.3.1-study-cfd-corrected-wallfidelity"

    def parameters(self, req):
        ps = []
        for p in super().parameters(req):
            if p.name == "wall_ratio":
                p = Param("wall_ratio", "float", 0.040, 0.12, units="t/a")
            ps.append(p)
        return ps

    def evaluate(self, p, req):
        ev = super().evaluate(p, req)
        mf = tpms.manufacturability("gyroid", p["wall_ratio"])
        t_min = mf.wall_min_over_a * p["cell_m"]
        ev.constraints["wall_fidelity"] = (T_MIN_FIDELITY - t_min) / T_MIN_FIDELITY
        ev.metrics["wall_min_m"] = t_min
        ev.warnings = list(ev.warnings) + [
            "wall_fidelity constraint: t_min >= 1.2 mm (sub-voxel wall fragmentation "
            "measured in build QA)"]
        return ev


def main():
    out = ROOT / "out" / "dac-fullpart-v31"
    out.mkdir(parents=True, exist_ok=True)
    model = HolisticFullPartV31()
    req = FullPartReq()
    t0 = time.time()
    res = search.run(model, req, trials=250, seed=0, population=32)
    print(f"search: {len(res.evaluations)} evaluations, {len(res.feasible)} feasible, "
          f"{len(res.pareto)} non-dominated, {time.time()-t0:.0f}s")
    with open(out / "evaluations.jsonl", "w") as fh:
        for e in res.evaluations:
            fh.write(json.dumps(e.as_dict(), default=float) + "\n")

    # selection: feasible on everything except the (impossible) 100 Pa air budget,
    # then lowest dp_air_total
    ok = [e for e in res.evaluations
          if all(v <= 0 for k, v in e.constraints.items() if k != "dp_air_total")]
    ok.sort(key=lambda e: e.metrics["dp_air_total_Pa"])
    if not ok:
        print("nothing feasible even excepting dp_air_total")
        return
    sel = ok[0]
    print(f"SELECTED (feasible except dp_air): dp_air={sel.metrics['dp_air_total_Pa']:.0f} Pa "
          f"core={sel.metrics['dp_core_air_Pa']:.0f} Pa fan={sel.objectives['fan_power_W']:.0f} W "
          f"mass={sel.objectives['mass_total_kg']:.0f} kg")
    print(json.dumps(sel.params, indent=1))
    print("wall_min", round(sel.metrics["wall_min_m"] * 1e3, 2), "mm; re_pore_air",
          round(sel.metrics["re_pore_air"]))
    trade = face_area_trade(sel.params["cell_m"], sel.params["wall_ratio"],
                            sel.params["core_len_m"])
    print("face-area trade (dP_core=100 Pa):", json.dumps(trade))
    pareto = sorted(res.pareto, key=lambda e: e.objectives["mass_total_kg"])
    with open(out / "pareto.json", "w") as fh:
        json.dump([e.as_dict() for e in pareto], fh, indent=2, default=float)
    with open(out / "selected-v31.json", "w") as fh:
        json.dump({"tag": "feasible-except-dp_air, min dp_air",
                   "params": sel.params, "objectives": sel.objectives,
                   "metrics": {k: v for k, v in sel.metrics.items() if k != "core"},
                   "warnings": sel.warnings, "uncalibrated": sel.uncalibrated,
                   "face_area_trade": trade}, fh, indent=2, default=float)
    rec = {"type": "design_run", "project": "dac-fullpart-holistic-v31-wallfidelity",
           "company": "CPA", "owner": "Aaron",
           "model": {"id": model.id, "version": model.version},
           "domain": "hx.two_fluid core + measured Forchheimer dP + wall-fidelity constraint",
           "uncalibrated": sel.uncalibrated,
           "status": "study tier; core dP grid-checked CFD law; NOT VERIFIED",
           "objectives": sel.objectives, "params": sel.params,
           "evidence_files": ["out/dac-fullpart-v31/pareto.json",
                              "out/dac-fullpart-v31/selected-v31.json"]}
    written = evidence.Ledger(ROOT / "out" / "evidence" / "ledger.jsonl").append(rec)
    print("ledger record", written["record_hash"][:12])


if __name__ == "__main__":
    main()
