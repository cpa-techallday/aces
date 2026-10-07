"""Tests for aces.costing (audit H2): hand-checked costs per route type, sourced inputs, exclusions, CLI.

Every rate and material value comes from tests/cost_rates_test_input.yaml, a labelled test input: the round
numbers are chosen so each cost can be checked by hand, and are not prices or material data.  The arithmetic
in the comments follows the cost model in the aces.costing module docstring.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from aces import costing
from aces.cli import main

RATES_PATH = Path(__file__).with_name("cost_rates_test_input.yaml")


@pytest.fixture(scope="module")
def rates() -> costing.Rates:
    return costing.load_rates(RATES_PATH)


def rates_dict() -> dict:
    return yaml.safe_load(RATES_PATH.read_text())


def hx_part(**kw) -> costing.PartProfile:
    """Small complex core (test input): bounding box 0.15 x 0.10 x 0.08 m, 400 cm3 solid."""
    d = {"name": "hx-core", "envelope_m": (0.15, 0.10, 0.08), "volume_m3": 0.0004, "min_feature_m": 0.0008,
         "max_temp_C": 60.0, "pressure_bar": 10.0, "tolerance_mm": 0.2, "complex_internal": True}
    d.update(kw)
    return costing.PartProfile(**d)


def block_part(**kw) -> costing.PartProfile:
    """Simple solid block, no internal features (test input): 0.2 x 0.12 x 0.05 m, 800 cm3."""
    d = {"name": "block", "envelope_m": (0.2, 0.12, 0.05), "volume_m3": 0.0008, "min_feature_m": 0.004,
         "max_temp_C": 40.0, "pressure_bar": 0.0, "tolerance_mm": 1.0, "complex_internal": False}
    d.update(kw)
    return costing.PartProfile(**d)


# ----------------------------------------------------------------------------- hand-checked costs


def test_lpbf_nested_plate_hand_check(rates):
    """Plate route: parts per build from the build volume and bounding box; builds = ceil(q / n).

    Test inputs: plate 0.25 x 0.25 m, 0.30 m tall, gap 0.01 m.  Part 0.15 x 0.10 x 0.08 m.
    Orientations (across x along, height):
      0.15 x 0.10 (h 0.08): floor(0.26/0.16)=1 x floor(0.26/0.11)=2 -> 2
      0.10 x 0.08 (h 0.15): floor(0.26/0.11)=2 x floor(0.26/0.09)=2 -> 4  <- best
    so n = 4.  metal_a: m = 0.0004 m3 x 2500 kg/m3 = 1.0 kg; 400 cm3.
    qty 1: builds = 1; H = 1 x 5 + 1 x (0.05 x 400 + 0 x 1.0) = 25 h
      machine 100 x 25 = 2500; build labour 80 x 0.1 x 25 = 200; material 1.0 / 0.5 x 100 = 200
      post 1 x 80 + 0.1 x 200 = 100; setup 2 x (100 + 80) = 360; tooling 0
      direct 3360; unit 3360 x 1.1 x 1.0 = 3696; band +/-30 % -> 2587.2 .. 4804.8
    qty 10: builds = ceil(10/4) = 3; H = 3 x 5 + 10 x 20 = 215 h
      machine 100 x 215/10 = 2150; build labour 80 x 0.1 x 215/10 = 172; material 200; post 100
      setup 360/10 = 36; direct 2658; unit 2658 x 1.1 = 2923.8
    """
    part = hx_part()
    e1 = costing.estimate(part, "lpbf", "metal_a", 1, rates=rates)
    assert e1.parts_per_build == 4 and e1.builds == 1
    assert e1.mass_kg == pytest.approx(1.0)
    assert e1.breakdown["machine"] == pytest.approx(2500.0)
    assert e1.breakdown["build_labour"] == pytest.approx(200.0)
    assert e1.breakdown["material"] == pytest.approx(200.0)
    assert e1.breakdown["post_processing"] == pytest.approx(100.0)
    assert e1.breakdown["setup_amortised"] == pytest.approx(360.0)
    assert e1.breakdown["tooling_amortised"] == 0.0
    assert e1.unit_aud == pytest.approx(3696.0)
    assert (e1.unit_low_aud, e1.unit_high_aud) == (pytest.approx(2587.2), pytest.approx(4804.8))
    e10 = costing.estimate(part, "lpbf", "metal_a", 10, rates=rates)
    assert e10.builds == 3
    assert e10.breakdown["machine"] == pytest.approx(2150.0)
    assert e10.breakdown["build_labour"] == pytest.approx(172.0)
    assert e10.unit_aud == pytest.approx(2923.8)
    assert e10.batch_aud == pytest.approx(29238.0)


def test_lpbf_build_not_shared_at_qty_one(rates):
    """The whole fixed build time falls on one part at qty 1, and a fifth part starts a second build.

    qty 4: builds 1, H = 5 + 4 x 20 = 85 h -> machine 100 x 85 / 4 = 2125 per part.
    qty 5: builds 2, H = 10 + 5 x 20 = 110 h -> machine 100 x 110 / 5 = 2200 per part.
    """
    part = hx_part()
    assert costing.estimate(part, "lpbf", "metal_a", 4, rates=rates).breakdown["machine"] == pytest.approx(2125.0)
    e5 = costing.estimate(part, "lpbf", "metal_a", 5, rates=rates)
    assert e5.builds == 2 and e5.breakdown["machine"] == pytest.approx(2200.0)


def test_robotic_single_part_route_hand_check(rates):
    """Single-part route, no tooling.  Panel 1.2 x 0.8 x 0.3 m, 0.02 m3 = 20 000 cm3, polymer_a 1250 kg/m3.

    m = 0.02 x 1250 = 25 kg.  qty 2: builds 2; H = 2 x 1 + 2 x (0.02 x 20 000) = 802 h
      machine 25 x 802/2 = 10 025; build labour 80 x 0.25 x 802/2 = 8020
      material 25 / 0.8 x 20 = 625; post 0.5 x 80 + 0.1 x 625 = 102.5; setup 3 x (25 + 80)/2 = 157.5
      direct 18 930; unit 18 930 x 1.05 = 19 876.5
    """
    panel = costing.PartProfile(name="panel", envelope_m=(1.2, 0.8, 0.3), volume_m3=0.02, min_feature_m=0.01,
                                max_temp_C=30.0, pressure_bar=0.0, complex_internal=True)
    e = costing.estimate(panel, "robotic_lattice_extrusion", "polymer_a", 2, rates=rates)
    assert e.parts_per_build == 1 and e.builds == 2
    assert e.breakdown["machine"] == pytest.approx(10025.0)
    assert e.breakdown["build_labour"] == pytest.approx(8020.0)
    assert e.breakdown["material"] == pytest.approx(625.0)
    assert e.breakdown["post_processing"] == pytest.approx(102.5)
    assert e.breakdown["setup_amortised"] == pytest.approx(157.5)
    assert e.unit_aud == pytest.approx(19876.5)


def test_arc_rod_welding_hand_check(rates):
    """Attended route with fixtures.  Chassis 2.8 x 1.9 x 1.2 m, 0.005 m3, metal_b 8000 kg/m3 -> 40 kg.

    qty 1: H = 1 x 2 + 1 x (0 + 1.0 x 40) = 42 h
      machine 40 x 42 = 1680; build labour 100 x 1.0 x 42 = 4200; material 40 / 0.8 x 10 = 500
      post 4 x 100 + 0.05 x 500 = 425; setup 8 x (40 + 100) = 1120; tooling ceil(1/200) = 1 set x 2000 = 2000
      direct 9925; unit 9925 x 1.05 = 10 421.25
    """
    chassis = costing.PartProfile(name="chassis", envelope_m=(2.8, 1.9, 1.2), volume_m3=0.005, min_feature_m=0.005,
                                  max_temp_C=40.0, pressure_bar=0.0, complex_internal=False)
    e = costing.estimate(chassis, "arc_lattice_rod_welding", "metal_b", 1, rates=rates)
    assert e.mass_kg == pytest.approx(40.0)
    assert e.breakdown["build_labour"] == pytest.approx(4200.0)
    assert e.breakdown["tooling_amortised"] == pytest.approx(2000.0)
    assert e.unit_aud == pytest.approx(10421.25)


def test_casting_hand_check_labour_and_whole_tooling_sets(rates):
    """Casting (planned): pour/fettle hours are labour, and tooling is bought in whole sets.

    Block 800 cm3, metal_a -> m = 0.0008 x 2500 = 2.0 kg.  qty 750:
      H = 750 x (0.5 h/kg x 2.0) = 750 h; machine 0 x ... = 0
      build labour 80 x 1.0 x 750/750 = 80   (the old model charged these hours at a A$0 machine rate)
      material 2.0 / 0.5 x 10 = 40; post 1 x 80 + 0.1 x 40 = 84; setup 4 x (0 + 80)/750 = 0.426667
      tooling ceil(750/500) = 2 sets x 9000 / 750 = 24   (not 9000/500 = 18)
      direct 228.426667; unit x 1.1 = 251.269333
    """
    e = costing.estimate(block_part(), "casting", "metal_a", 750, rates=rates)
    assert e.breakdown["build_labour"] == pytest.approx(80.0)
    assert e.breakdown["tooling_amortised"] == pytest.approx(24.0)
    assert e.breakdown["setup_amortised"] == pytest.approx(320.0 / 750)
    assert e.unit_aud == pytest.approx(228.426667 * 1.1, rel=1e-6)
    assert e.status == "planned" and any("never recommended" in a for a in e.assumptions)


def test_cnc_hand_check(rates):
    """CNC (planned).  Block, metal_a 2.0 kg.  qty 10:
      H = 10 x (0.5 x 2.0) = 10 h; machine 120 x 10/10 = 120; build labour 100 x 0.5 x 10/10 = 50
      material 2.0 / 0.4 x 20 = 100; post 0.5 x 100 + 0 = 50; setup 6 x (120 + 100)/10 = 132
      tooling ceil(10/400) = 1 set x 1000/10 = 100; direct 552; unit 552 x 1.05 = 579.6
    """
    e = costing.estimate(block_part(), "cnc_machining", "metal_a", 10, rates=rates)
    assert e.unit_aud == pytest.approx(579.6)


def test_mass_uses_each_materials_density(rates):
    """Same 400 cm3 part: metal_a 1.0 kg, metal_b 0.0004 x 8000 = 3.2 kg.  Material cost on LPBF:
    metal_a 1.0 / 0.5 x 100 = 200; metal_b 3.2 / 0.5 x 60 = 384, so metal_b is no longer cheaper per part
    just because its price per kg is lower (the audit's 316L-at-aluminium-mass case)."""
    part = hx_part()
    a = costing.estimate(part, "lpbf", "metal_a", 10, rates=rates)
    b = costing.estimate(part, "lpbf", "metal_b", 10, rates=rates)
    assert (a.mass_kg, b.mass_kg) == (pytest.approx(1.0), pytest.approx(3.2))
    assert (a.breakdown["material"], b.breakdown["material"]) == (pytest.approx(200.0), pytest.approx(384.0))
    best = next(s for s in costing.suggest(part, 10, rates=rates) if s.feasible)
    assert (best.process, best.material) == ("lpbf", "metal_a")


def test_rates_used_lists_every_source(rates):
    e = costing.estimate(hx_part(), "lpbf", "metal_a", 1, rates=rates)
    for k in ("processes.lpbf.machine_aud_h", "processes.lpbf.build_volume_m", "processes.lpbf.part_gap_m",
              "materials.metal_a.density_kg_m3", "materials.metal_a.feedstock_aud_kg.lpbf", "qa_factor.industrial"):
        assert e.rates_used[k]["source"] == "test input" and e.rates_used[k]["date"] == "2026-10-07"


def test_application_qa_factor_from_rates(rates):
    ind = costing.estimate(hx_part(), "lpbf", "metal_a", 1, rates=rates).unit_aud
    aero = costing.estimate(hx_part(application="aerospace"), "lpbf", "metal_a", 1, rates=rates).unit_aud
    assert aero == pytest.approx(ind * 1.3)  # 3696 x 1.3
    with pytest.raises(costing.CostInputError, match="qa_factor.defence"):
        costing.estimate(hx_part(application="defence"), "lpbf", "metal_a", 1, rates=rates)


# ----------------------------------------------------------------------------- routes and exclusions


def test_planned_routes_never_recommended(rates):
    """Casting and CNC are planned in aces.manufacturing: listed, never feasible, never first."""
    part = block_part(tolerance_mm=None)
    sug = costing.suggest(part, 1000, rates=rates)
    feasible = [s for s in sug if s.feasible]
    assert all(s.process not in ("casting", "cnc_machining") for s in feasible)
    planned = [s for s in sug if s.process in ("casting", "cnc_machining")]
    assert planned and all(not s.feasible and "planned, not available" in s.reasons[0] for s in planned)
    co = costing.crossovers(part, (("casting", "metal_a"), ("lpbf", "metal_a")), (1, 10, 100), rates=rates)
    assert co["planned"] == ["casting/metal_a"]
    assert all(lead["cheapest"] != "casting/metal_a" for lead in co["leaders"])


def test_service_temperature_and_pressure_margins(rates):
    """polymer_a is rated 80 C and 5 bar (test input).  An 80 C duty fails 80 >= 80 + 10; 70 C passes.
    3.4 bar fails 5 >= 1.5 x 3.4 = 5.1; 3.3 bar passes (4.95)."""
    def polymer_reasons(**kw):
        part = hx_part(min_feature_m=0.001, tolerance_mm=None, **kw)
        return [r for s in costing.suggest(part, 5, ("polymer_a",), rates=rates)
                if s.process == "polymer_extrusion" and not s.feasible for r in s.reasons]
    assert any("service temperature 80 C" in r for r in polymer_reasons(max_temp_C=80.0, pressure_bar=1.0))
    assert polymer_reasons(max_temp_C=70.0, pressure_bar=1.0) == []
    assert any("service pressure 5 bar" in r for r in polymer_reasons(max_temp_C=20.0, pressure_bar=3.4))
    assert polymer_reasons(max_temp_C=20.0, pressure_bar=3.3) == []


def test_missing_rates_are_reported_not_defaulted(rates):
    d = rates_dict()
    del d["processes"]["lpbf"]["machine_aud_h"]
    del d["materials"]["metal_a"]["max_service_temp_C"]
    r = costing.rates_from_dict(d, "test")
    with pytest.raises(costing.CostInputError, match="processes.lpbf.machine_aud_h"):
        costing.estimate(hx_part(), "lpbf", "metal_a", 1, rates=r)
    lp = [s for s in costing.suggest(hx_part(), 1, rates=r) if s.process == "lpbf"]
    assert lp and all(not s.feasible for s in lp)
    assert any("machine_aud_h" in reason for s in lp for reason in s.reasons)
    assert any("max_service_temp_C" in reason for s in lp if s.material == "metal_a" for reason in s.reasons)
    assert "processes.lpbf: machine_aud_h" in r.missing()


def test_curve_stops_at_route_quantity_limit(rates):
    pts = costing.curve(hx_part(), "lpbf", "metal_a", (1, 100, 500, 1000), rates=rates)  # limit 500
    assert [e.quantity for e in pts] == [1, 100, 500]
    with pytest.raises(costing.CostInputError, match="above the lpbf limit"):
        costing.estimate(hx_part(), "lpbf", "metal_a", 1000, rates=rates)
    co = costing.crossovers(hx_part(), (("lpbf", "metal_a"),), (10, 1000), rates=rates)
    assert co["curves"]["lpbf/metal_a"][1] is None
    assert co["leaders"][-1] == {"quantity": 1000, "cheapest": None, "unit_aud": None}


def test_big_part_excluded_by_build_volume(rates):
    big = costing.PartProfile(name="chassis", envelope_m=(2.8, 1.9, 1.2), volume_m3=0.005, min_feature_m=0.005,
                              max_temp_C=40.0, pressure_bar=0.0, complex_internal=False)
    sug = costing.suggest(big, 5, rates=rates)
    lpbf = [s for s in sug if s.process == "lpbf"]
    assert lpbf and all(not s.feasible for s in lpbf)
    assert any("build volume" in r for s in lpbf for r in s.reasons)
    assert any(s.feasible and (s.process, s.material) == ("arc_lattice_rod_welding", "metal_b") for s in sug)


def test_domain_limits_routes(rates):
    """A part from an hx.two_fluid run is only offered on routes that make hx.two_fluid parts."""
    part = hx_part(domain="hx.two_fluid", max_temp_C=20.0, pressure_bar=1.0, min_feature_m=0.005)
    feasible = {s.process for s in costing.suggest(part, 5, rates=rates) if s.feasible}
    assert feasible and feasible <= {"lpbf", "polymer_extrusion"}


# ----------------------------------------------------------------------------- input validation


@pytest.mark.parametrize("bad", [
    {"volume_m3": -0.0004}, {"volume_m3": float("nan")}, {"volume_m3": 0.01},  # 0.01 > 0.15 x 0.10 x 0.08
    {"envelope_m": (0.1, 0.1)}, {"envelope_m": (0.1, float("inf"), 0.1)}, {"min_feature_m": 0.0},
    {"max_temp_C": float("nan")}, {"pressure_bar": -1.0}, {"tolerance_mm": 0.0}, {"complex_internal": "no"},
])
def test_part_profile_rejects_bad_numbers(bad):
    with pytest.raises(costing.CostInputError):
        hx_part(**bad)


def test_from_dict_requires_conditions_and_refuses_mass():
    with pytest.raises(costing.CostInputError) as e:
        costing.PartProfile.from_dict({"envelope_m": [0.1, 0.1, 0.1], "volume_m3": 1e-4, "mass_kg": 1.2})
    msg = str(e.value)
    assert "mass_kg is not an input" in msg
    for k in ("min_feature_m", "max_temp_C", "pressure_bar"):
        assert f"{k}: missing" in msg
    with pytest.raises(costing.CostInputError, match="unknown key 'max_temp_c'"):
        costing.PartProfile.from_dict({"envelope_m": [0.1, 0.1, 0.1], "volume_m3": 1e-4, "min_feature_m": 1e-3,
                                       "max_temp_C": 20, "pressure_bar": 0, "max_temp_c": 20})


@pytest.mark.parametrize("q", [0, -3, 2.5, True])
def test_quantity_validated(rates, q):
    with pytest.raises(costing.CostInputError):
        costing.estimate(hx_part(), "lpbf", "metal_a", q, rates=rates)


@pytest.mark.parametrize("edit, match", [
    (lambda d: d["processes"]["lpbf"]["machine_aud_h"].pop("source"), "missing source"),
    (lambda d: d["processes"]["lpbf"]["machine_aud_h"].update(value=float("nan")), "finite number"),
    (lambda d: d["processes"]["lpbf"]["labour_aud_h"].update(value=-5), "must not be negative"),
    (lambda d: d["processes"]["lpbf"]["material_yield"].update(value=0), "above 0 and at most 1"),
    (lambda d: d["processes"]["lpbf"]["tooling_life"].update(value=2.5), "whole number"),
    (lambda d: d["processes"]["lpbf"]["build_volume_m"].update(value=[0.25, 0.25]), "three positive"),
    (lambda d: d["materials"]["metal_a"]["density_kg_m3"].update(date="last week"), "YYYY-MM-DD"),
    (lambda d: d["processes"].update(warp_drive={}), "unknown route"),
    (lambda d: d.pop("aces_cost_rates"), "not an ACES cost rates file"),
])
def test_rates_file_validation(edit, match):
    d = rates_dict()
    edit(d)
    with pytest.raises(costing.CostInputError, match=match):
        costing.rates_from_dict(d, "test")


def test_from_pareto_carries_conditions_from_spec(spec_dict):
    """Hot inlet 353.15 K -> 80.0 C; inlet pressures 2e5 Pa -> 2.0 bar (test spec in conftest)."""
    entry = {"params": {"length_m": 0.15, "width_m": 0.1, "height_m": 0.08},
             "metrics": {"core_volume_m3": 0.0012, "solid_fraction": 0.375, "wall_min_m": 0.0008}}
    part = costing.PartProfile.from_pareto(entry, "run1-rank0", spec=spec_dict)
    assert part.max_temp_C == pytest.approx(80.0)
    assert part.pressure_bar == pytest.approx(2.0)
    assert part.volume_m3 == pytest.approx(0.0012 * 0.375)
    assert part.domain == "hx.two_fluid" and part.complex_internal and part.tolerance_mm is None
    assert any("headers" in n for n in part.notes)


# ----------------------------------------------------------------------------- CLI


PART_YAML = {"part": {"name": "test-core", "envelope_m": [0.15, 0.10, 0.08], "volume_m3": 0.0004,
                      "min_feature_m": 0.0008, "max_temp_C": 60, "pressure_bar": 10, "tolerance_mm": 0.2,
                      "source": "test input"}}


def _cost(tmp_path, *extra, part=PART_YAML):
    p = tmp_path / "part.yaml"
    p.write_text(yaml.safe_dump(part))
    return main(["cost", str(p), "--rates", str(RATES_PATH), *extra])


def test_cli_cost_output_says_estimate_only_and_lists_sources(tmp_path, capsys):
    out_json = tmp_path / "est.json"
    assert _cost(tmp_path, "--quantity", "10", "--out", str(out_json)) == 0
    out = capsys.readouterr().out
    assert "Estimate only: not a quote, not verified" in out
    assert "## Rate sources" in out and "processes.lpbf.machine_aud_h = 100 (test input; 2026-10-07)" in out
    assert "Planned, not available" in out and "casting" in out
    rep = json.loads(out_json.read_text())
    assert rep["disclaimer"].startswith("Estimate only: not a quote, not verified")
    assert rep["rate_sources"]["processes.lpbf.machine_aud_h"]["source"] == "test input"
    assert rep["suggestions"][0]["process"] == "lpbf" and rep["suggestions"][0]["material"] == "metal_a"
    assert _cost(tmp_path, "--out", str(out_json)) == 2  # never overwrites


@pytest.mark.parametrize("args, part, needle", [
    (["--quantity", "0"], PART_YAML, "quantity must be"),
    (["--quantities", "10", "-5"], PART_YAML, "quantity must be"),
    (["--top", "0"], PART_YAML, "--top"),
    (["--materials", "unobtainium"], PART_YAML, "unobtainium"),
    (["--out", "/nonexistent-dir/x.json"], PART_YAML, "cannot write"),
    ([], ["not", "a", "mapping"], "mapping"),
    ([], {"part": {**PART_YAML["part"], "volume_m3": -1}}, "volume_m3"),
    ([], {"part": {**PART_YAML["part"], "mass_kg": 1.2}}, "mass_kg is not an input"),
])
def test_cli_cost_bad_inputs_exit_2_cleanly(tmp_path, capsys, args, part, needle):
    assert _cost(tmp_path, *args, part=part) == 2
    assert needle in capsys.readouterr().out


def test_cli_cost_needs_rates_and_readable_files(tmp_path, capsys):
    p = tmp_path / "part.yaml"
    p.write_text(yaml.safe_dump(PART_YAML))
    assert main(["cost", str(p)]) == 2
    assert "--rates" in capsys.readouterr().out
    assert main(["cost", str(tmp_path / "missing.yaml"), "--rates", str(RATES_PATH)]) == 2
    assert "missing.yaml" in capsys.readouterr().out
    (tmp_path / "bad.yaml").write_text("part: [unclosed")
    assert main(["cost", str(tmp_path / "bad.yaml"), "--rates", str(RATES_PATH)]) == 2
    assert main(["cost", str(p), "--rates", str(tmp_path / "missing-rates.yaml")]) == 2


def test_cli_cost_from_verified_hot_water_run(tmp_path, spec_dict, capsys):
    """A real 80 C water design run: the polymer rated 80 C is excluded on temperature, the run is loaded only
    after its evidence record verifies (audit M2), and an edited pareto.json is refused."""
    spec_dict["search"]["trials"] = 40
    sp = tmp_path / "spec.yaml"
    sp.write_text(yaml.safe_dump(spec_dict))
    ledger = tmp_path / "ledger.jsonl"
    run = tmp_path / "run"
    assert main(["design", str(sp), "--out", str(run), "--ledger", str(ledger)]) == 0
    out_json = tmp_path / "cost.json"
    rc = main(["cost", "--from-run", str(run), "--ledger", str(ledger), "--rates", str(RATES_PATH), "--quantity",
               "5", "--verbose", "--out", str(out_json)])
    out = capsys.readouterr().out
    assert rc in (0, 1)
    assert "80.0 C" in out and "Estimate only: not a quote, not verified" in out
    rep = json.loads(out_json.read_text())
    assert rep["part"]["max_temp_C"] == pytest.approx(80.0)
    poly = [s for s in rep["suggestions"] if s["material"] == "polymer_a" and s["process"] == "polymer_extrusion"]
    assert poly and not poly[0]["feasible"]
    assert any("service temperature 80 C" in r for r in poly[0]["reasons"])
    assert all(s["material"] != "polymer_a" for s in rep["suggestions"] if s["feasible"])

    assert main(["cost", "--from-run", str(run), "--ledger", str(ledger), "--rates", str(RATES_PATH), "--rank",
                 "99"]) == 2
    assert "--rank" in capsys.readouterr().out

    tampered = tmp_path / "tampered"
    shutil.copytree(run, tampered)
    pareto = json.loads((tampered / "pareto.json").read_text())
    pareto[0]["metrics"]["solid_fraction"] *= 0.5
    (tampered / "pareto.json").write_text(json.dumps(pareto))
    assert main(["cost", "--from-run", str(tampered), "--rates", str(RATES_PATH), "--ledger", str(ledger)]) == 2
    assert "no longer matches its evidence record" in capsys.readouterr().out


def test_test_rates_file_is_labelled():
    text = RATES_PATH.read_text()
    assert text.startswith("# TEST INPUT ONLY")
    d = rates_dict()
    sources = [v["source"] for m in d["materials"].values() for k, v in m.items() if isinstance(v, dict)
               and "source" in v]
    sources += [v["source"] for m in d["materials"].values() for v in m["feedstock_aud_kg"].values()]
    sources += [v["source"] for p in d["processes"].values() for v in p.values()]
    sources += [v["source"] for v in d["qa_factor"].values()]
    assert sources and all(s == "test input" for s in sources)


def test_an_impossible_date_in_the_rates_file_is_a_clean_error(tmp_path):
    """Verification finding: `date: 2026-13-01` raised a traceback."""
    p = tmp_path / "rates.yaml"
    p.write_text(RATES_PATH.read_text().replace("2026-10-07", "2026-13-01", 1))
    with pytest.raises(costing.CostInputError):
        costing.load_rates(p)
