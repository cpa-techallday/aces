"""The worker's prelude, filesystem roots and kernel guards; the env and selftest ops (sections 8.2, 8.3, 8.9, 18).

roots(job) is the one list of paths used by Landlock and by the audit hook:

- ro dirs: /usr, /etc, /opt/aces, /sys/devices/system/cpu, /proc/<own pid>, /var/cache/fontconfig, the realpaths of
  sys.prefix, sys.base_prefix and sysconfig's stdlib/platstdlib/purelib/platlib, lib_dir, and the job's ro_dirs;
- ro files: /proc/cpuinfo, /proc/meminfo, /dev/urandom, /dev/random, /dev/zero;
- rw dirs: the job dir, plus the cache dir when cache_rw;
- rw files: /dev/null.

Missing paths are skipped and listed. On Windows %SYSTEMROOT% is added to the read roots of the hook.
"""
from __future__ import annotations

import errno
import importlib
import os
import platform
import signal
import sys
import sysconfig
import threading
from dataclasses import dataclass, field
from pathlib import Path

from aces_cad.worker.main import WorkerError

_VERSION_NAMES = {"build123d": ("build123d", "build123d"), "OCP": ("ocp", "cadquery-ocp-novtk"),
                  "numpy": ("numpy", "numpy"), "scipy": ("scipy", "scipy"),
                  "skimage.measure": ("scikit-image", "scikit-image"), "trimesh": ("trimesh", "trimesh"),
                  "matplotlib": ("matplotlib", "matplotlib"), "ezdxf": ("ezdxf", "ezdxf"),
                  "lib3mf": ("lib3mf", "lib3mf")}


@dataclass
class Roots:
    ro_dirs: list[str] = field(default_factory=list)
    ro_files: list[str] = field(default_factory=list)
    rw_dirs: list[str] = field(default_factory=list)
    rw_files: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _linux() -> bool:
    return sys.platform.startswith("linux")


def _x86_64() -> bool:
    return platform.machine().lower() in ("x86_64", "amd64")


def prelude(job: dict) -> dict:
    """PDEATHSIG, the parent check, nice and the rlimits (POSIX); a report of what is enforced."""
    out = {"pdeathsig": False, "limits_enforced": {"memory": False, "cpu": False, "file_size": False},
           "notes": []}
    if os.name != "posix":
        out["notes"].append("rlimits, nice and PDEATHSIG are POSIX only")
        return out
    if _linux():
        try:
            import ctypes
            libc = ctypes.CDLL(None, use_errno=True)
            out["pdeathsig"] = libc.prctl(1, int(signal.SIGKILL), 0, 0, 0) == 0  # PR_SET_PDEATHSIG
        except (OSError, AttributeError):
            out["notes"].append("PR_SET_PDEATHSIG failed")
    if os.getppid() != job.get("parent_pid"):
        os._exit(3)  # the parent died before the death signal was armed
    limits = job.get("limits") or {}
    try:
        os.nice(int(limits.get("nice", 10)))
    except OSError:
        out["notes"].append("nice failed")
    import resource

    def setlim(res, soft, hard=None) -> bool:
        hard = soft if hard is None else hard
        cur_soft, cur_hard = resource.getrlimit(res)
        ok = True
        if cur_hard != resource.RLIM_INFINITY and hard > cur_hard:
            hard, ok = cur_hard, False
        soft = min(soft, hard)
        try:
            resource.setrlimit(res, (soft, hard))
        except (ValueError, OSError):
            return False
        return ok

    mib = 1 << 20
    timeout = int(limits.get("timeout_s", 120))
    out["limits_enforced"]["memory"] = setlim(resource.RLIMIT_AS, int(limits.get("memory_mb", 4096)) * mib)
    out["limits_enforced"]["file_size"] = setlim(resource.RLIMIT_FSIZE, int(limits.get("max_file_mb", 1024)) * mib)
    _soft, nofile_hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    nofile = 1024 if nofile_hard == resource.RLIM_INFINITY else min(1024, nofile_hard)
    setlim(resource.RLIMIT_NOFILE, nofile)
    setlim(resource.RLIMIT_CORE, 0)
    out["limits_enforced"]["cpu"] = setlim(resource.RLIMIT_CPU, timeout + 10, timeout + 15)
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    return out


def roots(job: dict) -> Roots:
    """The filesystem roots of a job (section 8.3); missing paths are skipped and listed."""
    r = Roots()
    pid = os.getpid()
    py = [sys.prefix, sys.base_prefix]
    paths = sysconfig.get_paths()
    py += [paths[k] for k in ("stdlib", "platstdlib", "purelib", "platlib") if paths.get(k)]
    ro_dirs = ["/usr", "/etc", "/opt/aces", "/sys/devices/system/cpu", f"/proc/{pid}", "/var/cache/fontconfig"]
    ro_dirs += [os.path.realpath(p) for p in py] + [job["lib_dir"]] + list(job.get("ro_dirs") or [])
    if os.name == "nt":
        for var in ("SYSTEMROOT", "WINDIR"):
            if os.environ.get(var):
                ro_dirs.append(os.environ[var])
    ro_files = ["/proc/cpuinfo", "/proc/meminfo", "/dev/urandom", "/dev/random", "/dev/zero"]
    rw_dirs = [job["job_dir"]] + ([job["cache_dir"]] if job.get("cache_rw") else [])
    rw_files = ["/dev/null"]
    if os.name == "nt":
        ro_files, rw_files = [], []
    for src, dest, is_dir in ((ro_dirs, r.ro_dirs, True), (ro_files, r.ro_files, False), (rw_dirs, r.rw_dirs, True),
                              (rw_files, r.rw_files, False)):
        for p in src:
            real = os.path.realpath(p)
            ok = os.path.isdir(real) if is_dir else os.path.exists(real)
            if not ok:
                if p not in r.skipped:
                    r.skipped.append(p)
                continue
            if real not in dest:
                dest.append(real)
    return r


def install_guards(job: dict, roots_: Roots, prelude_report: dict) -> dict:
    """seccomp, then Landlock (Linux), before any heavy import; the sandbox block of section 8.9."""
    sandbox = job.get("sandbox") or {}
    mode = sandbox.get("mode", "strict")
    seccomp_rep = {"active": False, "detail": "seccomp is Linux only"}
    landlock_rep = {"active": False, "abi": None, "tcp_rules": False, "scoped": False,
                    "skipped_roots": list(roots_.skipped)}
    missing_reasons = []
    if _linux():
        if _x86_64():
            from aces_cad.worker import seccomp
            seccomp_rep = seccomp.install(os.getpid())
        else:
            seccomp_rep = {"active": False, "detail": f"the seccomp filter is written for x86_64, not "
                                                      f"{platform.machine()}"}
        from aces_cad.worker import landlock
        landlock_rep = landlock.restrict(roots_)
        landlock_rep.setdefault("skipped_roots", list(roots_.skipped))
    else:
        landlock_rep["detail"] = "Landlock is Linux only"
    missing = []
    if not seccomp_rep.get("active"):
        missing.append("seccomp")
        missing_reasons.append(f"seccomp: {seccomp_rep.get('detail')}")
    if not landlock_rep.get("active"):
        missing.append("landlock")
        missing_reasons.append(f"landlock: {landlock_rep.get('detail')}")
    plat = f"{'linux' if _linux() else sys.platform}-{platform.machine().lower()}"
    note = None
    if os.name == "nt":
        note = ("Windows is for development only: the audit hook, the wall clock and the tree kill are the only "
                "guards; nothing here is confined")
    elif not _linux():
        note = "only Linux has the kernel guards; this platform is for development only"
    elif missing_reasons:
        note = "; ".join(missing_reasons)
    limits = dict(job.get("limits") or {})
    return {"mode": mode, "confined": not missing, "platform": plat, "kernel": platform.release(),
            "seccomp": seccomp_rep, "landlock": landlock_rep, "audit_hook": bool(sandbox.get("audit_hook")),
            "pdeathsig": bool(prelude_report.get("pdeathsig")), "limits": limits,
            "limits_enforced": dict(prelude_report.get("limits_enforced") or {}), "missing": missing,
            "note": note}


def _version(module_name: str):
    label, dist = _VERSION_NAMES.get(module_name, (module_name, module_name))
    mod = sys.modules.get(module_name.split(".")[0])
    value = getattr(mod, "__version__", None) if mod is not None else None
    if not isinstance(value, str):
        try:
            from importlib import metadata
            value = metadata.version(dist)
        except Exception:  # noqa: BLE001 (no metadata: no version)
            value = None
    return label, value


def import_modules(op: str, job_dir: Path, threads: int, ctx) -> dict:
    """Import the op's libraries with fds 1 and 2 on log/import.txt; set the OCCT pool; return their versions."""
    from aces_cad.worker.main import IMPORTS
    names = IMPORTS.get(op, [])
    versions = {"python": platform.python_version()}
    if not names:
        return versions
    failures = {}
    sys.stdout.flush()
    sys.stderr.flush()
    saved = (os.dup(1), os.dup(2))
    log = os.open(str(Path(job_dir) / "log" / "import.txt"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.dup2(log, 1)
        os.dup2(log, 2)
        for name in names:
            try:
                importlib.import_module(name)
            except Exception as e:  # noqa: BLE001 (reported below)
                failures[name] = f"{type(e).__name__}: {str(e)[:300]}"
        if "OCP" in sys.modules:
            try:
                from OCP.OSD import OSD_ThreadPool
                OSD_ThreadPool.DefaultPool_s(int(threads))
            except Exception as e:  # noqa: BLE001
                failures["OSD_ThreadPool"] = f"{type(e).__name__}: {str(e)[:300]}"
        if "matplotlib" in sys.modules:
            try:
                sys.modules["matplotlib"].use("Agg")
            except Exception:  # noqa: BLE001
                pass
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(saved[0], 1)
        os.dup2(saved[1], 2)
        for fd in (*saved, log):
            os.close(fd)
    for name in names:
        if name not in failures:
            label, value = _version(name)
            versions[label] = value
    if failures:
        ctx.state["import_failures"] = failures
        if op not in ("env", "selftest"):
            first = next(iter(failures.items()))
            raise WorkerError("internal", f"the worker could not import {first[0]} ({first[1]}): check the "
                                          f"aces-engineering venv (aces-cad doctor)", missing=sorted(failures))
    return versions


# --- op env -----------------------------------------------------------------------------------------------------------

def op_env(args: dict, ctx) -> dict:
    """Versions, the ray backend and what the imports and guards did; never refuses."""
    backend = None
    if "trimesh" in sys.modules:
        import trimesh
        backend = "embree" if getattr(trimesh.ray, "has_embree", False) else "triangle"
    failures = ctx.state.get("import_failures") or {}
    for name, err in failures.items():
        ctx.warn(f"import {name} failed: {err}")
    return {"python": sys.executable, "python_version": platform.python_version(), "versions": dict(ctx.versions),
            "ray_backend": backend, "import_failures": failures, "pid": os.getpid()}


# --- op selftest ------------------------------------------------------------------------------------------------------

def _errno_name(code: int | None) -> str:
    return errno.errorcode.get(code or 0, str(code))


def _attempt(fn) -> tuple[str, int | None]:
    """("allowed" | "errno N (NAME)" | "Type: message", errno)."""
    try:
        fn()
        return "allowed", None
    except OSError as e:
        return f"errno {e.errno} ({_errno_name(e.errno)})", e.errno
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {str(e)[:120]}", None


def op_selftest(args: dict, ctx) -> dict:
    """The doctor's sandbox self-checks, with the hook off, then one with the hook on (section 18)."""
    import socket
    report = ctx.state.get("sandbox") or {}
    seccomp_on = bool((report.get("seccomp") or {}).get("active"))
    landlock_on = bool((report.get("landlock") or {}).get("active"))
    permissive = report.get("mode") == "permissive"
    parent = int(ctx.job.get("parent_pid") or 0)
    secret = str(args.get("secret") or "")
    outside = str(args.get("outside") or "")
    checks: list[dict] = []

    def check(name: str, layer: str | None, want: int | None, fn) -> None:
        active = {"seccomp": seccomp_on, "landlock": landlock_on, None: True}[layer]
        expect = "ok" if want is None else _errno_name(want)
        if layer and not active and permissive:
            detail = (report.get(layer) or {}).get("detail") or "not active"
            checks.append({"check": name, "expect": expect, "got": f"skipped ({layer} is not active: {detail})",
                           "pass": None})
            return
        got, code = _attempt(fn)
        ok = (got == "allowed") if want is None else (code == want)
        checks.append({"check": name, "expect": expect, "got": got, "pass": ok})

    def fork_once():
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        os.waitpid(pid, 0)

    def spawn_once():
        pid = os.posix_spawn("/usr/bin/true", ["true"], {})
        os.waitpid(pid, 0)

    def read_secret():
        with open(secret, "rb") as fh:
            fh.read(1)

    def read_parent_environ():
        with open(f"/proc/{parent}/environ", "rb") as fh:
            fh.read(1)

    def write_outside():
        with open(outside, "wb") as fh:
            fh.write(b"x")

    def symlink_inside():
        os.symlink(secret or "/etc/hostname", str(ctx.job_dir / "cwd" / "tmp" / "selftest-link"))

    def write_inside():
        with open(ctx.job_dir / "cwd" / "tmp" / "selftest-write.txt", "wb") as fh:
            fh.write(b"ok")

    def thread_ok():
        box = {}
        t = threading.Thread(target=lambda: box.setdefault("ok", True))
        t.start()
        t.join(10)
        if not box.get("ok"):
            raise RuntimeError("the thread did not run")

    posix = os.name == "posix"
    check("socket(AF_INET)", "seccomp", errno.EACCES,
          lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM).close())
    if posix:
        check("socket(AF_UNIX)", "seccomp", errno.EACCES,
              lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM).close())
        check("os.fork()", "seccomp", errno.EPERM, fork_once)
        check("os.posix_spawn(/usr/bin/true)", "seccomp", errno.EPERM, spawn_once)
        check("os.kill(parent, 0)", "seccomp", errno.EPERM, lambda: os.kill(parent, 0))
    check("read the planted secret", "landlock", errno.EACCES, read_secret)
    if posix:
        check("read /proc/<parent>/environ", "landlock", errno.EACCES, read_parent_environ)
    check("write outside the job directory", "landlock", errno.EACCES, write_outside)
    if posix:
        check("symlink inside the job directory", "landlock", errno.EACCES, symlink_inside)
    check("write inside the job directory", None, None, write_inside)
    check("a thread", None, None, thread_ok)
    from aces_cad.worker import audit
    audit.install(ctx.state["roots"])
    try:
        socket.socket()
        got, ok = "allowed", False
    except PermissionError as e:
        got = f"PermissionError: {str(e)[:120]}"
        ok = str(e).startswith("aces-cad sandbox:")
    except OSError as e:
        got, ok = f"errno {e.errno} ({_errno_name(e.errno)})", False
    checks.append({"check": "socket.socket() with the audit hook", "expect": "PermissionError aces-cad sandbox:",
                   "got": got, "pass": ok})
    return {"checks": checks}
