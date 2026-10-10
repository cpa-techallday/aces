"""job31: net_graph — junction-graph extraction from KiCad net topology,
and equivalence of the extracted deck vs the hand-written job29 graph."""
import pathlib
import tempfile

import pytest

from aces.domains.radar import kicad_import as ki

TEE_FIXTURE = """(kicad_pcb (version 20240108)
  (net 0 "")
  (net 1 "TEE")
  (segment (start 32.004 29.972) (end 40.005 29.972) (width 0.51) (layer "F.Cu") (net 1))
  (segment (start 40.005 29.972) (end 48.006 29.972) (width 0.51) (layer "F.Cu") (net 1))
  (segment (start 40.005 29.972) (end 40.005 37.973) (width 0.51) (layer "F.Cu") (net 1))
)"""


def _parse(body: str, nets: str = '  (net 1 "X")'):
    fix = ("(kicad_pcb (version 20240108)\n  (net 0 \"\")\n" + nets + "\n"
           + body + "\n)")
    p = pathlib.Path(tempfile.mkdtemp()) / "t.kicad_pcb"
    p.write_text(fix, encoding="utf-8")
    return ki.parse_pcb(p)


@pytest.fixture
def tee_pcb():
    p = pathlib.Path(tempfile.mkdtemp()) / "tee.kicad_pcb"
    p.write_text(TEE_FIXTURE, encoding="utf-8")
    return ki.parse_pcb(p)


def test_tee_extraction(tee_pcb):
    g = ki.net_graph(tee_pcb, "TEE")
    assert g["junction_mm"] == (40.005, 29.972)
    assert len(g["branches"]) == 3
    assert all(b["width_mm"] == 0.51 for b in g["branches"])
    # every branch starts at the junction and ends at a degree-1 node
    for b in g["branches"]:
        assert b["points_mm"][0] == g["junction_mm"]
    ends = sorted(g["ends_mm"])
    assert ends == sorted([(32.004, 29.972), (48.006, 29.972),
                           (40.005, 37.973)])
    # deterministic order: descending angle from +x -> (-x arm, +y arm, +x arm)
    assert g["ends_mm"] == [(32.004, 29.972), (40.005, 37.973),
                            (48.006, 29.972)]


def test_chain_raises_with_guidance():
    pcb = _parse('  (segment (start 1 1) (end 5 1) (width 0.5) (layer "F.Cu") (net 1))')
    with pytest.raises(ValueError, match="use kicad_net"):
        ki.net_graph(pcb, "X")


def test_via_raises():
    pcb = _parse(
        '  (segment (start 1 1) (end 5 1) (width 0.5) (layer "F.Cu") (net 1))\n'
        '  (segment (start 5 1) (end 5 5) (width 0.5) (layer "F.Cu") (net 1))\n'
        '  (segment (start 5 1) (end 9 1) (width 0.5) (layer "F.Cu") (net 1))\n'
        '  (via (at 5 1) (size 0.6) (drill 0.3) (layers "F.Cu" "In1.Cu") (net 1))')
    with pytest.raises(ValueError, match="single-layer"):
        ki.net_graph(pcb, "X")


def test_off_layer_raises():
    pcb = _parse(
        '  (segment (start 1 1) (end 5 1) (width 0.5) (layer "F.Cu") (net 1))\n'
        '  (segment (start 5 1) (end 9 1) (width 0.5) (layer "In2.Cu") (net 1))\n'
        '  (segment (start 5 1) (end 5 5) (width 0.5) (layer "F.Cu") (net 1))')
    with pytest.raises(ValueError, match="off F.Cu"):
        ki.net_graph(pcb, "X")


def test_loop_raises():
    pcb = _parse(
        '  (segment (start 1 1) (end 5 1) (width 0.5) (layer "F.Cu") (net 1))\n'
        '  (segment (start 5 1) (end 5 5) (width 0.5) (layer "F.Cu") (net 1))\n'
        '  (segment (start 5 5) (end 1 1) (width 0.5) (layer "F.Cu") (net 1))')
    with pytest.raises(ValueError, match="loop"):
        ki.net_graph(pcb, "X")


def test_two_junctions_raises():
    pcb = _parse(
        '  (segment (start 1 1) (end 5 1) (width 0.5) (layer "F.Cu") (net 1))\n'
        '  (segment (start 5 1) (end 9 1) (width 0.5) (layer "F.Cu") (net 1))\n'
        '  (segment (start 5 1) (end 5 5) (width 0.5) (layer "F.Cu") (net 1))\n'
        '  (segment (start 9 1) (end 9 5) (width 0.5) (layer "F.Cu") (net 1))\n'
        '  (segment (start 9 1) (end 13 1) (width 0.5) (layer "F.Cu") (net 1))')
    with pytest.raises(ValueError, match="single-junction"):
        ki.net_graph(pcb, "X")


def test_mixed_width_within_branch_raises():
    pcb = _parse(
        '  (segment (start 1 1) (end 3 1) (width 0.5) (layer "F.Cu") (net 1))\n'
        '  (segment (start 3 1) (end 5 1) (width 0.8) (layer "F.Cu") (net 1))\n'
        '  (segment (start 5 1) (end 9 1) (width 0.5) (layer "F.Cu") (net 1))\n'
        '  (segment (start 5 1) (end 5 5) (width 0.5) (layer "F.Cu") (net 1))')
    with pytest.raises(ValueError, match="mixed widths"):
        ki.net_graph(pcb, "X")


def test_no_segments_raises(tee_pcb):
    with pytest.raises(ValueError, match="no segments"):
        ki.net_graph(tee_pcb, "NOPE")


def test_extracted_deck_matches_handwritten(tee_pcb):
    """Extracted graph, reordered to the job29 port order and translated to
    the origin, must rasterise to exactly the same deck as the hand-written
    job29 graph."""
    g = ki.net_graph(tee_pcb, "TEE")
    jn = g["junction_mm"]
    rel = {(-8.001, 0.0): None}  # placeholder; reorder by job29 arm order
    # job29 port order: (-8,0), (8,0), (0,8) relative to the junction
    job29_ends = [(-8.001, 0.0), (8.001, 0.0), (0.0, 8.001)]
    branches = []
    for want in job29_ends:
        for b in g["branches"]:
            end = (b["points_mm"][-1][0] - jn[0],
                   b["points_mm"][-1][1] - jn[1])
            if abs(end[0] - want[0]) < 1e-6 and abs(end[1] - want[1]) < 1e-6:
                branches.append({"points_mm": [
                    (p[0] - jn[0], p[1] - jn[1]) for p in b["points_mm"]],
                    "width_mm": b["width_mm"]})
                break
        else:
            raise AssertionError(f"no extracted branch ends at {want}")
    g_rel = {"net": "TEE", "junction_mm": (0.0, 0.0), "branches": branches}
    g_hand = {"net": "TEE", "junction_mm": (0.0, 0.0), "branches": [
        {"points_mm": [(0.0, 0.0), (-8.0, 0.0)], "width_mm": 0.51},
        {"points_mm": [(0.0, 0.0), (8.0, 0.0)], "width_mm": 0.51},
        {"points_mm": [(0.0, 0.0), (0.0, 8.0)], "width_mm": 0.51}]}
    cell = 0.00127 / 4
    d1, geo1 = ki.deck_kicad_nport(g_rel, cell, 4e-9, 10.5e9, 3.66, 0.00127,
                                   0.003, feed_port=1)
    d2, geo2 = ki.deck_kicad_nport(g_hand, cell, 4e-9, 10.5e9, 3.66, 0.00127,
                                   0.003, feed_port=1)
    # extracted arms are 8.001 mm (fixture) vs 8.0 mm hand-written; both
    # snap to the same cells, so geometry metrics must match exactly
    assert geo1["rasterised_cells"] == geo2["rasterised_cells"]
    assert geo1["n_ports"] == geo2["n_ports"] == 3
    assert d1.count("#box") == d2.count("#box")
