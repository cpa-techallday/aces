"""Fluid properties from CoolProp (MIT licence).

Fluid names are CoolProp strings, e.g. "Water", "Air", "Nitrogen",
"INCOMP::MEG[0.5]" (50 % mono-ethylene glycol by mass).  No property is ever
hard-coded here: if CoolProp cannot evaluate a state, the call fails loudly.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from CoolProp.CoolProp import PropsSI


@dataclass(frozen=True)
class FluidState:
    fluid: str
    T_K: float
    p_Pa: float
    rho: float  # kg/m3
    mu: float  # Pa s
    cp: float  # J/kg/K
    k: float  # W/m/K

    @property
    def pr(self) -> float:
        return self.cp * self.mu / self.k


@lru_cache(maxsize=4096)
def _state(fluid: str, T_K: float, p_Pa: float) -> FluidState:
    try:
        rho = PropsSI("D", "T", T_K, "P", p_Pa, fluid)
        mu = PropsSI("V", "T", T_K, "P", p_Pa, fluid)
        cp = PropsSI("C", "T", T_K, "P", p_Pa, fluid)
        k = PropsSI("L", "T", T_K, "P", p_Pa, fluid)
    except ValueError as exc:  # CoolProp raises ValueError for bad states/names
        raise ValueError(f"CoolProp could not evaluate {fluid!r} at T={T_K} K, p={p_Pa} Pa: {exc}") from exc
    return FluidState(fluid, T_K, p_Pa, rho, mu, cp, k)


@lru_cache(maxsize=4096)
def _phase(fluid: str, T_K: float, p_Pa: float) -> str:
    try:
        from CoolProp.CoolProp import PhaseSI

        ph = PhaseSI("T", T_K, "P", p_Pa, fluid)
    except ValueError:
        return "unknown"
    return "unknown" if ph.startswith("unknown") else ph


def phase(fluid: str, T_K: float, p_Pa: float) -> str:
    """CoolProp phase string ('liquid', 'gas', 'supercritical_gas', 'twophase', ...).

    Incompressible mixtures (INCOMP::) have no phase model and return 'unknown'.
    """
    return _phase(fluid, round(float(T_K), 3), round(float(p_Pa), 1))


def state(fluid: str, T_K: float, p_Pa: float) -> FluidState:
    # Round to keep the cache useful inside iterative solves without changing physics.
    return _state(fluid, round(float(T_K), 3), round(float(p_Pa), 1))
