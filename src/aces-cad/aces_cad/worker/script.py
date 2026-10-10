"""The run op: execute an agent's script after the audit hook, normalize its result, write the outputs, summarize.

Execution (section 7.4): compile(text, "script.py", "exec", dont_inherit=True) in a namespace with __name__
"__aces_cad_script__", __file__ <run_dir>/script.py, the builtins and INPUTS; exec, then the entry (build(p) or
gen_step()) with Params(values). The values come from the job, never from the script's PARAMS at run time.

Outputs (section 7.5), written into <job_dir>/out/ in this order and before any summary is computed (so no
triangulation is stored): p<i>.brep for B-rep parts, p<i>.npz (vertices float64 Nx3, faces int64 Mx3) for mesh
parts, d<i>.brep for datums, then parts.json.

Errors (section 8.7) keep the kernel's message verbatim, the script's own frames, where the exception was raised and
a hint for the known OCCT failures. Nothing is simplified, retried or repaired.
"""
from __future__ import annotations

import builtins
import errno
import hashlib
import json
import os
import re
import time
import traceback
from collections.abc import Mapping
from pathlib import Path

from aces_cad.worker.main import Ctx, WorkerError

PART_NAME_RE = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
DATUM_NAME_RE = r"@[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
MAX_PARTS = 64
MAX_DATUMS = 64
MAX_FACES = 200_000
MAX_TRIANGLES = 2_000_000
HINTS = (
    ("Failed creating a fillet", "the radius is too large for the adjacent faces: reduce it, fillet fewer edges, or "
                                 "fillet before booleans; max_fillet() can itself fail ('Failed to find the max "
                                 "value')"),
    ("Failed creating a chamfer", "the chamfer is too large for the adjacent faces: reduce it, chamfer fewer edges, or "
                                  "chamfer before booleans"),
    ("More than one wire is required", "outside a builder use Solid.make_loft(wires)"),
    ("offset Error", "OCC offset fails on multi-facet lofts: offset the 2-D sections and loft them instead"),
    ("BRepSweep_Translation", "an extrude or sweep of zero length, or along a direction in the profile's plane"),
)
KERNEL_MARKERS = ("StdFail_NotDone", "Standard_Failure", "Standard_ConstructionError", "BRep_API: command not done")
BUILDER_HINT = "return p.part (or p.sketch, p.line), not the builder"
EMPTY = ("the result is empty: a boolean produced nothing (a & b of disjoint shapes gives an empty Compound; "
         "a.intersect(b) gives None)")
SURFACES = "surfaces are not parts: return closed solids, a Sketch for 2-D, or a mesh"
SHELL = "an open shell is not a solid: close it (Solid(shell)) or return its faces as a Sketch"
# kinds a WorkerError raised through agent code (aces_cad.worker.sdf) may carry; others are reported as script
SCRIPT_KINDS = ("script", "kernel", "result", "memory", "disk", "sandbox")


class Params(Mapping):
    """The script's parameters: a read-only mapping with attribute access (p.length, p["length"])."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping):
        object.__setattr__(self, "_values", dict(values))

    def _missing(self, name):
        declared = ", ".join(sorted(self._values)) or "none"
        return f"no parameter {name!r}: the script declares {declared}"

    def __getitem__(self, name):
        try:
            return self._values[name]
        except KeyError:
            raise KeyError(self._missing(name)) from None

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        try:
            return object.__getattribute__(self, "_values")[name]
        except KeyError:
            raise AttributeError(self._missing(name)) from None

    def __setattr__(self, name, value):
        raise TypeError("the parameters are read-only: change them with cad_run params, not in the script")

    def __delattr__(self, name):
        raise TypeError("the parameters are read-only: change them with cad_run params, not in the script")

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __repr__(self):
        return f"Params({self._values!r})"


# --- error classification ---------------------------------------------------------------------------------------------

def _chain(exc: BaseException):
    seen = 0
    while exc is not None and seen < 20:
        yield exc
        exc = exc.__cause__ or exc.__context__
        seen += 1


def _inside(path: str, dirs) -> bool:
    try:
        real = os.path.realpath(os.fsdecode(path))
    except (TypeError, ValueError):
        return False
    for d in dirs:
        d = os.path.realpath(d)
        if real == d or real.startswith(d.rstrip(os.sep) + os.sep):
            return True
    return False


SOURCE: list[str] = []  # the script's lines, for frames (linecache cannot find "script.py")


def _frames(exc: BaseException) -> list[dict]:
    out = []
    for fr in traceback.extract_tb(exc.__traceback__):
        if fr.filename == "script.py":
            code = fr.line or ""
            if not code and fr.lineno and 0 < fr.lineno <= len(SOURCE):
                code = SOURCE[fr.lineno - 1].strip()
            out.append({"line": fr.lineno, "function": fr.name, "code": code[:200]})
    return out[-8:]


def _raised_in(exc: BaseException) -> str | None:
    tb = traceback.extract_tb(exc.__traceback__)
    if tb:
        path = tb[-1].filename.replace("\\", "/")
        parts = path.split("/")
        if "build123d" in parts[:-1]:
            i = len(parts) - 1 - parts[::-1].index("build123d")
            return f"build123d/{'/'.join(parts[i + 1:])}:{tb[-1].lineno}"
        if "OCP" in parts[:-1]:
            return "OCP"
    if type(exc).__module__.startswith("OCP"):
        return "OCP"
    return None


def _hint(message: str) -> str | None:
    for prefix, hint in HINTS:
        if message.startswith(prefix) or prefix in message[:200]:
            return hint
    return None


def classify(exc: BaseException, rw_dirs) -> tuple[str, dict]:
    """(kind, extra) of an exception from the script (section 8.7)."""
    if isinstance(exc, WorkerError):
        return (exc.kind if exc.kind in SCRIPT_KINDS else "script"), dict(exc.extra)
    if isinstance(exc, SyntaxError):
        return "script", {"line": exc.lineno, "offset": exc.offset, "text": (exc.text or "").rstrip("\n")[:400]}
    for e in _chain(exc):
        if isinstance(e, MemoryError) or type(e).__name__ == "Standard_OutOfMemory":
            return "memory", {"limit": "memory_mb"}
    for e in _chain(exc):
        if ((isinstance(e, PermissionError) or type(e).__name__ == "AuditRefusal")
                and str(e).startswith("aces-cad sandbox:")):
            event = str(e)[len("aces-cad sandbox:"):].strip().split(" ")[0]
            return "sandbox", {"event": event}
    for e in _chain(exc):
        if isinstance(e, OSError) and e.errno in (errno.EACCES, errno.EPERM, errno.EROFS, errno.EXDEV):
            names = [n for n in (e.filename, e.filename2) if n is not None]
            outside = [n for n in names if not _inside(n, rw_dirs)]
            if not names or outside or e.filename2 is not None:
                shown = outside[0] if outside else (names[-1] if names else None)
                extra = {"event": f"errno {e.errno} ({errno.errorcode.get(e.errno, '')})"}
                if shown is not None:
                    extra["path"] = os.fsdecode(shown)
                return "sandbox", extra
    for e in _chain(exc):
        if isinstance(e, OSError) and e.errno == errno.EFBIG:
            return "disk", {"limit": "file_size"}
    message = str(exc)
    if (type(exc).__module__.startswith("OCP") or message.startswith("Failed creating")
            or any(m in message for m in KERNEL_MARKERS) or type(exc).__name__ in KERNEL_MARKERS):
        return "kernel", {}
    return "script", {}


def _script_error(exc: BaseException, rw_dirs) -> WorkerError:
    kind, extra = classify(exc, rw_dirs)
    message = str(exc)
    if isinstance(exc, SystemExit):
        kind, message = "script", f"the script called sys.exit({exc.code!r:.60})"
    elif kind == "memory":
        message = f"the run ran out of memory (RLIMIT_AS): {type(exc).__name__}: {message}"[:2000]
    elif not message:
        message = type(exc).__name__
    if kind in ("script", "kernel"):
        extra.update(exception=type(exc).__name__, frames=_frames(exc), raised_in=_raised_in(exc))
        hint = _hint(message)
        if hint:
            extra["hint"] = hint
        if isinstance(exc, SyntaxError):
            extra.update(line=exc.lineno, offset=exc.offset, text=(exc.text or "").rstrip("\n")[:400])
        elif extra["frames"]:
            extra.setdefault("line", extra["frames"][-1]["line"])
    elif kind == "sandbox":
        extra.update(exception=type(exc).__name__, frames=_frames(exc))
    return WorkerError(kind, message[:4000], **{k: v for k, v in extra.items() if v is not None})


# --- result normalization ---------------------------------------------------------------------------------------------

def _bd():
    import build123d
    return build123d


def _is_mesh(obj) -> bool:
    try:
        import trimesh
    except ImportError:
        return False
    return isinstance(obj, trimesh.Trimesh)


def _type_name(obj) -> str:
    return type(obj).__name__


def _refuse(message: str, **extra) -> WorkerError:
    return WorkerError("result", message, **extra)


def _shape_kind(obj, where: str) -> tuple[str, object, list[str]]:
    """(kind, shape, problems) of one returned object: solid, sketch or mesh; WorkerError("result") otherwise."""
    bd = _bd()
    if _is_mesh(obj):
        return "mesh", obj, []
    builders = tuple(getattr(bd, n) for n in ("BuildPart", "BuildSketch", "BuildLine") if hasattr(bd, n))
    if isinstance(obj, builders):
        return _raise(_refuse(f"{where} is a {_type_name(obj)} builder, not a shape", hint=BUILDER_HINT))
    if isinstance(obj, bd.Shell):
        return _raise(_refuse(f"{where}: {SHELL}"))
    if isinstance(obj, (bd.Wire, bd.Edge, bd.Vertex)):
        return _raise(_refuse(f"{where} is a {_type_name(obj)}; return a solid (Part/Solid/Compound), a Sketch for "
                              f"2-D, or a mesh"))
    if isinstance(obj, bd.Face):
        if obj.geom_type != bd.GeomType.PLANE:
            return _raise(_refuse(f"{where} is a non-planar Face: {SURFACES}"))
        return "sketch", obj, []
    if isinstance(obj, bd.Solid):
        return "solid", obj, []
    if isinstance(obj, bd.Compound):
        if _empty(obj):
            return _raise(_refuse(EMPTY))
        solids = obj.solids()
        if solids:
            leftovers = []
            n_faces = len(obj.faces())
            solid_faces = sum(len(s.faces()) for s in solids)
            if n_faces > solid_faces or len(obj.edges()) > sum(len(s.edges()) for s in solids):
                leftovers.append("leftovers")
            return "solid", obj, leftovers
        faces = obj.faces()
        if not faces:
            return _raise(_refuse(EMPTY if not obj.edges() and not obj.vertices() else
                                  f"{where} is a {_type_name(obj)} of edges or vertices only; return a solid, a "
                                  f"Sketch or a mesh"))
        if any(f.geom_type != bd.GeomType.PLANE for f in faces):
            return _raise(_refuse(f"{where} holds non-planar faces: {SURFACES}"))
        return "sketch", obj, []
    if obj is None:
        return _raise(_refuse(f"{where} is None (the entry returned nothing; a.intersect(b) of disjoint shapes "
                              f"returns None)"))
    return _raise(_refuse(f"{where} is a {_type_name(obj)}; return a Part/Solid/Compound, a Sketch or planar Face, "
                          f"a dict or list of parts, or a trimesh.Trimesh"))


def _raise(err: WorkerError):
    raise err


def _empty(obj) -> bool:
    """An empty Compound (a & b of disjoint shapes): no wrapped TopoDS shape, or a null one."""
    if getattr(obj, "_wrapped", None) is None:
        return True
    try:
        return bool(obj.is_null)
    except Exception:  # noqa: BLE001
        return False


def _labelled_children(obj) -> list | None:
    bd = _bd()
    if not isinstance(obj, bd.Compound) or isinstance(obj, bd.Sketch):
        return None
    children = list(getattr(obj, "children", ()) or ())
    if len(children) >= 2 and all(isinstance(getattr(c, "label", None), str) and c.label for c in children):
        return children
    return None


def _datum(name: str, value) -> tuple[str, object]:
    bd = _bd()
    if isinstance(value, (bd.Face, bd.Edge, bd.Vertex)):
        return type(value).__name__ if type(value).__name__ in ("Face", "Edge", "Vertex") else "Face", value
    if isinstance(value, bd.Vector):
        return "Vertex", bd.Vertex(value.X, value.Y, value.Z)
    if (isinstance(value, (tuple, list)) and len(value) == 3
            and all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in value)):
        return "Vertex", bd.Vertex(*[float(c) for c in value])
    raise _refuse(f"datum {name} must be a Face, Edge, Vertex, Vector or (x, y, z); got {_type_name(value)}")


def normalize(value, run_name: str, warnings: list[str]) -> dict:
    """{"kind", "parts": [(name, kind, obj, problems)], "datums": [(name, type, shape)]} for a returned value."""
    items: list[tuple[str, object]] = []
    datums: list[tuple[str, str, object]] = []
    container = None
    if isinstance(value, dict):
        container = "dict"
        for key, item in value.items():
            if not isinstance(key, str):
                raise _refuse(f"result dict keys must be names; got {_type_name(key)}")
            if key.startswith("@"):
                if not re.fullmatch(DATUM_NAME_RE, key):
                    raise _refuse(f"datum name {key!r:.70} must match {DATUM_NAME_RE}")
                dtype, shape = _datum(key, item)
                datums.append((key, dtype, shape))
            else:
                if not re.fullmatch(PART_NAME_RE, key):
                    raise _refuse(f"part name {key!r:.70} must match {PART_NAME_RE}")
                items.append((key, item))
    elif isinstance(value, (list, tuple)):
        container = "list"
        if len(value) > MAX_PARTS:
            raise _refuse(f"the result lists {len(value)} parts; at most {MAX_PARTS} are allowed")
        names: list[str] = []
        for i, item in enumerate(value, 1):
            label = getattr(item, "label", None)
            if isinstance(label, str) and label and re.fullmatch(PART_NAME_RE, label):
                base = label
            else:
                if isinstance(label, str) and label:
                    warnings.append(f"part {i}'s label {label[:40]!r} is not a valid part name; it is called "
                                    f"part{i}")
                base = f"part{i}"
            name, n = base, 2
            while name in names:
                name, n = f"{base}-{n}", n + 1
            names.append(name)
            items.append((name, item))
    else:
        children = _labelled_children(value)
        if children is not None:
            container = "compound"
            names = []
            for i, c in enumerate(children, 1):
                base = c.label if re.fullmatch(PART_NAME_RE, c.label) else f"part{i}"
                name, n = base, 2
                while name in names:
                    name, n = f"{base}-{n}", n + 1
                names.append(name)
                items.append((name, c))
        else:
            items.append((run_name, value))
    if not items:
        raise _refuse("the result has no parts (only datums)" if datums else EMPTY)
    if len(items) > MAX_PARTS:
        raise _refuse(f"the result has {len(items)} parts; at most {MAX_PARTS} are allowed")
    if len(datums) > MAX_DATUMS:
        raise _refuse(f"the result has {len(datums)} datums; at most {MAX_DATUMS} are allowed")
    parts = []
    for name, obj in items:
        where = f"part {name}" if container else "the result"
        kind, shape, problems = _shape_kind(obj, where)
        parts.append((name, kind, shape, problems))
    kinds = {p[1] for p in parts}
    if "mesh" in kinds and kinds != {"mesh"}:
        raise _refuse("meshes and B-rep parts cannot be mixed in one result: return them from separate runs")
    if "mesh" in kinds and datums:
        raise _refuse("datums need B-rep parts; a mesh result cannot carry them")
    for name, kind, shape, _p in parts:
        if kind == "mesh":
            if len(shape.faces) > MAX_TRIANGLES:
                raise _refuse(f"mesh part {name} has {len(shape.faces)} triangles; at most {MAX_TRIANGLES}")
            if len(shape.faces) == 0:
                raise _refuse(f"mesh part {name} has no triangles")
        else:
            n = len(shape.faces())
            if n > MAX_FACES:
                raise _refuse(f"part {name} has {n} faces; at most {MAX_FACES}")
    run_kind = "assembly" if len(parts) >= 2 else parts[0][1]
    return {"kind": run_kind, "parts": parts, "datums": datums}


def _fresh_out_dir(ctx: Ctx) -> None:
    """Move anything the script left in out/ aside and start from an empty out/ (files a script writes are discarded;
    without Landlock a planted link must not redirect OCCT's writes)."""
    try:
        entries = os.listdir(ctx.out_dir)
    except FileNotFoundError:
        entries = []
    if entries:
        os.rename(ctx.out_dir, ctx.job_dir / f"out-discarded-{os.urandom(4).hex()}")
        ctx.warn("the script wrote into the job's out/ directory; those files were discarded")
    os.makedirs(ctx.out_dir, mode=0o700, exist_ok=True)
    ctx.files.clear()


def write_outputs(norm: dict, ctx: Ctx) -> dict:
    """p<i>.brep|npz, d<i>.brep, then parts.json (before any summary)."""
    bd = _bd()
    import numpy as np
    _fresh_out_dir(ctx)
    doc = {"schema": "aces.cad.parts/1", "kind": norm["kind"], "parts": [], "datums": []}
    for i, (name, kind, shape, _problems) in enumerate(norm["parts"]):
        if kind == "mesh":
            fname = f"p{i}.npz"
            with open(ctx.out_file(fname), "wb") as fh:
                np.savez(fh, vertices=np.asarray(shape.vertices, dtype=np.float64),
                         faces=np.asarray(shape.faces, dtype=np.int64))
        else:
            fname = f"p{i}.brep"
            if not bd.export_brep(shape, str(ctx.out_file(fname))):
                raise WorkerError("kernel", f"OCCT could not write the BREP of part {name}")
        doc["parts"].append({"index": i, "name": name, "kind": kind, "file": fname})
    for i, (name, dtype, shape) in enumerate(norm["datums"]):
        fname = f"d{i}.brep"
        if not bd.export_brep(shape, str(ctx.out_file(fname))):
            raise WorkerError("kernel", f"OCCT could not write the BREP of datum {name}")
        doc["datums"].append({"index": i, "name": name, "type": dtype, "file": fname})
    with open(ctx.out_file("parts.json"), "wb") as fh:
        fh.write(json.dumps(doc, ensure_ascii=True, indent=1).encode("ascii"))
    return doc


def _envelope(value, entry: str, warnings: list[str]):
    if entry == "gen_step" and isinstance(value, dict) and "shape" in value:
        if len(value) > 1:
            warnings.append("the gen_step envelope's other keys are ignored")
        return value["shape"]
    return value


def op_run(args: dict, ctx: Ctx) -> dict:
    """Run the script in this process (after the guards and the audit hook) and summarize its result."""
    from aces_cad.worker import geom
    run_dir = ctx.run_dir
    if run_dir is None:
        raise WorkerError("internal", "the run job has no run directory")
    path = run_dir / str(args.get("script") or "script.py")
    with open(path, "rb") as fh:
        raw = fh.read(4 << 20)
    if hashlib.sha256(raw).hexdigest() != args.get("script_sha256"):
        raise WorkerError("integrity", "script.py does not match the sha256 the server recorded")
    text = raw.decode("utf-8")
    SOURCE[:] = text.splitlines()
    rw_dirs = list(ctx.state["roots"].rw_dirs) if ctx.state.get("roots") else [str(ctx.job_dir)]
    t = time.monotonic()
    try:
        code = compile(text, "script.py", "exec", dont_inherit=True)
    except SyntaxError as e:
        raise WorkerError("script", f"SyntaxError: {e.msg} (script.py, line {e.lineno})", line=e.lineno,
                          offset=e.offset, text=(e.text or "").rstrip("\n")[:400], exception="SyntaxError") from None
    ns = {"__name__": "__aces_cad_script__", "__file__": str(run_dir / "script.py"), "__builtins__": builtins,
          "INPUTS": {str(k): str(v) for k, v in (args.get("inputs") or {}).items()}}
    ctx.state["namespace"] = ns
    entry = str(args.get("entry") or "build")
    try:
        exec(code, ns)
        fn = ns.get(entry)
        if not callable(fn):
            raise WorkerError("script", "define def build(p) (or gen_step()) that returns the part")
        value = fn(Params(args.get("params") or {})) if args.get("entry_takes_p", True) else fn()
    except WorkerError:
        raise
    except BaseException as e:  # noqa: BLE001 (every failure of the script is classified)
        if isinstance(e, GeneratorExit):
            raise
        raise _script_error(e, rw_dirs) from None
    ctx.timings["exec"] = time.monotonic() - t
    warnings: list[str] = []
    try:
        value = _envelope(value, entry, warnings)
        norm = normalize(value, str(args.get("name") or "part"), warnings)
        t = time.monotonic()
        write_outputs(norm, ctx)
        ctx.timings["write"] = time.monotonic() - t
    except WorkerError:
        raise
    except BaseException as e:  # noqa: BLE001
        raise _script_error(e, rw_dirs) from None
    for w in warnings:
        ctx.warn(w)
    del value
    ns.clear()
    t = time.monotonic()
    summaries = []
    problems = []
    for i, (name, kind, shape, extra_problems) in enumerate(norm["parts"]):
        part = geom.LoadedPart(index=i, name=name, kind=kind, shape=None if kind == "mesh" else shape,
                               mesh=shape if kind == "mesh" else None)
        summary = geom.summarize(part)
        summaries.append(summary)
        for reason in summary.get("reasons") or []:
            problems.append({"part": name, "code": reason, "detail": geom.REASON_TEXT.get(reason, reason)})
        n_solids = (summary.get("counts") or {}).get("solids") or 0
        if kind == "solid" and n_solids > 1:
            problems.append({"part": name, "code": "multi_solid",
                             "detail": f"part {name} holds {n_solids} solids; volume is their sum, overlaps count "
                                       f"twice: cad_inspect validity checks self-intersection"})
        if "leftovers" in extra_problems:
            problems.append({"part": name, "code": "leftovers",
                             "detail": f"part {name} also holds faces, edges or vertices outside its solids; they "
                                       f"are kept in the BREP but are not part of the volume"})
    datums = [{"name": n, "type": t} for n, t, _s in norm["datums"]]
    ctx.timings["analyse"] = time.monotonic() - t
    volumes = [s.get("volume_mm3") for s in summaries]
    boxes = [s.get("bbox_mm") for s in summaries if s.get("bbox_mm")]
    total_bbox = None
    if boxes:
        total_bbox = [min(b[i] for b in boxes) for i in range(3)] + [max(b[i] for b in boxes) for i in range(3, 6)]
    totals = {"parts": len(summaries),
              "volume_mm3": sum(v for v in volumes if v is not None) if any(v is not None for v in volumes) else None,
              "bbox_mm": total_bbox}
    return {"result": {"kind": norm["kind"], "parts": summaries, "datums": datums, "totals": totals},
            "problems": problems}
