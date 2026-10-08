"""Tier 0: generative proposers.

A proposer SUGGESTS geometry. Nothing it emits is a design: every proposal is
measured on the same level-set definitions as the domain models, checked
against the manufacturing route, and ranked by the search tier. Registered,
verified solvers still sign off (D5). A proposal outside the calibrated range
carries a warning, exactly like a hand-written parameter set.

Licence gate: proposer code and any weights it loads must allow commercial,
closed-source distribution and defence use (docs/LICENCES.md). Research-only
weights are registered in REJECTED so nobody wires them in by accident.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol


class LicenceError(ValueError):
    """A proposer's code or weights do not pass the licence gate."""


# Mirrors the docs/LICENCES.md rule: commercial, closed-source, defence use allowed.
ALLOWED_LICENCES = frozenset({
    "internal",  # written in-house; no external code or weights
    "MIT", "MIT-CMU", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "MPL-2.0", "PSF",
})

# Known-blocked proposers, with the reason, so an integration attempt fails loudly.
REJECTED: dict[str, str] = {
    "nvidia-xcube": ("Nvidia Source Code License: non-commercial research use only; "
                     "commercial use requires a separate NVIDIA Research Licence. "
                     "Verified 2026-10-08 against github.com/nv-tlabs/XCube."),
    "nvidia-scube": ("Nvidia Source Code License (same terms as XCube)."),
}


@dataclass(frozen=True)
class Proposal:
    """One suggested geometry, expressed as the parameters a domain model can evaluate.

    params must be complete enough that the domain's measurement layer can build and
    measure the geometry without further input — a proposal that cannot be measured is
    rejected at registration time, not at print time.
    """

    proposer: str
    domain: str
    seed: int
    params: dict[str, Any]
    notes: tuple[str, ...] = ()


class Proposer(Protocol):
    name: str
    licence: str
    domain: str

    def propose(self, req: dict[str, Any], n: int, seed: int) -> Iterable[Proposal]:
        """Yield up to n proposals satisfying req. Deterministic for a given seed."""
        ...


_REGISTRY: dict[str, Proposer] = {}


def register(p: Proposer) -> Proposer:
    if p.name in REJECTED:
        raise LicenceError(f"proposer {p.name!r} is blocked: {REJECTED[p.name]}")
    if p.licence not in ALLOWED_LICENCES:
        raise LicenceError(
            f"proposer {p.name!r} declares licence {p.licence!r}, which is not on the "
            f"allowlist in aces.propose.contract (see docs/LICENCES.md)")
    if not p.name or not p.domain:
        raise ValueError("a proposer needs a name and a domain")
    _REGISTRY[p.name] = p
    return p


def get(name: str) -> Proposer:
    if name in REJECTED:
        raise LicenceError(f"proposer {name!r} is blocked: {REJECTED[name]}")
    if name not in _REGISTRY:
        raise KeyError(f"unknown proposer {name!r}; registered: {', '.join(sorted(_REGISTRY)) or '(none)'}")
    return _REGISTRY[name]


def available(domain: str | None = None) -> list[str]:
    return sorted(p.name for p in _REGISTRY.values() if domain is None or p.domain == domain)
