"""Radar link budget for pulse-LFM phased-array radar (fast tier).

Energy form of the radar range equation, so pulse-compression gain is carried by
the pulse width and the waveform bandwidth only sets range resolution:

    SNR = P_peak * tau * n_pulses * Gt * Gr * lambda^2 * sigma
          / ((4*pi)^3 * R^4 * k * Ts * L),     Ts = Ta + T0 * (F - 1)

n_pulses here is coherent integration (energy summed).  With the antenna at T0 = 290 K,
Ts = T0 * F, the convention of the AERIS-X64 redesign study ("Radar Hardware Comparison
and Redesign.md"): its ~2.7 km Project-A baseline (160 W peak, EIRP ~40 dBW, 100 us
pulse, NF ~5 dB) reproduces within ~10 % here (2505 m).
"""
from __future__ import annotations

import math

C0 = 299_792_458.0  # m/s
K_B = 1.380_649e-23  # J/K
T0 = 290.0  # K reference noise temperature


def wavelength_m(frequency_hz: float) -> float:
    return C0 / frequency_hz


# Albersheim's fit is stated for these ranges (Albersheim 1981; Richards, Fundamentals of Radar Signal
# Processing, 2005, ch. 6): about 0.2 dB error inside them, unknown outside.  Rule 5: refuse outside.
ALBERSHEIM_PD = (0.1, 0.9)
ALBERSHEIM_PFA = (1e-7, 1e-3)
ALBERSHEIM_N = (1, 8096)


def albersheim_snr_db(pd: float, pfa: float, n_pulses: int) -> float:
    """Required single-pulse SNR (dB) for Pd/Pfa with NONCOHERENT integration of n pulses.

    Albersheim's equation (steady target, linear detector; W. J. Albersheim, Proc. IEEE 69(7), 1981):
    an engineering fit to Robertson's curves, accurate to about 0.2 dB for 0.1 <= Pd <= 0.9,
    1e-7 <= Pfa <= 1e-3 and 1 <= n <= 8096.  Outside that range it is refused (rule 5).
    Coherent integration is not this formula's n: credit it as n x single-pulse energy against the
    n = 1 requirement.  Swerling-fluctuating targets need more SNR; treat this as the optimistic bound.
    """
    problems = []
    if not ALBERSHEIM_PD[0] <= pd <= ALBERSHEIM_PD[1]:
        problems.append(f"Pd {pd:g} outside {ALBERSHEIM_PD[0]:g}-{ALBERSHEIM_PD[1]:g}")
    if not ALBERSHEIM_PFA[0] <= pfa <= ALBERSHEIM_PFA[1]:
        problems.append(f"Pfa {pfa:g} outside {ALBERSHEIM_PFA[0]:g}-{ALBERSHEIM_PFA[1]:g}")
    if not (isinstance(n_pulses, int) and ALBERSHEIM_N[0] <= n_pulses <= ALBERSHEIM_N[1]):
        problems.append(f"n {n_pulses!r} outside {ALBERSHEIM_N[0]}-{ALBERSHEIM_N[1]}")
    if problems:
        raise ValueError("Albersheim's equation is not valid here: " + "; ".join(problems))
    a = math.log(0.62 / pfa)
    b = math.log(pd / (1.0 - pd))
    return -5.0 * math.log10(n_pulses) + (6.2 + 4.54 / math.sqrt(n_pulses + 0.44)) * math.log10(
        a + 0.12 * a * b + 1.7 * b
    )


def system_noise_temperature_k(nf_db: float, antenna_temperature_k: float) -> float:
    """Ts = Ta + T0 * (F - 1): antenna noise plus the receiver's noise referred to its input.

    F is the noise factor defined at T0 = 290 K (IEEE definition), so Ta = T0 gives Ts = T0 * F, the
    form ``max_range_m`` uses when no Ts is given (Skolnik, Introduction to Radar Systems, 3rd ed., ch. 2).
    """
    if antenna_temperature_k <= 0:
        raise ValueError("antenna_temperature_k > 0 required")
    return antenna_temperature_k + T0 * (10.0 ** (nf_db / 10.0) - 1.0)


def blind_range_m(pulse_width_s: float) -> float:
    """Closest range a monostatic pulsed radar can see: c * tau / 2.

    The receiver is blanked while the pulse is transmitted.  T/R switching and receiver recovery time
    add to this and are not included, so the true blind zone is larger.
    """
    if pulse_width_s <= 0:
        raise ValueError("pulse_width_s > 0 required")
    return C0 * pulse_width_s / 2.0


def max_range_m(
    p_peak_w: float,
    tau_s: float,
    n_pulses: int,
    gain_tx_db: float,
    gain_rx_db: float,
    frequency_hz: float,
    rcs_m2: float,
    nf_db: float,
    losses_db: float,
    snr_req_db: float,
    system_noise_temp_k: float | None = None,
) -> float:
    """Range (m) at which SNR equals ``snr_req_db`` with the energy of ``n_pulses`` coherently summed.

    Noise power density is k * Ts.  With ``system_noise_temp_k`` None, Ts = T0 * F (antenna at 290 K);
    otherwise Ts is used as given (see ``system_noise_temperature_k``) and ``nf_db`` is not used.
    For noncoherent integration pass ``n_pulses=1`` and the Albersheim requirement for n pulses.
    """
    lam = wavelength_m(frequency_hz)
    g = 10.0 ** ((gain_tx_db + gain_rx_db) / 10.0)
    ts = T0 * (10.0 ** (nf_db / 10.0)) if system_noise_temp_k is None else system_noise_temp_k
    num = p_peak_w * tau_s * n_pulses * g * lam**2 * rcs_m2
    den = (4.0 * math.pi) ** 3 * K_B * ts * (10.0 ** (losses_db / 10.0)) * (10.0 ** (snr_req_db / 10.0))
    return (num / den) ** 0.25


def range_resolution_m(bandwidth_hz: float) -> float:
    """c / (2 B) for an LFM chirp of bandwidth B."""
    if bandwidth_hz <= 0:
        raise ValueError("bandwidth_hz > 0 required")
    return C0 / (2.0 * bandwidth_hz)


def unambiguous_range_m(prf_hz: float) -> float:
    return C0 / (2.0 * prf_hz)


def doppler_resolution_m_s(prf_hz: float, n_pulses: int, frequency_hz: float) -> float:
    """Velocity resolution of an n-pulse CPI at the given PRF."""
    return wavelength_m(frequency_hz) * prf_hz / (2.0 * n_pulses)


def unambiguous_velocity_m_s(prf_hz: float, frequency_hz: float) -> float:
    return wavelength_m(frequency_hz) * prf_hz / 4.0
