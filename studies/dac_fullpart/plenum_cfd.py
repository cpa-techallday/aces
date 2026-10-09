"""Non-periodic D3Q19 TRT LBM: air-side distribution solve of the DAC fullpart v2.

Domain (air path only): inlet slot -> inlet plenum -> gyroid core -> outlet plenum
-> outlet slot, at a coarse voxel pitch (default 6 mm).  The gyroid core is a
porous continuum: a Darcy-Forchheimer drag force calibrated so the dimensionless
momentum-resistance ratio J = rho v_port^2 / dP_core and the Forchheimer fraction
at the design point match the periodic-CFD measurements (out/cfd/dacv2-*).  The
plenum is free laminar LBM (TRT, Guo forcing, half-way bounce-back walls,
Zou/He velocity inlet / density outlet on the slot faces).

Honesty label: this is a DIMENSIONLESS distribution solve.  J and the drag-law
shape are exact at the design point; the plenum's own Reynolds number is far
below the turbulent truth, so absolute plenum losses and any turbulence-driven
mixing are not captured.  It replaces the 1/(1+J) ranking heuristic with a real
flow field for the purpose of ranking distributor designs.  Not verified;
simlab sign-off still required.

Selftest (--selftest): square duct, uniform-velocity inlet, density outlet;
checks centerline/mean = 2.0962 and fRe = 56.91 (Shah & London) and mass
conservation inlet vs outlet.

Checkpointed: --steps budgets each call, state in <out>.state.npz, resume by
re-running the same command.  Results land in <out>.json + <out>.png maps.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aces.cfd import lbm

# geometry of the v2 winner (winner-v2.json) and fixed module layout (fullpart.py)
W_FACE, H_FACE = 1.14, 1.20
WINNER = dict(cell_m=0.01234428584710754, wall_ratio=0.055928842488643674,
              core_len_m=0.06468906242825856, plenum_len_m=0.1535147410584605,
              slot_h_m=0.4333117169951801, gallery_w_m=0.05015986344809424,
              casing_t_m=0.003961835234470268)
# measured porous law (periodic CFD, v2 geometry): dpdx = A u_s + B u_s^2  [Pa/m]
# filled from out/cfd/dacv2-* by calibrate_drag(); keep None until fitted
AIR_RHO, AIR_NU = 1.204, 1.48e-5      # 293.15 K
M_DOT, V_PORT, U_S_OP = 4.5, 8.292, None  # kg/s, m/s (winner metrics), superficial op m/s


def build_air_domain(p, h):
    """Fluid mask + zone labels for the air path at voxel pitch h (metres)."""
    a = p["cell_m"]
    Lc, Lp = p["core_len_m"], p["plenum_len_m"]
    hp, wg, tc = p["slot_h_m"], p["gallery_w_m"], p["casing_t_m"]
    Lo = 0.6 * Lp
    nx_c = max(3, int(round(Lc / a)))
    ny_c = max(3, int(round((W_FACE - 2 * wg) / a)))
    nz_c = max(3, int(round(H_FACE / a)))
    Lc, Wc, Hc = nx_c * a, ny_c * a, nz_c * a
    yc0, zc0 = (W_FACE - Wc) / 2, (H_FACE - Hc) / 2
    L_tot = Lp + Lc + Lo
    m_ = tc + 2 * 0.003
    x0, x1 = -m_, L_tot + m_
    xa, xb = x0 - h, x1 + h
    ya, yb = yc0 + tc, yc0 + Wc - tc
    za, zb = zc0 + tc, zc0 + Hc - tc
    xs = np.arange(xa, xb + 0.5 * h, h); xs = xs[xs <= xb + 1e-12]
    ys = np.arange(ya, yb + 0.5 * h, h); ys = ys[ys <= yb + 1e-12]
    zs = np.arange(za, zb + 0.5 * h, h); zs = zs[zs <= zb + 1e-12]
    X, Y, Z = np.meshgrid(xs, ys, zs, indexing="ij")

    def box(xa_, xb_, ya_, yb_, za_, zb_):
        return (X >= xa_) & (X <= xb_) & (Y >= ya_) & (Y <= yb_) & (Z >= za_) & (Z <= zb_)

    zmid = zc0 + Hc / 2
    fluid = (box(x0 + tc, Lp, ya, yb, za, zb)                            # plenum in
             | box(Lp + Lc, x1 - tc, ya, yb, za, zb)                     # plenum out
             | box(xa, x0 + tc, ya, yb, zmid - hp / 2, zmid + hp / 2)    # slot in
             | box(x1 - tc, xb, ya, yb, zmid - hp / 2, zmid + hp / 2)    # slot out
             )
    core = box(Lp, Lp + Lc, yc0, yc0 + Wc, zc0, zc0 + Hc) & (X > Lp) & (X < Lp + Lc)
    core &= (Y >= ya) & (Y <= yb) & (Z >= za) & (Z <= zb)
    fluid |= core
    inlet = np.zeros_like(fluid); inlet[0] = fluid[0]
    outlet = np.zeros_like(fluid); outlet[-1] = fluid[-1]
    meta = dict(h=h, xs=[float(xs[0]), float(xs[-1])], shape=list(fluid.shape),
                i_core=[int(np.argmin(np.abs(xs - Lp))), int(np.argmin(np.abs(xs - (Lp + Lc))))],
                Lc=Lc, Wc=Wc, Hc=Hc, hp=hp, L_tot=L_tot, zmid=zmid)
    return fluid, core, inlet, outlet, meta



def _plane_edge(fl_np, idx):
    """Nodes of boundary plane `idx` (0 or -1) that have a non-fluid in-plane
    neighbour (or domain edge): the Zou/He corners.  They need a simpler BC."""
    base = fl_np[idx]
    edge = np.zeros_like(base)
    for dj, dk in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nb = np.roll(base, (dj, dk), axis=(0, 1))
        if dj > 0:
            nb[:dj] = False
        elif dj < 0:
            nb[dj:] = False
        if dk > 0:
            nb[:, :dk] = False
        elif dk < 0:
            nb[:, dk:] = False
        edge |= base & ~nb
    return edge


# ---------------------------------------------------------------- solver
def solve(fluid, porous, inlet, outlet, nu, u_in, a_drag, b_drag, *, xp,
          steps_budget, f0=None, check_every=500, ramp_steps=2000, log=print):
    """TRT D3Q19 with Zou/He inlet (velocity) / outlet (density), bounce-back walls,
    Darcy-Forchheimer drag in `porous`.  Returns (result dict, populations)."""
    tau_p = 3.0 * nu + 0.5
    tau_m = 0.5 + lbm.MAGIC_FLOW / (tau_p - 0.5)
    wp, wm = 1.0 / tau_p, 1.0 / tau_m
    fl = xp.asarray(fluid)
    solid = ~fl
    shape = fl.shape
    c = lbm.C19
    w = xp.asarray(lbm.W19).reshape(19, 1, 1, 1)
    cx = xp.asarray(c[:, 0].astype(float)).reshape(19, 1, 1, 1)
    cy = xp.asarray(c[:, 1].astype(float)).reshape(19, 1, 1, 1)
    cz = xp.asarray(c[:, 2].astype(float)).reshape(19, 1, 1, 1)
    bb = [fl & _nroll(xp, solid, c[i], fill=1) for i in range(19)]
    por = xp.asarray(porous)
    inl = xp.asarray(inlet)
    out = xp.asarray(outlet)
    # Zou/He is unstable at the boundary-plane corners (transverse pops there are
    # bounce-backed, not streamed): those nodes get a zero-gradient copy instead
    edge_in = xp.asarray(_plane_edge(lbm._asnp(xp, fl), 0) & lbm._asnp(xp, inl)[0])
    edge_out = xp.asarray(_plane_edge(lbm._asnp(xp, fl), -1) & lbm._asnp(xp, out)[-1])
    has_edge_in = bool(edge_in.any())
    has_edge_out = bool(edge_out.any())
    OPP = lbm.OPP19
    f = xp.asarray(f0) if f0 is not None else w * fl.astype(float)
    f = f * fl
    u_prev = xp.zeros((3,) + shape)
    hist = []
    steps = 0
    converged = False
    u_in_now = 0.0
    while steps < steps_budget:
        u_in_now = u_in * min(1.0, (steps + 1) / ramp_steps)   # soft start
        rho = f.sum(axis=0)
        rho_safe = xp.where(fl, rho, 1.0)
        # velocity-dependent drag from the previous field (explicit; steady state unaffected)
        umag_p = xp.sqrt((u_prev * u_prev).sum(axis=0))
        drag = xp.where(por, a_drag + b_drag * umag_p, 0.0)
        Fx = -drag * u_prev[0]
        Fy = -drag * u_prev[1]
        Fz = -drag * u_prev[2]
        ux = ((f * cx).sum(axis=0) + 0.5 * Fx) / rho_safe
        uy = ((f * cy).sum(axis=0) + 0.5 * Fy) / rho_safe
        uz = ((f * cz).sum(axis=0) + 0.5 * Fz) / rho_safe
        ux, uy, uz = ux * fl, uy * fl, uz * fl
        cu = cx * ux + cy * uy + cz * uz
        usq = ux * ux + uy * uy + uz * uz
        feq_p = w * rho_safe * (1.0 + 4.5 * cu * cu - 1.5 * usq)
        feq_m = w * rho_safe * 3.0 * cu
        cF = cx * Fx + cy * Fy + cz * Fz
        uF = ux * Fx + uy * Fy + uz * Fz
        s_p = w * (9.0 * cu * cF - 3.0 * uF)
        s_m = w * 3.0 * cF
        f_opp = f[OPP]
        f_p = 0.5 * (f + f_opp)
        f_m = 0.5 * (f - f_opp)
        fs = f - wp * (f_p - feq_p) - wm * (f_m - feq_m) + (1 - 0.5 * wp) * s_p + (1 - 0.5 * wm) * s_m
        fs = fs * fl
        fn = xp.empty_like(fs)
        for i in range(19):
            fn[i] = _nroll(xp, fs[i], c[i])
            fn[i] = xp.where(bb[i], fs[OPP[i]], fn[i])
        # ---- Zou/He inlet (i=0, prescribed u=(u_in_now,0,0))
        u0 = u_in_now
        r_in = (fn[0] + fn[3] + fn[4] + fn[5] + fn[6] + fn[15] + fn[16] + fn[17] + fn[18]
                + 2.0 * (fn[2] + fn[8] + fn[10] + fn[12] + fn[14]))
        r_in = r_in / max(1e-12, (1.0 - u0))
        f1 = fn[2] + (1.0 / 3.0) * r_in * u0
        f7 = fn[8] + 0.5 * (fn[4] - fn[3]) + (1.0 / 6.0) * r_in * u0
        f9 = fn[10] + 0.5 * (fn[3] - fn[4]) + (1.0 / 6.0) * r_in * u0
        f11 = fn[12] + 0.5 * (fn[6] - fn[5]) + (1.0 / 6.0) * r_in * u0
        f13 = fn[14] + 0.5 * (fn[5] - fn[6]) + (1.0 / 6.0) * r_in * u0
        for src, dst in ((f1, 1), (f7, 7), (f9, 9), (f11, 11), (f13, 13)):
            fn[dst] = xp.where(inl, src, fn[dst])
        # ---- Zou/He outlet (i=-1, prescribed rho=1)
        u_out = ((fn[0] + fn[3] + fn[4] + fn[5] + fn[6] + fn[15] + fn[16] + fn[17] + fn[18]
                  + 2.0 * (fn[1] + fn[7] + fn[9] + fn[11] + fn[13])) - 1.0)
        f2 = fn[1] - (1.0 / 3.0) * u_out
        f8 = fn[7] - 0.5 * (fn[4] - fn[3]) - (1.0 / 6.0) * u_out
        f10 = fn[9] - 0.5 * (fn[3] - fn[4]) - (1.0 / 6.0) * u_out
        f12 = fn[11] - 0.5 * (fn[6] - fn[5]) - (1.0 / 6.0) * u_out
        f14 = fn[13] - 0.5 * (fn[5] - fn[6]) - (1.0 / 6.0) * u_out
        for src, dst in ((f2, 2), (f8, 8), (f10, 10), (f12, 12), (f14, 14)):
            fn[dst] = xp.where(out, src, fn[dst])
        # corner/edge nodes of the boundary planes: zero-gradient copy from the
        # adjacent interior plane (Zou/He is ill-posed there and goes unstable)
        # corner/edge nodes: zero-gradient copy
        if has_edge_in:
            for i in range(19):
                fn[i][0] = xp.where(edge_in, fn[i][1], fn[i][0])
        if has_edge_out:
            for i in range(19):
                fn[i][-1] = xp.where(edge_out, fn[i][-2], fn[i][-1])
        f = fn * fl
        u_prev = xp.stack([ux, uy, uz])
        steps += 1
        if steps % check_every == 0:
            q_in = float(lbm._asnp(xp, (rho_safe * ux * inl).sum()))   # mass flux (compressible-consistent)
            q_out = float(lbm._asnp(xp, (rho_safe * ux * out).sum()))
            hist.append((steps, q_in, q_out))
            log(f"    step {steps}: Q_in={q_in:.6f} Q_out={q_out:.6f} u_in={u_in_now:.4f}")
            if len(hist) >= 3 and u_in_now >= u_in:
                recent = [h[1] for h in hist[-3:]]
                if abs(recent[-1]) > 0 and max(recent) - min(recent) < 2e-8 * abs(recent[-1]) * check_every \
                        and abs(q_in - q_out) / max(abs(q_in), 1e-12) < 8e-3:
                    converged = True
                    break
    res = dict(steps=steps, converged=converged, hist=hist)
    return res, f, u_prev, rho_safe


def _nroll(xp, a, c_, fill=0):
    """Roll without wraparound: values shifted in from outside the domain are `fill`.
    fill=True for the solid-neighbour test (outside = wall -> bounce-back),
    fill=0 for streaming (outside populations are overwritten by BCs anyway)."""
    r = xp.roll(a, shift=(int(c_[0]), int(c_[1]), int(c_[2])), axis=(0, 1, 2))
    if fill == 0:
        if c_[0] > 0:
            r[:c_[0]] = 0
        elif c_[0] < 0:
            r[c_[0]:] = 0
        if c_[1] > 0:
            r[:, :c_[1]] = 0
        elif c_[1] < 0:
            r[:, c_[1]:] = 0
        if c_[2] > 0:
            r[:, :, :c_[2]] = 0
        elif c_[2] < 0:
            r[:, :, c_[2]:] = 0
    else:
        if c_[0] > 0:
            r[:c_[0]] = fill
        elif c_[0] < 0:
            r[c_[0]:] = fill
        if c_[1] > 0:
            r[:, :c_[1]] = fill
        elif c_[1] < 0:
            r[:, c_[1]:] = fill
        if c_[2] > 0:
            r[:, :, :c_[2]] = fill
        elif c_[2] < 0:
            r[:, :, c_[2]:] = fill
    return r


# ---------------------------------------------------------------- selftest
def selftest(xp):
    """Square duct, uniform inlet velocity, density outlet.
    Shah & London: centreline/mean = 2.0962, fRe(Dh) = 56.91."""
    n = 16
    nx = 64
    fluid = np.zeros((nx, n, n), bool)
    fluid[:, 1:-1, 1:-1] = True
    inlet = np.zeros_like(fluid); inlet[0] = fluid[0]
    outlet = np.zeros_like(fluid); outlet[-1] = fluid[-1]
    porous = np.zeros_like(fluid)
    nu, u_in = 0.05, 0.02
    res, f, u_prev, rho = solve(fluid, porous, inlet, outlet, nu, u_in, 0.0, 0.0,
                                xp=xp, steps_budget=15000, check_every=500,
                                ramp_steps=4000, log=lambda s: None)
    ux = lbm._asnp(xp, u_prev[0])
    rr = lbm._asnp(xp, rho)
    fl_np = np.asarray(fluid)
    # developed-section profile (exclude inlet/outlet development lengths)
    prof = ux[-8, 1:-1, 1:-1]
    u_mean = float(prof.mean())
    u_max = float(prof.max())
    gap = n - 2
    # Darcy fRe with Dh = side: dpdx from mean density gradient over the mid-channel
    dpdx = (rr[4] - rr[-5]).mean() / 3.0 / (nx - 8)
    fre_geo = dpdx * 2 * gap ** 2 / (nu * u_mean)
    fre_mid = dpdx * 2 * (gap + 1) ** 2 / (nu * u_mean)
    # mass flux rho*u (NOT volume flux u): LBM is weakly compressible, so u rises
    # along the channel as rho falls; rho*u is the conserved quantity
    m_in = float((rr[1] * ux[1])[fl_np[1]].sum())
    m_out = float((rr[-2] * ux[-2])[fl_np[-2]].sum())
    mass_err = abs(m_in - m_out) / max(abs(m_in), 1e-12)
    # tolerance note: 14x14 duct + Zou/He end effects; profile ratio is the sharp
    # check, fRe is a coarse-grid ballpark (periodic exact refs live in cfd tests)
    ok = (abs(u_max / u_mean - 2.0962) / 2.0962 < 0.03 and min(fre_geo, fre_mid) < 56.91 * 1.15
          and max(fre_geo, fre_mid) > 56.91 * 0.70 and mass_err < 2e-3)
    print(f"SELFTEST square duct: u_max/u_mean={u_max/u_mean:.4f} (2.0962), "
          f"fRe={fre_geo:.1f}/{fre_mid:.1f} geo/mid-wall (56.91), mass-flux err={mass_err:.2e}, "
          f"steps={res['steps']} -> {'PASS' if ok else 'FAIL'}")
    return ok


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=40000)
    ap.add_argument("--h", type=float, default=0.006)
    ap.add_argument("--nu", type=float, default=0.01)
    ap.add_argument("--u-in", type=float, default=0.03)
    ap.add_argument("--variant", default="base", help="base | plate:<openfrac> | baffle")
    ap.add_argument("--out", default=str(ROOT / "out" / "plenum" / "base"))
    ap.add_argument("--params-json", default=None,
                    help="optional selected-*.json to override the v2 winner geometry")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    xp = lbm.backend(prefer_gpu=True)
    print("backend:", xp.__name__)
    if args.selftest:
        sys.exit(0 if selftest(xp) else 1)

    winner = dict(WINNER)
    if args.params_json:
        winner.update(json.loads(open(args.params_json).read())["params"])
        print("geometry override from", args.params_json)
    global V_PORT
    Ap_h = (W_FACE - 2 * winner["gallery_w_m"]) * winner["slot_h_m"]
    V_PORT = M_DOT / (AIR_RHO * Ap_h)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    state_path = out.parent / (out.name + ".state.npz")

    fluid, core, inlet, outlet, meta = build_air_domain(winner, args.h)
    print(f"grid {meta['shape']} fluid {int(fluid.sum())/1e6:.2f} M nodes, h={args.h*1e3:.0f} mm")

    # ---- drag calibration: dimensionless match of J and Forchheimer fraction
    # physical law from periodic-CFD sweep (loaded here, fitted below in __main__ path)
    sweep = []
    for f in sorted((ROOT / "out" / "cfd").glob("dacv2-gyroid-re*-n48")):
        d = json.loads(open(f).read())
        if d["measurement"]:
            m, dg = d["measurement"], d["diagnostics"]
            u_sup = m["mass_flux_kg_m2s"] / AIR_RHO
            sweep.append((dg["re_achieved"], m["dpdx_Pa_m"], u_sup))
    if len(sweep) < 2:
        print("need >= 2 dacv2 sweep points in out/cfd; run cfd_resweep first")
        sys.exit(2)
    us = np.array([s[2] for s in sweep])
    ys = np.array([s[1] for s in sweep]) / us
    B, A = np.polyfit(us, ys, 1)
    # scale the measured law (v2 anchor cell, dh0) to this geometry's dh (Ergun-form)
    from aces.domains.hx import tpms as _tpms
    DH0 = 6.7094e-3
    _uc = _tpms.unit_cell("gyroid", winner["wall_ratio"])
    _dh = _uc.hydraulic_diameter_over_a(1) * winner["cell_m"]
    A = A * (DH0 / _dh) ** 2
    B = B * (DH0 / _dh)
    print(f"geometry dh={_dh*1e3:.2f} mm -> A={A:.2f} B={B:.2f}")
    dx = args.h
    # operating point (winner): face velocity over active face
    Af = meta["Wc"] * meta["Hc"]
    u_s_op = (M_DOT / AIR_RHO) / Af
    dp_op = A * u_s_op + B * u_s_op ** 2                      # Pa/m of core
    dp_core_op = dp_op * meta["Lc"]
    j_phys = AIR_RHO * V_PORT ** 2 / dp_core_op
    beta_op = B * u_s_op ** 2 / dp_op
    # lattice: choose dt so that u_in maps from physical slot velocity
    # (this pins the time base; drag must then satisfy J exactly)
    dt = dx * args.u_in / V_PORT
    nu_l_check = AIR_NU * dt / dx ** 2
    a_l = A * dt / AIR_RHO
    b_l = B * dx / AIR_RHO
    # rescale (a,b) so that rho_L v_port_L^2 / dP_core_L == j_phys exactly
    # u_s_L at the core face follows continuity: u_in * slot_area / face_area
    u_s_l = args.u_in * meta["hp"] / meta["Hc"]  # continuity: slot -> core face
    dpdx_l = a_l * u_s_l + b_l * u_s_l ** 2
    dp_core_l = dpdx_l * (meta["Lc"] / dx)
    j_lattice = args.u_in ** 2 / dp_core_l
    scale = j_lattice / j_phys
    a_l *= scale
    b_l *= scale
    print(f"porous law: A={A:.3f} B={B:.3f} Pa/m per (m/s); op dp={dp_core_op:.2f} Pa, "
          f"J_phys={j_phys:.2f}, beta={beta_op:.2f}; lattice a={a_l:.3e} b={b_l:.3e} "
          f"(x{scale:.3f}); nu_eff/physical={args.nu/nu_l_check:.1f}")

    # ---- variant geometry: perforated distributor plate / baffle
    plate_drag = None
    if args.variant.startswith("plate"):
        openfrac = float(args.variant.split(":")[1]) if ":" in args.variant else 0.4
        # plate at core inlet face: extra local drag in 1 voxel layer,
        # graded: near the jet axis (y,z centre) more closed, far field open
        plate = np.zeros_like(fluid)
        i0 = meta["i_core"][0]
        yy, zz = np.meshgrid(np.arange(fluid.shape[1]), np.arange(fluid.shape[2]), indexing="ij")
        cy, cz = fluid.shape[1] / 2, fluid.shape[2] / 2
        r = np.sqrt(((yy - cy) / fluid.shape[1]) ** 2 + ((zz - cz) / fluid.shape[2]) ** 2)
        prof = openfrac + (1.0 - openfrac) * np.clip(1.6 * r, 0, 1)  # local open fraction
        # plate layer: allow only 'prof' fraction via extra drag ~ (1/prof^2 - 1) k_o 0.5 rho v^2
        plate[i0] = fluid[i0]
        k_plate = (1.0 / prof ** 2 - 1.0)                          # screen loss coeff K
        plate_drag = (plate, k_plate / (2.0 * args.h))             # b = K/(2 dx)
        print(f"distributor plate: centre open {openfrac:.2f} -> rim 1.0, "
              f"loss k {k_plate.min():.2f}..{k_plate.max():.2f}")
    f0 = None
    steps_done = 0
    hist = []
    if state_path.exists():
        with np.load(state_path, allow_pickle=True) as st:   # closed before unlink (Win lock)
            f0 = xp.asarray(st["f0"])
            steps_done = int(st["steps_done"])
        print(f"resuming at {steps_done} steps")

    # add plate drag as an extra porous-like zone with its own b coefficient
    porous_eff = core
    if plate_drag is not None:
        plate_mask, kprof = plate_drag
        porous_eff = core | plate_mask
        b_map = xp.zeros(fluid.shape)
        b_map = b_map + xp.asarray(np.where(core, b_l, 0.0))
        b_map = b_map + xp.asarray(np.where(plate_mask, kprof, 0.0))
        a_map = xp.asarray(np.where(core, a_l, 0.0))
    else:
        a_map = xp.asarray(np.where(core, a_l, 0.0))
        b_map = xp.asarray(np.where(core, b_l, 0.0))

    res, fpop, u_field, rho_f = solve_map(fluid, porous_eff, inlet, outlet, args.nu, args.u_in,
                                          a_map, b_map, xp=xp, steps_budget=args.steps,
                                          f0=f0, ramp_steps=(2000 if f0 is None else 1))
    steps_done += res["steps"]
    if not res["converged"]:
        np.savez_compressed(state_path, f0=lbm._asnp(xp, fpop), steps_done=steps_done)
        print(f"PARTIAL ({steps_done} steps); rerun to resume")
        sys.exit(3)
    state_path.unlink(missing_ok=True)

    # ---------------- metrics: face velocity distribution
    ux = lbm._asnp(xp, u_field[0])
    # first/last plane where the porous mask actually has nodes (strict X>Lp
    # excludes the argmin plane; derive from the mask, not the axis)
    core_planes = np.where(np.asarray(core).any(axis=(1, 2)))[0]
    i0, i1 = int(core_planes[0]), int(core_planes[-1])
    face_in = ux[i0] * np.asarray(fluid[i0])
    face_out = ux[i1] * np.asarray(fluid[i1])
    vals = face_in[np.asarray(core[i0])]
    vals = vals[vals > 0]
    u_mean, u_std = float(vals.mean()), float(vals.std())
    cv = u_std / u_mean
    within = float(np.mean(np.abs(vals / u_mean - 1.0) < 0.10))
    rr = lbm._asnp(xp, rho_f)
    dp_tot_l = float((rr[inlet].mean() - rr[outlet].mean()) / 3.0)
    dp_tot_pa = dp_tot_l * AIR_RHO * dx ** 2 / dt ** 2  # p_L -> Pa
    metrics = dict(u_face_mean_l=u_mean, cv=cv, min_over_mean=float(vals.min() / u_mean),
                   max_over_mean=float(vals.max() / u_mean), frac_within_10pct=within,
                   uniformity_eta=1.0 - cv, dp_total_Pa=dp_tot_pa,
                   dp_core_design_Pa=dp_core_op, J_matched=j_phys,
                   steps_total=steps_done, converged=True)
    result = dict(variant=args.variant, h=args.h, nu=args.nu, u_in=args.u_in,
                  meta=meta, porous_law=dict(A=A, B=B, dp_core_op=dp_core_op,
                                             beta_op=beta_op, j_phys=j_phys),
                  metrics=metrics,
                  warnings=["dimensionless distribution solve: J and drag-law shape matched "
                            "at the design point; plenum Re is laminar stand-in",
                            "not verified; simlab sign-off required"])
    with open(out.with_suffix(".json"), "w") as fh:
        json.dump(result, fh, indent=2, default=float)
    np.savez_compressed(out.with_suffix(".maps.npz"),
                        face_in=face_in, face_out=face_out, core_mask=np.asarray(core[i0]))
    print(json.dumps(metrics, indent=2, default=float))

    # ---------------- map PNG
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axs = plt.subplots(1, 2, figsize=(11, 4.6), constrained_layout=True)
    for ax, mp, ttl in ((axs[0], face_in, "core inlet face"), (axs[1], face_out, "core outlet face")):
        m = np.ma.masked_where(mp == 0, mp / max(u_mean, 1e-12))
        im = ax.imshow(m.T, origin="lower", cmap="turbo", vmin=0, vmax=2)
        ax.set_title(f"{ttl}  u/ū  (cv={cv:.3f})")
        ax.set_xticks([]); ax.set_yticks([])
        fig.colorbar(im, ax=ax, shrink=0.8)
    fig.suptitle(f"DAC v2 air distribution — variant '{args.variant}' (dimensionless LBM, J-matched)")
    fig.savefig(out.with_suffix(".png"), dpi=140)
    print("wrote", out.with_suffix(".png"))


def solve_map(fluid, porous, inlet, outlet, nu, u_in, a_map, b_map, *, xp, steps_budget, f0=None,
              check_every=500, ramp_steps=2000, log=print):
    """Like solve() but with spatially varying drag maps a_map/b_map (lattice units)."""
    tau_p = 3.0 * nu + 0.5
    tau_m = 0.5 + lbm.MAGIC_FLOW / (tau_p - 0.5)
    wp, wm = 1.0 / tau_p, 1.0 / tau_m
    fl = xp.asarray(fluid)
    solid = ~fl
    shape = fl.shape
    c = lbm.C19
    w = xp.asarray(lbm.W19).reshape(19, 1, 1, 1)
    cx = xp.asarray(c[:, 0].astype(float)).reshape(19, 1, 1, 1)
    cy = xp.asarray(c[:, 1].astype(float)).reshape(19, 1, 1, 1)
    cz = xp.asarray(c[:, 2].astype(float)).reshape(19, 1, 1, 1)
    bb = [fl & _nroll(xp, solid, c[i], fill=1) for i in range(19)]
    inl = xp.asarray(inlet)
    out = xp.asarray(outlet)
    edge_in = xp.asarray(_plane_edge(lbm._asnp(xp, fl), 0) & lbm._asnp(xp, inl)[0])
    edge_out = xp.asarray(_plane_edge(lbm._asnp(xp, fl), -1) & lbm._asnp(xp, out)[-1])
    has_edge_in = bool(edge_in.any())
    has_edge_out = bool(edge_out.any())
    a_m = xp.asarray(a_map)
    b_m = xp.asarray(b_map)
    OPP = lbm.OPP19
    f = xp.asarray(f0) if f0 is not None else w * fl.astype(float)
    f = f * fl
    u_prev = xp.zeros((3,) + shape)
    hist = []
    steps = 0
    converged = False
    u_in_now = 0.0
    ux = uy = uz = xp.zeros(shape)
    while steps < steps_budget:
        u_in_now = u_in * min(1.0, (steps + 1) / ramp_steps)
        rho = f.sum(axis=0)
        rho_safe = xp.where(fl, rho, 1.0)
        # semi-implicit drag: u = u* / (1 + 0.5 d(u*)/rho), d = a + b|u*|  -- the
        # explicit form overshoots when 2 b u > 1 at jet-edge velocity spikes
        usx = (f * cx).sum(axis=0) / rho_safe
        usy = (f * cy).sum(axis=0) / rho_safe
        usz = (f * cz).sum(axis=0) / rho_safe
        umag_s = xp.sqrt(usx * usx + usy * usy + usz * usz)
        dcoef = (a_m + b_m * umag_s) / rho_safe
        den = 1.0 + 0.5 * dcoef
        ux = usx / den
        uy = usy / den
        uz = usz / den
        Fx = -dcoef * ux
        Fy = -dcoef * uy
        Fz = -dcoef * uz
        ux, uy, uz = ux * fl, uy * fl, uz * fl
        cu = cx * ux + cy * uy + cz * uz
        usq = ux * ux + uy * uy + uz * uz
        feq_p = w * rho_safe * (1.0 + 4.5 * cu * cu - 1.5 * usq)
        feq_m = w * rho_safe * 3.0 * cu
        cF = cx * Fx + cy * Fy + cz * Fz
        uF = ux * Fx + uy * Fy + uz * Fz
        s_p = w * (9.0 * cu * cF - 3.0 * uF)
        s_m = w * 3.0 * cF
        f_opp = f[OPP]
        f_p = 0.5 * (f + f_opp)
        f_m = 0.5 * (f - f_opp)
        fs = f - wp * (f_p - feq_p) - wm * (f_m - feq_m) + (1 - 0.5 * wp) * s_p + (1 - 0.5 * wm) * s_m
        fs = fs * fl
        fn = xp.empty_like(fs)
        for i in range(19):
            fn[i] = _nroll(xp, fs[i], c[i])
            fn[i] = xp.where(bb[i], fs[OPP[i]], fn[i])
        u0 = u_in_now
        r_in = (fn[0] + fn[3] + fn[4] + fn[5] + fn[6] + fn[15] + fn[16] + fn[17] + fn[18]
                + 2.0 * (fn[2] + fn[8] + fn[10] + fn[12] + fn[14]))
        r_in = r_in / max(1e-12, (1.0 - u0))
        f1 = fn[2] + (1.0 / 3.0) * r_in * u0
        f7 = fn[8] + 0.5 * (fn[4] - fn[3]) + (1.0 / 6.0) * r_in * u0
        f9 = fn[10] + 0.5 * (fn[3] - fn[4]) + (1.0 / 6.0) * r_in * u0
        f11 = fn[12] + 0.5 * (fn[6] - fn[5]) + (1.0 / 6.0) * r_in * u0
        f13 = fn[14] + 0.5 * (fn[5] - fn[6]) + (1.0 / 6.0) * r_in * u0
        for src, dst in ((f1, 1), (f7, 7), (f9, 9), (f11, 11), (f13, 13)):
            fn[dst] = xp.where(inl, src, fn[dst])
        u_out = ((fn[0] + fn[3] + fn[4] + fn[5] + fn[6] + fn[15] + fn[16] + fn[17] + fn[18]
                  + 2.0 * (fn[1] + fn[7] + fn[9] + fn[11] + fn[13])) - 1.0)
        f2 = fn[1] - (1.0 / 3.0) * u_out
        f8 = fn[7] - 0.5 * (fn[4] - fn[3]) - (1.0 / 6.0) * u_out
        f10 = fn[9] - 0.5 * (fn[3] - fn[4]) - (1.0 / 6.0) * u_out
        f12 = fn[11] - 0.5 * (fn[6] - fn[5]) - (1.0 / 6.0) * u_out
        f14 = fn[13] - 0.5 * (fn[5] - fn[6]) - (1.0 / 6.0) * u_out
        for src, dst in ((f2, 2), (f8, 8), (f10, 10), (f12, 12), (f14, 14)):
            fn[dst] = xp.where(out, src, fn[dst])
        # corner/edge nodes of the boundary planes: zero-gradient copy (see solve())
        if has_edge_in:
            for i in range(19):
                fn[i][0] = xp.where(edge_in, fn[i][1], fn[i][0])
        if has_edge_out:
            for i in range(19):
                fn[i][-1] = xp.where(edge_out, fn[i][-2], fn[i][-1])
        f = fn * fl
        u_prev = xp.stack([ux, uy, uz])
        steps += 1
        if steps % check_every == 0:
            q_in = float(lbm._asnp(xp, (rho_safe * ux * inl).sum()))   # mass flux (compressible-consistent)
            q_out = float(lbm._asnp(xp, (rho_safe * ux * out).sum()))
            hist.append((steps, q_in, q_out))
            log(f"    step {steps}: Q_in={q_in:.6f} Q_out={q_out:.6f}")
            if not (math.isfinite(q_in) and math.isfinite(q_out)):
                log("    NON-FINITE flux; aborting chunk")
                break
            if len(hist) >= 3 and u_in_now >= u_in:
                recent = [h[1] for h in hist[-3:]]
                if (abs(recent[-1]) > 0
                        and max(recent) - min(recent) < 2e-8 * abs(recent[-1]) * check_every
                        and abs(q_in - q_out) / max(abs(q_in), 1e-12) < 8e-3):
                    converged = True
                    break
    return dict(steps=steps, converged=converged, hist=hist), f, u_prev, rho_safe


if __name__ == "__main__":
    main()
