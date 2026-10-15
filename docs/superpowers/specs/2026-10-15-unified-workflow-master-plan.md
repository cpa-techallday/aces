# ACES Unified Workflow — Master Plan (2026-10-15)

Supersedes: per-wave notes. Inputs: `docs/superpowers/specs/2026-10-15-brief-driven-playbooks-design.md`,
`docs/research/2026-10-15-*.md`, the Pi-OS update `TO-ACES-DESKTOP-2026-10-13.md` (Mach Dart v13 handoff),
the live GRID CONTROL console analysis (192.168.1.65:3200), and the solver inventory in
`cudax-sim-runtime` (33 registry rows, all audited 2026-10-15).

## 1. One sentence

Any brief — text, a file, or a staged spec like Mach Dart v13 — enters the console, is classified and
planned by the playbook planner, runs through the Golden Run loop on the rig using the full solver
fabric, and returns evidence-backed answers: how good it is, at what, and why.

## 2. The solver fabric (every tool, one access path)

Every capability is a registry row (`simlab/solvers.json` + `simlab/solvers.d/*.json`) with an adapter
module, a docker image, named-input refusals, and a validation record. Nothing is reachable except
through the registry — that is what keeps agents untangled: one catalogue, one contract, one evidence
format.

| Tier | Rows | State 2026-10-15 |
|---|---|---|
| Golden structural core | golden-case, golden-check, ccx-check | pass (rig-validated) |
| Generative design | gendesign-topopt, gendesign-topopt-mlc (multi-load-case + stress gate), gendesign-parametric, gendesign-beam-check, step-structures | pass |
| Geometry / topopt→CAD | geom-reconstruct, topopt-step (aces-geom:0.1) | pass (in-container evidence) |
| CFD | su2-rans, openfoam-cht (aces-cfd:0.2), pyfr, aces-tpms-periodic | cfd pair: validation run in flight |
| EM | aces-tile-em (Palace + gprMax GPU) | pass (gprMax GPU <0.001 dB vs CPU) |
| Rocket | rocket-cea, cantera-check (pass), pelec-combustion (image GPU-validated; adapter added 2026-10-15) | pass / reacting case pending |
| Optimisation | mdao-run, pymoo-nsga2, ax-bayesopt (aces-opt:0.1) | pass (in-container OPT-1..4) |
| Surrogates / ML | surrogate-bracket-{gp,et,gp-lhs,fno} + PhysicsNeMo tier (this wave) | mixed (two honest `fail` rows retained) |
| GPU FEM (this wave) | warp-fem-* (Warp 1.18 `warp.fem`) | integrating |
| Multibody / aeroelasticity / explicit | exudyn, flutter-screen, radioss tiers (existing) | validated earlier |

## 3. The workflow spine

```
brief (console Plan plate, or staged spec dir) 
  -> classify (aces_engineering.playbooks.classify)
  -> playbook YAML (per part class; stage order + gates are data)
  -> plan JSON (parameters extracted, defaults noted, blocked stages named)
  -> user edits spec sheet (live re-validation)
  -> launch -> Golden Run loop (simlab queue, GPU leases, evidence ledger)
  -> score + "why" panel (acceptance gates, surrogate feature-importance)
```

Access rules: the console never calls solvers directly (queue only); the planner never invents stage
order (playbooks only); solvers never run without a registry row and a case validated by its adapter.

## 4. Agent coordination protocol (how agents don't tangle)

1. **One worktree + one branch per agent** (`git worktree`), disjoint file scopes declared up front.
2. **Registry rows only as new drop-in files** under `simlab/solvers.d/` — never edit `solvers.json`,
   never touch `golden.json`; canonical `format_registry()` layout is byte-tested.
3. **Named-file commits only**, no push by agents; the coordinator merges and runs the full suite.
4. **Honesty contract**: pending stays pending without real numbers; a failed validation is reported,
   not hidden (the surrogate `fail` rows are the model).
5. **Sequencing**: tool waves (solvers) -> integration wave (pipeline) -> deployment wave (Pi/Tailscale);
   a later wave starts only on a green merged main.

## 5. Waves and status

- **Wave 1 (done)**: SU2/OpenFOAM, geom/topopt→CAD, opt tier, rocket tier, planner, Plan UI.
- **Wave 2 (done)**: opt validated in-container; PeleC built + Sod GPU-validated on the Blackwell;
  full-spectrum audit (registry/adapters/scripts; nozzle-angelino silent-numbers bug found, now fixed);
  main at 455 passed / 0 errors.
- **Wave 2.5 (in flight)**: finish CFD reference validation (NACA0012 SU2, heated-plate CHT).
- **Wave 3 (in flight)**: Warp `warp.fem` GPU-FEM tier; PhysicsNeMo deepening (versioned image, more
  surrogate families); console professional-UI pass with Playwright click-through of every screen.
- **Wave 4 (next)**: end-to-end pipeline integration; Mach Dart v13 (staged at
  `C:\Users\shedm\Work\machdart-airframe\specs\v13\`) as the first real mission through the whole spine:
  centre-body (structure playbook) then rocket-mount; then merge to the live GRID CONTROL backend and
  deploy to the Pi under Tailscale (tailnet-only, token server-side).

## 6. Acceptance for "done"

Every suite green on both repos; every registry row validated or honestly pending; the Plan plate drives
a real brief to evidence on the rig; every console screen clicked through with zero console errors;
the merged console reachable over Tailscale from off-LAN.
