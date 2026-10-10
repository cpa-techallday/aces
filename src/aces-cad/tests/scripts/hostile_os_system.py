"""Hostile (test fixture): runs a shell command with os.system; refused (kind sandbox)."""
import os


def build(p):
    os.system("echo aces-cad-hostile-fixture")
    raise RuntimeError("os.system was not refused")
