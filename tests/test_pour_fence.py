"""job33: via-fence stitching of the F.Cu pour to the ground plane."""
import pathlib
import tempfile

import pytest

from aces.domains.radar import kicad_import as ki

CELL = 0.254e-3 / 4  # h/4 on TILE_L1L2
POLY = {"net": "GCPW", "points_mm": [(0.0, 0.0), (12.0, 0.0)],
        "width_mm": 0.51, "pads": []}
POUR_POLY = [(-2.0, -4.0), (14.0, -4.0), (14.0, 4.0), (-2.0, 4.0)]


def _deck(pour=None):
    return ki.deck_kicad_net(POLY, CELL, 4e-9, 10.5e9, 3.66, 0.254e-3, 0.0037,
                             feed_end=1, margin_cells=24, pour=pour)


# ------------------------------------------------------------- extraction
def test_pour_stitch_vias_filters_net_polygon_window():
    fix = """(kicad_pcb (version 20240108)
  (net 0 "")
  (net 1 "GND")
  (net 2 "+28V")
  (via (at 5.0 2.0) (size 0.4) (drill 0.2) (layers "F.Cu" "In1.Cu") (net 1))
  (via (at 7.0 -2.0) (size 0.6) (drill 0.3) (layers "F.Cu" "In1.Cu") (net 1))
  (via (at 30.0 30.0) (size 0.4) (drill 0.2) (layers "F.Cu" "In1.Cu") (net 1))
  (via (at 6.0 1.0) (size 0.4) (drill 0.2) (layers "F.Cu" "In1.Cu") (net 2))
)"""
    p = pathlib.Path(tempfile.mkdtemp()) / "t.kicad_pcb"
    p.write_text(fix, encoding="utf-8")
    pcb = ki.parse_pcb(p)
    vias = ki.pour_stitch_vias(pcb, POUR_POLY)
    assert vias == [(7.0, -2.0, 0.6), (5.0, 2.0, 0.4)]  # sorted by (y, x)
    win = ki.pour_stitch_vias(pcb, POUR_POLY, window_mm=((4.0, 0.0), (8.0, 4.0)))
    assert win == [(5.0, 2.0, 0.4)]


# ------------------------------------------------------------------- deck
def test_fence_columns_emitted_and_geometry():
    # fence vias must sit inside the coupon window: margin 24 cells reaches
    # 1.524 mm from the copper bbox, so y = +/-1.0 mm is representable
    pour = {"polygon_mm": POUR_POLY, "clearance_m": 0.20e-3,
            "fence_mm": [(2.0, 1.0, 0.4), (6.0, -1.0, 0.4), (10.0, 1.0, 0.4)]}
    deck, geom = _deck(pour)
    assert geom["pour"]["stitched_to_plane"] is True
    assert geom["pour"]["fence"]["count"] == 3
    assert geom["pour"]["fence"]["skipped_mm"] == []
    # columns span the substrate: top of ground plane to top of pour
    n_sub = geom["n_sub_cells"]
    z_gnd_top = 12 * CELL + CELL            # lift_cells=12 default + plane
    z_pour_top = z_gnd_top + n_sub * CELL + CELL
    fence_lines = []
    for ln in deck.splitlines():
        if not ln.startswith("#box:"):
            continue
        f = ln.split()
        if f[3] == f"{z_gnd_top:.7f}" and f[6] == f"{z_pour_top:.7f}":
            fence_lines.append(ln)
    assert len(fence_lines) == 3


def test_no_fence_is_job28_behaviour_bit_identical():
    pour = {"polygon_mm": POUR_POLY, "clearance_m": 0.20e-3}
    d1, g1 = _deck(pour)
    d2, g2 = _deck({"polygon_mm": POUR_POLY, "clearance_m": 0.20e-3,
                    "fence_mm": []})
    assert d1 == d2
    assert g1["pour"]["stitched_to_plane"] is False
    assert "fence" not in g1["pour"]


def test_fence_via_on_trace_keepout_is_skipped():
    # via at (6, 0) sits on the trace centreline — must not short to it
    pour = {"polygon_mm": POUR_POLY, "clearance_m": 0.20e-3,
            "fence_mm": [(6.0, 0.0, 0.4), (6.0, 1.0, 0.4)]}
    deck, geom = _deck(pour)
    f = geom["pour"]["fence"]
    assert f["count"] == 1
    assert f["skipped_mm"] == [(6.0, 0.0)]


def test_fence_via_outside_pour_is_skipped():
    pour = {"polygon_mm": POUR_POLY, "clearance_m": 0.20e-3,
            "fence_mm": [(6.0, 1.0, 0.4), (30.0, 30.0, 0.4)]}  # (30,30) outside polygon
    deck, geom = _deck(pour)
    f = geom["pour"]["fence"]
    assert f["count"] == 1
    assert f["skipped_mm"] == [(30.0, 30.0)]


def test_nport_deck_accepts_fence():
    graph = {"net": "TEE", "junction_mm": (0.0, 0.0), "branches": [
        {"points_mm": [(0.0, 0.0), (-8.0, 0.0)], "width_mm": 0.51},
        {"points_mm": [(0.0, 0.0), (8.0, 0.0)], "width_mm": 0.51},
        {"points_mm": [(0.0, 0.0), (0.0, 8.0)], "width_mm": 0.51}]}
    pour = {"polygon_mm": [(-10.0, -4.0), (10.0, -4.0), (10.0, 10.0),
                           (-10.0, 10.0)],
            "clearance_m": 0.20e-3,
            "fence_mm": [(-6.0, 2.0, 0.4), (6.0, 2.0, 0.4)]}
    deck, geom = ki.deck_kicad_nport(graph, CELL, 4e-9, 10.5e9, 3.66,
                                     0.254e-3, 0.0037, feed_port=1,
                                     pour=pour)
    assert geom["pour"]["fence"]["count"] == 2
    assert geom["pour"]["stitched_to_plane"] is True
