"""Planner: brief + classification + playbook -> a concrete plan JSON (design doc layer 3).

`plan_from_brief` classifies the brief, loads the class playbook, extracts parameters from
the brief text (loads, materials, envelope dims, constraints — numbers with units, parsed by
regex), and resolves every stage: its solver reference is classified against the simlab
registry vocabulary (available / incoming / missing) and stages whose solver is missing are
named in `blocked` — never silently skipped.

Parameter extraction is conservative: only values explicitly in the brief are extracted;
anything unknown stays at the playbook's `spec.defaults` with its note. Nothing is invented.
"""
from __future__ import annotations

import hashlib
import re

from . import PLAN_SCHEMA, SOLVER_MISSING, solver_status
from .classify import classify
from .library import load

# ---- unit parsing --------------------------------------------------------------------------------------------------
_NUM = r"(\d+(?:[.,]\d+)?)"
_NUM_RE = re.compile(_NUM + r"\s*([a-zA-Zµ°/]+)")

# unit -> (canonical key, multiplier to the canonical unit)
UNITS: dict[str, tuple[str, float]] = {
    "kg": ("mass_kg", 1.0), "g": ("mass_kg", 1e-3), "t": ("mass_kg", 1e3),
    "lb": ("mass_kg", 0.45359237), "lbs": ("mass_kg", 0.45359237),
    "kn": ("force_N", 1e3), "n": ("force_N", 1.0),
    "mpa": ("pressure_Pa", 1e6), "gpa": ("pressure_Pa", 1e9), "kpa": ("pressure_Pa", 1e3),
    "pa": ("pressure_Pa", 1.0), "bar": ("pressure_Pa", 1e5), "psi": ("pressure_Pa", 6894.757),
    "kw": ("power_W", 1e3), "mw": ("power_W", 1e6), "w": ("power_W", 1.0),
    "kwh": ("energy_Wh", 1e3), "mwh": ("energy_Wh", 1e6), "wh": ("energy_Wh", 1.0),
    "ghz": ("frequency_Hz", 1e9), "mhz": ("frequency_Hz", 1e6), "khz": ("frequency_Hz", 1e3),
    "hz": ("frequency_Hz", 1.0),
    "rpm": ("speed_rpm", 1.0),
    "mm": ("length_mm", 1.0), "cm": ("length_mm", 10.0), "m": ("length_mm", 1e3),
}
_AMBIGUOUS = {"g", "m", "n", "t", "w", "pa"}  # single letters only count directly before/after a number context

# envelope: three lengths joined by x/×, units per dimension or inherited from the next one
# ("1200 x 800 x 400 mm" and "0.4 m x 30 cm x 200 mm" both parse)
_DIM = _NUM + r"\s*(mm|cm|m)?"
_ENVELOPE_RE = re.compile(_DIM + r"\s*[x×]\s*" + _DIM + r"\s*[x×]\s*" + _DIM, re.IGNORECASE)

MATERIALS_RE = re.compile(
    r"\b(steel|mild steel|stainless|aluminium|aluminum|al 6061|6061|7075|titanium|ti-6al-4v|"
    r"carbon[ -]fib(?:er|re)|cfrp|copper|inconel|magnesium|brass|pla|abs|peek|nylon)\b",
    re.IGNORECASE)

_WHEELED_RE = re.compile(r"\b(\w+)[- ]wheeled\b|\b(\d+)\s*wheels?\b", re.IGNORECASE)
_NUMWORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
             "nine": 9, "ten": 10, "twelve": 12}

# canonical extracted key -> playbook spec.targets keys it may fill (beyond an exact match)
TARGET_ALIASES: dict[str, tuple[str, ...]] = {
    "mass_kg": ("mass_kg",),
    "power_W": ("heat_load_W",),
    "force_N": ("thrust_N", "load_N"),
    "pressure_Pa": ("chamber_pressure_Pa", "pressure_drop_Pa"),
    "frequency_Hz": ("frequency_Hz",),
    "speed_rpm": ("speed_rpm",),
    "length_mm": (),
    "energy_Wh": (),
}


def extract_parameters(brief: str) -> dict:
    """Regex extraction of numbers+units, envelope dims, materials and wheel counts.

    Returns {key: {"value": ..., "unit": ..., "source": <the matched text>}} plus
    `envelope_mm` (a 3-list), `materials` (a list) and `notes`. Unknowns are absent —
    the caller falls back to the playbook's defaults and says so.
    """
    text = brief or ""
    out: dict[str, dict] = {}

    m = _ENVELOPE_RE.search(text)
    if m:
        nums = [float(m.group(i).replace(",", ".")) for i in (1, 3, 5)]
        units = [m.group(i).lower() if m.group(i) else None for i in (2, 4, 6)]
        # a dimension without its own unit inherits the next stated one (1200 x 800 x 400 mm)
        for i in (1, 0):
            units[i] = units[i] or units[i + 1]
        if units[0] is not None:
            out["envelope_mm"] = {"value": [v * UNITS[u][1] for v, u in zip(nums, units)],
                                  "unit": "mm", "source": m.group(0)}

    for match in _NUM_RE.finditer(text):
        raw, unit = match.group(1), match.group(2).lower()
        if unit not in UNITS or (unit in _AMBIGUOUS and not _plausible(raw, unit)):
            continue
        key, factor = UNITS[unit]
        value = float(raw.replace(",", ".")) * factor
        if key in out:  # keep the first occurrence of each kind
            continue
        out[key] = {"value": value, "unit": unit, "source": match.group(0).strip()}

    materials = [m.group(0).lower() for m in MATERIALS_RE.finditer(text)]
    if materials:
        out["materials"] = {"value": sorted(set(materials)), "unit": None, "source": ", ".join(sorted(set(materials)))}

    w = _WHEELED_RE.search(text)
    if w:
        word, digits = w.group(1), w.group(2)
        count = _NUMWORDS.get((word or "").lower()) if word else None
        if count is None and digits:
            count = int(digits)
        if count:
            out["wheels"] = {"value": count, "unit": None, "source": w.group(0)}
    return out


def _plausible(raw: str, unit: str) -> bool:
    """Single-letter units only count when the value is physically plausible, so 'a 50 m
    cable' in prose about metres is fine but '50 m' meaning minutes is not our problem here:
    conservative, first-match-wins."""
    try:
        v = float(raw.replace(",", "."))
    except ValueError:
        return False
    if unit == "g":
        return v >= 10  # grams only from 10 g up; smaller is likely 'g' for grams in chemistry prose we do not serve
    return True


def _resolve_targets(playbook: dict, params: dict) -> tuple[dict, list[str]]:
    """Overlay extracted parameters onto the playbook's spec.targets; returns (targets, notes)."""
    spec = playbook.get("spec") or {}
    targets = dict(spec.get("targets") or {})
    notes: list[str] = []
    for key, target_keys in TARGET_ALIASES.items():
        if key not in params:
            continue
        value = params[key]["value"]
        for tk in (key, *target_keys):
            if tk in targets and targets[tk] is None:
                targets[tk] = value
                notes.append(f"target {tk} = {value:g} (from the brief: '{params[key]['source']}')")
    if "materials" in params and "material" in (spec.get("defaults") or {}):
        notes.append(f"material from the brief: {params['materials']['source']} "
                     f"(replaces the default {spec['defaults']['material']['value']})")
    for tk, val in targets.items():
        if val is None:
            notes.append(f"target {tk} not in the brief; left open (spec.defaults apply)")
    return targets, notes


def plan_from_brief(brief: str, attachments: list[str] | None = None) -> dict:
    """The plan JSON for a brief (and optional uploaded files).

    Keys: schema, plan_id, brief, attachments, class, confidence, low_confidence,
    class_evidence, parameters, spec {metrics, targets, defaults}, stages (resolved),
    blocked, warnings. JSON-serialisable; deterministic for a given brief and library.
    """
    result = classify(brief)
    playbook = load(result.cls)
    params = extract_parameters(brief)
    targets, notes = _resolve_targets(playbook, params)
    spec = playbook.get("spec") or {}

    stages, blocked = [], []
    for st in playbook.get("stages") or []:
        status = solver_status(str(st.get("solver")))
        resolved = {
            "id": st.get("id"),
            "name": st.get("name"),
            "solver": st.get("solver"),
            "solver_status": status,
            "purpose": st.get("purpose"),
            "inputs": st.get("inputs") or [],
            "outputs": st.get("outputs") or [],
            "entry": st.get("entry") or [],
            "exit": st.get("exit") or [],
            "gate": st.get("gate") or {},
            "evidence": st.get("evidence") or [],
            "pending": bool(st.get("pending")),
            "parameters": {k: v["value"] for k, v in params.items()},
        }
        stages.append(resolved)
        if status == SOLVER_MISSING:
            blocked.append({"stage": st.get("id"), "solver": st.get("solver"),
                            "reason": "solver missing: neither available nor incoming in the simlab registry"})

    warnings = list(notes)
    if result.low_confidence:
        warnings.insert(0, f"low confidence ({result.confidence}) in class '{result.cls}': "
                           "confirm the class before launch")
    incoming = sorted({s["solver"] for s in stages if s["solver_status"] == "incoming"})
    if incoming:
        warnings.append("stages reference incoming solver rows (landing this cycle): " + ", ".join(incoming))
    for b in blocked:
        warnings.append(f"stage '{b['stage']}' is blocked: solver '{b['solver']}' is missing from the registry")

    plan_id = "plan-{}-{}".format(result.cls, hashlib.sha256((brief or "").encode("utf-8")).hexdigest()[:8])
    return {
        "schema": PLAN_SCHEMA,
        "plan_id": plan_id,
        "brief": brief,
        "attachments": list(attachments or []),
        "class": result.cls,
        "confidence": result.confidence,
        "low_confidence": result.low_confidence,
        "class_evidence": result.evidence,
        "parameters": params,
        "spec": {"metrics": list(spec.get("metrics") or []), "targets": targets,
                 "defaults": spec.get("defaults") or {}},
        "stages": stages,
        "blocked": blocked,
        "warnings": warnings,
    }
