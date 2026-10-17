"""Experiment specification: the contract every solver, agent and report speaks."""
from __future__ import annotations

import hashlib
import itertools
import os
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator


class Geometry(BaseModel):
    source: str = "none"              # fusion | step | mesh | parametric | none
    ref: Optional[str] = None         # path relative to the spec file
    sha256: Optional[str] = None      # filled by the loader when ref exists


class Resources(BaseModel):
    # 0 = CPU-only: the case goes to the `cpu` queue (queue.py: needs_gpu = vram_gb > 0).
    # A nonzero default here silently GPU-queued CPU jobs (gyroid SF-9, conformal-antenna SF-2).
    vram_gb: float = 0.0
    cpu_threads: int = 8
    wall_h: float = 2.0
    precision: str = "float64"        # float64 on the rig, float32 on the workstation


class Tracking(BaseModel):
    uri: Optional[str] = None         # MLflow tracking URI; default env MLFLOW_TRACKING_URI
    experiment: Optional[str] = None  # MLflow experiment name; default = project


class Experiment(BaseModel):
    id: str
    project: str = "default"
    owner: str = "aaron"
    physics: str
    solver: str
    description: str = ""
    geometry: Geometry = Field(default_factory=Geometry)
    params: Dict[str, Any] = Field(default_factory=dict)
    sweep: Dict[str, List[Any]] = Field(default_factory=dict)
    outputs: List[str] = Field(default_factory=list)
    resources: Resources = Field(default_factory=Resources)
    tracking: Tracking = Field(default_factory=Tracking)
    evidence: bool = True
    # R&D evidence fields (Phase 6): what the experiment is meant to show, for the contemporaneous record
    hypothesis: Optional[str] = None      # the technical question / expected outcome being tested
    expected: Optional[str] = None        # what result would confirm or refute it
    rd_activity: Optional[str] = None     # R&DTI activity reference (core / supporting activity id)
    company: Optional[str] = None         # evidence-engine company key (carbon, power, omni, ...)
    tags: Dict[str, str] = Field(default_factory=dict)
    spec_path: Optional[str] = None
    spec_sha256: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _id_is_filename_safe(cls, v: str) -> str:
        bad = set('/\\:*?"<>| ')
        if any(ch in bad for ch in v):
            raise ValueError("experiment id must be filename-safe (no spaces or path characters)")
        return v

    @property
    def base_dir(self) -> str:
        return os.path.dirname(os.path.abspath(self.spec_path)) if self.spec_path else os.getcwd()

    def resolve(self, rel: str) -> str:
        return rel if os.path.isabs(rel) else os.path.normpath(os.path.join(self.base_dir, rel))

    def cases(self) -> List[Dict[str, Any]]:
        """Cartesian fan-out of the sweep over the base params; each case carries case_id."""
        if not self.sweep:
            return [{"case_id": "c000", **self.params}]
        keys = list(self.sweep)
        out = []
        for i, combo in enumerate(itertools.product(*[self.sweep[k] for k in keys])):
            p = dict(self.params)
            p.update(dict(zip(keys, combo)))
            p["case_id"] = f"c{i:03d}"
            out.append(p)
        return out


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load(path: str) -> Experiment:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    exp = Experiment(**data)
    exp.spec_path = os.path.abspath(path)
    exp.spec_sha256 = sha256_file(path)
    if exp.geometry.ref:
        g = exp.resolve(exp.geometry.ref)
        if os.path.isfile(g):
            exp.geometry.sha256 = sha256_file(g)
    return exp
