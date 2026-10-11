# aces-console

The one ACES desktop console. A per-user, loopback-only web server written with the Python standard library that
imports the `aces` package (aces-base) in-process for the desktop's records and proxies the rig's published HTTP and
MCP ports with every token held server-side, plus one static Parameter Sky front-end served from the same origin. One
launcher entry, **ACES**, opens it. It replaces the three `aces-consoles` entries (GRID CONTROL, SIMLAB, MLflow): GRID
CONTROL at :3200 stays the rig operator's shell and the switch engine the console calls; the SIMLAB console container
at :8032 stays the rig's read-only data plane, proxied; MLflow's REST API is read and its UI is a link. Nothing is
deployed to the rig and the rig host is never touched.

Every value on the page traces to a file or an endpoint. Absence is drawn with its reason ("no manifest yet; run aces
fabric refresh", "GRID CONTROL reported -1", "rig-state unreachable: ..."). Every status word carries the name of the
vocabulary it belongs to (`/vocab.json`), because the console's PENDING/RUNNING/STALE, rq's queued/started, the job
meta's waiting_for_gpu/running and golden's done/error are different scales that are never merged.

## Commands

| Command | What it does |
|---|---|
| `aces-console` | same as `open` |
| `aces-console open` | starts the server when `/api/health` does not answer, waits for it (up to 90 s), then opens the page with `omarchy-launch-webapp`, else `xdg-open`, else the Python `webbrowser` module |
| `aces-console start` | spawns `python -I -m aces_console serve` detached (stdout and stderr appended to `server.log`), waits for health |
| `aces-console serve [--bind H] [--port N] [--static DIR] [--fixtures DIR]` | runs the server in the foreground |
| `aces-console stop` | SIGTERM to the recorded pid, 5 s, SIGKILL, removes the pid file |
| `aces-console status` | pid, URL, the health summary, or `not running` |
| `aces-console url` | prints the URL (`http://127.0.0.1:8070`) |
| `aces-console <server key>` | the old `aces-consoles` behaviour: opens `server.<key>` (`grid_control`, `simlab_console`, `mlflow`, `webodm`, `video_studio`) from the aces configuration in a web-app window, or fails naming the missing key |

Exit codes: 0, 1 failure, 2 usage. The server runs on demand; no systemd unit is installed (an example unit ships
under `/usr/share/doc/aces-console/`).

## Configuration

Read through `aces.config.load()`: built-in defaults, then `/etc/aces/aces.toml`, then `~/.config/aces/aces.toml`.
A `[console]` table is optional; these are the in-package defaults:

```toml
[console]
bind = "127.0.0.1"                                # 127.0.0.1 or ::1 only; anything else is refused at start
port = 8070
static_dir = "/usr/share/aces/console/static"     # ACES_CONSOLE_STATIC overrides it (development)
state_dir = "~/.local/state/aces/console"         # $ACES_STATE/console
```

The rig endpoints come from the `[server]` table of aces-base (`grid_control`, `rig_state`, `simlab_mcp`,
`simlab_console`, `mlflow`, `timeout_s`). LAN or VPN binding is not offered in v1: the console binds loopback only, and
remote access waits for the authenticated shared API.

## Tokens

The GRID CONTROL token (header `x-gc-token`) is read server-side, in this order: `$ACES_GC_TOKEN`, `$SIMLAB_GC_TOKEN`,
then `KEY=VALUE` lines of `~/.config/aces/gridcontrol.env` (`$ACES_USER_CONFIG/gridcontrol.env`), which should be mode
`0600`; a group- or world-readable file produces one warning in the log naming the file, never the value. The simlab
bearer token follows the same discipline with `$ACES_SIMLAB_TOKEN`, `$SIMLAB_MCP_TOKEN` and `~/.config/aces/simlab.env`.
The operator writes `gridcontrol.env` by hand; nothing pre-populates it. `/api/config` reports only whether each token
is configured and where it came from. No route, log line, URL, page or ledger payload ever carries a token value, and
the JSON encoder masks any credential-named key as a last line of defence. `~/.config/aces/onshape.env` is never opened.

Known limit, stated plainly: agents run as the same Unix user as the shell, so a token in `gridcontrol.env` or the
environment is readable by them and gives them a direct, unledgered path to GRID CONTROL's `POST /api/switch`. The v1
mitigations are the loopback bind, no MCP tool and no CLI flag for the switch, the gates, the two-step nonce and the
ledger trail; the v2 path is a rig-side authenticated control endpoint with a human-knowledge factor.

## State

`$ACES_STATE/console/server.pid` and `server.log` (mode 0600; one line per request: method, path without the query,
status, duration; never headers or bodies). Everything else (nonces, caches, the telemetry ring, the SSE ring, the
pending switch outcome) lives in memory. Secrets are written nowhere.

## The API in brief

Every JSON GET answers 200 with the envelope

```json
{"ok": true, "data": {}, "meta": {"source": "<url|path>", "fetched_utc": "...", "age_s": 0.4, "ttl_s": 3, "stale": false,
                                   "ok": true, "error": null, "next_try_s": null, "fixture": null}}
```

When an upstream is down, `data` is the last good payload with `meta.stale: true` for at most 600 s, then `null`;
`meta.ok` is false, `meta.error` is one line (`"unreachable: <reason>"`, `"HTTP 404"`, `"not configured: ..."`) and
`meta.next_try_s` the backoff. Other statuses are used only for: 400 bad parameters, 403 refused, 404 unknown record,
409 gate or busy, 410 nonce spent or expired, 413 too large, 415 type not served, 429 rate limit, 500 console bug, 502
an upstream POST failed, 503 not configured or not installed, with the error shape
`{"ok": false, "error": "<one line>", "code": "<slug>", "source": null, "status": null, "next_try_s": null}`.

Every reply carries `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`, `/api/*` adds
`Cache-Control: no-store`, the page carries a Content-Security-Policy with the hash of its inline importmap, served
files carry `Content-Security-Policy: sandbox`, and no CORS header is ever sent. A POST is accepted only from a loopback
peer with `Origin` equal to the console's own origin, `X-ACES-Intent: human`, `Content-Type: application/json` and a
JSON object body of at most 64 KiB; anything else is 403 `{code: "refused"}` and counted in `/api/health`.

Core and rig routes (this package's backend core):

| Route | Data |
|---|---|
| `GET /api/health` | version, pid, bind, origin, clients, poller state, per-source freshness and backoff, counters, pending switch outcome, static manifest hash, installed modules |
| `GET /api/config` | the `[server]` endpoints, the console table, token sources (never values), paths, platform |
| `GET /api/events?plate=<id>` | the SSE stream: `event: stamp` records per polled source, `: ping` every 15 s, `Last-Event-ID` replay, at most 32 streams |
| `GET /vocab.json` | the status vocabularies and inks |
| `GET /api/rig/status` | GRID CONTROL status with `queue.queued/running` null when reported below zero and `queue_note` |
| `GET /api/rig/modes` | GRID CONTROL modes (`switchable_reported`) and the sim pools from the SIMLAB console |
| `GET /api/rig/telemetry` | GRID CONTROL telemetry plus a 240-sample ring |
| `GET /api/rig/snapshot` | rig-state `rig_snapshot` plus a classification of every service and endpoint against the mode's stop/keep lists |
| `GET /api/rig/decisions` | GRID CONTROL decisions (503 without a token) |
| `GET /api/rig/switch` | the switch engine's status with `log_tail` |
| `POST /api/rig/switch/prepare` `{mode}` | gates on fresh reads, a single-use 30 s nonce |
| `POST /api/rig/switch/confirm` `{mode, nonce, confirm}` | the switch itself (also `POST /api/rig/switch`) |
| `GET /api/fabric` | the fabric manifest, its age against `max_age_s`, the modes table |
| `POST /api/fabric/refresh` | runs `aces fabric refresh` once per 60 s (202; 409 while running; 429 inside the minute) |

The records routes (`/api/projects`, `/api/golden/*`, `/api/cad/*`, `/api/sim/*`, `/api/mlflow/*`, `/api/ledger*`,
`/api/policy`, `/api/doctor*`, `/api/system/*`) are documented in `docs/CONSOLE.md`; when their modules are not
installed `/api/health.modules` says so and the server runs without them.

## The mode switch

Switching the rig's mode is a human act. The console never switches on its own, exposes no MCP tool and no CLI flag for
it, and never calls GRID CONTROL's `POST /api/auto`. The page asks for a target, the server checks on fresh reads that a
token is configured, GRID CONTROL answers, the mode exists and is not the current one, it is marked switchable, no
switch is in progress, `detected_mode` is not `transition`, auto mode is off and the switch engine is idle or done, and
hands back a nonce that lives 30 s. The person types the target mode name and confirms; the server checks everything
again, writes `console.switch.request` to the operator ledger, posts the switch, writes `console.switch.result`, and
writes `console.switch.outcome` once when the engine reports done (or `ok: null` with the reason when it never does).
Refusals that reached the gates are written as `console.switch.refused`, one per target and reason per minute. The
fabric refresh writes `console.fabric.refresh`. All writes go through `aces.ledger.record` and verify with
`aces ledger verify`.

## Fixtures mode

`aces-console serve --fixtures <dir>` replays the captured JSON of 2026-10-11 (`tests/fixtures`, with `MANIFEST.json`
naming each file's origin, capture time and sha256) instead of calling the rig, stamps `FIXTURE 2026-10-11` on every
reply and SSE record, and refuses the switch and the fabric refresh. It is accepted only with
`ACES_CONSOLE_ALLOW_FIXTURES=1` in the environment: it exists for builders and tests, not for operation.

## Development

On any machine with Python 3.11+ and this repository:

```sh
PYTHONPATH=src/aces-base:src/aces-console python -I -m aces_console serve --port 8070
python -m unittest discover -s src/aces-console/tests -t src/aces-console
```

(On Windows join `PYTHONPATH` with `;`.) `ACES_WORK`, `ACES_USER_CONFIG`, `ACES_STATE` and `XDG_DATA_HOME` point the
console at a work store; the tests isolate theirs. The tests open sockets only to their own fakes on 127.0.0.1 and
never touch the rig.

## Deferred in v1

Simlab submit/sweep/cancel from the UI (the rig's simlab-mcp still runs with principal `anonymous` and policy `full`),
CAD worker actions (inspect, render, drawing, pull, prune), starting golden runs, the auto-switch toggle (shown as
recorded state only), Mission Control planes, approvals (a UI button is not an approval), LAN/VPN binding and the phone
client, a systemd user unit, per-mode lanes from GRID CONTROL, a light theme.
