"""One periodic TPMS calibration sample: lattice-Boltzmann flow and H1 heat transfer.

A sample is one unit cell of one fluid network (side A of the sheet; the two networks
of gyroid, diamond and primitive sheets are congruent), periodic in all directions,
driven along x.  The result is a measurement row in exactly the format
``aces calibrate`` reads, so CFD and rig tests enter the registry the same way:

    source, tpms, cell_m, wall_ratio, fluid, T_K, p_Pa, mass_flux_kg_m2s, dpdx_Pa_m, h_W_m2K, boundary

Definitions match the design model so the fitted factor is the ratio the model needs:
  * dpdx is the mean pressure gradient (pressure drop per metre of core);
  * mass flux is per frontal area of the core (rho x superficial velocity);
  * h = heat taken up per cell / (ACES wall area x (T_wall - T_bulk)), with the wall area
    measured on the level set by ``tpms.unit_cell``; H1 condition (wall temperature uniform
    around the periphery, rising linearly along the flow), thermally developed.  Every row
    says so (``boundary: uniform_heat_flux``), and ``aces calibrate`` fits its Nusselt
    factors against the fully developed uniform-heat-flux baseline (4.36).

Similarity: the lattice runs at the target Reynolds number (ACES definition, interstitial
velocity and level-set hydraulic diameter) and Prandtl number; physical units come from the
cell size and CoolProp properties at (T, p).  Laminar and steady only: a case that does not
settle, or exceeds the Mach and grid-Peclet limits, is reported as not converged and is not a
measurement.
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from .. import fluids
from ..domains.hx import tpms
from . import lbm

MACH_LIMIT = 0.15  # sqrt(3) * u_max; compressibility error ~ Ma^2
TAU_MIN = 0.505
BOUNDARY = "uniform_heat_flux"  # H1: the thermal boundary every sample's h is measured under


@dataclass
class Sample:
    tpms: str
    wall_ratio: float
    cell_m: float
    fluid: str
    T_K: float
    p_Pa: float
    re_target: float
    n: int = 48  # voxels per cell edge
    side: int = 0
    thermal: bool = True
    u_lattice: float = 0.025  # target interstitial velocity, lattice units (peaks run ~3x higher)
    re_tolerance: float = 0.03
    source: str = ""


@dataclass
class SampleResult:
    measurement: dict | None  # None when the case is not a valid measurement
    diagnostics: dict = field(default_factory=dict)
    problems: list = field(default_factory=list)  # why the case is not a measurement
    notes: list = field(default_factory=list)  # a valid measurement with something missing (e.g. no h)


def voxel_mask(kind: str, ratio: float, n: int, side: int = 0) -> np.ndarray:
    c = (np.arange(n) + 0.5) / n
    x, y, z = np.meshgrid(c, c, c, indexing="ij")
    d = tpms.signed_distance(kind, x, y, z)
    half = 0.5 * ratio
    return d > half if side == 0 else d < -half


def _percolates_x(fl: np.ndarray) -> bool:
    from scipy import ndimage

    tiled = np.concatenate([fl, fl], axis=0)
    lab, _ = ndimage.label(tiled)
    a, b = set(np.unique(lab[0][fl[0]])), set(np.unique(lab[-1][fl[-1]]))
    return bool(a & b - {0})


def _geometry(s: Sample) -> tuple[tpms.UnitCell, np.ndarray]:
    """Level-set measurements and the voxel mask of the sample's cell.

    Private seam: the tests swap in a plane channel here to check the unit path against an exact solution.
    """
    return tpms.unit_cell(s.tpms, s.wall_ratio), voxel_mask(s.tpms, s.wall_ratio, s.n, s.side)


def run(s: Sample, *, xp=np, max_steps: int = 400_000, log=None) -> SampleResult:
    """Solve one sample.  ``max_steps`` bounds each flow solve; up to 8 flow solves (the Re iteration) and one
    thermal solve of up to 4 x ``max_steps`` run per sample."""
    t0 = time.time()
    problems: list[str] = []
    notes: list[str] = []
    if s.n < 8:
        raise ValueError("n must be at least 8 voxels per cell")
    uc, fl = _geometry(s)
    phi = uc.fluid_fraction[s.side]
    dh_a = uc.hydraulic_diameter_over_a(s.side)
    ad = uc.area_density_a[s.side]
    phi_vox = float(fl.mean())
    wall_vox = 0.5 * s.wall_ratio * s.n * 2
    diag: dict[str, Any] = {"n": s.n, "phi_levelset": phi, "phi_voxel": phi_vox, "dh_over_a": dh_a,
                            "wall_voxels": wall_vox}
    if wall_vox < 2.0:
        problems.append(f"wall spans {wall_vox:.1f} voxels at n={s.n}; need at least 2")
    if abs(phi_vox - phi) / phi > 0.05:
        problems.append(f"voxel porosity {phi_vox:.3f} differs from the level set's {phi:.3f} by more than 5 %; "
                        f"refine n")
    if not _percolates_x(fl):
        problems.append("fluid network does not connect across the cell along x")
    if problems:
        return SampleResult(None, diag, problems)

    dh = dh_a * s.n  # lattice units
    nu = s.u_lattice * dh / s.re_target
    nu = min(nu, 1.0 / 6.0)  # tau <= 1: low Re cases slow down rather than diffuse faster than they converge
    tau = 3.0 * nu + 0.5
    diag.update({"nu_lattice": nu, "tau": tau, "dh_lattice": dh})
    if tau < TAU_MIN:
        return SampleResult(None, diag, [f"Re {s.re_target:g} needs tau {tau:.4f} < {TAU_MIN} at n={s.n}; refine n"])

    def re_of(fr: lbm.FlowResult) -> float:
        return (fr.u_superficial / phi) * dh / nu  # ACES definition: interstitial = superficial / phi

    # Force from the laminar baseline, then secant iterations on the achieved Re (warm-started).
    u_t = s.re_target * nu / dh
    g = 64.0 / s.re_target * 0.5 * u_t * u_t / dh
    f0, hist = None, []
    for it in range(8):
        if it:  # next force from the last two solves (secant), or proportionally after the first
            if len(hist) >= 2 and hist[-1][1] != hist[-2][1]:
                (g1, r1, *_), (g2, r2, *_) = hist[-2], hist[-1]
                g_new = g2 + (s.re_target - r2) * (g2 - g1) / (r2 - r1)
                g = g_new if g_new > 0 else g2 * s.re_target / r2
            else:
                g = hist[-1][0] * s.re_target / hist[-1][1]
        fr, f0 = lbm.solve_flow(fl, nu, g, xp=xp, f0=f0, max_steps=max_steps)
        re = re_of(fr)
        hist.append((g, re, fr.steps, fr.converged))
        if log:
            log(f"  flow it {it}: g={g:.4e} Re={re:.4g} steps={fr.steps} converged={fr.converged}")
        if not fr.converged or not math.isfinite(re) or abs(re - s.re_target) / s.re_target < s.re_tolerance:
            break
    g = hist[-1][0]  # the force of the field we report, never an untried update
    diag.update({"flow_iterations": hist, "flow_steps": sum(h[2] for h in hist), "flow_converged": fr.converged,
                 "re_achieved": re, "mach": fr.mach, "u_superficial_lattice": fr.u_superficial,
                 "force_lattice": g})
    if not fr.converged:
        problems.append(f"flow did not reach a steady state in {max_steps} steps (unsteady or under-resolved)")
    if fr.mach > MACH_LIMIT:
        problems.append(f"Mach {fr.mach:.3f} exceeds {MACH_LIMIT}; lower u_lattice")
    if abs(re - s.re_target) / s.re_target > 3 * s.re_tolerance:
        problems.append(f"achieved Re {re:.4g} is far from the target {s.re_target:g}")

    # Physical units: dx from the cell, dt from the viscosity.
    st = fluids.state(s.fluid, s.T_K, s.p_Pa)
    dx = s.cell_m / s.n
    nu_phys = st.mu / st.rho
    dt = nu * dx * dx / nu_phys
    u_sup = fr.u_superficial * dx / dt
    dpdx = g * st.rho * dx / (dt * dt)
    meas: dict[str, Any] = {"source": s.source, "tpms": s.tpms, "cell_m": s.cell_m, "wall_ratio": s.wall_ratio,
                            "fluid": s.fluid, "T_K": s.T_K, "p_Pa": s.p_Pa,
                            "mass_flux_kg_m2s": st.rho * u_sup, "dpdx_Pa_m": dpdx, "h_W_m2K": None,
                            "boundary": BOUNDARY}

    if s.thermal and not problems:
        alpha = nu / st.pr
        try:
            sc = lbm.solve_scalar(fl, fr.u, alpha, xp=xp, max_steps=4 * max_steps)
            area = ad * s.n * s.n  # ACES wall area of one cell, lattice units
            nu_h1 = lbm.nusselt_h1(fl, fr, sc, area, dh)
            diag.update({"thermal_steps": sc.steps, "thermal_converged": sc.converged, "nusselt_h1": nu_h1,
                         "pr": st.pr, "grid_peclet": fr.u_max / alpha})
            if sc.converged and math.isfinite(nu_h1) and nu_h1 > 0:
                meas["h_W_m2K"] = nu_h1 * st.k / (dh_a * s.cell_m)
            else:
                notes.append("heat transfer did not converge; h not measured")
        except ValueError as e:
            diag["thermal_refused"] = str(e)
            notes.append(f"h not measured: {e}")
    diag["wall_s"] = round(time.time() - t0, 1)
    return SampleResult(None if problems else meas, diag, problems, notes)


def as_dict(r: SampleResult) -> dict:
    d = asdict(r)
    d["diagnostics"] = {k: v for k, v in d["diagnostics"].items()}
    return d
