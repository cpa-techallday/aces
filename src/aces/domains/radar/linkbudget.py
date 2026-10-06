"""Radar link budget for pulse-LFM phased-array radar (fast tier).

Energy form of the radar range equation, so pulse-compression gain is carried by
the pulse width and the waveform bandwidth only sets range resolution:

    SNR = P_peak * tau * n_pulses * Gt * Gr * lambda^2 * sigma
          / ((4*pi)^3 * R^4 * k * T0 * NF * L)

This matches the convention used in the AERIS-X64 redesign study
("Radar Hardware Comparison and Redesign.md"): its ~2.7 km Project-A baseline
(160 W peak, EIRP ~40 dBW, 100 us pulse, NF ~5 dB) reproduces within ~10 % here.
"""
from __future__ import annotations

import math

C0 = 299_792_458.0  # m/s
K_B = 1.380_649e-23  # J/K
T0 = 290.0  # K reference noise temperature


def wavelength_m(frequency_hz: float) -> float:
    return C0 / frequency_hz


def albersheim_snr_db(pd: float, pfa: float, n_pulses: int) -> float:
    """Required single-pulse SNR (dB) for Pd/Pfa with noncoherent integration of n pulses.

    Albersheim's equation (steady target, linear detector) — an engineering estimate
    accurate to ~0.2 dB over 1e-7 < Pfa < 1e-3, 0.5 < Pd < 0.95.  Swerling-fluctuating
    targets need more; treat this as the optimistic bound and say so in warnings.
    """
    if not (0.0 < pd < 1.0) or not (0.0 < pfa < 1.0) or n_pulses < 1:
        raise ValueError("need 0<pd<1, 0<pfa<1, n_pulses>=1")
    a = math.log(0.62 / pfa)
    b = math.log(pd / (1.0 - pd))
    return -5.0 * math.log10(n_pulses) + (6.2 + 4.54 / math.sqrt(n_pulses + 0.44)) * math.log10(
        a + 0.12 * a * b + 1.7 * b
    )


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
) -> float:
    """Detection range (m) at which SNR equals ``snr_req_db`` after ``n_pulses`` integrated."""
    lam = wavelength_m(frequency_hz)
    g = 10.0 ** ((gain_tx_db + gain_rx_db) / 10.0)
    num = p_peak_w * tau_s * n_pulses * g * lam**2 * rcs_m2
    den = (4.0 * math.pi) ** 3 * K_B * T0 * (10.0 ** (nf_db / 10.0)) * (10.0 ** (losses_db / 10.0)) * (
        10.0 ** (snr_req_db / 10.0)
    )
    return (num / den) ** 0.25


def range_resolution_m(bandwidth_hz: float) -> float:
    """c / (2 B) for an LFM chirp of bandwidth B."""
    if bandwidth_hz <= 0:
        raise ValueError("bandwidth_hz > 0 required")
    return C0 / (2.0 * bandwidth_hz)


def unambiguous_range_m(prf_hz: float) -> float:
    return C0 / (2.0 * prf_hz)


def doppler_resolution_m_s(prf_hz: int | float, n_pulses: int, frequency_hz: float) -> float:
    """Velocity resolution of an n-pulse CPI at the given PRF."""
    return wavelength_m(frequency_hz) * prf_hz / (2.0 * n_pulses)


def unambiguous_velocity_m_s(prf_hz: float, frequency_hz: float) -> float:
    return wavelength_m(frequency_hz) * prf_hz / 4.0
