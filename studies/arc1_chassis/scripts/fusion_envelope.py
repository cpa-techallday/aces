"""ARC-1 :: build the chassis outer envelope in Fusion and export it for lattice generation.

Runs INSIDE Fusion (via the fusion MCP). Reads cad/params.json, lofts the station
sections into a solid tub, cuts the cockpit trough, exports STL.

Fusion's API works in CENTIMETRES internally. Every length here is converted on
the way in with mm().
"""
import json
import math
import os
import traceback

import adsk.core
import adsk.fusion

PROJ = r"C:\Users\shedm\arc-lattice-chassis"
DOC_NAME = os.environ.get("ARC1_DOC", "ARC-1 Chassis - rev A")


def mm(v):
    """mm -> Fusion internal cm."""
    return v / 10.0


def superellipse(a, b, n_top, n_bot, count):
    """Rounded-rectangle section points (y, z_rel). Flatter floor than roof."""
    pts = []
    for k in range(count):
        t = 2.0 * math.pi * k / count
        ct, st = math.cos(t), math.sin(t)
        n = n_bot if st < 0 else n_top
        e = 2.0 / n
        y = a * (0.0 if ct == 0 else math.copysign(abs(ct) ** e, ct))
        z = b * (0.0 if st == 0 else math.copysign(abs(st) ** e, st))
        pts.append((y, z))
    return pts


def add_closed_spline(sketch, world_pts):
    """Closed fitted spline through world-space (x,y,z) mm tuples."""
    coll = adsk.core.ObjectCollection.create()
    for (x, y, z) in world_pts:
        p = adsk.core.Point3D.create(mm(x), mm(y), mm(z))
        coll.add(sketch.modelToSketchSpace(p))
    spline = sketch.sketchCurves.sketchFittedSplines.add(coll)
    try:
        spline.isClosed = True
    except Exception:
        # older API: close by joining last->first with the same spline call
        coll.add(coll.item(0))
        spline = sketch.sketchCurves.sketchFittedSplines.add(coll)
    return spline


def rounded_rect_pts(x0, x1, half_w, r, count_corner=10):
    """Closed rounded-rectangle in the XY plane, as (x, y) mm."""
    r = min(r, half_w * 0.95, (x1 - x0) * 0.45)
    pts = []
    corners = [
        (x1 - r,  half_w - r,   0.0),
        (x0 + r,  half_w - r,   math.pi / 2),
        (x0 + r, -half_w + r,   math.pi),
        (x1 - r, -half_w + r,   3 * math.pi / 2),
    ]
    for (cx, cy, a0) in corners:
        for k in range(count_corner + 1):
            a = a0 + (math.pi / 2) * k / count_corner
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def main():
    app = adsk.core.Application.get()
    ui = app.userInterface

    P = json.load(open(os.path.join(PROJ, "cad",
                   os.environ.get("ARC1_PARAMS", "params.json"))))
    env, ck = P["envelope"], P["cockpit"]

    # ---- fresh document -------------------------------------------------
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    doc.name = DOC_NAME
    design = adsk.fusion.Design.cast(app.activeProduct)
    design.designType = adsk.fusion.DesignTypes.ParametricDesignType
    root = design.rootComponent  # root name follows the document name

    planes = root.constructionPlanes
    npts = int(env["section_points"])

    # ---- station sections ----------------------------------------------
    sketches = []
    for st in env["stations"]:
        x = float(st["x"])
        a = float(st["half_width"])
        h = float(st["height"])
        zf = float(st["z_floor"])
        n_top = float(st["n"])
        n_bot = min(n_top * 1.7, 8.0)
        b = h / 2.0
        zc = zf + b

        pi = planes.createInput()
        pi.setByOffset(root.yZConstructionPlane,
                       adsk.core.ValueInput.createByReal(mm(x)))
        plane = planes.add(pi)
        plane.name = "STA_%d" % int(x)

        sk = root.sketches.add(plane)
        sk.name = "sec_%d_%s" % (int(x), st["label"].split("/")[0].strip().replace(" ", "_"))
        sk.isComputeDeferred = True
        world = [(x, y, zc + zr) for (y, zr) in
                 superellipse(a, b, n_top, n_bot, npts)]
        add_closed_spline(sk, world)
        sk.isComputeDeferred = False
        sketches.append(sk)

    print("sections built: %d" % len(sketches))

    # ---- loft the tub ---------------------------------------------------
    lofts = root.features.loftFeatures
    li = lofts.createInput(adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    for sk in sketches:
        if sk.profiles.count == 0:
            raise RuntimeError("sketch %s produced no closed profile" % sk.name)
        li.loftSections.add(sk.profiles.item(0))
    li.isSolid = True
    loft = lofts.add(li)
    body = loft.bodies.item(0)
    body.name = "envelope_solid"
    print("loft ok, volume = %.1f L" % (body.physicalProperties.volume / 1000.0))

    # ---- cockpit trough -------------------------------------------------
    pi = planes.createInput()
    pi.setByOffset(root.xYConstructionPlane,
                   adsk.core.ValueInput.createByReal(mm(float(ck["floor_z"]))))
    ck_plane = planes.add(pi)
    ck_plane.name = "cockpit_floor"

    sk = root.sketches.add(ck_plane)
    sk.name = "cockpit_opening"
    sk.isComputeDeferred = True
    rr = rounded_rect_pts(float(ck["x_front"]), float(ck["x_rear"]),
                          float(ck["half_width"]), float(ck["corner_radius"]))
    add_closed_spline(sk, [(px, py, float(ck["floor_z"])) for (px, py) in rr])
    sk.isComputeDeferred = False

    zmax = max(s["z_floor"] + s["height"] for s in env["stations"])
    ext = root.features.extrudeFeatures
    ei = ext.createInput(sk.profiles.item(0),
                         adsk.fusion.FeatureOperations.CutFeatureOperation)
    ei.setDistanceExtent(False, adsk.core.ValueInput.createByReal(
        mm(zmax - float(ck["floor_z"]) + 50.0)))
    ext.add(ei)
    print("cockpit cut ok, volume now = %.1f L"
          % (body.physicalProperties.volume / 1000.0))

    bb = body.boundingBox
    print("bbox mm  x:%.0f..%.0f  y:%.0f..%.0f  z:%.0f..%.0f" % (
        bb.minPoint.x * 10, bb.maxPoint.x * 10,
        bb.minPoint.y * 10, bb.maxPoint.y * 10,
        bb.minPoint.z * 10, bb.maxPoint.z * 10))

    # ---- export for the lattice pipeline --------------------------------
    outdir = os.path.join(PROJ, P["output"]["dir"])
    os.makedirs(outdir, exist_ok=True)
    stl = os.path.join(outdir, "arc1_envelope.stl")
    em = design.exportManager
    opts = em.createSTLExportOptions(body, stl)
    opts.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementHigh
    opts.isBinaryFormat = True
    em.execute(opts)
    print("exported %s (%.1f KB)" % (stl, os.path.getsize(stl) / 1024.0))

    step = os.path.join(outdir, "arc1_envelope.step")
    em.execute(em.createSTEPExportOptions(step, root))
    print("exported %s" % step)

    app.activeViewport.camera.viewOrientation = \
        adsk.core.ViewOrientations.IsoTopRightViewOrientation
    app.activeViewport.fit()


if __name__ == "__fusion_main__":
    try:
        main()
    except Exception:
        print(traceback.format_exc())
        raise
