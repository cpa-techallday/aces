"""The ledgers (build spec 3.11). Builder B.

    GET /api/ledger?kind=&n=       the operator ledger $ACES_STATE/ledger.jsonl through aces.ledger.entries(): newest
                                   first, `kind` as a prefix filter, n default 200 and at most 2000; a corrupt file is
                                   200 with data null and meta.error "ledger unreadable: ..." (the SystemExit of
                                   aces.ledger.entries at ledger.py:121 is caught)
    GET /api/ledger/verify         aces.ledger.verify() -> {ok, n, first_bad, message, path}; cached 30 s; a read
    GET /api/ledger/simlab         the rig-side evidence chain as the SIMLAB console reports it (sim.evidence and
                                   sim.evidence.verify), with the note that this desktop does not verify it
    GET /api/ledger/simlab/desktop the desktop's own simlab evidence ledger ($XDG_DATA_HOME/aces/simlab/evidence/
                                   ledger.jsonl, written by aces golden runs) with its `previous` chain re-checked here

Nothing here writes. Payload values under credential-looking keys are masked by the encoder (jsonenc).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from aces import ledger

from . import cache as cache_mod, config as console_config, server
from .server import Request, Response, default_meta, envelope, error

N_DEFAULT = 200
N_MAX = 2000
SIMLAB_RIG_NOTE = "rig-side chain; verified by the SIMLAB console, not by this desktop"
SIMLAB_DESKTOP_NOTE = ("desktop-side simlab evidence ledger written by aces golden runs; the `previous` chain is "
                       "re-checked by this console, the bundle hashes by aces golden verify")


class LedgerState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cache: dict[str, tuple[tuple[str, tuple[int, int]], list[dict[str, Any]] | None, str | None]] = {}


def _state(app: server.App) -> LedgerState:
    st = app.state.get("ledgers")
    if st is None:
        st = app.state["ledgers"] = LedgerState()
    return st


def _file_key(path: Path) -> tuple[int, int] | None:
    try:
        s = path.stat()
    except OSError:
        return None
    return (s.st_mtime_ns, s.st_size)


def aces_entries(app: server.App) -> tuple[list[dict[str, Any]] | None, str | None]:
    """aces.ledger.entries(), parsed once per (mtime, size); (entries, error)."""
    st = _state(app)
    p = ledger.path()
    fkey = _file_key(p)
    if fkey is None:
        return [], None
    key = (str(p), fkey)
    with st.lock:
        hit = st.cache.get("aces")
        if hit is not None and hit[0] == key:
            return hit[1], hit[2]
    try:
        es = ledger.entries()
        err = None
    except SystemExit as e:
        es, err = None, f"ledger unreadable: {e}"
    with st.lock:
        st.cache["aces"] = (key, es, err)
    return es, err


def absent(source: str, message: str, ttl_s: float = 5.0) -> Response:
    """A 200 envelope with data null and meta.error (the honest-absence shape of spec 3.1)."""
    meta = default_meta(source, ttl_s)
    meta.update({"ok": False, "error": message})
    return Response(status=200, data=None, meta=meta)


def rows_of(es: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"n": i, "ts": e.get("ts"), "kind": e.get("kind"), "operator": e.get("operator"), "host": e.get("host"),
             "payload": e.get("payload"), "hash": e.get("hash"), "prev": e.get("prev")} for i, e in enumerate(es, 1)]


# --- /api/ledger -------------------------------------------------------------------------------------------------------------

def h_ledger(req: Request) -> Response:
    app = req.app
    path = str(ledger.path())
    kind = req.q("kind") or ""
    try:
        n = int(req.q("n", str(N_DEFAULT)) or N_DEFAULT)
    except ValueError:
        return error(400, "n must be an integer", "bad-request", source=path)
    if n < 1:
        return error(400, "n must be at least 1", "bad-request", source=path)
    n = min(n, N_MAX)
    if len(kind) > 64:
        return error(400, "kind is too long", "bad-request", source=path)
    es, err = aces_entries(app)
    if es is None:
        return absent(path, err or "ledger unreadable")
    rows = rows_of(es)
    if kind:
        rows = [r for r in rows if str(r.get("kind") or "").startswith(kind)]
    rows.reverse()
    kinds: dict[str, int] = {}
    for e in es:
        k = str(e.get("kind"))
        kinds[k] = kinds.get(k, 0) + 1
    data = {"path": path, "exists": Path(path).is_file(), "n_total": len(es), "n_matching": len(rows), "entries": rows[:n],
            "kind_filter": kind or None, "n": n, "kinds": kinds}
    return server.ok(data, source=path, ttl_s=5.0)


def fetch_verify(app: server.App):
    try:
        okv, n, msg = ledger.verify()
    except SystemExit as e:
        return False, None, f"ledger unreadable: {e}"
    first_bad = None
    if not okv:
        try:
            first_bad = int(msg.split(":", 1)[0].split()[-1])
        except (ValueError, IndexError):
            first_bad = None
    return True, {"ok": okv, "n": n, "first_bad": first_bad, "message": msg, "path": str(ledger.path())}, None


def h_verify(req: Request) -> Response:
    e = req.app.cache.get("ledger.verify", 30.0, lambda: fetch_verify(req.app), source=str(ledger.path()))
    return envelope(e)


def fetch_aces_summary(app: server.App):
    """The ledger.aces poll topic (notice-only): a cheap change detector for the foot facts and the Evidence plate."""
    p = ledger.path()
    key = _file_key(p)
    if key is None:
        return True, {"path": str(p), "exists": False, "n_total": 0, "mtime_ns": None, "size": 0, "error": None}, None
    es, err = aces_entries(app)
    return True, {"path": str(p), "exists": True, "n_total": len(es) if es is not None else None, "mtime_ns": key[0],
                  "size": key[1], "error": err, "last_kind": (es[-1].get("kind") if es else None),
                  "last_ts": (es[-1].get("ts") if es else None)}, None


# --- the rig-side chain --------------------------------------------------------------------------------------------------------

def h_simlab(req: Request) -> Response:
    app = req.app
    if app.hub.source("sim.evidence") is None:
        return error(503, "the sim module is not installed; the rig-side chain is not available", "not-installed")
    ev = app.hub.get("sim.evidence")
    vf = app.hub.get("sim.evidence.verify")
    rows = (ev.data or {}).get("ledger") if isinstance(ev.data, dict) else None
    data = {"entries": rows, "n": len(rows) if isinstance(rows, list) else None, "verify": vf.data, "note": SIMLAB_RIG_NOTE,
            "errors": {"entries": ev.error, "verify": vf.error}, "stale": {"entries": ev.stale, "verify": vf.stale}}
    return envelope(ev, data=data, extra_meta={"parts": {"entries": ev.meta(), "verify": vf.meta()}})


# --- the desktop simlab evidence ledger ----------------------------------------------------------------------------------------

def desktop_simlab_path() -> Path:
    return console_config.data_dir() / "simlab" / "evidence" / "ledger.jsonl"


def read_desktop_simlab(path: Path) -> dict[str, Any]:
    """Rows and the `previous` chain check (vendor/simlab/evidence_bundle.py:208-224 checks ent.previous == the previous
    row's sha256 and the bundle file hash; the bundle hash needs the bundle file, which aces golden verify checks)."""
    if not path.is_file():
        return {"path": str(path), "exists": False, "entries": [], "n_total": 0,
                "verify": {"ok": None, "problems": [], "head": None, "note": "no desktop simlab ledger yet"}, "note": SIMLAB_DESKTOP_NOTE}
    rows: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    prev = None
    try:
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                line = line.strip()
                if not line:
                    continue
                try:
                    ent = json.loads(line)
                except json.JSONDecodeError as e:
                    problems.append({"line": i, "problem": f"not JSON: {e.msg}", "path": None})
                    continue
                if not isinstance(ent, dict):
                    problems.append({"line": i, "problem": "not an object", "path": None})
                    continue
                if ent.get("previous") != prev:
                    problems.append({"line": i, "problem": "chain break", "path": ent.get("path")})
                bundle_path = ent.get("path")
                present = isinstance(bundle_path, str) and os.path.isfile(bundle_path)
                row = dict(ent)
                row["n"] = len(rows) + 1
                row["bundle_present"] = present
                rows.append(row)
                prev = ent.get("sha256")
    except OSError as e:
        return {"path": str(path), "exists": True, "entries": [], "n_total": 0,
                "verify": {"ok": None, "problems": [{"line": None, "problem": f"unreadable: {e.strerror or e}", "path": None}], "head": None},
                "note": SIMLAB_DESKTOP_NOTE}
    return {"path": str(path), "exists": True, "entries": rows, "n_total": len(rows),
            "verify": {"ok": not problems, "problems": problems, "head": prev,
                       "note": "previous-links checked; bundle files " + ("all present" if all(r["bundle_present"] for r in rows) else
                                                                           f"{sum(1 for r in rows if not r['bundle_present'])} not on this desktop")},
            "note": SIMLAB_DESKTOP_NOTE}


def fetch_desktop_simlab(app: server.App):
    return True, read_desktop_simlab(desktop_simlab_path()), None


def h_simlab_desktop(req: Request) -> Response:
    return envelope(req.app.hub.get("ledger.simlab"))


# --- install -----------------------------------------------------------------------------------------------------------------

def install(register) -> None:
    app = server.current_app()
    _state(app)
    hub = app.hub
    hub.register("ledger.aces", lambda: fetch_aces_summary(app), 5.0, ("*",), inline=False, source=str(ledger.path()))
    hub.register("ledger.simlab", lambda: fetch_desktop_simlab(app), 30.0, ("evidence",), inline=False,
                 source=str(desktop_simlab_path()))
    register("GET", "/api/ledger", h_ledger)
    register("GET", "/api/ledger/verify", h_verify)
    register("GET", "/api/ledger/simlab", h_simlab)
    register("GET", "/api/ledger/simlab/desktop", h_simlab_desktop)
