"""Tests for the radar domain: physics references, the design model, calibration."""
from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from aces.calibration import Factor, Registry
from aces.domains.radar import PhasedArrayRadar, RadarRequirements
from aces.domains.radar import array, cascade, linkbudget
from aces.domains.radar.calibrate import fit, read_rows

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


def test_phase_quantization():
    sigma, loss_db = array.phase_quantization(6)
    assert sigma == pytest.approx(math.pi / (math.sqrt(3) * 64), rel=1e-9)
    assert abs(loss_db) < 0.01  # ~3.5 mdB at 6 bits
    assert array.rms_sidelobe_ratio(6, 64) > 0


def test_array_gain():
    # 64 el, 6 dBi element, uniform, no quantization: 6 + 10log10(64) = 24.06 dBi
    assert array.array_gain_db(8, 8, 6.0) == pytest.approx(24.06, abs=0.01)
    # taper costs gain
    assert array.array_gain_db(8, 8, 6.0, "hamming") < 24.06


# ------------------------------------------------------------------- cascade

def test_friis_two_stage():
    s = cascade.cascade([
        cascade.Stage("lna", 10.0, 2.0),
        cascade.Stage("mixer", 20.0, 5.0),
    ])
    # F = 1.5849 + (3.1623-1)/10 = 1.8011 -> 2.555 dB
    assert s.nf_db == pytest.approx(2.555, abs=0.001)
    assert s.gain_db == pytest.approx(30.0)


def test_cascade_p1db_output_referred():
    s = cascade.cascade([
        cascade.Stage("a", 10.0, 3.0, op1db_dbm=0.0),
        cascade.Stage("b", 10.0, 3.0, op1db_dbm=15.0),
    ])
    # stage a compresses at 0 dBm out -> +10 dB gain after it -> 10 dBm at cascade out
    assert s.output_p1db_dbm == pytest.approx(10.0)


# ------------------------------------------------------------------- link budget

def test_albersheim_single_pulse():
    # n=1, Pd 0.9, Pfa 1e-6 -> ~13.2 dB (Skolnik fig.)
    assert linkbudget.albersheim_snr_db(0.9, 1e-6, 1) == pytest.approx(13.2, abs=0.3)


def test_range_equation_project_a_baseline():
    # Cross-check against the redesign study's computed Project-A baseline:
    # 160 W peak, EIRP ~40 dBW (G=18 dB), 100 us pulse, NF 5 dB, 1 m^2 -> ~2.7 km.
    r = linkbudget.max_range_m(
        p_peak_w=160.0, tau_s=100e-6, n_pulses=1,
        gain_tx_db=18.0, gain_rx_db=18.0, frequency_hz=10.5e9,
        rcs_m2=1.0, nf_db=5.0, losses_db=4.0, snr_req_db=13.2,
    )
    assert 2300 < r < 3100  # ~2.7 km within the study's own approximations


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


# ------------------------------------------------------------------- model

def _req(**over):
    data = dict(
        frequency_hz=10.5e9,
        target_rcs_m2=1.0,
        min_range_m=8_000,
        max_range_resolution_m=0.2,
        rx_chain=[
            dict(name="lna", gain_db=20.0, nf_db=2.2, op1db_dbm=12.0, source="test"),
            dict(name="ps", gain_db=-6.5, nf_db=6.5, source="test"),
        ],
        max_dc_power_w=6_000,
        max_dissipation_w_cm2=5.0,
        max_aperture_mm=300,
    )
    data.update(over)
    return RadarRequirements(**data)


def _params(**over):
    p = dict(nx=8.0, ny=8.0, pitch_lambda=0.5, chirp_bw_hz=1e9, pulse_width_s=50e-6,
             prf_hz=6e3, element_power_w=25.0, taper="uniform", phase_bits=6)
    p.update(over)
    return p


def test_model_x64_point_meets_targets():
    ev = PhasedArrayRadar().evaluate(_params(), _req())
    assert ev.metrics["n_elements"] == 64
    assert ev.metrics["eirp_dbw"] == pytest.approx(56.1, abs=0.3)  # study: ~57 dBW
    assert ev.metrics["range_resolution_m"] == pytest.approx(0.15, abs=0.01)
    assert ev.metrics["detection_range_km"] > 8.0
    assert set(ev.uncalibrated) == {"radar.rx.nf_factor", "radar.tx.line_loss_factor", "radar.array.gain_factor"}
    assert ev.warnings  # fast-tier caveats always present


def test_model_constraint_violations():
    ev = PhasedArrayRadar().evaluate(_params(pitch_lambda=0.75), _req(scan_half_angle_deg=45))
    assert ev.constraints["scan_margin_rad"] > 0  # 0.75 lambda cannot scan 45 deg
    assert not ev.feasible
    ev2 = PhasedArrayRadar().evaluate(_params(element_power_w=1.0), _req())
    assert ev2.constraints["range_margin_m"] > 0  # 64 W peak cannot reach 8 km
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


# ------------------------------------------------------------------- calibration

def test_calibrate_fit(tmp_path):
    rows = [
        {"kind": "tile_insertion_loss_db", "frequency_hz": 10.5e9, "value": 1.4,
         "model_value": 1.0, "source": "simlab:run-a"},
        {"kind": "tile_insertion_loss_db", "frequency_hz": 11.0e9, "value": 1.6,
         "model_value": 1.0, "source": "simlab:run-b"},
    ]
    jf = tmp_path / "rows.json"
    import json
    jf.write_text(json.dumps(rows), encoding="utf-8")
    parsed = read_rows(jf)
    reg = Registry()
    factors = fit(parsed, reg, fitted_on="2026-10-07")
    assert len(factors) == 1
    fac = reg.get("radar.tx.line_loss_factor")
    # geometric mean of 1.4 and 1.6
    assert fac.value == pytest.approx(math.sqrt(1.4 * 1.6), rel=1e-9)
    assert fac.calibrated
    assert fac.valid_range["frequency_hz"] == (10.5e9, 11.0e9)
    assert set(fac.sources) == {"simlab:run-a", "simlab:run-b"}


def test_calibrate_rejects_bad_rows(tmp_path):
    jf = tmp_path / "bad.json"
    import json
    jf.write_text(json.dumps([{"kind": "nope", "frequency_hz": 1e9, "value": 1.0,
                               "model_value": 1.0, "source": "x"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown kind"):
        read_rows(jf)
