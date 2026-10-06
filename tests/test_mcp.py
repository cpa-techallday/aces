"""ACES MCP server: policy tiers, path confinement and the audit log."""
import asyncio
import json

import pytest
import yaml

pytest.importorskip("mcp")

from aces.mcp_server import Gate, build  # noqa: E402


def _tools(server):
    return {t.name for t in asyncio.run(server.list_tools())}


def _call(server, name, args):
    res = asyncio.run(server.call_tool(name, args))
    # mcp 2.x returns CallToolResult; 1.x returns (content, structured) or a content list
    structured = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)
    if structured is None and isinstance(res, tuple):
        structured = res[1]
    if isinstance(structured, dict) and "result" in structured and len(structured) == 1:
        structured = structured["result"]
    if structured is None:
        content = getattr(res, "content", res)
        structured = json.loads(content[0].text)
    return structured


def test_read_policy_hides_submit_tools(tmp_path):
    names = _tools(build(Gate(root=tmp_path, policy="read")))
    assert {"aces_processes", "aces_check", "aces_report", "aces_ledger_verify"} <= names
    assert not names & {"aces_design", "aces_form", "aces_rod", "aces_calibrate"}


def test_submit_and_full_tiers(tmp_path):
    sub = _tools(build(Gate(root=tmp_path, policy="submit")))
    assert {"aces_design", "aces_form", "aces_rod", "aces_export_simlab"} <= sub and "aces_calibrate" not in sub
    assert "aces_calibrate" in _tools(build(Gate(root=tmp_path, policy="full")))


def test_paths_outside_root_are_refused_and_audited(tmp_path):
    gate = Gate(root=tmp_path / "root", policy="read")
    (tmp_path / "root").mkdir()
    out = _call(build(gate), "aces_check", {"spec_path": "../outside.yaml"})
    assert "outside the ACES MCP root" in out["error"]
    rec = json.loads(gate.audit.read_text().splitlines()[-1])
    assert rec["tool"] == "aces_check" and rec["exit"] is None


def test_check_tool_runs_the_cli(tmp_path, spec_dict):
    (tmp_path / "s.yaml").write_text(yaml.safe_dump(spec_dict))
    gate = Gate(root=tmp_path, policy="read")
    out = _call(build(gate), "aces_check", {"spec_path": "s.yaml"})
    assert out["exit"] == 0 and "OK" in out["output"]
    assert json.loads(gate.audit.read_text().splitlines()[-1])["exit"] == 0


def test_bad_policy_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        Gate(root=tmp_path, policy="admin")


ROD = {"aces": 1, "domain": "rod.lattice", "project": "p", "owner": "o", "rd_activity": "r", "hypothesis": "h",
       "requirements": {"process": "arc_lattice_rod_welding", "study_dir": "../elsewhere",
                        "material_source": "test input"}}


def test_paths_inside_a_spec_are_confined_to_the_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "rod.yaml").write_text(yaml.safe_dump(ROD))
    gate = Gate(root=root, policy="submit")
    out = _call(build(gate), "aces_rod", {"spec_path": "rod.yaml", "out_dir": "out/r"})
    assert "outside the ACES MCP root" in out["error"]
    assert not (root / "out" / "r").exists()  # nothing ran


def test_hx_calibration_file_outside_root_is_refused(tmp_path, spec_dict):
    root = tmp_path / "root"
    root.mkdir()
    spec_dict["requirements"]["calibration_file"] = str(tmp_path / "secret.json")
    (root / "s.yaml").write_text(yaml.safe_dump(spec_dict))
    out = _call(build(Gate(root=root, policy="read")), "aces_check", {"spec_path": "s.yaml"})
    assert "outside the ACES MCP root" in out["error"]


def test_report_refuses_a_symlink_that_leaves_the_root(tmp_path):
    root = tmp_path / "root"
    run = root / "run"
    run.mkdir(parents=True)
    (tmp_path / "secret.txt").write_text("not for agents")
    (run / "report.md").symlink_to(tmp_path / "secret.txt")
    (run / "run.json").write_text("{}")
    out = _call(build(Gate(root=root, policy="read")), "aces_report", {"run_dir": "run"})
    assert "outside the ACES MCP root" in out["error"]
    assert "not for agents" not in json.dumps(out)


@pytest.mark.parametrize("trials", [0, -5, 100_001])
def test_design_trials_are_bounded(tmp_path, spec_dict, trials):
    (tmp_path / "s.yaml").write_text(yaml.safe_dump(spec_dict))
    out = _call(build(Gate(root=tmp_path, policy="submit")), "aces_design",
                {"spec_path": "s.yaml", "out_dir": "out/d", "trials": trials})
    assert "trials must be between" in out["error"]


@pytest.mark.parametrize("args", [{"cells": 0}, {"cells": 7}, {"rank": -1}, {"vram_gb": 61}, {"vram_gb": 0}])
def test_export_arguments_are_bounded(tmp_path, args):
    (tmp_path / "run").mkdir()
    a = {"run_dir": "run", "solver": "lbm", "vram_gb": 8.0, **args}
    out = _call(build(Gate(root=tmp_path, policy="submit")), "aces_export_simlab", a)
    assert out["exit"] is None and "must be" in out["error"]


def test_malformed_spec_is_refused_and_audited(tmp_path):
    (tmp_path / "s.yaml").write_text(yaml.safe_dump({"domain": ["rod.lattice"], "requirements": {}}))
    gate = Gate(root=tmp_path, policy="read")
    _call(build(gate), "aces_check", {"spec_path": "s.yaml"})
    (tmp_path / "t.yaml").write_text("- just\n- a list\n")
    out = _call(build(gate), "aces_check", {"spec_path": "t.yaml"})
    assert "not a mapping" in out["error"]
    assert len(gate.audit.read_text().splitlines()) == 2  # both calls audited
