"""The ACES plate with a named datum (test fixture): the part "plate" and the datum "@top", its top face at z = 5."""
from build123d import Align, Axis, Box, Cylinder, Pos


def build(p):
    plate = Box(60, 40, 5, align=(Align.MIN, Align.MIN, Align.MIN))
    plate = plate - Pos(30, 20, 0) * Cylinder(5, 5, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return {"plate": plate, "@top": plate.faces().sort_by(Axis.Z)[-1]}
