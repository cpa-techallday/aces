"""Hash-chained evidence ledger (R&D Tax Incentive grade).

Each design run appends one record: the spec and its hash, the model id and
version, the code revision, dependency versions, the calibration state, the inputs,
the observed results and sha256 digests of the artifacts written.  Records are
chained by hash so any later edit is detectable.  The format mirrors simlab's
``out/evidence/ledger.jsonl`` so the two ledgers can be merged.

Limits: a chain cannot detect records deleted from its end.  Anchor the head hash
outside the file (simlab delivers its ledger head to the Nabu evidence API).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import subprocess
import time
from contextlib import contextmanager
from importlib import metadata
from pathlib import Path
from typing import Any

from .spec import COMPANY, canonical_hash

GENESIS = "0" * 64
TRACKED_PACKAGES = ("numpy", "scipy", "scikit-image", "optuna", "CoolProp", "pydantic")
_REPO_ROOT = Path(__file__).resolve().parents[2]


def code_revision() -> str:
    """git describe of the ACES checkout; 'unversioned' when not running from one."""
    if not (_REPO_ROOT / "src" / "aces").is_dir() or not (_REPO_ROOT / ".git").exists():
        return "unversioned"
    try:
        out = subprocess.run(["git", "describe", "--always", "--dirty", "--tags"], cwd=_REPO_ROOT,
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or "unversioned"
    except (OSError, subprocess.SubprocessError):
        return "unversioned"


def dependency_versions() -> dict[str, str]:
    out = {}
    for pkg in TRACKED_PACKAGES:
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            out[pkg] = "missing"
    return out


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@contextmanager
def _lock(path: Path, timeout_s: float = 30.0):
    """Cross-platform exclusive lock: a sibling .lock file created with O_EXCL."""
    lock = path.with_suffix(path.suffix + ".lock")
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                raise TimeoutError(f"ledger lock {lock} held for more than {timeout_s} s")
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        os.unlink(lock)


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def find(self, record_hash: str) -> dict[str, Any] | None:
        return next((r for r in self.records() if r.get("record_hash") == record_hash), None)

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        if record.get("company") != COMPANY:
            raise ValueError(f"evidence must be recorded under {COMPANY}; got {record.get('company')!r}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _lock(self.path):
            recs = self.records()
            body = {k: v for k, v in record.items() if k not in ("prev_hash", "record_hash")}
            body["prev_hash"] = recs[-1]["record_hash"] if recs else GENESIS
            body["record_hash"] = canonical_hash({k: v for k, v in body.items() if k != "record_hash"})
            with self.path.open("a") as fh:
                fh.write(json.dumps(body, sort_keys=True, default=str) + "\n")
        return body

    def verify(self) -> tuple[bool, str]:
        if not self.path.exists():
            return False, f"ledger {self.path} not found"
        prev = GENESIS
        lines = [ln for ln in self.path.read_text().splitlines() if ln.strip()]
        for i, line in enumerate(lines):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                return False, f"record {i}: not valid JSON (truncated or edited)"
            if rec.get("prev_hash") != prev:
                return False, f"record {i}: prev_hash does not match the previous record"
            expect = canonical_hash({k: v for k, v in rec.items() if k != "record_hash"})
            if rec.get("record_hash") != expect:
                return False, f"record {i}: contents changed after it was written"
            prev = rec["record_hash"]
        if not lines:
            return False, "ledger is empty"
        return True, f"{len(lines)} records, chain intact, head {prev[:12]}"


def make_record(
    *,
    kind: str,
    spec: dict[str, Any],
    model_id: str,
    model_version: str,
    inputs: dict[str, Any],
    observations: dict[str, Any],
    calibration: dict[str, Any],
    artifacts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """artifacts: name -> path; each is stored with its sha256 so later edits are detectable."""
    arts = {name: {"path": str(p), "sha256": file_sha256(p)} for name, p in (artifacts or {}).items()}
    return {
        "kind": kind,
        "timestamp_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "company": spec.get("company", COMPANY),
        "project": spec.get("project"),
        "owner": spec.get("owner"),
        "rd_activity": spec.get("rd_activity"),
        "hypothesis": spec.get("hypothesis"),
        "spec_hash": canonical_hash(spec),
        "spec": spec,
        "model": {"id": model_id, "version": model_version},
        "code_revision": code_revision(),
        "dependencies": dependency_versions(),
        "calibration_hash": canonical_hash(calibration),
        "calibration": calibration,
        "inputs": inputs,
        "observations": observations,
        "artifacts": arts,
    }
