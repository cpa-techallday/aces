"""Operations: what the MCP tools and the CLI verbs do (sections 5.1, 9, 11, 12 and 16 of the build spec).

Dispatch order for every call: the arguments are validated (the tool's input schema, then the operation's own rules),
the class gate runs (every operation but params and doctor), then the operation. Operations that need geometry spawn
one worker job each (sandbox.run_worker); the parent never executes geometry or agent code. render, drawing and pull
are implemented in views.py and pull.py and are imported only when called.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from . import __version__, config, notes as notes_mod, params as params_mod, project as project_mod, sandbox, store
from .errors import (CadError, ConfigError, IntegrityError, NotFoundError, RunFailed, UsageError)

OPERATIONS = ("start", "run", "runs", "prune", "params", "inspect", "render", "drawing", "pull", "note", "notes",
              "doctor")
NO_GATE = ("params", "doctor")
TOOL = {"name": "aces-cad", "version": __version__}
SCRIPT_FILE_MAX = 1 << 20
INPUT_EXTS = (".step", ".stp", ".brep", ".stl", ".3mf", ".obj", ".dxf", ".svg", ".json", ".csv", ".txt", ".npy",
              ".npz")
INPUT_NAME_RE = r"[a-z][a-z0-9_]{0,31}"
INPUT_MAX = 16
INPUT_FILE_MAX = 256 << 20
INPUT_TOTAL_MAX = 1 << 30
PARTS_JSON_MAX = 1 << 20
CHECKS = ("mass", "validity", "topology", "holes", "thickness", "draft", "radii", "interference", "section", "measure",
          "design_space")
DEFAULT_CHECKS = ["mass", "validity", "topology"]
CONVENTIONS = {
    "entry": "def build(p) (gen_step() accepted)",
    "params": "a literal module-level PARAMS dict: name -> default or {default, min, max, choices, unit, doc, type}",
    "results": ["Part/Solid/Compound", "Sketch or planar Face",
                "dict or list of parts (assembly); @name -> Face/Edge/Vertex datums", "trimesh.Trimesh (mesh)"],
    "units": "mm, degrees",
    "inputs": "INPUTS[name] -> absolute path of each input",
    "sdf": "from aces_cad.worker.sdf import mesh_from_sdf",
    "refs": "@datum | part | part:f<n>|e<n>|v<n> | part:+x..-z",
}
_ENV_CACHE: dict = {}
_ENV_LOCK = threading.Lock()


def degraded_warning(missing: list[str]) -> str:
    """The permissive-mode warning (section 8.9), saying what each missing kernel layer no longer stops."""
    effects = []
    if "landlock" in missing:
        effects.append("agent code can read and write what OCCT opens anywhere the user can")
    if "seccomp" in missing:
        effects.append("native code in a run can open network sockets and start processes")
    return f"the sandbox is degraded (missing: {', '.join(missing)}): {'; '.join(effects) or 'see the sandbox note'}"


@dataclass
class OpContext:
    """What an operation runs with: the gate result, the environment, cancellation and progress."""

    info: dict | None
    env: Mapping[str, str]
    cancel: threading.Event | None = None
    progress: Callable | None = None
    _settings: config.Settings | None = field(default=None, repr=False)

    def settings(self) -> config.Settings:
        if self._settings is None:
            self._settings = config.settings(self.env)
        return self._settings

    def limits(self, op: str, timeout_s: int | None) -> sandbox.Limits:
        s = self.settings()
        return sandbox.Limits(timeout_s=int(timeout_s or s.job_timeout(op)), memory_mb=s.memory_mb,
                              threads=s.threads, max_file_mb=s.max_file_mb, nice=s.nice)

    def job(self, run: store.Run, op: str, args: dict, *, agent_code: bool = False, cache_rw: bool = False,
            timeout_s: int | None = None) -> sandbox.WorkerOutcome:
        """new_job + run_worker in the run directory (ro = the run dir, rw = the job dir); a failed job is cleaned up
        and raised (raise_for). The caller promotes the outputs of a successful job, then calls finish_job."""
        job_dir = sandbox.new_job(run.root, op)
        try:
            outcome = sandbox.run_worker(op, args, job_dir=job_dir, ro_dirs=[run.root], cache_rw=cache_rw,
                                         limits=self.limits(op, timeout_s), agent_code=agent_code, env=self.env,
                                         cancel=self.cancel, progress=self.progress)
        except BaseException:
            sandbox.finish_job(job_dir, env=self.env)
            raise
        if outcome.status != "ok":
            sandbox.finish_job(job_dir, env=self.env)
            sandbox.raise_for(outcome, run_id=run.id)
        return outcome

    def finish(self, outcome: sandbox.WorkerOutcome) -> None:
        sandbox.finish_job(outcome.job_dir, env=self.env)


# --- argument checks --------------------------------------------------------------------------------------------------

def _validate_schema(op: str, args: dict) -> None:
    """The tool's input schema (the MCP server's), for callers that are not the MCP server."""
    from . import mcp_server  # lazy: mcp_server imports this module
    tool = mcp_server.TOOL_FOR_OP.get(op)
    if tool is None:
        return
    problem = mcp_server.validate(args, mcp_server.input_schema(tool, project_required=False))
    if problem:
        raise UsageError(f"invalid arguments for {tool['name']}: {problem}")


def _only(args: dict, allowed: tuple[str, ...], op: str) -> None:
    extra = sorted(k for k in args if k not in allowed)
    if extra:
        raise UsageError(f"{op} takes no argument {', '.join(extra)}")


def _int_arg(args: dict, key: str, lo: int, hi: int, default=None):
    value = args.get(key, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise UsageError(f"{key} must be an integer from {lo} to {hi}; got {value!r:.40}")
    return value


def _bool_arg(args: dict, key: str, default: bool = False) -> bool:
    value = args.get(key, default)
    if not isinstance(value, bool):
        raise UsageError(f"{key} must be true or false")
    return value


# --- materials --------------------------------------------------------------------------------------------------------

def _material_table(path: Path, data: bytes, user: bool) -> dict:
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ConfigError(f"{path} is not valid JSON") from None
    if not isinstance(doc, dict) or doc.get("schema") != "aces.cad.materials/1" or not isinstance(
            doc.get("materials"), dict):
        raise ConfigError(f"{path} must be {{\"schema\": \"aces.cad.materials/1\", \"materials\": {{...}}}}")
    out = {}
    for name, entry in doc["materials"].items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._()-]{0,63}", name):
            raise ConfigError(f"{path}: material name {name!r:.40} is not allowed")
        if not isinstance(entry, dict):
            raise ConfigError(f"{path}: material {name} must be an object")
        rho = entry.get("density_kg_m3")
        if isinstance(rho, bool) or not isinstance(rho, (int, float)) or not 1 <= rho <= 30000:
            raise ConfigError(f"{path}: material {name} needs density_kg_m3 from 1 to 30000")
        src = entry.get("source")
        if not isinstance(src, str) or not src.strip():
            raise ConfigError(f"{path}: material {name} has no source; every density must say where it comes from")
        out[name] = {"kg_m3": float(rho), "material": name, "source": src,
                     "table": "user" if user else "aces-cad"}
    return out


def materials(env: Mapping[str, str] | None = None) -> dict:
    """aces-cad's materials.json extended by <ACES_USER_CONFIG>/cad-materials.json."""
    here = Path(__file__).resolve().parent
    table = _material_table(here / "materials.json", (here / "materials.json").read_bytes(), False)
    udir = config.user_config_dir(env)
    try:
        data = store.safe_read(udir, "cad-materials.json", 1 << 20)
    except NotFoundError:
        data = None
    except CadError as e:
        raise ConfigError(f"cannot read {udir / 'cad-materials.json'}: {e}") from None
    if data is not None:
        table.update(_material_table(udir / "cad-materials.json", data, True))
    return table


def material_density(name: str, env: Mapping[str, str] | None = None) -> dict:
    table = materials(env)
    if name not in table:
        raise UsageError(f"unknown material {name!r:.70}: known materials are {', '.join(sorted(table))} (add "
                         f"others with a source to ~/.config/aces/cad-materials.json)")
    entry = table[name]
    return {"kg_m3": entry["kg_m3"], "material": name, "source": entry["source"]}


# --- environment (env job) ------------------------------------------------------------------------------------------

def env_job(ctx: OpContext, *, cached: bool) -> dict:
    """The env job's report {"status","payload","error","sandbox","versions","warnings","stderr"}; cached for the
    process's life when `cached` (cad_start), never for doctor."""
    s = ctx.settings()
    key = (tuple(config.worker_cmd(ctx.env)), s.sandbox, str(s.cache), s.memory_mb, s.threads)
    if cached:
        with _ENV_LOCK:
            if key in _ENV_CACHE:
                return _ENV_CACHE[key]
    os.makedirs(s.cache, mode=0o700, exist_ok=True)
    job_dir = sandbox.new_job(store.ensure_subdir(s.cache, "env"), "env")
    try:
        outcome = sandbox.run_worker("env", {}, job_dir=job_dir, ro_dirs=[], cache_rw=False,
                                     limits=ctx.limits("env", None), agent_code=False, env=ctx.env,
                                     cancel=ctx.cancel, progress=None)
    finally:
        sandbox.finish_job(job_dir, env=ctx.env)
    report = {"status": outcome.status, "payload": outcome.result, "error": outcome.error,
              "sandbox": outcome.sandbox, "versions": outcome.versions, "warnings": outcome.warnings,
              "stderr": outcome.stderr[-2000:]}
    if cached and outcome.status == "ok":
        with _ENV_LOCK:
            _ENV_CACHE[key] = report
    return report


def _strict_ready(sb: dict) -> tuple[bool, list[str]]:
    missing = []
    if not (sb.get("seccomp") or {}).get("active"):
        missing.append("seccomp")
    if not (sb.get("landlock") or {}).get("active"):
        missing.append("landlock")
    return not missing, missing


# --- start ------------------------------------------------------------------------------------------------------------

def op_start(ctx: OpContext, args: dict) -> dict:
    _only(args, (), "start")
    info = ctx.info
    s = ctx.settings()
    cad = store.ensure_subdir(Path(info["dir"]), "cad")
    local = store.ensure_subdir(cad, "local")
    runs = store.ensure_subdir(local, "runs")
    warnings: list[str] = []
    recent = [{k: row.get(k) for k in ("run_id", "name", "status", "kind", "created_at", "valid", "error_kind")}
              for row in store.list_runs(info, 10)]
    environment = {"worker_python": s.worker_cmd_override[0] if s.worker_cmd_override else s.python, "versions": {},
                   "ray_backend": None}
    sandbox_block: dict = {}
    ready = False
    try:
        report = env_job(ctx, cached=True)
    except CadError as e:
        report = None
        warnings.append(f"the worker could not start ({e.kind}: {e}): run aces-cad doctor")
    if report is not None:
        payload = report.get("payload") or {}
        environment["versions"] = report.get("versions") or payload.get("versions") or {}
        environment["ray_backend"] = payload.get("ray_backend")
        sandbox_block = report.get("sandbox") or {}
        if report["status"] != "ok":
            err = report.get("error") or {}
            warnings.append(f"the env job failed ({err.get('kind')}: {err.get('message')}): run aces-cad doctor")
        else:
            strict_ok, missing = _strict_ready(sandbox_block)
            if s.sandbox == "strict":
                ready = strict_ok
                if not strict_ok:
                    warnings.append(f"the strict sandbox cannot run here (missing: {', '.join(missing)}): no agent "
                                    f"code will run; run aces-cad doctor")
            else:
                ready = True
                if missing:
                    warnings.append(degraded_warning(missing))
    return {"project": dict(info),
            "paths": {"cad": str(cad), "runs": str(runs), "pulls": str(local), "notes": str(cad / notes_mod.NOTES)},
            "notes": notes_mod.read_tail(info),
            "recent_runs": recent,
            "conventions": CONVENTIONS,
            "limits": {"timeout_s": s.timeout_s, "timeout_s_max": config.TIMEOUT_MAX, "memory_mb": s.memory_mb,
                       "threads": s.threads, "script_max_chars": params_mod.SCRIPT_MAX_CHARS,
                       "max_file_mb": s.max_file_mb, "max_run_mb": s.max_run_mb},
            "environment": environment, "sandbox": sandbox_block, "ready": ready, "warnings": warnings}


# --- run --------------------------------------------------------------------------------------------------------------

def _decode_script(raw: bytes, what: str) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise UsageError(f"{what} is not UTF-8 text") from None
    return params_mod.check_script_text(text)


def _check_inputs(inputs) -> dict:
    if inputs is None:
        return {}
    if not isinstance(inputs, dict) or len(inputs) > INPUT_MAX:
        raise UsageError(f"inputs must map at most {INPUT_MAX} names to project files")
    for name, rel in inputs.items():
        if not isinstance(name, str) or not re.fullmatch(INPUT_NAME_RE, name):
            raise UsageError(f"input name {name!r:.40} must match {INPUT_NAME_RE}")
        store.check_relpath(rel, f"inputs.{name}")
        if os.path.splitext(rel)[1].lower() not in INPUT_EXTS:
            raise UsageError(f"inputs.{name} must end in one of {', '.join(INPUT_EXTS)}")
    return dict(inputs)


def _check_params_arg(value) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > params_mod.MAX_PARAMS:
        raise UsageError(f"params must be an object of at most {params_mod.MAX_PARAMS} parameter values")
    return dict(value)


def _read_parts_json(out_dir: Path, max_file_bytes: int) -> tuple[dict, list[str]]:
    """The run job's parts.json, checked against its schema and the files present: (manifest, names to promote)."""
    try:
        doc = json.loads(store.safe_read(out_dir, "parts.json", PARTS_JSON_MAX).decode("utf-8"))
    except CadError as e:
        raise IntegrityError(f"the run job's parts.json: {e}") from None
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise IntegrityError("the run job's parts.json is not valid JSON") from None
    if not isinstance(doc, dict) or doc.get("schema") != "aces.cad.parts/1":
        raise IntegrityError("the run job's parts.json has the wrong schema")
    if doc.get("kind") not in ("solid", "assembly", "sketch", "mesh"):
        raise IntegrityError("the run job's parts.json has no valid kind")
    parts, datums = doc.get("parts"), doc.get("datums", [])
    if not isinstance(parts, list) or not 1 <= len(parts) <= 64 or not isinstance(datums, list) or len(datums) > 64:
        raise IntegrityError("the run job's parts.json must list 1 to 64 parts and at most 64 datums")
    names = ["parts.json"]
    seen: set[str] = set()
    for i, p in enumerate(parts):
        if not isinstance(p, dict) or p.get("index") != i or p.get("kind") not in ("solid", "sketch", "mesh"):
            raise IntegrityError(f"the run job's parts.json part {i} is malformed")
        name = p.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name) or name in seen:
            raise IntegrityError(f"the run job's parts.json part {i} has a bad or duplicate name")
        seen.add(name)
        ext = "npz" if p["kind"] == "mesh" else "brep"
        if p.get("file") != f"p{i}.{ext}":
            raise IntegrityError(f"the run job's parts.json part {i} names the wrong file")
        names.append(p["file"])
    for i, d in enumerate(datums):
        if (not isinstance(d, dict) or d.get("index") != i or d.get("file") != f"d{i}.brep"
                or d.get("type") not in ("Face", "Edge", "Vertex")
                or not isinstance(d.get("name"), str)
                or not re.fullmatch(r"@[A-Za-z0-9][A-Za-z0-9._-]{0,63}", d["name"])):
            raise IntegrityError(f"the run job's parts.json datum {i} is malformed")
        names.append(d["file"])
    for n in names:
        if not re.fullmatch(store.RUN_OUTPUT_RE, n):
            raise IntegrityError(f"the run job output {n} is not an allowed name")
    return doc, names


def _run_payload(payload: dict) -> tuple[dict, list]:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else None
    if result is None or not isinstance(result.get("parts"), list):
        raise RunFailed("protocol: the run job reported no result", kind="crash")
    problems = payload.get("problems") if isinstance(payload.get("problems"), list) else []
    return result, problems


def op_run(ctx: OpContext, args: dict) -> dict:
    _only(args, ("script", "script_path", "from_run", "params", "inputs", "name", "timeout_s", "render"), "run")
    info = ctx.info
    s = ctx.settings()
    pdir = Path(info["dir"])
    sources = [k for k in ("script", "script_path", "from_run") if k in args and args[k] is not None]
    if len(sources) != 1:
        raise UsageError("give exactly one of script (Python text), script_path (a .py file in the project) or "
                         "from_run (a run id or latest)")
    overrides = _check_params_arg(args.get("params"))
    inputs_req = _check_inputs(args.get("inputs"))
    timeout_s = _int_arg(args, "timeout_s", 1, config.TIMEOUT_MAX)
    render = _bool_arg(args, "render")
    name = args.get("name")
    if name is not None and (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}",
                                                                            name)):
        raise UsageError(f"name must match [A-Za-z0-9][A-Za-z0-9._-]{{0,39}}; got {name!r:.50}")
    inherited: dict = {}
    from_inputs: dict = {}
    src_run = None
    if sources[0] == "script":
        text = params_mod.check_script_text(args["script"])
        source, spath = "inline", None
    elif sources[0] == "script_path":
        raw = store.read_project_file(pdir, args["script_path"], max_bytes=SCRIPT_FILE_MAX, exts=(".py",))
        text = _decode_script(raw, args["script_path"])
        source, spath = "path", args["script_path"]
    else:
        ref = args["from_run"]
        src_run = store.open_run(info, ref, require_ok=(ref == "latest"))
        rec = src_run.record()
        raw = store.safe_read(src_run.root, "script.py", SCRIPT_FILE_MAX)
        want = (rec.get("script") or {}).get("sha256")
        if hashlib.sha256(raw).hexdigest() != want:
            raise IntegrityError(f"run {src_run.id}: script.py does not match the sha256 in its run.json")
        text = _decode_script(raw, f"run {src_run.id} script.py")
        old = rec.get("params") if isinstance(rec.get("params"), dict) else {}
        inherited = {n: old[n] for n in rec.get("params_overridden") or [] if n in old}
        from_inputs = rec.get("inputs") if isinstance(rec.get("inputs"), dict) else {}
        source, spath = "from_run", (rec.get("script") or {}).get("path")
        if name is None and isinstance(rec.get("name"), str):
            name = rec["name"]
    name = name or "part"
    script_info = params_mod.parse_script(text)  # ScriptError or ParamsError: no run, no spawn
    merged = dict(inherited)
    merged.update(overrides)
    snap = params_mod.snapshot(script_info, merged)
    run = store.new_run(info)
    try:
        script_bytes = text.encode("utf-8")
        store.write_atomic(run.root / "script.py", script_bytes)
        script_sha = hashlib.sha256(script_bytes).hexdigest()
        inputs_rec, worker_inputs = _stage_inputs(run, pdir, inputs_req, src_run, from_inputs)
    except BaseException:
        store.remove_tree(run.root)
        raise
    created = store.utc_iso()
    record = {"schema": "aces.cad.run/1", "run_id": run.id, "name": name, "project": info["name"],
              "class": info["class"], "created_at": created, "finished_at": None, "status": "running", "error": None,
              "script": {"source": source, "path": spath, "sha256": script_sha, "bytes": len(script_bytes)},
              "from_run": src_run.id if src_run else None, "entry": script_info.entry,
              "params": snap["params"], "params_declared": snap["params_declared"],
              "params_overridden": snap["params_overridden"], "params_sha256": snap["params_sha256"],
              "inputs": inputs_rec, "result": None, "problems": [], "timings_s": {}, "peak_rss_mb": None,
              "sandbox": {}, "versions": {}, "stdout": "", "stderr": "", "stdout_truncated": False,
              "stderr_truncated": False, "warnings": list(script_info.warnings), "tool": dict(TOOL)}
    run.write_record(record)
    wargs = {"script": "script.py", "script_sha256": script_sha, "entry": script_info.entry,
             "entry_takes_p": script_info.entry_takes_p, "params": snap["params"], "inputs": worker_inputs,
             "name": name}
    limits = ctx.limits("run", timeout_s)
    job_dir = sandbox.new_job(run.root, "run")
    try:
        store.write_json_atomic(run.root / "job.json", sandbox.job_document(
            "run", wargs, job_dir=job_dir, ro_dirs=[run.root], cache_rw=False, limits=limits, agent_code=True,
            env=ctx.env))
        outcome = sandbox.run_worker("run", wargs, job_dir=job_dir, ro_dirs=[run.root], cache_rw=False,
                                     limits=limits, agent_code=True, env=ctx.env, cancel=ctx.cancel,
                                     progress=ctx.progress)
    except BaseException as e:
        sandbox.finish_job(job_dir, env=ctx.env)
        if isinstance(e, CadError):
            err = e.to_dict()
        elif isinstance(e, KeyboardInterrupt):
            err = {"kind": "cancelled", "message": "interrupted; the run job was killed", "exit_code": 130}
        else:
            err = {"kind": "internal", "message": f"{type(e).__name__}: {e}", "exit_code": 1}
        record.update(status="error", error=err, finished_at=store.utc_iso())
        run.write_record(record)
        raise
    record.update(timings_s=outcome.timings_s, peak_rss_mb=outcome.peak_rss_mb, sandbox=outcome.sandbox,
                  versions=outcome.versions, stdout=outcome.stdout, stderr=outcome.stderr,
                  stdout_truncated=outcome.stdout_truncated, stderr_truncated=outcome.stderr_truncated,
                  warnings=record["warnings"] + outcome.warnings)
    failure: CadError | None = None
    if outcome.status == "ok":
        try:
            result, problems = _run_payload(outcome.result or {})
            _doc, names = _read_parts_json(outcome.out_dir, s.max_file_mb << 20)
            store.promote(outcome.out_dir, names, run.root / "out", max_file_bytes=s.max_file_mb << 20,
                          max_total_bytes=s.max_run_mb << 20)
            record.update(status="ok", result=result, problems=problems)
        except CadError as e:
            failure = e
    else:
        try:
            sandbox.raise_for(outcome, run_id=run.id)
        except CadError as e:
            failure = e
    sandbox.finish_job(job_dir, env=ctx.env)
    record["finished_at"] = store.utc_iso()
    if failure is not None:
        err = failure.to_dict()
        for key in ("stdout", "stderr"):
            err.pop(key, None)
        record.update(status="error", error=err)
        run.write_record(record)
        failure.extra.setdefault("run_id", run.id)
        for key in ("stdout", "stderr", "stdout_truncated", "stderr_truncated", "timings_s", "peak_rss_mb",
                    "sandbox"):
            if record.get(key) is not None:
                failure.extra.setdefault(key, record[key])
        raise failure
    run.write_record(record)
    out = {k: record[k] for k in ("run_id", "status", "name", "project", "script", "from_run", "entry", "params",
                                  "params_overridden", "params_declared", "inputs", "result", "problems", "stdout",
                                  "stderr", "stdout_truncated", "stderr_truncated", "timings_s", "peak_rss_mb",
                                  "sandbox", "versions")}
    out.update(renders=[], run_dir=str(run.root), warnings=list(record["warnings"]))
    if render:
        try:
            from . import views
            shown = views.op_render(ctx, {"run_id": run.id, "views": ["iso"], "size": "small", "inline": True})
            out["renders"] = shown.get("renders") or []
            if shown.get("inline_images"):
                out["inline_images"] = list(shown["inline_images"])[:4]
            out["warnings"].extend(shown.get("warnings") or [])
        except CadError as e:
            out["warnings"].append(f"the render failed ({e.kind}: {e}); the run itself succeeded")
        except ImportError as e:
            out["warnings"].append(f"renders are not available in this build ({e})")
    return out


def _stage_inputs(run: store.Run, pdir: Path, requested: dict, src_run: store.Run | None,
                  inherited: dict) -> tuple[dict, dict]:
    """Copy the inputs into runs/<id>/inputs/: from the source run (sha256 checked) and from the project."""
    if not requested and not inherited:
        return {}, {}
    idir = store.ensure_subdir(run.root, "inputs")
    record: dict = {}
    worker: dict = {}
    total = 0
    for name, entry in sorted(inherited.items()):
        if name in requested or src_run is None:
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
            raise IntegrityError(f"run {src_run.id}: inputs.{name} is malformed in run.json")
        rel = entry["file"]
        if not re.fullmatch(r"inputs/[a-z][a-z0-9_]{0,31}\.[a-z0-9]{1,5}", rel):
            raise IntegrityError(f"run {src_run.id}: inputs.{name} names {rel!r:.60}")
        data = store.safe_read(src_run.root, rel, INPUT_FILE_MAX)
        sha = hashlib.sha256(data).hexdigest()
        if sha != entry.get("sha256"):
            raise IntegrityError(f"run {src_run.id}: {rel} does not match the sha256 in its run.json")
        dest = idir / rel.split("/", 1)[1]
        store.write_atomic(dest, data)
        total += len(data)
        record[name] = {"path": entry.get("path"), "file": rel, "sha256": sha, "bytes": len(data)}
        worker[name] = str(dest)
    for name, rel in sorted(requested.items()):
        ext = os.path.splitext(rel)[1].lower()
        dest = idir / f"{name}{ext}"
        got = store.copy_project_file(pdir, rel, dest, max_bytes=INPUT_FILE_MAX, exts=INPUT_EXTS)
        total += got["bytes"]
        if total > INPUT_TOTAL_MAX:
            raise UsageError(f"the inputs total more than {INPUT_TOTAL_MAX >> 20} MiB")
        record[name] = {"path": rel, "file": f"inputs/{name}{ext}", "sha256": got["sha256"], "bytes": got["bytes"]}
        worker[name] = str(dest)
    return record, worker


# --- runs, prune, params ----------------------------------------------------------------------------------------------

def op_runs(ctx: OpContext, args: dict) -> dict:
    _only(args, ("run_id", "limit", "include_script", "include_output"), "runs")
    info = ctx.info
    include_script = _bool_arg(args, "include_script")
    include_output = _bool_arg(args, "include_output")
    if args.get("run_id") is not None:
        run = store.open_run(info, args["run_id"], require_ok=False)
        rec = run.record()
        out: dict = {"run": {k: v for k, v in rec.items() if k not in ("stdout", "stderr")}}
        if include_script:
            out["script"] = store.safe_read(run.root, "script.py", SCRIPT_FILE_MAX).decode("utf-8", "replace")
        if include_output:
            out["stdout"] = rec.get("stdout", "")
            out["stderr"] = rec.get("stderr", "")
        return out
    limit = _int_arg(args, "limit", 1, 50, 20)
    rows = store.list_runs(info, limit)
    return {"runs": rows, "count": len(rows), "total": len(store.run_ids(info))}


def op_prune(ctx: OpContext, args: dict) -> dict:
    _only(args, ("keep",), "prune")
    keep = _int_arg(args, "keep", 0, 1_000_000)
    if keep is None:
        raise UsageError("prune needs keep (the number of newest runs to keep)")
    return store.prune(ctx.info, keep)


def op_params(ctx: OpContext, args: dict) -> dict:
    _only(args, ("script",), "params")
    text = args.get("script")
    if text is None:
        raise UsageError("params needs the script text")
    info = params_mod.parse_script(params_mod.check_script_text(text))
    values = params_mod.defaults(info.declared)
    return {"entry": info.entry, "entry_takes_p": info.entry_takes_p, "params_declared": info.declared,
            "params": values, "params_sha256": params_mod.sha256_json(values), "warnings": list(info.warnings)}


# --- inspect ----------------------------------------------------------------------------------------------------------

def _at_label(at: float) -> str:
    text = f"{float(at):.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _new_inspect_id(idir: Path) -> str:
    for _ in range(100):
        iid = f"{store.utc_stamp()}-{secrets.token_hex(2)}"
        if not os.path.lexists(idir / f"{iid}.json"):
            return iid
    raise IntegrityError(f"could not choose an inspect id in {idir}")


def op_inspect(ctx: OpContext, args: dict) -> dict:
    _only(args, ("run_id", "checks", "part", "material", "density_kg_m3", "list_faces", "section", "pull_direction",
                 "min_draft_deg", "min_thickness_mm", "max_triangles", "min_clearance_mm", "measure", "gen_mm",
                 "timeout_s"), "inspect")
    checks = list(args.get("checks") or DEFAULT_CHECKS)
    unknown = [c for c in checks if c not in CHECKS]
    if unknown or len(set(checks)) != len(checks):
        raise UsageError(f"checks must be distinct names from {', '.join(CHECKS)}")
    if "section" in checks and not args.get("section"):
        raise UsageError("the section check needs section {axis, at_mm}")
    if "measure" in checks and not args.get("measure"):
        raise UsageError("the measure check needs measure {a, b}")
    if args.get("material") is not None and args.get("density_kg_m3") is not None:
        raise UsageError("give material or density_kg_m3, not both")
    density = None
    if args.get("material") is not None:
        density = material_density(args["material"], ctx.env)
    elif args.get("density_kg_m3") is not None:
        density = {"kg_m3": float(args["density_kg_m3"]), "material": None, "source": "density_kg_m3 argument"}
    run = store.open_run(ctx.info, args.get("run_id") or "latest", require_ok=True)
    idir = store.ensure_subdir(run.root, "inspect")
    inspect_id = _new_inspect_id(idir)
    section = args.get("section")
    svg_name = None
    if "section" in checks:
        svg_name = f"{inspect_id}-section-{section['axis']}-{_at_label(section['at_mm'])}.svg"
    wargs = {"inspect_id": inspect_id, "checks": checks, "part": args.get("part"), "density": density,
             "list_faces": bool(args.get("list_faces", False)), "section": section,
             "section_svg": svg_name, "pull_direction": list(args.get("pull_direction") or [0.0, 0.0, 1.0]),
             "min_draft_deg": float(args.get("min_draft_deg", 1.0)),
             "min_thickness_mm": args.get("min_thickness_mm"),
             "max_triangles": int(args.get("max_triangles", 300_000)),
             "min_clearance_mm": float(args.get("min_clearance_mm", 0.0)), "measure": args.get("measure"),
             "gen_mm": float(args.get("gen_mm", 4.0))}
    outcome = ctx.job(run, "inspect", wargs, timeout_s=args.get("timeout_s"))
    try:
        result = outcome.result or {}
        out_checks = result.get("checks") if isinstance(result.get("checks"), dict) else {}
        warnings = list(outcome.warnings)
        if svg_name and svg_name in outcome.files and isinstance(out_checks.get("section"), dict):
            s = ctx.settings()
            try:
                got = store.promote(outcome.out_dir, [svg_name], idir, max_file_bytes=64 << 20,
                                    max_total_bytes=s.max_run_mb << 20)
                out_checks["section"]["svg"] = got[svg_name]["path"]
            except CadError as e:
                warnings.append(f"the section SVG was not kept ({e})")
                out_checks["section"]["svg"] = None
    finally:
        ctx.finish(outcome)
    report = {"run_id": run.id, "inspect_id": inspect_id, "checks": out_checks,
              "report_path": str(idir / f"{inspect_id}.json"), "timings_s": outcome.timings_s,
              "peak_rss_mb": outcome.peak_rss_mb, "sandbox": outcome.sandbox, "warnings": warnings}
    store.write_json_atomic(idir / f"{inspect_id}.json", report)
    return report


# --- notes ------------------------------------------------------------------------------------------------------------

def op_note(ctx: OpContext, args: dict) -> dict:
    _only(args, ("text", "tags", "run_id"), "note")
    if args.get("text") is None:
        raise UsageError("note needs text")
    return notes_mod.append(ctx.info, args["text"], args.get("tags"), args.get("run_id"))


def op_notes(ctx: OpContext, args: dict) -> dict:
    _only(args, (), "notes")
    return notes_mod.read_all(ctx.info)


# --- doctor -----------------------------------------------------------------------------------------------------------

def _aces_report(env: Mapping[str, str]) -> dict:
    cmd = project_mod.aces_command(env)
    cap = project_mod.ledger_capability(env)
    add = False
    if cmd:
        probe = project_mod.run_aces(["project", "add-cad-source", "--help"], env=env)
        add = probe is not None and probe.returncode == 0
    return {"cli": " ".join(cmd) if cmd else None, "ledger_record": bool(cap["available"]), "add_cad_source": add}


def op_doctor(ctx: OpContext, args: dict) -> dict:
    """The env job and the selftest job, uncached: versions, guards and the sandbox self-checks."""
    _only(args, (), "doctor")
    s = ctx.settings()
    worker = {"python": s.worker_cmd_override[0] if s.worker_cmd_override else s.python, "ok": False,
              "versions": {}}
    sandbox_block: dict = {}
    checks: list[dict] = []
    notes: list[str] = []
    try:
        report = env_job(ctx, cached=False)
        worker["ok"] = report["status"] == "ok"
        worker["versions"] = report.get("versions") or {}
        sandbox_block = report.get("sandbox") or {}
        if report["status"] != "ok":
            err = report.get("error") or {}
            notes.append(f"env job: {err.get('kind')}: {err.get('message')}")
            if report.get("stderr"):
                notes.append("env job stderr tail: " + report["stderr"][-600:])
    except CadError as e:
        notes.append(f"the worker could not start: {e.kind}: {e}")
    if worker["ok"]:
        checks, more = _selftest(ctx)
        notes.extend(more)
    strict_ok, missing = _strict_ready(sandbox_block)
    failed = [c for c in checks if c.get("pass") is False]
    if s.sandbox == "strict":
        ready = worker["ok"] and strict_ok and bool(checks) and not failed
    else:
        ready = worker["ok"] and bool(checks) and not failed
    out = {"verdict": "ready" if ready else "not-ready", "mode": s.sandbox, "worker": worker,
           "sandbox": sandbox_block, "checks": checks, "aces": _aces_report(ctx.env), "cache_dir": str(s.cache)}
    if missing:
        out["missing"] = missing
    if notes:
        out["notes"] = notes
    return out


def _selftest(ctx: OpContext) -> tuple[list[dict], list[str]]:
    """Run the selftest job against a planted secret outside every root; the parent adds its own checks."""
    s = ctx.settings()
    os.makedirs(s.cache, mode=0o700, exist_ok=True)
    base = store.ensure_subdir(s.cache, "selftest")
    tag = secrets.token_hex(4)
    marker = f"aces-cad-selftest-marker-{secrets.token_hex(8)}"
    secret = base / f"secret-{tag}.txt"
    outside = base / f"outside-{tag}.txt"
    store.write_atomic(secret, (marker + "\n").encode("ascii"))
    notes: list[str] = []
    job_dir = sandbox.new_job(base, "selftest")
    try:
        outcome = sandbox.run_worker("selftest", {"secret": str(secret), "outside": str(outside)}, job_dir=job_dir,
                                     ro_dirs=[], cache_rw=False, limits=ctx.limits("selftest", None),
                                     agent_code=False, env=ctx.env, cancel=ctx.cancel, progress=None)
        checks = list((outcome.result or {}).get("checks") or []) if outcome.status == "ok" else []
        if outcome.status != "ok":
            err = outcome.error or {}
            notes.append(f"selftest job: {err.get('kind')}: {err.get('message')}")
        leaked = marker in outcome.stdout or marker in outcome.stderr or marker in json.dumps(outcome.result or {})
        checks.append({"check": "the planted secret appears in no output", "expect": "absent",
                       "got": "present" if leaked else "absent", "pass": not leaked})
        exists = os.path.lexists(outside)
        checks.append({"check": "no file was written outside the job directory", "expect": "absent",
                       "got": "present" if exists else "absent", "pass": not exists})
    finally:
        sandbox.finish_job(job_dir, env=ctx.env)
        for p in (secret, outside):
            try:
                os.unlink(p)
            except OSError:
                pass
    return checks, notes


# --- dispatch ---------------------------------------------------------------------------------------------------------

_OPS = {"start": op_start, "run": op_run, "runs": op_runs, "prune": op_prune, "params": op_params,
        "inspect": op_inspect, "note": op_note, "notes": op_notes, "doctor": op_doctor}


def dispatch(name: str, kwargs: Mapping | None, *, env=None, cancel=None, progress=None) -> dict:
    """validate -> class gate -> op. Returns the operation's JSON-able result; raises CadError."""
    if name not in OPERATIONS:
        raise UsageError(f"unknown operation {name!r:.40}")
    if kwargs is not None and not isinstance(kwargs, Mapping):
        raise UsageError("the arguments must be an object")
    environ = os.environ if env is None else env
    args = dict(kwargs or {})
    project = args.pop("project", None)
    _validate_schema(name, args)
    info = None
    if name != "params":
        config.settings(environ)  # ConfigError before anything runs
    if name not in NO_GATE:
        info = project_mod.gate(project, environ)
    ctx = OpContext(info=info, env=environ, cancel=cancel, progress=progress)
    if name == "render":
        from . import views
        return views.op_render(ctx, args)
    if name == "drawing":
        from . import views
        return views.op_drawing(ctx, args)
    if name == "pull":
        from . import pull
        return pull.op_pull(ctx, args)
    return _OPS[name](ctx, args)
