"""Returns an open shell (test fixture): five faces of a cube; refused, an open shell is not a solid."""
from build123d import Box, Shell


def build(p):
    return Shell(Box(10, 10, 10).faces()[:-1])
