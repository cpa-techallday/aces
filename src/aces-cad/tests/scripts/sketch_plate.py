"""A 2-D result (test fixture): a 20 x 10 rectangle minus a d6 circle, area 200 - 9 pi mm2."""
from build123d import Circle, Rectangle


def build(p):
    return Rectangle(20, 10) - Circle(3)
