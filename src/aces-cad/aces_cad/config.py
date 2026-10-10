"""Configuration: environment variables only (section 6 of the build spec).

| Variable             | Default                                | Range and meaning                                  |
|----------------------|----------------------------------------|----------------------------------------------------|
| ACES_WORK            | ~/Work                                 | project root; set but empty counts as unset        |
| ACES_CAD_PROJECT     | unset                                  | binds server and CLI to one project                |
| ACES_CAD_PYTHON      | /opt/aces/engineering/venv/bin/python  | the worker interpreter                             |
| ACES_CAD_WORKER_CMD  | unset                                  | tests only: a JSON array replacing the worker argv |
| ACES_CAD_SANDBOX     | strict                                 | strict or permissive                               |
| ACES_CAD_MEMORY_MB   | 4096                                   | 2048-65536, RLIMIT_AS per worker                   |
| ACES_CAD_THREADS     | 2                                      | 1-64, BLAS/OpenMP and the OCCT pool                |
| ACES_CAD_TIMEOUT_S   | 120                                    | 1-600, default timeout of run jobs                 |
| ACES_CAD_NICE        | 10                                     | 0-19                                               |
| ACES_CAD_MAX_FILE_MB | 1024                                   | 16-65536, RLIMIT_FSIZE                             |
| ACES_CAD_MAX_RUN_MB  | 2048                                   | 64-65536, job dir cap and promoted total           |
| ACES_CAD_MAX_LOG_MB  | 64                                     | 1-1024, cap per stdout/stderr file                 |
| ACES_CAD_CACHE       | $XDG_CACHE_HOME/aces-cad, ~/.cache/... | caches of data-only jobs; doctor scratch           |
| ACES_CAD_KEEP_JOBS   | 0                                      | 1 keeps job dirs (debugging)                       |
| ACES_CAD_ACES_CMD    | aces on PATH                           | JSON array of strings or one path; [] means none   |
| ACES_USER_CONFIG     | ~/.config/aces                         | cad-materials.json                                 |

Invalid values raise ConfigError (kind config). Every value is read when it is used, so tests and the CLI can pass an
explicit environment mapping.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .errors import ConfigError

DEFAULT_PYTHON = "/opt/aces/engineering/venv/bin/python"
MODES = ("strict", "permissive")
TIMEOUT_MAX = 600
SCRIPT_MAX_CHARS = 200_000
# default job timeouts per worker op (run uses ACES_CAD_TIMEOUT_S)
JOB_TIMEOUTS = {"env": 30, "selftest": 60, "inspect": 180, "render": 120, "drawing": 180, "export": 300}
_INTS = {
    # name: (variable, default, lo, hi)
    "memory_mb": ("ACES_CAD_MEMORY_MB", 4096, 2048, 65536),
    "threads": ("ACES_CAD_THREADS", 2, 1, 64),
    "timeout_s": ("ACES_CAD_TIMEOUT_S", 120, 1, TIMEOUT_MAX),
    "nice": ("ACES_CAD_NICE", 10, 0, 19),
    "max_file_mb": ("ACES_CAD_MAX_FILE_MB", 1024, 16, 65536),
    "max_run_mb": ("ACES_CAD_MAX_RUN_MB", 2048, 64, 65536),
    "max_log_mb": ("ACES_CAD_MAX_LOG_MB", 64, 1, 1024),
}


def _environ(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _get(env: Mapping[str, str] | None, name: str) -> str | None:
    """An environment value, stripped; set but empty counts as unset."""
    value = _environ(env).get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _home(env: Mapping[str, str] | None = None) -> Path:
    """The user's home (os.path.expanduser, as aces-onshape does: HOME on POSIX, USERPROFILE on Windows)."""
    return Path(os.path.expanduser("~"))


def work_dir(env: Mapping[str, str] | None = None) -> Path:
    """The ACES project root: ACES_WORK, else ~/Work."""
    value = _get(env, "ACES_WORK")
    return Path(value) if value else _home(env) / "Work"


def user_config_dir(env: Mapping[str, str] | None = None) -> Path:
    value = _get(env, "ACES_USER_CONFIG")
    return Path(value) if value else _home(env) / ".config" / "aces"


def cache_dir(env: Mapping[str, str] | None = None) -> Path:
    """ACES_CAD_CACHE, else $XDG_CACHE_HOME/aces-cad, else ~/.cache/aces-cad."""
    value = _get(env, "ACES_CAD_CACHE")
    if value:
        return Path(value)
    xdg = _get(env, "XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "aces-cad"
    return _home(env) / ".cache" / "aces-cad"


def lib_dir() -> Path:
    """The directory that holds the aces_cad package (/opt/aces/cad/lib when installed)."""
    return Path(__file__).resolve().parent.parent


def worker_python(env: Mapping[str, str] | None = None) -> str:
    """The worker interpreter as configured (ACES_CAD_PYTHON, else the aces-engineering venv); not checked here."""
    return _get(env, "ACES_CAD_PYTHON") or DEFAULT_PYTHON


def _worker_override(env: Mapping[str, str] | None) -> list[str] | None:
    raw = _get(env, "ACES_CAD_WORKER_CMD")
    if raw is None:
        return None
    try:
        argv = json.loads(raw)
    except ValueError:
        argv = None
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
        raise ConfigError("ACES_CAD_WORKER_CMD must be a non-empty JSON array of strings (tests only)")
    return argv


def worker_cmd(env: Mapping[str, str] | None = None) -> list[str]:
    """The worker argv: ACES_CAD_WORKER_CMD (tests), else the venv python running aces_cad/worker/main.py."""
    override = _worker_override(env)
    if override is not None:
        return override
    python = worker_python(env)
    if not os.path.isfile(python):
        explicit = _get(env, "ACES_CAD_PYTHON")
        where = "ACES_CAD_PYTHON" if explicit else "the aces-engineering venv"
        raise ConfigError(f"the worker interpreter {python} ({where}) does not exist: install aces-engineering "
                          f"(sudo pacman -S aces-engineering) or set ACES_CAD_PYTHON to a python with build123d")
    return [python, "-I", "-B", "-X", "utf8", "-X", "faulthandler",
            str(lib_dir() / "aces_cad" / "worker" / "main.py")]


def _int(env: Mapping[str, str] | None, name: str, default: int, lo: int, hi: int) -> int:
    raw = _get(env, name)
    if raw is None:
        return default
    try:
        value = int(raw, 10)
    except ValueError:
        raise ConfigError(f"{name} must be an integer from {lo} to {hi}; got {raw[:40]!r}") from None
    if not lo <= value <= hi:
        raise ConfigError(f"{name} must be an integer from {lo} to {hi}; got {value}")
    return value


def aces_command(env: Mapping[str, str] | None = None) -> list[str] | None:
    """The aces CLI as an argv prefix (ACES_CAD_ACES_CMD, else `aces` on PATH), or None when there is none."""
    raw = _get(env, "ACES_CAD_ACES_CMD")
    if raw is None:
        found = shutil.which("aces")
        return [found] if found else None
    bad = ConfigError('ACES_CAD_ACES_CMD must be a JSON array of strings (for example ["/usr/bin/aces"]) or a single '
                      'path; [] means no aces CLI')
    if raw.startswith("["):
        try:
            argv = json.loads(raw)
        except ValueError:
            raise bad from None
        if not isinstance(argv, list) or not all(isinstance(a, str) and a for a in argv):
            raise bad
        return argv or None
    if raw.startswith(("{", '"')):
        raise bad
    return [raw]


@dataclass(frozen=True)
class Settings:
    """Every section-6 value, validated."""

    work: Path
    project: str | None
    python: str
    worker_cmd_override: tuple[str, ...] | None
    sandbox: str
    memory_mb: int
    threads: int
    timeout_s: int
    nice: int
    max_file_mb: int
    max_run_mb: int
    max_log_mb: int
    cache: Path
    keep_jobs: bool
    aces_cmd: tuple[str, ...] | None
    user_config: Path

    def job_timeout(self, op: str) -> int:
        """The default timeout of a worker op (run: ACES_CAD_TIMEOUT_S)."""
        if op == "run":
            return self.timeout_s
        return JOB_TIMEOUTS.get(op, self.timeout_s)


def sandbox_mode(env: Mapping[str, str] | None = None) -> str:
    mode = (_get(env, "ACES_CAD_SANDBOX") or "strict").lower()
    if mode not in MODES:
        raise ConfigError(f"ACES_CAD_SANDBOX must be strict or permissive; got {mode[:40]!r}")
    return mode


def keep_jobs(env: Mapping[str, str] | None = None) -> bool:
    raw = _get(env, "ACES_CAD_KEEP_JOBS") or "0"
    if raw not in ("0", "1"):
        raise ConfigError(f"ACES_CAD_KEEP_JOBS must be 0 or 1; got {raw[:40]!r}")
    return raw == "1"


def settings(env: Mapping[str, str] | None = None) -> Settings:
    """Every section-6 value from the environment, validated (ConfigError on a bad value)."""
    ints = {key: _int(env, var, default, lo, hi) for key, (var, default, lo, hi) in _INTS.items()}
    override = _worker_override(env)
    aces = aces_command(env)
    project = _get(env, "ACES_CAD_PROJECT")
    return Settings(work=work_dir(env), project=project, python=worker_python(env),
                    worker_cmd_override=tuple(override) if override else None, sandbox=sandbox_mode(env),
                    cache=cache_dir(env), keep_jobs=keep_jobs(env), aces_cmd=tuple(aces) if aces else None,
                    user_config=user_config_dir(env), **ints)
