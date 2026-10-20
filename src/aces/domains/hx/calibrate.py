"""Fit the hx calibration factors from CFD or rig measurements.

Each measurement is one periodic sample (or one test) of a TPMS core.  The
dimensionless groups are computed here, with the same definitions the model uses,
so a fitted factor is the ratio the model actually needs:

  Dh, phi    measured on the level set for (tpms, wall_ratio), times cell_m
  u          interstitial velocity  = mass_flux / (rho * phi)   (mass_flux per frontal area)
  Re         rho * u * Dh / mu
  f_meas     dpdx * Dh / (0.5 * rho * u^2)      (dpdx per metre of CORE length)
  Nu_meas    h * Dh / k

  hx.<kind>.friction  = f_meas / f_baseline(Re)       (includes tortuosity, see below)
  hx.<kind>.nusselt   = Nu_meas / Nu_baseline(Re, Pr)   (FULLY DEVELOPED baseline, see below)
  hx.<kind>.tortuosity = 1.0, with the same sources: because dpdx is per metre of core,
                         the measured friction already contains the path-length effect.

Fluid properties come from CoolProp at the measurement's temperature and pressure.
Dh and porosity are measured on the level set at the model's resolution (64 by
default), which is recorded in each factor's basis.

A constant factor is fitted as the geometric mean of the ratios, one value per
operating condition (repeated rows of a condition are averaged first). When the ratios
trend with Reynolds number (inertia in a tortuous core does that), a power law
value * (Re / re_ref) ** b or an inertial-onset form is considered. A more complex
form must reduce the small-sample-corrected AIC by at least 2 when the constant is
already within the scatter limit; otherwise the constant is kept.
Rules:
  * points where the baseline correlation is outside its validity (the 2300-3000
    transition blend, or above the turbulent fit's range) are excluded and listed;
  * ``min_points`` counts distinct operating conditions, so repeated rows of one
    condition cannot make a fit look better supported than it is;
  * if the ratios scatter more than ``max_scatter`` (sample standard deviation of
    the log-ratio) a constant cannot represent them and the fit is refused, rather
    than writing a misleading factor.

The Nusselt baseline is the fully developed one (3.66 or 4.36 in laminar flow), never the
thermal-entry mean the design model uses: periodic CFD samples are thermally developed by
construction, so the factor is "periodic TPMS / fully developed duct".  The model then
multiplies its developing-flow baseline by that factor.

Thermal boundary.  A Nusselt ratio means nothing without the boundary condition h was
measured under, so every row with h carries ``boundary`` (``aces-tpms-periodic`` rows are H1:
``uniform_heat_flux``).  ``fit`` takes the boundary from the rows.  It refuses rows with mixed
boundaries and rows that disagree with an explicit ``boundary``; rows with h but no boundary
can be fitted only when ``boundary`` is given, otherwise the Nusselt factors are refused and
friction is still fitted.

Measurement file: CSV or JSON list with these columns
  source, tpms, cell_m, wall_ratio, fluid, T_K, p_Pa, mass_flux_kg_m2s, dpdx_Pa_m, h_W_m2K,
  boundary, evidence_sha256
(dpdx_Pa_m or h_W_m2K may be blank when a sample measured only one of them; boundary is
uniform_heat_flux or uniform_wall_temperature; evidence_sha256, written by ``aces cfd-collect``,
is the sha256 of the simlab run's evidence.json).
"""
from __future__ import annotations

import csv
import datetime as _dt
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, field_validator

from ... import correlations as corr
from ... import fluids
from ...calibration import Factor, Registry
from . import tpms


class Measurement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=3, description="simlab run id, test id or report reference")
    tpms: str
    cell_m: PositiveFloat
    wall_ratio: float = Field(gt=0.0, lt=1.0)
    fluid: str
    T_K: PositiveFloat
    p_Pa: PositiveFloat
    mass_flux_kg_m2s: PositiveFloat
    dpdx_Pa_m: PositiveFloat | None = None
    h_W_m2K: PositiveFloat | None = None
    boundary: Literal["uniform_heat_flux", "uniform_wall_temperature"] | None = Field(
        None, description="thermal boundary h was measured under (H1 = uniform_heat_flux)")
    evidence_sha256: str | None = Field(None, pattern=r"^[0-9a-f]{64}$",
                                        description="sha256 of the simlab run's evidence.json")

    @field_validator("tpms")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in tpms.KINDS:
            raise ValueError(f"tpms must be one of {tpms.KINDS}")
        return v


@dataclass
class Point:
    m: Measurement
    re: float
    pr: float
    f_ratio: float | None
    nu_ratio: float | None
    f_regime: str = ""
    nu_regime: str = ""
    f_valid: bool = True
    nu_valid: bool = True

    @property
    def condition(self) -> tuple:
        """One operating condition (to 6 significant figures); repeated rows of it count once."""
        m = self.m
        return (m.tpms, m.fluid) + tuple(float(f"{x:.6g}") for x in
                                         (m.cell_m, m.wall_ratio, m.T_K, m.p_Pa, m.mass_flux_kg_m2s))


def load(path: str | Path) -> list[Measurement]:
    p = Path(path)
    if p.suffix.lower() == ".json":
        rows = json.loads(p.read_text())
    else:
        with p.open(newline="") as fh:
            rows = [{k: (v if v not in ("", None) else None) for k, v in r.items()} for r in csv.DictReader(fh)]
    out, problems = [], []
    for i, r in enumerate(rows):
        try:
            out.append(Measurement.model_validate(r))
        except Exception as e:  # pydantic ValidationError
            problems.append(f"row {i + 1}: {e}")
    if problems:
        raise ValueError("measurement file has invalid rows:\n  " + "\n  ".join(problems))
    if not out:
        raise ValueError(f"{p} contains no measurements")
    return out


def reduce(m: Measurement, boundary: str, side: int = 0, resolution: int = 64) -> Point:
    """Dimensionless groups for one measurement, on the model's own definitions."""
    uc = tpms.unit_cell(m.tpms, m.wall_ratio, resolution)
    phi = uc.fluid_fraction[side]
    dh = uc.hydraulic_diameter_over_a(side) * m.cell_m
    st = fluids.state(m.fluid, m.T_K, m.p_Pa)
    u = m.mass_flux_kg_m2s / (st.rho * phi)
    re = st.rho * u * dh / st.mu
    pt = Point(m, re, st.pr, None, None)
    if m.dpdx_Pa_m is not None:
        f_meas = m.dpdx_Pa_m * dh / (0.5 * st.rho * u * u)
        c = corr.friction_factor_darcy(re)
        pt.f_ratio, pt.f_regime, pt.f_valid = f_meas / c.value, c.regime, c.valid
    if m.h_W_m2K is not None:
        nu_meas = m.h_W_m2K * dh / st.k
        c = corr.nusselt(re, st.pr, boundary)  # fully developed on purpose: no length, see the module docstring
        pt.nu_ratio, pt.nu_regime, pt.nu_valid = nu_meas / c.value, c.regime, c.valid
    return pt


def _fit(ratios: list[float]) -> tuple[float, float]:
    """Geometric mean and the sample standard deviation (n - 1) of the log-ratios."""
    logs = [math.log(r) for r in ratios]
    mean = sum(logs) / len(logs)
    sd = math.sqrt(sum((x - mean) ** 2 for x in logs) / (len(logs) - 1)) if len(logs) > 1 else 0.0
    return math.exp(mean), sd


def _aicc(residual_sd: float, n: int, parameters: int) -> float:
    """Small-sample corrected AIC for log residuals; ``residual_sd`` uses n - parameters dof."""
    if n <= parameters + 1:
        return math.inf
    rss = residual_sd * residual_sd * (n - parameters)
    if rss == 0.0:
        return -math.inf
    return n * math.log(rss / n) + 2 * parameters + 2 * parameters * (parameters + 1) / (n - parameters - 1)


def _re_levels(re: list[float], spread: float = 1.25) -> int:
    """Number of distinct Re levels: values within ``spread`` of the level's lowest count as one.

    Achieved Re scatters a few percent around each target, so raw distinct values would let two
    levels pass for many."""
    levels, lo = 0, None
    for r in sorted(re):
        if lo is None or r > lo * spread:
            levels, lo = levels + 1, r
    return levels


def _fit_power(re: list[float], ratios: list[float]) -> tuple[float, float, float, float] | None:
    """ratio = value * (Re / re_ref) ** b by least squares in logs.

    Returns (value, b, re_ref, residual sd with n - 2 degrees of freedom), or None with fewer than
    three distinct Re or less than a 1.5x spread in Re.
    """
    xs = [math.log(r) for r in re]
    if _re_levels(re) < 3:
        return None
    ys = [math.log(r) for r in ratios]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    res = [y - (my + b * (x - mx)) for x, y in zip(xs, ys)]
    sd = math.sqrt(sum(r * r for r in res) / (n - 2)) if n > 2 else 0.0
    return math.exp(my), b, math.exp(mx), sd


def _fit_onset(re: list[float], ratios: list[float]) -> tuple[float, float, float, float] | None:
    """ratio = value * (1 + (Re / re_onset) ** n): flat in creeping flow, rising once inertia matters.

    Least squares in logs; returns (value, n, re_onset, residual sd with n_points - 3 degrees of
    freedom), or None with fewer than four distinct Re.
    """
    from scipy.optimize import least_squares

    if _re_levels(re) < 4:
        return None
    x = np.log(np.asarray(re, float))
    y = np.log(np.asarray(ratios, float))

    def resid(q):
        la, lrc, n = q
        return y - (la + np.log1p(np.exp(n * (x - lrc))))

    lo, hi = float(x.min()), float(x.max())
    best = None
    for lrc0 in np.linspace(lo, hi, 5):
        r = least_squares(resid, [float(y.min()), lrc0, 1.0],
                          bounds=([-10.0, lo - math.log(10.0), 0.3], [10.0, hi + math.log(10.0), 4.0]))
        if best is None or r.cost < best.cost:
            best = r
    dof = len(x) - 3
    sd = math.sqrt(float((best.fun ** 2).sum()) / dof) if dof > 0 else 0.0
    la, lrc, n = best.x
    return math.exp(la), float(n), math.exp(lrc), sd


def thermal_boundary(measurements: list[Measurement], boundary: str | None = None) -> tuple[str | None, str | None]:
    """The boundary the Nusselt rows were measured under: (boundary, None), or (None, why Nu can't be fitted).

    Raises ValueError for rows with mixed boundaries, or rows that disagree with an explicit ``boundary``.
    """
    if boundary is not None and boundary not in corr.NU_LAMINAR_FD:
        raise ValueError(f"unknown boundary {boundary!r}; choose from {sorted(corr.NU_LAMINAR_FD)}")
    heat = [m for m in measurements if m.h_W_m2K is not None]
    declared = sorted({m.boundary for m in heat if m.boundary})
    if len(declared) > 1:
        raise ValueError(f"measurement rows have mixed thermal boundaries ({', '.join(declared)}); "
                         f"calibrate each boundary from its own file")
    if declared and boundary is not None and declared[0] != boundary:
        raise ValueError(f"measurement rows were measured under {declared[0]} but --boundary says {boundary}; "
                         f"they disagree, so the Nusselt factors would be filed under the wrong boundary")
    unlabelled = [m.source for m in heat if not m.boundary]
    if boundary is not None:
        return boundary, None
    if unlabelled:
        return None, (f"{len(unlabelled)} row(s) with h carry no boundary (e.g. {unlabelled[0]}); give --boundary "
                      f"for the condition they were measured under")
    return (declared[0] if declared else None), None


def fit(
    measurements: list[Measurement],
    *,
    boundary: str | None = None,
    min_points: int = 3,
    max_scatter: float = 0.15,
    resolution: int = 64,
) -> tuple[list[Factor], list[dict[str, Any]], list[str]]:
    """Return (factors, per-kind summary rows, refusals).

    ``boundary`` is needed only for rows with h that carry no ``boundary`` of their own (see the module docstring).
    """
    if min_points < 2:
        raise ValueError("min_points must be at least 2: one condition cannot show whether a constant factor holds")
    bc, no_bc = thermal_boundary(measurements, boundary)
    today = _dt.date.today().isoformat()
    # rows without h never use the boundary; the placeholder keeps reduce's signature
    points = [reduce(m, bc or "uniform_wall_temperature", resolution=resolution) for m in measurements]
    factors, summary, refused = [], [], []
    for kind in sorted({p.m.tpms for p in points}):
        pk = [p for p in points if p.m.tpms == kind]
        for qty, attr, key, valid_attr, regime_attr in (("friction", "f_ratio", "friction", "f_valid", "f_regime"),
                                                         ("nusselt", "nu_ratio", "nusselt", "nu_valid", "nu_regime")):
            have = [p for p in pk if getattr(p, attr) is not None]
            if not have:
                continue
            if key == "nusselt" and bc is None:
                refused.append(f"hx.{kind}.nusselt: {no_bc}")
                summary.append({"key": f"hx.{kind}.nusselt", "points": 0, "conditions": 0, "excluded": [],
                                "status": "refused: no thermal boundary"})
                continue
            use = [p for p in have if getattr(p, valid_attr)]
            excluded = [p for p in have if not getattr(p, valid_attr)]
            conditions = len({p.condition for p in use})
            row = {"key": f"hx.{kind}.{key}", "points": len(use), "conditions": conditions,
                   "excluded": [{"source": p.m.source, "reynolds": p.re, "regime": getattr(p, regime_attr)}
                                for p in excluded]}
            if key == "nusselt":
                row["boundary"] = bc
            if conditions < min_points:
                refused.append(f"hx.{kind}.{key}: {conditions} distinct condition(s) ({len(use)} usable row(s)), "
                               f"need at least {min_points}")
                summary.append({**row, "status": "refused: too few conditions"})
                continue
            # one value per condition (geometric mean of its rows), so repeats cannot shrink the scatter
            groups: dict[tuple, list[Point]] = {}
            for p in use:
                groups.setdefault(p.condition, []).append(p)
            per_condition = [math.exp(sum(math.log(getattr(p, attr)) for p in g) / len(g)) for g in groups.values()]
            re_condition = [math.exp(sum(math.log(p.re) for p in g) / len(g)) for g in groups.values()]
            value, sd = _fit(per_condition)
            exponent, re_ref, form = 0.0, 0.0, "constant"
            regimes = sorted({getattr(p, regime_attr) for p in use})
            row.update({"value": value, "scatter_sd_log": sd, "regimes": regimes,
                        "reynolds": (min(p.re for p in use), max(p.re for p in use)),
                        "wall_ratio": (min(p.m.wall_ratio for p in use), max(p.m.wall_ratio for p in use))})
            if sd > max_scatter:
                # simplest Re form that represents the data: power law, then inertial onset
                tried = []
                for name, fn in (("power", _fit_power), ("onset", _fit_onset)):
                    q = fn(re_condition, per_condition)
                    tried.append((name, q))
                    if q is not None and q[3] <= max_scatter:
                        value, exponent, re_ref, sd_q = q
                        form = name
                        row.update({"value": value, "re_exponent": exponent, "re_ref": re_ref,
                                    f"scatter_sd_log_{name}": sd_q})
                        break
                else:
                    extra = "".join(f"; {n} in Re leaves {q[3]:.2f}" if q is not None
                                    else f"; too few distinct Re for {n}" for n, q in tried)
                    refused.append(f"hx.{kind}.{key}: ratios scatter {sd:.2f} (sd of log) > {max_scatter:g}{extra}. "
                                   f"Narrow the range (Re, t/a) or add conditions.")
                    summary.append({**row, "status": "refused: scatter"})
                    continue
            else:
                constant_aicc = _aicc(sd, conditions, 1)
                candidates = []
                for name, fn, parameters in (("power", _fit_power, 2), ("onset", _fit_onset, 3)):
                    q = fn(re_condition, per_condition)
                    if q is None or q[3] > max_scatter:
                        continue
                    candidate_aicc = _aicc(q[3], conditions, parameters)
                    if candidate_aicc <= constant_aicc - 2.0:
                        candidates.append((candidate_aicc, name, q))
                if candidates:
                    _, form, q = min(candidates)
                    value, exponent, re_ref, sd_q = q
                    row.update({"value": value, "re_exponent": exponent, "re_ref": re_ref,
                                f"scatter_sd_log_{form}": sd_q})
            row["form"] = form
            sources = tuple(sorted({p.m.source for p in use}))
            vr = {"reynolds": row["reynolds"], "wall_ratio": row["wall_ratio"]}
            if key == "nusselt":
                vr["prandtl"] = (min(p.pr for p in use), max(p.pr for p in use))
            how = {"constant": "geometric mean",
                   "power": f"power law in Re (exponent {exponent:.3f} about Re {re_ref:.3g})",
                   "onset": f"inertial onset in Re (x (1 + (Re/{re_ref:.3g})^{exponent:.2f}))"}[form]
            basis = (f"{qty} baseline x factor; {how} of {len(use)} ratio(s) over {conditions} condition(s); "
                     f"{'/'.join(regimes)} baseline; level-set resolution {resolution}; "
                     + (f"Nu baseline {bc} (fully developed)" if key == "nusselt" else "dpdx per metre of core length"))
            factors.append(Factor(f"hx.{kind}.{key}", value, sources, vr, today, basis, exponent, re_ref,
                                  "" if form == "constant" else form))
            summary.append({**row, "status": "fitted"})
            if key == "friction":
                factors.append(Factor(f"hx.{kind}.tortuosity", 1.0, sources, vr, today,
                                      "folded into the friction factor (dpdx measured per metre of core); "
                                      f"level-set resolution {resolution}"))
    return factors, summary, refused


def update_registry(path: str | Path, factors: list[Factor]) -> Registry:
    p = Path(path)
    reg = Registry.load(p) if p.exists() else Registry(path=p)
    for f in factors:
        reg.set(f)
    reg.save(p)
    return reg
