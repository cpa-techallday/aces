"""The aces-cad command: the MCP server's operations on the command line (spec section 18).

  aces-cad [--version] VERB ...
    doctor | selftest | start | run | params | runs | prune | inspect | render | drawing | pull | note | notes

Every verb takes --json: exactly one JSON document on stdout, for success and for failure ({"error": {...}}). Without
it, results print as short lines (image and file paths included) and errors go to stderr as `aces-cad: <kind>:
<message>`. Project verbs take --project NAME (default ACES_CAD_PROJECT). RUN defaults to latest.

Exit codes: 0 ok; 1 a failed run or operation (script, kernel, result, crash, integrity, internal); 2 usage, config,
project, not_found, params; 3 sandbox_unavailable (and doctor not ready); 4 timeout, memory, disk; 5 sandbox (a
refused operation in a run); 130 cancelled or interrupted. Ctrl-C cancels the running job (the worker's process
group is killed) before the command exits. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from pathlib import Path

from . import __version__, errors

PROG = "aces-cad"
RENDER_VIEWS = ("iso", "front", "back", "top", "bottom", "right", "left", "cutaway", "thickness")
DRAWING_VIEWS = ("front", "back", "top", "bottom", "right", "left", "iso")
CHECKS = ("mass", "validity", "topology", "holes", "thickness", "draft", "radii", "interference", "section", "measure",
          "design_space")
JOIN_AFTER_CANCEL_S = 30.0
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")


class _ArgError(Exception):
    """argparse's complaint, raised instead of printed so --json can still answer with one document."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _ArgError(f"{self.prog}: {message}")


# --- argument parsing ---------------------------------------------------------------------------------------------

def _csv(allowed: tuple, what: str):
    def parse(text: str) -> list:
        items = [t.strip() for t in text.split(",") if t.strip()]
        bad = [t for t in items if t not in allowed]
        if not items or bad:
            raise argparse.ArgumentTypeError(f"{what} must be a comma-separated list of: {', '.join(allowed)}"
                                             + (f" (not {', '.join(bad)})" if bad else ""))
        return items
    return parse


def _section(text: str) -> dict:
    axis, sep, at = text.partition("@")
    try:
        value = float(at)
    except ValueError:
        value = None
    if not sep or axis.lower() not in ("x", "y", "z") or value is None or value != value:
        raise argparse.ArgumentTypeError("--section takes AXIS@MM, for example y@20")
    return {"axis": axis.lower(), "at_mm": value}


def _vec3(text: str) -> list:
    parts = text.split(",")
    try:
        values = [float(p) for p in parts]
    except ValueError:
        values = []
    if len(values) != 3 or any(v != v for v in values):
        raise argparse.ArgumentTypeError("--pull takes X,Y,Z, for example 0,0,1")
    return values


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog=PROG, description="ACES local text-to-CAD: build123d scripts run in a sandboxed child of the "
                                       "aces-engineering venv (no network), inspected, rendered, drawn and pulled into "
                                       "the project as STEP + parameters + sha256 with a cad.pull ledger entry.")
    p.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    sub = p.add_subparsers(dest="verb", metavar="VERB", parser_class=_Parser)
    sub.required = True

    def leaf(name: str, text: str, *, project: bool = True):
        s = sub.add_parser(name, help=text, description=text)
        s.add_argument("--json", action="store_true", help="print one JSON document (also for errors)")
        if project:
            s.add_argument("--project", metavar="NAME", default=None,
                           help="the ACES project (default: ACES_CAD_PROJECT)")
        return s

    def timeout(s) -> None:
        s.add_argument("--timeout", type=int, default=None, metavar="S", help="job timeout in seconds (1-600)")

    def run_ref(s) -> None:
        s.add_argument("run", nargs="?", default="latest", metavar="RUN", help="a run id, or latest (the default)")

    leaf("doctor", "versions, kernel guards and sandbox self-checks; exit 0 ready, 3 not ready", project=False)
    s = leaf("selftest", "end to end in a temporary ACES_WORK, ACES_STATE and ACES_USER_CONFIG", project=False)
    s.add_argument("--quick", action="store_true", help="checks 1-4 only (doctor, plate, parameter, inspect)")
    s.add_argument("--keep", action="store_true", help="keep the temporary directory")
    leaf("start", "class gate, cad/NOTES.md lessons, recent runs, conventions, limits and sandbox status")

    s = leaf("run", "run a build123d script in the sandbox")
    s.add_argument("script", nargs="?", default=None, metavar="SCRIPT|-", help="a .py file, or - for stdin")
    s.add_argument("--script-path", default=None, metavar="REL", help="a .py file in the project (relative path)")
    s.add_argument("--from", dest="from_run", default=None, metavar="RUN",
                   help="re-run that run's script and inputs, keeping its overrides")
    s.add_argument("--param", action="append", default=None, metavar="K=V",
                   help="override a PARAMS default; V is JSON when it parses (70, true), else a string")
    s.add_argument("--params", default=None, metavar="JSON", help="overrides as a JSON object (--param wins)")
    s.add_argument("--input", action="append", default=None, metavar="NAME=REL",
                   help="a project file the script reads through INPUTS[NAME]")
    s.add_argument("--name", default=None, metavar="N", help="the run name")
    timeout(s)
    s.add_argument("--render", action="store_true", help="also render the iso view (small)")

    s = leaf("params", "the declared PARAMS and the entry, read with ast (no project, no run)", project=False)
    s.add_argument("script", metavar="SCRIPT|-", help="a .py file, or - for stdin")

    s = leaf("runs", "list the project's runs, or show one")
    s.add_argument("run", nargs="?", default=None, metavar="RUN", help="show this run (or latest)")
    s.add_argument("--limit", type=int, default=None, metavar="N", help="at most N runs (1-50)")
    s.add_argument("--script", action="store_true", help="include the script text")
    s.add_argument("--output", action="store_true", help="include stdout and stderr")

    s = leaf("prune", "delete runs beyond the newest N (runs named by a pull are kept)")
    s.add_argument("--keep", type=int, required=True, metavar="N")

    s = leaf("inspect", "measure a run's geometry in a worker that runs no agent code")
    run_ref(s)
    s.add_argument("--check", type=_csv(CHECKS, "--check"), default=None, metavar="C,C")
    s.add_argument("--part", default=None, metavar="P")
    group = s.add_mutually_exclusive_group()
    group.add_argument("--material", default=None, metavar="M")
    group.add_argument("--density", type=float, default=None, metavar="KG_M3")
    s.add_argument("--list-faces", action="store_true")
    s.add_argument("--section", type=_section, default=None, metavar="AXIS@MM")
    s.add_argument("--pull", type=_vec3, default=None, metavar="X,Y,Z", help="pull direction for draft")
    s.add_argument("--min-draft", type=float, default=None, metavar="DEG")
    s.add_argument("--min-thickness", type=float, default=None, metavar="MM")
    s.add_argument("--max-triangles", type=int, default=None, metavar="N")
    s.add_argument("--min-clearance", type=float, default=None, metavar="MM")
    s.add_argument("--measure", nargs=2, default=None, metavar=("A", "B"))
    s.add_argument("--gen-mm", type=float, default=None, metavar="MM")
    timeout(s)

    s = leaf("render", "PNG renders of a run in the ACES dark look")
    run_ref(s)
    s.add_argument("--view", type=_csv(RENDER_VIEWS, "--view"), default=None, metavar="V,V")
    s.add_argument("--part", default=None, metavar="P")
    s.add_argument("--size", choices=("small", "medium", "large"), default=None)
    timeout(s)

    s = leaf("drawing", "a dimensioned 1:1 mm drawing (DXF, SVG, PNG) from OCCT hidden-line removal")
    run_ref(s)
    s.add_argument("--part", default=None, metavar="P")
    s.add_argument("--views", type=_csv(DRAWING_VIEWS, "--views"), default=None, metavar="V,V")
    s.add_argument("--projection", choices=("third-angle", "first-angle"), default=None)
    s.add_argument("--format", type=_csv(("dxf", "svg", "png"), "--format"), default=None, metavar="F,F")
    s.add_argument("--no-dimensions", action="store_true")
    s.add_argument("--section", type=_section, default=None, metavar="AXIS@MM")
    s.add_argument("--title", default=None, metavar="TEXT")
    timeout(s)

    s = leaf("pull", "the CadSource pull: part.step (+ stl, 3mf), parameters, sha256 and a cad.pull ledger entry")
    run_ref(s)
    s.add_argument("--name", default=None, metavar="N")
    s.add_argument("--format", type=_csv(("step", "stl", "3mf"), "--format"), default=None, metavar="F,F")
    s.add_argument("--stl-tolerance", type=float, default=None, metavar="MM")
    s.add_argument("--angular-tolerance", type=float, default=None, metavar="RAD")
    s.add_argument("--allow-invalid", action="store_true")
    timeout(s)

    s = leaf("note", "record one lesson in cad/NOTES.md, verbatim")
    s.add_argument("text", metavar="TEXT|-", help="the lesson, or - for stdin")
    s.add_argument("--tag", action="append", default=None, metavar="T")
    s.add_argument("--run", default=None, metavar="RUN_ID")

    leaf("notes", "print the project's cad/NOTES.md")
    return p


# --- turning arguments into an operation --------------------------------------------------------------------------

def read_text(spec: str, what: str) -> str:
    """A file or stdin (-) as UTF-8 text, a leading BOM dropped."""
    try:
        if spec == "-":
            stream = getattr(sys.stdin, "buffer", None)
            data = stream.read() if stream is not None else sys.stdin.read().encode("utf-8")
        else:
            data = Path(spec).read_bytes()
    except OSError as e:
        raise errors.UsageError(f"cannot read {what} {spec}: {e.strerror or type(e).__name__}") from None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise errors.UsageError(f"{what} {spec} is not UTF-8 text") from None


def _overrides(args) -> dict | None:
    values: dict = {}
    if args.params is not None:
        try:
            obj = json.loads(args.params)
        except ValueError as e:
            raise errors.UsageError(f"--params is not valid JSON ({e})") from None
        if not isinstance(obj, dict):
            raise errors.UsageError("--params must be a JSON object mapping parameter names to values")
        values.update(obj)
    for item in args.param or []:
        key, sep, raw = item.partition("=")
        if not sep or not key:
            raise errors.UsageError(f"--param needs K=V; got {item!r}")
        try:
            values[key] = json.loads(raw)
        except ValueError:
            values[key] = raw
    return values or None


def _inputs(args) -> dict | None:
    out: dict = {}
    for item in args.input or []:
        key, sep, rel = item.partition("=")
        if not sep or not key or not rel:
            raise errors.UsageError(f"--input needs NAME=REL; got {item!r}")
        out[key] = rel
    return out or None


def _run_name(args) -> str | None:
    """--name, else a run started from a file is named after it (plate.py -> plate) when the stem is a valid name."""
    if args.name is not None:
        return args.name
    source = args.script if args.script not in (None, "-") else args.script_path
    if not source:
        return None
    stem = Path(source).stem
    return stem if NAME_RE.match(stem) else None


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def operation(args) -> tuple[str, dict]:
    """The ops.dispatch name and keyword arguments (the MCP tools' argument names) for a parsed command line."""
    v = args.verb
    project = getattr(args, "project", None)
    if v == "doctor":
        return "doctor", {}
    if v == "start":
        return "start", _drop_none({"project": project})
    if v == "run":
        given = [x for x in (args.script, args.script_path, args.from_run) if x is not None]
        if len(given) != 1:
            raise errors.UsageError("give exactly one of SCRIPT (a file or -), --script-path REL or --from RUN")
        kw = {"script_path": args.script_path, "from_run": args.from_run, "params": _overrides(args),
              "inputs": _inputs(args), "name": _run_name(args), "timeout_s": args.timeout,
              "render": True if args.render else None, "project": project}
        if args.script is not None:
            kw["script"] = read_text(args.script, "script")
        return "run", _drop_none(kw)
    if v == "params":
        return "params", {"script": read_text(args.script, "script")}
    if v == "runs":
        return "runs", _drop_none({"run_id": args.run, "limit": args.limit,
                                   "include_script": True if args.script else None,
                                   "include_output": True if args.output else None, "project": project})
    if v == "prune":
        return "prune", _drop_none({"keep": args.keep, "project": project})
    if v == "inspect":
        measure = {"a": args.measure[0], "b": args.measure[1]} if args.measure else None
        return "inspect", _drop_none({
            "run_id": args.run, "checks": args.check, "part": args.part, "material": args.material,
            "density_kg_m3": args.density, "list_faces": True if args.list_faces else None, "section": args.section,
            "pull_direction": args.pull, "min_draft_deg": args.min_draft, "min_thickness_mm": args.min_thickness,
            "max_triangles": args.max_triangles, "min_clearance_mm": args.min_clearance, "measure": measure,
            "gen_mm": args.gen_mm, "timeout_s": args.timeout, "project": project})
    if v == "render":
        return "render", _drop_none({"run_id": args.run, "views": args.view, "part": args.part, "size": args.size,
                                     "timeout_s": args.timeout, "project": project})
    if v == "drawing":
        return "drawing", _drop_none({"run_id": args.run, "part": args.part, "views": args.views,
                                      "projection": args.projection, "formats": args.format,
                                      "dimensions": False if args.no_dimensions else None, "section": args.section,
                                      "title": args.title, "timeout_s": args.timeout, "project": project})
    if v == "pull":
        return "pull", _drop_none({"run_id": args.run, "name": args.name, "formats": args.format,
                                   "stl_tolerance_mm": args.stl_tolerance,
                                   "angular_tolerance_rad": args.angular_tolerance,
                                   "allow_invalid": True if args.allow_invalid else None, "timeout_s": args.timeout,
                                   "project": project})
    if v == "note":
        text = read_text(args.text, "note") if args.text == "-" else args.text
        return "note", _drop_none({"text": text, "tags": args.tag, "run_id": args.run, "project": project})
    if v == "notes":
        return "notes", _drop_none({"project": project})
    raise errors.UsageError(f"unknown verb {v}")


# --- human output -------------------------------------------------------------------------------------------------

def _num(value, fmt: str = ".2f") -> str:
    return format(value, fmt) if isinstance(value, (int, float)) and not isinstance(value, bool) else "-"


def _size(bbox) -> str:
    if not isinstance(bbox, list) or len(bbox) != 6:
        return "-"
    return " \u00d7 ".join(format(bbox[i + 3] - bbox[i], ".4g") for i in range(3))


def _warnings(r: dict) -> list:
    return [f"warning: {w}" for w in r.get("warnings") or []]


def _images(r: dict) -> list:
    return [f"image {p}" for p in r.get("inline_images") or []]


def _part_line(p: dict) -> str:
    valid = "valid" if p.get("valid") else f"INVALID ({', '.join(p.get('reasons') or []) or 'see problems'})"
    vol = f"volume {_num(p.get('volume_mm3'))} mm\u00b3" if p.get("volume_mm3") is not None else \
        f"area {_num(p.get('area_mm2'))} mm\u00b2"
    dot = " \u00b7 "
    return f"{p.get('name')}{dot}{p.get('kind')}{dot}{vol}{dot}bbox {_size(p.get('bbox_mm'))} mm{dot}{valid}"


def _h_run(r: dict) -> list:
    result = r.get("result") or {}
    parts = result.get("parts") or []
    if len(parts) == 1:
        head = f"run {r.get('run_id')} {r.get('status')} \u00b7 {_part_line(parts[0])}"
        lines = [head]
    else:
        totals = result.get("totals") or {}
        lines = [f"run {r.get('run_id')} {r.get('status')} \u00b7 {r.get('name')} \u00b7 {result.get('kind')} \u00b7 "
                 f"{len(parts)} parts \u00b7 volume {_num(totals.get('volume_mm3'))} mm\u00b3 \u00b7 bbox "
                 f"{_size(totals.get('bbox_mm'))} mm"]
        lines += [f"  part {_part_line(p)}" for p in parts]
    over = r.get("params_overridden") or []
    if over:
        lines.append("params " + ", ".join(f"{k}={(r.get('params') or {}).get(k)!r}" for k in over))
    for prob in r.get("problems") or []:
        lines.append(f"problem {prob.get('part')}: {prob.get('code')}: {prob.get('detail')}")
    if r.get("stdout"):
        lines.append("stdout:")
        lines.append(str(r["stdout"]).rstrip("\n"))
    for rr in r.get("renders") or []:
        lines.append(f"render {rr.get('view')} {rr.get('path')}")
    if r.get("run_dir"):
        lines.append(f"run dir {r['run_dir']}")
    return lines + _warnings(r)


def _h_start(r: dict) -> list:
    info = r.get("project") or {}
    notes = r.get("notes") or {}
    lines = [f"project {info.get('name')} ({info.get('class')}, from {info.get('class_source')}) {info.get('dir')}",
             f"ready: {'yes' if r.get('ready') else 'NO (run aces-cad doctor)'}"]
    if notes.get("exists"):
        lines.append(f"notes: {notes.get('entries_returned')} of {notes.get('entries_total')} entries "
                     f"({(r.get('paths') or {}).get('notes')})")
        if notes.get("text"):
            lines.append(str(notes["text"]).rstrip("\n"))
    for run in r.get("recent_runs") or []:
        lines.append(f"{run.get('run_id')}  {str(run.get('status')):6s} {run.get('name')}  {run.get('kind') or '-'}")
    return lines + _warnings(r)


def _h_params(r: dict) -> list:
    lines = [f"entry {r.get('entry')}"]
    for name, spec in (r.get("declared") or r.get("params_declared") or {}).items():
        if not isinstance(spec, dict):
            continue
        rng = ""
        if "min" in spec or "max" in spec:
            rng = f" [{spec.get('min', '')}..{spec.get('max', '')}]"
        choices = f" choices {spec['choices']}" if spec.get("choices") else ""
        unit = f" {spec['unit']}" if spec.get("unit") else ""
        doc = f"  {spec['doc']}" if spec.get("doc") else ""
        lines.append(f"{name}: {spec.get('type')} = {spec.get('default')!r}{unit}{rng}{choices}{doc}")
    return lines + _warnings(r)


def _h_runs(r: dict) -> list:
    if "run" in r:
        run = r.get("run") or {}
        lines = [json.dumps(run, indent=1, ensure_ascii=False)]
        if r.get("script") is not None:
            lines += ["script.py:", str(r["script"]).rstrip("\n")]
        for key in ("stdout", "stderr"):
            if r.get(key):
                lines += [f"{key}:", str(r[key]).rstrip("\n")]
        return lines
    rows = r.get("runs") or []
    if not rows:
        return ["no runs"]
    lines = []
    for run in rows:
        valid = {True: "valid", False: "invalid"}.get(run.get("valid"), "-")
        lines.append(f"{run.get('run_id')}  {str(run.get('status')):6s} {run.get('name')}  {run.get('kind') or '-'}  "
                     f"{_num(run.get('volume_mm3'))} mm\u00b3  {valid}"
                     + (f"  error {run.get('error_kind')}" if run.get("error_kind") else ""))
    lines.append(f"{r.get('count')} of {r.get('total')} runs")
    return lines


def _h_inspect(r: dict) -> list:
    lines = [f"inspect {r.get('inspect_id')} of run {r.get('run_id')}"]
    for name, out in (r.get("checks") or {}).items():
        text = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
        lines.append(f"{name}: {text if len(text) <= 400 else text[:400] + '...'}")
    if r.get("report_path"):
        lines.append(f"report {r['report_path']}")
    return lines + _warnings(r)


def _h_render(r: dict) -> list:
    lines = [f"render {x.get('view')} {x.get('path')} ({x.get('width_px')}\u00d7{x.get('height_px')} px, "
             f"{x.get('bytes')} bytes)" for x in r.get("renders") or []]
    return (lines or ["no renders"]) + _warnings(r)


def _h_drawing(r: dict) -> list:
    dims = r.get("dimensions") or []
    texts = ", ".join(str(d.get("text")) for d in dims[:12]) + (", ..." if len(dims) > 12 else "")
    lines = [f"drawing of run {r.get('run_id')} ({r.get('projection')}): {len(dims)} dimensions ({texts}); audit "
             f"{r.get('audit_errors')} errors"]
    for fmt, info in (r.get("files") or {}).items():
        lines.append(f"{fmt} {info.get('path')} ({info.get('bytes')} bytes)")
    lines += [f"note: {n}" for n in r.get("notes") or []]
    return lines + _images(r) + _warnings(r)


def _h_pull(r: dict) -> list:
    lines = [f"bundle {r.get('bundle')}", f"ref {r.get('ref')} \u00b7 {r.get('kind')} \u00b7 "
             f"{'valid' if r.get('valid') else 'NOT valid (pulled with allow_invalid)'}",
             f"geometry_sha256 {r.get('geometry_sha256')}"]
    for name, info in (r.get("files") or {}).items():
        lines.append(f"  {name:18s} {str(info.get('bytes')):>10} bytes  sha256 {info.get('sha256')}")
    ledger = r.get("ledger") or {}
    lines.append(f"ledger: cad.pull written (entry {ledger.get('n')}, hash {str(ledger.get('hash'))[:12]})"
                 if ledger.get("written") else f"ledger: not written: {ledger.get('reason')}")
    cad = r.get("cad_source") or {}
    lines.append(f"cad source: recorded {cad.get('ref')}" if cad.get("recorded")
                 else f"cad source: not recorded: {cad.get('reason')}")
    golden = r.get("golden") or {}
    if golden.get("applicable"):
        lines.append(f"golden: next {golden.get('next')}: {golden.get(golden.get('next') or 'init')}")
    else:
        lines.append(f"golden: not applicable: {golden.get('why_not')}")
    return lines + _warnings(r)


def _h_note(r: dict) -> list:
    entry = r.get("entry") or {}
    return [f"noted entry {entry.get('n')} ({entry.get('bytes')} bytes) in {r.get('path')}; "
            f"{r.get('entries_total')} entries, {r.get('bytes_total')} bytes"]


def _h_notes(r: dict) -> list:
    text = r.get("text")
    if isinstance(text, str):
        return [text.rstrip("\n")] if text else [f"no notes yet ({r.get('path')})"]
    return [json.dumps(r, indent=1, ensure_ascii=False)]


def _h_prune(r: dict) -> list:
    deleted = r.get("deleted") or []
    return [f"deleted {len(deleted)} runs" + (": " + ", ".join(map(str, deleted)) if deleted else "")] + \
        [f"kept {len(r.get('kept') or [])} runs"] + _warnings(r)


def _h_doctor(r: dict) -> list:
    sb = r.get("sandbox") or {}
    worker = r.get("worker") or {}
    lines = [f"verdict {r.get('verdict')} (mode {r.get('mode')}); worker {worker.get('python')} "
             f"{'ok' if worker.get('ok') else 'NOT ok'}",
             f"sandbox: confined {sb.get('confined')}; missing {', '.join(sb.get('missing') or []) or 'nothing'}"]
    for c in r.get("checks") or []:
        status = c.get("pass")
        mark = "PASS" if status is True else ("SKIP" if status in (None, "skipped") else "FAIL")
        lines.append(f"{mark}  {c.get('check')}: expect {c.get('expect')}, got {c.get('got')}")
    return lines + _warnings(r)


HUMAN = {"run": _h_run, "start": _h_start, "params": _h_params, "runs": _h_runs, "inspect": _h_inspect,
         "render": _h_render, "drawing": _h_drawing, "pull": _h_pull, "note": _h_note, "notes": _h_notes,
         "prune": _h_prune, "doctor": _h_doctor}


# --- running --------------------------------------------------------------------------------------------------------

def _out(text: str) -> None:
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    sys.stdout.flush()


def _err(text: str) -> None:
    sys.stderr.write(f"{PROG}: {text}\n")
    sys.stderr.flush()


def _fail(e: Exception, as_json: bool) -> int:
    if isinstance(e, errors.CadError):
        data = e.to_dict()
        code = int(getattr(e, "exit_code", 1))
    else:
        data = {"kind": "internal", "message": f"internal error: {type(e).__name__}: {e}", "exit_code": 1}
        code = 1
    if as_json:
        _out(json.dumps({"error": data}, ensure_ascii=True, default=str))
    else:
        _err(f"{data.get('kind')}: {data.get('message')}")
    return code


def _dispatch(name: str, kwargs: dict, as_json: bool):
    """ops.dispatch in a thread, so Ctrl-C in the main thread can cancel the job (the worker runs in its own session
    and never sees the terminal's SIGINT)."""
    from . import ops

    cancel = threading.Event()
    box: dict = {}
    tty = (not as_json) and bool(getattr(sys.stderr, "isatty", lambda: False)())

    def progress(message) -> None:
        if tty:
            _err(str(message))

    done = threading.Event()

    def target() -> None:
        try:
            box["result"] = ops.dispatch(name, kwargs, cancel=cancel, progress=progress)
        except BaseException as e:  # noqa: BLE001 (handed to the main thread)
            box["error"] = e
        finally:
            done.set()

    worker = threading.Thread(target=target, name=f"aces-cad-{name}", daemon=True)
    worker.start()
    # wait on an Event, not Thread.join: on Python 3.11 a join interrupted by Ctrl-C can mark the thread as stopped
    # while it still runs, and the job would not be waited for after the cancel
    try:
        while not done.wait(0.2):
            pass
    except KeyboardInterrupt:
        cancel.set()
        done.wait(JOIN_AFTER_CANCEL_S)
        raise
    if "error" in box:
        raise box["error"]
    return box.get("result")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="backslashreplace")
            except (ValueError, OSError):
                pass
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    try:
        args = build_parser().parse_args(argv)
    except _ArgError as e:
        return _fail(errors.UsageError(str(e)), as_json)
    except SystemExit as e:  # --help and --version
        return e.code if isinstance(e.code, int) else 0
    as_json = bool(getattr(args, "json", False))
    if args.verb == "selftest":
        from . import selftest

        return selftest.main(argv[argv.index("selftest") + 1:])
    try:
        name, kwargs = operation(args)
        result = _dispatch(name, kwargs, as_json)
    except KeyboardInterrupt:
        return _fail(errors.Cancelled("interrupted: the job was cancelled"), as_json)
    except Exception as e:  # noqa: BLE001 (every failure becomes one error line or one JSON document)
        return _fail(e, as_json)
    if not isinstance(result, dict):
        return _fail(errors.InternalError(f"the {name} operation returned no result"), as_json)
    if as_json:
        _out(json.dumps(result, indent=1, ensure_ascii=True, default=str))
    else:
        _out("\n".join(HUMAN.get(name, lambda r: [json.dumps(r, indent=1, ensure_ascii=False)])(result)))
    if name == "doctor" and result.get("verdict") != "ready":
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
