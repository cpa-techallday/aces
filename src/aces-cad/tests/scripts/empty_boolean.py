"""An empty result (test fixture): the intersection of two disjoint cubes is an empty Compound (0 solids)."""
from build123d import Box, Pos


def build(p):
    return Box(10, 10, 10) & (Pos(100, 0, 0) * Box(10, 10, 10))
