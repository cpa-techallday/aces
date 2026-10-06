import pytest

from aces import fluids, spec
from aces.calibration import Factor, Registry
from aces.domains.hx import HXRequirements, TwoFluidTPMS

BASE = {"tpms": "gyroid", "cell_m": 0.004, "wall_ratio": 0.1, "length_m": 0.1, "width_m": 0.06, "height_m": 0.06}


def _req(spec_dict):
    return spec.parse(spec_dict, HXRequirements)[1]


def test_energy_balance_closes(spec_dict):
    req = _req(spec_dict)
    ev = TwoFluidTPMS().evaluate(BASE, req)
    m = ev.metrics
    hot = fluids.state("Water", 0.5 * (req.hot.inlet_temperature_K + m["hot_outlet_K"]), req.hot.inlet_pressure_Pa)
    cold = fluids.state("Water", 0.5 * (req.cold.inlet_temperature_K + m["cold_outlet_K"]), req.cold.inlet_pressure_Pa)
    q_hot = req.hot.mass_flow_kg_s * hot.cp * (req.hot.inlet_temperature_K - m["hot_outlet_K"])
    q_cold = req.cold.mass_flow_kg_s * cold.cp * (m["cold_outlet_K"] - req.cold.inlet_temperature_K)
    assert q_hot == pytest.approx(m["duty_W"], rel=1e-3)
    assert q_cold == pytest.approx(m["duty_W"], rel=1e-3)


def test_second_law_respected(spec_dict):
    req = _req(spec_dict)
    m = TwoFluidTPMS().evaluate(BASE, req).metrics
    assert 0.0 < m["effectiveness"] < 1.0
    assert m["hot_outlet_K"] > req.cold.inlet_temperature_K
    assert m["cold_outlet_K"] < req.hot.inlet_temperature_K


def test_longer_core_transfers_more_and_costs_more_pressure(spec_dict):
    req = _req(spec_dict)
    model = TwoFluidTPMS()
    short = model.evaluate(BASE, req).metrics
    long_ = model.evaluate({**BASE, "length_m": 0.15}, req).metrics
    assert long_["duty_W"] > short["duty_W"]
    assert long_["hot"]["pressure_drop_Pa"] > short["hot"]["pressure_drop_Pa"]
    assert long_["mass_kg"] > short["mass_kg"]


def test_calibration_factor_is_applied_and_reported(spec_dict):
    req = _req(spec_dict)
    base = TwoFluidTPMS().evaluate(BASE, req)
    assert "hx.gyroid.friction" in base.uncalibrated
    reg = Registry()
    reg.set(Factor("hx.gyroid.friction", 2.0, sources=("simlab:test-run",)))
    cal = TwoFluidTPMS(registry=reg).evaluate(BASE, req)
    assert "hx.gyroid.friction" not in cal.uncalibrated
    assert cal.metrics["hot"]["pressure_drop_Pa"] == pytest.approx(2.0 * base.metrics["hot"]["pressure_drop_Pa"], rel=1e-6)


def test_registry_refuses_unsourced_factor():
    with pytest.raises(ValueError):
        Registry().set(Factor("hx.gyroid.nusselt", 1.7))


def test_constraints_flag_thin_walls(spec_dict):
    spec_dict["requirements"]["manufacturing"]["min_wall_m"] = 0.002
    req = _req(spec_dict)
    ev = TwoFluidTPMS().evaluate(BASE, req)
    assert ev.constraints["min_wall"] > 0 and not ev.feasible


def test_always_carries_scope_warnings(spec_dict):
    ev = TwoFluidTPMS().evaluate(BASE, _req(spec_dict))
    assert any("headers" in w for w in ev.warnings)
    assert any("verify on simlab" in w for w in ev.warnings)


def test_hot_must_be_hotter(spec_dict):
    spec_dict["requirements"]["hot"]["inlet_temperature_K"] = 280.0
    with pytest.raises(spec.SpecError):
        _req(spec_dict)
