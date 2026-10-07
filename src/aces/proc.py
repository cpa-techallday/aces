"""Running external tools: argument lists only, a minimal environment, and timeouts that stop the whole tree.

Audit findings C1 (shell injection), H1 (scripts inherited the server's environment) and I3/C5 (a timeout
killed only the shell, so the real work carried on). Every subprocess in ACES goes through :func:`run`.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections.abc import Mapping, Sequence

# Variables a child process needs to start and find its tools; nothing else is inherited by default.
_KEEP = ("PATH", "SYSTEMROOT", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR", "HOME",
         "USERPROFILE", "LOCALAPPDATA", "APPDATA", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "PYTHONIOENCODING",
         "VIRTUAL_ENV")


def minimal_env(extra: Mapping[str, str] | None = None, *, keep: Sequence[str] = ()) -> dict[str, str]:
    """The base variables in ``_KEEP`` plus ``keep`` names from this process, plus ``extra``.

    Secrets (tokens, keys, cloud credentials) are not passed unless a caller names them in ``keep``.
    """
    env = {k: os.environ[k] for k in (*_KEEP, *keep) if k in os.environ}
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env


def _kill_tree(p: subprocess.Popen) -> None:
    if p.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, check=False)
        try:
            p.kill()
        except OSError:
            pass
    else:
        try:
            os.killpg(p.pid, signal.SIGKILL)  # the child leads its own session (start_new_session)
        except (ProcessLookupError, PermissionError):
            try:
                p.kill()
            except OSError:
                pass


def run(cmd: Sequence[str | os.PathLike], *, cwd: str | os.PathLike | None = None,
        env: Mapping[str, str] | None = None, timeout: float | None = None, text: bool = True,
        input: str | bytes | None = None, check: bool = False) -> subprocess.CompletedProcess:
    """Run ``cmd`` (a list, never a shell string) and capture its output.

    On timeout the whole process group is killed, then :class:`subprocess.TimeoutExpired` is raised with
    whatever output was produced.
    """
    if isinstance(cmd, (str, bytes)):
        raise TypeError("pass an argument list, not a shell string")
    args = [os.fspath(c) for c in cmd]
    kw: dict = {}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    p = subprocess.Popen(args, cwd=cwd, env=dict(env) if env is not None else None, text=text,
                         stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
    try:
        out, err = p.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        out, err = p.communicate()
        raise subprocess.TimeoutExpired(args, timeout, output=out, stderr=err) from None
    except BaseException:
        _kill_tree(p)
        p.wait()
        raise
    cp = subprocess.CompletedProcess(args, p.returncode, out, err)
    if check:
        cp.check_returncode()
    return cp


PYTHON = sys.executable
