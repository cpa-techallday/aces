"""One hole more than hole detection reports (test fixture): perforated_block.py (a 100 x 100 x 20 block, corner at
the origin, with a 10 x 10 grid of d4 through holes at a 10 mm pitch starting at (5, 5)) plus a 101st d4 through hole
at (50, 50), the centre of the grid.
"""
from build123d import Align, Box, Cylinder, Pos


def build(p):
    block = Box(100, 100, 20, align=(Align.MIN, Align.MIN, Align.MIN))
    centres = [(5 + 10 * i, 5 + 10 * j) for i in range(10) for j in range(10)] + [(50, 50)]
    holes = [Pos(x, y, 0) * Cylinder(2, 20, align=(Align.CENTER, Align.CENTER, Align.MIN)) for x, y in centres]
    return block.cut(*holes)
