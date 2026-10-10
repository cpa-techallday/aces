"""Reads a project file given as an input (test fixture): INPUTS["vendor"] is the absolute path of the copied STEP."""
from build123d import import_step


def build(p):
    return import_step(INPUTS["vendor"])  # noqa: F821 (aces-cad defines INPUTS in the script namespace)
