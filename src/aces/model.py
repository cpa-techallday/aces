"""The DesignModel contract every ACES domain implements.

A design model is encoded engineering logic: given validated requirements it
defines a parameter space, turns a parameter set into a design, and evaluates
that design on the fast physics tier.  Objectives are minimised; constraints are
feasible when <= 0.  Anything a model cannot vouch for goes into ``warnings`` and
``uncalibrated`` so it reaches the report and the evidence record.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


@dataclass
class Evaluation:
    params: dict[str, Any]
    objectives: dict[str, float]  # minimised
    constraints: dict[str, float]  # feasible when every value <= 0
    metrics: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    uncalibrated: list[str] = field(default_factory=list)  # calibration keys still at baseline

    @property
    def feasible(self) -> bool:
        return all(v <= 0.0 for v in self.constraints.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "params": self.params,
            "objectives": self.objectives,
            "constraints": self.constraints,
            "feasible": self.feasible,
            "metrics": self.metrics,
            "warnings": self.warnings,
            "uncalibrated": self.uncalibrated,
        }


@dataclass(frozen=True)
class Param:
    name: str
    kind: str  # "float" | "categorical"
    low: float | None = None
    high: float | None = None
    choices: tuple[Any, ...] = ()
    log: bool = False
    units: str = ""


@runtime_checkable
class DesignModel(Protocol):
    id: str  # e.g. "hx.two_fluid"
    version: str
    requirements_model: type[BaseModel]

    def parameters(self, req: BaseModel) -> list[Param]: ...

    def evaluate(self, params: dict[str, Any], req: BaseModel) -> Evaluation: ...
