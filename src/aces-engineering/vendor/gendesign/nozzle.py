"""Physics-true nozzle geometry: Angelino aerospike (plug) contour -> STEP.

Given the ratio of specific heats and the area (expansion) ratio, the ideal external-
expansion plug contour follows from the Prandtl-Meyer function and the isentropic area
relation (Angelino, AIAA J. 2(10), 1964): each Mach line from the cowl lip reaches the
plug where the annular stream-tube area equals A* . A/A*(M). The contour is optionally
truncated (real aerospikes keep 20-40 % of the ideal length and carry a base).

Outputs: contour.csv (M, x, r, theta, mu), nozzle.json (Me, nu_e, throat gap, lengths,
throat-area check, ideal frozen-flow performance when chamber data is given) and, when
build123d is importable, aerospike.step (revolved solid with an upstream stub).

    python -m gendesign.nozzle --gamma 1.22 --eps 25 --re 40 --trunc 0.3 --out out/aerospike
    python -m gendesign.nozzle --ox LOX --fuel RP-1 --mr 2.6 --pc 60 --eps 25 --re 40   # needs rocketcea
"""
from __future__ import annotations

import argparse
import csv
import math
import os

import numpy as np

from .export import save_json

R_UNIV = 8314.462618  # J / (kmol K)
G0 = 9.80665
P_SL = 101325.0


# ----------------------------------------------------------------------------- gas dynamics

def prandtl_meyer(M: float, g: float) -> float:
    a = math.sqrt((g + 1.0) / (g - 1.0))
    b = math.sqrt(max(M * M - 1.0, 0.0))
    return a * math.atan(b / a) - math.atan(b)


def area_ratio(M: float, g: float) -> float:
    return (1.0 / M) * ((2.0 / (g + 1.0)) * (1.0 + 0.5 * (g - 1.0) * M * M)) ** ((g + 1.0) / (2.0 * (g - 1.0)))


def mach_from_area_ratio(eps: float, g: float, supersonic: bool = True) -> float:
    lo, hi = (1.0, 60.0) if supersonic else (1e-6, 1.0)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if (area_ratio(mid, g) < eps) == supersonic:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def ideal_performance(gamma: float, eps: float, Me: float, pc_bar: float, Tc: float, MW: float, At_mm2: float) -> dict:
    """Frozen-flow ideal performance of the full-expansion nozzle (no losses)."""
    g = gamma
    R = R_UNIV / MW
    Pc = pc_bar * 1e5
    Pe = Pc * (1.0 + 0.5 * (g - 1.0) * Me * Me) ** (-g / (g - 1.0))
    Ve = math.sqrt(2.0 * g / (g - 1.0) * R * Tc * (1.0 - (Pe / Pc) ** ((g - 1.0) / g)))
    At = At_mm2 * 1e-6
    Ae = eps * At
    mdot = Pc * At * math.sqrt(g / (R * Tc)) * (2.0 / (g + 1.0)) ** ((g + 1.0) / (2.0 * (g - 1.0)))
    F_vac = mdot * Ve + Pe * Ae
    F_sl = mdot * Ve + (Pe - P_SL) * Ae
    cstar = Pc * At / mdot
    return {"Pe_Pa": Pe, "Ve_m_s": Ve, "mdot_kg_s": mdot, "F_vac_N": F_vac, "F_sl_N": F_sl,
            "Isp_vac_s": F_vac / (mdot * G0), "Isp_sl_s": F_sl / (mdot * G0), "cstar_m_s": cstar}


def cea_properties(ox: str, fuel: str, pc_bar: float, mr: float, eps: float) -> dict:
    """Chamber gamma / Tc / MW from NASA CEA through RocketCEA (optional dependency)."""
    try:
        from rocketcea.cea_obj_w_units import CEA_Obj
    except ImportError as exc:
        raise ImportError("rocketcea is not installed (needs a Fortran toolchain; use the rig container "
                          "or pass --gamma/--tc/--mw explicitly)") from exc
    c = CEA_Obj(oxName=ox, fuelName=fuel, pressure_units="bar", temperature_units="K", isp_units="sec")
    Isp, Cstar, Tc, MW, gam = c.get_IvacCstrTc_ChmMwGam(Pc=pc_bar, MR=mr, eps=eps)
    return {"gamma": float(gam), "Tc": float(Tc), "MW": float(MW), "Isp_vac_cea_s": float(Isp), "cstar_cea_m_s": float(Cstar)}


# ----------------------------------------------------------------------------- contour

def angelino_contour(gamma: float, eps: float, Re: float, n: int = 200) -> dict:
    """Ideal full-length plug contour for exit (cowl lip) radius Re in mm."""
    Me = mach_from_area_ratio(eps, gamma)
    nu_e = prandtl_meyer(Me, gamma)
    At = math.pi * Re * Re / eps
    rows = []
    for M in np.linspace(1.0, Me, int(n)):
        nu = prandtl_meyer(float(M), gamma)
        mu = math.asin(1.0 / float(M))
        theta = nu_e - nu
        e = area_ratio(float(M), gamma)
        s = math.sin(theta + mu)
        arg = max(1.0 - e * At * s / (math.pi * Re * Re * math.sin(mu)), 0.0)
        l = Re * (1.0 - math.sqrt(arg)) / s
        rows.append((float(M), l * math.cos(theta + mu), Re - l * s, theta, mu, l))
    M1, x_t, r_t, th_t, mu_t, l_t = rows[0]
    throat_area = math.pi * (Re + r_t) * l_t  # annulus swept by the throat line (sin mu = 1)
    return {"gamma": gamma, "eps": eps, "Re_mm": Re, "Me": Me, "nu_e_deg": math.degrees(nu_e),
            "At_mm2": At, "throat_gap_mm": l_t, "throat_x_mm": x_t, "throat_r_mm": r_t,
            "throat_area_check": throat_area / At, "L_full_mm": rows[-1][1], "rows": rows}


def truncate(contour: dict, fraction: float) -> dict:
    rows = contour["rows"]
    L = contour["L_full_mm"] * float(fraction)
    kept = [r for r in rows if r[1] <= L + 1e-12]
    if len(kept) < len(rows) and len(kept) >= 1:
        a, b = kept[-1], rows[len(kept)]
        t = (L - a[1]) / (b[1] - a[1]) if b[1] != a[1] else 0.0
        kept.append(tuple(a[i] + t * (b[i] - a[i]) for i in range(6)))
    out = dict(contour)
    out["rows"] = kept
    out["truncation"] = float(fraction)
    out["L_trunc_mm"] = kept[-1][1]
    out["base_r_mm"] = kept[-1][2]
    return out


def write_contour(contour: dict, out_dir: str, upstream_mm: float = 20.0, make_step: bool = True) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    rows = contour["rows"]
    with open(os.path.join(out_dir, "contour.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["M", "x_mm", "r_mm", "theta_deg", "mu_deg", "l_mm"])
        for M, x, r, th, mu, l in rows:
            w.writerow([f"{M:.6f}", f"{x:.6f}", f"{r:.6f}", f"{math.degrees(th):.6f}", f"{math.degrees(mu):.6f}", f"{l:.6f}"])
    meta = {k: v for k, v in contour.items() if k != "rows"}
    meta["n_points"] = len(rows)
    meta["outputs"] = {"contour": "contour.csv"}
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        xs = [r[1] for r in rows]
        rs = [r[2] for r in rows]
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(xs, rs, "k-", lw=2, label="plug contour")
        ax.plot([0, contour["throat_x_mm"]], [contour["Re_mm"], contour["throat_r_mm"]], "r-", lw=2, label="throat line")
        ax.plot(0, contour["Re_mm"], "ro", label="cowl lip")
        ax.axhline(0, color="gray", lw=0.8, ls="--")
        ax.set_aspect("equal")
        ax.set_xlabel("x [mm]")
        ax.set_ylabel("r [mm]")
        ax.set_title(f"Angelino aerospike  gamma={contour['gamma']}  eps={contour['eps']}  Me={contour['Me']:.3f}")
        ax.legend()
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "contour.png"), dpi=110)
        plt.close(fig)
        meta["outputs"]["png"] = "contour.png"
    except Exception as exc:
        meta["outputs"]["png_error"] = str(exc)
    if make_step:
        try:
            meta["outputs"]["step"] = contour_to_step(contour, os.path.join(out_dir, "aerospike.step"), upstream_mm)
        except Exception as exc:
            meta["outputs"]["step_error"] = str(exc)
    save_json(os.path.join(out_dir, "nozzle.json"), meta)
    return meta


def contour_to_step(contour: dict, path: str, upstream_mm: float = 20.0) -> str:
    """Revolve the plug profile (plus an upstream mounting stub) into a solid and write STEP."""
    from build123d import Axis, BuildLine, BuildPart, BuildSketch, Plane, Polyline, export_step, make_face, revolve

    rows = contour["rows"]
    x_t, r_t = rows[0][1], rows[0][2]
    prof = [(x_t - upstream_mm, 0.0), (x_t - upstream_mm, r_t)] + [(r[1], r[2]) for r in rows]
    if prof[-1][1] > 1e-9:
        prof.append((prof[-1][0], 0.0))
    # drop consecutive duplicates
    clean = [prof[0]]
    for p in prof[1:]:
        if abs(p[0] - clean[-1][0]) > 1e-9 or abs(p[1] - clean[-1][1]) > 1e-9:
            clean.append(p)
    with BuildPart() as bp:
        with BuildSketch(Plane.XY):
            with BuildLine():
                Polyline(*clean, close=True)
            make_face()
        revolve(axis=Axis.X)
    export_step(bp.part, path)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="Angelino aerospike contour generator")
    ap.add_argument("--gamma", type=float, default=None, help="ratio of specific heats (or use --ox/--fuel with rocketcea)")
    ap.add_argument("--eps", type=float, required=True, help="area ratio A_e/A_t")
    ap.add_argument("--re", type=float, required=True, help="cowl lip (exit) radius, mm")
    ap.add_argument("--trunc", type=float, default=1.0, help="fraction of the ideal length to keep (0-1]")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--upstream", type=float, default=20.0, help="mounting stub length upstream of the throat, mm")
    ap.add_argument("--pc", type=float, default=None, help="chamber pressure, bar (for performance)")
    ap.add_argument("--tc", type=float, default=None, help="chamber temperature, K")
    ap.add_argument("--mw", type=float, default=None, help="exhaust molar mass, kg/kmol")
    ap.add_argument("--ox", default=None)
    ap.add_argument("--fuel", default=None)
    ap.add_argument("--mr", type=float, default=None, help="mixture ratio O/F for rocketcea")
    ap.add_argument("--out", default="out/aerospike")
    ap.add_argument("--no-step", action="store_true")
    a = ap.parse_args(argv)

    cea = None
    gamma, Tc, MW = a.gamma, a.tc, a.mw
    if a.ox and a.fuel:
        if a.pc is None or a.mr is None:
            raise SystemExit("--ox/--fuel need --pc and --mr")
        cea = cea_properties(a.ox, a.fuel, a.pc, a.mr, a.eps)
        gamma, Tc, MW = cea["gamma"], cea["Tc"], cea["MW"]
    if gamma is None:
        raise SystemExit("give --gamma (or --ox/--fuel/--mr/--pc with rocketcea installed)")

    c = angelino_contour(gamma, a.eps, a.re, a.n)
    if a.trunc < 1.0:
        c = truncate(c, a.trunc)
    if a.pc is not None and Tc is not None and MW is not None:
        c["performance_ideal"] = ideal_performance(gamma, a.eps, c["Me"], a.pc, Tc, MW, c["At_mm2"])
        c["chamber"] = {"pc_bar": a.pc, "Tc_K": Tc, "MW": MW}
    if cea:
        c["cea"] = cea
    meta = write_contour(c, a.out, a.upstream, not a.no_step)
    print(f"Me {meta['Me']:.4f}  nu_e {meta['nu_e_deg']:.2f} deg  throat gap {meta['throat_gap_mm']:.3f} mm at "
          f"x {meta['throat_x_mm']:.3f} r {meta['throat_r_mm']:.3f}  At {meta['At_mm2']:.2f} mm2 "
          f"(check {meta['throat_area_check']:.4f})  L_full {meta['L_full_mm']:.2f} mm"
          + (f"  L_trunc {meta['L_trunc_mm']:.2f} mm base r {meta['base_r_mm']:.2f}" if "L_trunc_mm" in meta else ""))
    if "performance_ideal" in meta:
        p = meta["performance_ideal"]
        print(f"ideal: Isp_vac {p['Isp_vac_s']:.1f} s  Isp_sl {p['Isp_sl_s']:.1f} s  F_vac {p['F_vac_N']:.0f} N  mdot {p['mdot_kg_s']:.3f} kg/s")
    print("outputs:", meta["outputs"])


if __name__ == "__main__":
    main()
