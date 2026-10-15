"""Phased-array radar design model: the AERIS-X64 domain for ACES.

Encoded logic, in order:
  1. Aperture: an nx x ny element lattice at a pitch of ``pitch_lambda`` wavelengths.
     Array gain from element gain, the separable planar taper (eta_x * eta_y) and the mean
     phase-quantisation loss; scan loss cos^n(theta) at the edge of the scan sector, with n
     from the spec.
  2. Transmit: per-element peak power summed coherently; EIRP = P_peak * G_tx.  Duty is the
     waveform's tau * PRF and is never clipped: above the PA's rated duty the design is
     infeasible.  DC draw = average RF / PAE + per-element support electronics; the dissipation
     density over the aperture is checked against the cold-plate limit (Project B's 6.5 W/cm2
     failure class).
  3. Receive: Friis cascade over the datasheet-quoted ``rx_chain`` stages, scaled by the
     calibration factor ``radar.rx.nf_factor`` (1.0 until EM/bench data lands); system noise
     temperature Ts = Ta + T0 (F - 1); output-referred P1dB cascade.
  4. Detection: Albersheim required SNR for Pd/Pfa (refused outside its fitted range), with
     coherent CPI integration credited as n x single-pulse energy against the n = 1
     requirement, or noncoherent integration as Albersheim(n) against single-pulse energy.
     Range at broadside and at the scan edge; nothing is claimed inside the blind range
     c * tau / 2.  Grating-lobe scan limit, unambiguous range/Doppler and range resolution
     from the waveform.
  5. Constraints: required range at the scan edge, blind range, resolution, scan sector,
     unambiguous range, duty, thermal density, DC budget, aperture width and height.

Every number the model cannot know (element gain and pattern, losses, PA efficiency and rated
duty, antenna noise temperature, support-electronics power) is a spec input with a ``source``.
There are no defaults for them (rules 1, 2): a spec without them is refused.

What this tier does NOT model (reported on every result): mutual coupling between elements,
scan blindness, mismatch vs frequency, multipath and clutter, Swerling fluctuation loss
(Albersheim is the steady-target bound), T/R switching time, and the tile's real
S-parameters.  Those come from the simlab EM tier (``aces-tile-em``, Palace/gprMax) and the
anechoic bench campaign, and enter through the calibration registry; until then every result
carries ``uncalibrated`` keys.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveFloat,
    field_validator,
    model_validator,
)

from ... import manufacturing
from ...calibration import Registry
from ...model import Evaluation, Param
from . import array, cascade, linkbudget

DOMAIN = "radar.phased_array"

ALWAYS_WARN = (
    "fast tier: Friis cascade + array factor + Albersheim (steady-target bound); EM sim (aces-tile-em) verifies before layout sign-off",
    "no mutual coupling, scan blindness, mismatch or clutter model; scan loss is the spec's cos^n element-pattern law",
    "Swerling fluctuation loss not applied; detection range is the optimistic bound",
    "blind range is c*tau/2 only: T/R switching and receiver recovery time are not included, so the real blind zone is larger",
    "phase-quantisation loss is the mean for decorrelated errors; taper efficiency is the one-axis table value squared (separable planar taper)",
)
INTEGRATION_WARN = {
    "coherent": "CPI credited as coherent integration (pulse-Doppler FFT): n x single-pulse energy against the "
                "single-pulse Albersheim SNR. The redesign study's headline ranges were single-pulse, so they are "
                "lower by up to n^(1/4) (2.8x at n = 64)",
    "noncoherent": "CPI credited as noncoherent integration: single-pulse energy against Albersheim's n-pulse SNR",
}

# Calibration registry keys this model consumes (all multiplicative on a linear quantity).
KEY_NF = "radar.rx.nf_factor"  # multiplies the linear Friis noise factor
KEY_TX_LOSS = "radar.tx.line_loss_factor"  # multiplies the TX line/distribution loss (linear)
KEY_GAIN = "radar.array.gain_factor"  # multiplies array gain (absorbs taper/mismatch reality)
CALIBRATION_KEYS = (KEY_NF, KEY_TX_LOSS, KEY_GAIN)


class Sourced(BaseModel):
    """A number the model cannot know, with the document it comes from (rules 1, 2)."""

    model_config = ConfigDict(extra="forbid")
    value: float
    source: str = Field(min_length=3, description="datasheet, test report or EM result the value comes from")

    @field_validator("source")
    @classmethod
    def _named(cls, v: str) -> str:
        if len(v.strip()) < 3:
            raise ValueError("source must name the datasheet, test report or EM result the value comes from")
        return v


class SourcedNonNegative(Sourced):
    value: float = Field(ge=0.0)


class SourcedPositive(Sourced):
    value: PositiveFloat


class SourcedFraction(Sourced):
    value: float = Field(gt=0.0, le=1.0)


class ChainStage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    gain_db: float
    nf_db: float
    op1db_dbm: float | None = None
    source: str = Field(min_length=3, description="datasheet or test report the numbers come from")


class Detection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pd: float = Field(0.9, ge=linkbudget.ALBERSHEIM_PD[0], le=linkbudget.ALBERSHEIM_PD[1],
                      description="probability of detection, within Albersheim's fitted range")
    pfa: float = Field(1e-6, ge=linkbudget.ALBERSHEIM_PFA[0], le=linkbudget.ALBERSHEIM_PFA[1],
                       description="probability of false alarm, within Albersheim's fitted range")
    cpi_pulses: int = Field(1, ge=1, description="pulses integrated per detection (one CPI)")
    integration: Literal["coherent", "noncoherent"] = Field(
        description="coherent: Doppler FFT, n x single-pulse energy against the n = 1 Albersheim SNR; "
                    "noncoherent: single-pulse energy against Albersheim's n-pulse SNR")

    @model_validator(mode="after")
    def _albersheim_n(self):
        if self.integration == "noncoherent" and self.cpi_pulses > linkbudget.ALBERSHEIM_N[1]:
            raise ValueError(f"noncoherent integration of {self.cpi_pulses} pulses is outside Albersheim's fitted "
                             f"range (n <= {linkbudget.ALBERSHEIM_N[1]})")
        return self


class RadarManufacturing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    process: str = Field(description="manufacturing route id from aces.manufacturing (rule 13a)")

    @field_validator("process")
    @classmethod
    def _known_route(cls, v: str) -> str:
        manufacturing.require(v, DOMAIN)
        return v


class RadarRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frequency_hz: PositiveFloat = Field(description="centre frequency, e.g. 10.5e9 for AERIS X-band")
    target_rcs_m2: PositiveFloat
    detection: Detection
    min_range_m: PositiveFloat = Field(description="required detection range, held over the whole scan sector")
    max_blind_range_m: PositiveFloat = Field(
        description="closest range at which a target must be seen; the blind zone c*tau/2 must end inside it")
    max_range_resolution_m: PositiveFloat
    scan_half_angle_deg: float = Field(45.0, gt=0.0, le=60.0)
    element_gain_db: Sourced = Field(description="embedded element broadside gain, dBi")
    element_pattern_exponent: SourcedNonNegative = Field(
        description="n in the embedded element pattern G(theta) = G(0) cos^n(theta); sets the scan loss")
    rx_chain: list[ChainStage] = Field(min_length=1)
    antenna_noise_temperature_k: SourcedPositive = Field(description="antenna noise temperature Ta, K")
    system_losses_db: SourcedNonNegative = Field(description="radome, distribution, processing, atmosphere")
    tx_line_loss_db: SourcedNonNegative = Field(description="element-to-antenna loss, calibratable")
    pa_efficiency: SourcedFraction = Field(description="PAE of the PA at its rated duty")
    duty_max: SourcedFraction = Field(description="the PA's rated duty cycle")
    support_dc_w_per_element: SourcedNonNegative = Field(
        description="DC power per element for LNA, beamformer and control (everything but the PA), W")
    max_dc_power_w: PositiveFloat
    max_dissipation_w_cm2: PositiveFloat = Field(description="cold-plate limit; Project B failed at 6.5")
    max_aperture_mm: PositiveFloat = Field(description="largest aperture width or height")
    phase_bits_options: tuple[int, ...] = (4, 5, 6, 7)
    manufacturing: RadarManufacturing
    calibration_file: str | None = Field(None, min_length=1, description="registry JSON, relative to the spec")


class PhasedArrayRadar:
    id = DOMAIN
    version = "0.2.0"
    requirements_model = RadarRequirements
    # Design contract for `aces design` (audit R1): the objective that orders pareto.json and the report,
    # the model settings recorded with the run (none here), and the report.
    rank_objective = "neg_detection_range_km"

    def __init__(self, registry: Registry | None = None):
        self._fixed_registry = registry
        self._registries: dict[str | None, Registry] = {}

    def run_inputs(self) -> dict[str, Any]:
        return {}

    def report(self, spec: dict, result, record_hash: str | None) -> str:
        from .report import radar_report

        return radar_report(spec, result, record_hash, self.id, self.version)

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
        nx, ny = round(float(params["nx"])), round(float(params["ny"]))
        pitch = float(params["pitch_lambda"])
        bw = float(params["chirp_bw_hz"])
        tau = float(params["pulse_width_s"])
        prf = float(params["prf_hz"])
        p_el = float(params["element_power_w"])
        taper = str(params["taper"])
        bits = int(params["phase_bits"])
        n_el = nx * ny
        f = req.frequency_hz

        # Aperture and gain (with calibration on the array gain); scan loss at the edge of the sector
        g_array = array.array_gain_db(nx, ny, req.element_gain_db.value, taper, bits)
        f_gain = reg.get(KEY_GAIN)
        g_tx = g_array + 10.0 * math.log10(f_gain.value)
        width_m, height_m = array.aperture_size_m(nx, ny, pitch, f)
        aperture_m2 = width_m * height_m
        scan_req = math.radians(req.scan_half_angle_deg)
        scan_loss = array.scan_loss_db(scan_req, req.element_pattern_exponent.value)
        g_edge = g_tx - scan_loss

        # Transmit: the waveform sets the duty; it is never clipped to the PA rating
        p_peak = n_el * p_el
        duty = tau * prf
        p_avg_rf = p_peak * duty
        dc_w = p_avg_rf / req.pa_efficiency.value + n_el * req.support_dc_w_per_element.value
        f_tx = reg.get(KEY_TX_LOSS)
        tx_loss_db = req.tx_line_loss_db.value + 10.0 * math.log10(f_tx.value)

        # Receive cascade (with calibration on the noise figure) and system noise temperature
        stages = [cascade.Stage(s.name, s.gain_db, s.nf_db, s.op1db_dbm) for s in req.rx_chain]
        rx = cascade.cascade(stages)
        f_nf = reg.get(KEY_NF)
        nf_db = rx.nf_db + 10.0 * math.log10(f_nf.value)
        ts_k = linkbudget.system_noise_temperature_k(nf_db, req.antenna_noise_temperature_k.value)

        # Detection.  Coherent: the CPI's n pulses of energy against the n = 1 Albersheim SNR (pairing
        # Albersheim(n) with an n-pulse energy budget would count integration twice).  Noncoherent: one
        # pulse of energy against Albersheim(n).
        det = req.detection
        if det.integration == "coherent":
            snr_req, n_energy = linkbudget.albersheim_snr_db(det.pd, det.pfa, 1), det.cpi_pulses
        else:
            snr_req, n_energy = linkbudget.albersheim_snr_db(det.pd, det.pfa, det.cpi_pulses), 1
        losses = req.system_losses_db.value + tx_loss_db

        def snr_range(g_db: float) -> float:
            return linkbudget.max_range_m(p_peak, tau, n_energy, g_db, g_db, f, req.target_rcs_m2, nf_db, losses,
                                          snr_req, system_noise_temp_k=ts_k)

        r_broadside, r_edge = snr_range(g_tx), snr_range(g_edge)
        blind = linkbudget.blind_range_m(tau)
        # No detection is claimed inside the blind range.
        det_broadside = r_broadside if r_broadside > blind else 0.0
        det_edge = r_edge if r_edge > blind else 0.0

        # Waveform and scan metrics
        res = linkbudget.range_resolution_m(bw)
        unamb_r = linkbudget.unambiguous_range_m(prf)
        doppler = linkbudget.doppler_resolution_m_s(prf, det.cpi_pulses, f)
        scan_limit = array.grating_lobe_scan_limit_rad(pitch)

        # Thermal: DC not radiated is dissipated (PA and support electronics)
        diss_w = dc_w - p_avg_rf
        diss_density = diss_w / (aperture_m2 * 1e4)

        objectives = {
            "neg_detection_range_km": -det_edge / 1000.0,
            "dc_power_kw": dc_w / 1000.0,
        }
        constraints = {
            "range_margin_m": req.min_range_m - det_edge,
            "blind_range_margin_m": blind - req.max_blind_range_m,
            "resolution_margin_m": res - req.max_range_resolution_m,
            "scan_margin_rad": scan_req - scan_limit,
            "unambiguous_range_margin_m": req.min_range_m - unamb_r,
            "duty_margin": duty - req.duty_max.value,
            "thermal_margin_w_cm2": diss_density - req.max_dissipation_w_cm2,
            "dc_margin_w": dc_w - req.max_dc_power_w,
            "aperture_margin_mm": max(width_m, height_m) * 1e3 - req.max_aperture_mm,
        }

        warnings = [*ALWAYS_WARN, INTEGRATION_WARN[det.integration]]
        uncal: list[str] = []
        for key, fac in ((KEY_NF, f_nf), (KEY_TX_LOSS, f_tx), (KEY_GAIN, f_gain)):
            if not fac.calibrated:
                uncal.append(key)
            else:
                oor = fac.out_of_range(frequency_hz=f)
                if oor:
                    warnings.append(f"calibration factor {key} used outside fitted range: {', '.join(oor)}")
        if duty > req.duty_max.value:
            warnings.append(f"waveform duty {duty:.1%} (tau x PRF) exceeds the PA's rated duty_max "
                            f"{req.duty_max.value:.1%}: infeasible. Power, DC and heat are reported at the waveform "
                            "duty, not clipped")
        if r_edge <= blind:
            where = "anywhere" if r_broadside <= blind else "at the scan edge"
            warnings.append(f"no detection claimed {where}: SNR is met only out to {r_edge:.0f} m at the scan edge "
                            f"and {r_broadside:.0f} m at broadside, inside the blind range c*tau/2 = {blind:.0f} m")
        if det_broadside > unamb_r:
            warnings.append(f"detection range {det_broadside / 1000:.1f} km exceeds the unambiguous range "
                            f"{unamb_r / 1000:.1f} km: returns from beyond it fold in range (not modelled)")
        if rx.output_p1db_dbm is None:
            warnings.append("rx P1dB cascade not computed: not every rx_chain stage gives op1db_dbm")

        metrics = {
            "n_elements": n_el,
            "aperture_x_mm": round(width_m * 1e3, 1),
            "aperture_y_mm": round(height_m * 1e3, 1),
            "array_gain_db": round(g_tx, 2),
            "scan_loss_db": round(scan_loss, 2),
            "eirp_dbw": round(10.0 * math.log10(p_peak) + g_tx, 1),
            "peak_power_w": round(p_peak, 1),
            "avg_rf_power_w": round(p_avg_rf, 1),
            "duty": round(duty, 4),
            "duty_max": req.duty_max.value,
            "rx_nf_db": round(nf_db, 2),
            "system_noise_temp_k": round(ts_k, 1),
            "rx_gain_db": round(rx.gain_db, 1),
            "rx_output_p1db_dbm": None if rx.output_p1db_dbm is None else round(rx.output_p1db_dbm, 2),
            "rx_input_p1db_dbm": None if rx.input_p1db_dbm is None else round(rx.input_p1db_dbm, 2),
            "integration": det.integration,
            "snr_required_db": round(snr_req, 2),
            "detection_range_km": round(det_broadside / 1000.0, 2),
            "detection_range_scan_edge_km": round(det_edge / 1000.0, 2),
            "blind_range_m": round(blind, 1),
            "range_resolution_m": round(res, 3),
            "unambiguous_range_km": round(unamb_r / 1000.0, 1),
            "doppler_resolution_m_s": round(doppler, 2),
            "beamwidth_x_deg": round(math.degrees(array.beamwidth_rad(nx, pitch, taper)), 2),
            "beamwidth_y_deg": round(math.degrees(array.beamwidth_rad(ny, pitch, taper)), 2),
            "grating_scan_limit_deg": round(math.degrees(scan_limit), 1),
            "dissipation_w_cm2": round(diss_density, 2),
            "dc_power_w": round(dc_w, 1),
        }
        return Evaluation(params=dict(params), objectives=objectives, constraints=constraints,
                          metrics=metrics, warnings=warnings, uncalibrated=uncal)
