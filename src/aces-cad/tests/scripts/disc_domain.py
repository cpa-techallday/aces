"""A design space for the golden path (test fixture): a d100 x 40 disc on z = 0, bbox 0..100 x 0..100 x 0..40 mm."""
from build123d import Align, Cylinder, Pos


def build(p):
    return Pos(50, 50, 0) * Cylinder(radius=50, height=40, align=(Align.CENTER, Align.CENTER, Align.MIN))
