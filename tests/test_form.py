"""formgen route: spec validation, manufacturing routes, and an end-to-end run on the demo panel."""
import shutil
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
