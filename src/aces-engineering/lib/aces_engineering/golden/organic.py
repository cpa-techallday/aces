"""aces golden: the organic skin. A SIMP density becomes one smooth implicit body, never a voxel staircase.

skin() turns the generator's density into a signed-distance field on a finer grid (section 10.3): EDT distances,
Gaussian smoothing, islands not connected to a functional interface removed, the field clipped to the design space,
an optional gyroid sheet core inside a shell (a TPMS lattice, bone-like), keep-ins blended in with smooth unions so the
feet and the cup grow out of the part rather than sit on it, then marching cubes with Taubin smoothing. Exact zeros of
the field are clamped away from the iso value before meshing (they made non-manifold meshes), and the result is
classified by signed component volume: components with positive volume are solids, negative ones enclosed cavities.

The primitives are vectorised and float32 in, float32 out. P is an (..., 3) array of points in the part frame (mm);
SDFs are negative inside."""
from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np

from . import SCHEMA
from .spec import param_values

AXES = "xyz"
ZERO_CLAMP = 1e-4  # x field spacing


def _f32(a) -> np.ndarray:
    return np.asarray(a, dtype=np.float32)


# ------------------------------------------------------------------------------------------------------- primitives

def smin(a, b, k):
    """Polynomial smooth minimum (a smooth union of two SDFs): min(a, b) - max(k - |a - b|, 0)^2 / (4k); k <= 0 is min."""
    a, b = _f32(a), _f32(b)
    k = float(k)
    if k <= 0.0:
        return np.minimum(a, b)
    h = np.maximum(np.float32(k) - np.abs(a - b), np.float32(0.0))
    return (np.minimum(a, b) - h * h / np.float32(4.0 * k)).astype(np.float32, copy=False)


def smax(a, b, k):
    """Smooth maximum (a smooth intersection): -smin(-a, -b, k)."""
    return -smin(-_f32(a), -_f32(b), k)


def sdf_box(P, box, round_mm=0.0):
    """Signed distance to an axis-aligned box [x0, y0, z0, x1, y1, z1], edges rounded by round_mm (outer dimensions
    unchanged)."""
    P = _f32(P)
    lo = np.asarray(box[:3], dtype=np.float64)
    hi = np.asarray(box[3:], dtype=np.float64)
    r = float(round_mm)
    c = ((lo + hi) / 2.0).astype(np.float32)
    half = ((hi - lo) / 2.0 - r).astype(np.float32)
    q = np.abs(P - c) - half
    out = np.linalg.norm(np.maximum(q, np.float32(0.0)), axis=-1) + np.minimum(q.max(axis=-1), np.float32(0.0)) - np.float32(r)
    return out.astype(np.float32, copy=False)


def sdf_cyl(P, axis, center, r, span, round_mm=0.0):
    """Signed distance to a cylinder along `axis` (center = the other two axes in order, span along the axis), its rims
    rounded by round_mm; r and span are the outer dimensions."""
    P = _f32(P)
    a = AXES.index(axis)
    o = [i for i in range(3) if i != a]
    rho = float(round_mm)
    dr = np.sqrt((P[..., o[0]] - np.float32(center[0])) ** 2 + (P[..., o[1]] - np.float32(center[1])) ** 2)
    mid = np.float32((float(span[0]) + float(span[1])) / 2.0)
    half = np.float32((float(span[1]) - float(span[0])) / 2.0 - rho)
    q0 = dr - np.float32(float(r) - rho)
    q1 = np.abs(P[..., a] - mid) - half
    zero = np.float32(0.0)
    out = (np.sqrt(np.maximum(q0, zero) ** 2 + np.maximum(q1, zero) ** 2) + np.minimum(np.maximum(q0, q1), zero)
           - np.float32(rho))
    return out.astype(np.float32, copy=False)


def sdf_region(P, region: dict):
    """SDF of a spec region (keep-in or keep-out): its box or its cylinder, with its round_mm."""
    rnd = float(region.get("round_mm") or 0.0)
    if region.get("box") is not None:
        return sdf_box(P, region["box"], rnd)
    c = region["cyl"]
    return sdf_cyl(P, c["axis"], c["center"], c["r"], c["span"], rnd)


def gyroid_sheet_sdf(P, cell_mm: float, wall_mm: float, origin):
    """Approximate signed distance to a gyroid sheet (a TPMS) of period cell_mm and wall thickness wall_mm:
    G = sin X cos Y + sin Y cos Z + sin Z cos X with X = 2 pi (P - origin) / cell, divided by |grad G| (floored at
    0.1 w), minus half the wall."""
    P = _f32(P)
    w = np.float32(2.0 * math.pi / float(cell_mm))
    X = (P - _f32(origin)) * w
    sx, cx = np.sin(X[..., 0]), np.cos(X[..., 0])
    sy, cy = np.sin(X[..., 1]), np.cos(X[..., 1])
    sz, cz = np.sin(X[..., 2]), np.cos(X[..., 2])
    G = sx * cy + sy * cz + sz * cx
    gx = cx * cy - sz * sx
    gy = cy * cz - sx * sy
    gz = cz * cx - sy * sz
    gn = w * np.sqrt(gx * gx + gy * gy + gz * gz)
    d = G / np.maximum(gn, np.float32(0.1) * w)
    return (np.abs(d) - np.float32(float(wall_mm) / 2.0)).astype(np.float32, copy=False)


def zero_clamp(sd, hf: float) -> np.ndarray:
    """Move every |sd| < 1e-4 hf to +1e-4 hf: values exactly at (or a rounding error from) the iso value make marching
    cubes emit degenerate, non-manifold faces."""
    eps = np.float32(ZERO_CLAMP * float(hf))
    sd = _f32(sd)
    return np.where(np.abs(sd) < eps, eps, sd).astype(np.float32, copy=False)


def grid_points(shape, h: float, origin) -> np.ndarray:
    """Cell centres origin + (idx + 0.5) * h of a grid, as an (nx, ny, nz, 3) float32 array."""
    P = np.moveaxis(np.indices(tuple(int(n) for n in shape), dtype=np.float32), 0, -1)
    P += np.float32(0.5)
    P *= np.float32(h)
    P += _f32(origin)
    return P


# -------------------------------------------------------------------------------------------------------- mesh stats

def mesh_stats(mesh) -> dict:
    """Topology of a closed mesh: components by signed volume (solids > 0, enclosed cavities < 0), Euler number and
    genus, volume, area, bbox."""
    nf = int(len(mesh.faces))
    if nf == 0:
        return {"faces": 0, "vertices": int(len(mesh.vertices)), "watertight": False, "solids": 0, "cavities": 0,
                "cavity_volume_mm3": 0.0, "euler": None, "genus": None, "mesh_volume_mm3": 0.0, "area_mm2": 0.0,
                "bbox_mm": None}
    vols = [float(p.volume) for p in mesh.split(only_watertight=False)]
    solids = sum(1 for v in vols if v > 0)
    cavities = sum(1 for v in vols if v < 0)
    euler = int(mesh.euler_number)
    lo, hi = mesh.bounds
    return {"faces": nf, "vertices": int(len(mesh.vertices)), "watertight": bool(mesh.is_watertight),
            "solids": solids, "cavities": cavities, "cavity_volume_mm3": float(-sum(v for v in vols if v < 0)),
            "euler": euler, "genus": max(0, (2 * (solids + cavities) - euler) // 2),
            "mesh_volume_mm3": float(mesh.volume), "area_mm2": float(mesh.area),
            "bbox_mm": [float(v) for v in lo] + [float(v) for v in hi]}


# -------------------------------------------------------------------------------------------------------------- skin

def _info(eff: dict, params: dict, hf: float) -> dict:
    sk = eff["generator"]["skin"]
    infill = eff["generator"]["infill"]
    inf = None
    if infill is not None:
        p = param_values(eff, params)
        inf = {"kind": infill["kind"], "cell_mm": p.get("cell_mm"), "wall_mm": p.get("wall_mm"),
               "shell_mm": p.get("shell_mm"), "blend_mm": float(infill["blend_mm"])}
    return {"schema": SCHEMA["skin"], "status": "empty", "message": "", "field_h_mm": hf, "upsample": int(sk["upsample"]),
            "sigma_vox": float(sk["sigma_vox"]), "blend_mm": float(sk["blend_mm"]),
            "clip_blend_mm": float(sk["clip_blend_mm"]), "taubin_iters": int(sk["taubin_iters"]), "iso": float(sk["iso"]),
            "clipped_to_domain": False, "infill": inf, "faces": 0, "vertices": 0, "watertight": False, "solids": 0,
            "cavities": 0, "cavity_volume_mm3": 0.0, "islands_removed": 0, "removed_volume_mm3": 0.0, "euler": None,
            "genus": None, "mesh_volume_mm3": 0.0, "area_mm2": 0.0, "bbox_mm": None, "wall_s": None}


def _domain_field(domain: dict):
    if domain.get("_sdf_field") is not None:
        return np.asarray(domain["_sdf_field"], dtype=np.float32)
    rel = domain.get("sdf_field_path")
    if not rel:
        return None
    p = Path(rel)
    if not p.is_absolute():
        p = Path(domain.get("_root") or ".") / p
    return np.load(p).astype(np.float32)


def skin(density, domain: dict, eff: dict, params: dict) -> tuple[object | None, dict]:
    """The organic skin of one SIMP density (section 10.3). Returns (trimesh.Trimesh or None, skin.json content).
    Status "ok" means one watertight solid; "empty", "not-watertight" and "multi-body" are design failures (the mesh
    is still returned when there is one, for diagnostics)."""
    import scipy.ndimage as nd
    import trimesh
    from gendesign.export import density_to_mesh

    t0 = time.time()
    d = eff["derived"]
    sk = eff["generator"]["skin"]
    infill = eff["generator"]["infill"]
    p = param_values(eff, params)
    up = int(sk["upsample"])
    hf = float(d["gen_mm"]) / up
    origin = np.asarray(d["grid"]["origin_mm"], dtype=np.float64)
    info = _info(eff, params, hf)

    def done(status: str, message: str, mesh=None):
        info["status"], info["message"] = status, message
        info["wall_s"] = time.time() - t0
        return mesh, info

    # 1-2: upsample, signed distance (mm, negative inside), smoothing
    fine = nd.zoom(np.asarray(density, dtype=np.float32), up, order=1, grid_mode=True, mode="nearest")
    inside = fine >= np.float32(sk["iso"])
    if not inside.any():
        return done("empty", f"the density never reaches the iso level {sk['iso']:g}")
    if inside.all():
        sd = np.full(fine.shape, -hf, dtype=np.float32)
    else:
        sd = (nd.distance_transform_edt(~inside, sampling=hf) - nd.distance_transform_edt(inside, sampling=hf)).astype(np.float32)
    if float(sk["sigma_vox"]) > 0:
        sd = nd.gaussian_filter(sd, sigma=float(sk["sigma_vox"])).astype(np.float32)
    P = grid_points(fine.shape, hf, origin)

    # 3: islands, by connectivity to an interface (a keep-in, or within one field cell of a support or load patch)
    keep_regions = list(domain.get("keep_in") or []) + list(domain.get("patch_keep_ins") or [])
    anchor = np.zeros(fine.shape, dtype=bool)
    for r in keep_regions:
        anchor |= sdf_region(P, r) <= 0.0
    for item in list(eff["supports"]) + list(eff["loads"]):
        anchor |= sdf_box(P, item["box"]) <= np.float32(hf)
    lab, n = nd.label(sd < 0, structure=np.ones((3, 3, 3), dtype=bool))
    if n:
        anchored = np.unique(lab[anchor & (lab > 0)])
        keep = np.zeros(n + 1, dtype=bool)
        keep[0] = True
        keep[anchored] = True
        remove = ~keep[lab]
        info["islands_removed"] = int(n - anchored.size)
        info["removed_volume_mm3"] = float(remove.sum()) * hf ** 3
        sd[remove] = np.float32(hf)

    # 4: clip to the design space
    field = _domain_field(domain)
    if field is not None:
        if field.shape != sd.shape:
            raise ValueError(f"the design-space field {field.shape} does not match the skin grid {sd.shape}")
        sd = smax(sd, field, float(sk["clip_blend_mm"]))
        info["clipped_to_domain"] = True

    # 5: gyroid sheet core inside a shell (bone-like)
    if infill is not None:
        shell_mm = np.float32(p["shell_mm"])
        shell = np.maximum(sd, -(sd + shell_mm))
        g = gyroid_sheet_sdf(P, p["cell_mm"], p["wall_mm"], origin)
        core = np.maximum(sd + shell_mm, g)
        sd = smin(shell, core, float(infill["blend_mm"]))

    # 6: keep-ins grow out of the part (smooth union, after the infill so they stay solid); keep-outs cut it
    if keep_regions:
        kmin = sdf_region(P, keep_regions[0])
        for r in keep_regions[1:]:
            kmin = np.minimum(kmin, sdf_region(P, r))
        sd = smin(sd, kmin, float(sk["blend_mm"]))
    for r in domain.get("keep_out") or []:
        sd = smax(sd, -sdf_region(P, r), float(sk["blend_mm"]) / 2.0)

    # 7: zero clamp (exact zeros at the iso value made non-manifold meshes)
    sd = zero_clamp(sd, hf)

    # 8: field -> marching cubes (zero padding closes the surface) -> Taubin
    fld = np.clip(np.float32(0.5) - sd / np.float32(2.0 * hf), 0.0, 1.0).astype(np.float32)
    try:
        verts, faces = density_to_mesh(fld, hf, 0.5, int(sk["taubin_iters"]))
    except ValueError:
        return done("empty", "the implicit field has no solid after clipping and blending")
    mesh = trimesh.Trimesh(np.asarray(verts, dtype=np.float64) + origin, np.asarray(faces), process=True)
    if len(mesh.faces) and mesh.volume < 0:
        mesh.invert()

    # 9: validity and topology
    if len(mesh.faces) and not mesh.is_watertight:
        mesh.merge_vertices()
        mesh.fix_normals()
        if mesh.is_watertight and mesh.volume < 0:
            mesh.invert()
    info.update(mesh_stats(mesh))
    if info["faces"] == 0:
        return done("empty", "marching cubes produced no faces")
    if not info["watertight"]:
        return done("not-watertight", "the skin is not watertight after merging vertices; it is never voxelised "
                    "(the voxel fallback over-fills open meshes)", mesh)
    if info["solids"] != 1:
        return done("multi-body", f"the skin has {info['solids']} solid bodies; a part is one body", mesh)
    return done("ok", "", mesh)
