"""job27: multi-layer net import (F.Cu <-> In2.Cu via chains)."""
import math

import pytest

from aces.domains.radar import kicad_import as ki

CELL = 0.0508e-3


def _chain(l_ms=6.0, l_sl=6.0, w_ms=0.508, w_sl=0.2032):
    """via_coupon-shaped chain: F.Cu --via--> In2.Cu --via--> F.Cu."""
    return {"net": "cal_like",
            "sections": [
                {"layer": "F.Cu", "points_mm": [(0.0, 0.0), (l_ms, 0.0)],
                 "width_mm": w_ms},
                {"layer": "In2.Cu", "points_mm": [(l_ms, 0.0), (l_ms + l_sl, 0.0)],
                 "width_mm": w_sl},
                {"layer": "F.Cu", "points_mm": [(l_ms + l_sl, 0.0),
                                                (2 * l_ms + l_sl, 0.0)],
                 "width_mm": w_ms}],
            "vias_mm": [(l_ms, 0.0, 0.4, 0.2), (l_ms + l_sl, 0.0, 0.4, 0.2)],
            "pads": []}


def _is_float(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


def test_ml_deck_shape():
    deck, geom = ki.deck_kicad_net_ml(_chain(), CELL, 4e-9, 10.5e9)
    assert deck.count(" 10.000 feed") == 5      # stacked sources (5-cell RO gap)
    assert deck.count(" 10.000 term") == 5
    assert deck.count("#rx:") == 6
    # planes 2 + traces 3 + via barrels 2 + stitches 8
    assert deck.count(" pec") == 15
    assert deck.count(" subfr4") == 1 + 2 + 2    # material + layers + antipads
    assert deck.count(" subro") == 1 + 1
    assert geom["n_vias"] == 2
    assert len(geom["sections_model"]) == 3
    sl = geom["sections_model"][1]
    assert sl["z0_ohm"] == pytest.approx(35.9, abs=0.6)     # Cohn, b incl. cell
    ms = geom["sections_model"][0]
    assert ms["z0_ohm"] == pytest.approx(50.6, abs=1.5)


def test_ml_deck_coordinates_inside_domain():
    deck, _ = ki.deck_kicad_net_ml(_chain(), CELL, 4e-9, 10.5e9)
    dom = None
    for line in deck.splitlines():
        toks = line.split()
        if toks[0] == "#domain:":
            dom = [float(v) for v in toks[1:4]]
    for line in deck.splitlines():
        toks = line.split()
        if toks[0] == "#voltage_source:":
            vals = [float(v) for v in toks[2:5]]
        elif toks[0] in ("#box:", "#rx:"):
            vals = [float(v) for v in toks[1:] if _is_float(v)]
        else:
            continue
        xs, ys, zs = vals[0::3], vals[1::3], vals[2::3]
        assert all(0 <= v <= dom[0] + 1e-9 for v in xs)
        assert all(0 <= v <= dom[1] + 1e-9 for v in ys)
        assert all(0 <= v <= dom[2] + 1e-9 for v in zs)


def test_ml_probe_order_matches_runner_contract():
    deck1, _ = ki.deck_kicad_net_ml(_chain(), CELL, 4e-9, 10.5e9, feed_end=1)
    deck2, _ = ki.deck_kicad_net_ml(_chain(), CELL, 4e-9, 10.5e9, feed_end=2)
    rx1 = [l for l in deck1.splitlines() if l.startswith("#rx:")]
    rx2 = [l for l in deck2.splitlines() if l.startswith("#rx:")]
    assert rx1[:3] != rx2[:3]
    assert sorted(rx1) == sorted(rx2)
    xs = [float(l.split()[1]) for l in rx1]
    assert max(xs[:3]) < min(xs[3:])     # run-1 near triad at the small-x end


def test_ml_expectations_match_via_coupon_reference():
    # same topology and stack as the job25 via_coupon -> same model values;
    # via_coupon snaps 6.0 mm to 118 cells = 5.9944 mm, so chain to that
    _, geom = ki.deck_kicad_net_ml(_chain(l_ms=5.9944, l_sl=5.9944),
                                   CELL, 4e-9, 10.5e9)
    ex = ki.expectations_kicad_net_ml(geom, 10e9)
    _, geom_ref = ki.deck_via_coupon(CELL, 4e-9, 10.5e9)
    ex_ref = ki.expectations_via_coupon(geom_ref, 10e9)
    assert abs(ex["s21_model"]) == pytest.approx(abs(ex_ref["s21_model"]), rel=1e-6)
    assert ex["delay_s_model"] == pytest.approx(ex_ref["delay_s_model"], rel=1e-9)


def test_ml_stitches_clear_traces():
    # job25 stitch-short rule must hold on both layers
    deck, geom = ki.deck_kicad_net_ml(_chain(), CELL, 4e-9, 10.5e9)
    cell = geom["cell_m"]
    boxes = []
    for line in deck.splitlines():
        toks = line.split()
        if toks[0] == "#box:" and toks[-1] == "pec":
            boxes.append([float(v) for v in toks[1:7]])
    z_in3 = 12 * cell
    z_in2 = z_in3 + cell + 3 * cell
    z_in1 = z_in2 + cell + 3 * cell
    traces = [b for b in boxes
              if abs(b[2] - z_in2) < 1e-12 and (b[3] - b[0]) > 1e-3]
    assert len(traces) == 1
    t = traces[0]
    stitches = [b for b in boxes
                if abs(b[2] - z_in3) < 1e-12 and abs(b[5] - (z_in1 + cell)) < 1e-12]
    assert len(stitches) == 8
    for s in stitches:
        overlap = not (s[3] <= t[0] or s[0] >= t[3] or s[4] <= t[1] or s[1] >= t[4])
        assert not overlap


def test_ml_rejects_bad_topology():
    # via count mismatch
    bad = _chain()
    bad["vias_mm"] = bad["vias_mm"][:1]
    with pytest.raises(ValueError, match="vias"):
        ki.deck_kicad_net_ml(bad, CELL, 4e-9, 10.5e9)
    # In5 section: deep stack region — supported since job30
    good = _chain()
    good["sections"][1]["layer"] = "In5.Cu"
    deck, geom = ki.deck_kicad_net_ml(good, CELL, 4e-9, 10.5e9)
    assert geom["deep_stack"] is True
    # other inner layers: still outside the modelled stack regions
    bad = _chain()
    bad["sections"][1]["layer"] = "In4.Cu"
    with pytest.raises(ValueError, match="not modelled"):
        ki.deck_kicad_net_ml(bad, CELL, 4e-9, 10.5e9)
    # chain ending on In2.Cu: no clean stripline feed (job25)
    bad = _chain()
    bad["sections"] = bad["sections"][:2]
    bad["vias_mm"] = bad["vias_mm"][:1]
    with pytest.raises(ValueError, match="ends must be on F.Cu"):
        ki.deck_kicad_net_ml(bad, CELL, 4e-9, 10.5e9)
    # diagonal end section (job26 probe rule)
    bad = _chain()
    bad["sections"][2]["points_mm"] = [(12.0, 0.0), (18.0, 1.0)]
    with pytest.raises(ValueError, match="end section is diagonal"):
        ki.deck_kicad_net_ml(bad, CELL, 4e-9, 10.5e9)
    # end section too short for the triad (CAL_* deferral reason)
    bad = _chain()
    bad["sections"][2]["points_mm"] = [(12.0, 0.0), (12.6, 0.0)]
    with pytest.raises(ValueError, match="too short"):
        ki.deck_kicad_net_ml(bad, CELL, 4e-9, 10.5e9)


def test_ml_diagonal_middle_section_rasterised():
    chain = _chain()
    a = math.radians(10.5)
    chain["sections"][1]["points_mm"] = [(6.0, 0.0), (6.0 + 6 * math.cos(a),
                                                      6 * math.sin(a))]
    chain["sections"][2]["points_mm"] = [(6.0 + 6 * math.cos(a), 6 * math.sin(a)),
                                         (12.0 + 6 * math.cos(a), 6 * math.sin(a))]
    chain["vias_mm"] = [(6.0, 0.0, 0.4, 0.2),
                        (6.0 + 6 * math.cos(a), 6 * math.sin(a), 0.4, 0.2)]
    deck, geom = ki.deck_kicad_net_ml(chain, CELL, 4e-9, 10.5e9)
    assert geom["n_diagonal_segments"] == 1
    assert geom["rasterised_cells"] > 0
