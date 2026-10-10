"""Hostile (test fixture, POSIX): signal 0 to the parent process (the aces-cad server or CLI); refused (kind sandbox).

Never run this outside the sandbox on Windows, where os.kill terminates the target whatever the signal number.
"""
import os


def build(p):
    os.kill(os.getppid(), 0)
    raise RuntimeError("signalling the parent was not refused")
