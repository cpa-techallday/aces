# Roadmap

The phases follow the platform plan "Beyond Noyron". The gates are stop-or-go: a phase does not spend until
the previous gate passes.

## Phase 0: foundation (Oct–Dec 2026)

- [x] Core engine: specs, design-model contract, NSGA-II search, evidence ledger, simlab export
- [x] First domain model on the fast tier: two-fluid TPMS heat exchangers
- [x] Manufacturing routes; formgen imported as the robotic lattice and printed formwork generator
- [x] ARC-1 rod-lattice chassis study imported (`studies/arc1_chassis`)
- [x] ARC-1 rod lattice wired as the `rod.lattice` route (spec, checks, evidence)
- [x] Structural check for strut lattices (Pynite), used by the formgen route
- [x] `aces calibrate`: fit `hx.*` factors from measurements with provenance and valid ranges
- [ ] simlab result adapter: read the rig's `results.json` straight into the calibration measurement format
- [ ] First CFD calibration set: periodic gyroid, diamond and primitive samples over Re and t/a on the rig
- [ ] Multigrid FP64 voxel FEA in simlab (runtime repo), for structural checks of printed cores
- [x] `aces-mcp` tools so Mission Control agents can drive runs (stdio)
- [ ] `aces-mcp` over HTTP on the LAN with bearer tokens, matching simlab-mcp

**Gate 1:** the calibrated hx model predicts held-out CFD pressure drop and Nusselt number within ±10 %.

## Phase 1: thermal MVP (Jan–Jun 2027)

- [ ] Headers and manifold model, so the full part is generated and not just the core
- [ ] Full-part geometry on a voxel kernel (PicoGK or OpenVDB), with STL/3MF export for the print bureau
- [ ] On-site flow and thermal rig; test results feed the calibration registry
- [ ] Cold plates and motor-cooling jackets as the next domains
- [ ] Conventional routes: CNC-machinable and cast geometry families, so a design can leave ACES without a printer
- [ ] Paid design sprints

**Gate 2:** a printed part tests within ±10 % of prediction, and there is a paying customer.

## Phase 2: propulsion parts (Jul 2027–Jun 2028)

- [ ] Regen-cooled chamber and nozzle domain, with NASA CEA and the Angelino aerospike generator from simlab
- [ ] Spike cooling and characteristic-length checks; injector element sizing
- [ ] Agent-authored design logic, test-gated by the Judge agent
- [ ] 1–5 kN partner hot-fire

**Gate 3:** the hot-fire works first time, and licence revenue is booked.
