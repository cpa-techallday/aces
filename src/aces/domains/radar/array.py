"""Planar phased-array mathematics for the radar fast tier.

All functions are analytic references, not simulation results.  They exist so the
design model can rank candidates in microseconds; the simlab EM tier (Palace /
gprMax, solver id ``aces-tile-em``) verifies the winner, per the standing rule
"surrogates rank, the real solver signs off".

References: Balanis, Antenna Theory (array factor, taper); Mailloux, Phased Array
Antenna Handbook (phase quantization, element pattern); Skolnik, Radar Handbook (scan limits).
"""
from __future__ import annotations

import math

# Taylor bar-n broadening and gain efficiency for common tapers (Mailloux table 2.2).
# uniform is the reference; taylor_25 = -25 dB sidelobe design, hamming ~ -42 dB.
# These are LINE-SOURCE (one-axis) values.  A planar array uses the separable taper
# w(x, y) = w(x) * w(y): beamwidth broadens per axis, and the aperture efficiency is the
# product of the two axes' efficiencies (``planar_taper_efficiency``).
TAPERS: dict[str, tuple[float, float]] = {
    "uniform": (1.00, 1.00),
    "taylor_25": (1.14, 0.86),
    "taylor_35": (1.25, 0.76),
    "hamming": (1.33, 0.73),
}


def taper_broadening(kind: str) -> float:
    """3 dB beamwidth broadening factor of a taper relative to uniform."""
    return TAPERS[kind][0]


def taper_efficiency(kind: str) -> float:
    """Gain efficiency of a taper relative to uniform illumination."""
    return TAPERS[kind][1]


def beamwidth_rad(n_elements: int, d_over_lambda: float, taper: str = "uniform") -> float:
    """3 dB beamwidth of a uniform-pitch linear array of ``n_elements``, broadside.

    theta_3dB ~= 0.886 * broadening / (N * d/lambda)  (Balanis 6.3).
    """
    if n_elements < 1 or d_over_lambda <= 0:
        raise ValueError("n_elements >= 1 and d_over_lambda > 0 required")
    return 0.886 * taper_broadening(taper) / (n_elements * d_over_lambda)


def grating_lobe_scan_limit_rad(d_over_lambda: float) -> float:
    """Largest scan angle with no grating lobe in visible space.

    Grating lobes stay out while d/lambda <= 1 / (1 + sin(theta_max))
    (Skolnik ch. 9).  d/lambda <= 0.5 allows full +-90 deg scan.
    """
    if d_over_lambda <= 0:
        raise ValueError("d_over_lambda > 0 required")
    if d_over_lambda <= 0.5:
        return math.pi / 2
    s = 1.0 / d_over_lambda - 1.0
    if s <= 0:
        return 0.0
    return math.asin(min(1.0, s))


def phase_quantization(bits: int) -> tuple[float, float]:
    """(rms phase error in rad, mean gain LOSS in dB, >= 0) for a ``bits``-bit phase shifter.

    The error is uniform over one least significant bit, phi ~ U(-a, a) with a = pi / 2^bits, so
    sigma = a / sqrt(3).  The coherent sum keeps |E[exp(j phi)]|^2 = sinc^2(a) = (sin a / a)^2 of the
    gain (large array, decorrelated errors), so the loss is -10 log10(sinc^2(pi / 2^bits)) dB:
    0.224 dB at 3 bits, 0.0035 dB at 6 bits.  To first order this is 1 - pi^2 / (3 * 2^(2 bits)),
    the phase-quantisation gain loss in Mailloux, Phased Array Antenna Handbook (array error effects).
    """
    if bits < 1:
        raise ValueError("bits >= 1 required")
    a = math.pi / 2**bits
    sigma = a / math.sqrt(3.0)
    loss_db = -10.0 * math.log10((math.sin(a) / a) ** 2)
    return sigma, loss_db


def rms_sidelobe_ratio(bits: int, n_elements: int) -> float:
    """RMS sidelobe level relative to the main beam from phase quantization alone.

    ~= sigma^2 / N for an N-element array with independent quantization errors.
    """
    sigma, _ = phase_quantization(bits)
    return sigma**2 / max(1, n_elements)


def planar_taper_efficiency(kind: str) -> float:
    """Aperture efficiency of the separable planar taper w(x) * w(y): the product of both axes'."""
    return taper_efficiency(kind) ** 2


def array_gain_db(
    nx: int,
    ny: int,
    element_gain_db: float,
    taper: str = "uniform",
    phase_bits: int | None = None,
) -> float:
    """Broadside boresight gain of an nx x ny array in dBi.

    G = G_element + 10*log10(N) + 10*log10(eta_x * eta_y) - quantisation loss,
    with the same taper on both axes (separable), so eta_x * eta_y = eta^2.
    """
    if nx < 1 or ny < 1:
        raise ValueError("nx, ny >= 1 required")
    g = element_gain_db + 10.0 * math.log10(nx * ny) + 10.0 * math.log10(planar_taper_efficiency(taper))
    if phase_bits is not None:
        g -= phase_quantization(phase_bits)[1]
    return g


def scan_loss_db(scan_rad: float, exponent: float) -> float:
    """One-way gain loss (dB, >= 0) at scan angle theta: G(theta) = G(0) * cos^n(theta).

    n is the exponent of the embedded element pattern, which the scanned array gain follows
    (Mailloux, Phased Array Antenna Handbook, element pattern and scan loss).  n must come from the
    spec with its source (embedded-element measurement or EM result); n = 1 is the projected
    aperture alone.  Scan blindness is not modelled.
    """
    if exponent < 0:
        raise ValueError("element pattern exponent >= 0 required")
    if not 0.0 <= scan_rad < math.pi / 2:
        raise ValueError("scan angle must be in [0, 90) degrees")
    return -10.0 * exponent * math.log10(math.cos(scan_rad))


def aperture_size_m(nx: int, ny: int, d_over_lambda: float, frequency_hz: float) -> tuple[float, float]:
    """Physical aperture width and height (element centres, one pitch per element)."""
    from .linkbudget import wavelength_m

    d = d_over_lambda * wavelength_m(frequency_hz)
    return nx * d, ny * d


def aperture_area_m2(nx: int, ny: int, d_over_lambda: float, frequency_hz: float) -> float:
    """Physical aperture area of the lattice (element centres, one pitch per element)."""
    from .linkbudget import wavelength_m

    d = d_over_lambda * wavelength_m(frequency_hz)
    return (nx * d) * (ny * d)
