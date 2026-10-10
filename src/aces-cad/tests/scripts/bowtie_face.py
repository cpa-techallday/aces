"""A self-intersecting planar face (test fixture): a bowtie polygon; build123d builds it and is_valid is False."""
from build123d import Polyline, make_face


def build(p):
    return make_face(Polyline((0, 0), (10, 10), (10, 0), (0, 10), close=True))
