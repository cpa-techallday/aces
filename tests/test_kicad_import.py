"""Tests for the KiCad layout import (kicad_net) — parser, polyline chaining,
deck builder, and model expectations.  No live FDTD here (that is job23).
"""
from __future__ import annotations

import math

import pytest

from aces.domains.radar import kicad_import as ki
from aces.domains.radar import em_adapter as em

FIXTURE = """(kicad_pcb (version 20240108)
  (net 0 "")
  (net 1 "GND")
  (net 2 "RF_A")
  (net 3 "RF_B")
  (net 4 "RF_VIA")
  (net 5 "RF_ANGLE")
  (segment (start 10.0 10.0) (end 10.0 12.0) (width 0.22) (layer "F.Cu") (net 2))
  (segment (start 10.0 12.0) (end 18.0 12.0) (width 0.22) (layer "F.Cu") (net 2))
  (segment (start 20.0 5.0) (end 24.0 5.0) (width 0.3) (layer "In2.Cu") (net 3))
  (segment (start 30.0 5.0) (end 31.0 6.0) (width 0.22) (layer "F.Cu") (net 5))
  (segment (start 40.0 5.0) (end 44.0 5.0) (width 0.22) (layer "F.Cu") (net 4))
  (via (at 44.0 5.0) (size 0.6) (drill 0.3) (layers "F.Cu" "In1.Cu") (net 4))
  (footprint "AERIS:SMPM" (layer "F.Cu") (at 10.0 8.0 0)
    (fp_text reference J1 (at 0 0) (layer "F.SilkS"))
    (pad "S" smd rect (at 0.0 1.6 0) (size 0.6 0.8) (layers "F.Cu") (net 2 "RF_A")))
  (footprint "AERIS:SMPM" (layer "F.Cu") (at 18.0 14.0 0)
    (fp_text reference J2 (at 0 0) (layer "F.SilkS"))
    (pad "S" smd rect (at 0.0 -1.6 0) (size 0.6 0.8) (layers "F.Cu") (net 2 "RF_A")))
  (zone (net 1) (net_name "GND") (layer "In1.Cu"))
)"""


@pytest.fixture
def pcb(tmp_path):
    p = tmp_path / "mini.kicad_pcb"
    p.write_text(FIXTURE, encoding="utf-8")
    return ki.parse_pcb(p)


def test_parse_nets_segments_pads_zones(pcb):
    assert pcb.nets[2] == "RF_A"
    assert len(pcb.segments) == 5
    assert len(pcb.vias) == 1
    rf_a = [s for s in pcb.segments if s.net == "RF_A"]
    assert all(s.layer == "F.Cu" and s.width == 0.22 for s in rf_a)
    pads = [p for p in pcb.pads if p.net == "RF_A"]
    assert len(pads) == 2
    # footprint transform applied: J1 pad at (10.0, 9.6), J2 at (18.0, 12.4)
    ats = sorted(p.at for p in pads)
    assert ats[0] == pytest.approx((10.0, 9.6))
    assert ats[1] == pytest.approx((18.0, 12.4))
    assert ("GND", "In1.Cu") in pcb.zones


def test_polyline_chains_l_shape(pcb):
    poly = ki.net_polyline(pcb, "RF_A")
    pts = poly["points_mm"]
    assert len(pts) == 3
    assert pts[0] in ((10.0, 10.0), (18.0, 12.0))
    assert pts[-1] in ((10.0, 10.0), (18.0, 12.0))
    assert pts[1] == (10.0, 12.0)  # the corner is always the middle
    assert poly["width_mm"] == 0.22
    assert len(poly["pads"]) == 2


def test_polyline_rejects_wrong_layer_via_and_angle(pcb):
    with pytest.raises(ValueError, match="no segments"):
        ki.net_polyline(pcb, "RF_B", layer="F.Cu")   # RF_B is on In2.Cu
    with pytest.raises(ValueError, match="via"):
        ki.net_polyline(pcb, "RF_VIA")
    with pytest.raises(ValueError, match="non-axis-aligned"):
        ki.net_polyline(pcb, "RF_ANGLE")
    with pytest.raises(ValueError, match="no segments"):
        ki.net_polyline(pcb, "DOES_NOT_EXIST")


def _deck(poly, feed_end=1):
    cell = 0.0635e-3
    return ki.deck_kicad_net(poly, cell, 4e-9, 10.5e9, 3.66, 0.254e-3, 0.0037,
                             feed_end=feed_end)


def test_deck_stacked_feed_load_and_probes(pcb):
    poly = ki.net_polyline(pcb, "RF_A")
    poly["net"] = "RF_A"
    deck, geom = _deck(poly)
    # job22 rule: one voltage source per substrate Ez node plane (4 cells)
    # at the feed, plus an identical zero-excitation string as the 50-ohm load
    assert deck.count("#voltage_source: z") == 8
    assert deck.count(" 12.500 feed") == 4       # 50 ohm / 4 stacked
    assert deck.count(" 12.500 term") == 4       # Thevenin termination
    assert deck.count("#rx:") == 6               # two probe triads
    assert "gaussiandot 1.0e-30" in deck         # zero-excitation waveform
    # width snapped: 0.22 mm / 0.0635 mm -> 3 cells = 0.1905 mm
    assert geom["line_w_m"] == pytest.approx(0.1905e-3)
    assert geom["n_sub_cells"] == 4
    assert geom["length_m"] == pytest.approx(10e-3)  # 2 mm + 8 mm L-shape
    assert geom["z0_model_ohm"] > 50.0     # narrower than the 50-ohm width


def test_deck_coordinates_inside_domain(pcb):
    poly = ki.net_polyline(pcb, "RF_A")
    poly["net"] = "RF_A"
    deck, geom = _deck(poly)
    nums = {}
    for line in deck.splitlines():
        toks = line.split()
        if toks[0] == "#domain:":
            nums["dom"] = [float(v) for v in toks[1:4]]
        elif toks[0] in ("#box:", "#rx:"):
            vals = [float(v) for v in toks[1:] if _is_float(v)]
            nums.setdefault("pts", []).append(vals)
    dx, dy, dz = nums["dom"]
    for vals in nums["pts"]:
        xs, ys, zs = vals[0::3], vals[1::3], vals[2::3]
        assert all(0 <= v <= dx + 1e-9 for v in xs)
        assert all(0 <= v <= dy + 1e-9 for v in ys)
        assert all(0 <= v <= dz + 1e-9 for v in zs)


def _is_float(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


def test_deck_feed_end_swaps_sources_not_probes(pcb):
    poly = ki.net_polyline(pcb, "RF_A")
    poly["net"] = "RF_A"
    deck1, _ = _deck(poly, feed_end=1)
    deck2, _ = _deck(poly, feed_end=2)
    src1 = [l for l in deck1.splitlines() if l.startswith("#voltage_source")]
    src2 = [l for l in deck2.splitlines() if l.startswith("#voltage_source")]
    assert src1 != src2
    assert len(src1) == len(src2) == 8
    rx1 = [l for l in deck1.splitlines() if l.startswith("#rx:")]
    rx2 = [l for l in deck2.splitlines() if l.startswith("#rx:")]
    assert sorted(rx1) == sorted(rx2)      # both triads present in every run


def test_deck_rejects_short_end_section():
    poly = {"points_mm": [(0.0, 0.0), (0.0, 0.5), (8.0, 0.5)], "width_mm": 0.22,
            "pads": []}
    with pytest.raises(ValueError, match="too short"):
        _deck(poly)


def test_expectations_sanity(pcb):
    poly = ki.net_polyline(pcb, "RF_A")
    poly["net"] = "RF_A"
    _, geom = _deck(poly)
    ex = ki.expectations(geom, 3.66, 0.254e-3, 0.0037, 10e9)
    assert 70 < ex["z0_model_ohm"] < 90          # thickness-corrected: ~80 ohm (NOT 50)
    assert 2.0 < ex["er_eff_model"] < 3.66
    assert 0 < ex["loss_db_model"] < 0.5         # 10 mm, dielectric only, PEC
    # delay = L * sqrt(er_eff) / c
    assert ex["delay_s_model"] == pytest.approx(
        10e-3 * math.sqrt(ex["er_eff_model"]) / 2.99792458e8, rel=1e-9)


def test_adapter_kicad_net_requires_pcb_and_net(tmp_path):
    ad = em.Adapter()
    with pytest.raises(ValueError, match="pcb"):
        ad._run_kicad_net({"structure": "kicad_net"}, tmp_path)


def test_thickness_correction_lowers_z0():
    # job23 control finding: bare Hammerstad over-reads Z0 by ~9 ohm at
    # w/h = 0.75; the 35 um thickness correction (stackup.py form) closes it
    z_bare = em.microstrip_z0(3.66, 0.254e-3, 0.1905e-3)
    z_corr = ki.z0_microstrip_t(3.66, 0.254e-3, 0.1905e-3)
    assert z_corr < z_bare
    assert z_corr == pytest.approx(80.5, abs=1.5)
    assert ki.er_eff_microstrip_t(3.66, 0.254e-3, 0.1905e-3) > \
        em.microstrip_er_eff(3.66, 0.254e-3, 0.1905e-3)
