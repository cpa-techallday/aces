# ACES radar domain — the AERIS-X64 EM simulation pipeline

Doc: CPA-ACES-RADAR-001 · Rev B · 2026-10-07 · status: fast tier live (needs sourced inputs and a route), verification tier pending image

This adds the AERIS-X64 X-band phased-array radar as an ACES domain
(`radar.phased_array`) and wires the simulation pipeline that carries a radar
design from trade study to EM sign-off, on the same rails as the HX domain:
spec → NSGA-II on the fast tier → rig verification → calibration back into the
fast tier → evidence ledger.

**Export control (rule 12):** radar design data (specs, run folders, stackups, meshes, S-parameters) must stay on
the LAN until the radar work has been classified under the DSGL; the classification is Aaron's decision.

Rev B (audit 2026-10-07, findings R1–R6) corrected the fast-tier physics, removed unsourced defaults, made
`aces design` complete for radar, added `aces radar-calibrate` and the `pcb_antenna` route, and fixed the EM
adapter. Results computed with Rev A are withdrawn (see §3, "Rev A result withdrawn").

---

## 1. Why this exists now

The AERIS-X64 rebuild (`C:\Users\shedm\aeris-x64-rebuild`, snapshot exported
2026-10-01) is electrically near-complete — boards 2/4/5/6/7/8 DRC-clean, board 3
final-as-documented, board 1 Rev C orderable. The gates that remain before REV D
and fabrication are **simulation gates**, and they are exactly what ACES is for:

| AERIS blocker (REMAINING_BLOCKERS.md / FUSION_HANDOFF.md) | Pipeline stage here |
|---|---|
| EM-sim pre-order gate on the Rev C tile (release path) | `aces-tile-em` verification tier (Palace FEM) |
| Board 9 RF distribution "not EM-simulated; EM gate s2.3 before any order" | `aces-tile-em` structure `rf_dist` |
| Tile stackup-vs-board discrepancy → RF return path must be re-derived for the EM/SI gate | stackup is an explicit case parameter; EM resolves the real return path |
| Antenna plate far-field / THRU coupon bench sweep | `antenna_plate` structure + `aces radar-calibrate` rows |
| SI sign-off (DDR byte lanes, JESD skew) — HyperLynx/ADS/Sigrity not installed | stays external; not claimed here |
| Fast design iteration on array size / power / waveform | `radar.phased_array` fast tier |

## 2. The pipeline

```
spec (YAML, radar.phased_array; sourced inputs, manufacturing route)
  │  aces check / aces design            fast tier: Friis cascade, array factor,
  ▼                                      link budget, thermal density, scan limits
NSGA-II search  ── shortlist ──►  simlab sweep on the rig (aces-tile-em)
                                      │  Palace FEM S-parameters of the tile /
                                      │  rf_dist / antenna_plate from the real
                                      │  stackup; gprMax FDTD for propagation
                                      ▼
                    measurement.json rows per case (kind: tile_insertion_loss_db, ...)
                                      │
                    aces radar-calibrate (D18 rules) ──► registry factors
                        radar.tx.line_loss_factor · radar.rx.nf_factor · radar.array.gain_factor
                                      │
                    fast tier re-runs calibrated; reports drop "uncalibrated"
                                      │
                    evidence ledger: every run hash-chained under CPA (R&DTI)
```

The loop rule is unchanged from the platform: **the fast tier ranks, the EM
solver verifies, bench data calibrates.** No EM numbers exist until the solver
image passes its validation cases — the adapter raises rather than fabricate.

**Manufacturing route (rule 13a).** A radar spec must name `manufacturing.process`. The only radar route,
`pcb_antenna` (RF PCB and planar antenna fabrication), is `planned`: `aces check` and `aces design` refuse it,
like every planned route, until it has a layout/stackup generator and fab-house checks. Until then no radar
spec runs end to end; the CLI path is tested with the route marked available inside the test only.

## 3. Fast tier — `aces.domains.radar`

| Module | Contents |
|---|---|
| `array.py` | planar array: beamwidth, grating-lobe scan limit, phase-quantisation loss/sidelobes, taper table (one axis) and separable planar efficiency, array gain, cos^n scan loss, aperture size |
| `cascade.py` | Friis NF/gain cascade, output-referred P1dB cascade (and its input-referred value) |
| `linkbudget.py` | Albersheim required SNR (refused outside its fitted range), system noise temperature, energy-form range equation, blind range, range/Doppler resolution, unambiguous range/velocity |
| `model.py` | `RadarRequirements` (pydantic) + `PhasedArrayRadar` (`radar.phased_array` v0.2.0) implementing the DesignModel contract and the `aces design` contract (`rank_objective`, `run_inputs`, `report`) |
| `report.py` | the radar run report: "Not verified", the export-control line, every sourced input with its source |
| `calibrate.py` | measurement rows (JSON/CSV or a finished simlab experiment) → registry factors with provenance, under D18 |
| `em_adapter.py` | simlab adapter `aces-tile-em` (see §4) |

**Sourced inputs (rules 1, 2).** The model has no defaults for numbers it cannot know. Each of these is a
`{value, source}` pair in the spec, and a spec without one is refused: element gain, element pattern exponent
n, antenna noise temperature, system losses, TX line loss, PA efficiency, the PA's rated duty, and support
electronics DC per element. The template leaves them empty until real datasheets, test reports or EM results
are cited. `max_blind_range_m` (the closest range a target must be seen at) is a required requirement.

Conventions the model applies and states on every result:

- **Integration is explicit.** `detection.integration: coherent` credits the CPI as n × single-pulse energy
  against the single-pulse (n = 1) Albersheim SNR. `noncoherent` uses Albersheim's n-pulse SNR against
  single-pulse energy. Coherent integration of 64 pulses gives 18.06 dB against 13.26 dB noncoherent, a range
  ratio of 1.32.
- **Albersheim's range.** Pd 0.1–0.9, Pfa 1e-7–1e-3, n 1–8096 (about 0.2 dB error). Outside it the spec is
  refused (rule 5). It is the steady-target bound: Swerling fluctuation loss is not applied.
- **Blind range.** A monostatic pulsed radar cannot see inside c·τ/2 (7.49 km for a 50 µs pulse). No detection
  is claimed inside it, and `max_blind_range_m` is a constraint. T/R switching and receiver recovery time are
  not included, so the real blind zone is larger.
- **Scan loss.** The requirement range is held at the edge of the scan sector, with the gain reduced by
  cos^n(θ) each way (n from the spec). Broadside and scan-edge ranges are both reported.
- **Duty is the waveform's** (τ × PRF). It is never clipped: above the PA's rated duty the design is
  infeasible, and power, DC and heat are reported at the waveform duty.
- **Thermal.** DC = average RF / PAE + support electronics per element; everything not radiated is
  dissipated over the aperture. (Rev A used an unsourced 20 % overhead.)
- **Noise.** Ts = Ta + T0 (F − 1); with Ta = 290 K this is the T0·F form of the redesign study.
- **Phase quantisation** costs gain: the mean loss for decorrelated errors is −10·log10(sinc²(π/2^B)),
  0.22 dB at 3 bits (Rev A had the sign reversed).
- **Taper.** The table holds one-axis (line-source) values; the planar array uses the separable taper, so its
  efficiency is the product of both axes'. The table's own values are flagged for a source check (§6).
- **Aperture.** The limit applies to the larger of the width and height (Rev A used √area).
- **P1dB** is cascaded output-referred: 1/P = Σ 1/(OP1dB_i × G_after_i), the cascaded-intercept form.
- **No coupling/mismatch/clutter/scan blindness.** Those enter only through the calibration factors from EM sim
  and bench.

Physics cross-checks (in `tests/test_radar.py`, each with its hand calculation):

- range equation: the study's Project-A baseline (160 W / EIRP 40 dBW / 100 µs / NF 5 dB) gives 2505 m here vs
  ~2.7 km computed there;
- Albersheim 13.11 dB (Pd 0.9, Pfa 1e-6, n = 1); Friis NF 2.76 dB for the template chain; beamwidth, grating-lobe
  limit and patch resonance unchanged;
- the X64 nominal point (64 × 25 W, λ/2, 1 GHz chirp, 50 µs @ 6 kHz) lands at EIRP 56.1 dBW (study: ~57 dBW),
  15 cm resolution, 25 km unambiguous range, and a 7.49 km blind range.

### Rev A result withdrawn

Rev A reported the X64 nominal point as thermally infeasible at ~10.3 W/cm². That figure used unsourced
defaults (PAE 0.30, a 20 % overhead, losses and element gain without sources) and is withdrawn until the spec
carries sourced values. Two observations do not depend on those inputs and stand:

- the 8×8 λ/2 beamwidth is 12.7°, not the "~3.6° per 16-el row" the study table claims (that figure needs ~28
  elements per row);
- a 50 µs pulse blinds the radar out to 7.49 km (c·τ/2), which the study's 10.5 km detection claim does not
  account for. Short-range cover needs a short-pulse mode or a separate waveform.

## 4. Verification tier — `aces-tile-em` (pending image)

Solver id `aces-tile-em`, adapter `aces.domains.radar.em_adapter.Adapter`,
same worker contract as `aces-tpms-periodic` (`run(case, spec, run_dir)`,
`measurement.json` per case). Physics: **Palace** (open-source FEM, S-parameters
of driven structures from mesh + stackup) as primary; **gprMax** (FDTD) for
propagation and antenna-plate checks. Both are on the runtime plan's solver
list (RUNTIME_PLAN §3, "Electromagnetics — Palace and gprMax (AERIS radar tile,
HERMES antennas)").

Case parameters: `structure` (`tile` | `rf_dist` | `antenna_plate`), frequency sweep, `stackup` (εr, loss
tangent and their `source` — from the board's real stackup, which is how the tile stackup discrepancy gets
resolved; there is no default substrate), the structure's Gmsh `mesh` and `mesh_unit_m`, mesh `attributes`
(substrate, air, PEC, optional absorbing), at least two lumped `ports`, solver choice, `timeout_s`, and the fast
tier's `model_loss_db` for the case.

How the adapter behaves:

- it writes a complete Palace driven-solve config (materials, PEC and port boundaries, sweep), or refuses and
  names what the case lacks;
- Palace runs through `aces.proc.run` (argument list, minimal environment, timeout that kills the process
  group); solver output goes to `palace.log`;
- S21 is read from the `|S[2][1]| (dB)` column of `postpro/port-S.csv`, found by its header;
- `converged` comes from the run: no non-convergence warning in the solver log, and a finite sweep covering the
  requested band. The warning text Palace prints must be confirmed against the image's pinned Palace version
  in its validation run;
- a measurement row is written only for a converged, passive result with `model_loss_db` given;
- gprMax (GPL-3.0) only ever runs as a separate process. The guard imports gprMax in the configured interpreter
  (`GPRMAX_PYTHON`) rather than trusting that `python` exists; the gprMax solve path then refuses until its input
  generation and post-processing land with the image;
- verification runs are FP64 only (rule 6).

**Validation cases that must pass before registry entry (D5 / rule 3: a registered solver signs off, and the
validation record is what registers it):**

| Case | Reference | Pass bar |
|---|---|---|
| `wr90_through` | 25.4 mm WR-90 through | S21 within 0.1 dB of 0 dB; cutoff within 1 % of 6.557 GHz |
| `patch_xband` | λ/2 patch on RO4350B (er 3.66, h 0.508 mm) | resonance within 2 % of the cavity-model frequency (`em_adapter.patch_resonance_hz`) |

**To bring the tier up** (mirrors the aces-cfd rollout, steps 3–12 of the
50-step plan):

1. Build the `aces-em:0.1` image on the workstation (Palace from source — Phase
   2b class build like MFEM+cuDSS; no PyPI route on the rig). Pin the Palace version.
2. Self-test both validation cases on the workstation GPU, and confirm the solver's non-convergence wording.
3. Ship to the rig with the server agent; register `aces-tile-em` in
   `solvers.json` marked *validation pending*.
4. `aces cfd-campaign`-style sweep over the tile stackup grid → `measurement.json`
   rows → `aces radar-calibrate` → registry with `simlab:<run-id>` provenance.
5. Reports stop saying "uncalibrated" for the three radar factors.

Export-control note: AERIS board data plausibly sits under DSGL 6A008. The EM
tier runs **on-prem on the rig only** (that is already the platform's sovereign
stance); no Gerber, stackup or S-parameter data leaves the LAN, and the registry
records factors and provenance, not geometry.

## 5. Calibration — `aces radar-calibrate`

```
aces radar-calibrate ROWS.json --registry radar_registry.json --project P --owner O --rd-activity A
```

Rows are dB values (`value` measured, `model_value` the fast tier's prediction for the same case), each with a
`simlab:` or `test:` source; `held_out: true` keeps a row out of the fit to check it. Under D18:

- the factor multiplies the linear quantity, so a row's ratio is 10^((value − model_value)/10) (Rev A divided
  the dB values);
- one factor per kind is the geometric mean over distinct frequencies (repeated rows of one frequency count
  once); fewer than 3 frequencies, or a sample sd of ln(ratio) above 0.15, is refused;
- held-out rows at frequencies the fit never saw must each be predicted within 10 % (the ROADMAP gate 1 bar); at
  least one is required unless `--min-held-out 0` is given, which the ledger record shows;
- the registry is written through `aces.safeio`, and every fit appends an `aces.calibration` record to the ledger.

## 6. Files and open items

| What | Where |
|---|---|
| Domain package | `src/aces/domains/radar/` (`array`, `cascade`, `linkbudget`, `model`, `report`, `calibrate`, `em_adapter`) |
| Registration | `src/aces/domains/__init__.py` (`MODELS`) |
| Spec template | `specs/templates/radar_phased_array.yaml` (X64 baseline requirements; sourced inputs left empty) |
| Manufacturing route | `pcb_antenna` in `src/aces/manufacturing.py` (planned) |
| Tests | `tests/test_radar.py` |

What this does not do yet:

- Palace/gprMax image does not exist; the adapter raises until it does.
- The `pcb_antenna` route has no generator or checks, so radar specs are refused by `check` and `design`.
- The taper table (`array.TAPERS`, attributed to Mailloux table 2.2) needs its source checked: computed from the
  window definitions, Hamming broadening is about 1.47 (table 1.33) and the Taylor values depend on n̄, which the
  table does not give.
- SI sign-off (DDR skew, JESD lanes) stays in HyperLynx/ADS/Sigrity class tools —
  not claimed by this pipeline.
- No mutual-coupling or scan-blindness model in the fast tier (calibration
  factors absorb first-order reality once measured).
- `aces em-sample` / `em-campaign` / `em-collect` CLI commands land with the
  image, mirroring the cfd trio; until then `calibrate.collect_from_experiment` is callable from Python and
  `aces radar-calibrate` takes the collected rows.
