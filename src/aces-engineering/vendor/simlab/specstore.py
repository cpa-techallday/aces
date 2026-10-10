"""Uploaded experiment specs: the store behind the simlab-mcp tools sim_put_spec and sim_list_specs.

sim_submit, sim_sweep and sim_validate take a spec path on the rig's own filesystem, so a desktop or an agent that is
not on the rig had no way to run anything. This module writes a spec it is handed as text into a confined directory
(SIMLAB_SPEC_ROOT, default <repo>/cases/uploaded; it must stay inside the repo because the queue and the sibling
solver containers mount only the repo), one sub-directory per principal, after validating it exactly the way
sim_validate would, and returns the repo-relative path the other tools accept.

Rules: the name is a bare file name ending in .yaml or .yml (no directories, no traversal); the content must be a
YAML mapping without aliases that builds an Experiment whose solver covers its physics and whose sweep expands to at
most SIMLAB_SPEC_MAX_CASES cases; the content is stored byte for byte; writes are atomic and an existing file is
left alone unless overwrite is requested (create-or-fail, so two concurrent uploads cannot both claim a name);
nothing here queues anything.
"""
from __future__ import annotations

import math
import os
import re
import tempfile
from typing import Any, Dict, List, Optional

import yaml

from . import registry
from .spec import Experiment, load, sha256_file

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ROOT = os.environ.get("SIMLAB_SPEC_ROOT", os.path.join(REPO, "cases", "uploaded"))
MAX_BYTES = int(os.environ.get("SIMLAB_SPEC_MAX_BYTES", str(256 * 1024)))
MAX_CASES = int(os.environ.get("SIMLAB_SPEC_MAX_CASES", "10000"))
_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.ya?ml\Z")  # \Z, not $: a trailing newline must not pass
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class SpecError(ValueError):
    """A rejected upload: the message says exactly why."""


class _NoAliasLoader(yaml.SafeLoader):
    """SafeLoader that refuses aliases: a chain of them turns a few hundred bytes into gigabytes of params."""

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise SpecError("YAML aliases are not allowed in uploaded specs")
        return super().compose_node(parent, index)


def _safe_name(s: Optional[str]) -> str:
    return _UNSAFE.sub("_", (s or "").strip()).strip("._-")


def _principal_dir(principal: Optional[str]) -> str:
    """A folder name derived from the principal: safe characters only, never '.', '..' or empty, at most 64 chars."""
    return (_safe_name(principal) or "anonymous")[:64]


def n_cases(exp: Experiment) -> int:
    """How many cases the sweep expands to, by arithmetic, without building them."""
    return math.prod(len(v) for v in exp.sweep.values()) if exp.sweep else 1


def _root(root: Optional[str]) -> str:
    """The real store root, which must be the repo or a directory inside it."""
    base = os.path.realpath(REPO)
    r = os.path.realpath(root or DEFAULT_ROOT)
    if r != base and not r.startswith(base + os.sep):
        raise SpecError("SIMLAB_SPEC_ROOT must be inside the repo: the queue and the solver containers mount only the repo")
    return r


def _confined(root: str, *parts: str) -> str:
    """Join under root and refuse anything that resolves outside it."""
    base = os.path.realpath(root)
    path = os.path.realpath(os.path.join(base, *parts))
    if path != base and not path.startswith(base + os.sep):
        raise SpecError("path escapes the spec store")
    return path


def store_dir(principal: Optional[str], root: Optional[str] = None) -> str:
    """The folder a principal's specs are stored in (created on the first put)."""
    return _confined(_root(root), _principal_dir(principal))


def parse_spec(content: str) -> Experiment:
    """Build an Experiment from YAML text; the same checks sim_validate makes on a file, without a file."""
    if not isinstance(content, str) or not content.strip():
        raise SpecError("content is empty")
    if len(content.encode("utf-8")) > MAX_BYTES:
        raise SpecError(f"content is larger than {MAX_BYTES} bytes (SIMLAB_SPEC_MAX_BYTES)")
    try:
        data = yaml.load(content, Loader=_NoAliasLoader)
    except SpecError:
        raise
    except yaml.YAMLError as exc:
        raise SpecError(f"content is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise SpecError("content must be a YAML mapping (an experiment.yaml)")
    try:
        exp = Experiment(**data)
    except Exception as exc:  # pydantic reports every field problem in one message
        raise SpecError(f"not a valid experiment spec: {exc}") from exc
    try:
        registry.check_physics(exp.solver, exp.physics)
    except Exception as exc:
        raise SpecError(str(exc)) from exc
    n = n_cases(exp)
    if n > MAX_CASES:
        raise SpecError(f"the sweep expands to {n} cases; the limit is {MAX_CASES} (SIMLAB_SPEC_MAX_CASES)")
    return exp


def put_spec(content: str, name: Optional[str] = None, principal: Optional[str] = None, overwrite: bool = False,
             root: Optional[str] = None) -> Dict[str, Any]:
    """Validate and store a spec. Returns the path (relative to the repo, as sim_submit takes it) and its facts."""
    root = _root(root)
    exp = parse_spec(content)
    if name is None:
        fname = (_safe_name(exp.id)[:95] or "spec") + ".yaml"
    else:
        fname = name
        if not _NAME.match(fname):
            raise SpecError(f"name {name!r} must be a bare file name of letters, digits, dot, dash or underscore ending in .yaml")
    pdir_name = _principal_dir(principal)
    pdir = _confined(root, pdir_name)
    path = _confined(root, pdir_name, fname)
    os.makedirs(pdir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".put-", suffix=".yaml", dir=pdir)
    replaced = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)  # byte for byte: the sha256 returned is the sha256 of what the caller sent
        os.chmod(tmp, 0o644)
        if overwrite:
            replaced = os.path.isfile(path)
            os.replace(tmp, path)
        else:
            try:
                os.link(tmp, path)  # create-or-fail: the second of two concurrent uploads is refused, not merged
            except FileExistsError:
                raise SpecError(f"{fname} already exists for this principal (sha256 {sha256_file(path)}); "
                                "pass overwrite=true to replace it") from None
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)
    spec = load(path)  # the stored file, read back the way the queue will read it
    warnings: List[str] = []
    if spec.geometry.ref and not os.path.isfile(spec.resolve(spec.geometry.ref)):
        warnings.append(f"geometry.ref {spec.geometry.ref!r} does not exist next to the stored spec on this host; "
                        "the case will fail at mesh time unless the file is placed there")
    others = [d for d in _principal_dirs(root) if d != pdir_name and os.path.isfile(os.path.join(root, d, fname))]
    if others:
        warnings.append(f"{fname} is also stored by {', '.join(others)}; experiment ids must be unique on the queue, "
                        "so give this spec its own id before submitting both")
    return {
        "spec_path": os.path.relpath(path, REPO).replace(os.sep, "/"),
        "abs_path": path,
        "sha256": spec.spec_sha256,
        "bytes": os.path.getsize(path),
        "id": spec.id,
        "solver": spec.solver,
        "physics": spec.physics,
        "n_cases": n_cases(spec),
        "principal": pdir_name,
        "replaced": replaced,
        "warnings": warnings,
    }


def _principal_dirs(root: str) -> List[str]:
    try:
        return sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
    except FileNotFoundError:
        return []


def list_specs(root: Optional[str] = None, principal: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every stored spec (optionally one principal's), newest first, with the path sim_submit takes."""
    root = _root(root)
    if not os.path.isdir(root):
        return []
    rows: List[tuple] = []
    dirs = [_principal_dir(principal)] if principal else _principal_dirs(root)
    for d in dirs:
        pdir = os.path.join(root, d)
        if not os.path.isdir(pdir):
            continue
        for fn in sorted(os.listdir(pdir)):
            path = os.path.join(pdir, fn)
            if not _NAME.match(fn) or not os.path.isfile(path):
                continue
            rec: Dict[str, Any] = {"spec_path": os.path.relpath(path, REPO).replace(os.sep, "/"), "principal": d}
            mtime_ns = 0
            try:
                st = os.stat(path)
                mtime_ns = st.st_mtime_ns
                rec.update({"bytes": st.st_size, "mtime_utc": _iso(st.st_mtime), "sha256": sha256_file(path)})
                exp = load(path)
                rec.update({"id": exp.id, "solver": exp.solver, "physics": exp.physics, "n_cases": n_cases(exp)})
            except Exception as exc:
                rec["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            rows.append((mtime_ns, rec))
    rows.sort(key=lambda t: t[0], reverse=True)
    return [rec for _, rec in rows]


def _iso(ts: float) -> str:
    import datetime as dt

    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds")
