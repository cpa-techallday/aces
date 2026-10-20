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
   Nusselt and friction use straight-duct baselines. The laminar default is uniform wall temperature (D11),
   the conservative choice.
   - **Thermal entry.** Laminar heat transfer uses the mean Nusselt number over the core length, from the
     thermal entry length x* = L / (Dh·Re·Pr) (Graetz number Gz = 1/x*). Uniform wall temperature uses Hausen
     (1943): Nu = 3.66 + 0.0668·Gz / (1 + 0.04·Gz^(2/3)). Uniform heat flux uses Shah (1975):
     Nu = 1.953·x*^(−1/3) up to x* = 0.03, and 4.364 + 0.0722/x* above. Both are circular-tube correlations for
     a developed velocity profile, valid for Re < 2300 at any x*, and tend to 3.66 and 4.36 for a long core. Each
     result reports x*, Gz, the fully developed Nu and the correlation used, and warns when x* < 0.05.
   - **Calibration.** The Nusselt factor is fitted to periodic, thermally developed CFD against the fully
     developed baseline. It multiplies the developing baseline above.
   - Overall UA is a series resistance, and the counterflow e-NTU gives duty and outlet temperatures. Pressure
     drop is Darcy over the flow path, fully developed.
   - **Walls.** `wall_min_m` and `wall_mean_m` are the thinnest and the area-weighted mean wall, both measured
     along the mid-surface normal. `wall_conduction_m` (solid volume ÷ mid-surface area) is what the wall
     conduction term uses. It is a different quantity: on saddle-shaped TPMS walls it is smaller than the
     normal thickness.
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

Every result states these caveats (`ALWAYS_WARN` in `domains/hx/model.py`):

- headers, manifolds and flow maldistribution are not modelled;
- the correlations are straight-duct ones applied to a TPMS core;
- laminar heat transfer uses circular-tube thermal-entry correlations, and the calibration factor (from fully
  developed CFD) multiplies them;
- the hydrodynamic entrance (pressure drop is fully developed), wall roughness and axial wall conduction are not
  modelled.

Compressibility is flagged on a result when a pressure drop exceeds 10 % of the inlet pressure.

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

For the rod route, the study's frame FEA checks each boom's chords against a nominal 60 mm rod.
`rodcheck.py` maps each member's forces onto every chord, batten and diagonal and checks each rod at its own
length; that check governs the route. ARC-1 rev A fails it at 1.68, in the links at the loaded lower front
pickups. Rev B (`params_revB.json`: a second link per quadrant there, 52 mm pickup booms) passes at 0.93 and
is 22 % stiffer in torsion, for 5 kg more lattice.

## The learning loop

1. `aces cfd-campaign` writes a simlab sweep of periodic unit-cell samples (TPMS × t/a × Re) for the rig's
   `aces-tpms-periodic` solver. That solver is ACES's own FP64 lattice-Boltzmann method (`aces.cfd`), validated
   against exact duct results; see [SIMLAB_CFD.md](SIMLAB_CFD.md). `aces export-simlab` still exports the
   geometry of shortlisted cores for other solvers.
2. simlab runs one case per GPU. Each case writes a measurement row: dpdx, mass flux and H1 heat transfer
   coefficient, on the design model's own definitions.
3. `aces cfd-collect` gathers the finished rows whose `evidence.json` names their experiment and agrees with
   them, and records its sha256 in each row. `aces calibrate` turns them into factors with `sources` (simlab run
   ids), `valid_range` (Re, t/a, and Pr for Nusselt) and `fitted_on`; the registry refuses a factor without all
   three. It takes the thermal boundary from the rows (CFD rows are H1, `uniform_heat_flux`) and refuses mixed
   or contradicted boundaries. It averages repeats of one operating condition first and needs 3 distinct
   conditions. It leaves out points where the baseline is not valid (the
   2300–3000 transition). If constant-factor scatter exceeds 0.15, it tries a power law in Re, then an
   inertial-onset form, and refuses if neither fits. If the constant is within the limit, a Re-dependent form is
   selected only when it also fits within the limit and improves the small-sample-corrected AIC by at least 2.
4. The next design run is calibrated. Factors used outside their fitted range raise warnings.
5. Rig test results enter the same registry with test ids as sources.
