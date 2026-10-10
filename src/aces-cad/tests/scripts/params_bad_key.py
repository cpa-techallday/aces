"""A PARAMS spec with an unknown key (test fixture): "minimum" is not one of the spec keys, so it is refused."""
from build123d import Box

PARAMS = {"size": {"default": 10.0, "minimum": 1.0, "unit": "mm"}}


def build(p):
    return Box(p.size, p.size, p.size)
