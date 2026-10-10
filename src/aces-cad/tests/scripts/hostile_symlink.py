"""Hostile (test fixture): creates a symlink inside the job directory pointing outside it. Landlock grants no MAKE_SYM,
so strict mode refuses it (EACCES on a target outside the run, kind sandbox).
"""
import os

PARAMS = {"target": {"default": "/etc/hostname", "doc": "what the link points at (outside the run)"}}


def build(p):
    os.symlink(p.target, "aces-cad-link")
    raise RuntimeError("creating a symlink was not refused")
