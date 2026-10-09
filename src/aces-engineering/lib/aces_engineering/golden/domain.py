"""aces golden: the design space. It is an input domain, never the output: the part is computed inside it.

`build` obtains the design-space STEP (a byte copy of the user's file, or a build123d builder exported to STEP) and
records how (inputs/domain.json). `prepare` voxelises it through the step-structures adapter's own stage_mesh (Embree
containment; no second implementation), turns everything outside it into passive void boxes for the SIMP generator,
writes the design-space signed-distance field the skin is clipped to, and fixes the zones every later stage shares:
patch keep-ins, stress-exclusion boxes, load and support patches (domain/domain.json).

Element (i, j, k) at resolution h has its centre at origin + (i + 0.5, j + 0.5, k + 0.5) * h. Passive regions for
gendesign.topopt3d.run_case are in the grid frame (part frame minus origin); everything recorded for people and for the
solver adapter is in the part frame."""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import math
import shutil
import time
from pathlib import Path

from . import EXIT_USAGE, SCHEMA, GoldenError, dump_json, sha256_file
from .spec import stress_lattice

AXES = "xyz"
MAX_VOID_BOXES = 2000
STEP_NOTE = ("the STEP header carries an export timestamp, so step_sha256 changes per export; the stable geometry "
             "digest is domain.json mask_sha256")
_TOL = 1e-6


# -------------------------------------------------------------------------------------------------------------- build

def _load_builder(ref: str):
    """`ref` is "pkg.module:func" or an absolute "file.py:func" (eff["derived"]["domain_ref"])."""
    mod, _, func = ref.rpartition(":")
    if mod.endswith(".py"):
        path = Path(mod)
        if not path.is_file():
            raise GoldenError(EXIT_USAGE, f"the design-space builder file {path} does not exist")
        spec = importlib.util.spec_from_file_location(f"aces_golden_domain_{path.stem}", str(path))
        if spec is None or spec.loader is None:
            raise GoldenError(EXIT_USAGE, f"cannot load the design-space builder file {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        try:
            module = importlib.import_module(mod)
        except ImportError as exc:
            raise GoldenError(EXIT_USAGE, f"cannot import the design-space builder module {mod}: {exc}") from None
    fn = getattr(module, func, None)
    if not callable(fn):
        raise GoldenError(EXIT_USAGE, f"{ref}: {func} is not a function of {mod}")
    return fn


def build(eff: dict, inputs_dir: Path, base_dir: Path) -> dict:
    """Write inputs/domain.step (a byte copy of the user STEP, or the build123d builder's export) and
    inputs/domain.json, and return the latter. The STEP is read back for its bbox, solids and B-rep volume."""
    import build123d
    from build123d import export_step, import_step

    inputs_dir = Path(inputs_dir)
    inputs_dir.mkdir(parents=True, exist_ok=True)
    dom, d = eff["domain"], eff["derived"]
    step_out = inputs_dir / "domain.step"
    if dom["source"] == "step":
        src = Path(d["domain_ref"])
        if not src.is_file():
            raise GoldenError(EXIT_USAGE, f"the design-space STEP {src} does not exist")
        shutil.copyfile(src, step_out)
        ref, kwargs, ref_file = dom["step_file"], {}, str(src)
    else:
        b = dom["build123d"]
        fn = _load_builder(d["domain_ref"])
        kwargs = dict(b.get("kwargs") or {})
        try:
            shape = fn(**kwargs)
        except Exception as exc:
            raise GoldenError(EXIT_USAGE, f"the design-space builder {b['ref']} failed: {type(exc).__name__}: {exc}") from None
        solids = list(shape.solids()) if hasattr(shape, "solids") else []
        if not solids:
            raise GoldenError(EXIT_USAGE, f"the design-space builder {b['ref']} returned no solid ({type(shape).__name__})")
        export_step(shape, str(step_out))
        ref = b["ref"]
        try:
            ref_file = inspect.getsourcefile(fn)
        except TypeError:
            ref_file = None
    shapes = import_step(str(step_out))
    solids = list(shapes.solids()) if hasattr(shapes, "solids") else [shapes]
    if not solids:
        raise GoldenError(EXIT_USAGE, f"the design-space STEP from {ref} holds no solid")
    bb = shapes.bounding_box()
    rec = {
        "schema": SCHEMA["inputs"], "source": dom["source"], "ref": ref, "kwargs": kwargs, "ref_file": ref_file,
        "ref_file_sha256": sha256_file(ref_file) if ref_file and Path(ref_file).is_file() else None,
        "build123d_version": getattr(build123d, "__version__", None),
        "step": "inputs/domain.step", "step_sha256": sha256_file(step_out), "step_bytes": step_out.stat().st_size,
        "brep_volume_mm3": float(sum(s.volume for s in solids)),
        "bbox_mm": [float(bb.min.X), float(bb.min.Y), float(bb.min.Z), float(bb.max.X), float(bb.max.Y), float(bb.max.Z)],
        "solids": len(solids), "note": STEP_NOTE,
    }
    dump_json(inputs_dir / "domain.json", rec)
    return rec


# ------------------------------------------------------------------------------------------------- regions and boxes

def to_grid(region: dict, origin) -> dict:
    """A region (box or cylinder, part frame) as a run_case passive entry shape in the grid frame (no type)."""
    o = [float(v) for v in origin]
    if region.get("box") is not None:
        b = [float(v) for v in region["box"]]
        return {"box": [b[0] - o[0], b[1] - o[1], b[2] - o[2], b[3] - o[0], b[4] - o[1], b[5] - o[2]]}
    c = region["cyl"]
    a = AXES.index(c["axis"])
    others = [i for i in range(3) if i != a]
    return {"cyl": {"axis": c["axis"],
                    "center": [float(c["center"][0]) - o[others[0]], float(c["center"][1]) - o[others[1]]],
                    "r": float(c["r"]), "span": [float(c["span"][0]) - o[a], float(c["span"][1]) - o[a]]}}


def _along(v, axis: int):
    shape = [1, 1, 1]
    shape[axis] = -1
    return v.reshape(shape)


def region_mask(regions: list, shape, h: float, origin):
    """Elements whose centres lie inside any region: the same tests (and arithmetic) as VoxelFEA.elems_in_box and
    elems_in_cyl on the grid-frame regions run_case receives, so a mask built here matches run_case's passive cells."""
    import numpy as np

    shape = tuple(int(n) for n in shape)
    c = [(np.arange(n, dtype=np.float64) + 0.5) * float(h) for n in shape]
    out = np.zeros(shape, dtype=bool)
    for r in regions:
        g = to_grid(r, origin)
        if "box" in g:
            b = g["box"]
            m = [(c[a] >= b[a]) & (c[a] <= b[a + 3]) for a in range(3)]
            out |= _along(m[0], 0) & _along(m[1], 1) & _along(m[2], 2)
        else:
            cy = g["cyl"]
            a = AXES.index(cy["axis"])
            o = [i for i in range(3) if i != a]
            d2 = _along(c[o[0]] - float(cy["center"][0]), o[0]) ** 2 + _along(c[o[1]] - float(cy["center"][1]), o[1]) ** 2
            span = (c[a] >= float(cy["span"][0])) & (c[a] <= float(cy["span"][1]))
            out |= (d2 <= float(cy["r"]) ** 2) & _along(span, a)
    return out


def void_boxes(void, h: float) -> list[list[float]]:
    """Run-length encode void cells along z into grid-frame boxes [i h, j h, k0 h, (i+1) h, (j+1) h, k1 h]; the
    element-centre test selects exactly the encoded cells."""
    import numpy as np

    h = float(h)
    out: list[list[float]] = []
    nx, ny, _ = void.shape
    for i in range(nx):
        for j in range(ny):
            col = np.asarray(void[i, j, :], dtype=np.int8)
            if not col.any():
                continue
            edges = np.flatnonzero(np.diff(np.concatenate(([0], col, [0]))))
            for k0, k1 in zip(edges[0::2], edges[1::2]):
                out.append([i * h, j * h, int(k0) * h, (i + 1) * h, (j + 1) * h, int(k1) * h])
    return out


def _clip(box: list[float], origin, env) -> list[float]:
    lo = [float(origin[a]) for a in range(3)]
    hi = [float(origin[a]) + float(env[a]) for a in range(3)]
    return [min(max(box[a], lo[a]), hi[a]) for a in range(3)] + [min(max(box[a + 3], lo[a]), hi[a]) for a in range(3)]


def _planar_axes(box) -> list[int]:
    return [a for a in range(3) if abs(float(box[a + 3]) - float(box[a])) <= _TOL]


def _bc_items(eff: dict) -> list[tuple[str, int, dict]]:
    return [("support", i, s) for i, s in enumerate(eff["supports"])] + [("load", i, ld) for i, ld in enumerate(eff["loads"])]


def patch_keep_ins(eff: dict, origin, env) -> list[dict]:
    """Each planar support or load box extruded into the envelope by patch_keep_in_mm (inward from the envelope face it
    lies on; both ways for an interior plane), so boundary conditions always act on preserved material."""
    t = float(eff["derived"]["patch_keep_in_mm"])
    if t <= 0:
        return []
    out = []
    for kind, i, item in _bc_items(eff):
        box = [float(v) for v in item["box"]]
        planar = _planar_axes(box)
        if not planar:
            continue
        b = list(box)
        for a in planar:
            p, lo, hi = box[a], float(origin[a]), float(origin[a]) + float(env[a])
            if abs(p - lo) <= _TOL:
                b[a], b[a + 3] = p, p + t
            elif abs(p - hi) <= _TOL:
                b[a], b[a + 3] = p - t, p
            else:
                b[a], b[a + 3] = p - t, p + t
        name = item.get("name") or f"{kind}{i + 1}"
        out.append({"name": f"patch-{kind}-{name}", "box": _clip(b, origin, env), "cyl": None, "round_mm": 0.0})
    return out


def exclusion_width(eff: dict) -> tuple[float, float]:
    """(width_mm, lattice_mm): stress_exclude_mm rounded up to a whole multiple of the stress lattice (spec.stress_lattice:
    the grid quantum of fea_mm and every planned check level), so the zone is never thinner than the spec asks."""
    q = stress_lattice(eff)
    s = float(eff["derived"]["stress_exclude_mm"])
    return round(math.ceil(s / q - 1e-9) * q, 9), q


def stress_exclude(eff: dict, origin, env) -> list[list[float]]:
    """The zones the constrained stress ignores: planar boundary-condition boxes thickened along their normal, volume
    boxes inflated on every side, by exclusion_width; every face then snapped outward to the stress lattice (a no-op for
    boxes on it, which check_geometry enforces) and clipped to the envelope. With every face on the lattice, the
    inclusive element-centre test excludes the same physical zone at fea_mm and at every check level."""
    s, q = exclusion_width(eff)
    o = [float(v) for v in origin]
    out = []
    for _kind, _i, item in _bc_items(eff):
        b = [float(v) for v in item["box"]]
        axes = _planar_axes(b) or [0, 1, 2]
        for a in axes:
            b[a] -= s
            b[a + 3] += s
        for a in range(3):
            b[a] = round(o[a] + math.floor((b[a] - o[a]) / q + 1e-9) * q, 9)
            b[a + 3] = round(o[a] + math.ceil((b[a + 3] - o[a]) / q - 1e-9) * q, 9)
        out.append(_clip(b, origin, env))
    return out


def _patches(eff: dict) -> tuple[list[dict], list[dict]]:
    loads = []
    for i, ld in enumerate(eff["loads"]):
        box = [float(v) for v in ld["box"]]
        axis = ld.get("axis")
        if axis is None:
            planar = _planar_axes(box)
            axis = AXES[planar[0]] if planar else None
        plane = box[AXES.index(axis)] if axis is not None else None
        loads.append({"name": ld.get("name") or f"load{i + 1}", "box": box, "axis": axis, "plane": plane})
    sups = [{"name": s.get("name") or f"support{i + 1}", "box": [float(v) for v in s["box"]]}
            for i, s in enumerate(eff["supports"])]
    return loads, sups


# ------------------------------------------------------------------------------------------------------------ prepare

def _stage_mesh(step_abs: str, h: float, origin, env, out: Path) -> dict:
    from simlab.solvers.step_structures import Adapter
    from simlab.spec import Experiment

    out.mkdir(parents=True, exist_ok=True)
    case = {"step_file": step_abs, "elem_mm": float(h), "origin_mm": [float(v) for v in origin],
            "envelope_mm": [float(v) for v in env]}
    exp = Experiment(id="domain", physics="structures", solver="step-structures", params=case)
    exp.spec_path = str(out / "spec.json")  # not written: the adapter only needs a base directory
    return Adapter().stage_mesh(case, exp, str(out))


def prepare(eff: dict, step_path: Path, out_dir: Path) -> dict:
    """Voxelise the design space at gen_mm (and at fea_mm into domain/fea/ when different), encode the cells outside it
    as passive void boxes, write the design-space signed-distance field when the design space is not the whole
    envelope, and fix the boundary-condition zones. Writes and returns domain/domain.json."""
    import numpy as np

    t0 = time.time()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    root = out_dir.parent
    d = eff["derived"]
    grid = d.get("grid")
    if not grid:
        raise ValueError("domain.prepare needs eff['derived']['grid']: derive it after domain.build")
    origin = [float(v) for v in grid["origin_mm"]]
    env = [float(v) for v in grid["envelope_mm"]]
    gen, fea, field_h = float(d["gen_mm"]), float(d["fea_mm"]), float(d["field_h_mm"])
    step_abs = str(Path(step_path).resolve())
    stats = _stage_mesh(step_abs, gen, origin, env, out_dir)
    stats_fea = _stage_mesh(step_abs, fea, origin, env, out_dir / "fea") if abs(fea - gen) > 1e-9 else None
    mask = np.load(out_dir / "mask.npy")
    shape = tuple(int(n) for n in mask.shape)

    dom = eff["domain"]
    patches = patch_keep_ins(eff, origin, env)
    keep_regions = list(dom["keep_in"]) + patches
    keep = region_mask(keep_regions, shape, gen, origin)
    boxes = void_boxes(~mask & ~keep, gen)  # keep-ins always win: run_case applies void after solid
    if len(boxes) > MAX_VOID_BOXES:
        raise GoldenError(EXIT_USAGE, f"design space too complex for the v0 passive-box encoding at {gen:g} mm: use a "
                          f"coarser --gen-res ({len(boxes)} void boxes, limit {MAX_VOID_BOXES})")
    passive = [{"box": b, "type": "void"} for b in boxes]
    passive += [dict(to_grid(r, origin), type="void") for r in dom["keep_out"]]
    passive += [dict(to_grid(r, origin), type="solid") for r in keep_regions]

    sdf_rel = None
    if float(stats["solid_fraction"]) < 0.999 and dom["clip_to_domain"]:
        import scipy.ndimage as nd
        import trimesh
        from gendesign.evaluators import voxelize_mesh

        tm = trimesh.load(str(out_dir / "tessellation.stl"), force="mesh")
        if not tm.is_watertight:
            tm.merge_vertices()
            tm.fix_normals()
        inside = np.asarray(voxelize_mesh(tm, env, field_h, origin), dtype=bool)
        if inside.any() and not inside.all():
            sdf = (nd.distance_transform_edt(~inside, sampling=field_h)
                   - nd.distance_transform_edt(inside, sampling=field_h))
            sdf = nd.gaussian_filter(sdf, 1.0).astype(np.float32)
            np.save(out_dir / "sdf_field.npy", sdf)
            sdf_rel = (out_dir / "sdf_field.npy").relative_to(root).as_posix()

    load_patches, support_patches = _patches(eff)
    width, lattice = exclusion_width(eff)
    rec = {
        "schema": SCHEMA["domain"], "step_path": step_abs, "step_sha256": sha256_file(step_abs),
        "origin_mm": origin, "envelope_mm": env, "h_gen_mm": gen, "shape": list(shape),
        "mask_path": (out_dir / "mask.npy").relative_to(root).as_posix(), "mask_sha256": sha256_file(out_dir / "mask.npy"),
        "design_share": float(stats["solid_fraction"]), "stats": stats, "stats_fea": stats_fea,
        "field_h_mm": field_h, "sdf_field_path": sdf_rel, "passive_grid": passive, "n_void_boxes": len(boxes),
        "keep_in": list(dom["keep_in"]), "keep_out": list(dom["keep_out"]), "patch_keep_ins": patches,
        "supports": list(eff["supports"]), "loads": list(eff["loads"]),
        "stress_exclude": stress_exclude(eff, origin, env), "stress_exclude_effective_mm": width,
        "stress_exclude_lattice_mm": lattice, "load_patches": load_patches,
        "support_patches": support_patches, "wall_s": time.time() - t0,
    }
    dump_json(out_dir / "domain.json", rec)
    return rec


def design_cells(domain: dict, eff: dict, shape):
    """Generator-grid cells free for the SIMP update: inside the design space and outside every keep-in, patch keep-in
    and keep-out. Needs the run root in domain["_root"] (set by the case runner) to read domain/mask.npy."""
    import numpy as np

    gen = float(eff["derived"]["gen_mm"])
    origin = eff["derived"]["grid"]["origin_mm"]
    root = domain.get("_root")
    space = None
    if root and domain.get("mask_path"):
        p = Path(root) / domain["mask_path"]
        if p.is_file():
            space = np.load(p).astype(bool)
    if space is None or tuple(space.shape) != tuple(shape):
        space = np.ones(tuple(shape), dtype=bool)
    fixed = region_mask(list(domain.get("keep_in") or []) + list(domain.get("patch_keep_ins") or [])
                        + list(domain.get("keep_out") or []), shape, gen, origin)
    return space & ~fixed
