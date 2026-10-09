"""Operator policy: mode (trial or distribution), default project class, overrides.

The audited licence rules are the distribution policy for anything ACES ships to a customer. On the operator's own
installation the policy is advisory: in `trial` mode every catalog component is installable, and an override records
the operator, the component and the reason in the ledger. Terms are re-read at the end-use gate, when a project
changes class (see project.classify).
"""
from __future__ import annotations

from typing import Any

from . import config, ledger

DEFAULT_POLICY: dict[str, Any] = {
    "mode": "trial",
    "default_project_class": "personal",
    "classes": ["personal", "open", "commercial", "sovereign", "controlled"],
    "end_use_gate": (
        "No component is restricted while a project is personal, education or research use with no contract, "
        "customer, paid deliverable or defence end use. When a project changes class, the terms of every component "
        "recorded against its runs are re-read at that moment."
    ),
}

MODES = ("trial", "distribution")


def load() -> dict[str, Any]:
    pol = dict(DEFAULT_POLICY)
    pol.update(config.read_json(config.ETC / "policy.json", {}) or {})
    pol.update(config.read_json(config.USER_CONFIG / "policy.json", {}) or {})
    pol["overrides"] = overrides()
    return pol


def set_mode(mode: str, reason: str) -> dict[str, Any]:
    if mode not in MODES:
        raise SystemExit(f"aces: mode must be one of {', '.join(MODES)}")
    user = config.read_json(config.USER_CONFIG / "policy.json", {}) or {}
    previous = load().get("mode")
    user["mode"] = mode
    config.write_json(config.USER_CONFIG / "policy.json", user)
    return ledger.append("policy.mode", {"from": previous, "to": mode, "reason": reason})


def override(component: str, reason: str, terms: str | None = None) -> dict[str, Any]:
    if not reason.strip():
        raise SystemExit("aces: an override needs a --reason")
    return ledger.append("policy.override", {"component": component, "reason": reason, "terms": terms or ""})


def overrides() -> list[dict[str, Any]]:
    return [e for e in ledger.entries() if e.get("kind") == "policy.override"]
