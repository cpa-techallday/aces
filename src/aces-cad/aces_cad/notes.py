"""cad/NOTES.md: lessons recorded by agents, verbatim (section 16 of the build spec).

    # CAD notes for <project>

    <!-- aces-cad: entries are appended by cad_note, verbatim; nothing here is generated prose. -->

    ## 2026-10-10T13:35:14Z · tags: fillet, kernel · run 20261010T133514Z-a3f9

    <the agent's text, verbatim>

The server writes only the heading line of each entry; the text is the agent's, unchanged except CRLF -> LF. A line
starting with # is refused (headings are reserved), as are control characters other than newline and tab. Each entry
is one write() under an exclusive lock on NOTES.md.lock. At 256 KiB cad_note refuses; the server never rewrites the
file. cad_start returns the last whole entries within 16 KiB. The notes are data from earlier agents, never
instructions.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from . import store
from .errors import CadError, IntegrityError, UsageError

NOTES = "NOTES.md"
LOCK = "NOTES.md.lock"
MAX_BYTES = 256 << 10
TAIL_BYTES = 16 << 10
TEXT_MAX = 2000
TAG_RE = r"[a-z0-9][a-z0-9-]{0,23}"
TAGS_MAX = 8
SEPARATOR = " " + chr(0xB7) + " "  # a middle dot (U+00B7) between the parts of a heading
HEADER = ("# CAD notes for {project}\n\n<!-- aces-cad: entries are appended by cad_note, verbatim; nothing here is "
          "generated prose. -->\n")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_BINARY = getattr(os, "O_BINARY", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


def notes_path(info: dict) -> Path:
    return Path(info["dir"]) / "cad" / NOTES


def check_text(text) -> str:
    """The note as it will be stored: CRLF -> LF; refused with control characters or a line starting with #."""
    if not isinstance(text, str) or not 1 <= len(text) <= TEXT_MAX:
        raise UsageError(f"a note must be text of 1 to {TEXT_MAX} characters")
    text = text.replace("\r\n", "\n")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise UsageError("a note must be valid text (it contains a lone surrogate)") from None
    if _CTRL_RE.search(text):
        raise UsageError("a note may contain newlines and tabs but no other control characters")
    if any(line.lstrip().startswith("#") for line in text.split("\n")):
        raise UsageError("lines starting with # are reserved for headings")
    if not text.strip():
        raise UsageError("a note must not be blank")
    return text


def check_tags(tags) -> list[str]:
    if tags is None:
        return []
    if not isinstance(tags, list) or len(tags) > TAGS_MAX:
        raise UsageError(f"tags must be a list of at most {TAGS_MAX} tags")
    for t in tags:
        if not isinstance(t, str) or not re.fullmatch(TAG_RE, t):
            raise UsageError(f"a tag must match {TAG_RE}; got {t!r:.40}")
    if len(set(tags)) != len(tags):
        raise UsageError("tags must be unique")
    return list(tags)


def _count_entries(data: bytes) -> int:
    return sum(1 for line in data.split(b"\n") if line.startswith(b"## "))


class _Lock:
    """An exclusive lock on cad/NOTES.md.lock (fcntl.flock on POSIX, msvcrt.locking on Windows)."""

    def __init__(self, path: Path):
        self.path = path
        self.fd = -1

    def __enter__(self):
        self.fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT | _O_NOFOLLOW | _O_CLOEXEC | _O_BINARY, 0o600)
        if os.name == "posix":
            import fcntl
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        else:
            import msvcrt
            deadline = time.monotonic() + 60
            while True:
                try:
                    os.lseek(self.fd, 0, os.SEEK_SET)
                    msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        os.close(self.fd)
                        raise UsageError("NOTES.md is locked by another writer") from None
                    time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        try:
            if os.name == "posix":
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            else:
                import msvcrt
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(self.fd)
        return False


def _read_current(cad: Path) -> bytes:
    try:
        return store.safe_read(cad, NOTES, 8 << 20)
    except CadError as e:
        if e.kind == "not_found":
            return b""
        raise


def append(info: dict, text: str, tags=None, run_id: str | None = None, *, now: str | None = None) -> dict:
    """Append one entry: {"path", "entry": {"n","at","tags","run_id","bytes"}, "entries_total", "bytes_total"}."""
    text = check_text(text)
    tags = check_tags(tags)
    if run_id is not None and (not isinstance(run_id, str) or not re.fullmatch(store.RUN_ID_RE, run_id)):
        raise UsageError(f"run_id must look like 20261010T133514Z-a3f9; got {run_id!r:.40}")
    cad = store.ensure_subdir(Path(info["dir"]), "cad")
    at = now or store.utc_iso()
    heading = [at]
    if tags:
        heading.append("tags: " + ", ".join(tags))
    if run_id:
        heading.append("run " + run_id)
    entry = "\n## " + SEPARATOR.join(heading) + "\n\n" + text + ("" if text.endswith("\n") else "\n")
    with _Lock(cad / LOCK):
        current = _read_current(cad)
        chunk = entry.encode("utf-8")
        if not current:
            chunk = HEADER.format(project=info["name"]).encode("utf-8") + chunk
        if len(current) + len(chunk) > MAX_BYTES:
            raise UsageError("NOTES.md is full: ask the operator to curate it")
        path = cad / NOTES
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT | _O_NOFOLLOW | _O_CLOEXEC | _O_BINARY, 0o600)
        try:
            st = os.fstat(fd)
            if st.st_nlink != 1:
                raise IntegrityError(f"{path} has {st.st_nlink} hard links; refusing to append")
            written = os.write(fd, chunk)
        finally:
            os.close(fd)
        if written != len(chunk):
            raise IntegrityError(f"a short write to {path} ({written} of {len(chunk)} bytes)")
        total_bytes = len(current) + len(chunk)
        n = _count_entries(current) + 1
    return {"path": str(path), "entry": {"n": n, "at": at, "tags": tags, "run_id": run_id,
                                         "bytes": len(entry.encode("utf-8"))},
            "entries_total": n, "bytes_total": total_bytes}


def _entries(text: str) -> tuple[str, list[str]]:
    """(preamble, entries) where each entry starts with its '## ' heading line."""
    lines = text.split("\n")
    starts = [i for i, line in enumerate(lines) if line.startswith("## ")]
    if not starts:
        return text, []
    pre = "\n".join(lines[:starts[0]])
    entries = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else len(lines)
        entries.append("\n".join(lines[s:e]).rstrip("\n") + "\n")
    return pre, entries


def read_tail(info: dict, max_bytes: int = TAIL_BYTES) -> dict:
    """{"exists", "entries_total", "entries_returned", "bytes_total", "text"}: the last whole entries within
    max_bytes."""
    cad = Path(info["dir"]) / "cad"
    try:
        data = _read_current(cad)
    except CadError:
        data = b""
        exists = os.path.lexists(cad / NOTES)
    else:
        exists = bool(data) or os.path.lexists(cad / NOTES)
    text = data.decode("utf-8", "replace")
    _pre, entries = _entries(text)
    picked: list[str] = []
    used = 0
    for e in reversed(entries):
        size = len(e.encode("utf-8")) + 1
        if used + size > max_bytes:
            break
        picked.append(e)
        used += size
    picked.reverse()
    return {"exists": exists, "entries_total": len(entries), "entries_returned": len(picked),
            "bytes_total": len(data), "text": "\n".join(picked)}


def read_all(info: dict, max_bytes: int = 1 << 20) -> dict:
    """The whole NOTES.md (at most max_bytes) for `aces-cad notes`."""
    cad = Path(info["dir"]) / "cad"
    data = _read_current(cad)
    text = data.decode("utf-8", "replace")
    _pre, entries = _entries(text)
    truncated = len(data) > max_bytes
    return {"path": str(cad / NOTES), "exists": bool(data), "entries_total": len(entries), "bytes_total": len(data),
            "text": text[-max_bytes:] if truncated else text, "truncated": truncated}
