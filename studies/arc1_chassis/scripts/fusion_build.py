"""ARC-1 :: build the chassis in Fusion.

Runs INSIDE Fusion via the fusion MCP. Adds to the document created by
fusion_envelope.py:

  LATTICE       the full 16,273-rod micro lattice as a mesh body
  SKELETON      macro member centrelines as 3D sketch lines (the design graph,
                and the robot's motion skeleton)
  DEMO_BOOM     one macro member as real B-rep rods and node balls, so the
                manufacturable detail is inspectable geometry not a picture
  ROCKER_L/R    the topology-optimised inboard suspension rockers

A full B-rep of 16k rods would not survive Fusion, which is why the whole
lattice is a mesh and only the demo boom is solid.
"""
import json
import os
import traceback

import adsk.core
import adsk.fusion

PROJ = r"C:\Users\shedm\arc-lattice-chassis"
DOC_NAME = "ARC-1 Chassis - rev A"


def mm(v):
    return v / 10.0


def P3(x, y, z):
    return adsk.core.Point3D.create(mm(x), mm(y), mm(z))


def load(name):
    return json.load(open(os.path.join(PROJ, "out", name)))


def new_comp(root, name):
    occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    occ.component.name = name
    return occ.component


# ---------------------------------------------------------------- lattice
def add_lattice(root, design):
    stl = os.path.join(PROJ, "out", "arc1_lattice.stl")
    comp = new_comp(root, "LATTICE")
    base = comp.features.baseFeatures.add()
    base.startEdit()
    lst = comp.meshBodies.add(stl, adsk.fusion.MeshUnits.MillimeterMeshUnit, base)
    base.finishEdit()
    if lst and lst.count:
        lst.item(0).name = "arc1_micro_lattice"
        print("  lattice mesh imported")
    return comp


# --------------------------------------------------------------- skeleton
def add_skeleton(root, frame):
    comp = new_comp(root, "SKELETON")
    sk = comp.sketches.add(root.xYConstructionPlane)
    sk.name = "macro_members"
    sk.isComputeDeferred = True
    nodes = frame["nodes"]
    lines = sk.sketchCurves.sketchLines
    for (i, j, _cls) in frame["members"]:
        a, b = nodes[i], nodes[j]
        lines.addByTwoPoints(P3(*a), P3(*b))
    sk.isComputeDeferred = False
    print(f"  skeleton: {sk.sketchCurves.sketchLines.count} lines")
    return comp


# -------------------------------------------------------------- demo boom
def add_demo_boom(root, frame, micro_nodes, micro_rods):
    """One macro member rebuilt as real solids."""
    tbm = adsk.fusion.TemporaryBRepManager.get()
    rod_r = 2.4 / 2.0
    ball_r = 7.0 / 2.0

    # pick a mid-chassis longeron - representative, and easy to find
    target = None
    for k, (i, j, cls) in enumerate(frame["members"]):
        if cls == "longeron":
            a, b = frame["nodes"][i], frame["nodes"][j]
            if 1200 < a[0] < 1700 and a[1] > 100 and a[2] > 200:
                target = (a, b)
                break
    if target is None:
        target = (frame["nodes"][frame["members"][0][0]],
                  frame["nodes"][frame["members"][0][1]])

    (ax, ay, az), (bx, by, bz) = target
    lo = [min(ax, bx) - 60, min(ay, by) - 60, min(az, bz) - 60]
    hi = [max(ax, bx) + 60, max(ay, by) + 60, max(az, bz) + 60]

    def inside(p):
        return all(lo[d] <= p[d] <= hi[d] for d in range(3))

    comp = new_comp(root, "DEMO_BOOM")
    base = comp.features.baseFeatures.add()
    base.startEdit()
    nrod = nball = 0
    used = set()
    for (i, j, _kind, _cls) in micro_rods:
        p0, p1 = micro_nodes[i], micro_nodes[j]
        if not (inside(p0) and inside(p1)):
            continue
        body = tbm.createCylinderOrCone(P3(*p0), mm(rod_r), P3(*p1), mm(rod_r))
        comp.bRepBodies.add(body, base)
        nrod += 1
        used.add(i)
        used.add(j)
    for i in used:
        comp.bRepBodies.add(tbm.createSphere(P3(*micro_nodes[i]), mm(ball_r)),
                            base)
        nball += 1
    base.finishEdit()
    print(f"  demo boom: {nrod} rods + {nball} node balls as B-rep")
    return comp


# ----------------------------------------------------------------- rocker
def add_rocker(root, topo, x, y, z, name, mirror=False):
    comp = new_comp(root, name)
    planes = comp.constructionPlanes
    pi = planes.createInput()
    pi.setByOffset(root.xZConstructionPlane,
                   adsk.core.ValueInput.createByReal(mm(y)))
    plane = planes.add(pi)

    sk = comp.sketches.add(plane)
    sk.name = "rocker_profile"
    sk.isComputeDeferred = True
    # Closed POLYLINES, not fitted splines. The contour points are already
    # resampled at 3 mm; a fitted spline through them overshoots on the tight
    # organic curvature, self-intersects, and shatters the region into slivers
    # (the first attempt extruded 0.1 cm3 instead of 82).
    lines = sk.sketchCurves.sketchLines
    for poly in topo["polys"]:
        pts = [sk.modelToSketchSpace(
                   P3(x + (-px if mirror else px), y, z + py))
               for (px, py) in poly]
        for k in range(len(pts)):
            lines.addByTwoPoints(pts[k], pts[(k + 1) % len(pts)])
    for (bx, by) in topo["hardpoints"].values():
        wx = x + (-bx if mirror else bx)
        sk.sketchCurves.sketchCircles.addByCenterRadius(
            sk.modelToSketchSpace(P3(wx, y, z + by)), mm(topo["bore_r"]))
    sk.isComputeDeferred = False

    profs = sorted([sk.profiles.item(k) for k in range(sk.profiles.count)],
                   key=lambda p: -p.areaProperties().area)
    print(f"    {name} profile areas cm2: " +
          ", ".join(f"{p.areaProperties().area*100:.1f}" for p in profs[:6]))
    ext = comp.features.extrudeFeatures
    ei = ext.createInput(profs[0],
                         adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    t = topo["plate_t"]
    ei.setDistanceExtent(True, adsk.core.ValueInput.createByReal(mm(t / 2.0)))
    f = ext.add(ei)
    f.bodies.item(0).name = name
    print(f"  {name}: {sk.profiles.count} profiles, {t} mm plate")
    return comp


def main():
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    root = design.rootComponent
    print("document:", app.activeDocument.name)

    frame = load("arc1_frame.json")
    topo = load("rocker_topo.json")
    micro = load("micro_rods.json")
    import array
    import struct
    # micro nodes were saved as .npy - read the raw float64 payload
    path = os.path.join(PROJ, "out", "micro_nodes.npy")
    with open(path, "rb") as fh:
        magic = fh.read(6)
        fh.read(2)
        hlen = struct.unpack("<H", fh.read(2))[0]
        fh.read(hlen)
        buf = array.array("d")
        buf.frombytes(fh.read())
    mn = [tuple(buf[k:k + 3]) for k in range(0, len(buf), 3)]
    print(f"  loaded {len(mn)} micro nodes, {len(micro['rods'])} rods")

    add_lattice(root, design)
    add_skeleton(root, frame)
    add_demo_boom(root, frame, mn, micro["rods"])
    add_rocker(root, topo, 605.0, 150.0, 330.0, "ROCKER_R")
    add_rocker(root, topo, 605.0, -150.0, 330.0, "ROCKER_L", mirror=True)

    # fade the envelope so the lattice reads
    for b in root.bRepBodies:
        if b.name == "envelope_solid":
            b.opacity = 0.12
            print("  envelope faded to 12%")

    app.activeViewport.camera.viewOrientation = \
        adsk.core.ViewOrientations.IsoTopRightViewOrientation
    app.activeViewport.fit()
    print("done")


if __name__ == "__fusion_main__":
    try:
        main()
    except Exception:
        print(traceback.format_exc())
        raise
