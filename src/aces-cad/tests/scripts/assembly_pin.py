"""Assembly with a clearance (test fixture): a 10 mm cube centred on the origin and a d8 x 12 pin centred at x = 20.

The closest points are (5, 0, z) on the cube and (16, 0, z) on the pin: an 11 mm clearance and no interference.
"""
from build123d import Box, Cylinder, Pos


def build(p):
    return {"base": Box(10, 10, 10), "pin": Pos(20, 0, 0) * Cylinder(4, 12)}
