"""Hostile (test fixture): os.fork (POSIX only); refused (kind sandbox). A child that did start exits at once."""
import os


def build(p):
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    raise RuntimeError("fork was not refused")
