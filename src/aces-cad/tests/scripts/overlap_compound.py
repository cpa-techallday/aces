"""One part made of two overlapping, unfused solids (test fixture): BRepCheck passes it, BRepAlgoAPI_Check reports
BOPAlgo_SelfIntersect, and its volume sum counts the 500 mm3 overlap twice.
"""
from build123d import Box, Compound, Pos


def build(p):
    return Compound(children=[Box(10, 10, 10), Pos(5, 0, 0) * Box(10, 10, 10)])
