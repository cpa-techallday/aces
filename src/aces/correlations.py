"""Textbook internal-flow and heat-exchanger relations (the fast physics tier).

Every function returns a ``Correlation`` carrying the value, the regime and whether
the inputs sit inside the published validity range, so callers can never silently
use a correlation outside where it was fitted.

Sources (standard references; formulas reproduced, not fitted here):
  - Laminar fully developed circular duct: f_D = 64/Re; Nu = 4.36 (uniform heat
    flux) or 3.66 (uniform wall temperature).  Incropera & DeWitt, Fundamentals of
    Heat and Mass Transfer, ch. 8.
  - Petukhov (1970) smooth-pipe friction factor, 3e3 <= Re <= 5e6.
  - Gnielinski (1976) Nusselt number, 3e3 <= Re <= 5e6, 0.5 <= Pr <= 2000.
  - Counterflow effectiveness-NTU relation.  Incropera & DeWitt, ch. 11.

These are straight-duct baselines.  Applied to TPMS cores they are *uncalibrated*:
the domain models multiply them by calibration factors that start at 1.0 and are
only replaced by values fitted to CFD or test data (see ``aces.calibration``).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

RE_LAMINAR_MAX = 2300.0
RE_TURBULENT_MIN = 3000.0
RE_TURBULENT_MAX = 5.0e6
PR_GNIELINSKI = (0.5, 2000.0)


@dataclass(frozen=True)
class Correlation:
    value: float
    regime: str  # "laminar" | "transition" | "turbulent"
    valid: bool
    notes: tuple[str, ...] = field(default_factory=tuple)


def _petukhov(re: float) -> float:
    return (0.790 * math.log(re) - 1.64) ** -2


def _gnielinski(re: float, pr: float) -> float:
    f = _petukhov(re)
    return (f / 8.0) * (re - 1000.0) * pr / (1.0 + 12.7 * math.sqrt(f / 8.0) * (pr ** (2.0 / 3.0) - 1.0))


def friction_factor_darcy(re: float) -> Correlation:
    """Darcy friction factor for fully developed flow in a smooth duct."""
    if re <= 0:
        raise ValueError(f"Reynolds number must be positive, got {re}")
    if re < RE_LAMINAR_MAX:
        return Correlation(64.0 / re, "laminar", True)
    if re < RE_TURBULENT_MIN:
        w = (re - RE_LAMINAR_MAX) / (RE_TURBULENT_MIN - RE_LAMINAR_MAX)
        val = (1.0 - w) * 64.0 / RE_LAMINAR_MAX + w * _petukhov(RE_TURBULENT_MIN)
        return Correlation(val, "transition", False, ("linear blend across 2300 < Re < 3000; no published fit",))
    valid = re <= RE_TURBULENT_MAX
    notes = () if valid else (f"Re {re:.3g} above Petukhov range {RE_TURBULENT_MAX:.0e}",)
    return Correlation(_petukhov(re), "turbulent", valid, notes)


def nusselt(re: float, pr: float, boundary: str = "uniform_heat_flux") -> Correlation:
    """Fully developed Nusselt number for a smooth duct."""
    if re <= 0 or pr <= 0:
        raise ValueError(f"Re and Pr must be positive, got Re={re}, Pr={pr}")
    if boundary not in ("uniform_heat_flux", "uniform_wall_temperature"):
        raise ValueError(f"unknown boundary condition {boundary!r}")
    nu_lam = 4.36 if boundary == "uniform_heat_flux" else 3.66
    if re < RE_LAMINAR_MAX:
        return Correlation(nu_lam, "laminar", True)
    pr_ok = PR_GNIELINSKI[0] <= pr <= PR_GNIELINSKI[1]
    if re < RE_TURBULENT_MIN:
        w = (re - RE_LAMINAR_MAX) / (RE_TURBULENT_MIN - RE_LAMINAR_MAX)
        val = (1.0 - w) * nu_lam + w * _gnielinski(RE_TURBULENT_MIN, pr)
        return Correlation(val, "transition", False, ("linear blend across 2300 < Re < 3000; no published fit",))
    notes = []
    if re > RE_TURBULENT_MAX:
        notes.append(f"Re {re:.3g} above Gnielinski range")
    if not pr_ok:
        notes.append(f"Pr {pr:.3g} outside Gnielinski range {PR_GNIELINSKI}")
    return Correlation(_gnielinski(re, pr), "turbulent", not notes, tuple(notes))


def effectiveness_counterflow(ntu: float, cr: float) -> float:
    """Effectiveness of a counterflow exchanger, 0 <= Cr <= 1."""
    if ntu < 0 or not (0.0 <= cr <= 1.0):
        raise ValueError(f"need NTU >= 0 and 0 <= Cr <= 1, got NTU={ntu}, Cr={cr}")
    if abs(1.0 - cr) < 1e-9:
        return ntu / (1.0 + ntu)
    e = math.exp(-ntu * (1.0 - cr))
    return (1.0 - e) / (1.0 - cr * e)
