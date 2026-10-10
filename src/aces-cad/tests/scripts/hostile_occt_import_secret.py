"""Hostile (test fixture): imports a planted STEP file outside the run through OCCT, whose C++ file I/O bypasses the
Python audit hook; only Landlock stops it (strict mode). The planted part is labelled with a marker that must not leak.
"""
from build123d import import_step

PARAMS = {"secret": {"default": "", "doc": "absolute path of a STEP file outside the run (the test plants it)"}}


def build(p):
    if not p.secret:
        raise ValueError("set the secret parameter to the absolute path of the planted STEP file")
    return import_step(p.secret)
