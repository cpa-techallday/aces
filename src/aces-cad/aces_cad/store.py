"""The run store: <ACES_WORK>/<project>/cad/local/runs/<run_id>/ (section 9 of the build spec).

    script.py            exactly the received text, UTF-8 (parent)
    run.json             parent record (atomic)
    job.json             the run job as sent (debug copy)
    inputs/<name><ext>   input copies (parent)
    out/                 parts.json, p<i>.brep|npz, d<i>.brep, promoted from the run job
    inspect/ renders/ drawings/
    jobs/<op>-<8 hex>/   transient job directories (sandbox.new_job)

Every directory is created 0700. Run ids are <YYYYmmddTHHMMSSZ>-<4 hex>, created with an exclusive mkdir; the hex part
is drawn above the ids already taken in the same second, so the lexical order of ids is their creation order and
"latest" is well defined. Every parent read inside a run directory goes through safe_read/sha256_file (each path
component opened with O_NOFOLLOW, a regular file with one link); parent writes go to a temporary file and os.replace.
Files written by a worker reach the run directory only through promote(), after their type, link count and size are
checked.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import time
from datetime import datetime, timezone
from pathlib import Path

from .errors import CadError, IntegrityError, NotFoundError, UsageError

RUN_ID_RE = r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{4}$"
RUN_REF_RE = r"^(latest|[0-9]{8}T[0-9]{6}Z-[0-9a-f]{4})$"
RELPATH_RE = r"^[A-Za-z0-9_][A-Za-z0-9._/-]{0,255}$"
RUN_OUTPUT_RE = r"^(parts\.json|[pd][0-9]{1,2}\.(brep|npz))$"
RUN_JSON_MAX = 8 << 20
SOURCE_JSON_MAX = 1 << 20
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_O_BINARY = getattr(os, "O_BINARY", 0)
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_POSIX_DIRFD = os.name == "posix" and os.open in os.supports_dir_fd


class _Unsafe(Exception):
    """A path component that is a link, a file that is not regular, has more than one link or is too large."""


def utc_stamp(when: datetime | None = None) -> str:
    return (when or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def utc_iso(when: datetime | None = None) -> str:
    return (when or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- safe file I/O --------------------------------------------------------------------------------------------------

def _split_rel(rel: str) -> list[str]:
    if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")) or "\\" in rel or "\x00" in rel:
        raise _Unsafe(f"{rel!r} is not a relative path")
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts) or re.match(r"^[A-Za-z]:", rel):
        raise _Unsafe(f"{rel!r} is not a plain relative path")
    return parts


def _is_link(st: os.stat_result) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def _check_file(st: os.stat_result, rel: str, max_bytes: int | None, need_single_link: bool) -> None:
    if not stat.S_ISREG(st.st_mode):
        raise _Unsafe(f"{rel} is not a regular file")
    if need_single_link and st.st_nlink != 1:
        raise _Unsafe(f"{rel} has {st.st_nlink} hard links")
    if max_bytes is not None and st.st_size > max_bytes:
        raise _Unsafe(f"{rel} is larger than {max_bytes} bytes ({st.st_size})")


def _open_file(base: Path, rel: str, *, max_bytes: int | None, need_single_link: bool = True) -> int:
    """An fd for base/rel, opened component by component without following links; raises _Unsafe or OSError."""
    parts = _split_rel(rel)
    if _POSIX_DIRFD:
        dfd = os.open(str(base), os.O_RDONLY | _O_DIRECTORY | _O_CLOEXEC)
        try:
            for comp in parts[:-1]:
                try:
                    nfd = os.open(comp, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC, dir_fd=dfd)
                except OSError as e:
                    if e.errno in (errno.ELOOP, errno.ENOTDIR):
                        raise _Unsafe(f"{rel}: {comp} is a link or not a directory") from None
                    raise
                os.close(dfd)
                dfd = nfd
            try:
                fd = os.open(parts[-1], os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK | _O_CLOEXEC, dir_fd=dfd)
            except OSError as e:
                if e.errno == errno.ELOOP:
                    raise _Unsafe(f"{rel} is a symbolic link") from None
                raise
        finally:
            os.close(dfd)
    else:  # Windows: lstat every component and refuse links and reparse points, then compare the opened file
        cur = Path(base)
        for comp in parts:
            cur = cur / comp
            st = os.lstat(cur)
            if _is_link(st):
                raise _Unsafe(f"{rel}: {comp} is a link or reparse point")
        before = os.lstat(cur)
        fd = os.open(str(cur), os.O_RDONLY | _O_BINARY)
        after = os.fstat(fd)
        if before.st_ino and (before.st_ino, before.st_dev) != (after.st_ino, after.st_dev):
            os.close(fd)
            raise _Unsafe(f"{rel} changed while it was opened")
    try:
        _check_file(os.fstat(fd), rel, max_bytes, need_single_link)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _read_fd(fd: int, max_bytes: int, rel: str) -> bytes:
    chunks = []
    total = 0
    while True:
        chunk = os.read(fd, min(1 << 20, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise _Unsafe(f"{rel} is larger than {max_bytes} bytes")
    return b"".join(chunks)


def safe_read(base: Path, rel: str, max_bytes: int) -> bytes:
    """base/rel, opened per component with O_NOFOLLOW: a regular file with one link and at most max_bytes bytes.

    NotFoundError when it does not exist; IntegrityError when it is a link, not regular, linked twice or too large.
    """
    try:
        fd = _open_file(Path(base), rel, max_bytes=max_bytes)
    except _Unsafe as e:
        raise IntegrityError(f"refusing to read {e}") from None
    except FileNotFoundError:
        raise NotFoundError(f"{rel} does not exist in {base}") from None
    except OSError as e:
        raise IntegrityError(f"cannot read {rel} in {base}: {e.strerror or type(e).__name__}") from None
    try:
        return _read_fd(fd, max_bytes, rel)
    except _Unsafe as e:
        raise IntegrityError(f"refusing to read {e}") from None
    finally:
        os.close(fd)


def sha256_file(base: Path, rel: str) -> tuple[str, int]:
    """(sha256 hex, size) of base/rel, opened as safe_read opens it."""
    try:
        fd = _open_file(Path(base), rel, max_bytes=None)
    except _Unsafe as e:
        raise IntegrityError(f"refusing to hash {e}") from None
    except FileNotFoundError:
        raise NotFoundError(f"{rel} does not exist in {base}") from None
    except OSError as e:
        raise IntegrityError(f"cannot read {rel} in {base}: {e.strerror or type(e).__name__}") from None
    h = hashlib.sha256()
    size = 0
    try:
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    finally:
        os.close(fd)
    return h.hexdigest(), size


def check_relpath(rel, what: str = "path") -> str:
    """A project-relative path: ^[A-Za-z0-9_][A-Za-z0-9._/-]{0,255}$, no '.' or '..' segment, not absolute."""
    if not isinstance(rel, str) or not re.fullmatch(RELPATH_RE, rel):
        raise UsageError(f"{what} must be a project-relative path matching {RELPATH_RE}; got {rel!r:.80}")
    if any(seg in ("", ".", "..") for seg in rel.split("/")):
        raise UsageError(f"{what} {rel!r} has an empty, '.' or '..' segment")
    return rel


def read_project_file(project_dir: Path, rel: str, *, max_bytes: int, exts: tuple[str, ...]) -> bytes:
    """A regular file inside the project, resolved component by component without following links (section 7.6)."""
    check_relpath(rel)
    ext = os.path.splitext(rel)[1].lower()
    if ext not in exts:
        raise UsageError(f"{rel} must end in one of {', '.join(exts)}")
    try:
        fd = _open_file(Path(project_dir), rel, max_bytes=max_bytes, need_single_link=False)
    except _Unsafe as e:
        raise UsageError(f"refusing {e}: project files are read without following links") from None
    except (FileNotFoundError, NotADirectoryError):
        raise NotFoundError(f"no file {rel} in the project ({project_dir})") from None
    except OSError as e:
        raise UsageError(f"cannot read {rel}: {e.strerror or type(e).__name__}") from None
    try:
        return _read_fd(fd, max_bytes, rel)
    except _Unsafe as e:
        raise UsageError(f"refusing {e}") from None
    finally:
        os.close(fd)


def copy_project_file(project_dir: Path, rel: str, dest: Path, *, max_bytes: int, exts: tuple[str, ...]) -> dict:
    """Copy a project file (read as read_project_file reads it) to dest (created exclusively, 0600), streaming:
    {"sha256", "bytes"}."""
    check_relpath(rel)
    ext = os.path.splitext(rel)[1].lower()
    if ext not in exts:
        raise UsageError(f"{rel} must end in one of {', '.join(exts)}")
    try:
        fd = _open_file(Path(project_dir), rel, max_bytes=max_bytes, need_single_link=False)
    except _Unsafe as e:
        raise UsageError(f"refusing {e}: project files are read without following links") from None
    except (FileNotFoundError, NotADirectoryError):
        raise NotFoundError(f"no file {rel} in the project ({project_dir})") from None
    except OSError as e:
        raise UsageError(f"cannot read {rel}: {e.strerror or type(e).__name__}") from None
    h = hashlib.sha256()
    size = 0
    try:
        out = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC | _O_BINARY, 0o600)
        try:
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise UsageError(f"{rel} is larger than {max_bytes} bytes")
                h.update(chunk)
                view = memoryview(chunk)
                while view:
                    view = view[os.write(out, view):]
        finally:
            os.close(out)
    finally:
        os.close(fd)
    return {"sha256": h.hexdigest(), "bytes": size}


def write_atomic(path: Path, data: bytes) -> None:
    """Write data to a temporary file next to path, then os.replace it (mode 0600)."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC | _O_BINARY, 0o600)
    try:
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
    except BaseException:
        os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.close(fd)
    os.replace(tmp, path)


def dumps(obj) -> bytes:
    """The JSON of every record aces-cad writes: ASCII, one-space indent, final newline."""
    return (json.dumps(obj, ensure_ascii=True, indent=1, allow_nan=False) + "\n").encode("ascii")


def write_json_atomic(path: Path, obj) -> None:
    write_atomic(path, dumps(obj))


def ensure_subdir(base: Path, name: str) -> Path:
    """base/name as a real directory (created 0700): a symlink or file of that name is replaced by the directory."""
    path = Path(base) / name
    for _ in range(3):
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            st = None
        if st is not None and stat.S_ISDIR(st.st_mode) and not _is_link(st):
            return path
        if st is not None:
            try:
                os.unlink(path)
            except (IsADirectoryError, PermissionError):
                os.rmdir(path)  # a Windows directory link or junction
        try:
            os.mkdir(path, 0o700)
            return path
        except FileExistsError:
            continue
    raise IntegrityError(f"cannot make {path} a directory")


def remove_tree(path: Path) -> None:
    """Delete a directory tree without following links, after making its real directories writable again."""
    path = Path(path)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(st.st_mode) or _is_link(st):
        os.unlink(path)
        return
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    for root, dirs, _files in os.walk(path, topdown=True, followlinks=False):
        for d in dirs:
            p = os.path.join(root, d)
            try:
                if stat.S_ISDIR(os.lstat(p).st_mode):
                    os.chmod(p, 0o700)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)
    if os.path.lexists(path):
        shutil.rmtree(path)


# --- promotion --------------------------------------------------------------------------------------------------------

def promote(src_dir: Path, names: list[str], dest_dir: Path, *, max_file_bytes: int, max_total_bytes: int) -> dict:
    """Move vetted files from a job's output to dest_dir: {name: {"path", "sha256", "bytes"}}.

    Each name must be a plain file name; each file a regular file (lstat) with one link and at most max_file_bytes,
    all together at most max_total_bytes. Anything else raises IntegrityError and nothing is moved.
    """
    src_dir, dest_dir = Path(src_dir), Path(dest_dir)
    total = 0
    for name in names:
        if not isinstance(name, str) or not name or "/" in name or "\\" in name or name in (".", ".."):
            raise IntegrityError(f"the job output name {name!r} is not a plain file name")
        try:
            st = os.lstat(src_dir / name)
        except FileNotFoundError:
            raise IntegrityError(f"the job output {name} is missing") from None
        try:
            _check_file(st, name, max_file_bytes, True)
        except _Unsafe as e:
            raise IntegrityError(f"refusing the job output: {e}") from None
        if _is_link(st):
            raise IntegrityError(f"refusing the job output: {name} is a link")
        total += st.st_size
    if total > max_total_bytes:
        raise IntegrityError(f"the job outputs total {total} bytes, above the {max_total_bytes} byte cap")
    out = {}
    for name in names:
        try:
            os.chmod(src_dir / name, 0o600)  # a regular file (checked above); the worker may have changed its mode
        except OSError:
            pass
        sha, size = sha256_file(src_dir, name)
        out[name] = {"sha256": sha, "bytes": size}
    os.makedirs(dest_dir, mode=0o700, exist_ok=True)
    for name in names:
        os.replace(src_dir / name, dest_dir / name)
        out[name] = {"path": str(dest_dir / name), **out[name]}
    return out


# --- runs ------------------------------------------------------------------------------------------------------------

def runs_dir(info: dict, *, create: bool = False) -> Path:
    """<project>/cad/local/runs (created, links replaced, when create is true)."""
    pdir = Path(info["dir"])
    if not create:
        return pdir / "cad" / "local" / "runs"
    return ensure_subdir(ensure_subdir(ensure_subdir(pdir, "cad"), "local"), "runs")


class Run:
    """One run directory: id, root (the directory) and info (the project's gate result)."""

    def __init__(self, info: dict, run_id: str, root: Path):
        self.id = run_id
        self.root = Path(root)
        self.info = info

    def __repr__(self) -> str:
        return f"Run({self.id!r})"

    @property
    def out_dir(self) -> Path:
        return self.root / "out"

    def record(self) -> dict:
        data = safe_read(self.root, "run.json", RUN_JSON_MAX)
        try:
            rec = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise IntegrityError(f"run {self.id}: run.json is not valid JSON") from None
        if not isinstance(rec, dict):
            raise IntegrityError(f"run {self.id}: run.json is not a JSON object")
        return rec

    def write_record(self, data: dict) -> None:
        write_json_atomic(self.root / "run.json", data)


def run_ids(info: dict) -> list[str]:
    """The project's run ids, newest first (real directories only)."""
    rdir = runs_dir(info)
    try:
        entries = list(os.scandir(rdir))
    except (FileNotFoundError, NotADirectoryError):
        return []
    ids = [e.name for e in entries if re.fullmatch(RUN_ID_RE, e.name) and e.is_dir(follow_symlinks=False)]
    return sorted(ids, reverse=True)


def new_run(info: dict) -> Run:
    """A new run directory with a fresh id (exclusive mkdir, retried)."""
    rdir = runs_dir(info, create=True)
    for _ in range(200):
        stamp = utc_stamp()
        taken = [int(name[-4:], 16) for name in run_ids(info) if name.startswith(stamp)]
        low = max(taken) + 1 if taken else 0
        if low > 0xFFFF:
            time.sleep(0.05)
            continue
        run_id = f"{stamp}-{low + secrets.randbelow(min(0x10000 - low, 0x1000)):04x}"
        root = rdir / run_id
        try:
            os.mkdir(root, 0o700)
        except FileExistsError:
            continue
        return Run(info, run_id, root)
    raise IntegrityError(f"could not create a run directory in {rdir}")


def open_run(info: dict, run_id: str, *, require_ok: bool) -> Run:
    """An existing run. "latest" is the newest run whose run.json status is ok (require_ok) or the newest of any
    status; an explicit id must exist and, with require_ok, have succeeded (UsageError otherwise)."""
    if run_id == "latest":
        for rid in run_ids(info):
            run = Run(info, rid, runs_dir(info) / rid)
            try:
                rec = run.record()
            except CadError:
                continue
            if not require_ok or rec.get("status") == "ok":
                return run
        what = "successful run" if require_ok else "run"
        raise NotFoundError(f"project {info['name']} has no {what} yet: run a script with cad_run first")
    if not isinstance(run_id, str) or not re.fullmatch(RUN_ID_RE, run_id):
        raise UsageError(f"a run id looks like 20261010T133514Z-a3f9 (or latest); got {run_id!r:.60}")
    root = runs_dir(info) / run_id
    try:
        st = os.lstat(root)
    except (FileNotFoundError, NotADirectoryError):
        st = None
    if st is None or not stat.S_ISDIR(st.st_mode) or _is_link(st):
        raise NotFoundError(f"project {info['name']} has no run {run_id}")
    run = Run(info, run_id, root)
    if require_ok:
        rec = run.record()
        if rec.get("status") != "ok":
            err = rec.get("error") if isinstance(rec.get("error"), dict) else {}
            raise UsageError(f"run {run_id} did not succeed (status {rec.get('status')}, kind "
                             f"{err.get('kind', 'unknown')}): fix the script and run it again")
    return run


def summarize_record(rec: dict) -> dict:
    """One row of a run listing: {"run_id","name","status","kind","created_at","params","volume_mm3","valid",
    "error_kind"}."""
    result = rec.get("result") if isinstance(rec.get("result"), dict) else {}
    parts = result.get("parts") if isinstance(result.get("parts"), list) else []
    totals = result.get("totals") if isinstance(result.get("totals"), dict) else {}
    err = rec.get("error") if isinstance(rec.get("error"), dict) else None
    valid = None
    if parts:
        valid = all(isinstance(p, dict) and p.get("valid") is True for p in parts)
    return {"run_id": rec.get("run_id"), "name": rec.get("name"), "status": rec.get("status"),
            "kind": result.get("kind"), "created_at": rec.get("created_at"), "params": rec.get("params"),
            "volume_mm3": totals.get("volume_mm3"), "valid": valid,
            "error_kind": err.get("kind") if err else None}


def list_runs(info: dict, limit: int) -> list[dict]:
    """The newest `limit` runs as listing rows (a run whose run.json cannot be read is listed as unreadable)."""
    rows = []
    for rid in run_ids(info)[:max(0, int(limit))]:
        try:
            rec = Run(info, rid, runs_dir(info) / rid).record()
            row = summarize_record(rec)
            row["run_id"] = rid
        except CadError as e:
            row = {"run_id": rid, "name": None, "status": "unreadable", "kind": None, "created_at": None,
                   "params": None, "volume_mm3": None, "valid": None, "error_kind": e.kind}
        rows.append(row)
    return rows


def pulled_run_ids(info: dict) -> set[str]:
    """Run ids named by any cad/local/*/source.json (prune keeps them)."""
    local = Path(info["dir"]) / "cad" / "local"
    out: set[str] = set()
    try:
        entries = list(os.scandir(local))
    except (FileNotFoundError, NotADirectoryError):
        return out
    for e in entries:
        if e.name == "runs" or not e.is_dir(follow_symlinks=False):
            continue
        try:
            data = json.loads(safe_read(Path(e.path), "source.json", SOURCE_JSON_MAX).decode("utf-8"))
        except (CadError, UnicodeDecodeError, ValueError, RecursionError):
            continue
        rid = data.get("run_id") if isinstance(data, dict) else None
        if isinstance(rid, str) and re.fullmatch(RUN_ID_RE, rid):
            out.add(rid)
    return out


def prune(info: dict, keep: int) -> dict:
    """Delete runs beyond the newest `keep`, except runs that a pull bundle's source.json names."""
    ids = run_ids(info)
    protected = pulled_run_ids(info)
    deleted, kept_pulled = [], []
    for rid in ids[keep:]:
        if rid in protected:
            kept_pulled.append(rid)
            continue
        remove_tree(runs_dir(info) / rid)
        deleted.append(rid)
    return {"kept": ids[:keep], "kept_pulled": kept_pulled, "deleted": deleted}
