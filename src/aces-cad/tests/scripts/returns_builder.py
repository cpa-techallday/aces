"""Returns the builder instead of its part (test fixture): refused with the hint to return p.part."""
from build123d import Box, BuildPart


def build(p):
    with BuildPart() as part:
        Box(10, 10, 10)
    return part
