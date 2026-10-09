"""job26: arbitrary-angle (staircase-rasterised) trace import."""
import math

import pytest

from aces.domains.radar import kicad_import as ki


CELL = 0.0508e-3


def _connectivity_ok(cells):
    """Every cell reachable from the first via face adjacency."""
    cells = set(cells)
    seen = {next(iter(cells))}
    frontier = list(seen)
    while frontier:
        i, j = frontier.pop()
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if (i + di, j + dj) in cells and (i + di, j + dj) not in seen:
                seen.add((i + di, j + dj))
                frontier.append((i + di, j + dj))
    return seen == cells


def test_rasterize_axis_aligned_matches_box_width():
    # horizontal capsule at y centred on a grid line, w = 4 cells:
    # the middle rows match the fast-path box exactly; the round end disks
    # extend ~half_w past the ends (joint continuity with neighbours/pads)
    cells = ki.rasterize_capsule_cells((0.0, 5 * CELL), (20 * CELL, 5 * CELL),
                                       2 * CELL, CELL)
    js = {j for _, j in cells}
    assert js == {3, 4, 5, 6}          # 4 cells tall, centred on j=5 line
    body = {(i, j) for i, j in cells if 0 <= i <= 19}
    for j in (3, 4, 5, 6):             # body fully covers the segment box
        assert sum(1 for i, jj in body if jj == j) == 20
    assert min(i for i, _ in cells) == -2        # round end disks
    assert max(i for i, _ in cells) == 21


def test_rasterize_diagonal_is_face_connected_after_bridge():
    for deg in (10.5, 18.4, 45.0):
        a = math.radians(deg)
        p2 = (12e-3 * math.cos(a), 12e-3 * math.sin(a))
        cells = ki.rasterize_capsule_cells((0.0, 0.0), p2, 0.127e-3, CELL)
        cells = ki.make_face_connected(cells)
        assert _connectivity_ok(cells), f"deg={deg}"
        # width sanity: perpendicular span stays within ~w + sqrt(2)*cell
        span = max(j for _, j in cells) - min(j for _, j in cells) + 1
        assert span * CELL <= 0.254e-3 + 1.5 * CELL + 12e-3 * math.sin(a)


def test_make_face_connected_bridges_corner_touch():
    cells = {(0, 0), (1, 1)}
    out = ki.make_face_connected(cells)
    assert _connectivity_ok(out)
    assert len(out) == 3


def test_merge_cell_runs_single_and_split():
    assert ki.merge_cell_runs({(2, 5)}) == [(2, 5, 2, 5)]
    boxes = ki.merge_cell_runs({(0, 0), (1, 0), (2, 0), (4, 0), (0, 1)})
    assert sorted(boxes) == [(0, 0, 2, 0), (0, 1, 0, 1), (4, 0, 4, 0)]


DIAG_FIXTURE = """(kicad_pcb (version 20240108)
  (net 0 "")
  (net 1 "DIAG")
  (segment (start 10.0 20.0) (end 16.0 20.0) (width 0.51) (layer "F.Cu") (net 1))
  (segment (start 16.0 20.0) (end 22.0 22.0) (width 0.51) (layer "F.Cu") (net 1))
)
"""


def test_net_polyline_accepts_diagonal(tmp_path):
    # a chain with one 18.4-degree segment must import (previously raised)
    p = tmp_path / "diag.kicad_pcb"
    p.write_text(DIAG_FIXTURE, encoding="utf-8")
    poly = ki.net_polyline(ki.parse_pcb(p), "DIAG")
    assert poly["n_diagonal"] == 1
    assert len(poly["points_mm"]) == 3
    assert poly["points_mm"][1] == (16.0, 20.0)


def test_deck_diagonal_trace_is_rasterised_and_contained():
    # diagonal middle section with axis-aligned lead-ins (job26 coupon form)
    a = math.radians(18.4)
    poly = {"points_mm": [(0.0, 0.0), (1.5, 0.0),
                          (1.5 + 9 * math.cos(a), 9 * math.sin(a)),
                          (3.0 + 9 * math.cos(a), 9 * math.sin(a))],
            "width_mm": 0.51, "pads": [], "net": "diag_test"}
    deck, geom = ki.deck_kicad_net(poly, CELL, 4e-9, 10.5e9, 3.66, 0.254e-3, 0.0037)
    assert geom["n_diagonal_segments"] == 1
    assert geom["rasterised_cells"] > 0
    # many small boxes (the staircase), none wider than the trace capsule
    boxes = [l for l in deck.splitlines() if l.startswith("#box:") and l.endswith(" pec")]
    stair = [b for b in boxes if float(b.split()[2]) > 0  # skip the GND plane
             and "sub" not in b]
    assert len(stair) > 20
    # every stair box is within the domain
    dom = None
    for line in deck.splitlines():
        toks = line.split()
        if toks[0] == "#domain:":
            dom = [float(v) for v in toks[1:4]]
    for line in deck.splitlines():
        toks = line.split()
        if toks[0] == "#voltage_source:":
            vals = [float(v) for v in toks[2:5]]   # x y z only (skip 'z', R)
        elif toks[0] in ("#box:", "#rx:"):
            vals = [float(v) for v in toks[1:] if _is_float(v)]
        else:
            continue
        xs, ys, zs = vals[0::3], vals[1::3], vals[2::3]
        assert all(0 <= v <= dom[0] + 1e-9 for v in xs)
        assert all(0 <= v <= dom[1] + 1e-9 for v in ys)
        assert all(0 <= v <= dom[2] + 1e-9 for v in zs)


def test_deck_axis_aligned_polyline_unchanged():
    # the fast path must still emit exactly one box per segment
    poly = {"points_mm": [(0.0, 0.0), (6.0, 0.0), (6.0, 6.0)],
            "width_mm": 0.51, "pads": [], "net": "l_test"}
    deck, geom = ki.deck_kicad_net(poly, CELL, 4e-9, 10.5e9, 3.66, 0.254e-3, 0.0037)
    assert geom["n_diagonal_segments"] == 0
    assert geom["rasterised_cells"] == 0


def test_deck_rejects_diagonal_end_section():
    # job26 finding: a probe triad on a staircase reads cell-scale ripple
    # through an ill-conditioned split -> fake gain / non-reciprocity.
    a = math.radians(10.5)
    poly = {"points_mm": [(0.0, 0.0), (12 * math.cos(a), 12 * math.sin(a))],
            "width_mm": 0.51, "pads": [], "net": "diag_end"}
    with pytest.raises(ValueError, match="end section is diagonal"):
        ki.deck_kicad_net(poly, CELL, 4e-9, 10.5e9, 3.66, 0.254e-3, 0.0037)


def _is_float(s):
    try:
        float(s)
        return True
    except ValueError:
        return False
