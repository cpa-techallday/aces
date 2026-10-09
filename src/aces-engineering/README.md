# aces-engineering

The desktop side of the ACES engineering loop: validate experiment specs, run the voxel kernel and gendesign's CPU
paths, write reports, build and verify evidence bundles, and talk to the rig's simlab and rig-state MCP servers.
GPU solving stays on the rig. The code is a vendored snapshot of `cudax-sim-runtime` (simlab and gendesign); nothing
in that repository is patched: where a module needs a rig-only or GPU package, the wrappers here keep it off the
desktop command line or wrap it, as listed below.

## Layout

| Path | What |
|---|---|
| `/opt/aces/engineering/venv` | one locked Python 3.14 venv: 907.6 MiB installed per pacman (982 MiB by du), package 255 MB: `requirements.txt` pins all 76 distributions with sha256 hashes |
| `/opt/aces/engineering/lib` | the vendored `simlab` and `gendesign`, the `aces_engineering` wrappers, `aces_engineering_site.py` (put on the venv's path by `aces-engineering.pth`) |
| `/opt/aces/engineering/SNAPSHOT` | source path and commit, sync time, content sha256 and per-file manifest of the vendored code |
| `/usr/bin/aces-simlab`, `aces-evidence`, `aces-gendesign` | shims that run `python -m aces_engineering.<cli>` in the venv |
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
| `aces-gendesign selftest` | the packaged self-test (same as the package build's `check()`) |
| `aces-gendesign topopt\|parametric\|nozzle\|fusion ...` | SIMP, Optuna studies, Angelino aerospike contour, push STL/STEP to Fusion |

`aces-simlab capacity` without `--local` and every `aces-simlab remote` call go to the rig: `remote call sim_submit`
queues real GPU work there, and the simlab server audits every call against the token's principal.

## Dependencies

| Class | Packages | State |
|---|---|---|
| Core (installed) | numpy, scipy, scikit-image, trimesh, rtree, optuna, matplotlib, PyYAML, pydantic, python-docx, Markdown, scikit-learn, joblib, build123d, cadquery-ocp-novtk, embreex (76 distributions with their dependencies) | pinned in `requirements.txt`, versions as recorded on the workstation (`beam-validation-v1` evidence) |
| Optional GPU (not installed) | cupy-cuda12x, warp-lang, torch | imported lazily: `fea3d`/`topopt3d` fall back to NumPy; `lattice_opt`, `warp_fea` and the FNO surrogate need them |
| Rig-only (not installed) | rq, redis, uvicorn, mcp (SDK; pulls uvicorn and starlette), starlette, physicsnemo, rocketcea, gmsh | used by the queue, leases, MCP server and console, by PyFR meshing, and by the nozzle's NASA CEA path |
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
| `simlab.solvers.pyfr_solver`, `simlab.meshing` | gmsh, PyFR inside functions | importable; run in the `simlab-pyfr` image on the rig |
| `simlab run --docker` | docker + a repo checkout mounted at `/work` | refused by the wrapper; submit to the rig instead |

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
config) and export `SIMLAB_MCP_TOKEN` (a submit-tier token from the rig's `SIMLAB_MCP_TOKENS`). The simlab URL is
`server.simlab_mcp` in the aces config; port 8020 on the rig serves the Univer workbench, not simlab.

## Maintenance

- `bash src/aces-engineering/sync.sh [cudax-sim-runtime path]` refreshes `vendor/` and `SNAPSHOT` (LF line endings;
  `__pycache__`, `solvers.json.bak-*` and `simlab/console` left out).
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

## Known gaps

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
