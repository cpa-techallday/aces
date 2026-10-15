"""Calibration CFD (periodic lattice Boltzmann) against exact solutions.  All inputs are test inputs."""
import hashlib
import json
import math

import numpy as np
import pytest
import yaml
from CoolProp.CoolProp import PropsSI

from aces.cfd import lbm, periodic
from aces.cfd.simlab_adapter import Adapter
from aces.cli import main
from aces.domains.hx import calibrate as hxcal
from aces.domains.hx import tpms


def _channel(h, nx=2):
    fl = np.ones((nx, 2, h + 2), bool)
    fl[:, :, 0] = fl[:, :, -1] = False
    return fl


def test_plane_channel_poiseuille_is_exact_up_to_the_node_average():
    # half-way bounce-back with TRT 3/16 puts the walls exactly mid-link; the node-sum flux of a parabola
    # exceeds the integral by h/12, so the measured fRe is 96 / (1 + 1/(2 h^2)) exactly
    for h in (6, 10):
        r, _ = lbm.solve_flow(_channel(h), 0.15, 1e-6, check_every=100)
        u, dh = r.u_interstitial, 2.0 * h
        f_re = (1e-6 * dh / (0.5 * u * u)) * (u * dh / 0.15)
        assert f_re == pytest.approx(96.0 / (1.0 + 1.0 / (2 * h * h)), rel=2e-4)
        assert r.converged


def test_permeability_does_not_depend_on_viscosity():
    us = []
    for nu in (0.03, 0.4):
        r, _ = lbm.solve_flow(_channel(8), nu, 1e-6, check_every=100)
        us.append(r.u_interstitial * nu)
    assert us[0] == pytest.approx(us[1], rel=1e-6)


def test_square_duct_friction_and_h1_nusselt():
    n = 12
    fl = np.zeros((2, n + 2, n + 2), bool)
    fl[:, 1:-1, 1:-1] = True
    r, _ = lbm.solve_flow(fl, 0.2, 1e-6, check_every=100)
    u = r.u_interstitial
    f_re = (1e-6 * n / (0.5 * u * u)) * (u * n / 0.2)
    assert f_re == pytest.approx(56.91, rel=0.01)  # Shah & London
    sc = lbm.solve_scalar(fl, r.u, alpha=0.2 / 0.7, check_every=100)
    assert lbm.nusselt_h1(fl, r, sc, 4.0 * n * 2, n) == pytest.approx(3.608, rel=0.025)


def test_plane_channel_h1_nusselt_converges_to_8_235():
    errs = []
    for h in (8, 16):
        fl = _channel(h)
        r, _ = lbm.solve_flow(fl, 0.3, 1e-6, check_every=100)
        sc = lbm.solve_scalar(fl, r.u, alpha=0.3 / 0.7, check_every=100)
        errs.append(abs(lbm.nusselt_h1(fl, r, sc, 2.0 * 2 * 2, 2.0 * h) / 8.235 - 1))
    assert errs[1] < 0.004 and errs[1] < errs[0] / 3  # second order


def test_advected_shear_wave_moves_with_the_flow():
    """u_y = A sin(k(x - U t)) exp(-nu k^2 t) is an exact Navier-Stokes solution: checks the inertial terms."""
    nx, U, A, nu, steps = 64, 0.05, 1e-3, 0.02, 400
    k = 2 * math.pi / nx
    x = np.arange(nx)
    fl = np.ones((nx, 1, 1), bool)
    ux = np.full((nx, 1, 1), U)
    uy = (A * np.sin(k * x)).reshape(nx, 1, 1)
    c = lbm.C19.astype(float)
    cu = c[:, 0].reshape(19, 1, 1, 1) * ux + c[:, 1].reshape(19, 1, 1, 1) * uy
    f0 = lbm.W19.reshape(19, 1, 1, 1) * (1 + 3 * cu + 4.5 * cu * cu - 1.5 * (ux * ux + uy * uy))
    r, _ = lbm.solve_flow(fl, nu, 0.0, f0=f0, max_steps=steps, check_every=10 ** 9)
    exact = A * np.sin(k * (x - U * steps)) * math.exp(-nu * k * k * steps)
    assert np.max(np.abs(np.asarray(r.u[1]).ravel() - exact)) < 0.01 * A
    assert np.asarray(r.u[0]).ravel() == pytest.approx(U, rel=1e-6)


def test_scalar_solve_refuses_an_unresolved_thermal_layer():
    fl = _channel(6)
    r, _ = lbm.solve_flow(fl, 0.1, 1e-4, check_every=100)
    with pytest.raises(ValueError, match="grid Peclet"):
        lbm.solve_scalar(fl, r.u, alpha=1e-7)


def test_gyroid_sample_is_a_valid_measurement_for_calibrate():
    s = periodic.Sample("gyroid", 0.15, 0.004, "Air", 320.0, 2e5, re_target=5.0, n=16, source="test:gyroid-n16")
    r = periodic.run(s)
    assert r.problems == [] and r.measurement is not None, r.problems
    m = hxcal.Measurement.model_validate(r.measurement)
    assert m.boundary == "uniform_heat_flux"  # the solver's H1 condition travels with the row (audit H3)
    pt = hxcal.reduce(m, m.boundary)
    assert pt.re == pytest.approx(5.0, rel=0.03)  # the solver hit the target on ACES's own definition
    assert 1.5 < pt.f_ratio < 8.0  # tortuous core: well above the straight duct, test-resolution sanity only
    assert r.diagnostics["mach"] < periodic.MACH_LIMIT
    assert abs(r.diagnostics["phi_voxel"] - r.diagnostics["phi_levelset"]) / r.diagnostics["phi_levelset"] < 0.05


def test_sample_refuses_walls_thinner_than_two_voxels():
    r = periodic.run(periodic.Sample("gyroid", 0.05, 0.004, "Air", 320.0, 2e5, re_target=5.0, n=16))
    assert r.measurement is None and any("voxels" in p for p in r.problems)


def test_simlab_adapter_validation_case(tmp_path):
    class Spec:
        id = "test-validation"

        class resources:
            precision = "float64"

    out = Adapter().run({"validate": "square_duct", "n": 10, "cpu": True}, Spec, str(tmp_path))
    assert abs(out["fRe_err_pct"]) < 1.5 and abs(out["Nu_err_pct"]) < 3.0 and out["converged"] == 1


def test_simlab_adapter_refuses_float32(tmp_path):
    class Spec:
        id = "x"

        class resources:
            precision = "float32"

    with pytest.raises(ValueError, match="float64"):
        Adapter().run({"validate": "square_duct", "n": 6, "cpu": True}, Spec, str(tmp_path))


def test_cfd_campaign_writes_a_simlab_sweep(tmp_path):
    out = tmp_path / "exp.yaml"
    assert main(["cfd-campaign", "--id", "aces-cal-t", "--tpms", "gyroid", "diamond", "--wall-ratio", "0.1",
                 "--re", "10", "100", "--n", "64", "--out", str(out)]) == 0
    exp = yaml.safe_load(out.read_text())
    assert exp["solver"] == "aces-tpms-periodic" and exp["company"] == "carbon"
    assert exp["resources"]["precision"] == "float64" and 0 < exp["resources"]["vram_gb"] <= 60
    assert exp["sweep"] == {"tpms": ["gyroid", "diamond"], "wall_ratio": [0.1], "re": [10.0, 100.0]}
    assert main(["cfd-campaign", "--id", "aces-cal-t", "--out", str(out)]) == 2  # never overwrites


# Test inputs. The per-run evidence.json layout (the experiment spec under "spec", with ACES's own "id", "solver",
# "params" and "sweep" fields) is what cfd-collect checks; the numbers are not results.
GOOD_ROW = {"source": "simlab:exp:c000", "tpms": "gyroid", "cell_m": 0.004, "wall_ratio": 0.1, "fluid": "Air",
            "T_K": 320.0, "p_Pa": 2e5, "mass_flux_kg_m2s": 1.0, "dpdx_Pa_m": 50.0, "h_W_m2K": 120.0,
            "boundary": "uniform_heat_flux"}


def _evidence(exp_id, **spec_over):
    spec = {"id": exp_id, "solver": "aces-tpms-periodic",
            "params": {"cell_m": 0.004, "fluid": "Air", "T_K": 320.0, "p_Pa": 2e5, "n": 96, "thermal": True},
            "sweep": {"tpms": ["gyroid", "diamond"], "wall_ratio": [0.1, 0.12], "re": [10.0, 100.0]}}
    spec.update(spec_over)
    return {"spec": spec, "image_digest": "sha256:test-input"}


def _case(exp, name, *, status="FINISHED", row=GOOD_ROW, evidence="default"):
    d = exp / name
    d.mkdir(parents=True)
    (d / "status.json").write_text(json.dumps({"status": status}))
    (d / "results.json").write_text(json.dumps({"problems": "test: not converged"}))
    if row is not None:
        (d / "measurement.json").write_text(json.dumps(row))
    if evidence == "default":
        evidence = json.dumps(_evidence(exp.name))
    if evidence is not None:
        (d / "evidence.json").write_text(evidence)
    return d


def test_cfd_collect_keeps_only_finished_citable_measurements(tmp_path):
    exp = tmp_path / "exp"
    _case(exp, "c000")
    _case(exp, "c001", status="FAILED")
    _case(exp, "c002", row=None)
    _case(exp, "c003", evidence=None)
    out = tmp_path / "m.json"
    assert main(["cfd-collect", str(exp), "--out", str(out)]) == 0
    rows = json.loads(out.read_text())
    assert [r["source"] for r in rows] == ["simlab:exp:c000"]
    assert hxcal.load(out)[0].h_W_m2K == 120.0  # readable by `aces calibrate`


@pytest.mark.parametrize("evidence, reason", [
    ("{}", "experiment"),  # exists, but says nothing about the run
    ("[1, 2]", "JSON object"),
    ("not json", "JSON"),
    (json.dumps(_evidence("another-exp")), "another-exp"),
    (json.dumps(_evidence("exp", solver="pyfr")), "solver"),
    (json.dumps(_evidence("exp", params={"cell_m": 0.005, "fluid": "Air", "T_K": 320.0, "p_Pa": 2e5})), "cell_m"),
    (json.dumps(_evidence("exp", sweep={"tpms": ["diamond"], "wall_ratio": [0.1]})), "tpms"),
    (json.dumps({**_evidence("exp"), "case_id": "c007"}), "c007"),
])
def test_cfd_collect_checks_what_the_evidence_says(tmp_path, capsys, evidence, reason):
    """L3: an evidence.json that merely exists, or belongs to another run, does not make a case citable."""
    exp = tmp_path / "exp"
    _case(exp, "c000", evidence=evidence)
    out = tmp_path / "m.json"
    assert main(["cfd-collect", str(exp), "--out", str(out)]) == 1
    assert json.loads(out.read_text()) == []
    printed = capsys.readouterr().out
    assert "SKIPPED c000" in printed and reason in printed


def test_cfd_collect_records_the_evidence_hash_and_the_h1_boundary(tmp_path):
    exp = tmp_path / "exp"
    old_row = {k: v for k, v in GOOD_ROW.items() if k != "boundary"}  # a row written before rows carried it
    d = _case(exp, "c000", row=old_row)
    _case(exp, "c001", row=dict(GOOD_ROW, boundary="uniform_wall_temperature"))  # this solver computes H1 only
    out = tmp_path / "m.json"
    assert main(["cfd-collect", str(exp), "--out", str(out)]) == 0
    rows = json.loads(out.read_text())
    assert [r["source"] for r in rows] == ["simlab:exp:c000"]
    assert rows[0]["evidence_sha256"] == hashlib.sha256((d / "evidence.json").read_bytes()).hexdigest()
    assert rows[0]["boundary"] == "uniform_heat_flux"
    assert hxcal.load(out)[0].evidence_sha256 == rows[0]["evidence_sha256"]


def _evidence_simlab(exp_id, case_id, **case_over):
    """The schema the rig's simlab worker actually writes (found 2026-10-09)."""
    case = {"case_id": case_id, "tpms": "gyroid", "wall_ratio": 0.1, "cell_m": 0.004,
            "fluid": "Air", "T_K": 320.0, "p_Pa": 2e5, "n": 96, "thermal": True, "re": 10}
    case.update(case_over)
    return {"schema": "simlab-evidence/1",
            "experiment": {"id": exp_id, "solver": "aces-tpms-periodic",
                           "spec_sha256": "0" * 64},
            "case": case, "status": "FINISHED"}


def test_cfd_collect_accepts_simlab_run_schema(tmp_path):
    exp = tmp_path / "exp"
    d = _case(exp, "c000", evidence=json.dumps(_evidence_simlab("exp", "c000")))
    out = tmp_path / "m.json"
    assert main(["cfd-collect", str(exp), "--out", str(out)]) == 0
    rows = json.loads(out.read_text())
    assert [r["source"] for r in rows] == ["simlab:exp:c000"]
    assert rows[0]["evidence_sha256"] == hashlib.sha256((d / "evidence.json").read_bytes()).hexdigest()


@pytest.mark.parametrize("evidence, reason", [
    (json.dumps(_evidence_simlab("another-exp", "c000")), "another-exp"),
    (json.dumps(_evidence_simlab("exp", "c007")), "c007"),
    (json.dumps(_evidence_simlab("exp", "c000", wall_ratio=0.5)), "wall_ratio"),
])
def test_cfd_collect_simlab_schema_is_still_checked(tmp_path, capsys, evidence, reason):
    """The run schema is accepted, but its experiment, case id and values are still verified."""
    exp = tmp_path / "exp"
    _case(exp, "c000", evidence=evidence)
    out = tmp_path / "m.json"
    assert main(["cfd-collect", str(exp), "--out", str(out)]) == 1
    printed = capsys.readouterr().out
    assert "SKIPPED c000" in printed and reason in printed


def test_a_settled_flux_with_a_moving_field_is_not_steady():
    """A shear wave decays slowly while the mean flux stays constant: not a steady state."""
    nx, U, A, nu = 64, 0.05, 1e-2, 0.002
    k = 2 * math.pi / nx
    x = np.arange(nx)
    ux = np.full((nx, 1, 1), U)
    uy = (A * np.sin(k * x)).reshape(nx, 1, 1)
    c = lbm.C19.astype(float)
    cu = c[:, 0].reshape(19, 1, 1, 1) * ux + c[:, 1].reshape(19, 1, 1, 1) * uy
    f0 = lbm.W19.reshape(19, 1, 1, 1) * (1 + 3 * cu + 4.5 * cu * cu - 1.5 * (ux * ux + uy * uy))
    r, _ = lbm.solve_flow(np.ones((nx, 1, 1), bool), nu, 0.0, f0=f0, max_steps=1000, check_every=100)
    assert not r.converged


@pytest.mark.filterwarnings("ignore::RuntimeWarning")  # the stub drives the force far off on purpose
def test_secant_reports_the_force_it_solved(monkeypatch):
    """If the target Re is never reached, dpdx must come from the last field solved, not an untried force."""
    used = []
    real = lbm.solve_flow

    def fake(fl, nu, g, **kw):
        used.append(g)
        r, f = real(fl, nu, g, **{**kw, "max_steps": 300})
        r.converged = True
        r.u_superficial = 1e-4  # Re stuck regardless of the force
        return r, f

    monkeypatch.setattr(lbm, "solve_flow", fake)
    r = periodic.run(periodic.Sample("gyroid", 0.15, 0.004, "Air", 320.0, 2e5, re_target=5.0, n=16, thermal=False))
    assert r.diagnostics["force_lattice"] == used[-1]


def test_simlab_adapter_never_falls_back_to_the_cpu_silently(tmp_path, monkeypatch):
    class Spec:
        id = "x"

        class resources:
            precision = "float64"

    monkeypatch.setattr(lbm, "backend", lambda prefer_gpu=False: np)
    case = {"tpms": "gyroid", "wall_ratio": 0.15, "cell_m": 0.004, "fluid": "Air", "T_K": 320.0, "p_Pa": 2e5,
            "re": 5.0, "n": 16}
    with pytest.raises(RuntimeError, match="CUDA"):
        Adapter().run(case, Spec, str(tmp_path))


def test_cfd_collect_takes_provenance_from_the_run_folder(tmp_path):
    exp = tmp_path / "aces-cal-x"
    row = dict(GOOD_ROW, source="simlab:other:c999", h_W_m2K=None)
    _case(exp, "c000", row=row)
    _case(exp, "c001", row=dict(row, wall_ratio=1.5),  # not a valid row, though its evidence matches
          evidence=json.dumps(_evidence(exp.name, sweep={"tpms": ["gyroid"], "wall_ratio": [1.5]})))
    out = tmp_path / "m.json"
    assert main(["cfd-collect", str(exp), "--out", str(out)]) == 0
    assert [r["source"] for r in json.loads(out.read_text())] == ["simlab:aces-cal-x:c000"]


def _channel_geometry(n, gap):
    """A plane channel in place of the TPMS (test hook): fluid layer ``gap`` voxels high in an n^3 periodic cell."""
    g = gap / n

    def geometry(s):
        fl = np.zeros((n, n, n), bool)
        fl[:, :, (n - gap) // 2:(n + gap) // 2] = True
        return tpms.UnitCell("plane_channel", s.wall_ratio, n, 1.0 - g, (g, 0.0), (2.0, 0.0)), fl

    return geometry


def test_plane_channel_through_periodic_run_is_exact(monkeypatch):
    """L6: periodic.run's unit path (level-set porosity, Dh and wall area -> Re, dpdx and h in SI units) on a plane
    channel, whose exact fully developed values are f Re = 96 and Nu_H1 = 8.235 (Shah & London 1978).

    The channel replaces the TPMS through the private ``_geometry`` hook: a 16-voxel periodic cell with a fluid
    layer 10 voxels high. Per unit cell (a = 1): fluid fraction 10/16, two walls of area 1, so area density 2 and
    Dh/a = 4 x (10/16) / 2 = 1.25 = twice the gap.
    """
    n, gap = 16, 10
    g = gap / n
    monkeypatch.setattr(periodic, "_geometry", _channel_geometry(n, gap))
    a, T, p = 0.004, 320.0, 2e5
    r = periodic.run(periodic.Sample("plane_channel", 1.0 - g, a, "Air", T, p, re_target=2.0, n=n,
                                     source="test:plane-channel"))
    assert r.problems == [] and r.measurement is not None, r.problems
    m = r.measurement
    # Independent reduction of the SI row: CoolProp properties, Dh = 2 x gap, interstitial u = G / (rho phi)
    rho, mu, k = (PropsSI(q, "T", T, "P", p, "Air") for q in ("D", "V", "L"))
    dh = 2.0 * g * a
    u = m["mass_flux_kg_m2s"] / (rho * g)
    re = rho * u * dh / mu
    f = m["dpdx_Pa_m"] * dh / (0.5 * rho * u * u)
    assert re == pytest.approx(2.0, rel=0.03)
    # half-way bounce-back: the node-sum flux of the parabola gives 96 / (1 + 1/(2 gap^2)) = 95.522 exactly
    assert f * re == pytest.approx(96.0 / (1.0 + 1.0 / (2 * gap * gap)), rel=1e-3)
    assert f * re == pytest.approx(96.0, rel=0.006)
    assert m["h_W_m2K"] * dh / k == pytest.approx(8.235, rel=0.01)
    assert m["boundary"] == "uniform_heat_flux"


def test_simlab_adapter_writes_the_boundary_with_the_row(tmp_path, monkeypatch):
    monkeypatch.setattr(periodic, "_geometry", _channel_geometry(12, 8))  # cheap geometry; the row is what matters

    class Spec:
        id = "test-adapter"

        class resources:
            precision = "float64"

    case = {"tpms": "gyroid", "wall_ratio": 1.0 / 3.0, "cell_m": 0.004, "fluid": "Air", "T_K": 320.0, "p_Pa": 2e5,
            "re": 2.0, "n": 12, "thermal": False, "cpu": True, "case_id": "c000"}
    out = Adapter().run(case, Spec, str(tmp_path))
    assert out["converged"] == 1 and out["boundary"] == "uniform_heat_flux"
    row = json.loads((tmp_path / "measurement.json").read_text())
    assert row["boundary"] == "uniform_heat_flux" and row["source"] == "simlab:test-adapter:c000"
    hxcal.Measurement.model_validate(row)
