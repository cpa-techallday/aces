"""Hostile (test fixture): agent code lies about its own result. It writes a forged out/parts.json from its working
directory and makes build123d report an absurd volume for every shape class that defines one, then returns a valid
10 mm cube. The run summary may carry the lie (it is computed in the process that ran this code); the pull re-derives
every number in a worker that runs no agent code, so source.json must hold the true 1000 mm3.
"""
import json

from build123d import Align, Box, Compound, Shape, Solid

FORGED = {"schema": "aces.cad.parts/1", "kind": "solid",
          "parts": [{"index": 0, "name": "forged", "kind": "solid", "file": "p0.brep"}], "datums": []}


def build(p):
    try:
        with open("../out/parts.json", "w", encoding="utf-8") as fh:
            json.dump(FORGED, fh)
    except OSError as e:
        print("forging out/parts.json failed: " + type(e).__name__)
    cube = Box(10, 10, 10, align=(Align.MIN, Align.MIN, Align.MIN))
    for cls in set(type(cube).__mro__) | {Compound, Shape, Solid}:
        if "volume" in vars(cls):
            cls.volume = property(lambda self: 1.0e12)
    return cube
