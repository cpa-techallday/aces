"""ARC-1 :: generative design of the inboard suspension rocker.

Architecture: inboard pushrod-actuated rocker. The upright drives a pushrod,
the pushrod drives a rocker (bellcrank) pivoting on the chassis, the rocker
drives a damper lying inside the tub. F1/LMP practice, and it suits this
chassis specifically: the spring loads land inside a well-triangulated part of
the frame instead of hanging off an outboard cantilever, which is where the
frame FEA found its only overloaded members.

The rocker is the part generative design was invented for - three hard points,
clear load cases, every gram rotating unsprung-adjacent mass.

Run:  python scripts/rocker.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from topopt import Domain, optimise, contours

ROOT = Path(__file__).resolve().parents[1]
P = json.loads((ROOT / "cad" /
                __import__("os").environ.get("ARC1_PARAMS",
                                             "params.json")).read_text())
OUT = ROOT / P["output"]["dir"]

# ---- rocker hard points (mm, rocker-local: pivot at origin, plate in XY) ----
PIVOT = (0.0, 0.0)
PUSHROD = (-95.0, -30.0)
DAMPER = (52.0, 78.0)

BOSS_R = 15.0          # bearing boss outer radius (passive solid)
BORE_R = 8.0           # bearing bore (passive void)
LOAD_R = 11.5          # radius over which load/support is spread

PLATE_T = 12.0         # mm, machined 7075-T6 or 316L plate
VOLFRAC = 0.16


def unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def design_loads() -> dict:
    """Pushrod axial force per case, derived from the vehicle mass and the
    suspension geometry rather than picked."""
    veh = P["vehicle"]
    sprung_corner = (veh["target_mass_kg"] + veh["driver_mass_kg"]) / 4.0
    unsprung = 15.0
    g = 9.81
    wheel_3g = (sprung_corner * 3.0 + unsprung * 3.0) * g

    # pushrod picks the lower arm at ~85% of its length, inclined 25 deg
    pr_eff = 0.85 * math.cos(math.radians(25.0))
    F_bump = wheel_3g / pr_eff
    return {
        "wheel_3g_N": wheel_3g,
        "BUMP": F_bump,
        "KERB": F_bump * 1.55,        # kerb strike / single-wheel impact
        "REBOUND": -F_bump * 0.35,    # droop, pushrod in tension
    }


def build_domain():
    xs = [PIVOT[0], PUSHROD[0], DAMPER[0]]
    ys = [PIVOT[1], PUSHROD[1], DAMPER[1]]
    pad = 38.0
    return Domain(min(xs) - pad, min(ys) - pad,
                  max(xs) + pad, max(ys) + pad, elem_mm=1.6)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    L = design_loads()
    print(f"wheel load at 3g      {L['wheel_3g_N']:,.0f} N")
    for k in ("BUMP", "KERB", "REBOUND"):
        print(f"  pushrod {k:<8}     {L[k]:>9,.0f} N")

    d = build_domain()
    print(f"\ndesign space {d.nelx}x{d.nely} elements "
          f"({d.nelx*d.h:.0f} x {d.nely*d.h:.0f} mm, {d.h} mm/elem)")

    # --- passive regions --------------------------------------------
    ps = np.zeros((d.nelx, d.nely), bool)
    pv = np.zeros((d.nelx, d.nely), bool)
    for (px, py) in (PIVOT, PUSHROD, DAMPER):
        ps |= d.elem_mask_disc(px, py, BOSS_R)
        pv |= d.elem_mask_disc(px, py, BORE_R)

    # --- support: the chassis pivot ONLY -----------------------------
    # The damper eye must NOT be a support. Ground both it and the pivot and
    # the optimiser only needs one path to ground, picks the cheaper one, and
    # deletes the damper arm entirely - which is exactly what it did. A rocker
    # is a lever: the pivot is the fulcrum, the damper is a reaction FORCE.
    # Clamping the whole pivot annulus (not a point pin) also restrains
    # rotation, so there is no rigid-body mode to worry about.
    fixed = []
    for n in d.nodes_in_annulus(*PIVOT, BORE_R, BOSS_R):
        fixed += [2 * n, 2 * n + 1]

    # --- loads: pushrod in, damper reaction out ----------------------
    # pushrod comes up and inboard from the wheel at 25 deg off vertical
    pr_dir = unit([math.sin(math.radians(25.0)), math.cos(math.radians(25.0))])
    pr_arm = np.array(PUSHROD)
    m_pr = pr_arm[0] * pr_dir[1] - pr_arm[1] * pr_dir[0]      # moment / unit F

    # damper acts perpendicular to its own arm (pure lever action), opposing
    dm_arm = np.array(DAMPER)
    dm_dir = unit([-dm_arm[1], dm_arm[0]]) * (-1.0 if m_pr > 0 else 1.0)
    m_dm = dm_arm[0] * dm_dir[1] - dm_arm[1] * dm_dir[0]
    ratio = abs(m_pr / m_dm)                                  # motion ratio

    pr_nodes = d.nodes_in_annulus(*PUSHROD, BORE_R, BOSS_R)
    dm_nodes = d.nodes_in_annulus(*DAMPER, BORE_R, BOSS_R)
    cases = []
    for key in ("BUMP", "KERB", "REBOUND"):
        c = {}
        f_pr = L[key] / len(pr_nodes)
        for n in pr_nodes:
            c[2 * n] = c.get(2 * n, 0.0) + f_pr * pr_dir[0]
            c[2 * n + 1] = c.get(2 * n + 1, 0.0) + f_pr * pr_dir[1]
        f_dm = L[key] * ratio / len(dm_nodes)
        for n in dm_nodes:
            c[2 * n] = c.get(2 * n, 0.0) + f_dm * dm_dir[0]
            c[2 * n + 1] = c.get(2 * n + 1, 0.0) + f_dm * dm_dir[1]
        cases.append(c)

    print(f"pushrod dir ({pr_dir[0]:.3f}, {pr_dir[1]:.3f})  "
          f"moment arm {abs(m_pr):.1f} mm")
    print(f"damper  dir ({dm_dir[0]:.3f}, {dm_dir[1]:.3f})  "
          f"moment arm {abs(m_dm):.1f} mm")
    print(f"motion ratio {ratio:.3f}   damper force at bump "
          f"{L['BUMP']*ratio:,.0f} N\n")

    dens = optimise(d, cases, fixed, volfrac=VOLFRAC, penal=3.0,
                    rmin=4.0, iters=130, passive_solid=ps, passive_void=pv)

    # --- extract the shape -------------------------------------------
    polys = contours(d, dens, level=0.5)
    polys = [p for p in polys if len(p) > 24]
    polys.sort(key=lambda p: -abs(_area(p)))
    print(f"\nextracted {len(polys)} contours "
          f"(outer {len(polys[0])} pts, area {abs(_area(polys[0]))/100:.1f} cm2)")

    smooth = [_smooth(_resample(p, 3.0), 3) for p in polys]
    area = abs(_area(smooth[0])) - sum(abs(_area(p)) for p in smooth[1:])
    rho = 2.81e-3                      # 7075-T6 g/mm3
    mass = area * PLATE_T * rho

    res = {
        "hardpoints": {"pivot": PIVOT, "pushrod": PUSHROD, "damper": DAMPER},
        "boss_r": BOSS_R, "bore_r": BORE_R, "plate_t": PLATE_T,
        "volfrac": VOLFRAC, "loads_N": L,
        "net_area_mm2": round(area, 1),
        "mass_g": round(mass, 1),
        "polys": [p.tolist() for p in smooth],
    }
    (OUT / "rocker_topo.json").write_text(json.dumps(res))
    np.save(OUT / "rocker_density.npy", dens)
    print(f"net plate area {area/100:.1f} cm2   "
          f"mass {mass:.0f} g at {PLATE_T} mm in 7075-T6")
    _plot(d, dens, smooth, L)
    print(f"wrote {OUT/'rocker_topo.json'} and rocker_topo.png")


def _area(p):
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _resample(p, step):
    d = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))]
    n = max(16, int(d[-1] / step))
    t = np.linspace(0, d[-1], n, endpoint=False)
    return np.column_stack([np.interp(t, d, p[:, 0]), np.interp(t, d, p[:, 1])])


def _smooth(p, n):
    for _ in range(n):
        p = 0.25 * np.roll(p, 1, 0) + 0.5 * p + 0.25 * np.roll(p, -1, 0)
    return p


def _plot(d, dens, polys, L):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(14, 5.5))
    X, Y = d.elem_centres()
    ax[0].pcolormesh(X, Y, 1 - dens, cmap="bone", shading="auto")
    ax[0].set_title("SIMP density field")
    for p in polys:
        ax[1].fill(p[:, 0], p[:, 1], color="#c9d4de" if p is polys[0] else "w",
                   ec="#1b2a38", lw=1.6, zorder=2)
    for name, (px, py) in (("pivot", PIVOT), ("pushrod", PUSHROD),
                           ("damper", DAMPER)):
        for a in ax:
            a.add_patch(plt.Circle((px, py), BORE_R, fc="w", ec="#1b2a38",
                                   lw=1.4, zorder=3))
        ax[1].annotate(name, (px, py), textcoords="offset points",
                       xytext=(0, BOSS_R + 6), ha="center", fontsize=9, zorder=4)
    ax[1].set_title(f"extracted profile - {L['BUMP']:,.0f} N pushrod (bump)")
    for a in ax:
        a.set_aspect("equal")
        a.set_xlim(d.x0, d.x0 + d.nelx * d.h)
        a.set_ylim(d.y0, d.y0 + d.nely * d.h)
    fig.suptitle("ARC-1 inboard rocker - topology optimisation", fontsize=13)
    fig.tight_layout()
    fig.savefig(OUT / "rocker_topo.png", dpi=130)


if __name__ == "__main__":
    main()
