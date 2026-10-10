"""Holes block (test fixture): an 80 x 40 x 20 mm block, corner at the origin, drilled from its top face (z = 20).

x 15: d10 through; x 35: d6 blind, 8 deep; x 52: d6.6 through with a d11 x 6 counterbore; x 68: d6.6 through with a
d12 x 90 degree countersink.
"""
from build123d import Align, Box, BuildPart, CounterBoreHole, CounterSinkHole, Hole, Locations, Plane

MIN = (Align.MIN, Align.MIN, Align.MIN)


def build(p):
    with BuildPart() as block:
        Box(80, 40, 20, align=MIN)
        with Locations(Plane(origin=(15, 20, 20), z_dir=(0, 0, 1))):
            Hole(radius=5)
        with Locations(Plane(origin=(35, 20, 20), z_dir=(0, 0, 1))):
            Hole(radius=3, depth=8)
        with Locations(Plane(origin=(52, 20, 20), z_dir=(0, 0, 1))):
            CounterBoreHole(radius=3.3, counter_bore_radius=5.5, counter_bore_depth=6)
        with Locations(Plane(origin=(68, 20, 20), z_dir=(0, 0, 1))):
            CounterSinkHole(radius=3.3, counter_sink_radius=6.0, counter_sink_angle=90)
    return block.part
