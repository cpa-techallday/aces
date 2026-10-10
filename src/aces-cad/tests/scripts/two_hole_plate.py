"""Two-hole plate (test fixture): Box(60, 40, 5) centred on the origin minus two d10 through holes at x = -15 and 15.

It declares no PARAMS: the script contract makes PARAMS optional, and any override is then refused.
"""
from build123d import Box, Cylinder, Pos


def build(p):
    plate = Box(60, 40, 5)
    return plate - Pos(-15, 0, 0) * Cylinder(5, 5) - Pos(15, 0, 0) * Cylinder(5, 5)
