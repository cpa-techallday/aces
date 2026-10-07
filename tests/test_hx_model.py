import math

import pytest
from CoolProp.CoolProp import PropsSI

from aces import fluids, spec
from aces.calibration import Factor, Registry
from aces.domains.hx import HXRequirements, TwoFluidTPMS, tpms

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
    reg.set(Factor("hx.gyroid.friction", 2.0, sources=("simlab:test-run",), valid_range={"reynolds": (1.0, 2000.0)},
                   fitted_on="2026-10-07"))
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


def _hand_side(uc, i, a, L, Af, V, mdot, T, p):
    """One stream of the audit hand check, from CoolProp and textbook formulas only (no aces code)."""
    phi, av = uc.fluid_fraction[i], uc.area_density_a[i] / a
    dh = 4.0 * phi / av  # hydraulic diameter from the measured porosity and wetted area
    rho, mu, k, cp = (PropsSI(q, "T", T, "P", p, "Water") for q in ("D", "V", "L", "C"))
    pr = cp * mu / k
    u = mdot / (rho * phi * Af)  # interstitial velocity
    re = rho * u * dh / mu
    assert re < 2300  # laminar
    x_star = L / (dh * re * pr)  # thermal entry length, Shah & London (1978)
    gz = 1.0 / x_star  # Graetz number Re Pr Dh / L
    nu = 3.66 + 0.0668 * gz / (1.0 + 0.04 * gz ** (2.0 / 3.0))  # Hausen (1943), Incropera ch. 8, uniform wall T
    h = nu * k / dh
    dp = 64.0 / re * (L / dh) * 0.5 * rho * u * u  # Darcy, laminar fully developed (f = 64/Re)
    return {"re": re, "x_star": x_star, "nu": nu, "h": h, "dp": dp, "A": av * V, "cp": cp, "dh": dh}


def test_end_to_end_hand_check_gyroid_water(spec_dict):
    """The audit hand-check case, recomputed independently of model.py (test inputs only).

    Gyroid a = 4 mm, t/a = 0.1, core 100 x 60 x 60 mm; water 0.2 kg/s each side, 80 C and 20 C at 2 bar; wall
    k = 15 W/m K (test input). Only the measured unit-cell geometry is shared with the model. Properties come
    from CoolProp at the mean stream temperatures, iterated here to convergence. For reference the result is
    about Re 775 / 348 (hot / cold), x* 0.028 / 0.026, Nu 5.33 / 5.44 (not 3.66), UA 236 W/K, eff. 0.220 and
    Q 11.0 kW; with the fully developed 3.66 the audit found Q 8.1 kW.
    """
    req = _req(spec_dict)
    m = TwoFluidTPMS().evaluate(BASE, req).metrics
    a, L, W, H = BASE["cell_m"], BASE["length_m"], BASE["width_m"], BASE["height_m"]
    V, Af = L * W * H, W * H
    uc = tpms.unit_cell("gyroid", 0.1, 64)
    t_wall = uc.solid_fraction / tpms.mid_surface_area("gyroid") * a  # wall conduction thickness, V_solid / A_mid
    Th_in, Tc_in, p, mdot, k_wall = 353.15, 293.15, 2e5, 0.2, 15.0
    Th_m, Tc_m = Th_in, Tc_in
    for _ in range(50):
        hs = _hand_side(uc, 0, a, L, Af, V, mdot, Th_m, p)
        cs = _hand_side(uc, 1, a, L, Af, V, mdot, Tc_m, p)
        UA = 1.0 / (1.0 / (hs["h"] * hs["A"]) + t_wall / (k_wall * 0.5 * (hs["A"] + cs["A"])) + 1.0 / (cs["h"] * cs["A"]))
        Ch, Cc = mdot * hs["cp"], mdot * cs["cp"]
        Cmin, Cmax = min(Ch, Cc), max(Ch, Cc)
        ntu, cr = UA / Cmin, Cmin / Cmax
        e = math.exp(-ntu * (1.0 - cr))
        eff = (1.0 - e) / (1.0 - cr * e)  # counterflow e-NTU, Incropera ch. 11
        Q = eff * Cmin * (Th_in - Tc_in)
        Th_m, Tc_m = Th_in - 0.5 * Q / Ch, Tc_in + 0.5 * Q / Cc
    for name, hand in (("hot", hs), ("cold", cs)):
        got = m[name]
        assert got["reynolds"] == pytest.approx(hand["re"], rel=2e-3)
        assert got["x_star"] == pytest.approx(hand["x_star"], rel=2e-3)
        assert got["graetz"] == pytest.approx(1.0 / hand["x_star"], rel=2e-3)
        assert got["nusselt_baseline"] == pytest.approx(hand["nu"], rel=2e-3)
        assert got["h_W_m2K"] == pytest.approx(hand["h"], rel=2e-3)
        assert got["pressure_drop_Pa"] == pytest.approx(hand["dp"], rel=2e-3)
        assert hand["x_star"] < 0.05 and hand["nu"] > 1.3 * 3.66  # the audit's 30-35 % under-prediction is gone
    assert m["UA_W_K"] == pytest.approx(UA, rel=2e-3)
    assert m["ntu"] == pytest.approx(ntu, rel=2e-3)
    assert m["effectiveness"] == pytest.approx(eff, rel=2e-3)
    assert m["duty_W"] == pytest.approx(Q, rel=2e-3)


def test_developing_flow_is_stated_on_the_result(spec_dict):
    ev = TwoFluidTPMS().evaluate(BASE, _req(spec_dict))
    assert any(w.startswith("hot: thermally developing") and "Hausen" in w for w in ev.warnings)
    # every result says what the entry correlation is and that calibration multiplies it
    assert any("circular-tube" in w and "fully developed CFD" in w for w in ev.warnings)
    assert ev.metrics["hot"]["nusselt_fully_developed"] == pytest.approx(3.66)


def test_long_core_has_no_developing_warning(spec_dict):
    """A slow, long core: x* above 0.05, so no developing-flow warning, and Nu close to fully developed."""
    spec_dict["requirements"]["hot"]["mass_flow_kg_s"] = 0.01
    spec_dict["requirements"]["cold"]["mass_flow_kg_s"] = 0.01
    ev = TwoFluidTPMS().evaluate({**BASE, "length_m": 0.15}, _req(spec_dict))
    hot = ev.metrics["hot"]
    assert hot["x_star"] > 0.05
    assert not any(w.startswith("hot: thermally developing") for w in ev.warnings)
    assert 3.66 < hot["nusselt_baseline"] < 1.3 * 3.66


def test_uniform_heat_flux_spec_uses_shah(spec_dict):
    spec_dict["requirements"]["thermal_boundary"] = "uniform_heat_flux"
    ev = TwoFluidTPMS().evaluate(BASE, _req(spec_dict))
    hot = ev.metrics["hot"]
    xs = hot["x_star"]
    expected = 1.953 * xs ** (-1.0 / 3.0) if xs <= 0.03 else 4.364 + 0.0722 / xs  # Shah (1975)
    assert hot["nusselt_baseline"] == pytest.approx(expected, rel=1e-9)
    assert "Shah" in hot["nusselt_correlation"]


@pytest.mark.parametrize("kind", tpms.KINDS)
@pytest.mark.parametrize("ratio", [0.06, 0.1, 0.2])
def test_reported_mean_wall_is_never_below_the_minimum(spec_dict, kind, ratio):
    """L8: mean and minimum wall use one definition (thickness along the mid-surface normal)."""
    ev = TwoFluidTPMS().evaluate({**BASE, "tpms": kind, "wall_ratio": ratio}, _req(spec_dict))
    m = ev.metrics
    mf = tpms.manufacturability(kind, ratio)
    assert m["wall_min_m"] == pytest.approx(mf.wall_min_over_a * BASE["cell_m"])
    assert m["wall_mean_m"] == pytest.approx(mf.wall_mean_over_a * BASE["cell_m"])
    assert m["wall_mean_m"] >= m["wall_min_m"]
    # the conduction thickness is a different quantity (solid volume / mid-surface area) under its own name
    uc = tpms.unit_cell(kind, ratio, 64)
    assert m["wall_conduction_m"] == pytest.approx(uc.solid_fraction / tpms.mid_surface_area(kind) * BASE["cell_m"])
