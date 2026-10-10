"""The seccomp-bpf filter of every aces-cad worker (x86_64 only; section 8.4 of the build spec).

Installed with prctl(PR_SET_NO_NEW_PRIVS) and seccomp(SECCOMP_SET_MODE_FILTER, SECCOMP_FILTER_FLAG_TSYNC); a TSYNC
failure means the filter is not active (no single-thread fallback). The program, in this order (args offset 16 is
args[0] low 32 bits, 24 is args[1]):

1. arch != AUDIT_ARCH_X86_64 -> EPERM; nr >= 0x40000000 (x32) -> EPERM
2. socket -> EACCES; clone3 -> ENOSYS (glibc falls back to clone for threads)
3. clone: allowed only with CLONE_THREAD in args[0], else EPERM
4. kill, tgkill, rt_sigqueueinfo, rt_tgsigqueueinfo: args[0] must be the worker's own pid, else EPERM
5. prlimit64, sched_setaffinity: args[0] in {own pid, 0}, else EPERM
6. setpriority: which == PRIO_PROCESS and who in {0, own pid}, else EPERM
7. EPERM: fork vfork execve execveat ptrace unshare setns add_key request_key keyctl perf_event_open
   process_vm_readv process_vm_writev bpf io_uring_setup io_uring_enter io_uring_register tkill pidfd_open
   pidfd_send_signal pidfd_getfd kcmp process_madvise userfaultfd ioprio_set
8. ALLOW

10 + 5 + 20 + 12 + 8 + 50 + 1 = 106 instructions. Each instruction is struct.pack("<HBBI", code, jt, jf, k); the
sock_fprog is struct.pack("<HxxxxxxQ", n, address). No ctypes.Structure.
"""
from __future__ import annotations

import errno
import os
import platform
import struct

AUDIT_ARCH_X86_64 = 0xC000003E
X32_SYSCALL_BIT = 0x40000000
CLONE_THREAD = 0x00010000
BPF_LD_W_ABS = 0x20
BPF_JEQ_K = 0x15
BPF_JGE_K = 0x35
BPF_JSET_K = 0x45
BPF_RET_K = 0x06
RET_ALLOW = 0x7FFF0000
RET_ERRNO = 0x00050000
OFF_NR, OFF_ARCH, OFF_ARG0, OFF_ARG1 = 0, 4, 16, 24
PR_SET_NO_NEW_PRIVS = 38
SYS_SECCOMP = 317
SECCOMP_SET_MODE_FILTER = 1
SECCOMP_FILTER_FLAG_TSYNC = 1
NR = {"socket": 41, "clone": 56, "fork": 57, "vfork": 58, "execve": 59, "kill": 62, "ptrace": 101,
      "rt_sigqueueinfo": 129, "setpriority": 141, "tkill": 200, "sched_setaffinity": 203, "tgkill": 234,
      "add_key": 248, "request_key": 249, "keyctl": 250, "ioprio_set": 251, "unshare": 272,
      "rt_tgsigqueueinfo": 297, "perf_event_open": 298, "prlimit64": 302, "setns": 308, "process_vm_readv": 310,
      "process_vm_writev": 311, "kcmp": 312, "bpf": 321, "execveat": 322, "userfaultfd": 323,
      "pidfd_send_signal": 424, "io_uring_setup": 425, "io_uring_enter": 426, "io_uring_register": 427,
      "pidfd_open": 434, "clone3": 435, "pidfd_getfd": 438, "process_madvise": 440}
OWN_PID_ONLY = ("kill", "tgkill", "rt_sigqueueinfo", "rt_tgsigqueueinfo")
OWN_PID_OR_ZERO = ("prlimit64", "sched_setaffinity")
DENY_EPERM = ("fork", "vfork", "execve", "execveat", "ptrace", "unshare", "setns", "add_key", "request_key", "keyctl",
              "perf_event_open", "process_vm_readv", "process_vm_writev", "bpf", "io_uring_setup", "io_uring_enter",
              "io_uring_register", "tkill", "pidfd_open", "pidfd_send_signal", "pidfd_getfd", "kcmp",
              "process_madvise", "userfaultfd", "ioprio_set")
INSTRUCTIONS = 106


def _ins(code: int, jt: int, jf: int, k: int) -> bytes:
    return struct.pack("<HBBI", code, jt, jf, k & 0xFFFFFFFF)


def program(pid: int) -> bytes:
    """The BPF program for a worker with this pid: 106 instructions of 8 bytes."""
    eperm = RET_ERRNO | errno.EPERM
    ins = [
        # 1. architecture, then the x32 bit
        _ins(BPF_LD_W_ABS, 0, 0, OFF_ARCH), _ins(BPF_JEQ_K, 1, 0, AUDIT_ARCH_X86_64), _ins(BPF_RET_K, 0, 0, eperm),
        _ins(BPF_LD_W_ABS, 0, 0, OFF_NR), _ins(BPF_JGE_K, 0, 1, X32_SYSCALL_BIT), _ins(BPF_RET_K, 0, 0, eperm),
        # 2. socket, clone3
        _ins(BPF_JEQ_K, 0, 1, NR["socket"]), _ins(BPF_RET_K, 0, 0, RET_ERRNO | errno.EACCES),
        _ins(BPF_JEQ_K, 0, 1, NR["clone3"]), _ins(BPF_RET_K, 0, 0, RET_ERRNO | errno.ENOSYS),
        # 3. clone: threads only
        _ins(BPF_JEQ_K, 0, 4, NR["clone"]), _ins(BPF_LD_W_ABS, 0, 0, OFF_ARG0),
        _ins(BPF_JSET_K, 1, 0, CLONE_THREAD), _ins(BPF_RET_K, 0, 0, eperm), _ins(BPF_RET_K, 0, 0, RET_ALLOW),
    ]
    for name in OWN_PID_ONLY:  # 4. args[0] == own pid
        ins += [_ins(BPF_JEQ_K, 0, 4, NR[name]), _ins(BPF_LD_W_ABS, 0, 0, OFF_ARG0), _ins(BPF_JEQ_K, 1, 0, pid),
                _ins(BPF_RET_K, 0, 0, eperm), _ins(BPF_RET_K, 0, 0, RET_ALLOW)]
    for name in OWN_PID_OR_ZERO:  # 5. args[0] in {own pid, 0}
        ins += [_ins(BPF_JEQ_K, 0, 5, NR[name]), _ins(BPF_LD_W_ABS, 0, 0, OFF_ARG0), _ins(BPF_JEQ_K, 2, 0, pid),
                _ins(BPF_JEQ_K, 1, 0, 0), _ins(BPF_RET_K, 0, 0, eperm), _ins(BPF_RET_K, 0, 0, RET_ALLOW)]
    # 6. setpriority(which, who, prio): which == PRIO_PROCESS (0) and who in {0, own pid}
    ins += [_ins(BPF_JEQ_K, 0, 7, NR["setpriority"]), _ins(BPF_LD_W_ABS, 0, 0, OFF_ARG0),
            _ins(BPF_JEQ_K, 0, 3, 0), _ins(BPF_LD_W_ABS, 0, 0, OFF_ARG1), _ins(BPF_JEQ_K, 2, 0, 0),
            _ins(BPF_JEQ_K, 1, 0, pid), _ins(BPF_RET_K, 0, 0, eperm), _ins(BPF_RET_K, 0, 0, RET_ALLOW)]
    for name in DENY_EPERM:  # 7.
        ins += [_ins(BPF_JEQ_K, 0, 1, NR[name]), _ins(BPF_RET_K, 0, 0, eperm)]
    ins.append(_ins(BPF_RET_K, 0, 0, RET_ALLOW))  # 8.
    prog = b"".join(ins)
    if len(prog) != INSTRUCTIONS * 8:
        raise AssertionError(f"the seccomp program has {len(prog) // 8} instructions, not {INSTRUCTIONS}")
    return prog


def decode(prog: bytes) -> list[tuple[int, int, int, int]]:
    """(code, jt, jf, k) per instruction."""
    return [struct.unpack_from("<HBBI", prog, i) for i in range(0, len(prog), 8)]


def install(pid: int | None = None) -> dict:
    """Install the filter on every thread (TSYNC): {"active", "detail"}."""
    if platform.machine().lower() not in ("x86_64", "amd64"):
        return {"active": False, "detail": f"the seccomp filter is written for x86_64, not {platform.machine()}"}
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
    except (OSError, AttributeError) as e:
        return {"active": False, "detail": f"libc is not available ({type(e).__name__})"}
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        return {"active": False, "detail": f"PR_SET_NO_NEW_PRIVS failed (errno {ctypes.get_errno()})"}
    prog = program(os.getpid() if pid is None else pid)
    buf = ctypes.create_string_buffer(prog, len(prog))
    fprog = ctypes.create_string_buffer(struct.pack("<HxxxxxxQ", len(prog) // 8, ctypes.addressof(buf)), 16)
    r = libc.syscall(SYS_SECCOMP, SECCOMP_SET_MODE_FILTER, SECCOMP_FILTER_FLAG_TSYNC, fprog)
    if r != 0:
        err = ctypes.get_errno()
        return {"active": False, "detail": f"seccomp(SET_MODE_FILTER, TSYNC) returned {r} (errno {err} "
                                           f"{errno.errorcode.get(err, '')}); no single-thread fallback"}
    return {"active": True, "detail": f"{len(prog) // 8} BPF instructions, TSYNC"}
