"""Tests for aces.compliance: naming, freshness, validation, index, collect plumbing."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from aces import compliance


def fake_collector(control="A.8.32", kind="change_record", name="fake-export",
                   output="ok", fail=False):
    cmd = "exit 1" if fail else f"python -c \"print('{output}')\""
    return compliance.CollectorSpec(control, kind, name, cmd)


def test_collect_writes_named_hashed_files(tmp_path):
    m = compliance.collect(tmp_path, [fake_collector()])
    r = m["results"][0]
    assert r["ok"] and r["sha256"]
    f = tmp_path / r["file"]
    assert f.exists() and f.read_text().strip() == 'ok'
    assert compliance.NAME_RE.match(f.name), f"bad evidence filename: {f.name}"
    assert list(tmp_path.glob("manifest_*.json")), "manifest not written"


def test_collect_never_overwrites(tmp_path):
    compliance.collect(tmp_path, [fake_collector()])
    m2 = compliance.collect(tmp_path, [fake_collector()])
    assert "_2." in m2["results"][0]["file"]


def test_failed_collector_is_recorded_not_silent(tmp_path):
    m = compliance.collect(tmp_path, [fake_collector(fail=True)])
    r = m["results"][0]
    assert not r["ok"] and r["error"] and r["file"] is None


def test_dry_run_writes_nothing(tmp_path):
    m = compliance.collect(tmp_path, [fake_collector()], dry_run=True)
    assert m["results"][0]["ok"]
    evidence = [p for p in tmp_path.iterdir() if not p.name.startswith("manifest_")]
    assert evidence == []


def _make_evidence(root: compliance.Path, control: str, age_days: int, name="export"):
    day = date.today() - timedelta(days=age_days)
    name = name.replace("_", "-")  # evidence names use hyphens per the naming convention
    p = root / f"{control}_{name}_{day.isoformat()}.json"
    p.write_text("{}")
    return p


def test_validate_flags_stale_and_missing(tmp_path):
    _make_evidence(tmp_path, "A.8.8", 45)  # vulnerability scans: 30-day limit
    findings = compliance.validate(tmp_path)
    stale = [f for f in findings if f.level == "stale"]
    gaps = [f for f in findings if f.level == "gap"]
    assert any(f.control == "A.8.8" for f in stale)
    missing = {f.control for f in gaps}
    assert "A.5.15" in missing and "A.8.13" in missing


def test_validate_fresh_critical_passes(tmp_path):
    for c in compliance.CONTROLS.values():
        for i, kind in enumerate(c.evidence_kinds):
            _make_evidence(tmp_path, c.id, 5, name=f"{kind}-{i}")
        if c.tier == "critical" and len(c.evidence_kinds) < 2:
            _make_evidence(tmp_path, c.id, 5, name="corroborating")  # skill: critical needs 2 forms
    findings = compliance.validate(tmp_path)
    assert all(f.level == "ok" for f in findings), [f for f in findings if f.level != "ok"]


def test_validate_flags_bad_names(tmp_path):
    (tmp_path / "random-screenshot.png").write_text("x")
    findings = compliance.validate(tmp_path)
    assert any(f.level == "naming" for f in findings)


def test_index_maps_controls_and_reports_gaps(tmp_path):
    _make_evidence(tmp_path, "A.8.15", 3, name="aces-ledger-verify")
    text = compliance.index(tmp_path)
    assert "| A.8.15 | Logging |" in text
    assert "## Validation" in text and "gap" in text  # other controls missing


def test_github_collectors_are_read_only_and_repo_scoped():
    import re
    specs = compliance.github_collectors("steeldragon666/aces")
    assert specs, "expected github collectors"
    for s in specs:
        assert "steeldragon666/aces" in s.command
        assert "gh" in s.command.split()[0] or "GitHub CLI" in s.command  # gh on PATH or quoted path
        assert not any(re.search(rf"\b{w}\b", s.command)
                       for w in ("delete", "edit", "close", "merge", "create"))


def test_aces_collectors_reference_ledger():
    specs = compliance.aces_collectors("out/evidence/ledger.jsonl")
    assert any("ledger verify" in s.command for s in specs)
