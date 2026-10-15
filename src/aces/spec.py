"""Design specifications: the single input to every ACES design run.

A spec file is YAML with a common envelope and a domain-specific ``requirements``
block.  Validation failures are reported as a plain list of what is missing or
wrong (field path, expected type, units where known), never as a stack trace, so an
empty template tells the user exactly what to fill in.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

COMPANY = "CPA"  # Carbon Project Australia Pty Ltd owns ACES and its R&D records (decision D2).


class SearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trials: int = Field(200, ge=8, le=100_000)
    seed: int = 0
    population: int = Field(40, ge=8, le=2_000)
    bounds: dict[str, tuple[float, float]] = Field(default_factory=dict)  # overrides of a model's default bounds


class SpecEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    aces: Literal[1] = 1
    domain: str
    project: str
    owner: str
    company: str = COMPANY
    rd_activity: str  # RDTI core R&D activity this run belongs to
    hypothesis: str  # what the run is expected to show (RDTI: hypothesis -> experiment -> observation)
    requirements: dict[str, Any]
    search: SearchConfig = Field(default_factory=SearchConfig)
    notes: str = ""

    @field_validator("company")
    @classmethod
    def _company_is_cpa(cls, v: str) -> str:
        if v != COMPANY:
            raise ValueError(f"ACES R&D records belong to {COMPANY} (Carbon Project Australia); got {v!r}")
        return v


class SpecError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("spec is incomplete or invalid:\n  - " + "\n  - ".join(problems))


def _format(err: ValidationError, prefix: str = "") -> list[str]:
    out = []
    for e in err.errors():
        loc = ".".join(str(p) for p in e["loc"])
        path = f"{prefix}{loc}" if loc else prefix.rstrip(".")
        if e["type"] in ("missing", "none_required") or (e["type"].endswith("_type") and e.get("input") is None):
            out.append(f"{path}: missing")
        else:
            out.append(f"{path}: {e['msg']}")
    return out


def parse(data: dict[str, Any], requirements_model: type[BaseModel] | None = None) -> tuple[SpecEnvelope, BaseModel | None]:
    problems: list[str] = []
    env = None
    try:
        env = SpecEnvelope.model_validate(data)
    except ValidationError as e:
        problems += _format(e)
    req = None
    if requirements_model is not None:
        try:
            req = requirements_model.model_validate((data or {}).get("requirements") or {})
        except ValidationError as e:
            problems += _format(e, "requirements.")
    if problems:
        raise SpecError(sorted(set(problems)))
    return env, req


def load(path: str | Path, requirements_model: type[BaseModel] | None = None):
    data = yaml.safe_load(Path(path).read_text()) or {}
    return parse(data, requirements_model)


def canonical_hash(obj: Any) -> str:
    """sha256 of the canonical JSON form (sorted keys, no whitespace)."""
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(blob).hexdigest()
