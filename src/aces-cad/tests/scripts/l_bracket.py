"""L-bracket (test fixture): a 40 x 30 x 5 base and a 5 x 30 x 40 upright, corner at the origin, a 2 mm fillet on
the inner corner edge (parallel to Y at x = 5, z = 5), and two d6 through holes, one in each leg.

The fillet is a concave cylinder that spans 90 degrees: it must be reported as a radius, never as a hole.
"""
from build123d import Align, Axis, Box, BuildPart, Hole, Locations, Plane, fillet

MIN = (Align.MIN, Align.MIN, Align.MIN)


def build(p):
    with BuildPart() as bracket:
        Box(40, 30, 5, align=MIN)
        Box(5, 30, 40, align=MIN)
        corner = [e for e in bracket.edges().filter_by(Axis.Y)
                  if abs(e.center().X - 5) < 1e-6 and abs(e.center().Z - 5) < 1e-6]
        fillet(corner, radius=2)
        with Locations(Plane(origin=(25, 15, 5), z_dir=(0, 0, 1))):
            Hole(radius=3)
        with Locations(Plane(origin=(5, 15, 25), z_dir=(1, 0, 0))):
            Hole(radius=3)
    return bracket.part
