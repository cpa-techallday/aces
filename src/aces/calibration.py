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

A factor with sources must say where it holds and when it was fitted: ``value``, a non-empty
``valid_range`` (each ``[low, high]``) and ``fitted_on`` (ISO date) are required, both when it
is loaded and when it is set.  Otherwise an entry with only ``sources`` would load as a
"calibrated" 1.0 that can never warn out of range.

A factor may vary with Reynolds number (``Factor.at``: a power law, or an inertial onset
value * (1 + (Re / re_ref) ** n)) when calibration data show a trend a constant cannot
represent; it is never used outside its fitted Re range without a warning.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .safeio import write_text


@dataclass(frozen=True)
class Factor:
    key: str  # e.g. "hx.gyroid.nusselt"
    value: float = 1.0
    sources: tuple[str, ...] = field(default_factory=tuple)  # simlab run ids, test ids
    valid_range: dict[str, tuple[float, float]] = field(default_factory=dict)
    fitted_on: str = ""  # ISO date
    basis: str = ""  # what the factor multiplies and how it was fitted
    re_exponent: float = 0.0
    re_ref: float = 0.0
    form: str = ""  # "" / "constant", "power" or "onset"; see ``at``

    @property
    def calibrated(self) -> bool:
        return bool(self.sources)

    @property
    def kind(self) -> str:
        if self.form:
            return self.form
        return "power" if self.re_exponent != 0.0 else "constant"

    def at(self, re: float) -> float:
        """The factor at Reynolds number ``re``.

        constant  value
        power     value * (Re / re_ref) ** re_exponent
        onset     value * (1 + (Re / re_ref) ** re_exponent): ``value`` is the low-Re (creeping-flow)
                  ratio and ``re_ref`` the Re at which inertia has doubled it (Forchheimer-like)
        """
        k, r = self.kind, max(re, 1e-12)
        if k == "power":
            return self.value * (r / self.re_ref) ** self.re_exponent
        if k == "onset":
            return self.value * (1.0 + (r / self.re_ref) ** self.re_exponent)
        return self.value

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
    if not (math.isfinite(f.value) and f.value > 0):
        raise ValueError(f"calibration factor {f.key!r} must be positive, got {f.value}")
    if not f.valid_range:
        raise ValueError(f"calibration factor {f.key!r} has no valid_range: without the fitted range it could "
                         f"never warn when used outside it")
    for name, rng in f.valid_range.items():
        lo, hi = rng
        if not (math.isfinite(lo) and math.isfinite(hi) and lo <= hi):
            raise ValueError(f"calibration factor {f.key!r} has a bad valid_range for {name}: {rng}")
    try:
        _dt.date.fromisoformat(str(f.fitted_on)[:10])
    except ValueError:
        raise ValueError(f"calibration factor {f.key!r} needs fitted_on as an ISO date, got {f.fitted_on!r}") from None
    if f.kind not in ("constant", "power", "onset"):
        raise ValueError(f"calibration factor {f.key!r} has unknown form {f.form!r}")
    if f.kind != "constant":
        if not f.re_ref > 0:
            raise ValueError(f"calibration factor {f.key!r} has an Re exponent but no positive re_ref")
        if "reynolds" not in f.valid_range:
            raise ValueError(f"calibration factor {f.key!r} varies with Re but has no fitted Re range")
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
        if not isinstance(raw, dict):  # a bad file, not a programming error: callers catch ValueError
            raise ValueError(f"calibration file {p} must hold a JSON object of factors")  # noqa: TRY004
        factors = {}
        for k, v in raw.items():
            if not isinstance(v, dict):
                raise ValueError(f"calibration factor {k!r} in {p} is not an object")  # noqa: TRY004
            if "value" not in v:  # never default a stored factor to 1.0: it would pass as calibrated
                raise ValueError(f"calibration factor {k!r} in {p} has no value")
            vr = {}
            for name, rng in (v.get("valid_range") or {}).items():
                if not (isinstance(rng, (list, tuple)) and len(rng) == 2):
                    raise ValueError(f"calibration factor {k!r} in {p}: valid_range {name} must be [low, high]")
                vr[name] = (float(rng[0]), float(rng[1]))
            factors[k] = Factor(key=k, value=float(v["value"]), sources=tuple(v.get("sources", ())),
                                valid_range=vr, fitted_on=v.get("fitted_on", ""), basis=v.get("basis", ""),
                                re_exponent=float(v.get("re_exponent", 0.0)), re_ref=float(v.get("re_ref", 0.0)),
                                form=v.get("form", ""))
        return cls(factors, p)

    def get(self, key: str) -> Factor:
        return self._factors.get(key, Factor(key=key))

    def set(self, factor: Factor) -> None:
        self._factors[factor.key] = _validate(factor)

    def save(self, path: str | Path | None = None) -> Path:
        p = Path(path or self.path or "calibration.json")
        data = {k: {kk: vv for kk, vv in asdict(f).items() if kk != "key"} for k, f in sorted(self._factors.items())}
        write_text(p, json.dumps(data, indent=2, sort_keys=True), overwrite=True)  # never through a planted link
        self.path = p
        return p

    def as_dict(self) -> dict:
        return {k: asdict(f) for k, f in sorted(self._factors.items())}
