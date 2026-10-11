"""Records routes for the fixture devserver (Builder D; standard library only).

Builder C's devserver.py serves what the shell and its three plates call and answers every other route with an honest
"not available". The four records plates (Pipeline, Solvers, Objects, Evidence) call the routes of build spec 3.6-3.11,
which belong to Builder B's backend. Until that backend is merged, this module answers them inside the devserver so the
plates can be driven and checked against REAL records on this machine:

- golden runs, run detail, files, events and verify: read from the work store the devserver was pointed at (--vm-work),
  through aces_console.safefs (Builder A's walker: the rules of spec 3.13); `verify` runs `python -m aces golden verify`
  as a subprocess, which exits 127 where aces-engineering is not installed and is reported as 503, exactly as the spec says;
- CAD runs, run detail, files, bundles, bundle files, bundle check, notes, drawings, the Onshape card: read from the same
  work store; `bundle-check` runs the real check_bundle.py (the installed copy under /usr/share/aces/cad/tests, else the
  source tree's src/aces-cad/tests/check_bundle.py);
- the SIMLAB experiment and case detail and the MLflow run: replayed from the captures of 2026-10-11 for the one
  experiment (aces-cal-air-03d), case (c000) and run (97cfbb614b98494b8d3aaa63e46cfd9e) that were captured; any other id
  answers data:null with the reason, never an invented record;
- the ledger verify (aces.ledger.verify in process) and the rig-side SIMLAB chain (captures).

Nothing here is sampled or seeded. Every reply is the envelope of spec 3.1 stamped FIXTURE 2026-10-11; served files carry
Content-Security-Policy: sandbox. No rig is contacted; no token file is read.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, "..", ".."))            # src/aces-console
REPO = os.path.abspath(os.path.join(PKG, "..", ".."))             # the aces-os checkout
if PKG not in sys.path:
    sys.path.insert(0, PKG)
from aces_console import safefs  # noqa: E402  (no `aces` import inside safefs)

FIXTURE = "FIXTURE 2026-10-11"
MiB = 1 << 20
GOLDEN_NOTES = ["no grade is a sign-off", "desktop grades are not rig results"]
CHECK_BUNDLE_PATHS = (Path("/usr/share/aces/cad/tests/check_bundle.py"), Path(REPO) / "src" / "aces-cad" / "tests" / "check_bundle.py")
NOTES_HEADING_RE = re.compile(r"^## (?P<ts>\S+)(?:\s+·\s+tags:\s*(?P<tags>.*?))?(?:\s+·\s+run\s+(?P<run>\S+))?\s*$")
_BUSY: set[str] = set()
_BUSY_LOCK = threading.Lock()

FILE_ROUTE_RE = re.compile(r"^/api/(golden/file|cad/file|cad/bundle)/([^/]+)/([^/]+)/(.+)$")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------- envelopes (the shapes of spec 3.1, duplicated from devserver.py so this module imports nothing from it) ----------
def envelope(data, source, ttl_s, error=None, fetched_utc=None, stale=False):
    fetched = fetched_utc or utcnow()
    return 200, {"ok": True, "data": data, "meta": {"source": source, "fetched_utc": fetched, "age_s": 0.0, "ttl_s": ttl_s,
                                                   "stale": bool(stale), "ok": error is None, "error": error, "next_try_s": None,
                                                   "fixture": FIXTURE}}


def error(status, msg, code, source=None, upstream=None):
    return status, {"ok": False, "error": msg, "code": code, "source": source, "status": upstream, "next_try_s": None, "fixture": FIXTURE}


def capture(handler, name, ttl_s, route, transform=None, absent_what=None):
    """A capture replayed verbatim (optionally transformed), or data:null with the reason it is absent."""
    d, info = handler.server.samples.get(name)
    if d is None:
        return envelope(None, route, ttl_s, error=f"not available to the fixture devserver: {absent_what or name + '.json'}")
    data = transform(d) if transform else d
    src = f"{route} (replayed from {info['file']}, captured {info.get('captured_utc') or 'time not recorded'})"
    return envelope(data, src, ttl_s, fetched_utc=info.get("captured_utc"))


def _aces(handler):
    return handler.server.aces


def _read_json_under(root: Path, rel: str, max_bytes=None):
    """(obj, error) for a JSON file under root through the safefs walker."""
    try:
        data, _ctype, _etag = safefs.open_under(root, rel, max_bytes=max_bytes)
    except safefs.SafeFsError as e:
        return None, e.reason
    try:
        return json.loads(data.decode("utf-8")), None
    except (UnicodeDecodeError, ValueError) as e:
        return None, f"{rel}: not JSON: {e}"


def _read_text_under(root: Path, rel: str, max_bytes=None):
    try:
        data, _ctype, _etag = safefs.open_under(root, rel, max_bytes=max_bytes)
    except safefs.SafeFsError as e:
        return None, e.reason
    return data.decode("utf-8", "replace"), None


def _listing(root: Path, limit=400):
    """[{rel, bytes}] of the regular files under root (sorted, capped), plus whether the cap cut it."""
    out = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            if p.is_symlink() or not p.is_file():
                continue
            if len(out) >= limit:
                truncated = True
                break
            try:
                size = p.stat().st_size
            except OSError:
                continue
            out.append({"rel": p.relative_to(root).as_posix(), "bytes": size})
        if truncated:
            break
    return out, truncated


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------- golden (spec 3.9) ----------
def _golden_row(project: str, run_dir: Path):
    r, err = _read_json_under(run_dir, "run.json", max_bytes=1 * MiB)
    if r is None:
        return {"run_id": run_dir.name, "project": project, "run_dir": str(run_dir), "created_utc": None, "finished_utc": None,
                "status": None, "outcome": None, "verdict": None, "grade": None, "exit_code": None, "wall_s": None, "has_result": False,
                "unreadable": f"run.json unreadable: {err}"}
    return {"run_id": r.get("run_id") or run_dir.name, "project": r.get("project") or project, "run_dir": str(run_dir),
            "created_utc": r.get("created_utc"), "finished_utc": r.get("finished_utc"), "status": r.get("status"),
            "outcome": r.get("outcome"), "verdict": r.get("verdict"), "grade": r.get("grade"), "exit_code": r.get("exit_code"),
            "wall_s": (r.get("timings") or {}).get("total_s"), "has_result": (run_dir / "result.json").is_file(), "unreadable": None}


def golden_runs(handler, query):
    A = _aces(handler)
    if not A.ok():
        return envelope(None, "aces.config.WORK", 10, error=A.error)
    work = A.config.WORK
    project = (query.get("project") or [None])[0]
    if project:
        try:
            safefs.check_project(project)
        except safefs.SafeFsError as e:
            return error(e.status, e.reason, "bad-parameter", source=str(work))
        roots = [work / project]
        root = work / project / "golden"
    else:
        roots = [p.parent for p in A.project.list_projects()] if work.exists() else []
        root = work
    if not work.exists():
        return envelope({"runs": [], "root": str(work), "reason": f"{work} does not exist"}, str(work), 10)
    rows = []
    for pdir in roots:
        gdir = pdir / "golden"
        if not gdir.is_dir():
            continue
        for rd in gdir.iterdir():
            if rd.is_dir() and (rd / "run.json").is_file() and safefs.GOLDEN_RUN_ID_RE.match(rd.name):
                rows.append(_golden_row(pdir.name, rd))
    rows.sort(key=lambda r: (r.get("created_utc") or "", r["run_id"]), reverse=True)
    reason = None if rows else f"no golden runs under {root}"
    return envelope({"runs": rows, "root": str(root), "reason": reason}, str(root), 10)


def _sweep(run_dir: Path):
    text, err = _read_text_under(run_dir, "sweep.csv", max_bytes=1 * MiB)
    if text is None:
        return None, err
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return {"columns": [], "rows": []}, None
    cols = rows[0]

    def conv(v):
        try:
            f = float(v)
            return int(f) if f.is_integer() and "." not in v else f
        except ValueError:
            return v

    return {"columns": cols, "rows": [[conv(v) for v in r] for r in rows[1:] if r]}, None


def golden_run(handler, project, run_id):
    A = _aces(handler)
    if not A.ok():
        return envelope(None, "aces.config.WORK", 10, error=A.error)
    try:
        safefs.check_project(project)
        safefs.check_golden_run_id(run_id)
    except safefs.SafeFsError as e:
        return error(e.status, e.reason, "bad-parameter")
    run_dir = A.config.WORK / project / "golden" / run_id
    if not (run_dir / "run.json").is_file():
        return error(404, f"no golden run {run_id} in {project} (run.json absent)", "not-found", source=str(run_dir))
    read_errors = []

    def rj(rel, cap=8 * MiB):
        obj, err = _read_json_under(run_dir, rel, max_bytes=cap)
        if obj is None and err and not err.endswith("does not exist"):
            read_errors.append({"rel": rel, "error": err})
        return obj

    run = rj("run.json", 1 * MiB)
    result = rj("result.json")
    absent = None
    if result is None:
        absent = f"result.json not written: this run ended with status {(run or {}).get('status')}"
        result = run  # aces golden show falls back to run.json (golden_cli.py:290-292)
    validation = rj("validation.json")
    compute_used = rj("compute_used.json")
    report = rj("report.json")
    sweep, sweep_err = _sweep(run_dir)
    if sweep is None and sweep_err and not sweep_err.endswith("does not exist"):
        read_errors.append({"rel": "sweep.csv", "error": sweep_err})
    winner_dir = run_dir / "winner"
    winner = {"json": rj("winner/winner.json") if (winner_dir / "winner.json").is_file() else None,
              "files": sorted(p.name for p in winner_dir.iterdir() if p.is_file()) if winner_dir.is_dir() else []}
    cand_dir = run_dir / "candidate"
    candidate = {"files": sorted(p.name for p in cand_dir.iterdir() if p.is_file())} if cand_dir.is_dir() else None
    fig_dir = run_dir / "figures"
    figures = sorted(p.name for p in fig_dir.iterdir() if p.is_file() and p.suffix.lower() in (".png", ".svg")) if fig_dir.is_dir() else []
    ledgers = rj("evidence/ledgers.json") if (run_dir / "evidence" / "ledgers.json").is_file() else None
    files, truncated = _listing(run_dir)
    events_tail = None
    if (run_dir / "events.jsonl").is_file():
        text, err = _read_text_under(run_dir, "events.jsonl", max_bytes=16 * MiB)
        if text is None:
            read_errors.append({"rel": "events.jsonl", "error": err})
        else:
            lines = [ln for ln in text.splitlines() if ln.strip()][-50:]
            events_tail = []
            for ln in lines:
                try:
                    events_tail.append(json.loads(ln))
                except ValueError:
                    events_tail.append(ln)
    data = {"run": run, "result": result, "result_absent_reason": absent, "validation": validation, "compute_used": compute_used,
            "report": report, "sweep": sweep, "winner": winner, "candidate": candidate, "figures": figures,
            "evidence_ledgers": ledgers, "files": files, "files_truncated": truncated, "events_tail": events_tail,
            "read_errors": read_errors, "notes": list(GOLDEN_NOTES), "project": project, "run_id": run_id, "run_dir": str(run_dir)}
    return envelope(data, str(run_dir), 10)


def golden_events(handler, project, run_id, query):
    A = _aces(handler)
    if not A.ok():
        return envelope(None, "aces.config.WORK", 3, error=A.error)
    try:
        safefs.check_project(project)
        safefs.check_golden_run_id(run_id)
    except safefs.SafeFsError as e:
        return error(e.status, e.reason, "bad-parameter")
    run_dir = A.config.WORK / project / "golden" / run_id
    run, _ = _read_json_under(run_dir, "run.json", max_bytes=1 * MiB)
    if run is None:
        return error(404, f"no golden run {run_id} in {project}", "not-found", source=str(run_dir))
    if not (run_dir / "events.jsonl").is_file():
        return error(404, "events.jsonl absent", "not-found", source=str(run_dir))
    if run.get("status") != "running":
        return error(404, f"run {run_id} is not running (status {run.get('status')}); its last events are in the run detail", "not-running", source=str(run_dir))
    text, err = _read_text_under(run_dir, "events.jsonl", max_bytes=16 * MiB)
    if text is None:
        return error(500, err, "read-error", source=str(run_dir))
    lines = [ln for ln in text.splitlines() if ln.strip()]
    try:
        after = int((query.get("after") or ["0"])[0])
    except ValueError:
        return error(400, "after must be an integer", "bad-parameter")
    return envelope({"lines": lines[after:], "eof": False, "status": run.get("status"), "n": len(lines)}, str(run_dir / "events.jsonl"), 3)


def golden_verify(handler, project, run_id):
    A = _aces(handler)
    if not A.ok():
        return error(503, A.error, "not-installed")
    try:
        safefs.check_project(project)
        safefs.check_golden_run_id(run_id)
    except safefs.SafeFsError as e:
        return error(e.status, e.reason, "bad-parameter")
    run_dir = A.config.WORK / project / "golden" / run_id
    if not (run_dir / "run.json").is_file():
        return error(404, f"no golden run {run_id} in {project}", "not-found", source=str(run_dir))
    key = f"verify:{run_dir}"
    with _BUSY_LOCK:
        if key in _BUSY:
            return error(409, f"a verify is already running for {run_dir}", "busy", source=str(run_dir))
        _BUSY.add(key)
    try:
        env = dict(os.environ)
        base = os.path.join(REPO, "src", "aces-base")
        env["PYTHONPATH"] = base + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        cmd = [sys.executable, "-m", "aces", "golden", "verify", str(run_dir), "--json", "--no-model"]
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
        except subprocess.TimeoutExpired:
            return error(500, "verify failed: timed out after 300 s", "verify-failed", source=str(run_dir))
        except OSError as e:
            return error(500, f"verify failed: {e}", "verify-failed", source=str(run_dir))
        if proc.returncode == 127:
            return error(503, "aces-engineering not installed (exit 127)", "not-installed", source=str(run_dir))
        try:
            out = json.loads(proc.stdout) if proc.stdout.strip() else None
        except ValueError:
            out = None
        if not isinstance(out, dict):
            return 500, {"ok": False, "error": "verify failed", "code": "verify-failed", "source": str(run_dir), "status": None, "next_try_s": None,
                         "exit": proc.returncode, "stderr_tail": proc.stderr[-2048:], "fixture": FIXTURE}
        data = {"run_dir": out.get("run_dir") or str(run_dir), "ok": out.get("ok"), "checks": out.get("checks") or [], "exit": proc.returncode,
                "ran_at": utcnow(), "elapsed_s": round(time.time() - t0, 3)}
        return envelope(data, " ".join(cmd[1:6]), 0)
    finally:
        with _BUSY_LOCK:
            _BUSY.discard(key)


# ---------- CAD (spec 3.10) ----------
def _cad_root(A, project):
    return A.config.WORK / project / "cad" / "local"


def _cad_run_row(rd: Path):
    rj = rd / "run.json"
    if not rj.is_file():
        return {"run_id": rd.name, "status": None, "name": None, "started": None, "finished": None, "sandbox": None, "unreadable": "run.json missing"}
    try:
        if rj.stat().st_size > 8 * MiB:
            return {"run_id": rd.name, "status": None, "name": None, "started": None, "finished": None, "sandbox": None, "unreadable": "run.json exceeds 8 MiB (store limit)"}
    except OSError as e:
        return {"run_id": rd.name, "status": None, "name": None, "started": None, "finished": None, "sandbox": None, "unreadable": f"run.json unreadable: {e}"}
    r, err = _read_json_under(rd, "run.json", max_bytes=8 * MiB)
    if r is None:
        return {"run_id": rd.name, "status": None, "name": None, "started": None, "finished": None, "sandbox": None, "unreadable": f"run.json unreadable: {err}"}
    sb = r.get("sandbox") or {}
    return {"run_id": r.get("run_id") or rd.name, "status": r.get("status"), "name": r.get("name"), "started": r.get("created_at"),
            "finished": r.get("finished_at"), "sandbox": {"mode": sb.get("mode"), "confined": sb.get("confined"), "platform": sb.get("platform")},
            "unreadable": None}


def cad_runs(handler, query):
    A = _aces(handler)
    if not A.ok():
        return envelope(None, "aces.config.WORK", 10, error=A.error)
    project = (query.get("project") or [None])[0]
    if not project:
        return error(400, "project is required (?project=<name>)", "bad-parameter")
    try:
        safefs.check_project(project)
    except safefs.SafeFsError as e:
        return error(e.status, e.reason, "bad-parameter")
    runs_dir = _cad_root(A, project) / "runs"
    if not runs_dir.is_dir():
        return envelope({"project": project, "runs": [], "counts": {"total": 0, "unreadable": 0}, "note": f"no CAD store in {project}"}, str(runs_dir), 10)
    rows = [_cad_run_row(rd) for rd in sorted(runs_dir.iterdir(), key=lambda p: p.name, reverse=True) if rd.is_dir() and safefs.CAD_RUN_ID_RE.match(rd.name)]
    return envelope({"project": project, "runs": rows, "counts": {"total": len(rows), "unreadable": sum(1 for r in rows if r["unreadable"])}}, str(runs_dir), 10)


def cad_run(handler, project, run_id):
    A = _aces(handler)
    if not A.ok():
        return envelope(None, "aces.config.WORK", 10, error=A.error)
    try:
        safefs.check_project(project)
        safefs.check_run_id(run_id)
    except safefs.SafeFsError as e:
        return error(e.status, e.reason, "bad-parameter")
    rd = _cad_root(A, project) / "runs" / run_id
    if not rd.is_dir() or not (rd / "run.json").is_file():
        return error(404, f"no run {run_id} in {project}", "not-found", source=str(rd))
    try:
        if (rd / "run.json").stat().st_size > 8 * MiB:
            return error(413, "run.json exceeds 8 MiB (store limit)", "too-large", source=str(rd))
    except OSError as e:
        return error(500, f"cannot stat run.json: {e}", "read-error", source=str(rd))
    record, err = _read_json_under(rd, "run.json", max_bytes=8 * MiB)
    if record is None:
        return error(500, f"run.json unreadable: {err}", "read-error", source=str(rd))
    files = []
    for dirpath, dirnames, filenames in os.walk(rd):
        dirnames.sort()
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            if p.is_symlink() or not p.is_file():
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            try:
                kind = safefs.classify(fn, kinds=frozenset(safefs.KINDS))[0]
            except safefs.SafeFsError:
                kind = "other"
            files.append({"rel": p.relative_to(rd).as_posix(), "size": size, "sha256": _sha256_file(p) if size <= 64 * MiB else None, "kind": kind})
    return envelope({"run_id": run_id, "project": project, "record": record, "files": files}, str(rd), 10)


def cad_bundles(handler, query):
    A = _aces(handler)
    if not A.ok():
        return envelope(None, "aces.config.WORK", 10, error=A.error)
    project = (query.get("project") or [None])[0]
    if not project:
        return error(400, "project is required (?project=<name>)", "bad-parameter")
    try:
        safefs.check_project(project)
    except safefs.SafeFsError as e:
        return error(e.status, e.reason, "bad-parameter")
    root = _cad_root(A, project)
    if not root.is_dir():
        return envelope({"project": project, "bundles": [], "note": f"no bundles in {project}"}, str(root), 10)
    out = []
    for bd in sorted((p for p in root.iterdir() if p.is_dir() and p.name != "runs" and safefs.BUNDLE_NAME_RE.match(p.name)), key=lambda p: p.name, reverse=True):
        has = {"source": (bd / "source.json").is_file(), "outcome": (bd / "outcome.json").is_file(), "parameters": (bd / "parameters.json").is_file(),
               "part_step": (bd / "part.step").is_file(), "part_stl": (bd / "part.stl").is_file(), "source_py": (bd / "source.py").is_file()}
        summary = None
        read_error = None
        if has["outcome"]:
            oc, err = _read_json_under(bd, "outcome.json", max_bytes=1 * MiB)
            if oc is None:
                read_error = err
            else:
                led = oc.get("ledger") or {}
                cs = oc.get("cad_source") or {}
                summary = {"ledger_written": led.get("written"), "ledger_n": led.get("n"), "ledger_kind": led.get("kind"),
                           "cad_source_recorded": cs.get("recorded"), "cad_source_ref": cs.get("ref"), "updated_at": oc.get("updated_at")}
        src = None
        if has["source"]:
            sj, err = _read_json_under(bd, "source.json", max_bytes=1 * MiB)
            if sj is not None:
                src = {"run_id": sj.get("run_id"), "name": sj.get("name"), "kind": sj.get("kind"), "geometry_sha256": sj.get("geometry_sha256"),
                       "exported_at": sj.get("exported_at"), "source": sj.get("source")}
            elif read_error is None:
                read_error = err
        out.append({"name": bd.name, "has": has, "outcome_summary": summary, "source_summary": src, "read_error": read_error})
    data = {"project": project, "bundles": out}
    if not out:
        data["note"] = f"no bundles in {project}"
    return envelope(data, str(root), 10)


def cad_bundle_check(handler, project, name):
    A = _aces(handler)
    if not A.ok():
        return error(503, A.error, "not-installed")
    try:
        safefs.check_project(project)
        safefs.check_bundle_name(name)
    except safefs.SafeFsError as e:
        return error(e.status, e.reason, "bad-parameter")
    bd = _cad_root(A, project) / name
    if not bd.is_dir():
        return error(404, f"no bundle {name} in {project}", "not-found", source=str(bd))
    script = next((p for p in CHECK_BUNDLE_PATHS if p.is_file()), None)
    if script is None:
        return error(503, "aces-cad not installed (check_bundle.py absent)", "not-installed", source=str(CHECK_BUNDLE_PATHS[0]))
    key = f"bundle:{bd}"
    with _BUSY_LOCK:
        if key in _BUSY:
            return error(409, f"a check is already running for {name}", "busy", source=str(bd))
        _BUSY.add(key)
    try:
        cmd = [sys.executable, "-I", str(script), str(bd)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return error(500, "check_bundle.py timed out after 60 s", "check-failed", source=str(bd))
        lines = [ln for ln in (proc.stdout + ("\n" + proc.stderr if proc.stderr else "")).splitlines() if ln.strip()]
        all_match = any(ln.rstrip().endswith("all files match") for ln in lines)
        return envelope({"name": name, "project": project, "exit": proc.returncode, "lines": lines, "all_match": all_match, "script": str(script),
                         "ran_at": utcnow()}, f"python -I {script.name} {name}", 0)
    finally:
        with _BUSY_LOCK:
            _BUSY.discard(key)


def cad_notes(handler, project):
    A = _aces(handler)
    if not A.ok():
        return envelope(None, "aces.config.WORK", 10, error=A.error)
    try:
        safefs.check_project(project)
    except safefs.SafeFsError as e:
        return error(e.status, e.reason, "bad-parameter")
    pdir = A.config.WORK / project
    rel = None
    for cand in ("cad/NOTES.md", "NOTES.md"):   # notes.py writes <project>/cad/NOTES.md
        if (pdir / cand).is_file():
            rel = cand
            break
    if rel is None:
        return envelope({"project": project, "entries": [], "truncated": False, "note": f"no NOTES.md in {project}", "path": str(pdir / "cad" / "NOTES.md")}, str(pdir), 10)
    p = pdir / rel
    try:
        size = p.stat().st_size
    except OSError as e:
        return envelope(None, str(p), 10, error=f"NOTES.md unreadable: {e}")
    truncated = size > 1 * MiB
    try:
        with open(p, "rb") as fh:
            raw = fh.read(1 * MiB)
    except OSError as e:
        return envelope(None, str(p), 10, error=f"NOTES.md unreadable: {e}")
    text = raw.decode("utf-8", "replace")
    entries = []
    preamble = []
    cur = None
    for line in text.split("\n"):
        if line.startswith("## "):
            m = NOTES_HEADING_RE.match(line)
            cur = {"heading": line[3:].strip(), "ts": m.group("ts") if m else None,
                   "tags": [t.strip() for t in (m.group("tags") or "").split(",") if t.strip()] if m else [],
                   "run_id": m.group("run") if m else None, "body": ""}
            entries.append(cur)
        elif cur is None:
            preamble.append(line)
        else:
            cur["body"] += line + "\n"
    for e in entries:
        e["body"] = e["body"].strip("\n")
    return envelope({"project": project, "path": str(p), "entries": entries, "truncated": truncated, "bytes": size,
                     "preamble": "\n".join(preamble).strip()}, str(p), 10)


def cad_drawings(handler, project):
    A = _aces(handler)
    if not A.ok():
        return envelope(None, "aces.config.WORK", 10, error=A.error)
    try:
        safefs.check_project(project)
    except safefs.SafeFsError as e:
        return error(e.status, e.reason, "bad-parameter")
    runs_dir = _cad_root(A, project) / "runs"
    out = []
    if runs_dir.is_dir():
        for rd in sorted((p for p in runs_dir.iterdir() if p.is_dir() and safefs.CAD_RUN_ID_RE.match(p.name)), key=lambda p: p.name, reverse=True):
            ddir = rd / "drawings"
            if not ddir.is_dir():
                continue
            for jf in sorted(ddir.glob("*.json")):
                dr, err = _read_json_under(rd, f"drawings/{jf.name}", max_bytes=8 * MiB)
                if not isinstance(dr, dict) or dr.get("schema") != "aces.cad.drawing/1":
                    continue
                stem = jf.stem
                files = {}
                for ext in ("dxf", "svg", "png", "json"):
                    files[ext] = f"/api/cad/file/{project}/{rd.name}/drawings/{stem}.{ext}" if (ddir / f"{stem}.{ext}").is_file() else None
                out.append({"id": stem, "run_id": dr.get("run_id") or rd.name, "schema": dr.get("schema"), "views": dr.get("views") or [],
                            "n_dimensions": len(dr.get("dimensions") or []), "dimensions": dr.get("dimensions") or [], "hole_table": dr.get("hole_table"),
                            "projection": dr.get("projection"), "units": dr.get("units"), "scale": dr.get("scale"), "audit_errors": dr.get("audit_errors"),
                            "method": dr.get("method"), "notes": dr.get("notes") or [], "warnings": dr.get("warnings") or [],
                            "files": files, "recorded_files": dr.get("files") or {}})
    data = {"project": project, "drawings": out}
    if not out:
        data["note"] = f"no drawings in {project}" if runs_dir.is_dir() else f"no CAD store in {project}"
    return envelope(data, str(runs_dir), 10)


def cad_onshape(handler, project):
    A = _aces(handler)
    if not A.ok():
        return envelope(None, "aces.config.WORK", 60, error=A.error)
    try:
        safefs.check_project(project)
    except safefs.SafeFsError as e:
        return error(e.status, e.reason, "bad-parameter")
    cls = None
    p = A.config.WORK / project / "aces.yaml"
    if p.is_file():
        try:
            cls = A.project._load(p).get("class")
        except (OSError, UnicodeDecodeError, SystemExit):
            cls = None
    return envelope({"connected": False, "note": "this console never reads Onshape keys", "project": project, "project_class": cls,
                     "gate_note": "Onshape pull is gated by class in aces_onshape/project.py:26 and :149-157; this console does not evaluate that gate"},
                    "aces-console (no remote read)", 60)


# ---------- SIMLAB detail and MLflow (spec 3.6, 3.7): the captures, or the honest reason ----------
def sim_experiment(handler, eid, query):
    d, info = handler.server.samples.get("simlab-experiment-detail")
    if d is None:
        return envelope(None, "SIMLAB console GET /api/experiments/{eid}", 3.5, error="not available to the fixture devserver: simlab-experiment-detail.json")
    if d.get("id") != eid:
        return envelope(None, f"SIMLAB console GET /api/experiments/{eid}", 3.5,
                        error=f"not captured in the fixtures: the one experiment detail capture of 2026-10-11 is {d.get('id')} (root {d.get('root')}); {eid} was not captured")
    return capture(handler, "simlab-experiment-detail", 3.5, f"SIMLAB console GET /api/experiments/{eid}?root={(query.get('root') or ['queue'])[0]}")


def sim_case(handler, eid, cid, query):
    d, info = handler.server.samples.get("simlab-case-detail")
    if d is None:
        return envelope(None, "SIMLAB console GET /api/case/{eid}/{cid}", 3.5, error="not available to the fixture devserver: simlab-case-detail.json")
    exp = (d.get("experiment") or {}).get("id")
    if exp != eid or d.get("case_id") != cid:
        return envelope(None, f"SIMLAB console GET /api/case/{eid}/{cid}", 3.5,
                        error=f"not captured in the fixtures: the one case detail capture of 2026-10-11 is {exp}/{d.get('case_id')}; {eid}/{cid} was not captured")

    def with_mlflow(case):
        out = dict(case)
        rid = case.get("mlflow_run_id")
        run, _info = handler.server.samples.get("mlflow-run-get")
        captured = ((run or {}).get("run") or {}).get("info", {}).get("run_id")
        if not rid:
            out["mlflow"] = {"run": None, "ui_url": None, "error": "no mlflow_run_id on this case"}
        elif run and captured == rid:
            out["mlflow"] = {"run": run["run"], "ui_url": _mlflow_ui(handler, run["run"]["info"]), "error": None}
        else:
            out["mlflow"] = {"run": None, "ui_url": None,
                             "error": f"not captured: MLflow runs/get for run {rid} was not captured on 2026-10-11 (the one capture is run {captured or 'none'})"}
        return out

    return capture(handler, "simlab-case-detail", 3.5, f"SIMLAB console GET /api/case/{eid}/{cid}?root={(query.get('root') or ['queue'])[0]}", transform=with_mlflow)


def _mlflow_ui(handler, info):
    base = (handler.server.aces.cfg.get("server") or {}).get("mlflow")
    if not base or not info:
        return None
    return f"{base}/#/experiments/{info.get('experiment_id')}/runs/{info.get('run_id')}"


def mlflow_run(handler, run_id):
    d, info = handler.server.samples.get("mlflow-run-get")
    if d is None:
        return envelope(None, f"MLflow GET /api/2.0/mlflow/runs/get?run_id={run_id}", 30, error="not available to the fixture devserver: mlflow-run-get.json")
    captured = ((d.get("run") or {}).get("info") or {}).get("run_id")
    if captured != run_id:
        return envelope(None, f"MLflow GET /api/2.0/mlflow/runs/get?run_id={run_id}", 30,
                        error=f"not captured in the fixtures: the one runs/get capture of 2026-10-11 is run {captured}; {run_id} was not captured")
    return capture(handler, "mlflow-run-get", 30, f"MLflow GET /api/2.0/mlflow/runs/get?run_id={run_id}",
                   transform=lambda r: {"run": r.get("run"), "ui_url": _mlflow_ui(handler, (r.get("run") or {}).get("info"))})


# ---------- ledgers (spec 3.11) ----------
def ledger_verify(handler):
    A = _aces(handler)
    if not A.ok():
        return envelope(None, "aces.ledger.verify", 30, error=A.error)
    path = str(A.ledger.path())
    try:
        ok, n, msg = A.ledger.verify()
    except SystemExit as e:
        return envelope(None, path, 30, error=f"ledger unreadable: {e}")
    return envelope({"ok": bool(ok), "n": n, "first_bad": None if ok else n, "message": msg, "path": path}, path, 30)


def ledger_simlab(handler):
    ev, info = handler.server.samples.get("simlab-evidence")
    vf, _vinfo = handler.server.samples.get("simlab-evidence-verify")
    route = "SIMLAB console GET /api/evidence + /api/evidence/verify"
    if ev is None:
        return envelope(None, route, 30, error="not available to the fixture devserver: simlab-evidence.json")
    data = {"entries": ev.get("ledger") or [], "verify": vf, "note": "rig-side chain; verified by the SIMLAB console, not by this desktop",
            "verify_note": None if vf is not None else "evidence/verify not captured"}
    return envelope(data, f"{route} (replayed from {info['file']}, captured {info.get('captured_utc')})", 30, fetched_utc=info.get("captured_utc"))


# ---------- dispatch ----------
def handle_get(handler, path, query):
    """(status, obj) for a JSON records route, or None when the path is not one of them."""
    if path == "/api/golden/runs":
        return golden_runs(handler, query)
    m = re.fullmatch(r"/api/golden/run/([^/]+)/([^/]+)", path)
    if m:
        return golden_run(handler, m.group(1), m.group(2))
    m = re.fullmatch(r"/api/golden/events/([^/]+)/([^/]+)", path)
    if m:
        return golden_events(handler, m.group(1), m.group(2), query)
    if path == "/api/cad/runs":
        return cad_runs(handler, query)
    m = re.fullmatch(r"/api/cad/run/([^/]+)/([^/]+)", path)
    if m:
        return cad_run(handler, m.group(1), m.group(2))
    if path == "/api/cad/bundles":
        return cad_bundles(handler, query)
    m = re.fullmatch(r"/api/cad/notes/([^/]+)", path)
    if m:
        return cad_notes(handler, m.group(1))
    m = re.fullmatch(r"/api/cad/drawings/([^/]+)", path)
    if m:
        return cad_drawings(handler, m.group(1))
    m = re.fullmatch(r"/api/cad/onshape/([^/]+)", path)
    if m:
        return cad_onshape(handler, m.group(1))
    m = re.fullmatch(r"/api/sim/experiments?/([^/]+)", path)
    if m:
        return sim_experiment(handler, m.group(1), query)
    m = re.fullmatch(r"/api/sim/case/([^/]+)/([^/]+)", path)
    if m:
        return sim_case(handler, m.group(1), m.group(2), query)
    if path == "/api/sim/file":
        return error(404, "not available to the fixture devserver: case files are not in the captures (names and sizes only)", "fixture", source=path)
    m = re.fullmatch(r"/api/sim/status/([^/]+)", path)
    if m:
        return envelope(None, f"simlab-mcp sim_status({m.group(1)})", 5, error="not available to the fixture devserver: simlab-mcp sim_status was not captured; no rig is contacted")
    m = re.fullmatch(r"/api/mlflow/run/([^/]+)", path)
    if m:
        return mlflow_run(handler, m.group(1))
    if path == "/api/ledger/verify":
        return ledger_verify(handler)
    if path == "/api/ledger/simlab":
        return ledger_simlab(handler)
    return None


def handle_post(handler, path, body):
    """(status, obj) for a records POST, or None."""
    m = re.fullmatch(r"/api/golden/verify/([^/]+)/([^/]+)", path)
    if m:
        return golden_verify(handler, m.group(1), m.group(2))
    m = re.fullmatch(r"/api/cad/bundle-check/([^/]+)/([^/]+)", path)
    if m:
        return cad_bundle_check(handler, m.group(1), m.group(2))
    return None


def is_file_route(path) -> bool:
    return FILE_ROUTE_RE.match(path) is not None


def send_file(handler, path, query) -> int:
    """Serve a golden, CAD run or bundle file under the safefs rules (spec 3.13: sandbox CSP, nosniff, ETag, 304)."""
    m = FILE_ROUTE_RE.match(path)
    kind, project, ident, rel = m.group(1), m.group(2), m.group(3), m.group(4)
    A = _aces(handler)
    if not A.ok():
        return handler._send_json(*error(503, A.error, "not-installed", source=path))
    try:
        safefs.check_project(project)
        if kind == "golden/file":
            safefs.check_golden_run_id(ident)
            root = A.config.WORK / project / "golden" / ident
            kinds = safefs.DEFAULT_KINDS | {"bin"}      # .joblib/.pkl as octet-stream: streamed, never loaded
        elif kind == "cad/file":
            safefs.check_run_id(ident)
            root = _cad_root(A, project) / "runs" / ident
            kinds = safefs.DEFAULT_KINDS
        else:
            safefs.check_bundle_name(ident)
            root = _cad_root(A, project) / ident
            kinds = safefs.DEFAULT_KINDS
        if not root.is_dir():
            return handler._send_json(*error(404, f"no {kind.split('/')[0]} record {ident} in {project}", "not-found", source=path))
        data, ctype, digest = safefs.open_under(root, rel, kinds=kinds)
    except safefs.SafeFsError as e:
        code = {400: "bad-path", 403: "refused", 404: "not-found", 413: "too-large", 415: "unsupported-type"}.get(e.status, "read-error")
        return handler._send_json(*error(e.status, e.reason if e.status != 403 or e.reason.startswith("refusing") else f"refusing to read {rel}: {e.reason}", code, source=path))
    hdrs = safefs.headers_for(ctype, digest, rel.rsplit("/", 1)[-1], sandbox=True)
    inm = handler.headers.get("If-None-Match")
    if inm and inm.strip() == hdrs["ETag"]:
        handler.send_response(304)
        handler.send_header("ETag", hdrs["ETag"])
        handler.send_header("Cache-Control", hdrs["Cache-Control"])
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return 304
    extra = {k: v for k, v in hdrs.items() if k not in ("Content-Type",)}
    return handler._send_bytes(200, data, ctype, extra)
