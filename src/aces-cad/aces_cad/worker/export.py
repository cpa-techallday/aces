"""Worker op `export`: the CadSource files of a run, written in a job that runs no agent code (spec section 15.1).

The parts come from the run's promoted BREP/NPZ files (geom.load), never from the run's record, and every number in
the returned summary is re-derived here. part.step is build123d's export_step with the header time fixed at
2000-01-01T00:00:00 and the part names as labels, so equal geometry with equal names (the run's name is in the
FILE_NAME and top PRODUCT records) under the same build123d and OCP gives equal bytes (decision D9); a STEP written
straight from the script's shape would differ from one written after the BREP round trip in signed zeros, which is why
the pull always goes through the BREP. part.stl is one binary STL of every part; part.3mf goes through lib3mf
(build123d's Mesher) and is not byte-reproducible (D10). Heavy libraries are imported inside the functions only.
"""
from __future__ import annotations

from .main import WorkerError

STEP_HEADER_TIME = "2000-01-01T00:00:00"
FORMATS = ("step", "stl", "3mf")
BREP_KINDS = ("solid", "assembly", "sketch")
FILES = {"step": "part.step", "stl": "part.stl", "3mf": "part.3mf"}
SUMMARY_KEYS = ("name", "kind", "volume_mm3", "area_mm2", "bbox_mm", "size_mm", "centroid_mm", "counts",
                "watertight")


def _number(args: dict, key: str, default: float, lo: float, hi: float) -> float:
    value = args.get(key, default)
    if value is None:
        value = default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= float(value) <= hi:
        raise WorkerError("usage", f"{key} must be a number from {lo} to {hi}")
    return float(value)


def _formats(args: dict, kind: str) -> list[str]:
    raw = args.get("formats")
    if not isinstance(raw, list) or not raw or any(f not in FORMATS for f in raw) or len(set(raw)) != len(raw):
        raise WorkerError("usage", f"formats must be a non-empty list of distinct values from {', '.join(FORMATS)}")
    allowed = {"solid": FORMATS, "assembly": FORMATS, "sketch": ("step",), "mesh": ("stl",)}.get(kind, ())
    bad = [f for f in raw if f not in allowed]
    if bad:
        raise WorkerError("usage", f"{', '.join(bad)} does not apply to a {kind} result (the parent filters formats "
                                   f"by kind; this job was built wrongly)")
    return [f for f in FORMATS if f in raw]


def _step_shape(parts: list, run_name: str):
    """One labelled part, or a Compound of the labelled parts labelled with the run name."""
    from build123d import Compound

    for p in parts:
        p.shape.label = p.name
    if len(parts) == 1:
        return parts[0].shape
    return Compound(children=[p.shape for p in parts], label=run_name)


def _all_shapes(parts: list):
    from build123d import Compound

    if len(parts) == 1:
        return parts[0].shape
    return Compound([p.shape for p in parts])


def _step_schema(path) -> str | None:
    with open(path, "rb") as fh:
        head = fh.read(4096).decode("ascii", "replace")
    for line in head.splitlines():
        if line.startswith("FILE_SCHEMA"):
            inner = line.split("'")
            return inner[1].split()[0] if len(inner) > 1 and inner[1].split() else line
    return None


def _stl_check(path) -> dict:
    import trimesh
    from trimesh import grouping

    mesh = trimesh.load(str(path), file_type="stl", force="mesh")
    edges = grouping.group_rows(mesh.edges_sorted, require_count=1)
    return {"watertight": bool(mesh.is_watertight), "open_edges": int(len(edges)), "triangles": int(len(mesh.faces))}


def _step_check(path, parts: list, kind: str) -> dict:
    """Re-import part.step and compare it with the BREP (a format round trip check, not a repair)."""
    from build123d import import_step

    shape = import_step(str(path))
    solids = shape.solids()
    out = {"solids": len(solids), "faces": len(shape.faces())}
    if kind != "sketch":
        expected = float(sum(p.shape.volume for p in parts))
        got = float(sum(s.volume for s in solids))
        out.update(volume_mm3=got, brep_volume_mm3=expected, volume_delta_mm3=got - expected)
    else:
        out.update(area_mm2=float(shape.area), brep_area_mm2=float(sum(p.shape.area for p in parts)))
    return out


def op_export(args: dict, ctx) -> dict:
    from build123d import Mesher, Unit, export_step, export_stl

    from . import geom

    if ctx.run_dir is None:
        raise WorkerError("internal", "the export job has no run directory")
    loaded = geom.load(ctx.run_dir / "out")
    kind = loaded.kind
    parts = list(loaded.parts)
    if not parts:
        raise WorkerError("result", "the run has no parts to export")
    formats = _formats(args, kind)
    tol = _number(args, "stl_tolerance_mm", 0.01, 0.001, 1.0)
    ang = _number(args, "angular_tolerance_rad", 0.1, 0.01, 1.0)
    run_name = args.get("name") if isinstance(args.get("name"), str) and args.get("name") else "part"
    warnings: list[str] = []
    files: list[str] = []
    step_schema = step_check = stl_check = None

    if "step" in formats:
        path = ctx.out_file(FILES["step"])
        if not export_step(_step_shape(parts, run_name), str(path), unit=Unit.MM, timestamp=STEP_HEADER_TIME):
            raise WorkerError("internal", "build123d export_step reported failure for part.step")
        files.append(FILES["step"])
        step_schema = _step_schema(path)
        step_check = _step_check(path, parts, kind)
    if "stl" in formats:
        path = ctx.out_file(FILES["stl"])
        if kind == "mesh":
            from trimesh.exchange.stl import export_stl as stl_bytes
            from trimesh.util import concatenate

            meshes = [p.mesh for p in parts]
            path.write_bytes(stl_bytes(meshes[0] if len(meshes) == 1 else concatenate(meshes)))
        elif not export_stl(_all_shapes(parts), str(path), tolerance=tol, angular_tolerance=ang, ascii_format=False):
            raise WorkerError("internal", "build123d export_stl reported failure for part.stl")
        files.append(FILES["stl"])
        stl_check = _stl_check(path)
        if not stl_check["watertight"]:
            warnings.append(f"part.stl is not watertight ({stl_check['open_edges']} open edges after trimesh "
                            f"processing); it was written as tessellated and was not repaired")
    if "3mf" in formats:
        path = ctx.out_file(FILES["3mf"])
        try:
            mesher = Mesher(unit=Unit.MM)
            for p in parts:
                mesher.add_shape(p.shape, linear_deflection=tol, angular_deflection=ang, part_number=p.name)
            mesher.write(str(path))
            files.append(FILES["3mf"])
        except Exception as e:  # noqa: BLE001 (lib3mf raises its own types; 3MF is a convenience copy)
            if path.exists():
                path.unlink()
            warnings.append(f"part.3mf was not written: {type(e).__name__}: {str(e)[:300]}")

    summary = []
    for p in parts:
        s = geom.summarize(p)
        ok, reasons = geom.quick_validity(p)
        row = {k: s[k] for k in SUMMARY_KEYS if k in s}
        row.update(name=p.name, kind=p.kind, valid=bool(ok), reasons=list(reasons))
        summary.append(row)
    return {"kind": kind, "files": files, "summary": summary, "stl_check": stl_check, "step_check": step_check,
            "step_schema": step_schema, "warnings": warnings}
