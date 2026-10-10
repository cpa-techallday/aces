"""Hostile (test fixture): writes a file at an absolute path outside the run directory; refused (kind sandbox) and the
file must not exist afterwards.
"""
PARAMS = {"target": {"default": "", "doc": "absolute path outside the run directory (the test sets it)"}}


def build(p):
    if not p.target:
        raise ValueError("set the target parameter to an absolute path outside the run directory")
    with open(p.target, "w", encoding="utf-8") as fh:
        fh.write("aces-cad hostile fixture: this file must not exist\n")
    raise RuntimeError("the write was not refused")
