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
