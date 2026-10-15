"""The desktop's records (build spec 3.8, 3.9, 3.10): projects, golden runs, the CAD store. Builder B.

Everything here is read from $ACES_WORK through the safefs rules (no link following, size caps, the content-type
allow-list); nothing is written. The two actions are reads too: `aces golden verify` and `check_bundle.py` run as
capped subprocesses and are not ledgered.

    GET  /api/projects                              every <WORK>/<name>/aces.yaml (aces.project._load) with counts
    GET  /api/projects/{name}                       the row plus its ledger entries, golden runs and CAD runs
    GET  /api/golden/runs?project=                  */golden/*/run.json rows, newest first (golden_cli._summary fields)
    GET  /api/golden/run/{project}/{run_id}         run.json, result.json (run.json fallback), validation, compute_used,
                                                    report, sweep.csv, winner, figures, evidence ledgers, files, events tail
    GET  /api/golden/file/{project}/{run_id}/<rel>  a file of the run under CSP: sandbox (.joblib/.pkl as octet-stream)
    GET  /api/golden/events/{project}/{run_id}?after= events.jsonl lines; 404 when absent
    POST /api/golden/verify/{project}/{run_id}      aces golden verify <run_dir> --json --no-model (300 s, one per run)
    GET  /api/cad/runs?project=                     <project>/cad/local/runs/<id>/run.json rows (store.py rules)
    GET  /api/cad/run/{project}/{run_id}            the record and every file with its sha256
    GET  /api/cad/file/{project}/{run_id}/<rel>     a file of the run
    GET  /api/cad/bundles?project=                  <project>/cad/local/<stamp>-<name>/ with the outcome summary
    GET  /api/cad/bundle/{project}/{name}/<rel>     a bundle file
    POST /api/cad/bundle-check/{project}/{name}     check_bundle.py <bundle_dir> (60 s)
    GET  /api/cad/notes/{project}                   cad/NOTES.md split on its "## <ts> · tags: ... · run <id>" headings
    GET  /api/cad/drawings/{project}                drawings/*.json with schema aces.cad.drawing/1
    GET  /api/cad/onshape/{project}                 connected:false, the class gate as recorded; onshape.env is never opened

Honest absence everywhere: a missing work store, a project without a CAD subtree, a run directory without run.json, a
run without result.json each say so in words; a grade is never a sign-off ("no grade is a sign-off", "desktop grades
are not rig results" travel with every golden run).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import stat
import sys
import threading
import time
from pathlib import Path
from typing import Any

from aces import config as aces_config, ledger, project as aces_project

from . import cache as cache_mod, safefs, server
from .server import Request, Response, envelope, error, file_response, ok
from .system import aces_command, run_capped

GOLDEN_VERIFY_TIMEOUT_S = 300.0
BUNDLE_CHECK_TIMEOUT_S = 60.0
RUN_JSON_MAX = 8 << 20          # store.py:38
GOLDEN_RUN_JSON_MAX = 1 << 20
NOTES_MAX = 1 << 20
DRAWING_JSON_MAX = 1 << 20
EVENTS_TAIL = 50
EVENTS_PAGE = 500
FILE_LIST_CAP = 2000
HASH_CAP = 64 << 20
SWEEP_ROWS_CAP = 2000
ONSHAPE_ALLOWED = ("personal", "open", "commercial")   # aces_onshape/project.py:26
GOLDEN_NOTES = ["no grade is a sign-off", "desktop grades are not rig results"]
GOLDEN_STATUSES = ("running", "done", "error", "interrupted")
CASE_DIR_RE = re.compile(r"^[a-z][0-9]{3}$")
NOTES_SEP = " · "          # notes.py:34
DRAWING_SCHEMA = "aces.cad.drawing/1"
CHECK_BUNDLE_INSTALLED = Path("/usr/share/aces/cad/tests/check_bundle.py")   # pkgbuilds/aces-cad/PKGBUILD:39-41
CHECK_BUNDLE_SOURCE = Path(__file__).resolve().parents[2] / "aces-cad" / "tests" / "check_bundle.py"
ALL_MATCH_RE = re.compile(r"^check_bundle: .*: all files match$")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
FILE_KINDS = tuple(sorted(safefs.DEFAULT_KINDS)) + ("bin",)


class RecordsState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.verifying: set[str] = set()
        self.checking: set[str] = set()
        self.sha_cache: dict[str, tuple[tuple[int, int], str]] = {}


def _state(app: server.App) -> RecordsState:
    st = app.state.get("records")
    if st is None:
        st = app.state["records"] = RecordsState()
    return st


# --- filesystem helpers (no link following anywhere) -------------------------------------------------------------------------

def work_root() -> Path:
    return Path(aces_config.WORK)


def _is_link(st: os.stat_result) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def real_dir(p: Path) -> bool:
    try:
        st = os.lstat(p)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and not _is_link(st)


def real_file(p: Path) -> os.stat_result | None:
    try:
        st = os.lstat(p)
    except OSError:
        return None
    return st if stat.S_ISREG(st.st_mode) and not _is_link(st) else None


def real_children(p: Path) -> list[os.DirEntry]:
    try:
        with os.scandir(p) as it:
            return sorted(it, key=lambda e: e.name)
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return []


def real_subdirs(p: Path) -> list[str]:
    return [e.name for e in real_children(p) if e.is_dir(follow_symlinks=False)]


def real_files(p: Path) -> list[dict[str, Any]]:
    out = []
    for e in real_children(p):
        if e.is_file(follow_symlinks=False):
            try:
                out.append({"name": e.name, "bytes": e.stat(follow_symlinks=False).st_size})
            except OSError:
                out.append({"name": e.name, "bytes": None})
    return out


def walk_files(root: Path, cap: int = FILE_LIST_CAP) -> tuple[list[dict[str, Any]], bool]:
    """Every regular file under root as {rel, bytes}, sorted, links skipped, at most `cap` entries."""
    rows: list[dict[str, Any]] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if real_dir(Path(dirpath) / d))
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            st = real_file(p)
            if st is None:
                continue
            if len(rows) >= cap:
                truncated = True
                return rows, truncated
            rows.append({"rel": p.relative_to(root).as_posix(), "bytes": st.st_size})
    return rows, truncated


def read_json(root: Path, rel: str, max_bytes: int | None = None) -> tuple[Any, str | None, bool]:
    """(object, error, present) for root/rel through safefs: absent -> (None, None, False); present but unreadable
    or not JSON -> (None, reason, True)."""
    try:
        data, _ctype, _digest = safefs.open_under(root, rel, max_bytes=max_bytes, kinds=("json",))
    except safefs.SafeFsError as e:
        if e.status == 404:
            return None, None, False
        return None, e.reason, True
    try:
        return json.loads(data.decode("utf-8")), None, True
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        return None, f"{rel} is not valid JSON: {str(e)[:120]}", True


def read_text(root: Path, rel: str, max_bytes: int | None = None, kinds: tuple[str, ...] = ("text", "ndjson", "html")) -> tuple[str | None, str | None, bool]:
    try:
        data, _ctype, _digest = safefs.open_under(root, rel, max_bytes=max_bytes, kinds=kinds)
    except safefs.SafeFsError as e:
        if e.status == 404:
            return None, None, False
        return None, e.reason, True
    return data.decode("utf-8", "replace"), None, True


def read_head(path: Path, n: int) -> bytes | None:
    """The first n bytes of a regular, unlinked file (for a NOTES.md over the cap)."""
    if real_file(path) is None:
        return None
    try:
        with open(path, "rb") as fh:
            return fh.read(n)
    except OSError:
        return None


def sha256_cached(st: RecordsState, path: Path, fst: os.stat_result) -> str | None:
    key = (fst.st_mtime_ns, fst.st_size)
    skey = str(path)
    with st.lock:
        hit = st.sha_cache.get(skey)
        if hit is not None and hit[0] == key:
            return hit[1]
    if fst.st_size > HASH_CAP:
        return None
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    digest = h.hexdigest()
    with st.lock:
        st.sha_cache[skey] = (key, digest)
    return digest


def _safefs_response(e: safefs.SafeFsError, what: str) -> Response:
    code = {400: "bad-request", 403: "refused", 404: "not-found", 413: "too-large", 415: "unsupported-type"}.get(e.status, "read-error")
    return error(e.status, e.reason if e.status != 404 else f"{what}: {e.reason}", code)


def project_dir(name: str) -> Path:
    return work_root() / name


# --- projects (3.8) -----------------------------------------------------------------------------------------------------------

def ledger_entries_for(es: list[dict[str, Any]] | None, name: str) -> list[tuple[int, dict[str, Any]]]:
    out = []
    for i, e in enumerate(es or [], 1):
        pl = e.get("payload")
        if isinstance(pl, dict) and (pl.get("project") == name or pl.get("name") == name):
            out.append((i, e))
    return out


def golden_run_dirs(pdir: Path) -> list[str]:
    g = pdir / "golden"
    if not real_dir(g):
        return []
    return [d for d in real_subdirs(g) if real_file(g / d / "run.json") is not None]


def cad_run_ids(pdir: Path) -> list[str]:
    """store.py:417-425: real directories under cad/local/runs whose name matches RUN_ID_RE, newest first."""
    rdir = pdir / "cad" / "local" / "runs"
    if not (real_dir(pdir / "cad") and real_dir(pdir / "cad" / "local") and real_dir(rdir)):
        return []
    ids = [d for d in real_subdirs(rdir) if safefs.CAD_RUN_ID_RE.match(d)]
    return sorted(ids, reverse=True)


def bundle_names(pdir: Path) -> list[str]:
    local = pdir / "cad" / "local"
    if not (real_dir(pdir / "cad") and real_dir(local)):
        return []
    return sorted((d for d in real_subdirs(local) if d != "runs" and safefs.BUNDLE_NAME_RE.match(d)), reverse=True)


def _ledger_all(app: server.App) -> tuple[list[dict[str, Any]] | None, str | None]:
    try:
        from . import ledgers
        return ledgers.aces_entries(app)
    except ImportError:
        try:
            return ledger.entries(), None
        except SystemExit as e:
            return None, f"ledger unreadable: {e}"


def project_row(name: str, yaml_path: Path, d: dict[str, Any], es: list[dict[str, Any]] | None) -> dict[str, Any]:
    pdir = yaml_path.parent
    cls = d.get("class")
    return {"name": name, "path": str(yaml_path), "dir": str(pdir), "class": cls, "class_source": "aces.yaml",
            "created": d.get("created"), "reclassified": d.get("reclassified"), "company": d.get("company") or "",
            "rd_activity": d.get("rd_activity") or "", "hypothesis": d.get("hypothesis") or "",
            "components": list(d.get("components") or []), "evidence": list(d.get("evidence") or []),
            "cad_sources": list(d.get("cad_sources") or []),
            "counts": {"golden_runs": len(golden_run_dirs(pdir)), "cad_runs": len(cad_run_ids(pdir)),
                       "bundles": len(bundle_names(pdir)), "ledger_entries": len(ledger_entries_for(es, name))},
            "onshape_gate": "allowed" if cls in ONSHAPE_ALLOWED else "refused for this class", "unreadable": None}


def fetch_projects(app: server.App):
    work = work_root()
    if not work.exists():
        return True, {"projects": [], "reason": f"{work} does not exist", "root": str(work), "ledger_note": None}, None
    es, lerr = _ledger_all(app)
    rows = []
    for p in aces_project.list_projects():
        if real_file(p) is None:
            rows.append({"name": p.parent.name, "path": str(p), "class": None, "class_source": "aces.yaml",
                         "unreadable": "aces.yaml is not a regular file", "counts": {}})
            continue
        try:
            d = aces_project._load(p)
        except (OSError, UnicodeDecodeError, ValueError) as e:
            rows.append({"name": p.parent.name, "path": str(p), "class": None, "class_source": "aces.yaml",
                         "unreadable": f"{type(e).__name__}: {e}"[:200], "counts": {}})
            continue
        name = d.get("name") if isinstance(d.get("name"), str) and d.get("name") else p.parent.name
        rows.append(project_row(name, p, d, es))
    return True, {"projects": rows, "reason": None, "root": str(work), "ledger_note": lerr, "n": len(rows)}, None


def h_projects(req: Request) -> Response:
    return envelope(req.app.hub.get("projects"))


def fetch_project(app: server.App, name: str):
    p = aces_project.path(name)
    if real_file(p) is None:
        return False, None, f"no project at {p}"
    try:
        d = aces_project._load(p)
    except (OSError, UnicodeDecodeError, ValueError) as e:
        return False, None, f"{p} is unreadable: {type(e).__name__}: {e}"[:200]
    es, lerr = _ledger_all(app)
    row = project_row(d.get("name") if isinstance(d.get("name"), str) and d.get("name") else name, p, d, es)
    rows = [{"n": i, "ts": e.get("ts"), "kind": e.get("kind"), "operator": e.get("operator"), "host": e.get("host"),
             "payload": e.get("payload"), "hash": e.get("hash"), "prev": e.get("prev")} for i, e in ledger_entries_for(es, name)]
    rows.reverse()
    row.update({"ledger": rows, "ledger_note": lerr, "golden_runs": golden_rows(name)["runs"],
                "cad_runs": cad_rows(name)["runs"], "bundles": bundle_rows(name)["bundles"]})
    return True, row, None


def h_project(req: Request) -> Response:
    try:
        name = safefs.check_project(req.params.get("name"))
    except safefs.SafeFsError as e:
        return _safefs_response(e, "project")
    e = req.app.cache.get(f"project:{name}", 10.0, lambda: fetch_project(req.app, name), source=str(aces_project.path(name)))
    if e.data is None and e.error and e.error.startswith("no project at"):
        return error(404, e.error, "not-found", source=str(aces_project.path(name)))
    return envelope(e)


# --- golden records (3.9) --------------------------------------------------------------------------------------------------

def golden_row(project: str, run_dir: Path) -> dict[str, Any]:
    meta, err, present = read_json(run_dir, "run.json", GOLDEN_RUN_JSON_MAX)
    meta = meta if isinstance(meta, dict) else {}
    timings = meta.get("timings") if isinstance(meta.get("timings"), dict) else {}
    status = meta.get("status")
    row = {"run_id": meta.get("run_id") or run_dir.name, "project": meta.get("project") or project, "run_dir": str(run_dir),
           "created_utc": meta.get("created_utc"), "finished_utc": meta.get("finished_utc"), "status": status,
           "status_known": status in GOLDEN_STATUSES, "outcome": meta.get("outcome"), "verdict": meta.get("verdict"),
           "grade": meta.get("grade"), "exit_code": meta.get("exit_code"), "wall_s": timings.get("total_s"),
           "has_result": real_file(run_dir / "result.json") is not None, "n_stages": len(meta.get("stages") or []),
           "unreadable": err if (err or (present and not meta)) else None, "notes": GOLDEN_NOTES}
    if err is None and present and not meta:
        row["unreadable"] = "run.json is not a JSON object"
    return row


def golden_rows(project: str | None) -> dict[str, Any]:
    work = work_root()
    if project:
        pdirs = [work / project]
        where = work / project / "golden"
    else:
        pdirs = [work / d for d in real_subdirs(work)] if real_dir(work) else []
        where = work
    rows = []
    for pdir in pdirs:
        for rid in golden_run_dirs(pdir):
            rows.append(golden_row(pdir.name, pdir / "golden" / rid))
    rows.sort(key=lambda r: (str(r.get("created_utc") or ""), str(r.get("run_id"))), reverse=True)
    reason = None if rows else f"no golden runs under {where}"
    return {"runs": rows, "root": str(where), "reason": reason, "n": len(rows), "project": project}


def fetch_golden_runs(app: server.App):
    return True, golden_rows(None), None


def h_golden_runs(req: Request) -> Response:
    project = req.q("project")
    if project:
        try:
            project = safefs.check_project(project)
        except safefs.SafeFsError as e:
            return _safefs_response(e, "project")
        e = req.app.cache.get(f"golden.runs:{project}", 10.0, lambda: (True, golden_rows(project), None),
                              source=str(work_root() / project / "golden"))
        return envelope(e)
    return envelope(req.app.hub.get("golden.runs"))


def _golden_run_dir(req: Request) -> tuple[tuple[str, str, Path] | None, Response | None]:
    try:
        project = safefs.check_project(req.params.get("project"))
        run_id = safefs.check_golden_run_id(req.params.get("run_id"))
    except safefs.SafeFsError as e:
        return None, _safefs_response(e, "golden run")
    run_dir = work_root() / project / "golden" / run_id
    if not real_dir(run_dir) or real_file(run_dir / "run.json") is None:
        return None, error(404, f"no golden run {run_id} in {project} (run.json absent)", "not-found", source=str(run_dir))
    return (project, run_id, run_dir), None


def read_sweep_csv(run_dir: Path) -> tuple[dict[str, Any] | None, str | None, bool]:
    text, err, present = read_text(run_dir, "sweep.csv", kinds=("text",))
    if text is None:
        return None, err, present
    reader = csv.reader(io.StringIO(text))
    try:
        columns = next(reader)
    except StopIteration:
        return {"columns": [], "rows": [], "n": 0}, None, True
    rows = []
    for row in reader:
        if len(rows) >= SWEEP_ROWS_CAP:
            break
        rows.append(dict(zip(columns, row)))
    return {"columns": columns, "rows": rows, "n": len(rows), "truncated": len(rows) >= SWEEP_ROWS_CAP}, None, True


def events_tail(run_dir: Path, n: int = EVENTS_TAIL) -> tuple[list[Any] | None, str | None, bool]:
    text, err, present = read_text(run_dir, "events.jsonl", kinds=("ndjson",))
    if text is None:
        return None, err, present
    lines = [ln for ln in text.splitlines() if ln.strip()]
    out = []
    for ln in lines[-n:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            out.append(ln[:400])
    return out, None, True


def golden_detail(project: str, run_id: str, run_dir: Path) -> dict[str, Any]:
    read_errors: list[dict[str, str]] = []
    absent: list[str] = []

    def take(rel: str, obj: Any, err: str | None, present: bool) -> Any:
        if err:
            read_errors.append({"rel": rel, "error": err})
        elif not present:
            absent.append(rel)
        return obj

    run = take("run.json", *read_json(run_dir, "run.json", GOLDEN_RUN_JSON_MAX))
    run = run if isinstance(run, dict) else {}
    status = run.get("status")
    result = take("result.json", *read_json(run_dir, "result.json"))
    result_source = "result.json"
    result_absent_reason = None
    if result is None and "result.json" in absent:
        result, result_source = run, "run.json"     # as `aces golden show` does (golden_cli.py:290-292)
        result_absent_reason = f"result.json not written: this run ended with status {status}"
    validation = take("validation.json", *read_json(run_dir, "validation.json"))
    compute_used = take("compute_used.json", *read_json(run_dir, "compute_used.json"))
    report = take("report.json", *read_json(run_dir, "report.json"))
    sweep = take("sweep.csv", *read_sweep_csv(run_dir))
    ledgers = take("evidence/ledgers.json", *read_json(run_dir, "evidence/ledgers.json")) if real_dir(run_dir / "evidence") else None
    if not real_dir(run_dir / "evidence"):
        absent.append("evidence/")
    winner_json = take("winner/winner.json", *read_json(run_dir, "winner/winner.json")) if real_dir(run_dir / "winner") else None
    winner = {"json": winner_json, "files": real_files(run_dir / "winner")} if real_dir(run_dir / "winner") else None
    if winner is None:
        absent.append("winner/")
    candidate = {"files": real_files(run_dir / "candidate")} if real_dir(run_dir / "candidate") else None
    figures = [f["name"] for f in real_files(run_dir / "figures")] if real_dir(run_dir / "figures") else []
    if not real_dir(run_dir / "figures"):
        absent.append("figures/")
    tail = take("events.jsonl", *events_tail(run_dir))
    files, truncated = walk_files(run_dir)
    paths = run.get("paths") if isinstance(run.get("paths"), dict) else {}
    stl_rel = paths.get("stl") if isinstance(paths.get("stl"), str) else None
    cu_rows = compute_used.get("rows") if isinstance(compute_used, dict) and isinstance(compute_used.get("rows"), list) else None
    provenance = report.get("provenance") if isinstance(report, dict) and isinstance(report.get("provenance"), list) else None
    sections = report.get("sections") if isinstance(report, dict) and isinstance(report.get("sections"), (list, dict)) else None
    return {"project": project, "run_id": run_id, "run_dir": str(run_dir), "run": run, "status": status,
            "status_known": status in GOLDEN_STATUSES, "stages": run.get("stages") or [], "n_stages": len(run.get("stages") or []),
            "grade": run.get("grade"), "verdict": run.get("verdict"), "outcome": run.get("outcome"),
            "result": result, "result_source": result_source, "result_absent_reason": result_absent_reason,
            "validation": validation, "compute_used": compute_used, "compute_used_rows": len(cu_rows) if cu_rows is not None else None,
            "report": report, "report_sections": len(sections) if sections is not None else None,
            "provenance_n": len(provenance) if provenance is not None else None,
            "sweep": sweep, "winner": winner, "candidate": candidate, "figures": figures, "evidence_ledgers": ledgers,
            "files": files, "files_truncated": truncated, "events_tail": tail, "read_errors": read_errors, "absent": absent,
            "report_html": "report.html" if real_file(run_dir / "report.html") is not None else None,
            "stl": stl_rel if stl_rel and real_file(run_dir / stl_rel) is not None else None,
            "case_dirs": [d for d in real_subdirs(run_dir) if CASE_DIR_RE.match(d)],
            "events_present": real_file(run_dir / "events.jsonl") is not None,
            "notes": GOLDEN_NOTES, "file_route": f"/api/golden/file/{project}/{run_id}/"}


def h_golden_run(req: Request) -> Response:
    found, resp = _golden_run_dir(req)
    if resp is not None:
        return resp
    project, run_id, run_dir = found
    meta, _, _ = read_json(run_dir, "run.json", GOLDEN_RUN_JSON_MAX)
    ttl = 3.0 if isinstance(meta, dict) and meta.get("status") == "running" else 10.0
    e = req.app.cache.get(f"golden.run:{project}/{run_id}", ttl, lambda: (True, golden_detail(project, run_id, run_dir), None),
                          source=str(run_dir))
    return envelope(e)


def h_golden_file(req: Request) -> Response:
    found, resp = _golden_run_dir(req)
    if resp is not None:
        return resp
    _project, _run_id, run_dir = found
    return file_response(req, run_dir, req.params.get("rel", ""), kinds=FILE_KINDS)


def h_golden_events(req: Request) -> Response:
    found, resp = _golden_run_dir(req)
    if resp is not None:
        return resp
    project, run_id, run_dir = found
    try:
        after = int(req.q("after", "0") or 0)
    except ValueError:
        return error(400, "after must be an integer", "bad-request")
    after = max(0, after)
    meta, _, _ = read_json(run_dir, "run.json", GOLDEN_RUN_JSON_MAX)
    status = meta.get("status") if isinstance(meta, dict) else None
    text, err, present = read_text(run_dir, "events.jsonl", kinds=("ndjson",))
    if not present:
        return error(404, "events.jsonl absent", "not-found", source=str(run_dir / "events.jsonl"))
    if text is None:
        return error(500, err or "events.jsonl unreadable", "read-error", source=str(run_dir / "events.jsonl"))
    lines = [ln for ln in text.splitlines() if ln.strip()]
    page = lines[after:after + EVENTS_PAGE]
    out = []
    for ln in page:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            out.append(ln[:400])
    return ok({"lines": out, "after": after, "next_after": after + len(page), "n_total": len(lines),
               "eof": status != "running" and after + len(page) >= len(lines), "status": status, "project": project, "run_id": run_id},
              source=str(run_dir / "events.jsonl"), ttl_s=3.0)


def _parse_json_document(text: str) -> Any:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start >= 0:
        try:
            return json.loads(text[start:])
        except json.JSONDecodeError:
            return None
    return None


def h_golden_verify(req: Request) -> Response:
    app = req.app
    st = _state(app)
    found, resp = _golden_run_dir(req)
    if resp is not None:
        return resp
    project, run_id, run_dir = found
    key = str(run_dir)
    with st.lock:
        if key in st.verifying:
            return error(409, f"a verify is already running for {run_id}", "busy", source=key)
        st.verifying.add(key)
    try:
        args = ["golden", "verify", str(run_dir), "--json", "--no-model"]
        cmd = aces_command(args)
        if cmd is None:
            return error(503, "aces CLI not found (aces-base is not installed and the aces package is not importable)", "not-installed")
        res = run_capped(cmd, GOLDEN_VERIFY_TIMEOUT_S)
    finally:
        with st.lock:
            st.verifying.discard(key)
    shown = ["aces", *args]
    if res["timed_out"]:
        return error(500, f"verify timed out after {GOLDEN_VERIFY_TIMEOUT_S:g} s", "verify-timeout", source=key)
    if res["exit"] == 127:
        return error(503, "aces-engineering not installed (exit 127)" + (f": {res['stderr_tail'].strip().splitlines()[-1][:200]}" if res["stderr_tail"].strip() else ""),
                     "not-installed", source=key)
    doc = _parse_json_document(res["stdout"])
    if not isinstance(doc, dict) or "checks" not in doc:
        tail = res["stderr_tail"].strip() or res["stdout"].strip()[-STDERR_LIMIT:]
        return error(500, f"verify failed (exit {res['exit']}): {tail[:600] or 'no output'}", "verify-failed", source=key)
    if doc.get("run_dir") is None and doc.get("error"):
        return error(404, str(doc.get("error")), "not-found", source=key)
    checks = doc.get("checks") if isinstance(doc.get("checks"), list) else []
    counts = {"ok": sum(1 for c in checks if isinstance(c, dict) and c.get("ok") is True),
              "failed": sum(1 for c in checks if isinstance(c, dict) and c.get("ok") is False),
              "not_run": sum(1 for c in checks if isinstance(c, dict) and c.get("ok") is None)}
    return ok({"project": project, "run_id": run_id, "run_dir": doc.get("run_dir"), "ok": doc.get("ok"), "checks": checks,
               "exit": res["exit"], "ran_at": server.now_utc(), "elapsed_s": res["duration_s"], "command": shown,
               "counts": counts, "error": doc.get("error"), "ledgered": False,
               "note": "a verify is a read; ok:null checks were not run (the tool's own detail text says why)"},
              source=" ".join(shown))


STDERR_LIMIT = 2048


# --- CAD (3.10) ----------------------------------------------------------------------------------------------------------

def summarize_record(rec: dict[str, Any]) -> dict[str, Any]:
    """store.py:481-494 plus the sandbox triple the Objects plate shows."""
    result = rec.get("result") if isinstance(rec.get("result"), dict) else {}
    parts = result.get("parts") if isinstance(result.get("parts"), list) else []
    totals = result.get("totals") if isinstance(result.get("totals"), dict) else {}
    err = rec.get("error") if isinstance(rec.get("error"), dict) else None
    sb = rec.get("sandbox") if isinstance(rec.get("sandbox"), dict) else {}
    valid = all(isinstance(p, dict) and p.get("valid") is True for p in parts) if parts else None
    return {"run_id": rec.get("run_id"), "name": rec.get("name"), "status": rec.get("status"), "kind": result.get("kind"),
            "started": rec.get("created_at"), "finished": rec.get("finished_at"), "params": rec.get("params"),
            "volume_mm3": totals.get("volume_mm3"), "valid": valid, "error_kind": err.get("kind") if err else None,
            "entry": rec.get("entry"), "n_parts": len(parts),
            "sandbox": {"mode": sb.get("mode"), "confined": sb.get("confined"), "platform": sb.get("platform")} if sb else None,
            "unreadable": None}


def cad_run_row(run_dir: Path) -> dict[str, Any]:
    rid = run_dir.name
    rec, err, present = read_json(run_dir, "run.json", RUN_JSON_MAX)
    base = {"run_id": rid, "status": "unreadable", "name": None, "started": None, "finished": None, "sandbox": None, "kind": None,
            "valid": None, "params": None, "error_kind": None, "unreadable": None}
    if not present:
        base["unreadable"] = "run.json missing"
        return base
    if err:
        base["unreadable"] = "run.json exceeds 8 MiB (store limit)" if "cap for json" in err else f"run.json unreadable: {err}"
        return base
    if not isinstance(rec, dict):
        base["unreadable"] = "run.json is not a JSON object"
        return base
    row = summarize_record(rec)
    row["run_id"] = rid
    return row


def cad_rows(project: str) -> dict[str, Any]:
    pdir = project_dir(project)
    runs_dir = pdir / "cad" / "local" / "runs"
    store_present = real_dir(pdir / "cad") and real_dir(pdir / "cad" / "local") and real_dir(runs_dir)
    rows = [cad_run_row(runs_dir / rid) for rid in cad_run_ids(pdir)]
    note = None
    if not real_dir(pdir):
        note = f"no project directory {pdir}"
    elif not store_present:
        note = f"no CAD store in {project}"
    return {"project": project, "runs": rows, "counts": {"total": len(rows), "unreadable": sum(1 for r in rows if r["unreadable"])},
            "root": str(runs_dir), "note": note}


def fetch_cad_runs_all(app: server.App):
    work = work_root()
    out: dict[str, Any] = {}
    for d in (real_subdirs(work) if real_dir(work) else []):
        pdir = work / d
        if real_file(pdir / "aces.yaml") is None:
            continue
        ids = cad_run_ids(pdir)
        if not ids and not real_dir(pdir / "cad"):
            continue
        rows = [cad_run_row(pdir / "cad" / "local" / "runs" / rid) for rid in ids]
        out[d] = {"total": len(ids), "unreadable": sum(1 for r in rows if r["unreadable"]), "newest": ids[0] if ids else None,
                  "bundles": len(bundle_names(pdir))}
    return True, {"projects": out, "root": str(work)}, None


def _cad_project(req: Request) -> tuple[str | None, Response | None]:
    try:
        return safefs.check_project(req.q("project") or req.params.get("project")), None
    except safefs.SafeFsError as e:
        return None, _safefs_response(e, "project")


def h_cad_runs(req: Request) -> Response:
    project, resp = _cad_project(req)
    if resp is not None:
        return resp
    e = req.app.cache.get(f"cad.runs:{project}", 10.0, lambda: (True, cad_rows(project), None),
                          source=str(project_dir(project) / "cad" / "local" / "runs"))
    return envelope(e)


def _cad_run_dir(req: Request) -> tuple[tuple[str, str, Path] | None, Response | None]:
    try:
        project = safefs.check_project(req.params.get("project"))
        run_id = safefs.check_run_id(req.params.get("run_id"))
    except safefs.SafeFsError as e:
        return None, _safefs_response(e, "CAD run")
    pdir = project_dir(project)
    run_dir = pdir / "cad" / "local" / "runs" / run_id
    if not (real_dir(pdir / "cad") and real_dir(pdir / "cad" / "local") and real_dir(run_dir)):
        return None, error(404, f"no run {run_id} in {project}", "not-found", source=str(run_dir))
    return (project, run_id, run_dir), None


def cad_detail(st: RecordsState, project: str, run_id: str, run_dir: Path) -> tuple[dict[str, Any] | None, Response | None]:
    rec, err, present = read_json(run_dir, "run.json", RUN_JSON_MAX)
    if err and "cap for json" in err:
        return None, error(413, "run.json exceeds 8 MiB (store limit)", "too-large", source=str(run_dir))
    files = []
    rows, truncated = walk_files(run_dir)
    for row in rows:
        p = run_dir / row["rel"]
        fst = real_file(p)
        try:
            kind, ctype, _cap = safefs.classify(row["rel"].rsplit("/", 1)[-1], kinds=FILE_KINDS)
        except safefs.SafeFsError:
            kind, ctype = None, None
        digest = sha256_cached(st, p, fst) if fst is not None else None
        files.append({"rel": row["rel"], "size": row["bytes"], "sha256": digest, "kind": kind, "content_type": ctype,
                      "servable": kind is not None and not row["rel"].rsplit("/", 1)[-1].startswith(".")})
    unreadable = None
    if not present:
        unreadable = "run.json missing"
    elif err:
        unreadable = f"run.json unreadable: {err}"
    elif not isinstance(rec, dict):
        unreadable = "run.json is not a JSON object"
    summary = summarize_record(rec) if isinstance(rec, dict) else None
    if summary is not None:
        summary["run_id"] = run_id
    drawings = []
    if real_dir(run_dir / "drawings"):
        drawings = [d for d in drawing_rows_for_run(run_dir, run_id)]
    return {"project": project, "run_id": run_id, "run_dir": str(run_dir), "record": rec if isinstance(rec, dict) else None,
            "summary": summary, "unreadable": unreadable, "files": files, "files_truncated": truncated,
            "subdirs": real_subdirs(run_dir), "drawings": drawings, "file_route": f"/api/cad/file/{project}/{run_id}/",
            "stl": next((f["rel"] for f in files if f["rel"].endswith(".stl")), None)}, None


def h_cad_run(req: Request) -> Response:
    found, resp = _cad_run_dir(req)
    if resp is not None:
        return resp
    project, run_id, run_dir = found
    st = _state(req.app)
    holder: dict[str, Response] = {}

    def fetch():
        data, err_resp = cad_detail(st, project, run_id, run_dir)
        if err_resp is not None:
            holder["resp"] = err_resp
            return False, None, err_resp.error
        return True, data, None

    rec_status = None
    rec, _, _ = read_json(run_dir, "run.json", RUN_JSON_MAX)
    if isinstance(rec, dict):
        rec_status = rec.get("status")
    e = req.app.cache.get(f"cad.run:{project}/{run_id}", 3.0 if rec_status == "running" else 10.0, fetch, source=str(run_dir))
    if "resp" in holder:
        return holder["resp"]
    if e.data is None and e.error and "8 MiB" in e.error:
        return error(413, e.error, "too-large", source=str(run_dir))
    return envelope(e)


def h_cad_file(req: Request) -> Response:
    found, resp = _cad_run_dir(req)
    if resp is not None:
        return resp
    _project, _run_id, run_dir = found
    return file_response(req, run_dir, req.params.get("rel", ""), kinds=FILE_KINDS)


# --- bundles -------------------------------------------------------------------------------------------------------------------

def bundle_row(local: Path, name: str) -> dict[str, Any]:
    bdir = local / name
    has = {k: real_file(bdir / f) is not None for k, f in (("source", "source.json"), ("outcome", "outcome.json"),
                                                            ("parameters", "parameters.json"), ("part_step", "part.step"),
                                                            ("part_stl", "part.stl"), ("part_3mf", "part.3mf"),
                                                            ("source_py", "source.py"))}
    outcome, oerr, _ = read_json(bdir, "outcome.json", 1 << 20)
    source, serr, _ = read_json(bdir, "source.json", 1 << 20)
    outcome = outcome if isinstance(outcome, dict) else {}
    source = source if isinstance(source, dict) else {}
    led = outcome.get("ledger") if isinstance(outcome.get("ledger"), dict) else {}
    cs = outcome.get("cad_source") if isinstance(outcome.get("cad_source"), dict) else {}
    files = source.get("files") if isinstance(source.get("files"), dict) else {}
    return {"name": name, "dir": str(bdir), "has": has,
            "outcome_summary": {"ledger_written": led.get("written"), "ledger_n": led.get("n"), "ledger_kind": led.get("kind"),
                                "ledger_via": led.get("via"), "ledger_reason": led.get("reason"),
                                "cad_source_recorded": cs.get("recorded"), "cad_source_ref": cs.get("ref"),
                                "updated_at": outcome.get("updated_at")} if outcome else None,
            "source_summary": {"run_id": source.get("run_id"), "run_name": source.get("name"), "kind": source.get("kind"),
                               "geometry_sha256": source.get("geometry_sha256"), "exported_at": source.get("exported_at"),
                               "files_n": len(files), "ref": source.get("ref")} if source else None,
            "read_errors": [x for x in ({"rel": "outcome.json", "error": oerr} if oerr else None,
                                        {"rel": "source.json", "error": serr} if serr else None) if x],
            "files": real_files(bdir), "file_route": f"/api/cad/bundle/{local.parent.parent.name}/{name}/"}


def bundle_rows(project: str) -> dict[str, Any]:
    pdir = project_dir(project)
    local = pdir / "cad" / "local"
    names = bundle_names(pdir)
    rows = [bundle_row(local, n) for n in names]
    return {"project": project, "bundles": rows, "n": len(rows), "root": str(local),
            "note": None if rows else f"no bundles in {project}"}


def h_cad_bundles(req: Request) -> Response:
    project, resp = _cad_project(req)
    if resp is not None:
        return resp
    e = req.app.cache.get(f"cad.bundles:{project}", 10.0, lambda: (True, bundle_rows(project), None),
                          source=str(project_dir(project) / "cad" / "local"))
    return envelope(e)


def _bundle_dir(req: Request) -> tuple[tuple[str, str, Path] | None, Response | None]:
    try:
        project = safefs.check_project(req.params.get("project"))
        name = safefs.check_bundle_name(req.params.get("name"))
    except safefs.SafeFsError as e:
        return None, _safefs_response(e, "bundle")
    pdir = project_dir(project)
    bdir = pdir / "cad" / "local" / name
    if not (real_dir(pdir / "cad") and real_dir(pdir / "cad" / "local") and real_dir(bdir)):
        return None, error(404, f"no bundle {name} in {project}", "not-found", source=str(bdir))
    return (project, name, bdir), None


def h_cad_bundle_file(req: Request) -> Response:
    found, resp = _bundle_dir(req)
    if resp is not None:
        return resp
    _project, _name, bdir = found
    return file_response(req, bdir, req.params.get("rel", ""), kinds=FILE_KINDS)


def check_bundle_script() -> tuple[Path, str] | None:
    if CHECK_BUNDLE_INSTALLED.is_file():
        return CHECK_BUNDLE_INSTALLED, "installed"
    if CHECK_BUNDLE_SOURCE.is_file():
        return CHECK_BUNDLE_SOURCE, "source-tree"
    return None


def h_cad_bundle_check(req: Request) -> Response:
    app = req.app
    st = _state(app)
    found, resp = _bundle_dir(req)
    if resp is not None:
        return resp
    project, name, bdir = found
    script = check_bundle_script()
    if script is None:
        return error(503, f"aces-cad not installed ({CHECK_BUNDLE_INSTALLED} absent)", "not-installed")
    key = str(bdir)
    with st.lock:
        if key in st.checking:
            return error(409, f"a bundle check is already running for {name}", "busy", source=key)
        st.checking.add(key)
    try:
        res = run_capped([sys.executable, "-I", str(script[0]), str(bdir)], BUNDLE_CHECK_TIMEOUT_S)
    finally:
        with st.lock:
            st.checking.discard(key)
    lines = [ln for ln in (res["stdout"] + ("\n" + res["stderr_tail"] if res["stderr_tail"] else "")).splitlines() if ln.strip()]
    if res["timed_out"]:
        return error(500, f"check_bundle.py timed out after {BUNDLE_CHECK_TIMEOUT_S:g} s", "check-timeout", source=key)
    if res["error"]:
        return error(500, f"check_bundle.py could not run: {res['error']}", "check-failed", source=key)
    all_match = any(ALL_MATCH_RE.match(ln.strip()) for ln in lines)
    return ok({"project": project, "name": name, "exit": res["exit"], "lines": lines, "all_match": all_match,
               "checker": {"path": str(script[0]), "source": script[1]}, "duration_s": res["duration_s"], "ran_at": server.now_utc(),
               "ledgered": False, "note": "all_match is true only when the checker itself printed its all-match line"},
              source=f"python -I {script[0]} {bdir}")


# --- notes -----------------------------------------------------------------------------------------------------------------------

def parse_notes(text: str) -> tuple[str, list[dict[str, Any]]]:
    """(preamble, entries) for the format notes.py writes: `## <ts> · tags: a, b · run <run_id>` headings."""
    lines = text.split("\n")
    starts = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    if not starts:
        return text, []
    preamble = "\n".join(lines[:starts[0]])
    entries = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else len(lines)
        heading = lines[s][3:].strip()
        body = "\n".join(lines[s + 1:e]).strip("\n")
        parts = [p.strip() for p in heading.split(NOTES_SEP)]
        ts = parts[0] if parts else None
        tags: list[str] = []
        run_id = None
        for p in parts[1:]:
            if p.startswith("tags:"):
                tags = [t.strip() for t in p[len("tags:"):].split(",") if t.strip()]
            elif p.startswith("run "):
                run_id = p[len("run "):].strip()
        entries.append({"n": k + 1, "ts": ts, "tags": tags, "run_id": run_id, "heading": heading, "body": body})
    return preamble, entries


def notes_data(project: str) -> dict[str, Any]:
    pdir = project_dir(project)
    path = pdir / "cad" / "NOTES.md"
    if not real_dir(pdir):
        return {"project": project, "path": str(path), "entries": [], "truncated": False, "note": f"no project directory {pdir}"}
    text, err, present = read_text(pdir, "cad/NOTES.md", kinds=("text",))
    truncated = False
    if not present:
        return {"project": project, "path": str(path), "entries": [], "truncated": False, "note": f"no NOTES.md in {project}"}
    if text is None and err and "cap for text" in err:
        head = read_head(path, NOTES_MAX)
        if head is None:
            return {"project": project, "path": str(path), "entries": [], "truncated": False, "note": f"NOTES.md unreadable: {err}"}
        text, truncated = head.decode("utf-8", "replace"), True
    elif text is None:
        return {"project": project, "path": str(path), "entries": [], "truncated": False, "note": f"NOTES.md unreadable: {err}"}
    preamble, entries = parse_notes(text)
    return {"project": project, "path": str(path), "entries": entries, "n": len(entries), "preamble": preamble,
            "truncated": truncated, "bytes": len(text.encode("utf-8")), "note": None if entries else "NOTES.md has no entries"}


def h_cad_notes(req: Request) -> Response:
    project, resp = _cad_project(req)
    if resp is not None:
        return resp
    e = req.app.cache.get(f"cad.notes:{project}", 10.0, lambda: (True, notes_data(project), None),
                          source=str(project_dir(project) / "cad" / "NOTES.md"))
    return envelope(e)


# --- drawings ----------------------------------------------------------------------------------------------------------------------

def drawing_rows_for_run(run_dir: Path, run_id: str) -> list[dict[str, Any]]:
    ddir = run_dir / "drawings"
    rows = []
    for f in real_files(ddir):
        if not f["name"].endswith(".json"):
            continue
        rel = f"drawings/{f['name']}"
        obj, err, _ = read_json(run_dir, rel, DRAWING_JSON_MAX)
        if not isinstance(obj, dict) or obj.get("schema") != DRAWING_SCHEMA:
            continue
        stem = f["name"][:-len(".json")]
        views = obj.get("views") if isinstance(obj.get("views"), list) else []
        dims = obj.get("dimensions") if isinstance(obj.get("dimensions"), list) else []
        files = {ext: (f"drawings/{stem}.{ext}" if real_file(ddir / f"{stem}.{ext}") is not None else None) for ext in ("dxf", "svg", "png", "json")}
        rows.append({"id": stem, "run_id": obj.get("run_id") or run_id, "run_dir_id": run_id, "schema": obj.get("schema"),
                     "part": obj.get("part"), "projection": obj.get("projection"), "units": obj.get("units"), "scale": obj.get("scale"),
                     "views": [v.get("name") if isinstance(v, dict) else v for v in views], "n_views": len(views),
                     "n_dimensions": len(dims), "dimensions": dims, "hole_table": obj.get("hole_table"),
                     "audit_errors": obj.get("audit_errors"), "warnings": obj.get("warnings"), "notes": obj.get("notes"),
                     "files": files, "file_route": None})
    return rows


def drawings_data(project: str) -> dict[str, Any]:
    pdir = project_dir(project)
    rows = []
    for rid in cad_run_ids(pdir):
        run_dir = pdir / "cad" / "local" / "runs" / rid
        if real_dir(run_dir / "drawings"):
            for d in drawing_rows_for_run(run_dir, rid):
                d["file_route"] = f"/api/cad/file/{project}/{rid}/"
                rows.append(d)
    return {"project": project, "drawings": rows, "n": len(rows),
            "note": None if rows else (f"no CAD store in {project}" if not cad_run_ids(pdir) and not real_dir(pdir / "cad" / "local" / "runs")
                                       else f"no drawing records in {project}")}


def h_cad_drawings(req: Request) -> Response:
    project, resp = _cad_project(req)
    if resp is not None:
        return resp
    e = req.app.cache.get(f"cad.drawings:{project}", 10.0, lambda: (True, drawings_data(project), None),
                          source=str(project_dir(project) / "cad" / "local" / "runs"))
    return envelope(e)


# --- Onshape card --------------------------------------------------------------------------------------------------------------

def onshape_data(project: str) -> dict[str, Any]:
    p = aces_project.path(project)
    cls = None
    class_error = None
    if real_file(p) is not None:
        try:
            cls = aces_project._load(p).get("class")
        except (OSError, UnicodeDecodeError, ValueError) as e:
            class_error = f"aces.yaml unreadable: {type(e).__name__}"
    else:
        class_error = f"no project at {p}"
    odir = project_dir(project) / "cad" / "onshape"
    exports = [f["name"] for f in real_files(odir / "exports")] if real_dir(odir / "exports") else []
    bundles = [d for d in real_subdirs(odir) if d != "exports"] if real_dir(odir) else []
    return {"project": project, "connected": False, "note": "this console never reads Onshape keys",
            "project_class": cls, "class_source": "aces.yaml", "class_error": class_error,
            "onshape_gate": "allowed" if cls in ONSHAPE_ALLOWED else "refused for this class",
            "gate_note": "Onshape pull is gated by class in aces_onshape/project.py:26 and :149-157; this console does not evaluate that gate",
            "local": {"dir": str(odir), "present": real_dir(odir), "exports": exports, "bundles": bundles,
                      "note": "local artefacts only (cad/onshape/); nothing remote is read"}}


def h_cad_onshape(req: Request) -> Response:
    project, resp = _cad_project(req)
    if resp is not None:
        return resp
    e = req.app.cache.get(f"cad.onshape:{project}", 30.0, lambda: (True, onshape_data(project), None),
                          source=str(project_dir(project) / "cad" / "onshape"))
    return envelope(e)


# --- install -----------------------------------------------------------------------------------------------------------------------

def install(register) -> None:
    app = server.current_app()
    _state(app)
    hub = app.hub
    hub.register("projects", lambda: fetch_projects(app), 10.0, ("projects",), inline=False, source=str(work_root()))
    hub.register("golden.runs", lambda: fetch_golden_runs(app), 10.0, ("evidence", "projects", "plan"), inline=False,
                 source=str(work_root() / "*" / "golden"))
    hub.register("cad.runs", lambda: fetch_cad_runs_all(app), 10.0, ("objects", "projects"), inline=False,
                 source=str(work_root() / "*" / "cad" / "local" / "runs"))
    register("GET", "/api/projects", h_projects)
    register("GET", "/api/projects/{name}", h_project)
    register("GET", "/api/golden/runs", h_golden_runs)
    register("GET", "/api/golden/run/{project}/{run_id}", h_golden_run)
    register("GET", "/api/golden/file/{project}/{run_id}/{rel:path}", h_golden_file)
    register("GET", "/api/golden/events/{project}/{run_id}", h_golden_events)
    register("POST", "/api/golden/verify/{project}/{run_id}", h_golden_verify)
    register("GET", "/api/cad/runs", h_cad_runs)
    register("GET", "/api/cad/run/{project}/{run_id}", h_cad_run)
    register("GET", "/api/cad/file/{project}/{run_id}/{rel:path}", h_cad_file)
    register("GET", "/api/cad/bundles", h_cad_bundles)
    register("GET", "/api/cad/bundle/{project}/{name}/{rel:path}", h_cad_bundle_file)
    register("POST", "/api/cad/bundle-check/{project}/{name}", h_cad_bundle_check)
    register("GET", "/api/cad/notes/{project}", h_cad_notes)
    register("GET", "/api/cad/drawings/{project}", h_cad_drawings)
    register("GET", "/api/cad/onshape/{project}", h_cad_onshape)
