"""Safe file reads under a root: the rules of build spec 3.13, shared by the golden, CAD, bundle, notes and static routes.

    data, content_type, etag = open_under(root, rel, max_bytes=None, kinds=None)   # or raises SafeFsError(reason, status)

1. Names. Project names match PROJECT_RE (a directory name under $ACES_WORK; aces.project.path at project.py:19-20
   joins the name without a check, so the console is the stricter party). CAD run ids match store.py:34
   (`<stamp>-<4 hex>`), golden run ids rundir.py's `<stamp>-<6 hex>` with an optional `-N` suffix, bundle names
   `<stamp>-<name>` with aces_cad/pull.py:26's NAME_RE. Relative paths split as store.py:63-69 (`_split_rel`): no
   leading slash, no backslash, no drive letter, no empty, `.` or `..` segment; each component
   `^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$`; at most 8 components.
2. Walk. Component by component without following links: on POSIX `os.open(..., O_NOFOLLOW | O_CLOEXEC, dir_fd=...)`
   and fstat (regular file only), mirroring store.py:85-126; on Windows (development only) lstat every component and
   refuse links and reparse points, then compare the opened file's identity. The resolved path must stay under root.
3. Size caps by kind: JSON 8 MiB (store.py:38); md/py/txt/csv 1 MiB; svg/dxf/html/ndjson 16 MiB; png/stl/step 64 MiB
   -> 413. `max_bytes` lowers a cap (a golden run.json is read with 1 MiB).
4. Never served: dotfiles, names ending .env/.key/.pem, names containing "token" (any case) -> 403.
5. Headers (`headers_for`): Content-Security-Policy: sandbox, X-Content-Type-Options: nosniff,
   Content-Disposition: inline; filename="<base>", Cache-Control: private, max-age=60, ETag: "<sha256>".
6. Content types are an allow-list (KINDS): application/json, text/plain; charset=utf-8, text/html; charset=utf-8,
   image/svg+xml, image/png, image/vnd.dxf, model/stl, application/step, application/x-ndjson. Anything else -> 415.
   application/octet-stream ("bin": .joblib/.pkl/.npz/.brep) is served only when the caller lists "bin" in `kinds`;
   it is never loaded, only streamed.
"""
from __future__ import annotations

import errno
import hashlib
import os
import re
import stat
from pathlib import Path
from typing import Iterable

__all__ = ["SafeFsError", "open_under", "headers_for", "split_rel", "classify", "check_project", "check_run_id",
           "check_golden_run_id", "check_bundle_name", "KINDS", "DEFAULT_KINDS", "STATIC_KINDS"]

PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
CAD_RUN_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{4}$")           # store.py:34
GOLDEN_RUN_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}(-[0-9]{1,3})?$")  # rundir.py:7-10
BUNDLE_NAME_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")  # <stamp>-<pull.py:26 NAME_RE>
COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
MAX_COMPONENTS = 8
FORBIDDEN_SUFFIXES = (".env", ".key", ".pem")

MiB = 1 << 20
# kind -> (content type, size cap, extensions)
KINDS: dict[str, tuple[str, int, tuple[str, ...]]] = {
    "json": ("application/json", 8 * MiB, (".json",)),
    "text": ("text/plain; charset=utf-8", 1 * MiB, (".md", ".py", ".txt", ".csv", ".log", ".yaml", ".yml", ".toml", ".sha256")),
    "html": ("text/html; charset=utf-8", 16 * MiB, (".html", ".htm")),
    "svg": ("image/svg+xml", 16 * MiB, (".svg",)),
    "png": ("image/png", 64 * MiB, (".png",)),
    "dxf": ("image/vnd.dxf", 16 * MiB, (".dxf",)),
    "stl": ("model/stl", 64 * MiB, (".stl",)),
    "step": ("application/step", 64 * MiB, (".step", ".stp")),
    "ndjson": ("application/x-ndjson", 16 * MiB, (".jsonl", ".ndjson")),
    "bin": ("application/octet-stream", 64 * MiB, (".joblib", ".pkl", ".npz", ".brep")),
}
DEFAULT_KINDS = frozenset(k for k in KINDS if k != "bin")
# the static tree of the page itself (scripts, styles, fonts); served by server.py through the same walker
STATIC_KINDS: dict[str, tuple[str, int, tuple[str, ...]]] = {
    "js": ("text/javascript; charset=utf-8", 4 * MiB, (".js", ".mjs")),
    "css": ("text/css; charset=utf-8", 1 * MiB, (".css",)),
    "html": ("text/html; charset=utf-8", 1 * MiB, (".html",)),
    "json": ("application/json", 1 * MiB, (".json", ".map", ".webmanifest")),
    "svg": ("image/svg+xml", 1 * MiB, (".svg",)),
    "png": ("image/png", 4 * MiB, (".png",)),
    "ico": ("image/x-icon", 256 * 1024, (".ico",)),
    "woff2": ("font/woff2", 2 * MiB, (".woff2",)),
    "woff": ("font/woff", 2 * MiB, (".woff",)),
    "text": ("text/plain; charset=utf-8", 1 * MiB, (".txt", ".md")),
}

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_O_BINARY = getattr(os, "O_BINARY", 0)
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_POSIX_DIRFD = os.name == "posix" and os.open in os.supports_dir_fd


class SafeFsError(Exception):
    """A refused or failed read: `reason` is one line for the error envelope, `status` the HTTP status (400 malformed
    path, 403 refused, 404 absent, 413 too large, 415 type not allowed, 500 unreadable)."""

    def __init__(self, reason: str, status: int):
        super().__init__(reason)
        self.reason = reason
        self.status = status


def check_project(name: object) -> str:
    if not isinstance(name, str) or not PROJECT_RE.match(name) or name in (".", ".."):
        raise SafeFsError(f"project name {str(name)[:40]!r} is not a plain directory name", 400)
    return name


def check_run_id(run_id: object) -> str:
    """A CAD run id (store.py:34)."""
    if not isinstance(run_id, str) or not CAD_RUN_ID_RE.match(run_id):
        raise SafeFsError(f"run id {str(run_id)[:40]!r} does not match {CAD_RUN_ID_RE.pattern}", 400)
    return run_id


def check_golden_run_id(run_id: object) -> str:
    """A golden run id (rundir.py:7-10)."""
    if not isinstance(run_id, str) or not GOLDEN_RUN_ID_RE.match(run_id):
        raise SafeFsError(f"run id {str(run_id)[:40]!r} does not match {GOLDEN_RUN_ID_RE.pattern}", 400)
    return run_id


def check_bundle_name(name: object) -> str:
    if not isinstance(name, str) or not BUNDLE_NAME_RE.match(name):
        raise SafeFsError(f"bundle name {str(name)[:48]!r} does not match {BUNDLE_NAME_RE.pattern}", 400)
    return name


def split_rel(rel: object) -> list[str]:
    """The components of a project-relative path (rules 1 and 4)."""
    if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")) or "\\" in rel or "\x00" in rel:
        raise SafeFsError(f"{str(rel)[:80]!r} is not a relative path", 400)
    if re.match(r"^[A-Za-z]:", rel):
        raise SafeFsError(f"{rel[:80]!r} is not a relative path", 400)
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise SafeFsError(f"{rel[:80]!r} has an empty, '.' or '..' segment", 400)
    if len(parts) > MAX_COMPONENTS:
        raise SafeFsError(f"{rel[:80]!r} has more than {MAX_COMPONENTS} components", 400)
    for p in parts:
        if not COMPONENT_RE.match(p):
            raise SafeFsError(f"path component {p[:80]!r} is not allowed", 400 if p.startswith(".") is False else 403)
        if p.startswith("."):
            raise SafeFsError(f"refusing a dotfile: {p[:80]}", 403)
        low = p.lower()
        if low.endswith(FORBIDDEN_SUFFIXES) or "token" in low:
            raise SafeFsError(f"refusing to read {p[:80]}: credential-looking name", 403)
    return parts


def classify(name: str, table: dict | None = None, kinds: Iterable[str] | None = None) -> tuple[str, str, int]:
    """(kind, content type, size cap) for a file name, from the allow-list `table` (KINDS by default) limited to
    `kinds` (DEFAULT_KINDS by default: everything but "bin")."""
    table = KINDS if table is None else table
    allowed = (DEFAULT_KINDS if table is KINDS else frozenset(table)) if kinds is None else frozenset(kinds)
    low = name.lower()
    for kind, (ctype, cap, exts) in table.items():
        if low.endswith(exts):
            if kind not in allowed:
                raise SafeFsError(f"{name[:80]}: content type {ctype} is not served by this route", 415)
            return kind, ctype, cap
    raise SafeFsError(f"{name[:80]}: file type not in the content-type allow-list", 415)


def _is_link(st: os.stat_result) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def _open_nofollow(base: Path, parts: list[str], rel: str) -> int:
    """An fd for base/parts opened component by component without following links (store.py:85-126)."""
    if _POSIX_DIRFD:
        try:
            dfd = os.open(str(base), os.O_RDONLY | _O_DIRECTORY | _O_CLOEXEC)
        except FileNotFoundError:
            raise SafeFsError(f"{rel}: root {base} does not exist", 404) from None
        try:
            for comp in parts[:-1]:
                try:
                    nfd = os.open(comp, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC, dir_fd=dfd)
                except OSError as e:
                    if e.errno in (errno.ELOOP, errno.ENOTDIR):
                        raise SafeFsError(f"refusing to read {rel}: {comp} is a link or not a directory", 403) from None
                    raise
                os.close(dfd)
                dfd = nfd
            try:
                return os.open(parts[-1], os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK | _O_CLOEXEC, dir_fd=dfd)
            except OSError as e:
                if e.errno == errno.ELOOP:
                    raise SafeFsError(f"refusing to read {rel}: it is a symbolic link", 403) from None
                raise
        finally:
            os.close(dfd)
    # Windows (development): lstat every component, refuse links and reparse points, compare identities after open
    cur = Path(base)
    for comp in parts:
        cur = cur / comp
        st = os.lstat(cur)
        if _is_link(st):
            raise SafeFsError(f"refusing to read {rel}: {comp} is a link or reparse point", 403)
    before = os.lstat(cur)
    fd = os.open(str(cur), os.O_RDONLY | _O_BINARY)
    after = os.fstat(fd)
    if before.st_ino and (before.st_ino, before.st_dev) != (after.st_ino, after.st_dev):
        os.close(fd)
        raise SafeFsError(f"refusing to read {rel}: it changed while it was opened", 403)
    return fd


def _under(root: Path, target: Path) -> bool:
    try:
        r = os.path.normcase(os.path.realpath(root))
        t = os.path.normcase(os.path.realpath(target))
        return os.path.commonpath([r, t]) == r
    except ValueError:  # different drives on Windows
        return False


def open_under(root: str | Path, rel: str, max_bytes: int | None = None, kinds: Iterable[str] | None = None,
               table: dict | None = None) -> tuple[bytes, str, str]:
    """Read root/rel under the rules above. Returns (bytes, content type, sha256 hex) or raises SafeFsError."""
    root = Path(root)
    parts = split_rel(rel)
    kind, ctype, cap = classify(parts[-1], table, kinds)
    if max_bytes is not None:
        cap = min(cap, int(max_bytes))
    try:
        fd = _open_nofollow(root, parts, rel)
    except SafeFsError:
        raise
    except FileNotFoundError:
        raise SafeFsError(f"{rel} does not exist", 404) from None
    except NotADirectoryError:
        raise SafeFsError(f"{rel} does not exist", 404) from None
    except PermissionError:
        raise SafeFsError(f"refusing to read {rel}: permission denied", 403) from None
    except OSError as e:
        raise SafeFsError(f"cannot read {rel}: {e.strerror or type(e).__name__}", 500) from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise SafeFsError(f"refusing to read {rel}: not a regular file", 403)
        if st.st_size > cap:
            raise SafeFsError(f"{rel} is {st.st_size} bytes; the cap for {kind} is {cap}", 413)
        if not _under(root, root.joinpath(*parts)):
            raise SafeFsError(f"refusing to read {rel}: resolves outside {root}", 403)
        chunks = []
        total = 0
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if total > cap:
                raise SafeFsError(f"{rel} grew past the cap of {cap} bytes while it was read", 413)
            chunks.append(chunk)
    except OSError as e:
        raise SafeFsError(f"cannot read {rel}: {e.strerror or type(e).__name__}", 500) from None
    finally:
        os.close(fd)
    data = b"".join(chunks)
    return data, ctype, hashlib.sha256(data).hexdigest()


def headers_for(content_type: str, etag: str, basename: str, sandbox: bool = True) -> dict[str, str]:
    """The response headers of rule 5 for a served file."""
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", basename)[:120] or "file"
    hdrs = {"Content-Type": content_type, "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'inline; filename="{safe_name}"', "Cache-Control": "private, max-age=60",
            "ETag": f'"{etag}"'}
    if sandbox:
        hdrs["Content-Security-Policy"] = "sandbox"
    return hdrs
