# Tier 0: generative proposers

Tier 0 sits *below* the fast tier: it suggests geometry. It never decides anything.

```
Tier 0  proposer (parametric today, latent diffusion later)   <- aces propose
Tier 1  correlations + measured manufacturability             <- aces.domains.hx
Tier 2  surrogates (simlab-ml)
Tier 3  registered verified solvers (simlab)                  <- signs off (D5)
```

## The contract (`aces.propose.contract`)

- A **Proposer** declares `name`, `licence`, `domain`, and `propose(req, n, seed)`.
- Every **Proposal** carries complete parameters: the domain's measurement layer must be able
  to build and measure the geometry with no further input. Unmeasurable proposals are a
  contract violation.
- **Licence gate.** `register()` refuses any proposer whose licence is not on the allowlist
  (commercial + closed-source + defence, per `docs/LICENCES.md`).
- **Research quarantine (D29).** Non-commercial research licences — currently `nvidia-xcube` and
  `nvidia-scube` (Nvidia Source Code License; verified 2026-10-08) — live in `RESEARCH_ONLY`.
  They register only with `research=True` (CLI: `--research`); without the flag the gate still
  refuses, even if one is registered. Every proposal from the research lane is stamped
  `RESEARCH ONLY` in its notes and the output JSON. Quarantined code and weights never enter a
  product image, a customer deliverable or a commercial sprint, and are replaced by the
  clean-room proposer before Gate 2.
- Deterministic per seed: proposals are reproducible evidence, not vibes.

## Reference proposer: `tpms-parametric`

`aces propose --method tpms-parametric --n 8 --seed 1 --out DIR` samples the calibrated TPMS
space (kind, wall ratio, cell size) and measures every proposal on the domain's own definitions
(solid fraction, wall min, necks, isolated pockets). Samples outside the calibrated wall-ratio
range 0.08–0.16 are allowed but flagged; `primitive` carries a "no calibrated factors" warning
until its Mach-limit problem is solved (the air campaign's primitive cases all exceeded Mach 0.15).

This proposer exists to exercise the contract end to end before any learned model plugs in.
It is also genuinely useful: a seeded, measured idea-list for design sprints.

## The corpus (`aces corpus`)

`aces corpus measurements.json --out corpus.jsonl` turns verified measurement rows (from
`aces cfd-collect`) into training rows with provenance (`source` run id, `internal-verified`
licence tag, thermal boundary). Rows without a source never load — the gate is enforced by
`aces.domains.hx.calibrate` itself (D18). First corpus: 17 rows from `aces-cal-air-01`
(gyroid + diamond, Re 9.7–100, air 320 K), committed at `data/corpus/` is *not* done — corpora
are regenerated from measurements, so the JSONL is a build artifact, not a repo file.

## Voxel measurement (`aces voxel-measure`)

Proposers that emit voxel grids (XCube in the research lane today, the clean-room learned
proposer later) are measured by `aces.propose.voxel_measure` on the same primitives as the
analytic TPMS path: solid fraction, local wall thickness (Hildebrand–Rüegsegger, EDT-level
quantised), per-axis percolation neck, and enclosed-void fraction (AM powder traps). A
quarantined proposer name requires `--research` and stamps the measurement RESEARCH ONLY.

Closed loop verified 2026-10-09: XCube chair checkpoint → `fine_xyz` voxel centres →
dense 220×467×178 grid → `aces voxel-measure --research` → solid 0.0175, wall min 0.40 mm,
neck min 4.22 mm, isolated void 0.0 (at a demonstration 0.2 mm pitch; ShapeNet geometry
carries no physical scale). The extraction script lives in the research lane
(`~/research/extract_voxel.py` on the rig; mirror in the workspace `research/xcube-env/`).

## The learned proposer (next)

The diffusion proposer registers against the same contract:

1. Architecture: hierarchical latent diffusion over sparse voxel cells (XCube-class), but
   trained on **our own verified corpus** under a clean-room implementation. While the work is
   R&D-only, NVIDIA's own XCube may run in the research quarantine (D29) as a reference
   generator — never in a product or deliverable, and replaced by the clean-room proposer
   before Gate 2. Permissive substrates: PyTorch (BSD-style) on the rig; fVDB/OpenVDB
   (MPL-2.0 family) for the sparse hierarchy.
   Status 2026-10-09: XCube is operational on the rig as image `research-xcube:0.1`
   (sm_80 build; chair checkpoint verified end-to-end, 2 meshes sampled). Build recipe
   and runbook live in the research lane (`~/research/` on the rig; mirror in the
   workspace under `research/xcube-env/`).
2. Conditioning: target stiffness/thermal tensors and the spec envelope, not text prompts.
   Text-to-3D is a demo feature; tensor-to-cell is the product.
3. Output decodes to the level-set grid → measured by `aces.propose.voxel_measure` →
   DfAM checks → NSGA-II → Tier 3 verification. No path around the ladder.
4. Training happens on the rig as a container image (no PyPI on the rig); the corpus grows with
   every calibrated campaign and coupon test.

### Dataset scaffold (done 2026-10-09)

`aces propose-dataset corpus.jsonl --resolution 32 --out DIR` builds the training set:
each corpus row is re-rasterised into its TPMS unit cell on the domain's own level set
(`aces.propose.learned.rasterize_cell` — the training grid *is* the geometry the factors were
measured on), paired with a conditioning vector (one-hot kind + wall ratio, cell size,
log10 Reynolds, temperature). The manifest carries per-row source and licence — the D18
provenance gate applies to training data too, and sourceless rows are refused. First build:
17 rows at 32³ from `hx-corpus-v1.jsonl`. Model training starts when the corpus has the
spread to support it; the scaffold exists so that day is a config change, not a project.

### Reference floor model + evaluation harness (done 2026-10-10, D32)

`aces.propose.tier0` is the numpy-only clean-room floor any future learned proposer must
beat: a PCA-conditional model (closed-form fit, deterministic, no external code or weights)
trained by `aces tier0-train` and judged by `aces tier0-eval`. The harness — held-out BCE,
Spearman ranking by requested wall_ratio, and sample validity on the shared voxel_measure
layer — is the part that outlives any model; ranking honestly excludes rows outside the
training anchor envelope rather than pretending the model extrapolates. First real run
(`research/tier0-run-v1`, 40-row corpus at 32³): ranking ρ 0.99 gyroid / 0.99 diamond /
0.95 primitive, solid-fraction conditioning error mean 0.004, and it already surfaces
printability signals (neck-blocked and enclosed-void shares at thick walls).

## What Tier 0 must never do

- Emit geometry to a customer without Tier 3 sign-off (D5).
- Load weights or code under a non-allowlisted licence — the gate refuses, and the research
  quarantine (D29) is the only exception, explicitly flagged and stamped.
- Propose parameters the measurement layer cannot measure.
