"""Jobs: one worker process per job (section 8 of the build spec).

The parent never executes geometry or agent code. A job is a fresh process of the engineering venv's python running
aces_cad/worker/main.py with:

- the job JSON on stdin (then closed), stdout and stderr in files of the job directory (never pipes), cwd
  <job_dir>/cwd, close_fds, a new session (POSIX) or process group (Windows);
- exactly the allowlisted environment (PATH, HOME and TMPDIR inside the job, C.UTF-8, UTC, the user, Agg, the cache
  dirs, the thread counts, MALLOC_ARENA_MAX, NO_COLOR): no ACES_*, ONSHAPE_*, proxy, ssh-agent, display or bus
  variable reaches a worker;
- a wait loop on a 100 ms tick, without threads: the wall clock and cancellation, both log files against
  ACES_CAD_MAX_LOG_MB every tick, the job directory against ACES_CAD_MAX_RUN_MB and 100,000 entries every second, the
  peak RSS (VmHWM) every tick on Linux, and a progress call every 5 s;
- the kill: killpg(SIGKILL) on POSIX, taskkill /F /T on Windows, then wait.

result.json is untrusted: it is read with safe_read (4 MiB), checked for schema, job id, op, types and caps (strings
<= 4000 characters, lists <= 500 items, finite numbers, depth <= 64), and anything malformed becomes kind crash
("protocol"). The caller promotes the job's outputs, then calls finish_job.
"""
from __future__ import annotations

import json
import math
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from . import config, store
from .errors import CadError, InternalError, from_kind

TICK_S = 0.1
JOB_JSON_MAX = 1 << 20
RESULT_MAX = 4 << 20
LOG_HEAD = 16 << 10
LOG_TAIL = 48 << 10
MAX_ENTRIES = 100_000
LEFTOVER_MAX = 50
STR_CAP = 4000
LIST_CAP = 500
DEPTH_CAP = 64
ERROR_KEYS = ("exception", "frames", "hint", "raised_in", "line", "offset", "text", "limit", "event", "path",
              "reason", "missing", "detail", "traceback")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SURROGATE_RE = re.compile("[\ud800-\udfff]")
REPLACEMENT = chr(0xFFFD)
_THREAD_VARS = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS")
_MEMORY_MARKERS = ("MemoryError", "Standard_OutOfMemory", "std::bad_alloc")


@dataclass(frozen=True)
class Limits:
    timeout_s: int
    memory_mb: int
    threads: int
    max_file_mb: int
    nice: int

    def to_dict(self) -> dict:
        return {"timeout_s": self.timeout_s, "memory_mb": self.memory_mb, "threads": self.threads,
                "max_file_mb": self.max_file_mb, "nice": self.nice}


@dataclass
class WorkerOutcome:
    op: str
    status: str                      # "ok" | "error"
    result: dict | None
    error: dict | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    returncode: int | None
    signal: str | None
    timings_s: dict
    peak_rss_mb: float | None
    sandbox: dict
    versions: dict
    warnings: list[str]
    job_dir: Path
    out_dir: Path
    leftover_files: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    killed: bool = False             # killed by the parent, or no valid result: leftover_files applies


def new_job(base: Path, op: str) -> Path:
    """<base>/jobs/<op>-<8 hex>/{cwd/home,cwd/tmp,out,log}, every directory 0700."""
    if not re.fullmatch(r"[a-z]{1,16}", op):
        raise InternalError(f"bad job op {op!r}")
    jobs = store.ensure_subdir(Path(base), "jobs")
    for _ in range(100):
        job_dir = jobs / f"{op}-{secrets.token_hex(4)}"
        try:
            os.mkdir(job_dir, 0o700)
        except FileExistsError:
            continue
        for sub in ("cwd", "cwd/home", "cwd/tmp", "out", "log"):
            os.mkdir(job_dir / sub, 0o700)
        return job_dir
    raise InternalError(f"could not create a job directory in {jobs}")


def finish_job(job_dir: Path, *, env: Mapping[str, str] | None = None) -> None:
    """Remove the job directory unless ACES_CAD_KEEP_JOBS=1."""
    try:
        keep = config.keep_jobs(env)
    except CadError:
        keep = False
    if not keep:
        store.remove_tree(Path(job_dir))


def _user() -> str:
    if os.name == "posix":
        try:
            import pwd
            return pwd.getpwuid(os.getuid()).pw_name
        except (ImportError, KeyError):
            return str(os.getuid())
    return os.environ.get("USERNAME") or "user"


def child_env(job_dir: Path, cache: Path, cache_rw: bool, threads: int,
              env: Mapping[str, str] | None = None) -> dict:
    """Exactly the allowlisted variables (section 8.1); nothing is inherited."""
    home = job_dir / "cwd" / "home"
    tmp = job_dir / "cwd" / "tmp"
    out = {"PATH": "/usr/bin:/bin", "HOME": str(home), "TMPDIR": str(tmp), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
           "TZ": "UTC", "USER": _user(), "LOGNAME": _user(), "MPLBACKEND": "Agg",
           "MPLCONFIGDIR": str(cache / "mpl") if cache_rw else str(home / ".mpl"),
           "XDG_CACHE_HOME": str(cache) if cache_rw else str(home / ".cache"),
           "MALLOC_ARENA_MAX": "2", "NO_COLOR": "1"}
    for var in _THREAD_VARS:
        out[var] = str(threads)
    if os.name == "nt":
        for var in ("SYSTEMROOT", "WINDIR", "PATHEXT"):
            value = os.environ.get(var)
            if value:
                out[var] = value
        out["TEMP"] = out["TMP"] = str(tmp)
        out["USERPROFILE"] = str(home)  # Path.home() reads it on Windows (ezdxf and build123d call it at import)
    source = os.environ if env is None else env
    if (source.get("ACES_CAD_WORKER_CMD") or "").strip():
        # tests only: the stand-in worker reads its mode from FAKE_WORKER_* (never set in a real deployment)
        out.update({k: v for k, v in source.items() if k.startswith("FAKE_WORKER_")})
    return out


def _kill(proc: subprocess.Popen) -> None:
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except OSError:
                pass
    else:
        try:  # stdin DEVNULL: an inherited stdin pipe that aces-cad-mcp's reader is blocked on hangs a child at start
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


def _vm_hwm_mb(pid: int) -> float | None:
    try:
        with open(f"/proc/{pid}/status", "rb") as fh:
            for line in fh:
                if line.startswith(b"VmHWM:"):
                    return int(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        return None
    return None


def _dir_usage(root: Path, cap_entries: int) -> tuple[int, int]:
    """(entries, apparent bytes) under root, walked with lstat; stops counting at cap_entries + 1."""
    entries = 0
    total = 0
    stack = [str(root)]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                entries += 1
                if entries > cap_entries:
                    return entries, total
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISDIR(st.st_mode):
                    stack.append(e.path)
                else:
                    total += st.st_size
    return entries, total


def _leftovers(job_dir: Path) -> list[str]:
    names: list[str] = []
    for top in ("cwd", "out"):
        stack = [(job_dir / top, top)]
        while stack and len(names) < LEFTOVER_MAX:
            d, rel = stack.pop()
            try:
                entries = sorted(os.scandir(d), key=lambda e: e.name)
            except OSError:
                continue
            for e in entries:
                if len(names) >= LEFTOVER_MAX:
                    break
                sub = f"{rel}/{e.name}"
                if e.is_dir(follow_symlinks=False):
                    if sub not in ("cwd/home", "cwd/tmp"):
                        names.append(sub + "/")
                    stack.append((Path(e.path), sub))
                else:
                    names.append(sub)
    return names


def clean_text(text: str) -> str:
    """Strip ANSI escapes and control characters other than newline and tab; drop Fontconfig noise lines."""
    text = _ANSI_RE.sub("", text.replace("\r\n", "\n"))
    text = _CTRL_RE.sub("", text)
    lines = [ln for ln in text.split("\n") if not ln.startswith(("Fontconfig error:", "Fontconfig warning:"))]
    return "\n".join(lines)


def read_log(path: Path) -> tuple[str, bool]:
    """A log file as head 16 KiB + tail 48 KiB with the marker [... N bytes omitted ...], cleaned."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size <= LOG_HEAD + LOG_TAIL:
                data = fh.read(LOG_HEAD + LOG_TAIL)
                truncated = False
            else:
                head = fh.read(LOG_HEAD)
                fh.seek(size - LOG_TAIL)
                tail = fh.read(LOG_TAIL)
                omitted = size - LOG_HEAD - LOG_TAIL
                data = head + f"\n[... {omitted} bytes omitted ...]\n".encode("ascii") + tail
                truncated = True
    except OSError:
        return "", False
    return clean_text(data.decode("utf-8", "replace")), truncated


class _Bad(Exception):
    pass


def _check_value(value, depth: int = 0):
    """A copy of an untrusted JSON value with lone surrogates replaced; _Bad when it breaks a cap."""
    if depth > DEPTH_CAP:
        raise _Bad("nested deeper than 64")
    if isinstance(value, str):
        if len(value) > STR_CAP:
            raise _Bad(f"a string of {len(value)} characters")
        return _SURROGATE_RE.sub(REPLACEMENT, value)
    if isinstance(value, bool) or value is None or isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _Bad("a number that is not finite")
        return value
    if isinstance(value, list):
        if len(value) > LIST_CAP:
            raise _Bad(f"a list of {len(value)} items")
        return [_check_value(v, depth + 1) for v in value]
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if not isinstance(k, str) or len(k) > STR_CAP:
                raise _Bad("a key that is not a short string")
            out[_SURROGATE_RE.sub(REPLACEMENT, k)] = _check_value(v, depth + 1)
        return out
    raise _Bad(f"a value of type {type(value).__name__}")


def _no_constants(name: str):
    raise _Bad(f"the number {name}")


def parse_result(data: bytes, job_id: str, op: str) -> dict:
    """The checked result.json, or _Bad."""
    try:
        raw = json.loads(data.decode("utf-8"), parse_constant=_no_constants)
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        raise _Bad(f"result.json is not valid JSON ({type(e).__name__})") from None
    res = _check_value(raw)
    if not isinstance(res, dict) or res.get("schema") != "aces.cad.result/1":
        raise _Bad("result.json has the wrong schema")
    if res.get("job_id") != job_id or res.get("op") != op:
        raise _Bad("result.json belongs to another job")
    status = res.get("status")
    if status not in ("ok", "error"):
        raise _Bad("result.json has no valid status")
    if status == "ok" and not isinstance(res.get("payload"), dict):
        raise _Bad("result.json has no payload object")
    if status == "error":
        err = res.get("error")
        if not isinstance(err, dict) or not isinstance(err.get("kind"), str) or not isinstance(err.get("message"),
                                                                                                 str):
            raise _Bad("result.json has no valid error")
    for key, kind in (("files", list), ("timings_s", dict), ("sandbox", dict), ("versions", dict),
                      ("warnings", list)):
        if key in res and res[key] is not None and not isinstance(res[key], kind):
            raise _Bad(f"result.json {key} has the wrong type")
    if any(not isinstance(f, str) for f in res.get("files") or []):
        raise _Bad("result.json files are not names")
    if any(not isinstance(w, str) for w in res.get("warnings") or []):
        raise _Bad("result.json warnings are not text")
    return res


def _signal_name(rc: int | None) -> str | None:
    if rc is None or rc >= 0 or os.name != "posix":
        return None
    try:
        return signal.Signals(-rc).name
    except ValueError:
        return f"signal {-rc}"


def job_document(op: str, args: dict, *, job_dir: Path, ro_dirs: list[Path], cache_rw: bool, limits: Limits,
                 agent_code: bool, env: Mapping[str, str] | None = None) -> dict:
    """The job JSON a worker receives on stdin (section 5.4)."""
    cfg = config.settings(env)
    job_dir = Path(job_dir)
    return {"schema": "aces.cad.job/1", "job_id": job_dir.name, "op": op, "args": args, "job_dir": str(job_dir),
            "ro_dirs": [str(p) for p in ro_dirs], "cache_dir": str(cfg.cache), "cache_rw": bool(cache_rw),
            "limits": limits.to_dict(), "sandbox": {"mode": cfg.sandbox, "audit_hook": bool(agent_code)},
            "parent_pid": os.getpid(), "lib_dir": str(config.lib_dir())}


def run_worker(op: str, args: dict, *, job_dir: Path, ro_dirs: list[Path], cache_rw: bool, limits: Limits,
               agent_code: bool, env: Mapping[str, str] | None = None, cancel: threading.Event | None = None,
               progress: Callable[[str], None] | None = None) -> WorkerOutcome:
    """Run one worker job and wait for it (section 8.1); never raises for a failure of the job itself."""
    cfg = config.settings(env)
    argv = config.worker_cmd(env)
    job_dir = Path(job_dir)
    cache = cfg.cache
    if cache_rw:
        os.makedirs(cache, mode=0o700, exist_ok=True)
    job = job_document(op, args, job_dir=job_dir, ro_dirs=ro_dirs, cache_rw=cache_rw, limits=limits,
                       agent_code=agent_code, env=env)
    payload = json.dumps(job, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    if len(payload) > JOB_JSON_MAX:
        raise InternalError(f"the {op} job is larger than 1 MiB ({len(payload)} bytes)")
    log_dir = job_dir / "log"
    out_fh = open(log_dir / "stdout.txt", "xb")
    err_fh = open(log_dir / "stderr.txt", "xb")
    kwargs: dict = {"stdin": subprocess.PIPE, "stdout": out_fh, "stderr": err_fh, "cwd": str(job_dir / "cwd"),
                    "env": child_env(job_dir, cache, cache_rw, limits.threads, env), "close_fds": True}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    max_log = cfg.max_log_mb << 20
    max_run = cfg.max_run_mb << 20
    started = time.monotonic()
    killed_for: tuple[str, str, str | None] | None = None
    peak: float | None = None
    try:
        proc = subprocess.Popen(argv, **kwargs)
    except OSError as e:
        out_fh.close()
        err_fh.close()
        raise InternalError(f"cannot start the worker {argv[0]}: {e.strerror or type(e).__name__}") from None
    pending = memoryview(payload)
    blocking_stdin = True
    if os.name == "posix":
        try:
            os.set_blocking(proc.stdin.fileno(), False)
            blocking_stdin = False
        except OSError:
            pass
    tick = 0
    try:
        while True:
            if pending is not None:
                try:
                    while pending:
                        n = os.write(proc.stdin.fileno(), pending)
                        pending = pending[n:]
                        if not blocking_stdin and n == 0:
                            break
                except BlockingIOError:
                    pass
                except (BrokenPipeError, OSError):
                    pending = memoryview(b"")
                if not pending:
                    try:
                        proc.stdin.close()
                    except OSError:
                        pass
                    pending = None
            if sys.platform.startswith("linux"):
                hwm = _vm_hwm_mb(proc.pid)
                if hwm is not None:
                    peak = hwm if peak is None else max(peak, hwm)
            if proc.poll() is not None:
                break
            elapsed = time.monotonic() - started
            if elapsed >= limits.timeout_s:
                killed_for = ("timeout", f"the {op} job ran past its {limits.timeout_s} s timeout and was killed",
                              "timeout_s")
                break
            if cancel is not None and cancel.is_set():
                killed_for = ("cancelled", f"the call was cancelled; the {op} job was killed", None)
                break
            for name, fh in (("stdout", out_fh), ("stderr", err_fh)):
                try:
                    size = os.fstat(fh.fileno()).st_size
                except OSError:
                    size = 0
                if size > max_log:
                    killed_for = ("disk", f"{name} grew past {cfg.max_log_mb} MiB (ACES_CAD_MAX_LOG_MB); the {op} "
                                          f"job was killed", "max_log_mb")
                    break
            if killed_for:
                break
            if tick % 10 == 9:
                entries, used = _dir_usage(job_dir, MAX_ENTRIES)
                if entries > MAX_ENTRIES:
                    killed_for = ("disk", f"the {op} job created more than {MAX_ENTRIES} files and was killed",
                                  "max_entries")
                    break
                if used > max_run:
                    killed_for = ("disk", f"the {op} job directory grew past {cfg.max_run_mb} MiB "
                                          f"(ACES_CAD_MAX_RUN_MB) and was killed", "max_run_mb")
                    break
            if progress is not None and tick % 50 == 49:
                try:
                    progress(f"{op} job running ({elapsed:.1f} s of {limits.timeout_s} s)")
                except Exception:  # noqa: BLE001 (a progress sink must never break the job)
                    pass
            tick += 1
            if cancel is not None:
                cancel.wait(TICK_S)
            else:
                time.sleep(TICK_S)
    except BaseException:
        _kill(proc)
        out_fh.close()
        err_fh.close()
        raise
    if killed_for is not None:
        _kill(proc)
    elif os.name == "posix":
        try:  # stragglers in the worker's process group (none can exist under seccomp)
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    wall = time.monotonic() - started
    rc = proc.returncode
    for fh in (out_fh, err_fh):
        fh.close()
    if proc.stdin and not proc.stdin.closed:
        try:
            proc.stdin.close()
        except OSError:
            pass
    stdout, stdout_trunc = read_log(log_dir / "stdout.txt")
    stderr, stderr_trunc = read_log(log_dir / "stderr.txt")
    res = None
    bad = None
    try:
        res = parse_result(store.safe_read(job_dir, "result.json", RESULT_MAX), job_dir.name, op)
    except CadError as e:
        bad = "no result.json" if e.kind == "not_found" else str(e)
    except _Bad as e:
        bad = str(e)
    error = None
    status = "error"
    leftovers: list[str] = []
    sig = _signal_name(rc)
    if killed_for is not None:
        kind, message, limit = killed_for
        error = {"kind": kind, "message": message}
        if limit:
            error["limit"] = limit
    elif os.name == "posix" and rc == -signal.SIGXCPU:
        error = {"kind": "timeout", "message": f"the {op} job hit its CPU time limit (RLIMIT_CPU, "
                                               f"{limits.timeout_s + 10} s) and was killed", "limit": "cpu_s"}
    elif res is not None:
        status = res["status"]
        error = res.get("error") if status == "error" else None
    elif any(m in stderr[-8192:] for m in _MEMORY_MARKERS):
        error = {"kind": "memory", "message": f"the {op} job ran out of memory (RLIMIT_AS {limits.memory_mb} MiB) "
                                              f"before it could report", "limit": "memory_mb"}
    elif os.name == "posix" and rc == -signal.SIGKILL:
        error = {"kind": "crash", "message": f"the {op} job was killed by SIGKILL",
                 "hint": "killed by SIGKILL: the kernel OOM killer or an operator"}
    elif rc is not None and rc < 0:
        error = {"kind": "crash", "message": f"the worker died with {sig}"}
    elif rc not in (0, None):
        shown = f"0x{rc:08X}" if rc > 0xFFFF else str(rc)
        error = {"kind": "crash", "message": f"the worker exited with code {shown} without a valid result ({bad})"}
    else:
        error = {"kind": "crash", "message": f"protocol: the worker exited 0 without a valid result ({bad})"}
    if status != "ok" and (killed_for is not None or res is None):
        leftovers = _leftovers(job_dir)
    if error is not None:
        error = {k: v for k, v in error.items() if k in ("kind", "message") or k in ERROR_KEYS}
    timings = dict((res or {}).get("timings_s") or {})
    timings = {k: v for k, v in timings.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    timings["wall"] = round(wall, 3)
    return WorkerOutcome(op=op, status=status, result=(res or {}).get("payload") if status == "ok" else None,
                         error=error, stdout=stdout, stderr=stderr, stdout_truncated=stdout_trunc,
                         stderr_truncated=stderr_trunc, returncode=rc, signal=sig, timings_s=timings,
                         peak_rss_mb=None if peak is None else round(peak, 1),
                         sandbox=(res or {}).get("sandbox") or {}, versions=(res or {}).get("versions") or {},
                         warnings=list((res or {}).get("warnings") or []), job_dir=job_dir, out_dir=job_dir / "out",
                         leftover_files=leftovers, files=list((res or {}).get("files") or []),
                         killed=status != "ok" and (killed_for is not None or res is None))


def raise_for(outcome: WorkerOutcome, *, run_id: str | None = None) -> None:
    """Raise the CadError of a failed outcome (nothing for status ok); the error carries the job's evidence."""
    if outcome.status == "ok":
        return
    err = dict(outcome.error or {"kind": "crash", "message": "the job failed without an error report"})
    kind = err.pop("kind", "crash")
    message = err.pop("message", "the job failed")
    extra = {k: err[k] for k in ERROR_KEYS if k in err}
    extra.update(run_id=run_id, stdout=outcome.stdout, stderr=outcome.stderr,
                 stdout_truncated=outcome.stdout_truncated, stderr_truncated=outcome.stderr_truncated,
                 timings_s=outcome.timings_s, peak_rss_mb=outcome.peak_rss_mb, sandbox=outcome.sandbox or None,
                 leftover_files=list(outcome.leftover_files) if outcome.killed else None)
    raise from_kind(kind, message, **extra)
