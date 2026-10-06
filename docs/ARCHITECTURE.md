# ACES architecture

ACES follows the platform plan "Beyond Noyron" (Claude doc, 2026-10-06): keep the speed of encoded design
logic, and add what LEAP 71's Noyron lacks: search across trade-offs, measured accuracy, a learning loop, and
sovereign on-prem operation.

## The seven stages and where they live

| # | Stage | Where | Status in v0.1 |
| --- | --- | --- | --- |
| 1 | Performance spec | `aces.spec` + `specs/templates/` | Done: envelope + per-domain requirements, missing fields listed |
| 2 | Agent layer (Author, Optimiser, Judge) | Mission Control agents, through `aces-mcp` and simlab-mcp | `aces-mcp` done (read/submit/full); agent roles not started |
| 3 | Design logic + geometry kernel | `aces.domains.*` (encoded rules) + numerical level sets | `hx.two_fluid` done; PicoGK/OpenVDB kernel to come for full parts |
| 4 | Multi-fidelity physics | Tier 1 `aces.correlations` / `aces.fluids`; Tier 2 surrogates (simlab-ml); Tier 3 simlab solvers | Tier 1 done; tiers 2 and 3 reached through simlab |
| 5 | Build | DfAM checks in each model; slicing and print partners outside ACES | Wall, neck, pockets, cell count checked for hx |
| 6 | Test | Rig and partner test data | Not started |
| 7 | Data lake + learning loop | `aces.evidence` ledger + `aces.calibration` registry + `aces calibrate` | Fitting works; awaiting the first CFD calibration set |

## Manufacturing routes

Not every part is printed in metal, and some are not printed at all. Every design model or generator
declares the routes it supports (`aces processes` lists them). A spec must name an available route that its
domain supports, or it is refused.

| Route | Family | Status | Domain | What ACES checks |
| --- | --- | --- | --- | --- |
| `lpbf` | Metal powder-bed fusion | available | `hx.two_fluid` | measured minimum wall, channel neck, isolated pockets, envelope |
| `polymer_extrusion` | FFF/FDM (on-site Raise3D) | available | `hx.two_fluid` | as `lpbf`; polymer properties come from the spec |
| `robotic_lattice_extrusion` | Robotic spatial printing (HC10DTP cell) | available | `form.lattice` (formgen) | every strut sequenced, no islands, scale-model minimum feature, optional Pynite structural check |
| `printed_formwork` | Lattice formwork for foam fill and sprayed concrete/render | available | `form.lattice` (formgen) | as above; fill and finish are outside ACES |
| `arc_lattice_rod_welding` | Rod placement + stud-welded nodes (ARC-1) | available | `rod.lattice` (runs `studies/arc1_chassis`) | frame FEA utilisation, rod length limits, hubs, fixtures |
| `cnc_machining` | Subtractive | planned | none | none yet |
| `casting` | Sand or investment, printed patterns | planned | none | none yet |

`form.lattice` is a deterministic generator, not a search model. `aces form` validates the spec and runs
formgen's pipeline (load, remesh, tessellate, sequence, export). It then checks the route and records every
output (lattice JSON, waypoints, JBI jobs, scale model) by sha256 in the evidence ledger. See
[FORMGEN.md](FORMGEN.md).

## ACES and simlab

```
ACES (this repo, any machine)                       simlab (cudax-sim-runtime, rig 192.168.1.65)
  spec -> design model -> NSGA-II search              queue, GPU leases, solver images, MLflow
  shortlist -> export-simlab  ---- experiment.yaml -->  validate -> mesh -> solve -> post
  calibration registry  <-------- results ----------   results + evidence bundle
  evidence ledger (CPA)  ------- record hash ------->   tags.aces_record links the two ledgers
```

ACES never runs a high-fidelity solver itself, and it never touches the rig host. It writes experiments that
simlab validates against its own schema. Each experiment carries the hash of the ACES evidence record of the
design run it verifies.

## The design-model contract

A domain model (`aces.model.DesignModel`) provides:

- `requirements_model`: a pydantic model of the domain's spec block;
- `parameters(req)`: the search space (continuous, log or categorical);
- `evaluate(params, req)`: an `Evaluation` with objectives (minimised), constraints (feasible when <= 0),
  metrics, warnings, and the calibration keys still at baseline.

Search, reporting, evidence and simlab export are domain-agnostic. A new domain, such as regen-cooled
chambers or motor cooling jackets, is one new package under `aces/domains/` plus tests.

## The heat-exchanger model (`hx.two_fluid`)

1. **Geometry.** Gyroid, diamond and primitive sheet cores. Porosity, wetted area and hydraulic diameter are
   measured on the voxelised level set, using a one-voxel volume-of-fluid ramp and marching cubes. The
   resolution is raised automatically so every wall spans at least 3 voxels.
2. **Manufacturability.** These are measured, not nominal:
   - the thinnest wall, by bisection along the mid-surface normal;
   - the channel neck, as the largest sphere that percolates through each network (EDT on a periodic tile);
   - isolated fluid pockets, which trap powder.

   The measurements are tabulated in `domains/hx/data/tpms_manufacturability.json` by
   `scripts/build_tpms_table.py`, and a test checks the table against a live measurement.
3. **Thermal-hydraulics.** Fluid properties come from CoolProp, iterated to the mean stream temperatures.
   Nusselt and friction use straight-duct baselines. The laminar default is uniform wall temperature (3.66),
   the conservative choice. Overall UA is a series resistance, and the counterflow e-NTU gives duty and
   outlet temperatures. Pressure drop is Darcy over the flow path.
4. **Constraints.** The model checks:
   - duty;
   - both pressure drops;
   - minimum wall;
   - both necks;
   - isolated pockets;
   - cells across;
   - envelope;
   - single phase.
5. **Objectives.** Minimise mass, and minimise the worse of the two pressure-drop ratios.
6. **Calibration keys** for each TPMS kind are `hx.<kind>.nusselt`, `hx.<kind>.friction` and
   `hx.<kind>.tortuosity`.

What it does not model, stated on every result: headers and manifolds, maldistribution, entrance effects, wall
roughness, axial conduction, and compressibility at large pressure drop.

## Structural check for lattices

`aces.structures.frame` builds a Pynite model of any strut graph. Each strut is a solid round rod of the bead
or rod diameter, and the joints are rigid. Load cases come from the spec, already factored by the engineer:

- self-weight as a multiple of g;
- a uniform pressure on the projected area of the loaded skin, shared by tributary area so the total does not
  depend on how finely the skin is meshed (the area is the convex hull, so openings count as loaded);
- point loads, snapped to the nearest node within a tolerance or refused.

Per strut and load case it checks:

- combined axial, bending and torsional stress (von Mises) against strength / material factor;
- column capacity, the lower of Euler and Johnson's parabola, so short struts are not credited with elastic
  buckling loads above yield;
- bending in compressed struts amplified by 1 / (1 − N/N_euler) (P-delta);
- deflection against the spec's limit, and reactions that must balance the applied load.

Pynite's axial sign convention is measured at start-up rather than assumed. That is how a sign error in the
ARC-1 study's own check was found (peak utilisation 1.85, not 1.67).

Not checked, and listed on every result: global (system) buckling, dynamic response and fatigue, joint and
weld strength, shear deformation, and loads between nodes.

For the rod route, the frame FEA uses the capacity of a nominal-length rod. ACES also rescales the peak
utilisation to the weakest rod's own capacity. For ARC-1 rev A that bound is 2.77: the weakest rod carries
661 N against 992 N nominal. Clearing it needs a per-rod force map.

## The learning loop

1. `aces export-simlab` writes periodic fluid samples of shortlisted cores.
2. simlab runs CFD on them: XLB lattice-Boltzmann for pressure gradient, conjugate heat transfer for Nusselt.
3. `aces calibrate` turns those results into factors with `sources`
   (simlab run ids) and `valid_range` (Re, t/a, and Pr for Nusselt). It averages repeats of one operating
   condition first and needs 3 distinct conditions. It leaves out points where the baseline is not valid (the
   2300–3000 transition). It refuses a constant when the sample standard deviation of log(ratio) exceeds 0.15.
4. The next design run is calibrated. Factors used outside their fitted range raise warnings.
5. Rig test results enter the same registry with test ids as sources.
