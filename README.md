# ACES: Advanced Computational Engineering System

Carbon Project Australia's computational engineering platform. ACES turns a performance spec into a set of
printable, physics-derived hardware designs. It ranks them on a fast physics tier, sends the shortlist to the
8-GPU rig for solver verification, and records every step as R&D evidence.

ACES is the **design engine**. It sits on top of **simlab**, the CUDA-X simulation runtime in
`cudax-sim-runtime`. That runtime provides the queue, the solvers, the GPU leases and the operator UI. ACES writes
simlab experiments and learns from their results; it does not run high-fidelity solvers itself.

```
spec.yaml -> design model (encoded engineering logic) -> NSGA-II search on the fast tier
          -> shortlist -> simlab experiment (CFD / FEA on the rig) -> calibration registry
          -> evidence ledger (hash-chained, CPA)
```

## Status: v0.1 (2026-10-06)

| Area | State |
| --- | --- |
| Core engine: spec validation, design-model contract, NSGA-II search, evidence ledger, simlab export | Working, tested |
| First domain: two-fluid TPMS heat-exchanger core (`hx.two_fluid`): gyroid, diamond, primitive | Working on the fast tier; **uncalibrated** |
| TPMS geometry: porosity, wetted area, hydraulic diameter measured on the level set; STL export | Working; mid-surface areas within 0.4 % of literature |
| Manufacturability: measured minimum wall, channel neck (passable sphere), isolated pockets, cells across | Working; precomputed table checked against live measurement |
| Calibration loop from simlab CFD and rig tests | Registry and plumbing only; no fitted factors yet |
| Agent-authored design logic (Author / Judge agents) | Not started |

Every result is labelled "fast-tier ranking only, not verified" until simlab has checked it. The calibration
factors stay at 1.0, and are listed on every report, until CFD or test data replaces them.

## Quick start

```bash
pip install -e .[dev]
python -m pytest                       # 71 tests: textbook checks, geometry, safety guards, end to end

cp specs/templates/hx_two_fluid.yaml my_job.yaml   # fill every null from the customer requirement
aces check my_job.yaml                 # lists anything missing
aces design my_job.yaml --out runs/my_job
aces export-simlab runs/my_job --rank 0 --solver <registered-solver> --vram-gb 8
aces ledger verify
```

`aces sample --tpms gyroid --cell-m 0.004 --wall-ratio 0.1 --out gyroid.stl` writes a printable TPMS block (mm by default).

## Layout

```
src/aces/
  spec.py           spec envelope + validation that lists every missing field
  model.py          DesignModel contract (parameters, evaluate -> objectives, constraints, metrics)
  search.py         NSGA-II multi-objective search, dedupe, Pareto front
  correlations.py   textbook duct friction / Nusselt / e-NTU, each flagged when out of range
  fluids.py         CoolProp properties (no hard-coded property values)
  calibration.py    factors with provenance; unsourced factors are refused
  evidence.py       hash-chained ledger, company enforced as CPA
  simlab.py         simlab experiment export (float64 default, vram cap 60 GB)
  report.py         markdown report that states what is not verified
  domains/hx/       TPMS geometry, manufacturability table, two-fluid heat-exchanger model
scripts/            build_tpms_table.py (regenerates the measured manufacturability table)
specs/templates/    spec templates (nulls, units in comments)
docs/               architecture, decisions, licence register, roadmap
```

## Rules

See [CLAUDE.md](CLAUDE.md). In short: no invented data, sourced properties only, FP64 for verification,
surrogates and fast tiers rank but a registered solver signs off, licence-clean dependencies, and R&D evidence
under Carbon Project Australia.

Proprietary. Copyright 2026 Carbon Project Australia Pty Ltd. See [LICENSE](LICENSE).
