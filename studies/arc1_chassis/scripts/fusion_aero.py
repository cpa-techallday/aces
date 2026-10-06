"""ARC-1 :: build the aero package in Fusion from out/aero.json.

Generic: it does not know what a wing is. A "loft job" is a list of planar
closed sections; a "plate job" is one closed polygon extruded symmetrically.
Section plane orientation is detected from the points themselves - wing
sections are spanwise (constant y, XZ plane), duct and diffuser sections are
streamwise (constant x, YZ plane).
"""
import json
import os
import traceback

import adsk.core
import adsk.fusion

PROJ = r"C:\Users\shedm\arc-lattice-chassis"


def mm(v):
    return v / 10.0


def P3(x, y, z):
    return adsk.core.Point3D.create(mm(x), mm(y), mm(z))


def dedupe(pts, tol=0.02):
    out = []
    for p in pts:
        if not out or max(abs(p[k] - out[-1][k]) for k in range(3)) > tol:
            out.append(p)
    if len(out) > 1 and max(abs(out[0][k] - out[-1][k]) for k in range(3)) < tol:
        out.pop()
    return out


def section_plane(comp, root, sec):
    """Offset construction plane matching the section's constant axis."""
    xs = [p[0] for p in sec]
    ys = [p[1] for p in sec]
    planes = comp.constructionPlanes
    pi = planes.createInput()
    if max(xs) - min(xs) < 1e-6:                    # streamwise: YZ plane
        pi.setByOffset(root.yZConstructionPlane,
                       adsk.core.ValueInput.createByReal(mm(xs[0])))
    elif max(ys) - min(ys) < 1e-6:                  # spanwise: XZ plane
        pi.setByOffset(root.xZConstructionPlane,
                       adsk.core.ValueInput.createByReal(mm(ys[0])))
    else:
        raise RuntimeError("section is not planar in x or y")
    return planes.add(pi)


def polyline(sk, pts3d):
    lines = sk.sketchCurves.sketchLines
    sp = [sk.modelToSketchSpace(P3(*p)) for p in pts3d]
    for k in range(len(sp)):
        lines.addByTwoPoints(sp[k], sp[(k + 1) % len(sp)])


def build_loft(root, job):
    occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    comp = occ.component
    comp.name = job["name"]
    lofts = comp.features.loftFeatures
    li = lofts.createInput(
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    n = 0
    for si, sec in enumerate(job["sections"]):
        pts = dedupe([tuple(p) for p in sec])
        plane = section_plane(comp, root, pts)
        sk = comp.sketches.add(plane)
        sk.name = f"{job['name']}_s{si}"
        sk.isComputeDeferred = True
        polyline(sk, pts)
        sk.isComputeDeferred = False
        if sk.profiles.count == 0:
            raise RuntimeError(f"{job['name']} section {si}: no closed profile")
        best = max((sk.profiles.item(k) for k in range(sk.profiles.count)),
                   key=lambda p: p.areaProperties().area)
        li.loftSections.add(best)
        n += 1
    li.isSolid = True
    f = lofts.add(li)
    vol = sum(f.bodies.item(k).physicalProperties.volume
              for k in range(f.bodies.count))
    for k in range(f.bodies.count):
        f.bodies.item(k).name = job["name"]
    print(f"  {job['name']:<18} {n} sections  {vol:>7.1f} cm3")
    return comp


def build_plate(root, job):
    occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    comp = occ.component
    comp.name = job["name"]
    planes = comp.constructionPlanes
    pi = planes.createInput()
    pi.setByOffset(root.xZConstructionPlane,
                   adsk.core.ValueInput.createByReal(mm(job["offset"])))
    sk = comp.sketches.add(planes.add(pi))
    sk.name = job["name"]
    sk.isComputeDeferred = True
    pts = [(u, job["offset"], v) for (u, v) in job["poly"]]
    polyline(sk, dedupe(pts))
    sk.isComputeDeferred = False
    prof = max((sk.profiles.item(k) for k in range(sk.profiles.count)),
               key=lambda p: p.areaProperties().area)
    ext = comp.features.extrudeFeatures
    ei = ext.createInput(prof,
                         adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ei.setDistanceExtent(
        job.get("sym", True),
        adsk.core.ValueInput.createByReal(mm(job["thick"] / 2.0)))
    f = ext.add(ei)
    f.bodies.item(0).name = job["name"]
    print(f"  {job['name']:<22} {job['thick']:>4.0f} mm plate  "
          f"{f.bodies.item(0).physicalProperties.volume:>6.1f} cm3")
    return comp


def main():
    app = adsk.core.Application.get()
    des = adsk.fusion.Design.cast(app.activeProduct)
    root = des.rootComponent
    aero = json.load(open(os.path.join(PROJ, "out", "aero.json")))

    # clear any previous aero build
    known = {j["name"] for j in aero["lofts"]} | {j["name"] for j in aero["plates"]}
    for occ in list(root.occurrences):
        if occ.component.name in known:
            occ.deleteMe()

    print("LOFTS")
    for job in aero["lofts"]:
        build_loft(root, job)
    print("PLATES")
    for job in aero["plates"]:
        build_plate(root, job)

    tot = 0.0
    for occ in root.occurrences:
        if occ.component.name in known:
            for b in occ.component.bRepBodies:
                tot += b.physicalProperties.volume
    print(f"\naero solid volume {tot:,.0f} cm3   "
          f"({tot*1.55/1000:.1f} kg in CFRP-equivalent 1.55 g/cm3 solid, "
          f"skinned parts will be far less)")
    print(f"downforce at {aero['v_ref_ms']*3.6:.0f} km/h: "
          f"{aero['total_downforce_N']:,.0f} N")


if __name__ == "__fusion_main__":
    try:
        main()
    except Exception:
        print(traceback.format_exc())
        raise
