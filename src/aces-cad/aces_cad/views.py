"""cad_render and cad_drawing, parent side (spec sections 13, 14 and 5.2).

Both ops run after ops.dispatch has validated the call and run the class gate. They open the run (`latest` is the
newest successful run), send one data-only job to a worker (no agent code; ro = the run directory, rw = the job
directory, the font cache writable), promote exactly the files they asked for into renders/ or drawings/, and report
sizes and sha256 measured by the parent. Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import struct
import time
from pathlib import Path

from . import config, errors, params, sandbox, store

RENDER_VIEWS = ("iso", "front", "back", "top", "bottom", "right", "left", "cutaway", "thickness")
SIZES = {"small": (640, 500), "medium": (1024, 800), "large": (1600, 1250)}
DRAWING_VIEWS = ("front", "back", "top", "bottom", "right", "left", "iso")
PROJECTIONS = ("third-angle", "first-angle")
DRAWING_FORMATS = ("dxf", "svg", "png")
RUN_RE = re.compile(r"^(latest|[0-9]{8}T[0-9]{6}Z-[0-9a-f]{4})$")
PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
TITLE_RE = re.compile(r"^[^\x00-\x1f\x7f]*$")
MAX_INLINE = 4
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


# --- argument checks (the MCP schema checks these too; the CLI and tests call the ops directly) -------------------

def _run_id(args: dict) -> str:
    value = args.get("run_id")
    if value is None:
        return "latest"
    if not isinstance(value, str) or not RUN_RE.match(value):
        raise errors.UsageError("run_id must be latest or a run id like 20261010T133514Z-a3f9")
    return value


def _part(args: dict) -> str | None:
    value = args.get("part")
    if value is None:
        return None
    if not isinstance(value, str) or not PART_RE.match(value):
        raise errors.UsageError("part must be a part name (letters, digits, '.', '_', '-'; at most 64 characters)")
    return value


def _bool(args: dict, key: str, default: bool) -> bool:
    value = args.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise errors.UsageError(f"{key} must be true or false")
    return value


def _timeout(args: dict) -> int | None:
    value = args.get("timeout_s")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 600:
        raise errors.UsageError("timeout_s must be an integer from 1 to 600")
    return value


def _choices(args: dict, key: str, allowed: tuple, default: list, *, max_items: int | None = None) -> list:
    value = args.get(key)
    if value is None:
        return list(default)
    if not isinstance(value, list) or not value or any(not isinstance(v, str) or v not in allowed for v in value):
        raise errors.UsageError(f"{key} must be a non-empty list of values from: {', '.join(allowed)}")
    if len(set(value)) != len(value):
        raise errors.UsageError(f"{key} must not repeat a value")
    if max_items is not None and len(value) > max_items:
        raise errors.UsageError(f"{key} takes at most {max_items} values")
    return list(value)


def _section(args: dict) -> dict | None:
    value = args.get("section")
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"axis", "at_mm"} or value.get("axis") not in ("x", "y", "z"):
        raise errors.UsageError("section must be {\"axis\": \"x\"|\"y\"|\"z\", \"at_mm\": number}")
    at = value.get("at_mm")
    if isinstance(at, bool) or not isinstance(at, (int, float)) or not -100000 <= at <= 100000 or at != at:
        raise errors.UsageError("section.at_mm must be a number from -100000 to 100000")
    return {"axis": value["axis"], "at_mm": float(at)}


# --- shared parent helpers ----------------------------------------------------------------------------------------

def run_failed(kind: str, message: str) -> Exception:
    """A RunFailed of one of its kinds (script, kernel, result, crash, integrity), built in one place."""
    return errors.RunFailed(message, kind=kind)


def open_ok_run(ctx, run_id: str):
    """The run and its record; a run whose status is not ok is refused (kind usage)."""
    run = store.open_run(ctx.info, run_id, require_ok=True)
    rec = run.record()
    if not isinstance(rec, dict) or rec.get("status") != "ok":
        status = rec.get("status") if isinstance(rec, dict) else None
        err = rec.get("error") if isinstance(rec, dict) else None
        kind = (err.get("kind") if isinstance(err, dict) else None) or "unknown"
        raise errors.UsageError(f"run {run.id} did not succeed (status {status}, error kind {kind}): fix the script "
                                f"and run it again; only successful runs can be rendered, drawn or pulled")
    return run, rec


def caps(ctx, op: str, timeout_s: int | None) -> tuple[int, int]:
    """(max bytes per promoted file, max bytes in total): ACES_CAD_MAX_FILE_MB and ACES_CAD_MAX_RUN_MB."""
    limits = ctx.limits(op, timeout_s)
    return int(limits.max_file_mb) << 20, int(config.settings(ctx.env).max_run_mb) << 20


def result_kind(rec: dict) -> str | None:
    result = rec.get("result") if isinstance(rec.get("result"), dict) else {}
    kind = result.get("kind")
    return kind if isinstance(kind, str) else None


def payload(outcome, op: str) -> dict:
    data = outcome.result
    if not isinstance(data, dict):
        raise run_failed("crash", f"the {op} worker returned no payload")
    return data


def declared_files(data: dict, expected: set, op: str) -> list:
    """The payload's file list, refused (kind integrity) when it names anything this op did not ask for."""
    files = data.get("files")
    if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
        raise run_failed("crash", f"the {op} worker returned no file list")
    extra = sorted(set(files) - expected)
    if extra:
        raise run_failed("integrity", f"the {op} worker reported files it was not asked for: {', '.join(extra)}")
    return files


def png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != PNG_MAGIC or data[12:16] != b"IHDR":
        raise run_failed("integrity", "a render is not a PNG file")
    return struct.unpack(">II", data[16:24])


def write_json(directory: Path, name: str, obj) -> Path:
    """Parent write: a fresh temporary file in the same directory, then os.replace (never follows a symlink)."""
    data = (json.dumps(obj, indent=1, ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
    tmp = Path(directory) / f".{name}.{secrets.token_hex(4)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, Path(directory) / name)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return Path(directory) / name


def timings(outcome, t0: float) -> dict:
    out = {k: v for k, v in (outcome.timings_s or {}).items() if k != "wall"}
    out["wall"] = round(time.monotonic() - t0, 3)
    return out


# --- cad_render ---------------------------------------------------------------------------------------------------

def render_name(view: str, size: str, part: str | None) -> str:
    return f"{view}-{size}" + (f"-{part}" if part else "") + ".png"


def op_render(ctx, args: dict) -> dict:
    """args: run_id, views, part, size, inline, timeout_s."""
    t0 = time.monotonic()
    run_id = _run_id(args)
    views = _choices(args, "views", RENDER_VIEWS, ["iso"], max_items=6)
    part = _part(args)
    size = args.get("size") or "medium"
    if size not in SIZES:
        raise errors.UsageError(f"size must be one of {', '.join(SIZES)}")
    inline = _bool(args, "inline", True)
    timeout_s = _timeout(args)
    run, rec = open_ok_run(ctx, run_id)
    names = {v: render_name(v, size, part) for v in views}
    job_args = {"views": views, "part": part, "size": size, "files": names, "project": ctx.info["name"],
                "run_id": run.id, "name": rec.get("name")}
    outcome = ctx.job(run, "render", job_args, cache_rw=True, timeout_s=timeout_s)
    try:
        data = payload(outcome, "render")
        files = declared_files(data, set(names.values()), "render")
        max_file, max_total = caps(ctx, "render", timeout_s)
        dest = store.ensure_subdir(run.root, "renders")
        promoted = store.promote(outcome.out_dir, files, dest, max_file_bytes=max_file, max_total_bytes=max_total)
    finally:
        sandbox.finish_job(outcome.job_dir)
    renders = []
    for view in views:
        name = names[view]
        if name not in promoted:
            continue
        info = promoted[name]
        width, height = png_size(store.safe_read(dest, name, max_file))
        renders.append({"view": view, "path": str(dest / name), "width_px": width, "height_px": height,
                        "bytes": info["bytes"], "sha256": info["sha256"]})
    tess = data.get("tessellation") if isinstance(data.get("tessellation"), dict) else {}
    tessellation = {"tolerance_mm": tess.get("tolerance_mm"), "angular_rad": tess.get("angular_rad"),
                    "triangles": tess.get("triangles"), "doublings": tess.get("doublings")}
    if isinstance(tess.get("mesh_decimation"), dict):
        tessellation["mesh_decimation"] = tess["mesh_decimation"]
    out = {"run_id": run.id, "renders": renders, "tessellation": tessellation,
           "timings_s": timings(outcome, t0), "sandbox": outcome.sandbox, "warnings": list(outcome.warnings or [])}
    if inline:
        out["inline_images"] = [r["path"] for r in renders[:MAX_INLINE]]
        if len(renders) > MAX_INLINE:
            out["warnings"].append(f"only the first {MAX_INLINE} renders are returned inline; the rest are files")
    return out


# --- cad_drawing --------------------------------------------------------------------------------------------------

def _title(args: dict) -> str | None:
    value = args.get("title")
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 80 or not TITLE_RE.match(value):
        raise errors.UsageError("title must be at most 80 characters without control characters")
    return value


def drawing_stem(label: str, options: dict) -> str:
    return f"{label}-{params.sha256_json(options)[:8]}"


def op_drawing(ctx, args: dict) -> dict:
    """args: run_id, part, views, projection, formats, dimensions, section, title, inline, timeout_s."""
    run_id = _run_id(args)
    part = _part(args)
    views = _choices(args, "views", DRAWING_VIEWS, ["front", "top", "right", "iso"], max_items=7)
    projection = args.get("projection") or "third-angle"
    if projection not in PROJECTIONS:
        raise errors.UsageError(f"projection must be {' or '.join(PROJECTIONS)}")
    formats = _choices(args, "formats", DRAWING_FORMATS, list(DRAWING_FORMATS))
    dimensions = _bool(args, "dimensions", True)
    section = _section(args)
    title = _title(args)
    inline = _bool(args, "inline", False)
    timeout_s = _timeout(args)
    run, rec = open_ok_run(ctx, run_id)
    if result_kind(rec) == "mesh":
        raise errors.UsageError(f"drawings need a B-rep: run {run.id} returned a mesh (render it instead)")
    label = part or rec.get("name") or "part"
    views = [v for v in DRAWING_VIEWS if v in views]
    formats = [f for f in DRAWING_FORMATS if f in formats]
    options = {"part": part, "views": views, "projection": projection, "formats": formats, "dimensions": dimensions,
               "section": section, "title": title}
    stem = drawing_stem(label, options)
    script = rec.get("script") if isinstance(rec.get("script"), dict) else {}
    title_block = {"title": title or label, "project": ctx.info["name"], "class": ctx.info.get("class"),
                   "run_id": run.id, "script_sha256": script.get("sha256"), "params_sha256": rec.get("params_sha256"),
                   "date": str(rec.get("finished_at") or rec.get("created_at") or "")[:10]}
    job_args = {"part": part, "views": views, "projection": projection, "formats": formats,
                "dimensions": dimensions, "section": section, "stem": stem, "title_block": title_block,
                "name": rec.get("name")}
    outcome = ctx.job(run, "drawing", job_args, cache_rw=True, timeout_s=timeout_s)
    wanted = {f"{stem}.{f}" for f in formats}
    try:
        data = payload(outcome, "drawing")
        files = declared_files(data, {f"{stem}.{f}" for f in DRAWING_FORMATS}, "drawing")
        missing = sorted(wanted - set(files))
        if missing:
            raise run_failed("crash", f"the drawing worker did not write {', '.join(missing)}")
        max_file, max_total = caps(ctx, "drawing", timeout_s)
        dest = store.ensure_subdir(run.root, "drawings")
        promoted = store.promote(outcome.out_dir, sorted(wanted), dest, max_file_bytes=max_file,
                                 max_total_bytes=max_total)
    finally:
        sandbox.finish_job(outcome.job_dir)
    files_out = {}
    for f in formats:
        info = promoted[f"{stem}.{f}"]
        files_out[f] = {"path": str(dest / f"{stem}.{f}"), "bytes": info["bytes"], "sha256": info["sha256"]}
    warnings = list(outcome.warnings or []) + [str(w) for w in data.get("warnings") or []]
    if inline and "png" not in formats:
        warnings.append("inline needs the png format: no image is returned")
    doc = {"schema": "aces.cad.drawing/1", "run_id": run.id, "part": part, "projection": projection, "units": "mm",
           "scale": "1:1", "views": data.get("views") or [], "dimensions": data.get("dimensions") or [],
           "hole_table": data.get("hole_table"), "audit_errors": data.get("audit_errors"), "files": files_out,
           "method": "OCCT hidden-line removal (build123d project_to_viewport), ezdxf DIMENSION entities on values "
                     "measured from the B-rep",
           "notes": [str(n) for n in data.get("notes") or []], "warnings": warnings}
    if data.get("section") is not None:
        doc["section"] = data.get("section")
    write_json(dest, f"{stem}.json", doc)
    out = dict(doc)
    if inline and "png" in formats:
        out["inline_images"] = [files_out["png"]["path"]]
    return out
