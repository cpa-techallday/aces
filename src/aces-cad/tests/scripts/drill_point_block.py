"""Two drilled holes (test fixture): a 60 x 40 x 20 block, corner at the origin.

- At (15, 20): a d6 hole drilled from the top face, its full-diameter bore 10 mm deep with a 118 degree drill point
  below it, the usual form of a blind hole in a vendor STEP part. It is a simple blind hole of depth 10 entering at
  (15, 20, 20): the cone at its closed bottom is not a countersink.
- At (45, 20): a d6.6 through hole countersunk 90 degrees on both faces, d12 at the top and d8 at the bottom. The
  upper end is the entry and its countersink is the one measured: d12 x 90 degrees.
"""
import math

from build123d import Align, Box, Cone, Cylinder, Pos

CCM = (Align.CENTER, Align.CENTER, Align.MIN)


def build(p):
    block = Box(60, 40, 20, align=(Align.MIN, Align.MIN, Align.MIN))
    tip = 3 / math.tan(math.radians(59))
    bore = Pos(15, 20, 10) * Cylinder(3, 11, align=CCM)
    point = Pos(15, 20, 10 - tip) * Cone(0, 3, tip, align=CCM)
    drill = Pos(45, 20, 0) * Cylinder(3.3, 20, align=CCM)
    top = Pos(45, 20, 17) * Cone(3, 7, 4, align=CCM)
    bottom = Pos(45, 20, -1) * Cone(5, 2, 3, align=CCM)
    return block - bore - point - drill - top - bottom
