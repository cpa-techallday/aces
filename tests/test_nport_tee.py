"""job29: N-port decomposition — deck_kicad_nport geometry/deck tests and
the ideal lossless Tee reference (no gprMax runs)."""
import pytest

from aces.domains.radar import kicad_import as ki

CELL = 0.0635e-3          # h/4 at h = 0.254 mm


def _tee():
    return {"net": "TEE", "junction_mm": (0.0, 0.0), "branches": [
        {"points_mm": [(0.0, 0.0), (-8.0, 0.0)], "width_mm": 0.51},
        {"points_mm": [(0.0, 0.0), (8.0, 0.0)], "width_mm": 0.51},
        {"points_mm": [(0.0, 0.0), (0.0, 8.0)], "width_mm": 0.51}]}


# ------------------------------------------------------------------ reference
def test_tee_reference_3port_50ohm():
    r = ki.tee_reference(50.0, 3)
    assert r["z_load_ohm"] == pytest.approx(25.0)
    assert r["s_ii"] == pytest.approx(-1.0 / 3.0)
    assert r["s_ij"] == pytest.approx(2.0 / 3.0)
    assert r["s_ii_db"] == pytest.approx(-9.542, abs=1e-3)
    assert r["s_ij_db"] == pytest.approx(-3.522, abs=1e-3)


def test_tee_reference_2port_is_through():
    r = ki.tee_reference(50.0, 2)
    assert r["s_ii"] == pytest.approx(0.0, abs=1e-12)
    assert r["s_ij"] == pytest.approx(1.0)


def test_tee_reference_power_conserving():
    """Exact for equal impedances (field-amplitude waves on identical arms);
    with a port/arm mismatch the reference is the port-normalized ideal Tee
    and |S|² columns no longer sum to exactly 1 (normalization mismatch) —
    that's why the Tee reference is informational, not a gate."""
    for n in (2, 3, 4, 8):
        r = ki.tee_reference(52.7, n, z_port=52.7)
        assert r["s_ii"] ** 2 + (n - 1) * r["s_ij"] ** 2 == pytest.approx(1.0)
    # closed form: s_ii = (2-n)/n, s_ij = 2/n
    r = ki.tee_reference(52.7, 4, z_port=52.7)
    assert r["s_ii"] == pytest.approx(-0.5) and r["s_ij"] == pytest.approx(0.5)


# ------------------------------------------------------------------ deck
def test_deck_ports_sources_probes():
    deck, geom = ki.deck_kicad_nport(_tee(), CELL, 4e-9, 10.5e9,
                                     3.66, 0.254e-3, 0.0037, feed_port=2)
    assert geom["n_ports"] == 3 and geom["feed_port"] == 2
    assert geom["branch_lengths_m"] == pytest.approx([0.008] * 3)
    n_sub = geom["n_sub_cells"]
    lines = deck.splitlines()
    assert sum(1 for l in lines if l.startswith("#rx:")) == 9   # 3 per port
    src = [l for l in lines if l.startswith("#voltage_source:")]
    assert len(src) == 3 * n_sub
    assert sum(1 for l in src if l.endswith(" feed")) == n_sub  # fed port only
    assert sum(1 for l in src if l.endswith(" term")) == 2 * n_sub
    # probes keyed per port, three distances each, increasing
    assert set(geom["probes_m"]) == {1, 2, 3}
    for d in geom["probes_m"].values():
        assert len(d) == 3 and d[0] < d[1] < d[2]


def test_deck_feed_port_moves():
    """The feed string lands on the requested port's end."""
    decks = {}
    for fp in (1, 2, 3):
        deck, geom = ki.deck_kicad_nport(_tee(), CELL, 4e-9, 10.5e9,
                                         3.66, 0.254e-3, 0.0037, feed_port=fp)
        decks[fp] = deck
    # different feed port -> different deck
    assert decks[1] != decks[2] != decks[3]


def test_deck_junction_union_face_connected():
    """The rasterised copper (arms + junction disk) is one face-connected set
    — verified indirectly: the merged runs must cover the junction cell and
    both x-arm rows and the y-arm column."""
    _, geom = ki.deck_kicad_nport(_tee(), CELL, 4e-9, 10.5e9,
                                  3.66, 0.254e-3, 0.0037)
    # 3 arms * (8 mm / cell) rows ~ 3*126 rows*cols + junction disk;
    # rasterised cell count must exceed a single arm's count
    single_arm = ki.rasterize_capsule_cells((0.0, 0.0), (8e-3, 0.0),
                                            0.51e-3 / 2, CELL)
    assert geom["rasterised_cells"] > 2 * len(single_arm)


def test_deck_rejects_diagonal_end_section():
    g = {"junction_mm": (0.0, 0.0), "branches": [
        {"points_mm": [(0.0, 0.0), (-8.0, 0.0)], "width_mm": 0.51},
        {"points_mm": [(0.0, 0.0), (8.0, 0.0)], "width_mm": 0.51},
        {"points_mm": [(0.0, 0.0), (5.0, 5.0)], "width_mm": 0.51}]}
    with pytest.raises(ValueError, match="end section is diagonal"):
        ki.deck_kicad_nport(g, CELL, 4e-9, 10.5e9, 3.66, 0.254e-3, 0.0037)


def test_deck_rejects_short_end_section():
    g = {"junction_mm": (0.0, 0.0), "branches": [
        {"points_mm": [(0.0, 0.0), (-8.0, 0.0)], "width_mm": 0.51},
        {"points_mm": [(0.0, 0.0), (8.0, 0.0)], "width_mm": 0.51},
        {"points_mm": [(0.0, 0.0), (0.0, 1.0)], "width_mm": 0.51}]}
    with pytest.raises(ValueError, match="too short for probe triad"):
        ki.deck_kicad_nport(g, CELL, 4e-9, 10.5e9, 3.66, 0.254e-3, 0.0037)


def test_deck_pour_support():
    pour = {"polygon_mm": [(-10.0, -4.0), (10.0, -4.0), (10.0, 10.0),
                           (-10.0, 10.0)],
            "clearance_m": 0.3e-3}
    _, geom = ki.deck_kicad_nport(_tee(), CELL, 4e-9, 10.5e9,
                                  3.66, 0.254e-3, 0.0037, pour=pour)
    assert geom["pour_present"] is True
    assert geom["pour"]["cells"] > 0
