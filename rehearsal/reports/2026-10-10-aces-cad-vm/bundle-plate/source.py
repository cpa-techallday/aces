from build123d import Align, Box, Cylinder, Pos

PARAMS = {
    "length": {"default": 60.0, "min": 10.0, "max": 500.0, "unit": "mm", "doc": "plate length along X"},
    "width": {"default": 40.0, "min": 10.0, "max": 500.0, "unit": "mm", "doc": "plate width along Y"},
    "thickness": {"default": 5.0, "min": 1.0, "max": 50.0, "unit": "mm", "doc": "plate thickness along Z"},
    "hole_d": {"default": 10.0, "min": 1.0, "max": 30.0, "unit": "mm", "doc": "centre hole diameter"},
}


def build(p):
    plate = Box(p.length, p.width, p.thickness, align=(Align.MIN, Align.MIN, Align.MIN))
    hole = Cylinder(p.hole_d / 2, p.thickness, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return plate - Pos(p.length / 2, p.width / 2, 0) * hole
