"""aces calibrate recovers known factors from measurements built with them (test inputs only)."""
import csv
import math
import json

import pytest

from aces import correlations as corr
from aces import fluids
from aces.calibration import Factor, Registry
from aces.cli import main
from aces.domains.hx import HXRequirements, TwoFluidTPMS, tpms
from aces.domains.hx import calibrate as hxcal
from aces import spec

F_TRUE, NU_TRUE = 2.0, 1.7
FIELDS = ["source", "tpms", "cell_m", "wall_ratio", "fluid", "T_K", "p_Pa", "mass_flux_kg_m2s", "dpdx_Pa_m", "h_W_m2K"]


def _synthetic_rows(kind="gyroid", f=F_TRUE, nu=NU_TRUE):
    rows = []
    for i, (cell, ratio, G) in enumerate([(0.003, 0.1, 50.0), (0.004, 0.12, 80.0), (0.005, 0.1, 120.0),
                                          (0.004, 0.08, 60.0)]):
        uc = tpms.unit_cell(kind, ratio)
        phi, dh = uc.fluid_fraction[0], uc.hydraulic_diameter_over_a(0) * cell
        st = fluids.state("Water", 320.0, 2e5)
        u = G / (st.rho * phi)
        re = st.rho * u * dh / st.mu
        dpdx = f * corr.friction_factor_darcy(re).value * 0.5 * st.rho * u * u / dh
        h = nu * corr.nusselt(re, st.pr, "uniform_wall_temperature").value * st.k / dh
        rows.append({"source": f"test:{kind}:{i}", "tpms": kind, "cell_m": cell, "wall_ratio": ratio, "fluid": "Water",
                     "T_K": 320.0, "p_Pa": 2e5, "mass_flux_kg_m2s": G, "dpdx_Pa_m": dpdx, "h_W_m2K": h})
    return rows


def _write_csv(path, rows):
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def test_fit_recovers_known_factors():
    meas = [hxcal.Measurement.model_validate(r) for r in _synthetic_rows()]
    factors, summary, refused = hxcal.fit(meas)
    got = {f.key: f for f in factors}
    assert not refused
    assert got["hx.gyroid.friction"].value == pytest.approx(F_TRUE, rel=1e-6)
    assert got["hx.gyroid.nusselt"].value == pytest.approx(NU_TRUE, rel=1e-6)
    assert got["hx.gyroid.tortuosity"].value == 1.0 and got["hx.gyroid.tortuosity"].calibrated
    assert set(got["hx.gyroid.friction"].sources) == {f"test:gyroid:{i}" for i in range(4)}
    lo, hi = got["hx.gyroid.friction"].valid_range["reynolds"]
    assert lo < hi


def test_scattered_ratios_are_refused():
    rows = _synthetic_rows()
    rows[0]["dpdx_Pa_m"] *= 3.0  # one point disagrees by 3x
    meas = [hxcal.Measurement.model_validate(r) for r in rows]
    factors, _, refused = hxcal.fit(meas)
    assert any("friction" in r and "scatter" in r for r in refused)
    assert not any(f.key == "hx.gyroid.friction" for f in factors)


def test_too_few_points_are_refused():
    meas = [hxcal.Measurement.model_validate(r) for r in _synthetic_rows()[:2]]
    _, _, refused = hxcal.fit(meas)
    assert any("need at least 3" in r for r in refused)


def test_measurement_needs_source(tmp_path):
    rows = _synthetic_rows()
    rows[1]["source"] = ""
    p = tmp_path / "m.csv"
    _write_csv(p, rows)
    with pytest.raises(ValueError, match="row 2"):
        hxcal.load(p)


def test_cli_calibrate_then_design_uses_factors(tmp_path, spec_dict):
    meas = tmp_path / "m.csv"
    _write_csv(meas, _synthetic_rows())
    reg = tmp_path / "cal.json"
    ledger = tmp_path / "ledger.jsonl"
    rc = main(["calibrate", str(meas), "--registry", str(reg), "--project", "cal-test", "--owner", "test",
               "--rd-activity", "calibration test", "--ledger", str(ledger)])
    assert rc == 0
    assert evidence_ok(ledger)
    spec_dict["requirements"]["calibration_file"] = str(reg)
    req = spec.parse(spec_dict, HXRequirements)[1]
    ev = TwoFluidTPMS().evaluate({"tpms": "gyroid", "cell_m": 0.004, "wall_ratio": 0.1, "length_m": 0.1,
                                  "width_m": 0.06, "height_m": 0.06}, req)
    assert not any(k.startswith("hx.gyroid.") for k in ev.uncalibrated)
    params = {"tpms": "gyroid", "cell_m": 0.004, "wall_ratio": 0.1, "length_m": 0.1, "width_m": 0.06, "height_m": 0.06}
    manual = Registry()
    for k, v in (("friction", F_TRUE), ("nusselt", NU_TRUE), ("tortuosity", 1.0)):
        manual.set(Factor(f"hx.gyroid.{k}", v, ("test",)))
    same = TwoFluidTPMS(registry=manual).evaluate(params, req)
    assert ev.metrics["hot"]["pressure_drop_Pa"] == pytest.approx(same.metrics["hot"]["pressure_drop_Pa"], rel=1e-9)
    assert ev.metrics["duty_W"] == pytest.approx(same.metrics["duty_W"], rel=1e-9)
    base = TwoFluidTPMS(registry=Registry()).evaluate(params, req)
    # Pressure drop roughly doubles; not exactly, because the higher Nusselt factor shifts the stream temperatures.
    assert ev.metrics["hot"]["pressure_drop_Pa"] / base.metrics["hot"]["pressure_drop_Pa"] == pytest.approx(F_TRUE, rel=0.05)


def evidence_ok(ledger):
    from aces import evidence
    ok, _ = evidence.Ledger(ledger).verify()
    rec = evidence.Ledger(ledger).records()[-1]
    return ok and rec["kind"] == "aces.calibration" and rec["company"] == "CPA"


def _row_at_re(re_target, kind="gyroid", cell=0.004, ratio=0.1, source="test:transition"):
    uc = tpms.unit_cell(kind, ratio)
    phi, dh = uc.fluid_fraction[0], uc.hydraulic_diameter_over_a(0) * cell
    st = fluids.state("Water", 320.0, 2e5)
    G = re_target * phi * st.mu / dh
    u = G / (st.rho * phi)
    return {"source": source, "tpms": kind, "cell_m": cell, "wall_ratio": ratio, "fluid": "Water", "T_K": 320.0,
            "p_Pa": 2e5, "mass_flux_kg_m2s": G,
            "dpdx_Pa_m": 5.0 * corr.friction_factor_darcy(2600).value * 0.5 * st.rho * u * u / dh, "h_W_m2K": None}


def test_repeated_rows_of_one_condition_count_once():
    one = _synthetic_rows()[0]
    rows = [dict(one, source=f"test:repeat:{i}") for i in range(5)]
    meas = [hxcal.Measurement.model_validate(r) for r in rows]
    factors, summary, refused = hxcal.fit(meas)
    assert not factors
    assert any("1 distinct condition(s)" in r for r in refused)
    assert all(row["conditions"] == 1 for row in summary)


def test_points_where_the_baseline_is_invalid_are_excluded():
    rows = _synthetic_rows() + [_row_at_re(2600)]  # transition blend: no published fit
    meas = [hxcal.Measurement.model_validate(r) for r in rows]
    factors, summary, refused = hxcal.fit(meas)
    fr = next(r for r in summary if r["key"] == "hx.gyroid.friction")
    assert fr["points"] == 4 and len(fr["excluded"]) == 1 and fr["excluded"][0]["regime"] == "transition"
    got = {f.key: f for f in factors}
    # the 5x outlier in the transition region did not pull the factor
    assert got["hx.gyroid.friction"].value == pytest.approx(F_TRUE, rel=1e-6)
    assert "test:transition" not in got["hx.gyroid.friction"].sources


def test_scatter_is_the_sample_standard_deviation():
    import math
    value, sd = hxcal._fit([1.0, math.e])
    assert value == pytest.approx(math.exp(0.5))
    assert sd == pytest.approx(1.0 / math.sqrt(2.0))  # n - 1, not n


def test_min_points_below_two_is_rejected():
    meas = [hxcal.Measurement.model_validate(r) for r in _synthetic_rows()]
    with pytest.raises(ValueError, match="at least 2"):
        hxcal.fit(meas, min_points=1)


def test_basis_records_resolution_and_model_warns_on_mismatch(spec_dict):
    meas = [hxcal.Measurement.model_validate(r) for r in _synthetic_rows()]
    factors, _, _ = hxcal.fit(meas)
    reg = Registry()
    for f in factors:
        assert "level-set resolution 64" in f.basis
        reg.set(f)
    req = spec.parse(spec_dict, HXRequirements)[1]
    params = {"tpms": "gyroid", "cell_m": 0.004, "wall_ratio": 0.1, "length_m": 0.1, "width_m": 0.06, "height_m": 0.06}
    same = TwoFluidTPMS(resolution=64, registry=reg).evaluate(params, req)
    assert not any("level-set resolution" in w for w in same.warnings)
    other = TwoFluidTPMS(resolution=48, registry=reg).evaluate(params, req)
    assert any("level-set resolution" in w for w in other.warnings)


def test_repeats_cannot_shrink_the_scatter():
    rows = _synthetic_rows()[:3]
    rows[2]["dpdx_Pa_m"] *= math.exp(0.5)  # log-ratios 0, 0, 0.5: sd 0.29, refused at 0.15
    base = [hxcal.Measurement.model_validate(r) for r in rows]
    _, _, refused = hxcal.fit(base)
    assert any("friction" in r and "scatter" in r for r in refused)
    padded = base + [hxcal.Measurement.model_validate(dict(rows[0], source=f"test:copy:{i}")) for i in range(20)]
    _, summary, refused2 = hxcal.fit(padded)
    assert any("friction" in r and "scatter" in r for r in refused2)
    fr = next(r for r in summary if r["key"] == "hx.gyroid.friction")
    assert fr["conditions"] == 3 and fr["scatter_sd_log"] == pytest.approx(0.5 / math.sqrt(3), rel=1e-6)


def test_nusselt_factor_records_prandtl_range_and_warns_for_another_fluid(spec_dict):
    meas = [hxcal.Measurement.model_validate(r) for r in _synthetic_rows()]
    factors, _, _ = hxcal.fit(meas)
    nu = next(f for f in factors if f.key == "hx.gyroid.nusselt")
    lo, hi = nu.valid_range["prandtl"]
    assert 2.0 < lo <= hi < 5.0  # water at 320 K
    reg = Registry()
    for f in factors:
        reg.set(f)
    spec_dict["requirements"]["hot"]["fluid"] = "Air"
    spec_dict["requirements"]["hot"]["inlet_pressure_Pa"] = 2e5
    req = spec.parse(spec_dict, HXRequirements)[1]
    params = {"tpms": "gyroid", "cell_m": 0.004, "wall_ratio": 0.1, "length_m": 0.1, "width_m": 0.06, "height_m": 0.06}
    ev = TwoFluidTPMS(registry=reg).evaluate(params, req)
    assert any("prandtl" in w for w in ev.warnings)


def test_a_trend_in_re_is_fitted_as_a_power_law(tmp_path):
    rows = []
    for i, G in enumerate((20.0, 60.0, 150.0, 400.0)):
        r = dict(_synthetic_rows()[1], source=f"test:power:{i}", mass_flux_kg_m2s=G)
        pt = hxcal.reduce(hxcal.Measurement.model_validate(r), "uniform_wall_temperature")
        # rescale dpdx so the friction ratio is exactly 2.0 * (Re / 100) ** 0.3
        r["dpdx_Pa_m"] *= 2.0 * (pt.re / 100.0) ** 0.3 / pt.f_ratio
        rows.append(r)
    factors, summary, refused = hxcal.fit([hxcal.Measurement.model_validate(r) for r in rows])
    fr = next(f for f in factors if f.key == "hx.gyroid.friction")
    assert fr.re_exponent == pytest.approx(0.3, abs=1e-6)
    assert fr.at(100.0) == pytest.approx(2.0, rel=1e-6)
    assert "power law" in fr.basis
    reg = Registry()
    for f in factors:
        reg.set(f)
    reg.save(tmp_path / "r.json")
    back = Registry.load(tmp_path / "r.json").get("hx.gyroid.friction")  # the exponent survives the JSON
    assert back.re_exponent == pytest.approx(0.3, abs=1e-9) and back.re_ref == pytest.approx(fr.re_ref)


def test_power_law_needs_a_fitted_re_range():
    with pytest.raises(ValueError, match="Re range"):
        Registry().set(Factor("hx.gyroid.friction", 2.0, ("test",), {}, "", "", 0.3, 100.0))


def test_inertial_onset_is_fitted_when_a_power_law_cannot_follow_it(tmp_path):
    """Creeping flow is flat in Re and inertia then lifts the ratio: the shape LBM samples of a gyroid show."""
    rows = []
    for i, G in enumerate((0.5, 2.0, 8.0, 30.0, 120.0, 400.0)):
        r = dict(_synthetic_rows()[1], source=f"test:onset:{i}", mass_flux_kg_m2s=G)
        pt = hxcal.reduce(hxcal.Measurement.model_validate(r), "uniform_wall_temperature")
        r["dpdx_Pa_m"] *= 3.0 * (1 + (pt.re / 40.0) ** 1.3) / pt.f_ratio
        rows.append(r)
    factors, summary, refused = hxcal.fit([hxcal.Measurement.model_validate(r) for r in rows])
    fr = next(f for f in factors if f.key == "hx.gyroid.friction")
    assert fr.kind == "onset"
    assert fr.value == pytest.approx(3.0, rel=1e-4) and fr.re_ref == pytest.approx(40.0, rel=1e-3)
    assert fr.at(100.0) == pytest.approx(3.0 * (1 + 2.5 ** 1.3), rel=1e-4)
    reg = Registry()
    for f in factors:
        reg.set(f)
    reg.save(tmp_path / "r.json")
    assert Registry.load(tmp_path / "r.json").get("hx.gyroid.friction").kind == "onset"
