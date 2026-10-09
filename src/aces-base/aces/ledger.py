"""The operator ledger: an append-only, hash-chained JSON-lines file.

Every policy change, override, project reclassification and lane test lands here. Entries carry the operator, a
UTC timestamp, the previous entry's hash and their own hash over the canonical JSON of the record, so a rewrite
anywhere in the file breaks `aces ledger verify`. This is the desktop-side ledger; simlab keeps the experiment
ledger on the server with the same shape.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config

GENESIS = "0" * 64


def path() -> Path:
    return config.STATE / "ledger.jsonl"


def _canonical(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(prev: str, record: dict[str, Any]) -> str:
    return hashlib.sha256((prev + _canonical(record)).encode("utf-8")).hexdigest()


def entries() -> list[dict[str, Any]]:
    p = path()
    if not p.exists():
        return []
    out = []
    with open(p, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SystemExit(f"aces: ledger line {n} is not JSON: {e}") from e
    return out


def last_hash() -> str:
    es = entries()
    return es[-1]["hash"] if es else GENESIS


def append(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Append one entry and return it."""
    prev = last_hash()
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": kind,
        "operator": getpass.getuser(),
        "host": socket.gethostname(),
        "payload": payload,
        "prev": prev,
    }
    record["hash"] = _hash(prev, record)
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return record


def verify() -> tuple[bool, int, str]:
    """Return (ok, count, message). Recomputes every hash and the chain."""
    prev = GENESIS
    es = entries()
    for n, e in enumerate(es, 1):
        rec = {k: v for k, v in e.items() if k != "hash"}
        if e.get("prev") != prev:
            return False, n, f"entry {n}: prev hash does not match entry {n - 1}"
        if _hash(prev, rec) != e.get("hash"):
            return False, n, f"entry {n}: hash does not match its content"
        prev = e["hash"]
    return True, len(es), "chain intact" if es else "ledger empty"
