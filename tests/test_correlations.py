import math

import pytest

from aces import correlations as c


def test_laminar_friction_is_64_over_re():
    r = c.friction_factor_darcy(1000.0)
    assert r.regime == "laminar" and r.valid
    assert r.value == pytest.approx(0.064)


def test_petukhov_matches_hand_calculation():
    # (0.790 ln 1e4 - 1.64)^-2 worked by hand = 0.03148
    r = c.friction_factor_darcy(1e4)
    assert r.regime == "turbulent" and r.valid
    assert r.value == pytest.approx(0.03148, rel=1e-3)


@pytest.mark.parametrize("re", [1e4, 3e4, 1e5])
def test_gnielinski_agrees_with_dittus_boelter_for_air(re):
    pr = 0.7
    # Dittus-Boelter is an independent, cruder correlation (errors up to ~25 %, Incropera ch. 8),
    # so agreement within 15 % checks the implementation, not the physics.
    db = 0.023 * re**0.8 * pr**0.4
    g = c.nusselt(re, pr)
    assert g.valid
    assert g.value == pytest.approx(db, rel=0.15)


def test_gnielinski_matches_hand_calculation():
    # Re 1e5, Pr 0.7 worked by hand: f = 0.017992, Nu = 178.6
    assert c.nusselt(1e5, 0.7).value == pytest.approx(178.6, rel=1e-3)


def test_laminar_nusselt_constants():
    assert c.nusselt(500, 5).value == pytest.approx(4.36)
    assert c.nusselt(500, 5, "uniform_wall_temperature").value == pytest.approx(3.66)


def test_hausen_mean_nusselt_matches_hand_calculation():
    # Hausen (1943), as given in Incropera & DeWitt ch. 8 (thermal entry, uniform wall temperature):
    #   Nu_m = 3.66 + 0.0668 Gz / (1 + 0.04 Gz^(2/3)),  Gz = Re Pr Dh / L = 1 / x*
    # x* = 0.03 (Re 500, Pr 2, L/Dh 30): Gz = 33.333, Gz^(2/3) = 10.3574, 1 + 0.04 x 10.3574 = 1.41430,
    # 0.0668 x 33.333 = 2.22667, 2.22667 / 1.41430 = 1.57440, Nu_m = 5.2344
    r = c.nusselt(500.0, 2.0, "uniform_wall_temperature", l_over_dh=30.0)
    assert r.regime == "laminar" and r.valid
    assert r.x_star == pytest.approx(0.03)
    assert r.value == pytest.approx(5.2344, rel=1e-4)
    assert r.fully_developed == pytest.approx(3.66)
    assert "Hausen" in r.source and "Re < 2300" in r.validity
    # x* = 0.2: Gz = 5, Gz^(2/3) = 2.92402, 1.11696; 0.334 / 1.11696 = 0.29902, Nu_m = 3.9590
    assert c.nusselt(500.0, 2.0, "uniform_wall_temperature", l_over_dh=200.0).value == pytest.approx(3.9590, rel=1e-4)


def test_shah_mean_nusselt_matches_hand_calculation():
    # Shah (1975) / Shah & London (1978), thermal entry, uniform heat flux (H):
    #   Nu_m = 1.953 x*^(-1/3) for x* <= 0.03;  4.364 + 0.0722 / x* above
    # x* = 0.01: 0.01^(-1/3) = 4.64159, x 1.953 = 9.0650
    r = c.nusselt(500.0, 2.0, "uniform_heat_flux", l_over_dh=10.0)
    assert r.x_star == pytest.approx(0.01) and r.value == pytest.approx(9.0650, rel=1e-4)
    assert "Shah" in r.source and r.fully_developed == pytest.approx(4.36)
    # x* = 0.1: 4.364 + 0.722 = 5.086
    assert c.nusselt(500.0, 2.0, "uniform_heat_flux", l_over_dh=100.0).value == pytest.approx(5.086, rel=1e-6)


def test_developing_nusselt_tends_to_the_fully_developed_value():
    # x* = 100: Hausen 3.66 + 0.000668 / 1.0009 = 3.6607; Shah 4.364 + 0.000722 = 4.3647
    assert c.nusselt(10.0, 1.0, "uniform_wall_temperature", l_over_dh=1000.0).value == pytest.approx(3.6607, rel=1e-4)
    assert c.nusselt(10.0, 1.0, "uniform_heat_flux", l_over_dh=1000.0).value == pytest.approx(4.3647, rel=1e-4)


def test_without_a_length_the_laminar_value_is_fully_developed():
    """Calibration reduction divides periodic (fully developed) CFD by this; it must not include entry effects."""
    r = c.nusselt(500, 2.0, "uniform_heat_flux")
    assert r.value == pytest.approx(4.36) and r.x_star is None


def test_turbulent_nusselt_ignores_the_length_but_reports_x_star():
    a, b = c.nusselt(1e4, 3.0), c.nusselt(1e4, 3.0, l_over_dh=20.0)
    assert a.value == pytest.approx(b.value) and b.x_star == pytest.approx(20.0 / 3e4)


def test_every_nusselt_and_friction_branch_states_source_and_validity():
    for r in (c.nusselt(500, 2.0), c.nusselt(500, 2.0, l_over_dh=30.0), c.nusselt(2600, 2.0),
              c.nusselt(1e4, 2.0), c.friction_factor_darcy(500), c.friction_factor_darcy(1e4)):
        assert r.source and r.validity


def test_transition_is_flagged_invalid():
    r = c.nusselt(2600, 5)
    assert r.regime == "transition" and not r.valid and r.notes


def test_out_of_range_flags():
    assert not c.nusselt(1e4, 0.01).valid
    assert not c.friction_factor_darcy(1e7).valid


def test_effectiveness_limits():
    assert c.effectiveness_counterflow(2.0, 0.0) == pytest.approx(1 - math.exp(-2.0))
    assert c.effectiveness_counterflow(3.0, 1.0) == pytest.approx(0.75)
    assert c.effectiveness_counterflow(50.0, 0.5) == pytest.approx(1.0, abs=1e-9)
    assert c.effectiveness_counterflow(0.0, 0.7) == 0.0


def test_effectiveness_continuous_near_cr_one():
    a = c.effectiveness_counterflow(2.0, 1.0)
    b = c.effectiveness_counterflow(2.0, 1.0 - 1e-6)
    assert a == pytest.approx(b, rel=1e-5)


def test_bad_inputs_raise():
    with pytest.raises(ValueError):
        c.friction_factor_darcy(0)
    with pytest.raises(ValueError):
        c.effectiveness_counterflow(1.0, 1.5)
    with pytest.raises(ValueError):
        c.nusselt(500, 2.0, l_over_dh=0.0)
