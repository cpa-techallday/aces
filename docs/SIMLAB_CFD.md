# Calibration CFD on the rig: `aces-tpms-periodic`

The heat-exchanger model's friction and Nusselt factors start at 1.0 and stay uncalibrated until measured.
This is the path that measures them on the rig. It needs no mesher and no commercial code. Each case is one
periodic unit cell of a TPMS core, solved with an FP64 lattice-Boltzmann method that is part of ACES
(`aces.cfd`).

```
aces cfd-campaign --id aces-cal-air-01 --out aces-cal-air-01.yaml     # simlab experiment (sweep)
   -> simlab validate / sim_submit on the rig (one case per GPU)
aces cfd-collect out/exp/aces-cal-air-01 --out cal-air-01.json         # measurement rows
aces calibrate cal-air-01.json --registry calibration.json \
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
periphery and rises linearly along the flow. With conductive metal walls this is the closest single-stream
idealisation. Conjugate heat transfer through the wall is not modelled.

A case is not a measurement, and `cfd-collect` leaves it out, when any of these holds:

- the flow does not settle (it may be unsteady at that Re);
- Mach exceeds 0.15;
- the relaxation time needed for that Re would fall below 0.505 at that resolution;
- the voxel porosity differs from the level set's by more than 5 %;
- a wall spans fewer than 2 voxels.

If the thermal boundary layers are under-resolved (the grid Péclet limit), h is left out but the pressure drop is
kept.

### The thermal boundary travels with the rows

- Every row says `boundary: uniform_heat_flux`. The adapter writes it, and `cfd-collect` adds it to older rows
  from this solver.
- `aces calibrate` takes the boundary from the rows, so no `--boundary` flag is needed. It divides the measured
  Nu by the fully developed H1 baseline (4.36) and records the boundary in the factor's basis.
- It refuses:
  - rows with mixed boundaries;
  - rows that disagree with an explicit `--boundary`;
  - Nusselt fitting from rows with h but no boundary, unless `--boundary` says which one (friction is still
    fitted).
- **The design default is unchanged.** Specs default to uniform wall temperature (D11, Nu 3.66). A factor fitted
  to H1 rows and used on a uniform-wall-temperature spec multiplies the uniform-wall-temperature baseline. For a
  long core that is factor × 3.66, 16 % below the H1 value the CFD measured. The model warns that the factor
  was fitted against a different boundary.
- **Decision for Aaron** (next free decision number): keep uniform wall temperature as the design default (D11),
  or move to H1 for conductive metal cores. Until then, set `thermal_boundary: uniform_heat_flux` in a spec only
  when that is the condition intended.

### Thermally developed samples, developing designs

A sample is periodic, so it is thermally developed by construction. The design model's laminar baseline is the
mean over the core length from circular-tube thermal-entry correlations (Hausen; Shah), which is higher than the
fully developed value when x* = L/(Dh·Re·Pr) is small. Calibration therefore divides the CFD Nu by the fully
developed baseline, and the model multiplies its developing baseline by that factor. That a TPMS core's entry
gain follows the circular-tube correlation is an assumption that the periodic samples cannot test: every result
says so, and a full-length CFD run or a rig test has to confirm it.

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

The ACES test suite also checks four more properties:

- Poiseuille flow is exact, up to the node average: 96 / (1 + 1/2h²).
- Permeability does not depend on viscosity, because TRT is used with Λ = 3/16.
- An advected shear wave follows the exact Navier–Stokes solution, which checks the inertial terms.
- A plane channel run through `periodic.run` itself, in SI units, gives f·Re = 96 (to the node average) and
  Nu_H1 = 8.235 within 1 %. This checks the path from level-set porosity, Dh and wall area to the row's Re,
  dpdx and h.

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
the high-Re cases take longest: at n = 96 and Re 300 that is several hundred thousand steps per flow solve.
`max_steps` in the params (default 2,000,000) limits **each flow solve**, not the case. A case runs up to 8 flow
solves while it iterates the force to the target Re, then one thermal solve of up to 4 × `max_steps`, so one case
can run up to 24,000,000 steps. A solve that hits its limit is reported as not converged, not as a
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
  - `aces-cal-air-02p` (primitive Mach probe: Re 1/3/10 — every campaign-1 primitive case exceeded
    Mach 0.15, so its factors were never fitted; the probe finds the Mach-safe Re range)
  - `aces-cal-air-02r` (wall-ratio extension: gyroid + diamond at t/a 0.20/0.24/0.28, Re 10/30/100 —
    widens the calibrated 0.08–0.16 range)
- **Run state (2026-10-09, evening):** validation, resolution, `aces-cal-air-01` and campaign 2
  (`aces-cal-air-02p`/`02r`) are all complete and fitted — the registry holds 9 factors (D30),
  including primitive's first (valid at Re <= ~6 only: Mach/Re ~ 0.023). Corpus v2 (40 rows) and
  Tier-0 dataset v2 rebuilt from the combined measurements.

```bash
docker exec -w /work simlab-queue-1 python -m simlab enqueue cases/aces/aces-cal-air-02p.yaml --docker
docker exec -w /work simlab-queue-1 python -m simlab enqueue cases/aces/aces-cal-air-02r.yaml --docker
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
measurement) in each run folder. `cfd-collect` keeps a case only when all of these hold:

- `status.json` says FINISHED, and there is a `measurement.json`;
- `evidence.json` is a JSON object whose experiment spec (its `spec` member, or the top level) names this
  experiment (`id` = the folder name) and the solver `aces-tpms-periodic`;
- any `case_id` in it names this case, and every sample parameter the spec fixes (`params`) or lists (`sweep`)
  agrees with the row;
- the row is H1 and passes the schema `aces calibrate` applies.

Each kept row records the sha256 of its `evidence.json` (`evidence_sha256`). The `spec` layout is inferred from
the simlab hand-off ("evidence.json holds the spec") and the adapter contract; confirm it against simlab's writer
on the first rig run. If it differs, `cfd-collect` keeps nothing and says why, rather than accepting rows.
