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
  hx.<kind>.nusselt   = Nu_meas / Nu_baseline(Re, Pr)
  hx.<kind>.tortuosity = 1.0, with the same sources: because dpdx is per metre of core,
                         the measured friction already contains the path-length effect.

Fluid properties come from CoolProp at the measurement's temperature and pressure.
Dh and porosity are measured on the level set at the model's resolution (64 by
default), which is recorded in each factor's basis.

A constant factor is fitted as the geometric mean of the ratios, one value per
operating condition (repeated rows of a condition are averaged first).  Rules:
  * points where the baseline correlation is outside its validity (the 2300-3000
    transition blend, or above the turbulent fit's range) are excluded and listed;
  * ``min_points`` counts distinct operating conditions, so repeated rows of one
    condition cannot make a fit look better supported than it is;
  * if the ratios scatter more than ``max_scatter`` (sample standard deviation of
    the log-ratio) a constant cannot represent them and the fit is refused, rather
    than writing a misleading factor.

Measurement file: CSV or JSON list with these columns
  source, tpms, cell_m, wall_ratio, fluid, T_K, p_Pa, mass_flux_kg_m2s, dpdx_Pa_m, h_W_m2K
(dpdx_Pa_m or h_W_m2K may be blank when a sample measured only one of them).
"""
from __future__ import annotations

import csv
import datetime as _dt
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
        c = corr.nusselt(re, st.pr, boundary)
        pt.nu_ratio, pt.nu_regime, pt.nu_valid = nu_meas / c.value, c.regime, c.valid
    return pt


def _fit(ratios: list[float]) -> tuple[float, float]:
    """Geometric mean and the sample standard deviation (n - 1) of the log-ratios."""
    logs = [math.log(r) for r in ratios]
    mean = sum(logs) / len(logs)
    sd = math.sqrt(sum((x - mean) ** 2 for x in logs) / (len(logs) - 1)) if len(logs) > 1 else 0.0
    return math.exp(mean), sd


def fit(
    measurements: list[Measurement],
    *,
    boundary: str = "uniform_wall_temperature",
    min_points: int = 3,
    max_scatter: float = 0.15,
    resolution: int = 64,
) -> tuple[list[Factor], list[dict[str, Any]], list[str]]:
    """Return (factors, per-kind summary rows, refusals)."""
    if min_points < 2:
        raise ValueError("min_points must be at least 2: one condition cannot show whether a constant factor holds")
    today = _dt.date.today().isoformat()
    points = [reduce(m, boundary, resolution=resolution) for m in measurements]
    factors, summary, refused = [], [], []
    for kind in sorted({p.m.tpms for p in points}):
        pk = [p for p in points if p.m.tpms == kind]
        for qty, attr, key, valid_attr, regime_attr in (("friction", "f_ratio", "friction", "f_valid", "f_regime"),
                                                         ("nusselt", "nu_ratio", "nusselt", "nu_valid", "nu_regime")):
            have = [p for p in pk if getattr(p, attr) is not None]
            if not have:
                continue
            use = [p for p in have if getattr(p, valid_attr)]
            excluded = [p for p in have if not getattr(p, valid_attr)]
            conditions = len({p.condition for p in use})
            row = {"key": f"hx.{kind}.{key}", "points": len(use), "conditions": conditions,
                   "excluded": [{"source": p.m.source, "reynolds": p.re, "regime": getattr(p, regime_attr)}
                                for p in excluded]}
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
            value, sd = _fit(per_condition)
            regimes = sorted({getattr(p, regime_attr) for p in use})
            row.update({"value": value, "scatter_sd_log": sd, "regimes": regimes,
                        "reynolds": (min(p.re for p in use), max(p.re for p in use)),
                        "wall_ratio": (min(p.m.wall_ratio for p in use), max(p.m.wall_ratio for p in use))})
            if sd > max_scatter:
                refused.append(f"hx.{kind}.{key}: ratios scatter {sd:.2f} (sd of log) > {max_scatter:g}; a constant "
                               f"factor does not represent them. Narrow the range or fit a Re-dependent form.")
                summary.append({**row, "status": "refused: scatter"})
                continue
            sources = tuple(sorted({p.m.source for p in use}))
            vr = {"reynolds": row["reynolds"], "wall_ratio": row["wall_ratio"]}
            if key == "nusselt":
                vr["prandtl"] = (min(p.pr for p in use), max(p.pr for p in use))
            basis = (f"{qty} baseline x factor; geometric mean of {len(use)} ratio(s) over {conditions} condition(s); "
                     f"{'/'.join(regimes)} baseline; level-set resolution {resolution}; "
                     + ("Nu baseline " + boundary if key == "nusselt" else "dpdx per metre of core length"))
            factors.append(Factor(f"hx.{kind}.{key}", value, sources, vr, today, basis))
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
