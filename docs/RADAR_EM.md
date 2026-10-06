# ACES radar domain — the AERIS-X64 EM simulation pipeline

Doc: CPA-ACES-RADAR-001 · Rev A · 2026-10-07 · status: fast tier live, verification tier pending image

This adds the AERIS-X64 X-band phased-array radar as an ACES domain
(`radar.phased_array`) and wires the simulation pipeline that carries a radar
design from trade study to EM sign-off, on the same rails as the HX domain:
spec → NSGA-II on the fast tier → rig verification → calibration back into the
fast tier → evidence ledger.

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
| Antenna plate far-field / THRU coupon bench sweep | `antenna_plate` structure + `radar.calibrate` rows |
| SI sign-off (DDR byte lanes, JESD skew) — HyperLynx/ADS/Sigrity not installed | stays external; not claimed here |
| Fast design iteration on array size / power / waveform | `radar.phased_array` fast tier |

## 2. The pipeline

```
spec (YAML, radar.phased_array)
  │  aces check / aces design            fast tier: Friis cascade, array factor,
  ▼                                      link budget, thermal density, scan limits
NSGA-II search  ── shortlist ──►  simlab sweep on the rig (aces-tile-em)
                                      │  Palace FEM S-parameters of the tile /
                                      │  rf_dist / antenna_plate from the real
                                      │  stackup; gprMax FDTD for propagation
                                      ▼
                    measurement.json rows per case (kind: tile_insertion_loss_db, ...)
                                      │
                    aces.domains.radar.calibrate.fit ──► registry factors
                        radar.tx.line_loss_factor · radar.rx.nf_factor · radar.array.gain_factor
                                      │
                    fast tier re-runs calibrated; reports drop "uncalibrated"
                                      │
                    evidence ledger: every run hash-chained under CPA (R&DTI)
```

The loop rule is unchanged from the platform: **the fast tier ranks, the EM
solver verifies, bench data calibrates.** No EM numbers exist until the solver
image passes its validation cases — the adapter raises rather than fabricate.

## 3. Fast tier — `aces.domains.radar`

| Module | Contents |
|---|---|
| `array.py` | planar array: beamwidth, grating-lobe scan limit, phase-quantization loss/sidelobes, taper table, array gain, aperture area |
| `cascade.py` | Friis NF/gain cascade, output-referred P1dB |
| `linkbudget.py` | Albersheim required SNR, energy-form range equation, range/Doppler resolution, unambiguous range/velocity |
| `model.py` | `RadarRequirements` (pydantic) + `PhasedArrayRadar` (`radar.phased_array` v0.1.0) implementing the DesignModel contract |
| `calibrate.py` | measurement rows (JSON/CSV or a finished simlab experiment) → registry factors with provenance |
| `em_adapter.py` | simlab adapter `aces-tile-em` (see §4) |

Conventions the model documents on every result:

- **Coherent CPI.** Detection range credits the 64-pulse Doppler CPI as coherent
  integration (n× gain against the single-pulse Albersheim requirement). The
  redesign study's headline ranges (10.5 km vs 1 m²) were single-pulse and are
  ~2× more conservative; the model's numbers will read higher, and say so in
  `warnings`.
- **Albersheim is the steady-target bound.** Swerling fluctuation loss is not
  applied.
- **No coupling/mismatch/clutter.** Those enter only through the calibration
  factors from EM sim and bench.

Physics cross-checks (in `tests/test_radar.py`):

- the study's Project-A baseline reproduces: 160 W / EIRP 40 dBW / 100 µs /
  NF 5 dB → 2.5 km here vs ~2.7 km computed there;
- the X64 nominal point (64 × 25 W, λ/2, 1 GHz chirp, 50 µs @ 6 kHz) lands at
  EIRP 56.1 dBW (study: ~57 dBW), 15 cm resolution, 25 km unambiguous range.

### First result worth reading

Running the template spec at the X64 nominal point: **feasible = False —
thermal.** 64 × 25 W at 30 % duty dissipates ~10.3 W/cm² over the 11.4 × 11.4 cm
aperture, above both the 4 W/cm² template limit and Project B's 6.5 W/cm²
failure class. The study's thermal answer (16 liquid-cooled ~80 W tiles, i.e.
spreading dissipation beyond the bare aperture) is a *system* assumption the
fast tier correctly refuses to take for free — the search will trade duty, per-
element power and pitch until the density closes, and the EM/thermal rig work
decides what limit is real. Also note the model's 8×8 λ/2 beamwidth is 12.7°,
not the "~3.6° per 16-el row" the study table claims (that figure needs ~28
elements per row; the study's own audit habit applies to itself).

## 4. Verification tier — `aces-tile-em` (pending image)

Solver id `aces-tile-em`, adapter `aces.domains.radar.em_adapter.Adapter`,
same worker contract as `aces-tpms-periodic` (`run(case, spec, run_dir)`,
`measurement.json` per case). Physics: **Palace** (open-source FEM, S-parameters
of driven structures from mesh + stackup) as primary; **gprMax** (FDTD) for
propagation and antenna-plate checks. Both are on the runtime plan's solver
list (RUNTIME_PLAN §3, "Electromagnetics — Palace and gprMax (AERIS radar tile,
HERMES antennas)").

Case parameters: `structure` (`tile` | `rf_dist` | `antenna_plate`), frequency
sweep, `stackup` (er, h, loss tangent — from the board's real stackup, which is
how the tile stackup discrepancy gets resolved), structure geometry, solver
choice.

**Validation cases that must pass before registry entry (decision 6):**

| Case | Reference | Pass bar |
|---|---|---|
| `wr90_through` | 25.4 mm WR-90 through | S21 within 0.1 dB of 0 dB; cutoff within 1 % of 6.557 GHz |
| `patch_xband` | λ/2 patch on RO4350B (er 3.66, h 0.508 mm) | resonance within 2 % of the cavity-model frequency (`em_adapter.patch_resonance_hz`) |

**To bring the tier up** (mirrors the aces-cfd rollout, steps 3–12 of the
50-step plan):

1. Build the `aces-em:0.1` image on the workstation (Palace from source — Phase
   2b class build like MFEM+cuDSS; no PyPI route on the rig).
2. Self-test both validation cases on the workstation GPU.
3. Ship to the rig with the server agent; register `aces-tile-em` in
   `solvers.json` marked *validation pending*.
4. `aces cfd-campaign`-style sweep over the tile stackup grid → `measurement.json`
   rows → `radar.calibrate.fit` → registry with `simlab:<run-id>` provenance.
5. Reports stop saying "uncalibrated" for the three radar factors.

Export-control note: AERIS board data plausibly sits under DSGL 6A008. The EM
tier runs **on-prem on the rig only** (that is already the platform's sovereign
stance); no Gerber, stackup or S-parameter data leaves the LAN, and the registry
records factors and provenance, not geometry.

## 5. Files

| What | Where |
|---|---|
| Domain package | `src/aces/domains/radar/` (`array`, `cascade`, `linkbudget`, `model`, `calibrate`, `em_adapter`) |
| Registration | `src/aces/domains/__init__.py` (`MODELS`) |
| Spec template | `specs/templates/radar_phased_array.yaml` (X64 baseline values, datasheet-cited chain) |
| Tests | `tests/test_radar.py` (16 tests) |

## 6. What this does not do yet

- Palace/gprMax image does not exist; the adapter raises until it does.
- SI sign-off (DDR skew, JESD lanes) stays in HyperLynx/ADS/Sigrity class tools —
  not claimed by this pipeline.
- No mutual-coupling or scan-blindness model in the fast tier (calibration
  factors absorb first-order reality once measured).
- `aces em-sample` / `em-campaign` / `em-collect` CLI commands land with the
  image, mirroring the cfd trio; until then `calibrate.fit` is callable from
  Python and from `aces calibrate`'s measurement-row convention.
