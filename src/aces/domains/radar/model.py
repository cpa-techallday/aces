"""Phased-array radar design model: the AERIS-X64 domain for ACES.

Encoded logic, in order:
  1. Aperture: an nx x ny element lattice at a pitch of ``pitch_lambda`` wavelengths.
     Array gain from element gain, taper efficiency and phase-quantization loss.
  2. Transmit: per-element peak power summed coherently; EIRP = P_peak * G_tx.
     DC draw from PA efficiency and duty cycle; dissipation density over the aperture
     checked against the cold-plate limit (Project B's 6.5 W/cm2 failure class).
  3. Receive: Friis cascade over the datasheet-quoted ``rx_chain`` stages, scaled by
     the calibration factor ``radar.rx.nf_factor`` (1.0 until EM/bench data lands).
  4. Detection: Albersheim required SNR for Pd/Pfa over the CPI, then the energy-form
     range equation; grating-lobe scan limit, unambiguous range/Doppler and range
     resolution derived from the waveform.
  5. Manufacturability/operations as constraints: required range, required
     resolution, scan sector, thermal density, DC budget.

What this tier does NOT model (reported on every result): mutual coupling between
elements, scan-dependent element patterns, mismatch vs frequency, multipath and
clutter, Swerling fluctuation loss (Albersheim is the steady-target bound), and the
tile's real S-parameters.  Those come from the simlab EM tier (``aces-tile-em``,
Palace/gprMax) and the anechoic bench campaign, and enter through the calibration
registry — until then every result carries ``uncalibrated`` keys.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, field_validator, model_validator

from ...calibration import Registry
from ...model import Evaluation, Param
from . import array, cascade, linkbudget

ALWAYS_WARN = (
    "fast tier: Friis cascade + array factor + Albersheim (steady-target bound); EM sim (aces-tile-em) verifies before layout sign-off",
    "no mutual coupling, scan-dependent element pattern, mismatch or clutter model",
    "Swerling fluctuation loss not applied; detection range is the optimistic bound",
    "CPI credited as coherent integration (pulse-Doppler FFT); the redesign study's headline ranges were single-pulse, so its numbers are ~2x more conservative",
)

# Calibration registry keys this model consumes (all multiplicative on a linear quantity).
KEY_NF = "radar.rx.nf_factor"  # multiplies the linear Friis noise factor
KEY_TX_LOSS = "radar.tx.line_loss_factor"  # multiplies the TX line/distribution loss (linear)
KEY_GAIN = "radar.array.gain_factor"  # multiplies array gain (absorbs taper/mismatch reality)
CALIBRATION_KEYS = (KEY_NF, KEY_TX_LOSS, KEY_GAIN)


class ChainStage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    gain_db: float
    nf_db: float
    op1db_dbm: float | None = None
    source: str = Field(min_length=3, description="datasheet or test report the numbers come from")


class Detection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pd: float = Field(0.9, gt=0.5, lt=1.0)
    pfa: float = Field(1e-6, gt=0.0, lt=1e-3)
    cpi_pulses: int = Field(1, ge=1, description="pulses noncoherently integrated per detection")


class RadarRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frequency_hz: PositiveFloat = Field(description="centre frequency, e.g. 10.5e9 for AERIS X-band")
    target_rcs_m2: PositiveFloat
    detection: Detection = Field(default_factory=Detection)
    min_range_m: PositiveFloat
    max_range_resolution_m: PositiveFloat
    scan_half_angle_deg: float = Field(45.0, gt=0.0, le=60.0)
    element_gain_db: float = Field(6.0, description="single-element broadside gain (patch ~6 dBi)")
    rx_chain: list[ChainStage] = Field(min_length=1)
    system_losses_db: float = Field(4.0, ge=0.0, description="radome, distribution, processing, atm — baseline")
    tx_line_loss_db: float = Field(1.0, ge=0.0, description="element-to-antenna baseline, calibratable")
    pa_efficiency: float = Field(0.30, gt=0.05, le=0.7, description="PAE of the GaN PA at rated duty")
    duty_max: float = Field(0.30, gt=0.0, le=1.0)
    max_dc_power_w: PositiveFloat
    max_dissipation_w_cm2: PositiveFloat = Field(description="cold-plate limit; Project B failed at 6.5")
    max_aperture_mm: PositiveFloat
    phase_bits_options: tuple[int, ...] = (4, 5, 6, 7)
    calibration_file: str | None = Field(None, min_length=1, description="registry JSON, relative to the spec")

    @model_validator(mode="after")
    def _chain_ordered(self):
        if not self.rx_chain:
            raise ValueError("rx_chain must list at least one stage")
        return self


class PhasedArrayRadar:
    id = "radar.phased_array"
    version = "0.1.0"
    requirements_model = RadarRequirements

    def __init__(self, registry: Registry | None = None):
        self._fixed_registry = registry
        self._registries: dict[str | None, Registry] = {}

    # ------------------------------------------------------------------ parameters
    def parameters(self, req: RadarRequirements) -> list[Param]:
        return [
            Param("nx", "float", 4.0, 32.0),  # rounded to int in evaluate
            Param("ny", "float", 4.0, 32.0),
            Param("pitch_lambda", "float", 0.45, 0.75, units="wavelengths"),
            Param("chirp_bw_hz", "float", 50e6, 1.2e9, log=True),
            Param("pulse_width_s", "float", 5e-6, 200e-6, log=True),
            Param("prf_hz", "float", 1e3, 20e3, log=True),
            Param("element_power_w", "float", 1.0, 40.0, log=True),
            Param("taper", "categorical", choices=tuple(array.TAPERS)),
            Param("phase_bits", "categorical", choices=req.phase_bits_options),
        ]

    # ------------------------------------------------------------------ registry
    def registry(self, req: RadarRequirements) -> Registry:
        if self._fixed_registry is not None:
            return self._fixed_registry
        key = req.calibration_file
        if key not in self._registries:
            self._registries[key] = Registry.load(key)
        return self._registries[key]

    # ------------------------------------------------------------------ evaluation
    def evaluate(self, params: dict[str, Any], req: RadarRequirements) -> Evaluation:
        reg = self.registry(req)
        nx, ny = int(round(params["nx"])), int(round(params["ny"]))
        pitch = float(params["pitch_lambda"])
        bw = float(params["chirp_bw_hz"])
        tau = float(params["pulse_width_s"])
        prf = float(params["prf_hz"])
        p_el = float(params["element_power_w"])
        taper = str(params["taper"])
        bits = int(params["phase_bits"])
        n_el = nx * ny

        f = req.frequency_hz
        lam = linkbudget.wavelength_m(f)

        # Aperture and gain (with calibration on the array gain)
        g_array = array.array_gain_db(nx, ny, req.element_gain_db, taper, bits)
        f_gain = reg.get(KEY_GAIN)
        g_tx = g_array + 10.0 * math.log10(f_gain.value)
        aperture_m2 = array.aperture_area_m2(nx, ny, pitch, f)

        # Transmit
        p_peak = n_el * p_el
        duty = min(req.duty_max, tau * prf)
        p_avg_rf = p_peak * duty
        dc_w = p_avg_rf / req.pa_efficiency
        f_tx = reg.get(KEY_TX_LOSS)
        tx_loss_db = req.tx_line_loss_db + 10.0 * math.log10(f_tx.value)

        # Receive cascade (with calibration on the noise figure)
        stages = [cascade.Stage(s.name, s.gain_db, s.nf_db, s.op1db_dbm) for s in req.rx_chain]
        rx = cascade.cascade(stages)
        f_nf = reg.get(KEY_NF)
        nf_db = rx.nf_db + 10.0 * math.log10(f_nf.value)

        # Detection: coherent CPI (Doppler FFT) gets full n_pulses integration gain;
        # the single-pulse SNR requirement is the n=1 Albersheim value.  Pairing
        # Albersheim(n) with an n-pulse energy budget would double-count integration.
        det = req.detection
        snr_req = linkbudget.albersheim_snr_db(det.pd, det.pfa, 1)
        losses = req.system_losses_db + tx_loss_db
        r_max = linkbudget.max_range_m(
            p_peak, tau, det.cpi_pulses, g_tx, g_tx, f, req.target_rcs_m2, nf_db, losses, snr_req
        )

        # Waveform and scan metrics
        res = linkbudget.range_resolution_m(bw)
        unamb_r = linkbudget.unambiguous_range_m(prf)
        doppler = linkbudget.doppler_resolution_m_s(prf, det.cpi_pulses, f)
        beam = array.beamwidth_rad(nx, pitch, taper)
        scan_limit = array.grating_lobe_scan_limit_rad(pitch)
        scan_req = math.radians(req.scan_half_angle_deg)

        # Thermal: RF energy not radiated is dissipated, plus LNA/control overhead ~20 %
        diss_w = dc_w - p_avg_rf
        diss_density = diss_w / (aperture_m2 * 1e4) * 1.2

        objectives = {
            "neg_detection_range_km": -r_max / 1000.0,
            "dc_power_kw": dc_w / 1000.0,
        }
        constraints = {
            "range_margin_m": req.min_range_m - r_max,
            "resolution_margin_m": res - req.max_range_resolution_m,
            "scan_margin_rad": scan_req - scan_limit,
            "unambiguous_range_margin_m": req.min_range_m - unamb_r,
            "thermal_margin_w_cm2": diss_density - req.max_dissipation_w_cm2,
            "dc_margin_w": dc_w - req.max_dc_power_w,
            "aperture_margin_mm": math.sqrt(aperture_m2) * 1e3 - req.max_aperture_mm,
        }

        warnings = list(ALWAYS_WARN)
        uncal: list[str] = []
        for key, fac in ((KEY_NF, f_nf), (KEY_TX_LOSS, f_tx), (KEY_GAIN, f_gain)):
            if not fac.calibrated:
                uncal.append(key)
            else:
                oor = fac.out_of_range(frequency_hz=f)
                if oor:
                    warnings.append(f"calibration factor {key} used outside fitted range: {', '.join(oor)}")
        if duty < tau * prf:
            warnings.append(f"duty clipped to duty_max ({req.duty_max:.0%}); waveform duty {tau * prf:.0%} exceeds it")

        metrics = {
            "n_elements": n_el,
            "aperture_mm": round(math.sqrt(aperture_m2) * 1e3, 1),
            "array_gain_db": round(g_tx, 2),
            "eirp_dbw": round(10.0 * math.log10(p_peak) + g_tx, 1),
            "peak_power_w": round(p_peak, 1),
            "avg_rf_power_w": round(p_avg_rf, 1),
            "duty": round(duty, 4),
            "rx_nf_db": round(nf_db, 2),
            "rx_gain_db": round(rx.gain_db, 1),
            "rx_output_p1db_dbm": rx.output_p1db_dbm,
            "snr_required_db": round(snr_req, 2),
            "detection_range_km": round(r_max / 1000.0, 2),
            "range_resolution_m": round(res, 3),
            "unambiguous_range_km": round(unamb_r / 1000.0, 1),
            "doppler_resolution_m_s": round(doppler, 2),
            "beamwidth_deg": round(math.degrees(beam), 2),
            "grating_scan_limit_deg": round(math.degrees(scan_limit), 1),
            "dissipation_w_cm2": round(diss_density, 2),
            "dc_power_w": round(dc_w, 1),
        }
        return Evaluation(params=dict(params), objectives=objectives, constraints=constraints,
                          metrics=metrics, warnings=warnings, uncalibrated=uncal)
