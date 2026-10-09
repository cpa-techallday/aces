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
| `aces-omarchy` | 0.1.0-4 | Overlay for Omarchy: `post-boot.d` and `post-update.d` hooks, the `aces-engineering` agent skill, the `aces-fabric` bar-widget plugin, `aces-omarchy-setup` (links every skill any ACES package ships under `/usr/share/aces/*/skills`, copies the plugin, and puts the widget on the bar: through `omarchy plugin enable` when the shell runs, else by adding `{"id": "aces-fabric"}` to the bar's right section in `~/.config/omarchy/shell.json`, which is where the shell's PluginRegistry reads enabled bar widgets from; `--remove` takes it off again) | built | Installed on a real Omarchy 4.0.4 (the rehearsal VM, 2026-10-09): `aces-omarchy-setup` linked the hooks and the skills into `~/.claude`, `~/.codex`, `~/.pi/agent` and `~/.agents`, copied the plugin, and **`omarchy plugin validate` passed on both the copy and the packaged directory**. `qmllint` against Omarchy's own `qs.Commons`/`qs.Ui` modules found two real defects, fixed in -3 (a `state` property shadowing Item's, and an unresolvable parameter type in the `Process.exited` handler); the remaining warning classes also occur in Omarchy's first-party plugins. -4 adds the bar placement: a copied plugin is discovered but off the bar until `shell.json` names it (`omarchy plugin enable` needs the running shell, which an SSH session does not have). Not yet seen rendering live until someone logs into the VM's desktop |
| `aces-field` | 0.1.0-1 | Meta-package over the Arch-packaged field tools (RF receive and analysis, firmware analysis, device access over SSH, CAN, serial); AUR-only members are optional until forked | built with `--nodeps` | Not installed (multi-GB); `pacman -Sp aces-field` resolves a 372-package transaction against the official repos, so every hard dependency exists |
| `aces-survey` | 0.1.0-1 | Survey and photogrammetry: `aces-survey-setup` (installs the vendored FlyPath 3.0.0 QGIS plugin into the user's QGIS profile and enables it; creates the WebODM tree), `aces-webodm` (start/stop/status/open/update/logs over the pinned WebODM v3.3.0 tree under Docker, GPU node when nvidia-smi and the Docker nvidia runtime are present), a WebODM desktop entry, README with the DJI Fly mission-loading procedure, LICENSES.md. Depends on `qgis`, `python-shapely`, `python-pyproj`, `docker`, `docker-compose`; `cloudcompare`, `colmap`, `meshroom` (AUR) and `nvidia-container-toolkit` optional. FlyPath zip and WebODM release tarball are fetched at build time with pinned sha256s (34.7 MB package) | built with `--nodeps` (QGIS is not needed to build an `any` package) | Clean container: installed with `-Sdd`; `aces-survey-setup` placed FlyPath 3.0.0 and wrote `[PythonPlugins] FlyPath=true`, created the WebODM 3.3.0 tree; `aces-webodm --help` prints usage and `status` reports that docker is missing instead of crashing; `--remove` cleared both. `pacman -Sp aces-survey` resolves a 256-package transaction. On DARKNESS (Windows, Docker Desktop) the same stack runs outside the package: `GPU_NVIDIA=true ./webodm.sh start --port 8090 --detached` brought up webapp, worker, db, broker and `node-odx-1` (`webodm/nodeodx:gpu`), the node sees the RTX PRO 4000, the console answers HTTP 302 on :8090; FlyPath 3.0.0 imports under QGIS 3.44.15 (OSGeo4W, user-profile install) with shapely 2.1.2 and pyproj 3.8.0 |
| `aces-os` | 0.1.0-3 | The meta-package: `aces-base`, `aces-creative`, `aces-field`, `aces-consoles`, `aces-omarchy`, `aces-survey`, `aces-engineering`, `aces-voxel`; `aces-studio` stays optional until the rig's video lane exists | built with `--nodeps` | `pacman -Sp aces-os` resolves in a clean signed container: 508 packages when aces-base, aces-creative, aces-consoles and aces-omarchy are already installed, 529 when aces-base, aces-engineering and aces-voxel are (qgis, docker and the engineering venv's libraries included) |
| `aces-engineering` | 0.1.0-1 | One locked Python 3.14 venv at `/opt/aces/engineering/venv` (76 distributions, each hash-pinned in `requirements.txt`, binary wheels only; 907.6 MiB installed, 255 MB package) with a vendored snapshot of cudax-sim-runtime's `simlab` and `gendesign` (commit `28d92a8`, content sha256 in `SNAPSHOT`) on its path. Commands: `aces-simlab` (validate, local run, report, evidence, surrogate, export, capacity, `remote` MCP client), `aces-evidence` (ledger and bundle verification, relocates ledgers from other machines), `aces-gendesign` (list, selftest, topopt, parametric, nozzle, fusion); `mcp.json.example` for the rig's simlab `:8022` and rig-state `:8010`; LICENSES.md. Not installed: rig-only rq, redis, uvicorn, mcp, starlette, gmsh, rocketcea, physicsnemo, and the GPU packages cupy, warp, torch (mlflow too); the wrappers keep the queue, leases, MCP-server and console commands off the desktop CLI. Depends on `libglvnd` and `libx11` (the OCP wheel links libGL and libX11) | built (x86_64, signed); `check()` = `aces-gendesign selftest` passed | `check()`: 56/56 imports, fea3d element test, the 1 mm cantilever on NumPy reproduces the workstation's GPU record (0.1989054912444315 mm) to 8.3e-13 in 401 PCG iterations, evidence bundle and chain verified, md/html/docx report, 8 SIMP iterations, L-bracket STEP round trip at 26,694 voxels as on the workstation (needs `embreex`; trimesh's rtree fallback gives 26,616, all 78 differences on one 45-degree face). Clean container (own script, `SigLevel = Optional TrustAll`): relocated shebangs, `pyvenv.cfg` and bytecode paths, selftest again, `aces-voxel fea` of the bracket STEP at 2 mm (converged, 1,232 iterations), the workstation's 4-bundle evidence ledger verified (4 entries relocated) and a hand-edited bundle rejected, `aces-simlab remote` against a local mcp 2.0.0 server (JSON and SSE replies, bearer token, 401 on a wrong one), a 10-trial parametric study and a GP surrogate in a per-user registry, `pacman -R` leaves nothing under `/opt/aces`. After the full signed rebuild, `bin/install-test aces-base aces-engineering aces-voxel` passed under `SigLevel = Required DatabaseRequired`: `aces-engineering selftest: all hard checks passed`, `aces-voxel fea --self-test` PASS (STEP round trip at 26,694 voxels in 0.3 s) |
| `aces-voxel` | 0.1.0-1 | `aces-voxel`: `fea` (STEP to voxel H8 FEA through simlab's step-structures stages, or `--self-test`), `topopt` (3-D SIMP), `lattice` (Warp rod lattices, GPU only), `export` (density to STL); `docs/VOXEL_KERNEL.md` (what is standard, what is not, the source modules) in `/usr/share/doc/aces-voxel`; an `aces-voxel` agent skill in `/usr/share/aces/voxel/skills` (linked by `aces-omarchy-setup` 0.1.0-2). Runs in the aces-engineering venv, no second venv | built (any, signed) with its dependency installed (`pkgbuilds/ORDER` no longer marks it `nodeps`), so `check()` runs against the installed aces-engineering: fea self-test, 6 SIMP iterations, export, a build123d bar STEP through `fea`, `lattice` exits 2 without a GPU | Clean container: `--help`, `fea --self-test`, `fea` of the bracket STEP, `export` of a SIMP density, `lattice` exit 2 with its reason |
| `aces-cad-onshape` | 0.1.0-1 | Onshape adapter (cloud; every write, evaluation, export and pull runs the project class gate and is refused for sovereign and controlled projects): standard-library REST client, `aces-onshape` CLI (whoami, documents, create-document, elements, fs read/write, eval, features, add-feature, add-custom-feature, update-feature, delete-feature, version, massprops, export step/stl, pull), `aces-onshape-mcp` stdio MCP server (17 tools; descriptions name every write; changing a feature or Feature Studio it did not create needs confirm_foreign, writing into a public document needs allow_public), the `aces-cad-onshape` agent skill in `/usr/share/aces/cad-onshape/skills`, `mcp.json.example` with this server and the official Onshape FeatureScript MCP Server (Onshape Labs, `https://fs-mcp.labs.onshape.app/mcp`, browser sign-in after an App Store subscription). `aces-onshape pull onshape://<did>/<wid>/<eid> --project NAME` writes `part.step`, `parameters.json`, `source.json` and `outcome.json` under `~/Work/NAME/cad/onshape/` and a `cad.pull` ledger entry with the sha256 of the first three where `aces` offers `ledger record` | built (any) **unsigned** in the `cad-onshape` worktree, 2026-10-09 13:02 UTC, with `bin/build aces-base aces-cad-onshape` (no release key in that worktree; the signed build comes with the next full `bin/build`): the wheel carries `License-Expression: MIT` with no setuptools warning, `check()` ran the 62 unit tests against the fake Onshape server (all OK), namcap reported nothing. The review fixes of 2026-10-10 (69 tests) came after this build: rebuild before release | Clean container, `bin/install-test aces-base aces-omarchy aces-cad-onshape` (unsigned repo, `SigLevel = Optional TrustAll`): `aces-onshape --help`; `aces-onshape whoami` exits 3 with `no Onshape API keys: ...`; the installed tests in `/usr/share/aces/cad-onshape/tests` 62/62 as root against the installed package; `aces-omarchy-setup` links the skill as `~/.claude/skills/aces-cad-onshape` and `~/.agents/skills/aces-cad-onshape`, and `--remove` unlinks both. Workstation: 62 tests OK on Python 3.11.9 and 3.13.13 (one skipped there, the POSIX file-mode test), 62/62 on 3.14.7 in `aces-builder`; the stdio MCP server driven through the skill's whole loop against the fake server. No live Onshape run yet (no credentials on DARKNESS) |

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
