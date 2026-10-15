# Brief-Driven Engineering Playbooks — Design (Approach A)

Date: 2026-10-15. Status: approved by owner. Research basis: `docs/research/2026-10-15-*.md`
(generative-topopt processes, AI-native engineering loops, open-source tool gaps).

## 1. Intent

The brief is the project. The user describes an object/part/machine ("optimise this design",
"a 1500 kg six-wheeled off-roader…") and ACES — not the user — decides what it is, what "good"
means for it, and the engineer-ordered stage sequence to get there. Mission setup complexity
moves from the user into the software.

## 2. Architecture

Three new layers, each independently testable:

1. **Classifier** (`aces_engineering.playbooks.classify`): brief text + optional uploaded
   geometry → part class. Deterministic rule table over a vocabulary (regex/keyword families,
   units, material hints), LLM-assist only as a tie-breaker later. Classes at launch:
   `structure` (chassis/bracket/frame), `flow-device` (duct/intake/nozzle), `heat-rejection`
   (cooler/heatsink/heat-exchanger), `em-device` (antenna/radome/waveguide), `rotating-machine`,
   `rocket-motor`. Unknown → `structure` default with a `low_confidence` flag on the plan.
2. **Playbook library** (`aces_engineering/playbooks/*.yaml`, versioned, golden-tested):
   one YAML per part class encoding the stage skeleton from the research:
   - universal rule: constraint physics before objective physics; iterate cheap, gate expensive;
     the optimiser's own numbers are never the answer — every playbook ends in an independent
     verification stage (second discretisation or second solver) with explicit acceptance gates.
   - `structure`: baseline FEA → SIMP topopt (multi-load-case) → density threshold sweep →
     implicit reconstruction → voxel FEA re-check → CalculiX conforming check → score.
   - `flow-device`: geometry param → DOE (LHS) → CFD sweep (SU2/OpenFOAM) → surrogate →
     NSGA-II Pareto → solver re-check of Pareto knee → score.
   - `heat-rejection`: CHT (chtMultiRegionFoam) consuming flow fields → thermal resistance /
     heat rejection scoring.
   - `em-device`: existing Palace/gprMax tier → S-parameter/eigen gates.
   - `rocket-motor`: RocketCEA design point → Cantera chemistry → (PeleC when image ready).
   Each stage declares: solver (registry row name), inputs, outputs, entry/exit criteria,
   evidence artefacts, acceptance gate. Stage order and gates are data, not code.
3. **Planner** (`aces_engineering.playbooks.plan`): brief + classification + playbook →
   concrete Plan (ordered stages with resolved parameters from the brief: loads, materials,
   envelope, constraints) + spec-sheet skeleton (the metrics that matter for the class).
   Plan is JSON, rendered editable in the console; user edits re-validate against the playbook
   schema before launch. Launch hands the stage graph to the Golden Run loop.

## 3. Console UI (aces-console)

Conversational first: brief box with file upload (already present) → on submit, a **Plan view**
renders: class chip + confidence, ordered stage cards (solver, purpose, gate), live spec sheet
that re-computes when the user edits any parameter, stage progress during the run, and the final
"how good is it and why" panel (score, acceptance gates, surrogate feature-importance where a
surrogate was used). No document-style wall of options; the plan IS the interface.

## 4. Tool integration (cudax-sim-runtime)

New solver rows land as drop-ins under `simlab/solvers.d/` (never rewriting `solvers.json`),
one file per family, preserving the canonical registry layout (`format_registry`).

- **aces-cfd:0.2**: SU2 (RANS, discrete adjoint, FFD morphing) + OpenFOAM (general CFD,
  chtMultiRegionFoam CHT). CPU with Ginkgo offload where practical.
- **aces-geom:0.1**: density→SDF (OpenVDB or scikit-image fallback) → watertight mesh →
  OpenCascade/pythonocc/CadQuery sewing → STEP; semi-automatic topopt→CAD path.
- **aces-opt:0.1**: OpenMDAO, pymoo (NSGA-II), Ax/BoTorch; multi-load-case and
  stress-constrained extensions to the gendesign SIMP loop.
- **aces-rocket:0.1**: Cantera + RocketCEA (pip); PeleC Dockerfile authored, build attempted,
  documented if infeasible in-session.

## 5. Verification and testing

- Every new solver: adapter module + unit tests + registry row; full suite
  `python -m pytest tests/ -q` in cudax-sim-runtime must stay at 0 failed / 0 errors
  (baseline 266 passed). Playbooks: golden tests that each YAML parses, references only
  existing registry rows, and stage gates are satisfiable.
- Console: route/verify tests in the existing harness style.
- No fabricated results: docker builds that cannot finish in-session are committed as
  Dockerfile + docs and marked `validation: pending`, never claimed working.

## 6. Error handling

Classifier low confidence → plan flagged, user confirms class before launch. Missing solver
for a stage → plan marks the stage `blocked` with the gap named (never silently skipped).
Gate failure at any stage → loop stops, evidence + failing gate reported, rework suggestion
from the playbook's gate notes.

## 7. Out of scope (this cycle)

LLM free-form plan composition, FSI/preCICE coupling, EnergyPlus, full-GPU OpenFOAM forks.
