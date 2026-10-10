"""Hostile (test fixture): starts a child process with subprocess; refused (kind sandbox)."""
import subprocess
import sys


def build(p):
    subprocess.run([sys.executable, "-c", "print(1)"], check=True)
    raise RuntimeError("starting a process was not refused")
