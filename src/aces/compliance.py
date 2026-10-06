"""ISO 27001:2022 evidence collection, alongside ACES's evidence architecture.

ACES already keeps a hash-chained evidence ledger for engineering runs; an ISO
or SOC 2 auditor wants the same properties (timestamped, tamper-evident,
reproducible) for *organisational* controls.  This module collects that
evidence API-first, names it by control (``{control}_{type}_{YYYY-MM-DD}``),
validates freshness and coverage, generates an auditor index, and records each
collection run in the ACES ledger so the evidence package itself is
hash-chained.

Control guidance follows the iso-27001-evidence-collection procedure (MIT,
open-agreements; attribution: Internal ISO Audit / Hazel Castro).  ISO control
text is cited by section ID only.

Collectors are read-only command specs run with existing local credentials
(``gh``, cloud CLIs, or local probes).  Nothing is sent anywhere; evidence
stays in the local evidence directory.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------------- controls
#
# ISO 27001:2022 Annex A subset relevant to ACES as it stands today: a small
# engineering org whose crown jewels are code, simulation data and test
# records.  Tiers: critical (audit blockers), relevant (expected), checkbox
# (policies/attestations).  Freshness from the collection procedure's table.

FRESHNESS_DAYS = {
    "access_list": 90,
    "vulnerability_scan": 30,
    "config_export": 90,
    "training_record": 365,
    "penetration_test": 365,
    "policy": 365,
    "incident_record": None,   # whole audit period
    "risk_assessment": 365,
    "log_export": 90,
    "backup_test": 90,
    "asset_inventory": 90,
    "change_record": 90,
}


@dataclass(frozen=True)
class Control:
    id: str              # Annex A section id, e.g. "A.5.15"
    title: str
    tier: str            # critical | relevant | checkbox
    evidence_kinds: tuple[str, ...]  # keys into FRESHNESS_DAYS


CONTROLS: dict[str, Control] = {
    c.id: c
    for c in (
        Control("A.5.9", "Inventory of information and other associated assets", "relevant",
                ("asset_inventory",)),
        Control("A.5.15", "Access control", "critical", ("access_list", "config_export")),
        Control("A.5.16", "Identity management", "critical", ("access_list",)),
        Control("A.5.17", "Authentication information", "critical", ("config_export",)),
        Control("A.8.8", "Management of technical vulnerabilities", "critical",
                ("vulnerability_scan",)),
        Control("A.8.13", "Information backup", "critical", ("backup_test", "config_export")),
        Control("A.8.15", "Logging", "critical", ("log_export", "config_export")),
        Control("A.8.16", "Monitoring activities", "relevant", ("log_export",)),
        Control("A.8.24", "Use of cryptography", "relevant", ("config_export",)),
        Control("A.8.32", "Change management", "critical", ("change_record",)),
    )
}


# ----------------------------------------------------------------------------- collectors


@dataclass(frozen=True)
class CollectorSpec:
    """One read-only collection command mapped to a control.

    ``command`` is a shell string run locally with existing credentials;
    ``{repo}``/``{org}`` placeholders are filled at collect time.  The output
    (stdout) becomes the evidence file.  A collector that fails is recorded as
    failed in the manifest — never silently skipped.
    """
    control: str
    evidence_kind: str   # key into FRESHNESS_DAYS
    name: str            # becomes {control}_{name}_{date}.{ext}
    command: str
    ext: str = "json"
    platform: str = "github"
    optional: bool = False  # optional collectors may fail without failing the run


def _gh() -> str:
    """The gh executable, on PATH or at its standard Windows install location."""
    gh = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"
    return f'"{gh}"'


def github_collectors(repo: str) -> list[CollectorSpec]:
    """Repo-level GitHub evidence (the ACES repo is personal-account, so no org calls).

    Raw API JSON is captured unfiltered: a full export is stronger evidence
    than a jq projection, and it dodges shell-quoting issues on Windows cmd.
    """
    gh = _gh()
    return [
        CollectorSpec("A.8.32", "change_record", "github-repo-settings",
                      f"{gh} api repos/{repo}"),
        CollectorSpec("A.8.32", "change_record", "github-branch-protection",
                      f"{gh} api repos/{repo}/branches/main/protection", optional=True),
        CollectorSpec("A.8.32", "change_record", "github-merged-prs",
                      f"{gh} pr list --repo {repo} --state merged --limit 50 "
                      "--json number,title,author,mergedAt,mergedBy"),
        CollectorSpec("A.8.32", "change_record", "github-recent-commits",
                      f"{gh} api repos/{repo}/commits?per_page=100"),
        CollectorSpec("A.8.8", "vulnerability_scan", "github-dependabot-alerts",
                      f"{gh} api repos/{repo}/dependabot/alerts?state=open", optional=True),
        CollectorSpec("A.8.24", "config_export", "github-secret-scanning",
                      f"{gh} api repos/{repo}/secret-scanning/alerts", optional=True),
        CollectorSpec("A.5.17", "config_export", "github-collaborators",
                      f"{gh} api repos/{repo}/collaborators"),
    ]


def aces_collectors(ledger_path: str) -> list[CollectorSpec]:
    """Evidence drawn from ACES itself: the ledger is the logging control's own proof."""
    return [
        CollectorSpec("A.8.15", "log_export", "aces-ledger-verify",
                      f"aces ledger verify {ledger_path}", ext="txt", platform="aces"),
        CollectorSpec("A.8.15", "log_export", "aces-ledger-tail",
                      f"powershell -Command \"Get-Content '{ledger_path}' -Tail 50\"",
                      ext="txt", platform="aces", optional=True),
    ]


NAME_RE = re.compile(r"^(A\.\d+\.\d+)_[a-z0-9-]+_(\d{4}-\d{2}-\d{2})\.[a-z0-9]+$")


def evidence_filename(c: CollectorSpec, day: date) -> str:
    return f"{c.control}_{c.name}_{day.isoformat()}.{c.ext}"


# ----------------------------------------------------------------------------- collection


@dataclass
class CollectionResult:
    file: str | None
    control: str
    evidence_kind: str
    name: str
    ok: bool
    sha256: str | None = None
    bytes: int = 0
    error: str | None = None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def collect(outdir: str | Path, collectors: list[CollectorSpec],
            dry_run: bool = False, timeout_s: int = 120) -> dict[str, Any]:
    """Run collectors, write evidence files, return a manifest.

    Never overwrites: a second collection on the same day gets a ``_2`` suffix.
    """
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    if out.is_symlink():
        raise PermissionError(f"refusing to collect into the symbolic link {out}")
    today = datetime.now(timezone.utc).date()
    results: list[CollectionResult] = []
    for c in collectors:
        fname = evidence_filename(c, today)
        path = out / fname
        n = 2
        while path.exists():
            path = out / fname.replace(f".{c.ext}", f"_{n}.{c.ext}")
            n += 1
        if dry_run:
            results.append(CollectionResult(str(path.name), c.control, c.evidence_kind, c.name, ok=True,
                                            error="dry run: not collected"))
            continue
        try:
            proc = subprocess.run(c.command, shell=True, capture_output=True, text=True,
                                  timeout=timeout_s)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip()[:300] or f"exit {proc.returncode}")
            if not proc.stdout.strip():
                raise RuntimeError("collector produced no output")
            path.write_text(proc.stdout, encoding="utf-8")
            results.append(CollectionResult(path.name, c.control, c.evidence_kind, c.name, ok=True,
                                            sha256=_sha256(path), bytes=path.stat().st_size))
        except Exception as e:
            results.append(CollectionResult(None, c.control, c.evidence_kind, c.name,
                                            ok=False, error=str(e)[:300]))
            if not c.optional:
                continue
    manifest = {
        "kind": "aces.compliance_collection",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "evidence_dir": str(out.resolve()),
        "results": [r.__dict__ for r in results],
    }
    mpath = out / f"manifest_{today.isoformat()}.json"
    mpath.write_text(json.dumps(manifest, indent=2))
    manifest["manifest_file"] = str(mpath.resolve())
    return manifest


# ----------------------------------------------------------------------------- validation


@dataclass(frozen=True)
class Finding:
    level: str     # gap | stale | naming | ok
    control: str
    detail: str


def validate(evidence_dir: str | Path, today: date | None = None) -> list[Finding]:
    """Check an evidence directory for completeness, freshness, naming and coverage."""
    today = today or datetime.now(timezone.utc).date()
    root = Path(evidence_dir)
    findings: list[Finding] = []
    files = [p for p in root.iterdir() if p.is_file()
             and not p.name.startswith("manifest_") and p.name not in ("ledger.jsonl", "INDEX.md")]

    by_control: dict[str, list[tuple[Path, date]]] = {}
    for p in files:
        m = NAME_RE.match(p.name)
        if not m:
            findings.append(Finding("naming", "-", f"{p.name}: not {{control}}_{{type}}_{{date}}.ext"))
            continue
        ctrl, day = m.group(1), date.fromisoformat(m.group(2))
        by_control.setdefault(ctrl, []).append((p, day))

    for c in CONTROLS.values():
        entries = by_control.get(c.id, [])
        if not entries:
            findings.append(Finding("gap", c.id, f"{c.title}: no evidence collected"))
            continue
        newest = max(day for _, day in entries)
        # freshness: the tightest requirement among this control's evidence kinds
        limits = [FRESHNESS_DAYS[k] for k in c.evidence_kinds if FRESHNESS_DAYS.get(k)]
        if limits and (today - newest).days > min(limits):
            findings.append(Finding("stale", c.id,
                                    f"newest evidence {newest} is {(today - newest).days} days old "
                                    f"(limit {min(limits)})"))
        if c.tier == "critical" and len({p.suffix for p, _ in entries}) < 2 and len(entries) < 2:
            findings.append(Finding("gap", c.id,
                                    "critical-tier control has fewer than 2 forms of evidence"))
        findings.append(Finding("ok", c.id, f"{len(entries)} file(s), newest {newest}"))
    return findings


def index(evidence_dir: str | Path) -> str:
    """Auditor-facing markdown index of the evidence package."""
    root = Path(evidence_dir)
    today = datetime.now(timezone.utc).date()
    lines = [
        "# Evidence Package Index",
        "",
        f"Generated: {today.isoformat()} (ACES compliance module)",
        "",
        "| Control | Title | Evidence file | Collected | SHA-256 (first 12) |",
        "| --- | --- | --- | --- | --- |",
    ]
    files = sorted(p for p in root.iterdir()
                   if p.is_file() and NAME_RE.match(p.name) and not p.name.startswith("manifest_"))
    for p in files:
        m = NAME_RE.match(p.name)
        ctrl = CONTROLS.get(m.group(1))
        title = ctrl.title if ctrl else "(control not in registry)"
        lines.append(f"| {m.group(1)} | {title} | {p.name} | {m.group(2)} | "
                     f"{_sha256(p)[:12]} |")
    findings = validate(root, today)
    bad = [f for f in findings if f.level != "ok"]
    lines += ["", "## Validation", ""]
    if not bad:
        lines.append("No gaps: every registered control has current evidence.")
    else:
        for f in bad:
            lines.append(f"- **{f.level}** {f.control}: {f.detail}")
    return "\n".join(lines) + "\n"
