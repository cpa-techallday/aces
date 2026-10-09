"""Run directories of the golden path: `$ACES_WORK/<project>/golden/<run-id>/`.

A run directory is a valid simlab experiment directory (spec.resolved.json + cases.json, one sub-directory per case)
plus the golden records: run.json (lifecycle, rewritten as the run proceeds), result.json, inputs/, compute.json and
manifest.sha256. This module owns their layout and the run id; it does not compute anything.

Run id: `<UTC %Y%m%dT%H%M%SZ>-<first 6 hex of sha256(canonical_json(key) + "|" + seed)>`, where `key` is the
validated spec after overrides without its `derived` block and, for a STEP source, with `domain.step_file` replaced by
the STEP's content hash. The suffix therefore names the inputs, not the machine: the same spec, STEP and seed give the
same suffix anywhere. An existing directory gets `-2`, `-3`, ... appended.

Standard library only; importing this module stays instant.
"""
from __future__ import annotations

import copy
import fnmatch
import hashlib
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from . import (EXIT_USAGE, SCHEMA, VERSION, GoldenError, canonical_json, dump_json, read_json, sha256_bytes,
               sha256_file, utc_now, write_text)

RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{6}(-\d+)?$")
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]")


class RunNotFound(GoldenError):
    """No run matches the reference (a usage error; `aces golden show` reports it as "none")."""

    def __init__(self, message: str, *, hint: str | None = None):
        super().__init__(EXIT_USAGE, message, hint=hint)


def _safe_json(p: Path) -> dict:
    """A JSON object from `p`, or {} when the file is missing, unreadable or not an object."""
    try:
        data = read_json(p, {})
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def work_root() -> Path:
    """The projects root, as aces-base: $ACES_WORK, else ~/Work."""
    return Path(os.environ.get("ACES_WORK") or Path.home() / "Work")


def slug(text: str) -> str:
    """Anything outside [A-Za-z0-9._-] becomes '-'."""
    return _SLUG_RE.sub("-", text)


def run_key(eff: dict) -> dict:
    """The part of the effective spec that names a run: eff without `derived`, with a STEP source's path replaced by
    the STEP's sha256 (`derived.domain_ref` is the absolute path the pipeline resolved)."""
    key = copy.deepcopy({k: v for k, v in eff.items() if k != "derived"})
    dom = key.get("domain") or {}
    if dom.get("source") == "step":
        ref = (eff.get("derived") or {}).get("domain_ref") or dom.get("step_file")
        if not ref:
            raise GoldenError(EXIT_USAGE, "the spec names a STEP design space but no step_file")
        p = Path(ref)
        if not p.is_file():
            raise GoldenError(EXIT_USAGE, f"STEP file not found: {p}", hint="check domain.step_file (relative to the spec) or --step")
        dom["step_file"] = "sha256:" + sha256_file(p)
    return key


def new_run_id(eff: dict, seed: int, now: datetime) -> str:
    """`<UTC stamp>-<6 hex>`; see the module docstring."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = sha256_bytes((canonical_json(run_key(eff)) + "|" + str(seed)).encode("utf-8"))
    return f"{stamp}-{digest[:6]}"


def _matcher(exclude: "Callable[[str], bool] | Iterable[str] | None") -> Callable[[str], bool]:
    """Exclusion test over run-relative POSIX paths. A callable is used as it is. Patterns follow .gitignore habits:
    a pattern with a '/' is matched (fnmatch) against the whole relative path, one without against the file name, so
    "log.txt" drops every log.txt and "evidence/*" everything under evidence/."""
    if exclude is None:
        return lambda rel: False
    if callable(exclude):
        return exclude
    pats = [str(p) for p in exclude]

    def match(rel: str) -> bool:
        name = rel.rsplit("/", 1)[-1]
        for pat in pats:
            if "/" in pat:
                if fnmatch.fnmatchcase(rel, pat):
                    return True
            elif fnmatch.fnmatchcase(name, pat):
                return True
        return False

    return match


class RunDir:
    """One golden run directory (implements golden.RunDirLike, plus the lifecycle writers of run.json)."""

    def __init__(self, root: "Path | str", *, run_id: str, project: str, exp_id: str | None = None):
        self.root = Path(root).resolve()
        self.run_id = run_id
        self.project = project
        self.exp_id = exp_id or f"golden-{slug(project)}-{run_id}"
        self._lock = threading.RLock()
        self._stage_t0: dict[str, float] = {}
        self.t0: float | None = None  # epoch seconds the command started (set by the pipeline); total_s counts from it

    def __repr__(self) -> str:
        return f"RunDir({str(self.root)!r})"

    # ---- creation and lookup ------------------------------------------------------------------------------------
    @classmethod
    def create(cls, project: str, eff: dict, *, seed: int, now: datetime | None = None) -> "RunDir":
        """Make `$ACES_WORK/<project>/golden/<run-id>[-k]/` (exclusive mkdir, so two runs never share a directory) and
        write the first run.json (status running)."""
        now = now or datetime.now(timezone.utc)
        rid = new_run_id(eff, seed, now)
        base = work_root() / project / "golden"
        base.mkdir(parents=True, exist_ok=True)
        k = 1
        while True:
            name = rid if k == 1 else f"{rid}-{k}"
            try:
                (base / name).mkdir(exist_ok=False)
                break
            except FileExistsError:
                k += 1
        run = cls(base / name, run_id=name, project=project)
        derived = eff.get("derived") or {}
        run.write_json("run.json", {
            "schema": SCHEMA["run"], "run_id": name, "project": project, "exp_id": run.exp_id, "run_dir": str(run.root),
            "tool": {"name": "aces-golden", "version": VERSION, "python": sys.version.split()[0], "argv": []},
            "created_utc": utc_now(), "finished_utc": None, "status": "running", "seed": seed, "demo": None,
            "spec_sha256": None,
            "resolution": {k2: derived.get(k2) for k2 in ("gen_mm", "fea_mm", "field_h_mm", "check_levels")},
            "stages": [], "outcome": None, "verdict": None, "grade": None, "exit_code": None, "message": None,
            "note": None, "paths": {}, "timings": {}})
        return run

    @classmethod
    def open(cls, ref: str | None = None, *, project: str | None = None) -> "RunDir":
        """A run by reference: None or "latest" (the newest), a run directory path, a run id, or a unique prefix of
        one. `project` narrows the search to that project. RunNotFound (exit 2) when nothing matches; GoldenError
        (exit 2) when a prefix is ambiguous."""
        if ref and ref != "latest":
            p = Path(ref).expanduser()
            if (p / "run.json").is_file():
                return cls._from_dir(p)
            if p.is_dir() or os.sep in ref or "/" in ref:
                raise RunNotFound(f"{p} is not a golden run directory (no run.json)")
        runs = list_runs(project)
        where = work_root() / project / "golden" if project else work_root()
        if not runs:
            raise RunNotFound(f"no golden runs under {where}", hint="aces golden run --demo")
        if not ref or ref == "latest":
            return cls._from_dir(runs[0].parent)
        exact = [r for r in runs if r.parent.name == ref]
        if exact:
            return cls._from_dir(exact[0].parent)
        hits = [r for r in runs if r.parent.name.startswith(ref)]
        if not hits:
            raise RunNotFound(f"no golden run '{ref}' under {where}", hint="aces golden show lists the runs")
        if len(hits) > 1:
            names = ", ".join(sorted(h.parent.parent.parent.name + "/" + h.parent.name for h in hits)[:6])
            raise GoldenError(EXIT_USAGE, f"run prefix '{ref}' is ambiguous: {names}", hint="give more of the run id or --project")
        return cls._from_dir(hits[0].parent)

    @classmethod
    def _from_dir(cls, d: Path) -> "RunDir":
        d = Path(d).resolve()
        meta = _safe_json(d / "run.json")
        project = meta.get("project") or d.parent.parent.name
        run_id = meta.get("run_id") or d.name
        return cls(d, run_id=run_id, project=project, exp_id=meta.get("exp_id"))

    # ---- RunDirLike -----------------------------------------------------------------------------------------------
    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def rel(self, p: "Path | str") -> str:
        """Run-relative POSIX path of `p` (absolute paths inside the run, or paths already relative); other absolute
        paths come back unchanged."""
        pp = Path(p)
        if pp.is_absolute():
            try:
                return pp.resolve().relative_to(self.root).as_posix()
            except ValueError:
                return str(p)
        return pp.as_posix()

    def case_dir(self, case_id: str) -> Path:
        d = self.root / case_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_json(self, rel: str, obj: Any) -> Path:
        return dump_json(self.root / rel, obj)

    def read_json(self, rel: str, default: Any = None) -> Any:
        return read_json(self.root / rel, default)

    def write_text(self, rel: str, text: str) -> Path:
        return write_text(self.root / rel, text)

    def register_case(self, case: dict) -> Path:
        """Add `case` to cases.json (in registration order; a case id registered again replaces its entry in place)
        and write <case>/case.json. Returns the case directory."""
        cid = str(case.get("case_id") or "")
        if not cid or slug(cid) != cid or cid in (".", ".."):
            raise ValueError(f"case_id {cid!r} is not a plain directory name")
        with self._lock:
            cases = self.cases()
            for i, c in enumerate(cases):
                if c.get("case_id") == cid:
                    cases[i] = case
                    break
            else:
                cases.append(case)
            self.write_json("cases.json", cases)
            d = self.case_dir(cid)
            dump_json(d / "case.json", case)
        return d

    def cases(self) -> list[dict]:
        return list(self.read_json("cases.json", []) or [])

    # ---- golden lifecycle -----------------------------------------------------------------------------------------
    def write_experiment(self, exp: Any) -> Path:
        """spec.resolved.json = the simlab Experiment (exp.model_dump(mode="json")): what makes this directory a
        simlab experiment directory for build_bundle."""
        data = exp.model_dump(mode="json") if hasattr(exp, "model_dump") else dict(exp)
        return self.write_json("spec.resolved.json", data)

    def meta(self) -> dict:
        """run.json as it is now ({} when it is missing or unreadable)."""
        return _safe_json(self.root / "run.json")

    def update(self, **fields: Any) -> dict:
        """Merge top-level fields into run.json (atomic rewrite); returns the new content."""
        with self._lock:
            data = self.meta()
            data.update(fields)
            self.write_json("run.json", data)
            return data

    def stage(self, name: str, state: str, note: str = "", *, t0: float | None = None) -> None:
        """Record a stage transition in run.json: running (start time; `t0` backdates it, epoch seconds), then done,
        skipped or failed (wall time, note). timings["<stage>_s"] follows."""
        if state not in ("running", "done", "skipped", "failed"):
            raise ValueError(f"stage state {state!r}")
        now = time.time()
        with self._lock:
            data = self.meta()
            stages = list(data.get("stages") or [])
            ent = next((s for s in stages if s.get("name") == name), None)
            if ent is None:
                ent = {"name": name, "status": state, "started_utc": None, "wall_s": None, "note": ""}
                stages.append(ent)
            if state == "running":
                start = t0 if t0 is not None else now
                self._stage_t0[name] = start
                ent["started_utc"] = datetime.fromtimestamp(start, timezone.utc).isoformat(timespec="milliseconds")
                ent["wall_s"] = None
            elif name in self._stage_t0:
                ent["wall_s"] = round(now - self._stage_t0[name], 3)
            ent["status"] = state
            if note or state != "running":
                ent["note"] = note
            data["stages"] = stages
            timings = dict(data.get("timings") or {})
            if ent["wall_s"] is not None:
                timings[f"{name}_s"] = ent["wall_s"]
            data["timings"] = timings
            self.write_json("run.json", data)

    def finish(self, *, status: str, outcome: str, verdict: str | None, grade: str | None, exit_code: int,
               message: str, paths: dict) -> None:
        """Close run.json: status done|error|interrupted, the outcome, verdict, grade, exit code, message, paths and
        the total wall time."""
        with self._lock:
            data = self.meta()
            created = data.get("created_utc")
            start = self.t0
            if start is None and created:
                try:
                    start = datetime.fromisoformat(created).timestamp()
                except ValueError:
                    start = None
            timings = dict(data.get("timings") or {})
            if start is not None:
                timings["total_s"] = round(time.time() - start, 3)
            data.update({"finished_utc": utc_now(), "status": status, "outcome": outcome, "verdict": verdict,
                         "grade": grade, "exit_code": int(exit_code), "message": message, "paths": dict(paths or {}),
                         "timings": timings})
            self.write_json("run.json", data)

    def hash_tree(self, out_rel: str, *, exclude: "Callable[[str], bool] | Iterable[str] | None" = None) -> str:
        """Write `out_rel` in sha256sum binary format (`<sha256> *<run-relative POSIX path>`, sorted by path, LF) over
        every file of the run except `out_rel` itself and what `exclude` matches (see _matcher). Returns the sha256 of
        the written listing."""
        skip = _matcher(exclude)
        out_posix = Path(out_rel).as_posix()
        rows = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames.sort()
            for fn in filenames:
                full = Path(dirpath) / fn
                rel = full.relative_to(self.root).as_posix()
                if rel == out_posix or skip(rel) or not full.is_file():
                    continue
                rows.append((rel, sha256_file(full)))
        rows.sort()
        text = "".join(f"{h} *{rel}\n" for rel, h in rows)
        write_text(self.root / out_rel, text)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


def list_runs(project: str | None = None) -> list[Path]:
    """Every `$ACES_WORK/*/golden/*/run.json` (or only `<project>`'s), newest first by run.json created_utc."""
    root = work_root()
    pattern = f"{project}/golden/*/run.json" if project else "*/golden/*/run.json"
    found = [p for p in root.glob(pattern) if p.is_file()] if root.is_dir() else []

    def key(p: Path) -> tuple[str, str]:
        return (str(_safe_json(p).get("created_utc") or ""), p.parent.name)

    return sorted(found, key=key, reverse=True)
