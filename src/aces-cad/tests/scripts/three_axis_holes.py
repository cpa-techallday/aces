"""Holes along X, Y and Z (test fixture): a 60 x 40 x 30 block, corner at the origin, with a d8 through hole along Y
at x 40, z 15, a d10 through hole along Z at (15, 20) and a d6 hole 10 mm deep from the +X face at y 20, z 20. Hole
detection numbers them by face order (d8, d10, d6 on build123d 0.11.1), which is not their position order: the
drawing must keep those ids, not renumber them.
"""
from build123d import Align, Box, Cylinder, Pos, Rot

MIN3 = (Align.MIN, Align.MIN, Align.MIN)
CCM = (Align.CENTER, Align.CENTER, Align.MIN)


def build(p):
    block = Box(60, 40, 30, align=MIN3)
    z_hole = Pos(15, 20, 0) * Cylinder(5, 30, align=CCM)
    y_hole = Pos(40, 0, 15) * Rot(-90, 0, 0) * Cylinder(4, 40, align=CCM)
    x_hole = Pos(50, 20, 20) * Rot(0, 90, 0) * Cylinder(3, 10, align=CCM)
    return block - z_hole - y_hole - x_hole
