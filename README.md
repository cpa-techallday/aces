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

## Status: v0.1 (2026-10-07)

| Area | State |
| --- | --- |
| Core engine: spec validation, design-model contract, NSGA-II search, evidence ledger, simlab export | Working, tested |
| First domain: two-fluid TPMS heat-exchanger core (`hx.two_fluid`): gyroid, diamond, primitive | Working on the fast tier; **uncalibrated** |
| TPMS geometry: porosity, wetted area, hydraulic diameter measured on the level set; STL export | Working; mid-surface areas within 0.4 % of literature |
| Manufacturability: measured minimum wall, channel neck (passable sphere), isolated pockets, cells across | Working; precomputed table checked against live measurement |
| Manufacturing routes (`aces processes`): LPBF, polymer extrusion, robotic lattice, printed formwork, rod-lattice welding; CNC and casting planned | Working |
| ARC-1 robotic arc-lattice chassis (rod placement + stud-welded nodes) | `studies/arc1_chassis`, runnable as the `rod.lattice` route (`aces rod`) |
| Structural check of strut lattices (Pynite linear static: stress, Euler buckling, deflection) | Working; optional `structure` block in form.lattice specs |
| Calibration from CFD/rig measurements (`aces calibrate`) | Working; constant, power-law or inertial-onset factors; refuses thin or scattered data; the thermal boundary comes from the rows |
| Next 50 steps, shared by the agents working on ACES | [docs/WORKFLOW.md](docs/WORKFLOW.md) |
| Calibration CFD (`aces cfd-sample`, `cfd-campaign`, `cfd-collect`; simlab solver `aces-tpms-periodic`) | Validated against exact duct results. `aces-cfd:0.1` is built and registered on the rig (2026-10-07); the rig GPU validation run waits for a sim-lab window ([docs/SIMLAB_CFD.md](docs/SIMLAB_CFD.md)) |
| MCP server for Mission Control agents (`aces-mcp`, read/submit/full tiers, audit log) | Working (stdio); each call runs in its own limited process; the rod route is `full` tier only |
| Radar phased-array domain (`radar.phased_array`) | Fast tier with hand-checked link budget. Refused by `check`/`design` until the spec has sourced losses and efficiencies and the `pcb_antenna` route exists (planned). Keep radar data on the LAN until it is classified under the DSGL |
| Manufacturing cost estimate (`aces cost`, proposed D24) | Estimate only, not a quote: every rate comes from a sourced `--rates` file; planned routes are never recommended |
| Isaac Sim scene tier (`aces.isaac`, DARKNESS) | Scene and robotics checks only: PhysX is FP32, so never verification. No defence use until the EULA review ([docs/LICENCES.md](docs/LICENCES.md)) |
| ISO 27001 evidence (`aces compliance`) | Collects into a new `run-*` folder with a sha256 manifest, chained into the ledger |
| CI (`.github/workflows/ci.yml`) | pytest on Linux and Windows, the ruff error gate and pip-audit on every push and pull request |
| formgen (Fusion forms to robot-printed lattices, JBI jobs, scale models) as the `form.lattice` route | Imported and tested; see [docs/FORMGEN.md](docs/FORMGEN.md) |
| Agent-authored design logic (Author / Judge agents) | Not started |

Every result is labelled "fast-tier ranking only, not verified" until simlab has checked it. The calibration
factors stay at 1.0, and are listed on every report, until CFD or test data replaces them.

## Quick start

```bash
pip install -e .[dev]
python -m pytest                       # ~TESTCOUNT tests: textbook and hand checks, geometry, safety guards, routes, structures, calibration, MCP

cp specs/templates/hx_two_fluid.yaml my_job.yaml   # fill every null from the customer requirement
aces check my_job.yaml                 # lists anything missing
aces design my_job.yaml --out runs/my_job   # a new folder for every run
aces export-simlab runs/my_job --rank 0 --solver <registered-solver> --vram-gb 8
aces ledger verify
aces ledger head                       # copy the head hash somewhere else; later: verify --expect-head H --min-records N
```

Every run writes into a new folder, and no tool writes through a symbolic link. Set `ACES_LEDGER_HMAC_KEY`
(kept outside the repo) and run `aces ledger head --sign` to sign the ledger head. If a crash leaves a half-written
last line, appends stop until `aces ledger seal` records it; nothing in the ledger is ever edited or deleted.

Large-format forms (robot cell or printed formwork):

```bash
pip install -e .[formgen]              # or tools\windows\install.cmd on Windows
cp specs/templates/form_lattice.yaml job.yaml
aces form job.yaml --out runs/job
```

The formgen Studio app, CLI and Fusion add-in are documented in [docs/FORMGEN.md](docs/FORMGEN.md).

Rod lattices (robot-placed rods, welded nodes):

```bash
cp specs/templates/rod_lattice.yaml job.yaml        # study_dir: studies/arc1_chassis
aces rod job.yaml --out runs/arc1
```

Calibration and agents:

```bash
aces cfd-sample --tpms gyroid --wall-ratio 0.1 --cell-m 0.004 --fluid Air --T 320 --p 2e5 --re 20 --out s.json
aces cfd-campaign --id aces-cal-air-01 --out aces-cal-air-01.yaml   # simlab sweep for the rig
aces cfd-collect out/exp/aces-cal-air-01 --out cal.json            # measurement rows from the rig
aces calibrate cal.json --registry calibration.json --project P --owner O --rd-activity A  # boundary comes from the rows; --boundary only for rows without one
pip install -e .[mcp] && ACES_MCP_POLICY=submit aces-mcp      # stdio MCP server for agents
```

`aces-mcp` limits: `ACES_MCP_RUN_TIMEOUT_S`, `ACES_MCP_READ_TIMEOUT_S`, `ACES_MCP_MEMORY_MB` (POSIX only),
`ACES_MCP_MAX_RUNS` and `ACES_MCP_MAX_LEDGER_MB`. Paths are confined to `ACES_MCP_ROOT`, and never reach `src/` or
`.git`.

Cost estimates (not quotes):

```bash
aces cost examples/cost_hx_core.yaml --rates RATES.yaml   # every rate {value, source, date}; nothing built in
aces cost --from-run runs/my_job --rank 0 --rates RATES.yaml
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
  evidence.py       hash-chained ledger, company enforced as CPA; verified run loading
  safeio.py         output writes that never follow links or overwrite silently
  proc.py           subprocesses: argument lists, minimal environment, whole-tree timeouts
  costing.py        route cost estimates from a sourced rates file
  compliance.py     ISO 27001 evidence collection
  isaac/            Isaac Sim scene tier (FP32, not verification)
  simlab.py         simlab experiment export (float64 default, vram cap 60 GB)
  report.py         markdown report that states what is not verified
  manufacturing.py  manufacturing routes and what is checked for each
  structures/       Pynite frame check for strut lattices
  mcp_server.py     MCP tools for agents (policy tiers, path confinement, audit)
  domains/hx/       TPMS geometry, manufacturability table, two-fluid heat-exchanger model
  domains/form/     form.lattice route: formgen run + structural check + evidence
  domains/rod/      rod.lattice route: runs a committed rod-lattice study, checks, evidence
  domains/radar/    phased-array radar: link budget, array, RF cascade, EM adapter, calibration
src/formgen/        formgen package (load, remesh, lattice, sequence, export, scale model, Studio GUI)
fusion_addin/       formgen Fusion 360 add-in
tools/windows/      install / formgen / formgen-studio launchers
examples/formgen/   demo facade panel
studies/            design studies (arc1_chassis: robotic rod-lattice chassis)
scripts/            build_tpms_table.py (regenerates the measured manufacturability table)
specs/templates/    spec templates (nulls, units in comments)
docs/               architecture, decisions, licence register, roadmap
```

## Rules

See [CLAUDE.md](CLAUDE.md). In short: no invented data, sourced properties only, FP64 for verification,
surrogates and fast tiers rank but a registered solver signs off, licence-clean dependencies, and R&D evidence
under Carbon Project Australia.

Proprietary. Copyright 2026 Carbon Project Australia Pty Ltd. See [LICENSE](LICENSE).
