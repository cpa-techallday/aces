# aces (aces-base)

The ACES OS command line. Standard library only, Python 3.11+.

| Command | What it does |
|---|---|
| `aces fabric refresh` | Read GRID CONTROL and rig-state on the ACES Server and write the lane manifest to `~/.config/aces/fabric.json` |
| `aces fabric show` | Print the manifest (mode, GPUs, lanes) |
| `aces policy show` | Effective policy (`/etc/aces/policy.json` + `~/.config/aces/policy.json`) |
| `aces policy mode trial|distribution` | Switch the operator policy mode (recorded in the ledger) |
| `aces policy override <component> --reason ...` | Record an operator override for a component (ledger entry) |
| `aces project new <name> [--class personal]` | Scaffold `~/Work/<name>/aces.yaml` |
| `aces project classify <name> <class> --reason ...` | Change a project's class at the end-use gate (ledger entry) |
| `aces project add-component <name> <component>` | Record a solver, model or tool the project used (the end-use gate lists them) |
| `aces project add-evidence <name> <ref>` | Record an evidence reference in `aces.yaml` (`aces golden` writes `golden/<run-id> bundle:<sha16> <verdict>`); idempotent |
| `aces project add-cad-source <name> <ref>` | Record a CAD source of the project (`cad/part.step`, relative to the project directory); idempotent |
| `aces ledger verify [--json]` | Recompute the hash chain of `~/.local/state/aces/ledger.jsonl`; `--json` prints `{"ok", "entries", "message", "path"}`; exit 1 when broken |
| `aces ledger show [-n N] [--kind K] [--json]` | The last N entries (default 20, `-n 0` all), optionally of one kind; `--json` is an array whose entries carry their 1-based position `n` |
| `aces ledger record <kind> --json FILE\|- [--print text\|json]` | Append one entry for a tool outside aces-base (`aces golden` records `golden.run`). The kind is dotted lower-case words of at most 64 characters; `policy.`, `project.`, `lane.` and `ledger.` kinds are refused (their own commands write them); the payload is a JSON object of at most 65536 bytes (canonical JSON). `--print json` prints one line `{"recorded", "n", "kind", "ts", "hash", "prev", "path"}`. Exit 2 on a refused kind, payload or unreadable JSON |
| `aces golden ...` | Passthrough to `aces-golden` from aces-engineering (the golden path: `run`, `show`, `verify`, `compute`, `spec`, `init`, `report`); exit 127 with the package to install when it is missing. `ACES_GOLDEN_CMD` (a JSON array, or one path) names another command |
| `aces lane list` / `aces lane test <lane>` | List lanes from the manifest; probe a model lane (models, a chat completion, a tool call) |
| `aces doctor` | Check config, server endpoints, Docker, GPU, Omarchy integration, and whether `aces golden` can run (with the last `golden.run` in the ledger) |

`python -m aces` is the same command line.

Configuration: `/etc/aces/aces.toml` (defaults, owned by the package), overridden by `~/.config/aces/aces.toml`.
Modes-to-lanes table: `/etc/aces/modes.json`. Policy: `/etc/aces/policy.json`.

The ledger is append-only and hash-chained. Writers hold an exclusive lock on `ledger.jsonl.lock` next to it
(`flock` on Linux, a byte lock on Windows; up to 30 s) while they read the last hash and append, so two processes
writing at once cannot fork the chain. Tests: `python -m unittest discover -s tests` (temporary state only).
