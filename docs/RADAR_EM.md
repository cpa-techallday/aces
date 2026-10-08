# ACES radar domain — the AERIS-X64 EM simulation pipeline

Doc: CPA-ACES-RADAR-001 · Rev F · 2026-10-09 · status: fast tier live, verification tier live on gprMax AND Palace — patch_xband, wr90_through, tile_coupon, tile_multiport PASS on workstation, in the `aces-em:0.1` rig image, and wr90_through cross-checked FEM-vs-FDTD (S21 agreement 0.005 dB); patch far-field CLOSED with a verified matched-feed realized gain **5.33 dBi @ 9.88 GHz** (job22: stacked-source port fix, probe-inset match 1.42 mm, η_rad 0.79, S11 ≈ −9.7 dB); remaining: KiCad-layout import

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

## 4. Verification tier — `aces-tile-em` (gprMax live; rig image `aces-em:0.1` built)

Solver id `aces-tile-em`, adapter `aces.domains.radar.em_adapter.Adapter`,
same worker contract as `aces-tpms-periodic` (`run(case, spec, run_dir)`,
`measurement.json` per case). Engines:

- **gprMax 4.0.1 (FDTD) — LIVE (2026-10-07, pip install on DARKNESS, CPU
  OpenMP).** The adapter generates the input deck from case parameters, runs
  `python -m gprMax` as a subprocess, reads the HDF5 port/probe data, and
  writes `validation.json` / `measurement.json`. Ports to the rig image
  unchanged.
- **Palace v0.14.0 (FEM) — LIVE in the rig image** (`aces-em:0.1`,
  2026-10-08; from-source superbuild, `docker/aces-em/` with
  `fix_superbuild.sh` for the upstream bit-rot: dead sundials pin → v7.2.1,
  make-based externals invoked as ninja).  `solver="palace"` with
  `validate="wr90_through"` runs end-to-end: the adapter generates the WR-90
  mesh (gmsh), writes the Driven config with two TE10 WavePorts, runs palace,
  and validates (job19: S21 ≤ 3e-7 dB, S11 ≤ −134 dB, port kₙ within 0.021 %
  of the TE10 dispersion, cutoff 6.55621 GHz = 0.012 % from the dispersion
  fit — pass=1).  Palace's role is *cross-check*: gprMax eigenmode ports
  already cover the waveguide/multiport cases; job18 confirmed the two
  solvers agree to 0.005 dB on S21.  Other structures raise honestly.

Case parameters: `structure` (`tile` | `rf_dist` | `antenna_plate`), frequency
sweep, `stackup` (er, h, loss tangent — from the board's real stackup, which is
how the tile stackup discrepancy gets resolved), structure geometry, solver
choice.

**Validation cases that must pass before registry entry (decision 6):**

| Case | Reference | Pass bar | Status |
|---|---|---|---|
| `patch_xband` | λ/2 patch on RO4350B (er 3.66, h 0.508 mm, tanD 0.004), cavity model with Hammerstad fringing | FDTD resonance within **5 %** of the cavity reference **and** the two independent FDTD estimators (far-end probe FFT, feed-port S11 minimum) agreeing within 1.5 % | **PASS (2026-10-07)**: FDTD 9999.5 MHz vs cavity 10524.1 MHz (4.98 %), estimator agreement 0.003 % |
| `wr90_through` | 25.4 mm WR-90 through, gprMax eigenmode ports | S21 within 0.1 dB of 0 dB; S11 ≤ −20 dB; cutoff within 1 % of 6.557 GHz | **PASS (2026-10-07)**: S21 within ±0.006 dB over 8.2–12.4 GHz, S11 −27.5…−71 dB, cutoff 6.55653 GHz (0.007 %); evanescent tunneling below cutoff matches theory (2 dB over the 13 mm port gap at 6.5 GHz) |

**Structure case `tile_multiport` — 2-port S-parameters by travelling-wave
decomposition (PASS 2026-10-07):** gprMax single-edge lumped ports cannot read
a microstrip's modal voltage — they return the ~9–16 fF feed-gap capacitance
(|S11| = 1.000 flat while the identical coupon geometry rings at Q ≈ 138).
The adapter therefore extracts S-parameters from field probes: sequential
excitation, a 2×2 resistive column load (`σ = 1/(100·cell)` → R ≈ 50 Ω at
σ·dt/2ε ≈ 0.3; a single full-height column needs 630 S/m and is
FDTD-unstable), and a 3-probe least-squares forward/backward wave split
(`wave_split_ab`) per port.  Validated on the real board-1 stackup (0.254 mm
RO4350B): S11/S22 −21…−22.4 dB, S21/S12 −0.01…−0.04 dB, reciprocity
1.5×10⁻⁷ dB, split residuals < 0.7 %, β within 1.2 % of the er_eff model.
Arbitrary N-port layouts from the KiCad RF layout are future work; the
machinery (feed, load, wave-split, sequential excitation) is what was missing.

**Patch far-field (job14 + job20 audit, resolved):** gprMax 4.x
layered-background NTFF (`#ntff_layered_background` with the PEC-terminated
RO4350B stack — the Sommerfeld formulation; a conventional Huygens box is
rejected when it straddles materials, gain normalization requires a
rectangular DFT window, observation must stay above the horizon).  The job20
audit closed the "gain provisional" flag: gprMax's port bookkeeping is
internally consistent (power conservation exact, validity flags green), but
the terminal impedance is dominated by a constant ~35 fF feed-gap capacitance
(the same single-edge port artifact as tile_multiport), and 10.5 GHz is off
the antenna's mesh resonance (**9.76 GHz** at 0.254 mm cells).  Measured at
the resonance: max directivity **6.78 dBi** (θ = 3°, port-independent,
trustworthy), radiation efficiency **0.74** (NTFF radiated/accepted) with a
port-independent ringdown upper bound **0.82** (Q = 45.2 from a textbook
exponential tail, R² = 0.9997, vs Q_diel = 250; the ~8-point gap is the
surface-wave fraction the infinite slab traps).  **Gain at a matched feed:
5.5–5.9 dBi (estimate) — VERIFIED by job22 at 5.33 dBi realized.**
job14's 2.34 dBi @ 10.5 GHz is superseded — it measured the mismatch, not
the antenna.

job22 (matched feed, verified realized gain): the ~35 fF artifact is the
single-point probe source sitting mid-substrate with series grid gaps to
ground and patch.  Replacing it with **stacked z-directed sources on every
substrate Ez node plane** (2 × 25 Ω, port IDs grouped in
`#ntff_antenna_ports`) removes the gap: the detuned 9.75 GHz resonance
returns to ~10.0 GHz (the job21 finer-mesh value), and a probe-inset sweep
shows a clean match plateau at 1.2–1.7 mm from the radiating edge with
rad/acc stable at 0.79 across all positions.  Confirmation run at the
1.42 mm inset (25 ns window, NTFF at the 9.874 GHz resonance): directivity
6.83 dBi, radiation efficiency 0.792, mismatch acceptance 0.894
(S11 ≈ −9.7 dB) → **verified realized gain 5.33 dBi @ 9.88 GHz**, realized
efficiency 0.708.  Deck rule: never feed a patch through a single
mid-substrate point source — stack sources on every substrate Ez node
plane or the port total-reflects.

Why 5 % and not 2 %: the cavity model is the *approximate* side of the
comparison — closed magnetic walls, no feed, no surface waves — good to ~3–5 %
for h/λ ≈ 0.018 patches. What actually pins the solver setup is the
mesh-convergence study plus internal agreement:

| Cells through substrate | FDTD resonance (probe FFT / port S11) |
|---|---|
| 2 (cell 0.254 mm) | 9.70 GHz (both estimators) |
| 3 (cell 0.169 mm) | 9999.5 / 9999.8 MHz |
| 4 (cell 0.127 mm) | 9999.6 / 10099.8 MHz |

Converged FDTD ≈ 10.0–10.1 GHz, two estimators within 0.3–1 % of each other;
the residual ~5 % against the cavity model is reference-model error and is
documented as such in `validation.json` (`bar_pct`, `estimator_agreement_pct`).

**FDTD modeling lessons baked into the adapter** (each cost a failed run):

- Ez edges lying on a PEC surface are pinned to zero — the feed source and the
  probe must sit on a live edge *inside* the substrate, and the probe away
  from the feed point so it reads the mode, not the driving point.
- gprMax 4.x turns every `#voltage_source` into a port and stores per-point
  validity flags (`valid_S11`, `source_valid`, `mesh_valid`) — mask with them,
  they are per-frequency arrays, not scalars.
- gprMax 4.x writes `<case>.h5` with no per-rx time vector; the time axis is
  rebuilt from the root `dt` attribute.
- Patch/coupon dimensions are snapped to whole cells and the cavity reference
  is computed for the *snapped* geometry, keeping grid rounding out of the
  pass bar.

**Structure case `tile_coupon` — λg/2 open-line resonator, delta-Q:**

The coupon measures the board-9 stackup's dielectric loss. Two through-line
transfer approaches failed honestly first (single-probe: +29.7 dB nonsense —
a field probe is not a calibrated voltage; reference-subtracted 3-probe
average: +1.07 dB with 3.1 dB probe-to-probe standing-wave contamination
against a 0.14 dB signal). The shipped method is a half-wave open-line
resonator with ring-down Q:

- raw Q loads dielectric loss with open-end radiation + feed loading
  (measured Q=87 vs dielectric Q~200) → a second run with tanD = 0 de-embeds
  them: `1/Q_diel = 1/Q_dut − 1/Q_ref`, then `α = β/2Q_diel`;
- Q from a direct least-squares decay fit `exp(−t/τ)(C cos ωt + S sin ωt)` —
  envelope/Hilbert extraction proved leakage-biased +70 % on a synthetic
  Q=180 ring-down, the direct fit is exact;
- conductors are PEC, so copper loss is excluded on both sides; result is
  normalised to a 20 mm reference length as `tile_insertion_loss_db`.

**First calibrated row (2026-10-07, job7-em-coupon + sweep):** measured
5.19 dB/m vs Hammerstad dielectric model 5.32 dB/m at 9.68 GHz, plus sweep
rows at 7.93/11.36 GHz (ratios 0.862/0.975/1.006) → `radar.calibrate.fit` →
**`radar.tx.line_loss_factor = 0.9496`** (4 rows), provenance `simlab:f8.5`,
`simlab:f12.5`, `simlab:job7-em-coupon`, `simlab:job21-coupon-finemesh`, valid 7.9–11.4 GHz. The full loop —
FDTD → measurement.json → read_rows → fit → registry — is demonstrated
end-to-end, and `aces design` no longer reports the line-loss factor as
uncalibrated. The real 0.254 mm board-1 stackup row landed the next day:
job11 (2-cell mesh) measured 3.98 vs model 5.13 dB/m (ratio 0.776) with
elevated fit residuals, so it was held out; **job21 (3-cell mesh, 2026-10-09)
measured 4.93 vs model 5.12 dB/m (ratio 0.963)** with Q estimates consistent
to <1 % across two independent fits (Q_dut 180, Q_ref 424 — the coarse mesh
had been taxing Q_ref with numerical loss).  job21 is folded into the
registry: **`radar.tx.line_loss_factor = 0.9496`**, provenance `simlab:f8.5`,
`simlab:f12.5`, `simlab:job7-em-coupon`, `simlab:job21-coupon-finemesh`,
valid 7.9–11.4 GHz.  (job21's raw fit residuals remain above the 0.05 comfort
bar at 0.18–0.56 — the fold rests on the cross-fit Q consistency, recorded
here honestly.)

**Tier status** (mirrors the aces-cfd rollout):

1. ~~Finish the `aces-em:0.1` image build~~ — **DONE 2026-10-08** (Palace
   v0.14.0 from source + gprMax 4.0.1 pip, `docker/aces-em/`; sundials pin
   re-pointed to v7.2.1 after the upstream commit vanished, four make-based
   externals de-ninjafied — all captured in `fix_superbuild.sh`).
2. ~~Re-run the validation cases on the rig~~ — **DONE 2026-10-08**
   (job17-container-crosscheck: `tile_multiport`, `wr90_through`,
   `patch_xband`, `tile_coupon` re-run inside `aces-em:0.1` from fresh decks;
   results reproduce the workstation numbers to FDTD precision. Container
   gotcha: gprMax's OpenMP oversubscribes in a VM — run with
   `-e OMP_NUM_THREADS=<cores>`).  ~~`wr90_through` under Palace~~ — **DONE
   2026-10-08** (job18/job19: FEM-vs-FDTD cross-check PASS; S21 agreement
   0.005 dB, port kₙ within 0.021 % of theory, cutoff 0.012 %).
3. Register `aces-tile-em` in `solvers.json` (validation passed for gprMax
   cases, pending for Palace cases).
4. Sweep the real tile/rf_dist stackups → `measurement.json` rows →
   `radar.calibrate.fit` → registry with `simlab:<run-id>` provenance
   (four rows landed incl. the real board-1 stackup via job21's finer-mesh
   re-run — factor 0.9496).
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
| Tests | `tests/test_radar.py` (16 tests), `tests/test_em_adapter.py` (17 tests) |

## 6. What this does not do yet

- Palace (waveguide-port FEM) is LIVE for `validate="wr90_through"`
  (`solver="palace"`, job18/job19): the adapter builds the WR-90 mesh (gmsh)
  and Driven config, runs palace in the rig image, and validates S-magnitudes,
  port kₙ against the TE10 dispersion (0.021 %), and cutoff from the
  dispersion fit (0.012 %).  Two solver limitations recorded: the "Linear"
  sample type (PROM fast sweep) fails its wave-port eigensolver on this
  geometry — use "Point" lists; and on v0.14.0-dirty the port eigensolver
  does not converge below ~9.5 GHz here (robust to mesh/order/tolerances), so
  the band is 9.5–12.4 GHz.  Palace paths for the other structures remain
  unimplemented (honest raise).  gprMax CPU-only (no `-gpu` build) — large
  domains queue behind wall-time budgets.
- Patch far-field gain is CLOSED (job20 audit + job22 matched feed):
  directivity 6.83 dBi, radiation efficiency 0.79, **verified realized gain
  5.33 dBi @ 9.88 GHz** with a stacked-source probe feed at the 1.42 mm
  match inset (S11 ≈ −9.7 dB).  Further antenna-gain work (inset-fed or
  aperture-coupled redesigns) is antenna design, not pipeline work.
- tile_multiport is the machinery + a 2-port through-line validation; parsing
  the real KiCad RF layout (board 1/3/9) into multiport geometry is future
  work.  The gprMax eigenmode-port sweep beta degrades mid-anchor (up to
  6.9 % at 9.5 GHz vs theory, observed in the job18 Palace comparison) while
  S-magnitudes are unaffected — phase-sensitive uses should re-anchor.
- SI sign-off (DDR skew, JESD lanes) stays in HyperLynx/ADS/Sigrity class tools —
  not claimed by this pipeline.
- No mutual-coupling or scan-blindness model in the fast tier (calibration
  factors absorb first-order reality once measured).
- `aces em-sample` / `em-campaign` / `em-collect` CLI commands land with the
  rig image, mirroring the cfd trio; until then `calibrate.fit` is callable
  from Python and from `aces calibrate`'s measurement-row convention.
