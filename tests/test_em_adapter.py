"""Tests for the radar EM verification adapter: pure analysis functions and deck builders.

These pin the numerics without running FDTD.  The live gprMax runs are exercised
separately as pipeline jobs (see docs/RADAR_EM.md) because they take minutes.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from aces.domains.radar import em_adapter as em

RO = em.RO4350B  # er 3.66, h 0.508 mm, tanD 0.004


# ------------------------------------------------------------------ gaussiandot

def test_gaussiandot_spectrum_peaks_near_f0():
    f0 = 10.5e9
    t = np.arange(0, 10e-9, 2e-12)
    w = em.gaussiandot(t, f0)
    spec = np.abs(np.fft.rfft(w))
    freqs = np.fft.rfftfreq(len(t), d=t[1] - t[0])
    peak_f = freqs[int(np.argmax(spec))]
    # gaussiandot spectrum peaks slightly below the quoted centre frequency;
    # require within 15 % so the excitation actually covers the band
    assert abs(peak_f - f0) / f0 < 0.15


def test_gaussiandot_is_zero_at_t_zero():
    # -2 zeta (t-chi) exp(...) -> at t=0 value is small but nonzero; check the
    # pulse is causal-ish: amplitude at t=0 is far below the peak
    f0 = 10.5e9
    t = np.arange(0, 10e-9, 2e-12)
    w = em.gaussiandot(t, f0)
    assert abs(w[0]) < 0.2 * np.max(np.abs(w))


# ------------------------------------------------------- resonance_from_field

def test_resonance_recovers_synthetic_decay_tone():
    fres_true = 10.3e9
    f0 = 10.5e9
    dt = 2e-12
    t = np.arange(0, 20e-9, dt)
    # decaying sinusoid convolved-ish with the source: multiply the source by a
    # resonant response by simply building a decaying tone — the normalised
    # transfer must still peak at the tone frequency
    ez = np.exp(-t / 4e-9) * np.sin(2 * np.pi * fres_true * t)
    res = em.resonance_from_field(t, ez, f0, 8e9, 13e9)
    assert abs(res["resonance_hz"] - fres_true) <= res["resolution_hz"]


def test_resonance_raises_when_band_outside_window():
    t = np.arange(0, 1e-9, 2e-12)  # 1 ns window -> 1 GHz resolution, bins to 250 GHz
    ez = np.sin(2 * np.pi * 10e9 * t)
    with pytest.raises(ValueError):
        em.resonance_from_field(t, ez, 10e9, 600e9, 700e9)


# ------------------------------------------------------ transfer_db_from_probes

def test_transfer_db_unity_for_source_shaped_probe():
    f0 = 10.5e9
    t = np.arange(0, 10e-9, 2e-12)
    ez = em.gaussiandot(t, f0)  # probe sees the source waveform itself
    loss = em.transfer_db_from_probes(t, ez, f0, f0)
    assert loss == pytest.approx(0.0, abs=0.5)


def test_transfer_db_half_amplitude_is_minus_6db():
    f0 = 10.5e9
    t = np.arange(0, 10e-9, 2e-12)
    ez = 0.5 * em.gaussiandot(t, f0)
    loss = em.transfer_db_from_probes(t, ez, f0, f0)
    assert loss == pytest.approx(-6.02, abs=0.5)


# ------------------------------------------------------ resonator_q_from_field

def test_resonator_q_recovers_synthetic_ringdown():
    fres_true, q_true = 10.2e9, 180.0
    tau = q_true / (math.pi * fres_true)
    dt = 2e-12
    t = np.arange(0, 60e-9, dt)  # ~10 tau
    # pure ring-down, as the FDTD probe sees after the excitation pulse
    ez = np.exp(-t / tau) * np.sin(2 * np.pi * fres_true * t)
    res = em.resonator_q_from_field(t, ez, 10.5e9, 8e9, 13e9)
    assert abs(res["resonance_hz"] - fres_true) <= 2 * res["resolution_hz"]
    assert res["q"] == pytest.approx(q_true, rel=0.05)
    assert res["tau_s"] == pytest.approx(tau, rel=0.05)


def test_resonator_q_raises_on_short_ringdown():
    t = np.arange(0, 3e-9, 2e-12)  # window far shorter than tau
    tau = 6e-9
    ez = np.exp(-t / tau) * np.sin(2 * np.pi * 10.2e9 * t)
    with pytest.raises(RuntimeError, match="ring-down"):
        em.resonator_q_from_field(t, ez, 10.5e9, 8e9, 13e9)


# ------------------------------------------------------------------ references

def test_patch_design_roundtrip():
    L = em.design_patch_l_m(10.5e9, RO["substrate_er"], RO["substrate_h_m"], 9.4e-3)
    f = em.patch_resonance_hz(L, RO["substrate_er"], RO["substrate_h_m"], 9.4e-3)
    assert f == pytest.approx(10.5e9, rel=1e-3)
    # sanity: an X-band patch on 0.5 mm RO4350B is ~9 mm long
    assert 7e-3 < L < 11e-3


def test_microstrip_50ohm_width_on_ro4350b():
    w = em.microstrip_width_for_z0(RO["substrate_er"], RO["substrate_h_m"], 50.0)
    # known result: ~1.10 mm for er 3.66 / h 0.508 mm
    assert w == pytest.approx(1.10e-3, rel=0.05)
    z0 = em.microstrip_z0(RO["substrate_er"], RO["substrate_h_m"], w)
    assert z0 == pytest.approx(50.0, abs=0.5)


def test_microstrip_z0_decreases_with_width():
    h = RO["substrate_h_m"]
    assert em.microstrip_z0(3.66, h, 0.5e-3) > em.microstrip_z0(3.66, h, 2.0e-3)


# ------------------------------------------------------------------ deck builders

def test_patch_deck_contains_required_commands():
    deck, geom = em._gprmax_patch_input(0.254e-3, 10e-9, 10.5e9, 8e9, 13e9,
                                        RO["substrate_er"], RO["substrate_h_m"], RO["loss_tan"])
    for cmd in ("#domain:", "#dx_dy_dz:", "#time_window:", "#material:",
                "#waveform:", "#box:", "#voltage_source:", "#rx:"):
        assert cmd in deck, f"missing {cmd}"
    # one material, one source, one receiver
    assert deck.count("#material:") == 1
    assert deck.count("#voltage_source:") == 1
    assert deck.count("#rx:") == 1
    # geometry payload carries the cavity-model reference for the snapped patch;
    # it must be consistent with the snapped dimensions actually simulated
    assert geom["design_resonance_hz"] == pytest.approx(
        em.patch_resonance_hz(geom["patch_l_m"], RO["substrate_er"],
                              RO["substrate_h_m"], geom["patch_w_m"]), rel=1e-9)
    assert geom["design_resonance_hz"] == pytest.approx(10.5e9, rel=0.02)


def test_coupon_deck_line_is_50_ohm():
    deck, geom = em._gprmax_coupon_input(0.254e-3, 10e-9, 10.5e9,
                                         RO["substrate_er"], RO["substrate_h_m"], RO["loss_tan"])
    assert geom["z0_ohm"] == pytest.approx(50.0, abs=0.5)
    assert "#voltage_source:" in deck and "#rx:" in deck


def test_deck_coordinates_inside_domain():
    deck, _ = em._gprmax_patch_input(0.254e-3, 10e-9, 10.5e9, 8e9, 13e9,
                                     RO["substrate_er"], RO["substrate_h_m"], RO["loss_tan"])
    lines = {ln.split(":")[0]: ln for ln in deck.strip().splitlines()}
    dom = [float(v) for v in lines["#domain"].split(":")[1].split()]
    for key in ("#voltage_source", "#rx"):
        toks = lines[key].split(":")[1].split()
        if toks[0].isalpha():  # voltage_source carries the polarisation letter first
            toks = toks[1:]
        coords = [float(v) for v in toks[:3]]
        for c, d in zip(coords, dom):
            assert 0.0 <= c <= d


# ------------------------------------------------------------------ routing

def test_palace_raises_without_binary():
    ad = em.Adapter()
    with pytest.raises(RuntimeError, match="Palace"):
        ad.run({"solver": "palace"}, None, ".")


def test_wr90_deck_geometry_is_exact():
    deck = em._gprmax_wr90_input(8e-9, 6e9, 13e9, 15)
    assert "#eigenmode_port" in deck and "#eigenmode_excitation" in deck
    cell = 0.508e-3
    assert abs(45 * cell - 22.86e-3) < 1e-12  # WR-90 broad wall, exact
    assert abs(20 * cell - 10.16e-3) < 1e-12  # narrow wall, exact
    assert abs(50 * cell - 25.4e-3) < 1e-12   # through length, exact


def test_unknown_structure_raises(tmp_path):
    ad = em.Adapter()
    with pytest.raises(RuntimeError, match="unknown structure"):
        ad.run({"solver": "gprmax", "structure": "warp_drive"}, None, str(tmp_path))


# ------------------------------------------------------------------ wave split

def test_wave_split_recovers_synthetic_waves():
    beta = 350.0
    ys = np.array([0.003, 0.0055, 0.008])
    a_true, b_true = 1.0 + 0.2j, 0.08 - 0.03j
    specs = a_true * np.exp(-1j * beta * ys) + b_true * np.exp(1j * beta * ys)
    a, b, resid = em.wave_split_ab(specs, ys, beta)
    assert abs(a - a_true) < 1e-9
    assert abs(b - b_true) < 1e-9
    assert resid < 1e-12


def test_wave_split_two_probes_exact():
    beta = 280.0
    ys = np.array([0.0, 0.004])
    a_true, b_true = 0.7, -0.1j
    specs = a_true * np.exp(-1j * beta * ys) + b_true * np.exp(1j * beta * ys)
    a, b, resid = em.wave_split_ab(specs, ys, beta)
    assert abs(a - a_true) < 1e-9 and abs(b - b_true) < 1e-9


def test_wave_split_flags_wrong_beta_with_residual():
    beta = 350.0
    ys = np.array([0.003, 0.0055, 0.008])
    specs = np.exp(-1j * beta * ys)
    _, _, resid_ok = em.wave_split_ab(specs, ys, beta)
    _, _, resid_bad = em.wave_split_ab(specs, ys, beta * 1.6)
    assert resid_ok < 1e-12
    assert resid_bad > 0.1


def test_multiport_deck_load_and_probes():
    cell = RO["substrate_h_m"] / 2
    deck, geom = em._gprmax_multiport_input(cell, 4e-9, 10.5e9, RO["substrate_er"],
                                            RO["substrate_h_m"], RO["loss_tan"])
    lines = {ln.split(":")[0]: ln for ln in deck.strip().splitlines()}
    assert "#material" in lines and "rload" in deck
    assert deck.count("#rx:") == 6
    assert deck.count("#voltage_source:") == 1
    # feed at port1 by default; feed moves to port2 for feed_end=2
    deck2, geom2 = em._gprmax_multiport_input(cell, 4e-9, 10.5e9, RO["substrate_er"],
                                              RO["substrate_h_m"], RO["loss_tan"], feed_end=2)
    feed1 = [ln for ln in deck.splitlines() if ln.startswith("#voltage_source")][0]
    feed2 = [ln for ln in deck2.splitlines() if ln.startswith("#voltage_source")][0]
    assert feed1 != feed2
    assert geom["probes_near_m"][0] < geom["probes_far_m"][0]


def test_multiport_deck_rejects_wrong_substrate_cells():
    with pytest.raises(ValueError, match="2 cells"):
        em._gprmax_multiport_input(RO["substrate_h_m"] / 4, 4e-9, 10.5e9,
                                   RO["substrate_er"], RO["substrate_h_m"], RO["loss_tan"])


# ------------------------------------------------------------------ palace path

def test_palace_wr90_config_shape():
    cfg = em._palace_wr90_config([9.5, 10.0, 12.4])
    assert cfg["Problem"]["Type"] == "Driven"
    assert cfg["Model"]["L0"] == 1.0e-3  # mesh in mm
    mats = cfg["Domains"]["Materials"]
    assert mats == [{"Attributes": [1], "Permeability": 1.0, "Permittivity": 1.0,
                     "LossTan": 0.0}]
    ports = cfg["Boundaries"]["WavePort"]
    assert [p["Index"] for p in ports] == [1, 2]
    assert all(p["Mode"] == 1 for p in ports)              # TE10
    assert [p["Excitation"] for p in ports] == [1, 2]      # full 2x2 S-matrix
    assert [p["Attributes"] for p in ports] == [[2], [3]]  # z=0 / z=L faces
    assert cfg["Boundaries"]["PEC"]["Attributes"] == [4]
    samples = cfg["Solver"]["Driven"]["Samples"]
    assert len(samples) == 1 and samples[0]["Type"] == "Point"  # not PROM "Linear"
    assert samples[0]["Freq"] == [9.5, 10.0, 12.4]


def test_palace_kn_parser():
    log = (
        "Calculating boundary modes at wave ports for ω/2π = 9.500e+00 GHz (4.559e+00)\n"
        " Port 1, mode 1: kₙ = 1.441e+02-2.751e-18i m⁻¹\n"
        " Port 2, mode 1: kₙ = 1.441e+02-9.999e-18i m⁻¹\n"
        "Calculating boundary modes at wave ports for ω/2π = 1.000e+01 GHz (4.791e+00)\n"
        " Port 1, mode 1: kₙ = 1.582e+02+1.488e-17i m⁻¹\n"
        " Port 2, mode 1: kₙ = 1.582e+02+2.096e-18i m⁻¹\n"
    )
    kn = em._parse_palace_kn(log)
    assert kn == {9.5: 144.1, 10.0: 158.2}  # port 1 only, real part


def test_palace_rejects_unknown_case():
    ad = em.Adapter()
    with pytest.raises(RuntimeError, match="no Palace path"):
        ad._run_palace({"validate": "patch_xband"}, __import__("pathlib").Path("."))
