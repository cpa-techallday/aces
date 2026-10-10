"""Loft blend (test fixture): a d40 circle at z = 0, a 50 x 24 ellipse at z = 30 and a d20 circle at z = 60, lofted.

Its side is one free-form face, so radii there are sampled estimates (inspection check radii).
"""
from build123d import BuildPart, BuildSketch, Circle, Ellipse, Plane, loft


def build(p):
    with BuildPart() as blend:
        with BuildSketch(Plane.XY):
            Circle(20)
        with BuildSketch(Plane.XY.offset(30)):
            Ellipse(25, 12)
        with BuildSketch(Plane.XY.offset(60)):
            Circle(10)
        loft()
    return blend.part
