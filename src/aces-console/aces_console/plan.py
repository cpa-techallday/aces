"""The brief-driven Plan view backend (design 2026-10-15 §3, "the plan IS the interface").

    GET  /api/plan/status            planner availability and how it would be reached, plus the launch capability
    POST /api/plan                   {brief, attachments?} -> the planner's plan JSON, normalised, stored, returned
    GET  /api/plan/{plan_id}         a stored plan; 404 when this server has never seen the id
    POST /api/plan/{plan_id}/launch  submit the plan's first runnable stage to the rig via simlab-mcp
                                     (sim_put_spec -> sim_validate -> sim_submit); every other stage answers a
                                     structured reason (blocked, no solver, GPU lane, deferred)

The planner is aces_engineering.playbooks.plan.plan_from_brief (a sibling package that may not be installed in this
environment; a source tree is found beside this package). When the module is not importable the fallback is the CLI
`aces golden plan --json`; when neither exists the answer is 503 planner-unavailable, never an exception and never a
fabricated plan. Plans are kept in this server's memory only (a restart forgets them; the GET says so honestly).

Launch honesty: a stage is submitted only when its solver is registered on the rig, the rig accepts the derived
spec at sim_validate, and sim_submit answers with real job ids; any failure (network, validation, queue) is
returned as the stage's reason, never hidden. CPU-lane stages are submitted with vram_gb 0 (the simlab cpu queue);
a stage whose solver occupies the GPU lane is refused rather than submitted without a VRAM sizing. Subprocess
output and planner exceptions are scrubbed of any configured token value (secrets.scrub, via run_capped).
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path
from secrets import token_hex
from typing import Any, Callable

from . import secrets as tokens, server, sim
from .server import Request, Response, error, ok
from .system import aces_command, run_capped

BRIEF_MAX = 8000
ATTACH_MAX = 32
ATTACH_REF_MAX = 300
PLAN_TIMEOUT_S = 120.0
PLAN_STORE_CAP = 100
LAUNCH_NOTE = ("the first runnable stage is submitted through simlab-mcp (sim_put_spec, sim_validate, sim_submit) "
               "with vram_gb 0 (cpu queue); later stages answer deferred — gates between stages are not wired")


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
               "launch": {"available": bool(sim.mcp_url(app)), "mode": "simlab-mcp sim_submit",
                          "detail": LAUNCH_NOTE if sim.mcp_url(app) else "not configured: server.simlab_mcp is unset"},
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


_EXP_ID_BAD = re.compile(r"[^A-Za-z0-9._-]")


def _exp_id(plan_id: str, stage_id: str) -> str:
    """A simlab-name-safe experiment id joining plan and stage (128 chars max, like aces.simlab.ID_RE)."""
    raw = _EXP_ID_BAD.sub("-", f"{plan_id}-{stage_id}").strip("-.") or "plan-stage"
    return raw[:128]


def _registry_rows(result: Any) -> dict[str, dict[str, Any]]:
    """The solver registry as {name: row}, tolerating the {name: row} mapping and a list of named rows."""
    if isinstance(result, dict):
        rows = result.get("solvers") if isinstance(result.get("solvers"), (dict, list)) else result
    else:
        rows = result
    if isinstance(rows, dict):
        return {str(k): v for k, v in rows.items() if isinstance(v, dict)}
    if isinstance(rows, list):
        return {str(r.get("name")): r for r in rows if isinstance(r, dict) and r.get("name")}
    return {}


def _solver_is_gpu_lane(row: dict[str, Any]) -> bool:
    """True when the registry row puts any stage on the GPU (sim) queue, or names the sim queue outright."""
    stages = row.get("stages")
    if isinstance(stages, dict) and any(str(v) == "sim" for v in stages.values()):
        return True
    return str(row.get("queue") or "") == "sim"


def _stage_outcome(stage: dict[str, Any], launched: bool, code: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"id": stage["id"], "name": stage["name"], "solver": stage.get("solver"),
            "launched": launched, "code": code, "reason": reason, **extra}


def _submit_stage(app: server.App, plan: dict[str, Any], stage: dict[str, Any],
                  rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """One real launch attempt: registry check, spec put, validate, submit. Never raises; every failure is the
    stage's reason. The spec body is JSON (valid YAML) so the console needs no YAML writer."""
    solver = stage.get("solver")
    row = rows.get(str(solver))
    if row is None:
        return _stage_outcome(stage, False, "solver-unregistered",
                              f"solver {solver!r} is not in the rig registry (sim_list_solvers); nothing submitted")
    if _solver_is_gpu_lane(row):
        return _stage_outcome(stage, False, "gpu-lane-unsized",
                              f"solver {solver!r} runs on the GPU lane; the console launch does not size VRAM "
                              "(declare vram_gb from the CLI instead); nothing submitted")
    physics = row.get("physics")
    physics = str(physics[0] if isinstance(physics, list) and physics else physics or "") or "unknown"
    exp_id = _exp_id(plan["plan_id"], stage["id"])
    spec = {"id": exp_id, "project": "aces-console", "owner": "aces-console", "physics": physics,
            "solver": str(solver),
            "description": str(stage.get("purpose") or f"plan {plan['plan_id']} stage {stage['id']}"),
            "params": stage.get("params") or {}, "outputs": stage.get("evidence") or [],
            "resources": {"precision": "float64", "vram_gb": 0.0},
            "tags": {"origin": "aces-console", "plan_id": plan["plan_id"], "stage_id": stage["id"]}}
    ok_, result, err, _info = sim.mcp_call(app, "sim_put_spec",
                                           {"content": json.dumps(spec, indent=2), "name": exp_id + ".yaml"})
    if not ok_:
        return _stage_outcome(stage, False, "put-spec-failed", err or "sim_put_spec failed")
    spec_path = result.get("spec_path") if isinstance(result, dict) else None
    if not spec_path:
        return _stage_outcome(stage, False, "put-spec-failed",
                              f"sim_put_spec answered without a spec_path: {str(result)[:200]}")
    warnings = [str(w) for w in (result.get("warnings") or [])] if isinstance(result, dict) else []
    ok_, result, err, _info = sim.mcp_call(app, "sim_validate", {"spec_path": spec_path})
    if not ok_:
        return _stage_outcome(stage, False, "validate-failed",
                              f"the rig refused the derived spec: {err or 'sim_validate failed'}", spec_path=spec_path)
    ok_, result, err, _info = sim.mcp_call(app, "sim_submit", {"spec_path": spec_path, "docker": True})
    if not ok_:
        return _stage_outcome(stage, False, "submit-failed", err or "sim_submit failed", spec_path=spec_path)
    res = result if isinstance(result, dict) else {}
    return _stage_outcome(stage, True, "submitted",
                          f"queued on the rig as {res.get('experiment') or exp_id}", spec_path=spec_path,
                          experiment=res.get("experiment"), queue=res.get("queue"), jobs=res.get("jobs"),
                          n_cases=res.get("n_cases"), warnings=warnings)


def h_launch(req: Request) -> Response:
    if req.app.fixture:
        return error(503, "fixture mode: a launch would submit to the rig", "fixture")
    st = _state(req.app)
    plan_id = req.params.get("plan_id", "")
    with st.lock:
        plan = st.plans.get(plan_id)
    if plan is None:
        return error(404, f"no plan {plan_id} on this console", "not-found", source="console")
    if not sim.mcp_url(req.app):
        return error(503, "not configured: server.simlab_mcp is unset", "launch-unavailable", source="console")
    stages = [_stage_outcome(s, False, "stage-blocked", f"blocked: {s['gap'] or 'no gap recorded'}")
              if s["blocked"] else
              _stage_outcome(s, False, "no-solver", "the stage names no solver; nothing to submit")
              if not s.get("solver") else None
              for s in plan["stages"]]
    runnable = [s for s, o in zip(plan["stages"], stages) if o is None]
    if not runnable:
        return ok({"plan_id": plan_id, "launched": None, "stages": [o for o in stages if o],
                   "note": "no runnable stage: every stage is blocked or names no solver"}, source="console")
    ok_, result, err, _info = sim.mcp_call(req.app, "sim_list_solvers")
    if not ok_:
        return error(503, f"launch needs the rig solver registry: {err}", "launch-unavailable", source="simlab-mcp")
    rows = _registry_rows(result)
    first, rest = runnable[0], runnable[1:]
    launched = _submit_stage(req.app, plan, first, rows)
    deferred = [_stage_outcome(s, False, "deferred",
                               "one stage per launch; re-launch once this stage's gate is met"
                               if launched["launched"] else "behind the first runnable stage, which did not submit")
                for s in rest]
    by_id = {o["id"]: o for o in [launched, *deferred]}
    outcomes = [by_id.get(s["id"]) or o for s, o in zip(plan["stages"], stages)]
    return ok({"plan_id": plan_id, "launched": launched if launched["launched"] else None,
               "stages": outcomes, "note": LAUNCH_NOTE},
              source="simlab-mcp" if launched["launched"] else "console")


def install(register) -> None:
    app = server.current_app()
    _state(app)
    register("GET", "/api/plan/status", h_status)
    register("POST", "/api/plan", h_create)
    register("GET", "/api/plan/{plan_id}", h_get)
    register("POST", "/api/plan/{plan_id}/launch", h_launch)
