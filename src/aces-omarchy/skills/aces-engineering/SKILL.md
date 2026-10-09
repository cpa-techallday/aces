---
name: aces-engineering
description: Work inside ACES OS - read the lane manifest before choosing compute, respect the operator policy and the end-use gate, record overrides and project classes in the ledger, submit simulation work through simlab, and never switch the server's GPU mode.
---

# ACES engineering

ACES OS is engine-first: the ACES Server (the rig) offers lanes, the desktop reads a manifest, and every consequential
decision is written to a ledger. Start every engineering session with these three commands and read the output.

```bash
aces fabric refresh && aces fabric show
aces policy show
aces project list
```

## Rules

1. **Mode switching is a human act.** GPU modes are changed in GRID CONTROL by the operator, never by an agent or a
   script. If the active mode does not offer the lane you need, say so and ask; do not work around it.
2. **Lanes come from the manifest** (`~/.config/aces/fabric.json`). A lane with no endpoint is reported as such. Never
   guess a URL and never fall back to a cloud model silently; a cloud lane is only used when the manifest lists it.
3. **Projects carry a class** (`personal` by default). In `trial` policy mode nothing is restricted. When a project's
   end use changes (a contract, a customer, a paid deliverable, a defence end use), run
   `aces project classify <name> <class> --reason "..."` and re-read the terms of every component it lists.
4. **Record what a run used**: `aces project add-component <name> <component>` for every solver, model or tool with
   non-permissive terms, so the end-use gate has the list.
5. **Operator overrides are explicit**: `aces policy override <component> --reason "..."` writes the ledger entry.
   Agents request overrides; they do not grant them.
6. **Simulation work goes through simlab** (experiment.yaml, evidence.json, the server-side ledger). FP64 is the
   default precision. Measurements are evidence; opinions are not.
7. **Field and device sessions start read-only.** Writes, motion, flashing and transmit need operator approval and a
   ledger entry. Only equipment the operator owns or is authorised to work on.
8. **Never touch the rig host's driver, kernel or CUDA**, and never run GPU mutators there. Containers only.

## Commands

| Command | Use |
|---|---|
| `aces fabric refresh` / `show [--json]` | Rebuild or print the lane manifest from GRID CONTROL and rig-state |
| `aces lane list` / `aces lane test <lane>` | Lanes in the active mode; probe a model lane (models, completion, tool call) |
| `aces policy show [--json]` | Mode, default class, end-use gate text, recent overrides |
| `aces policy override <component> --reason ...` | Record an operator override |
| `aces project new <name> [--class personal] [--hypothesis ...]` | Scaffold `~/Work/<name>/aces.yaml` |
| `aces project add-component <name> <component>` | Record a component against a project |
| `aces project classify <name> <class> --reason ...` | The end-use gate |
| `aces ledger verify` / `show` | Check the hash chain; list recent entries |
| `aces doctor [--offline]` | Config, server reachability, Docker, GPU, Omarchy, agents, skills |

## Honesty

Report what was measured and what failed, with the output. If an endpoint is unreachable, the manifest says so; do
not fill the gap with assumptions. Fabricated data has no place in evidence.
