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
| `aces-os` | 0.1.0-4 | The meta-package: `aces-base`, `aces-creative`, `aces-field`, `aces-consoles`, `aces-omarchy`, `aces-survey`, `aces-engineering`, `aces-voxel`; `aces-studio` stays optional until the rig's video lane exists; -4 adds `aces-cad-onshape` as an optional dependency (cloud adapter, not installed by default) | built with `--nodeps` (signed, -4 on 2026-10-09 14:50 UTC) | `pacman -Sp aces-os` resolves in a clean signed container: 508 packages when aces-base, aces-creative, aces-consoles and aces-omarchy are already installed, 529 when aces-base, aces-engineering and aces-voxel are (qgis, docker and the engineering venv's libraries included) |
| `aces-engineering` | 0.1.0-1 | One locked Python 3.14 venv at `/opt/aces/engineering/venv` (76 distributions, each hash-pinned in `requirements.txt`, binary wheels only; 907.6 MiB installed, 255 MB package) with a vendored snapshot of cudax-sim-runtime's `simlab` and `gendesign` (commit `28d92a8`, content sha256 in `SNAPSHOT`) on its path. Commands: `aces-simlab` (validate, local run, report, evidence, surrogate, export, capacity, `remote` MCP client), `aces-evidence` (ledger and bundle verification, relocates ledgers from other machines), `aces-gendesign` (list, selftest, topopt, parametric, nozzle, fusion); `mcp.json.example` for the rig's simlab `:8022` and rig-state `:8010`; LICENSES.md. Not installed: rig-only rq, redis, uvicorn, mcp, starlette, gmsh, rocketcea, physicsnemo, and the GPU packages cupy, warp, torch (mlflow too); the wrappers keep the queue, leases, MCP-server and console commands off the desktop CLI. Depends on `libglvnd` and `libx11` (the OCP wheel links libGL and libX11) | built (x86_64, signed); `check()` = `aces-gendesign selftest` passed | `check()`: 56/56 imports, fea3d element test, the 1 mm cantilever on NumPy reproduces the workstation's GPU record (0.1989054912444315 mm) to 8.3e-13 in 401 PCG iterations, evidence bundle and chain verified, md/html/docx report, 8 SIMP iterations, L-bracket STEP round trip at 26,694 voxels as on the workstation (needs `embreex`; trimesh's rtree fallback gives 26,616, all 78 differences on one 45-degree face). Clean container (own script, `SigLevel = Optional TrustAll`): relocated shebangs, `pyvenv.cfg` and bytecode paths, selftest again, `aces-voxel fea` of the bracket STEP at 2 mm (converged, 1,232 iterations), the workstation's 4-bundle evidence ledger verified (4 entries relocated) and a hand-edited bundle rejected, `aces-simlab remote` against a local mcp 2.0.0 server (JSON and SSE replies, bearer token, 401 on a wrong one), a 10-trial parametric study and a GP surrogate in a per-user registry, `pacman -R` leaves nothing under `/opt/aces`. After the full signed rebuild, `bin/install-test aces-base aces-engineering aces-voxel` passed under `SigLevel = Required DatabaseRequired`: `aces-engineering selftest: all hard checks passed`, `aces-voxel fea --self-test` PASS (STEP round trip at 26,694 voxels in 0.3 s) |
| `aces-voxel` | 0.1.0-1 | `aces-voxel`: `fea` (STEP to voxel H8 FEA through simlab's step-structures stages, or `--self-test`), `topopt` (3-D SIMP), `lattice` (Warp rod lattices, GPU only), `export` (density to STL); `docs/VOXEL_KERNEL.md` (what is standard, what is not, the source modules) in `/usr/share/doc/aces-voxel`; an `aces-voxel` agent skill in `/usr/share/aces/voxel/skills` (linked by `aces-omarchy-setup` 0.1.0-2). Runs in the aces-engineering venv, no second venv | built (any, signed) with its dependency installed (`pkgbuilds/ORDER` no longer marks it `nodeps`), so `check()` runs against the installed aces-engineering: fea self-test, 6 SIMP iterations, export, a build123d bar STEP through `fea`, `lattice` exits 2 without a GPU | Clean container: `--help`, `fea --self-test`, `fea` of the bracket STEP, `export` of a SIMP density, `lattice` exit 2 with its reason |
| `aces-cad-onshape` | 0.1.0-2 | -2 changes two unit tests only: the reply-that-cannot-be-encoded test uses a circular reference instead of a list nested 100,000 deep (json encoded that list on the GitHub runner, whose containers run with an unlimited stack, so CI run 37948580881 failed the package's `check()` while the same test passed on DARKNESS and the VM with the default 8 MB stack). Onshape adapter (cloud; every write, evaluation, export and pull runs the project class gate and is refused for sovereign and controlled projects): standard-library REST client, `aces-onshape` CLI (whoami, documents, create-document, elements, fs read/write, eval, features, add-feature, add-custom-feature, update-feature, delete-feature, version, massprops, export step/stl, pull), `aces-onshape-mcp` stdio MCP server (17 tools; descriptions name every write; changing a feature or Feature Studio it did not create needs confirm_foreign, writing into a public document needs allow_public), the `aces-cad-onshape` agent skill in `/usr/share/aces/cad-onshape/skills`, `mcp.json.example` with this server and the official Onshape FeatureScript MCP Server (Onshape Labs, `https://fs-mcp.labs.onshape.app/mcp`, browser sign-in after an App Store subscription). `aces-onshape pull onshape://<did>/<wid>/<eid> --project NAME` writes `part.step`, `parameters.json`, `source.json` and `outcome.json` under `~/Work/NAME/cad/onshape/` and a `cad.pull` ledger entry with the sha256 of the first three where `aces` offers `ledger record` | built (any, signed) on main, 0.1.0-2 on 2026-10-09 15:14 UTC with `bin/build aces-cad-onshape`: `check()` ran the 69 unit tests (the 62 originals plus the seven review regressions) against the fake Onshape server, all OK on Python 3.14.7, namcap reported nothing; the same 69 pass in the builder image with the runner's unlimited stack (`docker run --ulimit stack=-1:-1`), which is where 0.1.0-1's test 34c had failed in CI. 0.1.0-1 (14:49 UTC, same sources apart from that test) and the earlier unsigned worktree build (62 tests, before the review fixes) are superseded | Clean container, the default `bin/install-test` set (aces-base, aces-creative, aces-consoles, aces-omarchy, aces-cad-onshape) from the signed repo (`SigLevel = Required DatabaseRequired`, 2026-10-09 14:52 UTC, exit 0): `aces-onshape --help`; `aces-onshape whoami` exits 3 with `no Onshape API keys: ...`; the installed tests in `/usr/share/aces/cad-onshape/tests` 69/69 as root against the installed package; `pacman -Sp aces-os` (0.1.0-4) resolves to 508 packages. Earlier, unsigned, with `bin/install-test aces-base aces-omarchy aces-cad-onshape` (62/62 before the review fixes): `aces-omarchy-setup` links the skill as `~/.claude/skills/aces-cad-onshape` and `~/.agents/skills/aces-cad-onshape`, and `--remove` unlinks both. Workstation: 62 tests OK on Python 3.11.9 and 3.13.13 (one skipped there, the POSIX file-mode test), 62/62 on 3.14.7 in `aces-builder`; the stdio MCP server driven through the skill's whole loop against the fake server. Real Omarchy 4.0.4 (the rehearsal VM, 2026-10-09 14:55 UTC): `pacman -Sy aces-cad-onshape aces-os` from the signed LAN repo installed 0.1.0-1 and upgraded aces-os to 0.1.0-4 (pacman lists the new optional dependency as installed); `aces-onshape whoami` exits 3 with the keys message; the installed tests 69/69; `aces-omarchy-setup` linked the skill into `~/.claude/skills` and `~/.agents/skills`; `aces-onshape-mcp` answered `initialize` and `tools/list` (17 tools) over stdio with `ACES_ONSHAPE_PROJECT=golden-demo`; upgraded to 0.1.0-2 from the same repo at 15:16 UTC, installed tests 69/69 again, whoami exit 3. No live Onshape run yet (no credentials on DARKNESS or the VM) |
| `aces-cad` | 0.1.0-1 | Local text-to-CAD (no network, no quota, every project class): agents write build123d Python; `aces-cad-mcp` (stdio MCP server, 8 tools) and the `aces-cad` CLI run it in a sandboxed child of the aces-engineering venv (seccomp, Landlock, rlimits, a scrubbed environment; strict mode by default), report volume, area, bbox, validity and topology, inspect (mass, validity, holes, thickness, draft, radii, interference, section, measure, design space), render PNGs in the golden-path look, draw dimensioned DXF/SVG/PNG, and pull a run into `cad/local/` as STEP + parameters + sha256 with a `cad.pull` ledger entry; the `aces-cad` agent skill; no venv of its own (section "Local CAD" below) | built (any) from branch `cad-local/integration` at 032c1d0 with `bin/build aces-cad` in the integration worktree, **unsigned** (the worktree has no secret key; its repo held unsigned copies of main's packages), 2026-10-10 07:49 UTC: `check()` ran the 108 tests as the `builder` user on Python 3.14.7 in strict mode (no "strict-mode tests skipped" line), OK in 641.9 s with one skip (S02b: the makepkg tree has no lock file); namcap printed nothing; the package holds 138 files, 27 checked-hash `.pyc`, the two shims at 755, no `__pycache__` under the tests and no file naming the build directory. The first build of the merge (6e3f6ef, before the ledger-result fix) passed 107 in 428.2 s with the same skip | Clean `archlinux:base`, `bin/install-test aces-base aces-engineering aces-cad` from that unsigned repo (`SigLevel = Optional TrustAll`, 2026-10-10 07:51-08:01 UTC, exit 0), as root: `aces-cad doctor` ready, mode **strict**, seccomp active (106 BPF instructions, TSYNC), Landlock ABI 3 (no TCP rules and no scoping at that ABI; `/var/cache/fontconfig` absent, so skipped), 14 of 14 checks passed, memory, CPU and file-size limits enforced, PDEATHSIG on; the ACES plate 11607.300918301276 mm³, counts 1/1/7/15/10, valid, 5.5 s wall (imports 3.5 s), peak 500.0 MB; inspect 0.030991493451864402 kg in AlSi10Mg, one simple through Ø10 hole entering at (30, 20, 5), minimum thickness 5.0 mm (embree), validity ok; iso and front renders at 1024 × 800; drawing texts 60, 5, 40, Ø10 THRU, 30, 20 with 0 audit errors; the pull wrote `part.step`, `part.stl`, `part.3mf`, `parameters.json`, `source.py`, `source.json` and `outcome.json`, `geometry_sha256` `806c9be7a86c8ae60308ee75d9a910f371d5f5aae6fe6517fa5ce6aa5fd1742f`, the `cad.pull` ledger entry 7 (class sovereign), the CAD source recorded, `check_bundle.py` all files match, `aces ledger verify` ok (7 entries); the socket script kind sandbox exit 5, the loop kind timeout exit 4 (3.0 s); the installed tests as root 108, OK in 475.3 s (one skip, S02b); the golden block passed (45 s). As the non-root `builder` user in the `aces-builder` image with the package installed (the packaged layout): doctor ready and strict, the installed tests 108, OK in 521.9 s (S02b skipped), `aces-cad selftest` 13 passed, 0 failed, 0 skipped in 40.0 s, and the stdio MCP loop 17 of 17 (section "Local CAD"). `pacman -Sp aces-os` (0.1.0-5): 530 packages after this set, 509 in the default install test (0.1.0-4 there: 508) |

## Changed for the golden path

These versions were built in the Arch builder (`bin/build aces-base aces-omarchy aces-engineering aces-voxel`, signed)
and install-tested in a clean container (`bin/install-test` and `bin/install-test aces-base aces-engineering
aces-voxel`) on DARKNESS on 2026-10-09 (UTC), last after the review fixes (the same version-releases: none had been
published); the rows above describe the previous builds. The rehearsal VM has run these versions (the golden-path table
below, run `20261009T144006Z-b3ac99`), and CI has built them and passed both install tests (run 37946810947; only its
log upload failed, on file permissions, fixed with the merge that followed). The rehearsal web root
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
| CI (GitHub Actions run 37946810947 on commit 24e86af, ubuntu-latest: AMD EPYC 9V74, 2 logical CPUs, no GPU) | 2026-10-09 | same (`bin/install-test aces-base aces-engineering aces-voxel`, after the build step's makepkg `check()` had already run the 169 golden tests and the demo; build 7.5 min, default install test 50 s, this step 91 s). The run's only failure was the log upload after the tests: the copied run records were root-owned 0600 and the runner could not read them (fixed in `bin/install-test` with `chmod -R a+rX /logs/golden`) | pass (exit 0, 40 s including interpreter start) | verified-desktop | 40 (command time; result.json not kept) | `20261009T145633Z-b3ac99` | `0dcdf22e36c3` | 1 | 5 |
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

## Onshape adapter (`aces-cad-onshape`), 2026-10-09

**What it is.** `aces-cad-onshape` lets the default agent turn a request in words into FeatureScript, run it in Onshape,
read the errors and the geometry back, iterate, and hand the result to the engineering loop as STEP + parameters +
sha256. It is a standard-library REST client with the `aces-onshape` CLI, the `aces-onshape-mcp` stdio MCP server (17
tools) and the `aces-cad-onshape` agent skill, written as the "ACES REST client" of VISION.md's licence row (MIT, no
third-party code) for the `onshape://` pull of VISION.md section 5 step 2 and the `CadSource` pull. Onshape is a cloud
service, so every write, evaluation, export and pull runs the project class gate and refuses `sovereign` and
`controlled` projects before a request is sent.

**Source of truth.** Onshape's public OpenAPI 3.0.1 spec (`info.version` `1.221.89963-13d10d36cb23`, server
`https://cad.onshape.com/api/v17`, saved 2026-10-09) for every path, request body and response field; Onshape's public
FeatureScript documentation (the slot-feature tutorial, imports, the UI spec and the standard library reference) for
the skill; and Onshape's own texts on the official FeatureScript MCP Server: the blog posts of 3, 9 and 30 Sep 2026 and
the Tech Tip "How to Connect the FeatureScript MCP Server and Claude Code" (8 Sep 2026). The package's `README.md`
carries the details.

**Where the OpenAPI differs from or sharpens the brief.**

*Translation states.* The brief had translation jobs going from PENDING to DONE. `BTTranslationRequestState` is
`ACTIVE`, `DONE` or `FAILED`; there is no `PENDING`, and a failure carries `failureReason`. `GET /translations/{tid}`
asks clients not to poll several times a second and to back off exponentially, so the client waits 1 s
(`ACES_ONSHAPE_POLL_S`), then 1.5 times longer before each poll up to 10 s, gives up after 600 s
(`ACES_ONSHAPE_TRANSLATION_TIMEOUT_S`), and records every state change with its time and poll number in
`source.json`. The fake server goes `ACTIVE`, then `DONE` on the second poll.

*The STL redirect.* The brief expected STL bytes straight from `GET .../stl`; the operation "Returns a 307 redirect from
which to download the exported file". urllib's default redirect handler would forward `Authorization` to whatever host
the redirect names, so the client follows redirects itself (at most 5): it forwards `Authorization` only to the base
URL's own origin or between https `*.onshape.com` hosts, sends none to any other host (a pre-signed URL carries its own
authorisation), and shows redirect targets in traces and messages without their query. The `units` value goes out in
lower case (`millimeter`); whether Onshape accepts that case is open.

*The namespace forms (inferred from /fstable, unverified for features).* The brief said a custom feature instance
references its Feature Studio through a version (`d<did>::v<vid>::e<eid>::m<mv>`), so every instance would need
`POST /documents/d/{did}/versions` first. The OpenAPI documents namespace forms only for `/fstable` custom tables
(`tableNamespace`): `e{eid}::m{mid}` when the Feature Studio and the Part Studio are in the same workspace,
`d{did}::v{vid}::e{eid}::m{mid}` when they are in different workspaces, with `{mid}` the Feature Studio's
`microversionId` from getElementsInDocument; `BTMFeature-134.namespace` adds that std features have `""` and custom
features identify their Feature Studio. So `add-custom-feature` uses the namespace the Part Studio's own featurespecs
report for that studio, else the workspace form, and creates no version; `--pin-version` (`pin_version`) creates a
version first and uses the version form, and the skill retries once with it when the workspace form is rejected.
Versions are permanent, and one per agent iteration would litter the document.

*No documented compile errors on write.* The brief expected the studio write to return compile errors. Only
`BTFeatureScriptEvalResponse-1859` documents `notices` (level ERROR, WARNING, INFO, INTERNAL or UNKNOWN; type PARSE,
SEMANTIC, EXECUTION, MODELING, TEST, OTHER or UNKNOWN; a message); the write response, `BTFeatureStudioContents-2239`,
has only `btType`, `contents` and the base fields. So errors are surfaced through the featurespecs call after a write
(`specs_error`, plus a warning when code that defines features yields no specs), the feature status after
instantiation (`OK`, `INFO`, `WARNING`, `ERROR`) with a `getFeatureError`/`getFeatureWarning`/`getFeatureInfo` eval
probe, eval notices, and any `notices` the write response carries anyway, passed through.

**What the unit tests cover.** The tests in `src/aces-cad-onshape/tests` run against a fake Onshape server
(`http.server` on `127.0.0.1` with in-memory documents, versions, Feature Studios, features, translations and external
data, a 307 STL redirect, and knobs for injected statuses, lost responses and delays) and a fake `aces` CLI. There are
69 test methods: the spec's tests 1-56, with 14b, 19b, 47a-c and 55a-c as methods of their own, and the regression
tests 15b, 24b, 34b, 34c, 40b, 44b and 52b added with the review fixes of 2026-10-10. On 2026-10-09 the first 62 passed
on Python 3.11.9 and 3.13.13 on the workstation (1 of them skipped: the file-mode test is POSIX only), 62/62 on 3.14.7
in `aces-builder`, 62/62 in the package's `check()`, and 62/62 installed in a clean container. On 2026-10-10 all 69
passed on 3.11.9 and 3.13.13 on the workstation (1 skipped) and 69/69 on 3.14.7 in the `aces-builder` image, run from
the source tree; the package has not been rebuilt since, so `check()` and the clean-container run are still at 62.

- The client: the Basic header on every request; the 401 and 403 messages (Onshape's text kept, no secret); base URL
  validation; key precedence between the environment and `onshape.env` (a BOM, `export`, quotes, inline comments
  refused, empty values, the 0600 check); 429 with `Retry-After` in seconds and as an HTTP date, and the 60 s cap; 5xx
  retried only on idempotent calls; transport errors classified as not sent or possibly sent (`maybe_applied`); a
  delete that applied while its response was lost, and a 404 taken as "already deleted" only after an attempt that may
  have applied (not after a 429 or a request that was never sent); ids with a trailing newline refused before any
  request; the redirect auth rule, and an STL redirect to another origin that receives no `Authorization`; a download
  retry with a fresh hash; ids in responses validated before use.
- The FeatureScript helpers: header parsing and prepending, value decoding, parameter building (quantities, booleans,
  strings, queries, enums and their namespaces), namespaces, references, imports and declared enums; header and import
  scans that stay linear on comment-heavy and identifier-heavy text (run in a child process with a timeout).
- The operations: the Feature Studio round trip; eval without `queries`; adding, updating and deleting features;
  versions; STEP export polling and its sha256; the custom-feature namespace preference (Part Studio featurespecs,
  computed, version form), `pin_version` creating exactly one version and none for an unknown feature type; the error
  probe; the `specs_error` classification; the pull bundle (layout, hashes, `source.json` never rewritten, the Feature
  Studio capture with workspace, linked and blob imports and the depth limit, `source.json`'s `ledger` note,
  `outcome.json` with the sha256 of `source.json`, no `.partial` left, a lone surrogate from Onshape written as its JSON
  escape); export names; the ledger entry and `add-cad-source` through the fake `aces` in its new and old modes.
- The guards: the class gate for sovereign, controlled, unknown and empty classes with zero requests sent (pull, studio
  write, add feature, eval); the bound project; the `aces` CLI reading projects under the adapter's own `ACES_WORK`
  (an empty one included); the foreign-target and public-document guards; no secret in any file, output or ledger
  payload.
- The CLI's exit codes and redaction, and the MCP server over stdio (initialize, tools/list, tools/call, argument
  errors, session ownership, redaction; a line nested too deep for json, lone surrogates in an id or in Onshape's text,
  ids that are not a string, a number or null, and a reply that cannot be encoded, after each of which the server still
  answers).
- Isolation: every test clears the `ONSHAPE_*`, `ACES_*` and proxy variables, points `HOME`, `USERPROFILE` and the ACES
  directories at a temp directory, and refuses any connection that is not to loopback, so the tests never read the
  operator's keys or reach Onshape.

**What is not verified.** There is no Onshape account on DARKNESS, so nothing has run against Onshape. The open
questions, numbered for the live check:

1. Whether a REST write to a Feature Studio commits it (in Onshape's UI, changes reach other tabs only after Commit).
2. Whether the Part Studio featurespecs report, in `namespace`, the string `POST .../features` needs for a custom
   feature (the adapter prefers it when it names the same studio).
3. Whether `POST .../features` accepts the `/fstable` namespace forms, including a version of the same document, and
   whether an instance follows later studio writes or must be re-pointed (`--replace`).
4. Whether a parameter whose featurespec has no `defaultValue` may be left out of the instance (the adapter omits it).
5. Whether the studio featurespecs' `namespace` and `defaultValue` are usable verbatim.
6. Where the compile errors of a studio write appear.
7. Whether `makeId("<feature id>")` addresses a top-level feature for `getFeatureError`, `getFeatureWarning` and
   `getFeatureInfo` in the eval probe.
8. The shape of error bodies (the adapter shows a JSON `message` when there is one).
9. Whether 429 answers carry `Retry-After`, and which status plan and API-allocation limits answer with.
10. The eval `queries` format and what `console` holds (println output from the lambda is expected there).
11. The target of the `/stl` 307 redirect, whether it needs `Authorization`, and whether lower-case `units` is
    accepted.
12. Translation timing, when `resultDocumentId` is set, and the formats of translation and external-data ids.
13. The official FeatureScript MCP Server's tool list and OAuth scopes.
14. Mass-property units (the schema states none).
15. Free-plan public documents: whether `isPublic: false` holds on a paid plan and is overridden on a free one.
16. The namespace a `BTMParameterEnum-145` needs, for an enum the studio declares and for a std enum it re-exports.
17. Whether an element microversion id is a valid document microversion id (the pull would then read the source a
    stale namespace points at; today it reads the current source and warns).
18. How much API allocation each operation consumes.

**Live check (Aaron).** Record what Onshape actually returned at every step:

1. Create API keys with read and write access in the developer portal (`https://cad.onshape.com/appstore/dev-portal`).
2. Run `install -Dm600 /dev/null ~/.config/aces/onshape.env`, then add `ONSHAPE_ACCESS_KEY=...` and
   `ONSHAPE_SECRET_KEY=...` with an editor (no inline comments).
3. `aces-onshape whoami` must exit 0. Note `plan_group`: on a free plan, documents are public.
4. `aces project new onshape-check --class personal` and `aces project new onshape-sov --class sovereign`.
5. `aces-onshape create-document "ACES onshape check" --project onshape-check --json` gives `did`, `wid` and `public`.
   Then `aces-onshape elements <did> w <wid> --type PARTSTUDIO --json` gives the Part Studio id `<ps>`.
6. `aces-onshape fs write <did> <wid> plate.fs --name "ACES plate" --project onshape-check --json`, where `plate.fs` is
   the skill's example. Record `feature_specs`, `specs_error` and any `notices`.
7. `aces-onshape add-custom-feature <did> <wid> <ps> <studio> acesPlate --param length="60 mm" --param width="40 mm" --param thickness="5 mm" --param holeDiameter="10 mm" --project onshape-check --json`.
   Record `namespace`, `namespace_source`, `featurespec_namespace`, `partstudio_featurespec_namespace`,
   `feature_status` and `messages`.
8. `aces-onshape eval <did> w <wid> <ps> readback.fs --project onshape-check`, where `readback.fs` is the skill's
   readback lambda. Expect 1 part, a bounding box of 60 x 40 x 5 mm, and a volume near 60·40·5 − π·5²·5 ≈ 11 607 mm³.
9. Re-point the instance through a version:
   `aces-onshape add-custom-feature <did> <wid> <ps> <studio> acesPlate ... --pin-version --replace <fid> --confirm-foreign --project onshape-check --json`.
   Record whether the `d::v::e::m` form was accepted. The readback must still show 1 part.
10. Enum check. Add `export enum PlateCorner { SQUARE, ROUND }` to `plate.fs`, and
    `annotation { "Name" : "Corner" } definition.corner is PlateCorner;` to its precondition (the body may ignore it).
    Then:
    - `aces-onshape fs write <did> <wid> plate.fs --eid <studio> --confirm-foreign --project onshape-check --json`;
    - `aces-onshape add-custom-feature ... --param corner=ROUND --replace <fid> --confirm-foreign --project onshape-check --json`.

    Record `parameters_sent` (the namespace sent and `spec_namespace`) and whether the feature regenerated.
11. `aces-onshape pull onshape://<did>/<wid>/<ps> --project onshape-check --json`, then `aces ledger verify`. Check
    `parameters.json` (`feature_studios`, `namespace_current`, `imports`) and `source.json` (`document.public`). With
    aces-base 0.1.0 the `cad.pull` ledger entry is not written (no `ledger record`); `source.json`
    (`ledger.available` false) and `outcome.json` say so, and `outcome.json` holds the sha256 of `source.json`.
12. Sovereign refusals. Each of these must exit 4, and `--verbose` must print no HTTP line:
    - `aces-onshape --verbose pull onshape://<did>/<wid>/<ps> --project onshape-sov`;
    - `aces-onshape --verbose fs write <did> <wid> plate.fs --project onshape-sov`;
    - `aces-onshape --verbose eval <did> w <wid> <ps> readback.fs --project onshape-sov`.
13. `aces-onshape --verbose export stl <did> w <wid> <ps> --project onshape-check --json`. Record the redirect host (the
    trace shows scheme, host and path only) and whether `units=millimeter` was accepted.
14. Optional: connect the official FeatureScript MCP Server per the Tech Tip, and record its tool list, whether its
    sign-in is OAuth (the address of the Authorize page, for example an `/oauth/authorize` path, and what `/mcp` shows),
    and the scopes it asked for.
15. Write the answers to open questions 1-18 into this section.

Not run yet: no Onshape credentials on DARKNESS (2026-10-09).

**The official FeatureScript MCP Server.** Source: Onshape's Tech Tip "How to Connect the FeatureScript MCP Server and
Claude Code" (8 Sep 2026, `https://www.onshape.com/en/resource-center/tech-tips/connect-featurescript-mcp-server-claude-code`)
and the blog posts above.

- It launched through Onshape Labs, an early-access program, and is available through the Onshape App Store
  (`https://cad.onshape.com/appstore/apps/Onshape%20Labs/6a29aea7c03f8bf659841734`); the user subscribes there first.
- Claude Code connects with `claude mcp add --transport http onshape https://fs-mcp.labs.onshape.app/mcp`; the
  `onshape-featurescript` entry in `mcp.json.example` is the same server.
- Sign-in runs in the browser: launching Claude Code asks you to authorize the Onshape subscription on an "Authorize"
  page, and `/mcp` then brings up the authentication screen that connects the Onshape account. It uses neither API keys
  nor `onshape.env`. Onshape's texts do not name the protocol: OAuth is inferred from MCP's authorization model and is
  checked in live-check step 14.
- Its calls draw on the user's API allocation, or the company's when the user asks for that.
- Per Onshape's posts, the assistant can write FeatureScript directly into a document, review errors, iterate on the
  code and inspect the geometry its code produces.
- Not connected from this machine; tool list and OAuth scopes unknown.
- Never for sovereign or controlled projects: the ACES class gate cannot see its calls.

## Local CAD (aces-cad), 2026-10-10

**Why.** Aaron, 2026-10-10: "The cloud-based system from Onshape is useless: the API calls will burn through the annual
allocation straight away, and I don't feel like paying huge amounts if we can build our own integration." `aces-cad` is
that integration. The agent (Claude Code, OpenCode, Codex, Hermes) writes build123d Python; this machine runs it in a
sandboxed child of the aces-engineering venv; the agent inspects, renders, draws and fixes the part, then pulls it into
the ACES project as STEP + parameters + sha256 with a `cad.pull` ledger entry. No API call, no allocation and no
network, so every project class may use it, sovereign and controlled included: the class gate still runs on every tool
and records the class, and an unknown or missing class is refused with the `aces project classify` fix. The package's
`README.md` (`/usr/share/doc/aces-cad/README.md`) has the details.

**Process model.**

- `aces-cad-mcp` (stdio MCP server) and the `aces-cad` CLI run on the system `python3` with the standard library only.
  They never import a network module and never `exec` agent code.
- Every geometry job is a fresh child process: `/opt/aces/engineering/venv/bin/python -I -B -X utf8 -X faulthandler
  /opt/aces/cad/lib/aces_cad/worker/main.py`, with the job JSON on stdin, stdout and stderr in files of the job
  directory, and `result.json` written atomically and validated by the parent. Jobs: `run` (the only one that runs agent
  code) and the data-only `env`, `selftest`, `inspect`, `render`, `drawing` and `export`. Nothing is kept between jobs,
  so an OCCT crash or hang costs one worker, never the server; the price is the imports, about 2 s per job (probe
  below).
- The server reads requests on one thread and runs tool calls on one executor thread (at most 8 waiting).
  `notifications/cancelled` kills the running job, progress notifications go out every 5 s, and the hostile-input
  hardening is aces-onshape's plus a 4 MiB line cap.
- Runs live in `<ACES_WORK>/<project>/cad/local/runs/<run_id>/` (mode 0700). Failed runs are kept, so an agent that lost
  its context gets the script back with `cad_runs`; outputs are promoted from the job directory only after validation.

**Sandbox.** In the order the worker installs it, before any heavy import:

1. its own session, `PR_SET_PDEATHSIG` (the worker dies with its parent) and nice 10;
2. rlimits with soft = hard: address space 4096 MiB, file size 1024 MiB, at most 1024 open files, no core dumps, CPU
   time just past the wall clock; 2 threads for BLAS/OpenMP and the OCCT pool;
3. seccomp-bpf (x86_64, 106 instructions, applied to every thread with TSYNC): `socket` fails with EACCES; `fork`,
   `vfork`, `execve`, `execveat`, `ptrace`, `unshare`, `setns`, `bpf`, `io_uring_*`, `pidfd_*`, `userfaultfd` and the
   other listed calls fail with EPERM; `clone` only makes threads; `kill`, `tgkill`, `prlimit64`, `sched_setaffinity`
   and `setpriority` reach only the worker itself;
4. Landlock: read-only `/usr`, `/etc`, `/opt/aces`, the venv and the run directory; read-write only the job directory,
   where symlinks, FIFOs, sockets, devices and executables cannot be created; `$HOME`, `/tmp`, `/run`, `/dev/shm` and
   other processes' `/proc` are out of reach; ABI 4 and later deny all TCP a second time, ABI 6 and later scope signals
   and abstract sockets;
5. a scrubbed environment (an allowlist: no `ACES_*`, `ONSHAPE_*`, proxy, SSH agent, display or D-Bus variables);
6. op `run` only, after the imports: a 16 MiB memory reserve (so a MemoryError is reported, not a crash) and the Python
   audit hook, which refuses sockets, processes, `ctypes`, tracing and writes outside the job directory with a clear
   message. The hook is not a boundary: OCCT's C++ I/O bypasses it.

The parent adds the wall clock (`cad_run` 120 s by default, at most 600 s), size caps on stdout, stderr and the job
directory, and a process-group kill (`killpg`; `taskkill /F /T` on Windows) so that no grandchild survives.
`ACES_CAD_SANDBOX=strict`, the default, runs no agent code unless seccomp **and** Landlock are both active (otherwise
exit 3, kind `sandbox_unavailable`); `permissive` runs with whatever is available, and every result then says `confined:
false` and names what is missing. Windows and macOS are permissive only (development: the audit hook, the wall clock and
the tree kill).

**Probe (B3's run of the judge's sandbox probe, 2026-10-10).** `archlinux:base` on Docker Desktop, kernel
6.6.114.1-microsoft-standard-WSL2 (no `/sys/kernel/security/lsm` in the container), aces-engineering 0.1.0-2's venv
(Python 3.14.7, build123d 0.11.1, OCP 7.9.3.1, trimesh 5.1.0, ezdxf 1.4.4, numpy 2.4.6), as uid 1000, guards installed
before the imports, RLIMIT_AS 4096 MiB, 2 threads:

- seccomp active (106 BPF instructions, TSYNC); Landlock ABI 3 (no TCP rules and no scoping at this ABI), every rule
  added, no root skipped;
- imports 2.02 s; VmPeak 1256184 kB, VmHWM 566140 kB, 16 threads;
- worked inside the guards: a two-hole plate (11214.60183660255 mm³), BREP, STEP (sha256
  `ef07b1340bc61c27c9cae49dfc2e8b074e523fce5d46c31ace9bf4b14a2565d8`), STL, 3MF, `BRepAlgoAPI_Check`, embree rays
  (`trimesh.ray.ray_pyembree`, thickness 4.9999), a watertight SDF mesh, a matmul, a Python thread, a matplotlib PNG, an
  ezdxf dimension (60.0, audit 0 errors) and an ezdxf PNG preview;
- refused: `socket` (AF_INET and AF_UNIX) errno 13; `fork` and `posix_spawn` errno 1; `kill(parent | 1 | -1, 0)` errno 1
  (its own pid allowed); `prlimit` on the parent errno 1; raising the hard RLIMIT_AS ValueError; `setpriority` on the
  user or the parent errno 1; `sched_setaffinity` on the parent errno 1; `symlink` and `mkfifo` in the run directory
  errno 13; a hard link of a secret into it errno 18; reading the secret, listing `$HOME`, and reading
  `/proc/<parent>/environ` and `/proc/1/environ` errno 13; writing `/tmp` or `/dev/shm` errno 13; OCCT `export_step`
  outside the job directory `RuntimeError: Failed to write STEP file`, and no file appeared;
- allowed: reading `/etc/hostname`, writing `/dev/null`;
- memory: a growing list hit MemoryError at VmHWM 1678912 kB under RLIMIT_AS 2048 MiB and at 3777008 kB under 4096 MiB;
  `np.ones(2**33)` hit MemoryError under both; an OCCT allocation bomb exited 1 under both, which is why the worker
  keeps a memory reserve.

**Tools (8).** `cad_start` (the class gate, the project's `cad/NOTES.md` lessons, the last runs, the conventions, the
limits and the sandbox status), `cad_run` (inline script, a project `.py` or a re-run, with parameter overrides and
input files; per-part volume, area, bbox, centroid, validity and topology), `cad_runs` (list or show runs, with the
script), `cad_inspect` (mass, validity, topology, holes, thickness, draft, radii, interference, section, measure,
design_space), `cad_render` (PNG views in the golden-path look, inline), `cad_drawing` (DXF with dimensions, plus SVG
and PNG rendered from it), `cad_pull` (the CadSource pull) and `cad_note` (one lesson appended verbatim to
`cad/NOTES.md`). The CLI has the same operations plus `doctor`, `selftest`, `params`, `prune` and `notes`.

**Pull bundle.** `cad_pull` re-derives the numbers in an export worker that runs no agent code and writes
`<ACES_WORK>/<project>/cad/local/<UTC stamp>-<name>/`: `part.step` (from the run's BREP, header time
`2000-01-01T00:00:00`, part names as labels), `part.stl`, `part.3mf` (written, never compared: it is not
byte-reproducible), `parameters.json`, `source.py` (its sha256 checked against the run), `inputs/`, `source.json`
(sha256 and size of every file, versions, environment, sandbox) and `outcome.json` (the ledger and cad-source outcomes,
with the sha256 of `source.json`). `geometry_sha256` is the sha256 of `part.step` (of `part.stl` for mesh runs). The
`cad.pull` ledger entry carries the class, the bundle, every file's sha256 and the versions, and `aces project
add-cad-source` records `cad/local/<bundle>/part.step`. An invalid part is refused unless `allow_invalid` is set, and
`valid` is recorded either way. The pull result names the golden hand-off (`aces golden init` or `aces golden run
--step`).

**FreeCAD is not used.** In a clean `archlinux:base` (B3's run, 2026-10-10, resolution only: `pacman -Sp freecad`,
nothing installed), FreeCAD 1.1.4-3 (LGPL-2.0-only in Arch's metadata) brings 141 packages, 403.5 MiB to download and
1905.3 MiB installed, and architect B's probe found no headless page-SVG export in TechDraw. build123d's OCCT
hidden-line removal plus ezdxf makes the drawing (DIMENSION entities in the DXF, SVG and PNG rendered from the DXF)
without it.

**Changed for local CAD.**

| Package | Version | What changed | Test evidence |
|---|---|---|---|
| `aces-cad` | 0.1.0-1 | new (its row is in "Built and tested") | built and install-tested from the merged branch (its row, and "Integration" below) |
| `aces-os` | 0.1.0-5 | `aces-cad` added to `depends` (the sovereign CAD path). Spec section 3: golden-rig/packaging (45f2c16, whose aces-os 0.1.0-5 adds `calculix-ccx-bin`) was not an ancestor of main (1680edf) when this integration started, so this is 0.1.0-5 against main. Whichever of the two merges second renumbers: golden-rig's aces-os must become 0.1.0-6 with both comment lines and both `'calculix-ccx-bin'` and `'aces-cad'` in `depends` (a trial `git merge-tree` against golden-rig/packaging and golden-rig/integration conflicts in this file only), and that aces-os does not resolve until calculix-ccx-bin builds (golden-rig records that 2.23-1 needs `libgfortran.so.4`) | built (any, `--nodeps`, unsigned, integration worktree) with `bin/build aces-os`; `pacman -Sp aces-os` resolves: 509 packages in the default install test (0.1.0-4 there: 508, so aces-cad adds one package), 530 after aces-base, aces-engineering and aces-cad are installed |

`bin/install-test` gains an aces-cad block, which runs when aces-cad is in the package set (doctor, the ACES plate run,
inspected, rendered, drawn and pulled in a sovereign project, `check_bundle.py`, the ledger, two hostile scripts by exit
code, the installed tests; artefacts in `logs/cad/`), and CI a third install test, `bin/install-test aces-base
aces-engineering aces-cad`. The default install test is unchanged.

**Integration (2026-10-10).** The three builder branches (B1 ef59108, B2 5b5b8c6, B3 2008e7e) merged into
`cad-local/integration` from main 1680edf without a conflict: no file is shared by two builders, and no file on the
spec's never-edit list changed. The branch is named `cad-local/integration` because git cannot hold a branch
`cad-local` next to the builders' `cad-local/b1..b3`. The merged code showed these defects, each fixed in its own
commit:

- aces-os back to 0.1.0-5 (spec section 3, the table above).
- Children of the parent no longer inherit its stdin. `project.run_aces`, and the Windows `taskkill` of a job, gave
  every child the server's stdin, which under `aces-cad-mcp` is the JSON-RPC pipe. On Windows each aces CLI call then
  waited out its 30 s timeout (B3's probe: 8 s and still waiting with the pipe, 0.08 s with stdin on the null device);
  on Linux a child that read stdin would have consumed MCP input. Children now get the null device.
- The `cad.pull` result keeps `n`, `hash` and `path`. The `--print json` line of `aces ledger record` was cut at 300
  characters before it was parsed; that line is about 245 characters plus the ledger path, so a long path (any Windows
  path, a Linux path over about 55 characters) left only `{"written": true, "output": ...}`. Found by the stdio loop
  below on Windows; a new U03 test fails on the old code and passes on the fix.
- The 100-hole cap is reported only when a part has more holes. `geom.find_holes` cut its own records at 100, so the
  inspect `holes_truncated` flag could never be true for one part and the drawing's note fired at exactly 100 holes;
  both callers now ask for 101.
- Render, drawing and pull remove their job directories under the call's environment (`ACES_CAD_KEEP_JOBS`), as
  `OpContext.finish` does.

Results (runs of 2026-10-10, UTC):

- Workstation, Windows 11, Python 3.11.9 parent, the workstation venv as the worker (build123d 0.11.1, OCP 7.9.3.1),
  `ACES_CAD_SANDBOX=permissive`, `ACES_CAD_REQUIRE_WORKER=1`: the merge as it came from the builders (6ad086f) ran 107
  tests OK in 1161.5 s; the final branch (032c1d0) ran 108 OK in 1235.7 s. Both skipped the same 14: the strict-only
  H06, H07, H08 and H12; H03, H14, U06 and the FIFO check (POSIX or Linux only); four U04 checks and W06 (symlinks need
  a privilege there); S02b, because those runs used an archive of `src/aces-cad` alone (the static tests from the full
  tree, 8 of 8, ran S02b and S03 against the real lock). `aces-cad selftest`: 12 passed, 0 failed, 1 skipped (9d needs
  strict) in 79.7 s.
- Linux: the package build and the install tests in the row above, all in strict mode on Python 3.14.7.
- The agent loop through the stdio server, against a scratch project made by the real aces CLI (`aces project new
  cadloop --class sovereign`; aces-base 0.1.0-3, the installed package on Linux and `src/aces-base` on Windows):
  `initialize`, `tools/list` (the 8 tools), `cad_start`, the ACES plate written to `cad/src/plate.py` and run by
  `script_path` with `render` and a progress token, `cad_inspect` (mass in AlSi10Mg, validity, holes, thickness),
  `cad_render` (iso, front and cutaway, inline), `cad_drawing` (inline), `cad_pull`, `cad_note`, `cad_runs`, `cad_start`
  again (the note comes back) and EOF; then `aces ledger verify`, `aces ledger show --kind cad.pull` (class sovereign),
  `aces project show` (the CAD source) and `check_bundle.py`. 17 of 17 steps passed on Linux as uid 1000 in strict mode
  (tool calls of 2.9, 6.9, 3.5, 4.7, 4.2 and 3.6 s from start to pull; no job ran long enough for a progress
  notification) and on Windows in permissive mode (6.7, 14.3, 6.5, 7.9, 6.5 and 7.1 s; progress at 5.3 of 120 s in the
  run job and 10.6 of 125.3 s in its render job). No call waited on a timeout, every reply line was ASCII, and the
  server exited 0 at EOF.
- The images were looked at: the iso, front and cutaway renders in the dark look, captioned "designed in build123d
  (aces-cad)", and the drawing PNG in third-angle layout reading 60, 40, 5, 30, 20 and Ø10 THRU above the title block.
- The ACES plate's `geometry_sha256` was `806c9be7a86c8ae60308ee75d9a910f371d5f5aae6fe6517fa5ce6aa5fd1742f` in every
  run on Windows (Python 3.11.9) and on Arch (Python 3.14.7); `part.stl` differed (`1dfaf2d9aae7…` on Windows,
  `3d24ea3da0fd…` on Arch), so STL hashes are not compared across operating systems.

**Not verified.**

- CI has not run the merged branch (nothing is pushed), the packages above are unsigned integration builds, and the VM
  acceptance (section 29) has not run.
- Whether the VM kernel has Landlock, and which ABI (acceptance step 2). The Docker Desktop kernel used here,
  `6.6.114.1-microsoft-standard-WSL2`, offers ABI 3, so the TCP rules (ABI 4) and the signal and abstract-socket
  scoping (ABI 6) are written but not exercised.
- Whether GitHub runners allow the Landlock syscalls under Docker's seccomp profile; if not, the CI install test runs
  permissive and says so in its log.
- Which MCP clients show image content to the model (acceptance step 9); the loop above checked only that the server
  sends valid PNG image content.
- Cross-OS equality of `geometry_sha256` beyond the ACES plate on the two platforms above, and on the VM (acceptance
  step 8).

**Known gaps (aces-cad).**

- Strict mode needs Landlock (and x86_64 for the seccomp filter): a kernel without it refuses every agent run until the
  operator chooses permissive, which runs without the filesystem guard and says so in every result.
- 3MF is not byte-reproducible; its sha256 is recorded and never compared.
- Mesh pulls (SDF and TPMS results) are STL only: no 3MF and no faceted STEP.
- One sourced material (AlSi10Mg, 2670 kg/m³, the golden demo's nominal value); others go in
  `~/.config/aces/cad-materials.json` with a non-empty `source`.
- Drawings have no section hatching, no radius dimensions and no centre marks, and they are not GD&T.
- No `aces cad` passthrough in aces-base 0.1.0: the commands are `aces-cad` and `aces-cad-mcp`.
- The aces-omarchy agent skill does not mention aces-cad yet (golden-rig owns that file now).

**VM acceptance** (exact commands in `rehearsal/README.md`, "aces-cad on the VM"). On the Omarchy VM: install aces-cad
from the LAN repo; `aces-cad doctor` (expected: ready, strict, seccomp 106 instructions with TSYNC, the Landlock ABI
recorded); `aces-cad selftest`; a sovereign project; the ACES plate (11607.300918301276 ± 1e-6 mm³, 60 × 40 × 5 mm,
counts 1/1/7/15/10, valid); inspect (one through Ø10 hole entering at (30, 20, 5), minimum thickness 5.0 mm, mass 0.0310
kg in AlSi10Mg); the renders and the drawing checked by eye (60, 40, 5, 30, 20 and Ø10 THRU); pull, `check_bundle.py`,
`aces ledger verify`, the `cad.pull` entry with `class: sovereign`, and a second pull with the same `geometry_sha256`;
the agent loop in Claude Code, with Esc during a long run; the hostile scripts as the desktop user (socket exit 5, loop
exit 4, memory exit 4, secret exit 5, OCCT export outside fails, kill parent exit 5); desktop responsiveness during a 60
s loop; optionally the golden hand-off. Not run yet (2026-10-10).

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
- aces-cad-onshape has not run against a live Onshape account; see its section.

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
