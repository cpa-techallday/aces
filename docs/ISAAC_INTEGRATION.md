# ACES + Isaac Sim — workstation solver tier

Doc: CPA-ACES-ISAAC-001 · Rev A · 2026-10-07 · status: integrated, environment on DARKNESS not yet provisioned

Isaac Sim joins ACES as the **scene/robotics solver tier**, next to the
structures/CFD tiers on the rig and the radar EM tier (`docs/RADAR_EM.md`).
The contract mirrors the isaac-sim plugin exactly: headless only, layered
health probe first, file-based I/O, honest error semantics.

## 1. Placement: workstation only

ACES_HANDOFF excludes Isaac Sim from the rig ("the rig has no RTX renderer";
the CMP 170HX cards are headless compute cards). That decision stands. The
`aces-isaac-scene` solver therefore runs on **DARKNESS** (RTX PRO 4000
Blackwell) as a workstation-resident solver in the simlab registry, and the rig
never carries an `aces-isaac` image. GRID CONTROL modes are unaffected — Isaac
jobs do not lease rig GPUs.

## 2. What was added

| Piece | Where | What it does |
|---|---|---|
| Layered health probe | `src/aces/isaac/health.py` | GPU (nvidia-smi, degraded flag when NVML fails) → install (`ISAAC_SIM_PATH`, standard roots, `python.bat`/`python.sh` detection) → deep (boots a minimal headless `SimulationApp`, cached per install root). `aces isaac-health --deep` prints it. |
| Solver adapter | `src/aces/isaac/adapter.py` | simlab worker contract (`run(case, spec, run_dir)`, solver id `aces-isaac-scene`): generates the standalone headless script, static gate, subprocess run with wall-clock limits, parses `result.json`. |
| Gate | `validate_script()` | refuses `headless=False` or a missing `{"headless": True}`; timeout default 300 s, hard cap 3600 s, >300 s needs `allow_extended_limits` in the case. |
| Validation case | `free_fall` | drops a 0.1 m cube from 1 m onto a ground plane; checks it fell within the analytic window t = sqrt(2h/g). Required before the solver enters `solvers.json` (decision 6). |
| Scene safety | adapter | input USDs are opened read-only; `outputs: ["stage_usd"]` writes `scene_out.usd` as a new file unless the case sets `allow_scene_save`. |

## 3. Environment on DARKNESS — PROVISIONED (2026-10-07)

Isaac Sim **6.1.0.0** is installed as a pip environment at
`isaac-venv` (workspace), interpreter recorded via `ISAAC_SIM_PYTHON`.  The health
probe accepts pip installs through `ISAAC_SIM_PYTHON` in addition to workstation
roots (`ISAAC_SIM_PATH` / standard paths).  Notes from the install:

- Full bundle: `pip install "isaacsim[all,extscache]"` with
  `--extra-index-url https://pypi.nvidia.com` (the pinned
  `opencv-python-headless-noffmpeg` rc and `isaacsim.cortex.behaviors` /
  `isaacsim.replicator.behavior` extensions live there, not on PyPI).
- The two multi-hundred-MB extscache wheels exceed any single automation window;
  they were fetched with resumable `curl -C -` from files.pythonhosted.org and
  installed from local files.  pip does not resume a partially downloaded wheel.
- All packages pinned to one release (6.1.0.0); mixed 6.0.1/6.1.0 states fail the
  Kit dependency solver at boot.
- `OMNI_KIT_ACCEPT_EULA=YES` accepted non-interactively as part of the requested
  install (NVIDIA Omniverse License Agreement); the adapter and probe pass it to
  every subprocess.
- **GPU layer from automation shells stays degraded** (nvidia-smi/NVML fails in
  this context), so the probe now follows the plugin rule: a degraded GPU layer
  does not block the deep probe — the real `SimulationApp` boot arbitrates.
  CUDA itself works (CuPy 14.2 ran the job-3 CFD solve on the card).

### Trial record (2026-10-07, all real, artifacts in `pipeline-runs/`)

| # | Job | Result |
|---|---|---|
| 1 | STL → USD staging of `aeris_x64_assembly.stl` (real KiCad/Fusion export) | 4,172 vertices / 8,332 triangles, 258.9 × 247.8 × 95.3 mm → `aeris_x64_assembly.usda` (metres) |
| 2 | `aces design` on `radar_phased_array.yaml` (200 NSGA-II trials) | 194 distinct / 21 feasible / 5 non-dominated; evidence record `e4c4fbd15680`, ledger chain verified. Front shows the nominal 64 × 25 W point is thermally dominated by e.g. 156 el × 7.3 W (22.3 km at 60 W DC) |
| 3 | `aces cfd-sample` gyroid t/a 0.12, Re 20, air, n = 32 **on the workstation GPU** (CuPy) | Re 19.57, Nu_H1 5.35, converged, 40.2 s — consistent with the repo resolution study (Nu 5.53 @ n = 32, t/a 0.10). Measurement row written. CPU FP64 at n = 32 exceeds 300 s — the rig-vs-workstation split is validated by measurement |
| 4 | Isaac `free_fall` validation + staged-scene load through `aces-isaac-scene` | **PASS**: cube from 1 m rests at z = 0.049997 m (half-scale 0.05 m, −0.005 %); analytic fall time 0.4515 s inside the 10 s window. Staged AERIS USD opened and stepped 120 frames, wall 14.2 s warm |

## 4. Registration (once the environment is green)

1. `aces isaac-health --deep` passes on DARKNESS.
2. Add to simlab `solvers.json` on the workstation copy:
   `"aces-isaac-scene": {"module": "aces.isaac.adapter", "host": "workstation", "validation": "pending"}`.
3. Run the `free_fall` validation case; record the result in the registry entry
   with the probe's Isaac version, per decision 6 (no validation record, no entry).
4. Every run writes `result.json` + `measurement.json` into its run directory and
   chains into the evidence ledger like every other solver.

## 5. First use cases (queued, in order)

1. **AERIS-X64 assembly fit check** — load `aeris_x64_assembly.stl`-derived USD,
   verify board clearances against the chassis envelopes from
   `cad/aeris_quad_head.py` (16-channel head).
2. **Sensor placement / field-of-view** — camera + LiDAR prims on the radar head
   mount; coverage maps for the C-UAS emplacements.
3. **Target-drone scenario** (`aeris-target-drone` project) — a scripted quad
   trajectory against the radar's scan sector, feeding the detection-model
   assumptions in `radar.phased_array` with realistic aspect/RCS exposure times.

## 6. Boundaries

- Interactive GUI debugging, Isaac Lab RL training orchestration and large-scale
  synthetic data generation are out of scope for this tier (same boundary as the
  plugin).
- Isaac Sim is robotics/scene physics, not EM: radar RF verification stays with
  the Palace/gprMax tier (`docs/RADAR_EM.md`).
