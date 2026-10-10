"""An assembly with holes in both parts (test fixture): "left", a 40 x 40 x 5 plate with a d8 through hole at
(20, 20), and "right", the same plate moved 50 mm along X with two d6 through holes at (60, 20) and (80, 20).
cad_inspect numbers the holes of several parts across them, in part order, and gives each part its own groups.
"""
from build123d import Align, Box, Cylinder, Pos

MIN3 = (Align.MIN, Align.MIN, Align.MIN)
CCM = (Align.CENTER, Align.CENTER, Align.MIN)


def build(p):
    left = Box(40, 40, 5, align=MIN3) - Pos(20, 20, 0) * Cylinder(4, 5, align=CCM)
    right = Box(40, 40, 5, align=MIN3)
    for x in (10, 30):
        right = right - Pos(x, 20, 0) * Cylinder(3, 5, align=CCM)
    return {"left": left, "right": Pos(50, 0, 0) * right}
