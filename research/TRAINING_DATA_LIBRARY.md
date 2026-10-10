# ACES Training Data Library

Annotated catalog of training/validation data sources for the ACES computational
engineering platform. Compiled 2026-10-10. Status: research-phase catalog — verify
licenses before any commercial use (NVIDIA-license items are fine while research-only).

## How the tiers map

| Tier in ACES | What the data is for | Sections |
|---|---|---|
| Tier-0 | Calibration anchors + surrogate training (our own rig corpus) | §0, §1, §2 |
| Tier-1 | Pretraining / transfer corpora for generative geometry (XCube-class) | §5, §6 |
| Tier-2 | Physics validation & solver V&V (must pass before any claim) | §1, §2, §8 |
| Tier-3 | Manufacturing-stage + cost model grounding | §2, §7 |
| Tier-4 | Mechanics / metamaterials / structural surrogates | §4 |

Annotation legend: **Size** = public scale; **License** = as published (verify before
commercial use); **ACES use** = concrete role in this project.

---

## §0. Our own corpus (ground truth, grows every campaign)

| Data | What | Size | ACES use |
|---|---|---|---|
| `research/hx-corpus-v2.jsonl` | TPMS HX CFD calibration rows (gyroid/diamond/primitive, air) | 40 rows | Tier-0 surrogate training + registry fits |
| `research/tier0-dataset-v2/` | Voxelized 32³ cells + 7-dim conditioning | 40 samples | Tier-0 generative model dataset |
| `data/calibration/hx.json` | Fitted Nu/f factors + scatter + validity envelopes | 9 factors | Inference-time engine, refusal thresholds |
| Rig run archive (`research/rig-runs/`) | Raw simlab outputs, evidence manifests | campaigns 01–02 | Audit trail, D28 evidence schema |
| XCube stress-test grids (`research/xcube-env/`) | Car/plane samples, voxelized, measured | 4 grids | RESEARCH ONLY stress tests |

Gap note: 40 rows is thin. Next campaigns: diamond Re spread at wr 0.24/0.28
(scatter 0.143 near the 0.15 refusal line), primitive Re≈5 probe to pin its Mach
boundary (Mach/Re ≈ 0.023 measured).

---

## §1. Fluid dynamics & CFD

### 1a. Physics-ML benchmark suites (industry standard)

| Dataset | What | Size | License | ACES use |
|---|---|---|---|---|
| **The Well** (Polymathic AI / Flatiron, NeurIPS 2024) | 16 spatiotemporal physics sim datasets, unified HDF5 + PyTorch interface, 80/10/10 splits | 15 TB total (6.9 GB–5.1 TB each) | Open (per-dataset CC variants) | Tier-1/2: shear_flow, rayleigh_benard, turbulence sets = surrogate pretraining + honest baselines (FNO/U-Net checkpoints published). huggingface.co/polymathic-ai |
| **PDEBench** (FZ Jülich/Heidelberg, NeurIPS 2022) | PDE datasets: advection, Burgers, diffusion-reaction/sorption, compressible + incompressible NS, Darcy, shallow-water; baseline code | ~TB-scale, DaRUS repo | MIT code / open data | Tier-2: compressible NS = Mach-sensitivity practice before our primitive-Mach edge cases |
| **PDEArena** (Microsoft) | Shallow-water + NS velocity-form datasets, modern baselines | multi-GB | MIT | Tier-2: second opinion on operator-learning baselines |
| **AirfRANS** (ONERA/ISIR, NeurIPS 2022) | 1000 steady RANS over NACA 4/5-digit airfoils, Re 2–6M, AoA −5°..15°, OpenFOAM SA; four tasks (full/scarce/Re-extrap/AoA-extrap) | ~1000 sims, PyG + raw OpenFOAM | ODbL-1.0 | Tier-2: THE benchmark for "does our surrogate rank designs correctly" — Spearman ρ on lift is the metric to copy for HX ranking. Scarce-data task mirrors our 40-row regime |
| **DrivAerNet++** (MIT DeCoDE + TUM + BETA CAE) | 8000 car designs, k-ω SST RANS, full flow+surface fields, Cd/Cl/Cm, meshes, parametric models, point clouds to 500k | 39 TB | Open (registration) | Tier-1: largest industry-grade geometry→CFD corpus; surrogate + generative aero training; benchmarking standard (Neural Concept etc. publish on it) |
| **BubbleML** (Polymathic/Univ. Cambridge) | Multiphase boiling/bubble CFD for ML | GBs | Open | Tier-2: two-phase HX future (pool boiling = condenser/evaporator tier) |
| **CFDBench** | Multi-PDE CFD benchmark (cavity, tube, dam, cylinder) with BC/property variation | GBs | Open | Tier-2: quick surrogate smoke tests |
| **FlowBench** (2024) | Graded LES→DNS flow-physics benchmark with uncertainty metrics | TB-scale | Open | Tier-2: UQ calibration — matches our scatter-factor honesty policy |

### 1b. DNS/LES reference databases (solver V&V gold)

| Dataset | What | Size | License | ACES use |
|---|---|---|---|---|
| **Johns Hopkins Turbulence Database (JHTDB)** | Forced isotropic (1024³), channel (Re_τ 1000/5200), mixing, MHD; query via web service | ~100+ TB backend | Open (CILogon token) | Tier-2: turbulence closure sanity checks; channel data = wall-function validation for lattice pores |
| **UT Austin / Oxford channel-pipe DNS** (Lee & Moser et al.) | Channel Re_τ up to 5200, pipe up to 6000, budgets | GBs | Open | Tier-2: friction-factor anchor across Re — direct cross-check for our f factors |
| **NASA Turbulence Modeling Resource** | Validated RANS cases (flat plate, NACA 0012/4412, bumps, jets) with grids + experimental data | n/a | Public domain | Tier-2: MANDATORY solver checkout before any new fluid regime |
| **ERCOFTAC Classic Collection** | ~80 peer-reviewed experimental/DNS validation cases (jets, mixing layers, separation, bluff bodies) | n/a | Academic registration | Tier-2: the classic V&V suite; pick the 5 cases nearest HX pore flow |
| **Taylor-Green / cylinder / lid-cavity canonical sets** | Textbook convergence cases | tiny | n/a | Tier-2: already mirrored in our resolution-timing run philosophy |

### 1c. Aerodynamic shape corpora

| Dataset | What | Size | License | ACES use |
|---|---|---|---|---|
| **UIUC Airfoil Coordinates DB** | ~1600 airfoil coordinate files | small | Open | Tier-1: geometry conditioning variety |
| **DrivAer / Ahmed / Windsor open bodies** | Industry-standard bluff-body validation geometries + published wind-tunnel data | small | Open | Tier-2: external-flow validation, cost-free |
| **Train/wing CFD sets (Qiu 2025, Wang 2025)** | High-speed train + maglev (76 sims), aircraft wings (280 sims) in open papers | GBs | Paper-linked | Tier-2: supersonic/transonic edge — only if HX goes gas-turbine |

---

## §2. Heat transfer, heat exchangers & thermal properties

| Dataset | What | Size | License | ACES use |
|---|---|---|---|---|
| **Kays & London, *Compact Heat Exchangers*** | j/f vs Re for ~120 surfaces (the field's bible; digitized versions circulate) | book | © (digitize=research) | Tier-0: sanity band for our fitted factors — gyroid Nu 1.36 @ Re 28.8 must sit in a physically sane j-band |
| **NIST REFPROP** | Thermophysical properties, 147 fluids | n/a | Commercial (NIST) | Tier-0/3: property backend for cost + sim (CoolProp for open fallback) |
| **CoolProp** | Open-source fluid properties, 120+ fluids, HAProps for humid air | n/a | MIT-style | Tier-0: what the rig code already can call freely |
| **Published TPMS correlation literature** | Gyroid/diamond/primitive Nu & f fits from ~20 papers (Iyer, Dyer, Mahmoud & Elbestawi, Attarzadeh, etc.) | scatter in papers | © papers, data citable | Tier-0: EXACTLY what our registry refits against; each new campaign row outranks literature |
| **Lee & Moser channel DNS heat transfer** | Nu vs Re_τ at Pr 0.71–7 | small | Open | Tier-2: Pr-scaling check for air→water→oil media tiers |
| **HX fouling/operation field data (Kaggle + UCI sets)** | Real plant HX fouling rate, efficiency decay | MBs | CC0/various | Tier-3: degradation modeling for cost-of-ownership estimates |
| **ASHRAE / AHRI rating methodologies** | Standard rating conditions for HX comparison | docs | Standards bodies | Tier-3: how customers will compare our numbers — marketing must speak this language |

---

## §3. (reserved for future acoustic/vibration data)

---

## §4. Mechanics, metamaterials & structural surrogates

The Lejeune lab index (elejeune11.github.io, updated Feb 2026) curates 21 open
mechanics datasets — the highest-density single source for this section. Highlights:

| Dataset | What | Size | License | ACES use |
|---|---|---|---|---|
| **Mechanical MNIST** (BU, Lejeune) | 70k FEM sims of heterogeneous neo-Hookean blocks (uniaxial/biaxial/shear/confined), full-field displacement + energy | GBs | CC BY-SA 4.0 | Tier-4: MNIST-scale onboarding for structure surrogates; diffusion inverse-design papers already built on it |
| **Lattice quasi-static + impact tests** (KAB Lab) | Experimental compression/impact curves for printed lattices | MBs | Open | Tier-4: REAL measurements to validate our lattice stiffness claims — the missing experiment tier |
| **3D-printed crossed-barrel dataset** (KAB Lab, Science Advances) | Shell structures, experiment + ABAQUS | MBs | Open | Tier-4: energy-absorption design |
| **Autonomous discovery of tough structures** (Nature Comms 2024) | Cylindrical shell energy-absorption search data | MBs | Open | Tier-4: active-learning loop template |
| **Combinatorial metamaterials zero-modes** (UvA, PRL 2022) | k×k unit-cell compatibility classification | small | Open | Tier-4: classification warm-up |
| **Buckling Instability Classification (BIC)** | Stable/unstable column prediction | small | Open | Tier-4: thin-wall buckling = the failure mode of our 8–28% wall_ratio lattices |
| **PFM-Fracture benchmark** (Harvard 2025) | Phase-field crack paths, force-displacement | GBs | Open | Tier-4: failure prediction tier |
| **Comparisons for Neural Operators** (Lu group) | 16 benchmark PDE/mechanics examples w/ DeepONet+FNO code | GBs | MIT | Tier-4/2: baseline operator code we don't have to write |
| **Materials Project** | 150k+ DFT materials, elastic tensors, phase data | API | Open (account) | Tier-4: material conditioning vectors (stiffness tensors for metamaterial diffusion) |
| **JARVIS** (NIST) | 80k+ materials, ML-ready, curated leaderboard tasks | API | Public domain | Tier-4: same, with NIST curation |
| **OQMD** | 1M+ DFT entries, open API | API | Open | Tier-4: breadth for generative material conditioning |
| **MatBench** | 13 standardized materials-ML tasks + leaderboard | GBs | MIT | Tier-4: honest benchmark for material-property models |
| **NanoMine / MaterialsMine** | Polymer nanocomposite + metamaterial property DB | DB | Open | Tier-4: polymer-tier cost/material model |
| **NIST Materials Data Facility (MDF)** | General publishable materials datasets incl. AM | varies | Open | Tier-3/4: where OUR calibration data should eventually be published (DOI, citable) |

---

## §5. 3D shape & generative-geometry corpora (XCube tier)

| Dataset | What | Size | License | ACES use |
|---|---|---|---|---|
| **ShapeNet Core/Part/Sem** (Stanford/Princeton) | 51k aligned meshes, 55 categories, part labels | GBs | Research/education (ToU) | Tier-1: XCube's actual training base (chair/car/plane classes we sampled) |
| **ModelNet40/10** (Princeton) | 12k aligned CAD meshes, 40 classes | GBs | Research/education | Tier-1: classifier/encoder pretraining standard |
| **ABC Dataset** (Autodesk) | 1M CAD models with sharp-feature ground truth (B-rep provenance) | TB-scale | CC BY 4.0 (verify) | Tier-1: THE engineering-geometry corpus — closest thing to "text-to-B-Rep" training fuel |
| **Thingi10K** | 10k real user-printed meshes, tagged, manufacturability-graded | GBs | CC-BY (per-model) | Tier-1/3: printable-shape prior + manufacturability labels |
| **Objaverse 1.0 / XL** (AI2) | 800k (1.0) / 10M+ (XL) annotated 3D objects with captions | 100s TB (XL) | CC-BY core; XL inherits per-source | Tier-1: caption-conditioned pretraining (text→shape tier) |
| **PartNet** | 27k fine-grained part-segmented shapes | GBs | MIT | Tier-1: part-level structure for assembly-aware generation |
| **Fusion 360 Gallery** (Autodesk) | 8.6k sequential CAD construction sequences (sketch→extrude ops) | GBs | Autodesk research license | Tier-1: B-rep program generation (Zoo-style text-to-CAD) |
| **DeepCAD** | 178k CAD command sequences | GBs | CC BY-NC-SA (verify) | Tier-1: same, bigger; NC flag — research only |
| **Engineering Shape Benchmark (ESB)** | 867 engineering-part meshes, classification | small | Open | Tier-1: mechanical-part classifier eval |
| **MCB (Mechanical Components Benchmark)** | 68k mechanical components, hierarchical labels | GBs | Open (registration) | Tier-1: the BEST-matched public corpus for our market (gears, flanges, brackets) |
| **ABO (Amazon Berkeley Objects)** | 8k products, meshes + images + metadata | GBs | CC BY-NC 4.0 | Tier-1: product-scale realism |
| **CO3D** (Meta) | 19k object videos w/ 3D reconstructions | TBs | CC BY-NC (verify) | Tier-1: image→3D conditioning tier |
| **Stanford 3D Scanning Repo / AIM@SHAPE** | Bunny, dragon, classic test meshes | small | Open | Tier-2: geometry-kernel unit tests (watertight, boolean, voxelize) |

---

## §6. Voxel / SDF / implicit-representation resources

| Resource | What | License | ACES use |
|---|---|---|---|
| **3D-R2N2 / OccNet ShapeNet voxel packs** | Pre-voxelized 32³/64³ ShapeNet + occupancy samples | research | Tier-1: exact 32³ format match with our tier-0 dataset builder |
| **OpenVDB sample volumes + NanoVDB** | Sparse-voxel reference volumes, GPU port | MPL-2.0 | Tier-1/2: our sparse-grid substrate; test assets included |
| **SDF dataset tools (mesh-to-SDF, DeepSDF splits)** | Standard ShapeNet SDF preprocessing | MIT | Tier-1: SDF conditioning channel for the diffusion tier |
| **NVIDIA XCube** | Hierarchical latent diffusion to 1024³ sparse voxels | NVIDIA license (research OK now) | Tier-1: current stress-test engine (car 10.4 mm neck / plane 32.6 mm neck measured) |
| **TSDF fusion test data (7-Scenes, Replica)** | RGB-D → TSDF reference reconstructions | research | Tier-2: voxelizer correctness cross-check |

---

## §7. Manufacturing stage & cost-model grounding

| Dataset | What | Size | License | ACES use |
|---|---|---|---|---|
| **NIST AM Bench (2018/2022/2025 rounds)** | Rigorous LPBF/polymer AM benchmark measurements: powder, in-situ thermography, melt-pool radiography, distortion, residual stress, microstructure (IN718/IN625); 138 blind modeling submissions (2022) | TBs, permanently archived | Open (NIST, public domain-ish) | Tier-3: THE ground truth for "can this geometry actually be printed" + process-window priors for the manufacturing suggester |
| **AM Bench challenge submissions** | 138 modeling team's predictions vs measurement | via NIST | Open | Tier-3: error magnitudes the field accepts — calibrates our tolerance claims |
| **ExaAM / ORNL AM process data** | Exascale AM sim + in-situ datasets | varies | Open | Tier-3: process-sim validation |
| **DesignSafe-CI Data Depot** | Natural-hazard + structural experiment archive | TBs | Open | Tier-3/4: steel-fracture and fatigue experimental sets (via Lejeune index #8/#9) |
| **Kaggle/UCI manufacturing sets** (CNC tool wear, milling, PHM) | Sensor + outcome data | MBs | CC0/various | Tier-3: conventional-route (CNC) cost/time model priors |
| **aPriori / Papertoilet public cost studies** | Published part-cost breakdowns (not datasets) | docs | © | Tier-3: shape of the cost model (material/energy/machine-hour/labor splits) to calibrate our quotes module against |
| **Our quotes + invoices (step 44 workflow)** | Real vendor quotes per process/material/volume | grows | ours | Tier-3: the ONLY cost data that ultimately matters; public data just seeds the model shape |

Cost-model note: public AM cost *data* barely exists — the plan stays: seed model
structure from published breakdowns, calibrate from real quotes (workflow §43–45).

---

## §8. V&V frameworks & standards (not datasets, but required)

| Resource | What | ACES use |
|---|---|---|
| **ASME V&V 20 / V&V 10** | CFD & solid-mech verification/validation standards | Tier-2: how to phrase every validation claim (we already do: Mach ceiling, scatter refusal) |
| **NAFEMS benchmarks** | Industry FEA/CFD benchmark cases | Tier-2: structural solver checkout |
| **AIAA/NASA CFD verification cases** | Method-of-manufactured-solutions packs | Tier-2: order-of-accuracy proof for new solvers |
| **ISO/ASTM 52900-series (AM)** | AM terminology + test-artifact standards | Tier-3: which artifacts to print for our own machine calibration |
| **ISO 27001:2022 Annex A** | Evidence collection discipline (loaded skill) | Cross-cutting: rig evidence manifests already follow this pattern (D28) |

---

## §9. Prioritized shortlist — what to actually pull next

Ordered by value-per-byte for the current tier-0 → tier-1 path:

1. **AirfRANS** (~GBs) — adopt its Spearman-ρ ranking metric for our HX surrogate eval. Solves "40 rows — is the model useful?" with a defensible metric.
2. **The Well: shear_flow + rayleigh_benard** (~70 GB) — pretraining physics prior for the surrogate tier.
3. **MCB (Mechanical Components Benchmark)** — closest public corpus to our market segments; train the shape encoder here before XCube-class models.
4. **KAB lattice test data** (MBs) — first real experimental anchor for lattice stiffness; pairs with our CFD-only calibration.
5. **NIST AM Bench 2022 IN718 set** — grounds the manufacturing suggester (process windows, distortion magnitudes) before the first real print quote.
6. **JHTDB channel Re_τ 1000–5200** — friction-factor cross-check vs our fitted 4.09×(Re/28.8)^0.344 gyroid factor.
7. **ABC dataset subset** (engineering parts) — tier-1 pretraining when the generative tier spins up.

Everything above the line is open and downloadable on the rig's schedule without
license risk at research stage.

## §10. Pull status (2026-10-10)

Shortlist executed against the rig's `/mnt/nvme2/data-library/` — **301 files,
~494 GB queued** in a resumable background pull (`research/data-pull/` has the
driver + manifests; see `research/data-pull/README.md` for live status):

1. AirfRANS — **queued** (preprocessed 10 GB + raw OpenFOAM 71 GB)
2. The Well shear_flow + rayleigh_benard — **queued** (full valid+test splits;
   L-shaped train subsets ~230 + ~168 GB; full trains were 376 + 294 GB — over
   the disk budget, on-demand per-combo later)
3. MCB — **queued** (A + B via working direct Box links, ~5 GB)
4. KAB lattice tests — **queued** (Drive folder was 401; got the *entire* 5-dataset
   KABlab collection from open.bu.edu instead, incl. quasi-static + impact raw test
   data, ~4.6 GB, plus both code repos)
5. NIST AM Bench IN718 — **queued** (challenge description + residual stress +
   thermography derived data; raw camera files up to 119 GB each excluded on
   disk budget, documented for on-demand fetch)
6. JHTDB — **blocked**: needs a free personal CILogon token from
   turbulence.pha.jhu.edu before scripted queries can run
7. ABC subset — **queued** (8 parquet shards of the processed ABC HF mirror, ~6 GB;
   full chunks are 50–100 GB each — for when tier-1 training actually starts)
