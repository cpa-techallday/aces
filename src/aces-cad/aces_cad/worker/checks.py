"""cad_inspect: the 11 checks, run in a worker that executes no agent code (section 12 of the build spec).

Each check works on the run's promoted outputs (parts.json, p<i>.brep|npz, d<i>.brep) and says which method it used.
A check that raises is recorded as {"error": {"kind", "message"}} and the others still run. Mesh parts skip the
B-rep-only checks. Lists are capped with *_truncated flags. (Not named inspect.py: it would shadow the stdlib module
if this directory were ever on sys.path.)
"""
from __future__ import annotations

import math

from aces_cad.worker import geom
from aces_cad.worker.main import Ctx, WorkerError

MESH_SKIP = {"skipped": "mesh results have no B-rep faces"}
MAX_FACES_LISTED = 200
MAX_STATUSES = 20
MAX_SMALL = 20
SMALL_EDGE_MM = 1e-3
SLIVER_FACE_MM2 = 1e-4
MAX_PAIRS = 500
MAX_CELLS = 2_000_000
VOID_BOX_LIMIT = 2000


def _v(vec) -> list[float]:
    return [float(vec.X), float(vec.Y), float(vec.Z)]


def _gtype(shape) -> str:
    return str(shape.geom_type).split(".")[-1]


def _np(vec):
    import numpy as np
    return np.array([vec.X, vec.Y, vec.Z], dtype=float)


# --- thickness (shared with the thickness render) --------------------------------------------------------------------

def thickness_per_triangle(mesh, max_triangles: int = 300_000):
    """Per-triangle wall thickness: one ray per triangle from its centroid, offset 1e-4 mm inward along -normal, to
    the first hit (trimesh's intersector: embree when available). NaN where the ray hits nothing. Rays are cast in
    batches of at most max_triangles."""
    import numpy as np
    n = len(mesh.faces)
    out = np.full(n, np.nan)
    if n == 0:
        return out
    centres = mesh.triangles_center
    normals = mesh.face_normals
    origins = centres - normals * 1e-4
    dirs = -normals
    step = max(1000, int(max_triangles))
    for start in range(0, n, step):
        stop = min(n, start + step)
        locs, ray_idx, _tri = mesh.ray.intersects_location(origins[start:stop], dirs[start:stop],
                                                           multiple_hits=False)
        if len(ray_idx):
            dist = np.linalg.norm(locs - origins[start:stop][ray_idx], axis=1) + 1e-4
            out[start + ray_idx] = dist
    return out


def _ray_backend() -> str:
    import trimesh
    return "embree" if getattr(trimesh.ray, "has_embree", False) else "triangle"


def _weighted_percentile(values, weights, q: float) -> float:
    import numpy as np
    order = np.argsort(values)
    v = values[order]
    w = np.cumsum(weights[order])
    target = q * w[-1]
    i = int(np.searchsorted(w, target, side="left"))
    return float(v[min(i, len(v) - 1)])


# --- mass -------------------------------------------------------------------------------------------------------------

def check_mass(loaded, parts, args, ctx) -> dict:
    import build123d as bd
    import numpy as np
    density = args.get("density")
    rho = float(density["kg_m3"]) if density else None
    rows = []
    for p in parts:
        if p.kind == "mesh":
            m = p.mesh
            if m.is_watertight:
                mp = m.mass_properties
                row = {"name": p.name, "kind": "mesh", "volume_mm3": float(mp["volume"]),
                       "area_mm2": float(m.area), "centroid_mm": [float(c) for c in mp["center_mass"]],
                       "inertia_mm5": np.asarray(mp["inertia"]).tolist(), "method": "trimesh mass_properties"}
            else:
                row = {"name": p.name, "kind": "mesh", "volume_mm3": None, "area_mm2": float(m.area),
                       "reason": "the mesh is not watertight: it has no volume or inertia"}
        elif p.kind == "sketch":
            row = {"name": p.name, "kind": "sketch", "volume_mm3": None, "area_mm2": float(p.shape.area),
                   "centroid_mm": _v(p.shape.center(bd.CenterOf.MASS)), "reason": "a sketch has no volume"}
        else:
            s = p.shape
            moi = [[float(x) for x in r] for r in s.matrix_of_inertia]
            principal = s.principal_properties
            row = {"name": p.name, "kind": p.kind, "volume_mm3": float(s.volume), "area_mm2": float(s.area),
                   "centroid_mm": _v(s.center(bd.CenterOf.MASS)), "inertia_mm5": moi,
                   "principal_moments_mm5": [float(mm) for _ax, mm in principal],
                   "principal_axes": [_v(ax) for ax, _mm in principal]}
        if rho is not None and row.get("volume_mm3") is not None:
            row["mass_kg"] = row["volume_mm3"] * rho * 1e-9
            if row.get("inertia_mm5") is not None:
                row["inertia_kg_mm2"] = [[x * rho * 1e-9 for x in r] for r in row["inertia_mm5"]]
        rows.append(row)
    vols = [r["volume_mm3"] for r in rows if r.get("volume_mm3") is not None]
    totals = {"volume_mm3": sum(vols) if vols else None, "area_mm2": sum(r["area_mm2"] for r in rows)}
    if rho is not None:
        masses = [r["mass_kg"] for r in rows if "mass_kg" in r]
        totals["mass_kg"] = sum(masses) if masses else None
    return {"method": "OCCT BRepGProp volume and surface integrals on the B-rep (trimesh for watertight meshes); "
                      "inertia per part about its own centroid in global axes, unit density (mm^5)",
            "parts": rows, "totals": totals, "density": density}


# --- validity ---------------------------------------------------------------------------------------------------------

def _brepcheck_statuses(shape) -> list[dict]:
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.TopAbs import (TopAbs_COMPOUND, TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID, TopAbs_VERTEX,
                            TopAbs_WIRE)
    from OCP.TopExp import TopExp_Explorer
    an = BRepCheck_Analyzer(shape.wrapped)
    counts: dict[tuple[str, str], int] = {}
    for kind in (TopAbs_VERTEX, TopAbs_EDGE, TopAbs_WIRE, TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID, TopAbs_COMPOUND):
        ex = TopExp_Explorer(shape.wrapped, kind)
        while ex.More():
            res = an.Result(ex.Current())
            if res is not None:
                for st in res.Status():
                    name = str(st).split(".")[-1]
                    if name != "BRepCheck_NoError":
                        key = (name, str(kind).split(".")[-1].replace("TopAbs_", ""))
                        counts[key] = counts.get(key, 0) + 1
            ex.Next()
    rows = [{"status": k[0], "shape_type": k[1], "count": v} for k, v in sorted(counts.items())]
    return rows


def _free_edge_count(shape) -> int:
    from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    fb = ShapeAnalysis_FreeBounds(shape.wrapped)
    n = 0
    for comp in (fb.GetClosedWires(), fb.GetOpenWires()):
        ex = TopExp_Explorer(comp, TopAbs_EDGE)
        while ex.More():
            n += 1
            ex.Next()
    return n


def _bop_check(shape) -> tuple[bool | None, list[str]]:
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Check
    chk = BRepAlgoAPI_Check(shape.wrapped, True, True)
    names = sorted({str(r.GetCheckStatus()).split(".")[-1] for r in chk.Result()})
    return "BOPAlgo_SelfIntersect" in names, names


def validity_of(part, ctx: Ctx | None = None) -> dict:
    """The 12.2 record of one part, with its reasons and ok."""
    from OCP.ShapeAnalysis import ShapeAnalysis_ShapeTolerance
    if part.kind == "mesh":
        m = part.mesh
        reasons = []
        if not m.is_watertight:
            reasons.append("mesh_not_watertight")
        if not m.is_winding_consistent:
            reasons.append("mesh_inconsistent_winding")
        if m.is_watertight and m.volume < 0:
            reasons.append("mesh_negative_volume")
        return {"name": part.name, "kind": "mesh",
                "mesh": {"watertight": bool(m.is_watertight), "winding_consistent": bool(m.is_winding_consistent),
                         "euler_number": int(m.euler_number),
                         "volume_mm3": float(m.volume) if m.is_watertight else None},
                "reasons": reasons, "ok": not reasons}
    s = part.shape
    reasons: list[str] = []
    out: dict = {"name": part.name, "kind": part.kind}
    out["brepcheck_valid"] = geom.brepcheck_valid(s)
    statuses = _brepcheck_statuses(s)
    out["statuses"] = statuses[:MAX_STATUSES]
    out["statuses_truncated"] = len(statuses) > MAX_STATUSES
    if not out["brepcheck_valid"]:
        reasons.append("invalid_topology")
    if part.kind == "solid":
        free = geom.has_free_edges(s) if s.shells() else True
        out["closed"] = not free
        out["free_edges"] = _free_edge_count(s)
        if free:
            reasons.append("open_shell")
        vols = geom._signed_volumes(s)
        out["signed_volumes_mm3"] = vols[:200]
        out["signed_volumes_truncated"] = len(vols) > 200
        if any(v <= 0 for v in vols):
            reasons.append("non_positive_volume")
        if not s.solids():
            reasons.append("no_solid")
        try:
            si, names = _bop_check(s)
            out["self_intersection"] = si
            out["bop_statuses"] = names
            if si:
                reasons.append("self_intersecting")
        except Exception as e:  # noqa: BLE001 (recorded, not fatal)
            out["self_intersection"] = None
            out["bop_statuses"] = []
            out["self_intersection_error"] = f"{type(e).__name__}: {str(e)[:200]}"
            reasons.append("self_intersection_unchecked")
        out["manifold"] = bool(s.is_manifold)
        if not out["manifold"]:
            reasons.append("non_manifold")
    else:
        out.update(closed=None, free_edges=None, signed_volumes_mm3=[], self_intersection=None, bop_statuses=[],
                   manifold=None)
    out["max_tolerance_mm"] = float(ShapeAnalysis_ShapeTolerance().Tolerance(s.wrapped, 1))
    small = [{"ref": f"e{i}", "length_mm": float(e.length)} for i, e in enumerate(s.edges(), 1)
             if e.length < SMALL_EDGE_MM]
    slivers = [{"ref": geom.face_ref(i), "area_mm2": float(f.area)} for i, f in enumerate(s.faces(), 1)
               if f.area < SLIVER_FACE_MM2]
    out["small_edges"] = small[:MAX_SMALL]
    out["small_edges_truncated"] = len(small) > MAX_SMALL
    out["sliver_faces"] = slivers[:MAX_SMALL]
    out["sliver_faces_truncated"] = len(slivers) > MAX_SMALL
    if part.kind == "solid":
        tol = geom.render_tolerance(s)
        m = geom.to_trimesh(part, tol, 0.2)
        brep_vol = float(s.volume)
        mvol = float(m.volume)
        out["mesh"] = {"tolerance_mm": tol, "watertight": bool(m.is_watertight),
                       "winding_consistent": bool(m.is_winding_consistent), "euler_number": int(m.euler_number),
                       "volume_mm3": mvol,
                       "volume_error_pct": (mvol - brep_vol) / abs(brep_vol) * 100 if brep_vol else None}
        if not m.is_watertight:
            reasons.append("mesh_not_watertight")
        if not m.is_winding_consistent:
            reasons.append("mesh_inconsistent_winding")
        if m.is_watertight and mvol < 0:
            reasons.append("mesh_negative_volume")
    out["reasons"] = reasons
    out["ok"] = not [r for r in reasons if r != "self_intersection_unchecked"]
    return out


def check_validity(loaded, parts, args, ctx) -> dict:
    rows = [validity_of(p, ctx) for p in parts]
    return {"method": "OCCT BRepCheck_Analyzer, ShapeAnalysis_Shell and ShapeAnalysis_FreeBounds, signed volumes per "
                      "solid (BRepGProp), BRepAlgoAPI_Check with self-intersection, build123d is_manifold, and a "
                      "tessellation at the render tolerance checked by trimesh",
            "parts": rows, "ok": all(r["ok"] for r in rows)}


# --- topology ---------------------------------------------------------------------------------------------------------

def _face_record(n: int, f) -> dict:
    import build123d as bd
    rec = {"ref": geom.face_ref(n), "type": _gtype(f), "area_mm2": float(f.area),
           "center_mm": _v(f.center(bd.CenterOf.MASS))}
    if f.geom_type == bd.GeomType.PLANE:
        rec["normal"] = _v(f.normal_at())
    elif f.geom_type == bd.GeomType.CYLINDER:
        ax = f.axis_of_rotation
        rec.update(axis=_v(ax.direction), axis_point=_v(ax.position), radius_mm=float(f.radius),
                   concave=bool(f.is_circular_concave))
    else:
        rec["concave"] = _curved_concave(f)
    return rec


def check_topology(loaded, parts, args, ctx) -> dict:
    rows = []
    for p in parts:
        if p.kind == "mesh":
            rows.append({"name": p.name, "kind": "mesh", **MESH_SKIP,
                         "counts": {"vertices": int(len(p.mesh.vertices)), "faces": int(len(p.mesh.faces))}})
            continue
        s = p.shape
        faces = list(s.faces())
        edges = list(s.edges())
        row = {"name": p.name, "kind": p.kind,
               "counts": {"solids": len(s.solids()), "shells": len(s.shells()), "faces": len(faces),
                          "edges": len(edges), "vertices": len(s.vertices())},
               "face_types": _histogram(_gtype(f) for f in faces),
               "edge_types": _histogram(_gtype(e) for e in edges)}
        if args.get("list_faces"):
            row["faces"] = [_face_record(n, f) for n, f in enumerate(faces[:MAX_FACES_LISTED], 1)]
            row["faces_truncated"] = len(faces) > MAX_FACES_LISTED
        rows.append(row)
    return {"method": "build123d topology explorers; f<n>, e<n> and v<n> are 1-based in faces()/edges()/vertices() "
                      "order", "parts": rows}


def _histogram(names) -> dict:
    out: dict[str, int] = {}
    for n in names:
        out[n] = out.get(n, 0) + 1
    return dict(sorted(out.items()))


# --- holes ------------------------------------------------------------------------------------------------------------

def check_holes(loaded, parts, args, ctx) -> dict:
    holes = []
    skipped = []
    for p in parts:
        if p.kind == "mesh":
            skipped.append(p.name)
            continue
        if p.kind != "solid":
            continue
        for h in geom.find_holes(p.shape):
            holes.append(dict(h, part=p.name))
    truncated = len(holes) > geom.HOLE_LIMIT
    holes = holes[:geom.HOLE_LIMIT]
    if len(parts) > 1:  # renumber across parts; a group never spans two parts
        groups: dict[tuple, str] = {}
        for i, h in enumerate(holes, 1):
            h["id"] = f"h{i}"
            key = (h["part"], h["group"])
            h["group"] = groups.setdefault(key, f"g{len(groups) + 1}")
    out = {"method": "coaxial concave cylinder and cone faces whose drill-radius cylinders span 360 degrees "
                     "(BRepTools UV bounds); ends by Solid.is_inside 0.01 mm beyond the stack",
           "holes": holes, "hole_groups": geom.hole_groups(holes), "count": len(holes),
           "holes_truncated": truncated}
    if skipped:
        out["skipped"] = MESH_SKIP["skipped"]
    return out


# --- thickness --------------------------------------------------------------------------------------------------------

def check_thickness(loaded, parts, args, ctx) -> dict:
    import numpy as np
    max_tri = int(args.get("max_triangles") or 300_000)
    min_t = args.get("min_thickness_mm")
    rows = []
    for p in parts:
        if p.kind == "sketch":
            rows.append({"name": p.name, "skipped": "a sketch has no wall thickness"})
            continue
        tol = geom.render_tolerance(p)
        raised = 0
        if p.kind == "mesh":
            mesh = p.mesh
        else:
            mesh = geom.to_trimesh(p, tol, 0.2)
            while len(mesh.faces) > max_tri and raised < 12:
                tol *= 2
                raised += 1
                mesh = geom.to_trimesh(p, tol, 0.2)
        t = thickness_per_triangle(mesh, max_tri)
        areas = np.asarray(mesh.area_faces)
        hit = ~np.isnan(t)
        row = {"name": p.name, "tolerance_mm": tol, "tolerance_raised": raised, "rays": int(len(t)),
               "hits": int(hit.sum()), "misses": int((~hit).sum())}
        if hit.any():
            tv, wa = t[hit], areas[hit]
            i_min = int(np.flatnonzero(hit)[np.argmin(tv)])
            row.update(min_mm=float(tv.min()), p01_mm=_weighted_percentile(tv, wa, 0.01),
                       p05_mm=_weighted_percentile(tv, wa, 0.05), p50_mm=_weighted_percentile(tv, wa, 0.50),
                       min_location_mm=[float(c) for c in mesh.triangles_center[i_min]])
        else:
            row.update(min_mm=None, p01_mm=None, p05_mm=None, p50_mm=None, min_location_mm=None)
        if min_t is not None:
            below = hit & (np.nan_to_num(t, nan=np.inf) < float(min_t))
            fi = mesh.metadata.get("face_index") if p.kind != "mesh" else None
            refs = sorted({int(x) for x in fi[below]}) if fi is not None else []
            row["below_min"] = {"min_thickness_mm": float(min_t), "area_mm2": float(areas[below].sum()),
                                "faces": [geom.face_ref(x) for x in refs[:50]], "faces_truncated": len(refs) > 50}
        rows.append(row)
    return {"method": "ray along -normal from triangle centroids", "backend": _ray_backend(),
            "note": "normal-direction thickness, not a medial-axis thickness; overestimates in sharp concave corners",
            "parts": rows,
            "min_mm": min((r["min_mm"] for r in rows if r.get("min_mm") is not None), default=None)}


# --- draft ------------------------------------------------------------------------------------------------------------

def _draft_deg(normal, pull) -> float:
    import numpy as np
    n = normal / (np.linalg.norm(normal) or 1.0)
    c = float(np.clip(np.dot(n, pull), -1.0, 1.0))
    return 90.0 - math.degrees(math.acos(c))


def check_draft(loaded, parts, args, ctx) -> dict:
    import numpy as np
    import build123d as bd
    pull = np.asarray(args.get("pull_direction") or [0.0, 0.0, 1.0], dtype=float)
    if np.linalg.norm(pull) == 0:
        raise WorkerError("usage", "pull_direction must not be zero")
    pull = pull / np.linalg.norm(pull)
    min_d = float(args.get("min_draft_deg", 1.0))
    counts = {"facing_pull": 0, "near_vertical": 0, "facing_away": 0}
    listed = []
    for p in parts:
        if p.kind == "mesh":
            return {"method": "face normals", **MESH_SKIP}
        for n, f in enumerate(p.shape.faces(), 1):
            if f.geom_type == bd.GeomType.PLANE:
                ds = [_draft_deg(_np(f.normal_at()), pull)]
            else:
                ds = []
                for i in range(12):
                    for j in range(12):
                        ds.append(_draft_deg(_np(f.normal_at((i + 0.5) / 12, (j + 0.5) / 12)), pull))
            lo, hi = min(ds), max(ds)
            flat = [d for d in ds if abs(d) < 89.5]  # faces perpendicular to the pull are not undercuts
            if flat and min(flat) <= -min_d and min(flat) < -1e-6:  # a vertical wall is not an undercut at min 0
                cls = "facing_away"
            elif flat and min(abs(d) for d in flat) < min_d:
                cls = "near_vertical"
            else:
                cls = "facing_pull"
            counts[cls] += 1
            if cls != "facing_pull":
                listed.append({"ref": geom.face_ref(n), "type": _gtype(f), "area_mm2": float(f.area),
                               "draft_deg_min": lo, "draft_deg_max": hi, "class": cls,
                               **({"part": p.name} if len(parts) > 1 else {})})
    return {"method": "draft = 90 deg - angle(normal, pull); planar faces exact, other faces on a 12 x 12 UV grid",
            "pull_direction": [float(x) for x in pull], "min_draft_deg": min_d, "counts": counts,
            "faces": listed[:50], "faces_truncated": len(listed) > 50,
            "note": "relative to one pull direction; parting lines are not inferred; faces perpendicular to the pull "
                    "(within 0.5 deg) count as facing_pull"}


# --- radii ------------------------------------------------------------------------------------------------------------

def _curved_concave(f) -> bool | None:
    """The 12.4 normal test for curved faces: the normal at the UV centre points toward the centre of curvature."""
    import numpy as np
    import build123d as bd
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    ad = BRepAdaptor_Surface(f.wrapped)
    p = _np(f.position_at(0.5, 0.5))
    nrm = _np(f.normal_at(0.5, 0.5))
    if f.geom_type == bd.GeomType.CYLINDER:
        return bool(f.is_circular_concave)
    if f.geom_type in (bd.GeomType.CONE, bd.GeomType.CYLINDER):
        info = geom._axis_of(f)
        if info is None:
            return None
        point, d = info[0], info[1] / np.linalg.norm(info[1])
        foot = point + np.dot(p - point, d) * d
        return float(np.dot(nrm, foot - p)) > 0
    if f.geom_type == bd.GeomType.SPHERE:
        c = ad.Sphere().Location()
        return float(np.dot(nrm, np.array([c.X(), c.Y(), c.Z()]) - p)) > 0
    if f.geom_type == bd.GeomType.TORUS:
        t = ad.Torus()
        ax = t.Axis()
        c = np.array([ax.Location().X(), ax.Location().Y(), ax.Location().Z()])
        d = np.array([ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()])
        q = p - c
        radial = q - np.dot(q, d) * d
        rn = np.linalg.norm(radial)
        tube = c + (radial / rn * t.MajorRadius() if rn > 0 else 0)
        return float(np.dot(nrm, tube - p)) > 0
    return None


def _sampled_radius(f) -> tuple[float | None, bool | None]:
    """Smallest radius of curvature on a 24 x 24 UV grid (BRepLProp_SLProps), with its concavity."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepLProp import BRepLProp_SLProps
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_REVERSED
    ad = BRepAdaptor_Surface(f.wrapped)
    u0, u1, v0, v1 = BRepTools.UVBounds_s(f.wrapped)
    reversed_ = f.wrapped.Orientation() == TopAbs_REVERSED
    best_k, best_sign = 0.0, None
    for i in range(24):
        for j in range(24):
            u = u0 + (u1 - u0) * (i + 0.5) / 24
            v = v0 + (v1 - v0) * (j + 0.5) / 24
            pr = BRepLProp_SLProps(ad, u, v, 2, 1e-6)
            if not pr.IsCurvatureDefined():
                continue
            for k in (pr.MaxCurvature(), pr.MinCurvature()):
                if abs(k) > abs(best_k):
                    best_k = k
                    # OCCT: the centre of curvature is P + N / k; concave when it lies on the material's outer side
                    best_sign = (k > 0) != reversed_
    if best_k == 0.0:
        return None, None
    return 1.0 / abs(best_k), best_sign


def check_radii(loaded, parts, args, ctx) -> dict:
    import build123d as bd
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    entries = []
    sampled = 0
    for p in parts:
        if p.kind == "mesh":
            return {"method": "B-rep surfaces", **MESH_SKIP}
        tag = {"part": p.name} if len(parts) > 1 else {}
        for n, f in enumerate(p.shape.faces(), 1):
            gt = f.geom_type
            if gt == bd.GeomType.PLANE:
                continue
            if gt == bd.GeomType.CYLINDER:
                entries.append({"ref": geom.face_ref(n), "radius_mm": float(f.radius),
                                "concave": bool(f.is_circular_concave), "exact": True, **tag})
            elif gt == bd.GeomType.SPHERE:
                entries.append({"ref": geom.face_ref(n), "radius_mm": float(BRepAdaptor_Surface(f.wrapped).Sphere()
                                                                            .Radius()),
                                "concave": _curved_concave(f), "exact": True, **tag})
            elif gt == bd.GeomType.TORUS:
                entries.append({"ref": geom.face_ref(n), "radius_mm": float(BRepAdaptor_Surface(f.wrapped).Torus()
                                                                            .MinorRadius()),
                                "concave": _curved_concave(f), "exact": True, **tag})
            elif gt == bd.GeomType.CONE:
                continue  # a cone has no single radius of curvature; holes report countersinks
            else:
                r, concave = _sampled_radius(f)
                sampled += 1
                if r is not None:
                    entries.append({"ref": geom.face_ref(n), "radius_mm": r, "concave": concave, "exact": False,
                                    **tag})
        for n, e in enumerate(p.shape.edges(), 1):
            if e.geom_type == bd.GeomType.CIRCLE:
                entries.append({"ref": f"e{n}", "radius_mm": float(e.radius), "concave": None, "exact": True,
                                **tag})
    conc = [e["radius_mm"] for e in entries if e["concave"] is True]
    conv = [e["radius_mm"] for e in entries if e["concave"] is False]
    smallest = sorted(entries, key=lambda e: e["radius_mm"])
    return {"method": "exact radii of cylinder, sphere and torus (minor) faces and circle edges; other curved faces "
                      "sampled on a 24 x 24 UV grid (BRepLProp_SLProps, largest principal curvature)",
            "min_concave_mm": min(conc) if conc else None, "min_convex_mm": min(conv) if conv else None,
            "smallest": smallest[:20], "smallest_truncated": len(smallest) > 20, "sampled_faces": sampled,
            "note": ("sampled values are estimates (a loft face gave 3.4699 mm against 3.5 analytic); circle edges "
                     "carry no concavity")}


# --- interference -----------------------------------------------------------------------------------------------------

def _aabb_overlap(a: list[float], b: list[float]) -> bool:
    return all(a[i] <= b[i + 3] and b[i] <= a[i + 3] for i in range(3))


def _intersection_volume(a, b) -> float:
    x = a.intersect(b)
    if x is None:
        return 0.0
    if hasattr(x, "volume") and not isinstance(x, list):
        return float(x.volume)
    return float(sum(getattr(s, "volume", 0.0) for s in x))


def check_interference(loaded, parts, args, ctx) -> dict:
    if any(p.kind == "mesh" for p in parts):
        return {"method": "BRepExtrema", "skipped": "no mesh collision library in the lock"}
    if len(parts) < 2:
        return {"method": "BRepExtrema", "skipped": "one part"}
    min_c = float(args.get("min_clearance_mm") or 0.0)
    pairs = []
    skipped = 0
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            if len(pairs) >= MAX_PAIRS:
                skipped += 1
                continue
            a, b = parts[i], parts[j]
            ba, bb = geom.bbox(a.shape), geom.bbox(b.shape)
            inter = _intersection_volume(a.shape, b.shape) if _aabb_overlap(ba, bb) else 0.0
            dist, pa, pb = a.shape.distance_to_with_closest_points(b.shape)
            if inter > 1e-3:
                status = "interfere"
            elif dist <= 1e-6:
                status = "touch"
            elif dist < min_c:
                status = "below_min_clearance"
            else:
                status = "clear"
            pairs.append({"a": a.name, "b": b.name, "intersection_mm3": inter, "clearance_mm": float(dist),
                          "closest_points_mm": [_v(pa), _v(pb)], "status": status})
    out = {"method": "BRepAlgoAPI common volume where the bounding boxes overlap; BRepExtrema distance and closest "
                     "points", "min_clearance_mm": min_c, "pairs": pairs,
           "ok": not any(p["status"] in ("interfere", "below_min_clearance") for p in pairs)}
    if skipped:
        out["skipped_pairs"] = {"count": skipped, "reason": f"at most {MAX_PAIRS} pairs are checked"}
    return out


# --- section ----------------------------------------------------------------------------------------------------------

def _plane(axis: str, at: float):
    import build123d as bd
    if axis == "x":
        return bd.Plane(origin=(at, 0, 0), x_dir=(0, 1, 0), z_dir=(1, 0, 0))
    if axis == "y":
        return bd.Plane(origin=(0, at, 0), x_dir=(1, 0, 0), z_dir=(0, -1, 0))
    return bd.Plane(origin=(0, 0, at), x_dir=(1, 0, 0), z_dir=(0, 0, 1))


def _section_props(faces, plane) -> dict:
    """Area, face count, bbox in (u, v), global centroid and the second moments about the section centroid."""
    import build123d as bd
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    comp = bd.Compound(children=list(faces)) if len(faces) != 1 else faces[0]
    local = plane.to_local_coords(comp)
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(local.wrapped, props)
    m = props.MatrixOfInertia()
    bb = local.bounding_box()
    c = props.CentreOfMass()
    centre = plane.from_local_coords(bd.Vector(c.X(), c.Y(), c.Z()))
    return {"area_mm2": float(props.Mass()), "faces": len(faces),
            "bbox_uv_mm": [float(bb.min.X), float(bb.min.Y), float(bb.max.X), float(bb.max.Y)],
            "centroid_mm": _v(centre), "second_moments_mm4": {"I_u": float(m.Value(1, 1)), "I_v": float(m.Value(2, 2))},
            "_local": local}


def check_section(loaded, parts, args, ctx) -> dict:
    import build123d as bd
    sec = args.get("section") or {}
    axis, at = sec.get("axis"), float(sec.get("at_mm"))
    if axis not in ("x", "y", "z"):
        raise WorkerError("usage", "section needs axis x, y or z")
    plane = _plane(axis, at)
    rows = []
    all_faces = []
    for p in parts:
        if p.kind != "solid":
            rows.append({"name": p.name, "skipped": "only solid parts are sectioned"})
            continue
        cut = bd.section(p.shape, plane)
        faces = list(cut.faces()) if cut is not None else []
        if not faces or sum(f.area for f in faces) <= 0:
            rows.append({"name": p.name, "error": {"kind": "result", "message": "the plane misses the part"}})
            continue
        props = _section_props(faces, plane)
        props.pop("_local")
        rows.append({"name": p.name, **props})
        all_faces.extend(faces)
    if not all_faces:
        raise WorkerError("result", "the plane misses the part")
    total = _section_props(all_faces, plane)
    local = total.pop("_local")
    svg_name = args.get("section_svg")
    out = {"method": "build123d section() with the plane of the axis; second moments from BRepGProp surface "
                     "integrals of the plane-local faces about the section centroid",
           "axis": axis, "at_mm": at,
           "plane": {"origin": _v(plane.origin), "x_dir": _v(plane.x_dir), "z_dir": _v(plane.z_dir),
                     "u": {"x": "+Y", "y": "+X", "z": "+X"}[axis], "v": {"x": "+Z", "y": "+Z", "z": "+Y"}[axis]},
           **total, "parts": rows, "note": "the product of inertia is not reported in 0.1.0", "svg": None}
    if svg_name:
        exp = bd.ExportSVG(unit=bd.Unit.MM)
        exp.add_layer("section")
        exp.add_shape(local, layer="section")
        exp.write(str(ctx.out_file(svg_name)))
        out["svg"] = svg_name
    return out


# --- measure ----------------------------------------------------------------------------------------------------------

def _resolve(ref: str, loaded) -> tuple[object, dict]:
    import re
    import numpy as np
    import build123d as bd
    if ref.startswith("@"):
        if ref not in loaded.datums:
            raise WorkerError("not_found", f"no datum {ref!r:.70}; datums: {', '.join(loaded.datums) or 'none'}")
        shape = loaded.datums[ref]
        return shape, {"ref": ref, "resolved": ref}
    name, _, sub = ref.partition(":")
    part = next((p for p in loaded.parts if p.name == name), None)
    if part is None:
        raise WorkerError("not_found", f"no part {name!r:.70}; parts: {', '.join(p.name for p in loaded.parts)}")
    if part.kind == "mesh":
        if sub:
            raise WorkerError("usage", "a mesh part has no faces, edges or vertices to name: measure the whole part")
        return part, {"ref": ref, "resolved": name}
    if not sub:
        return part.shape, {"ref": ref, "resolved": name}
    m = re.fullmatch(r"([fev])([1-9][0-9]*)", sub)
    if m:
        items = {"f": part.shape.faces, "e": part.shape.edges, "v": part.shape.vertices}[m.group(1)]()
        i = int(m.group(2))
        if i > len(items):
            raise WorkerError("not_found", f"{ref}: part {name} has {len(items)} of those")
        return items[i - 1], {"ref": ref, "resolved": f"{name}:{sub}"}
    sign = 1.0 if sub[0] == "+" else -1.0
    axis = {"x": 0, "y": 1, "z": 2}[sub[1]]
    want = np.zeros(3)
    want[axis] = sign
    best = None
    for n, f in enumerate(part.shape.faces(), 1):
        if f.geom_type != bd.GeomType.PLANE:
            continue
        nrm = _np(f.normal_at())
        if np.dot(nrm, want) < math.cos(math.radians(1.0)):
            continue
        key = sign * _np(f.center(bd.CenterOf.MASS))[axis]
        if best is None or key > best[0] + 1e-9:
            best = (key, n, f)
    if best is None:
        raise WorkerError("not_found", f"{ref}: no planar face of {name} faces {sub}")
    return best[2], {"ref": ref, "resolved": f"{name}:{geom.face_ref(best[1])}"}


def _describe(shape, info: dict) -> tuple[dict, object | None]:
    """The ref record and the direction used for angles (a planar normal, a line direction or a cylinder axis)."""
    import build123d as bd
    rec = dict(info)
    rec["type"] = type(shape).__name__
    direction = None
    if isinstance(shape, bd.Face):
        rec["surface"] = _gtype(shape)
        rec["area_mm2"] = float(shape.area)
        rec["center_mm"] = _v(shape.center(bd.CenterOf.MASS))
        if shape.geom_type == bd.GeomType.PLANE:
            rec["normal"] = _v(shape.normal_at())
            direction = _np(shape.normal_at())
        elif shape.geom_type == bd.GeomType.CYLINDER:
            direction = _np(shape.axis_of_rotation.direction)
    elif isinstance(shape, bd.Edge):
        rec["surface"] = _gtype(shape)
        rec["center_mm"] = _v(shape.center())
        if shape.geom_type == bd.GeomType.LINE:
            direction = _np(shape.tangent_at(0))
    elif isinstance(shape, bd.Vertex):
        rec["center_mm"] = [float(shape.X), float(shape.Y), float(shape.Z)]
    return rec, direction


def check_measure(loaded, parts, args, ctx) -> dict:
    import numpy as np
    spec = args.get("measure") or {}
    a, ia = _resolve(str(spec.get("a")), loaded)
    b, ib = _resolve(str(spec.get("b")), loaded)
    if isinstance(a, geom.LoadedPart) or isinstance(b, geom.LoadedPart):
        if not (isinstance(a, geom.LoadedPart) and isinstance(b, geom.LoadedPart)):
            raise WorkerError("usage", "a mesh part can be measured only against another whole mesh part")
        import trimesh
        pts_a = np.asarray(a.mesh.vertices)
        pts_b = np.asarray(b.mesh.vertices)
        cb, db, _ = trimesh.proximity.closest_point(b.mesh, pts_a)
        ca, da, _ = trimesh.proximity.closest_point(a.mesh, pts_b)
        if db.min() <= da.min():
            k = int(np.argmin(db))
            dist, pa, pb = float(db[k]), pts_a[k], cb[k]
        else:
            k = int(np.argmin(da))
            dist, pa, pb = float(da[k]), ca[k], pts_b[k]
        return {"method": "vertex-to-surface closest points between the meshes (trimesh.proximity)",
                "a": dict(ia, type="mesh"), "b": dict(ib, type="mesh"), "distance_mm": dist,
                "closest_points_mm": [[float(x) for x in pa], [float(x) for x in pb]], "angle_deg": None,
                "parallel": None}
    ra, da_ = _describe(a, ia)
    rb, db_ = _describe(b, ib)
    dist, pa, pb = a.distance_to_with_closest_points(b)
    angle = None
    parallel = None
    if da_ is not None and db_ is not None:
        c = abs(float(np.dot(da_ / np.linalg.norm(da_), db_ / np.linalg.norm(db_))))
        angle = math.degrees(math.acos(min(1.0, c)))
        parallel = angle < 1e-6
    return {"method": "BRepExtrema distance and closest points; the angle is between planar normals, line directions "
                      "or cylinder axes (0 to 90 deg)",
            "a": ra, "b": rb, "distance_mm": float(dist), "closest_points_mm": [_v(pa), _v(pb)],
            "angle_deg": angle, "parallel": parallel}


# --- design space -----------------------------------------------------------------------------------------------------

def check_design_space(loaded, parts, args, ctx) -> dict:
    import numpy as np
    import build123d as bd
    if any(p.kind == "mesh" for p in parts):
        return {"method": "cell centres", **MESH_SKIP}
    solids = [s for p in parts if p.kind == "solid" for s in p.shape.solids()]
    if not solids:
        raise WorkerError("result", "the design space needs a solid")
    fused = solids[0] if len(solids) == 1 else solids[0].fuse(*solids[1:])
    fused_solids = fused.solids() if hasattr(fused, "solids") else [fused]
    single = len(fused_solids) == 1
    vpart = geom.LoadedPart(index=0, name="design_space", kind="solid", shape=fused, mesh=None)
    valid = validity_of(vpart, ctx)
    gen = float(args.get("gen_mm") or 4.0)
    b = geom.bbox(fused)
    size = [b[i + 3] - b[i] for i in range(3)]
    cells = [max(1, math.ceil(s / gen - 1e-9)) for s in size]
    total = cells[0] * cells[1] * cells[2]
    out = {"method": "cell centres at gen_mm from the bounding-box minimum, tested with trimesh contains on the "
                     "tessellated solid; void boxes = z-runs of empty cells per (i, j) column",
           "single_solid": single, "valid": valid["reasons"], "bbox_mm": b, "envelope_mm": size, "gen_mm": gen,
           "cells": cells, "void_box_limit": VOID_BOX_LIMIT,
           "notes": ["golden subtracts keep-in regions, so its count can only be lower",
                     "support and load boxes must sit on the gen_mm grid from the bbox origin"]}
    env = []
    for n, f in enumerate(fused.faces(), 1):
        if f.geom_type != bd.GeomType.PLANE or len(env) >= 12:
            continue
        fb = geom.bbox(f)
        for ax, label in enumerate("xyz"):
            for side, val in (("min", b[ax]), ("max", b[ax + 3])):
                if abs(fb[ax] - val) <= 1e-6 and abs(fb[ax + 3] - val) <= 1e-6:
                    env.append({"ref": geom.face_ref(n), "plane": f"{label}={side}", "area_mm2": float(f.area)})
    out["envelope_faces"] = env[:12]
    if total > MAX_CELLS:
        out.update(solid_fraction=None, void_boxes_upper_bound=None, ok=False,
                   reason=f"{total} cells at gen_mm {gen} is more than {MAX_CELLS}: use a coarser gen_mm")
        return out
    mesh = geom.to_trimesh(vpart, geom.render_tolerance(fused), 0.2)
    axes = [b[i] + (np.arange(cells[i]) + 0.5) * gen for i in range(3)]
    gx, gy, gz = np.meshgrid(*axes, indexing="ij")
    pts = np.column_stack([gx.ravel(), gy.ravel(), gz.ravel()])
    inside = np.zeros(len(pts), dtype=bool)
    for start in range(0, len(pts), 200_000):
        inside[start:start + 200_000] = mesh.contains(pts[start:start + 200_000])
    solid = inside.reshape(cells)
    void = ~solid
    prev = np.concatenate([np.zeros(cells[:2] + [1], dtype=bool), void[:, :, :-1]], axis=2)
    runs = int((void & ~prev).sum())
    out.update(solid_fraction=float(solid.mean()), void_boxes_upper_bound=runs,
               ok=bool(single and valid["ok"] and runs <= VOID_BOX_LIMIT))
    return out


CHECKS = {"mass": check_mass, "validity": check_validity, "topology": check_topology, "holes": check_holes,
          "thickness": check_thickness, "draft": check_draft, "radii": check_radii,
          "interference": check_interference, "section": check_section, "measure": check_measure,
          "design_space": check_design_space}


def op_inspect(args: dict, ctx: Ctx) -> dict:
    """Run the requested checks on the run's outputs; a failing check records its error and the others run."""
    if ctx.run_dir is None:
        raise WorkerError("internal", "the inspect job has no run directory")
    loaded = geom.load(ctx.run_dir / "out")
    parts = geom.select(loaded, args.get("part"))
    out: dict = {}
    for name in args.get("checks") or ["mass", "validity", "topology"]:
        fn = CHECKS.get(name)
        if fn is None:
            out[name] = {"error": {"kind": "usage", "message": f"unknown check {name!r:.40}"}}
            continue
        try:
            out[name] = fn(loaded, parts, args, ctx)
        except WorkerError as e:
            if e.kind == "memory":
                raise
            out[name] = {"error": {"kind": e.kind, "message": e.message}}
        except MemoryError:
            raise
        except Exception as e:  # noqa: BLE001 (one failing check does not stop the others)
            kind = "kernel" if type(e).__module__.startswith("OCP") or "Standard_" in str(e) else "internal"
            out[name] = {"error": {"kind": kind, "message": f"{type(e).__name__}: {str(e)[:1000]}"}}
    return {"checks": out, "parts": [p.name for p in parts]}
