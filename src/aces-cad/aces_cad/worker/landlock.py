"""Landlock for aces-cad workers (section 8.5 of the build spec).

The ABI comes from landlock_create_ruleset(NULL, 0, LANDLOCK_CREATE_RULESET_VERSION); <= 0 means unavailable. The
handled filesystem rights are bits 0-12 (ABI 1), plus REFER (ABI >= 2), TRUNCATE (>= 3) and IOCTL_DEV (>= 5). With
ABI >= 4 TCP bind and connect are handled with no rule (all TCP denied, a second network layer); with ABI >= 6
abstract unix sockets and signals are scoped. Rules (each masked by the handled set):

    RO_DIR  = EXECUTE | READ_FILE | READ_DIR
    RO_FILE = READ_FILE | IOCTL_DEV
    RW_DIR  = READ_FILE | WRITE_FILE | READ_DIR | REMOVE_DIR | REMOVE_FILE | MAKE_DIR | MAKE_REG | REFER | TRUNCATE
    RW_FILE = READ_FILE | WRITE_FILE | TRUNCATE | IOCTL_DEV

No EXECUTE, MAKE_SYM, MAKE_CHAR, MAKE_BLOCK, MAKE_FIFO or MAKE_SOCK on read-write directories: no symlinks, devices,
FIFOs or sockets can be created. Attributes and rules are packed with struct (no ctypes.Structure). Landlock restricts
the calling thread and the threads it creates later; the worker installs it before any import that starts threads.
"""
from __future__ import annotations

import os
import struct

SYS_CREATE_RULESET, SYS_ADD_RULE, SYS_RESTRICT_SELF = 444, 445, 446
LANDLOCK_CREATE_RULESET_VERSION = 1
LANDLOCK_RULE_PATH_BENEATH = 1
PR_SET_NO_NEW_PRIVS = 38
FS = {"EXECUTE": 1 << 0, "WRITE_FILE": 1 << 1, "READ_FILE": 1 << 2, "READ_DIR": 1 << 3, "REMOVE_DIR": 1 << 4,
      "REMOVE_FILE": 1 << 5, "MAKE_CHAR": 1 << 6, "MAKE_DIR": 1 << 7, "MAKE_REG": 1 << 8, "MAKE_SOCK": 1 << 9,
      "MAKE_FIFO": 1 << 10, "MAKE_BLOCK": 1 << 11, "MAKE_SYM": 1 << 12, "REFER": 1 << 13, "TRUNCATE": 1 << 14,
      "IOCTL_DEV": 1 << 15}
NET_BIND_TCP, NET_CONNECT_TCP = 1 << 0, 1 << 1
SCOPE_ABSTRACT_UNIX_SOCKET, SCOPE_SIGNAL = 1 << 0, 1 << 1
RO_DIR = FS["EXECUTE"] | FS["READ_FILE"] | FS["READ_DIR"]
RO_FILE = FS["READ_FILE"] | FS["IOCTL_DEV"]
RW_DIR = (FS["READ_FILE"] | FS["WRITE_FILE"] | FS["READ_DIR"] | FS["REMOVE_DIR"] | FS["REMOVE_FILE"] | FS["MAKE_DIR"]
          | FS["MAKE_REG"] | FS["REFER"] | FS["TRUNCATE"])
RW_FILE = FS["READ_FILE"] | FS["WRITE_FILE"] | FS["TRUNCATE"] | FS["IOCTL_DEV"]


def handled_fs(abi: int) -> int:
    bits = (1 << 13) - 1
    if abi >= 2:
        bits |= FS["REFER"]
    if abi >= 3:
        bits |= FS["TRUNCATE"]
    if abi >= 5:
        bits |= FS["IOCTL_DEV"]
    return bits


def ruleset_attr(abi: int) -> bytes:
    """landlock_ruleset_attr for this ABI: handled_access_fs, then handled_access_net (>= 4), then scoped (>= 6)."""
    fs = handled_fs(abi)
    if abi >= 6:
        return struct.pack("<QQQ", fs, NET_BIND_TCP | NET_CONNECT_TCP, SCOPE_ABSTRACT_UNIX_SOCKET | SCOPE_SIGNAL)
    if abi >= 4:
        return struct.pack("<QQ", fs, NET_BIND_TCP | NET_CONNECT_TCP)
    return struct.pack("<Q", fs)


def _libc():
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return ctypes, libc


def abi() -> int:
    """The Landlock ABI version, or -errno when Landlock is unavailable."""
    try:
        ctypes, libc = _libc()
    except (OSError, AttributeError):
        return -1
    r = libc.syscall(SYS_CREATE_RULESET, None, 0, LANDLOCK_CREATE_RULESET_VERSION)
    return int(r) if r > 0 else -ctypes.get_errno()


def restrict(roots) -> dict:
    """Restrict this thread (and the threads it creates later) to the roots: {"active", "abi", "tcp_rules",
    "scoped", "added", "skipped_roots", "detail"?}."""
    skipped = list(getattr(roots, "skipped", []))
    base = {"active": False, "abi": None, "tcp_rules": False, "scoped": False, "added": [], "skipped_roots": skipped}
    try:
        ctypes, libc = _libc()
    except (OSError, AttributeError) as e:
        return dict(base, detail=f"libc is not available ({type(e).__name__})")
    version = abi()
    if version <= 0:
        import errno as errno_mod
        code = -version
        return dict(base, detail=f"Landlock is unavailable (errno {code} {errno_mod.errorcode.get(code, '')}); the "
                                 f"kernel needs CONFIG_SECURITY_LANDLOCK and landlock in the lsm= list")
    fs = handled_fs(version)
    attr = ruleset_attr(version)
    abuf = ctypes.create_string_buffer(attr, len(attr))
    rfd = libc.syscall(SYS_CREATE_RULESET, abuf, len(attr), 0)
    if rfd < 0:
        return dict(base, abi=version, detail=f"landlock_create_ruleset failed (errno {ctypes.get_errno()})")
    rfd = int(rfd)
    added: list[str] = []
    try:
        for paths, access in ((roots.ro_dirs, RO_DIR), (roots.ro_files, RO_FILE), (roots.rw_dirs, RW_DIR),
                              (roots.rw_files, RW_FILE)):
            for p in paths:
                try:
                    pfd = os.open(p, os.O_PATH | os.O_CLOEXEC)
                except OSError as e:
                    skipped.append(f"{p}: {e.strerror}")
                    continue
                try:
                    rule = struct.pack("<Qi", access & fs, pfd)
                    rbuf = ctypes.create_string_buffer(rule, len(rule))
                    r = libc.syscall(SYS_ADD_RULE, rfd, LANDLOCK_RULE_PATH_BENEATH, rbuf, 0)
                    err = ctypes.get_errno()
                finally:
                    os.close(pfd)
                if r == 0:
                    added.append(p)
                else:
                    skipped.append(f"{p}: landlock_add_rule errno {err}")
        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            return dict(base, abi=version, added=added,
                        detail=f"PR_SET_NO_NEW_PRIVS failed (errno {ctypes.get_errno()})")
        r = libc.syscall(SYS_RESTRICT_SELF, rfd, 0)
        if r != 0:
            return dict(base, abi=version, added=added,
                        detail=f"landlock_restrict_self failed (errno {ctypes.get_errno()})")
    finally:
        os.close(rfd)
    return {"active": True, "abi": version, "tcp_rules": version >= 4, "scoped": version >= 6, "added": added,
            "skipped_roots": skipped, "detail": f"ABI {version}, {len(added)} rules"}
