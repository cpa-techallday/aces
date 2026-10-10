# Brief: a phone app to monitor and control the ACES system (requested by Aaron, 2026-10-10)

Aaron: "i want a iphone app to control and monitor the system too". This brief is what exists today (measured from
DARKNESS on 2026-10-10, read-only), the delivery options for an iPhone with their real constraints, a recommended
architecture, the feature list, the security rules, the open questions, and a build plan. Nothing here is built yet.

## 1. What exists today (the surfaces a phone app can use)

All on the LAN at 192.168.1.65 (the ACES Server rig); none is reachable from outside the LAN today (Tailscale is
installed on DARKNESS but logged out; the rig is not on a tailnet).

| Surface | Port | Read | Write | Auth today | Source |
|---|---|---|---|---|---|
| GRID CONTROL (Aaron's mode switcher, Node UI) | 3200 | `/api/status` (mode, detected_mode, auto_enabled, last_switch_ts, queue, 8 gpus, switching), `/api/modes` (11 modes: label, switchable, pending), `/api/telemetry` (gpus, cpu load, mem, 2 disks), `/api/switch/status` (target, phase, 49-line log, done, ok) | `/api/switch`, `/api/auto`, `/api/decisions`, `/api/mc/*` | GETs open; mutators take an `x-gc-token` header (the token sits in plaintext in `aces/docs/UI-INTEGRATION.md`, flagged for removal in RIG_HANDOFF 5) | probed GET 2026-10-10; simlab console app.py docstring |
| rig-state MCP (read-only by construction, runs on the rig) | 8010/mcp | tools `rig_endpoints`, `rig_gpus`, `rig_services`, `rig_health`, `rig_snapshot` | none | open | `C:\Users\shedm\rig-state-mcp\src\server.ts` |
| simlab-mcp 0.4.0 | 8022/mcp | 12 read tools (`rig_capacity`, `sim_status`, `sim_results`, `sim_list_experiments`, `sim_list_solvers`, `sim_list_specs`, `sim_evidence`, `sim_report`, `sim_report_files`, `sim_validate`, `sim_whoami`, `sim_get_file`) | `sim_submit`, `sim_sweep`, `sim_put_spec`, `sim_put_file`, `sim_cancel` | policy `full`, auth open: anyone on the LAN can submit and cancel (measured `sim_whoami`: principal anonymous, policy full) | cudax `docs/GOLDEN_RIG.md` 3; `sim_whoami` |
| SIMLAB console (Starlette, in `simlab-console-1`) | 8032 | `/api/summary`, `/api/capacity`, `/api/telemetry`, `/api/modes`, `/api/experiments`, `/api/experiments/{id}`, `/api/case/{id}/{case}`, `/api/file?p=`, `/api/registry`, `/api/evidence`, `/api/evidence/verify`, `/api/audit`, `/gc/{path}` (GET proxy to GRID CONTROL); a desktop web UI (index.html with a 3-D viewer, breakpoints down to 760 px) | none | open | cudax `simlab/console/app.py`, `static/` |
| MLflow | 5000 | experiments and runs (`aces-golden` holds today's 11 runs) | n/a | open | REST probe |
| aces desktop (ACES OS) | local | `aces fabric` (manifest of lanes and health), `aces ledger`, `aces golden show/verify`, golden run records under `~/Work/<project>/golden/<run>/` | `aces golden run` | the user's session | aces-base, aces-engineering |

Standing rule (VISION, the rig memory, GRID CONTROL): **mode switching stays human, through GRID CONTROL.** A phone app
may switch a mode only as Aaron's own hand, with his token, after a confirmation; never automatically. Agents submit and
read through simlab-mcp.

What already exists as a UI: the SIMLAB console (fleet view, experiments, evidence; desktop layout), GRID CONTROL's
own tabs (Ops/Overview/Modes/Chat/Agents/Projects/...), Aaron's Nabu/command-center dashboards on DARKNESS (different
scope: agents and companies). None is installable on a phone, none has push alerts, none has an authenticated control
path.

## 2. Delivery options for an iPhone

| Option | What it is | Needs | Gets | Does not get |
|---|---|---|---|---|
| (a) Installable PWA | A web app served from the rig; Safari "Add to Home Screen" gives a full-screen icon; Web Push works for installed PWAs on iOS 16.4 and later | no Mac, no Apple Developer account, no App Store; a server on the LAN (and a VPN for remote use) | ships today, updates by redeploying the container; the same backend serves a native app later | no background location or Bluetooth; notifications only while installed to the home screen; iOS may evict the service worker cache |
| (b) Native SwiftUI | an Xcode project | a Mac with Xcode; an Apple Developer account (free account installs last 7 days on a device; $99/year for TestFlight or the App Store) | best feel, widgets, Live Activities, proper push | every change needs a Mac build; distribution friction |
| (c) Expo / React Native | one JS codebase | an Apple Developer account for any device install; EAS cloud builds can run without a Mac | native shell, push | still Apple account + provisioning; two toolchains to keep green |

Recommendation: **(a) first**, with a thin authenticated backend that a later (b) client would reuse unchanged.
It can be in Aaron's hand this week, it costs nothing from Apple, and it does not depend on a Mac that may not exist.
If Aaron has a Mac and a developer account, (b) becomes a follow-up on the same backend, not a restart.

## 3. Proposed architecture ("ACES Pocket")

- **Backend `aces-pocket`** (Python, Starlette like the console; one container on the rig next to `simlab-console-1`,
  or the console itself extended): aggregates GRID CONTROL, rig-state, simlab console and simlab-mcp into one
  authenticated JSON API; serves the PWA (manifest, service worker, static UI); keeps a small event log and a
  subscription table for Web Push (VAPID keys generated at install, stored on the rig, never in git).
  - One phone bearer token (per device, revocable), checked on every request; the GRID CONTROL token and the simlab-mcp
    principal stay on the server. The phone never holds either.
  - Reads are cached a few seconds (as the console already does), so a phone polling every 5 s does not load the rig.
  - Alerts (push): job finished or failed (queue state change), GPU over a temperature or derated, mode changed or a
    switch failed, a golden run graded (from the aces ledger of the desktop that ran it, if that desktop reports in),
    simlab-mcp unreachable. Each alert names its source record.
- **Why on the rig and not DARKNESS:** the rig is the system being watched and it is always on; DARKNESS reboots and
  sleeps. The container only needs the LAN and the Docker network `simlab_default` to reach the console and redis.
- **Reach:** LAN today (phone on the home Wi-Fi). For remote use: Tailscale on the rig host is not an option under the
  "containers only" rule; a Tailscale sidecar container (userspace networking) on the rig, or Tailscale on DARKNESS as a
  subnet router for 192.168.1.0/24, is. DARKNESS has Tailscale installed but logged out (measured). Decide with Aaron.
- **TLS:** on the LAN, plain HTTP is what every rig service uses today; Web Push and some PWA features require HTTPS
  or `localhost`. The PWA therefore needs HTTPS: a self-signed CA installed on the phone (one profile) or a Tailscale
  cert if Tailscale is used. This is the one real friction point of option (a).

## 4. Features

Tier 1, monitor (every item has a live source today):
- GPU table: index, name, util %, memory used/total, temperature, derated, in pool, lease holder, processes (rig_capacity).
- Mode and lanes: current and detected mode, auto flag, last switch, switch in progress with its log (GRID CONTROL),
  lanes from the fabric manifest shape (`aces-base/etc/modes.json`).
- Queue: queued/started/deferred per queue, workers, abandoned, orphaned deferred (the gate logic from
  `scripts/rig_gate.py`, read-only).
- Experiments: list with case status and wall time, one experiment's cases, a case's results.json and evidence (console).
- Golden solvers: the three registry rows with validation status, image id, device and date (console `/api/registry`).
- Golden runs: grade, verdict line, conforming gaps, bundle hash (needs the desktops to publish their run summaries to
  the backend, or the backend to read `~/Work` on a share; not available from the rig today).
- Ledger tail and evidence verify result (console `/api/evidence`, `/api/evidence/verify`).
- MLflow run list for `aces-golden` and the calibration experiments.

Tier 2, control (each behind a confirmation sheet, each logged with who/when/what):
- Switch mode (GRID CONTROL `/api/switch` with the server-side token; shows `switchable` and `pending` first; the app
  refuses while a switch is in progress). Supported by the surface today.
- Cancel an experiment (simlab-mcp `sim_cancel`). Supported today.
- Submit a stored spec or the golden demo (simlab-mcp `sim_put_spec` + `sim_submit`; the golden demo needs a desktop
  to run the client, so on the phone it is "ask DARKNESS to run the demo", a new endpoint on DARKNESS). Partly new.
- Acknowledge or mute an alert (backend only). New.
- Never: anything on the rig host, driver or unlocker; no mode switch without a human tap; no automatic retries of
  control actions.

## 5. Security

- Today simlab-mcp runs with auth open and policy `full`, and the console and GRID CONTROL GETs are open: anyone on the
  LAN can submit, cancel, or read everything. The phone backend must not widen this: it is the only new thing
  listening, it authenticates every call, and it is bound to the LAN (and the VPN) only.
- Nothing from this system is to be exposed to the public internet (no port forward, no public tunnel). Remote access
  means a VPN whose login is Aaron's.
- Tokens: the phone token is random, per device, stored in the app, revocable from the backend's admin command; the
  GRID CONTROL token moves from the docs into the backend's environment (and the docs copy is deleted, as RIG_HANDOFF 5
  already asks).
- The backend logs every control action to its own append-only log and to the aces ledger when a desktop is involved.

## 6. Open questions for Aaron

1. Do you have a Mac with Xcode and an Apple Developer account? (Decides whether native is even possible now.)
2. Should the app work away from home? (Decides the VPN work: Tailscale sidecar on the rig vs DARKNESS subnet router.)
3. Which control actions do you want on the phone: mode switch, cancel, submit, all three, or monitor-only first?
4. Which iPhone and iOS version? (Web Push needs iOS 16.4 or later and the app installed to the home screen.)
5. Should the phone see the golden runs from the desktops (needs the desktops to report in), or the rig only?

## 7. Build plan (option (a))

- cudax-sim-runtime: `pocket/` package (backend + static PWA) and `docker/pocket.Dockerfile`, a compose profile next
  to the console; tests: API contract with a fake GRID CONTROL and a fake simlab-mcp (the fakes already exist for the
  console and the golden client), auth refusal paths, push subscription round trip; `scripts/pocket_deploy.sh` with
  the same gate and rollback discipline as `rig_deploy.sh`.
- aces-os: `aces pocket pair` (shows a QR code with the backend URL and a one-time token on the desktop; the phone
  scans it) and `aces pocket report` (sends a finished golden run's summary to the backend); rows in docs/PACKAGES.md.
- Acceptance on the real devices: install on Aaron's iPhone from Safari, receive one push (a job finishing), switch a
  mode once with Aaron watching GRID CONTROL, cancel a throwaway experiment, verify the backend log and the ledger.
- Size: backend about 1,500 lines with tests, PWA about 1,500 lines (no framework; the console's style tokens reused),
  deploy script and docs about 400 lines. Two to three sessions of work after the questions above are answered.
