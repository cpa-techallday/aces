"""Periodic lattice-Boltzmann solvers for calibration samples (FP64, numpy or CuPy).

Two steady problems on a voxel mask that is periodic in all three directions:

``solve_flow``    incompressible laminar flow driven by a uniform body force along x
                  (the periodic stand-in for a mean pressure gradient).  D3Q19, two-
                  relaxation-time (TRT) collision with the magic parameter 3/16, so the
                  half-way bounce-back wall sits exactly midway between a fluid and a
                  solid node and the permeability does not depend on viscosity
                  (Ginzburg 2008).  Guo forcing, split into its even and odd parts.

``solve_scalar``  thermally developed heat transfer in the same field, H1 condition: the
                  wall temperature rises linearly along x and is uniform around the
                  periphery.  With T = gamma*x + theta the periodic part solves
                      u . grad(theta) = alpha lap(theta) - gamma u_x,   theta = 0 on walls
                  D3Q7 TRT advection-diffusion, anti-bounce-back Dirichlet walls.

Everything is in lattice units (dx = dt = 1, rho = 1).  ``aces.cfd.periodic`` maps
results to physical units.  Exact references used by the tests (all H1 / fully
developed, Shah & London 1978):

  plane channel   f Re = 96,     Nu = 8.235   (Dh = 2 x gap)
  square duct     f Re = 56.91,  Nu = 3.608   (Dh = side)

FP64 throughout: these are verification solves (simlab rule).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# D3Q19 -----------------------------------------------------------------------------
C19 = np.array([(0, 0, 0),
                (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1),
                (1, 1, 0), (-1, -1, 0), (1, -1, 0), (-1, 1, 0),
                (1, 0, 1), (-1, 0, -1), (1, 0, -1), (-1, 0, 1),
                (0, 1, 1), (0, -1, -1), (0, 1, -1), (0, -1, 1)], dtype=int)
W19 = np.array([1 / 3] + [1 / 18] * 6 + [1 / 36] * 12)
OPP19 = np.array([int(np.where((C19 == -c).all(axis=1))[0][0]) for c in C19])

# D3Q7 ------------------------------------------------------------------------------
C7 = np.array([(0, 0, 0), (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)], dtype=int)
W7 = np.array([1 / 4] + [1 / 8] * 6)
OPP7 = np.array([0, 2, 1, 4, 3, 6, 5])
CS2_7 = 0.25

MAGIC_FLOW = 3.0 / 16.0
MAGIC_SCALAR = 1.0 / 4.0


def backend(prefer_gpu: bool = False):
    """numpy, or CuPy when asked for and importable with a visible device."""
    if prefer_gpu:
        try:
            import cupy as cp

            cp.cuda.runtime.getDeviceCount()
            return cp
        except Exception:
            pass
    return np


def _asnp(xp, a):
    return a.get() if hasattr(a, "get") else np.asarray(a)


def _roll(xp, a, c):
    return xp.roll(a, shift=(int(c[0]), int(c[1]), int(c[2])), axis=(0, 1, 2))


@dataclass
class FlowResult:
    u: object  # (3, nx, ny, nz), zero in solid
    steps: int
    converged: bool
    u_superficial: float  # mean u_x over the whole box
    u_interstitial: float  # mean u_x over fluid nodes
    u_max: float  # largest |u| anywhere (Mach check)
    nu: float
    force: float
    history: list = field(default_factory=list)

    @property
    def mach(self) -> float:
        return self.u_max * math.sqrt(3.0)


def solve_flow(fluid, nu: float, force: float, *, xp=np, tol: float = 1e-10, field_tol: float = 1e-8,
               check_every: int = 200, max_steps: int = 400_000, f0=None) -> tuple[FlowResult, object]:
    """Steady periodic flow through ``fluid`` (bool array) under body force ``force`` along +x.

    Converged when, per step and averaged over ``check_every`` steps, the mean velocity changes by
    less than ``tol`` (relative) AND no velocity component anywhere changes by more than
    ``field_tol`` of the peak speed: a flux that has settled while the field still moves (an
    unsteady flow) is not a steady state.  Returns the result and the final populations (to
    warm-start a neighbouring case).
    """
    if not 0.0 < nu:
        raise ValueError("viscosity must be positive")
    tau_p = 3.0 * nu + 0.5
    tau_m = 0.5 + MAGIC_FLOW / (tau_p - 0.5)
    wp, wm = 1.0 / tau_p, 1.0 / tau_m
    fl = xp.asarray(fluid, dtype=bool)
    if not bool(fl.any()):
        raise ValueError("no fluid nodes")
    solid = ~fl
    nfl = int(fl.sum())
    shape = fl.shape
    c = C19
    w = xp.asarray(W19).reshape(19, 1, 1, 1)
    cx = xp.asarray(c[:, 0].astype(float)).reshape(19, 1, 1, 1)
    cy = xp.asarray(c[:, 1].astype(float)).reshape(19, 1, 1, 1)
    cz = xp.asarray(c[:, 2].astype(float)).reshape(19, 1, 1, 1)
    bb = [fl & _roll(xp, solid, c[i]) for i in range(19)]  # fluid node whose upstream neighbour is solid
    F = xp.zeros((3,) + shape)
    F[0] = xp.where(fl, force, 0.0)

    f = xp.asarray(f0) if f0 is not None else w * xp.ones((19,) + shape)
    f = f * fl  # solid nodes carry nothing
    hist, prev, steps, converged = [], None, 0, False
    while steps < max_steps:
        rho = f.sum(axis=0)
        rho_safe = xp.where(fl, rho, 1.0)
        ux = ((f * cx).sum(axis=0) + 0.5 * F[0]) / rho_safe
        uy = ((f * cy).sum(axis=0) + 0.5 * F[1]) / rho_safe
        uz = ((f * cz).sum(axis=0) + 0.5 * F[2]) / rho_safe
        ux, uy, uz = ux * fl, uy * fl, uz * fl
        cu = cx * ux + cy * uy + cz * uz
        usq = ux * ux + uy * uy + uz * uz
        feq_p = w * rho_safe * (1.0 + 4.5 * cu * cu - 1.5 * usq)
        feq_m = w * rho_safe * 3.0 * cu
        cF = cx * F[0] + cy * F[1] + cz * F[2]
        uF = ux * F[0] + uy * F[1] + uz * F[2]
        s_p = w * (9.0 * cu * cF - 3.0 * uF)
        s_m = w * 3.0 * cF
        f_opp = f[OPP19]
        f_p = 0.5 * (f + f_opp)
        f_m = 0.5 * (f - f_opp)
        fs = f - wp * (f_p - feq_p) - wm * (f_m - feq_m) + (1 - 0.5 * wp) * s_p + (1 - 0.5 * wm) * s_m
        fs = fs * fl
        fn = xp.empty_like(fs)
        for i in range(19):
            fn[i] = _roll(xp, fs[i], c[i])
            fn[i] = xp.where(bb[i], fs[OPP19[i]], fn[i])
        f = fn * fl
        steps += 1
        if steps % check_every == 0:
            U = float(_asnp(xp, ux.sum())) / nfl
            hist.append((steps, U))
            if not math.isfinite(U):
                break
            cur = xp.stack([ux, uy, uz])
            if prev is not None and abs(U) > 0 and abs(U - prev[0]) / abs(U) < tol * check_every:
                peak = float(_asnp(xp, xp.abs(cur).max()))
                change = float(_asnp(xp, xp.abs(cur - prev[1]).max()))
                if peak > 0 and change / peak < field_tol * check_every:
                    converged = True
                    break
            prev = (U, cur)
    umag = xp.sqrt(ux * ux + uy * uy + uz * uz)
    u = xp.stack([ux, uy, uz])
    ui = float(_asnp(xp, ux.sum())) / nfl
    res = FlowResult(u=u, steps=steps, converged=converged, u_superficial=ui * nfl / fl.size,
                     u_interstitial=ui, u_max=float(_asnp(xp, umag.max())), nu=nu, force=force, history=hist)
    return res, f


@dataclass
class ScalarResult:
    theta: object
    steps: int
    converged: bool
    theta_bulk: float  # mixing-cup mean of theta (flux weighted)
    alpha: float
    gamma: float
    velocity_scale: float
    history: list = field(default_factory=list)


def solve_scalar(fluid, u, alpha: float, gamma: float = 1e-3, *, xp=np, tol: float = 1e-10,
                 check_every: int = 200, max_steps: int = 2_000_000, tau_max: float = 1.0, tau_min: float = 0.505,
                 u_lattice_max: float = 0.1) -> ScalarResult:
    """H1 thermally developed temperature in the steady field ``u`` (lattice units).

    The equation is linear and fixed by the Peclet number, so the solve uses its own scale:
    velocity and diffusivity are multiplied by the same factor ``s``.  ``s`` is chosen so the
    fastest velocity is at most ``u_lattice_max`` (the D3Q7 advection limit) and the scalar
    relaxation time at most ``tau_max``.  When the grid Peclet number u_max / alpha is so high
    that the relaxation time would fall below ``tau_min``, the mesh cannot resolve the thermal
    boundary layers and the call is refused.
    """
    fl = xp.asarray(fluid, dtype=bool)
    nfl = int(fl.sum())
    umax = float(_asnp(xp, xp.sqrt((u * u).sum(axis=0)).max()))
    alpha_t = min(CS2_7 * (tau_max - 0.5), u_lattice_max * alpha / umax) if umax > 0 else CS2_7 * (tau_max - 0.5)
    tau_target = 0.5 + alpha_t / CS2_7
    if tau_target < tau_min:
        raise ValueError(f"grid Peclet number u_max/alpha = {umax / alpha:.1f} is too high for this mesh "
                         f"(limit {u_lattice_max / (CS2_7 * (tau_min - 0.5)):.0f}); refine the voxels")
    s = alpha_t / alpha
    ut = u * s
    # multiplying the whole equation by s scales u and alpha; the source -gamma*u_x scales with u
    # already, so gamma itself is unchanged and so is theta
    tau_m = tau_target  # diffusion is set by the odd relaxation
    tau_p = 0.5 + MAGIC_SCALAR / (tau_m - 0.5)
    wp, wm = 1.0 / tau_p, 1.0 / tau_m
    c = C7
    w = xp.asarray(W7).reshape(7, 1, 1, 1)
    cx = xp.asarray(c[:, 0].astype(float)).reshape(7, 1, 1, 1)
    cy = xp.asarray(c[:, 1].astype(float)).reshape(7, 1, 1, 1)
    cz = xp.asarray(c[:, 2].astype(float)).reshape(7, 1, 1, 1)
    solid = ~fl
    bb = [fl & _roll(xp, solid, c[i]) for i in range(7)]
    cu = (cx * ut[0] + cy * ut[1] + cz * ut[2]) / CS2_7
    q = -gamma * ut[0] * fl  # source per node per step
    g = xp.zeros((7,) + fl.shape)
    hist, prev, steps, converged = [], None, 0, False
    ux_sum = float(_asnp(xp, (ut[0] * fl).sum()))
    while steps < max_steps:
        th = g.sum(axis=0) + 0.5 * q  # second-order source correction
        geq_p = w * th
        geq_m = w * th * cu
        g_opp = g[OPP7]
        g_p = 0.5 * (g + g_opp)
        g_m = 0.5 * (g - g_opp)
        gs = g - wp * (g_p - geq_p) - wm * (g_m - geq_m) + (1 - 0.5 * wp) * w * q
        gs = gs * fl
        gn = xp.empty_like(gs)
        for i in range(7):
            gn[i] = _roll(xp, gs[i], c[i])
            gn[i] = xp.where(bb[i], -gs[OPP7[i]], gn[i])  # anti-bounce-back: theta_wall = 0
        g = gn * fl
        steps += 1
        if steps % check_every == 0:
            tb = float(_asnp(xp, (ut[0] * th * fl).sum())) / ux_sum
            hist.append((steps, tb))
            if not math.isfinite(tb):
                break
            if prev is not None and abs(tb) > 0 and abs(tb - prev[0]) / abs(tb) < tol * check_every:
                peak = float(_asnp(xp, xp.abs(th).max()))
                change = float(_asnp(xp, xp.abs(th - prev[1]).max()))
                if peak > 0 and change / peak < 1e-8 * check_every:
                    converged = True
                    break
            prev = (tb, th * fl)
    th = (g.sum(axis=0) + 0.5 * q) * fl
    tb = float(_asnp(xp, (ut[0] * th).sum())) / ux_sum
    return ScalarResult(theta=th, steps=steps, converged=converged, theta_bulk=tb, alpha=alpha, gamma=gamma,
                        velocity_scale=s, history=hist)


def nusselt_h1(fluid, flow: FlowResult, scalar: ScalarResult, area: float, dh: float) -> float:
    """Nu = h Dh / k with h = (heat taken up per cell) / (wall area x (T_wall - T_bulk)).

    Per unit time the flow takes up rho cp gamma U_superficial V (all of it crossing the
    walls), so h = gamma U_superficial V / (area (0 - theta_bulk)) with rho = cp = 1, and k = alpha.
    ``area`` and ``dh`` are in lattice units; pass the definitions the consuming model uses.
    """
    vol = float(np.asarray(fluid).size)
    h = scalar.gamma * flow.u_superficial * vol / (area * (-scalar.theta_bulk))
    return h * dh / scalar.alpha
