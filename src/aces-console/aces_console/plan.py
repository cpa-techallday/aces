"""The brief-driven Plan view backend (design 2026-10-15 §3, "the plan IS the interface").

    GET  /api/plan/status            planner availability and how it would be reached, plus the launch capability
    POST /api/plan                   {brief, attachments?} -> the planner's plan JSON, normalised, stored, returned
    GET  /api/plan/{plan_id}         a stored plan; 404 when this server has never seen the id
    POST /api/plan/{plan_id}/launch  an honest refusal while the console has no job-submit path (spec 1 defers submit)

The planner is aces_engineering.playbooks.plan.plan_from_brief (a sibling package that may not be installed in this
environment; a source tree is found beside this package). When the module is not importable the fallback is the CLI
`aces golden plan --json`; when neither exists the answer is 503 planner-unavailable, never an exception and never a
fabricated plan. Plans are kept in this server's memory only (a restart forgets them; the GET says so honestly).

Subprocess output and planner exceptions are scrubbed of any configured token value (secrets.scrub, via run_capped).
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from secrets import token_hex
from typing import Any, Callable

from . import secrets as tokens, server
from .server import Request, Response, error, ok
from .system import aces_command, run_capped

BRIEF_MAX = 8000
ATTACH_MAX = 32
ATTACH_REF_MAX = 300
PLAN_TIMEOUT_S = 120.0
PLAN_STORE_CAP = 100
LAUNCH_REFUSAL = ("job submit is deferred in this console (spec 1): the console has no submit path to the queue, "
                  "so a plan cannot be launched from here; run the plan from the CLI (aces golden run) instead")


class PlanState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.plans: dict[str, dict[str, Any]] = {}
        self.order: list[str] = []          # oldest first, for the cap


def _state(app: server.App) -> PlanState:
    st = app.state.get("plan")
    if st is None:
        st = app.state["plan"] = PlanState()
    return st


# --- the loose planner boundary -----------------------------------------------------------------------------------------------


def _import_planner() -> tuple[Callable | None, str | None]:
    """(plan_from_brief, None) when aces_engineering.playbooks.plan imports, else (None, why). A source tree's
    sibling src/aces-engineering/lib is put on sys.path for the attempt; an installed package needs nothing."""
    try:
        from aces_engineering.playbooks.plan import plan_from_brief
        return plan_from_brief, None
    except ImportError as first:
        reason = getattr(first, "name", None) or str(first)
    lib = Path(__file__).resolve().parents[2] / "aces-engineering" / "lib"
    if lib.is_dir() and (lib / "aces_engineering").is_dir() and str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
        try:
            from aces_engineering.playbooks.plan import plan_from_brief
            return plan_from_brief, None
        except ImportError as second:
            reason = getattr(second, "name", None) or str(second)
    return None, f"aces_engineering.playbooks.plan is not importable ({reason})"


def _cli_planner(brief: str, attachments: list[str]) -> tuple[dict[str, Any] | None, str | None]:
    """(plan dict, None) from `aces golden plan --json`, else (None, why)."""
    args = ["golden", "plan", "--json", "--brief", brief]
    for ref in attachments:
        args += ["--attach", ref]
    cmd = aces_command(args)
    if cmd is None:
        return None, "aces CLI not found (aces-base is not installed and the aces package is not importable)"
    res = run_capped(cmd, PLAN_TIMEOUT_S)
    if res["timed_out"]:
        return None, f"aces golden plan timed out after {PLAN_TIMEOUT_S:g} s"
    if res["exit"] != 0:
        tail = (res["stderr_tail"].strip() or res["stdout"].strip())[:300]
        return None, f"aces golden plan failed (exit {res['exit']}): {tail or 'no output'}"
    doc = _parse_json_document(res["stdout"])
    if not isinstance(doc, dict):
        return None, "aces golden plan answered no JSON plan"
    return doc, None


def _parse_json_document(text: str) -> Any:
    text = (text or "").strip()
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


def run_planner(brief: str, attachments: list[str]) -> tuple[dict[str, Any] | None, str | None, str | None, str | None]:
    """(plan, mode, None, None) on success; (None, mode|None, code, why) otherwise. mode is "module" or "cli".

    Never raises: the planner is a sibling under construction and this console degrades instead of failing."""
    fn, why_import = _import_planner()
    if fn is not None:
        try:
            doc = fn(brief, attachments=attachments or None)
        except TypeError:                     # an early planner without the attachments keyword
            try:
                doc = fn(brief)
            except Exception as e:  # noqa: BLE001
                return None, "module", "planner-failed", f"the planner raised {type(e).__name__}: {tokens.scrub(str(e))[:300]}"
            else:
                return _coerce(doc, "module")
        except Exception as e:  # noqa: BLE001
            return None, "module", "planner-failed", f"the planner raised {type(e).__name__}: {tokens.scrub(str(e))[:300]}"
        return _coerce(doc, "module")
    doc, why_cli = _cli_planner(brief, attachments)
    if doc is not None:
        return _coerce(doc, "cli")
    return None, None, "planner-unavailable", (f"the planner is not available from this console: {why_import}; "
                                               f"CLI fallback: {why_cli}")


def _coerce(doc: Any, mode: str) -> tuple[dict[str, Any] | None, str, str | None, str | None]:
    if not isinstance(doc, dict):
        return None, mode, "planner-failed", f"the planner returned {type(doc).__name__}, not a plan object"
    return doc, mode, None, None


# --- normalisation: the UI contract is stable even when the planner's answer is partial -----------------------------------------


def _norm_stage(raw: Any, i: int) -> dict[str, Any]:
    s = raw if isinstance(raw, dict) else {"name": str(raw)}
    ev = s.get("evidence")
    return {"id": str(s.get("id") or f"stage-{i + 1}"), "name": str(s.get("name") or s.get("id") or f"stage {i + 1}"),
            "solver": s.get("solver") if s.get("solver") is None or isinstance(s.get("solver"), str) else str(s.get("solver")),
            "purpose": s.get("purpose"), "params": s.get("params") if isinstance(s.get("params"), dict) else {},
            "gate": s.get("gate"), "evidence": ev if isinstance(ev, list) else ([] if ev is None else [ev]),
            "blocked": bool(s.get("blocked")), "gap": s.get("gap")}


def _norm_metric(raw: Any, i: int) -> dict[str, Any]:
    m = raw if isinstance(raw, dict) else {"name": str(raw)}
    return {"name": str(m.get("name") or f"metric-{i + 1}"), "value": m.get("value"), "unit": m.get("unit"),
            "target": m.get("target"), "note": m.get("note")}


def normalize_plan(doc: dict[str, Any], brief: str, attachments: list[str], mode: str) -> dict[str, Any]:
    """The plan the console stores and serves: every key of the contract present, blocked stages marked on the stage
    cards as well as in the top-level list, and the request that produced it recorded."""
    stages = [_norm_stage(s, i) for i, s in enumerate(doc.get("stages") if isinstance(doc.get("stages"), list) else [])]
    blocked = doc.get("blocked") if isinstance(doc.get("blocked"), list) else []
    warnings = [str(w) for w in (doc.get("warnings") if isinstance(doc.get("warnings"), list) else [])]
    for entry in blocked:
        if isinstance(entry, dict):
            name = str(entry.get("stage") or entry.get("id") or entry.get("name") or "")
            gap = entry.get("gap") or entry.get("reason")
        else:
            name, gap = str(entry), None
        for s in stages:
            if name and name in (s["id"], s["name"]):
                s["blocked"] = True
                if gap and not s["gap"]:
                    s["gap"] = str(gap)
    spec_raw = doc.get("spec") if isinstance(doc.get("spec"), dict) else {}
    spec = {"metrics": [_norm_metric(m, i) for i, m in
                        enumerate(spec_raw.get("metrics") if isinstance(spec_raw.get("metrics"), list) else [])],
            "targets": spec_raw.get("targets") if isinstance(spec_raw.get("targets"), dict) else {}}
    confidence = doc.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        confidence = None
    plan_id = str(doc.get("plan_id") or "")
    if not plan_id:
        plan_id = f"plan-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{token_hex(2)}"
    return {"plan_id": plan_id, "class": doc.get("class") if doc.get("class") is None else str(doc.get("class")),
            "confidence": confidence, "low_confidence": bool(doc.get("low_confidence")),
            "class_evidence": doc.get("class_evidence") if isinstance(doc.get("class_evidence"), list) else [],
            "stages": stages, "spec": spec, "blocked": blocked, "warnings": warnings,
            "brief": brief, "attachments": attachments, "planner": mode, "created_utc": server.now_utc()}


# --- the handlers ---------------------------------------------------------------------------------------------------------------


def _cli_probe() -> tuple[bool, str]:
    """(verb exists, detail): `aces golden plan --help` is actually run (10 s cap), because an `aces` CLI without the
    golden plan verb is not a planner."""
    cmd = aces_command(["golden", "plan", "--help"])
    if cmd is None:
        return False, "aces CLI not found (aces-base is not installed and the aces package is not importable)"
    res = run_capped(cmd, 10.0)
    if res["exit"] == 0:
        return True, "aces golden plan --help answered"
    if res["timed_out"]:
        return False, "aces golden plan --help timed out"
    return False, f"aces golden plan --help exited {res['exit']}: the verb is not in this CLI"


def h_status(req: Request) -> Response:
    app = req.app
    st = _state(app)
    fn, why_import = _import_planner()
    if fn is not None:
        available, mode, detail = True, "module", "aces_engineering.playbooks.plan.plan_from_brief"
    else:
        cli_ok, cli_detail = _cli_probe()
        if cli_ok:
            available, mode, detail = True, "cli", f"aces golden plan (the module is not importable: {why_import})"
        else:
            available, mode, detail = False, None, (f"the planner module is not importable ({why_import}); "
                                                    f"CLI fallback: {cli_detail}")
    with st.lock:
        n = len(st.plans)
    return ok({"planner": {"available": available, "mode": mode, "detail": detail},
               "launch": {"available": False, "reason": LAUNCH_REFUSAL},
               "plans_stored": n, "storage": "in memory; a console restart forgets them"},
              source="console")


def h_create(req: Request) -> Response:
    app = req.app
    if app.fixture:
        return error(503, "fixture mode: the planner is not run while fixtures are replayed", "fixture")
    body = req.json if isinstance(req.json, dict) else {}
    brief = body.get("brief")
    if not isinstance(brief, str) or not brief.strip():
        return error(400, "brief must be a non-empty string (describe the part or the mission)", "bad-request")
    brief = brief.strip()
    if len(brief) > BRIEF_MAX:
        return error(400, f"brief is longer than {BRIEF_MAX} characters", "bad-request")
    attachments = body.get("attachments")
    if attachments is None:
        attachments = []
    if (not isinstance(attachments, list) or len(attachments) > ATTACH_MAX
            or any(not isinstance(a, str) or not a.strip() or len(a) > ATTACH_REF_MAX for a in attachments)):
        return error(400, f"attachments must be a list of at most {ATTACH_MAX} reference strings "
                          f"(each at most {ATTACH_REF_MAX} characters)", "bad-request")
    attachments = [a.strip() for a in attachments]
    doc, mode, code, why = run_planner(brief, attachments)
    if doc is None:
        status = 503 if code == "planner-unavailable" else 502
        return error(status, why or "the planner produced no plan", code or "planner-failed", source="planner")
    plan = normalize_plan(doc, brief, attachments, mode or "module")
    st = _state(app)
    with st.lock:
        st.plans[plan["plan_id"]] = plan
        st.order.append(plan["plan_id"])
        while len(st.order) > PLAN_STORE_CAP:
            st.plans.pop(st.order.pop(0), None)
    return ok(plan, source=f"planner ({mode})")


def h_get(req: Request) -> Response:
    st = _state(req.app)
    plan_id = req.params.get("plan_id", "")
    with st.lock:
        plan = st.plans.get(plan_id)
    if plan is None:
        return error(404, f"no plan {plan_id} on this console (plans are kept in memory; a restart forgets them)",
                     "not-found", source="console")
    return ok(plan, source="console")


def h_launch(req: Request) -> Response:
    if req.app.fixture:
        return error(503, "fixture mode: a launch would submit to the rig", "fixture")
    st = _state(req.app)
    plan_id = req.params.get("plan_id", "")
    with st.lock:
        known = plan_id in st.plans
    if not known:
        return error(404, f"no plan {plan_id} on this console", "not-found", source="console")
    return error(503, LAUNCH_REFUSAL, "launch-unavailable", source="console")


def install(register) -> None:
    app = server.current_app()
    _state(app)
    register("GET", "/api/plan/status", h_status)
    register("POST", "/api/plan", h_create)
    register("GET", "/api/plan/{plan_id}", h_get)
    register("POST", "/api/plan/{plan_id}/launch", h_launch)
