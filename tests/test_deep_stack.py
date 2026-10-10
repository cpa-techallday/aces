"""job30: In5.Cu deep stack region — deck structure tests (no gprMax runs)."""
import math

import pytest

from aces.domains.radar import kicad_import as ki
from aces.domains.radar.linkbudget import C0

CELL = 0.0508e-3
L = 5.9944
N_FR4 = 3
N_RO = 5


def _chain(mid_layer):
    return {"net": "deep", "sections": [
        {"layer": "F.Cu", "points_mm": [(0.0, 0.0), (L, 0.0)], "width_mm": 0.508},
        {"layer": mid_layer, "points_mm": [(L, 0.0), (2 * L, 0.0)], "width_mm": 0.2032},
        {"layer": "F.Cu", "points_mm": [(2 * L, 0.0), (3 * L, 0.0)], "width_mm": 0.508}],
        "vias_mm": [(L, 0.0, 0.4, 0.2), (2 * L, 0.0, 0.4, 0.2)], "pads": []}


def _boxes_at_z(deck, z_m):
    return [l for l in deck.splitlines()
            if l.startswith("#box:") and f"{z_m:.7f}" in l]


def test_in2_only_deck_unchanged():
    """Shallow chains keep the job27 stack exactly: In3 at the lift plane,
    no deep region, two planes only."""
    deck, geom = ki.deck_kicad_net_ml(_chain("In2.Cu"), CELL, 4e-9, 10.5e9)
    assert geom["deep_stack"] is False
    z_in3 = 12 * CELL
    planes = [l for l in deck.splitlines()
              if l.startswith("#box: 0 0") and l.endswith(" pec")]
    assert len(planes) == 2                     # In3 + In1 only
    assert _boxes_at_z(deck, z_in3)             # In3 still at the lift plane
    # domain height identical to job27 recipe
    import re
    dom_z = float(re.search(r"#domain: \S+ \S+ (\S+)", deck).group(1))
    assert dom_z == pytest.approx((12 + 1 + 3 + 1 + 3 + 1 + 5 + 1 + 16) * CELL)


def test_deep_stack_levels_and_planes():
    deck, geom = ki.deck_kicad_net_ml(_chain("In5.Cu"), CELL, 4e-9, 10.5e9)
    assert geom["deep_stack"] is True
    planes = [l for l in deck.splitlines()
              if l.startswith("#box: 0 0") and l.endswith(" pec")]
    assert len(planes) == 4                     # In6/In4/In3/In1
    z_in6 = 12 * CELL
    z_in5 = z_in6 + CELL + N_FR4 * CELL
    z_in4 = z_in5 + CELL + N_FR4 * CELL
    z_in3 = z_in4 + CELL + N_FR4 * CELL
    for zp in (z_in6, z_in4, z_in3):
        assert any(f"{zp:.7f}" in l for l in planes), f"missing plane at {zp}"
    # FR4 dielectric fills every gap below In3
    assert _boxes_at_z(deck, z_in6 + CELL)      # In6 -> In5 gap
    assert _boxes_at_z(deck, z_in4 + CELL)      # In4 -> In3 gap
    dom_z = float(__import__("re").search(r"#domain: \S+ \S+ (\S+)", deck).group(1))
    assert dom_z == pytest.approx(
        (12 + 1 + 3 + 1 + 3 + 1 + 3 + 1 + 3 + 1 + 3 + 1 + 5 + 1 + 16) * CELL)


def test_deep_via_barrel_antipads_stitches():
    deck, geom = ki.deck_kicad_net_ml(_chain("In5.Cu"), CELL, 4e-9, 10.5e9)
    z_in6 = 12 * CELL
    z_in5 = z_in6 + CELL + N_FR4 * CELL
    z_in4 = z_in5 + CELL + N_FR4 * CELL
    z_in3 = z_in4 + CELL + N_FR4 * CELL
    z_in2 = z_in3 + CELL + N_FR4 * CELL
    z_in1 = z_in2 + CELL + N_FR4 * CELL
    z_fc = z_in1 + CELL + N_RO * CELL
    # barrels span In5 -> F.Cu (2 vias)
    barrels = [l for l in deck.splitlines()
               if f"{z_in5:.7f}" in l and f"{z_fc + CELL:.7f}" in l
               and l.endswith(" pec") and not l.startswith("#box: 0 0")]
    assert len(barrels) == 2
    # antipads on In1, In3, In4 — never on In6 (below the signal layer)
    ap = [l for l in deck.splitlines() if l.endswith(" subfr4")
          and not l.startswith("#box: 0 0")]
    n_at = lambda zp: sum(1 for l in ap if f"{zp:.7f}" in l)
    assert n_at(z_in1) == 2 and n_at(z_in3) == 2 and n_at(z_in4) == 2
    assert n_at(z_in6) == 0
    # stitches span the full plane cage for deep vias (2 vias x 4 stitches)
    stitches = [l for l in deck.splitlines()
                if f"{z_in6:.7f}" in l and f"{z_in1 + CELL:.7f}" in l
                and l.endswith(" pec") and not l.startswith("#box: 0 0")]
    assert len(stitches) == 8


def test_shallow_via_recipe_unchanged():
    """In2 vias keep the job25 recipe: barrel In2->F.Cu, In3..In1 stitches,
    antipad on In1 only."""
    deck, geom = ki.deck_kicad_net_ml(_chain("In2.Cu"), CELL, 4e-9, 10.5e9)
    z_in3 = 12 * CELL
    z_in2 = z_in3 + CELL + N_FR4 * CELL
    z_in1 = z_in2 + CELL + N_FR4 * CELL
    z_fc = z_in1 + CELL + N_RO * CELL
    barrels = [l for l in deck.splitlines()
               if f"{z_in2:.7f}" in l and f"{z_fc + CELL:.7f}" in l
               and l.endswith(" pec") and not l.startswith("#box: 0 0")]
    assert len(barrels) == 2
    stitches = [l for l in deck.splitlines()
                if f"{z_in3:.7f}" in l and f"{z_in1 + CELL:.7f}" in l
                and l.endswith(" pec") and not l.startswith("#box: 0 0")]
    assert len(stitches) == 8


def test_per_via_transit():
    _, g2 = ki.deck_kicad_net_ml(_chain("In2.Cu"), CELL, 4e-9, 10.5e9)
    _, g5 = ki.deck_kicad_net_ml(_chain("In5.Cu"), CELL, 4e-9, 10.5e9)
    # shallow reproduces the job25 transit exactly
    assert g2["via_transits_s"][0] == pytest.approx(g2["via_transit_s"])
    # deep via: (4*(n_fr4+1) - 1) FR4 cells + n_ro RO4350B cells (the F.Cu
    # plane cell excluded, job25 convention)
    ro = N_RO * CELL * math.sqrt(3.66)
    fr4 = (4 * (N_FR4 + 1) - 1) * CELL * math.sqrt(4.10)
    assert g5["via_transits_s"][0] == pytest.approx((ro + fr4) / C0, rel=0.02)
    assert g5["via_transits_s"][0] > 2 * g2["via_transits_s"][0]


def test_expectations_use_per_via_transits():
    _, g5 = ki.deck_kicad_net_ml(_chain("In5.Cu"), CELL, 4e-9, 10.5e9)
    e5 = ki.expectations_kicad_net_ml(g5, 10.5e9)
    assert e5["delay_s_model"] == pytest.approx(
        sum(s["length_m"] * math.sqrt(s["er_eff"]) / C0
            for s in e5["sections"]) + sum(g5["via_transits_s"]), rel=1e-6)


def test_other_layers_still_raise():
    ch = _chain("In4.Cu")
    with pytest.raises(ValueError, match="not modelled"):
        ki.deck_kicad_net_ml(ch, CELL, 4e-9, 10.5e9)
