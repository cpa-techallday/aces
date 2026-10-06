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

## 3. Current environment status on DARKNESS (2026-10-07, this session)

Reported honestly, per the probe discipline:

- **GPU: RTX PRO 4000 Blackwell, 24 GB — confirmed present and assigned to this
  tier (owner, 2026-10-07).** The probe's NVML call fails from agent shells
  ("Failed to initialize NVML: Unknown Error" from both Git Bash and PowerShell
  in the automation context) — a session-visibility issue, not missing hardware:
  the card is proven by the platform's own benchmarks and the LTX renders. The
  deep probe remains the arbiter and has not run. **VRAM budget for the tier:
  ≤ 20 GB per Isaac job** (leave 4 GB headroom for the display driver and the
  FP32 dev/preview lanes sharing the card); scenes are headless, so 24 GB is
  well above Isaac Sim's 8 GB minimum.
- **Install layer: not found.** No `ISAAC_SIM_PATH`, nothing under
  `~/isaacsim`, `~/IsaacSim`, `C:\isaacsim`, `C:\isaac-sim`, or
  `%LOCALAPPDATA%\ov\pkg\isaac-sim-*`. **This is the one real blocker.**

Until both pass, `aces-isaac-scene` raises `environment_error` with the official
guidance instead of running. To provision: install Isaac Sim 5.0+ workstation
(or set `ISAAC_SIM_PATH`), then `aces isaac-health --deep` — a green deep layer
caches per install root and the adapter will run.

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
