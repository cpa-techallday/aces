"""Shared geometry helpers of the worker ops (section 5.3 of the build spec).

Everything heavy (build123d, OCP, numpy, trimesh) is imported inside the functions. Face, edge and vertex references
are 1-based in build123d's faces()/edges()/vertices() order on the loaded part ("f7").
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from aces_cad.worker.main import WorkerError

REASON_TEXT = {
    "invalid_topology": "BRepCheck reports the topology invalid",
    "open_shell": "a shell has free edges (the part is not closed)",
    "non_positive_volume": "a solid has zero or negative signed volume (reversed or degenerate)",
    "no_solid": "the part holds no solid",
}
HOLE_LIMIT = 100
DIR_TOL_RAD = 1e-6
AXIS_TOL_MM = 1e-4
END_PROBE_MM = 0.01


@dataclass
class LoadedPart:
    index: int
    name: str
    kind: str                 # "solid" | "sketch" | "mesh"
    shape: object | None      # build123d Shape
    mesh: object | None       # trimesh.Trimesh


@dataclass
class Loaded:
    kind: str                 # "solid" | "assembly" | "sketch" | "mesh"
    parts: list[LoadedPart]
    datums: dict[str, object] = field(default_factory=dict)


def face_ref(index: int) -> str:
    """"f<n>", 1-based in build123d faces() order."""
    return f"f{int(index)}"


def load(out_dir: Path) -> Loaded:
    """parts.json + p<i>.brep/.npz + d<i>.brep of a run (np.load with allow_pickle=False)."""
    import build123d as bd
    out_dir = Path(out_dir)
    try:
        with open(out_dir / "parts.json", "rb") as fh:
            doc = json.loads(fh.read(1 << 20).decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as e:
        raise WorkerError("integrity", f"the run's parts.json cannot be read ({type(e).__name__})") from None
    parts = []
    for p in doc.get("parts") or []:
        path = out_dir / str(p["file"])
        if p["kind"] == "mesh":
            import numpy as np
            import trimesh
            with np.load(path, allow_pickle=False) as data:
                mesh = trimesh.Trimesh(vertices=np.asarray(data["vertices"], dtype=np.float64),
                                       faces=np.asarray(data["faces"], dtype=np.int64), process=False)
            parts.append(LoadedPart(index=int(p["index"]), name=str(p["name"]), kind="mesh", shape=None, mesh=mesh))
        else:
            shape = bd.import_brep(str(path))
            parts.append(LoadedPart(index=int(p["index"]), name=str(p["name"]), kind=str(p["kind"]), shape=shape,
                                    mesh=None))
    datums = {}
    for d in doc.get("datums") or []:
        datums[str(d["name"])] = bd.import_brep(str(out_dir / str(d["file"])))
    return Loaded(kind=str(doc.get("kind")), parts=parts, datums=datums)


def select(loaded: Loaded, part: str | None) -> list[LoadedPart]:
    """Every part, or the one named; WorkerError("not_found") for an unknown name."""
    if part is None:
        return list(loaded.parts)
    for p in loaded.parts:
        if p.name == part:
            return [p]
    names = ", ".join(p.name for p in loaded.parts)
    raise WorkerError("not_found", f"no part {part!r:.70} in this run; parts: {names}")


def bbox(shape) -> list[float]:
    """[xmin, ymin, zmin, xmax, ymax, zmax] from bounding_box(optimal=True)."""
    bb = shape.bounding_box(optimal=True)
    return [bb.min.X, bb.min.Y, bb.min.Z, bb.max.X, bb.max.Y, bb.max.Z]


def _bounds(obj) -> list[float]:
    if hasattr(obj, "bounds") and not hasattr(obj, "wrapped"):
        b = obj.bounds
        return [float(b[0][0]), float(b[0][1]), float(b[0][2]), float(b[1][0]), float(b[1][1]), float(b[1][2])]
    if isinstance(obj, LoadedPart):
        return _bounds(obj.mesh) if obj.kind == "mesh" else bbox(obj.shape)
    return bbox(obj)


def render_tolerance(obj) -> float:
    """max(0.01, 0.001 x the bounding-box diagonal) mm."""
    b = _bounds(obj)
    diag = math.sqrt(sum((b[i + 3] - b[i]) ** 2 for i in range(3)))
    return max(0.01, 0.001 * diag)


def tessellate(shape, tol_mm: float, ang_rad: float) -> tuple:
    """(vertices Nx3, faces Mx3, face_index M): the triangulation of every face (any earlier one is cleared first);
    face_index holds the 1-based face number of each triangle."""
    import numpy as np
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_REVERSED
    from OCP.TopLoc import TopLoc_Location
    BRepTools.Clean_s(shape.wrapped)
    BRepMesh_IncrementalMesh(shape.wrapped, float(tol_mm), False, float(ang_rad), True)
    verts, tris, index = [], [], []
    offset = 0
    for n, face in enumerate(shape.faces(), 1):
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face.wrapped, loc)
        if tri is None:
            continue
        trsf = loc.Transformation()
        nodes = np.empty((tri.NbNodes(), 3), dtype=np.float64)
        for j in range(1, tri.NbNodes() + 1):
            p = tri.Node(j).Transformed(trsf)
            nodes[j - 1] = (p.X(), p.Y(), p.Z())
        faces = np.empty((tri.NbTriangles(), 3), dtype=np.int64)
        for k in range(1, tri.NbTriangles() + 1):
            faces[k - 1] = tri.Triangle(k).Get()
        faces -= 1
        if face.wrapped.Orientation() == TopAbs_REVERSED:
            faces = faces[:, [0, 2, 1]]
        verts.append(nodes)
        tris.append(faces + offset)
        index.append(np.full(len(faces), n, dtype=np.int64))
        offset += len(nodes)
    if not tris:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), np.zeros(0, dtype=np.int64)
    return np.vstack(verts), np.vstack(tris), np.concatenate(index)


def to_trimesh(part: LoadedPart, tol_mm: float, ang_rad: float):
    """The part as a trimesh.Trimesh (a mesh part as it is); metadata["face_index"] maps triangles to faces."""
    import trimesh
    if part.kind == "mesh":
        return part.mesh
    v, f, fi = tessellate(part.shape, tol_mm, ang_rad)
    mesh = trimesh.Trimesh(vertices=v, faces=f, process=True)
    if len(mesh.faces) == len(fi):
        mesh.metadata["face_index"] = fi
    return mesh


def _signed_volumes(shape) -> list[float]:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    out = []
    for s in shape.solids():
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(s.wrapped, props)
        out.append(props.Mass())
    return out


def has_free_edges(shape) -> bool:
    """ShapeAnalysis_Shell: LoadShells + CheckOrientedShells(shape, True) + HasFreeEdges."""
    from OCP.ShapeAnalysis import ShapeAnalysis_Shell
    sas = ShapeAnalysis_Shell()
    sas.LoadShells(shape.wrapped)
    sas.CheckOrientedShells(shape.wrapped, True)
    return bool(sas.HasFreeEdges())


def brepcheck_valid(shape) -> bool:
    from OCP.BRepCheck import BRepCheck_Analyzer
    return bool(BRepCheck_Analyzer(shape.wrapped).IsValid())


def quick_validity(part: LoadedPart) -> tuple[bool, list[str]]:
    """BRepCheck, free edges and signed volumes (solids); BRepCheck (sketches); watertight and volume (meshes)."""
    reasons: list[str] = []
    if part.kind == "mesh":
        m = part.mesh
        if not m.is_watertight:
            reasons.append("open_shell")
        elif m.volume <= 0:
            reasons.append("non_positive_volume")
        return not reasons, reasons
    shape = part.shape
    if not brepcheck_valid(shape):
        reasons.append("invalid_topology")
    if part.kind == "solid":
        if shape.shells() and has_free_edges(shape):
            reasons.append("open_shell")
        if any(v <= 0 for v in _signed_volumes(shape)):
            reasons.append("non_positive_volume")
        if not shape.solids():
            reasons.append("no_solid")
    return not reasons, reasons


def _vec(v) -> list[float]:
    return [float(v.X), float(v.Y), float(v.Z)]


def summarize(part: LoadedPart) -> dict:
    """The per-part summary of a run (section 11.2)."""
    valid, reasons = quick_validity(part)
    if part.kind == "mesh":
        m = part.mesh
        b = _bounds(m)
        watertight = bool(m.is_watertight)
        vol = float(m.volume) if watertight else None
        centroid = m.center_mass if watertight and vol and vol > 0 else m.centroid
        return {"name": part.name, "kind": "mesh", "volume_mm3": vol, "area_mm2": float(m.area), "bbox_mm": b,
                "size_mm": [b[3] - b[0], b[4] - b[1], b[5] - b[2]], "centroid_mm": [float(c) for c in centroid],
                "valid": valid, "reasons": reasons, "counts": {"vertices": int(len(m.vertices)),
                                                               "faces": int(len(m.faces))},
                "watertight": watertight}
    import build123d as bd
    shape = part.shape
    b = bbox(shape)
    counts = {"solids": len(shape.solids()), "shells": len(shape.shells()), "faces": len(shape.faces()),
              "edges": len(shape.edges()), "vertices": len(shape.vertices())}
    # MASS for sketches too: build123d's GEOMETRY centre of a planar face is its area centroid, and a Sketch compound
    # does not support GEOMETRY
    centroid = _vec(shape.center(bd.CenterOf.MASS))
    return {"name": part.name, "kind": part.kind, "volume_mm3": float(shape.volume) if part.kind == "solid" else None,
            "area_mm2": float(shape.area), "bbox_mm": b, "size_mm": [b[3] - b[0], b[4] - b[1], b[5] - b[2]],
            "centroid_mm": centroid, "valid": valid, "reasons": reasons, "counts": counts}


# --- holes ------------------------------------------------------------------------------------------------------------

def canonical(d):
    """A unit direction with its dominant component positive."""
    import numpy as np
    d = np.asarray(d, dtype=float)
    d = d / np.linalg.norm(d)
    i = int(np.argmax(np.abs(d)))
    return -d if d[i] < 0 else d


def _axis_of(face):
    """(point on the axis, unit direction, radius or None, cone data or None) of a cylinder or cone face."""
    import numpy as np
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Cylinder
    ad = BRepAdaptor_Surface(face.wrapped)
    st = ad.GetType()
    if st == GeomAbs_Cylinder:
        cyl = ad.Cylinder()
        ax = cyl.Axis()
        loc, d = ax.Location(), ax.Direction()
        return np.array([loc.X(), loc.Y(), loc.Z()]), np.array([d.X(), d.Y(), d.Z()]), cyl.Radius(), None
    if st == GeomAbs_Cone:
        cone = ad.Cone()
        ax = cone.Axis()
        loc, d = ax.Location(), ax.Direction()
        return (np.array([loc.X(), loc.Y(), loc.Z()]), np.array([d.X(), d.Y(), d.Z()]), None,
                {"semi_angle": cone.SemiAngle(), "ref_radius": cone.RefRadius()})
    return None


def _concave(face, point, direction) -> bool:
    """The 12.4 normal test: the normal at the UV centre points toward the axis."""
    import numpy as np
    p = face.position_at(0.5, 0.5)
    n = face.normal_at(0.5, 0.5)
    pv = np.array([p.X, p.Y, p.Z])
    foot = point + np.dot(pv - point, direction) * direction
    return float(np.dot(np.array([n.X, n.Y, n.Z]), foot - pv)) > 0


def _u_span(face) -> float:
    from OCP.BRepTools import BRepTools
    umin, umax, _vmin, _vmax = BRepTools.UVBounds_s(face.wrapped)
    return float(umax - umin)


def _axial_extent(face, origin, direction) -> tuple[float, float]:
    """(min, max) of the face boundary projected on the axis (each edge sampled at 9 points)."""
    import numpy as np
    ts = []
    for e in face.edges():
        for k in range(9):
            p = e.position_at(k / 8)
            ts.append(float(np.dot(np.array([p.X, p.Y, p.Z]) - origin, direction)))
    return min(ts), max(ts)


def _radius_at(face, origin, direction, t: float) -> float:
    """The radius of a cone face at axial coordinate t, interpolated between its boundary circles."""
    import numpy as np
    samples = []
    for e in face.edges():
        for k in range(9):
            p = e.position_at(k / 8)
            pv = np.array([p.X, p.Y, p.Z])
            a = float(np.dot(pv - origin, direction))
            r = float(np.linalg.norm(pv - (origin + a * direction)))
            samples.append((a, r))
    samples.sort()
    lo, hi = samples[0], samples[-1]
    if abs(hi[0] - lo[0]) < 1e-12:
        return max(r for _a, r in samples)
    return lo[1] + (t - lo[0]) * (hi[1] - lo[1]) / (hi[0] - lo[0])


def _inside(solids, point) -> bool:
    import build123d as bd
    v = bd.Vector(float(point[0]), float(point[1]), float(point[2]))
    return any(s.is_inside(v) for s in solids)


def find_holes(shape, limit: int = HOLE_LIMIT) -> list[dict]:
    """Holes: groups of coaxial concave cylinder (and cone) faces whose drill-radius cylinders span 360 degrees.

    Through/blind from Solid.is_inside 0.01 mm beyond each end of the stack; counterbore (a larger coaxial cylinder at
    the entry) and countersink (a concave cone at the entry) detected, only between an open end and the drill-radius
    bore, so a drill point at a blind bottom is neither. A blind hole's depth runs from the entry to the far end of
    the bore. Records of section 12.4, at most `limit` (100 by default); callers that report the cap ask for
    HOLE_LIMIT + 1 to learn whether more holes exist.
    """
    import numpy as np
    import build123d as bd
    faces = list(shape.faces())
    cands = []
    for n, f in enumerate(faces, 1):
        if f.geom_type not in (bd.GeomType.CYLINDER, bd.GeomType.CONE):
            continue
        info = _axis_of(f)
        if info is None:
            continue
        point, direction, radius, cone = info
        direction = direction / np.linalg.norm(direction)
        if f.geom_type == bd.GeomType.CYLINDER:
            concave = bool(getattr(f, "is_circular_concave", False))
        else:
            concave = _concave(f, point, direction)
        if not concave:
            continue
        cands.append({"n": n, "face": f, "point": point, "dir": canonical(direction), "radius": radius,
                      "cone": cone})
    # union-find on coaxial faces
    parent = list(range(len(cands)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(cands)):
        for j in range(i + 1, len(cands)):
            a, b = cands[i], cands[j]
            if np.linalg.norm(np.cross(a["dir"], b["dir"])) > DIR_TOL_RAD:
                continue
            off = b["point"] - a["point"]
            dist = np.linalg.norm(off - np.dot(off, a["dir"]) * a["dir"])
            if dist <= AXIS_TOL_MM:
                parent[root(j)] = root(i)
    groups: dict[int, list[dict]] = {}
    for i, c in enumerate(cands):
        groups.setdefault(root(i), []).append(c)
    solids = list(shape.solids())
    holes = []
    for members in sorted(groups.values(), key=lambda g: min(m["n"] for m in g)):
        cyls = [m for m in members if m["radius"] is not None]
        if not cyls:
            continue
        drill = min(m["radius"] for m in cyls)
        span = sum(_u_span(m["face"]) for m in cyls if abs(m["radius"] - drill) <= 1e-6)
        if span < 2 * math.pi - 1e-6:
            continue
        origin, d = members[0]["point"], members[0]["dir"]
        for m in members:
            m["t"] = _axial_extent(m["face"], origin, d)
        t_lo = min(m["t"][0] for m in members)
        t_hi = max(m["t"][1] for m in members)
        lo_open = not _inside(solids, origin + (t_lo - END_PROBE_MM) * d)
        hi_open = not _inside(solids, origin + (t_hi + END_PROBE_MM) * d)
        through = lo_open and hi_open
        bore = [m for m in cyls if abs(m["radius"] - drill) <= 1e-6]  # the drill-radius cylinders
        bore_lo = min(m["t"][0] for m in bore)
        bore_hi = max(m["t"][1] for m in bore)
        # Spec 12.4 puts a counterbore or a countersink at an open end: a face counts when it lies between an open end
        # of the stack and the drill-radius bore, so a drill point at a closed bottom, or a groove inside the bore, is
        # neither. With features at both ends the upper end is the entry, as for a plain through hole, and the
        # counterbore or countersink is measured there.
        extra = [m for m in members if m["cone"] is not None or m["radius"] > drill + 1e-6]
        at_hi = [m for m in extra if hi_open and m["t"][0] >= bore_hi - 1e-6]
        at_lo = [m for m in extra if lo_open and m["t"][1] <= bore_lo + 1e-6]
        if at_hi or at_lo:
            entry_hi = bool(at_hi)
        elif through:
            entry_hi = True
        else:
            entry_hi = hi_open or not lo_open
        side = at_hi if entry_hi else at_lo
        bigger = [m for m in side if m["cone"] is None]
        cones = [m for m in side if m["cone"] is not None]
        t_entry = t_hi if entry_hi else t_lo
        axis_dir = d if entry_hi else -d
        cbore = None
        csk = None
        kind = "simple"
        if bigger:
            big = max(bigger, key=lambda m: m["radius"])
            kind = "counterbore"
            cbore = {"diameter_mm": 2 * big["radius"], "depth_mm": big["t"][1] - big["t"][0]}
        elif cones:
            cone = cones[0]
            kind = "countersink"
            r_entry = abs(_radius_at(cone["face"], origin, d, t_entry))
            csk = {"diameter_mm": 2 * r_entry, "angle_deg": 2 * abs(math.degrees(cone["cone"]["semi_angle"]))}
        entry = origin + t_entry * d
        # a through hole is as deep as its stack; a blind hole to the far end of its drill-radius bore, the drawing
        # convention (a drill point below the bore is not part of the depth)
        if through:
            depth = t_hi - t_lo
        else:
            depth = t_hi - bore_lo if entry_hi else bore_hi - t_lo
        holes.append({"kind": kind, "diameter_mm": 2 * drill, "axis_dir": [float(x) + 0.0 for x in axis_dir],
                      "entry_point_mm": [float(x) + 0.0 for x in entry], "depth_mm": float(depth),
                      "through": bool(through), "cbore": cbore, "csk": csk,
                      "faces": [face_ref(m["n"]) for m in sorted(members, key=lambda m: m["n"])]})
    holes = holes[:max(0, int(limit))]
    for i, h in enumerate(holes, 1):
        h["id"] = f"h{i}"
    _group_holes(holes)
    return [{"id": h["id"], "kind": h["kind"], "diameter_mm": h["diameter_mm"], "axis_dir": h["axis_dir"],
             "entry_point_mm": h["entry_point_mm"], "depth_mm": h["depth_mm"], "through": h["through"],
             "cbore": h["cbore"], "csk": h["csk"], "faces": h["faces"], "group": h["group"]} for h in holes]


def _near(a, b, tol: float = 1e-6) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_near(a[k], b[k], tol) for k in a)
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(_near(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)):
        return abs(a - b) <= tol
    return a == b


def _group_holes(holes: list[dict]) -> None:
    keys = ("kind", "diameter_mm", "depth_mm", "through", "cbore", "csk", "axis_dir")
    groups: list[list[dict]] = []
    for h in holes:
        for g in groups:
            if all(_near(h[k], g[0][k]) for k in keys):
                g.append(h)
                break
        else:
            groups.append([h])
    for i, g in enumerate(groups, 1):
        for h in g:
            h["group"] = f"g{i}"


def hole_groups(holes: list[dict]) -> list[dict]:
    """{"id","count","kind","diameter_mm","depth_mm","through","cbore","csk","holes"} per group of equal holes."""
    out: dict[str, dict] = {}
    for h in holes:
        g = out.setdefault(h["group"], {"id": h["group"], "count": 0, "kind": h["kind"],
                                        "diameter_mm": h["diameter_mm"], "depth_mm": h["depth_mm"],
                                        "through": h["through"], "cbore": h["cbore"], "csk": h["csk"], "holes": []})
        g["count"] += 1
        g["holes"].append(h["id"])
    return list(out.values())
