"""Playbook validation: schema, solver references, and gate satisfiability.

`validate_playbook` checks one loaded playbook document and returns a list of problems
(strings); an empty list means valid. `validate_all` checks every shipped playbook. Solver
references are classified against the registry vocabulary (`solver_status`): `missing` is an
error, `incoming` is reported separately as a note (the row is being landed this cycle, so
the playbook is valid but its stage will run only when the row exists).

Gate satisfiability: a gate is satisfiable when its metric is produced by the stage's own
outputs or is a class metric, its operator is a known comparison, and its limit is numeric.
"""
from __future__ import annotations

from . import CLASSES, SCHEMA, SOLVER_INCOMING, SOLVER_MISSING, solver_status
from .library import load_all

GATE_OPS = ("<=", ">=", "<", ">", "==", "!=")
STAGE_KEYS = ("id", "name", "solver", "purpose", "inputs", "outputs", "entry", "exit", "gate", "evidence")


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_gate(stage: dict, metrics: list[str]) -> list[str]:
    """Gate satisfiability for one stage: named metric, known operator, numeric limit."""
    problems = []
    sid = stage.get("id", "?")
    gate = stage.get("gate")
    if not isinstance(gate, dict):
        return [f"stage {sid}: gate must be a mapping"]
    metric, op, limit = gate.get("metric"), gate.get("op"), gate.get("limit")
    if not metric or not isinstance(metric, str):
        problems.append(f"stage {sid}: gate.metric missing")
    elif metric not in (stage.get("outputs") or []) and metric not in metrics:
        problems.append(f"stage {sid}: gate metric {metric!r} is not an output of the stage nor a class metric")
    if op not in GATE_OPS:
        problems.append(f"stage {sid}: gate.op must be one of {GATE_OPS}, got {op!r}")
    if not _is_number(limit):
        problems.append(f"stage {sid}: gate.limit must be numeric, got {limit!r}")
    return problems


def validate_playbook(pb: dict, name: str = "?") -> tuple[list[str], list[str]]:
    """(errors, notes) for one playbook document."""
    errors: list[str] = []
    notes: list[str] = []
    if pb.get("schema") != SCHEMA:
        errors.append(f"{name}: schema must be {SCHEMA!r}")
    cls = pb.get("class")
    if cls not in CLASSES:
        errors.append(f"{name}: class must be one of {CLASSES}, got {cls!r}")
    if not _is_number(pb.get("version")):
        errors.append(f"{name}: version missing or not numeric")
    spec = pb.get("spec")
    if not isinstance(spec, dict) or not spec.get("metrics"):
        errors.append(f"{name}: spec.metrics missing")
    metrics = list((spec or {}).get("metrics") or [])

    stages = pb.get("stages")
    if not isinstance(stages, list) or not stages:
        errors.append(f"{name}: stages missing or empty")
        return errors, notes
    ids = [s.get("id") for s in stages if isinstance(s, dict)]
    if len(ids) != len(set(ids)):
        errors.append(f"{name}: duplicate stage ids")
    if stages and isinstance(stages[-1], dict) and stages[-1].get("id") != "score":
        errors.append(f"{name}: the last stage must be 'score' (every playbook ends in scoring)")
    for i, st in enumerate(stages):
        sid = st.get("id", f"#{i}") if isinstance(st, dict) else f"#{i}"
        if not isinstance(st, dict):
            errors.append(f"{name}: stage {sid} is not a mapping")
            continue
        for key in STAGE_KEYS:
            if key not in st:
                errors.append(f"{name}: stage {sid}: missing key {key!r}")
        solver = st.get("solver")
        if not solver or not isinstance(solver, str):
            errors.append(f"{name}: stage {sid}: solver must be a registry row name or 'none'")
        else:
            status = solver_status(solver)
            if status == SOLVER_MISSING:
                if st.get("pending") is True:
                    # a declared-pending stage (e.g. PeleC before its image lands): plans mark
                    # it blocked; the playbook is valid but the gap is named, never skipped.
                    notes.append(f"{name}: stage {sid}: solver {solver!r} is pending (declared; plans mark it blocked)")
                else:
                    errors.append(f"{name}: stage {sid}: solver {solver!r} is missing from the registry vocabulary")
            elif status == SOLVER_INCOMING:
                notes.append(f"{name}: stage {sid}: solver {solver!r} is incoming (not yet in the registry)")
        for key in ("inputs", "outputs", "entry", "exit", "evidence"):
            if key in st and not isinstance(st[key], list):
                errors.append(f"{name}: stage {sid}: {key} must be a list")
        errors.extend(f"{name}: {p}" for p in validate_gate(st, metrics))
    return errors, notes


def validate_all() -> dict[str, dict]:
    """Validate every shipped playbook: {class: {"errors": [...], "notes": [...]}}."""
    return {cls: dict(zip(("errors", "notes"), validate_playbook(pb, name=f"{cls}.yaml")))
            for cls, pb in load_all().items()}


def ok(report: dict[str, dict]) -> bool:
    """True when no playbook has errors (notes do not fail validation)."""
    return all(not r["errors"] for r in report.values())
