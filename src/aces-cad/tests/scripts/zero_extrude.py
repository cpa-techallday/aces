"""A kernel failure (test fixture): an extrude of zero length raises Standard_ConstructionError from OCP."""
from build123d import Rectangle, extrude


def build(p):
    return extrude(Rectangle(10, 10), amount=0)
