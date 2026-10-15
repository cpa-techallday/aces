"""aces_engineering.playbooks: the brief -> class -> playbook -> plan pipeline (Approach A).

The brief is the project: a natural-language brief is classified into a part class
(`classify`), the class's versioned YAML playbook (`<class>.yaml` next to this module, loaded
by `library`) supplies the engineer-ordered stage skeleton, and `plan.plan_from_brief`
resolves the brief's parameters into a concrete plan JSON whose stage graph the Golden Run
loop executes. `validate` checks the playbooks themselves: schema, solver references against
the simlab registry vocabulary, and gate satisfiability.

Standard library only at import time; PyYAML is imported lazily by `library`/`validate`.
"""
from __future__ import annotations

VERSION = "0.1.0"
SCHEMA = "aces-playbook/1"
PLAN_SCHEMA = "aces-plan/1"

# The part classes at launch (design doc section 2, classifier layer).
CLASSES = ("structure", "flow-device", "heat-rejection", "em-device", "rotating-machine", "rocket-motor")
DEFAULT_CLASS = "structure"

# The simlab solver-registry vocabulary (solver row names). `available` rows exist in the
# registry of cudax-sim-runtime (or its solvers.d drop-ins); `incoming` rows are the drop-ins
# the aces-cfd / aces-geom / aces-opt / aces-rocket images are landing this cycle (design doc
# section 4). A reference to anything else is `missing` and blocks the stage that needs it.
# `surrogate-bracket-*` is a family: any row with that prefix is available.
SOLVERS_AVAILABLE = frozenset({
    "golden-case", "golden-check", "ccx-check",
    "gendesign-topopt", "gendesign-parametric", "step-structures",
    "pyfr", "aces-tile-em", "aces-tpms-periodic",
    "surrogate-bracket-gp", "surrogate-bracket-gp-lhs", "surrogate-bracket-et", "surrogate-bracket-fno",
    "nozzle-angelino",
})
SOLVERS_INCOMING = frozenset({
    "su2-rans", "openfoam-cht", "geom-reconstruct", "topopt-step",
    "mdao-run", "pymoo-nsga2", "ax-bayesopt",
    "rocket-cea", "cantera-check",
})
SOLVER_PREFIXES_AVAILABLE = ("surrogate-bracket-",)

# A stage whose work is pure composition (scoring, spec assembly) declares solver "none":
# the planner/golden loop computes it in-process and it is always satisfiable.
SOLVER_NONE = "none"

SOLVER_AVAILABLE = "available"
SOLVER_INCOMING = "incoming"
SOLVER_MISSING = "missing"


def solver_status(name: str) -> str:
    """available / incoming / missing for a solver reference (`none` is always available)."""
    if name == SOLVER_NONE or name in SOLVERS_AVAILABLE:
        return SOLVER_AVAILABLE
    if any(name.startswith(p) for p in SOLVER_PREFIXES_AVAILABLE):
        return SOLVER_AVAILABLE
    if name in SOLVERS_INCOMING:
        return SOLVER_INCOMING
    return SOLVER_MISSING
