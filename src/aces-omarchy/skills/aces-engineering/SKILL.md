---
name: aces-engineering
description: Work inside ACES OS - read the lane manifest before choosing compute, respect the operator policy and the end-use gate, record overrides and project classes in the ledger, submit simulation work through simlab, never switch the server's GPU mode, and run the golden path (aces golden) to compute, verify and evidence organic parts from a design space, on the rig only when asked (never in CI), reading the grade and its reasons and never presenting a desktop result as a rig result.
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
| `aces golden compute [--lane sim/pool]` | What can compute here and the plan (CPU, CuPy GPU, Warp, gmsh and ccx, rig lanes); with a lane, probe its simlab-mcp (exit 0 usable, 2 not); computes nothing |
| `aces golden run --demo --json` | The whole loop on the built-in root mount; prints exactly one JSON document (`result.json`) |
| `aces golden run --spec spec.json --project <p> [--new]` | The loop on your spec (without `--spec`: `~/Work/<p>/golden/spec.json`) |
| `aces golden run ... --lane sim/pool [--rig-sweep] [--rig-budget S]` | Only when the person asks for a rig run: the independent check and the conforming check go to the rig (`--rig-sweep` also sends the sweep) |
| `aces golden run ... --conforming auto\|rig\|local\|off` / `--require-grade G` | Where the conforming tet10 check runs (default `auto`); exit 1 when a PASS has a grade below G |
| `aces golden spec --demo root-mount --out spec.json` | A spec to start from (`--schema` prints the JSON schema) |
| `aces golden init <p> --step part.step` | Project, the STEP in `<p>/cad/`, a spec template to fill in (supports, loads, keep-ins, material with its source) |
| `aces golden show [--json]` | Runs, newest first; `show RUN` for one |
| `aces golden verify [RUN]` | Audit a run, 13 checks (files, bundle, both ledgers, tables, models, verdict, report provenance, compute rows, rig records, conforming check, manifest, resolve); never contacts the rig |

Rules:

1. **Read `outcome`, `verdict_line`, `grade` and `grade_reasons` from the JSON and quote the verdict line.** The
   grades are `preview-grade` < `verified-desktop` < `rig-verified`. `verified-desktop` means the finer-resolution
   independent check agreed within the tolerance and the constraints held there. `rig-verified` means the check and
   the conforming tet10 check (gmsh C3D10 + CalculiX ccx) ran on the rig and passed, on images the rig registry
   validated, with matching code and no integrity error. A constraint violated on the conforming mesh caps the grade
   at `preview-grade`. None of it is sign-off by a person.
2. **Read the grade reasons and quote them.** `not rig-verified: ...` names the first condition that failed (a
   pending registry validation, another image, the conforming check not agreeing or not run, code identity, an
   integrity error). Report it; never change bands, tolerances or the spec to chase a grade: raise it with the
   operator.
3. **Never present a desktop result as a rig result.** The verdict line says where the check ran (`check 2 mm on the
   rig (GPU n)` against `check 2 mm`) and where the conforming check ran (`ccx 2.23, rig` or `desktop`). A fallback
   (`x101`, `m101`) is a desktop result; a conforming check that was not run is quoted with its reason.
4. **The rig only on request, never in CI.** Use `--lane` only when the person asks for a rig run; never in CI or an
   unattended job (there is no rig there, and a refused lane exits 2). Lanes come from the fabric manifest
   (`aces fabric refresh`); probe with `aces golden compute --lane sim/pool` and read its `rig:` lines; never guess a
   URL or a lane id. If the rig's mode releases no simulation GPU, say so and ask: switching modes is a human act in
   GRID CONTROL. The simlab token lives in `ACES_SIMLAB_TOKEN` or `~/.config/aces/simlab.env`; never print, log or
   paste it.
5. **Exit codes**: 0 pass, 1 verification failed (or `--strict`/`--require-grade` not met), 2 usage (including a lane
   that cannot be used: nothing was submitted and there is no run directory), 3 thin sweep (too few designs finished),
   4 under-resolved (the resolution guard refused the spec), 5 infeasible (no candidate predicted feasible), 6
   evidence, ledger or rig-integrity failure (when only the aces ledger write failed, the message carries the replay
   command), 7 internal error (traceback in the run's `log.txt`), 130 interrupted (rig jobs are cancelled). A non-zero
   exit is a result to report, not to hide. `aces golden` itself exits 127 with nothing on stdout when
   aces-engineering is not installed (`aces doctor` shows `golden absent`; install it with
   `sudo pacman -S aces-engineering`, and `calculix-ccx-bin` for the conforming check).
6. **Stress**: the constrained stress is `vm_p99` outside the support and load zones. Quote `vm_max` only with its
   caveat: it is resolution-sensitive (it grows as the voxels shrink) and is not a design value.
7. **Before citing a run, run `aces golden verify latest`** (or the run id). The run is in the simlab evidence ledger,
   in the aces ledger (`golden.run`) and in the project's `aces.yaml`; `aces project classify` still gates end use.
8. **Never edit a run directory.** Anything in it may be anchored by hash; change the spec or the flags and run again.

## Honesty

Report what was measured and what failed, with the output. If an endpoint is unreachable, the manifest says so; do
not fill the gap with assumptions. Fabricated data has no place in evidence.
