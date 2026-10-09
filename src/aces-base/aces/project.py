"""Projects: ~/Work/<name>/aces.yaml and the end-use gate.

A project starts in the policy's default class (personal). Reclassifying it is the end-use gate: the change is
recorded in the ledger with the reason, and the components the project used (as recorded by tools that write to
aces.yaml's `components` list) are listed so their terms can be re-read. Tools also record the project's evidence
references (`evidence`, e.g. aces golden runs with their bundle hash) and its CAD sources (`cad_sources`).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config, ledger, policy

FILENAME = "aces.yaml"


def path(name: str) -> Path:
    return config.WORK / name / FILENAME


def _dump(data: dict[str, Any]) -> str:
    """Minimal YAML writer for flat keys, lists of scalars and one level of mapping."""
    lines = []
    for k, v in data.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            if not v:
                lines[-1] += " []"
            for item in v:
                lines.append(f"  - {_scalar(item)}")
        elif isinstance(v, dict):
            lines.append(f"{k}:")
            for kk, vv in v.items():
                lines.append(f"  {kk}: {_scalar(vv)}")
        else:
            lines.append(f"{k}: {_scalar(v)}")
    return "\n".join(lines) + "\n"


def _scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if s == "" or any(c in s for c in ":#{}[],&*!|>'\"%@`") or s.strip() != s:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _load(p: Path) -> dict[str, Any]:
    """Minimal YAML reader for the subset _dump writes."""
    data: dict[str, Any] = {}
    cur_key = None
    cur_list: list | None = None
    cur_map: dict | None = None
    for raw in p.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("  - ") and cur_key is not None:
            if cur_list is None:
                cur_list = []
                data[cur_key] = cur_list
            cur_list.append(_unscalar(raw[4:]))
            continue
        if raw.startswith("  ") and ":" in raw and cur_key is not None:
            if cur_map is None:
                cur_map = {}
                data[cur_key] = cur_map
            k, _, v = raw.strip().partition(":")
            cur_map[k.strip()] = _unscalar(v.strip())
            continue
        k, _, v = raw.partition(":")
        cur_key = k.strip()
        cur_list = None
        cur_map = None
        v = v.strip()
        if v == "[]":
            data[cur_key] = []
        elif v == "":
            data[cur_key] = None
        else:
            data[cur_key] = _unscalar(v)
    return data


def _unscalar(s: str) -> Any:
    if s == "null":
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s


def new(name: str, cls: str | None, company: str | None, rd_activity: str | None, hypothesis: str | None) -> Path:
    pol = policy.load()
    cls = cls or pol.get("default_project_class", "personal")
    if cls not in pol.get("classes", []):
        raise SystemExit(f"aces: class must be one of {', '.join(pol.get('classes', []))}")
    p = path(name)
    if p.exists():
        raise SystemExit(f"aces: {p} already exists")
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "name": name,
        "class": cls,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "company": company or "",
        "rd_activity": rd_activity or "",
        "hypothesis": hypothesis or "",
        "cad_sources": [],
        "components": [],
        "evidence": [],
    }
    p.write_text(_dump(data), encoding="utf-8")
    ledger.append("project.new", {"name": name, "class": cls, "path": str(p)})
    return p


def show(name: str) -> dict[str, Any]:
    p = path(name)
    if not p.exists():
        raise SystemExit(f"aces: no project at {p}")
    return _load(p)


def classify(name: str, cls: str, reason: str) -> dict[str, Any]:
    pol = policy.load()
    if cls not in pol.get("classes", []):
        raise SystemExit(f"aces: class must be one of {', '.join(pol.get('classes', []))}")
    if not reason.strip():
        raise SystemExit("aces: reclassifying a project needs a --reason (this is the end-use gate)")
    p = path(name)
    data = show(name)
    previous = data.get("class")
    data["class"] = cls
    data["reclassified"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    p.write_text(_dump(data), encoding="utf-8")
    entry = ledger.append("project.reclassify", {
        "name": name, "from": previous, "to": cls, "reason": reason,
        "components_to_reread": list(data.get("components") or []),
    })
    return entry


def _append_list(name: str, key: str, value: str) -> bool:
    """Add `value` to the list `key` of the project's aces.yaml unless it is already there (idempotent).
    Returns True when the value was added."""
    p = path(name)
    data = show(name)
    items = list(data.get(key) or [])
    if value in items:
        return False
    items.append(value)
    data[key] = items
    p.write_text(_dump(data), encoding="utf-8", newline="\n")
    return True


def add_component(name: str, component: str) -> bool:
    """Record a solver, model or tool the project used (the end-use gate lists these)."""
    return _append_list(name, "components", component)


def add_evidence(name: str, ref: str) -> bool:
    """Record an evidence reference, e.g. `golden/<run-id> bundle:<sha16> PASS` written by aces golden."""
    return _append_list(name, "evidence", ref)


def add_cad_source(name: str, ref: str) -> bool:
    """Record a CAD source of the project, e.g. `cad/part.step` (relative to the project directory)."""
    return _append_list(name, "cad_sources", ref)


def list_projects() -> list[Path]:
    if not config.WORK.exists():
        return []
    return sorted(d / FILENAME for d in config.WORK.iterdir() if (d / FILENAME).exists())
