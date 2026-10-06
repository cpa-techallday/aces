"""Calibration factors: how the fast tier learns from CFD and tests.

A factor multiplies a baseline correlation (e.g. the straight-duct Nusselt number
applied inside a gyroid core).  Every stored factor carries provenance and the range
of conditions it was fitted over.  A factor with no provenance does not exist: the
model uses 1.0 and reports the key as ``uncalibrated`` on every result, so a baseline
estimate is never mistaken for a calibrated one.  A calibrated factor used outside
its fitted range produces a warning.

The registry is a JSON file so the learning loop (simlab CFD runs, rig tests) can
update it and the evidence ledger can hash it:

    {"hx.gyroid.nusselt": {"value": 1.9, "sources": ["simlab:<run-id>"],
                           "valid_range": {"reynolds": [50, 1500], "wall_ratio": [0.05, 0.15]},
                           "fitted_on": "2026-11-02"}}
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Factor:
    key: str  # e.g. "hx.gyroid.nusselt"
    value: float = 1.0
    sources: tuple[str, ...] = field(default_factory=tuple)  # simlab run ids, test ids
    valid_range: dict[str, tuple[float, float]] = field(default_factory=dict)
    fitted_on: str = ""  # ISO date
    basis: str = ""  # what the factor multiplies and how it was fitted

    @property
    def calibrated(self) -> bool:
        return bool(self.sources)

    def out_of_range(self, **conditions: float) -> list[str]:
        """Names of conditions that fall outside the fitted range."""
        out = []
        for name, value in conditions.items():
            if name in self.valid_range:
                lo, hi = self.valid_range[name]
                if not lo <= value <= hi:
                    out.append(f"{name}={value:.4g} outside fitted [{lo}, {hi}]")
        return out


def _validate(f: Factor) -> Factor:
    if not f.sources:
        raise ValueError(f"calibration factor {f.key!r} has no provenance (sources); refusing to use it")
    if not f.value > 0:
        raise ValueError(f"calibration factor {f.key!r} must be positive, got {f.value}")
    return f


class Registry:
    def __init__(self, factors: dict[str, Factor] | None = None, path: Path | None = None):
        self._factors = {k: _validate(v) for k, v in (factors or {}).items()}
        self.path = path

    @classmethod
    def load(cls, path: str | Path | None) -> "Registry":
        """Load a registry.  None means no calibration; a named file that is missing is an error."""
        if path is None:
            return cls()
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"calibration file {p} not found (set calibration_file: null for none)")
        raw = json.loads(p.read_text())
        factors = {}
        for k, v in raw.items():
            vr = {name: (float(lo), float(hi)) for name, (lo, hi) in (v.get("valid_range") or {}).items()}
            factors[k] = Factor(key=k, value=float(v.get("value", 1.0)), sources=tuple(v.get("sources", ())),
                                valid_range=vr, fitted_on=v.get("fitted_on", ""), basis=v.get("basis", ""))
        return cls(factors, p)

    def get(self, key: str) -> Factor:
        return self._factors.get(key, Factor(key=key))

    def set(self, factor: Factor) -> None:
        self._factors[factor.key] = _validate(factor)

    def save(self, path: str | Path | None = None) -> Path:
        p = Path(path or self.path or "calibration.json")
        data = {k: {kk: vv for kk, vv in asdict(f).items() if kk != "key"} for k, f in sorted(self._factors.items())}
        p.write_text(json.dumps(data, indent=2, sort_keys=True))
        self.path = p
        return p

    def as_dict(self) -> dict:
        return {k: asdict(f) for k, f in sorted(self._factors.items())}
