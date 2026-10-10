# aces-engineering

The desktop side of the ACES engineering loop: validate experiment specs, run the voxel kernel and gendesign's CPU
paths, write reports, build and verify evidence bundles, and talk to the rig's simlab and rig-state MCP servers.
GPU solving stays on the rig. The code is a vendored snapshot of `cudax-sim-runtime` (simlab and gendesign); nothing
in that repository is patched: where a module needs a rig-only or GPU package, the wrappers here keep it off the
desktop command line or wrap it, as listed below.

## Layout

| Path | What |
|---|---|
| `/opt/aces/engineering/venv` | one locked Python 3.14 venv: `requirements.txt` pins all 77 distributions with sha256 hashes. 0.1.0-2 (76, no gmsh) measured 907.6 MiB installed per pacman (982 MiB by du), package 255 MB; 0.1.0-3 adds the gmsh wheel (40,006,930 bytes, a 92,641,960-byte `libgmsh.so.4.15` inside); 0.1.0-4 (the integration) measured 2026-10-10 in the aces-builder container: venv 1.1G (78 distributions), package 295,086,073 bytes |
| `/opt/aces/engineering/lib` | the vendored `simlab` and `gendesign`, the `aces_engineering` wrappers, `aces_engineering_site.py` (put on the venv's path by `aces-engineering.pth`) |
| `/opt/aces/engineering/SNAPSHOT` | source path and commit, sync time, content sha256 and per-file manifest of the vendored code |
| `/usr/bin/aces-simlab`, `aces-evidence`, `aces-gendesign` | shims that run `python -m aces_engineering.<cli>` in the venv |
| `/usr/bin/aces-golden` | the golden path (`aces golden ...` through aces-base's passthrough): `python -m aces_engineering.golden_cli` in the venv |
| `/usr/share/aces/engineering/mcp.json.example` | MCP client entries for the rig: simlab `:8022/mcp` (bearer `${SIMLAB_MCP_TOKEN}`) and rig-state `:8010/mcp` |
| `/usr/share/aces/engineering/examples` | generic structural cases from `cases/` (cantilever, wall bracket, beam validation, L-bracket parametric study) |

## Commands

| Command | What it does |
|---|---|
| `aces-simlab validate SPEC` / `run SPEC --out DIR` | parse an `experiment.yaml`; run its cases here with local workers (NumPy, FP64) |
| `aces-simlab solvers` / `summary DIR` / `report DIR` | registry with validation records; summary table; md/html/docx (pdf with LibreOffice) |
| `aces-simlab surrogate fit-tabular\|loop\|predict` | GP / extra-trees surrogates on the CPU (`train-fno` needs torch) |
| `aces-simlab export ...` | Fusion to STEP + parameter snapshot + `experiment.yaml` over the Fusion bridge on `:9876` |
| `aces-simlab capacity [--local]` | GPU truth table from rig-state and GRID CONTROL (URLs from `/etc/aces/aces.toml`); leases are not readable from the desktop |
| `aces-simlab remote info\|tools\|call TOOL '{json}'` | MCP client for `--server simlab` (default) or `--server rig-state` |
| `aces-evidence verify [ROOT]` / `bundle FILE` / `ls` / `build EXP_DIR` | ledger chain and bundle hashes (relocates ledgers copied from another machine) |
| `aces-gendesign list` | evaluators, solver adapters and what runs on this machine |
| `aces-gendesign selftest` | the packaged self-test (same as the package build's `check()`); its `conforming` check runs the C3D10 bar patch test through gmsh and ccx (PASS when exact to 1e-6, INFO `not run: ccx not found` without ccx) |
| `aces-gendesign topopt\|parametric\|nozzle\|fusion ...` | SIMP, Optuna studies, Angelino aerospike contour, push STL/STEP to Fusion |
| `aces golden run --demo [--json]` | the golden path end to end on the built-in root mount (below); exit 0 only when verification passed and both ledgers verify |
| `aces golden run --spec FILE --project P [--new]` / `--project P` | the same on your own spec (`$ACES_WORK/P/golden/spec.json` when no `--spec`) |
| `aces golden run ... --lane sim/pool [--rig-sweep] [--rig-budget S]` | send the independent check and the conforming check (and with `--rig-sweep` the sweep) to the rig through simlab-mcp; `--lane none` forces this desktop (below) |
| `aces golden run ... --conforming auto\|rig\|local\|off` / `--require-grade G` | where the conforming tet10 check runs (default `auto`); exit 1 when a PASS has a grade below G |
| `aces golden compute [--json] [--lane LANE\|none]` | what would compute where: CPUs, CuPy GPU, Warp, torch/PhysicsNeMo, gmsh and ccx for the conforming check, rig lanes, and the plan; computes nothing. With a lane it probes simlab-mcp and prints `rig:` lines (URL, server, principal, policy, token source, tools, solvers with image, validation and code digest, mode, pool and free GPUs); exit 0 when the lane is usable, 2 when not |
| `aces golden init P --step part.step` | create project P, copy the STEP to `P/cad/`, record it as a CAD source, write a spec template to fill in |
| `aces golden spec --demo root-mount --out spec.json` / `--schema` | the demo spec as a starting point; the JSON schema of `aces-golden-spec/1` |
| `aces golden show [RUN] [--json] [--bench-out FILE]` | list runs (newest first) or show one; `--bench-out` appends its wall-time row to a benchmarks file |
| `aces golden verify [RUN] [--json] [--no-model] [--resolve]` | audit a run with 13 checks (below): anchored files, bundle, both ledgers, sweep table, surrogate models, verdict, report provenance, compute-used rows, rig records, the conforming check, manifest, resolve; never contacts the rig |
| `aces golden report RUN` | rebuild `report.*` and `manifest.sha256` from the run's records |

`aces-simlab capacity` without `--local` and every `aces-simlab remote` call go to the rig: `remote call sim_submit`
queues real GPU work there, and the simlab server audits every call against the token's principal.

## Dependencies

| Class | Packages | State |
|---|---|---|
| Core (installed) | numpy, scipy, scikit-image, trimesh, rtree, optuna, matplotlib, PyYAML, pydantic, python-docx, Markdown, scikit-learn, joblib, build123d, cadquery-ocp-novtk, embreex, gmsh (77 distributions with their dependencies) | pinned in `requirements.txt`, versions as recorded on the workstation (`beam-validation-v1` evidence); gmsh 4.15.2 since 0.1.0-3, for the conforming tet10 mesh (its `libgmsh` needs the X11/GL/fontconfig libraries in the PKGBUILD's `depends`) |
| Separate package (optional) | `calculix-ccx-bin` (CalculiX ccx 2.23, the author's binary, `/usr/bin/ccx`) | the solver of the conforming check; without it the check is recorded as not run (`ccx-not-found`). `ACES_CCX=/path/to/ccx` points at another binary |
| Optional GPU (not installed) | cupy-cuda12x, warp-lang, torch | imported lazily: `fea3d`/`topopt3d` fall back to NumPy; `lattice_opt`, `warp_fea` and the FNO surrogate need them |
| Rig-only (not installed) | rq, redis, uvicorn, mcp (SDK; pulls uvicorn and starlette), starlette, physicsnemo, rocketcea | used by the queue, leases, MCP server and console, and by the nozzle's NASA CEA path |
| Optional (not installed) | mlflow | `simlab.tracking` warns once and becomes a no-op; mlflow-skinny 3.x pulls fastapi, starlette and uvicorn |

GPU extras on a desktop with an NVIDIA GPU (untested on ACES OS; TODO: verify the CUDA runtime libraries cupy needs):
`sudo /opt/aces/engineering/venv/bin/pip install --only-binary=:all: cupy-cuda12x==14.2.0 warp-lang==1.18.0 numpy==2.4.6`
(versions from the workstation venv). pacman does not track those files: `pip uninstall` them before removing the package.

## Rig-only modules and how the wrappers handle them

| Vendored module | Imports at import time | Desktop handling |
|---|---|---|
| `simlab.queue`, `simlab.leases` | rq, redis | `aces-simlab enqueue/worker/workers/qstatus/qresults/qcancel` exit 2 and point at `aces-simlab remote call sim_submit/sim_status/...` |
| `simlab.mcp_server` | mcp, uvicorn, rq, redis | `aces-simlab mcp` exits 2; the server runs on the rig |
| `simlab.console` | starlette | not vendored (the console runs on the rig at `:8032`; `aces-console simlab_console`) |
| `simlab.capacity` (via `simlab capacity`) | the command imports `simlab.leases` | `aces-simlab capacity` calls `simlab.capacity.gpu_table` directly with no leases |
| `gendesign.lattice_opt`, `gendesign.warp_fea` | cupy, warp inside functions | importable; running them needs the GPU extras |
| `simlab.solvers.pyfr_solver`, `simlab.meshing` | gmsh (installed since 0.1.0-3), PyFR (not installed) inside functions | importable; run in the `simlab-pyfr` image on the rig |
| `simlab run --docker` | docker + a repo checkout mounted at `/work` | refused by the wrapper; submit to the rig instead |

## Golden path (`aces golden`)

Parts are computed, not drawn. A design space (a STEP file, or a build123d function) with its supports, loads,
keep-in interfaces and material goes in; what comes out is an organic part: each design is a SIMP topology
optimisation (`gendesign.topopt3d.run_case`) turned into an implicit signed-distance skin (smooth unions with the
preserved interfaces, clipped to the design space, Taubin-smoothed; a gyroid-sheet core inside a shell in the `bone`
variant), and every design is solved on the real voxel FEA (the step-structures adapter, H8, Jacobi-PCG, FP64). The
built-in demo is a six-foot root mount computed inside a disc 100 mm across and 40 mm tall (`aces golden spec --demo`
prints it; the disc is the input domain only).

`aces golden run` runs nine stages and says what it is doing on stderr (a bar on a terminal, plain lines otherwise,
JSON lines with `--progress jsonl`, a heartbeat from the solvers' progress beacons when nothing else was printed for
10 s):

1. **compute**: detect what can run here (CPU workers and BLAS, the kernel self-test, a CuPy GPU with a patch test,
   Warp, torch and PhysicsNeMo, gmsh and CalculiX `ccx` for the conforming check, Code_Aster, the rig's simulation
   lanes from the fabric manifest); with a lane, probe its simlab-mcp before anything else is computed.
2. **inputs**: the spec as given and as resolved, the design-space STEP, the grid, the compute plan.
3. **domain**: the design space voxelised through the step-structures adapter.
4. **sweep**: N designs (Latin hypercube over the generator's variables, seeded), each generated, skinned and solved.
5. **loop**: a GP surrogate fitted to the sweep (K-fold error per output), the predicted-feasible optimum with
   margins, and that design solved for real; up to three rounds.
6. **check**: the winner re-solved at a finer resolution (on the GPU when one is usable, else on the CPU within a time
   budget; with a lane, on the rig's GPUs), re-solved on CuPy and on Warp, when they are usable, for device parity,
   and solved a second time on a conforming tet10 mesh with CalculiX (below).
7. **export**: the winner's STL (the mesh that was solved), renders, density and field slices, the sweep gallery, the
   compute-used table.
8. **evidence**: every anchored file hashed (`evidence/files.sha256`), a simlab evidence bundle chained into the
   per-user simlab ledger (`~/.local/share/aces/simlab/evidence`), a `golden.run` entry in the aces ledger, and the
   run recorded in the project's `aces.yaml`.
9. **report**: `report.md`, `report.html`, `report.json` (every number in the verdict line and the tables has a
   provenance pointer to the record it came from).

The run directory is `$ACES_WORK/<project>/golden/<run-id>/` (`~/Work/...`; the demo uses project `golden-demo`).
The run id is a UTC stamp and six hex digits of the inputs' hash, so the same spec, design space and seed give the
same suffix on any machine. `result.json` there is exactly what `--json` prints; `run.json` has the stages and wall
times; `log.txt` collects everything libraries printed; `manifest.sha256` covers the rest.

Spec rules the grids enforce (exit 2 before anything is solved): every face of every support and load box sits on the
grid that `gen_mm`, `fea_mm` and each planned check level share, so the generator, the solves and the independent check
fix and load the same patch (the refusal names the nearest box on that grid); a traction plane is an envelope face (the
vendored traction loads both sides of an interior plane); a spec has one load case (v0: one spec per case); the seed is
a 32-bit unsigned integer. The stress-exclusion width is rounded up to the grid of `fea_mm` and the check levels, so
every resolution ignores the same zone (`domain/domain.json` records the effective width; the demo's 6 mm becomes 8 mm).

### What runs where

Nothing is sent to the rig unless a lane is chosen: `--lane sim/pool` on the command line, or `lane = "sim/pool"` in
the `[golden]` table of `aces.toml` (`/etc/aces/aces.toml`, then `~/.config/aces/aces.toml`, which wins); `--lane
none` or `lane = "none"` keeps everything here. Lanes come from the fabric manifest (`aces fabric refresh`), and a
lane's simlab-mcp URL is its `endpoint`, else `server.simlab_mcp`.

| Work | No lane | `--lane sim/pool` | `--lane sim/pool --rig-sweep` |
|---|---|---|---|
| sweep designs | this desktop | this desktop | rig GPUs (`golden-case`) |
| surrogate, verification rounds, device parity, export, evidence, report | this desktop | this desktop | this desktop |
| independent check, levels 1 and 2 | this desktop (the GPU, or the CPU within the budget) | rig GPUs (`golden-check`) | rig GPUs (`golden-check`) |
| conforming check (`--conforming auto`) | this desktop, when gmsh and ccx are usable | rig CPUs (`ccx-check`, 16 threads) | rig CPUs (`ccx-check`) |

The rig runs the registry's `aces-golden:0.1` image. The probe compares the golden code that image carries (its
compute digest in the rig registry) with this desktop's and refuses the lane when they differ. When the rig does not
deliver level 1, this desktop runs it as `x101`; when it does not deliver the conforming case, or returns it not run,
`--conforming auto` runs it here as `m101` if gmsh and ccx are usable. Level 2, rig sweep designs and a conforming
check that failed on the rig never fall back. A check level above 2,000,000 cells (`CHECK_MAX_CELLS`) is not run on
the rig.

`--conforming rig` runs the conforming check only on the rig (it needs a lane), `local` only here (exit 2 when gmsh
or ccx is not usable), `off` not at all. `--rig-budget S` (default 1800) bounds each rig experiment from its
submission to its last pulled file; then the client cancels what is left (when the server offers `sim_cancel`) and
those cases are not delivered (`rig-timeout`). The rig's GPU mode is never changed from here: switching modes is a
human act in GRID CONTROL, and a mode that releases no simulation GPU refuses the lane.

**A lane that cannot be used** stops the run with exit 2 before anything is computed, and leaves no run directory:
`lane '<lane>' cannot be used: <reason>; nothing was submitted` (with `--json`, exactly one JSON document). The
reasons, checked in order: no fabric manifest; the lane is not in it, is not a simulation lane, or has no simlab-mcp
endpoint; simlab-mcp did not answer, or refused the token (HTTP 401); it lacks tools or registry solvers the run
needs; the rig registry does not record the image's golden code, or records other code; the rig's mode releases no
GPU; both checks are off. A lane that comes from `aces.toml` only warns (`warning: rig lane <L> (from aces.toml)
unavailable: <reason>; computing on this desktop`) and the run continues here.

**The token**: `ACES_SIMLAB_TOKEN`, else `SIMLAB_MCP_TOKEN`, else an `ACES_SIMLAB_TOKEN=...` (or
`SIMLAB_MCP_TOKEN=...`) line in `~/.config/aces/simlab.env` (`$ACES_USER_CONFIG/simlab.env`; `#` starts a comment,
quotes are optional; keep it `chmod 600`, a group- or world-readable file gets a warning). It is sent only as the
bearer header; records name only where it came from (`token_source`), never the token.

Every file pulled from the rig is checked against the sha256 the rig reports (each chunk, the whole file, and the
case's `files.json`), and a case is written, atomically, only when all its files have passed. A mismatch that survives
one re-pull marks the case `rig-integrity`, and the run exits 6 if nothing worse happened.

### The conforming second discretisation

The verified part's STL is meshed by gmsh into straight-sided ten-node tetrahedra (C3D10) at the level-1 check spacing
(2 mm for the demo); the spec's supports and tractions are mapped onto the mesh surface (consistent tet10 nodal loads;
a loaded surface must cover 90 % of its footprint), and CalculiX `ccx` solves it, linear static. Mass, compliance, the
largest displacement and the volume-weighted von Mises percentiles (outside the same exclusion boxes as the voxel
solves) are compared with the finest voxel level that finished. The bands are fixed before any run: mass and mesh
volume 1 %; von Mises outputs the larger of the spec's tolerance and 15 %; everything else the spec's tolerance. The
check **agrees** when the objective, every constraint and the compliance are within their bands, and **passes** when
it also meets every constraint on the tet10 mesh. Limits: 1,500,000 DOF and 900 s here (up to 8 threads), 3,000,000
DOF and 3000 s on the rig (16 threads); a larger part is recorded as not run (`too-large`), and the mesh is never
coarsened silently. ccx solves with SPOOLES only, so a mesh above 800,000 DOF (`SPOOLES_MAX_DOF`) is `too-large` on
either host as well: above it ccx would switch to its iterative Cholesky solver, which stops with the reactions out of
balance far beyond the equilibrium tolerance of 1e-6 (1.46e-3 to 2.71e-3 relative on the test parts). Without gmsh or
ccx the check is recorded as not run, with the reason.

Measured on 2026-10-10 in a Linux test container (Ubuntu 24.04) with the official ccx 2.23 binary and gmsh 4.15.2
(its libgmsh byte-identical to the locked wheel's): the demo's verified part meshed on gmsh's discrete route into
38,618 C3D10 (187,011 DOF) and solved with 16 CPUs in 53.1 s; against the 1 mm voxel check it agreed within every band
(vm_p99 -4.8 %, umax -4.2 %, compliance -5.0 %). On the packages (aces-engineering 0.1.0-3 with calculix-ccx-bin
2.23-1 in a clean `archlinux:base`, `bin/install-test`, the same day) the part again meshed into 38,618 C3D10 (187,011
DOF); the check took 28.7 s with 8 threads (ccx 20.9 s, SPOOLES, equilibrium 3.6e-08) and its record names the rig
image's ccx sha256 (`31be21fc...`); against the 2 mm voxel check (no GPU there, so no 1 mm level) it agreed within
every band and met every constraint (vm_p99 -5.7 %, umax -2.2 %, compliance -2.3 %).

### Grades

`preview-grade` < `verified-desktop` < `rig-verified`, recorded with its reasons (`grade_reasons`):

- **preview-grade**: verification failed, the level-1 check did not run or did not agree, or the **constraint cap**
  applies: the conforming check finished and a constraint is violated on the tet10 mesh (here or on the rig).
- **verified-desktop**: the level-1 check agreed within the tolerance, the constraints held at the finer resolution,
  and no cap applies.
- **rig-verified**, only when all of these hold: level 1 was solved on the rig; the rig registry's `golden-check`
  validation is `pass` for the very image id level 1 ran on; the conforming check ran on the rig and passed; the
  `ccx-check` validation is `pass` for the image id it ran on; the rig's golden code and every simlab and gendesign
  module it used hash like this desktop's (code identity); and there was no integrity error. Otherwise the reasons
  say `not rig-verified: <the first condition that failed>`.

`--strict` requires `verified-desktop` or better and `--require-grade G` requires G (the higher requirement wins): a
PASS below it exits 1 with `strict: grade ...` or `required grade ...`.

### Exit codes of `run`

0 pass; 1 verification failed, or `--strict` or `--require-grade` not met; 2 usage, including a lane that cannot be
used and `--rig-sweep`, `--rig-budget` or `--conforming rig` without a lane (no run directory); 3 thin sweep;
4 under-resolved; 5 infeasible; 6 evidence, ledger or rig-integrity failure; 7 internal error (traceback in `log.txt`;
also a kernel that fails its element and patch self-test, which stops the run before the project or the run directory
is touched); 130 interrupted (rig jobs are cancelled on a best-effort basis and `rig/session.json` is written). A rig
case that fails or is not delivered has no exit code of its own: it follows the verdict and the grade, with the
fallbacks above. Check a run with `aces golden verify RUN`; the ledgers with `aces-evidence verify` and
`aces ledger verify`.

### Records of rig and conforming work

- `rig/session.json`: lane, URL, token source, principal, policy, server, budget, scope, the uploads with their
  sha256, each experiment (its spec and the spec's sha256, job ids, wall time, counts, cancellations, the rig ledger's
  evidence entry), the state events and the integrity error; `rig/specs/<id>.yaml` is each experiment spec as
  submitted.
- `<cid>/rig.json` for every rig case: experiment, rig case, job id, GPU index, name and uuid, image and image id,
  queue wait and run seconds, and every pulled file with its sha256. `<cid>/rig/` holds the rig's own records
  unchanged (`outcome.json`, `files.json`, `results.json`, `evidence.json`, `status.json`, `case.json`,
  `worker.log`).
- The conforming case (`m001/`, or `m101/` after a fallback): `conforming.json` (the mesh, the boundary-condition
  mapping with faces, areas and coverage, the solver, equilibrium, the metrics and the hashes of the case files),
  `tetmesh.json`, `mesh.npz`, `mesh.log`, `ccx.inp.gz`, `ccx.dat.gz`, `ccx.sta`, `ccx.cvg`, `ccx.log`, and
  `results.json` when it finished.
- `validation.json` gains the `conforming` and `rig` blocks and each check level's `host`, `rig` and `rig_attempt`;
  `report.md` gains section 10 (conforming) and section 11 (rig), and its verdict line names where the check ran
  (`check 2 mm on the rig (GPU n): ...`) and how the conforming check ended.

### The audit

`aces golden verify RUN` runs 13 checks in this order: files, bundle, simlab-ledger, aces-ledger, sweep, surrogate,
verdict, report, compute, **rig**, **conforming**, manifest, resolve. `rig` is INFO when the run had no rig work;
otherwise it re-hashes the submitted specs and the uploads and every pulled file and record against `rig.json` and
`files.json`, compares the evidence copies and the results with the rig's outcome, re-derives the solid cells,
stresses, displacement, mass and mesh volume from the pulled raw files, checks the image and GPU facts, and re-derives
the code identity and `validation.json`'s rig block. `conforming` is INFO when no conforming case ran; otherwise it
recomputes the metrics from `ccx.inp.gz` and `ccx.dat.gz` (to 1e-12), checks the case files' hashes and re-derives the
comparison rows. The audit never contacts the rig.

### What it does not claim

A desktop result is evidence, not sign-off, and never a rig result. Both solvers are linear static: the voxel FEA in
a fictitious domain, and the conforming check on the same part with the same boundary conditions. Stresses next to
supports and loads are excluded (the constrained stress is the 99th percentile outside those zones; the maximum is
recorded and is resolution-sensitive). No fatigue or buckling check; the material is nominal; `rig-verified` is only
as good as the rig registry's validation of the image; an organic part is shipped as STL, never as a pretend STEP;
gyroid cores enclose powder (drain holes are not modelled).

## Desktop defaults

- Evidence: `SIMLAB_EVIDENCE_ROOT` defaults to `~/.local/share/aces/simlab/evidence` (the module default,
  `<install>/out/evidence`, is read-only).
- Registry: the packaged `solvers.json` is read-only. A command that registers a surrogate copies it to
  `~/.local/share/aces/simlab/solvers.json` and sets `ACES_SIMLAB_REGISTRY`; `aces_engineering_site.py` applies that
  variable at interpreter start, so `simlab run` workers see the same registry. Delete the file to go back.
- `aces-simlab surrogate` resolves its `out/surrogate/<name>` and `cases/` defaults against the current directory.
- MLflow: not in the venv; set nothing and tracking is skipped with one warning.

## Agents

Copy `/usr/share/aces/engineering/mcp.json.example` to a project's `.mcp.json` (or merge it into the agent's MCP
config) and export `SIMLAB_MCP_TOKEN` (a submit-tier token from the rig's `SIMLAB_MCP_TOKENS`): an agent expands only
environment variables in that file. The aces tools themselves (`aces golden ... --lane`, `aces-simlab remote`) read
`ACES_SIMLAB_TOKEN`, then `SIMLAB_MCP_TOKEN`, then `~/.config/aces/simlab.env`. The simlab URL is `server.simlab_mcp`
in the aces config; port 8020 on the rig serves the Univer workbench, not simlab. An agent driving the golden path
follows the aces-engineering skill: never `--lane` in CI, lanes only from the fabric manifest, and a desktop result is
never reported as a rig result.

## Maintenance

- `bash src/aces-engineering/sync.sh [--commit REV] [cudax-sim-runtime path]` refreshes `vendor/` and `SNAPSHOT` (LF
  line endings; `__pycache__`, `solvers.json.bak-*` and `simlab/console` left out; every hash `sha256sum -b`, so Git
  Bash and Linux agree). With `--commit` it copies from a clean clone at REV and SNAPSHOT says so; without it, it
  warns when the cudax tree has uncommitted changes under `simlab/`, `gendesign/` or `cases/`. The procedure for a
  release: `bash src/aces-engineering/sync.sh --commit rig-golden-1 /c/Users/shedm/cudax-sim-runtime`, then copy the
  `source_commit` sha and the `content_sha256` from SNAPSHOT into `_snapshot_commit` and `_snapshot_content_sha256`
  in the PKGBUILD. `build()` refuses a tree whose SNAPSHOT or `vendor/` content does not match those two pins.
- `bash src/aces-engineering/lock.sh` re-resolves `requirements.in` in the aces-builder image and rewrites the
  hash-pinned `requirements.txt`; bump `_py` in the PKGBUILD when Arch moves to Python 3.15.

## Tested (2026-10-09)

- Build (`bash bin/build aces-engineering`): `check()` runs `aces-gendesign selftest` against the build venv: 56/56
  imports; fea3d element test; the 1 mm cantilever reproduces the workstation's GPU record (0.1989054912444315 mm) to
  8.3e-13 in 401 PCG iterations on NumPy; evidence bundle built and chain verified; md/html/docx report; 8 SIMP
  iterations; L-bracket STEP round trip to 26,694 voxels, the workstation's count (Embree; 26,616 without it).
- Clean `archlinux:base` with aces-base + aces-engineering + aces-voxel: relocated shebangs and `pyvenv.cfg`, no build
  paths left, bytecode at `/opt/...`; the selftest again; `aces-voxel fea` of the bracket STEP at 2 mm (18,000
  elements, 1,232 PCG iterations, converged); the workstation's 4-bundle evidence ledger verified after relocation,
  and a hand-edited bundle rejected; `aces-simlab remote` info/tools/call against a local MCP server on the mcp 2.0.0
  SDK (JSON and SSE replies, bearer token, 401 on a wrong token); a 10-trial parametric study and a GP surrogate
  registered in the per-user registry. Nothing here contacted the rig.

## Tested (2026-10-10, the conforming check)

- Unit tests in the dev layout on the workstation (Windows, Python 3.11.9, gmsh 4.15.2, no ccx): all golden tests,
  215, OK, 5 skipped (the 2 ccx tests; the opt-in GPU parity test; Ctrl-C, which needs POSIX signals; one aces-base
  launcher shape that this checkout does not have).
- A throwaway Linux test container (Ubuntu 24.04, Python 3.12, gmsh 4.15.2, the official ccx 2.23 binary with the
  GCC 7 Fortran runtime it needs): the 35 conforming tests, OK, none skipped; the selftest's `conforming` check PASS
  (bar patch test: 539 C3D10 on the remesh route, worst relative difference 2.3e-08, equilibrium 5.4e-09); without
  ccx it reports INFO `not run: ccx not found ...`, and with a ccx that cannot start, FAIL.
- On the packaging branch `bash bin/build calculix-ccx-bin` stopped in `check()` at `libgfortran.so.4 => not
  found`, so this package was not built there. At the integration (6db440f, where calculix-ccx-bin brings the GCC 7
  runtime: review finding 4) `bash bin/build calculix-ccx-bin` and `bash bin/build aces-engineering aces-omarchy
  aces-os` exit 0; this package's `check()`, with calculix-ccx-bin installed, passed the selftest (`92/92 import`,
  `conforming` PASS: 539 C3D10, worst relative difference 2.3e-08, equilibrium 5.4e-09), the 300 golden tests (2
  skipped) and the demo; `bin/install-test aces-base aces-engineering aces-voxel calculix-ccx-bin` exit 0, the demo
  `verified-desktop` with the conforming check `ok` on the packaged ccx (above).

## Known gaps

- `calculix-ccx-bin` brings GCC 7's `libgfortran.so.4` (from Ubuntu 20.04's `libgfortran4` 7.5.0-6ubuntu2, privately
  under `/usr/lib/calculix-ccx-bin`), because the official ccx_2.23 binary needs it (symbol version GFORTRAN_7) and no
  Arch package provides it (Arch's is `.so.5`). The operator's decisions of 2026-10-10 chose that deb (item 1, with a
  wrapper script at `/usr/bin/ccx`); the package keeps the binary itself behind `/usr/bin/ccx` and the library behind
  an `ld.so.conf.d` entry instead (review finding 4), so the ccx sha256 a conforming record names stays the binary's.
  The layout waits for confirmation.
- A mesh above 800,000 DOF (`SPOOLES_MAX_DOF`) is not solved (`too-large`), so the `MAX_DOF` limits are not reached
  until SPOOLES is measured at those sizes against the time limits (review finding 1).
- The rig registry's `golden-check` and `ccx-check` rows are `pending` until the operator validates them on the rig
  (cudax-sim-runtime `docs/GOLDEN_RIG.md`): until then `rig-verified` is unreachable by design, and every rebuild of
  the image invalidates the validation.
- A desktop conforming check on a small machine (a 2-vCPU CI runner) may hit its 900 s timeout; it is then recorded
  as not run, and it is never a CI gate.

- The vendored simlab and gendesign have no licence file; VISION.md decision 4 says the kernel's source licence is
  decided before the first external install. Until then this package is for Carbon Project machines only.
- `aces-tpms-periodic` is in the registry but its adapter (`aces.cfd.simlab_adapter`) lives in the separate `aces`
  repository, not in this snapshot; its top-level name also collides with aces-base's `aces` package.
- The registered surrogates (`surrogate-bracket-*`) point at model files on the workstation; they predict only where
  those files exist.
- PDF reports need LibreOffice (`soffice`); without it `report` writes md, html and docx and reports no pdf.
- Third-party licences, including the native libraries bundled inside wheels, are in `LICENSES.md`.
- The aces-base package in the repo (0.1.0-2) still sets `server.simlab_mcp` to port 8020; its source has the
  corrected 8022 but has not been rebuilt. Until then `aces-simlab remote` needs `--url http://192.168.1.65:8022/mcp`
  or `simlab_mcp` in `~/.config/aces/aces.toml`.
- The OCP wheel bundles its own older fontconfig: importing build123d prints `Fontconfig error: Cannot load default
  config file` (no fontconfig installed) or `Fontconfig warning: ... invalid constant used` lines (with Arch's
  fontconfig). Cosmetic: build123d `Text` with system fonts was checked to work. `FONTCONFIG_FILE` did not quiet it.
- `pkgbuilds/ORDER` builds aces-voxel with `nodeps`; its `check()` then skips (it needs this venv installed).

## Local CAD (aces-cad)

aces-cad (`src/aces-cad/`, package `aces-cad`) runs build123d in this venv, in sandboxed child processes of
`/opt/aces/engineering/venv/bin/python` (seccomp, Landlock, rlimits, no network); it has no venv of its own. It relies
on build123d 0.11.1, cadquery-ocp-novtk 7.9.3.1.1, trimesh, scipy, scikit-image, matplotlib and embreex, and on
ezdxf 1.4.4 (drawings) and lib3mf 2.5.0 (3MF export) from `requirements.txt`; the last two are transitive pins of
build123d (it requires `ezdxf>=1.1.0,<2` and `lib3mf>=2.4.1`), not entries of `requirements.in`. After a re-lock,
rebuild aces-cad and run its tests: its static test S03 fails when `ezdxf==` or `lib3mf==` is no longer pinned here,
and its `check()` checks that the installed venv still imports both.
