"""The ACES plate through the global cad skill entry (test fixture): gen_step() with no parameters, returning the
envelope {"shape": part, ...}; aces-cad uses the shape and warns that the other keys are ignored.
"""
from build123d import Align, Box, Cylinder, Pos


def gen_step():
    plate = Box(60, 40, 5, align=(Align.MIN, Align.MIN, Align.MIN))
    plate = plate - Pos(30, 20, 0) * Cylinder(5, 5, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return {"shape": plate, "note": "this key is ignored by aces-cad (fixture)"}
