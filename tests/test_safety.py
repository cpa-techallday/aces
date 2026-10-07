"""Guards against non-conservative numbers and untraceable evidence (review of 2026-10-06)."""
import json

import numpy as np
import pytest
import yaml

from aces import evidence, fluids, safeio, search, spec
from aces.calibration import Factor, Registry
from aces.cli import main
from aces.domains.hx import HXRequirements, TwoFluidTPMS, tpms

BASE = {"tpms": "gyroid", "cell_m": 0.004, "wall_ratio": 0.1, "length_m": 0.1, "width_m": 0.06, "height_m": 0.06}


def _req(spec_dict):
    return spec.parse(spec_dict, HXRequirements)[1]


# ----------------------------------------------------------------------------- geometry

@pytest.mark.parametrize("kind", tpms.KINDS)
def test_analytic_gradients_match_finite_differences(kind):
    rng = np.random.default_rng(0)
    X, Y, Z = rng.uniform(0, 2 * np.pi, (3, 2000))
    _, gx, gy, gz = tpms._field_grad(kind, X, Y, Z)
    e = 1e-6
    for g, (dx, dy, dz) in ((gx, (e, 0, 0)), (gy, (0, e, 0)), (gz, (0, 0, e))):
        fd = (tpms._field_grad(kind, X + dx, Y + dy, Z + dz)[0] - tpms._field_grad(kind, X - dx, Y - dy, Z - dz)[0]) / (2 * e)
        assert np.max(np.abs(fd - g)) < 1e-6


@pytest.mark.parametrize("kind", tpms.KINDS)
def test_neck_is_narrower_than_hydraulic_diameter(kind):
    m = tpms.manufacturability(kind, 0.1)
    u = tpms.unit_cell(kind, 0.1)
    assert 0 < m.neck_over_a[0] < u.hydraulic_diameter_over_a(0)


@pytest.mark.parametrize("kind", tpms.KINDS)
def test_min_wall_never_exceeds_mean(kind):
    m = tpms.manufacturability(kind, 0.25)
    assert m.wall_min_over_a <= m.wall_mean_over_a


def test_table_matches_live_measurement():
    table = tpms._table()
    assert table, "precomputed table missing; run scripts/build_tpms_table.py"
    row = next(r for r in table["kinds"]["diamond"] if abs(r["ratio"] - 0.12) < 1e-9)
    live = tpms.measure_manufacturability("diamond", 0.12, table["resolution"])
    assert live.neck_over_a[0] == pytest.approx(row["neck_over_a"][0], abs=1e-9)
    assert live.wall_min_over_a == pytest.approx(row["wall_min_over_a"], rel=1e-6)


@pytest.mark.parametrize("region", ["solid", "fluid_a", "fluid_b"])
def test_sample_mesh_stays_inside_the_block(region):
    a, n = 0.004, 2
    verts, faces = tpms.sample_mesh("gyroid", a, 0.1 * a, n_cells=n, region=region, resolution=24)
    assert verts.min() >= 0.0 and verts.max() <= n * a + 1e-12
    assert tpms.mesh_volume(verts, faces) > 0


def test_fluid_mesh_volume_matches_measured_fraction():
    a, r, n = 0.004, 0.1, 2
    verts, faces = tpms.sample_mesh("gyroid", a, r * a, n_cells=n, region="fluid_a", resolution=40)
    expected = tpms.unit_cell("gyroid", r, 96).fluid_fraction[0] * (n * a) ** 3
    assert tpms.mesh_volume(verts, faces) == pytest.approx(expected, rel=0.02)


# ----------------------------------------------------------------------------- hx model

def test_reported_h_ua_and_dp_follow_from_reported_quantities(spec_dict):
    req = _req(spec_dict)
    ev = TwoFluidTPMS().evaluate(BASE, req)
    m = ev.metrics
    hs = m["hot"]
    T_mean = 0.5 * (req.hot.inlet_temperature_K + m["hot_outlet_K"])
    st = fluids.state("Water", T_mean, req.hot.inlet_pressure_Pa)
    assert hs["h_W_m2K"] == pytest.approx(hs["nusselt_baseline"] * st.k / hs["hydraulic_diameter_m"], rel=1e-3)
    dp = hs["friction_baseline"] * (BASE["length_m"] / hs["hydraulic_diameter_m"]) * 0.5 * st.rho * hs["velocity_m_s"] ** 2
    assert hs["pressure_drop_Pa"] == pytest.approx(dp, rel=1e-3)
    cs = m["cold"]
    a_m = 0.5 * (hs["wetted_area_m2"] + cs["wetted_area_m2"])
    ua = 1 / (1 / (hs["h_W_m2K"] * hs["wetted_area_m2"]) + m["wall_conduction_m"] / (15.0 * a_m) + 1 / (cs["h_W_m2K"] * cs["wetted_area_m2"]))
    assert m["UA_W_K"] == pytest.approx(ua, rel=1e-3)


def test_manufacturing_checks_use_neck_and_min_wall(spec_dict):
    req = _req(spec_dict)
    m = TwoFluidTPMS().evaluate(BASE, req).metrics
    assert m["hot"]["neck_m"] < m["hot"]["hydraulic_diameter_m"]
    mf = tpms.manufacturability("gyroid", BASE["wall_ratio"])
    assert m["wall_min_m"] == pytest.approx(mf.wall_min_over_a * BASE["cell_m"])
    assert m["wall_min_m"] <= mf.wall_mean_over_a * BASE["cell_m"]


def test_too_few_cells_is_infeasible(spec_dict):
    ev = TwoFluidTPMS().evaluate({**BASE, "cell_m": 0.03, "width_m": 0.04, "height_m": 0.04}, _req(spec_dict))
    assert ev.constraints["cells_across"] > 0 and not ev.feasible


def test_outside_envelope_is_infeasible(spec_dict):
    ev = TwoFluidTPMS().evaluate({**BASE, "length_m": 0.3}, _req(spec_dict))
    assert ev.constraints["envelope"] > 0 and not ev.feasible


def test_phase_change_is_infeasible(spec_dict):
    spec_dict["requirements"]["hot"]["inlet_temperature_K"] = 420.0  # water at 2 bar boils near 393 K
    ev = TwoFluidTPMS().evaluate(BASE, _req(spec_dict))
    assert ev.constraints["single_phase"] > 0 and not ev.feasible


def test_laminar_default_is_uniform_wall_temperature_with_entry_gain(spec_dict):
    """D11 baseline (uniform wall temperature, 3.66 fully developed) with Hausen's entry gain (audit H4)."""
    hs = TwoFluidTPMS().evaluate(BASE, _req(spec_dict)).metrics["hot"]
    assert hs["regime"] == "laminar" and hs["nusselt_fully_developed"] == pytest.approx(3.66)
    assert "Hausen" in hs["nusselt_correlation"] and hs["nusselt_baseline"] >= 3.66


# ----------------------------------------------------------------------------- calibration

def test_registry_load_refuses_unsourced_factor(tmp_path):
    p = tmp_path / "cal.json"
    p.write_text(json.dumps({"hx.gyroid.friction": {"value": 3.0}}))
    with pytest.raises(ValueError):
        Registry.load(p)


def test_registry_missing_file_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        Registry.load(tmp_path / "nope.json")


def test_registry_cache_follows_calibration_file(tmp_path, spec_dict):
    p = tmp_path / "cal.json"
    Registry({"hx.gyroid.friction": Factor("hx.gyroid.friction", 3.0, ("simlab:t",), valid_range={"reynolds": (1.0, 2000.0)},
                                          fitted_on="2026-10-07")}).save(p)
    model = TwoFluidTPMS()
    base = model.evaluate(BASE, _req(spec_dict)).metrics["hot"]["pressure_drop_Pa"]
    spec_dict["requirements"]["calibration_file"] = str(p)
    cal = model.evaluate(BASE, _req(spec_dict)).metrics["hot"]["pressure_drop_Pa"]
    assert cal == pytest.approx(3.0 * base, rel=1e-6)


def test_factor_outside_fitted_range_warns(spec_dict):
    reg = Registry()
    reg.set(Factor("hx.gyroid.nusselt", 1.5, ("simlab:t",), valid_range={"reynolds": (5000.0, 9000.0)},
                   fitted_on="2026-10-07"))
    ev = TwoFluidTPMS(registry=reg).evaluate(BASE, _req(spec_dict))
    assert any("outside its fitted range" in w for w in ev.warnings)


# ----------------------------------------------------------------------------- evidence

def test_verify_missing_ledger_fails(tmp_path):
    ok, msg = evidence.Ledger(tmp_path / "none.jsonl").verify()
    assert not ok and "not found" in msg
    assert main(["ledger", "verify", str(tmp_path / "none.jsonl")]) == 1


def test_verify_corrupt_ledger_fails_cleanly(tmp_path, spec_dict):
    led = evidence.Ledger(tmp_path / "l.jsonl")
    led.append(evidence.make_record(kind="t", spec=spec_dict, model_id="m", model_version="0",
                                    inputs={}, observations={}, calibration={}))
    with led.path.open("a") as fh:
        fh.write('{"truncated": ')
    ok, msg = led.verify()
    assert not ok and "append was interrupted" in msg and "aces ledger seal" in msg
    with led.path.open("a") as fh:
        fh.write('\n{"truncated": \n')
    ok, msg = led.verify()
    assert not ok and "not valid JSON" in msg


def test_record_hashes_artifacts(tmp_path, spec_dict):
    art = tmp_path / "a.json"
    art.write_text("[]")
    rec = evidence.make_record(kind="t", spec=spec_dict, model_id="m", model_version="0", inputs={},
                               observations={}, calibration={}, artifacts={"a": art})
    assert rec["artifacts"]["a"]["sha256"] == evidence.file_sha256(art)
    assert "numpy" in rec["dependencies"]


def _run(tmp_path, spec_dict, project, ledger):
    spec_dict = json.loads(json.dumps(spec_dict))
    spec_dict["project"] = project
    spec_dict["rd_activity"] = f"activity-{project}"
    spec_dict["search"]["trials"] = 40
    sp = tmp_path / f"{project}.yaml"
    sp.write_text(yaml.safe_dump(spec_dict))
    out = tmp_path / f"run_{project}"
    assert main(["design", str(sp), "--out", str(out), "--ledger", str(ledger)]) == 0
    return out


def test_export_uses_its_own_run_not_the_ledger_tail(tmp_path, spec_dict):
    ledger = tmp_path / "ledger.jsonl"
    run_a = _run(tmp_path, spec_dict, "projA", ledger)
    _run(tmp_path, spec_dict, "projB", ledger)
    assert main(["export-simlab", str(run_a), "--solver", "s", "--vram-gb", "4", "--cells", "1",
                 "--ledger", str(tmp_path / "ledger.jsonl")]) == 0
    exp = yaml.safe_load(next(run_a.glob("simlab_*.yaml")).read_text())
    assert exp["project"] == "projA" and exp["rd_activity"] == "activity-projA"
    assert json.loads((run_a / "run.json").read_text())["record_hash"] == exp["tags"]["aces_record"]


def test_export_refuses_edited_results(tmp_path, spec_dict):
    run = _run(tmp_path, spec_dict, "projC", tmp_path / "ledger.jsonl")
    pareto = json.loads((run / "pareto.json").read_text())
    pareto[0]["params"]["cell_m"] *= 0.5
    (run / "pareto.json").write_text(json.dumps(pareto))
    assert main(["export-simlab", str(run), "--solver", "s", "--vram-gb", "4", "--cells", "1",
                 "--ledger", str(tmp_path / "ledger.jsonl")]) == 2
    assert not list(run.glob("simlab_*.yaml"))


def test_export_refuses_a_ledger_record_edited_to_match(tmp_path, spec_dict, capsys):
    """Audit M2: pareto.json edited and its sha256 edited in the ledger record to match."""
    ledger = tmp_path / "ledger.jsonl"
    run = _run(tmp_path, spec_dict, "projE", ledger)
    pareto = json.loads((run / "pareto.json").read_text())
    pareto[0]["params"]["cell_m"] *= 0.5
    (run / "pareto.json").write_text(json.dumps(pareto, indent=2))
    lines = ledger.read_text().splitlines()
    rec = json.loads(lines[-1])
    rec["artifacts"]["pareto"]["sha256"] = evidence.file_sha256(run / "pareto.json")
    lines[-1] = json.dumps(rec, sort_keys=True)
    ledger.write_text("\n".join(lines) + "\n")
    assert main(["export-simlab", str(run), "--solver", "s", "--vram-gb", "4", "--cells", "1",
                 "--ledger", str(tmp_path / "ledger.jsonl")]) == 2
    assert "does not verify" in capsys.readouterr().out
    assert not list(run.glob("simlab_*.yaml")) and not list(run.glob("*.stl"))


def test_export_refuses_a_truncated_ledger(tmp_path, spec_dict, capsys):
    ledger = tmp_path / "ledger.jsonl"
    run_a = _run(tmp_path, spec_dict, "projT", ledger)
    run_b = _run(tmp_path, spec_dict, "projU", ledger)
    lines = ledger.read_text().splitlines(keepends=True)
    ledger.write_text("".join(lines[:-1]))  # the last record (run_b's) cut off the end
    assert main(["export-simlab", str(run_b), "--solver", "s", "--vram-gb", "4", "--cells", "1",
                 "--ledger", str(tmp_path / "ledger.jsonl")]) == 2
    assert "is not in" in capsys.readouterr().out
    ledger.write_text("".join(lines[:-1]) + lines[-1][:40])  # half of it: an interrupted append
    assert main(["export-simlab", str(run_a), "--solver", "s", "--vram-gb", "4", "--cells", "1",
                 "--ledger", str(tmp_path / "ledger.jsonl")]) == 2
    assert "interrupted" in capsys.readouterr().out


@pytest.mark.parametrize("project", ["../evil", "a/b", "..", "x" * 65, "C:evil"])
def test_export_refuses_a_path_like_project(tmp_path, spec_dict, project, capsys):
    run = _run(tmp_path, spec_dict, "projP", tmp_path / "ledger.jsonl")
    spec_dict = json.loads(json.dumps(spec_dict))
    spec_dict["project"] = project
    spec_dict["search"]["trials"] = 40
    sp = tmp_path / "evil.yaml"
    sp.write_text(yaml.safe_dump(spec_dict))
    out = tmp_path / "run_evil"
    assert main(["design", str(sp), "--out", str(out), "--ledger", str(tmp_path / "ledger.jsonl")]) == 0
    assert main(["export-simlab", str(out), "--solver", "s", "--vram-gb", "4", "--cells", "1",
                 "--ledger", str(tmp_path / "ledger.jsonl")]) == 2
    assert "project" in capsys.readouterr().out and not list(out.glob("simlab_*.yaml"))
    assert main(["export-simlab", str(run), "--solver", "../s", "--vram-gb", "4", "--cells", "1",
                 "--ledger", str(tmp_path / "ledger.jsonl")]) == 2
    assert not list(run.glob("simlab_*.yaml"))


def test_reexport_never_overwrites_the_experiment(tmp_path, spec_dict, capsys):
    run = _run(tmp_path, spec_dict, "projX", tmp_path / "ledger.jsonl")
    args = ["export-simlab", str(run), "--solver", "s", "--vram-gb", "4", "--cells", "1",
                 "--ledger", str(tmp_path / "ledger.jsonl")]
    assert main(args) == 0
    exp = next(run.glob("simlab_*.yaml"))
    before = exp.read_bytes()
    assert main([*args[:3], "other-solver", *args[4:]]) == 2
    assert "never overwrites" in capsys.readouterr().out
    assert exp.read_bytes() == before


# ----------------------------------------------------------------------------- run folders

def test_design_refuses_to_overwrite_a_finished_run(tmp_path, spec_dict, capsys):
    ledger = tmp_path / "ledger.jsonl"
    out = _run(tmp_path, spec_dict, "projR", ledger)
    before = (out / "run.json").read_text()
    n = len(evidence.Ledger(ledger).records())
    assert main(["design", str(tmp_path / "projR.yaml"), "--out", str(out), "--ledger", str(ledger)]) == 2
    assert "already holds a finished run" in capsys.readouterr().out
    assert (out / "run.json").read_text() == before
    assert len(evidence.Ledger(ledger).records()) == n  # nothing appended


def test_run_files_are_not_written_through_symlinks(tmp_path):
    from aces.cli import _safe_write
    target = tmp_path / "elsewhere.txt"
    target.write_text("keep")
    link = tmp_path / "report.md"
    link.symlink_to(target)
    with pytest.raises(safeio.UnsafePathError):
        _safe_write(link, "overwrite")
    assert target.read_text() == "keep"


def test_run_files_are_not_written_into_a_folder_swapped_for_a_link(tmp_path):
    """Audit M1: the run folder replaced by a link to somewhere else in the middle of a search."""
    from aces.cli import _safe_write
    run, elsewhere = tmp_path / "run", tmp_path / "elsewhere"
    run.mkdir()
    elsewhere.mkdir()
    run.rmdir()
    run.symlink_to(elsewhere, target_is_directory=True)
    for name in ("pareto.json", "evaluations.jsonl", "run.json", "report.md"):
        with pytest.raises(safeio.UnsafePathError):
            _safe_write(run / name, "x")
    assert not list(elsewhere.iterdir())


def test_a_run_needs_a_new_folder(tmp_path, spec_dict, capsys):
    out = tmp_path / "existing"
    out.mkdir()
    sp = tmp_path / "s.yaml"
    sp.write_text(yaml.safe_dump(spec_dict))
    assert main(["design", str(sp), "--out", str(out), "--ledger", str(tmp_path / "l.jsonl")]) == 2
    assert "needs a new --out folder" in capsys.readouterr().out
    assert not (tmp_path / "l.jsonl").exists()


@pytest.mark.parametrize("bounds", [{"wall_ratio": [0.01, 0.02]}, {"wall_ratio": [0.05, 0.9]},
                                    {"cell_m": [float("nan"), 0.01]}, {"tpms": [0, 1]}])
def test_search_bounds_may_only_narrow_the_model_range(tmp_path, spec_dict, bounds, capsys):
    """Audit M5: a wall ratio of 0.012 (outside the model's 0.03-0.35) took 2.5 GB; such bounds are refused."""
    spec_dict["search"]["bounds"] = bounds
    sp = tmp_path / "s.yaml"
    sp.write_text(yaml.safe_dump(spec_dict))
    assert main(["check", str(sp)]) == 2
    assert "bounds" in capsys.readouterr().out
    assert main(["design", str(sp), "--out", str(tmp_path / "run"), "--ledger", str(tmp_path / "l.jsonl")]) == 2
    assert not (tmp_path / "run").exists()
    req = _req(spec_dict)
    with pytest.raises(ValueError):
        search.run(TwoFluidTPMS(), req, trials=8, bounds={k: tuple(v) for k, v in bounds.items()})


def test_narrower_search_bounds_are_accepted(tmp_path, spec_dict):
    spec_dict["search"]["bounds"] = {"wall_ratio": [0.08, 0.2]}
    sp = tmp_path / "s.yaml"
    sp.write_text(yaml.safe_dump(spec_dict))
    assert main(["check", str(sp)]) == 0


def test_sample_never_overwrites_or_follows_a_link(tmp_path, capsys):
    args = ["sample", "--tpms", "gyroid", "--cell-m", "0.004", "--wall-ratio", "0.1", "--cells", "1",
            "--resolution", "12"]
    out = tmp_path / "s.stl"
    assert main([*args, "--out", str(out)]) == 0
    before = out.read_bytes()
    assert main([*args, "--wall-ratio", "0.2", "--out", str(out)]) == 2
    assert out.read_bytes() == before
    target = tmp_path / "elsewhere.txt"
    target.write_text("keep")
    (tmp_path / "link.stl").symlink_to(target)
    assert main([*args, "--out", str(tmp_path / "link.stl")]) == 2
    assert target.read_text() == "keep"


def test_calibration_file_is_relative_to_the_spec(tmp_path, spec_dict, monkeypatch):
    from aces.cli import _load
    d = tmp_path / "specs"
    d.mkdir()
    Registry({"hx.gyroid.friction": Factor("hx.gyroid.friction", 3.0, ("simlab:t",), valid_range={"reynolds": (1.0, 2000.0)},
                                          fitted_on="2026-10-07")}).save(d / "cal.json")
    spec_dict["requirements"]["calibration_file"] = "cal.json"
    (d / "s.yaml").write_text(yaml.safe_dump(spec_dict))
    monkeypatch.chdir(tmp_path)  # a CWD-relative lookup would miss the file
    _, _, req, model_cls = _load(str(d / "s.yaml"))
    assert req.calibration_file == str((d / "cal.json").resolve())
    assert model_cls().registry(req).get("hx.gyroid.friction").value == 3.0


def test_empty_calibration_file_is_a_spec_error(spec_dict):
    spec_dict["requirements"]["calibration_file"] = ""
    with pytest.raises(spec.SpecError):
        spec.parse(spec_dict, HXRequirements)


def test_export_refuses_a_run_json_that_names_another_ledger(tmp_path, spec_dict, capsys):
    """Verification finding: a forged run folder can name a ledger made for it; the caller's ledger decides."""
    run = _run(tmp_path, spec_dict, "projF", tmp_path / "ledger.jsonl")
    forged = tmp_path / "forged.jsonl"
    forged.write_bytes((tmp_path / "ledger.jsonl").read_bytes())
    m = json.loads((run / "run.json").read_text())
    m["ledger"] = str(forged)
    (run / "run.json").write_text(json.dumps(m))
    args = ["export-simlab", str(run), "--solver", "s", "--vram-gb", "4", "--cells", "1"]
    assert main([*args, "--ledger", str(tmp_path / "ledger.jsonl")]) == 2
    assert "names ledger" in capsys.readouterr().out
    assert main([*args, "--ledger", str(forged)]) == 0  # trusted only when the caller names it


def test_export_refuses_symlinked_run_files(tmp_path, spec_dict):
    run = _run(tmp_path, spec_dict, "projL", tmp_path / "ledger.jsonl")
    elsewhere = tmp_path / "elsewhere.yaml"
    elsewhere.write_text("keep")
    (run / "simlab_rank0_fluid_a.yaml").symlink_to(elsewhere)
    assert main(["export-simlab", str(run), "--solver", "s", "--vram-gb", "4", "--cells", "1",
                 "--ledger", str(tmp_path / "ledger.jsonl")]) == 2
    assert elsewhere.read_text() == "keep"
