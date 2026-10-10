"""The worker entry point (section 8.2 of the build spec).

    python -I -B -X utf8 -X faulthandler /opt/aces/cad/lib/aces_cad/worker/main.py   (job JSON on stdin)

1. Read stdin (at most 1 MiB), parse the job, put job["lib_dir"] first on sys.path.
2. Prelude (POSIX): PR_SET_PDEATHSIG(SIGKILL) and exit at once if the parent is gone; nice; rlimits with soft = hard
   (AS, FSIZE, NOFILE, CORE; CPU timeout + 10 / + 15); SIGXFSZ ignored.
3. Guards (Linux x86_64): seccomp, then Landlock, before any heavy import. Strict mode without both writes kind
   sandbox_unavailable and exits 0, before any agent code or heavy import (env and selftest only report).
4. Imports of IMPORTS[op] with fds 1 and 2 on log/import.txt; the OCCT thread pool set to the job's thread count.
5. Op run only: a 16 MiB memory reserve and the audit hook, then the script.
6. result.json written atomically (tmp + os.replace); exit 0. MemoryError (or Standard_OutOfMemory) anywhere frees the
   reserve and reports kind memory; any other exception escaping aces-cad's code is kind internal.
"""
from __future__ import annotations

import gc
import importlib
import json
import math
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

OPS = {"run": "aces_cad.worker.script:op_run", "env": "aces_cad.worker.guard:op_env",
       "selftest": "aces_cad.worker.guard:op_selftest", "inspect": "aces_cad.worker.checks:op_inspect",
       "render": "aces_cad.worker.render:op_render", "drawing": "aces_cad.worker.drawing:op_drawing",
       "export": "aces_cad.worker.export:op_export"}
IMPORTS = {"run": ["build123d", "OCP", "numpy", "scipy", "skimage.measure", "trimesh"],
           "inspect": ["build123d", "OCP", "numpy", "scipy", "trimesh"],
           "render": ["build123d", "OCP", "numpy", "trimesh", "matplotlib"],
           "drawing": ["build123d", "OCP", "numpy", "ezdxf", "matplotlib"],
           "export": ["build123d", "OCP", "numpy", "trimesh", "lib3mf"],
           "env": ["build123d", "OCP", "numpy", "scipy", "skimage.measure", "trimesh", "matplotlib", "ezdxf",
                   "lib3mf"],
           "selftest": []}
JOB_MAX = 1 << 20
TRACEBACK_MAX = 4000
RESULT_SCHEMA = "aces.cad.result/1"
RESERVE: bytearray | None = None


class WorkerError(Exception):
    """A failure an op reports with its kind (usage, result, not_found, integrity, ...) and structured extras."""

    def __init__(self, kind: str, message: str, **extra):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.extra = extra


@dataclass
class Ctx:
    job: dict
    job_dir: Path
    out_dir: Path
    run_dir: Path | None
    limits: dict
    versions: dict
    warnings: list[str]
    files: list[str] = field(default_factory=list)
    timings: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)

    def warn(self, message: str) -> None:
        text = str(message)[:1000]
        if text not in self.warnings and len(self.warnings) < 100:
            self.warnings.append(text)

    def out_file(self, rel: str) -> Path:
        """<out_dir>/rel for a file this op writes; refuses absolute paths and '..'; registers the name."""
        if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")) or "\\" in rel or ":" in rel:
            raise WorkerError("internal", f"bad output name {rel!r:.80}")
        parts = rel.split("/")
        if any(p in ("", ".", "..") for p in parts):
            raise WorkerError("internal", f"bad output name {rel!r:.80}")
        path = self.out_dir.joinpath(*parts)
        if len(parts) > 1:
            os.makedirs(path.parent, mode=0o700, exist_ok=True)
        if rel not in self.files:
            self.files.append(rel)
        return path


def jsonable(obj, depth: int = 0):
    """A JSON-able copy: numpy scalars and arrays to Python, tuples to lists, non-finite floats to None."""
    if depth > 60:
        return None
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, float):
        return float(obj) if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): jsonable(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v, depth + 1) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    tolist = getattr(obj, "tolist", None)
    if callable(tolist):  # numpy scalars and arrays
        return jsonable(tolist(), depth + 1)
    return str(obj)


def degraded_warning(missing: list[str]) -> str:
    """The permissive-mode warning (section 8.9), saying what each missing kernel layer no longer stops."""
    effects = []
    if "landlock" in missing:
        effects.append("agent code can read and write what OCCT opens anywhere the user can")
    if "seccomp" in missing:
        effects.append("native code in a run can open network sockets and start processes")
    return f"the sandbox is degraded (missing: {', '.join(missing)}): {'; '.join(effects) or 'see the sandbox note'}"


def _write_result(job_dir: Path, doc: dict) -> None:
    data = json.dumps(jsonable(doc), ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    tmp = job_dir / f".result.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(str(tmp), flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
    finally:
        os.close(fd)
    os.replace(tmp, job_dir / "result.json")


def _is_memory(exc: BaseException) -> bool:
    seen = 0
    while exc is not None and seen < 20:
        if isinstance(exc, MemoryError) or type(exc).__name__ == "Standard_OutOfMemory":
            return True
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return False


def _release_memory(ctx: Ctx | None, exc: BaseException | None = None) -> None:
    """Free the reserve, the script's namespace and the frames the exception chain keeps alive, then collect."""
    global RESERVE
    RESERVE = None
    seen = 0
    while exc is not None and seen < 20:
        if exc.__traceback__ is not None:
            traceback.clear_frames(exc.__traceback__)
            exc.__traceback__ = None
        nxt = exc.__cause__ or exc.__context__
        exc.__cause__ = exc.__context__ = None
        exc, seen = nxt, seen + 1
    if ctx is not None:
        ns = ctx.state.pop("namespace", None)
        if isinstance(ns, dict):
            ns.clear()
        roots, report = ctx.state.get("roots"), ctx.state.get("sandbox")
        ctx.state.clear()
        ctx.state.update(roots=roots, sandbox=report)
    gc.collect()


def run(job: dict, t0: float) -> int:
    """Run one job (the job JSON already parsed and lib_dir on sys.path); returns the exit code."""
    global RESERVE
    job_dir = Path(job["job_dir"])
    op = job["op"]
    timings = {"read": round(time.monotonic() - t0, 4)}
    from aces_cad.worker import guard
    try:
        t = time.monotonic()
        prelude = guard.prelude(job)
        timings["prelude"] = round(time.monotonic() - t, 4)
        t = time.monotonic()
        roots = guard.roots(job)
        report = guard.install_guards(job, roots, prelude)
        timings["guards"] = round(time.monotonic() - t, 4)
    except Exception as e:  # noqa: BLE001 (no agent code has run; report and stop)
        _write_result(job_dir, {"schema": RESULT_SCHEMA, "job_id": job.get("job_id"), "op": op, "status": "error",
                                "payload": None, "files": [], "timings_s": timings, "sandbox": {}, "versions": {},
                                "warnings": [],
                                "error": {"kind": "internal", "message": f"the sandbox set-up failed: "
                                                                         f"{type(e).__name__}: {str(e)[:1000]}",
                                          "traceback": traceback.format_exc()[-TRACEBACK_MAX:]}})
        return 0
    ctx = Ctx(job=job, job_dir=job_dir, out_dir=job_dir / "out",
              run_dir=Path(job["ro_dirs"][0]) if job.get("ro_dirs") else None, limits=dict(job.get("limits") or {}),
              versions={}, warnings=[])
    ctx.state["roots"] = roots
    ctx.state["sandbox"] = report
    doc = {"schema": RESULT_SCHEMA, "job_id": job.get("job_id"), "op": op, "status": "error", "payload": None,
           "error": None, "files": ctx.files, "timings_s": timings, "sandbox": report, "versions": ctx.versions,
           "warnings": ctx.warnings}
    if report.get("missing") and report.get("mode") == "permissive" and job.get("sandbox", {}).get("audit_hook"):
        ctx.warn(degraded_warning(report["missing"]))
    if report.get("mode") == "strict" and report.get("missing") and op not in ("env", "selftest"):
        doc["error"] = {"kind": "sandbox_unavailable",
                        "message": "the strict sandbox needs seccomp and Landlock; missing: "
                                   + "; ".join(f"{m} ({(report.get(m) or {}).get('detail') or 'not active'})"
                                               for m in report["missing"]),
                        "missing": list(report["missing"])}
        _write_result(job_dir, doc)
        return 0
    spawned_env = dict(os.environ)
    try:
        t = time.monotonic()
        ctx.versions.update(guard.import_modules(op, job_dir, int(ctx.limits.get("threads") or 2), ctx))
        timings["imports"] = round(time.monotonic() - t, 4)
        if op == "run":
            # agent code sees exactly the spawned (allowlisted) environment: drop what libraries set at import
            # (build123d's FONTCONFIG_*, re-set by every FontManager; sklearn/threadpoolctl's KMP_*, read at
            # OpenMP start-up only)
            for key in [k for k in os.environ if k not in spawned_env]:
                del os.environ[key]
            os.environ.update(spawned_env)
            RESERVE = bytearray(16 << 20)
            if job.get("sandbox", {}).get("audit_hook"):
                from aces_cad.worker import audit
                audit.install(roots)
        module_name, func_name = OPS[op].split(":")
        fn = getattr(importlib.import_module(module_name), func_name)
        t = time.monotonic()
        payload = fn(dict(job.get("args") or {}), ctx)
        timings["op"] = round(time.monotonic() - t, 4)
        timings.update({k: round(float(v), 4) for k, v in ctx.timings.items()})
        doc.update(status="ok", payload=payload if isinstance(payload, dict) else {"value": payload})
    except WorkerError as e:
        error = {"kind": e.kind, "message": e.message, **{k: v for k, v in e.extra.items()}}
        if e.kind == "memory":
            _release_memory(ctx, e)
        doc.update(status="error", payload=None, error=error)
    except BaseException as e:  # noqa: BLE001 (every failure becomes a result)
        if _is_memory(e):
            _release_memory(ctx, e)
            doc.update(status="error", payload=None,
                       error={"kind": "memory", "message": f"the {op} job ran out of memory (RLIMIT_AS "
                                                           f"{ctx.limits.get('memory_mb')} MiB): "
                                                           f"{type(e).__name__}",
                              "limit": "memory_mb"})
        else:
            tb = traceback.format_exc()
            doc.update(status="error", payload=None,
                       error={"kind": "internal", "message": f"{type(e).__name__}: {str(e)[:1500]}",
                              "exception": type(e).__name__, "traceback": tb[-TRACEBACK_MAX:]})
    try:
        _write_result(job_dir, doc)
    except MemoryError:
        _release_memory(ctx)
        _write_result(job_dir, {"schema": RESULT_SCHEMA, "job_id": job.get("job_id"), "op": op, "status": "error",
                                "payload": None, "error": {"kind": "memory", "message": "out of memory while "
                                                                                       "writing the result"},
                                "files": [], "timings_s": timings, "sandbox": report, "versions": {},
                                "warnings": []})
    return 0


def _entry() -> int:
    t0 = time.monotonic()
    data = sys.stdin.buffer.read(JOB_MAX + 1)
    if len(data) > JOB_MAX:
        sys.stderr.write("aces-cad worker: the job is larger than 1 MiB\n")
        return 2
    try:
        job = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        sys.stderr.write("aces-cad worker: the job is not valid JSON\n")
        return 2
    if (not isinstance(job, dict) or job.get("schema") != "aces.cad.job/1" or job.get("op") not in OPS
            or not isinstance(job.get("job_dir"), str) or not isinstance(job.get("lib_dir"), str)):
        sys.stderr.write("aces-cad worker: the job is malformed\n")
        return 2
    sys.path.insert(0, job["lib_dir"])
    import aces_cad.worker.main as worker_main  # the module copy, so ops and this entry share WorkerError
    return worker_main.run(job, t0)


if __name__ == "__main__":
    sys.exit(_entry())
