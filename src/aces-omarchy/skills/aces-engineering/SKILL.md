---
name: aces-engineering
description: Work inside ACES OS - read the lane manifest before choosing compute, respect the operator policy and the end-use gate, record overrides and project classes in the ledger, submit simulation work through simlab, never switch the server's GPU mode, and run the golden path (aces golden) to compute, verify and evidence organic parts from a design space.
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
| `aces project add-evidence <name> <ref>` / `add-cad-source <name> <ref>` | Record an evidence reference or a CAD source in `aces.yaml` (`aces golden` records its runs itself) |
| `aces ledger verify [--json]` / `show [--kind K] [--json]` | Check the hash chain; list recent entries (of one kind) |
| `aces ledger record <kind> --json FILE` | How tools append their own entries (`aces golden` writes `golden.run`). Never record by hand something that did not run |
| `aces doctor [--offline]` | Config, server reachability, Docker, GPU, Omarchy, agents, skills, `aces golden` |

## The golden path (`aces golden`)

Parts are computed, not drawn. A box, a disc or a customer's STEP is only the design space (with its supports, loads,
keep-in interfaces and material); the generators output organic parts: SIMP topology optimisation turned into a
smooth implicit skin that blends into the preserved interfaces, and gyroid-cored variants (a TPMS sheet core inside a
shell). Every design is solved on the real voxel FEA, a GP surrogate picks the winner, the winner is solved again
(and at a finer resolution, and on CuPy and Warp when they are usable), and the run lands in both ledgers with a
report a person can read (`report.md`) and an agent can parse (`report.json`, `result.json`).

| Command | Use |
|---|---|
| `aces golden compute` | What can compute here and the plan (CPU, CuPy GPU, Warp, rig lanes); computes nothing |
| `aces golden run --demo --json` | The whole loop on the built-in root mount; prints exactly one JSON document (`result.json`) |
| `aces golden run --spec spec.json --project <p> [--new]` | The loop on your spec (without `--spec`: `~/Work/<p>/golden/spec.json`) |
| `aces golden spec --demo root-mount --out spec.json` | A spec to start from (`--schema` prints the JSON schema) |
| `aces golden init <p> --step part.step` | Project, the STEP in `<p>/cad/`, a spec template to fill in (supports, loads, keep-ins, material with its source) |
| `aces golden show [--json]` | Runs, newest first; `show RUN` for one |
| `aces golden verify [RUN]` | Audit a run (files, bundle, both ledgers, tables, models, verdict, report provenance) |

Rules:

1. **Read `outcome`, `verdict_line` and `grade` from the JSON and quote the verdict line.** Desktop results are
   `preview-grade`; `verified-desktop` means the finer-resolution independent check agreed within the tolerance and
   the constraints held there. None of it is sign-off (that needs a rig run and a conforming-mesh second
   discretisation, which v0 does not do).
2. **`--lane` is not wired yet** (exit 2, nothing submitted). Say so; never fake a rig run or present a desktop run as
   one. The report lists the rig lanes it saw and marks them not used.
3. **Exit codes**: 0 pass, 1 verification failed (or `--strict` without `verified-desktop`), 2 usage, 3 thin sweep (too
   few designs finished), 4 under-resolved (the resolution guard refused the spec), 5 infeasible (no candidate
   predicted feasible), 6 evidence or ledger failure (when only the aces ledger write failed, the message carries the
   replay command), 7 internal error (traceback in the run's `log.txt`), 130 interrupted. A non-zero exit is a result
   to report, not to hide. `aces golden` itself exits 127 with nothing on stdout when aces-engineering is not installed
   (`aces doctor` shows `golden absent`; install it with `sudo pacman -S aces-engineering`).
4. **Stress**: the constrained stress is `vm_p99` outside the support and load zones. Quote `vm_max` only with its
   caveat: it is resolution-sensitive (it grows as the voxels shrink) and is not a design value.
5. **Before citing a run, run `aces golden verify latest`** (or the run id). The run is in the simlab evidence ledger,
   in the aces ledger (`golden.run`) and in the project's `aces.yaml`; `aces project classify` still gates end use.
6. **Never edit a run directory.** Anything in it may be anchored by hash; change the spec or the flags and run again.

## Honesty

Report what was measured and what failed, with the output. If an endpoint is unreachable, the manifest says so; do
not fill the gap with assumptions. Fabricated data has no place in evidence.
