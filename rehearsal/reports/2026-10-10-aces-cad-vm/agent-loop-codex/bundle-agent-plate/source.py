from build123d import *

PARAMS = {
    "length": 60,
    "width": 40,
    "thickness": 5,
    "hole": 10,
}


def build(p):
    length = p["length"]
    width = p["width"]
    thickness = p["thickness"]
    hole = p["hole"]

    with BuildPart() as plate:
        Box(length, width, thickness)
        Cylinder(radius=hole / 2, height=thickness * 2, mode=Mode.SUBTRACT)

    return plate.part
