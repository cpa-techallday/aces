"""aces golden: every 3-D render of a computed part (Matplotlib Agg only; no GUI, no extra dependencies).

The look is the Parameter Sky of cudax-sim-runtime/DESIGN.md: a navy ground, the part in chalk with Lambert shading and
a soft specular from averaged vertex normals, a cyan rim where the surface turns away from the eye, the preserved
functional interfaces (keep-ins) tinted amber so they read as grown into the part rather than bolted on, no axes,
equal aspect, and a tight crop (the part fills about 80 % of the frame). Captions are monospace and say what each
colour means and the grade. Stress renders map the real solve's von Mises field onto the skin: each vertex takes the
value of the nearest solid element of the solve, and the boundary-condition zones the constrained stress ignores are
grey."""
from __future__ import annotations

import math
import shutil
from pathlib import Path

import numpy as np

from . import read_json

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
INTERFACE_MM = 0.6
FILL = 0.80
SUBDIVIDE_MAX_FACES = 150_000
THUMB_SUBDIVIDE_MAX_FACES = 40_000


def _rgb(h: str) -> np.ndarray:
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)])


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def stress_cmap():
    from matplotlib.colors import LinearSegmentedColormap

    return LinearSegmentedColormap.from_list("aces-stress", [_rgb(c) for c in STYLE["stress_ramp"]])


def _load(stl):
    import trimesh

    return trimesh.load(str(stl), force="mesh")


def _view_vectors(view) -> tuple[np.ndarray, np.ndarray]:
    elev, azim = math.radians(view[0]), math.radians(view[1])
    v = np.array([math.cos(elev) * math.cos(azim), math.cos(elev) * math.sin(azim), math.sin(elev)])
    side = np.cross(v, [0.0, 0.0, 1.0])
    side = side / (np.linalg.norm(side) or 1.0)
    light = 0.45 * v + 0.75 * np.array([0.0, 0.0, 1.0]) + 0.45 * side
    return v / np.linalg.norm(v), light / np.linalg.norm(light)


def _face_colours(mesh, view, base_v: np.ndarray, two_sided: bool = False) -> np.ndarray:
    """Lambert + soft specular per vertex from the averaged vertex normals, a cyan rim, averaged to faces."""
    v, light = _view_vectors(view)
    n = np.asarray(mesh.vertex_normals, dtype=np.float64)
    if two_sided:
        n = n * np.where((n @ v) < 0, -1.0, 1.0)[:, None]
    lam = np.clip(n @ light, 0.0, 1.0)
    half = (light + v) / np.linalg.norm(light + v)
    spec = np.clip(n @ half, 0.0, 1.0) ** 32
    rim = np.clip(1.0 - np.abs(n @ v), 0.0, 1.0) ** 2.5
    col = base_v * (0.30 + 0.70 * lam[:, None]) + 0.22 * spec[:, None] + _rgb(STYLE["rim"])[None, :] * 0.45 * rim[:, None]
    col = np.clip(col, 0.0, 1.0)
    return col[np.asarray(mesh.faces)].mean(axis=1)


def _draw(mesh, face_rgb: np.ndarray, view, size, dpi: int) -> np.ndarray:
    """Draw the mesh alone (no text) and return the RGB image."""
    plt = _plt()
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    bg = STYLE["background"]
    fig = plt.figure(figsize=size, dpi=dpi, facecolor=bg)
    ax = fig.add_axes((0.0, 0.0, 1.0, 1.0), projection="3d", facecolor=bg)
    tris = np.asarray(mesh.vertices)[np.asarray(mesh.faces)]
    pc = Poly3DCollection(tris, facecolors=face_rgb, edgecolors=face_rgb, linewidths=0.15, antialiased=True)
    ax.add_collection3d(pc)
    lo, hi = mesh.bounds
    ext = np.maximum(hi - lo, 1e-6)
    ax.set_xlim(lo[0], hi[0])
    ax.set_ylim(lo[1], hi[1])
    ax.set_zlim(lo[2], hi[2])
    ax.set_box_aspect(tuple(float(e) for e in ext), zoom=1.0)
    ax.view_init(elev=view[0], azim=view[1])
    ax.set_axis_off()
    fig.canvas.draw()
    img = np.asarray(fig.canvas.buffer_rgba())[..., :3].copy()
    plt.close(fig)
    return img


def _crop(img: np.ndarray, aspect: float, fill: float = FILL) -> np.ndarray:
    """Crop to the drawn part so it fills `fill` of the frame in its limiting dimension, at the given aspect (w/h)."""
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
        img = np.concatenate([np.broadcast_to(bg.astype(img.dtype), (pad, img.shape[1], 3)), img,
                              np.broadcast_to(bg.astype(img.dtype), (pad, img.shape[1], 3))], axis=0)
        side = np.broadcast_to(bg.astype(img.dtype), (img.shape[0], pad, 3))
        img = np.concatenate([side, img, side], axis=1)
        X0, Y0 = X0 + pad, Y0 + pad
    return img[Y0:Y0 + H, X0:X0 + W]


def _compose(img: np.ndarray, png, *, title: str, subtitle: str, caption: str, size, dpi: int,
             cbar=None) -> Path:
    plt = _plt()
    bg, muted, chalk, font = STYLE["background"], STYLE["muted"], STYLE["part"], STYLE["font"]
    fig = plt.figure(figsize=size, dpi=dpi, facecolor=bg)
    top = 0.88 if subtitle else 0.91
    bottom = 0.075 if caption else 0.03
    right = 0.86 if cbar is not None else 0.985
    ax = fig.add_axes((0.015, bottom, right - 0.015, top - bottom))
    ax.imshow(img, interpolation="antialiased")
    ax.set_axis_off()
    small = size[0] < 5
    if title:
        fig.text(0.02, 0.975, title, color=chalk, fontsize=7.5 if small else 11, family=font, va="top")
    if subtitle:
        fig.text(0.02, 0.928, subtitle, color=muted, fontsize=6 if small else 8.2, family=font, va="top")
    if caption:
        fig.text(0.02, 0.022, caption, color=muted, fontsize=5.5 if small else 7.4, family=font, va="bottom")
    if cbar is not None:
        from matplotlib.cm import ScalarMappable
        from matplotlib.colors import Normalize

        cmap, clim, label = cbar
        cax = fig.add_axes((0.885, bottom + 0.08, 0.022, (top - bottom) - 0.16))
        cb = fig.colorbar(ScalarMappable(norm=Normalize(vmin=clim[0], vmax=clim[1]), cmap=cmap), cax=cax)
        cb.set_label(label, color=muted, fontsize=7, family=font)
        cb.ax.tick_params(colors=muted, labelsize=7)
        cb.outline.set_edgecolor(muted)
    png = Path(png)
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(png), dpi=dpi, facecolor=bg)
    plt.close(fig)
    return png


def shading_mesh(mesh, max_faces: int = SUBDIVIDE_MAX_FACES):
    """Midpoint subdivision for display only: the geometry (and its volume) is unchanged, but each face's colour now
    averages four times as many shading samples, so the shading reads smooth instead of faceted. Meshes already finer
    than max_faces are drawn as they are."""
    return mesh.subdivide() if len(mesh.faces) <= max_faces else mesh


def _render(mesh, png, *, title: str, subtitle: str = "", caption: str = "", scalars=None, clim=None, grey_mask=None,
            interfaces=(), view=(24, -45), size=(9, 7), dpi: int = 130, cbar_label: str = "",
            two_sided: bool = False) -> Path:
    from . import organic

    nv, nf = len(mesh.vertices), len(mesh.faces)
    base_v = np.tile(_rgb(STYLE["part"]), (nv, 1))
    cbar = None
    if scalars is not None:
        s = np.asarray(scalars, dtype=np.float64)
        sv = s if s.shape[0] == nv else None
        if sv is None:  # per-face values -> per-vertex by averaging the faces around each vertex
            acc = np.zeros(nv)
            cnt = np.zeros(nv)
            np.add.at(acc, np.asarray(mesh.faces).ravel(), np.repeat(s, 3))
            np.add.at(cnt, np.asarray(mesh.faces).ravel(), 1.0)
            sv = acc / np.maximum(cnt, 1.0)
        if clim is None:
            fin = sv[np.isfinite(sv)]
            clim = [0.0, float(np.percentile(fin, 99)) if fin.size else 1.0]
        cmap = stress_cmap()
        span = max(float(clim[1]) - float(clim[0]), 1e-12)
        base_v = cmap(np.clip((np.nan_to_num(sv, nan=float(clim[0])) - float(clim[0])) / span, 0.0, 1.0))[:, :3]
        cbar = (cmap, [float(clim[0]), float(clim[1])], cbar_label)
    elif interfaces:
        V = np.asarray(mesh.vertices, dtype=np.float32)
        near = np.zeros(nv, dtype=bool)
        for r in interfaces:
            near |= np.abs(organic.sdf_region(V, r)) <= INTERFACE_MM
        base_v[near] = 0.30 * base_v[near] + 0.70 * _rgb(STYLE["interface"])
    cols = _face_colours(mesh, view, base_v, two_sided=two_sided)
    if grey_mask is not None:
        g = np.asarray(grey_mask, dtype=bool)
        if g.shape[0] == nv:
            g = g[np.asarray(mesh.faces)].any(axis=1)
        grey = _rgb(STYLE["excluded"])
        shade = cols.mean(axis=1, keepdims=True)
        cols[g] = np.clip(0.55 * grey[None, :] + 0.45 * shade[g], 0.0, 1.0)
    hi_dpi = int(round(dpi * (1.6 if size[0] >= 5 else 1.4)))
    img = _draw(mesh, cols, view, size, hi_dpi)
    right = 0.86 if cbar is not None else 0.985
    top = 0.88 if subtitle else 0.91
    bottom = 0.075 if caption else 0.03
    aspect = ((right - 0.015) * size[0]) / ((top - bottom) * size[1])
    return _compose(_crop(img, aspect), png, title=title, subtitle=subtitle, caption=caption, size=size, dpi=dpi,
                    cbar=cbar)


def iso(stl, png, *, title, subtitle="", scalars=None, clim=None, grey_mask=None, interfaces=(), view=(24, -45),
        size=(9, 7), dpi=130, cbar_label: str = "", caption: str | None = None, grade: str | None = None) -> Path:
    """Isometric render of a part. scalars (per vertex or per face) colour it on the stress ramp within clim;
    grey_mask greys faces; interfaces (region dicts) tint the surface within 0.6 mm of a keep-in amber."""
    mesh = _load(stl)
    if scalars is None and grey_mask is None:
        mesh = shading_mesh(mesh)
    if caption is None:
        if scalars is not None:
            caption = "colour: value on the ramp; grey: excluded zones"
        else:
            caption = ("chalk: the computed organic part · amber: preserved interfaces (keep-ins) · cyan: silhouette rim"
                       if interfaces else "chalk: the computed organic part · cyan: silhouette rim")
        if grade:
            caption += f" · {grade}"
    return _render(mesh, png, title=title, subtitle=subtitle, caption=caption, scalars=scalars, clim=clim,
                   grey_mask=grey_mask, interfaces=interfaces, view=view, size=size, dpi=dpi, cbar_label=cbar_label)


def nearest_elements(case_dir, mesh) -> tuple[np.ndarray, dict]:
    """For each vertex, the (i, j, k) of the nearest solid element of the case's solve (EDT indices of the mask).
    Returns ((3, n_vertices) indices, the solve's mesh.json)."""
    import scipy.ndimage as nd

    case_dir = Path(case_dir)
    mask = np.load(case_dir / "mask.npy").astype(bool)
    mj = read_json(case_dir / "mesh.json", {}) or {}
    h = float(mj["elem_mm"])
    origin = np.asarray(mj["origin_mm"], dtype=np.float64)
    _, inds = nd.distance_transform_edt(~mask, return_indices=True)
    ijk = np.floor((np.asarray(mesh.vertices, dtype=np.float64) - origin) / h).astype(int)
    ijk = np.clip(ijk, 0, np.array(mask.shape) - 1)
    return inds[:, ijk[:, 0], ijk[:, 1], ijk[:, 2]], mj


def vertex_stress(case_dir, mesh) -> tuple[np.ndarray, float]:
    """Each vertex takes the von Mises value of the nearest solid element of the case's solve. Returns (values per
    vertex, the solve's h)."""
    near, mj = nearest_elements(case_dir, mesh)
    shape = tuple(int(n) for n in mj["shape"])
    vm = np.asarray(np.load(Path(case_dir) / "fields.npz")["vm_MPa"], dtype=np.float64).reshape(shape)
    return vm[near[0], near[1], near[2]], float(mj["elem_mm"])


def stress_on_skin(case_dir, png, *, stl, exclude_boxes, clim, title) -> Path:
    """von Mises of the real solve on the organic skin: each vertex takes the value of the nearest solid element, and
    a face is grey when most of its vertices take theirs from an element in an excluded boundary-condition zone (the
    zones the constrained stress ignores). clim [0, allowable] when a stress constraint exists, else [0, p99]."""
    from .fea import box_elems

    mesh = shading_mesh(_load(stl))
    near, mj = nearest_elements(case_dir, mesh)
    shape = tuple(int(n) for n in mj["shape"])
    h = float(mj["elem_mm"])
    vm = np.asarray(np.load(Path(case_dir) / "fields.npz")["vm_MPa"], dtype=np.float64).reshape(shape)
    vals = vm[near[0], near[1], near[2]]
    excluded = np.zeros(shape, dtype=bool)
    for b in exclude_boxes or []:
        excluded |= box_elems(b, mj["origin_mm"], shape, h)
    faces = np.asarray(mesh.faces)
    face_vals = vals[faces].mean(axis=1)
    grey = excluded[near[0], near[1], near[2]][faces].sum(axis=1) >= 2
    if clim is None:
        live = face_vals[~grey]
        clim = [0.0, float(np.percentile(live, 99)) if live.size else float(np.max(face_vals))]
    label = f"von Mises (MPa), nearest solid element of the {h:g} mm solve; excluded zones grey; preview-grade"
    peak = float(np.max(face_vals[~grey])) if (~grey).any() else 0.0
    area = np.asarray(mesh.area_faces, dtype=np.float64)
    grey_pct = 100.0 * float(area[grey].sum()) / max(float(area.sum()), 1e-12)
    sub = (f"colour 0 .. {float(clim[1]):.4g} MPa · grey {grey_pct:.1f} % of the surface · "
           f"max on the skin outside the excluded zones {peak:.4g} MPa")
    return _render(mesh, png, title=title, subtitle=sub, caption="ramp: navy 0 -> cyan -> chalk -> amber -> red-orange at "
                   "the top of the range · grey: boundary-condition zones (stress not constrained there)",
                   scalars=face_vals, clim=clim, grey_mask=grey, cbar_label=label)


def thumb(stl, png, *, label="") -> Path:
    """Small render for the sweep gallery (runs in the worker)."""
    mesh = shading_mesh(_load(stl), THUMB_SUBDIVIDE_MAX_FACES)
    return _render(mesh, png, title=label, size=(3.2, 2.6), dpi=100)


def cutaway(stl, png, *, title) -> Path:
    """Infill designs: the half nearest the eye cut away through the bbox centre, showing the gyroid core."""
    import trimesh
    from trimesh.intersections import slice_faces_plane  # numpy only; Trimesh.slice_plane imports shapely (not locked)

    mesh = _load(stl)
    v, _ = _view_vectors((24, -45))
    vh = np.array([v[0], v[1], 0.0])
    vh = vh / (np.linalg.norm(vh) or 1.0)
    centre = mesh.bounds.mean(axis=0)
    verts, faces, _uv = slice_faces_plane(np.asarray(mesh.vertices, dtype=np.float64), np.asarray(mesh.faces),
                                          plane_normal=-vh, plane_origin=centre)
    half = trimesh.Trimesh(vertices=verts, faces=faces, process=False) if len(faces) else mesh
    return _render(shading_mesh(half), png, title=title, subtitle="cut through the bounding-box centre, facing the eye",
                   caption="chalk: the shell and the gyroid sheet core seen through the cut · cyan: silhouette rim",
                   two_sided=True)


def gallery(items: list[dict], png, *, cols=5, highlight=None) -> Path:
    """The sweep at a glance: one tile per design, failed designs labelled with their design status, the winner outlined
    in cyan. items: {"case_id", "png", "label", "ok"}."""
    plt = _plt()
    import matplotlib.image as mpimg

    bg, muted, chalk, font = STYLE["background"], STYLE["muted"], STYLE["part"], STYLE["font"]
    n = max(1, len(items))
    cols = max(1, min(int(cols), n))
    rows = int(math.ceil(n / cols))
    fig = plt.figure(figsize=(cols * 2.6, rows * 2.3 + 0.45), facecolor=bg)
    for k, it in enumerate(items):
        ax = fig.add_subplot(rows, cols, k + 1)
        ax.set_facecolor(bg)
        ax.set_xticks([])
        ax.set_yticks([])
        p = it.get("png")
        if p and Path(p).is_file():
            ax.imshow(mpimg.imread(str(p)), interpolation="antialiased")
        else:
            ax.text(0.5, 0.5, "no render", color=muted, ha="center", va="center", family=font, fontsize=8,
                    transform=ax.transAxes)
        ok = bool(it.get("ok"))
        ax.set_title(str(it.get("label") or it.get("case_id")), color=chalk if ok else STYLE["fault"], fontsize=7,
                     family=font)
        win = highlight is not None and it.get("case_id") == highlight
        for s in ax.spines.values():
            s.set_edgecolor(STYLE["rim"] if win else (muted if ok else STYLE["fault"]))
            s.set_linewidth(2.4 if win else 0.5)
    fig.suptitle("generative sweep: every design computed and solved; red-orange = failed (status shown); cyan = winner",
                 color=muted, fontsize=8, family=font)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    png = Path(png)
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(png), dpi=110, facecolor=bg)
    plt.close(fig)
    return png


def _metric_line(res: dict, params: dict) -> str:
    parts = [f"{k} {v:.4g}" for k, v in (params or {}).items()]
    for k, unit, name in (("mass_kg", "kg", "mass"), ("vm_p99_MPa", "MPa", "vm_p99"), ("umax_mm", "mm", "umax")):
        if res.get(k) is not None:
            parts.append(f"{name} {res[k]:.4g} {unit}")
    return " · ".join(parts)


def winner_figures(run, label: str, case_id: str, domain: dict, eff: dict) -> dict:
    """<label>_iso.png, <label>_stress.png, density_slices.png, copies of the case's vm_slices.png and
    disp_slices.png, and <label>_cutaway.png for infill designs, in <label>/. Returns {name: run-relative path} and
    records it in <label>/<label>.json when that exists."""
    ldir = Path(run.root) / label
    cdir = Path(run.root) / case_id
    stl = ldir / f"{label}.stl"
    if not stl.is_file():
        stl = cdir / "part.stl"
    if not stl.is_file():
        return {}
    ldir.mkdir(parents=True, exist_ok=True)
    val = read_json(Path(run.root) / "validation.json", {}) or {}
    grade = val.get("grade") or "preview-grade"
    res = read_json(cdir / "results.json", {}) or {}
    params = res.get("design") or {}
    interfaces = list(domain.get("keep_in") or []) + list(domain.get("patch_keep_ins") or [])
    out: dict[str, Path] = {}
    head = f"{eff['name']} · {label} {case_id}"
    out["iso"] = iso(stl, ldir / f"{label}_iso.png", title=f"{head} · computed, not drawn",
                     subtitle=_metric_line(res, params), interfaces=interfaces, grade=grade)
    if (cdir / "fields.npz").is_file() and (cdir / "mask.npy").is_file():
        clim = None
        for c in eff["derived"]["constraints"]:
            if c["output"].startswith("vm_") and c["op"] == "<=":
                clim = [0.0, float(c["limit"])]
                break
        out["stress"] = stress_on_skin(cdir, ldir / f"{label}_stress.png", stl=stl,
                                       exclude_boxes=domain.get("stress_exclude") or [], clim=clim,
                                       title=f"{head} · von Mises on the organic skin")
    dens = cdir / "generator" / "density.npy"
    if dens.is_file():
        from gendesign.export import plot_slices

        p = plot_slices(np.load(dens), float(eff["derived"]["gen_mm"]), str(ldir / "density_slices.png"),
                        f"{case_id}: SIMP density at {float(eff['derived']['gen_mm']):g} mm (the generator's field, "
                        "not the part)")
        if p:
            out["density_slices"] = Path(p)
    for name in ("vm_slices.png", "disp_slices.png"):
        if (cdir / name).is_file():
            shutil.copyfile(cdir / name, ldir / name)
            out[name[:-4]] = ldir / name
    if eff["generator"].get("infill") is not None:
        out["cutaway"] = cutaway(stl, ldir / f"{label}_cutaway.png", title=f"{head} · gyroid core")
    rel = {k: Path(p).resolve().relative_to(Path(run.root).resolve()).as_posix() for k, p in out.items()}
    doc_path = ldir / f"{label}.json"
    doc = read_json(doc_path)
    if isinstance(doc, dict):
        doc["renders"] = rel
        from . import dump_json

        dump_json(doc_path, doc)
    return rel
