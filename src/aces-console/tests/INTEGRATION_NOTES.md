# Builder B integration notes (records backend: sim.py, records.py, ledgers.py, system.py)

Written for the integrator. Every item is a fact about the code on branch `console/b` after `console/a` and `console/c`
were merged into it; nothing of Builder A's or C's was edited.

## Builder A's interfaces, as used

- Router: every route goes through `install(register)` in each module; `server.py` already lists `sim`, `records`,
  `ledgers`, `system` in `MODULES`, so the merge needed no change there. The frozen `register(method, pattern, handler)`
  signature and the `{rel:path}` tail capture fitted every route (golden, CAD and bundle file routes).
- Helpers used exactly as delivered: `server.envelope/ok/error/file_response/default_meta/current_app/now_utc`,
  `app.hub.register/get/refresh/source`, `app.cache.get/invalidate`, `httpc.get_json/post_json/stream/fixtures`,
  `secrets.simlab_token/scrub`, `safefs.open_under/check_project/check_run_id/check_golden_run_id/check_bundle_name/
  classify/DEFAULT_KINDS`, `jsonenc` (through the server), `config.data_dir`. No signature had to change.
- Per-module state lives in `app.state["sim"|"records"|"ledgers"|"system"]` as `server.py` suggests.
- One **latent bug in A's `config.load`** (not fixed, A's file): `port = int(port if port is not None else
  table.get("port") or CONSOLE_DEFAULTS["port"])` treats a `[console] port = 0` override as unset, so
  `tests/support.py:Console` binds the real **8070** (its tests pass because each class closes its console before the
  next starts). Two live consoles in one process collide (`WinError 10048`). `tests/support_b.py:ConsoleB` forces
  `settings.port = 0` after `load()` (make_server rewrites port and origin after the bind). Suggested fix in A's
  `config.py`: `table.get("port") if table.get("port") is not None else CONSOLE_DEFAULTS["port"]` and let `make_server`
  keep its port-0 branch. Consequence for the integrator's full run: the A suite still binds 8070, so nothing else may
  listen on 8070 while the tests run.
- A's `Fixtures.replay` only replays `GET`; MLflow `runs/search` is a POST upstream, so in fixtures mode `sim.runs_entry`
  issues a GET of the same path to hit the replay map. The replayed file is unreadable (see Fixtures) and the route says so.
- `httpc.stream` is not replayed in fixtures mode (A's design), so `/api/sim/file` answers 502 "fixtures mode: streams
  are not replayed" there; live it streams the console's file (verified: 181-byte `case.json` of
  `aces-cal-air-03d/c000`).

## Deviations from the spec, and why

- **`/api/ledger/simlab` vs the `ledger.simlab` poll topic.** Spec 3.11 makes the route the rig-side chain (SIMLAB
  console `/api/evidence` + `/api/evidence/verify`, 13 entries in the capture) while spec 3.2 makes the `ledger.simlab`
  topic "the desktop simlab ledger mtime". Both exist: `GET /api/ledger/simlab` is the rig-side chain as specified
  (with the note "rig-side chain; verified by the SIMLAB console, not by this desktop"), and
  `GET /api/ledger/simlab/desktop` is the desktop file `$XDG_DATA_HOME/aces/simlab/evidence/ledger.jsonl` (4 entries on
  the VM) with its `previous` chain re-checked here; the `ledger.simlab` topic polls the desktop file (30 s, Evidence).
- **`GET /api/mlflow/runs?experiment_id=&max=`** (not in the spec; the task named the runs/search capture): a POST
  `runs/search` query upstream, 30 s cache, `{experiment_id, runs[{info, ui_url, n_metrics, n_params}], n, page_more}`.
  `next_page_token` is never passed through (the encoder would mask the key anyway); `page_more` says whether one exists.
- **MLflow `experiments/search`** is requested with `?max_results=1000`: the live MLflow 3.16.1 answers HTTP 400 to the
  bare GET the 2026-10-10 capture used (verified read-only on 2026-10-11). MLflow's own JSON error
  (`{error_code, message}`) is surfaced as `HTTP <n>: <error_code>: <message>`; a non-JSON error body (the compose proxy's
  "Invalid Host header") is fetched once more as text and shown verbatim.
- **`POST /api/doctor/run` answers 202 `{started}`** and runs `aces doctor` in a worker thread (90 s cap); `GET /api/doctor`
  then carries `{text, exit, ran_at, running, duration_s, command}` and the `doctor` stamp republishes. Spec 3.12 reads
  as synchronous; Builder C's Ops plate already handles the 202-then-poll shape. 409 while running; 503 `fixture` in
  fixtures mode (a doctor run probes the rig).
- **`POST /api/golden/verify/...` is synchronous** (200 with the tool's JSON, as the spec shapes it) with the 300 s cap;
  a timeout is 500 `verify-timeout`, a tool crash 500 `verify-failed` with the scrubbed stderr tail in the message
  (the spec's `stderr_tail` key has no place in the frozen error shape); exit 127 is 503 `not-installed` with the aces
  message ("aces golden needs aces-golden from the aces-engineering package").
- The aces CLI is resolved by `system.aces_command`: `aces` on PATH (the target), else the importable `aces` package run
  through `sys.executable -I -c` with its location on `sys.path` (a source tree). Tests replay the real VM verify record
  through `ACES_GOLDEN_CMD` (`tests/fakes/fake_golden.py`), the hook `aces golden` itself honours.
- `check_bundle.py` is looked up at `/usr/share/aces/cad/tests/check_bundle.py` (installed) else
  `src/aces-cad/tests/check_bundle.py` (source tree; reported as `checker.source: "source-tree"`), else 503.
- Golden JSON reads are capped by safefs at 8 MiB (spec 3.9 says 16 MiB; safefs's cap can only be lowered). `run.json`
  is read with 1 MiB as specified. Path components must start with an alphanumeric (safefs is stricter than
  store.py's `RELPATH_RE`, which allows a leading underscore).
- CAD and golden file routes serve `.joblib/.pkl/.npz/.brep` as `application/octet-stream` (never loaded); `.vtk`,
  `.gz`, `.sta`, `.cvg`, `.inp` answer 415 (not in the allow-list).
- Project rows carry an additional `counts.bundles` (spec 4.3 lists bundles on the Work cards); `/api/projects/{name}`
  adds `bundles` rows; `onshape_gate` follows `aces_onshape/project.py:26`.
- `/api/cad/onshape/{project}` adds `local` (the `cad/onshape/` artefacts listing) and `class_error`; `onshape.env` is
  never opened (nothing under `~/.config/aces` is read by records.py).
- `/api/sim/registry` returns `{solvers:[rows], n, counts, citable_rule, lane_note}` (the upstream is a dict keyed by
  solver name; rows keep every upstream key plus `name, validation_status, validation_word, image_id, citable,
  citable_note, lane, lane_recorded`). The capture: 14 rows, 12 pass, 2 fail, `image_id` on 3, `queue` on none.
- `/api/sim/status/{eid}` adds `principal` from the last `sim_whoami` of this session (null with a note before one
  answered) and `token_source`; `/api/sim/whoami` adds `token_source`, `server`, `protocol`, `n_tools`.
- Hub plates follow the integrator's seven ids (spec 3.2 still names Work/Ledger/System): `sim.capacity`
  (fabric, pipeline, solvers), `sim.summary` (pipeline, solvers), `sim.experiments` (pipeline), `sim.registry` (solvers),
  `sim.evidence`/`sim.evidence.verify`/`sim.audit` (pipeline, evidence), `sim.whoami` (pipeline, ops),
  `mlflow.experiments` (pipeline), `projects` (projects), `golden.runs` (evidence, projects), `cad.runs` (objects,
  projects), `ledger.aces` (*), `ledger.simlab` (evidence), `doctor`/`system.packages`/`system.entries` (ops).
  All notice-only except `sim.capacity` (inline).
- `POST /api/system/open` on Windows (development only) falls back to `webbrowser.open` when `xdg-open` is absent;
  the launcher is injectable (`app.state["system"].launcher`) so tests never open anything.

## Fixtures

- `tests/fixtures/MANIFEST.json` is still the JSON array A's `FixtureManifestTest` requires (top-level `*.json` only,
  `origin_host 192.168.1.65`); 14 rows were appended for the copied captures, sha256 checked against the integrator's
  `ui-samples/MANIFEST.json`.
- **`mlflow-runs-search.json` is not valid JSON as captured**: the credential-line stripping removed its
  `next_page_token` line and left a trailing comma (line 657). It is kept byte-exact (its MANIFEST note says so); a
  fixtures-mode read of `/api/mlflow/runs` therefore answers `meta.error "fixture file unreadable: ..."`, the honest
  outcome. The live route works (5 runs for experiment 11 on 2026-10-11).
- The record-only golden runs live under `tests/fixtures/golden/<project>/golden/<run>/` with their own
  `tests/fixtures/golden/MANIFEST.json` (A's test forbids non-capture rows in the top-level manifest): cad-gold
  `20261010T102210Z-bc7391` (175 files, 173 hash to the run's own `manifest.sha256`, 59 binaries omitted) and
  golden-demo `20261010T084920Z-b3ac99` (207 files, 205 matching, 63 omitted); `log.txt` and `manifest.sha256` are the
  two files the manifest does not list. Also `golden/desktop-simlab-evidence-ledger.jsonl` (the VM's 4-entry desktop
  chain). `tests/fixtures/.gitattributes` gained `golden/** -text` so git never rewrites them.
- Builder C's `tests/harness/fixtures/` duplicates 20 captures byte-identically; with this branch merged the devserver's
  preferred `tests/fixtures/` set is complete (the two files C left out, `simlab-experiments.json` and
  `simlab-audit.json`, are here), so C's copies can be deleted if wanted (not done here: C's files).

## Tests and how they stay honest

- `tests/support_b.py:build_store` assembles a work root from the committed real records only: projects are created by
  `aces.project.new` (the real writer; the ledger receives the real `project.new` entries), the golden runs are the
  record-only copies, the CAD project is `rehearsal/reports/2026-10-10-aces-cad-vm` placed under the names its own
  records carry (bundle `20261010T101736Z-plate` from `outcome.json`, run `20261010T101628Z-0ffb` from the drawing
  record, with no `run.json`, as the spec predicts). The partial-copy test copies only a real `run.json` into a
  deliberately incomplete directory to prove the absence wording.
- Fakes (`tests/fakes/fake_console.py`, `fake_mlflow.py`, `fake_simlab_mcp.py`) replay the captures; the two stand-in
  replies that are not captures are labelled in their docstrings: MLflow's 404 body for an unknown run and the fake
  MCP's `-32601` for `sim_status` (no capture exists; the live route was verified read-only instead).
- The doctor test runs `aces doctor --offline` (module constant `system.DOCTOR_ARGS` patched) so no probe leaves the
  loopback; it compares the route's text with a direct run line by line except the three rows that carry timings.

## Observed in the merged tree (console/a + console/c + console/b), not in B's files

Full suite on this Windows machine: `python -m unittest discover -s src/aces-console/tests -t src/aces-console` ->
118 tests, 3 failures, 1 skip (A's symlink case). All 38 of B's tests pass; the three failures are:

1. `tests.test_sse.SseTest.test_poller_idles_after_the_last_client_plus_grace` (A). A Windows-only timing artefact:
   a TCP connect to a closed loopback port takes ~2 s here (measured: raw `socket.create_connection` 2016 ms ->
   WinError 10061; `localhost` 4125 ms) while Linux refuses instantly. A's poller runs every due source sequentially
   inside one `poll_once`, and that test's console has rig-state and the SIMLAB console on closed ports, so the first
   poll already costs ~4 s (telemetry seed from the console 2 s + rig-state 2 s) against the test's 3 s idle window;
   it passed narrowly on A's branch and any additional Fabric-gated source tips it over. B's `sim.capacity` is gated
   on Fabric because C's Fabric plate subscribes to it (static/plates/fabric.js:46, static/lib/api.js:55), so the
   gate stays. Fixes belong to A: fetch sources concurrently (or per source thread), or give that test listening fakes
   for every upstream (the FakeGC is already one), or widen its wait. On the Linux target and in the PKGBUILD check()
   closed ports refuse in microseconds and the test is not affected. A related design note for the operator guide:
   with the rig host unreachable (a hanging connect, not a refusal) one poll cycle lasts the sum of the timeouts of
   every due source, since the poller is single-threaded.
2. `tests.test_harness.TestDevServer.test_projects` and `TestVerifyUI.test_verify_ui` (C) fail on the plain a+c merge
   too (verified by running them on commit 1ae47e4 extracted with `git archive`: "FAILED (failures=2)"). Two causes:
   (a) `tests/harness/devserver.py:prepare_env` uses `os.environ.setdefault` for ACES_WORK/ACES_USER_CONFIG/ACES_STATE/
   XDG_DATA_HOME, and A's `tests/__init__.py` (C's own was empty) exports the scratch tree to the devserver subprocess,
   so the devserver never sees `--vm-work` ("the backend lists no project", "NO MANIFEST"); the fix is `os.environ[...]
   =` when `--vm-work` is given, or a clean env in `DevServerProcess`. (b) the devserver serves A's
   `aces_console/vocab.json` in preference to C's `tests/harness/vocab.json`; A's `endpoint` vocabulary lists the
   words `by-design` / `unreachable` (with the sentences under a separate `sentences` key) while C's plates render the
   sentence "stopped by mode sim-lab-4 (by design)" as the `data-word`, so the harness counts 8 unknown words. Either
   C renders `row.word` (A's classification rows carry both `word` and `verdict`) or the vocabulary lists the
   sentences; one line on either side.

## Verified live (read-only, 2026-10-11, this machine)

`python -I tests/verify_records.py live 8074 --store <vm-work>`: every route above against the real SIMLAB console
(:8032), simlab-mcp (:8022, `sim_whoami` and `sim_status` for `aces-cal-air-03d`, principal `anonymous`) and MLflow
3.16.1 (:5000, 12 experiments, run `97cfbb61...` with 2 params, 17 metrics, 17 tags, 5 runs for experiment 11), plus
the vm-work store (3 projects, 4 golden runs, 16 CAD runs, 4 bundles, the real 11-entry ledger, the 4-entry desktop
simlab chain). No token variable was set; nothing was sent to the rig host, port 8020 or redis.
