"""The operator ledger: an append-only, hash-chained JSON-lines file.

Every policy change, override, project reclassification and lane test lands here. Entries carry the operator, a
UTC timestamp, the previous entry's hash and their own hash over the canonical JSON of the record, so a rewrite
anywhere in the file breaks `aces ledger verify`. This is the desktop-side ledger; simlab keeps the experiment
ledger on the server with the same shape.

Writers hold an exclusive lock on `<ledger>.lock` (ledger.jsonl.lock next to the ledger) while they read the last
hash and append, so two processes writing at once (an `aces golden` run and a lane test, say) cannot fork the chain.
Readers take no lock: an entry is one line written in one call.

Tools outside aces-base append through `record()` (`aces ledger record KIND --json FILE`), which checks the kind and
the payload first. Kinds under policy., project., lane. and ledger. are written only by the aces commands that own
them.
"""
from __future__ import annotations

import contextlib
import getpass
import hashlib
import json
import os
import re
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import config

GENESIS = "0" * 64
RESERVED_PREFIXES = ("policy.", "project.", "lane.", "ledger.")
KIND_RE = re.compile(r"^[a-z][a-z0-9-]*(\.[a-z0-9-]+)+$")
MAX_KIND_CHARS = 64
MAX_PAYLOAD_BYTES = 65536
LOCK_TIMEOUT_S = 30.0


def path() -> Path:
    return config.STATE / "ledger.jsonl"


def lock_path() -> Path:
    p = path()
    return p.with_name(p.name + ".lock")


if os.name == "nt":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        with contextlib.suppress(OSError):
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (BlockingIOError, PermissionError):
            return False

    def _unlock(fd: int) -> None:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def locked(timeout_s: float = LOCK_TIMEOUT_S) -> Iterator[None]:
    """Hold the ledger's exclusive writer lock (flock on POSIX, msvcrt.locking of byte 0 on Windows), retrying for
    up to `timeout_s` seconds. Raises TimeoutError when another writer keeps it longer than that."""
    lp = lock_path()
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lp, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + timeout_s
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"the ledger lock {lp} is held by another writer (waited {timeout_s:.0f} s)")
            time.sleep(0.02)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


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


def count() -> int:
    """Number of entries in the ledger."""
    return len(entries())


def last_hash() -> str:
    es = entries()
    return es[-1]["hash"] if es else GENESIS


def _new_record(kind: str, payload: dict[str, Any], prev: str) -> dict[str, Any]:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": kind,
        "operator": getpass.getuser(),
        "host": socket.gethostname(),
        "payload": payload,
        "prev": prev,
    }
    record["hash"] = _hash(prev, record)
    return record


def _write(record: dict[str, Any]) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def append(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Append one entry and return it (aces-base's own writers; no kind or payload checks)."""
    with locked():
        record = _new_record(kind, payload, last_hash())
        _write(record)
    return record


def validate(kind: Any, payload: Any) -> None:
    """Raise ValueError when `kind` or `payload` may not be recorded by an outside tool."""
    if not isinstance(kind, str) or len(kind) > MAX_KIND_CHARS or not KIND_RE.match(kind):
        raise ValueError(f"kind '{kind}' must be dotted lower-case words (e.g. golden.run), at most {MAX_KIND_CHARS} characters")
    if kind.startswith(RESERVED_PREFIXES):
        raise ValueError(f"kind '{kind}' is written by aces itself; use the aces command that owns it")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    try:
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as e:
        raise ValueError(f"payload is not plain JSON ({e})") from e
    size = len(text.encode("utf-8"))
    if size > MAX_PAYLOAD_BYTES:
        raise ValueError(f"payload is {size} bytes; the limit is {MAX_PAYLOAD_BYTES}")


def record(kind: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Validate, then append under the lock. Returns (n, entry): n is the entry's 1-based position in the ledger,
    counted under the same lock, so it is exact even with concurrent writers."""
    validate(kind, payload)
    with locked():
        es = entries()
        entry = _new_record(kind, payload, es[-1]["hash"] if es else GENESIS)
        _write(entry)
    return len(es) + 1, entry


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
