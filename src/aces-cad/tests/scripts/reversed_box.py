"""A reversed solid (test fixture): a 10 mm cube with its orientation flipped. BRepCheck passes it and its signed
volume is -1000 mm3, so the validity check must report non_positive_volume.
"""
from build123d import Box, Solid


def build(p):
    return Solid(Box(10, 10, 10).wrapped.Reversed())
