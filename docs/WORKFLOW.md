# ACES: the next 50 steps

Rev 2 (2026-10-07). This revision takes in the review from the agent working in `aces-dev/`:

- backup and scheduled copy-off now come before the first campaign;
- the costing module (its D24–D26) has been added;
- ARC-1 rev B is flagged as Aaron's decision.

**Status:** ✅ done · ⏸️ blocked (reason given) · blank = to do. **Bold** steps are gates.

## Coordination

- **Two agents, one repo.** This agent works in the cloud copy and pushes through `C:\Users\shedm\aces`. The
  costing agent works in `aces-dev/`.
- **Merging the costing patch.** It is additive: one import and one command block in `cli.py`, a new module and a
  test file. Rebase it onto `origin/main` and run the full suite before pushing.
- **Decision numbers.** D24–D26 are reserved for the costing agent's decisions, and the compliance commit
  (`0bfaa88`) claims D27. Take the next free number from DECISIONS.md when a decision is accepted.
- **The rig is shared.** Only Aaron switches GRID CONTROL modes. No agent launches GPU work outside the simlab
  queue. Agents tell each other before touching rig services (for example the GLM-5.2 lane on :8019).

## A. Calibration solver onto the rig

1. ✅ Read how simlab on the rig works: the compose stack and `/opt/simlab-runtime`, which is root-owned, with
   pi in the docker group.
2. ✅ `gendesign:0.3` is on the rig, so `aces-cfd` is built there on top of it.
3. ✅ The CoolProp wheel (manylinux, cp312) and the `aces` wheel were downloaded on DARKNESS. Their hashes were
   checked after the copy.
4. ✅ `aces-cfd:0.1` was built on the rig, offline (`docker/aces-cfd.offline.Dockerfile`): image
   `sha256:c95bff6e9d76…`. The CPU self-test passes on the rig.
5. ✅ `aces-tpms-periodic` is in the rig's `simlab/solvers.json`:
   - the file was backed up first (`solvers.json.bak-20261006-223029`);
   - only the new key was added (checked semantically);
   - `simlab solvers` lists it.
6. ✅ The same entry is in the workstation copy, also backed up first, with the same CRLF/LF line endings.
7. ⏸️ **GPU lease check.** The rig is in mode `general-flagship`: the simulation pool is empty and every card
   holds LLM weights. Simulation needs Aaron to switch to `sim-lab-4` (GPUs 0–3) for a window, coordinated with
   the GLM-5.2 lane.
8. ⏸️ **Validation through the queue** (`cases/aces/aces-cfd-validation.yaml`, already on the rig and parsed by
   `simlab validate`). Pass mark: f·Re within 1.5 % and Nu within 3 % on a CMP 170HX.
9. Write the rig's numbers and image digest into the registry validation record.
10. ⏸️ Rig timing and resolution (`cases/aces/aces-cfd-resolution.yaml`): gyroid at n = 64, 96 and 128, Re 20
    and 100.
11. Set n and `max_steps` for the campaign from step 10. Split off the Re 300 cases if they hold the queue.

## B. Storage before data

12. Measure what `/mnt/nvme2/simlab` holds: queue, mlflow, exp. The disk is 80 % full (709 GB free), with no NAS.
13. Agree the backup target with Aaron: an external drive, DARKNESS, or a NAS purchase.
14. Scheduled copy-off of `/mnt/nvme2/simlab/queue` and the ACES ledger, as a recurring job rather than a
    one-off, with a restore test.
15. Prune or archive the old experiment folders Aaron approves, so the campaign has headroom.
16. **Gate: a verified backup exists and the campaign's projected output fits on disk.**

## C. First calibration campaign

17. Air campaign `cases/aces/aces-cal-air-01.yaml`: gyroid, diamond and primitive × t/a 0.08/0.12/0.16 × Re
    10/30/100/300 (36 cases at n = 96).
18. Record the cases that never settle (unsteady flow) and the Re where that starts.
19. `aces cfd-collect`: measurement rows with citable evidence.
20. Hold out about 20 % of the rows (one t/a and one Re per TPMS).
21. Fit `aces calibrate --boundary uniform_heat_flux` on the other 80 %.
22. If one factor can't cover all wall thicknesses, add t/a as a second variable in the fit.
23. Water campaign at the Re and n the thermal grid limit allows.
24. Cross-check gyroid and diamond friction and Nu against published TPMS heat-exchanger data, and record any
    gaps.

## D. Gate 1 and the fast tier

25. A held-out check command: the calibrated model against CFD rows it never saw.
26. **Gate 1: pressure drop and Nu within ±10 % on held-out cases.**
27. Re-run `aces design` with the calibrated registry; compare the Pareto fronts before and after.
28. Reports drop "uncalibrated" for the calibrated factors, but keep saying "not verified".
29. Commit the calibration registry with its campaign provenance.

## E. Solver quality and speed

30. Interpolated walls (Bouzidi) from the level set's distance along each link; re-run the duct and gyroid
    convergence.
31. A fused GPU collide-and-stream kernel, if rig cases take minutes.
32. Warm-start each Re step from the previous Re's field.
33. A conjugate-heat check on one case, to measure how far H1 is from the real wall.
34. Third independent review of `aces.cfd` and the calibration path.

## F. A heat exchanger you can sell

35. Headers and manifold model: plenums, distribution losses, and a check on uneven flow between channels.
36. Full-part geometry on a voxel kernel (OpenVDB or PicoGK) with fluid-A/fluid-B ports.
37. STL and 3MF export with wall and channel-neck checks over the whole part.
38. Pressure-containment check of the walls and headers (simlab voxel FEA).
39. Cold-plate / motor-jacket domain; the drift-kart motor is the first case.
40. The full chain on that case: spec → design → rig verification → print files.
41. Print and test one part (on-site polymer, or LPBF through a bureau); the rig test data goes into
    `aces calibrate`.
42. **Gate 2: the printed part tests within ±10 % of prediction.**

## G. Cost, routes and revenue

43. Costing module (costing agent, D24): `aces cost` per route, merged as described under Coordination.
44. Cost-calibration registry (D25): quotes and invoices calibrate cost the way CFD calibrates physics, with the
    same provenance rules.
45. Lead-time model (D26) per route: print, machine, robot cell.
46. ARC-1 rev B: **Aaron's decision**, and safety-relevant. Then fatigue coupons for the welded nodes. Nothing
    downstream waits on it.
47. CNC-machinable geometry family (plate-fin and milled channels), so a design can leave ACES without a
    printer.
48. `aces-mcp` over HTTP on the LAN with bearer tokens, in Mission Control.
49. A design-sprint offer and a case study from step 40, priced with steps 43–45. R&DTI mapping of the ledger.
50. **First paid sprint delivered and invoiced.**
