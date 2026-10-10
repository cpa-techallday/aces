"""PARAMS that is not a literal (test fixture): a dict() call, refused before anything is spawned."""
from build123d import Box

PARAMS = dict(size=10.0)


def build(p):
    return Box(p.size, p.size, p.size)
