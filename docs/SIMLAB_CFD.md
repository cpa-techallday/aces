# Calibration CFD on the rig: `aces-tpms-periodic`

The heat-exchanger model's friction and Nusselt factors start at 1.0 and stay uncalibrated until measured.
This is the path that measures them on the rig. It needs no mesher and no commercial code. Each case is one
periodic unit cell of a TPMS core, solved with an FP64 lattice-Boltzmann method that is part of ACES
(`aces.cfd`).

```
aces cfd-campaign --id aces-cal-air-01 --out aces-cal-air-01.yaml     # simlab experiment (sweep)
   -> simlab validate / sim_submit on the rig (one case per GPU)
aces cfd-collect out/exp/aces-cal-air-01 --out cal-air-01.json         # measurement rows
aces calibrate cal-air-01.json --registry calibration.json --boundary uniform_heat_flux \
     --project ... --owner ... --rd-activity ...                        # factors + evidence record
```

## What a case computes

Each case is one unit cell of fluid network A, periodic in all directions, driven along x by a body force. The
force is iterated until the Reynolds number hits the target, using the design model's own definition: interstitial
velocity, and hydraulic diameter measured on the level set.

| Output | Definition (same as the design model) |
| --- | --- |
| `dpdx_Pa_m` | mean pressure gradient: pressure drop per metre of core |
| `mass_flux_kg_m2s` | ρ × superficial velocity: per frontal area of the core |
| `h_W_m2K` | heat taken up per cell ÷ (level-set wall area × (T_wall − T_bulk)) |

The heat transfer is the thermally developed **H1** condition: the wall temperature is uniform around the
periphery and rises linearly along the flow. Fit Nusselt factors from these rows with
`--boundary uniform_heat_flux`. With conductive metal walls this is the closest single-stream idealisation.
Conjugate heat transfer through the wall is not modelled.

A case is not a measurement, and `cfd-collect` leaves it out, when any of these holds:

- the flow does not settle (it may be unsteady at that Re);
- Mach exceeds 0.15;
- the relaxation time needed for that Re would fall below 0.505 at that resolution;
- the voxel porosity differs from the level set's by more than 5 %;
- a wall spans fewer than 2 voxels.

If the thermal boundary layers are under-resolved (the grid Péclet limit), h is left out but the pressure drop is
kept.

## Validation (registry record)

`python -m aces.cfd.selftest` runs two cases against exact fully developed laminar results (Shah & London, 1978):

| Case | f·Re exact | Nu_H1 exact |
| --- | --- | --- |
| square duct | 56.91 | 3.608 |
| plane channel | 96 | 8.235 |

Both converge at second order. At 16 cells across, on the CPU:

| Case | f·Re error | Nu_H1 error |
| --- | --- | --- |
| square duct | −0.37 % | +1.06 % |
| plane channel | −0.19 % | +0.31 % |

The ACES test suite also checks three more properties:

- Poiseuille flow is exact, up to the node average: 96 / (1 + 1/2h²).
- Permeability does not depend on viscosity, because TRT is used with Λ = 3/16.
- An advected shear wave follows the exact Navier–Stokes solution, which checks the inertial terms.

## Resolution: use n ≥ 96 on the rig

This was measured for a gyroid with t/a = 0.10, at Re 19.6, in air. The n = 48 and 64 runs were on the
workstation GPU in the `aces-cfd` image (64 took 390 s there; the workstation runs FP64 at 1/64 rate).

| n (voxels per cell) | friction ratio | Nu_H1 |
| --- | --- | --- |
| 24 | 3.483 | 5.619 |
| 32 | 3.611 | 5.526 |
| 48 | 3.4292 | 5.417 |
| 64 | 3.4293 | 5.389 |

- **Friction:** settles from n = 48. The two finest grids agree to 0.003 %.
- **Nusselt:** converges at second order. Extrapolating from 48 and 64 puts the n = 64 value about 0.7 % high.
- **Coarse grids:** below about 5 voxels across the wall, the voxel staircase moves the effective wall by up to
  half a voxel. The friction ratio then wanders by ±5 % (24 and 32 above), so treat those grids as development
  only.

Calibration runs use n = 96, which gives at least 7 voxels across a t/a = 0.08 wall. Each case declares about
4 GB of device memory (measured peak with a 1.5x margin). The first rig campaign should still run one case at
n = 64, 96 and 128 to bound the error on the rig.

Interpolated walls (Bouzidi), using the level set's distance along each link, would remove the staircase noise
and allow coarser, faster grids. They are the next solver improvement.

Run time on the rig is not measured yet. The time to reach a steady state scales with Dh²/ν in lattice units, so
the high-Re cases take longest: at n = 96 and Re 300 that is several hundred thousand steps. Cases default to
2,000,000 steps (`max_steps` in the params). A case that hits the limit is reported as not converged, not as a
measurement. The validation run on the rig gives the first timings; put the Re 300 cases in a separate
experiment if they hold the queue.

The low-resolution sweeps also show how the ratios depend on Reynolds number (t/a = 0.12, n = 20, air):

- **Friction ratio:** about 3.0 in creeping flow (Re 1–5). It rises to 3.6 at Re 20 and 5.6 at Re 60.
- **Nu ratio:** 1.28 at low Re, rising to 1.79 at Re 60.

A constant factor cannot represent this. `aces calibrate` therefore tries a power law, then an inertial-onset form
`value × (1 + (Re/Re_onset)^n)`, and keeps the simplest that fits. On that sweep, the onset form leaves a
residual of 0.010 (sd of log) for friction and 0.002 for Nusselt.

## On the rig (2026-10-07)

- **Image:** `aces-cfd:0.1` was built on the rig, offline, from `gendesign:0.3` and two wheels
  (`docker/aces-cfd.offline.Dockerfile`, `scripts/rig/install_aces_cfd.sh`). Image id `sha256:c95bff6e9d76…`.
  The CPU self-test passes there.
- **Registry:** `aces-tpms-periodic` is in `/opt/simlab-runtime/simlab/solvers.json` and in the workstation copy:
  - `scripts/rig/register_solver.py` backed up each file first and added only the new key;
  - `scripts/rig/check_registry.sh` confirmed that no other key changed;
  - `simlab solvers` lists it.
  - The validation record holds the workstation GPU numbers. It says the rig GPU run is pending.
- **Specs:** in `/opt/simlab-runtime/cases/aces/` (copies in `specs/simlab/`), all parsed by `simlab validate`:
  - `aces-cfd-validation`
  - `aces-cfd-resolution`
  - `aces-cal-air-01`
- **Not run yet:** the rig is in mode `general-flagship`, so the simulation pool is empty and every card holds LLM
  weights. In a `sim-lab` mode, run these in order:

```bash
docker exec -w /work simlab-queue-1 python -m simlab enqueue cases/aces/aces-cfd-validation.yaml --docker
docker exec -w /work simlab-queue-1 python -m simlab enqueue cases/aces/aces-cfd-resolution.yaml --docker
# after storage is sorted (docs/WORKFLOW.md section B):
docker exec -w /work simlab-queue-1 python -m simlab enqueue cases/aces/aces-cal-air-01.yaml --docker
```

## Building and registering from scratch

The image is built on the workstation and shipped, like every simlab image:

```powershell
cd C:\Users\shedm\aces
.\.venv\Scripts\python.exe -m pip wheel . --no-deps -w dist
docker build -f docker/aces-cfd.Dockerfile -t aces-cfd:0.1 .
docker run --rm --gpus all --entrypoint python3 aces-cfd:0.1 -m aces.cfd.selftest
```

Once it is shipped and loaded on the rig, the server agent does three things:

1. Run the self-test there.
2. Add the registry entry below to `simlab/solvers.json`, with the rig's self-test numbers and the image digest.
3. Submit the validation experiment (`aces cfd-campaign --validation`) before any calibration campaign.

```json
"aces-tpms-periodic": {
  "module": "aces.cfd.simlab_adapter",
  "physics": ["cfd"],
  "precision": ["float64"],
  "image": "aces-cfd:0.1",
  "description": "ACES periodic TPMS calibration samples: FP64 D3Q19/D3Q7 TRT lattice Boltzmann, body-force driven, H1 heat transfer",
  "validation": {"status": "pass", "date": "<rig run date>", "device": "<rig GPU>", "dtype": "float64",
                 "cases": ["<selftest JSON lines>"],
                 "record": "<simlab experiment id of the validation run>"}
}
```

The adapter refuses `float32`. It writes `sample.json` (everything) and `measurement.json` (only for a valid
measurement) in each run folder. `cfd-collect` keeps only cases with `status.json` FINISHED, a
`measurement.json`, and an `evidence.json`.
