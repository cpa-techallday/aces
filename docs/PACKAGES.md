# ACES OS package register

State as of 2026-10-09. Packages are built by `bin/build` inside the `aces-builder` image (Arch `base-devel`) into the
local `[aces]` repo at `repo/x86_64/`, and install-tested by `bin/install-test` in a clean `archlinux:base` container.
Nothing here is signed yet (`SigLevel = Optional TrustAll` on the local repo); signing is a distribution-gate item.

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
| `aces-omarchy` | 0.1.0-1 | Overlay for Omarchy: `post-boot.d` and `post-update.d` hooks, the `aces-engineering` agent skill, the `aces-fabric` bar-widget plugin, `aces-omarchy-setup` | built | Installed; `aces-omarchy-setup` linked the hooks, the skill into `~/.claude/skills` and `~/.agents/skills`, and the plugin, then `--remove` cleared all of it. **The plugin QML is unvalidated**: there is no `omarchy-shell` on the build host, so it was written against the published manifest contract and the first-party port-forward plugin; run `omarchy plugin validate` on the Omarchy host |
| `aces-field` | 0.1.0-1 | Meta-package over the Arch-packaged field tools (RF receive and analysis, firmware analysis, device access over SSH, CAN, serial); AUR-only members are optional until forked | built with `--nodeps` | Not installed (multi-GB); `pacman -Sp aces-field` resolves a 372-package transaction against the official repos, so every hard dependency exists |
| `aces-survey` | 0.1.0-1 | Survey and photogrammetry: `aces-survey-setup` (installs the vendored FlyPath 3.0.0 QGIS plugin into the user's QGIS profile and enables it; creates the WebODM tree), `aces-webodm` (start/stop/status/open/update/logs over the pinned WebODM v3.3.0 tree under Docker, GPU node when nvidia-smi and the Docker nvidia runtime are present), a WebODM desktop entry, README with the DJI Fly mission-loading procedure, LICENSES.md. Depends on `qgis`, `python-shapely`, `python-pyproj`, `docker`, `docker-compose`; `cloudcompare`, `colmap`, `meshroom` (AUR) and `nvidia-container-toolkit` optional. FlyPath zip and WebODM release tarball are fetched at build time with pinned sha256s (34.7 MB package) | built with `--nodeps` (QGIS is not needed to build an `any` package) | Clean container: installed with `-Sdd`; `aces-survey-setup` placed FlyPath 3.0.0 and wrote `[PythonPlugins] FlyPath=true`, created the WebODM 3.3.0 tree; `aces-webodm --help` prints usage and `status` reports that docker is missing instead of crashing; `--remove` cleared both. `pacman -Sp aces-survey` resolves a 256-package transaction. On DARKNESS (Windows, Docker Desktop) the same stack runs outside the package: `GPU_NVIDIA=true ./webodm.sh start --port 8090 --detached` brought up webapp, worker, db, broker and `node-odx-1` (`webodm/nodeodx:gpu`), the node sees the RTX PRO 4000, the console answers HTTP 302 on :8090; FlyPath 3.0.0 imports under QGIS 3.44.15 (OSGeo4W, user-profile install) with shapely 2.1.2 and pyproj 3.8.0 |
| `aces-os` | 0.1.0-2 | The meta-package: `aces-base`, `aces-creative`, `aces-field`, `aces-consoles`, `aces-omarchy`, `aces-survey` | built with `--nodeps` | `pacman -Sp aces-os` resolves a 551-package transaction in a clean container (qgis and docker included); the install-test container reports 506 because aces-base, aces-creative, aces-consoles and aces-omarchy are already installed there |

## Not yet written

| Package | What it will be | Why it waits |
|---|---|---|
| `aces-engineering` | venv over the cudax-sim-runtime simlab client and gendesign, the SIMLAB console service | needs the cudax-sim-runtime client split out and a rig hand-off of the console |
| `aces-voxel` | the voxel kernel standard (voxel H8 FEA, SIMP) as an installable library | same source split |
| `aces-studio` | ACES Video Studio client and LoRA registry client | the server side (ComfyUI + ltx-pipelines workers) is not deployed |

They are listed as optional dependencies of `aces-os` so the meta-package resolves honestly today.

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
- Corrected 2026-10-09 in `src/aces-base` (rebuild pending): the simlab MCP is on `http://192.168.1.65:8022/mcp` (port 8020
  serves the Univer workbench) and the SIMLAB console is live on the rig at `http://192.168.1.65:8032`; the earlier defaults
  (8020 and the workstation's 8030) were wrong.
- `aces-field`'s AUR-only members (`can-utils`, `python-cantools`, `python-udsoncan`, `python-snap7`, `python-pyvisa`,
  `openems`) are optional dependencies until their recipes are forked into `pkgbuilds/`.
- The ArtCraft recipes are byte-for-byte AUR copies; `namcap` warns about a literal `x86_64` in each (upstream style).
- `aces-survey`: CloudCompare, COLMAP and Meshroom are AUR-only, so they stay optional until their recipes are forked;
  the WebODM docker images (`webodm/webodm_webapp`, `webodm_db`, `nodeodx`, `nodeodx:gpu`) are pulled from Docker
  Hub at first start, not shipped; `aces-webodm` has not been run on an Omarchy host yet (the Docker path was
  exercised on DARKNESS under Docker Desktop); the DJI Fly loading step is manual by design (the app has no import).
- No git repository and no signing key yet (both day-14 gate items in VISION.md §11).

## How to reproduce

```bash
bash bin/build && bash bin/install-test
```

Logs for every package are in `logs/<pkg>.log` and the install test in `logs/install-test.log`; the repo database is
`repo/x86_64/aces.db.tar.zst`. Sources under `src/<pkg>/` are packed at build time, so a source edit needs a rebuild; a
rebuild without a `pkgrel` bump replaces the package in the local repo in place (fine for development, never for a published
channel).
