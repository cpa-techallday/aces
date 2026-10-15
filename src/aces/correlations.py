"""Textbook internal-flow and heat-exchanger relations (the fast physics tier).

Every function returns a ``Correlation`` carrying the value, the regime and whether
the inputs sit inside the published validity range, so callers can never silently
use a correlation outside where it was fitted.

Sources (standard references; formulas reproduced, not fitted here):
  - Laminar fully developed circular duct: f_D = 64/Re; Nu = 4.36 (uniform heat
    flux) or 3.66 (uniform wall temperature).  Incropera & DeWitt, Fundamentals of
    Heat and Mass Transfer, ch. 8.  Valid for Re < 2300, far from the entrance.
  - Laminar thermal entry, mean Nusselt number over a length L, with the thermal entry
    length x* = L / (Dh Re Pr) and the Graetz number Gz = Re Pr Dh / L = 1 / x*:
      uniform wall temperature, Hausen (1943), as given by Incropera & DeWitt ch. 8:
        Nu_m = 3.66 + 0.0668 Gz / (1 + 0.04 Gz^(2/3))
      uniform heat flux (H), Shah (1975), as given by Shah & London (1978):
        Nu_m = 1.953 x*^(-1/3)       for x* <= 0.03
        Nu_m = 4.364 + 0.0722 / x*   for x* >  0.03
    Validity: laminar (Re < 2300), circular tube, velocity profile already developed
    (the thermal entry, or Graetz, problem), any x*; both tend to the fully developed
    value as x* grows.  Hausen also holds for a velocity profile developing at the same
    time when Pr >= 5; below that the true Nu is higher, so the value is conservative.
    The Shah form steps by 7.6 % at x* = 0.03 (6.29 below, 6.76 above); integrating
    Shah's local Nu_x,H gives 6.50 there, so the two branches are within 4.2 % of it.
  - Petukhov (1970) smooth-pipe friction factor, 3e3 <= Re <= 5e6.
  - Gnielinski (1976) Nusselt number, 3e3 <= Re <= 5e6, 0.5 <= Pr <= 2000; fully
    developed (the turbulent entrance is short and is not modelled).
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
X_STAR_DEVELOPED = 0.05  # below this thermal entry length the mean Nu is well above the fully developed value
NU_LAMINAR_FD = {"uniform_heat_flux": 4.36, "uniform_wall_temperature": 3.66}

LAMINAR_FD_SOURCE = "fully developed laminar circular duct (Incropera & DeWitt ch. 8)"
LAMINAR_FD_VALIDITY = "Re < 2300, thermally and hydrodynamically fully developed"
HAUSEN_SOURCE = "Hausen (1943) mean Nu, laminar thermal entry, uniform wall temperature (Incropera & DeWitt ch. 8)"
SHAH_SOURCE = "Shah (1975) mean Nu, laminar thermal entry, uniform heat flux (Shah & London 1978)"
ENTRY_VALIDITY = ("Re < 2300, circular tube, developed velocity profile, any x* = L/(Dh Re Pr); Hausen also for "
                  "a developing velocity profile when Pr >= 5")
GNIELINSKI_SOURCE = "Gnielinski (1976), fully developed"
GNIELINSKI_VALIDITY = "3e3 <= Re <= 5e6, 0.5 <= Pr <= 2000"
TRANSITION_VALIDITY = "none: linear blend across 2300 < Re < 3000, no published fit"


@dataclass(frozen=True)
class Correlation:
    value: float
    regime: str  # "laminar" | "transition" | "turbulent"
    valid: bool
    notes: tuple[str, ...] = field(default_factory=tuple)
    source: str = ""  # the correlation used and its reference
    validity: str = ""  # its published validity range
    x_star: float | None = None  # thermal entry length L / (Dh Re Pr), when a length was given
    fully_developed: float | None = None  # the fully developed value at the same Re and Pr

    @property
    def graetz(self) -> float | None:
        """Gz = Re Pr Dh / L = 1 / x*."""
        return 1.0 / self.x_star if self.x_star else None


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
        return Correlation(64.0 / re, "laminar", True, (), "f = 64/Re, " + LAMINAR_FD_SOURCE, LAMINAR_FD_VALIDITY)
    if re < RE_TURBULENT_MIN:
        w = (re - RE_LAMINAR_MAX) / (RE_TURBULENT_MIN - RE_LAMINAR_MAX)
        val = (1.0 - w) * 64.0 / RE_LAMINAR_MAX + w * _petukhov(RE_TURBULENT_MIN)
        return Correlation(val, "transition", False, ("linear blend across 2300 < Re < 3000; no published fit",),
                           "64/Re to Petukhov blend", TRANSITION_VALIDITY)
    valid = re <= RE_TURBULENT_MAX
    notes = () if valid else (f"Re {re:.3g} above Petukhov range {RE_TURBULENT_MAX:.0e}",)
    return Correlation(_petukhov(re), "turbulent", valid, notes, "Petukhov (1970), smooth pipe, fully developed",
                       "3e3 <= Re <= 5e6")


def _nusselt_entry(x_star: float, boundary: str) -> tuple[float, str]:
    """Laminar mean Nu over a thermal entry length x* (circular tube, developed velocity profile)."""
    if boundary == "uniform_wall_temperature":
        gz = 1.0 / x_star
        return 3.66 + 0.0668 * gz / (1.0 + 0.04 * gz ** (2.0 / 3.0)), HAUSEN_SOURCE
    if x_star <= 0.03:
        return 1.953 * x_star ** (-1.0 / 3.0), SHAH_SOURCE
    return 4.364 + 0.0722 / x_star, SHAH_SOURCE


def nusselt(re: float, pr: float, boundary: str = "uniform_heat_flux", *, l_over_dh: float | None = None) -> Correlation:
    """Nusselt number for a smooth duct.

    Without ``l_over_dh`` the value is fully developed (what a periodic CFD sample measures).  With the heated
    length over hydraulic diameter, laminar flow gets the mean Nu over that length from the thermal-entry
    correlations (Hausen for uniform wall temperature, Shah for uniform heat flux); ``x_star`` and
    ``fully_developed`` are reported either way.  Turbulent flow stays fully developed.
    """
    if re <= 0 or pr <= 0:
        raise ValueError(f"Re and Pr must be positive, got Re={re}, Pr={pr}")
    if boundary not in NU_LAMINAR_FD:
        raise ValueError(f"unknown boundary condition {boundary!r}")
    if l_over_dh is not None and not l_over_dh > 0:
        raise ValueError(f"heated length / Dh must be positive, got {l_over_dh}")
    x_star = l_over_dh / (re * pr) if l_over_dh is not None else None
    nu_fd = NU_LAMINAR_FD[boundary]
    nu_lam, lam_source, lam_validity = nu_fd, LAMINAR_FD_SOURCE, LAMINAR_FD_VALIDITY
    if x_star is not None:
        nu_lam, lam_source = _nusselt_entry(x_star, boundary)
        lam_validity = ENTRY_VALIDITY
    if re < RE_LAMINAR_MAX:
        return Correlation(nu_lam, "laminar", True, (), lam_source, lam_validity, x_star, nu_fd)
    pr_ok = PR_GNIELINSKI[0] <= pr <= PR_GNIELINSKI[1]
    if re < RE_TURBULENT_MIN:
        w = (re - RE_LAMINAR_MAX) / (RE_TURBULENT_MIN - RE_LAMINAR_MAX)
        val = (1.0 - w) * nu_lam + w * _gnielinski(RE_TURBULENT_MIN, pr)
        fd = (1.0 - w) * nu_fd + w * _gnielinski(RE_TURBULENT_MIN, pr)
        return Correlation(val, "transition", False, ("linear blend across 2300 < Re < 3000; no published fit",),
                           f"blend of {lam_source} and {GNIELINSKI_SOURCE}", TRANSITION_VALIDITY, x_star, fd)
    notes = []
    if re > RE_TURBULENT_MAX:
        notes.append(f"Re {re:.3g} above Gnielinski range")
    if not pr_ok:
        notes.append(f"Pr {pr:.3g} outside Gnielinski range {PR_GNIELINSKI}")
    nu_t = _gnielinski(re, pr)
    return Correlation(nu_t, "turbulent", not notes, tuple(notes), GNIELINSKI_SOURCE, GNIELINSKI_VALIDITY, x_star, nu_t)


def effectiveness_counterflow(ntu: float, cr: float) -> float:
    """Effectiveness of a counterflow exchanger, 0 <= Cr <= 1."""
    if ntu < 0 or not (0.0 <= cr <= 1.0):
        raise ValueError(f"need NTU >= 0 and 0 <= Cr <= 1, got NTU={ntu}, Cr={cr}")
    if abs(1.0 - cr) < 1e-9:
        return ntu / (1.0 + ntu)
    e = math.exp(-ntu * (1.0 - cr))
    return (1.0 - e) / (1.0 - cr * e)
