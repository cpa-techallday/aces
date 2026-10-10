"""cad_pull, parent side: the CadSource pull of a successful run (spec section 15).

Order: validate, gate (ops.dispatch), open the run (`latest` = the newest successful run), ask the aces CLI once
whether it offers `ledger record`, run one `export` job (a worker that runs no agent code and re-derives every number
from the run's BREP), refuse an invalid part unless allow_invalid, assemble .<bundle>.partial/ under
<project>/cad/local/, rename it to <UTC stamp>-<name>[-n], then write the cad.pull ledger entry, record the CAD source
and rewrite outcome.json. source.json is written once and carries the sha256 of every other file; outcome.json carries
the sha256 of source.json. Standard library only.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import platform
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, config, errors, project, sandbox, store
from .views import caps, declared_files, open_ok_run, payload, result_kind, run_failed, write_json

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
RUN_RE = re.compile(r"^(latest|[0-9]{8}T[0-9]{6}Z-[0-9a-f]{4})$")
INPUT_FILE_RE = re.compile(r"^inputs/[a-z][a-z0-9_]{0,31}\.(step|stp|brep|stl|3mf|obj|dxf|svg|json|csv|txt|npy|npz)$")
FORMATS = ("step", "stl", "3mf")
FILES = {"step": "part.step", "stl": "part.stl", "3mf": "part.3mf"}
APPLIES = {"solid": FORMATS, "assembly": FORMATS, "sketch": ("step",), "mesh": ("stl",)}
DEFAULT_FORMATS = {"solid": ["step", "stl", "3mf"], "assembly": ["step", "stl", "3mf"], "sketch": ["step"],
                   "mesh": ["stl"]}
NOT_APPLICABLE = {
    ("mesh", "step"): "a mesh has no B-rep; a STEP made from a mesh would be a pretend STEP",
    ("mesh", "3mf"): "3MF is written from a B-rep only in 0.1.0; the mesh is pulled as STL",
    ("sketch", "stl"): "a sketch has no volume; STL needs a closed solid",
    ("sketch", "3mf"): "a sketch has no volume; 3MF needs a closed solid",
}
STEP_HEADER_TIME = "2000-01-01T00:00:00"
LEDGER_KIND = "cad.pull"
LEDGER_MAX_BYTES = 65536
MAX_SCRIPT_BYTES = 4 << 20
MAX_INPUT_BYTES = 256 << 20
PART_KEYS = ("name", "kind", "volume_mm3", "area_mm2", "bbox_mm", "valid", "reasons", "counts")
GOLDEN_NOTE = ("init copies the STEP to cad/part.step (refused if a different cad/part.step exists) and writes "
               "golden/spec.json, a template you must complete (supports, loads, keep_in, material with its source); "
               "once spec.json exists, init is refused and run --step uses any pulled STEP")


def utc_z(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)


def _args(args: dict) -> dict:
    run_id = args.get("run_id")
    if run_id is None:
        run_id = "latest"
    if not isinstance(run_id, str) or not RUN_RE.match(run_id):
        raise errors.UsageError("run_id must be latest or a run id like 20261010T133514Z-a3f9")
    name = args.get("name")
    if name is not None and (not isinstance(name, str) or not NAME_RE.match(name)):
        raise errors.UsageError("name must be letters, digits, '.', '_' and '-' (at most 40 characters, starting with "
                                "a letter or digit)")
    formats = args.get("formats")
    if formats is not None:
        if not isinstance(formats, list) or not formats or any(f not in FORMATS for f in formats) or \
                len(set(formats)) != len(formats):
            raise errors.UsageError(f"formats must be a non-empty list of distinct values from {', '.join(FORMATS)}")
    out = {"run_id": run_id, "name": name, "formats": formats}
    for key, default, lo, hi in (("stl_tolerance_mm", 0.01, 0.001, 1.0), ("angular_tolerance_rad", 0.1, 0.01, 1.0)):
        value = args.get(key)
        if value is None:
            value = default
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= float(value) <= hi:
            raise errors.UsageError(f"{key} must be a number from {lo} to {hi}")
        out[key] = float(value)
    allow = args.get("allow_invalid")
    if allow is not None and not isinstance(allow, bool):
        raise errors.UsageError("allow_invalid must be true or false")
    out["allow_invalid"] = bool(allow)
    timeout = args.get("timeout_s")
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 600):
        raise errors.UsageError("timeout_s must be an integer from 1 to 600")
    out["timeout_s"] = timeout
    return out


def _formats(kind: str, requested: list | None, warnings: list) -> list:
    """The formats to write. The geometry file (part.step for a B-rep, part.stl for a mesh) is always written:
    geometry_sha256 is its sha256, and the CadSource contract is STEP + parameters + sha256."""
    if requested is None:
        return list(DEFAULT_FORMATS[kind])
    chosen = []
    for f in FORMATS:
        if f not in requested:
            continue
        if f in APPLIES[kind]:
            chosen.append(f)
        else:
            warnings.append(f"{f} skipped: {NOT_APPLICABLE.get((kind, f), f'{f} does not apply to a {kind} result')}")
    if not chosen:
        raise errors.UsageError(f"none of the requested formats applies to a {kind} result (it pulls as "
                                f"{', '.join(APPLIES[kind])})")
    geometry = "stl" if kind == "mesh" else "step"
    if geometry not in chosen:
        warnings.append(f"part.{geometry} is always written: geometry_sha256 is its sha256")
        chosen = [f for f in FORMATS if f in chosen or f == geometry]
    return chosen


def _encode(obj) -> bytes:
    return (json.dumps(obj, indent=1, ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def _write_new(path: Path, data: bytes) -> dict:
    """Write a file that must not exist yet (no symlink is followed); returns {"sha256", "bytes"}."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def _bundle_name(stem: str, n: int) -> str:
    return stem if n == 1 else f"{stem}-{n}"


def _reserve(base: Path, stem: str) -> tuple[Path, int]:
    for n in range(1, 100):
        name = _bundle_name(stem, n)
        if os.path.lexists(base / name) or os.path.lexists(base / f".{name}.partial"):
            continue
        try:
            os.mkdir(base / f".{name}.partial", 0o700)
        except FileExistsError:
            continue
        return base / f".{name}.partial", n
    raise errors.InternalError(f"no free bundle name for {stem} in {base} after 99 tries")


def _publish(tmp: Path, base: Path, stem: str, n: int) -> Path:
    """Rename the partial bundle to its final name; a name taken meanwhile moves on to the next n (at most 99)."""
    while n < 100:
        target = base / _bundle_name(stem, n)
        if not os.path.lexists(target):
            try:
                os.rename(tmp, target)
                return target
            except FileExistsError:
                pass
            except OSError as e:
                if e.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                    raise
        n += 1
    raise errors.InternalError(f"no free bundle name for {stem} in {base} after 99 tries")


def _summary(data: dict, kind: str) -> list:
    rows = data.get("summary")
    if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
        raise run_failed("crash", "the export worker returned no per-part summary")
    out = []
    for r in rows:
        if not isinstance(r.get("valid"), bool) or not isinstance(r.get("name"), str):
            raise run_failed("crash", "the export worker returned a part summary without name or valid")
        row = {k: r.get(k) for k in PART_KEYS}
        row["reasons"] = [str(x) for x in r.get("reasons") or []]
        out.append(row)
    if data.get("kind") != kind:
        raise run_failed("integrity", f"the run record says {kind} but its parts.json says {data.get('kind')}: the "
                                      f"run directory was changed after the run")
    return out


def _versions(v: dict) -> dict:
    v = v if isinstance(v, dict) else {}
    lower = {str(k).lower(): val for k, val in v.items()}
    return {"build123d": lower.get("build123d"),
            "ocp": lower.get("ocp") or lower.get("cadquery-ocp-novtk") or lower.get("cadquery-ocp")}


def _platform(sandbox_report: dict) -> str:
    plat = sandbox_report.get("platform") if isinstance(sandbox_report, dict) else None
    return plat if isinstance(plat, str) and plat else f"{sys.platform}-{platform.machine().lower()}"


def _digest_method(kind: str, ocp: str | None) -> str:
    if kind == "mesh":
        return ("sha256 of part.stl: trimesh binary STL (zeroed header) of the run's mesh parts; equal for equal "
                "meshes under the same trimesh")
    return (f"sha256 of part.step: build123d export_step from the run's BREP, header time {STEP_HEADER_TIME}, part "
            f"names as labels (OCP {ocp or 'unknown'}); equal for equal geometry under the same build123d and OCP")


def _golden(info: dict, kind: str, parts: list, bundle: Path) -> dict:
    why = None
    if kind == "mesh":
        why = "a mesh result has no STEP: the golden path takes a STEP design space"
    elif kind == "sketch":
        why = "a sketch is not a solid: the golden path takes one closed solid"
    elif kind == "assembly" or len(parts) != 1:
        why = f"an assembly of {len(parts)} parts: the golden path takes one solid design space"
    elif not parts[0].get("valid"):
        why = f"the part is not valid (reasons: {', '.join(parts[0].get('reasons') or []) or 'unknown'})"
    else:
        solids = (parts[0].get("counts") or {}).get("solids")
        if solids != 1:
            why = f"the part holds {solids} solids: the golden path takes one closed solid"
    if why:
        return {"applicable": False, "why_not": why, "init": None, "run": None, "next": None, "note": GOLDEN_NOTE}
    step = bundle / "part.step"
    spec = Path(info["dir"]) / "golden" / "spec.json"
    return {"applicable": True, "why_not": None,
            "init": f"aces golden init {info['name']} --step {step}",
            "run": f"aces golden run --project {info['name']} --step {step}",
            "next": "run" if spec.is_file() else "init", "note": GOLDEN_NOTE}


def _copy_run_file(run, rel: str, dest: Path, max_bytes: int, expected_sha: str | None, what: str) -> dict:
    data = store.safe_read(run.root, rel, max_bytes)
    digest = hashlib.sha256(data).hexdigest()
    if not isinstance(expected_sha, str) or digest != expected_sha:
        raise run_failed("integrity", f"{what} in run {run.id} does not match the sha256 recorded in its run.json "
                                      f"(recorded {expected_sha}, found {digest}): the run directory was changed")
    return _write_new(dest, data)


def op_pull(ctx, args: dict, *, now: datetime | None = None) -> dict:
    """args: run_id, name, formats, stl_tolerance_mm, angular_tolerance_rad, allow_invalid, timeout_s."""
    a = _args(args)
    info = ctx.info
    run, rec = open_ok_run(ctx, a["run_id"])
    kind = result_kind(rec)
    if kind not in APPLIES:
        raise run_failed("integrity", f"run {run.id} records an unknown result kind {kind!r}")
    warnings: list = []
    formats = _formats(kind, a["formats"], warnings)
    name = a["name"] or (rec.get("name") if isinstance(rec.get("name"), str) and
                         NAME_RE.match(rec.get("name") or "") else "part")
    capability = project.ledger_capability(ctx.env)
    job_args = {"formats": formats, "stl_tolerance_mm": a["stl_tolerance_mm"],
                "angular_tolerance_rad": a["angular_tolerance_rad"], "name": rec.get("name") or name, "kind": kind}
    outcome = ctx.job(run, "export", job_args, timeout_s=a["timeout_s"])
    tmp = None
    try:
        data = payload(outcome, "export")
        wanted = [FILES[f] for f in formats]
        written = declared_files(data, set(wanted), "export")
        required = [w for w in wanted if w != "part.3mf"]
        missing = [w for w in required if w not in written]
        if missing:
            raise run_failed("crash", f"the export worker did not write {', '.join(missing)}")
        written = [w for w in wanted if w in written]
        parts = _summary(data, kind)
        warnings += [str(w) for w in outcome.warnings or []] + [str(w) for w in data.get("warnings") or []]
        bad = [p for p in parts if not p["valid"]]
        if bad and not a["allow_invalid"]:
            p = bad[0]
            reasons = ", ".join(p["reasons"]) or "unknown"
            raise run_failed("result", f"part {p['name']} is not valid (reasons: {reasons}): fix it, or pass "
                                       f"allow_invalid (MCP) / --allow-invalid (CLI) to pull it anyway; valid: false "
                                       f"is then recorded")
        valid = not bad
        when = _now(now)
        exported_at = utc_z(when)
        max_file, max_total = caps(ctx, "export", a["timeout_s"])
        local = store.ensure_subdir(store.ensure_subdir(Path(info["dir"]), "cad"), "local")
        stem = f"{when.strftime('%Y%m%dT%H%M%SZ')}-{name}"
        tmp, n = _reserve(local, stem)
        promoted = store.promote(outcome.out_dir, written, tmp, max_file_bytes=max_file, max_total_bytes=max_total)
        files = {k: {"sha256": promoted[k]["sha256"], "bytes": promoted[k]["bytes"]} for k in written}
        script = rec.get("script") if isinstance(rec.get("script"), dict) else {}
        files["source.py"] = _copy_run_file(run, "script.py", tmp / "source.py", MAX_SCRIPT_BYTES,
                                            script.get("sha256"), "script.py")
        inputs_sha = {}
        rec_inputs = rec.get("inputs") if isinstance(rec.get("inputs"), dict) else {}
        if rec_inputs:
            os.mkdir(tmp / "inputs", 0o700)
        for in_name, in_info in sorted(rec_inputs.items()):
            rel = in_info.get("file") if isinstance(in_info, dict) else None
            if not isinstance(rel, str) or not INPUT_FILE_RE.match(rel):
                raise run_failed("integrity", f"run {run.id} records input {in_name!r} with an unexpected file name")
            files[rel] = _copy_run_file(run, rel, tmp / rel, MAX_INPUT_BYTES, in_info.get("sha256"), rel)
            inputs_sha[in_name] = files[rel]["sha256"]
        parameters = {"schema": "aces.cad.local.parameters/1", "params": rec.get("params"),
                      "params_declared": rec.get("params_declared"), "params_overridden": rec.get("params_overridden"),
                      "entry": rec.get("entry"), "params_sha256": rec.get("params_sha256")}
        files["parameters.json"] = _write_new(tmp / "parameters.json", _encode(parameters))
        geometry_file = "part.stl" if kind == "mesh" else "part.step"
        geometry_sha = files[geometry_file]["sha256"]
        ref = f"aces-cad://{info['name']}/runs/{run.id}"
        versions = _versions(outcome.versions)
        stl_check = data.get("stl_check") if isinstance(data.get("stl_check"), dict) else None
        schema = data.get("step_schema")
        source = {
            "schema": "aces.cad.local.source/1", "source": "build123d",
            "tool": {"name": "aces-cad", "version": __version__},
            "ref": ref, "run_id": run.id, "name": name, "kind": kind,
            "script_sha256": script.get("sha256"), "params_sha256": rec.get("params_sha256"), "inputs": inputs_sha,
            "geometry_sha256": geometry_sha, "geometry_digest_method": _digest_method(kind, versions["ocp"]),
            "result": {"parts": parts, "derived_by": "export worker (no agent code)"},
            "files": dict(sorted(files.items())),
            "exported_at": exported_at,
            "export": {"step_schema": ("AP214 (OCCT default)" if schema == "AUTOMOTIVE_DESIGN" else schema)
                       if "part.step" in files else None,
                       "step_header_time": STEP_HEADER_TIME if "part.step" in files else None,
                       "stl_tolerance_mm": a["stl_tolerance_mm"], "angular_tolerance_rad": a["angular_tolerance_rad"],
                       "stl_assembly": "one mesh", "stl_watertight": stl_check.get("watertight") if stl_check else None,
                       "stl_open_edges": stl_check.get("open_edges") if stl_check else None,
                       "step_reimport": data.get("step_check") if "part.step" in files else None,
                       "3mf_reproducible": False},
            "versions": {"run": rec.get("versions") if isinstance(rec.get("versions"), dict) else {},
                         "export": outcome.versions if isinstance(outcome.versions, dict) else {}},
            "environment": {"worker_python": config.worker_python(ctx.env), "platform": _platform(outcome.sandbox),
                            "sandbox": outcome.sandbox},
            "outcome_file": "outcome.json",
            "ledger": {"kind": LEDGER_KIND, "available": capability["available"], "reason": capability["reason"]},
            "project": info, "allow_invalid": a["allow_invalid"], "warnings": list(warnings),
        }
        files["source.json"] = _write_new(tmp / "source.json", _encode(source))
        pending = {"schema": "aces.cad.local.outcome/1", "files": {"source.json": files["source.json"]},
                   "ledger": {"written": False, "kind": LEDGER_KIND, "reason": "pending"},
                   "cad_source": {"recorded": False, "reason": "pending"}}
        _write_new(tmp / "outcome.json", _encode(pending))
        bundle = _publish(tmp, local, stem, n)
        tmp = None
    except BaseException:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
        raise
    finally:
        sandbox.finish_job(outcome.job_dir)

    ledger_payload = {"source": "build123d", "tool": "aces-cad", "tool_version": __version__, "ref": ref,
                      "project": info["name"], "class": info.get("class"), "bundle": str(bundle), "run_id": run.id,
                      "name": name, "kind": kind, "valid": valid, "exported_at": exported_at,
                      "script_sha256": script.get("sha256"), "params_sha256": rec.get("params_sha256"),
                      "geometry_sha256": geometry_sha, "files": {k: v["sha256"] for k, v in sorted(files.items())},
                      "versions": versions}
    size = len(json.dumps(ledger_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    if size >= LEDGER_MAX_BYTES:
        ledger = {"written": False, "kind": LEDGER_KIND,
                  "reason": f"the ledger payload is {size} bytes; aces ledger record takes less than "
                            f"{LEDGER_MAX_BYTES}"}
    else:
        ledger = project.ledger_record(LEDGER_KIND, ledger_payload, ctx.env, capability=capability)
    cad_ref = f"cad/local/{bundle.name}/{geometry_file}"
    cad_source = project.add_cad_source(info["name"], cad_ref, ctx.env)
    final = {"schema": "aces.cad.local.outcome/1", "files": {"source.json": files["source.json"]}, "ledger": ledger,
             "cad_source": cad_source, "updated_at": utc_z(datetime.now(timezone.utc))}
    write_json(bundle, "outcome.json", final)
    return {"bundle": str(bundle), "ref": ref, "run_id": run.id, "name": name, "kind": kind,
            "files": dict(sorted(files.items())), "geometry_sha256": geometry_sha, "valid": valid, "ledger": ledger,
            "cad_source": cad_source, "golden": _golden(info, kind, parts, bundle), "warnings": warnings}
