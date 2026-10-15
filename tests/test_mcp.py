"""ACES MCP server: policy tiers, path confinement, limits and the audit log.

Specs, ledgers and folders here are test inputs made up for the checks, not design data.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, Field

pytest.importorskip("mcp")

from aces import mcp_server  # noqa: E402
from aces.mcp_server import Gate, build, path_fields  # noqa: E402

posix_only = pytest.mark.skipif(os.name != "posix", reason="symbolic links / rlimits need POSIX")


def _tools(server):
    return {t.name for t in asyncio.run(server.list_tools())}


def _unwrap(res):
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


def _call(server, name, args):
    return _unwrap(asyncio.run(server.call_tool(name, args)))


def test_read_policy_hides_submit_tools(tmp_path):
    names = _tools(build(Gate(root=tmp_path, policy="read")))
    assert {"aces_processes", "aces_check", "aces_report", "aces_ledger_verify"} <= names
    assert not names & {"aces_design", "aces_form", "aces_rod", "aces_calibrate"}


def test_submit_and_full_tiers(tmp_path):
    sub = _tools(build(Gate(root=tmp_path, policy="submit")))
    assert {"aces_design", "aces_form", "aces_export_simlab"} <= sub
    assert not sub & {"aces_rod", "aces_calibrate"}  # H1: the rod route runs study code, so it is full-tier only
    assert {"aces_rod", "aces_calibrate"} <= _tools(build(Gate(root=tmp_path, policy="full")))


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
    gate = Gate(root=root, policy="full")
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


@pytest.mark.parametrize("args", [{"cells": 0}, {"cells": 7}, {"rank": -1}, {"vram_gb": 61}, {"vram_gb": 0},
                                  {"solver": "../evil"}, {"solver": ".."}, {"solver": "a/b"}])
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


# ----------------------------------------------------------------------------- H1: code execution

def test_rod_with_an_uncommitted_study_under_the_root_is_refused(tmp_path):
    """The audit's attack: plant a study folder under the root and run its script through aces_rod."""
    root = tmp_path / "root"
    study = root / "studies" / "planted"
    (study / "cad").mkdir(parents=True)
    (study / "scripts").mkdir()
    (study / "cad" / "params.json").write_text("{}")
    marker = tmp_path / "PWNED"
    (study / "scripts" / "evil.py").write_text(f"open({str(marker)!r}, 'w').write('ran')\n")
    spec = json.loads(json.dumps(ROD))
    spec["requirements"].update(study_dir="studies/planted", stages=["evil.py"])
    (root / "rod.yaml").write_text(yaml.safe_dump(spec))
    assert "aces_rod" not in _tools(build(Gate(root=root, policy="submit")))
    out = _call(build(Gate(root=root, policy="full")), "aces_rod", {"spec_path": "rod.yaml", "out_dir": "out/r"})
    assert out["exit"] == 2 and "committed" in out["output"]
    assert not marker.exists() and not (root / "out" / "r").exists()


@pytest.mark.parametrize("p", ["src/aces/x.py", "SRC/evil.py", ".git/config", "sub/.git/hooks/pre-commit",
                               "a/.GIT/config"])
def test_gate_refuses_src_and_git(tmp_path, p):
    with pytest.raises(PermissionError, match=r"src/, a \.git folder"):
        Gate(root=tmp_path, policy="submit").path(p)


def test_gate_refuses_the_running_code(tmp_path):
    gate = Gate(root=mcp_server.CODE_DIR.parent, policy="submit")
    with pytest.raises(PermissionError):
        gate.path(str(mcp_server.CODE_DIR / "aces" / "cli.py"))


def test_outputs_and_spec_paths_into_git_are_refused(tmp_path, spec_dict):
    (tmp_path / ".git").mkdir()
    spec_dict["requirements"]["calibration_file"] = ".git/config"
    (tmp_path / "s.yaml").write_text(yaml.safe_dump(spec_dict))
    out = _call(build(Gate(root=tmp_path, policy="submit")), "aces_check", {"spec_path": "s.yaml"})
    assert ".git folder" in out["error"]
    spec_dict["requirements"].pop("calibration_file")
    (tmp_path / "t.yaml").write_text(yaml.safe_dump(spec_dict))
    out = _call(build(Gate(root=tmp_path, policy="submit")), "aces_design",
                {"spec_path": "t.yaml", "out_dir": ".git/hooks"})
    assert ".git folder" in out["error"]


# ----------------------------------------------------------------------------- R7: paths inside specs

RADAR = {"aces": 1, "domain": "radar.phased_array", "project": "p", "owner": "o", "rd_activity": "r",
         "hypothesis": "h", "requirements": {"calibration_file": "/etc/passwd"}}


def test_radar_calibration_file_is_checked(tmp_path):
    from aces.domains.radar.model import RadarRequirements

    assert (("calibration_file",), ()) in path_fields(RadarRequirements)
    (tmp_path / "r.yaml").write_text(yaml.safe_dump(RADAR))
    out = _call(build(Gate(root=tmp_path, policy="read")), "aces_check", {"spec_path": "r.yaml"})
    assert "outside the ACES MCP root" in out["error"]


def test_path_fields_come_from_the_models():
    """A new domain's path fields are found without a table: by name, by type and by marker, at any depth."""

    class Probe(BaseModel):
        mesh_file: str | None = None
        out: Path | None = None
        label: str = ""

    class Domain(BaseModel):
        name: str = ""
        probes: list[Probe] = []
        by_name: dict[str, Probe] = {}
        source: str = Field("", json_schema_extra={"aces_path": {"not_paths": ["live"]}})
        work_dir: str = "."

    found = dict(path_fields(Domain))
    assert found[("probes", "*", "mesh_file")] == ()
    assert ("probes", "*", "out") in found and ("by_name", "*", "mesh_file") in found
    assert found[("source",)] == ("live",) and ("work_dir",) in found
    assert ("name",) not in found and ("probes", "*", "label") not in found
    from aces.mcp_server import requirement_models

    models = requirement_models()
    assert {"hx.two_fluid", "radar.phased_array", "form.lattice", "rod.lattice"} <= set(models)
    assert dict(path_fields(models["form.lattice"]))[("source",)] == ("fusion",)
    assert ("study_dir",) in dict(path_fields(models["rod.lattice"]))
    assert ("calibration_file",) in dict(path_fields(models["hx.two_fluid"]))


# ----------------------------------------------------------------------------- M5: limits

def test_read_calls_do_not_wait_for_a_run(tmp_path, spec_dict, monkeypatch):
    (tmp_path / "s.yaml").write_text(yaml.safe_dump(spec_dict))
    real = mcp_server._child_cmd

    def child(argv, mem):  # the design run is replaced by a long sleep (test input)
        return [sys.executable, "-c", "import time; time.sleep(15)"] if argv[0] == "design" else real(argv, mem)

    monkeypatch.setattr(mcp_server, "_child_cmd", child)
    server = build(Gate(root=tmp_path, policy="submit"))

    async def scenario():
        run = asyncio.ensure_future(server.call_tool("aces_design", {"spec_path": "s.yaml", "out_dir": "out/d"}))
        await asyncio.sleep(0.5)
        check = _unwrap(await server.call_tool("aces_check", {"spec_path": "s.yaml"}))
        running = not run.done()
        await run
        return check, running

    check, running = asyncio.run(scenario())
    assert check["exit"] == 0 and running  # the check finished while the run was still going


def test_a_second_run_is_refused_not_queued(tmp_path, spec_dict, monkeypatch):
    (tmp_path / "s.yaml").write_text(yaml.safe_dump(spec_dict))
    monkeypatch.setattr(mcp_server, "_child_cmd", lambda argv, mem: [sys.executable, "-c", "import time; time.sleep(5)"])
    server = build(Gate(root=tmp_path, policy="submit", max_runs=1))

    async def scenario():
        first = asyncio.ensure_future(server.call_tool("aces_design", {"spec_path": "s.yaml", "out_dir": "a"}))
        await asyncio.sleep(0.5)
        second = _unwrap(await server.call_tool("aces_design", {"spec_path": "s.yaml", "out_dir": "b"}))
        await first
        return second

    assert "already in progress" in asyncio.run(scenario())["error"]


def test_a_run_is_stopped_at_its_time_limit(tmp_path, spec_dict, monkeypatch):
    (tmp_path / "s.yaml").write_text(yaml.safe_dump(spec_dict))
    monkeypatch.setattr(mcp_server, "_child_cmd", lambda argv, mem: [sys.executable, "-c", "import time; time.sleep(60)"])
    gate = Gate(root=tmp_path, policy="submit", run_timeout_s=1)
    t0 = time.monotonic()
    out = _call(build(gate), "aces_design", {"spec_path": "s.yaml", "out_dir": "out/d"})
    assert time.monotonic() - t0 < 20
    assert out["exit"] is None and "1 s limit" in out["error"]
    assert "limit" in json.loads(gate.audit.read_text().splitlines()[-1])["error"]


@posix_only
def test_a_call_runs_under_a_memory_limit(tmp_path, monkeypatch):
    # the child allocates 1 GiB under a 256 MB limit (test input); without the limit this would just succeed
    monkeypatch.setattr(mcp_server, "_ENTRY", "rc = len(bytearray(1 << 30)) * 0")
    out = mcp_server._run_cli(Gate(root=tmp_path, policy="read", memory_mb=256), "t", [], {}, timeout=60)
    assert out["exit"] is None and "out of memory" in out["error"]
    monkeypatch.setattr(mcp_server, "_ENTRY", "rc = len(bytearray(1 << 20)) * 0")
    assert mcp_server._run_cli(Gate(root=tmp_path, policy="read", memory_mb=256), "t", [], {}, timeout=60)["exit"] == 0


def test_the_child_does_not_import_from_the_root(tmp_path, spec_dict):
    """A planted aces/ or yaml.py in the MCP root (the child's working directory) is never imported."""
    marker = tmp_path / "PWNED"
    (tmp_path / "yaml.py").write_text(f"open({str(marker)!r}, 'w').write('ran')\n")
    (tmp_path / "s.yaml").write_text(yaml.safe_dump(spec_dict))
    out = _call(build(Gate(root=tmp_path, policy="read")), "aces_check", {"spec_path": "s.yaml"})
    assert out["exit"] == 0 and not marker.exists()


def test_the_child_gets_the_minimal_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ACES_TEST_SERVER_SECRET", "test input")
    monkeypatch.setattr(mcp_server, "_ENTRY", "import os; print(sorted(os.environ)); rc = 0")
    out = mcp_server._run_cli(Gate(root=tmp_path, policy="read"), "t", [], {}, timeout=60)
    assert out["exit"] == 0 and "ACES_TEST_SERVER_SECRET" not in out["output"]


def test_ledger_and_spec_reads_are_size_limited(tmp_path):
    (tmp_path / "big.jsonl").write_bytes(b"{}\n" * 400_000)  # 1.2 MB of test input
    gate = Gate(root=tmp_path, policy="read", max_ledger_mb=1)
    out = _call(build(gate), "aces_ledger_verify", {"ledger_path": "big.jsonl"})
    assert out["exit"] is None and "verifies ledgers up to" in out["error"]
    (tmp_path / "big.yaml").write_text("a: " + "x" * 1_100_000 + "\n")
    out = _call(build(gate), "aces_check", {"spec_path": "big.yaml"})
    assert "limit" in out["error"]


def test_ledger_verify_passes_the_anchors(tmp_path, spec_dict):
    from aces import evidence

    led = evidence.Ledger(tmp_path / "out" / "evidence" / "ledger.jsonl")
    led.append(evidence.make_record(kind="t", spec=spec_dict, model_id="m", model_version="0", inputs={},
                                    observations={}, calibration={}))
    n, head = led.head()
    server = build(Gate(root=tmp_path, policy="read"))
    assert _call(server, "aces_ledger_verify", {"expect_head": head, "min_records": n})["exit"] == 0
    out = _call(server, "aces_ledger_verify", {"min_records": n + 1})
    assert out["exit"] == 1 and "fewer than" in out["output"]
    assert "64-character" in _call(server, "aces_ledger_verify", {"expect_head": "--min-records=0"})["error"]


@posix_only
def test_audit_log_is_not_written_through_a_link(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "elsewhere.txt").write_text("keep")
    (tmp_path / "out" / "mcp_audit.jsonl").symlink_to(tmp_path / "elsewhere.txt")
    with pytest.raises(OSError):
        Gate(root=tmp_path, policy="read").log("t", {}, 0)
    assert (tmp_path / "elsewhere.txt").read_text() == "keep"


def test_the_cli_gets_a_frozen_spec_with_checked_absolute_paths(tmp_path, spec_dict):
    """Verification finding: a spec rewritten after its check reached the child. The child now gets a frozen copy."""
    root = tmp_path / "root"
    (root / "cal").mkdir(parents=True)
    (root / "cal" / "c.json").write_text("{}")
    spec_dict["requirements"]["calibration_file"] = "cal/c.json"
    (root / "s.yaml").write_text(yaml.safe_dump(spec_dict))
    gate = Gate(root=root, policy="read")
    frozen = Path(gate.check_spec("s.yaml"))
    assert frozen.parent == gate.frozen_dir and not frozen.is_relative_to(root)
    data = yaml.safe_load(frozen.read_text())
    assert data["requirements"]["calibration_file"] == str((root / "cal" / "c.json").resolve())
    spec_dict["requirements"]["calibration_file"] = str(tmp_path / "outside.json")  # rewritten after the check
    (root / "s.yaml").write_text(yaml.safe_dump(spec_dict))
    assert yaml.safe_load(frozen.read_text())["requirements"]["calibration_file"].startswith(str(root.resolve()))


def test_the_child_rechecks_paths_against_the_root(tmp_path, spec_dict, monkeypatch, capsys):
    """Defence in depth: under aces-mcp (ACES_PATH_ROOT) the CLI refuses a spec path outside the root."""
    from aces.cli import main

    root = tmp_path / "root"
    root.mkdir()
    secret = tmp_path / "outside.json"
    secret.write_text('{"OUTSIDE_ROOT_TEST_KEY": {"value": 1}}')
    spec_dict["requirements"]["calibration_file"] = str(secret)
    sp = root / "s.yaml"
    sp.write_text(yaml.safe_dump(spec_dict))
    monkeypatch.setenv("ACES_PATH_ROOT", str(root))
    assert main(["design", str(sp), "--out", str(root / "run"), "--ledger", str(root / "l.jsonl")]) == 2
    out = capsys.readouterr().out
    assert "outside the ACES MCP root" in out and "OUTSIDE_ROOT_TEST_KEY" not in out
