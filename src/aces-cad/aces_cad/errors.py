"""The errors of aces-cad (section 17 of the build spec).

Every error derives from CadError and carries `kind` (what the MCP server reports in an isError result and what the
CLI prints) and `exit_code` (what the aces-cad CLI exits with), so both front ends map a failure the same way:

    0 ok; 1 a failed run or operation (script, kernel, result, crash, integrity, internal); 2 usage, config, project,
    not_found, params; 3 sandbox_unavailable; 4 timeout, memory, disk; 5 sandbox; 130 cancelled or interrupted.

`str(e)` is a complete sentence. `extra` holds the structured details (run_id, line, offset, frames, hint, the
sandbox report, ...) that `to_dict()` merges into the error object; keys that do not apply are absent.
"""
from __future__ import annotations

KINDS_EXIT = {
    "error": 1, "script": 1, "kernel": 1, "result": 1, "crash": 1, "integrity": 1, "internal": 1,
    "usage": 2, "config": 2, "project": 2, "not_found": 2, "params": 2,
    "sandbox_unavailable": 3,
    "timeout": 4, "memory": 4, "disk": 4,
    "sandbox": 5,
    "cancelled": 130,
}
RUN_KINDS = ("script", "kernel", "result", "crash", "integrity")
LIMIT_KINDS = ("timeout", "memory", "disk")


class CadError(Exception):
    """Base of every aces-cad error; `kind` may be narrowed per instance (RunFailed, LimitError)."""

    exit_code = 1
    kind = "error"

    def __init__(self, message: str, *, kind: str | None = None, **extra):
        super().__init__(message)
        if kind is not None:
            self.kind = kind
        self.extra = {k: v for k, v in extra.items() if v is not None}

    @property
    def message(self) -> str:
        return str(self)

    def to_dict(self) -> dict:
        """The error as the MCP server's structuredContent shows it: {"kind","message","exit_code",**extra}."""
        out = {"kind": self.kind, "message": str(self), "exit_code": self.exit_code}
        for key, value in self.extra.items():
            if key not in out:
                out[key] = value
        return out


class UsageError(CadError, ValueError):
    """Bad arguments: names, paths, values, combinations; a request that cannot be run."""

    exit_code = 2
    kind = "usage"


class ConfigError(UsageError):
    """A bad ACES_CAD_* setting, or a worker interpreter that is not installed."""

    kind = "config"


class ProjectError(UsageError):
    """No project, a bound-project mismatch, a missing project, a class that is not a policy class."""

    kind = "project"


class NotFoundError(UsageError):
    """A run, part, file or reference that does not exist."""

    kind = "not_found"


class ParamsError(UsageError):
    """PARAMS that cannot be read without running the script, or overrides that do not fit them."""

    kind = "params"


class RunFailed(CadError):
    """A failed run or job: kind script, kernel, result, crash or integrity."""

    exit_code = 1
    kind = "crash"


class ScriptError(RunFailed):
    """The script does not parse, or its entry point is missing (no worker is spawned)."""

    kind = "script"


class IntegrityError(RunFailed):
    """A file that is not what its record says (sha256, type, link count, size, schema)."""

    kind = "integrity"


class InternalError(CadError):
    """A bug in aces-cad itself."""

    exit_code = 1
    kind = "internal"


class LimitError(CadError):
    """A limit was hit: kind timeout, memory or disk."""

    exit_code = 4
    kind = "timeout"


class SandboxRefused(CadError):
    """The sandbox refused an operation of the script (network, processes, files outside the job directory)."""

    exit_code = 5
    kind = "sandbox"


class SandboxUnavailable(CadError):
    """Strict mode cannot run here: seccomp or Landlock is missing."""

    exit_code = 3
    kind = "sandbox_unavailable"


class Cancelled(CadError):
    """The call was cancelled (MCP notifications/cancelled, Ctrl-C) and its job was killed."""

    exit_code = 130
    kind = "cancelled"


_BY_KIND = {
    "usage": UsageError, "config": ConfigError, "project": ProjectError, "not_found": NotFoundError,
    "params": ParamsError, "script": RunFailed, "kernel": RunFailed, "result": RunFailed, "crash": RunFailed,
    "integrity": RunFailed, "internal": InternalError, "timeout": LimitError, "memory": LimitError,
    "disk": LimitError, "sandbox": SandboxRefused, "sandbox_unavailable": SandboxUnavailable,
    "cancelled": Cancelled,
}


def exit_code_for(kind: str) -> int:
    """The CLI exit code of an error kind (1 for an unknown kind)."""
    return KINDS_EXIT.get(kind, 1)


def from_kind(kind: str, message: str, **extra) -> CadError:
    """The exception for an error kind reported by a worker (an unknown kind becomes crash)."""
    cls = _BY_KIND.get(kind)
    if cls is None:
        return RunFailed(f"the worker reported an unknown error kind {kind!r}: {message}", kind="crash", **extra)
    if cls in (RunFailed, LimitError):
        return cls(message, kind=kind, **extra)
    return cls(message, **extra)
