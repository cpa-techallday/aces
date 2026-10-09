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
| `aces ledger verify` | Recompute the hash chain of `~/.local/state/aces/ledger.jsonl` |
| `aces lane list` / `aces lane test <lane>` | List lanes from the manifest; probe a model lane (models, a chat completion, a tool call) |
| `aces doctor` | Check config, server endpoints, Docker, GPU, Omarchy integration |

Configuration: `/etc/aces/aces.toml` (defaults, owned by the package), overridden by `~/.config/aces/aces.toml`.
Modes-to-lanes table: `/etc/aces/modes.json`. Policy: `/etc/aces/policy.json`.
