"""A design space too busy for the golden path at a fine grid (test fixture): a 100 x 100 x 20 block, corner at the
origin, with a 10 x 10 grid of d4 through holes at a 10 mm pitch starting at (5, 5).
"""
from build123d import Align, Box, Cylinder, Pos


def build(p):
    block = Box(100, 100, 20, align=(Align.MIN, Align.MIN, Align.MIN))
    holes = [Pos(5 + 10 * i, 5 + 10 * j, 0) * Cylinder(2, 20, align=(Align.CENTER, Align.CENTER, Align.MIN))
             for i in range(10) for j in range(10)]
    return block.cut(*holes)
