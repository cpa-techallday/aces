"""Calibration CFD (periodic lattice Boltzmann) against exact solutions.  All inputs are test inputs."""
import json
import math

import numpy as np
import pytest
import yaml

from aces.cfd import lbm, periodic
from aces.cfd.simlab_adapter import Adapter
from aces.cli import main
from aces.domains.hx import calibrate as hxcal


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
    pt = hxcal.reduce(m, "uniform_heat_flux")
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


def test_cfd_collect_keeps_only_finished_citable_measurements(tmp_path):
    exp = tmp_path / "exp"
    good = {"source": "simlab:exp:c000", "tpms": "gyroid", "cell_m": 0.004, "wall_ratio": 0.1, "fluid": "Air",
            "T_K": 320.0, "p_Pa": 2e5, "mass_flux_kg_m2s": 1.0, "dpdx_Pa_m": 50.0, "h_W_m2K": 120.0}
    for name, status, meas, ev in (("c000", "FINISHED", good, True), ("c001", "FAILED", good, True),
                                   ("c002", "FINISHED", None, True), ("c003", "FINISHED", good, False)):
        d = exp / name
        d.mkdir(parents=True)
        (d / "status.json").write_text(json.dumps({"status": status}))
        (d / "results.json").write_text(json.dumps({"problems": "test: not converged"}))
        if meas:
            (d / "measurement.json").write_text(json.dumps(meas))
        if ev:
            (d / "evidence.json").write_text("{}")
    out = tmp_path / "m.json"
    assert main(["cfd-collect", str(exp), "--out", str(out)]) == 0
    rows = json.loads(out.read_text())
    assert [r["source"] for r in rows] == ["simlab:exp:c000"]
    assert hxcal.load(out)[0].h_W_m2K == 120.0  # readable by `aces calibrate`
