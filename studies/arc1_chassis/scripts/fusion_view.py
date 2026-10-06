"""ARC-1 :: camera + visibility control for presentable views.

Fusion's ViewOrientations enum is relative to its own world, not to the car:
this chassis runs along +X, so 'right' looks down the car's nose. Set the
camera explicitly instead.

Usage from the MCP: set VIEW at the bottom before running.
"""
import math
import os
import traceback

import adsk.core
import adsk.fusion

PROJ = r"C:\Users\shedm\arc-lattice-chassis"

VIEWS = {
    #            azimuth(deg from +X, about Z), elevation(deg), label
    "hero":      (128.0, 24.0),
    "side":      (90.0, 0.0),
    "top":       (90.0, 89.5),
    "front34":   (35.0, 18.0),
    "rear34":    (215.0, 20.0),
}


def set_sketches(root, visible):
    for comp in [root] + [o.component for o in root.allOccurrences]:
        for sk in comp.sketches:
            sk.isVisible = visible


def body_named(root, name):
    for b in root.bRepBodies:
        if b.name == name:
            return b
    return None


def frame_view(app, az_deg, el_deg, zoom=1.0):
    vp = app.activeViewport
    cam = vp.camera
    cam.isSmoothTransition = False
    # orthographic: perspective rejects an explicit viewExtents, and for an
    # engineering view parallel projection reads better anyway
    cam.cameraType = adsk.core.CameraTypes.OrthographicCameraType

    # chassis centre, in cm
    cx, cy, cz = 130.0, 0.0, 34.0
    dist = 620.0 * zoom
    az, el = math.radians(az_deg), math.radians(el_deg)
    ex = cx + dist * math.cos(el) * math.cos(az)
    ey = cy + dist * math.cos(el) * math.sin(az)
    ez = cz + dist * math.sin(el)

    cam.target = adsk.core.Point3D.create(cx, cy, cz)
    cam.eye = adsk.core.Point3D.create(ex, ey, ez)
    cam.upVector = adsk.core.Vector3D.create(0, 0, 1)
    cam.viewExtents = 300.0 * zoom
    vp.camera = cam
    adsk.doEvents()


def main():
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    root = design.rootComponent

    cfg = os.environ.get("ARC1_VIEW", "hero")
    show_sk = os.environ.get("ARC1_SKETCH", "0") == "1"
    show_env = os.environ.get("ARC1_ENV", "1") == "1"

    set_sketches(root, show_sk)
    env = body_named(root, "envelope_solid")
    if env:
        env.isVisible = show_env
        env.opacity = 0.10

    az, el = VIEWS.get(cfg, VIEWS["hero"])
    frame_view(app, az, el, float(os.environ.get("ARC1_ZOOM", "1.0")))
    print(f"view={cfg} az={az} el={el} sketches={show_sk} envelope={show_env}")


if __name__ == "__fusion_main__":
    try:
        main()
    except Exception:
        print(traceback.format_exc())
        raise
