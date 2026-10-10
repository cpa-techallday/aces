"""The PEP 578 audit hook of run jobs (section 8.6 of the build spec): a clear-message layer, not a boundary.

Installed last, after the imports, and only in jobs that run agent code. It refuses network, process, ctypes,
tracing and registry events by name, and file access outside the roots the Landlock rules use: writes, creations,
renames and removals outside the read-write roots, and reads outside every root. Paths are compared as
os.path.realpath (normcase on Windows); integer paths (file descriptors) pass. OCCT's C++ file I/O never reaches the
hook, which is why Landlock is the boundary on Linux; on Windows the hook is the only guard.
"""
from __future__ import annotations

import os
import sys

DENY_PREFIXES = ("socket.", "subprocess.", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.system",
                 "os.startfile", "os.kill", "ctypes.dlopen", "ctypes.dlsym", "pty.spawn", "webbrowser.open",
                 "urllib.Request", "http.client.connect", "ftplib.", "smtplib.", "imaplib.", "poplib.", "nntplib.",
                 "telnetlib.", "sys.addaudithook", "sys.settrace", "sys.setprofile", "winreg.", "msvcrt.")
# event -> the indexes of the path arguments that must lie inside the read-write roots
MUTATIONS = {"os.remove": (0,), "os.rename": (0, 1), "os.replace": (0, 1), "os.rmdir": (0,), "os.mkdir": (0,),
             "os.symlink": (1,), "os.link": (0, 1), "os.chmod": (0,), "os.chown": (0,), "os.truncate": (0,),
             "os.utime": (0,), "shutil.rmtree": (0,), "shutil.copyfile": (1,), "shutil.move": (0, 1)}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


class AuditRefusal(BaseException):
    """The refusal of sys.addaudithook: CPython silently swallows an Exception raised by a hook for that event (the new
    hook is just not added), so it is refused with a BaseException that reaches the script."""


def _norm(path) -> str | None:
    """realpath (normcase on Windows) of a path argument; None for an fd or something that is not a path."""
    if isinstance(path, int) and not isinstance(path, bool):
        return None
    try:
        text = os.fsdecode(path)
    except (TypeError, ValueError):
        return None
    real = os.path.realpath(text)
    return os.path.normcase(real) if os.name == "nt" else real


class _Rules:
    def __init__(self, roots):
        norm = (lambda p: os.path.normcase(os.path.realpath(p))) if os.name == "nt" else os.path.realpath
        self.rw_dirs = tuple(norm(p) for p in roots.rw_dirs)
        self.rw_files = frozenset(norm(p) for p in roots.rw_files)
        self.ro_dirs = tuple(norm(p) for p in roots.ro_dirs)
        self.ro_files = frozenset(norm(p) for p in roots.ro_files)

    @staticmethod
    def _under(path: str, dirs: tuple[str, ...]) -> bool:
        for d in dirs:
            if path == d or path.startswith(d.rstrip(os.sep) + os.sep):
                return True
        return False

    def writable(self, path: str) -> bool:
        return path in self.rw_files or self._under(path, self.rw_dirs)

    def readable(self, path: str) -> bool:
        return self.writable(path) or path in self.ro_files or self._under(path, self.ro_dirs)


def _writing(mode, flags) -> bool:
    if isinstance(mode, str) and any(c in mode for c in "wax+"):
        return True
    return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)


def install(roots) -> None:
    """Add the hook; from here on the process cannot add another hook or a tracer."""
    rules = _Rules(roots)

    def hook(event: str, args: tuple) -> None:
        if event.startswith(DENY_PREFIXES):
            if event == "sys.addaudithook":
                raise AuditRefusal(f"aces-cad sandbox: {event} is not available in a CAD run")
            raise PermissionError(f"aces-cad sandbox: {event} is not available in a CAD run")
        if event == "open":
            path = args[0] if args else None
            if path is None:
                return
            real = _norm(path)
            if real is None:
                return
            mode = args[1] if len(args) > 1 else None
            flags = args[2] if len(args) > 2 else None
            if _writing(mode, flags):
                if not rules.writable(real):
                    raise PermissionError(f"aces-cad sandbox: writing {real} (outside the run directory) is refused")
            elif not rules.readable(real):
                raise PermissionError(f"aces-cad sandbox: reading {real} (outside the allowed roots) is refused")
            return
        positions = MUTATIONS.get(event)
        if positions:
            for i in positions:
                if i < len(args):
                    real = _norm(args[i])
                    if real is not None and not rules.writable(real):
                        raise PermissionError(f"aces-cad sandbox: {event} on {real} (outside the run directory) is "
                                              f"refused")

    sys.addaudithook(hook)
