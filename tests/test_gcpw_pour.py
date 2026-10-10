"""job28: F.Cu GND pour (GCPW) — zone polygons, pour rasterisation, deck
integration.  Pure geometry/deck tests (no gprMax runs)."""
import pytest

from aces.domains.radar import kicad_import as ki


CELL = 0.0635e-3          # h/4 at h = 0.254 mm
W = 0.51e-3               # RXO_1 redesign width


# ------------------------------------------------------------------ polygon
def test_point_in_polygon_rect():
    poly = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)]
    assert ki.point_in_polygon(5.0, 2.5, poly)
    assert not ki.point_in_polygon(11.0, 2.5, poly)
    assert not ki.point_in_polygon(5.0, 6.0, poly)
    assert ki.point_in_polygon(0.0, 2.5, poly)      # boundary counts as inside
    assert ki.point_in_polygon(10.0, 0.0, poly)     # corner


def test_point_in_polygon_concave():
    # L-shaped polygon
    poly = [(0.0, 0.0), (10.0, 0.0), (10.0, 4.0), (4.0, 4.0),
            (4.0, 10.0), (0.0, 10.0)]
    assert ki.point_in_polygon(2.0, 8.0, poly)
    assert ki.point_in_polygon(8.0, 2.0, poly)
    assert not ki.point_in_polygon(8.0, 8.0, poly)  # inside the notch


# ------------------------------------------------------------------ pour raster
def _straight_keepout(g: float):
    return ki.rasterize_capsule_cells((0.0, 0.0), (12e-3, 0.0),
                                      W / 2 + g, CELL)


def test_pour_clearance_enforced():
    g = 0.3e-3
    poly_m = [(-2e-3, -4e-3), (14e-3, -4e-3), (14e-3, 4e-3), (-2e-3, 4e-3)]
    nx = round(16e-3 / CELL)
    ny = round(8e-3 / CELL)
    # deck frame: shift polygon so origin is at (-2mm, -4mm)
    poly_shift = [(x + 2e-3, y + 4e-3) for x, y in poly_m]
    keepout = {(i + round(2e-3 / CELL), j + round(4e-3 / CELL))
               for i, j in _straight_keepout(g)}
    pour = ki.rasterize_pour_cells(poly_shift, CELL, nx, ny, keepout)
    assert pour, "pour must not be empty"
    # every pour cell centre is strictly beyond w/2 + g from the trace axis
    # (keepout swallows every cell whose centre is within the clearance)
    y_trace = 4e-3
    for i, j in pour:
        cx, cy = (i + 0.5) * CELL, (j + 0.5) * CELL
        in_span = 2e-3 <= cx <= 14e-3
        if in_span:
            assert abs(cy - y_trace) > W / 2 + g - 1e-15
    # pour exists on BOTH sides of the trace (GCPW, not a single-sided strip)
    sides = {1 if cy > y_trace else -1
             for i, j in pour
             if 3e-3 <= (cx := (i + 0.5) * CELL) <= 13e-3
             for cy in [(j + 0.5) * CELL]}
    assert sides == {1, -1}


def test_pour_clipped_to_domain():
    poly_m = [(-10e-3, -10e-3), (30e-3, -10e-3), (30e-3, 10e-3), (-10e-3, 10e-3)]
    nx, ny = 50, 40
    pour = ki.rasterize_pour_cells(poly_m, CELL, nx, ny, set())
    assert pour
    assert all(0 <= i < nx and 0 <= j < ny for i, j in pour)
    # huge polygon covers the whole domain
    assert len(pour) == nx * ny


def test_pour_clearance_scales_with_g():
    poly_m = [(0.0, 0.0), (12e-3, 0.0), (12e-3, 6e-3), (0.0, 6e-3)]
    nx, ny = round(12e-3 / CELL), round(6e-3 / CELL)
    n_cells = []
    for g in (0.2e-3, 0.5e-3):
        keep = {(i + round(2e-3 / CELL), j + round(3e-3 / CELL))
                for i, j in _straight_keepout(g)}
        pour = ki.rasterize_pour_cells(poly_m, CELL, nx, ny, keep)
        n_cells.append(len(pour))
    assert n_cells[0] > n_cells[1]   # wider clearance -> less pour copper


# ------------------------------------------------------------------ deck integration
def _polyline():
    return {"net": "GCPW_COUPON", "points_mm": [(0.0, 0.0), (12.0, 0.0)],
            "width_mm": 0.51, "pads": []}


def test_deck_pour_boxes_and_geom():
    poly = _polyline()
    pour = {"polygon_mm": [(-2.0, -4.0), (14.0, -4.0), (14.0, 4.0), (-2.0, 4.0)],
            "clearance_m": 0.3e-3}
    deck0, geom0 = ki.deck_kicad_net(poly, CELL, 4e-9, 10.5e9, 3.66, 0.254e-3,
                                     0.0037, feed_end=1)
    deck1, geom1 = ki.deck_kicad_net(poly, CELL, 4e-9, 10.5e9, 3.66, 0.254e-3,
                                     0.0037, feed_end=1, pour=pour)
    assert "pour_present" not in geom0            # backward compatible
    assert geom1["pour_present"] is True
    assert geom1["pour"]["cells"] > 0
    assert geom1["pour"]["clearance_m"] == pytest.approx(0.3e-3)
    assert not geom1["pour"]["stitched_to_plane"]  # documented limitation
    n_boxes0 = sum(1 for ln in deck0.splitlines() if ln.startswith("#box:"))
    n_boxes1 = sum(1 for ln in deck1.splitlines() if ln.startswith("#box:"))
    assert n_boxes1 > n_boxes0                    # pour adds copper
    # pour boxes sit on the trace plane (same z as the trace box)
    z_trace = 12 * CELL + CELL + 4 * CELL         # lift + gnd + n_sub(=4)
    trace_z = [ln for ln in deck1.splitlines()
               if ln.startswith("#box:") and f"{z_trace:.7f}" in ln]
    assert len(trace_z) >= 3                      # trace + pour rows


def test_deck_pour_shrinks_with_clearance():
    """Wider clearance -> strictly less pour copper in the emitted deck."""
    poly = _polyline()
    poly_mm = [(-2.0, -4.0), (14.0, -4.0), (14.0, 4.0), (-2.0, 4.0)]
    counts = []
    for g in (0.15e-3, 0.45e-3):
        _, geom = ki.deck_kicad_net(poly, CELL, 4e-9, 10.5e9, 3.66, 0.254e-3,
                                    0.0037, feed_end=1,
                                    pour={"polygon_mm": poly_mm, "clearance_m": g})
        counts.append(geom["pour"]["cells"])
    assert counts[0] > counts[1]
