"""Tests for via support: net_sections chaining, stripline/cascade models,
via coupon deck builder."""
from __future__ import annotations

import math

import pytest

from aces.domains.radar import kicad_import as ki

from test_kicad_import import FIXTURE, _is_float  # reuse the mini board


@pytest.fixture
def pcb(tmp_path):
    p = tmp_path / "mini.kicad_pcb"
    p.write_text(FIXTURE, encoding="utf-8")
    return ki.parse_pcb(p)


def test_net_sections_rejects_single_layer_net_vias(pcb):
    # RF_VIA: one segment + one via — chain ends at the via site with no
    # continuation segment, so it cannot reach a far end on another layer
    with pytest.raises(ValueError):
        ki.net_sections(pcb, "RF_VIA")


def test_net_sections_multi_layer(tmp_path):
    text = FIXTURE.replace(
        '(segment (start 40.0 5.0) (end 44.0 5.0) (width 0.22) (layer "F.Cu") (net 4))',
        '(segment (start 40.0 5.0) (end 44.0 5.0) (width 0.22) (layer "F.Cu") (net 4))\n'
        '  (segment (start 44.0 5.0) (end 44.0 9.0) (width 0.2) (layer "In2.Cu") (net 4))')
    p = tmp_path / "via_net.kicad_pcb"
    p.write_text(text, encoding="utf-8")
    pcb2 = ki.parse_pcb(p)
    out = ki.net_sections(pcb2, "RF_VIA")
    assert [s["layer"] for s in out["sections"]] == ["F.Cu", "In2.Cu"]
    assert out["sections"][0]["points_mm"] == [(40.0, 5.0), (44.0, 5.0)]
    assert out["sections"][1]["points_mm"] == [(44.0, 5.0), (44.0, 9.0)]
    assert out["n_vias_used"] == 1


def test_stripline_z0_sanity():
    # stackup.py: 50 ohm stripline in this stack is below min track; at
    # w = 0.2 mm, b = 0.324 mm, er = 4.10 the model reads ~29 ohm
    z = ki.stripline_z0_t(4.10, 0.324e-3, 0.2032e-3)
    assert 25 < z < 40
    # symmetric: narrower -> higher Z0
    assert ki.stripline_z0_t(4.10, 0.324e-3, 0.1e-3) > z


def test_cascade_matched_line_is_transparent():
    sp = ki.cascade_sparams([10e9], [{"z0": 50.0, "er_eff": 2.8, "length_m": 12e-3}])[0]
    assert abs(sp["s21"]) == pytest.approx(1.0, abs=1e-12)
    assert abs(sp["s11"]) == pytest.approx(0.0, abs=1e-12)
    # phase = -beta L
    beta_l = 2 * math.pi * 10e9 * math.sqrt(2.8) / ki.C0 * 12e-3
    assert math.atan2(sp["s21"].imag, sp["s21"].real) == pytest.approx(-beta_l + 2 * math.pi, abs=1e-6) \
        or math.atan2(sp["s21"].imag, sp["s21"].real) == pytest.approx(-beta_l % (2 * math.pi) - (2 * math.pi if beta_l % (2 * math.pi) > math.pi else 0), abs=1e-6)


def test_cascade_quarter_wave_inverter():
    # lambda/4 of 100 ohm between 50 ohm ports: |S11| = (Z^2-50^2)/(Z^2+50^2)
    f = 10e9
    er_eff = 1.0
    l = 2.99792458e8 / f / 4
    sp = ki.cascade_sparams([f], [{"z0": 100.0, "er_eff": er_eff, "length_m": l}])[0]
    g = (100 ** 2 - 50 ** 2) / (100 ** 2 + 50 ** 2)
    assert abs(sp["s11"]) == pytest.approx(g, rel=1e-6)
    assert abs(sp["s21"]) == pytest.approx(math.sqrt(1 - g ** 2), rel=1e-6)


def test_via_coupon_deck_shape():
    deck, geom = ki.deck_via_coupon(0.0508e-3, 4e-9, 10.5e9)
    # 5-cell RO4350B gap: 5 feed + 5 term stacked sources (job22 rule)
    assert deck.count(" feed") == 1 + 5        # waveform + 5 sources
    assert deck.count(" 10.000 feed") == 5
    assert deck.count(" 10.000 term") == 5
    assert deck.count("#rx:") == 6
    assert deck.count(" subfr4") == 1 + 2 + 2  # material + 2 layer boxes + 2 antipads
    assert deck.count(" subro") == 1 + 1       # material + layer box
    # 2 ground planes + 3 trace boxes + 2 via barrels + 8 stitching vias
    assert deck.count(" pec") == 15
    assert geom["z0_sl_model_ohm"] < geom["z0_ms_model_ohm"]
    assert geom["z0_ms_model_ohm"] == pytest.approx(50.6, abs=1.5)


def test_via_coupon_deck_coordinates_inside_domain():
    deck, geom = ki.deck_via_coupon(0.0508e-3, 4e-9, 10.5e9)
    dom = None
    pts = []
    for line in deck.splitlines():
        toks = line.split()
        if toks[0] == "#domain:":
            dom = [float(v) for v in toks[1:4]]
        elif toks[0] in ("#box:", "#rx:"):
            vals = [float(v) for v in toks[1:] if _is_float(v)]
            pts.append(vals)
        elif toks[0] == "#voltage_source:":
            pts.append([float(v) for v in toks[2:5]])   # x y z only
    for vals in pts:
        xs, ys, zs = vals[0::3], vals[1::3], vals[2::3]
        assert all(0 <= v <= dom[0] + 1e-9 for v in xs)
        assert all(0 <= v <= dom[1] + 1e-9 for v in ys)
        assert all(0 <= v <= dom[2] + 1e-9 for v in zs)


def test_via_coupon_probe_order_matches_runner_contract():
    # runner contract: rx1-3 = fed-end triad, rx4-6 = terminated-end triad,
    # and the fed end swaps with feed_end (job25 bug: interleaved/static
    # probes silently broke the wave split)
    deck1, g1 = ki.deck_via_coupon(0.0508e-3, 4e-9, 10.5e9, feed_end=1)
    deck2, g2 = ki.deck_via_coupon(0.0508e-3, 4e-9, 10.5e9, feed_end=2)
    rx1 = [l for l in deck1.splitlines() if l.startswith("#rx:")]
    rx2 = [l for l in deck2.splitlines() if l.startswith("#rx:")]
    assert rx1[:3] != rx2[:3]
    assert sorted(rx1) == sorted(rx2)     # same six physical probes both runs
    # run-1 near triad sits at the small-x end
    xs = [float(l.split()[1]) for l in rx1]
    assert max(xs[:3]) < min(xs[3:])


def test_via_coupon_stitches_clear_the_trace():
    # job25 bug: axis-aligned (±off, 0) stitches land on the stripline trace
    # and short it to the planes (S11 ~ 0 dB).  Stitches must be diagonal.
    deck, geom = ki.deck_via_coupon(0.0508e-3, 4e-9, 10.5e9)
    cell = geom["cell_m"]
    boxes = []
    for line in deck.splitlines():
        toks = line.split()
        if toks[0] == "#box:" and toks[-1] == "pec":
            boxes.append([float(v) for v in toks[1:7]])
    # stripline trace = the long pec box at the In2 z-level, narrow in y
    # (via barrels also start at z_in2 and are narrow in y, but they are
    #  short in x — the trace runs the full stripline length)
    z_in2 = 12 * cell + cell + 3 * cell
    trace = [b for b in boxes if abs(b[2] - z_in2) < 1e-12
             and (b[4] - b[1]) < 3e-4 and (b[3] - b[0]) > 1e-3]
    assert len(trace) == 1
    t = trace[0]
    # every stitch barrel (small square pec boxes spanning z_in3..z_in1+cell)
    z_in3 = 12 * cell
    z_in1 = z_in2 + cell + 3 * cell
    stitches = [b for b in boxes
                if abs(b[2] - z_in3) < 1e-12 and abs(b[5] - (z_in1 + cell)) < 1e-12]
    assert len(stitches) == 8
    for s in stitches:
        # no xy overlap with the trace box
        overlap = not (s[3] <= t[0] or s[0] >= t[3] or s[4] <= t[1] or s[1] >= t[4])
        assert not overlap


def test_via_coupon_rejects_bad_cell():
    with pytest.raises(ValueError, match="0.254"):
        ki.deck_via_coupon(0.05e-3, 4e-9, 10.5e9)


def test_via_coupon_expectations():
    _, geom = ki.deck_via_coupon(0.0508e-3, 4e-9, 10.5e9)
    ex = ki.expectations_via_coupon(geom, 10e9)
    # mismatched stripline section -> finite reflection, near-unity transmission
    assert 0.0 < abs(ex["s11_model"]) < 0.6
    assert abs(ex["s21_model"]) > 0.7


def test_stripline_cohn_matches_static_field_solve():
    # job25: independent 2-D Laplace SOR solve of the exact coupon
    # cross-section (b=7 cells, w=4 cells, t=1 cell, er=4.1) gives
    # 35.6 ohm; Cohn must agree within 2 % — IPC-2141 gives 27 ohm here
    # (wide-strip regime w/(b-t) = 0.75, outside its accuracy range).
    cell = 0.0508e-3
    z_cohn = ki.stripline_z0_cohn(4.10, 7 * cell, 4 * cell, t=cell)
    assert z_cohn == pytest.approx(35.6, rel=0.02)
    z_ipc = ki.stripline_z0_t(4.10, 7 * cell, 4 * cell, t=cell)
    assert z_ipc < 0.85 * z_cohn      # documents the IPC under-prediction


def test_via_coupon_geometry_uses_full_plane_spacing():
    _, geom = ki.deck_via_coupon(0.0508e-3, 4e-9, 10.5e9)
    cell = geom["cell_m"]
    # b = 3 FR4 cells + trace cell + 3 FR4 cells (not 2*n_fr4: job25 bug)
    assert geom["b_sl_modelled_m"] == pytest.approx(7 * cell, rel=1e-9)
    assert geom["z0_sl_model_ohm"] == pytest.approx(35.9, abs=0.6)
    assert geom["z0_sl_ipc2141_ohm"] < geom["z0_sl_model_ohm"]
    # stitched transition: no series-L elements in the reference cascade
    ex = ki.expectations_via_coupon(geom, 10e9)
    assert all("series_l_h" not in s for s in ex["sections"])
    # delay includes the two via vertical transits (~3 ps each)
    lines_only = (2 * geom["l_ms_m"] * math.sqrt(geom["er_eff_ms_model"])
                  + geom["l_sl_m"] * math.sqrt(geom["er_sl"])) / 299792458.0
    assert ex["delay_s_model"] == pytest.approx(
        lines_only + 2 * geom["via_transit_s"], rel=1e-9)
    assert ex["delay_s_model"] > lines_only * 1.03
    assert ex["delay_s_model"] > 0
