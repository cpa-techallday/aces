# Review: the `aces-cal-air-01` hx corpus (gyroid focus)

Reviewed 2026-10-09 against MLflow experiment 9 ("ACES-calibration") on the rig, the SIMLAB console on the rig
(`:8032/api`), commits `ff9a431` (calibration factors) and `2a7d87a` (`aces corpus`), and the solver code at
`2a7d87a`. Every number below was recomputed from the run records; eight independent verifier agents then tried to
refute each finding and two critic agents looked for what the review missed. Where they corrected me, the
correction is what appears here.

## The short version

- **The 17 corpus rows are faithful.** `dpdx_Pa_m` and `h_W_m2K` are bit-identical to the MLflow metrics, and
  `reynolds` is recomputed by `calibrate.reduce` from the mass flux and agrees with `re_achieved` to 4e-16.
  Refitting the ratios reproduces every factor in `data/calibration/hx.json` to 10 digits.
- **Gyroid has 8 of 12 cells.** One cell, `c002` (wall 0.08, Re 100), never ran: its queue job waited six hours
  for a GPU lease while the four Re-300 cells held all four cards, then failed with "no GPU lease within wait".
  The three gyroid Re-300 cells ran 6.3 h each and produced nothing.
- **The campaign ran the night of Wednesday 7 October** (18:57 to 06:57 AEST), not last night.
  2.9 of its 36.2 GPU-hours produced the 17 usable rows; 26.6 hours went to Re-300 cells and 6.7 to primitive cells
  that cannot produce rows as configured.
- **The fitted Nusselt factors carry a systematic error of about -16 % to +20 % inside their declared valid range**,
  because `calibrate.fit` only tries a Reynolds-dependent form when the constant's scatter exceeds 0.15.
- **Provenance has a hole.** The evidence gate in `cmd_cfd_collect` at `2a7d87a` cannot read the rig's
  `simlab-evidence/1` files (it looks for `spec`/top-level `id`; the files carry `experiment`/`case`), so the 17 rows
  did not pass through the gate as committed, and the evidence records carry no git revision.
- **Friction at Re 100 is not demonstrably grid-converged at n = 96**; the rig's own resolution study shows
  differences that grow with refinement. Nusselt is within about 1.5 %.
- **Two endpoints are mis-recorded**: the simlab MCP is on port 8022 (8020 serves the Univer workbench), and the
  SIMLAB console is live on the rig at port 8032 with a JSON API that answers every "what happened" question here.

## 1. What the sweep was and what came back

Spec `specs/simlab/aces-cal-air-01.yaml`: tpms {gyroid, diamond, primitive} x wall ratio {0.08, 0.12, 0.16} x
target Re {10, 30, 100, 300} = 36 cells `c000..c035`, air at 320 K and 2 bar, 4 mm cell, n = 96, FP64 LBM
(`aces.cfd.periodic`), H1 thermal boundary. Case index i maps to tpms i // 12, wall (i // 4) % 3, Re i % 4.

| Group | Cells | Rows | GPU-hours | Why |
|---|---|---|---|---|
| Converged, in the corpus | 17 | 17 | 2.94 | gyroid 8, diamond 9 |
| Re 300, gyroid and diamond | 6 | 0 | 26.59 | unsettled or diverged, see section 3 |
| Primitive, all Re | 12 | 0 | 6.66 | Mach limit, see section 4 |
| `c002` | 1 | 0 | 0 | never got a GPU, see section 2 |
| Total logged | 35 of 36 | 17 | 36.19 | |

Gyroid cells in the corpus: `c000 c001` (wall 0.08, Re 10 and 30), `c004 c005 c006` (0.12, Re 10/30/100),
`c008 c009 c010` (0.16, Re 10/30/100). The wall-0.08 series has no Re-100 point.

## 2. Cell c002: a scheduling casualty

SIMLAB console job `aces-cal-air-01--c002--run--a074ae`: status FAILED, reason "no GPU lease within wait", mode
`sim-lab-4`, pool GPUs 0-3, available list empty. It waited from 08:57:13 to 14:57:37 UTC, which is the queue's
`lease_wait_s` of 6 x 3600 (`simlab/queue.py:39,101-109`). During that window `c011` and `c015` held two cards for
6.3 h each and `c003` and `c007` took the other two at 13:38 and 14:40. The next card freed at 15:23, 26 minutes
after the wait expired. The console still shows the case as PENDING; MLflow has no run for it because the solver
never started. All other 35 contenders got a card. Nothing is wrong with the cell; it needs about 20 minutes of GPU
time to run.

## 3. The Re-300 cells

None was refused by the tau floor: every Re-300 cell had tau between 0.5069 and 0.5169, above `TAU_MIN` 0.505.

Gyroid (`c003`, `c007`, `c011`): the first flow solve used the laminar-baseline force (`periodic.py:131-132`) and
settled at Re 57-59 in about 75,000 steps. The proportional second guess (`periodic.py:141`) reached Re 150-154 and
did not settle within `max_steps` = 2,000,000 (`simlab_adapter.py:85`), so the loop broke before any secant step.
Each cost 22,514-22,603 s. The gyroid periodic cell therefore does not settle at Re about 150 in two million steps,
while Re 100 settled in 102,000-124,000.

Diamond: `c015` did reach a settled state at Re 174.5, then failed to settle at Re 252.8 in two million steps
(6.6 h). `c019` and `c023` settled at Re 164.6-167.4 and then diverged to NaN 22,000-37,000 steps into the secant
solve, at tau 0.5069-0.5079, within 0.003 of the floor. Their problem text still says "did not reach a steady state
in 2000000 steps" because NaN fails the Mach and Re tests silently (`periodic.py:153-158`); they should be reported
as diverged.

The primitive Re-300 cells (`c027`, `c031`, `c035`) converged in flow and were then rejected on Mach, costing 4.26 h.
All nine Re-300 cells together cost 30.8 GPU-hours, 85 % of the campaign, for zero rows. `docs/SIMLAB_CFD.md`
already advised putting Re 300 in a separate experiment.

## 4. Primitive: rejected by construction

Every primitive cell converged in flow with achieved Re within 1.7 % of target, then failed the single check
`Mach > 0.15` with Mach 0.214-0.273 (`periodic.py:155`). `Sample.u_lattice` is 0.025 for every geometry and cannot
be set per case (`simlab_adapter.py:80-84`, `cli.py:949` never pass it). The primitive's peak-to-interstitial
velocity ratio is 5.0-6.3 against 2.9-3.4 for the converged gyroid cells, 1.6 to 2.0 times higher. Scaling Mach
with `u_lattice`, 0.012 gives Mach 0.103-0.131 and keeps tau above 0.505 even at Re 300. The cost is about double
the flow steps: roughly 9 minutes per Re-10 cell, 22 per Re-30 cell and 60-70 per Re-100 cell, about 4.8 GPU-hours
for nine cells before the thermal solve. The Tier-0 proposer already labels primitive "no calibrated factors"
(`propose/tpms_parametric.py:49-50`).

## 5. Data quality of the 17 rows

### 5.1 The gyroid Nusselt step between Re 30 and Re 100

Gyroid `Nu_H1` rises 4.3-5.2 % from Re 10 to 30, then x1.68 (wall 0.12) and x1.73 (wall 0.16) to Re 100. Diamond
rises 10-11 % then 15-20 %. The rig's resolution study (gyroid t/a 0.10, `aces-cfd-resolution`) gives
`Nu_H1` at Re 99.3 of 9.251 / 9.218 / 9.187 at n = 64 / 96 / 128, and Nu(Re 99)/Nu(Re 20) = 1.717 / 1.714 / 1.708,
so the step is not a resolution artefact: n-sensitivity is about 1.5 % at most. What the data cannot show is
whether the step is physical: there is no point between Re 30 and 100, and the Re-100 steady states come from
exactly symmetric single-cell starts (see 5.4). A published laminar gyroid study over 20 <= Re <= 100 (Savoldi et al.
2026, Int. J. Heat and Fluid Flow, abstract only) describes the same window as a transition from a Darcian to a
mixed viscous-inertial regime; that is consistent, not confirmation.

### 5.2 Resolution

| Quantity, gyroid t/a 0.10 | n = 64 | n = 96 | n = 128 | Step 64 to 96 | Step 96 to 128 |
|---|---|---|---|---|---|
| Nu_H1 at Re 99.3 | 9.251 | 9.218 | 9.187 | -0.35 % | -0.34 % |
| dp/dx at Re 99.3, Pa/m | 553.0 | 550.0 | 543.4 | -0.55 % | -1.22 % |
| friction ratio at Re 19.6 | 3.4293 | 3.4236 | 3.4031 | -0.17 % | -0.60 % |
| friction ratio at Re 99.3 | 6.8106 | 6.7734 | 6.6865 | -0.55 % | -1.28 % |

The friction differences grow with refinement, so no observed order exists and `docs/SIMLAB_CFD.md` ("settles
from n = 48, 0.003 %") is contradicted by the rig's own data. If the error is first to second order, the n = 96
friction is 1.4-5.4 % high, and worse for diamond t/a 0.16, whose hydraulic diameter is 27.6 voxels against 45.5.
Nusselt moves 0.35 % per step, so its n = 96 error is about 0.8-1.4 %. The thermal boundary layer at Re 100 is
3.3-5.1 voxels thick (`c022` is thinner than the study's coarsest grid), and the only thermal guard is a tau floor
that trips at grid Peclet about 80, not a resolution check.

### 5.3 Checks that cleared

Compressibility (Mach 0.118-0.146; CPU reruns at half `u_lattice` move friction and Nu by less than 0.1 % up to
Re 30, untested at Re 100), the D3Q7 scalar scheme's internal velocity scale (Nu identical to 1e-7), the nu <= 1/6
cap (inactive), the thermal step budget (under 0.5 % used), warm starts between force iterations (drift under
6e-7), the H1 wall area, the Re and Dh definitions (one network, interstitial velocity; solver and reducer agree to
4e-16), and the geometry pipeline (`phi_voxel` within 0.01-0.32 % of the level set, walls 7.4-7.5 voxels, gates pass
with margin).

### 5.4 An open risk: the Re-100 steady states

The n = 96 masks keep every x-preserving symmetry exactly, the initial field and the force are uniform, and the
settle test (`lbm.py:155-161`) passes while a growing mode's 200-step change is under 2e-6 of the peak speed. A mode
with an e-folding time longer than about 4,000 steps stays invisible through the 102,000-130,000 steps that settled
the five Re-100 cells, and the same gyroid box did not settle at Re 150. Settling at Re 100 therefore does not bound
the critical Re. The flux history is computed but not saved (`periodic.py:150-152`).

### 5.5 The calibration fit (`data/calibration/hx.json`, commit `ff9a431`)

Ratios recomputed with the committed definitions (f_meas = dpdx Dh / (0.5 rho u_int^2) over 64/Re; Nu_H1 / 4.36)
reproduce the four factors to 10 digits. Residuals of those fits, CFD over fit minus one:

| Factor | Form kept | Re 10 | Re 30 | Re 100 | sd of log | Note |
|---|---|---|---|---|---|---|
| gyroid friction | power, b 0.357 | +2 to +5 % | -5 to -9 % | +4 to +6 % | 0.066 | |
| gyroid nusselt | power, b 0.231 | +2 to +13 % | -8 to -17 % | +9 to +11 % | 0.130 | passed the 0.15 gate |
| diamond friction | power, b 0.257 | +4 to +7 % | -9 to -10 % | +1 to +8 % | 0.083 | |
| diamond nusselt | constant 1.457 | -8 to -15 % | -6 to +3 % | +13 to +19 % | 0.121 | under 0.15, so no Re form was tried; a power law (b 0.115) would leave 5.9 % |

As design-tier prediction errors the signs flip: the model over-predicts diamond Nu by 8-18 % at Re 10 and
under-predicts by 11-16 % at Re 100; gyroid Nu is over-predicted by up to 20 % at Re 30. The cause is in
`calibrate.fit` (`calibrate.py:307-318`): a Reynolds form is tried only when the constant's scatter exceeds
`max_scatter`, and the first form under the gate is kept; nothing tests whether the residuals trend with Re. The
inertial-onset form is unreachable anyway: it needs four distinct Re levels and the campaign delivered three.

## 6. Corpus and provenance

- `licence: internal-verified` is a literal written for every row that loads (`corpus.py:42`), not a per-row state.
- `thermal_boundary` comes from the CLI `--boundary` default (`cli.py:1359`, `corpus.py:40`), not from each row;
  a file with a mixed-boundary row is refused by `calibrate` but accepted by `corpus`.
- Rows drop `mass_flux_kg_m2s` (the measured input), `evidence_sha256` (pinned by `cfd-collect`), and carry no
  n, grid Peclet, Mach, converged flag or uncertainty, so rows from different resolutions cannot be told apart.
- The `cmd_cfd_collect` evidence gate at `2a7d87a` (`cli.py:1055-1072`) reads `spec` / top-level `id`; the rig's
  `evidence.json` (schema `simlab-evidence/1`) carries `experiment.id` and `case.*`. On the real `c000` folder the
  committed command collects zero rows. Whatever produced the 17 rows was not this gate, and `corpus` and
  `calibrate` accept rows without `evidence_sha256`. A rebuild from the MLflow metrics alone reproduces
  `corpus.jsonl` bit for bit, which is reassuring about the values and worrying about the gate.
- `evidence.json` has `git_rev: null`, no image digest and no CoolProp version; the adapter overwrites
  `measurement.json` (`simlab_adapter.py:89,92`); hashes are over raw bytes, so a CRLF copy changes them.
- `collect` walks existing folders only and exits 0 with any rows, so the missing `c002` is never mentioned;
  `hx.json`'s box-shaped valid range admits the unsampled corner (gyroid 0.08, Re 100) without warning.
- A resolution replicate counts as a new condition in `fit` (`Point.condition` keys on mass flux to 6 s.f.).

## 7. Infrastructure

- `cudax-sim-runtime/.mcp.json` and `aces/docs/agent-brief.yaml:63` point simlab at `:8020/mcp`. Port 8020 serves
  the Univer workbench (`docs/UI-INTEGRATION.md:16,94`); the simlab MCP answers on `:8022/mcp`.
- The SIMLAB console is live on the rig at `:8032` ("SIMLAB · Fleet") with unauthenticated JSON under
  `/api/summary`, `/api/experiments`, `/api/registry`, `/api/evidence`; it is how `c002`'s failure was found.
- `docs/UI-INTEGRATION.md:24` holds a plaintext GRID CONTROL token.

## 8. Actions, in the order I would take them

| # | Action | Owner | Cost |
|---|---|---|---|
| 1 | Resubmit `c002` alone (gyroid 0.08, Re 100) and re-collect | Kimi | about 20 GPU-minutes |
| 2 | Fix `calibrate.fit`: test for a Re trend in the residuals (or compare forms by residual), refit `hx.json`, and record the per-row n = 96 vs 128 spread as uncertainty | Claude Code | code + 1 refit |
| 3 | Fix `cmd_cfd_collect` to parse `simlab-evidence/1` (require `experiment.id`, `case.case_id` = folder, case keys = row, results = row), make `evidence_sha256` mandatory downstream, add the real `c000` files as fixtures, re-collect | Claude Code | code |
| 4 | Move Re 300 to its own experiment with early unsteady detection (abort on a flux limit cycle), report NaN as diverged, raise `TAU_MIN` or refuse tau under 0.51; or accept Re <= 100 as the calibrated range | Claude Code + Kimi | code |
| 5 | Let `simlab_adapter` pass `u_lattice`; rerun primitive at 0.012 for Re <= 100 | Claude Code + Kimi | 5-7 GPU-hours |
| 6 | Extend the resolution study to n = 160 and 192 for gyroid 0.10 and diamond 0.16 at Re 30 and 100; gate h on boundary-layer voxels; land Bouzidi walls before the next campaign | Kimi + Claude Code | tens of GPU-hours |
| 7 | Re-settle the five Re-100 cells from their final populations with 1e-3 velocity noise, and run `c006` and `c022` as 2x2x2-cell boxes; keep rows only if dpdx and h hold within 1 %; save the flux history | Kimi | about 10-20 GPU-hours |
| 8 | Carry `mass_flux`, `n`, grid Peclet, Mach, the row's own boundary and the evidence hash into corpus rows; make `collect` enumerate the sweep and write a manifest with every case's disposition | Claude Code | code |
| 9 | Point `.mcp.json` and `agent-brief.yaml` at `:8022/mcp`; move the token out of `UI-INTEGRATION.md` | Aaron | minutes |

## Evidence

- MLflow experiment 9 on `http://192.168.1.65:5000` (runs `aces-cal-air-01/cNNN`, `aces-cfd-resolution/c000-c005`,
  `aces-cfd-validation/c000-c001`), with `sample.json` and `evidence.json` artifacts per run.
- SIMLAB console `http://192.168.1.65:8032/api/experiments` (job list and statuses for the experiment).
- Code: `cpa-techallday/aces` at `2a7d87a`; factors at `ff9a431`.
- Verification: eight refuter agents (one per finding) and two critic agents (numerics, data pipeline) ran on
  2026-10-09 with the evidence bundle and a read-only worktree; their harness scripts and the bundle are in the
  session scratchpad.
