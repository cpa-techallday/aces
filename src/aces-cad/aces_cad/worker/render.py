"""Worker op `render`: PNG renders of a run in the ACES dark look (spec section 13), in a job that runs no agent code.

The look is the golden path's: STYLE below is aces_engineering/golden/render.py's STYLE copied verbatim (aces-cad never
imports golden/*, which the golden-rig work owns). A navy ground, the part in chalk with Lambert shading, a soft
specular and a cyan rim where the surface turns away from the eye, named datum faces tinted amber, no axes, an
orthographic camera, and a crop that makes the part fill 80 % of the frame. Matplotlib Agg only.

B-rep parts are tessellated with geom.tessellate at geom.render_tolerance and 0.2 rad, the tolerance doubled until
the scene holds at most 200,000 triangles (at most six doublings, recorded). Normals are averaged per B-rep face, so
curved faces shade smooth and edges between faces stay crisp. Large triangles are subdivided for display only, so the
painter's-algorithm depth sort of matplotlib's 3-D axes stays right; the geometry is unchanged. Mesh parts are drawn
as returned, unless they would push the scene over 200,000 triangles: then they are decimated for display by
vertex clustering (the tessellation record and the caption say so).
"""
from __future__ import annotations

import math

from .main import WorkerError

STYLE = {
    "background": "#061225",       # sky: the ground
    "part": "#dbe7f3",             # chalk: the computed part
    "rim": "#22d3ee",              # live cyan: silhouette rim
    "interface": "#f59e0b",        # amber: preserved interfaces (keep-ins)
    "fault": "#ff4d2e",            # red-orange: failed designs
    "muted": "#8aa0b8",            # captions
    "excluded": "#5d6b7d",         # grey: zones the constrained stress ignores
    "stress_ramp": ("#1d3b6a", "#22d3ee", "#dbe7f3", "#f59e0b", "#ff4d2e"),
    "font": "monospace",
}
FILL = 0.80
ISO = (24, -45)
VIEWS = {"iso": ISO, "front": (0, -90), "back": (0, 90), "right": (0, 0), "left": (0, 180), "top": (90, -90),
         "bottom": (-90, -90), "cutaway": ISO, "thickness": ISO}
SIZES = {"small": (640, 500), "medium": (1024, 800), "large": (1600, 1250)}
DPI = 100
ANGULAR_RAD = 0.2
MAX_TRIANGLES = 200_000
MAX_DOUBLINGS = 6
MAX_DISPLAY_TRIANGLES = 150_000
MAX_BISECTIONS = 24
DATUM_TINT = 0.70
THICKNESS_LABEL = "wall thickness (mm), inward ray, preview"
THICKNESS_SUBDIVISION = 40.0   # display edge length for the thickness map: bbox diagonal / 40
THICKNESS_BUDGET = 60_000       # at most this many inward rays per thickness render
MIN_THICKNESS_SPAN = 0.10


def _rgb(h: str):
    import numpy as np

    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)])


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def _view_vectors(view):
    """The unit vector toward the eye and the light direction for matplotlib's (elev, azim)."""
    import numpy as np

    elev, azim = math.radians(view[0]), math.radians(view[1])
    v = np.array([math.cos(elev) * math.cos(azim), math.cos(elev) * math.sin(azim), math.sin(elev)])
    side = np.cross(v, [0.0, 0.0, 1.0])
    side = side / (np.linalg.norm(side) or 1.0)
    light = 0.45 * v + 0.75 * np.array([0.0, 0.0, 1.0]) + 0.45 * side
    return v / np.linalg.norm(v), light / np.linalg.norm(light)


# --- the scene ----------------------------------------------------------------------------------------------------

class Scene:
    """Triangles of every selected part: V (N x 3), F (M x 3, 0-based), group (M,) one id per B-rep face (per mesh
    part for meshes), tint (M,) True on datum faces, overlay (M,) True for datum faces drawn on their own."""

    def __init__(self, V, F, group, tint, overlay, closed: bool):
        self.V, self.F, self.group, self.tint, self.overlay, self.closed = V, F, group, tint, overlay, closed

    @property
    def triangles(self) -> int:
        return int(len(self.F))


def _zero_based(V, F):
    """geom.tessellate's faces as 0-based vertex indices (a 1-based array has its largest index equal to N)."""
    import numpy as np

    F = np.asarray(F, dtype=np.int64).reshape(-1, 3)
    if F.size and int(F.max()) == len(V):
        F = F - 1
    if F.size and (int(F.min()) < 0 or int(F.max()) >= len(V)):
        raise WorkerError("internal", "geom.tessellate returned face indices outside its vertex list")
    return F


def _shape_of(parts):
    from build123d import Compound

    shapes = [p.shape for p in parts if p.shape is not None]
    if not shapes:
        return None
    return shapes[0] if len(shapes) == 1 else Compound(shapes)


def _datum_faces(datums: dict) -> list:
    out = []
    for name, shape in sorted((datums or {}).items()):
        try:
            if shape is None or shape.solids():
                continue
            for f in shape.faces():
                out.append((name, f))
        except AttributeError:
            continue
    return out


def _same_face(a, b, tol: float) -> bool:
    try:
        if str(a.geom_type) != str(b.geom_type):
            return False
        if abs(a.area - b.area) > max(1e-6, 1e-6 * abs(b.area)):
            return False
        return (a.center() - b.center()).length <= tol
    except Exception:  # noqa: BLE001 (a degenerate datum face simply matches nothing)
        return False


def _datum_mask(shape, fid, dfaces, geom):
    """True on the triangles of the part's B-rep faces that are geometrically one of the datum faces."""
    import numpy as np

    mask = np.zeros(len(fid), dtype=bool)
    if not dfaces or not len(fid):
        return mask
    faces = shape.faces()
    # face_index is 1-based (spec 5.3); a 0-based array never reaches len(faces)
    offset = 1 if int(fid.max()) == len(faces) or int(fid.min()) >= 1 else 0
    b = geom.bbox(shape)
    tol = 1e-6 * max(1e-9, math.dist(b[:3], b[3:])) + 1e-9
    for _name, df in dfaces:
        for i, f in enumerate(faces):
            if _same_face(df, f, tol):
                mask |= fid == i + offset
    return mask


def cluster_decimate(V, F, cell: float):
    """Display-only simplification of a mesh: vertices merged per grid cell of `cell` mm (their mean), collapsed and
    repeated triangles dropped. Returns (V, F)."""
    import numpy as np

    keys = np.floor(V / cell).astype(np.int64)
    _uniq, inv = np.unique(keys, axis=0, return_inverse=True)
    inv = np.asarray(inv).reshape(-1)
    counts = np.bincount(inv)
    V2 = np.zeros((len(counts), 3))
    np.add.at(V2, inv, V)
    V2 /= counts[:, None]
    F2 = inv[F]
    keep = (F2[:, 0] != F2[:, 1]) & (F2[:, 1] != F2[:, 2]) & (F2[:, 0] != F2[:, 2])
    F2 = F2[keep]
    if len(F2):
        _u, first = np.unique(np.sort(F2, axis=1), axis=0, return_index=True)
        F2 = F2[np.sort(first)]
    return V2, F2


def _decimate_meshes(parts, items, budget: int, record: dict) -> None:
    """Shrink the mesh parts' triangles (in place in items) to fit `budget`, by vertex clustering at a growing
    cell."""
    import numpy as np

    mesh_idx = [i for i, p in enumerate(parts) if p.mesh is not None]
    total = sum(len(items[i][1]) for i in mesh_idx)
    if not mesh_idx or total <= budget or budget <= 0:
        return
    lo = np.min([items[i][0].min(axis=0) for i in mesh_idx], axis=0)
    hi = np.max([items[i][0].max(axis=0) for i in mesh_idx], axis=0)
    cell = float(np.linalg.norm(hi - lo)) / 400.0 or 1e-3
    out = []
    for _ in range(12):
        out = [cluster_decimate(items[i][0], items[i][1], cell) for i in mesh_idx]
        if sum(len(f) for _v, f in out) <= budget:
            break
        cell *= 1.5
    for i, (v, f) in zip(mesh_idx, out):
        items[i][0], items[i][1] = v, f
        items[i][2] = np.full(len(f), int(items[i][2][0]) if len(items[i][2]) else 0, dtype=np.int64)
        items[i][3] = np.zeros(len(f), dtype=bool)
    record["mesh_decimation"] = {"method": "vertex clustering, for display only", "cell_mm": cell,
                                 "triangles_before": int(total), "triangles_after": int(sum(len(f) for _v, f in out))}


def _tessellate_all(parts, dfaces, geom, tol):
    """Per part: (V, F with 0-based local indices, group per triangle, datum tint per triangle) and whether every
    part is closed. Group ids are unique across parts: one per B-rep face, one per mesh part."""
    import numpy as np

    items = []
    gbase = 0
    closed = True
    for p in parts:
        if p.mesh is not None:
            V = np.asarray(p.mesh.vertices, dtype=np.float64)
            F = np.asarray(p.mesh.faces, dtype=np.int64)
            G = np.full(len(F), gbase, dtype=np.int64)
            T = np.zeros(len(F), dtype=bool)
            closed = closed and bool(p.mesh.is_watertight)
        else:
            V, F, fid = geom.tessellate(p.shape, tol, ANGULAR_RAD)
            V = np.asarray(V, dtype=np.float64).reshape(-1, 3)
            F = _zero_based(V, F)
            fid = np.asarray(fid, dtype=np.int64).reshape(-1)
            if len(fid) != len(F):
                raise WorkerError("internal", "geom.tessellate returned a face index per triangle count mismatch")
            G = gbase + fid - (int(fid.min()) if len(fid) else 0)
            T = _datum_mask(p.shape, fid, dfaces, geom)
            closed = closed and p.kind != "sketch"
        items.append([V, F, G, T])
        gbase = (int(G.max()) + 1) if len(G) else gbase + 1
    return items, gbase, closed


def build_scene(parts, datums, geom, warnings: list) -> tuple:
    """(Scene, tessellation record). The B-rep tolerance is doubled (at most six times) until the scene has at most
    MAX_TRIANGLES; mesh parts over budget are decimated for display by vertex clustering (recorded)."""
    import numpy as np

    whole = _shape_of(parts)
    tol0 = float(geom.render_tolerance(whole)) if whole is not None else None
    dfaces = _datum_faces(datums) if whole is not None else []
    tol = tol0
    doublings = 0
    extra: dict = {}
    while True:
        items, gbase, closed = _tessellate_all(parts, dfaces, geom, tol)
        brep_total = sum(len(it[1]) for it, p in zip(items, parts) if p.mesh is None)
        total = sum(len(it[1]) for it in items)
        if total <= MAX_TRIANGLES or tol is None or doublings >= MAX_DOUBLINGS or brep_total <= MAX_TRIANGLES // 2:
            break
        tol *= 2.0
        doublings += 1
    if total > MAX_TRIANGLES:
        _decimate_meshes(parts, items, MAX_TRIANGLES - brep_total, extra)
        total = sum(len(it[1]) for it in items)
    if total > MAX_TRIANGLES:
        raise WorkerError("result", f"the render needs {total} triangles, more than {MAX_TRIANGLES} even after "
                                    f"{doublings} doublings of the display tolerance: render one part (part) or "
                                    f"simplify the geometry")
    if total == 0:
        raise WorkerError("result", "nothing to render: the tessellation produced no triangles")
    matched = any(bool(np.any(it[3])) for it in items)
    overlay = [False] * len(items)
    if dfaces and not matched:
        for _name, df in dfaces:
            V, F, _fid = geom.tessellate(df, tol, ANGULAR_RAD)
            V = np.asarray(V, dtype=np.float64).reshape(-1, 3)
            F = _zero_based(V, F)
            items.append([V, F, np.full(len(F), gbase, dtype=np.int64), np.ones(len(F), dtype=bool)])
            overlay.append(True)
            gbase += 1
        warnings.append("datum faces that are not faces of the part are drawn on their own in amber")
    Vs, Fs, Gs, Ts, Os = [], [], [], [], []
    nv = 0
    for (V, F, G, T), over in zip(items, overlay):
        Vs.append(V)
        Fs.append(F + nv)
        Gs.append(G)
        Ts.append(T)
        Os.append(np.full(len(F), over, dtype=bool))
        nv += len(V)
    scene = Scene(np.concatenate(Vs), np.concatenate(Fs), np.concatenate(Gs), np.concatenate(Ts),
                  np.concatenate(Os), closed)
    record = {"tolerance_mm": tol, "angular_rad": ANGULAR_RAD, "triangles": scene.triangles,
              "doublings": doublings, "initial_tolerance_mm": tol0}
    record.update(extra)
    return scene, record


def corner_normals(V, F, group):
    """Per-corner normals averaged over the triangles of the same group that share the vertex (M x 3 x 3)."""
    import numpy as np

    tri = V[F]
    fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])          # area-weighted face normals
    ng = int(group.max()) + 1 if group.size else 1
    key = (F.astype(np.int64) * ng + group[:, None]).ravel()
    uniq, inv = np.unique(key, return_inverse=True)
    acc = np.zeros((len(uniq), 3))
    np.add.at(acc, inv, np.repeat(fn, 3, axis=0))
    n = acc[inv].reshape(-1, 3, 3)
    length = np.linalg.norm(n, axis=2, keepdims=True)
    unit_fn = fn / np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-300)
    n = np.where(length > 1e-300, n / np.maximum(length, 1e-300), unit_fn[:, None, :])
    return n, unit_fn


def shade(normals, base, view, two_sided: bool):
    """Lambert + soft specular + cyan rim per corner, averaged to faces (the golden formula)."""
    import numpy as np

    v, light = _view_vectors(view)
    n = normals
    if two_sided:
        n = n * np.where((n @ v) < 0, -1.0, 1.0)[..., None]
    lam = np.clip(n @ light, 0.0, 1.0)
    half = (light + v) / np.linalg.norm(light + v)
    spec = np.clip(n @ half, 0.0, 1.0) ** 32
    rim = np.clip(1.0 - np.abs(n @ v), 0.0, 1.0) ** 2.5
    col = (base[:, None, :] * (0.30 + 0.70 * lam[..., None]) + 0.22 * spec[..., None]
           + _rgb(STYLE["rim"])[None, None, :] * 0.45 * rim[..., None])
    return np.clip(col, 0.0, 1.0).mean(axis=1)


def subdivide(V, F, max_edge: float, budget: int = MAX_DISPLAY_TRIANGLES):
    """Display-only refinement: the longest edge of every triangle longer than max_edge is bisected (one triangle
    becomes two, winding kept), longest first while the budget lasts, so matplotlib's depth sort and the per-triangle
    thickness rays work on small triangles. Returns (V, F, index of the source triangle per new triangle). New
    midpoints are not shared (T-junctions are harmless for display); normals are interpolated, not recomputed."""
    import numpy as np

    V = np.asarray(V, dtype=np.float64)
    F = np.asarray(F, dtype=np.int64)
    index = np.arange(len(F), dtype=np.int64)
    if not len(F) or max_edge <= 0:
        return V, F, index
    for _ in range(MAX_BISECTIONS):
        tri = V[F]
        lengths = np.stack([np.linalg.norm(tri[:, 1] - tri[:, 2], axis=1),
                            np.linalg.norm(tri[:, 2] - tri[:, 0], axis=1),
                            np.linalg.norm(tri[:, 0] - tri[:, 1], axis=1)], axis=1)
        longest = lengths.max(axis=1)
        sel = np.nonzero(longest > max_edge)[0]
        room = budget - len(F)
        if not len(sel) or room <= 0:
            break
        if len(sel) > room:
            sel = sel[np.argsort(-longest[sel], kind="stable")[:room]]
        k = len(sel)
        j = lengths[sel].argmax(axis=1)                    # the corner opposite the longest edge
        rows = np.arange(k)
        a = F[sel, j]
        b = F[sel, (j + 1) % 3]
        c = F[sel, (j + 2) % 3]
        m = len(V) + rows
        V = np.concatenate([V, (V[b] + V[c]) / 2.0])
        F = F.copy()
        F[sel] = np.stack([a, b, m], axis=1)
        F = np.concatenate([F, np.stack([a, m, c], axis=1)])
        index = np.concatenate([index, index[sel]])
    return V, F, index


def interpolate_normals(V0, F0, N0, V, F, index):
    """Corner normals of the subdivided triangles, interpolated barycentrically from their source triangle's corner
    normals (M x 3 x 3), unit length."""
    import numpy as np

    A, B, C = (V0[F0[index, k]] for k in range(3))
    P = V[F]
    v0, v1 = B - A, C - A
    v2 = P - A[:, None, :]
    d00 = (v0 * v0).sum(axis=1)
    d01 = (v0 * v1).sum(axis=1)
    d11 = (v1 * v1).sum(axis=1)
    d20 = (v2 * v0[:, None, :]).sum(axis=2)
    d21 = (v2 * v1[:, None, :]).sum(axis=2)
    denom = d00 * d11 - d01 * d01
    ok = np.abs(denom) > 1e-300
    safe = np.where(ok, denom, 1.0)[:, None]
    bv = (d11[:, None] * d20 - d01[:, None] * d21) / safe
    bw = (d00[:, None] * d21 - d01[:, None] * d20) / safe
    bu = 1.0 - bv - bw
    src = N0[index]
    n = bu[..., None] * src[:, None, 0] + bv[..., None] * src[:, None, 1] + bw[..., None] * src[:, None, 2]
    n = np.where(ok[:, None, None], n, src.mean(axis=1, keepdims=True))
    length = np.linalg.norm(n, axis=2, keepdims=True)
    return n / np.maximum(length, 1e-300)


def face_normals(V, F):
    import numpy as np

    tri = V[F]
    fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    return fn / np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-300)


def cut_half(scene: Scene, view):
    """The half nearest the eye removed through the bbox centre (trimesh slice_faces_plane), per group."""
    import numpy as np
    from trimesh.intersections import slice_faces_plane

    v, _ = _view_vectors(view)
    vh = np.array([v[0], v[1], 0.0])
    vh = vh / (np.linalg.norm(vh) or 1.0)
    centre = (scene.V.min(axis=0) + scene.V.max(axis=0)) / 2.0
    Vs, Fs, Gs, Ts = [], [], [], []
    nv = 0
    order = np.argsort(scene.group, kind="stable")
    groups, starts = np.unique(scene.group[order], return_index=True)
    bounds = list(starts) + [len(order)]
    for gi in range(len(groups)):
        idx = order[bounds[gi]:bounds[gi + 1]]
        F = scene.F[idx]
        used, inv = np.unique(F.ravel(), return_inverse=True)
        Vsub = scene.V[used]
        Fsub = inv.reshape(-1, 3)
        res = slice_faces_plane(Vsub, Fsub, plane_normal=-vh, plane_origin=centre)
        V2, F2 = np.asarray(res[0], dtype=np.float64), np.asarray(res[1], dtype=np.int64)
        if not len(F2):
            continue
        Vs.append(V2)
        Fs.append(F2 + nv)
        Gs.append(np.full(len(F2), groups[gi], dtype=np.int64))
        Ts.append(np.full(len(F2), bool(scene.tint[idx][0])))
        nv += len(V2)
    if not Fs:
        return None
    return Scene(np.concatenate(Vs), np.concatenate(Fs), np.concatenate(Gs), np.concatenate(Ts),
                 np.zeros(sum(len(f) for f in Fs), dtype=bool), False)


# --- drawing --------------------------------------------------------------------------------------------------------

def draw(V, F, colours, view, size_px, dpi: int, cull_normals=None):
    """The triangles alone (no text) on the navy ground, orthographic; returns the RGB image."""
    import numpy as np
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    plt = _plt()
    bg = STYLE["background"]
    tris = V[F]
    if cull_normals is not None:
        v, _ = _view_vectors(view)
        front = (cull_normals @ v) > 1e-9
        tris, colours = tris[front], colours[front]
    w, h = size_px
    fig = plt.figure(figsize=(w / DPI, h / DPI), dpi=dpi, facecolor=bg)
    ax = fig.add_axes((0.0, 0.0, 1.0, 1.0), projection="3d", facecolor=bg)
    ax.set_proj_type("ortho")
    pc = Poly3DCollection(tris, facecolors=colours, edgecolors="none", linewidths=0.0, antialiased=False)
    ax.add_collection3d(pc)
    lo, hi = V.min(axis=0), V.max(axis=0)
    ext = np.maximum(hi - lo, 1e-9)
    ext = np.maximum(ext, 1e-3 * float(ext.max()))
    mid = (lo + hi) / 2.0
    ax.set_xlim(mid[0] - ext[0] / 2, mid[0] + ext[0] / 2)
    ax.set_ylim(mid[1] - ext[1] / 2, mid[1] + ext[1] / 2)
    ax.set_zlim(mid[2] - ext[2] / 2, mid[2] + ext[2] / 2)
    ax.set_box_aspect(tuple(float(e) for e in ext), zoom=1.0)
    ax.view_init(elev=view[0], azim=view[1])
    ax.set_axis_off()
    fig.canvas.draw()
    img = np.asarray(fig.canvas.buffer_rgba())[..., :3].copy()
    plt.close(fig)
    return img


def crop(img, aspect: float, fill: float = FILL):
    """Crop to the drawn part so it fills `fill` of the frame in its limiting dimension, at the aspect (w/h)."""
    import numpy as np

    bg = (_rgb(STYLE["background"]) * 255).round()
    diff = np.abs(img.astype(np.float64) - bg[None, None, :]).max(axis=-1) > 6
    if not diff.any():
        return img
    ys, xs = np.nonzero(diff)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    w, h = x1 - x0, y1 - y0
    W = max(w / fill, (h / fill) * aspect)
    H = W / aspect
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    X0, Y0 = int(round(cx - W / 2)), int(round(cy - H / 2))
    W, H = int(round(W)), int(round(H))
    pad = max(0, -X0, -Y0, X0 + W - img.shape[1], Y0 + H - img.shape[0])
    if pad:
        fillv = bg.astype(img.dtype)
        img = np.concatenate([np.broadcast_to(fillv, (pad, img.shape[1], 3)), img,
                              np.broadcast_to(fillv, (pad, img.shape[1], 3))], axis=0)
        side = np.broadcast_to(fillv, (img.shape[0], pad, 3))
        img = np.concatenate([side, img, side], axis=1)
        X0, Y0 = X0 + pad, Y0 + pad
    return img[Y0:Y0 + H, X0:X0 + W]


def _fit_font(text: str, base_pt: float, width_in: float, share: float = 0.95) -> float:
    """A monospace font size (pt) at most base_pt at which `text` fits `share` of the figure width."""
    if not text:
        return base_pt
    return max(4.0, min(base_pt, share * width_in * 72.0 / (0.66 * len(text))))


LAYOUT = {"top_sub": 0.88, "top": 0.91, "bottom_cap": 0.075, "bottom": 0.03, "right_bar": 0.86, "right": 0.985}


def _frame(subtitle: str, caption: str, cbar: bool) -> tuple:
    top = LAYOUT["top_sub"] if subtitle else LAYOUT["top"]
    bottom = LAYOUT["bottom_cap"] if caption else LAYOUT["bottom"]
    right = LAYOUT["right_bar"] if cbar else LAYOUT["right"]
    return top, bottom, right


def compose(img, png, *, title: str, subtitle: str, caption: str, size_px, cbar=None) -> None:
    """The cropped image under a title and subtitle and over a caption, at exactly size_px pixels."""
    plt = _plt()
    bg, muted, chalk, font = STYLE["background"], STYLE["muted"], STYLE["part"], STYLE["font"]
    w_in, h_in = size_px[0] / DPI, size_px[1] / DPI
    fig = plt.figure(figsize=(w_in, h_in), dpi=DPI, facecolor=bg)
    top, bottom, right = _frame(subtitle, caption, cbar is not None)
    ax = fig.add_axes((0.015, bottom, right - 0.015, top - bottom))
    ax.imshow(img, interpolation="antialiased")
    ax.set_axis_off()
    if title:
        fig.text(0.02, 0.975, title, color=chalk, fontsize=_fit_font(title, 11.0, w_in), family=font, va="top")
    if subtitle:
        fig.text(0.02, 0.928, subtitle, color=muted, fontsize=_fit_font(subtitle, 8.2, w_in), family=font,
                 va="top")
    if caption:
        fig.text(0.02, 0.022, caption, color=muted, fontsize=_fit_font(caption, 7.4, w_in), family=font,
                 va="bottom")
    if cbar is not None:
        from matplotlib.cm import ScalarMappable
        from matplotlib.colors import Normalize

        cmap, clim, label = cbar
        cax = fig.add_axes((0.885, bottom + 0.08, 0.022, (top - bottom) - 0.16))
        cb = fig.colorbar(ScalarMappable(norm=Normalize(vmin=clim[0], vmax=clim[1]), cmap=cmap), cax=cax)
        cb.set_label(label, color=muted, fontsize=6.5, family=font)
        cb.ax.tick_params(colors=muted, labelsize=6.5)
        cb.outline.set_edgecolor(muted)
    fig.savefig(str(png), dpi=DPI, facecolor=bg)
    plt.close(fig)


def thickness_map(V, F):
    """checks.thickness_per_triangle on the display triangles (one inward ray from each centroid)."""
    import numpy as np
    import trimesh

    from .checks import thickness_per_triangle

    mesh = trimesh.Trimesh(vertices=V, faces=F, process=False)
    values = np.asarray(thickness_per_triangle(mesh), dtype=np.float64).reshape(-1)
    if values.shape[0] != len(F):
        raise WorkerError("internal", "thickness_per_triangle returned a value count that does not match the triangles")
    return values


def _thickness_cmap():
    from matplotlib.colors import LinearSegmentedColormap

    return LinearSegmentedColormap.from_list("aces-thickness", [_rgb(c) for c in reversed(STYLE["stress_ramp"])])


# --- the op ---------------------------------------------------------------------------------------------------------

def _text(value, limit: int = 200) -> str:
    return "".join(ch for ch in str(value) if ch.isprintable())[:limit]


def _summary(parts, geom) -> dict:
    vols, valid, boxes = [], True, []
    sketch = False
    for p in parts:
        s = geom.summarize(p)
        ok, _reasons = geom.quick_validity(p)
        valid = valid and bool(ok)
        if s.get("volume_mm3") is None:
            sketch = True
        else:
            vols.append(float(s["volume_mm3"]))
        if s.get("bbox_mm"):
            boxes.append([float(x) for x in s["bbox_mm"]])
    box = None
    if boxes:
        box = [min(b[0] for b in boxes), min(b[1] for b in boxes), min(b[2] for b in boxes),
               max(b[3] for b in boxes), max(b[4] for b in boxes), max(b[5] for b in boxes)]
    return {"volume_mm3": None if sketch or not vols else sum(vols), "bbox_mm": box, "valid": valid}


def _subtitle(summary: dict, view: str) -> str:
    vol = summary["volume_mm3"]
    vtxt = f"volume {vol:.2f} mm\u00b3" if vol is not None else "volume n/a (no closed solid)"
    b = summary["bbox_mm"]
    btxt = (f"bbox {b[3] - b[0]:.4g} \u00d7 {b[4] - b[1]:.4g} \u00d7 {b[5] - b[2]:.4g} mm" if b else "bbox n/a")
    return f"{vtxt} \u00b7 {btxt} \u00b7 valid {'yes' if summary['valid'] else 'no'} \u00b7 {view}"


def _caption(record: dict, mesh_only: bool) -> str:
    dec = record.get("mesh_decimation")
    if mesh_only:
        what = ("chalk: the mesh as the script returned it" if not dec else
                f"chalk: the mesh, decimated for display by vertex clustering at {dec['cell_mm']:.3g} mm")
    else:
        what = f"chalk: B-rep tessellated at {record['tolerance_mm']:.3g} mm for display"
        if dec:
            what += f", meshes clustered at {dec['cell_mm']:.3g} mm"
    return (f"designed in build123d (aces-cad) \u00b7 {what} \u00b7 amber: named datums \u00b7 cyan: silhouette "
            f"rim")


def op_render(args: dict, ctx) -> dict:
    import numpy as np

    from . import geom

    views = args.get("views")
    if not isinstance(views, list) or not views or any(v not in VIEWS for v in views) or len(set(views)) != len(views):
        raise WorkerError("usage", f"views must be distinct values from {', '.join(VIEWS)}")
    size = args.get("size")
    if size not in SIZES:
        raise WorkerError("usage", f"size must be one of {', '.join(SIZES)}")
    files = args.get("files")
    if not isinstance(files, dict) or set(files) != set(views):
        raise WorkerError("internal", "the render job names no output file for some view")
    if ctx.run_dir is None:
        raise WorkerError("internal", "the render job has no run directory")
    loaded = geom.load(ctx.run_dir / "out")
    parts = geom.select(loaded, args.get("part"))
    warnings: list[str] = []
    scene, record = build_scene(parts, loaded.datums, geom, warnings)
    mesh_only = all(p.mesh is not None for p in parts)
    summary = _summary(parts, geom)
    label = args.get("part") or args.get("name") or (parts[0].name if len(parts) == 1 else "assembly")
    title = _text(f"{args.get('project') or '-'} \u00b7 {label} \u00b7 run {args.get('run_id') or '-'}")
    caption = _caption(record, mesh_only)
    diag = float(np.linalg.norm(scene.V.max(axis=0) - scene.V.min(axis=0)))
    w_px, h_px = SIZES[size]
    hi_dpi = int(round(DPI * 1.6))
    chalk, amber = _rgb(STYLE["part"]), _rgb(STYLE["interface"])
    renders = []
    for view in views:
        elev_azim = VIEWS[view]
        cbar = None
        two_sided = not scene.closed
        sc = scene
        if view == "cutaway":
            if any(p.kind == "sketch" for p in parts):
                warnings.append("cutaway skipped: a sketch has no inside to cut")
                continue
            sc = cut_half(scene, elev_azim)
            if sc is None:
                warnings.append("cutaway skipped: the cut through the bounding-box centre left nothing")
                continue
            two_sided = True
        base0 = np.where(sc.tint[:, None], (1 - DATUM_TINT) * chalk + DATUM_TINT * amber, chalk)
        normals0, _fn0 = corner_normals(sc.V, sc.F, sc.group)
        if view == "thickness":
            if any(p.kind == "sketch" for p in parts) or not scene.closed:
                warnings.append("thickness skipped: wall thickness needs closed solids")
                continue
            # one inward ray per display triangle: the finer display mesh gives a map, not one value per facet
            V, F, index = subdivide(sc.V, sc.F, diag / THICKNESS_SUBDIVISION if diag > 0 else 0.0,
                                    budget=THICKNESS_BUDGET)
            thickness = thickness_map(V, F)
            fin = thickness[np.isfinite(thickness)]
            if not fin.size:
                warnings.append("thickness skipped: no inward ray hit the part")
                continue
            lo_t, hi_t = float(fin.min()), float(np.percentile(fin, 99))
            # a near-uniform wall keeps one colour: the scale never spans less than 10 % of the thickest value
            span = max(1e-6, MIN_THICKNESS_SPAN * hi_t)
            if hi_t - lo_t < span:
                lo_t = max(0.0, hi_t - span)
                hi_t = lo_t + span
            cmap = _thickness_cmap()
            vals = np.clip((np.nan_to_num(thickness, nan=hi_t) - lo_t) / (hi_t - lo_t), 0.0, 1.0)
            base = np.asarray(cmap(vals))[:, :3]
            base[~np.isfinite(thickness)] = _rgb(STYLE["excluded"])
            cbar = (cmap, [lo_t, hi_t], THICKNESS_LABEL)
            record["thickness_rays"] = int(len(F))
        else:
            V, F, index = subdivide(sc.V, sc.F, diag / 25.0 if diag > 0 else 0.0)
            base = base0[index]
        normals = interpolate_normals(sc.V, sc.F, normals0, V, F, index)
        colours = shade(normals, base, elev_azim, two_sided)
        img = draw(V, F, colours, elev_azim, (w_px, h_px), hi_dpi,
                   cull_normals=None if two_sided else face_normals(V, F))
        top, bottom, right = _frame(True, True, cbar is not None)
        aspect = ((right - 0.015) * w_px) / ((top - bottom) * h_px)
        png = ctx.out_file(files[view])
        compose(crop(img, aspect), png, title=title, subtitle=_text(_subtitle(summary, view), 300),
                caption=caption, size_px=(w_px, h_px), cbar=cbar)
        renders.append({"view": view, "file": files[view], "width_px": w_px, "height_px": h_px,
                        "triangles_drawn": int(len(F))})
    if not renders:
        raise WorkerError("usage", "no view could be rendered: " + "; ".join(warnings))
    for w in warnings:
        ctx.warn(w)
    return {"files": [r["file"] for r in renders], "renders": renders, "tessellation": record, "summary": summary}
