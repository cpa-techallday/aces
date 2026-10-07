"""ISO/IEC 27001:2022 evidence collection, alongside ACES's evidence architecture.

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

Collectors are read-only.  A command collector is an argument list, never a
shell string, run through :func:`aces.proc.run` with a timeout and a minimal
environment, using the existing local ``gh`` credentials (audit C1, C5).  The
ACES collectors read the ledger in-process.  Nothing is sent anywhere; evidence
stays in the local evidence folder.

Layout of an evidence folder (audit C4)::

    <evidence dir>/
        INDEX.md                    auditor index, written by ``aces compliance index``
        run-20261007T031500Z/       one new folder per collection run, never reused
            manifest.json           what the run collected, with each file's sha256; written once
            A.8.32_github-merged-prs_2026-10-07.json
            ...

Only a file listed in its run's manifest, with a matching sha256, non-empty,
readable as its type, dated on its run's date and not after today, and sitting
directly in its run folder (not a link) counts as evidence (audit C3).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path
from typing import Any

from . import evidence, proc, safeio

# ----------------------------------------------------------------------------- controls
#
# ISO/IEC 27001:2022 Annex A subset relevant to ACES as it stands today: a small
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
    title: str           # ISO/IEC 27001:2022 Annex A control title
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
        Control("A.5.18", "Access rights", "critical", ("access_list",)),
        Control("A.8.4", "Access to source code", "critical", ("config_export",)),
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

NAME_RE = re.compile(r"(?P<control>A\.\d+\.\d+)_(?P<type>[a-z0-9-]+)_(?P<date>\d{4}-\d{2}-\d{2})\.(?P<ext>[a-z0-9]+)")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
RUN_RE = re.compile(r"run-\d{8}T\d{6}Z(-\d+)?")
MANIFEST = "manifest.json"
ROOT_FILES = ("INDEX.md", "ledger.jsonl", "ledger.jsonl.lock", "ledger.jsonl.head")  # allowed beside the run folders
MAX_EVIDENCE_BYTES = 64 * 1024 * 1024
DEFAULT_TIMEOUT_S = 120.0

# What gh needs to find its credentials, config and proxy; nothing else from this process is passed on.
_GH_ENV_KEEP = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_HOST",
                "GH_CONFIG_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME",
                "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
                "https_proxy", "http_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR")
_GH_ENV_SET = {"GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1"}


@dataclass(frozen=True)
class CollectorSpec:
    """One read-only collector mapped to a control.

    ``command`` is an argument list run with :func:`aces.proc.run` (no shell) and the existing local
    credentials; its stdout becomes the evidence file.  ``produce`` is the in-process alternative, used by the
    ACES ledger collectors.  JSON output must parse, and keys named in ``redact_keys`` are dropped at any depth
    before anything is written.  A failed collector is recorded in the manifest, never silently skipped: a
    failed ``optional`` collector is a warning, a failed required one fails the run.
    """
    control: str
    evidence_kind: str   # key into FRESHNESS_DAYS
    name: str            # becomes {control}_{name}_{date}.{ext}
    command: tuple[str, ...] = ()
    ext: str = "json"
    platform: str = "github"
    optional: bool = False  # optional collectors may fail without failing the run
    redact_keys: tuple[str, ...] = ()
    produce: Callable[[], str] | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.command, (str, bytes)):
            raise TypeError("a collector command is an argument list, not a shell string")
        object.__setattr__(self, "command", tuple(os.fspath(a) for a in self.command))
        if bool(self.command) == (self.produce is not None):
            raise ValueError(f"collector {self.name!r} needs exactly one of a command or a produce function")
        if not NAME_RE.fullmatch(f"{self.control}_{self.name}_2000-01-01.{self.ext}"):
            raise ValueError(f"collector {self.control} {self.name!r} .{self.ext} does not give a valid evidence name")


def check_repo(repo: str) -> str:
    """``repo`` if it is a plain GitHub ``owner/name``; ValueError otherwise (audit C1)."""
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
        raise ValueError(f"--repo must be owner/name using letters, digits, '_', '.' and '-'; got {repo!r}")
    if any(part in (".", "..") or part.startswith("-") for part in repo.split("/")):
        raise ValueError(f"--repo {repo!r} is not a GitHub owner/name")
    return repo


def check_ledger_path(path: str | os.PathLike, *, must_exist: bool = True) -> Path:
    """The ledger as an absolute path, if it is a regular file and not a link (audit C1).

    With ``must_exist=False`` a missing ledger is allowed (the first record creates it).
    """
    p = Path(path)
    if safeio.is_link(p):
        raise safeio.UnsafePathError(f"ledger {p} is a link; give the ledger file itself")
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        if must_exist:
            raise FileNotFoundError(f"ledger {p} not found; give an existing ACES ledger file") from None
        return p.absolute()
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"ledger {p} is not a regular file")
    return p.absolute()


def _gh() -> str:
    """The gh executable, on PATH or at its standard Windows install location."""
    found = shutil.which("gh")
    if found:
        return found
    win = Path(r"C:\Program Files\GitHub CLI\gh.exe")
    return str(win) if os.name == "nt" and win.is_file() else "gh"


def github_collectors(repo: str) -> list[CollectorSpec]:
    """Repo-level GitHub evidence (the ACES repo is personal-account, so no org calls).

    Raw API JSON is captured unfiltered (a full export is stronger evidence than a projection), except that
    secret-scanning alerts are requested with ``hide_secret=true`` and any ``secret`` field is dropped before
    writing (audit C2).

    Mapping to ISO/IEC 27001:2022 Annex A (audit C6):

    * A.8.4 Access to source code: repository settings (visibility, forking, security features) and the branch
      protection on ``main`` (who may write to it).
    * A.5.18 Access rights: the collaborators and their permissions.
    * A.5.17 Authentication information: secret-scanning alerts (credentials leaked into the code).
    * A.8.8 Management of technical vulnerabilities: open Dependabot alerts.
    * A.8.32 Change management: merged pull requests and recent commits.
    """
    repo = check_repo(repo)
    gh = _gh()
    return [
        CollectorSpec("A.8.4", "config_export", "github-repo-settings", (gh, "api", f"repos/{repo}")),
        CollectorSpec("A.8.4", "config_export", "github-branch-protection",
                      (gh, "api", f"repos/{repo}/branches/main/protection"), optional=True),
        CollectorSpec("A.8.32", "change_record", "github-merged-prs",
                      (gh, "pr", "list", f"--repo={repo}", "--state", "merged", "--limit", "50",
                       "--json", "number,title,author,mergedAt,mergedBy")),
        CollectorSpec("A.8.32", "change_record", "github-recent-commits",
                      (gh, "api", f"repos/{repo}/commits?per_page=100")),
        CollectorSpec("A.8.8", "vulnerability_scan", "github-dependabot-alerts",
                      (gh, "api", f"repos/{repo}/dependabot/alerts?state=open"), optional=True),
        CollectorSpec("A.5.17", "config_export", "github-secret-scanning",
                      (gh, "api", f"repos/{repo}/secret-scanning/alerts?hide_secret=true"),
                      optional=True, redact_keys=("secret",)),
        CollectorSpec("A.5.18", "access_list", "github-collaborators",
                      (gh, "api", f"repos/{repo}/collaborators")),
    ]


def _ledger_state(ledger: Path) -> tuple[str, int, str]:
    """(verify message, record count, head hash); RuntimeError if the chain does not verify."""
    led = evidence.Ledger(ledger)
    ok, msg = led.verify()
    if not ok:
        raise RuntimeError(f"ledger does not verify: {msg}")
    recs = led.records()
    return msg, len(recs), recs[-1]["record_hash"]


def _ledger_verify_text(ledger: Path) -> str:
    msg, _, _ = _ledger_state(ledger)
    return f"OK: {msg}\n"  # as `aces ledger verify` prints it


def _ledger_head_json(ledger: Path) -> str:
    msg, n, head = _ledger_state(ledger)
    return json.dumps({"kind": "aces.ledger_head", "ledger": str(ledger), "verified": True,
                       "verify_message": msg, "record_count": n, "head_hash": head}, indent=2) + "\n"


def aces_collectors(ledger_path: str | os.PathLike) -> list[CollectorSpec]:
    """Evidence drawn from ACES itself: the ledger is the logging control's own proof (A.8.15).

    Only the verify result, the record count and the head hash leave the ledger.  Record contents (specs,
    inputs and results, which may be export-controlled) are never copied into the package (audit C6, rule 12).
    """
    ledger = check_ledger_path(ledger_path, must_exist=True)
    return [
        CollectorSpec("A.8.15", "log_export", "aces-ledger-verify", ext="txt", platform="aces",
                      produce=partial(_ledger_verify_text, ledger)),
        CollectorSpec("A.8.15", "log_export", "aces-ledger-head", platform="aces",
                      produce=partial(_ledger_head_json, ledger)),
    ]


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
    optional: bool = False


def _redact(obj: Any, keys: frozenset[str]) -> Any:
    if isinstance(obj, dict):
        return {k: _redact(v, keys) for k, v in obj.items() if k not in keys}
    if isinstance(obj, list):
        return [_redact(v, keys) for v in obj]
    return obj


def _evidence_bytes(c: CollectorSpec, timeout_s: float) -> bytes:
    """Run one collector and return what is to be written; raises on any failure."""
    if c.produce is not None:
        out = c.produce()
    else:
        env = proc.minimal_env(_GH_ENV_SET, keep=_GH_ENV_KEEP) if c.platform == "github" else proc.minimal_env()
        try:
            cp = proc.run(list(c.command), env=env, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"timed out after {timeout_s:g} s; its process group was killed") from None
        if cp.returncode != 0:
            raise RuntimeError((cp.stderr or "").strip()[:300] or f"exit {cp.returncode}")
        out = cp.stdout or ""
    if not out.strip():
        raise RuntimeError("collector produced no output")
    if c.ext == "json":
        try:
            obj = json.loads(out)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"output is not valid JSON ({e.msg}); nothing written") from None
        if c.redact_keys:
            out = json.dumps(_redact(obj, frozenset(c.redact_keys)), indent=2) + "\n"
    return out.encode("utf-8")


def _run_name(now: datetime) -> str:
    return "run-" + now.strftime("%Y%m%dT%H%M%SZ")


def _new_run_dir(root: safeio.OutDir, now: datetime) -> safeio.OutDir:
    """A run folder that did not exist before; an existing name (or a planted link) gets a numbered suffix."""
    base = _run_name(now)
    for n in range(1, 100):
        try:
            return root.subdir(base if n == 1 else f"{base}-{n}", new=True)
        except (FileExistsError, safeio.UnsafePathError):
            continue
    raise FileExistsError(f"could not create a new run folder for {base} under {root.path}")


def collect(outdir: str | Path, collectors: list[CollectorSpec],
            dry_run: bool = False, timeout_s: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    """Run collectors into a new run folder under ``outdir`` and return the run's manifest.

    Each run gets its own new folder (``run-YYYYMMDDTHHMMSSZ``) and nothing earlier is touched.  Every file is
    created exclusively and never through a link, and ``manifest.json`` is written once, last (audit C4).  Every
    command has a timeout; on timeout its whole process group is killed (audit C5).  The manifest's ``ok`` is
    false only when a required collector failed; failed optional collectors are listed as warnings.
    A dry run writes nothing.
    """
    if not isinstance(timeout_s, (int, float)) or isinstance(timeout_s, bool) or not 0 < timeout_s < float("inf"):
        raise ValueError(f"every collector needs a timeout: timeout_s must be a positive number, got {timeout_s!r}")
    names = [evidence_filename(c, date(2000, 1, 1)) for c in collectors]
    if len(set(names)) != len(names):
        raise ValueError("two collectors would write the same evidence file; give each a distinct control/name")
    started = datetime.now(UTC)
    today = started.date()

    if dry_run:
        results = [CollectionResult(evidence_filename(c, today), c.control, c.evidence_kind, c.name, ok=True,
                                    error="dry run: not collected", optional=c.optional) for c in collectors]
        return {"kind": "aces.compliance_collection", "dry_run": True, "run_date": today.isoformat(),
                "run_dir": None, "evidence_dir": None, "manifest_file": None, "ok": True,
                "required_failed": [], "optional_failed": [], "results": [asdict(r) for r in results]}

    with safeio.OutDir(outdir) as root, _new_run_dir(root, started) as run:
        results: list[CollectionResult] = []
        for c in collectors:
            try:
                data = _evidence_bytes(c, timeout_s)
            except Exception as e:  # noqa: BLE001 - every collector failure is recorded in the manifest
                results.append(CollectionResult(None, c.control, c.evidence_kind, c.name, ok=False,
                                                error=str(e)[:300] or type(e).__name__, optional=c.optional))
                continue
            fname = evidence_filename(c, today)
            run.write_bytes(fname, data)
            results.append(CollectionResult(fname, c.control, c.evidence_kind, c.name, ok=True,
                                            sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
                                            optional=c.optional))
        required_failed = [r.name for r in results if not r.ok and not r.optional]
        manifest = {
            "kind": "aces.compliance_collection",
            "run_dir": run.path.name,
            "run_date": today.isoformat(),
            "started_at": started.isoformat(),
            "collected_at": datetime.now(UTC).isoformat(),
            "evidence_dir": str(run.path),
            "timeout_s": timeout_s,
            "ok": not required_failed,
            "required_failed": required_failed,
            "optional_failed": [r.name for r in results if not r.ok and r.optional],
            "results": [asdict(r) for r in results],
        }
        mpath = run.write_text(MANIFEST, json.dumps(manifest, indent=2) + "\n")
    manifest["manifest_file"] = str(mpath)
    return manifest


# ----------------------------------------------------------------------------- validation


@dataclass(frozen=True)
class Finding:
    level: str     # gap | stale | naming | invalid | ok
    control: str
    detail: str


@dataclass(frozen=True)
class Evidence:
    """One evidence file that passed every check in :func:`validate`."""
    run: str
    file: str
    control: str
    type: str
    day: date
    sha256: str
    bytes: int


def _control_of(name: str) -> str:
    m = NAME_RE.fullmatch(name)
    return m.group("control") if m else "-"


def _read_regular(path: Path) -> bytes:
    """The bytes of a regular file that is not a link, up to MAX_EVIDENCE_BYTES."""
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or safeio.is_link(path):
        raise safeio.UnsafePathError("is a link")
    if not stat.S_ISREG(st.st_mode):
        raise ValueError("is not a regular file")
    if st.st_size > MAX_EVIDENCE_BYTES:
        raise ValueError(f"is {st.st_size:,} bytes; the limit is {MAX_EVIDENCE_BYTES:,}")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise ValueError("is not a regular file")
        data = f.read(MAX_EVIDENCE_BYTES + 1)
    if len(data) > MAX_EVIDENCE_BYTES:
        raise ValueError(f"is larger than {MAX_EVIDENCE_BYTES:,} bytes")
    return data


def _content_problem(data: bytes, ext: str) -> str | None:
    """Why ``data`` is not usable evidence of type ``ext``, or None."""
    if not data:
        return "is empty"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "is not UTF-8 text"
    if not text.strip():
        return "is blank"
    if ext == "json":
        try:
            json.loads(text)
        except json.JSONDecodeError:
            return "is not valid JSON"
    return None


def _scan_run(rd: Path, today: date) -> tuple[list[Evidence], list[Finding]]:
    run = rd.name
    try:
        man = json.loads(_read_regular(rd / MANIFEST).decode("utf-8"))
        if man.get("kind") != "aces.compliance_collection" or not isinstance(man.get("results"), list):
            raise ValueError("not a compliance collection manifest")
        run_date = date.fromisoformat(man["run_date"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        return [], [Finding("invalid", "-", f"{run}: no valid {MANIFEST} ({e}); none of its files are counted")]

    accepted: list[Evidence] = []
    findings: list[Finding] = []
    listed = {str(r["file"]): r for r in man["results"] if isinstance(r, dict) and r.get("ok") and r.get("file")}
    for name in sorted({p.name for p in rd.iterdir()} - {MANIFEST} - set(listed)):
        findings.append(Finding("invalid", _control_of(name),
                                f"{run}/{name} is not listed in the run's manifest; not counted"))
    for name, r in sorted(listed.items()):
        m = NAME_RE.fullmatch(name)
        if not m or Path(name).name != name or "\\" in name:
            findings.append(Finding("invalid", "-", f"{run}: manifest entry {name!r} is not a plain evidence "
                                                    "file name in the run folder; not counted"))
            continue
        ctrl = m.group("control")
        problem = None
        try:
            day = date.fromisoformat(m.group("date"))
            data = _read_regular(rd / name)
        except FileNotFoundError:
            problem = "is listed in the manifest but missing"
        except (OSError, ValueError) as e:
            problem = str(e)
        else:
            problem = _content_problem(data, m.group("ext"))
            if problem is None and hashlib.sha256(data).hexdigest() != r.get("sha256"):
                problem = "does not match the sha256 in its manifest (changed after collection)"
            elif problem is None and r.get("control") != ctrl:
                problem = f"is filed under {ctrl} but its manifest says {r.get('control')}"
            elif problem is None and day > today:
                problem = f"is dated {day}, after today ({today})"
            elif problem is None and day != run_date:
                problem = f"is dated {day} but its run collected on {run_date}"
        if problem:
            findings.append(Finding("invalid", ctrl, f"{run}/{name} {problem}; not counted"))
        else:
            accepted.append(Evidence(run, name, ctrl, m.group("type"), day, hashlib.sha256(data).hexdigest(),
                                     len(data)))
    return accepted, findings


def _scan(evidence_dir: str | Path, today: date) -> tuple[list[Evidence], list[Finding]]:
    """Every collection run under ``evidence_dir``: the evidence that verifies, and why the rest does not."""
    root = Path(evidence_dir)
    if safeio.is_link(root):
        raise safeio.UnsafePathError(f"{root} is a link; give the evidence folder itself")
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not an evidence folder")
    accepted: list[Evidence] = []
    findings: list[Finding] = []
    for p in sorted(root.iterdir()):
        if safeio.is_link(p):
            findings.append(Finding("invalid", _control_of(p.name), f"{p.name} is a link; not followed, not counted"))
        elif p.name in ROOT_FILES and p.is_file():
            continue
        elif p.is_dir() and RUN_RE.fullmatch(p.name):
            a, f = _scan_run(p, today)
            accepted += a
            findings += f
        elif p.is_file() and p.name.startswith("manifest_"):
            findings.append(Finding("invalid", "-", f"{p.name} is a manifest from the old flat layout (before audit "
                                                    "C3/C4); its files are not counted. Collect again."))
        elif p.is_file() and not NAME_RE.fullmatch(p.name):
            findings.append(Finding("naming", "-", f"{p.name}: not {{control}}_{{type}}_{{date}}.ext"))
        else:
            findings.append(Finding("invalid", _control_of(p.name),
                                    f"{p.name} is not in a collection run (no manifest lists it); not counted"))
    return accepted, findings


def _coverage(accepted: list[Evidence], today: date) -> list[Finding]:
    findings: list[Finding] = []
    for c in CONTROLS.values():
        entries = [e for e in accepted if e.control == c.id]
        if not entries:
            findings.append(Finding("gap", c.id, f"{c.title}: no evidence collected"))
            continue
        newest = max(e.day for e in entries)
        # freshness: the tightest requirement among this control's evidence kinds
        limits = [FRESHNESS_DAYS[k] for k in c.evidence_kinds if FRESHNESS_DAYS.get(k)]
        if limits and (today - newest).days > min(limits):
            findings.append(Finding("stale", c.id,
                                    f"newest evidence {newest} is {(today - newest).days} days old "
                                    f"(limit {min(limits)})"))
        # copies of one evidence type (e.g. the same collector in two runs) are one form, not two
        if c.tier == "critical" and len({e.type for e in entries}) < 2:
            findings.append(Finding("gap", c.id,
                                    "critical-tier control has fewer than 2 forms of evidence"))
        findings.append(Finding("ok", c.id, f"{len(entries)} file(s), newest {newest}"))
    return findings


def validate(evidence_dir: str | Path, today: date | None = None) -> list[Finding]:
    """Check an evidence folder for completeness, freshness, naming, coverage and integrity (audit C3).

    Only files that pass every check in the module docstring count towards a control.
    """
    today = today or datetime.now(UTC).date()
    accepted, findings = _scan(evidence_dir, today)
    return findings + _coverage(accepted, today)


def index(evidence_dir: str | Path) -> str:
    """Auditor-facing markdown index of the evidence package (verified evidence only)."""
    today = datetime.now(UTC).date()
    accepted, findings = _scan(evidence_dir, today)
    findings += _coverage(accepted, today)
    lines = [
        "# Evidence Package Index",
        "",
        f"Generated: {today.isoformat()} (ACES compliance module)",
        "",
        "Only files listed in their collection run's manifest with a matching SHA-256 are listed and counted.",
        "",
        "| Control | Title | Evidence file | Collected | SHA-256 (first 12) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for e in sorted(accepted, key=lambda e: (e.run, e.file)):
        ctrl = CONTROLS.get(e.control)
        title = ctrl.title if ctrl else "(control not in registry)"
        lines.append(f"| {e.control} | {title} | {e.run}/{e.file} | {e.day.isoformat()} | {e.sha256[:12]} |")
    bad = [f for f in findings if f.level != "ok"]
    lines += ["", "## Validation", ""]
    if not bad:
        lines.append("No gaps: every registered control has current evidence.")
    else:
        for f in bad:
            lines.append(f"- **{f.level}** {f.control}: {f.detail}")
    return "\n".join(lines) + "\n"
