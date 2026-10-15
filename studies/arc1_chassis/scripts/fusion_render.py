"""ARC-1 :: write the presentation renders to out/renders/."""
import math
import os
import traceback

import adsk.core
import adsk.fusion

PROJ = r"C:\Users\shedm\arc-lattice-chassis"
OUTDIR = os.path.join(PROJ, "out", "renders")


def cam(app, target_mm, az_deg, el_deg, extents_cm):
    vp = app.activeViewport
    c = vp.camera
    c.isSmoothTransition = False
    c.cameraType = adsk.core.CameraTypes.OrthographicCameraType
    tx, ty, tz = [v / 10.0 for v in target_mm]
    az, el = math.radians(az_deg), math.radians(el_deg)
    d = max(extents_cm * 4.0, 200.0)
    c.target = adsk.core.Point3D.create(tx, ty, tz)
    c.eye = adsk.core.Point3D.create(
        tx + d * math.cos(el) * math.cos(az),
        ty + d * math.cos(el) * math.sin(az),
        tz + d * math.sin(el))
    c.upVector = adsk.core.Vector3D.create(0, 0, 1)
    c.viewExtents = extents_cm
    vp.camera = c
    adsk.doEvents()


def show(root, names, envelope=False):
    for occ in root.occurrences:
        occ.isLightBulbOn = occ.component.name in names
    for b in root.bRepBodies:
        if b.name == "envelope_solid":
            b.isVisible = envelope
            b.opacity = 0.10
    for comp in [root] + [o.component for o in root.allOccurrences]:
        for sk in comp.sketches:
            sk.isVisible = False
    adsk.doEvents()


def shot(app, name, w=1800, h=1100):
    os.makedirs(OUTDIR, exist_ok=True)
    p = os.path.join(OUTDIR, name + ".png")
    ok = app.activeViewport.saveAsImageFile(p, w, h)
    print(f"  {'ok ' if ok else 'FAIL'} {name}.png")
    return p


def main():
    app = adsk.core.Application.get()
    des = adsk.fusion.Design.cast(app.activeProduct)
    root = des.rootComponent
    ALL = {"LATTICE", "ROCKER_R", "ROCKER_L", "DEMO_BOOM"}

    show(root, ALL)
    cam(app, (1300, 0, 340), 128, 24, 165)
    shot(app, "01_hero")

    cam(app, (1300, 0, 400), 90, 2, 160)
    shot(app, "02_side", 1900, 850)

    cam(app, (1300, 0, 340), 90, 89, 160)
    shot(app, "03_plan", 1900, 850)

    cam(app, (1300, 0, 340), 215, 20, 165)
    shot(app, "04_rear34")

    # suspension bay: rockers in context
    show(root, ALL)
    cam(app, (620, 60, 330), 118, 22, 42)
    shot(app, "05_rocker_in_situ", 1600, 1100)

    show(root, {"ROCKER_R"})
    cam(app, (605, 150, 335), 90, 0, 13)
    shot(app, "06_rocker_profile", 1400, 1200)

    show(root, {"DEMO_BOOM"})
    cam(app, (1430, 300, 300), 140, 18, 16)
    shot(app, "07_boom_detail", 1600, 1200)

    show(root, ALL)
    cam(app, (1300, 0, 340), 128, 24, 165)
    print("done")


if __name__ == "__fusion_main__":
    try:
        main()
    except Exception:
        print(traceback.format_exc())
        raise
