"""ARC-1 rod-lattice study: regenerates from params and runs as the rod.lattice route."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from aces import evidence, manufacturing, spec
from aces.cli import main
from aces.domains.rod import RodRequirements

STUDY = Path(__file__).resolve().parents[1] / "studies" / "arc1_chassis"

ROD_SPEC = {
    "aces": 1,
    "domain": "rod.lattice",
    "project": "rod-test",
    "owner": "test",
    "rd_activity": "rod-lattice route tests",
    "hypothesis": "The ARC-1 params run through the route.",
    "requirements": {
        "process": "arc_lattice_rod_welding",
        "study_dir": str(STUDY),
        "material_source": "study params (test)",
        "stages": ["process.py", "boom.py", "frame.py --dump", "micro.py"],
    },
}


def test_rod_route_is_available_for_rod_lattice_only():
    assert manufacturing.get("arc_lattice_rod_welding").status == "available"
    manufacturing.require("arc_lattice_rod_welding", "rod.lattice")
    with pytest.raises(ValueError):
        manufacturing.require("arc_lattice_rod_welding", "hx.two_fluid")


def test_rod_spec_needs_material_source():
    d = json.loads(json.dumps(ROD_SPEC))
    del d["requirements"]["material_source"]
    with pytest.raises(spec.SpecError):
        spec.parse(d, RodRequirements)


def test_arc1_first_stages_regenerate(tmp_path):
    pytest.importorskip("Pynite")
    work = tmp_path / "arc1"
    shutil.copytree(STUDY / "cad", work / "cad")
    shutil.copytree(STUDY / "scripts", work / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    for args in (["process.py"], ["boom.py"], ["frame.py", "--dump"]):
        r = subprocess.run([sys.executable, str(work / "scripts" / args[0]), *args[1:]], cwd=work,
                           capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stderr[-2000:]
    for name in ("process_rules.json", "boom_calibration.json", "arc1_frame.json"):
        assert (work / "out" / name).stat().st_size > 0


def test_rod_route_end_to_end_flags_overloaded_frame(tmp_path):
    pytest.importorskip("Pynite")
    sp = tmp_path / "spec.yaml"
    sp.write_text(yaml.safe_dump(ROD_SPEC))
    ledger = tmp_path / "ledger.jsonl"
    assert main(["check", str(sp)]) == 0
    # rev A params are over-utilised at the pickups (1.85 after the sign fix), so the route must fail.
    assert main(["rod", str(sp), "--out", str(tmp_path / "run"), "--ledger", str(ledger)]) == 1
    rec = evidence.Ledger(ledger).records()[-1]
    assert rec["kind"] == "aces.rod_run" and rec["company"] == "CPA"
    assert rec["observations"]["summary"]["frame"]["max_utilisation"] > 1.0
    assert any("utilisation" in p for p in rec["observations"]["problems"])
    assert all(s["exit"] == 0 for s in rec["observations"]["stages"])
    work = (tmp_path / "run" / "work").resolve()
    assert all(Path(a["path"]).resolve().is_relative_to(tmp_path.resolve()) for a in rec["artifacts"].values())
    assert (work / "cad" / "params.json").exists()  # the route ran on its own copy of the study
    assert rec["observations"]["summary"]["frame"]["utilisation_weakest_rod"] >= \
        rec["observations"]["summary"]["frame"]["max_utilisation"] * 0.999


@pytest.mark.parametrize("stages", [["../evil.py"], ["/tmp/evil.py"], ["sub/x.py"], ["x.sh"], [],
                                    ["frame.py ../../etc/passwd"], ["frame.py --out=/etc/x"],
                                    ["frame.py C:\\Windows"]])
def test_rod_stages_must_be_scripts_in_the_study(stages):
    d = json.loads(json.dumps(ROD_SPEC))
    d["requirements"]["stages"] = stages
    with pytest.raises(spec.SpecError):
        spec.parse(d, RodRequirements)


@pytest.mark.parametrize("params", ["../params.json", "/etc/params.json", "a\\b.json"])
def test_rod_params_must_be_a_file_in_cad(params):
    d = json.loads(json.dumps(ROD_SPEC))
    d["requirements"]["params"] = params
    with pytest.raises(spec.SpecError):
        spec.parse(d, RodRequirements)


def _req(**kw):
    d = json.loads(json.dumps(ROD_SPEC))["requirements"]
    d.update(kw)
    return RodRequirements.model_validate(d)


def test_rod_route_refuses_unknown_stage_script(tmp_path):
    from aces.domains.rod import route
    with pytest.raises(FileNotFoundError, match="not in"):
        route.run(_req(stages=["nope.py"]), tmp_path / "run", tmp_path, log=lambda *_: None)


def test_rod_route_needs_a_new_folder(tmp_path):
    from aces.domains.rod import route
    keep = tmp_path / "run" / "work" / "precious.txt"
    keep.parent.mkdir(parents=True)
    keep.write_text("user data")
    with pytest.raises(FileExistsError, match="already exists"):
        route.run(_req(stages=["process.py"]), tmp_path / "run", tmp_path, log=lambda *_: None)
    assert keep.read_text() == "user data"


# ----------------------------------------------------------------------------- committed test studies

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                           "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
                          cwd=repo, check=True, capture_output=True, text=True).stdout


def _committed_study(tmp_path: Path, files: dict[str, str], name: str = "s") -> tuple[Path, Path]:
    """A study committed in a temporary git checkout (test input). Returns (study folder, studies root)."""
    repo = tmp_path / "repo"
    study = repo / "studies" / name
    for rel, text in files.items():
        f = study / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    if not (repo / ".git").exists():
        _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "test study")
    return study, repo / "studies"


def test_rod_route_reports_timeouts_and_missing_checks(tmp_path):
    from aces.domains.rod import route
    study, root = _committed_study(tmp_path, {"cad/params.json": json.dumps({"rev": "test", "output": {"dir": "out"}}),
                                              "scripts/slow.py": "import time\ntime.sleep(30)\n"})
    req = _req(study_dir=str(study), stages=["slow.py"], stage_timeout_s=1)
    res = route.run(req, tmp_path / "run", tmp_path, log=lambda *_: None, studies_root=root)
    assert res["stages"][0]["exit"] is None
    assert any("timed out" in p for p in res["problems"])
    assert any("frame check not run" in p for p in res["problems"])
    assert any("rod check not run" in p for p in res["problems"])
    assert res["summary"]["study"] == {"path": "studies/s", "commit": _git(study, "rev-parse", "HEAD").strip()}
    with pytest.raises(FileExistsError):  # a run needs a new folder (audit M1)
        route.run(req, tmp_path / "run", tmp_path, log=lambda *_: None, studies_root=root)


def test_rod_route_refuses_an_output_inside_the_study(tmp_path):
    from aces.domains.rod import route
    study, root = _committed_study(tmp_path, {"cad/params.json": "{}", "scripts/a.py": ""})
    with pytest.raises(FileExistsError, match="overlaps the study"):
        route.run(_req(study_dir=str(study), stages=["a.py"]), study / "cad" / "run", tmp_path,
                  log=lambda *_: None, studies_root=root)
    assert sorted(p.name for p in (study / "cad").iterdir()) == ["params.json"]


def test_rod_route_reports_malformed_outputs_as_problems(tmp_path):
    from aces.domains.rod import route
    study, root = _committed_study(tmp_path, {
        "cad/params.json": json.dumps({"output": {"dir": "out"}}),
        "scripts/w.py": ("from pathlib import Path\nPath('out').mkdir(exist_ok=True)\n"
                         "Path('out/arc1_frame.json').write_text('{truncated')\n"
                         "Path('out/micro_stats.json').write_text('{\"capacity_N\": {\"min\": 0}}')\n"
                         "import sys\nsys.stdout.write('hello')\n")})
    res = route.run(_req(study_dir=str(study), stages=["w.py"]), tmp_path / "run", tmp_path, log=lambda *_: None,
                    studies_root=root)
    assert any("could not read arc1_frame.json" in p for p in res["problems"])
    assert any("frame check not run" in p for p in res["problems"])


def test_rod_stage_timeout_log_is_text(tmp_path):
    from aces.domains.rod import route
    study, root = _committed_study(tmp_path, {
        "cad/params.json": "{}", "scripts/s.py": "import sys, time\nprint('hello', flush=True)\ntime.sleep(30)\n"})
    route.run(_req(study_dir=str(study), stages=["s.py"], stage_timeout_s=2), tmp_path / "run", tmp_path,
              log=lambda *_: None, studies_root=root)
    assert "b'" not in (tmp_path / "run" / "stages.log").read_text()


# ----------------------------------------------------------------------------- H1: only committed studies run

EVIL = "open({marker!r}, 'w').write('ran')\n"


def test_rod_route_refuses_a_study_outside_studies(tmp_path):
    from aces.domains.rod import route
    marker = tmp_path / "PWNED"
    study = tmp_path / "planted"
    (study / "cad").mkdir(parents=True)
    (study / "scripts").mkdir()
    (study / "cad" / "params.json").write_text("{}")
    (study / "scripts" / "evil.py").write_text(EVIL.format(marker=str(marker)))
    with pytest.raises(route.StudyError, match="not under"):
        route.run(_req(study_dir=str(study), stages=["evil.py"]), tmp_path / "run", tmp_path, log=lambda *_: None)
    assert not marker.exists() and not (tmp_path / "run").exists()


@pytest.mark.parametrize("change", ["untracked", "modified", "staged"])
def test_rod_route_refuses_local_changes(tmp_path, change):
    from aces.domains.rod import route
    marker = tmp_path / "PWNED"
    study, root = _committed_study(tmp_path, {"cad/params.json": "{}", "scripts/a.py": "print('committed')\n"})
    target = study / "scripts" / ("evil.py" if change == "untracked" else "a.py")
    target.write_text(EVIL.format(marker=str(marker)))
    if change == "staged":
        _git(study, "add", "-A")
    stage = "evil.py" if change == "untracked" else "a.py"
    with pytest.raises(route.StudyError, match="local changes"):
        route.run(_req(study_dir=str(study), stages=[stage]), tmp_path / "run", tmp_path, log=lambda *_: None,
                  studies_root=root)
    assert not marker.exists() and not (tmp_path / "run").exists()


def test_rod_cli_refuses_a_planted_study_and_makes_no_folder(tmp_path, monkeypatch):
    """The audit's attack through the CLI: a study folder planted (not committed) under studies/."""
    marker = tmp_path / "PWNED"
    _, root = _committed_study(tmp_path, {"README.md": "test checkout\n"}, name="real")
    monkeypatch.setattr(evidence, "REPO_ROOT", root.parent)  # the checkout the CLI trusts, for this test only
    study = root / "planted"
    (study / "cad").mkdir(parents=True)
    (study / "scripts").mkdir()
    (study / "cad" / "params.json").write_text("{}")
    (study / "scripts" / "evil.py").write_text(EVIL.format(marker=str(marker)))
    d = json.loads(json.dumps(ROD_SPEC))
    d["requirements"].update(study_dir=str(study), stages=["evil.py"])
    sp = tmp_path / "spec.yaml"
    sp.write_text(yaml.safe_dump(d))
    assert main(["rod", str(sp), "--out", str(tmp_path / "run"), "--ledger", str(tmp_path / "l.jsonl")]) == 2
    assert not marker.exists() and not (tmp_path / "run").exists() and not (tmp_path / "l.jsonl").exists()


@pytest.mark.skipif(sys.platform.startswith("win"), reason="symbolic links in git need POSIX here")
def test_rod_route_refuses_links_in_the_study(tmp_path):
    from aces.domains.rod import route
    study, root = _committed_study(tmp_path, {"cad/params.json": "{}", "scripts/a.py": ""})
    (study / "scripts" / "link.py").symlink_to("/etc/passwd")
    _git(study, "add", "-A")
    _git(study, "commit", "-q", "-m", "a link (test input)")
    with pytest.raises(route.StudyError, match="link"):
        route.run(_req(study_dir=str(study), stages=["a.py"]), tmp_path / "run", tmp_path, log=lambda *_: None,
                  studies_root=root)


def test_rod_route_runs_the_committed_files_only(tmp_path):
    """Ignored files in the study (a planted sitecustomize.py, say) are not copied into the run."""
    from aces.domains.rod import route
    study, root = _committed_study(tmp_path, {
        ".gitignore": "*.local.py\n", "cad/params.json": "{}",
        "scripts/a.py": "import os\nprint(sorted(os.listdir('scripts')))\n"})
    (study / "scripts" / "sitecustomize.local.py").write_text("raise SystemExit('planted')\n")
    route.run(_req(study_dir=str(study), stages=["a.py"]), tmp_path / "run", tmp_path, log=lambda *_: None,
              studies_root=root)
    assert sorted(p.name for p in (tmp_path / "run" / "work" / "scripts").iterdir()) == ["a.py"]


def test_rod_stages_get_a_minimal_environment(tmp_path, monkeypatch):
    from aces.domains.rod import route
    monkeypatch.setenv("ACES_TEST_SERVER_SECRET", "test input, must not reach a stage")
    study, root = _committed_study(tmp_path, {
        "cad/params.json": "{}", "scripts/env.py": "import os\nprint(sorted(os.environ))\n"})
    route.run(_req(study_dir=str(study), stages=["env.py"]), tmp_path / "run", tmp_path, log=lambda *_: None,
              studies_root=root)
    log = (tmp_path / "run" / "stages.log").read_text()
    assert "ARC1_PARAMS" in log and "ACES_TEST_SERVER_SECRET" not in log


def test_planted_git_config_does_not_run_during_the_study_check(tmp_path):
    from aces.domains.rod import route
    marker = tmp_path / "PWNED"
    hook = tmp_path / "hook.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\ncat\n")
    hook.chmod(0o755)
    study, root = _committed_study(tmp_path, {".gitattributes": "* filter=evil\n", "cad/params.json": "{}",
                                              "scripts/a.py": ""})
    _git(study, "config", "core.fsmonitor", str(hook))
    _git(study, "config", "filter.evil.clean", str(hook))
    os.utime(study / "scripts" / "a.py")  # stat-dirty, so git would re-read the file through the filter
    route.run(_req(study_dir=str(study), stages=["a.py"]), tmp_path / "run", tmp_path, log=lambda *_: None,
              studies_root=root)
    assert not marker.exists()


@pytest.mark.parametrize("bad", [{"stage_timeout_s": 3601}, {"stages": ["process.py"] * 21}])
def test_rod_stage_limits(bad):
    d = json.loads(json.dumps(ROD_SPEC))
    d["requirements"].update(bad)
    with pytest.raises(spec.SpecError):
        spec.parse(d, RodRequirements)


def _rod_spec(tmp_path, params="params.json", stages=None):
    d = json.loads(json.dumps(ROD_SPEC))
    d["requirements"]["params"] = params
    d["requirements"]["stages"] = stages or ["process.py", "boom.py", "frame.py --dump", "micro.py", "rodcheck.py"]
    sp = tmp_path / f"spec_{params}.yaml"
    sp.write_text(yaml.safe_dump(d))
    return sp


def test_rod_by_rod_check_governs_rev_a(tmp_path):
    pytest.importorskip("Pynite")
    ledger = tmp_path / "ledger.jsonl"
    assert main(["rod", str(_rod_spec(tmp_path)), "--out", str(tmp_path / "a"), "--ledger", str(ledger)]) == 1
    obs = evidence.Ledger(ledger).records()[-1]["observations"]
    rc = obs["summary"]["rod_check"]
    # rev A: the bending-compression chords of the loaded lower pickup links fail; the lacing does not
    assert 1.5 < rc["max_utilisation"] < 1.9
    assert rc["by_kind"]["diagonal"]["over"] == 0 and rc["by_kind"]["batten"]["over"] == 0
    assert any(p.startswith("rod-by-rod utilisation") and "pickup" in p for p in obs["problems"])
    assert not any("rescaled to the weakest rod" in p for p in obs["problems"])  # the bound is replaced


def test_rev_b_passes_the_rod_route(tmp_path):
    pytest.importorskip("Pynite")
    ledger = tmp_path / "ledger.jsonl"
    rc_ = main(["rod", str(_rod_spec(tmp_path, "params_revB.json")), "--out", str(tmp_path / "b"),
                "--ledger", str(ledger)])
    obs = evidence.Ledger(ledger).records()[-1]["observations"]
    assert rc_ == 0, obs["problems"]
    assert obs["summary"]["params_rev"] == "B"
    assert obs["summary"]["rod_check"]["max_utilisation"] <= 1.0
    assert obs["summary"]["frame"]["max_utilisation"] <= 1.0


def test_rev_a_geometry_is_unchanged_by_the_frame_overrides():
    """frame.py gained optional overrides for rev B; rev A must build the same frame as before."""
    out = subprocess.run([sys.executable, "-c",
                          "import frame; f, h = frame.build(); "
                          "print(len(f.nodes), len(f.members), sum(1 for m in f.members if m[2] == 'pickup'))"],
                         cwd=STUDY / "scripts", capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-1000:]
    assert out.stdout.split() == ["107", "356", "52"]
