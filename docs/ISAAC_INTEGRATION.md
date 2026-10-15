# ACES + Isaac Sim — workstation solver tier

Doc: CPA-ACES-ISAAC-001 · Rev B · 2026-10-07 · status: integrated, environment on DARKNESS not yet provisioned

Rev B (audit 2026-10-07, I1–I4): the free-fall validation measures a fall time; results are labelled
float32; Isaac runs in its own process group with a minimal environment; scene and save paths are confined
to the run folder.

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
| Layered health probe | `src/aces/isaac/health.py` | GPU (nvidia-smi, degraded flag when NVML fails) → install (`ISAAC_SIM_PATH`, standard roots, `python.bat`/`python.sh` detection) → deep (boots a minimal headless `SimulationApp`, cached per install root). If the GPU or install layer fails, the deep layer is reported as failed ("deep probe not run: …") with the reason. `aces isaac-health --deep` prints it. |
| Solver adapter | `src/aces/isaac/adapter.py` | simlab worker contract (`run(case, spec, run_dir)`, solver id `aces-isaac-scene`): generates the standalone headless script, static gate, runs it through `aces.proc.run` with wall-clock limits, parses `result.json`. |
| Gate | `validate_script()`, `Adapter.run()` | refuses `headless=False` or a missing `{"headless": True}`; timeout default 300 s, hard cap 3600 s, >300 s needs `allow_extended_limits` in the case; refuses a float64 request or `kind: verification` (see Precision). |
| Validation case | `free_fall` | drops a 0.1 m cube (centre at 1.0 m) onto a ground plane at z = 0 with g = 9.80665 m/s², records z(t) every physics step at dt = 1/240 s, and takes the contact time where the bottom face reaches the plane (linear interpolation). Passes only if that is within 5 % of t = √(2d/g), d = 0.95 m (0.440 s), the run finished and the cube is at rest at the end (below 1 mm/s over the last 0.1 s). Required before the solver enters `solvers.json` (D5 in `docs/DECISIONS.md`: a registered simlab solver signs off). |
| Scene safety | adapter | `scene_usd` must resolve to a file inside the run folder; paths outside it, and links, are refused. The input USD is never saved over: `outputs: ["stage_usd"]` writes a new `scene_out.usd` in the run folder, and `allow_scene_save` is refused. `result.json` and `scene_out.usd` are created exclusively; `sim_script.py` and `measurement.json` go through `aces.safeio`. |
| Not yet supported | adapter | `robot_asset` is refused: no robot is spawned yet. Build the robot into `scene_usd` instead. |

### Precision

PhysX integrates in float32. Every result and validation record carries `precision: float32` and a warning
that it is for scene and robotics checks only. The adapter refuses `precision: float64` (in the spec's
resources or the case) and `kind: verification`: verification solves need FP64 (CLAUDE.md rule 6) and a
float64 simlab solver. Submit Isaac experiments with `precision: float32` and a kind such as `scene` or
`validation`.

### Processes and environment

Isaac and the health probes run through `aces.proc.run`, so a timeout kills the whole process group (the
launcher script and the Kit process under it), not just the shell. The child gets `aces.proc`'s minimal
environment plus `ISAAC_ENV_KEEP` in `health.py`: `ISAAC_SIM_PATH`, the EULA acceptance variables
(`OMNI_KIT_ACCEPT_EULA`, `ACCEPT_EULA`), GPU selection (`CUDA_VISIBLE_DEVICES`, `NVIDIA_*`, `CUDA_PATH`),
the driver and Vulkan loader paths, the XDG cache and config folders, the user name and the Windows system
folders. Nucleus credentials and other secrets are not passed.

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
- `OMNI_KIT_ACCEPT_EULA=YES` was accepted non-interactively as part of the requested
  install (NVIDIA Omniverse License Agreement). ACES never sets it itself: the adapter
  and probe pass it through only when it is set in the operator's environment
  (set it once in DARKNESS's user environment). See section 6 before any defence use.
- **GPU layer from automation shells stays degraded** (nvidia-smi/NVML fails in
  this context), so the probe now follows the plugin rule: a degraded GPU layer
  does not block the deep probe — the real `SimulationApp` boot arbitrates.
  CUDA itself works (CuPy 14.2 ran the job-3 CFD solve on the card).
- **VRAM budget for the tier: ≤ 20 GB per Isaac job** (leave 4 GB headroom for the display driver and the
  FP32 dev/preview lanes sharing the card); scenes are headless, so 24 GB is well above Isaac Sim's 8 GB
  minimum.

### Trial record (2026-10-07, all real, artifacts in `pipeline-runs/`)

| # | Job | Result |
|---|---|---|
| 1 | STL → USD staging of `aeris_x64_assembly.stl` (real KiCad/Fusion export) | 4,172 vertices / 8,332 triangles, 258.9 × 247.8 × 95.3 mm → `aeris_x64_assembly.usda` (metres) |
| 2 | `aces design` on `radar_phased_array.yaml` (200 NSGA-II trials; the template's built-in losses and efficiencies were removed afterwards as unsourced, audit R3, so this run can't be repeated until sourced values are supplied) | 194 distinct / 21 feasible / 5 non-dominated; evidence record `e4c4fbd15680`, ledger chain verified. Front shows the nominal 64 × 25 W point is thermally dominated by e.g. 156 el × 7.3 W (22.3 km at 60 W DC) |
| 3 | `aces cfd-sample` gyroid t/a 0.12, Re 20, air, n = 32 **on the workstation GPU** (CuPy) | Re 19.57, Nu_H1 5.35, converged, 40.2 s — consistent with the repo resolution study (Nu 5.53 @ n = 32, t/a 0.10). Measurement row written. CPU FP64 at n = 32 exceeds 300 s — the rig-vs-workstation split is validated by measurement |
| 4 | Isaac `free_fall` validation + staged-scene load through `aces-isaac-scene` | **PASS under the old check** (re-run needed: the validation now needs the contact time within 5 % and a free-fall path, audit I1): cube from 1 m rests at z = 0.049997 m (half-scale 0.05 m, −0.005 %); analytic fall time 0.4515 s inside the 10 s window. Staged AERIS USD opened and stepped 120 frames, wall 14.2 s warm |

## 4. Registration (once the environment is green)

1. `aces isaac-health --deep` passes on DARKNESS.
2. Run the `free_fall` validation case (`precision: float32`). It writes `measurement.json`
   (the validation record: g, cube size, start height, fall distance, analytic and measured
   contact times, error, `converged`, `pass`, `precision: float32`).
3. Only if `pass` is 1, add to simlab `solvers.json` on the workstation copy, with the probe's
   Isaac version and the validation record (D5: no validation record, no entry):
   `"aces-isaac-scene": {"module": "aces.isaac.adapter", "host": "workstation", "precision": "float32", "validation": "<measurement.json result>"}`.
4. Every run writes `result.json` into its run directory (validation runs also `measurement.json`)
   and chains into the evidence ledger like every other solver.

## 5. First use cases (queued, in order)

1. **AERIS-X64 assembly fit check** — load `aeris_x64_assembly.stl`-derived USD,
   verify board clearances against the chassis envelopes from
   `cad/aeris_quad_head.py` (16-channel head).
2. **Sensor placement / field-of-view** — camera + LiDAR prims on the radar head
   mount; coverage maps for the C-UAS emplacements.
3. **Target-drone scenario** (`aeris-target-drone` project) — a scripted quad
   trajectory against the radar's scan sector, feeding the detection-model
   assumptions in `radar.phased_array` with realistic aspect/RCS exposure times.

## 6. Licence

Isaac Sim is used under NVIDIA's licence (EULA), not an open-source licence. Its entry is in
`docs/LICENCES.md` (restricted: no defence use until the legal review is recorded). ACES does not redistribute Isaac Sim and no ACES
image carries it. ACES never accepts the EULA for anyone: the acceptance variables reach Isaac only
if the operator has set them. The C-UAS and AERIS use cases in section 5 wait for the legal review
of the EULA that the audit asks for (military use and export terms).

## 7. Boundaries

- Interactive GUI debugging, Isaac Lab RL training orchestration and large-scale
  synthetic data generation are out of scope for this tier (same boundary as the
  plugin).
- Isaac Sim is robotics/scene physics, not EM: radar RF verification stays with
  the Palace/gprMax tier (`docs/RADAR_EM.md`).
