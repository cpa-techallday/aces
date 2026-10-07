import json
from pathlib import Path

import pytest
import yaml

from aces import evidence, search, simlab, spec
from aces.cli import main
from aces.domains.hx import HXRequirements, TwoFluidTPMS

TEMPLATE = Path(__file__).resolve().parents[1] / "specs" / "templates" / "hx_two_fluid.yaml"


def test_template_reports_every_missing_field():
    with pytest.raises(spec.SpecError) as e:
        spec.load(TEMPLATE, HXRequirements)
    problems = e.value.problems
    assert "requirements.material.source: missing" in problems
    assert "rd_activity: missing" in problems
    assert all(p.endswith("missing") for p in problems)


def test_company_must_be_cpa(spec_dict):
    spec_dict["company"] = "omni"
    with pytest.raises(spec.SpecError) as e:
        spec.parse(spec_dict, HXRequirements)
    assert any("company" in p for p in e.value.problems)


def test_ledger_chain_and_tamper_detection(tmp_path, spec_dict):
    led = evidence.Ledger(tmp_path / "ledger.jsonl")
    for i in range(3):
        led.append(evidence.make_record(kind="t", spec=spec_dict, model_id="m", model_version="0",
                                        inputs={"i": i}, observations={}, calibration={}))
    assert led.verify()[0]
    lines = led.path.read_text().splitlines()
    rec = json.loads(lines[1])
    rec["inputs"]["i"] = 99
    lines[1] = json.dumps(rec, sort_keys=True)
    led.path.write_text("\n".join(lines) + "\n")
    ok, msg = led.verify()
    assert not ok and "record 1" in msg


def test_ledger_refuses_other_company(tmp_path):
    with pytest.raises(ValueError):
        evidence.Ledger(tmp_path / "l.jsonl").append({"company": "omni"})


def test_search_returns_nondominated_feasible_set(spec_dict):
    req = spec.parse(spec_dict, HXRequirements)[1]
    res = search.run(TwoFluidTPMS(resolution=40), req, trials=60, seed=3, population=20)
    assert res.pareto, "expected at least one feasible design for the test inputs"
    for a in res.pareto:
        assert a.feasible
        assert not any(search.dominates(b, a) for b in res.feasible)


def test_search_rejects_unknown_bounds(spec_dict):
    req = spec.parse(spec_dict, HXRequirements)[1]
    with pytest.raises(ValueError):
        search.run(TwoFluidTPMS(), req, trials=8, bounds={"nonsense": (0, 1)})


SIMLAB_COMMON = {"exp_id": "x", "project": "p", "owner": "o", "rd_activity": "r", "physics": "cfd", "solver": "s",
                 "description": "d", "hypothesis": "h", "expected": "e", "geometry_ref": "g.stl", "params": {},
                 "outputs": []}


def test_simlab_export_rules():
    common = dict(SIMLAB_COMMON)
    exp = simlab.experiment(vram_gb=8, **common)
    assert exp["company"] == "carbon" and exp["resources"]["precision"] == "float64"
    with pytest.raises(ValueError):
        simlab.experiment(vram_gb=61, **common)


def test_verification_refuses_float32():
    """Rule 6 / audit L7: FP32 Jacobi-PCG falsely converges, so a verification solve is FP64."""
    with pytest.raises(ValueError, match="float64"):
        simlab.experiment(vram_gb=8, precision="float32", **SIMLAB_COMMON)
    exp = simlab.experiment(vram_gb=8, precision="float32", kind="calibration", **SIMLAB_COMMON)
    assert exp["resources"]["precision"] == "float32"


@pytest.mark.parametrize("field,value", [("solver", "../evil"), ("solver", "a/b"), ("solver", ".."),
                                         ("project", "x" * 65), ("project", "C:\\x"), ("project", ""),
                                         ("exp_id", "a b"), ("solver", "s\n")])
def test_simlab_names_are_plain(field, value):
    with pytest.raises(ValueError, match="1-"):
        simlab.experiment(vram_gb=8, **{**SIMLAB_COMMON, field: value})


def test_simlab_write_never_overwrites_or_follows_a_link(tmp_path):
    exp = simlab.experiment(vram_gb=8, **SIMLAB_COMMON)
    p = simlab.write(exp, tmp_path / "e.yaml")
    with pytest.raises(FileExistsError):
        simlab.write(exp, p)
    target = tmp_path / "elsewhere.txt"
    target.write_text("keep")
    (tmp_path / "link.yaml").symlink_to(target)
    with pytest.raises(OSError):
        simlab.write(exp, tmp_path / "link.yaml")
    assert target.read_text() == "keep"


def test_write_stl_never_follows_a_link(tmp_path):
    from aces.domains.hx import tpms

    verts, faces = tpms.sample_mesh("gyroid", 0.004, 0.0004, n_cells=1, region="solid", resolution=10)
    target = tmp_path / "elsewhere.txt"
    target.write_text("keep")
    (tmp_path / "link.stl").symlink_to(target)
    with pytest.raises(OSError):
        tpms.write_stl(tmp_path / "link.stl", verts, faces)
    assert target.read_text() == "keep"
    p = tpms.write_stl(tmp_path / "s.stl", verts, faces)
    assert p.stat().st_size == 84 + 50 * len(faces)  # binary STL: 80-byte header, count, 50 bytes a face


def test_cli_end_to_end(tmp_path, spec_dict):
    spec_dict["search"]["trials"] = 40
    sp = tmp_path / "spec.yaml"
    sp.write_text(yaml.safe_dump(spec_dict))
    ledger = tmp_path / "ledger.jsonl"
    assert main(["check", str(sp)]) == 0
    assert main(["design", str(sp), "--out", str(tmp_path / "run"), "--ledger", str(ledger)]) == 0
    assert (tmp_path / "run" / "report.md").read_text().count("Not verified") == 1
    assert main(["ledger", "verify", str(ledger)]) == 0
    assert main(["export-simlab", str(tmp_path / "run"), "--solver", "test-solver", "--vram-gb", "4",
                 "--ledger", str(ledger), "--cells", "1"]) == 0
    exp = yaml.safe_load(next((tmp_path / "run").glob("simlab_*.yaml")).read_text())
    assert exp["company"] == "carbon" and exp["solver"] == "test-solver"
