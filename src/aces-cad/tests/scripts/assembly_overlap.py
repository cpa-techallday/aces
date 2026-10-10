"""Assembly with an interference (test fixture): two 10 mm cubes, the second moved 5 mm along X (500 mm3 shared)."""
from build123d import Box, Pos


def build(p):
    return {"a": Box(10, 10, 10), "b": Pos(5, 0, 0) * Box(10, 10, 10)}
