"""Tests for aces.costing: route suggestion sanity, cost-curve shape, crossovers."""
from __future__ import annotations

import pytest

from aces import costing


def hx_part() -> costing.PartProfile:
    """Small complex aluminium heat-exchanger core, like an hx.two_fluid pareto entry."""
    return costing.PartProfile(
        name="hx-core", envelope_m=(0.15, 0.10, 0.08), volume_m3=0.00045, mass_kg=1.2,
        min_feature_m=0.0008, max_temp_C=180.0, pressure_bar=15.0, tolerance_mm=0.2,
        complex_internal=True, application="industrial")


def block_part() -> costing.PartProfile:
    """Simple solid block, no internal features - the case CNC and casting exist for."""
    return costing.PartProfile(
        name="bracket", envelope_m=(0.2, 0.12, 0.05), volume_m3=0.0006, mass_kg=1.6,
        min_feature_m=0.002, max_temp_C=80.0, pressure_bar=1.0, tolerance_mm=0.05,
        complex_internal=False, application="industrial")


def test_every_process_and_material_named_exists():
    for pc in costing.PROCESSES.values():
        for mid in pc.materials:
            assert mid in costing.MATERIALS, f"{pc.id} names unknown material {mid}"
            assert pc.id in costing.MATERIALS[mid].feedstock_aud_kg, \
                f"{mid} has no {pc.id} feedstock price"


def test_unit_cost_falls_with_quantity():
    part = hx_part()
    units = [e.unit_aud for e in costing.curve(part, "lpbf", "alsi10mg", (1, 10, 100))]
    assert units[0] > units[1] > units[2]


def test_casting_loses_at_one_wins_at_scale_for_simple_parts():
    part = block_part()
    one_cast = costing.estimate(part, "casting", "alsi10mg", 1).unit_aud
    one_cnc = costing.estimate(part, "cnc_machining", "al6061", 1).unit_aud
    assert one_cast > one_cnc, "tooling should make casting the wrong answer for a one-off"
    big_cast = costing.estimate(part, "casting", "alsi10mg", 5_000).unit_aud
    big_cnc = costing.estimate(part, "cnc_machining", "al6061", 5_000).unit_aud
    assert big_cast < big_cnc, "at volume, casting should undercut machining"


def test_suggestion_prefers_lpbf_for_complex_metal_core_at_low_qty():
    part = hx_part()
    feasible = [s for s in costing.suggest(part, 10) if s.feasible]
    assert feasible, "expected at least one feasible route"
    assert feasible[0].process == "lpbf"
    assert all(s.process != "cnc_machining" for s in feasible), \
        "CNC must be excluded for internal-channel cores"


def test_suggestion_excludes_polymers_for_hot_high_pressure_duty():
    part = hx_part()  # 180 C, 15 bar
    sug = costing.suggest(part, 10)
    polymer = [s for s in sug if s.process == "polymer_extrusion"]
    assert polymer and all(not s.feasible for s in polymer)


def test_suggestion_gives_reasons_for_exclusion():
    part = block_part()
    sug = costing.suggest(part, 10, preferred_materials=("al6061",))
    cnc = [s for s in sug if s.process == "cnc_machining"]
    assert cnc and cnc[0].feasible
    tight = costing.PartProfile(**{**part.__dict__, "tolerance_mm": 0.05})
    sug2 = costing.suggest(tight, 10, preferred_materials=("abs",))
    cast = [s for s in sug2 if s.process == "casting"]
    assert cast and not cast[0].feasible and cast[0].reasons


def test_big_part_excludes_small_envelope_processes():
    big = costing.PartProfile(name="chassis", envelope_m=(2.8, 1.9, 1.2), volume_m3=0.05,
                              mass_kg=55.0, min_feature_m=0.005, complex_internal=False)
    sug = costing.suggest(big, 5)
    lpbf = [s for s in sug if s.process == "lpbf"]
    assert lpbf and all(not s.feasible for s in lpbf)
    assert any("envelope" in r for s in lpbf for r in s.reasons)
    assert any(s.feasible and s.process == "arc_lattice_rod_welding" for s in sug)


def test_crossover_reports_cheapest_leader():
    part = block_part()
    co = costing.crossovers(part, (("casting", "alsi10mg"), ("cnc_machining", "al6061")))
    assert co["leaders"][0]["cheapest"].startswith("cnc_machining")
    assert any(l["cheapest"].startswith("casting") for l in co["leaders"]), \
        "expected a crossover to casting at volume"
    qs = [l["quantity"] for l in co["leaders"]]
    assert qs == sorted(qs)


def test_estimate_rejects_incompatible_material():
    with pytest.raises(ValueError):
        costing.estimate(hx_part(), "lpbf", "abs", 10)


def test_application_qa_factor_raises_cost():
    part = hx_part()
    ind = costing.estimate(part, "lpbf", "alsi10mg", 10).unit_aud
    aero = costing.estimate(
        costing.PartProfile(**{**part.__dict__, "application": "aerospace"}),
        "lpbf", "alsi10mg", 10).unit_aud
    assert aero > ind


def test_from_pareto_entry():
    entry = {"params": {"length_m": 0.15, "width_m": 0.1, "height_m": 0.08},
             "metrics": {"core_volume_m3": 0.0012, "solid_fraction": 0.375,
                         "mass_kg": 1.2, "wall_min_m": 0.0008}}
    part = costing.PartProfile.from_pareto(entry, "run1-rank0")
    assert part.complex_internal
    assert part.min_feature_m == pytest.approx(0.0008)
    assert part.volume_m3 == pytest.approx(0.0012 * 0.375)


def test_uncertainty_bands_bracket_central():
    e = costing.estimate(hx_part(), "lpbf", "alsi10mg", 10)
    assert e.unit_low_aud < e.unit_aud < e.unit_high_aud
    assert abs(e.batch_aud - e.unit_aud * 10) < 1e-6
    assert set(e.breakdown) == {"material", "machine", "post_processing",
                                "setup_amortised", "tooling_amortised", "scrap_and_qa"}
