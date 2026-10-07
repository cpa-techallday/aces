"""aces calibrate recovers known factors from measurements built with them (test inputs only).

The rows are built from hand-chosen factors, CoolProp properties and the textbook baselines written out here
(laminar f = 64/Re; fully developed laminar Nu 3.66 for uniform wall temperature, 4.36 for uniform heat flux;
Incropera & DeWitt ch. 8), not with the functions ``reduce`` calls. A mistake in the reduction (velocity, Re, f or
Nu definitions, or the baseline) therefore shows up as a wrong factor. Only the level-set geometry (porosity and
hydraulic diameter of the TPMS) is shared, because it is the definition both sides use.
"""
import csv
import json
import math

import pytest
from CoolProp.CoolProp import PropsSI

from aces import evidence, spec
from aces.calibration import Factor, Registry
from aces.cli import main
from aces.domains.hx import HXRequirements, TwoFluidTPMS, tpms
from aces.domains.hx import calibrate as hxcal

F_TRUE, NU_TRUE = 2.0, 1.7
UWT, UHF = "uniform_wall_temperature", "uniform_heat_flux"
NU_FD = {UWT: 3.66, UHF: 4.36}  # fully developed laminar circular duct, Incropera & DeWitt ch. 8
FIELDS = ["source", "tpms", "cell_m", "wall_ratio", "fluid", "T_K", "p_Pa", "mass_flux_kg_m2s", "dpdx_Pa_m",
          "h_W_m2K", "boundary"]
CONDITIONS = [(0.003, 0.1, 150.0), (0.004, 0.12, 400.0), (0.005, 0.1, 900.0), (0.004, 0.08, 250.0)]  # cell, t/a, Re
PARAMS = {"tpms": "gyroid", "cell_m": 0.004, "wall_ratio": 0.1, "length_m": 0.1, "width_m": 0.06, "height_m": 0.06}


def _row(re, f_ratio, nu_ratio, *, kind="gyroid", cell=0.004, ratio=0.1, boundary=UWT, source="test:row",
         T=320.0, p=2e5):
    """A measurement at Reynolds number ``re`` whose friction and Nusselt ratios are exactly f_ratio and nu_ratio."""
    uc = tpms.unit_cell(kind, ratio)
    phi, dh = uc.fluid_fraction[0], uc.hydraulic_diameter_over_a(0) * cell
    rho, mu, k = (PropsSI(q, "T", T, "P", p, "Water") for q in ("D", "V", "L"))
    u = re * mu / (rho * dh)  # interstitial velocity giving this Re on Dh
    G = rho * u * phi  # mass flux per frontal area = rho x superficial velocity
    row = {"source": source, "tpms": kind, "cell_m": cell, "wall_ratio": ratio, "fluid": "Water", "T_K": T,
           "p_Pa": p, "mass_flux_kg_m2s": G, "dpdx_Pa_m": None, "h_W_m2K": None, "boundary": boundary}
    if f_ratio is not None:
        row["dpdx_Pa_m"] = f_ratio * (64.0 / re) * 0.5 * rho * u * u / dh  # Darcy: dp/dx = f rho u^2 / (2 Dh)
    if nu_ratio is not None:
        row["h_W_m2K"] = nu_ratio * NU_FD[boundary or UWT] * k / dh  # h = Nu k / Dh
    return row


def _synthetic_rows(kind="gyroid", f=F_TRUE, nu=NU_TRUE, boundary=UWT):
    return [_row(re, f, nu, kind=kind, cell=cell, ratio=ratio, boundary=boundary, source=f"test:{kind}:{i}")
            for i, (cell, ratio, re) in enumerate(CONDITIONS)]


def _meas(rows):
    return [hxcal.Measurement.model_validate(r) for r in rows]


def _write_csv(path, rows):
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def _full(key, value, **kw):
    """A complete registry factor (test input)."""
    return Factor(key, value, kw.pop("sources", ("test",)), kw.pop("valid_range", {"reynolds": (1.0, 2000.0)}),
                  kw.pop("fitted_on", "2026-10-07"), **kw)


def test_fit_recovers_known_factors():
    factors, _, refused = hxcal.fit(_meas(_synthetic_rows()))
    got = {f.key: f for f in factors}
    assert not refused
    assert got["hx.gyroid.friction"].value == pytest.approx(F_TRUE, rel=1e-6)
    assert got["hx.gyroid.nusselt"].value == pytest.approx(NU_TRUE, rel=1e-6)
    assert got["hx.gyroid.tortuosity"].value == 1.0 and got["hx.gyroid.tortuosity"].calibrated
    assert set(got["hx.gyroid.friction"].sources) == {f"test:gyroid:{i}" for i in range(4)}
    assert got["hx.gyroid.friction"].valid_range["reynolds"] == pytest.approx((150.0, 900.0), rel=1e-6)
    assert got["hx.gyroid.friction"].valid_range["wall_ratio"] == (0.08, 0.12)


def test_reduce_reproduces_the_hand_chosen_ratios():
    pt = hxcal.reduce(hxcal.Measurement.model_validate(_row(400.0, 2.5, 1.3, boundary=UHF)), UHF)
    assert pt.re == pytest.approx(400.0, rel=1e-6)
    assert pt.f_ratio == pytest.approx(2.5, rel=1e-6) and pt.nu_ratio == pytest.approx(1.3, rel=1e-6)


def test_scattered_ratios_are_refused():
    rows = _synthetic_rows()
    rows[0]["dpdx_Pa_m"] *= 3.0  # one point disagrees by 3x
    factors, _, refused = hxcal.fit(_meas(rows))
    assert any("friction" in r and "scatter" in r for r in refused)
    assert not any(f.key == "hx.gyroid.friction" for f in factors)


def test_too_few_points_are_refused():
    _, _, refused = hxcal.fit(_meas(_synthetic_rows()[:2]))
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
    ev = TwoFluidTPMS().evaluate(PARAMS, req)
    assert not any(k.startswith("hx.gyroid.") for k in ev.uncalibrated)
    manual = Registry()
    for k, v in (("friction", F_TRUE), ("nusselt", NU_TRUE), ("tortuosity", 1.0)):
        manual.set(_full(f"hx.gyroid.{k}", v))
    same = TwoFluidTPMS(registry=manual).evaluate(PARAMS, req)
    assert ev.metrics["hot"]["pressure_drop_Pa"] == pytest.approx(same.metrics["hot"]["pressure_drop_Pa"], rel=1e-9)
    assert ev.metrics["duty_W"] == pytest.approx(same.metrics["duty_W"], rel=1e-9)
    base = TwoFluidTPMS(registry=Registry()).evaluate(PARAMS, req)
    # Pressure drop roughly doubles; not exactly, because the higher Nusselt factor shifts the stream temperatures.
    assert ev.metrics["hot"]["pressure_drop_Pa"] / base.metrics["hot"]["pressure_drop_Pa"] == pytest.approx(F_TRUE, rel=0.05)


def evidence_ok(ledger):
    ok, _ = evidence.Ledger(ledger).verify()
    rec = evidence.Ledger(ledger).records()[-1]
    return ok and rec["kind"] == "aces.calibration" and rec["company"] == "CPA"


def test_repeated_rows_of_one_condition_count_once():
    one = _synthetic_rows()[0]
    factors, summary, refused = hxcal.fit(_meas([dict(one, source=f"test:repeat:{i}") for i in range(5)]))
    assert not factors
    assert any("1 distinct condition(s)" in r for r in refused)
    assert all(row["conditions"] == 1 for row in summary)


def test_points_where_the_baseline_is_invalid_are_excluded():
    # Re 2600 sits in the 2300-3000 blend, where no published fit exists; give it a 5x friction outlier
    rows = _synthetic_rows() + [_row(2600.0, 5.0 * F_TRUE, None, source="test:transition")]
    factors, summary, _ = hxcal.fit(_meas(rows))
    fr = next(r for r in summary if r["key"] == "hx.gyroid.friction")
    assert fr["points"] == 4 and len(fr["excluded"]) == 1 and fr["excluded"][0]["regime"] == "transition"
    got = {f.key: f for f in factors}
    # the outlier in the transition region did not pull the factor
    assert got["hx.gyroid.friction"].value == pytest.approx(F_TRUE, rel=1e-6)
    assert "test:transition" not in got["hx.gyroid.friction"].sources


def test_scatter_is_the_sample_standard_deviation():
    value, sd = hxcal._fit([1.0, math.e])
    assert value == pytest.approx(math.exp(0.5))
    assert sd == pytest.approx(1.0 / math.sqrt(2.0))  # n - 1, not n


def test_min_points_below_two_is_rejected():
    with pytest.raises(ValueError, match="at least 2"):
        hxcal.fit(_meas(_synthetic_rows()), min_points=1)


def test_basis_records_resolution_and_model_warns_on_mismatch(spec_dict):
    factors, _, _ = hxcal.fit(_meas(_synthetic_rows()))
    reg = Registry()
    for f in factors:
        assert "level-set resolution 64" in f.basis
        reg.set(f)
    req = spec.parse(spec_dict, HXRequirements)[1]
    same = TwoFluidTPMS(resolution=64, registry=reg).evaluate(PARAMS, req)
    assert not any("level-set resolution" in w for w in same.warnings)
    other = TwoFluidTPMS(resolution=48, registry=reg).evaluate(PARAMS, req)
    assert any("level-set resolution" in w for w in other.warnings)


def test_repeats_cannot_shrink_the_scatter():
    rows = _synthetic_rows()[:3]
    rows[2]["dpdx_Pa_m"] *= math.exp(0.5)  # log-ratios 0, 0, 0.5: sd 0.29, refused at 0.15
    base = _meas(rows)
    _, _, refused = hxcal.fit(base)
    assert any("friction" in r and "scatter" in r for r in refused)
    padded = base + _meas([dict(rows[0], source=f"test:copy:{i}") for i in range(20)])
    _, summary, refused2 = hxcal.fit(padded)
    assert any("friction" in r and "scatter" in r for r in refused2)
    fr = next(r for r in summary if r["key"] == "hx.gyroid.friction")
    assert fr["conditions"] == 3 and fr["scatter_sd_log"] == pytest.approx(0.5 / math.sqrt(3), rel=1e-6)


def test_nusselt_factor_records_prandtl_range_and_warns_for_another_fluid(spec_dict):
    factors, _, _ = hxcal.fit(_meas(_synthetic_rows()))
    nu = next(f for f in factors if f.key == "hx.gyroid.nusselt")
    lo, hi = nu.valid_range["prandtl"]
    assert 2.0 < lo <= hi < 5.0  # water at 320 K
    reg = Registry()
    for f in factors:
        reg.set(f)
    spec_dict["requirements"]["hot"]["fluid"] = "Air"
    spec_dict["requirements"]["hot"]["inlet_pressure_Pa"] = 2e5
    req = spec.parse(spec_dict, HXRequirements)[1]
    ev = TwoFluidTPMS(registry=reg).evaluate(PARAMS, req)
    assert any("prandtl" in w for w in ev.warnings)


def test_a_trend_in_re_is_fitted_as_a_power_law(tmp_path):
    # hand-chosen friction ratio 2.0 x (Re / 100)^0.3 at four Re levels
    rows = [_row(re, 2.0 * (re / 100.0) ** 0.3, None, ratio=0.12, source=f"test:power:{i}")
            for i, re in enumerate((40.0, 120.0, 300.0, 800.0))]
    factors, _, _ = hxcal.fit(_meas(rows))
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
        Registry().set(Factor("hx.gyroid.friction", 2.0, ("test",), {"wall_ratio": (0.1, 0.12)}, "2026-10-07", "",
                              0.3, 100.0))


def test_inertial_onset_is_fitted_when_a_power_law_cannot_follow_it(tmp_path):
    """Creeping flow is flat in Re and inertia then lifts the ratio: the shape LBM samples of a gyroid show."""
    # hand-chosen friction ratio 3.0 x (1 + (Re / 40)^1.3) at six Re levels
    rows = [_row(re, 3.0 * (1 + (re / 40.0) ** 1.3), None, ratio=0.12, source=f"test:onset:{i}")
            for i, re in enumerate((1.0, 4.0, 15.0, 50.0, 200.0, 700.0))]
    factors, _, _ = hxcal.fit(_meas(rows))
    fr = next(f for f in factors if f.key == "hx.gyroid.friction")
    assert fr.kind == "onset"
    assert fr.value == pytest.approx(3.0, rel=1e-4) and fr.re_ref == pytest.approx(40.0, rel=1e-3)
    assert fr.at(100.0) == pytest.approx(3.0 * (1 + 2.5 ** 1.3), rel=1e-4)
    reg = Registry()
    for f in factors:
        reg.set(f)
    reg.save(tmp_path / "r.json")
    assert Registry.load(tmp_path / "r.json").get("hx.gyroid.friction").kind == "onset"


def test_two_re_levels_cannot_carry_a_re_dependent_fit():
    """Achieved Re scatters around each target; two levels must not pass for many."""
    re = [10.0, 10.2, 9.9, 100.0, 103.0, 98.0]
    assert hxcal._re_levels(re) == 2
    assert hxcal._fit_power(re, [2.0, 2.1, 1.9, 3.0, 3.1, 2.9]) is None
    assert hxcal._fit_onset(re, [2.0, 2.1, 1.9, 3.0, 3.1, 2.9]) is None
    assert hxcal._re_levels([1, 5, 20, 60]) == 4


# ----------------------------------------------------------------------------- thermal boundary (audit H3)

def test_h1_rows_are_fitted_against_the_h1_baseline():
    """CFD rows are H1. Without --boundary they must be divided by 4.36 and labelled so, not by 3.66 as UWT."""
    factors, summary, refused = hxcal.fit(_meas(_synthetic_rows(boundary=UHF)))
    nu = next(f for f in factors if f.key == "hx.gyroid.nusselt")
    assert not refused
    assert nu.value == pytest.approx(NU_TRUE, rel=1e-6)  # the old default gave 1.7 x 4.36 / 3.66 = 2.03
    assert "Nu baseline uniform_heat_flux" in nu.basis and UWT not in nu.basis
    assert next(r for r in summary if r["key"] == "hx.gyroid.nusselt")["boundary"] == UHF


def test_mixed_boundaries_are_refused():
    rows = _synthetic_rows(boundary=UHF)
    rows[0] = _row(150.0, F_TRUE, NU_TRUE, cell=0.003, boundary=UWT, source="test:uwt")
    with pytest.raises(ValueError, match="mixed"):
        hxcal.fit(_meas(rows))


def test_rows_that_disagree_with_an_explicit_boundary_are_refused():
    with pytest.raises(ValueError, match="disagree"):
        hxcal.fit(_meas(_synthetic_rows(boundary=UHF)), boundary=UWT)


def test_rows_without_a_boundary_fit_friction_only_unless_one_is_given():
    rows = _synthetic_rows(boundary=None)
    factors, _, refused = hxcal.fit(_meas(rows))
    keys = {f.key for f in factors}
    assert "hx.gyroid.friction" in keys and "hx.gyroid.nusselt" not in keys
    assert any("hx.gyroid.nusselt" in r and "--boundary" in r for r in refused)
    factors, _, refused = hxcal.fit(_meas(rows), boundary=UWT)
    assert not refused
    assert next(f for f in factors if f.key == "hx.gyroid.nusselt").value == pytest.approx(NU_TRUE, rel=1e-6)


def test_partly_labelled_rows_need_an_explicit_boundary():
    rows = _synthetic_rows(boundary=UHF)
    rows[0]["boundary"] = None
    factors, _, refused = hxcal.fit(_meas(rows))
    assert not any(f.key == "hx.gyroid.nusselt" for f in factors) and any("--boundary" in r for r in refused)
    factors, _, refused = hxcal.fit(_meas(rows), boundary=UHF)
    assert not refused and any(f.key == "hx.gyroid.nusselt" for f in factors)


def test_friction_only_rows_need_no_boundary():
    rows = [dict(r, h_W_m2K=None, boundary=None) for r in _synthetic_rows()]
    factors, _, refused = hxcal.fit(_meas(rows), boundary=UWT)  # an explicit boundary is harmless here
    assert not refused and {f.key for f in factors} == {"hx.gyroid.friction", "hx.gyroid.tortuosity"}


def test_unknown_boundary_in_a_row_is_invalid():
    with pytest.raises(ValueError):
        hxcal.Measurement.model_validate(dict(_synthetic_rows()[0], boundary="H2"))


def _calibrate(meas, reg, ledger, *extra):
    return main(["calibrate", str(meas), "--registry", str(reg), "--project", "cal-test", "--owner", "test",
                 "--rd-activity", "calibration test", "--ledger", str(ledger), *extra])


def test_cli_takes_the_boundary_from_the_rows(tmp_path):
    meas = tmp_path / "m.json"
    meas.write_text(json.dumps(_synthetic_rows(boundary=UHF)))
    reg, ledger = tmp_path / "cal.json", tmp_path / "l.jsonl"
    assert _calibrate(meas, reg, ledger) == 0  # no --boundary: the rows say H1
    nu = Registry.load(reg).get("hx.gyroid.nusselt")
    assert nu.value == pytest.approx(NU_TRUE, rel=1e-6) and "uniform_heat_flux" in nu.basis
    assert evidence.Ledger(ledger).records()[-1]["inputs"]["boundary"] == UHF


def test_cli_refuses_rows_that_disagree_with_boundary(tmp_path, capsys):
    meas = tmp_path / "m.json"
    meas.write_text(json.dumps(_synthetic_rows(boundary=UHF)))
    reg, ledger = tmp_path / "cal.json", tmp_path / "l.jsonl"
    assert _calibrate(meas, reg, ledger, "--boundary", UWT) == 2
    assert "disagree" in capsys.readouterr().out
    assert not reg.exists() and not ledger.exists()


# ----------------------------------------------------------------------------- registry (audit M4)

@pytest.mark.parametrize("entry, problem", [
    ({"sources": ["simlab:x"]}, "value"),
    ({"value": 1.5, "sources": ["simlab:x"], "fitted_on": "2026-10-07"}, "valid_range"),
    ({"value": 1.5, "sources": ["simlab:x"], "valid_range": {}, "fitted_on": "2026-10-07"}, "valid_range"),
    ({"value": 1.5, "sources": ["simlab:x"], "valid_range": {"reynolds": [10, 100]}}, "fitted_on"),
    ({"value": 1.5, "sources": ["simlab:x"], "valid_range": {"reynolds": [100, 10]}, "fitted_on": "2026-10-07"},
     "valid_range"),
    ({"value": 1.5, "sources": ["simlab:x"], "valid_range": {"reynolds": [10, 100]}, "fitted_on": "last week"},
     "fitted_on"),
])
def test_registry_refuses_an_incomplete_calibrated_factor(tmp_path, entry, problem):
    """An entry with only sources used to load as a calibrated 1.0 with no range, and could never warn."""
    p = tmp_path / "cal.json"
    p.write_text(json.dumps({"hx.gyroid.nusselt": entry}))
    with pytest.raises(ValueError, match=problem):
        Registry.load(p)


def test_registry_set_refuses_a_factor_without_range_or_date():
    with pytest.raises(ValueError, match="valid_range"):
        Registry().set(Factor("hx.gyroid.friction", 2.0, ("simlab:x",), {}, "2026-10-07"))
    with pytest.raises(ValueError, match="fitted_on"):
        Registry().set(Factor("hx.gyroid.friction", 2.0, ("simlab:x",), {"reynolds": (1.0, 10.0)}, ""))


def test_complete_factor_round_trips(tmp_path):
    reg = Registry({"hx.gyroid.nusselt": _full("hx.gyroid.nusselt", 1.5)})
    p = reg.save(tmp_path / "cal.json")
    back = Registry.load(p).get("hx.gyroid.nusselt")
    assert back.calibrated and back.value == 1.5 and back.valid_range == {"reynolds": (1.0, 2000.0)}
    reg.save(p)  # saving again replaces the file


def test_registry_save_does_not_follow_a_planted_link(tmp_path):
    """Saving goes through aces.safeio: a link at the registry path is replaced, never written through."""
    target = tmp_path / "victim.txt"
    target.write_text("keep")
    link = tmp_path / "cal.json"
    link.symlink_to(target)
    Registry({"hx.gyroid.nusselt": _full("hx.gyroid.nusselt", 1.5)}).save(link)
    assert target.read_text() == "keep"
    assert not link.is_symlink() and Registry.load(link).get("hx.gyroid.nusselt").value == 1.5
