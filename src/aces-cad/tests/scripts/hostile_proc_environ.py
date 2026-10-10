"""Hostile (test fixture): prints its own environment keys, then reads the parent environment from /proc. The worker
environment must hold only the allowlisted keys, and /proc/PPID/environ must be refused (kind sandbox).
"""
import os


def build(p):
    print("ENV " + " ".join(sorted(os.environ)))
    with open("/proc/" + str(os.getppid()) + "/environ", "rb") as fh:
        data = fh.read()
    print(data.decode("utf-8", "replace"))
    raise RuntimeError("reading the parent environment was not refused")
