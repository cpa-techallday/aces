"""simlab-mcp file transfer: the store behind sim_put_file (uploads) and sim_get_file (downloads).

A desktop that is not on the rig sends the inputs of an experiment (meshes, masks, JSON) with sim_put_file and reads
its outputs back with sim_get_file, over the same streamable-HTTP MCP endpoint as everything else. Both move bytes as
standard base64 in chunks of at most FILE_CHUNK_MAX decoded bytes (2 MiB): the MCP SDK refuses request bodies above
4 MiB with HTTP 413, and 2 MiB of data is about 2.8 MB of base64.

Uploads (put_file)
  name        1 to 4 POSIX components, each [A-Za-z0-9][A-Za-z0-9._-]{0,99}, at most 255 characters in all; no
              component may end in .yaml, .yml, .partial or .partial.json (specs go through sim_put_spec only; the
              other two are this module's own working files). The file lands next to the principal's specs:
              <spec store>/<principal>/<name>, the same confinement as specstore (SIMLAB_SPEC_ROOT, inside the repo).
  protocol    every call carries total_bytes and the sha256 of the whole file. offset 0 starts (or restarts) the
              upload: <target>.partial is truncated and <target>.partial.json records {total_bytes, sha256}. A later
              call must continue exactly where the partial ends, for the same file, or it is refused with a message
              that says to restart at offset 0. The call that brings the partial to total_bytes completes it: the
              bytes are hashed; a mismatch discards the partial. An existing target with the same sha256 is kept
              (existed: true); one with another sha256 is refused unless overwrite is true; otherwise the partial is
              hard-linked into place, so a completion is create-or-fail. Files end 0644, directories 0755.
Downloads (get_file)
  path        relative to the experiment's run directory (HGET simlab:exp:<id> exp_dir), 1 to 8 components with the
              same character rule, resolving (symlinks included) to a regular file inside that directory. Each call
              returns one chunk and the sha256 of the whole file, recomputed in the same pass that reads the chunk.

At module level this imports only the standard library and .specstore; redis and .queue are imported inside
get_file, so the desktop and the unit tests can import it.
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import json
import os
import re
import tempfile
import time
from typing import Any, Dict, List, Optional

from . import specstore

FILE_CHUNK_MAX = int(os.environ.get("SIMLAB_FILE_CHUNK_MAX", "2097152"))    # decoded bytes per call
FILE_MAX_BYTES = int(os.environ.get("SIMLAB_FILE_MAX_BYTES", "268435456"))   # per file
NAME_MAX_CHARS = 255
NAME_MAX_COMPONENTS = 4
PATH_MAX_COMPONENTS = 8
REFUSED_SUFFIXES = (".yaml", ".yml", ".partial", ".partial.json")
_COMPONENT = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")   # \Z, not $: a trailing newline must not pass
_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
_BLOCK = 1 << 20
_REPLACE_TRIES = 20                # see _replace
_RETRY_REPLACE = os.name == "nt"


class FileError(ValueError):
    """A refused upload or download: the message says exactly why."""


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _components(text: Any, what: str, max_components: int) -> List[str]:
    if not isinstance(text, str) or not text:
        raise FileError(f"{what} must be a non-empty string")
    if "\\" in text:
        raise FileError(f"{what} {text!r} contains a backslash; use / between components")
    if text.startswith("/"):
        raise FileError(f"{what} {text!r} must be relative (no leading /)")
    parts = text.split("/")
    if len(parts) > max_components:
        raise FileError(f"{what} {text!r} has {len(parts)} components; at most {max_components} are allowed")
    for p in parts:
        if not _COMPONENT.match(p):
            raise FileError(f"{what} {text!r}: component {p!r} must start with a letter or digit and contain only "
                            "letters, digits, dot, dash or underscore (at most 100 characters)")
    return parts


def check_name(name: str) -> List[str]:
    """The components of an upload name; FileError on any rule (see the module docstring)."""
    parts = _components(name, "name", NAME_MAX_COMPONENTS)
    if len(name) > NAME_MAX_CHARS:
        raise FileError(f"name is {len(name)} characters long; at most {NAME_MAX_CHARS} are allowed")
    for p in parts:
        low = p.lower()
        for suffix in REFUSED_SUFFIXES:
            if low.endswith(suffix):
                why = ("experiment specs are stored with sim_put_spec" if suffix in (".yaml", ".yml")
                       else "the suffix is reserved for uploads in progress")
                raise FileError(f"name {name!r}: component {p!r} ends in {suffix}; {why}")
    return parts


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_BLOCK), b""):
            h.update(block)
    return h.hexdigest()


def _repo_rel(path: str) -> str:
    return os.path.relpath(path, specstore.REPO).replace(os.sep, "/")


def _read_sidecar(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _replace(src: str, dst: str) -> None:
    """os.replace, retried for up to a second on Windows only. There MoveFileEx refuses with WinError 5 while
    another process holds the destination open: the unit tests met it once on DARKNESS (2026-10-10), on a sidecar
    that a restart rewrote a few milliseconds after it was first written. On Linux (the rig) rename replaces the
    destination whatever holds it open, so there the first PermissionError is final."""
    for attempt in range(_REPLACE_TRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if not _RETRY_REPLACE or attempt == _REPLACE_TRIES - 1:
                raise
            time.sleep(0.05)


def _write_sidecar(path: str, total_bytes: int, sha256: str) -> None:
    # the temp name starts with a dot, which no upload name can, so it never meets another upload's file
    fd, tmp = tempfile.mkstemp(prefix=".sidecar-", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"total_bytes": total_bytes, "sha256": sha256}, f)
        _replace(tmp, path)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def _discard(*paths: str) -> None:
    for p in paths:
        try:
            os.unlink(p)
        except FileNotFoundError:
            pass


def _set_modes(pdir: str, target: str) -> None:
    """0755 on every directory from the principal's folder down to the target's, 0644 on the target."""
    d = os.path.dirname(target)
    while True:
        os.chmod(d, 0o755)
        if os.path.normcase(os.path.realpath(d)) == os.path.normcase(os.path.realpath(pdir)) or len(d) <= len(pdir):
            break
        d = os.path.dirname(d)
    os.chmod(target, 0o644)


def put_file(name: str, content_b64: str, *, total_bytes: int, sha256: str, offset: int = 0,
             principal: Optional[str] = None, overwrite: bool = False, root: Optional[str] = None) -> Dict[str, Any]:
    """Receive one chunk of an upload; the reply says how far the upload has got (see the module docstring)."""
    parts = check_name(name)
    if not isinstance(sha256, str) or not _SHA256.match(sha256):
        raise FileError("sha256 must be the 64 lowercase hex characters of the whole file's SHA-256")
    if not _is_int(total_bytes) or not 0 <= total_bytes <= FILE_MAX_BYTES:
        raise FileError(f"total_bytes must be an integer from 0 to {FILE_MAX_BYTES} (SIMLAB_FILE_MAX_BYTES), "
                        f"not {total_bytes!r}")
    if not _is_int(offset) or offset < 0:
        raise FileError(f"offset must be a non-negative integer, not {offset!r}")
    if not isinstance(content_b64, str):
        raise FileError("content_b64 must be a string of standard base64")
    if len(content_b64) > 4 * ((FILE_CHUNK_MAX + 2) // 3):
        raise FileError(f"content_b64 is longer than {FILE_CHUNK_MAX} bytes can encode to (SIMLAB_FILE_CHUNK_MAX); "
                        "send smaller chunks")
    try:
        data = base64.b64decode(content_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise FileError(f"content_b64 is not valid standard base64: {exc}") from None
    if len(data) > FILE_CHUNK_MAX:
        raise FileError(f"a chunk may hold at most {FILE_CHUNK_MAX} bytes (SIMLAB_FILE_CHUNK_MAX), "
                        f"this one holds {len(data)}")
    try:
        store = specstore._root(root)
        pdir_name = specstore._principal_dir(principal)
        pdir = specstore._confined(store, pdir_name)
        target = specstore._confined(store, pdir_name, *parts)
    except specstore.SpecError as exc:
        raise FileError(str(exc)) from None
    partial, sidecar = target + ".partial", target + ".partial.json"
    restart = "restart the upload at offset 0"
    received = offset + len(data)

    if offset == 0:
        if received > total_bytes:
            raise FileError(f"{len(data)} bytes at offset 0 pass total_bytes {total_bytes}; send the file's real size")
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
        except OSError as exc:   # a component of name is an existing file in this principal's store
            raise FileError(f"{name}: cannot create its folder in this principal's store ({exc.strerror})") from None
        if os.path.isdir(target):
            raise FileError(f"{name} is a directory in this principal's store; choose another name")
        _write_sidecar(sidecar, total_bytes, sha256)
        with open(partial, "wb") as f:
            f.write(data)
    else:
        side = _read_sidecar(sidecar)
        have = os.path.getsize(partial) if os.path.isfile(partial) else 0
        same_upload = bool(side) and side.get("total_bytes") == total_bytes and side.get("sha256") == sha256
        if not same_upload or not os.path.isfile(partial) or have != offset:
            expected = have if same_upload else 0
            raise FileError(f"expected offset {expected} for {name}, got {offset}: {restart}")
        if received > total_bytes:
            raise FileError(f"offset {offset} plus {len(data)} bytes passes total_bytes {total_bytes}: {restart}")
        with open(partial, "ab") as f:
            f.write(data)

    out: Dict[str, Any] = {"name": name, "principal": pdir_name, "complete": False, "received_bytes": received,
                           "total_bytes": total_bytes, "path": None, "abs_path": None, "sha256": None,
                           "existed": False, "replaced": False}
    if received < total_bytes:
        return out

    got = _sha256_file(partial)
    if got != sha256:
        _discard(partial, sidecar)
        raise FileError(f"received bytes hash to {got}, not {sha256}; the partial upload was discarded")
    existed = replaced = False
    if os.path.lexists(target):
        if not os.path.isfile(target):
            _discard(partial, sidecar)
            raise FileError(f"{name} exists for this principal and is not a regular file")
        have_sha = _sha256_file(target)
        if have_sha == sha256:
            existed = True
            _discard(partial)
        elif not overwrite:
            _discard(partial, sidecar)
            raise FileError(f"{name} already exists for this principal with sha256 {have_sha}; pass overwrite=true")
    if not existed:
        if overwrite:
            replaced = os.path.lexists(target)
            _replace(partial, target)
        else:
            try:
                os.link(partial, target)   # create-or-fail: of two concurrent completions, the second is refused
            except FileExistsError:
                have_sha = _sha256_file(target)
                _discard(partial, sidecar)
                if have_sha != sha256:
                    raise FileError(f"{name} already exists for this principal with sha256 {have_sha}; "
                                    "pass overwrite=true") from None
                existed = True
            _discard(partial)
    _discard(sidecar)
    _set_modes(pdir, target)
    out.update({"complete": True, "path": _repo_rel(target), "abs_path": target, "sha256": sha256,
                "existed": existed, "replaced": replaced})
    return out


def _exp_dir_of(experiment_id: str) -> str:
    from .leases import get_redis
    from .queue import EXP_KEY

    v = get_redis().hget(EXP_KEY + experiment_id, "exp_dir")
    if not v:
        raise KeyError(f"unknown experiment {experiment_id}")
    return v.decode() if isinstance(v, bytes) else str(v)


def get_file(experiment_id: str, path: str, *, offset: int = 0, max_bytes: int = 2097152,
             exp_dir: Optional[str] = None) -> Dict[str, Any]:
    """One chunk of a file in an experiment's run directory, with the whole file's sha256."""
    if not isinstance(experiment_id, str) or not experiment_id:
        raise FileError("experiment_id must be a non-empty string")
    parts = _components(path, "path", PATH_MAX_COMPONENTS)
    if not _is_int(offset) or offset < 0:
        raise FileError(f"offset must be a non-negative integer, not {offset!r}")
    if not _is_int(max_bytes) or not 1 <= max_bytes <= FILE_CHUNK_MAX:
        raise FileError(f"max_bytes must be an integer from 1 to {FILE_CHUNK_MAX} (SIMLAB_FILE_CHUNK_MAX), "
                        f"not {max_bytes!r}")
    base = os.path.realpath(exp_dir if exp_dir is not None else _exp_dir_of(experiment_id))
    real = os.path.realpath(os.path.join(base, *parts))
    if not real.startswith(base + os.sep):
        raise FileError(f"path {path!r} resolves outside the experiment's run directory")
    if not os.path.isfile(real):
        raise FileError(f"{path} is not a file in experiment {experiment_id}")
    size = os.path.getsize(real)
    if offset > size:
        raise FileError(f"offset {offset} is past the end of {path} ({size} bytes)")
    want = min(max_bytes, size - offset)
    h = hashlib.sha256()
    chunk = bytearray()
    pos = 0
    with open(real, "rb") as f:
        for block in iter(lambda: f.read(_BLOCK), b""):
            h.update(block)
            lo, hi = max(offset, pos), min(offset + want, pos + len(block))
            if hi > lo:
                chunk += block[lo - pos:hi - pos]
            pos += len(block)
        st = os.fstat(f.fileno())
    if pos != size or len(chunk) != want:
        raise FileError(f"{path} changed while it was read ({size} bytes, then {pos}); read it again")
    return {"experiment_id": experiment_id, "path": path, "size": size, "sha256": h.hexdigest(), "offset": offset,
            "length": want, "eof": offset + want == size, "content_b64": base64.b64encode(bytes(chunk)).decode("ascii"),
            "mtime_utc": dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc).isoformat(timespec="seconds")}
