# ACES radar domain — the AERIS-X64 EM simulation pipeline

Doc: CPA-ACES-RADAR-001 · Rev P · 2026-10-11 · status: fast tier live, verification tier live on gprMax AND Palace — patch_xband, wr90_through, tile_coupon, tile_multiport PASS on workstation, in the `aces-em:0.1` rig image, and wr90_through cross-checked FEM-vs-FDTD (S21 agreement 0.005 dB); patch far-field CLOSED with a verified matched-feed realized gain **5.33 dBi @ 9.88 GHz** (job22); **KiCad-layout import LIVE** (job23: `kicad_net` case, RXO_1 PASS — measured Z0 76.5 Ω vs 50 Ω spec: a real design finding); **50 Ω redesign verified** (job24: w = 0.51 mm → measured 52.7 Ω, inside the ±7 % fab band); **via transitions LIVE** (job25: `via_coupon` case PASS — stitched F.Cu→In2→F.Cu transition, delay 0.3 %, S21 0.30 dB vs cascade); **arbitrary-angle routes LIVE** (job26: staircase-rasterised diagonal import, coupon PASS — Z0 bias ≤ 0.02 %, er_eff fringe +3.1 % at h/4 → +1.7 % at h/5); **multi-layer import LIVE** (job27: `kicad_net_ml` case PASS — F.Cu↔In2.Cu via chains, coupon-equivalence vs job25 within 0.05 dB; the real PAI/TR/CAL nets raise honestly: component-interrupted or stub-ended); **F.Cu pour / GCPW LIVE** (job28: zone polygons parsed, pour rasterised onto decks, coupon PASS — pour drops Z0 8.7 % at 0.2 mm gap, monotonic in gap; RXO_1 with its real pour measures **67.4 Ω**, 11.8 % below the pour-free line); **N-port decomposition LIVE** (job29: `kicad_net_nport` case, 3-port Tee coupon PASS — reciprocity 0.012 dB, column power 0.98–0.99, junction discontinuity sub-dB quantified vs the ideal lossless Tee); **In5.Cu deep stack LIVE** (job30: full TILE_8L depth — In4/In6 planes, deep via antipads + full-cage stitching; via barrels are LINE sections in the cascade, not series L — deep coupon PASS, S11 residual 0.060); **junction-graph extraction LIVE** (job31: `kicad_import.net_graph` reads the Tee topology straight off the `.kicad_pcb` net — extracted-vs-hand-written coupon equivalence **0.0000 dB** across the band; the 17 real RF nets raise honestly — chains or multi-layer, no silent approximation); **EM CLI trio LIVE** (job32: `aces em-sample` / `em-campaign` / `em-collect` mirror the cfd trio — one-command rig samples, simlab sweep export, measurement-row collection for `aces calibrate`); **pour via-fence stitching LIVE** (job33: `pour["fence_mm"]` / `case["stitch"]` ties the F.Cu pour to the bottom plane with real or synthetic stitching vias — job28’s last limitation closed, fence-free decks bit-identical)

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
Arbitrary N-port layouts landed in job29 (`kicad_net_nport`), and job31
closes the loop: `net_graph` extracts the junction graph from the KiCad
net topology itself (`pcb` + `net` on the case), with extracted-vs-
hand-written coupon equivalence of 0.0000 dB across the band.

**KiCad-layout import (job23, LIVE):** `structure: "kicad_net"` parses a
`.kicad_pcb` (S-expressions: nets, segments, vias, pads, zones), chains a
net's copper into an axis-aligned polyline, and builds a grid-snapped
multiport deck from the *real routed geometry* — stacked-source feed on
every substrate Ez node plane (job22 rule) and a stacked zero-excitation
string as a true 50 Ω Thevenin termination (the 2×2 resistive column does
not generalise off its home recipe).  Two solver findings, pinned by a
straight-line control run: with Thevenin ports the wave-split S11 *is* the
far-end interface reflection, so Z0 comes from the complex
termination-interface reflection (`Z0 = 50(1−Γ)/(1+Γ)`); and the
thickness-corrected Hammerstad (35 µm, stackup.py form) is the model FDTD
agrees with (~3 %), not the bare one (−9 Ω bias at w/h = 0.75).  First
real net, `RXO_1` (12.0 mm U-trace, w = 0.22 mm, two unmitered bends):
**PASS** — Z0 **76.5 Ω** vs 80.5 Ω model (5.0 %), er_eff 2.561 vs 2.683
(4.6 %, i.e. 0.28 mm of corner path-saving, 0.7·w per unmitered bend),
reciprocity 0.17 dB, residuals 0.24 %.  **Design finding: RXO_1 (and
TXD_1..4 / ANT_1..4 / CAL_* at 0.20–0.30 mm) is a ~76 Ω line against the
50 Ω ±7 % fab note — 50 Ω needs w ≈ 0.51 mm on this stackup.**  CAL_*
additionally drops to In2.Cu (vias raise honestly in the importer; the
stackup note already flags inner-layer RF as not-50 Ω).  Limitations:
width snaps to the cell grid; the F.Cu GND pour was added in job28 (with
its real pour RXO_1 measures 67.4 Ω — the GCPW correction is *larger* than
the job23 model gap).  (Diagonal routes were added in job26 —
staircase-rasterised, probe triads need axis-aligned end sections.)

**50 Ω redesign verification (job24, PASS):** the job23 finding drove a
respinnable fix — RXO_1 re-routed at w = 0.51 mm.  The kicad_net run on
the widened trace measures **Z0 = 52.7 Ω** (thickness-corrected Hammerstad
model 50.65 Ω, +5.5 % vs the 50 Ω target — inside the ±7 % fab band).
The two unmitered 90° bends reflect at −12 dB: miter at 0.7·w before
tape-out.  Results in `pipeline-runs/job24-redesign/`.

**Via transitions (job25, `structure: "via_coupon"`, PASS):** a stitched
F.Cu→In2.Cu→F.Cu transition coupon on the board-1 hybrid stack (0.254 mm
RO4350B + 2×0.152 mm FR4): 6 mm microstrip – via – 6 mm stripline
(w = 0.203 mm) – via – 6 mm microstrip, square PEC barrels through In1
antipads, 4 stitching vias per signal via.  Reference = ABCD cascade of
the three lossy line sections.  Measured vs cascade: **delay 0.30 %**,
**S21 0.30 dB** (bar 0.5), reciprocity 1.8×10⁻⁵ dB, wave-split residuals
0.002; |S11| delta 0.137 (bar 0.15 — the 1-D cascade cannot represent
antipad capacitance / radiation damping at the transition, which reduce
the measured ripple below the lossless-junction prediction).  Three
findings, each pinned by a test:

- **Stitching is mandatory and must be diagonal.**  Unstitched antipads
  break the return path and mask the stripline section entirely (flat
  −15…−30 dB S11).  But axis-aligned (±off, 0) stitches at the natural
  offset *land on the trace axis and short it* (S11 −0.03 dB, total
  reflection) — stitches go at 45° (d = off/√2 in x and y).
- **IPC-2141 under-predicts stripline Z0 in the wide-strip regime.**  At
  the coupon's w/(b−t) = 0.75 it gives 28.2 Ω; a 2-D Laplace SOR field
  solve of the exact cross-section gives 35.6 Ω and Cohn's fringing form
  35.9 Ω — the model now uses Cohn (`stripline_z0_cohn`), IPC-2141 kept
  for reference.  Compounding bug: the model's b had forgotten the trace
  cell (6 vs 7 cells).
- **Stitched via loop inductance is negligible at X-band** (fit
  L_eff < 0.02 nH; the isolated-barrel Grover 0.16 nH over-predicts and
  is *not* in the reference).  Group delay must include the via vertical
  transits (~3 ps each) — that takes the delay error from 5.2 % to 0.3 %.

A standalone stripline test fixture fed by vertical source strings
between the planes launches parallel-plate modes (S21 −13 dB junk) —
feed stripline coupons via microstrip transitions, never directly.
CAL_* nets stay deferred: their 0.6 mm end sections cannot host a probe
triad and the CPL pad offsets are odd — recorded finding, not a blocker.

**Arbitrary-angle routes (job26, LIVE):** the board has no 45° routes but
several shallow-angle RF fan-outs (RF_IN 10.5°, PAI_1..4 18.4°,
TR_CH1..4 ~6–7°) — these import now.  Diagonal segments are rasterised
as capsules (`rasterize_capsule_cells`: cell centres within w/2 of the
centreline, round end disks for joint continuity), bridged to
face-connected unions (corner-only cell contact is marginal Ez edge
sharing — never emitted), and merged into x-run boxes.  Coupon
(12 mm lines, 9 mm diagonal middle + 1.5 mm straight lead-ins, w =
0.51 mm) vs a straight control: **Z0 bias ≤ 0.02 %, loss bias ≤
0.002 dB**, er_eff +2.4 % (10.5°) / +3.1 % (18.4°).  The er_eff residual
is the physical voxel sawtooth edge fringe — mesh-convergent, +3.1 % at
h/4 → **+1.7 % at h/5** — not import error.  Two findings, both pinned
by tests:

- **Probe triads must never sit on a staircase.**  v1 of the coupon used
  diagonal end sections: the ~0.2 rad triad phase span makes the 3-probe
  split ill-conditioned and the cell-scale stair-edge ripple leaks in
  coherently — apparent non-reciprocity 1.3 dB and >0 dB "gain" on a
  passive line.  `deck_kicad_net` now raises on a diagonal end section.
- **`RF_IN` stays blocked honestly** (19.0 mm straight + 1.37 mm 10.5°
  tail — the tail can't host a triad); measurable after a layout tweak.

**Multi-layer import (job27, `structure: "kicad_net_ml"`, LIVE):** a
`net_sections` chain (F.Cu microstrip + In2.Cu stripline joined by vias)
builds the full deck with the job25 via recipe (barrels, In1 antipads,
4 diagonal stitches each) and job26 rasterised diagonals; reference =
ABCD cascade of per-section lossy models with via vertical transits.
Validated by **coupon-equivalence**: a synthetic chain with the
via_coupon's snapped geometry reproduces the job25 *measured* S-params
to 0.052 dB (S21) / 0.0038 (|S11|) — two independent deck builders, one
physics — and meets the cascade contract (S21 0.25 dB, delay 0.33 %,
reciprocity 2×10⁻⁵ dB).  Real-net outcome is honest negatives, now with
explicit reasons: `CAL_2` raises on its 0.6 mm end section (triad guard),
`PAI_1`/`TR_CH1` are component-interrupted chains (4 segment-graph ends)
with their buried runs on In5.Cu (a stack region this deck does not
model).  The machinery is validated and waiting for the Rev D layout.

**F.Cu pour / GCPW (job28, LIVE):** `parse_pcb` now captures zone
outline polygons (`Zone(net, layer, polygon_mm, clearance_mm,
min_thickness_mm)`) and `deck_kicad_net(..., pour=…)` rasterises the pour
onto the trace layer — polygon cells minus a clearance halo around the
trace capsules and end pads, merged row-run PEC boxes; `case["pour"] =
"auto"` takes the GND zone on the net's layer straight from the board
file.  With pour present there is no closed-form reference, so the
er_eff/Z0 model bars become informational signed shifts while the
structural bars (reciprocity 0.2 dB, residuals 0.05, extra loss
−0.3…1.0 dB) still gate.  GCPW coupon (12 mm, w = 0.51 mm, gap sweep):
**PASS** — the pour lowers Z0 at every gap (−8.7 % at 0.2 mm, −3.6 % at
0.3 mm, −1.1 % at 0.5 mm, monotonic) and lowers er_eff (the air gap takes
field share out of the substrate — the classic CPW limit); the first
sweep script asserted the wrong er_eff sign and was corrected before the
summary was written.  Payoff on the real net: RXO_1 under its actual
board-wide pour (0.25 mm clearance) measures **67.4 Ω vs 76.5 Ω
pour-free (−11.8 %)** — the GCPW correction is more than twice the job23
model gap, so a 50 Ω RXO feed under this pour needs a wider trace than
the pour-free job24 redesign predicts.  Recorded warning: the margin-24
no-pour control sits at 5.85 % on the 5 % er_eff bar — group-delay
estimator margin sensitivity (~1.5 % at h/4; job23 measured 4.56 % on
identical geometry at margin 16, Z0 agrees to 0.01 Ω).  Limitations:
pour clipped to the deck domain; ~~not via-stitched to the bottom plane
inside the window~~ — **closed in job33**: `pour["fence_mm"]` (or
`case["stitch"] = True` with a pcb, which pulls the board's real GND
stitching vias via `pour_stitch_vias`, window-clipped to the coupon)
emits vertical PEC via columns tying the pour to the ground plane; a
column is emitted only when its snapped footprint lies wholly inside the
pour cells (no free-standing pegs, no shorts into the trace keepout —
skips are counted in `geom["pour"]["fence"]`).  The fence-free deck is
bit-identical to job28 (regression-checked per run).  Measured effect
(g0.20 coupon, 0.4 mm vias at y = ±1.0 mm): stitching drops Z0 a further
1.3 % (47.42 → 46.81 Ω at 2.0 mm pitch, 46.76 Ω at 1.0 mm —
pitch-converged) as the pour stops floating and becomes a true GCPW
return; RXO_1 with its one in-window stitching via reads 67.28 Ω vs
67.40 Ω floating (−0.18 %).  The small deltas are the honest physics of a
0.254 mm substrate: the pour is already capacitively locked to the plane,
so fence inductance barely shows at X-band — the machinery matters more
for thicker substrates / higher frequencies.

**N-port decomposition (job29, `structure: "kicad_net_nport"`, LIVE):**
`deck_kicad_nport` builds an N-branch junction graph (segment capsules +
junction disk, face-connected, merged row-runs) and the adapter extracts
the full N×N S-matrix from N sequential excitations — per-port feed/term
stacks and probe triads, same wave frames as the 2-port path.  Gates:
reciprocity 0.2 dB, residuals 0.05, per-column power conservation
0.90…1.02, er_eff from transmission phase slopes 5 %.  3-port Tee coupon
(8 mm arms, w = 0.51 mm): **PASS** — reciprocity 0.012 dB, residuals
0.46 %, column power 0.983–0.991, er_eff 3.5 %.  The ideal lossless Tee
reference (`tee_reference`: fed port sees the other arms in parallel;
exact in field normalisation at equal impedances, S_ii = (2−n)/n) is
recorded with measured deltas — S_ii −9.24 dB vs −9.69 dB ideal,
S_ij −3.62 dB vs −3.45 dB: the junction discontinuity is sub-dB at
10.5 GHz for w/h = 2, now quantified rather than assumed.

**In5.Cu deep stack (job30, LIVE):** `deck_kicad_net_ml` accepts In5.Cu
sections — the deep region (In4/In6 GND planes, three 0.162 mm FR4 gaps
below In3, per the TILE_8L budget in `09_STACKUP_IMPEDANCE/stackup.py`) is
added only when a chain uses it, so In2-only decks are bit-identical to
job27.  In5.Cu is the same symmetric-stripline environment as In2.Cu
(b = 0.324 mm FR4, er 4.10 — same Cohn model).  Deep vias get antipads on
every crossed GND plane (In1/In3/In4) and full plane-cage stitching
(In6..In1); shallow vias keep the job25 recipe untouched.  The deep coupon
(F.Cu–via–In5–via–F.Cu, job27 geometry) exposed a real model gap,
diagnosed honestly in three steps: the raw cascade fails (S11 residual
0.265 vs 0.15 — measured S11 null at ~8.5 GHz vs model 12.36 GHz); a
fitted series L does NOT reconcile it (L = 0 optimum — not inductive, and
the power balance matches the dielectric-loss model, so not leakage); the
via barrels are **line sections** (1.118 mm at er_fr4, Cohn Z0 at the
barrel width) and with them the coupon **PASSES** (S11 residual 0.060,
S21 0.146 dB, delay 0.4 %).  The job25 shallow-via fit (L_eff < 0.02 nH)
does not extend to 2.4× longer barrels — but no fit is needed, just the
barrel's electrical length.  job27 regression with the new cascade: S11
0.118 / S21 0.268 dB, unchanged PASS.  Real-net honesty is unchanged with
the stack off the table: PAI_1/TR_CH1 are component-interrupted chains,
CAL_2 trips the triad guard.

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
3. ~~Register `aces-tile-em` in `solvers.json`~~ — **DONE 2026-10-09**
   (`scripts/rig/register_em_solver.py` against the rig's
   `cudax-sim-runtime/simlab/solvers.json`, backup
   `solvers.json.bak-20261009-192258`; gprMax cases + Palace wr90_through
   all PASS in the validation record, other Palace paths noted as
   unimplemented).
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
| Tests | `tests/test_radar.py` (16 tests), `tests/test_em_adapter.py` (25 tests), `tests/test_kicad_import.py` (11 tests), `tests/test_via_coupon.py` (13 tests), `tests/test_staircase_import.py` (8 tests), `tests/test_kicad_net_ml.py` (7 tests), `tests/test_gcpw_pour.py` (7 tests), `tests/test_nport_tee.py` (9 tests), `tests/test_deep_stack.py` (8 tests), `tests/test_net_graph.py` (9 tests), `tests/test_em_cli.py` (13 tests), `tests/test_pour_fence.py` (6 tests) |

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
- tile_multiport + kicad_net cover the 2-port machinery and real-layout
  import (job23, RXO_1 PASS), the 50 Ω redesign is verified (job24,
  w = 0.51 mm → 52.7 Ω), via transitions are validated (job25,
  via_coupon PASS — stitched F.Cu→In2→F.Cu), and arbitrary-angle routes
  import as face-connected staircases (job26 coupon PASS — Z0/loss
  unbiased, er_eff fringe +3.1 % at h/4 → +1.7 % at h/5; probe triads
  require axis-aligned end sections).  Multi-layer net import is live
  (job27, kicad_net_ml PASS — F.Cu↔In2.Cu via chains, coupon-equivalent
  to job25 within 0.05 dB; the real PAI/TR/CAL nets raise with explicit
  reasons: component interruptions, In5.Cu stack region, stub ends).
  The F.Cu GND pour is modelled (job28 — zone polygons rasterised onto
  the trace layer, coupon PASS, RXO_1 with its real pour at 67.4 Ω) and
  N-port junctions decompose (job29, kicad_net_nport PASS — 3-port Tee
  coupon, junction discontinuity sub-dB quantified), and the In5.Cu deep
  stack region is modelled (job30 PASS — deep via barrels are cascade
  line sections; antipads on every crossed plane, full-cage stitching),
  and junction graphs are extracted from the KiCad net topology itself
  (job31 PASS — `net_graph`, coupon equivalence 0.0000 dB; single-layer
  stars only — multi-layer junction extraction remains future work).
  CAL_* nets are deferred (0.6 mm
  end sections cannot host a probe
  triad, CPL pad offsets odd — recorded finding).  The gprMax
  eigenmode-port sweep beta degrades mid-anchor (up to
  6.9 % at 9.5 GHz vs theory, observed in the job18 Palace comparison) while
  S-magnitudes are unaffected — phase-sensitive uses should re-anchor.
- SI sign-off (DDR skew, JESD lanes) stays in HyperLynx/ADS/Sigrity class tools —
  not claimed by this pipeline.
- No mutual-coupling or scan-blindness model in the fast tier (calibration
  factors absorb first-order reality once measured).
- ~~`aces em-sample` / `em-campaign` / `em-collect` CLI commands land with the
  rig image~~ — **DONE 2026-10-11 (job32)**: the trio mirrors the cfd commands;
  `em-sample` runs any adapter structure (gprMax/Palace, stackup and band
  flags, `--pcb/--net` for the kicad_* cases), `em-campaign` writes a simlab
  experiment (`physics: em`, solver `aces-tile-em`) for a calibration sweep or
  the `--validation` verification suite, and `em-collect` gathers
  measurement.json rows with the same provenance-stamping and schema checks as
  cfd-collect.  `calibrate.fit` remains callable from Python.
