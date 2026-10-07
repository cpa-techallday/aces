"""Hash-chained evidence ledger (R&D Tax Incentive grade).

Each design run appends one record: the spec and its hash, the model id and
version, the code revision, dependency versions, the calibration state, the inputs,
the observed results and sha256 digests of the artifacts written.  Records are
chained by hash so any later edit is detectable.  The format mirrors simlab's
``out/evidence/ledger.jsonl`` so the two ledgers can be merged.

Integrity rules (audit M3):

* every line is one JSON object; a duplicate key or any other JSON value fails ``verify``;
* appends hold an OS file lock on ``<ledger>.lock`` (``fcntl`` on POSIX, ``msvcrt`` on Windows) and are
  fsynced.  ``verify`` takes the same lock, so it never reads half an append.  The OS drops a lock when its
  holder exits, so a crashed run can't leave a stale lock behind;
* a last line without its newline is an interrupted append.  It is reported and kept exactly as written,
  never edited or deleted (rule 10), and nothing more is appended until ``aces ledger seal PATH`` closes it:
  that ends the line and appends an ``aces.ledger_seal`` record holding the line's sha256, chained to the last
  complete record.  ``verify`` then accepts the sealed line and counts it separately.

Limits: a hash chain alone can't tell a full rewrite with recomputed hashes, or records cut off its end, from
the real ledger.  Anchor the head outside the file:

* ``aces ledger head PATH`` prints the record count and head hash.  Keep a copy where the ledger's writers
  can't change it (simlab sends its head to the Nabu evidence API); ``aces ledger verify PATH --expect-head
  HASH --min-records N`` then fails if that record is gone, the chain up to it was rewritten, or fewer records
  remain;
* with ``ACES_LEDGER_HMAC_KEY`` set (environment only, never stored in the repo), every append also writes
  ``<ledger>.head``: count and head hash, HMAC-SHA256-signed.  ``verify`` then needs that file, checks its
  signature and that the chain still reaches the signed head.  Without the key nobody can forge a head, but
  replaying an older signed head together with the matching truncation passes; only the external copy above
  catches that.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import json
import os
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import metadata
from pathlib import Path
from typing import Any

from . import safeio
from .proc import minimal_env
from .proc import run as _run
from .spec import COMPANY, canonical_hash

# git goes through aces.proc; ``_run`` is bound here so a test that fakes proc.run for gh doesn't see git.

GENESIS = "0" * 64
SEAL_KIND = "aces.ledger_seal"
HMAC_ENV = "ACES_LEDGER_HMAC_KEY"
MIN_KEY_CHARS = 16
MAX_RECORD_BYTES = 32 * 1024 * 1024  # one line; verify streams the file, so memory never holds more than this
TRACKED_PACKAGES = ("numpy", "scipy", "scikit-image", "optuna", "CoolProp", "pydantic")
REPO_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = REPO_ROOT
GIT_TIMEOUT_S = 30.0


class LedgerError(ValueError):
    """The ledger can't take an append, or its signed head can't be used."""


# ----------------------------------------------------------------------------- git, hardened (audit H1)

def git(args: list[str], cwd: str | Path, *, timeout: float = GIT_TIMEOUT_S, text: bool = True,
        input: str | bytes | None = None) -> subprocess.CompletedProcess:
    """Run ``git ARGS`` in ``cwd`` so that a planted repository config can't run anything.

    A repository's own ``.git/config`` can name commands that git runs during ``status`` or ``describe
    --dirty``.  Here ``core.fsmonitor`` is off, hooks point at an empty path, every clean/smudge/process filter
    driver the config names is blanked, the index is never rewritten (``GIT_OPTIONAL_LOCKS=0``), and git gets
    the minimal environment and a timeout, all through :mod:`aces.proc`.
    """
    env = minimal_env({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "",
                            "GIT_PAGER": "cat", "PAGER": "cat"})
    fixed = [("core.fsmonitor", "false"), ("core.hooksPath", os.devnull), ("core.untrackedCache", "false"),
             ("diff.ignoreSubmodules", "all")]
    base = ["git"]
    for k, v in fixed:
        base += ["-c", f"{k}={v}"]
    overrides = list(fixed)
    listing = _run([*base, "config", "--null", "--get-regexp", r"^filter\."], cwd=cwd, env=env, timeout=timeout)
    for item in listing.stdout.split("\0"):
        key = item.split("\n", 1)[0]
        if key.startswith("filter.") and key.count(".") >= 2:
            name = key[len("filter."):key.rindex(".")]
            overrides += [(f"filter.{name}.{var}", "") for var in ("clean", "smudge", "process")]
    # GIT_CONFIG_KEY/VALUE carry any filter name verbatim (a '-c' value would split at '=').
    env["GIT_CONFIG_COUNT"] = str(len(overrides))
    for i, (k, v) in enumerate(overrides):
        env[f"GIT_CONFIG_KEY_{i}"] = k
        env[f"GIT_CONFIG_VALUE_{i}"] = v
    return _run([*base, *args], cwd=cwd, env=env, timeout=timeout, text=text, input=input)


def code_revision(repo: str | Path | None = None) -> str:
    """git describe of the ACES checkout; 'unversioned' when not running from one."""
    root = Path(repo) if repo is not None else REPO_ROOT
    if repo is None and not (root / "src" / "aces").is_dir():
        return "unversioned"
    if not (root / ".git").exists():
        return "unversioned"
    try:
        out = git(["describe", "--always", "--dirty", "--tags"], root)
    except (OSError, subprocess.SubprocessError):
        return "unversioned"
    return (out.stdout.strip() if out.returncode == 0 else "") or "unversioned"


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


# ----------------------------------------------------------------------------- locking

def _try_lock(fd: int, shared: bool) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # Windows has no shared lock here; readers lock exclusively
    else:
        import fcntl

        fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def _lock(path: Path, timeout_s: float = 30.0, *, shared: bool = False):
    """OS lock on the sibling ``<ledger>.lock``.  The file stays; only the lock on it comes and goes."""
    with safeio.OutDir(path.parent, mkdir=False) as d:
        fh = d.open_append(path.name + ".lock")
    try:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                _try_lock(fh.fileno(), shared)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"ledger lock {path}.lock held for more than {timeout_s} s") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(fh.fileno())
    finally:
        fh.close()


# ----------------------------------------------------------------------------- reading

def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"duplicate key {k!r}")
        out[k] = v
    return out


def _parse(body: bytes) -> dict[str, Any]:
    """One ledger line; ValueError unless it is a single JSON object with no repeated keys."""
    try:
        obj = json.loads(body, object_pairs_hook=_no_duplicates)
    except json.JSONDecodeError:
        raise ValueError("not valid JSON (truncated or edited)") from None
    except UnicodeDecodeError:
        raise ValueError("not valid UTF-8 JSON (truncated or edited)") from None
    if not isinstance(obj, dict):
        raise ValueError(f"not a JSON object (a {type(obj).__name__})")  # noqa: TRY004 - callers catch ValueError
    return obj


def _body(raw: bytes) -> bytes | None:
    """The line without its newline, or None for an unterminated line."""
    if not raw.endswith(b"\n"):
        return None
    body = raw[:-1]
    return body[:-1] if body.endswith(b"\r") else body


def _seals(raw_next: bytes | None, body: bytes) -> bool:
    """True when the next line is a seal record for this line's exact bytes."""
    nb = _body(raw_next) if raw_next else None
    if not nb or SEAL_KIND.encode() not in nb:
        return False
    try:
        nxt = _parse(nb)
    except ValueError:
        return False
    sealed = nxt.get("sealed_line")
    return (nxt.get("kind") == SEAL_KIND and isinstance(sealed, dict)
            and sealed.get("sha256") == hashlib.sha256(body).hexdigest())


def _hmac_key() -> bytes | None:
    key = os.environ.get(HMAC_ENV)
    if not key:
        return None
    if len(key) < MIN_KEY_CHARS:
        raise LedgerError(f"{HMAC_ENV} is shorter than {MIN_KEY_CHARS} characters; use a long random key")
    return key.encode()


def _sign(payload: dict[str, Any], key: bytes) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key, blob, hashlib.sha256).hexdigest()


class _Scan:
    """What one pass over the ledger found."""

    def __init__(self) -> None:
        self.ok = True
        self.problem = ""
        self.count = 0
        self.head = GENESIS
        self.sealed = 0
        self.partial: bytes | None = None  # an unterminated last line (interrupted append)
        self.positions: dict[str, int] = {}  # wanted hash -> 1-based record position

    def fail(self, msg: str) -> _Scan:
        self.ok, self.problem = False, msg
        return self


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @property
    def head_path(self) -> Path:
        return self.path.with_name(self.path.name + ".head")

    # ------------------------------------------------------------------ low level
    def _lines(self) -> Iterator[tuple[int, str, Any]]:
        """(line index, kind, value): kind is record, sealed, blank, bad, partial or too_long."""
        with self.path.open("rb") as fh:
            nxt = fh.readline(MAX_RECORD_BYTES + 1)
            i = 0
            while nxt:
                raw, nxt = nxt, fh.readline(MAX_RECORD_BYTES + 1)
                body = _body(raw)
                if body is None:
                    if len(raw) > MAX_RECORD_BYTES:
                        yield i, "too_long", None
                        return
                    yield i, "partial", raw
                elif not body.strip():
                    yield i, "blank", None
                elif _seals(nxt, body):
                    yield i, "sealed", body
                else:
                    try:
                        yield i, "record", _parse(body)
                    except ValueError as e:
                        yield i, "bad", str(e)
                i += 1

    def _scan(self, want: tuple[str, ...] = ()) -> _Scan:
        st = _Scan()
        for i, kind, val in self._lines():
            if kind == "blank":
                continue
            if kind == "sealed":
                st.sealed += 1
                continue
            if kind == "partial":
                st.partial = val
                return st.fail(f"record {i}: the last line has no newline, so an append was interrupted. The "
                               f"line is kept as written; close it with `aces ledger seal {self.path}` "
                               f"(it appends a seal record) before anything more is appended")
            if kind == "too_long":
                return st.fail(f"record {i}: longer than {MAX_RECORD_BYTES:,} bytes")
            if kind == "bad":
                return st.fail(f"record {i}: {val}")
            rec = val
            if rec.get("prev_hash") != st.head:
                return st.fail(f"record {i}: prev_hash does not match the previous record")
            if rec.get("record_hash") != canonical_hash({k: v for k, v in rec.items() if k != "record_hash"}):
                return st.fail(f"record {i}: contents changed after it was written")
            st.head = rec["record_hash"]
            st.count += 1
            if st.head in want:
                st.positions[st.head] = st.count
        return st

    def _read_signed_head(self, key: bytes) -> dict[str, Any] | str:
        """The signed head, or why it can't be used."""
        hp = self.head_path
        if not hp.exists() and not safeio.is_link(hp):
            return f"signed head {hp} is missing ({HMAC_ENV} is set)"
        try:
            head = _parse(safeio.read_text_limited(hp, 64 * 1024).strip().encode())
        except (OSError, ValueError) as e:
            return f"signed head {hp} can't be read: {e}"
        payload = {k: v for k, v in head.items() if k != "hmac"}
        if not isinstance(head.get("hmac"), str) or not hmac.compare_digest(head["hmac"], _sign(payload, key)):
            return f"signed head {hp} does not match its signature (edited, or signed with another key)"
        if payload.get("ledger") != self.path.name or not isinstance(payload.get("records"), int) \
                or not isinstance(payload.get("head"), str):
            return f"signed head {hp} does not belong to {self.path.name}"
        return payload

    def _check(self, *, expect_head: str | None = None, min_records: int | None = None,
               allow_partial: bool = False) -> _Scan:
        """Scan and apply the anchors.  Call with the lock held."""
        key = _hmac_key()
        signed = self._read_signed_head(key) if key is not None and self.path.exists() else None
        want = tuple(h for h in (expect_head, signed.get("head") if isinstance(signed, dict) else None) if h)
        st = self._scan(want)
        if not st.ok:
            if not (allow_partial and st.partial is not None):
                return st
            st.ok, st.problem = True, ""  # the interrupted last line is the only fault (seal)
        if isinstance(signed, str):
            if st.count or st.partial is not None:  # a new, empty ledger has no head yet
                return st.fail(signed + "; after checking the record count against your own copy of the head, "
                                        f"write a new one with `aces ledger head {self.path} --sign`")
        elif signed is not None:
            n, h = signed["records"], signed["head"]
            if st.positions.get(h) != n:
                return st.fail(f"the chain no longer reaches its signed head (record {n}, {h[:12]}): records were "
                               f"cut off the end, or the ledger was rewritten")
        if expect_head is not None and expect_head not in st.positions:
            return st.fail(f"expected head {expect_head[:12]} is not in the chain: records were cut off the end, or "
                           f"the ledger was rewritten")
        if min_records is not None and st.count < min_records:
            return st.fail(f"{st.count} records, fewer than the {min_records} expected: records were cut off the end")
        return st

    def _write_signed_head(self, count: int, head: str, key: bytes) -> None:
        payload = {"ledger": self.path.name, "records": count, "head": head,
                   "time_utc": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")}
        text = json.dumps({**payload, "hmac": _sign(payload, key)}, sort_keys=True) + "\n"
        with safeio.OutDir(self.path.parent, mkdir=False) as d:
            d.write_text(self.head_path.name, text, overwrite=True)

    def _append_line(self, data: bytes) -> None:
        created = not self.path.exists()
        with safeio.OutDir(self.path.parent, mkdir=False) as d:
            fh = d.open_append(self.path.name)
        with fh:
            fh.buffer.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if created and os.name == "posix":  # make the new directory entry durable too
            dfd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)

    # ------------------------------------------------------------------ public
    def records(self) -> list[dict[str, Any]]:
        """Every complete record in order (sealed lines are skipped).  Use :meth:`verify` to trust them."""
        if not self.path.exists():
            return []
        with _lock(self.path, shared=True):
            out = []
            for i, kind, val in self._lines():
                if kind == "record":
                    out.append(val)
                elif kind in ("bad", "too_long"):
                    raise LedgerError(f"ledger {self.path} record {i}: {val or 'too long'}")
            return out

    def find(self, record_hash: str) -> dict[str, Any] | None:
        return next((r for r in self.records() if r.get("record_hash") == record_hash), None)

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        if record.get("company") != COMPANY:
            raise ValueError(f"evidence must be recorded under {COMPANY}; got {record.get('company')!r}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        key = _hmac_key()
        with _lock(self.path):
            st = self._check() if self.path.exists() else _Scan()
            if not st.ok:
                raise LedgerError(f"ledger {self.path} does not verify, so nothing was appended: {st.problem}")
            body = {k: v for k, v in record.items() if k not in ("prev_hash", "record_hash")}
            body["prev_hash"] = st.head
            body["record_hash"] = canonical_hash({k: v for k, v in body.items() if k != "record_hash"})
            self._append_line((json.dumps(body, sort_keys=True, default=str) + "\n").encode())
            if key is not None:
                self._write_signed_head(st.count + 1, body["record_hash"], key)
        return body

    def seal(self) -> dict[str, Any]:
        """Close an interrupted last line: end it and append a seal record holding its sha256.

        The line's bytes stay in the file exactly as written (rule 10).  Refuses unless that line is the
        only fault.
        """
        if not self.path.exists():
            raise LedgerError(f"ledger {self.path} not found")
        key = _hmac_key()
        with _lock(self.path):
            st = self._check(allow_partial=True)
            if not st.ok:
                raise LedgerError(f"ledger {self.path} can't be sealed: {st.problem}")
            if st.partial is None:
                raise LedgerError(f"ledger {self.path} has no interrupted last line; nothing to seal")
            body = {
                "kind": SEAL_KIND,
                "company": COMPANY,
                "timestamp_utc": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
                "sealed_line": {"sha256": hashlib.sha256(st.partial).hexdigest(), "bytes": len(st.partial),
                                "preview": st.partial[:200].decode("utf-8", errors="replace")},
                "note": "the line before this record is an interrupted append, kept as written (rule 10)",
                "prev_hash": st.head,
            }
            body["record_hash"] = canonical_hash(body)
            self._append_line(b"\n" + (json.dumps(body, sort_keys=True) + "\n").encode())
            if key is not None:
                self._write_signed_head(st.count + 1, body["record_hash"], key)
        return body

    def head(self) -> tuple[int, str]:
        """(record count, head hash) of a ledger that verifies; LedgerError otherwise."""
        ok, msg, st = self._verify()
        if not ok:
            raise LedgerError(f"ledger {self.path} does not verify: {msg}")
        return st.count, st.head

    def sign_head(self) -> tuple[int, str]:
        """Write the signed head for the current chain (needs ``ACES_LEDGER_HMAC_KEY``)."""
        key = _hmac_key()
        if key is None:
            raise LedgerError(f"set {HMAC_ENV} (environment only) to sign the head")
        if not self.path.exists():
            raise LedgerError(f"ledger {self.path} not found")
        with _lock(self.path):
            st = self._scan()
            if not st.ok:
                raise LedgerError(f"ledger {self.path} does not verify: {st.problem}")
            if self.head_path.exists():
                signed = self._read_signed_head(key)
                if isinstance(signed, str):
                    raise LedgerError(signed + "; not replacing it")
                pos = self._scan((signed["head"],)).positions.get(signed["head"])
                if pos != signed["records"]:
                    raise LedgerError("the chain no longer reaches the existing signed head; not replacing it")
            if st.count == 0:
                raise LedgerError("ledger is empty")
            self._write_signed_head(st.count, st.head, key)
        return st.count, st.head

    def _verify(self, expect_head: str | None = None, min_records: int | None = None) -> tuple[bool, str, _Scan]:
        if not self.path.exists():
            return False, f"ledger {self.path} not found", _Scan()
        try:
            with _lock(self.path, shared=True):
                st = self._check(expect_head=expect_head, min_records=min_records)
        except (LedgerError, OSError) as e:
            return False, str(e), _Scan()
        if not st.ok:
            return False, st.problem, st
        if st.count == 0:
            return False, "ledger is empty", st
        msg = f"{st.count} records, chain intact, head {st.head[:12]}"
        if st.sealed:
            msg += f", {st.sealed} sealed interrupted line(s)"
        if expect_head is not None:
            later = st.count - st.positions[expect_head]
            msg += f"; expected head found, {later} record(s) appended since"
        if self.head_path.exists() and _hmac_key() is not None:
            msg += "; signed head checked"
        return True, msg, st

    def verify(self, *, expect_head: str | None = None, min_records: int | None = None) -> tuple[bool, str]:
        """Check the whole chain, and the anchors given (see the module docstring)."""
        ok, msg, _ = self._verify(expect_head, min_records)
        return ok, msg


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


class EvidenceError(ValueError):
    """A run folder or ledger record failed verification."""


def load_verified_run(run_dir: str | Path, ledger_path: str | Path | None = None,
                      artifacts: tuple[str, ...] = ("pareto",)) -> tuple[dict[str, Any], dict[str, Any]]:
    """The run manifest and its ledger record, after checking everything that ties them together (audit M2).

    Refuses unless:
    * ``run.json`` and each named artifact are regular files, not links;
    * the whole ledger chain verifies (so the record's own hash is recomputed, not trusted);
    * the record named in ``run.json`` is in that ledger, and its spec hash matches the manifest;
    * each named artifact still has the sha256 recorded when the run finished.

    Used by ``export-simlab`` and ``cost --from-run``.
    """
    run_dir = Path(run_dir)
    manifest_path = run_dir / "run.json"
    if manifest_path.is_symlink():
        raise EvidenceError(f"{manifest_path} is a symbolic link; refusing to use it")
    if not manifest_path.is_file():
        raise EvidenceError(f"{manifest_path} not found; this works on the output folder of a finished run")
    try:
        manifest = json.loads(safeio.read_text_limited(manifest_path, 1_000_000))
        record_hash = manifest["record_hash"]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        raise EvidenceError(f"{manifest_path} is not a valid run manifest: {e}") from e
    ledger = Ledger(ledger_path or manifest.get("ledger") or "")
    ok, msg = ledger.verify()
    if not ok:
        raise EvidenceError(f"ledger {ledger.path} does not verify ({msg}); refusing to use its records")
    rec = ledger.find(record_hash)
    if rec is None:
        raise EvidenceError(f"evidence record {str(record_hash)[:12]} is not in {ledger.path}")
    if manifest.get("spec_hash") not in (None, rec.get("spec_hash")):
        raise EvidenceError(f"{manifest_path} names a different spec from its evidence record")
    for name in artifacts:
        art = (rec.get("artifacts") or {}).get(name)
        if not art:
            raise EvidenceError(f"evidence record {record_hash[:12]} has no '{name}' artifact")
        p = run_dir / Path(art["path"]).name
        if p.is_symlink() or not p.is_file():
            raise EvidenceError(f"{p} is missing or a symbolic link")
        if file_sha256(p) != art["sha256"]:
            raise EvidenceError(f"{p} no longer matches its evidence record (edited after the run)")
    return manifest, rec
