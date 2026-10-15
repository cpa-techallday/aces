"""formgen route: spec validation, manufacturing routes, and an end-to-end run on the demo panel."""
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from aces import evidence, manufacturing, spec
from aces.cli import main
from aces.domains.form import FormRequirements

DEMO = Path(__file__).resolve().parents[1] / "examples" / "formgen" / "demo_panel.stl"

FORM_SPEC = {
    "aces": 1,
    "domain": "form.lattice",
    "project": "form-test",
    "owner": "test",
    "rd_activity": "formgen route tests (test inputs only)",
    "hypothesis": "The demo panel sequences completely.",
    "requirements": {
        "process": "printed_formwork",
        "source": "demo_panel.stl",
        "mode": "panel",
        "strut_mm": 200,
        "thickness_mm": 100,
        "bead_mm": 8,
        "material": {"name": "test-polymer", "density_g_cm3": 1.1, "source": "test input only"},
        "robot": {"print_speed_mm_s": 25, "jbi": True},
    },
}


def test_planned_route_is_refused():
    with pytest.raises(ValueError, match="planned"):
        manufacturing.require("cnc_machining", "hx.two_fluid")


def test_route_must_match_domain():
    with pytest.raises(ValueError, match="not supported"):
        manufacturing.require("lpbf", "form.lattice")
    assert manufacturing.for_domain("hx.two_fluid") == ["lpbf", "polymer_extrusion"]


def test_hx_spec_rejects_unknown_process(spec_dict):
    from aces.domains.hx import HXRequirements

    spec_dict["requirements"]["manufacturing"]["process"] = "casting"
    with pytest.raises(spec.SpecError):
        spec.parse(spec_dict, HXRequirements)


def test_form_spec_needs_sourced_density():
    d = yaml.safe_load(yaml.safe_dump(FORM_SPEC))
    del d["requirements"]["material"]["source"]
    with pytest.raises(spec.SpecError) as e:
        spec.parse(d, FormRequirements)
    assert "requirements.material.source: missing" in e.value.problems


def test_processes_command():
    assert main(["processes"]) == 0


def test_form_run_end_to_end(tmp_path):
    pytest.importorskip("trimesh")
    pytest.importorskip("fast_simplification")
    shutil.copy(DEMO, tmp_path / "demo_panel.stl")
    sp = tmp_path / "spec.yaml"
    sp.write_text(yaml.safe_dump(FORM_SPEC))
    ledger = tmp_path / "ledger.jsonl"
    assert main(["check", str(sp)]) == 0
    assert main(["form", str(sp), "--out", str(tmp_path / "run"), "--ledger", str(ledger)]) == 0
    rec = evidence.Ledger(ledger).records()[-1]
    assert rec["company"] == "CPA" and rec["kind"] == "aces.form_run"
    assert rec["observations"]["sequence"]["unreachable_struts"] == 0
    assert any(k.startswith("jbi_") for k in rec["artifacts"])
    for art in rec["artifacts"].values():
        assert evidence.file_sha256(art["path"]) == art["sha256"]
    report = (tmp_path / "run" / "report.md").read_text()
    assert "MotoSim" in report and "No structural check was requested" in report


# ----------------------------------------------------------------------------- M1: writes never follow links

def _form_spec(tmp_path: Path) -> Path:
    shutil.copy(DEMO, tmp_path / "demo_panel.stl")
    sp = tmp_path / "spec.yaml"
    sp.write_text(yaml.safe_dump(FORM_SPEC))
    return sp


@pytest.mark.skipif(sys.platform.startswith("win"), reason="symbolic links need POSIX here")
def test_form_refuses_a_folder_with_planted_links(tmp_path, capsys):
    """The audit's repro: formgen.log and the estimate JSON planted as links into an existing run folder."""
    sp = _form_spec(tmp_path)
    run = tmp_path / "run"
    run.mkdir()
    victims = {}
    for name in ("formgen.log", "demo_panel_estimate.json"):
        victims[name] = tmp_path / f"victim_{name}"
        victims[name].write_text("keep")
        (run / name).symlink_to(victims[name])
    assert main(["form", str(sp), "--out", str(run), "--ledger", str(tmp_path / "l.jsonl")]) == 2
    assert "new --out folder" in capsys.readouterr().out
    assert all(v.read_text() == "keep" for v in victims.values())


@pytest.mark.skipif(sys.platform.startswith("win"), reason="symbolic links need POSIX here")
@pytest.mark.parametrize("name", ["formgen.log", "demo_panel_estimate.json"])
def test_form_route_refuses_a_link_planted_during_the_run(tmp_path, name):
    pytest.importorskip("trimesh")
    pytest.importorskip("fast_simplification")
    from aces import safeio
    from aces.domains.form import route

    sp = _form_spec(tmp_path)
    _, req = spec.parse(yaml.safe_load(sp.read_text()), FormRequirements)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    with safeio.OutDir(tmp_path / "run", new=True) as od:
        (tmp_path / "run" / name).symlink_to(victim)  # planted after the folder was made (a race)
        with pytest.raises(safeio.UnsafePathError):
            route.run(req, od, tmp_path, log=lambda *_: None)
    assert victim.read_text() == "keep"


@pytest.mark.skipif(sys.platform.startswith("win"), reason="symbolic links need POSIX here")
def test_formgen_standalone_replaces_a_planted_link_without_following_it(tmp_path):
    pytest.importorskip("trimesh")
    pytest.importorskip("fast_simplification")
    from formgen.pipeline import Options, run

    out = tmp_path / "out"
    out.mkdir()
    victims = {}
    for name in ("demo_panel_estimate.json", "demo_panel_waypoints.csv", "DEMO_P_001.JBI"):
        victims[name] = tmp_path / f"victim_{name}"
        victims[name].write_text("keep")
        (out / name).symlink_to(victims[name])
    opts = Options(input=str(DEMO), mode="panel", strut=200, thickness=100, out=str(out), want_preview=False,
                   want_viewer=False, want_png=False, want_jbi=True)
    res = run(opts, log=lambda *_: None)
    assert all(v.read_text() == "keep" for v in victims.values())
    for name in victims:
        assert not (out / name).is_symlink()
    assert json.loads((out / "demo_panel_estimate.json").read_text())["stem"] == "demo_panel"
    run(opts, log=lambda *_: None)  # a standalone re-run into the same folder still works
    assert res["outputs"]["jbi"]


def test_formgen_text_outputs_keep_their_line_endings(tmp_path):
    pytest.importorskip("trimesh")
    import numpy as np

    from formgen import export
    from formgen.graph import LatticeGraph

    g = LatticeGraph(nodes=np.array([[0.0, 0, 0], [100.0, 0, 0], [0, 100.0, 0]]),
                     struts=[(0, 1, "skin"), (1, 2, "skin")])
    steps = [{"strut": 0, "from": 0, "to": 1}, {"strut": 1, "from": 1, "to": 2}]
    text, n = export.waypoints_csv(g, steps)
    assert n == 8 and text.startswith("strut,phase,x,y,z,extrude\r\n") and "\n" not in text.replace("\r\n", "")
    (ci, job), = export.jbi_jobs(g, steps, job_name="T")
    assert ci == 1 and job.startswith("/JOB\r\n//NAME T001\r\n") and job.endswith("END\r\n")
    assert "\n" not in job.replace("\r\n", "")
    paths = export.save_jbi(str(tmp_path / "T"), g, steps, job_name="T")
    assert (tmp_path / "T_001.JBI").read_bytes() == job.encode() and paths == [str(tmp_path / "T_001.JBI")]
