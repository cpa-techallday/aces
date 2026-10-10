"""Hostile (test fixture): writes a STEP file outside the run through OCCT (C++ file I/O, not seen by the audit hook).

Strict mode: Landlock makes the write fail ("Failed to write STEP file") and the file is absent. Permissive mode has no
kernel layer that can stop it.
"""
from build123d import Box, export_step

PARAMS = {"target": {"default": "", "doc": "absolute path outside the run directory (the test sets it)"}}


def build(p):
    if not p.target:
        raise ValueError("set the target parameter to an absolute path outside the run directory")
    export_step(Box(1, 1, 1), p.target)
    return Box(1, 1, 1)
