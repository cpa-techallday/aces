"""Tests for aces.compliance: collection, redaction, validation, index and the CLI (audit C1-C6).

All inputs are test inputs: repo names, alert payloads, ledgers and evidence files are made up here. The gh CLI
is never run: ``aces.proc.run`` is replaced by a fake that answers the way gh would.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from aces import cli, compliance, evidence, proc, safeio, spec

posix_only = pytest.mark.skipif(os.name != "posix", reason="symlink semantics checked on POSIX")

TODAY = date(2026, 10, 7)  # a fixed "today" so validation tests don't depend on the clock (test input)
GOOD = b'{"test_input": true}\n'
SECRET = "ghp_TESTINPUTnotARealToken0000000000000000"  # shaped like a GitHub token; test input only


class FakeGh:
    """Stands in for ``aces.proc.run``: records every call and answers from a table (test input)."""

    def __init__(self, answers: dict[str, str] | None = None, fail: tuple[str, ...] = ()):
        self.calls: list[tuple[list[str], dict]] = []
        self.answers = answers or {}
        self.fail = fail

    def __call__(self, cmd, **kw):
        assert not isinstance(cmd, (str, bytes)), "commands must be argument lists"
        self.calls.append((list(cmd), kw))
        joined = " ".join(cmd)
        for key in self.fail:
            if key in joined:
                return subprocess.CompletedProcess(cmd, 1, "", f"HTTP 404 for {key} (test input)")
        for key, out in self.answers.items():
            if key in joined:
                return subprocess.CompletedProcess(cmd, 0, out, "")
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"test_input": True, "args": list(cmd[1:])}), "")


@pytest.fixture
def fake_gh(monkeypatch):
    fake = FakeGh()
    monkeypatch.setattr(proc, "run", fake)
    return fake


def ev_name(control: str, kind: str, day: date, ext: str = "json") -> str:
    return f"{control}_{kind}_{day.isoformat()}.{ext}"


def make_run(root: Path, run_date: date, files: dict[str, bytes], *, name: str | None = None) -> Path:
    """A run folder with a manifest listing ``files`` and their sha256, as ``collect`` writes it (test input)."""
    rd = root / (name or f"run-{run_date:%Y%m%d}T120000Z")
    rd.mkdir(parents=True)
    results = []
    for fname, data in files.items():
        (rd / fname).write_bytes(data)
        m = compliance.NAME_RE.fullmatch(fname)
        results.append({"file": fname, "control": m.group("control") if m else "-", "evidence_kind": "test",
                        "name": m.group("type") if m else fname, "ok": True, "optional": False,
                        "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "error": None})
    manifest = {"kind": "aces.compliance_collection", "run_date": run_date.isoformat(), "results": results}
    (rd / "manifest.json").write_text(json.dumps(manifest))
    return rd


def set_manifest_sha(rd: Path, fname: str, data: bytes) -> None:
    """Forge the manifest entry for ``fname`` to match ``data`` (simulates a tampered package)."""
    m = json.loads((rd / "manifest.json").read_text())
    for r in m["results"]:
        if r["file"] == fname:
            r["sha256"] = hashlib.sha256(data).hexdigest()
    (rd / "manifest.json").write_text(json.dumps(m))


def findings_for(findings, control):
    return [f for f in findings if f.control == control]


def make_ledger(path: Path, marker: str = "test input") -> dict:
    rec = evidence.make_record(
        kind="test.record",
        spec={"company": spec.COMPANY, "project": "test", "owner": "test", "rd_activity": "test input",
              "hypothesis": marker},
        model_id="test", model_version="0", inputs={}, observations={}, calibration={})
    return evidence.Ledger(path).append(rec)


def all_bytes_under(root: Path) -> bytes:
    return b"".join(p.read_bytes() for p in root.rglob("*") if p.is_file() and not p.is_symlink())


# ----------------------------------------------------------------------------- C1: no shell, validated inputs


@pytest.mark.parametrize("repo", ["x/y; touch PWNED", "x/y && id", "x/$(id)", "x/y\n", "x", "x/y/z", "x y/z",
                                  "../y", "x/..", "-x/y", ""])
def test_repo_must_be_owner_slash_name(repo):
    with pytest.raises(ValueError):
        compliance.github_collectors(repo)


def test_valid_repo_names_are_accepted():
    for repo in ("steeldragon666/aces", "a.b/c_d-e", "Org-1/.github"):
        assert compliance.github_collectors(repo)


def test_injected_repo_is_refused_and_nothing_runs(tmp_path, monkeypatch, fake_gh):
    """Audit C1 reproduction: ``--repo "x/y; touch PWNED"`` ran ``touch``. Now it is refused before any run."""
    monkeypatch.chdir(tmp_path)
    ledger = tmp_path / "ledger.jsonl"
    rc = cli.main(["compliance", "collect", str(tmp_path / "ev"), "--repo", "x/y; touch PWNED",
                   "--ledger", str(ledger)])
    assert rc == 2
    assert fake_gh.calls == []
    assert not (tmp_path / "PWNED").exists()
    assert not (tmp_path / "ev").exists() and not ledger.exists()


def test_injected_ledger_is_refused_and_nothing_runs(tmp_path, monkeypatch, fake_gh):
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["compliance", "collect", str(tmp_path / "ev"), "--with-aces",
                   "--ledger", "x.jsonl; touch PWNED"])
    assert rc == 2
    assert fake_gh.calls == [] and not (tmp_path / "PWNED").exists() and not (tmp_path / "ev").exists()


def test_ledger_must_be_an_existing_regular_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        compliance.aces_collectors(tmp_path / "missing.jsonl")
    (tmp_path / "adir").mkdir()
    with pytest.raises(ValueError):
        compliance.aces_collectors(tmp_path / "adir")


@posix_only
def test_linked_ledger_is_refused(tmp_path, fake_gh):
    real = tmp_path / "real.jsonl"
    make_ledger(real)
    (tmp_path / "link.jsonl").symlink_to(real)
    with pytest.raises(safeio.UnsafePathError):
        compliance.aces_collectors(tmp_path / "link.jsonl")
    before = real.read_bytes()
    rc = cli.main(["compliance", "collect", str(tmp_path / "ev"), "--repo", "owner/name",
                   "--ledger", str(tmp_path / "link.jsonl")])
    assert rc == 2 and fake_gh.calls == [] and real.read_bytes() == before


def test_collectors_are_argument_lists_with_a_minimal_environment(tmp_path, monkeypatch, fake_gh):
    monkeypatch.setenv("ACES_TEST_UNRELATED_SECRET", "test-input")
    specs = compliance.github_collectors("owner/name")
    for s in specs:
        assert isinstance(s.command, tuple) and all(isinstance(a, str) for a in s.command)
        assert Path(s.command[0]).name.lower() in ("gh", "gh.exe")
        assert any("owner/name" in a for a in s.command)
        assert not set(s.command) & {"delete", "edit", "close", "merge", "create", "-X", "--method"}
    compliance.collect(tmp_path / "ev", specs)
    assert len(fake_gh.calls) == len(specs)
    for cmd, kw in fake_gh.calls:
        assert isinstance(cmd, list) and "shell" not in kw
        assert "ACES_TEST_UNRELATED_SECRET" not in (kw.get("env") or {})
        assert kw.get("env") is not None  # never the full inherited environment


def test_shell_metacharacters_are_plain_arguments(tmp_path, monkeypatch):
    """Real subprocess (no fake): an argument holding ``; touch PWNED`` is passed through, never run."""
    monkeypatch.chdir(tmp_path)
    c = compliance.CollectorSpec("A.8.32", "change_record", "argv-echo",
                                 (sys.executable, "-c", "import json, sys; print(json.dumps(sys.argv[1:]))",
                                  "x; touch PWNED"), platform="test")
    m = compliance.collect(tmp_path / "ev", [c])
    r = m["results"][0]
    assert r["ok"], r
    assert json.loads((Path(m["evidence_dir"]) / r["file"]).read_text()) == ["x; touch PWNED"]
    assert not (tmp_path / "PWNED").exists()


def test_a_shell_string_command_is_refused():
    with pytest.raises(TypeError):
        compliance.CollectorSpec("A.8.32", "change_record", "x", "gh api repos/a/b")


# ----------------------------------------------------------------------------- C2: secrets never reach disk


def test_secret_scanning_hides_and_drops_secrets(tmp_path, monkeypatch):
    alerts = [{"number": 1, "state": "open", "secret_type": "github_personal_access_token", "secret": SECRET,
               "locations": [{"details": {"secret": SECRET, "path": "x.py"}}]}]  # test input
    fake = FakeGh(answers={"secret-scanning": json.dumps(alerts)})
    monkeypatch.setattr(proc, "run", fake)
    ledger = tmp_path / "ledger.jsonl"
    rc = cli.main(["compliance", "collect", str(tmp_path / "ev"), "--repo", "owner/name", "--ledger", str(ledger)])
    assert rc == 0
    cmd = next(c for c, _ in fake.calls if any("secret-scanning" in a for a in c))
    assert any("hide_secret=true" in a for a in cmd)
    assert cli.main(["compliance", "index", str(tmp_path / "ev")]) == 0
    assert SECRET.encode() not in all_bytes_under(tmp_path)
    files = list((tmp_path / "ev").glob("run-*/A.5.17_github-secret-scanning_*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data[0]["secret_type"] == "github_personal_access_token"
    assert "secret" not in data[0] and "secret" not in data[0]["locations"][0]["details"]


def test_unparseable_output_is_not_written_when_redaction_is_required(tmp_path, monkeypatch):
    monkeypatch.setattr(proc, "run", FakeGh(answers={"secret-scanning": "not json " + SECRET}))
    m = compliance.collect(tmp_path / "ev", compliance.github_collectors("owner/name"))
    r = next(r for r in m["results"] if r["name"] == "github-secret-scanning")
    assert not r["ok"] and r["file"] is None and "JSON" in r["error"]
    assert SECRET.encode() not in all_bytes_under(tmp_path)


# ----------------------------------------------------------------------------- C3: content, hash, date, place


def test_empty_future_dated_files_are_not_evidence(tmp_path):
    """Audit C3 reproduction: empty files named like future-dated logging evidence counted as complete."""
    (tmp_path / "A.8.15_log_2027-01-01.json").write_bytes(b"")
    (tmp_path / "A.8.15_log-2_2027-01-01.json").write_bytes(b"")
    findings = compliance.validate(tmp_path, today=TODAY)
    a815 = findings_for(findings, "A.8.15")
    assert not any(f.level == "ok" for f in a815), a815
    assert any(f.level == "gap" and "no evidence" in f.detail for f in a815)
    assert any(f.level == "invalid" and "A.8.15_log_2027-01-01.json" in f.detail for f in a815)


def _good_logging_run(root: Path, day: date = TODAY) -> tuple[Path, str, str]:
    a, b = ev_name("A.8.15", "aces-ledger-head", day), ev_name("A.8.15", "aces-ledger-verify", day, "txt")
    rd = make_run(root, day, {a: GOOD, b: b"OK: 3 records, chain intact, head 0123456789ab\n"})
    return rd, a, b


def test_a_verified_run_counts_as_evidence(tmp_path):
    _good_logging_run(tmp_path)
    a815 = findings_for(compliance.validate(tmp_path, today=TODAY), "A.8.15")
    assert [f.level for f in a815] == ["ok"], a815


def _tamper(kind: str, root: Path, rd: Path, a: str) -> str:
    """Apply one kind of tampering to evidence file ``a`` in run ``rd``; return the expected finding text."""
    p = rd / a
    if kind == "empty":
        p.write_bytes(b"")
        set_manifest_sha(rd, a, b"")
        return "empty"
    if kind == "not_json":
        p.write_bytes(b"not json at all")
        set_manifest_sha(rd, a, b"not json at all")
        return "not valid JSON"
    if kind == "changed":
        p.write_bytes(b'{"test_input": "edited"}')
        return "sha256"
    if kind == "unlisted":
        m = json.loads((rd / "manifest.json").read_text())
        m["results"] = [r for r in m["results"] if r["file"] != a]
        (rd / "manifest.json").write_text(json.dumps(m))
        return "not listed"
    if kind == "missing_manifest":
        (rd / "manifest.json").unlink()
        return "manifest"
    if kind == "link":
        outside = root.parent / "outside.json"
        outside.write_bytes(GOOD)
        p.unlink()
        p.symlink_to(outside)
        return "link"
    if kind == "traversal":
        outside = root.parent / a
        outside.write_bytes(GOOD)
        p.unlink()
        m = json.loads((rd / "manifest.json").read_text())
        for r in m["results"]:
            if r["file"] == a:
                r["file"] = f"../../{a}"
        (rd / "manifest.json").write_text(json.dumps(m))
        return "plain"
    raise AssertionError(kind)


@pytest.mark.parametrize("kind", ["empty", "not_json", "changed", "unlisted", "missing_manifest",
                                  pytest.param("link", marks=posix_only), "traversal"])
def test_tampered_evidence_is_not_counted(tmp_path, kind):
    root = tmp_path / "pkg"
    rd, a, _ = _good_logging_run(root)
    expect = _tamper(kind, root, rd, a)
    findings = compliance.validate(root, today=TODAY)
    a815 = findings_for(findings, "A.8.15") + findings_for(findings, "-")
    assert any(f.level == "invalid" and expect in f.detail for f in a815), a815
    # with one of its two files rejected, the critical logging control is no longer complete
    assert any(f.level == "gap" for f in findings_for(findings, "A.8.15")), a815


def test_future_dated_run_is_not_counted(tmp_path):
    future = TODAY + timedelta(days=30)
    _good_logging_run(tmp_path, day=future)
    a815 = findings_for(compliance.validate(tmp_path, today=TODAY), "A.8.15")
    assert not any(f.level == "ok" for f in a815)
    assert any(f.level == "invalid" and "after today" in f.detail for f in a815)


def test_file_date_must_match_the_run_date(tmp_path):
    day = TODAY - timedelta(days=1)
    make_run(tmp_path, TODAY, {ev_name("A.8.15", "aces-ledger-head", day): GOOD})
    a815 = findings_for(compliance.validate(tmp_path, today=TODAY), "A.8.15")
    assert any(f.level == "invalid" and "run" in f.detail for f in a815), a815


@posix_only
def test_linked_run_folder_is_not_followed(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    _good_logging_run(elsewhere)
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "run-20261007T120000Z").symlink_to(next(elsewhere.iterdir()))
    findings = compliance.validate(root, today=TODAY)
    assert not any(f.level == "ok" for f in findings_for(findings, "A.8.15"))
    assert any(f.level == "invalid" and "link" in f.detail for f in findings)


def test_index_lists_only_verified_evidence(tmp_path):
    rd, a, b = _good_logging_run(tmp_path)
    (rd / a).write_bytes(b"")  # emptied after collection
    text = compliance.index(tmp_path)
    table = text.split("## Validation")[0]
    assert b in table and hashlib.sha256((rd / b).read_bytes()).hexdigest()[:12] in table
    assert a not in table
    assert a in text.split("## Validation")[1]


# ----------------------------------------------------------------------------- C4: new folder, no overwrite


def test_each_collection_gets_a_new_folder_and_the_manifest_is_written_once(tmp_path, fake_gh):
    specs = compliance.github_collectors("owner/name")
    m1 = compliance.collect(tmp_path, specs)
    first = Path(m1["manifest_file"]).read_bytes()
    m2 = compliance.collect(tmp_path, specs)
    assert m1["run_dir"] != m2["run_dir"]
    assert Path(m1["manifest_file"]).read_bytes() == first
    for m in (m1, m2):
        assert Path(m["manifest_file"]).name == "manifest.json"
        for r in m["results"]:
            assert compliance.NAME_RE.fullmatch(r["file"]), r["file"]
            assert (Path(m["evidence_dir"]) / r["file"]).is_file()
    with pytest.raises(FileExistsError):
        safeio.OutDir(m1["evidence_dir"]).write_text("manifest.json", "{}")


@posix_only
def test_collect_refuses_a_linked_evidence_folder(tmp_path, fake_gh):
    target = tmp_path / "target"
    target.mkdir()
    (tmp_path / "ev").symlink_to(target)
    with pytest.raises(safeio.UnsafePathError):
        compliance.collect(tmp_path / "ev", compliance.github_collectors("owner/name"))
    assert list(target.iterdir()) == []


@posix_only
def test_collect_never_writes_through_a_planted_run_folder(tmp_path, monkeypatch, fake_gh):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "ev"
    root.mkdir()
    monkeypatch.setattr(compliance, "_run_name", lambda now: "run-20261007T120000Z")
    (root / "run-20261007T120000Z").symlink_to(outside)
    m = compliance.collect(root, compliance.github_collectors("owner/name"))
    assert m["run_dir"] == "run-20261007T120000Z-2"
    assert list(outside.iterdir()) == []


@posix_only
def test_index_refuses_a_planted_link(tmp_path, capsys):
    victim = tmp_path / "victim.md"
    victim.write_text("keep")
    root = tmp_path / "ev"
    root.mkdir()
    (root / "INDEX.md").symlink_to(victim)
    assert cli.main(["compliance", "index", str(root)]) == 2
    assert victim.read_text() == "keep"


def test_dry_run_writes_nothing(tmp_path, fake_gh):
    m = compliance.collect(tmp_path / "ev", compliance.github_collectors("owner/name"), dry_run=True)
    assert all(r["ok"] for r in m["results"]) and m["manifest_file"] is None
    assert not (tmp_path / "ev").exists() and fake_gh.calls == []


# ----------------------------------------------------------------------------- C5: timeouts everywhere


def test_every_command_gets_a_timeout(tmp_path, fake_gh):
    compliance.collect(tmp_path / "ev", compliance.github_collectors("owner/name"), timeout_s=45)
    assert fake_gh.calls and all(kw.get("timeout") == 45 for _, kw in fake_gh.calls)
    for bad in (0, -1, None):
        with pytest.raises(ValueError):
            compliance.collect(tmp_path / "ev2", compliance.github_collectors("owner/name"), timeout_s=bad)


def test_a_hung_collector_times_out_and_is_recorded(tmp_path):
    c = compliance.CollectorSpec("A.8.32", "change_record", "hangs",
                                 (sys.executable, "-c", "import time; time.sleep(30)"), platform="test")
    t0 = time.monotonic()
    m = compliance.collect(tmp_path / "ev", [c], timeout_s=0.5)
    assert time.monotonic() - t0 < 10
    r = m["results"][0]
    assert not r["ok"] and "timed out" in r["error"]


# ----------------------------------------------------------------------------- C6: mapping, optional, ledger


# ISO/IEC 27001:2022, Annex A (Table A.1) control titles, checked by hand against the standard.
ANNEX_A_2022 = {
    "A.5.9": "Inventory of information and other associated assets",
    "A.5.15": "Access control",
    "A.5.16": "Identity management",
    "A.5.17": "Authentication information",
    "A.5.18": "Access rights",
    "A.8.4": "Access to source code",
    "A.8.8": "Management of technical vulnerabilities",
    "A.8.12": "Data leakage prevention",
    "A.8.13": "Information backup",
    "A.8.15": "Logging",
    "A.8.16": "Monitoring activities",
    "A.8.24": "Use of cryptography",
    "A.8.32": "Change management",
}


def test_control_titles_match_iso_27001_2022():
    assert "A.8.4" in compliance.CONTROLS and "A.5.18" in compliance.CONTROLS
    for cid, c in compliance.CONTROLS.items():
        assert c.id == cid and ANNEX_A_2022.get(cid) == c.title, cid


def test_collectors_map_to_the_right_controls(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    make_ledger(ledger)
    specs = compliance.github_collectors("owner/name") + compliance.aces_collectors(ledger)
    got = {s.name: s.control for s in specs}
    assert got == {
        "github-repo-settings": "A.8.4",      # visibility, forking, security features: who can read the code
        "github-branch-protection": "A.8.4",  # who may write to main
        "github-merged-prs": "A.8.32",
        "github-recent-commits": "A.8.32",
        "github-dependabot-alerts": "A.8.8",
        "github-secret-scanning": "A.5.17",   # leaked credentials are authentication information
        "github-collaborators": "A.5.18",     # who holds which access right
        "aces-ledger-verify": "A.8.15",
        "aces-ledger-head": "A.8.15",
    }
    for s in specs:
        assert s.evidence_kind in compliance.CONTROLS[s.control].evidence_kinds, s.name


def test_optional_failure_warns_and_required_failure_fails(tmp_path, monkeypatch, capsys):
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(proc, "run", FakeGh(fail=("branches/main/protection",)))
    rc = cli.main(["compliance", "collect", str(tmp_path / "ev"), "--repo", "owner/name", "--ledger", str(ledger)])
    out = capsys.readouterr().out
    assert rc == 0 and "WARN" in out and "FAILED" not in out
    monkeypatch.setattr(proc, "run", FakeGh(fail=("branches/main/protection", "collaborators")))
    rc = cli.main(["compliance", "collect", str(tmp_path / "ev"), "--repo", "owner/name", "--ledger", str(ledger)])
    out = capsys.readouterr().out
    assert rc == 1 and "FAILED" in out


def test_optional_flag_in_the_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(proc, "run", FakeGh(fail=("dependabot",)))
    m = compliance.collect(tmp_path / "a", compliance.github_collectors("owner/name"))
    assert m["ok"] and m["optional_failed"] == ["github-dependabot-alerts"] and m["required_failed"] == []
    monkeypatch.setattr(proc, "run", FakeGh(fail=("pr list",)))
    m = compliance.collect(tmp_path / "b", compliance.github_collectors("owner/name"))
    assert not m["ok"] and m["required_failed"] == ["github-merged-prs"]
    saved = json.loads(Path(m["manifest_file"]).read_text())
    assert saved["ok"] is False and saved["required_failed"] == ["github-merged-prs"]


def test_ledger_contents_stay_out_of_the_package(tmp_path):
    marker = "TEST-INPUT-HYPOTHESIS-7f3a91"
    ledger = tmp_path / "ledger.jsonl"
    make_ledger(ledger, "first " + marker)
    head = make_ledger(ledger, "second " + marker)
    m = compliance.collect(tmp_path / "ev", compliance.aces_collectors(ledger))
    assert m["ok"], m["results"]
    run = Path(m["evidence_dir"])
    assert marker.encode() not in all_bytes_under(tmp_path / "ev")
    head_file = next(run.glob("A.8.15_aces-ledger-head_*.json"))
    data = json.loads(head_file.read_text())
    assert data["record_count"] == 2 and data["head_hash"] == head["record_hash"] and data["verified"] is True
    assert next(run.glob("A.8.15_aces-ledger-verify_*.txt")).read_text().startswith("OK: 2 records")
    assert not any("tail" in s.name for s in compliance.aces_collectors(ledger))


def test_broken_ledger_fails_the_logging_collectors(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    make_ledger(ledger)
    ledger.write_text(ledger.read_text().replace("test input", "edited"))
    m = compliance.collect(tmp_path / "ev", compliance.aces_collectors(ledger))
    assert not m["ok"] and set(m["required_failed"]) == {"aces-ledger-verify", "aces-ledger-head"}


# ----------------------------------------------------------------------------- validation and index basics


def test_validate_flags_stale_and_missing(tmp_path):
    old = TODAY - timedelta(days=45)  # vulnerability scans: 30-day limit
    make_run(tmp_path, old, {ev_name("A.8.8", "export", old): GOOD})
    findings = compliance.validate(tmp_path, today=TODAY)
    assert any(f.level == "stale" and f.control == "A.8.8" for f in findings)
    missing = {f.control for f in findings if f.level == "gap"}
    assert "A.5.15" in missing and "A.8.13" in missing


def test_validate_fresh_critical_passes(tmp_path):
    day = TODAY - timedelta(days=5)
    files = {}
    for c in compliance.CONTROLS.values():
        for i, kind in enumerate(c.evidence_kinds):
            files[ev_name(c.id, f"{kind.replace('_', '-')}-{i}", day)] = GOOD
        if c.tier == "critical" and len(c.evidence_kinds) < 2:
            files[ev_name(c.id, "corroborating", day)] = GOOD  # critical controls need 2 forms
    make_run(tmp_path, day, files)
    findings = compliance.validate(tmp_path, today=TODAY)
    assert all(f.level == "ok" for f in findings), [f for f in findings if f.level != "ok"]


def test_copies_of_one_evidence_type_are_not_two_forms(tmp_path):
    """Two runs of the same collector are one form of evidence, not two."""
    for d in (TODAY - timedelta(days=2), TODAY - timedelta(days=1)):
        make_run(tmp_path, d, {ev_name("A.5.18", "github-collaborators", d): GOOD})
    a518 = findings_for(compliance.validate(tmp_path, today=TODAY), "A.5.18")
    assert any(f.level == "gap" and "2 forms" in f.detail for f in a518), a518


def test_validate_flags_bad_names(tmp_path):
    (tmp_path / "random-screenshot.png").write_text("x")
    findings = compliance.validate(tmp_path, today=TODAY)
    assert any(f.level == "naming" for f in findings)


def test_index_maps_controls_and_reports_gaps(tmp_path):
    _good_logging_run(tmp_path, day=TODAY)
    text = compliance.index(tmp_path)
    assert "| A.8.15 | Logging |" in text
    assert "## Validation" in text and "gap" in text  # other controls missing


def test_collect_writes_named_hashed_files_via_the_cli(tmp_path, fake_gh, capsys):
    ledger = tmp_path / "ledger.jsonl"
    make_ledger(ledger)
    assert cli.main(["compliance", "collect", str(tmp_path / "ev"), "--repo", "owner/name", "--with-aces",
                     "--ledger", str(ledger)]) == 0
    runs = list((tmp_path / "ev").glob("run-*"))
    assert len(runs) == 1
    m = json.loads((runs[0] / "manifest.json").read_text())
    for r in m["results"]:
        data = (runs[0] / r["file"]).read_bytes()
        assert r["ok"] and hashlib.sha256(data).hexdigest() == r["sha256"]
    rec = evidence.Ledger(ledger).records()[-1]
    assert rec["kind"] == "aces.compliance_collection"
    assert rec["artifacts"]["manifest"]["sha256"] == hashlib.sha256((runs[0] / "manifest.json").read_bytes()).hexdigest()
    assert evidence.Ledger(ledger).verify()[0]
