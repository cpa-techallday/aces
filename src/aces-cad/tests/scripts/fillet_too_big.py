"""A kernel failure (test fixture): a 50 mm fillet on every edge of a 10 mm cube; build123d raises
ValueError("Failed creating a fillet with radius of 50, ...") and aces-cad reports kind kernel with a hint.
"""
from build123d import Box, fillet


def build(p):
    cube = Box(10, 10, 10)
    return fillet(cube.edges(), radius=50)
