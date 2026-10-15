"""Tests for the radar domain: physics references, the design model, calibration, EM adapter and CLI.

Every requirement, datasheet value and measurement below is a TEST INPUT that exists only to exercise the
code. None of it is AERIS data, a datasheet value or a measured result, and none of it may be quoted.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import math
import subprocess
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from aces import evidence, manufacturing, spec
from aces.calibration import Factor, Registry
from aces.cli import main
from aces.domains.radar import (
    PhasedArrayRadar,
    RadarRequirements,
    array,
    cascade,
    em_adapter,
    linkbudget,
)
from aces.domains.radar import calibrate as rcal
from aces.domains.radar.model import Detection

ROOT = Path(__file__).resolve().parents[1]
PCB_REAL = manufacturing.PROCESSES["pcb_antenna"]  # the real (planned) route, captured before any patching


@pytest.fixture(autouse=True)
def _pcb_route_available(monkeypatch):
    """TEST ONLY: treat the planned pcb_antenna route as available, so model tests can build requirements.
    Tests of the real route put PCB_REAL back."""
    monkeypatch.setitem(manufacturing.PROCESSES, "pcb_antenna", dataclasses.replace(PCB_REAL, status="available"))


def _src(v):
    return {"value": v, "source": "test input"}


# TEST INPUTS ONLY (see module docstring).
REQ = {
    "frequency_hz": 10.5e9,
    "target_rcs_m2": 1.0,
    "detection": {"pd": 0.9, "pfa": 1e-6, "cpi_pulses": 64, "integration": "coherent"},
    "min_range_m": 8_000,
    "max_blind_range_m": 2_000,
    "max_range_resolution_m": 0.2,
    "scan_half_angle_deg": 45,
    "element_gain_db": _src(6.0),
    "element_pattern_exponent": _src(1.5),
    "antenna_noise_temperature_k": _src(290.0),
    "system_losses_db": _src(4.0),
    "tx_line_loss_db": _src(1.0),
    "pa_efficiency": _src(0.30),
    "duty_max": _src(0.30),
    "support_dc_w_per_element": _src(0.5),
    "rx_chain": [
        {"name": "lna", "gain_db": 20.0, "nf_db": 2.2, "op1db_dbm": 12.0, "source": "test input"},
        {"name": "ps", "gain_db": -6.5, "nf_db": 6.5, "source": "test input"},
    ],
    "max_dc_power_w": 6_000,
    "max_dissipation_w_cm2": 5.0,
    "max_aperture_mm": 300,
    "manufacturing": {"process": "pcb_antenna"},
}


def _req(**over):
    data = copy.deepcopy(REQ)
    data.update(over)
    return RadarRequirements(**data)


def _params(**over):
    p = {"nx": 8.0, "ny": 8.0, "pitch_lambda": 0.5, "chirp_bw_hz": 1e9, "pulse_width_s": 50e-6,
         "prf_hz": 6e3, "element_power_w": 25.0, "taper": "uniform", "phase_bits": 6}
    p.update(over)
    return p


LAM = linkbudget.C0 / 10.5e9  # 0.0285517 m at 10.5 GHz


# ------------------------------------------------------------------- array math

def test_beamwidth_16el_half_lambda():
    # 16 elements at lambda/2, uniform: 0.886 / 8 rad = 6.34 deg
    bw = array.beamwidth_rad(16, 0.5)
    assert math.degrees(bw) == pytest.approx(6.34, abs=0.05)


def test_grating_lobe_limits():
    assert array.grating_lobe_scan_limit_rad(0.5) == pytest.approx(math.pi / 2)
    # d = 0.6 lambda -> asin(1/0.6 - 1) = 41.8 deg
    assert math.degrees(array.grating_lobe_scan_limit_rad(0.6)) == pytest.approx(41.8, abs=0.1)
    assert array.grating_lobe_scan_limit_rad(1.0) == 0.0


def test_phase_quantization_loss_is_a_loss():
    # Error uniform over one LSB: phi ~ U(-a, a), a = pi/2^B.  |E exp(j phi)|^2 = (sin a / a)^2.
    # 3 bits: a = pi/8 = 0.392699, sin a = 0.382683, ratio 0.974495, squared 0.949641 -> 0.2244 dB loss
    #   (the ~0.22 dB of Mailloux's 1 - pi^2/(3*2^(2B)) = 0.9486 -> 0.229 dB, first order).
    # 6 bits: a = pi/64 -> 0.00349 dB.
    sigma, loss3 = array.phase_quantization(3)
    assert sigma == pytest.approx(math.pi / (math.sqrt(3) * 8), rel=1e-12)
    assert loss3 == pytest.approx(0.2244, abs=1e-3)
    assert array.phase_quantization(6)[1] == pytest.approx(0.00349, abs=1e-4)
    # coarser phase shifters must LOSE gain (the old code added it)
    g = {b: array.array_gain_db(8, 8, 6.0, "uniform", b) for b in (3, 4, 6)}
    g_ideal = array.array_gain_db(8, 8, 6.0, "uniform", None)
    assert g[3] < g[4] < g[6] < g_ideal
    assert g_ideal - g[3] == pytest.approx(0.2244, abs=1e-3)
    assert array.rms_sidelobe_ratio(6, 64) > 0


def test_array_gain_uniform():
    # 64 el, 6 dBi element, uniform, no quantization: 6 + 10log10(64) = 24.06 dBi
    assert array.array_gain_db(8, 8, 6.0) == pytest.approx(24.06, abs=0.01)


def test_planar_taper_is_separable():
    # Hamming one-axis efficiency 0.73 (table); separable planar taper: 0.73 * 0.73 = 0.5329 -> -2.733 dB.
    # 8x8, 6 dBi: 6 + 18.062 - 2.733 = 21.33 dBi (the old code applied 0.73 once: 22.70 dBi).
    assert array.planar_taper_efficiency("hamming") == pytest.approx(0.5329, abs=1e-9)
    assert array.array_gain_db(8, 8, 6.0, "hamming") == pytest.approx(21.33, abs=0.01)


def test_scan_loss_cos_n():
    # n = 1.5 at 45 deg: -10 * 1.5 * log10(cos 45) = -15 * (-0.150515) = 2.2577 dB one way
    assert array.scan_loss_db(math.radians(45), 1.5) == pytest.approx(2.2577, abs=1e-4)
    assert array.scan_loss_db(math.radians(45), 0.0) == 0.0
    with pytest.raises(ValueError):
        array.scan_loss_db(math.radians(90), 1.0)
    with pytest.raises(ValueError):
        array.scan_loss_db(0.3, -1.0)


def test_aperture_size():
    # 16 x 4 at lambda/2, 10.5 GHz: d = 14.276 mm -> 228.4 mm x 57.1 mm
    w, h = array.aperture_size_m(16, 4, 0.5, 10.5e9)
    assert w * 1e3 == pytest.approx(16 * 0.5 * LAM * 1e3, rel=1e-12) == pytest.approx(228.41, abs=0.01)
    assert h * 1e3 == pytest.approx(57.10, abs=0.01)


# ------------------------------------------------------------------- cascade

def test_friis_two_stage():
    s = cascade.cascade([
        cascade.Stage("lna", 10.0, 2.0),
        cascade.Stage("mixer", 20.0, 5.0),
    ])
    # F = 1.5849 + (3.1623-1)/10 = 1.8011 -> 2.555 dB
    assert s.nf_db == pytest.approx(2.555, abs=0.001)
    assert s.gain_db == pytest.approx(30.0)


def test_friis_template_chain():
    # Template chain: F = 1.65959 + 3.46684/100 + 4.01187/(100*0.223872) + 4.62341/(22.3872*15.8489)
    #               = 1.65959 + 0.03467 + 0.17920 + 0.01303 = 1.88649 -> 2.757 dB
    s = cascade.cascade([cascade.Stage("lna", 20.0, 2.2), cascade.Stage("ps", -6.5, 6.5),
                         cascade.Stage("buf", 12.0, 7.0), cascade.Stage("comb", -7.5, 7.5)])
    assert s.nf_db == pytest.approx(2.757, abs=0.001)
    assert s.gain_db == pytest.approx(18.0)


def test_cascade_p1db_output_referred():
    # Output-referred, mW: 1/P = 1/(OP1_a * G_b) + 1/OP1_b.
    # a: G 20 dB, OP1 0 dBm (1 mW); b: G 5 dB (3.16228), OP1 30 dBm (1000 mW)
    #   1/P = 1/3.16228 + 1/1000 = 0.316228 + 0.001 = 0.317228 -> 3.15230 mW = 4.986 dBm
    #   (the old code added stage a's own gain: 20 dBm).  Input-referred: 4.986 - 25 = -20.014 dBm.
    s = cascade.cascade([cascade.Stage("a", 20.0, 3.0, op1db_dbm=0.0),
                         cascade.Stage("b", 5.0, 3.0, op1db_dbm=30.0)])
    assert s.output_p1db_dbm == pytest.approx(4.986, abs=1e-3)
    assert s.input_p1db_dbm == pytest.approx(-20.014, abs=1e-3)
    # equal gains: 1/P = 1/(1 * 10) + 1/31.6228 = 0.131623 -> 7.5975 mW = 8.807 dBm
    s2 = cascade.cascade([cascade.Stage("a", 10.0, 3.0, op1db_dbm=0.0),
                          cascade.Stage("b", 10.0, 3.0, op1db_dbm=15.0)])
    assert s2.output_p1db_dbm == pytest.approx(8.807, abs=1e-3)
    # not every stage declares one -> not computed
    assert cascade.cascade([cascade.Stage("a", 10.0, 3.0, 0.0), cascade.Stage("b", 1.0, 1.0)]).output_p1db_dbm is None


# ------------------------------------------------------------------- link budget

def test_albersheim_single_pulse():
    # n=1, Pd 0.9, Pfa 1e-6: A = ln(0.62e6) = 13.3374, B = ln 9 = 2.1972,
    # A + 0.12AB + 1.7B = 20.589; (6.2 + 4.54/sqrt(1.44)) * log10(20.589) = 9.9833 * 1.31365 = 13.11 dB
    assert linkbudget.albersheim_snr_db(0.9, 1e-6, 1) == pytest.approx(13.11, abs=0.01)


def test_albersheim_refused_outside_its_fitted_range():
    # Fitted for 0.1 <= Pd <= 0.9, 1e-7 <= Pfa <= 1e-3, 1 <= n <= 8096 (Albersheim 1981; rule 5).
    for args in ((0.95, 1e-6, 1), (0.05, 1e-6, 1), (0.9, 1e-8, 1), (0.9, 1e-2, 1), (0.9, 1e-6, 8097)):
        with pytest.raises(ValueError, match="not valid"):
            linkbudget.albersheim_snr_db(*args)
    linkbudget.albersheim_snr_db(0.1, 1e-7, 8096)  # the corners are inside
    with pytest.raises(ValidationError):
        Detection(pd=0.95, integration="coherent")
    with pytest.raises(ValidationError):
        Detection(pfa=1e-8, integration="coherent")
    with pytest.raises(ValidationError):
        Detection(cpi_pulses=9000, integration="noncoherent")


def test_range_equation_project_a_baseline():
    # Cross-check against the redesign study's computed Project-A baseline:
    # 160 W peak, EIRP ~40 dBW (G=18 dB), 100 us pulse, NF 5 dB, 1 m^2 -> 2505 m here (~2.7 km there).
    r = linkbudget.max_range_m(
        p_peak_w=160.0, tau_s=100e-6, n_pulses=1,
        gain_tx_db=18.0, gain_rx_db=18.0, frequency_hz=10.5e9,
        rcs_m2=1.0, nf_db=5.0, losses_db=4.0, snr_req_db=13.2,
    )
    assert r == pytest.approx(2505.06, abs=0.1)
    # Ts = Ta + T0 (F - 1) with Ta = T0 is the same thing
    ts = linkbudget.system_noise_temperature_k(5.0, 290.0)
    assert ts == pytest.approx(290.0 * 10 ** 0.5, rel=1e-12)
    assert linkbudget.max_range_m(160.0, 100e-6, 1, 18.0, 18.0, 10.5e9, 1.0, 5.0, 4.0, 13.2,
                                  system_noise_temp_k=ts) == pytest.approx(r, rel=1e-12)


def test_x64_vs_project_a_ratio():
    # X64 target column: 1.6 kW, 64 el (G~24 dB), 50 us, NF 3.5 dB, 64-pulse
    # coherent CPI vs the study's single-pulse Project-A baseline (~2.5 km here).
    snr_1 = linkbudget.albersheim_snr_db(0.9, 1e-6, 1)
    r_a = linkbudget.max_range_m(160.0, 100e-6, 1, 18.0, 18.0, 10.5e9, 1.0, 5.0, 4.0, 13.2)
    r_x = linkbudget.max_range_m(1600.0, 50e-6, 64, 24.0, 24.0, 10.5e9, 1.0, 3.5, 4.0, snr_1)
    assert 6.0 < r_x / r_a < 12.0
    # Single-pulse pairing reproduces the study's own ~10.5 km headline (conservative)
    r_x_sp = linkbudget.max_range_m(1600.0, 50e-6, 1, 24.0, 24.0, 10.5e9, 1.0, 3.5, 4.0, snr_1)
    assert 7.0 < r_x_sp / 1000 < 13.0


def test_waveform_metrics():
    assert linkbudget.range_resolution_m(1e9) == pytest.approx(0.1499, abs=1e-3)
    assert linkbudget.unambiguous_range_m(6000) == pytest.approx(24_965, rel=1e-3)
    assert linkbudget.doppler_resolution_m_s(6000, 64, 10.5e9) == pytest.approx(1.34, abs=0.05)


def test_blind_range():
    # c * tau / 2 = 299792458 * 50e-6 / 2 = 7494.81 m
    assert linkbudget.blind_range_m(50e-6) == pytest.approx(7494.81, abs=0.01)


# ------------------------------------------------------------------- model

def test_model_x64_point():
    ev = PhasedArrayRadar().evaluate(_params(), _req())
    m = ev.metrics
    assert m["n_elements"] == 64
    assert m["eirp_dbw"] == pytest.approx(56.1, abs=0.3)  # study: ~57 dBW
    assert m["range_resolution_m"] == pytest.approx(0.15, abs=0.01)
    assert m["detection_range_km"] > 8.0
    assert set(ev.uncalibrated) == {"radar.rx.nf_factor", "radar.tx.line_loss_factor", "radar.array.gain_factor"}
    assert ev.warnings  # fast-tier caveats always present
    # Broadside range is the hand-checked range equation with this point's own inputs (Ta = T0 here)
    rx = cascade.cascade([cascade.Stage("lna", 20.0, 2.2), cascade.Stage("ps", -6.5, 6.5)])
    r = linkbudget.max_range_m(1600.0, 50e-6, 64, m["array_gain_db"], m["array_gain_db"], 10.5e9, 1.0, rx.nf_db,
                               4.0 + 1.0, linkbudget.albersheim_snr_db(0.9, 1e-6, 1))
    assert m["detection_range_km"] == pytest.approx(r / 1000, abs=0.01)


def test_model_thermal_and_dc():
    # 64 x 25 W, duty 0.3: 480 W RF average; DC = 480 / 0.30 + 64 * 0.5 = 1632 W; heat 1632 - 480 = 1152 W
    # over (8 * 14.2758 mm)^2 = 130.43 cm2 -> 8.832 W/cm2 (no hidden 20 % overhead any more)
    m = PhasedArrayRadar().evaluate(_params(), _req()).metrics
    assert m["dc_power_w"] == pytest.approx(1632.0, abs=0.1)
    area_cm2 = (8 * 0.5 * LAM * 100) ** 2
    assert m["dissipation_w_cm2"] == pytest.approx(1152.0 / area_cm2, abs=0.01) == pytest.approx(8.83, abs=0.01)


def test_model_duty_is_not_clipped():
    # 50 us at 10 kHz = 50 % duty > 30 % rating: infeasible, and power/heat at the real duty:
    # 1600 W * 0.5 = 800 W RF; DC = 800 / 0.3 + 32 = 2698.7 W (the old code clipped to 480 W RF silently)
    ev = PhasedArrayRadar().evaluate(_params(prf_hz=10e3), _req())
    assert ev.metrics["duty"] == pytest.approx(0.5)
    assert ev.metrics["avg_rf_power_w"] == pytest.approx(800.0)
    assert ev.metrics["dc_power_w"] == pytest.approx(800.0 / 0.3 + 32.0, abs=0.1)
    assert ev.constraints["duty_margin"] == pytest.approx(0.2)
    assert not ev.feasible
    assert any("exceeds the PA's rated duty_max" in w for w in ev.warnings)


def test_model_aperture_uses_width_and_height():
    # 16 x 4 at lambda/2: 228.4 mm x 57.1 mm; sqrt(area) = 114.2 mm would pass a 200 mm limit, the width fails
    ev = PhasedArrayRadar().evaluate(_params(nx=16.0, ny=4.0), _req(max_aperture_mm=200))
    assert ev.metrics["aperture_x_mm"] == pytest.approx(228.4, abs=0.05)
    assert ev.metrics["aperture_y_mm"] == pytest.approx(57.1, abs=0.05)
    assert ev.constraints["aperture_margin_mm"] == pytest.approx(16 * 0.5 * LAM * 1e3 - 200, abs=1e-6)
    assert ev.constraints["aperture_margin_mm"] > 0


def test_model_phase_bits_cost_gain():
    g = {b: PhasedArrayRadar().evaluate(_params(phase_bits=b), _req(phase_bits_options=(4, 6))).metrics["array_gain_db"]
         for b in (4, 6)}
    # 4 bits: a = pi/16, loss 0.0559 dB; 6 bits 0.0035 dB
    assert g[4] < g[6]
    assert g[6] - g[4] == pytest.approx(0.0559 - 0.0035, abs=0.01)


def test_model_scan_loss_at_the_scan_edge():
    # Two-way scan loss 2 x 2.2577 dB -> range x 10^(-4.5154/40) = 0.7711 at 45 deg with n = 1.5
    ev = PhasedArrayRadar().evaluate(_params(pulse_width_s=20e-6), _req())
    m = ev.metrics
    assert m["scan_loss_db"] == pytest.approx(2.26, abs=0.005)
    assert m["detection_range_scan_edge_km"] / m["detection_range_km"] == pytest.approx(0.7711, abs=0.002)
    # the requirement is held at the scan edge
    assert ev.constraints["range_margin_m"] == pytest.approx(8000 - m["detection_range_scan_edge_km"] * 1000, abs=6)
    flat = PhasedArrayRadar().evaluate(_params(pulse_width_s=20e-6),
                                       _req(element_pattern_exponent=_src(0.0))).metrics
    assert flat["detection_range_scan_edge_km"] == flat["detection_range_km"]


def test_model_blind_range():
    ev = PhasedArrayRadar().evaluate(_params(), _req())
    assert ev.metrics["blind_range_m"] == pytest.approx(7494.8, abs=0.1)
    assert ev.constraints["blind_range_margin_m"] == pytest.approx(7494.81 - 2000, abs=0.01)
    assert not ev.feasible
    # 200 us pulse (blind 29.98 km) with 16 x 1 W: SNR is met only inside the blind zone -> no detection claimed
    ev2 = PhasedArrayRadar().evaluate(_params(nx=4.0, ny=4.0, pulse_width_s=200e-6, prf_hz=1e3, element_power_w=1.0),
                                      _req())
    assert ev2.metrics["detection_range_km"] == 0.0
    assert ev2.metrics["detection_range_scan_edge_km"] == 0.0
    assert ev2.objectives["neg_detection_range_km"] == 0.0
    assert any("no detection claimed" in w for w in ev2.warnings)


def test_model_warns_beyond_the_unambiguous_range():
    # 6 kHz PRF: c / (2 PRF) = 24.98 km; the X64 point reaches 23.6 km broadside (no warning), 20 kHz gives 7.5 km
    assert not any("unambiguous" in w for w in PhasedArrayRadar().evaluate(_params(), _req()).warnings)
    ev = PhasedArrayRadar().evaluate(_params(prf_hz=20e3, pulse_width_s=10e-6), _req())
    assert ev.metrics["unambiguous_range_km"] == pytest.approx(7.5, abs=0.05)
    assert any("exceeds the unambiguous range" in w for w in ev.warnings)


def test_model_integration_labels():
    # Coherent: 64 x energy vs Albersheim(1) = 13.11 dB -> 18.06 dB of credit.
    # Noncoherent: 1 x energy vs Albersheim(64) = -0.143 dB -> 13.11 + 0.143 = 13.26 dB of credit.
    # Range ratio 10^((18.062 - 13.258)/40) = 1.3186.
    coh = PhasedArrayRadar().evaluate(_params(pulse_width_s=20e-6), _req())
    det = dict(REQ["detection"], integration="noncoherent")
    nc = PhasedArrayRadar().evaluate(_params(pulse_width_s=20e-6), _req(detection=det))
    assert nc.metrics["snr_required_db"] == pytest.approx(-0.14, abs=0.01)
    assert coh.metrics["detection_range_km"] / nc.metrics["detection_range_km"] == pytest.approx(1.3186, abs=0.002)
    assert any("coherent integration" in w and "noncoherent" not in w for w in coh.warnings)
    assert any("noncoherent integration" in w for w in nc.warnings)


def test_model_antenna_noise_temperature():
    # Chain F = 1.65959 + 3.46684/100 = 1.69426.  Ts(290 K) = 290 F = 491.3 K; Ts(100 K) = 100 + 290 * 0.69426
    # = 301.3 K.  Range ratio (491.34 / 301.34)^(1/4) = 1.1300.
    hot = PhasedArrayRadar().evaluate(_params(pulse_width_s=20e-6), _req()).metrics
    cold = PhasedArrayRadar().evaluate(_params(pulse_width_s=20e-6),
                                       _req(antenna_noise_temperature_k=_src(100.0))).metrics
    assert hot["system_noise_temp_k"] == pytest.approx(491.3, abs=0.1)
    assert cold["system_noise_temp_k"] == pytest.approx(301.3, abs=0.1)
    assert cold["detection_range_km"] / hot["detection_range_km"] == pytest.approx(1.1300, abs=0.002)


def test_model_constraint_violations():
    ev = PhasedArrayRadar().evaluate(_params(pitch_lambda=0.75), _req(scan_half_angle_deg=45))
    assert ev.constraints["scan_margin_rad"] > 0  # 0.75 lambda cannot scan 45 deg
    assert not ev.feasible
    ev2 = PhasedArrayRadar().evaluate(_params(element_power_w=1.0, pulse_width_s=10e-6), _req())
    assert ev2.constraints["range_margin_m"] > 0  # 64 W peak, 10 us pulses cannot reach 8 km at the scan edge
    assert not ev2.feasible


def test_model_calibrated_nf_changes_result():
    reg = Registry()
    reg.set(Factor(key="radar.rx.nf_factor", value=1.2, sources=("simlab:test",),
                   valid_range={"frequency_hz": (9e9, 12e9)}, fitted_on="2026-10-07"))
    ev0 = PhasedArrayRadar().evaluate(_params(), _req())
    ev1 = PhasedArrayRadar(registry=reg).evaluate(_params(), _req())
    assert ev1.metrics["rx_nf_db"] > ev0.metrics["rx_nf_db"]
    assert ev1.metrics["detection_range_km"] < ev0.metrics["detection_range_km"]
    assert "radar.rx.nf_factor" not in ev1.uncalibrated


def test_requirements_validation():
    with pytest.raises(ValidationError):
        RadarRequirements(frequency_hz=-1)  # missing most fields and bad frequency


@pytest.mark.parametrize("field", ["element_gain_db", "element_pattern_exponent", "antenna_noise_temperature_k",
                                   "system_losses_db", "tx_line_loss_db", "pa_efficiency", "duty_max",
                                   "support_dc_w_per_element"])
def test_unsourced_inputs_are_refused(field):
    data = copy.deepcopy(REQ)
    data.pop(field)
    with pytest.raises(spec.SpecError, match=f"requirements.{field}: missing"):
        spec.parse({"aces": 1, "domain": "radar.phased_array", "project": "t", "owner": "t", "rd_activity": "t",
                    "hypothesis": "t", "requirements": data}, RadarRequirements)
    data[field] = {"value": REQ[field]["value"], "source": "  "}
    with pytest.raises(ValidationError, match="source"):
        RadarRequirements(**data)
    data[field] = {"value": REQ[field]["value"]}
    with pytest.raises(ValidationError):
        RadarRequirements(**data)


def test_template_lists_what_is_missing(capsys, monkeypatch):
    monkeypatch.setitem(manufacturing.PROCESSES, "pcb_antenna", PCB_REAL)
    assert main(["check", str(ROOT / "specs/templates/radar_phased_array.yaml")]) == 2
    out = capsys.readouterr().out
    for field in ("pa_efficiency.value", "duty_max.source", "element_pattern_exponent.value", "max_blind_range_m"):
        assert f"requirements.{field}: missing" in out
    assert "planned" in out


# ------------------------------------------------------------------- manufacturing route (rule 13a)

def test_radar_route_is_planned_and_refused(tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(manufacturing.PROCESSES, "pcb_antenna", PCB_REAL)
    assert PCB_REAL.status == "planned" and "radar.phased_array" in PCB_REAL.domains
    with pytest.raises(ValidationError, match="planned"):
        _req()
    data = copy.deepcopy(REQ)
    data.pop("manufacturing")
    with pytest.raises(ValidationError, match="manufacturing"):
        RadarRequirements(**data)
    sp = _radar_spec(tmp_path)
    assert main(["check", str(sp)]) == 2
    assert "planned" in capsys.readouterr().out
    assert main(["design", str(sp), "--out", str(tmp_path / "run"), "--ledger", str(tmp_path / "l.jsonl"),
                 "--trials", "8"]) == 2
    assert not (tmp_path / "run" / "run.json").exists()


# ------------------------------------------------------------------- `aces design`: one test per domain (R1)

def _radar_spec(tmp_path, **req_over):
    data = {"aces": 1, "domain": "radar.phased_array", "project": "radar-unit-test", "owner": "test",
            "rd_activity": "ACES radar unit tests (test inputs only)",
            "hypothesis": "The radar design path completes on test inputs.",
            "requirements": {**copy.deepcopy(REQ), **req_over}, "search": {"trials": 8, "population": 8, "seed": 1}}
    sp = tmp_path / "radar.yaml"
    sp.write_text(yaml.safe_dump(data))
    return sp


def _check_design_run(run: Path, ledger: Path, model_id: str) -> dict:
    _, rec = evidence.load_verified_run(run, ledger)  # run.json, its ledger record and pareto.json agree
    assert rec["kind"] == "aces.design_run" and rec["model"]["id"] == model_id
    assert main(["ledger", "verify", str(ledger)]) == 0
    report = (run / "report.md").read_text()
    assert report.count("Not verified") == 1
    assert (run / "evaluations.jsonl").read_text().count("\n") == 8
    return rec


def test_design_cli_hx_completes(tmp_path, spec_dict):
    sp = tmp_path / "hx.yaml"
    sp.write_text(yaml.safe_dump(spec_dict))
    ledger = tmp_path / "ledger.jsonl"
    rc = main(["design", str(sp), "--out", str(tmp_path / "run"), "--ledger", str(ledger), "--trials", "8"])
    assert rc in (0, 1)  # 1 = no feasible design in 8 trials, still a complete run
    rec = _check_design_run(tmp_path / "run", ledger, "hx.two_fluid")
    assert rec["inputs"]["resolution"] == 64  # hx's run input, unchanged
    pareto = json.loads((tmp_path / "run" / "pareto.json").read_text())
    assert [p["objectives"]["mass_kg"] for p in pareto] == sorted(p["objectives"]["mass_kg"] for p in pareto)


def test_design_cli_radar_completes(tmp_path):
    sp = _radar_spec(tmp_path)
    ledger = tmp_path / "ledger.jsonl"
    rc = main(["design", str(sp), "--out", str(tmp_path / "run"), "--ledger", str(ledger), "--trials", "8"])
    assert rc in (0, 1)
    rec = _check_design_run(tmp_path / "run", ledger, "radar.phased_array")
    assert "resolution" not in rec["inputs"]
    report = (tmp_path / "run" / "report.md").read_text()
    assert "DSGL" in report and "Inputs and their sources" in report
    pareto = json.loads((tmp_path / "run" / "pareto.json").read_text())
    rng = [p["objectives"]["neg_detection_range_km"] for p in pareto]
    assert rng == sorted(rng)


def test_design_contract_is_explicit():
    from aces.cli import DesignContract
    from aces.domains.hx import TwoFluidTPMS

    c = DesignContract.of(PhasedArrayRadar())
    assert c.rank_objective == "neg_detection_range_km" and c.run_inputs == {}
    h = DesignContract.of(TwoFluidTPMS())
    assert h.rank_objective == "mass_kg" and h.run_inputs == {"resolution": 64}

    class Bare:
        id = "x.bare"

    with pytest.raises(ValueError, match="design contract"):
        DesignContract.of(Bare())


# ------------------------------------------------------------------- calibration (D18)

def _rows(values=(1.4, 1.5, 1.6), freqs=(10.0e9, 10.5e9, 11.0e9), model=1.0, **extra):
    return [{"kind": "tile_insertion_loss_db", "frequency_hz": f, "value": v, "model_value": model,
             "source": f"simlab:run-{i}", **extra} for i, (f, v) in enumerate(zip(freqs, values))]


def test_calibrate_fit_is_in_db():
    # Rows are dB: ratio = 10^((measured - model)/10).  Deltas 0.4, 0.5, 0.6 dB -> mean 0.5 dB ->
    # factor 10^0.05 = 1.12202 (the old code divided the dB values: (1.4 * 1.5 * 1.6)^(1/3) = 1.4979).
    held = {"kind": "tile_insertion_loss_db", "frequency_hz": 11.5e9, "value": 1.55, "model_value": 1.0,
            "source": "test:bench-1", "held_out": True}
    rows = rcal.check_rows([*_rows(), held])
    factors, summary, refused = rcal.fit(rows, fitted_on="2026-10-07")
    assert not refused
    (fac,) = factors
    assert fac.key == "radar.tx.line_loss_factor"
    assert fac.value == pytest.approx(10 ** 0.05, rel=1e-9)
    assert fac.valid_range["frequency_hz"] == (10.0e9, 11.0e9)
    assert set(fac.sources) == {"simlab:run-0", "simlab:run-1", "simlab:run-2"}
    # held-out: predicted 1.0 + 0.5 = 1.5 dB vs 1.55 measured -> 10^(0.005) - 1 = +1.16 %
    (chk,) = summary[0]["held_out"]
    assert chk["error"] == pytest.approx(10 ** 0.005 - 1, rel=1e-9) and chk["ok"]
    # the model applies it as +0.5 dB of TX loss
    reg = Registry()
    reg.set(fac)
    base = PhasedArrayRadar().evaluate(_params(), _req())
    cal = PhasedArrayRadar(registry=reg).evaluate(_params(), _req())
    assert "radar.tx.line_loss_factor" not in cal.uncalibrated
    # range scales as 10^(-0.5/40)
    assert cal.metrics["detection_range_km"] / base.metrics["detection_range_km"] == pytest.approx(
        10 ** (-0.5 / 40), abs=0.002)


def test_calibrate_refuses_d18_violations():
    held = {"kind": "tile_insertion_loss_db", "frequency_hz": 11.5e9, "value": 1.55, "model_value": 1.0,
            "source": "test:bench-1", "held_out": True}
    # three rows at one frequency are one condition
    _, _, refused = rcal.fit(rcal.check_rows([*_rows(freqs=(10e9, 10e9, 10e9)), held]))
    assert any("1 distinct condition" in r for r in refused)
    # scatter: deltas 0, 2, 4 dB -> ln ratios 0, 0.4605, 0.9210, sd 0.4605 > 0.15
    _, _, refused = rcal.fit(rcal.check_rows([*_rows(values=(1.0, 3.0, 5.0)), held]))
    assert any("scatter" in r for r in refused)
    # held-out miss: predicted 1.5 dB vs 2.0 measured -> +12.2 % > 10 %
    _, _, refused = rcal.fit(rcal.check_rows([*_rows(), dict(held, value=2.0)]))
    assert any("held-out" in r and "miss" in r for r in refused)
    # no held-out row, or one at a fitted frequency (not independent)
    _, _, refused = rcal.fit(rcal.check_rows(_rows()))
    assert any("held-out row(s) at unseen conditions" in r for r in refused)
    factors, summary, refused = rcal.fit(rcal.check_rows([*_rows(), dict(held, frequency_hz=10.5e9)]))
    assert not factors and summary[0]["held_out_not_independent"] == ["test:bench-1"]
    # an explicit opt-out is allowed and recorded by the caller
    factors, _, refused = rcal.fit(rcal.check_rows(_rows()), min_held_out=0)
    assert len(factors) == 1 and not refused


def test_calibrate_rows_need_provenance(tmp_path):
    for bad in ("", "x", "simlab:", "notes from a call"):
        with pytest.raises(ValueError, match="source"):
            rcal.check_rows([dict(_rows()[0], source=bad)])
    jf = tmp_path / "bad.json"
    jf.write_text(json.dumps([{"kind": "nope", "frequency_hz": 1e9, "value": 1.0,
                               "model_value": 1.0, "source": "test:x"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown kind"):
        rcal.read_rows(jf)
    cf = tmp_path / "rows.csv"
    cf.write_text("kind,frequency_hz,value,model_value,source,held_out\n"
                  "rx_nf_db,1e10,3.0,2.8,test:nf-1,false\nrx_nf_db,1.1e10,3.1,2.8,test:nf-2,yes\n")
    rows = rcal.read_rows(cf)
    assert [r["held_out"] for r in rows] == [False, True] and rows[0]["value"] == 3.0


def test_collect_from_experiment_sets_provenance(tmp_path):
    exp = tmp_path / "aces-em-t"
    row = _rows()[0] | {"source": "whatever the row claims"}
    for name, status, ev in (("c0", "FINISHED", True), ("c1", "RUNNING", True), ("c2", "FINISHED", False)):
        d = exp / name
        d.mkdir(parents=True)
        (d / "status.json").write_text(json.dumps({"status": status}))
        (d / "measurement.json").write_text(json.dumps(row))
        if ev:
            (d / "evidence.json").write_text("{}")
    rows, skipped = rcal.collect_from_experiment(exp)
    assert [r["source"] for r in rows] == ["simlab:aces-em-t:c0"]
    assert len(skipped) == 2


def test_cli_radar_calibrate(tmp_path):
    rows = [*_rows(), {"kind": "tile_insertion_loss_db", "frequency_hz": 11.5e9, "value": 1.55, "model_value": 1.0,
                       "source": "test:bench-1", "held_out": True}]
    meas = tmp_path / "rows.json"
    meas.write_text(json.dumps(rows))
    reg_path = tmp_path / "radar_registry.json"
    ledger = tmp_path / "ledger.jsonl"
    rc = main(["radar-calibrate", str(meas), "--registry", str(reg_path), "--project", "cal-test", "--owner", "test",
               "--rd-activity", "radar calibration test", "--ledger", str(ledger)])
    assert rc == 0
    reg = Registry.load(reg_path)
    assert reg.get("radar.tx.line_loss_factor").value == pytest.approx(10 ** 0.05, rel=1e-9)
    ok, _ = evidence.Ledger(ledger).verify()
    rec = evidence.Ledger(ledger).records()[-1]
    assert ok and rec["kind"] == "aces.calibration" and rec["company"] == "CPA"
    # a spec that names the registry is calibrated by it
    req = _req(calibration_file=str(reg_path))
    assert "radar.tx.line_loss_factor" not in PhasedArrayRadar().evaluate(_params(), req).uncalibrated
    # refused fits write nothing and still exit non-zero
    meas.write_text(json.dumps(_rows()))
    before = reg_path.read_text()
    assert main(["radar-calibrate", str(meas), "--registry", str(reg_path), "--project", "p", "--owner", "o",
                 "--rd-activity", "r", "--ledger", str(ledger)]) == 1
    assert reg_path.read_text() == before


def test_cli_radar_calibrate_refuses_symlinked_registry(tmp_path):
    meas = tmp_path / "rows.json"
    meas.write_text(json.dumps(_rows()))
    target = tmp_path / "elsewhere.json"
    target.write_text("{}")
    (tmp_path / "reg.json").symlink_to(target)
    assert main(["radar-calibrate", str(meas), "--registry", str(tmp_path / "reg.json"), "--min-held-out", "0",
                 "--project", "p", "--owner", "o", "--rd-activity", "r",
                 "--ledger", str(tmp_path / "l.jsonl")]) == 2
    assert target.read_text() == "{}"


# ------------------------------------------------------------------- EM adapter (solvers mocked)

PORT_S_CSV = (  # TEST INPUT: shaped like Palace's postpro/port-S.csv; not a solver result
    "            f (GHz),        |S[1][1]| (dB),     arg(S[1][1]) (deg.),        |S[2][1]| (dB),     arg(S[2][1]) (deg.)\n"
    "+9.000000000e+00,-2.000000000e+01,+1.0e+01,-1.200000000e+00,-3.0e+01\n"
    "+1.000000000e+01,-2.200000000e+01,+1.2e+01,-1.400000000e+00,-3.5e+01\n"
    "+1.100000000e+01,-2.100000000e+01,+1.1e+01,-1.600000000e+00,-4.0e+01\n"
)


def _em_case(tmp_path):
    (tmp_path / "tile.msh").write_text("test input: not a mesh")
    return {"structure": "tile", "freq_lo_hz": 9e9, "freq_hi_hz": 11e9, "n_freq": 3,
            "stackup": {"substrate_er": 3.5, "loss_tan": 0.003, "source": "test input"},
            "mesh": "tile.msh", "mesh_unit_m": 1e-3,
            "attributes": {"substrate": [1], "air": [2], "pec": [3]},
            "ports": [{"index": 1, "attributes": [4], "r_ohm": 50, "direction": "+X"},
                      {"index": 2, "attributes": [5], "r_ohm": 50, "direction": "-X"}],
            "model_loss_db": 1.0, "timeout_s": 60}


def _fake_palace(log="", csv=PORT_S_CSV, calls=None):
    def fake(cmd, *, cwd=None, env=None, timeout=None, **kw):
        if calls is not None:
            calls.append({"cmd": cmd, "cwd": cwd, "timeout": timeout, "env": env})
        (Path(cwd) / "postpro").mkdir(exist_ok=True)
        (Path(cwd) / "postpro" / "port-S.csv").write_text(csv)
        return subprocess.CompletedProcess(cmd, 0, log, "")
    return fake


def test_em_palace_reads_s21_and_converged_from_the_run(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(em_adapter.shutil, "which", lambda exe: f"/opt/{exe}")
    monkeypatch.setattr(em_adapter.proc, "run", _fake_palace(log="Completed 3 frequency steps\n", calls=calls))
    out = em_adapter.Adapter().run(_em_case(tmp_path), None, str(tmp_path))
    assert calls[0]["cmd"] == ["palace", "config.json"] and calls[0]["timeout"] == 60
    assert isinstance(calls[0]["cmd"], list) and calls[0]["env"] is not None
    # |S[2][1]| column, not |S[1][1]|: mid-band S21 -1.4 dB -> 1.4 dB insertion loss (old code: 22 dB)
    assert [r["s21_db"] for r in out["sweep"]] == [-1.2, -1.4, -1.6]
    assert out["converged"] == 1 and out["measurement_written"] == 1
    row = json.loads((tmp_path / "measurement.json").read_text())
    assert row["value"] == pytest.approx(1.4) and row["frequency_hz"] == pytest.approx(10e9)
    rcal.check_rows([row])  # readable by radar calibration
    cfg = json.loads((tmp_path / "config.json").read_text())
    assert cfg["Boundaries"]["PEC"]["Attributes"] == [3]
    assert [p["Index"] for p in cfg["Boundaries"]["LumpedPort"]] == [1, 2]
    assert cfg["Boundaries"]["LumpedPort"][0]["Excitation"] is True
    assert cfg["Domains"]["Materials"][0]["Permittivity"] == 3.5  # from the case, no default
    assert cfg["Solver"]["Driven"]["FreqStep"] == pytest.approx(1.0)


def test_em_palace_not_converged_writes_no_row(tmp_path, monkeypatch):
    monkeypatch.setattr(em_adapter.shutil, "which", lambda exe: f"/opt/{exe}")
    monkeypatch.setattr(em_adapter.proc, "run",
                        _fake_palace(log="Warning: GMRES solver did not converge in 400 iterations\n"))
    out = em_adapter.Adapter().run(_em_case(tmp_path), None, str(tmp_path))
    assert out["converged"] == 0 and out["measurement_written"] == 0
    assert not (tmp_path / "measurement.json").exists()
    # a sweep that stops short of the band is not converged either
    monkeypatch.setattr(em_adapter.proc, "run", _fake_palace(csv="\n".join(PORT_S_CSV.splitlines()[:3]) + "\n"))
    out = em_adapter.Adapter().run(_em_case(tmp_path), None, str(tmp_path))
    assert out["converged"] == 0 and any("covers" in p for p in out["problems"])


def test_em_palace_timeout_and_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(em_adapter.shutil, "which", lambda exe: f"/opt/{exe}")

    def timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"), output="partial", stderr="")

    monkeypatch.setattr(em_adapter.proc, "run", timeout)
    with pytest.raises(RuntimeError, match="did not finish"):
        em_adapter.Adapter().run(_em_case(tmp_path), None, str(tmp_path))
    monkeypatch.setattr(em_adapter.proc, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 3, "", "boom"))
    with pytest.raises(RuntimeError, match="exited with 3"):
        em_adapter.Adapter().run(_em_case(tmp_path), None, str(tmp_path))


def test_em_palace_config_needs_a_complete_case(tmp_path, monkeypatch):
    case = _em_case(tmp_path)
    case["stackup"] = {"loss_tan": 0.003}
    with pytest.raises(ValueError, match="stackup.substrate_er.*stackup.source"):
        em_adapter.palace_config(case)
    case = _em_case(tmp_path)
    case["ports"] = case["ports"][:1]
    with pytest.raises(ValueError, match="at least two"):
        em_adapter.palace_config(case)
    monkeypatch.setattr(em_adapter.shutil, "which", lambda exe: None)
    with pytest.raises(RuntimeError, match="not found"):
        em_adapter.Adapter().run(_em_case(tmp_path), None, str(tmp_path))


def test_em_gprmax_guard_checks_the_import(tmp_path, monkeypatch):
    # `python` on PATH is not gprMax: the guard must try the import (the old guard only checked `python`)
    monkeypatch.setattr(em_adapter.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    calls = []

    def no_gprmax(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", "ModuleNotFoundError: No module named 'gprMax'")

    monkeypatch.setattr(em_adapter.proc, "run", no_gprmax)
    with pytest.raises(RuntimeError, match="not importable"):
        em_adapter.Adapter().run({"solver": "gprmax"}, None, str(tmp_path))
    assert calls == [["python", "-c", "import gprMax"]]
    monkeypatch.setattr(em_adapter.proc, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))
    with pytest.raises(RuntimeError, match="no gprMax run"):
        em_adapter.Adapter().run({"solver": "gprmax"}, None, str(tmp_path))


def test_em_refuses_fp32(tmp_path):
    class Spec:
        class resources:
            precision = "float32"

    with pytest.raises(ValueError, match="float64"):
        em_adapter.Adapter().run(_em_case(tmp_path), Spec(), str(tmp_path))
