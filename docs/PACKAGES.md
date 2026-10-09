# ACES OS package register

State as of 2026-10-09. Packages are built by `bin/build` inside the `aces-builder` image (Arch `base-devel`) into the
local `[aces]` repo at `repo/x86_64/`, and install-tested by `bin/install-test` in a clean `archlinux:base` container.
Every package and the repo database are signed with the development key (`keys/aces-pkgs.pub.asc`, fingerprint in
`keys/FINGERPRINT`, ed25519, expires 2028-10-08); the install test trusts that key and installs with
`SigLevel = Required DatabaseRequired`. CI (`.github/workflows/build.yml`) repeats the build and the install test on every
push to the private repo `github.com/cpa-techallday/aces-os`, signing with the same key from a repository secret. The key
is a development key: rotate it before anything is distributed.

## Built and tested

| Package | Version | What it is | Build | Test evidence |
|---|---|---|---|---|
| `aces-base` | 0.1.0-2 | The `aces` CLI (standard-library Python: `fabric`, `policy`, `project`, `ledger`, `lane`, `doctor`) and `/etc/aces` defaults (`aces.toml`, `policy.json`, `modes.json`) | built; the PKGBUILD `check()` self-test passed (project, end-use gate, override, 3-entry ledger chain, offline doctor) | Clean container: `aces --version`, `doctor --offline`, `policy show`, `project new/add-component/classify`, `policy override`, `ledger verify` (chain intact), `fabric refresh` reported every endpoint unreachable and derived **no** lanes. 0.1.0-2 adds the `server.webodm` default (`http://127.0.0.1:8090`) for aces-survey; rebuilt and re-tested 2026-10-09 (same CLI checks pass). On DARKNESS against the real rig: GRID CONTROL answered (mode `sim-lab-4`, 8 GPU rows, auto-switching off), rig-state tool call merged per-GPU processes (GPU 4: `VLLM::EngineCore`, 57.6 GB), MLflow on the rig answered HTTP 200, five lanes derived from `modes.json` (see the lane-test table below), two lane tests recorded in the ledger |
| `aces-creative` | 0.1.0-1 | Meta-package over the seven ArtCraft apps, `status.json` with upstream's status words, `aces-creative-labels` | built | Installed with its seven members; `aces-creative-labels` wrote 7 per-user launcher overrides (`ai.storyteller.*.desktop`) |
| `photocraft-bin` | 0.3.0-1 | Pinned copy of the AUR recipe (GitHub release tarball) | built | installed as an `aces-creative` member; not launched (no display in the container) |
| `vectorcraft-bin` | 0.4.0-1 | same | built | same |
| `filmcraft-bin` | 0.2.1-1 | same | built | same |
| `lightcraft-bin` | 0.2.1-1 | same | built | same |
| `printcraft-bin` | 0.2.1-1 | same (upstream now calls it PdfCraft) | built | same |
| `effectcraft-bin` | 0.4.0-1 | same | built | same |
| `designcraft-bin` | 0.2.1-1 | same | built | same |
| `aces-consoles` | 0.1.0-2 | `aces-console <key>` launcher (now also `webodm`) (reads the URL from the aces config; Omarchy web-app window or `xdg-open`) and desktop entries for GRID CONTROL, SIMLAB, MLflow | built | Installed; the three desktop entries are present. The launcher itself was not exercised (no browser in the container) |
| `aces-omarchy` | 0.1.0-4 | Overlay for Omarchy: `post-boot.d` and `post-update.d` hooks, the `aces-engineering` agent skill, the `aces-fabric` bar-widget plugin, `aces-omarchy-setup` (links every skill any ACES package ships under `/usr/share/aces/*/skills`, copies the plugin, and puts the widget on the bar: through `omarchy plugin enable` when the shell runs, else by adding `{"id": "aces-fabric"}` to the bar's right section in `~/.config/omarchy/shell.json`, which is where the shell's PluginRegistry reads enabled bar widgets from; `--remove` takes it off again) | built | Installed on a real Omarchy 4.0.4 (the rehearsal VM, 2026-10-09): `aces-omarchy-setup` linked the hooks and the skills into `~/.claude`, `~/.codex`, `~/.pi/agent` and `~/.agents`, copied the plugin, and **`omarchy plugin validate` passed on both the copy and the packaged directory**. `qmllint` against Omarchy's own `qs.Commons`/`qs.Ui` modules found two real defects, fixed in -3 (a `state` property shadowing Item's, and an unresolvable parameter type in the `Process.exited` handler); the remaining warning classes also occur in Omarchy's first-party plugins. -4 adds the bar placement: a copied plugin is discovered but off the bar until `shell.json` names it (`omarchy plugin enable` needs the running shell, which an SSH session does not have). Seen rendering live on the VM's desktop the same evening, once the desktop user signed in at the console (captured with `rehearsal/vm-shot.ps1`) |
| `aces-field` | 0.1.0-1 | Meta-package over the Arch-packaged field tools (RF receive and analysis, firmware analysis, device access over SSH, CAN, serial); AUR-only members are optional until forked | built with `--nodeps` | Not installed (multi-GB); `pacman -Sp aces-field` resolves a 372-package transaction against the official repos, so every hard dependency exists |
| `aces-survey` | 0.1.0-1 | Survey and photogrammetry: `aces-survey-setup` (installs the vendored FlyPath 3.0.0 QGIS plugin into the user's QGIS profile and enables it; creates the WebODM tree), `aces-webodm` (start/stop/status/open/update/logs over the pinned WebODM v3.3.0 tree under Docker, GPU node when nvidia-smi and the Docker nvidia runtime are present), a WebODM desktop entry, README with the DJI Fly mission-loading procedure, LICENSES.md. Depends on `qgis`, `python-shapely`, `python-pyproj`, `docker`, `docker-compose`; `cloudcompare`, `colmap`, `meshroom` (AUR) and `nvidia-container-toolkit` optional. FlyPath zip and WebODM release tarball are fetched at build time with pinned sha256s (34.7 MB package) | built with `--nodeps` (QGIS is not needed to build an `any` package) | Clean container: installed with `-Sdd`; `aces-survey-setup` placed FlyPath 3.0.0 and wrote `[PythonPlugins] FlyPath=true`, created the WebODM 3.3.0 tree; `aces-webodm --help` prints usage and `status` reports that docker is missing instead of crashing; `--remove` cleared both. `pacman -Sp aces-survey` resolves a 256-package transaction. On DARKNESS (Windows, Docker Desktop) the same stack runs outside the package: `GPU_NVIDIA=true ./webodm.sh start --port 8090 --detached` brought up webapp, worker, db, broker and `node-odx-1` (`webodm/nodeodx:gpu`), the node sees the RTX PRO 4000, the console answers HTTP 302 on :8090; FlyPath 3.0.0 imports under QGIS 3.44.15 (OSGeo4W, user-profile install) with shapely 2.1.2 and pyproj 3.8.0 |
| `aces-os` | 0.1.0-3 | The meta-package: `aces-base`, `aces-creative`, `aces-field`, `aces-consoles`, `aces-omarchy`, `aces-survey`, `aces-engineering`, `aces-voxel`; `aces-studio` stays optional until the rig's video lane exists | built with `--nodeps` | `pacman -Sp aces-os` resolves in a clean signed container: 508 packages when aces-base, aces-creative, aces-consoles and aces-omarchy are already installed, 529 when aces-base, aces-engineering and aces-voxel are (qgis, docker and the engineering venv's libraries included) |
| `aces-engineering` | 0.1.0-1 | One locked Python 3.14 venv at `/opt/aces/engineering/venv` (76 distributions, each hash-pinned in `requirements.txt`, binary wheels only; 907.6 MiB installed, 255 MB package) with a vendored snapshot of cudax-sim-runtime's `simlab` and `gendesign` (commit `28d92a8`, content sha256 in `SNAPSHOT`) on its path. Commands: `aces-simlab` (validate, local run, report, evidence, surrogate, export, capacity, `remote` MCP client), `aces-evidence` (ledger and bundle verification, relocates ledgers from other machines), `aces-gendesign` (list, selftest, topopt, parametric, nozzle, fusion); `mcp.json.example` for the rig's simlab `:8022` and rig-state `:8010`; LICENSES.md. Not installed: rig-only rq, redis, uvicorn, mcp, starlette, gmsh, rocketcea, physicsnemo, and the GPU packages cupy, warp, torch (mlflow too); the wrappers keep the queue, leases, MCP-server and console commands off the desktop CLI. Depends on `libglvnd` and `libx11` (the OCP wheel links libGL and libX11) | built (x86_64, signed); `check()` = `aces-gendesign selftest` passed | `check()`: 56/56 imports, fea3d element test, the 1 mm cantilever on NumPy reproduces the workstation's GPU record (0.1989054912444315 mm) to 8.3e-13 in 401 PCG iterations, evidence bundle and chain verified, md/html/docx report, 8 SIMP iterations, L-bracket STEP round trip at 26,694 voxels as on the workstation (needs `embreex`; trimesh's rtree fallback gives 26,616, all 78 differences on one 45-degree face). Clean container (own script, `SigLevel = Optional TrustAll`): relocated shebangs, `pyvenv.cfg` and bytecode paths, selftest again, `aces-voxel fea` of the bracket STEP at 2 mm (converged, 1,232 iterations), the workstation's 4-bundle evidence ledger verified (4 entries relocated) and a hand-edited bundle rejected, `aces-simlab remote` against a local mcp 2.0.0 server (JSON and SSE replies, bearer token, 401 on a wrong one), a 10-trial parametric study and a GP surrogate in a per-user registry, `pacman -R` leaves nothing under `/opt/aces`. After the full signed rebuild, `bin/install-test aces-base aces-engineering aces-voxel` passed under `SigLevel = Required DatabaseRequired`: `aces-engineering selftest: all hard checks passed`, `aces-voxel fea --self-test` PASS (STEP round trip at 26,694 voxels in 0.3 s) |
| `aces-voxel` | 0.1.0-1 | `aces-voxel`: `fea` (STEP to voxel H8 FEA through simlab's step-structures stages, or `--self-test`), `topopt` (3-D SIMP), `lattice` (Warp rod lattices, GPU only), `export` (density to STL); `docs/VOXEL_KERNEL.md` (what is standard, what is not, the source modules) in `/usr/share/doc/aces-voxel`; an `aces-voxel` agent skill in `/usr/share/aces/voxel/skills` (linked by `aces-omarchy-setup` 0.1.0-2). Runs in the aces-engineering venv, no second venv | built (any, signed) with its dependency installed (`pkgbuilds/ORDER` no longer marks it `nodeps`), so `check()` runs against the installed aces-engineering: fea self-test, 6 SIMP iterations, export, a build123d bar STEP through `fea`, `lattice` exits 2 without a GPU | Clean container: `--help`, `fea --self-test`, `fea` of the bracket STEP, `export` of a SIMP density, `lattice` exit 2 with its reason |

## Changed for the golden path

These versions were built in the Arch builder (`bin/build aces-base aces-omarchy aces-engineering aces-voxel`, signed)
and install-tested in a clean container (`bin/install-test` and `bin/install-test aces-base aces-engineering
aces-voxel`) on DARKNESS on 2026-10-09 (UTC), last after the review fixes (the same version-releases: none had been
published); the rows above describe the previous builds. The rehearsal VM has run these versions (the golden-path table
below, run `20261009T144006Z-b3ac99`); CI has not run them yet. The rehearsal web root
(`D:\HyperV\aces-rehearsal\serve\www`) had still served aces-base 0.1.0-2, aces-engineering 0.1.0-1, aces-voxel 0.1.0-1
and aces-omarchy 0.1.0-4 and an `aces-install.sh` without the golden block; `rehearsal\serve-repo.ps1` re-synced it
after the last build (signed, the four new versions and their `.sig`, the golden block; checked byte for byte against
`repo\x86_64` before the VM run). Re-run it after any rebuild before the VM step, and upgrade aces-omarchy there too so
the VM's agent skill documents `aces golden`. Before the VM step the VM itself had to be restarted: its virtual disks sit
on a USB SSD that stalled on the evening of 2026-10-09, and the guest's btrfs root had gone read-only (see
`rehearsal/README.md`).

| Package | Version | What changed | Test evidence |
|---|---|---|---|
| `aces-base` | 0.1.0-3 | `aces ledger record KIND --json FILE\|-` (kind and payload checks; `policy.`, `project.`, `lane.`, `ledger.` reserved; 64 KiB payload limit; `--print json`), `aces ledger verify --json`, `aces ledger show --kind K --json` (entries carry their position `n`), an exclusive writer lock around read-last-hash-and-append, `aces project add-evidence` and `add-cad-source`, the `aces golden` passthrough (exit 127 with the package to install when `aces-golden` is missing; `ACES_GOLDEN_CMD` for development; on that non-exec path a Ctrl-C reaches the child from the same console and `aces` waits for its interrupted run records, then returns its 130), a `golden` row in `aces doctor`, `python -m aces`; `check()` adds the unit tests (`tests/test_ledger_record.py`, including two concurrent writers) and inline asserts for the new commands | built (any, signed); `check()` on Python 3.14.7: the 3-entry self-check, 17 unit tests OK, then `ledger record golden.run` as entry 4, `policy.override` and `Bad Kind` refused with exit 2, verify, `project add-evidence`, `ledger show --json`, the passthrough 127 without `aces-golden` and 3 from a stub. Clean container (`SigLevel = Required DatabaseRequired`): `aces ledger verify` ok with 5 entries after the golden run |
| `aces-engineering` | 0.1.0-2 | `aces-golden` (`aces_engineering.golden_cli` and the `aces_engineering.golden` package: spec, demo, domain, generator, organic skin, FEA, cases, sweep, export, renders, compute detection, surrogate, optimiser, verification, evidence, report, run directory, progress, pipeline); depends on `aces-base>=0.1.0-3`; `check()` runs the golden unit tests, `aces golden run --demo` (printing the run's log when it fails, and requiring exactly one JSON document on stdout) and `aces golden verify latest` after the selftest; the selftest imports the golden modules and tqdm, threadpoolctl, psutil. After review: every support and load box face sits on the grid that `gen_mm`, `fea_mm` and the planned check levels share (the refusal names the nearest box; `--demo --res 5` used to fix 42 nodes at 5 mm and 150 at 2.5 mm), a traction plane must be an envelope face, one load case per spec, the seed is a 32-bit unsigned integer, the stress-exclusion zones are rounded up to the solve grid so every resolution excludes the same zone (the demo's 6 mm becomes 8 mm; `domain.json` records it), a failed kernel self-test stops the run before the project and run directory (exit 7), the CPU-only cell warning is emitted, the verdict line labels its stage-time sum, the audit reports the compute table as INFO for a run made by other golden code, and the report's stage-time sum and its provenance check share one plain-addition helper | built (x86_64, signed). The first build failed `check()`: the gyroid cutaway render called `Trimesh.slice_plane`, which imports shapely, and shapely is not in the lock (the workstation venv had it from the system site-packages); the render now uses `trimesh.intersections.slice_faces_plane` (numpy only). The first rebuild after review failed `check()` in `aces golden verify`: the report summed the stage times with `sum()`, which is compensated on Python 3.12 and later, the verifier with a plain loop (29.057 against 29.057000000000002); both now use one helper, and the compute-used sums use `math.fsum`. Rebuilt: selftest all hard checks passed (81/81 imports), 169 golden unit tests OK (2 skipped) on Python 3.14.7, `aces golden run --demo` PASS · verified-desktop (run `20261009T142505Z-b3ac99`, bundle `a24c2ae54277`; 2 mm check vm_p99 56.88 MPa, +4.4 %), `aces golden verify latest` all checks pass. Clean container: selftest passed, golden block in the table below |
| `aces-voxel` | 0.1.0-2 | the skill names the end-to-end generative loop (`aces golden run --demo`); depends on `aces-engineering>=0.1.0-2`, the first version with `aces golden` | built (any, signed); its `check()` ran against the installed aces-engineering 0.1.0-2 (the build after review). Clean container with aces-engineering 0.1.0-2: `aces-voxel fea --self-test` OK |
| `aces-omarchy` | 0.1.0-5 | the aces-engineering skill documents `aces golden` (commands, grades, exit codes including the passthrough's 127, rules); depends on `aces-base>=0.1.0-3`, the first version with the commands the skill documents | built (any, signed); default install test (`bin/install-test`: aces-base, aces-creative, aces-consoles, aces-omarchy) passed, `aces-omarchy-setup` linked the skill into `~/.claude/skills` and `~/.agents/skills` |

## Golden path (`aces golden`)

One command from a design space to a computed, verified and evidenced organic part (VISION section 11.1: STEP ->
voxel -> sweep -> surrogate -> verify -> evidence -> report). `aces golden run --demo` computes a six-foot root mount
inside a disc design space: every design is SIMP topology optimisation turned into an organic implicit skin and solved
on the real voxel FEA; a GP surrogate fitted to the sweep proposes the winner, which is solved again, checked at a
finer resolution, and re-solved on CuPy and Warp when they are usable. The loop is described in
`src/aces-engineering/README.md` (section "Golden path") and in the aces-engineering skill.

- Run directory: `$ACES_WORK/<project>/golden/<run-id>/` (a simlab experiment directory plus `run.json`,
  `result.json`, `compute.json`, `compute_used.json`, `validation.json`, `winner/` or `candidate/`, `figures/`,
  `evidence/`, `report.md/html/json`, `log.txt`, `events.jsonl`, `manifest.sha256`).
- Evidence: `evidence/files.sha256` anchors the run's records; its digest is inside a simlab evidence bundle chained
  into the per-user simlab ledger; a `golden.run` entry in the aces ledger names the bundle; the project's `aces.yaml`
  gets the evidence reference and the components used.
- Exit codes: 0 pass, 1 verification failed (or `--strict` without `verified-desktop`), 2 usage (including `--lane`),
  3 thin sweep, 4 under-resolved, 5 infeasible, 6 evidence or ledger failure, 7 internal error, 130 interrupted; 127
  from the `aces golden` passthrough when aces-engineering is not installed.
- Verify: `aces golden verify RUN` (files, bundle, both ledgers, sweep table, surrogate models, verdict, report
  provenance, compute-used rows, manifest), `aces-evidence verify`, `aces ledger verify`.
- Grades: desktop solves are `preview-grade`; `verified-desktop` when the finer-resolution check agreed; neither is a
  sign-off.

| Where | Date | Command | Outcome | Grade | Wall s | Run id | Bundle sha12 | simlab entry | aces entry |
|---|---|---|---|---|---|---|---|---|---|
| Clean container (`bin/install-test aces-base aces-engineering aces-voxel`) | 2026-10-09 | `aces golden run --demo --json --progress lines` (packaged venv, Python 3.14.7, CPU only: no CuPy or Warp in the package) | pass (exit 0) | verified-desktop | 22.3 | `20261009T142823Z-b3ac99` | `54b28de54d31` | 1 | 5 |
| CI (GitHub Actions, same install test) | not yet run | same | not yet run | not yet run | not yet run | not yet run | not yet run | not yet run | not yet run |
| Rehearsal VM (Omarchy 4.0.4, Python 3.14.7, 4 vCPUs, no GPU) | 2026-10-09 | `aces golden run --demo` from a login shell over ssh, after `pacman -Sy` upgraded aces-base, aces-engineering, aces-voxel and aces-omarchy from the signed LAN repo (packaged venv, CPU only; the 2 mm check ran on the CPU, estimate 2.8 s within the 60 s budget); then `aces golden verify latest` (10 PASS, 1 INFO), `aces-evidence verify` ok, `aces doctor` golden row `ok ... PASS`, the linked skills carry the new text | pass (exit 0, 25 s including interpreter start) | verified-desktop | 23.7 | `20261009T144006Z-b3ac99` | `070c439672c8` | 1 | 2 |
| DARKNESS (workstation) | 2026-10-09 | `python -m aces_engineering.golden_cli run --demo` (workstation dev venv, Python 3.11.9: CPU sweep in 10 workers, check and parity on the RTX PRO 4000 through CuPy and Warp) | pass (exit 0) | verified-desktop | 42.8 | `20261009T143033Z-b3ac99` | `d8977ebb61f8` | 1 | 2 |

## Not yet written

| Package | What it will be | Why it waits |
|---|---|---|
| `aces-studio` | ACES Video Studio client and LoRA registry client | the server side (ComfyUI + ltx-pipelines workers) is not deployed |

It is listed as an optional dependency of `aces-os` so the meta-package resolves honestly today.

## Live lane tests (DARKNESS to the rig, 2026-10-09, mode `sim-lab-4`)

GRID CONTROL does not publish model endpoints, so they were discovered by probing `/v1/models` on the rig (read-only) and
written into `modes.json`. `aces lane test` then ran one models list, one 32-token completion and one tool-call probe per
lane. Each result is a `lane.test` ledger entry.

| Lane | Endpoint | Model served | Completion | Tool call |
|---|---|---|---|---|
| `model/glm-flash` | `:8016/v1` | `QuantTrio/GLM-4.7-Flash-AWQ` | 32 tokens in 0.36 to 0.56 s (57.6 to 88.4 tokens/s over two runs); reply began with the model's reasoning text, not the single word asked for | rejected, HTTP 400: the server says `"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set` |
| `model/glm-flash-2` | `:8017/v1` | same | 82.1 tokens/s | same rejection |
| `model/gpt-oss` | `:8018/v1` | `openai/gpt-oss-20b` | 31 tokens in 0.23 s (136.4 tokens/s); reply `OK` | accepted in 0.22 s but returned no structured tool call |
| `embed/qwen3` | `:8005/v1` | `Qwen/Qwen3-Embedding-0.6B` (owned_by `embed-cpu`) | not tested (embedding lane; the probe is for chat lanes) | n/a |

What this says for the plan's lane qualification ("ACES 0.1 Bench"): in the current `sim-lab-4` mode no lane returns
structured tool calls. The GLM-Flash servers need `--enable-auto-tool-choice --tool-call-parser` (a server-side change on the
rig, Kimi's to make), and the gpt-oss lane needs its tool parser checked. Until then an agent on this desktop has no
tool-calling lane on the rig; the manifest and the skill say so rather than hiding it. Port `8033` answers `/v1/models` with
HTTP 401 (authenticated service, not identified). GRID CONTROL's status also publishes two consoles, recorded in the
manifest under `server.discovered`: `mc_console` on the rig's loopback (`127.0.0.1:3100`, not reachable from here) and
`gridwatch` at `192.168.1.65:3000`.

## Known gaps

- Endpoints are known only for `sim-lab-4` (and the CPU embedding lane); the other modes' lanes stay `null` until probed in
  those modes. Per-instance GPU assignment for the two GLM-Flash servers is not published.
- Corrected 2026-10-09 in `aces-base` (rebuilt): the simlab MCP is on `http://192.168.1.65:8022/mcp` (port 8020 serves the
  Univer workbench) and the SIMLAB console is live on the rig at `http://192.168.1.65:8032`; the earlier defaults (8020 and
  the workstation's 8030) were wrong. `aces doctor` from DARKNESS now reports all five server endpoints reachable.
- `aces-field`'s AUR-only members (`can-utils`, `python-cantools`, `python-udsoncan`, `python-snap7`, `python-pyvisa`,
  `openems`) are optional dependencies until their recipes are forked into `pkgbuilds/`.
- The ArtCraft recipes are byte-for-byte AUR copies; `namcap` warns about a literal `x86_64` in each (upstream style).
- `aces-survey`: CloudCompare, COLMAP and Meshroom are AUR-only, so they stay optional until their recipes are forked;
  the WebODM docker images (`webodm/webodm_webapp`, `webodm_db`, `nodeodx`, `nodeodx:gpu`) are pulled from Docker
  Hub at first start, not shipped; `aces-webodm` has not been run on an Omarchy host yet (the Docker path was
  exercised on DARKNESS under Docker Desktop); the DJI Fly loading step is manual by design (the app has no import).
- Git, CI and signing landed on 2026-10-09 (day-14 gate items in VISION.md §11); the key is a development key. The Omarchy
  rehearsal ran the same afternoon and passed (below); what it did not cover is the desktop session itself (Hyprland and
  the shell on Hyper-V, the bar widget rendering live) and the VHDX cidata path.
- `aces-engineering` vendors `simlab` and `gendesign` under `LicenseRef-ACES-undecided` (CPA code with no licence file
  yet), and the OCP wheel it installs bundles OpenCASCADE (LGPL), libjbig (GPL) and FreeImage without licence files:
  both are distribution-gate items, not trial-mode blockers.

## How to reproduce

```bash
bash bin/build && bash bin/install-test
```

`bin/build` with no arguments follows `pkgbuilds/ORDER` (members before dependants; `nodeps` for the meta-packages) and
signs when `keys/aces-pkgs.sec.asc` is present. The same two commands run in CI on every push.

Logs for every package are in `logs/<pkg>.log` and the install test in `logs/install-test.log`; the repo database is
`repo/x86_64/aces.db.tar.zst`. Sources under `src/<pkg>/` are packed at build time, so a source edit needs a rebuild; a
rebuild without a `pkgrel` bump replaces the package in the local repo in place (fine for development, never for a published
channel).

## Rehearsal VM kit (`rehearsal/`, 2026-10-09)

Scripts that install stock Omarchy 4.0.4 unattended into a Hyper-V VM on DARKNESS (`get-iso.ps1`, `make-cidata.ps1`,
`new-vm.ps1`), serve this repo to it over the LAN (`serve-repo.ps1`) and run `post-install/aces-install.sh` in it
(`validate.ps1`); step order, elevation and reset are in `rehearsal/README.md`.

**Run on 2026-10-09 (afternoon):** `omarchy-4.0.4.iso` downloaded and verified (3.5 min), Omarchy installed itself
from the cidata ISO in under 2 min, and the ACES step passed **13 PASS, 0 FAIL** on its third attempt
(`rehearsal/reports/2026-10-09-aces-rehearsal-report.txt`): `omarchy update -y`, then all 16 packages from the signed
LAN repo under `SigLevel = Required DatabaseOptional` in 75 s, `aces doctor` reaching every rig endpoint from the VM,
`aces fabric refresh` reading the rig's `sim-lab-4` mode and five lanes, the engineering and voxel self-tests passing in
the venv, and `omarchy plugin validate` passing on the real host (the predicted symlink failure is gone: the plugin is
copied). Two checkpoints exist on the VM, `omarchy-clean` and `aces-installed`. The first two attempts found three kit
defects, all fixed: `get-iso.ps1` died on curl's stderr under PowerShell 5.1's error handling; `sudo -v` prompts on a
NOPASSWD host when the stock `%wheel` rule lacks NOPASSWD (`verifypw=all`), stalling an unattended run; and
`omarchy update` aborts with `OMARCHY_PATH` unbound in a plain ssh command, because Arch's bash reads no rc file there
(`validate.ps1` now uses a login shell and `aces-install.sh` sources Omarchy's `env-bootstrap`). Earlier container-stage
findings that still hold:

- `repo-add` in the build container leaves `aces.db` and `aces.files` as WSL symlinks that Windows programs cannot open,
  so `repo/` cannot be served as it is from Windows (`aces.db` answers 404); `serve-repo.ps1` serves real copies. The
  signing key is published to the VM only once every package has a `.sig`: with the key and no signatures,
  `SigLevel = Required` refuses every package.
- The cidata format has no post-install hook (archinstall's `packages` and `custom_commands` are not acted on by the
  Omarchy orchestrator), so the ACES overlay stays a post-install step over SSH or the console.
