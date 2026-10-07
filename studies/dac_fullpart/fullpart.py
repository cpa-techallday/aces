# ACES study: holistic full-part generative design of the DAC contactor panel.
#
# One generative system, built on ACES libraries only:
#   - core performance: aces.domains.hx TwoFluidTPMS (fast tier, uncalibrated)
#   - geometry: aces.domains.hx.tpms gyroid level set assembled on a voxel field
#     (full-solid box with disjoint carved voids; network-selective seal slabs:
#     air faces open network A only, water gallery walls open network B only)
#   - search: aces.search NSGA-II (Optuna)
#   - evidence: aces.evidence ledger append
#
# Manifold / casing / tank layer is a STUDY heuristic, not an ACES verified tier:
# Borda-Carnot plenum losses, thin-wall shell stress, distributor-momentum
# uniformity index. Everything here stays "not verified" until simlab signs off.
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aces import evidence, fluids, search
from aces.domains.hx import tpms
from aces.domains.hx.model import HXRequirements, Material, Manufacturing, Stream, TwoFluidTPMS
from aces.model import Evaluation, Param

# ---------------------------------------------------------------- fixed requirement (DAC panel rev 2)
W_FACE, H_FACE = 1.14, 1.20          # module face, m (fixed by module layout)
TANK_H = 0.08                        # buffer-tank annex height, m
RHO_AL = 2670.0                      # AlSi10Mg LPBF (EOS datasheet, as-built)
YIELD_AL = 220e6                     # conservative as-built yield, Pa
P_WATER = 300e3                      # utility-loop pressure, Pa
FAN_ETA = 0.5
V_TANK_MIN = 0.005                   # 5 L service buffer

HX_REQ = HXRequirements(
    hot=Stream(fluid="Water", mass_flow_kg_s=0.07, inlet_temperature_K=368.15,
               inlet_pressure_Pa=300000.0, max_pressure_drop_Pa=30000.0),
    cold=Stream(fluid="Air", mass_flow_kg_s=4.5, inlet_temperature_K=293.15,
                inlet_pressure_Pa=101325.0, max_pressure_drop_Pa=100.0),
    duty_W=2500.0,
    material=Material(name="AlSi10Mg LPBF as-built", thermal_conductivity_W_mK=130.0,
                      density_kg_m3=2670.0,
                      source="EOS Aluminium AlSi10Mg datasheet (as-built): k~130 W/mK, rho~2670 kg/m3"),
    manufacturing=Manufacturing(process="lpbf", min_wall_m=0.0003, min_channel_m=0.0015,
                                envelope_length_m=0.9, envelope_width_m=1.2,
                                envelope_height_m=1.35),
    tpms_types=["gyroid"],
    calibration_file=None,
)
CORE = TwoFluidTPMS()
AIR = fluids.state("Air", 293.15, 101325.0)
WAT = fluids.state("Water", 360.0, 300000.0)


class FullPartReq(BaseModel):
    note: str = "holistic DAC full-part study"


# ---------------------------------------------------------------- holistic model
class HolisticFullPart:
    """DesignModel: TPMS core + casing + box plenums + side galleries + buffer tank."""

    id = "hx.fullpart_dac"
    version = "0.1.0-study"
    requirements_model = FullPartReq

    def parameters(self, req):
        return [
            Param("cell_m", "float", 0.012, 0.020, log=True, units="m"),
            Param("wall_ratio", "float", 0.040, 0.080, units="t/a"),
            Param("core_len_m", "float", 0.05, 0.12, units="m"),
            Param("plenum_len_m", "float", 0.12, 0.30, units="m"),
            Param("slot_h_m", "float", 0.15, 0.45, units="m"),        # full-width air slot ports
            Param("gallery_w_m", "float", 0.02, 0.12, units="m"),     # water gallery depth
            Param("casing_t_m", "float", 0.002, 0.006, units="m"),
        ]

    def evaluate(self, p, req) -> Evaluation:
        a, ratio = p["cell_m"], p["wall_ratio"]
        Lc, Lp = p["core_len_m"], p["plenum_len_m"]
        hp, wg, tc = p["slot_h_m"], p["gallery_w_m"], p["casing_t_m"]
        Lo = 0.6 * Lp

        warnings = ["holistic study tier: manifold/casing/tank models are textbook-loss heuristics",
                    "water side crosses the core (cross-flow); counterflow e-NTU kept (Cr~0.065, small error)",
                    "uniformity is a distributor-momentum ranking heuristic, not a flow solve",
                    "manifold seal concept: network-selective one-cell slabs; leak-tightness to be "
                    "confirmed by rig flow test",
                    "air ports are full-width slots; uniformity is a Pareto objective, not a constraint",
                    "not verified: fast tier + study heuristics; simlab sign-off required"]

        # ---- core (ACES fast tier, uncalibrated factors 1.0)
        ev = CORE.evaluate({"tpms": "gyroid", "cell_m": a, "wall_ratio": ratio,
                            "length_m": Lc, "width_m": W_FACE - 2 * wg, "height_m": H_FACE}, HX_REQ)
        cm = ev.metrics

        # ---- air plenums: full-width slot port -> face; Borda-Carnot + bend losses
        Af = (W_FACE - 2 * wg) * H_FACE
        Ap = (W_FACE - 2 * wg) * hp
        beta = Ap / Af
        v_face = HX_REQ.cold.mass_flow_kg_s / (AIR.rho * Af)
        v_port = HX_REQ.cold.mass_flow_kg_s / (AIR.rho * Ap)
        dp_in = 0.5 * AIR.rho * (v_face ** 2 * (1 - beta) ** 2 + 0.3 * v_port ** 2)
        dp_out = 0.5 * AIR.rho * 0.5 * (1 - beta) ** 2 * v_port ** 2
        dp_plenum = dp_in + dp_out
        dp_air_total = cm["cold"]["pressure_drop_Pa"] + dp_plenum

        # ---- uniformity: distributor momentum ratio (ranking heuristic)
        J = AIR.rho * v_port ** 2 / max(cm["cold"]["pressure_drop_Pa"], 1e-6)
        eta_u = 1.0 / (1.0 + J)

        # ---- water galleries + ports
        A_gal = max(wg - tc, 1e-3) * H_FACE
        v_gal = HX_REQ.hot.mass_flow_kg_s / (WAT.rho * A_gal)
        L_gal = Lc
        dp_gal = 0.05 * (L_gal / max(2 * (wg - tc), 1e-3)) * 0.5 * WAT.rho * v_gal ** 2
        rp_w = 0.4 * wg
        v_wport = HX_REQ.hot.mass_flow_kg_s / (WAT.rho * math.pi * rp_w ** 2)
        dp_water_total = cm["hot"]["pressure_drop_Pa"] + dp_gal

        # ---- casing stress: water gallery pressure on span wg, wall tc (thin-wall)
        sigma = P_WATER * wg / (2.0 * tc)
        stress_util = sigma / YIELD_AL

        # ---- masses
        L_tot = Lp + Lc + Lo
        shell_area = (2 * (W_FACE * H_FACE + W_FACE * L_tot + H_FACE * L_tot)
                      + 2 * (W_FACE * TANK_H + TANK_H * L_tot))
        m_shell = shell_area * tc * RHO_AL
        V_tank = (W_FACE - 2 * tc) * (L_tot - 2 * tc) * (TANK_H - 2 * tc) * 0.9
        m_water = WAT.rho * (V_tank + 2 * A_gal * L_gal
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
        metrics = {"core": cm, "dp_plenum_Pa": dp_plenum, "dp_air_total_Pa": dp_air_total,
                   "dp_water_total_Pa": dp_water_total, "uniformity": eta_u,
                   "fan_power_W": fan_power, "mass_shell_kg": m_shell,
                   "mass_water_kg": m_water, "mass_total_kg": mass_total,
                   "tank_volume_L": V_tank * 1e3, "L_total_m": L_tot,
                   "v_port_air": v_port, "v_gallery_water": v_gal,
                   "stress_gallery_MPa": sigma / 1e6}
        return Evaluation(params=dict(p),
                          objectives={"mass_total_kg": mass_total,
                                      "fan_power_W": fan_power,
                                      "maldistribution": 1.0 - eta_u},
                          constraints={k: (v if math.isfinite(v) else 1e9)
                                       for k, v in constraints.items()},
                          metrics=metrics, warnings=warnings + ev.warnings,
                          uncalibrated=list(ev.uncalibrated))


# ---------------------------------------------------------------- voxel full-part generator
def build_part(p: dict, voxel_m: float = 0.003):
    """Voxel-assemble the part. Full-solid box; disjoint voids carved with >= tc
    walls; gyroid core with network-selective seal slabs. Returns verts, faces, info."""
    from skimage.measure import marching_cubes

    a, ratio = p["cell_m"], p["wall_ratio"]
    Lc, Lp = p["core_len_m"], p["plenum_len_m"]
    hp, wg, tc = p["slot_h_m"], p["gallery_w_m"], p["casing_t_m"]
    Lo = 0.6 * Lp
    nx = max(3, int(round(Lc / a)))
    ny = max(3, int(round((W_FACE - 2 * wg) / a)))
    nz = max(3, int(round(H_FACE / a)))
    Lc = nx * a
    Wc = ny * a                      # active core width
    Hc = nz * a
    yc0 = (W_FACE - Wc) / 2          # centre core in y
    zc0 = (H_FACE - Hc) / 2          # centre core in z
    L_tot = Lp + Lc + Lo
    h = voxel_m
    m_ = tc + 2 * h                  # outer margin beyond internal cavities
    x0, x1 = -m_, L_tot + m_
    y0, y1 = -m_, W_FACE + m_
    z0, z1 = -TANK_H - m_, H_FACE + m_
    xs = np.arange(x0, x1 + h, h); ys = np.arange(y0, y1 + h, h); zs = np.arange(z0, z1 + h, h)
    X, Y, Z = np.meshgrid(xs, ys, zs, indexing="ij")
    print(f"voxel grid {X.shape} = {X.size/1e6:.0f} M voxels ({X.size*4/1e9:.2f} GB f32)")

    def box(xa, xb, ya, yb, za, zb):
        return np.minimum(np.minimum(X - xa, xb - X),
                          np.minimum(np.minimum(Y - ya, yb - Y), np.minimum(Z - za, zb - Z)))

    # full-solid outer box, chamfered edges (voxel-faceted aesthetic)
    cham = 0.03
    d_out = (np.maximum(x0 - X, 0) + np.maximum(X - x1, 0)
             + np.maximum(y0 - Y, 0) + np.maximum(Y - y1, 0)
             + np.maximum(z0 - Z, 0) + np.maximum(Z - z1, 0))
    solid = np.minimum(box(x0, x1, y0, y1, z0, z1), cham - d_out)

    # ---- gyroid core with selective seals ----------------------------------
    Xg, Yg, Zg = (X - Lp) / a, (Y - yc0) / a, (Z - zc0) / a
    d = tpms.signed_distance("gyroid", Xg, Yg, Zg)
    half = 0.5 * ratio
    inC = ((X >= Lp) & (X <= Lp + Lc) & (Y >= yc0) & (Y <= yc0 + Wc)
           & (Z >= zc0) & (Z <= zc0 + Hc))
    sheet = np.where(inC, half - np.abs(d), -1.0)
    slab = a
    fA = ((X < Lp + slab) | (X > Lp + Lc - slab)) & inC
    sealA = np.where(fA, half - d, -1.0)                    # air faces: open A (d > half) only
    fB = ((Y < yc0 + slab) | (Y > yc0 + Wc - slab)) & inC
    sealB = np.where(fB, d + half, -1.0)                    # gallery walls: open B (d < -half) only
    fZ = ((Z < zc0 + slab) | (Z > zc0 + Hc - slab)) & inC
    sealZ = np.where(fZ, 1.0, -1.0)                         # top/bottom faces: fully closed
    solid = np.maximum(solid, np.maximum(sheet, np.maximum(sealA, np.maximum(sealB, sealZ))))

    # ---- interior voids (disjoint, >= tc walls everywhere) ------------------
    voids = [
        box(x0 + tc, Lp, yc0 + tc, yc0 + Wc - tc, zc0 + tc, zc0 + Hc - tc),        # air plenum in
        box(Lp + Lc, x1 - tc, yc0 + tc, yc0 + Wc - tc, zc0 + tc, zc0 + Hc - tc),   # air plenum out
        box(Lp + tc, Lp + Lc - tc, y0 + tc, yc0 - tc, zc0 + tc, zc0 + Hc - tc),    # water gallery -
        box(Lp + tc, Lp + Lc - tc, yc0 + Wc + tc, y1 - tc, zc0 + tc, zc0 + Hc - tc),  # gallery +
        # buffer tank in two lobes -> the uncarved strip between them is the baffle
        box(x0 + tc, L_tot / 2 - tc / 2, y0 + tc, y1 - tc, z0 + tc, -tc),
        box(L_tot / 2 + tc / 2, x1 - tc, y0 + tc, y1 - tc, z0 + tc, -tc),
    ]
    ymid, zmid = W_FACE / 2, zc0 + Hc / 2
    voids += [
        box(x0 - h, x0 + tc, yc0 + tc, yc0 + Wc - tc, zmid - hp / 2, zmid + hp / 2),   # air slot in
        box(x1 - tc, x1 + h, yc0 + tc, yc0 + Wc - tc, zmid - hp / 2, zmid + hp / 2),   # air slot out
    ]
    V = voids[0]
    for v in voids[1:]:
        V = np.maximum(V, v)
    # water ports: cylinders along y, from the outer y-face into each gallery
    rp_w = 0.4 * wg
    for xp, sgn in ((Lp + 0.25 * Lc, -1.0), (Lp + 0.75 * Lc, +1.0)):
        cyl = rp_w ** 2 - ((X - xp) ** 2 + (Z - zmid) ** 2)
        if sgn < 0:   # y-minus side: pierce shell y in [y0-h, y0+tc], gallery at yc0 side
            reach = np.minimum(Y - (y0 - h), (yc0 - tc) - Y)
        else:         # y-plus side
            reach = np.minimum((y1 + h) - Y, Y - (yc0 + Wc + tc))
        V = np.maximum(V, np.minimum(cyl, reach))
    # tank fill + drain necks: cylinders along y through the y-minus shell into the tank
    for xp, rr in ((L_tot / 4, 0.03), (3 * L_tot / 4, 0.03)):
        cyl = rr ** 2 - ((X - xp) ** 2 + (Z - (-TANK_H / 2)) ** 2)
        reach = np.minimum(Y - (y0 - h), (y0 + tc + 0.01) - Y)
        V = np.maximum(V, np.minimum(cyl, reach))
    solid = np.minimum(solid, -V)

    # ---- port flanges (rectangular rings around the air slots), feet --------
    for xe in (x0, x1):
        boss = box(xe - 0.02, xe + tc, yc0 + tc - 0.02, yc0 + Wc - tc + 0.02,
                   zmid - hp / 2 - 0.02, zmid + hp / 2 + 0.02)
        hole = box(xe - 0.03, xe + tc + h, yc0 + tc, yc0 + Wc - tc, zmid - hp / 2, zmid + hp / 2)
        solid = np.maximum(solid, np.minimum(boss, -hole))
    for fx in (0.05, L_tot - 0.05):
        for fy in (0.05, W_FACE - 0.05):
            solid = np.maximum(solid, box(fx - 0.03, fx + 0.03, fy - 0.03, fy + 0.03, z0 - 0.02, z0))

    solid = np.clip(solid / h, -1.0, 1.0).astype(np.float32)
    padded = np.pad(solid, 1, mode="constant", constant_values=-1.0e9)
    verts, faces, _, _ = marching_cubes(padded, level=0.0, spacing=(h, h, h))
    verts -= h
    info = dict(grid=list(X.shape), voxel_m=h, core_cells=[nx, ny, nz],
                core_active=[Lc, Wc, Hc], L_total=L_tot)
    return verts, faces, info


# ---------------------------------------------------------------- run
def main():
    out = ROOT / "out" / "dac-fullpart"
    out.mkdir(parents=True, exist_ok=True)
    model = HolisticFullPart()
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
              {k: round(v, 2) for k, v in e.objectives.items()})

    if not pareto:
        print("no feasible design; see evaluations.jsonl")
        return
    cand = pareto[: max(3, len(pareto) // 3)]
    win = min(cand, key=lambda e: e.objectives["maldistribution"])
    print("WINNER", json.dumps(win.params, indent=1))
    print(json.dumps({k: v for k, v in win.metrics.items() if k != "core"}, indent=1, default=float))
    verts, faces, info = build_part(win.params)
    from aces.domains.hx.tpms import mesh_volume, write_stl
    vol = mesh_volume(verts, faces)
    print(f"mesh: {len(faces)} triangles, enclosed {vol:.4f} m3")
    if vol < 0:
        faces = faces[:, ::-1]
    write_stl(out / "dac-fullpart-winner.stl", verts, faces,
              header="ACES holistic DAC full part (study, not verified)", scale=1000.0)
    with open(out / "winner.json", "w") as fh:
        json.dump({"params": win.params, "objectives": win.objectives,
                   "metrics": {k: v for k, v in win.metrics.items() if k != "core"},
                   "warnings": win.warnings, "uncalibrated": win.uncalibrated,
                   "mesh": info}, fh, indent=2, default=float)
    rec = {"type": "design_run", "project": "dac-fullpart-holistic", "company": "CPA",
           "owner": "Aaron",
           "model": {"id": model.id, "version": model.version},
           "domain": "hx.two_fluid core + study manifold/casing/tank heuristics",
           "uncalibrated": win.uncalibrated,
           "status": "fast-tier ranking + study heuristics; NOT VERIFIED",
           "objectives": win.objectives, "params": win.params,
           "evidence_files": ["out/dac-fullpart/pareto.json", "out/dac-fullpart/winner.json",
                              "out/dac-fullpart/dac-fullpart-winner.stl"]}
    written = evidence.Ledger(ROOT / "out" / "evidence" / "ledger.jsonl").append(rec)
    print("ledger record", written["record_hash"][:12])


if __name__ == "__main__":
    main()
