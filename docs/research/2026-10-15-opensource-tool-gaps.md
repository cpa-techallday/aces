# Open-Source Tool & Library Gap Analysis for ACES

**Date:** 2026-10-15
**Scope:** Gap analysis of the ACES autonomous engineering optimisation platform (structures / CFD / EM / ML surrogates / geometry) against what best-in-class commercial (nTop, Altair, Ansys, Siemens HEEDS) and open-source organisations actually deploy.
**Hardware baseline:** NVIDIA RTX PRO 4000 Blackwell 24 GB + rig GPUs, FP64-validated, Dockerised toolchain.

---

## Executive summary

ACES already covers the "deep niche" solvers well (PyFR flux reconstruction, TPMS lattice-Boltzmann, gprMax, Palace, GPU voxel SIMP). The genuine gaps are not exotic solvers but the **boring, production-critical middle layer**:

1. **General-purpose RANS CFD with adjoints** (external aero, intake ducts, cooling passages): no OpenFOAM or SU2 in the stack. This is the single largest gap.
2. **Optimisation orchestration beyond Optuna**: no OpenMDAO/Dakota-class MDO framework, no gradient-based adjoint pipeline connecting disciplines.
3. **Implicit modelling kernel for geometry reconstruction**: topopt density fields → manufacturable B-rep/STEP is the industry pain point (nTop's core value). No libfive/Fidget/OpenVDB + OpenCascade sewing pipeline exists yet.
4. **Stress-constrained, multi-load-case topopt** and level-set/BESO alternatives to voxel SIMP.
5. **System-level thermal** (heat rejection loops, electronics cooling) — Elmer/OpenFOAM CHT or Modelica-class tools absent.
6. **Compressible reacting flow for rocket motors**: PyFR is high-order LES-class but there is no production RANS/LES reacting-flow path (PeleC/Cantera/RocketCEA).

---

## 1. Topology optimisation — is voxel SIMP enough?

**Verdict: necessary but not sufficient.** Voxel SIMP with GPU matrix-free PCG is an excellent workhorse (this is essentially what Altair OptiStruct's free-size/density path and most research codes do), but best-in-class pipelines add four things ACES lacks:

### 1a. Level-set / BESO methods
Level-set topopt produces **smooth, directly manufacturable boundaries with no grey intermediate densities** — the main reason nTop-style implicit pipelines converge cleanly to CAD (source: M2DO lab, https://m2do.ucsd.edu/software). Open-source options:

| Tool | Notes | Licence | GPU | Docker | Maturity | Priority |
|---|---|---|---|---|---|---|
| **OpenLSTO** (M2DO lab, UCSD/Cardiff) | C++ level-set topopt, 2D+3D, compliance **and stress minimisation**, hole nucleation (source: M2DOLab/OpenLSTO v1.0 release notes, https://github.com/M2DOLab/OpenLSTO) | Apache 2.0 | No (serial C++; parallel fork **ParaLeSTO** on GitLab) | Easy, no exotic deps | Research-grade, low maintenance cadence | **Nice-to-have** (method diversity, smooth-boundary cross-checks) |
| **BESO / soft-kill** | No dominant standalone OSS package; easily implemented on top of the existing voxel FEA kernel (~200 lines). | n/a | Reuse GPU PCG | n/a | Trivial | **Nice-to-have** |

### 1b. Stress-constrained and multi-load-case topopt
Commercial differentiator (OptiStruct/nTop both optimise with von-Mises stress constraints, fatigue, and multiple load cases). OpenLSTO v1.0 already does stress minimisation in 3D (source: https://github.com/M2DOLab/OpenLSTO/releases). For density-based stress-constrained work the open literature standard is qp-relaxation + P-norm aggregation; the pragmatic route is to **extend the existing "gendesign" SIMP loop** (aggregated stress constraint, multi-load-case objective = weighted/max compliance) rather than adopt a new code. Supporting libraries:
- **NLopt** (includes MMA/GCMMA — the Svanberg optimisers that are the de-facto topopt standard) — LGPL/MIT dual, C with Python bindings, Docker-trivial (source: https://github.com/stevengj/nlopt).
- **IPOPT** for large-scale NLP with many constraints — EPL 1.0 (source: https://coin-or.github.io/Ipopt/).

### 1c. Adjoint/PDE-constrained framework topopt
- **dolfin-adjoint + FEniCS**: automatic adjoint derivation for PDE-constrained optimisation; the reference research stack for topopt with arbitrary physics (thermal compliance, fluid topopt). Legacy FEniCS is maintenance-mode; the maintained path is **FEniCSx** + **dolfin-adjoint (2023+ rewrite)** or **Firedrake + pyadjoint**. LGPL-3 (source: https://www.dolfin-adjoint.org/).
- **PolyTop / PolyStress / ToPy**: MATLAB/Python teaching-grade codes — useful as *verification benchmarks*, not production. **Skip as dependencies; use as test references.**

### 1d. Lattice / TPMS infill generation (the nTop gap)
nTop/Altair class tools win on **implicit geometry**: conformal lattices, graded TPMS infill, shell-and-fill optimisation. ACES has TPMS *physics* (LBM) but no TPMS *geometry generator*:

| Tool | Notes | Licence | GPU | Docker | Maturity | Priority |
|---|---|---|---|---|---|---|
| **libfive** | f-rep implicit kernel, watertight feature-preserving meshing, C API + Python bindings; the historical basis of early nTop (source: https://github.com/libfive/libfive; Blake Courter via upfrontezine, https://upfrontezine.substack.com/p/readers-write-me-letters-368) | MPL-2.0 | No (CPU JIT) | Easy | Stable, slow-moving | **Must-have** |
| **Fidget** | Keeter's successor to libfive — Rust, hand-written JIT, Manifold Dual Contouring mesher, much faster evaluation (source: https://github.com/mkeeter/fidget via awesome-cad, https://github.com/mlightcad/awesome-cad) | MIT | No | Easy (cargo) | Young but very active | **Must-have** (preferred over libfive for new work) |
| **OpenVDB** | Sparse voxel/level-set infrastructure (nanoVDB GPU port); the industry backbone for volumetric geometry (source: https://www.openvdb.org/) | MPL-2.0 | Yes (NanoVDB CUDA) | Medium | Production (DreamWorks) | **Must-have** |
| **PicoGK** (LEAP 71) | Compact implicit kernel explicitly built for computational engineering (rocket nozzles, heat exchangers) — closest OSS-adjacent analogue to what ACES does; note: source-available, not OSI licence — evaluate licence before embedding (source: https://picogk.org/) | Proprietary (free) | No | Easy | Production at LEAP 71 | Evaluate; **nice-to-have** |

**Section 1 priorities:** (1) multi-load-case + aggregated stress constraints in existing SIMP (must), (2) Fidget/OpenVDB implicit geometry kernel (must), (3) NLopt/MMA optimiser bindings (must), (4) OpenLSTO for level-set cross-validation (nice), (5) dolfin-adjoint/Firedrake for exotic physics topopt (nice).

---

## 2. CFD gaps

PyFR (high-order FR, CUDA) is superb for scale-resolving LES/DNS-class work but is **not** a general engineering RANS tool: no wall-function RANS workflow, no general unstructured-mesh robustness for dirty CAD, no adjoint for shape optimisation. Best-in-class orgs (and the entire automotive/aerospace open-source community) fill this with:

### 2a. General RANS / external aero / duct flow

| Tool | What it adds | Licence | GPU | Docker | Maturity | Priority |
|---|---|---|---|---|---|---|
| **SU2** | Euler/RANS (SST, SA), incompressible & compressible, CHT, **discrete adjoint via CoDiPack AD with consistent turbulence treatment** — the reference open-source aerodynamic shape-optimisation stack, with built-in FFD mesh morphing (source: SU2 docs, https://su2code.github.io/docs/Software-Components/; arXiv 2507.23443) | LGPL-2.1 | **No production GPU port** (CPU/MPI only as of 2025-26) | Official Docker images | Very mature (Stanford + TU-KL etc.) | **Must-have** |
| **OpenFOAM** (Foundation or ESI) | The general-purpose workhorse: simpleFoam/pimpleFoam, wall functions, multiphase, overset. | GPL-3.0 | Partial: linear-solver offload via **PETSc4FOAM/AmgX** (up to ~7x linear-solver, 2–3x end-to-end) or **OGL/Ginkgo** (CUDA/HIP/SYCL, documented limitations: basic cyclic BCs only, no AMI) — full GPU ports (zeptoFOAM, NeoN) still experimental (source: arXiv 2507.18268; hivenet.com/post/openfoam-gpu-state-of-play) | Official images | Most mature OSS CFD | **Must-have** |
| **RapidCFD / SpeedIT forks** | Full-GPU OpenFOAM forks | GPL | Yes | Possible | **Abandoned/stale** — do not adopt (source: arXiv 2507.18268) | ❌ |

**Recommendation:** SU2 for anything adjoint/shape-optimisation-driven (intake ducts, aero surfaces); OpenFOAM (CPU MPI + Ginkgo/AmgX GPU offload for the linear solve) for dirty-geometry multiphase/multiphysics. On the 24 GB Blackwell rig, expect the CPU+GPU-offload hybrid to be the realistic 2026 pattern — end-to-end GPU OpenFOAM is not production-proven (source: TIB benchmark abstract, https://oa.tib.eu/renate/items/823e9cda-34cd-47d7-b5c3-d75ae2408c41).

### 2b. Conjugate heat transfer (CHT)
- **OpenFOAM chtMultiRegionFoam** — industry-standard OSS CHT (solid/fluid region coupling). GPL-3. CPU; pairs with GPU linear-solver offload. **Must-have** for intake/cooling jackets.
- **SU2** also has native CHT with adjoint support (source: SU2 docs above). **Must-have** (already covered by 2a).
- **preCICE** coupling if ACES wants to couple CalculiX solids to OpenFOAM fluids (see §4).

### 2c. Compressible reacting flow (rocket motors)
PyFR can run compressible Euler/NS but has no finite-rate chemistry. Options:

| Tool | Notes | Licence | GPU | Docker | Maturity | Priority |
|---|---|---|---|---|---|---|
| **PeleC + PelePhysics** (NREL/LBNL/SNL) | Compressible reacting flow, block-structured AMR, embedded boundaries, **proven at exascale on NVIDIA/AMD/Intel GPUs** (160B-cell run on Summit; weak-scaled to all of Frontier) (source: Henry de Frahan et al., IJHPCA 2023, https://doi.org/10.1177/10943420221121151; ORNL, https://www.ornl.gov/publication/pele-simulation-suite-reacting-flows-exascale) | BSD-3 | **Yes, best-in-class** | Medium (AMReX/SUNDIALS deps) | Production (US DOE) | **Must-have** for motor gas dynamics |
| **Cantera** | Chemical kinetics / equilibrium / reactor networks; also does chamber-equilibrium calculations (NASA CEA-class) via `equilibrate()` | BSD-3 | No | Easy (conda) | Very mature | **Must-have** (cheap, huge value for propellant/chamber states) |
| **RocketCEA** | Rocket engine design-point analysis wrapping CEA (Isp, c*, chamber sizing) | GPL — verify before redistribution | No | Easy | Mature hobbyist/professional | **Must-have** for the motor design loop front-end |
| **OpenFOAM reactingFoam / hyStrath** | Reacting RANS/DSMC-hypersonics | GPL | As OpenFOAM | Medium | Mature | Nice-to-have fallback |

---

## 3. Thermal — system-level heat rejection & electronics cooling

ACES has no system-thermal capability at all. Gaps:

| Tool | Scope | Licence | GPU | Docker | Maturity | Priority |
|---|---|---|---|---|---|---|
| **Elmer FEM** | Open multiphysics FEM: heat transfer, fluid flow, electromagnetics — widely used for **electronics cooling** and CHT (source: https://www.elmerfem.org/) | GPL/LGPL | No (MPI CPU) | Community images | Mature (CSC Finland) | **Must-have** (electronics cooling niche) |
| **OpenFOAM CHT** (§2b) | Detailed 3D CHT | GPL | Partial | Yes | Mature | Must-have (shared with CFD) |
| **OpenModelica / Modelica Buildings + CoolProp** | 0D/1D system-level heat-rejection loops (pumps, radiators, cold plates) — the "HEEDS/Amesim-class" lumped model layer; **CoolProp** (MIT) for working-fluid properties (source: https://coolprop.org/; https://openmodelica.org/) | OSMC-PL / MIT | No | Easy | Mature | **Must-have** for heat-rejection sizing |
| **EnergyPlus** | If chassis/vehicle cabin or facility heat rejection matters | BSD-3 | No | Official images | Very mature | Nice-to-have |

---

## 4. Multiphysics coupling & optimisation orchestration

Optuna covers black-box hyperparameter/DOE loops. What's missing is the **gradient-based MDO layer** and the **coupling layer** — the open-source analogues of HEEDS/ModelCenter:

| Tool | Role | Licence | GPU | Docker | Maturity | Priority |
|---|---|---|---|---|---|---|
| **OpenMDAO** (NASA) | MDO framework with **automatic total-derivative assembly** across coupled components, MPI, Newton-type coupled solvers; proven path for wrapping external codes (including topopt — OpenLSTO has been integrated into OpenMDAO via Cython) (source: Gray et al., https://openmdao.org/pubs/chung_top_opt_openmdao_2019_smo.pdf; NASA NTRS 20190026794) | Apache-2.0 | n/a (orchestrator) | Easy (pip) | Very mature, NASA-maintained | **Must-have** |
| **Dakota** (Sandia) | UQ, sensitivity analysis, surrogate-based and gradient-free optimisation at scale; stronger than Optuna for UQ/reliability | GPL | No | Easy | Very mature | **Must-have** for UQ; otherwise nice |
| **pymoo** | Multi-objective evolutionary (NSGA-II/III, MOEA/D) — the reference OSS multi-objective library | Apache-2.0 | No | Easy | Mature | **Must-have** (Pareto fronts for chassis/motor trade-offs) |
| **Ax / BoTorch** (Meta) | Bayesian optimisation with modern GP surrogates, multi-fidelity, constrained BO — upgrade path over the current tabular GP | MIT | Yes (PyTorch) | Easy | Mature | **Must-have** |
| **GEMSEO** (IRT Saint-Exupéry) | Alternative MDO framework with stronger surrogate/DOE catalogue; benchmarks show OpenMDAO more efficient for gradient-based MDF/IDF architectures (source: ISAE-Supaéro comparative study, https://openscience.isae-supaero.fr/ ...DigitalCollectionAttachmentDownloadHandler.ashx?parentDocumentId=14833) | LGPL-3 | No | Easy | Mature | Nice-to-have |
| **preCICE** | **The** OSS multiphysics coupling library (peer-to-peer coupling of CalculiX, OpenFOAM, SU2, FEniCS with quasi-Newton acceleration) — enables ACES to couple its existing ccx FEA to OpenFOAM/SU2 fluids without writing a monolithic solver (source: https://precice.org/) | LGPL-3 | n/a | Official images | Mature | **Must-have** for fluid-structure interaction |
| **FMI/FMPy** | Co-simulation standard for system models | BSD | No | Easy | Mature | Nice-to-have |

### Geometry parametrisation & mesh morphing
- **CadQuery / build123d** (OpenCascade-based parametric B-rep in Python) — Apache-2.0; Docker-easy (source: https://github.com/CadQuery/cadquery). **Must-have** for parametric seed geometry.
- **pythonocc-core** — full OpenCascade bindings, LGPL-2.1 with exception (source: https://github.com/tpaviot/pythonocc). **Must-have** (shared kernel with CadQuery).
- **PyGeM** (SISSA mathLab): FFD, RBF, IDW mesh morphing, integrates with OpenFOAM/SU2 meshes — MIT (source: https://github.com/mathLab/PyGeM). **Must-have** for shape-optimisation loops. (SU2's built-in FFD partially covers this — source: https://www.simtool.com/article/su2-discrete-adjoint-solver-gradient-based-aerodynamic-shape-optimization/.)
- **OpenFOAM mesh-motion / SU2_DEF**: free alternatives already inside the recommended CFD stack.

---

## 5. Geometry healing / reconstruction: topopt output → CAD

This is where commercial tools (nTop implicit kernel, Altair Inspire's PolyNURBS, Ansys SpaceClaim skinning) still outclass open source — **and it is ACES's most strategically important gap**, because the platform already emits density fields and voxel skins. The realistic open-source pipeline is three stages:

1. **Density field → implicit function:** represent the topopt result as a level-set/SDF (marching-cubes → Laplacian/TAubin smoothing → SDF refit). Tooling: **OpenVDB/NanoVDB** (GPU level-set ops, smoothing, morphology), **scikit-image** marching cubes, **trimesh** repairs. (MPL-2.0 / BSD / MIT.)
2. **Implicit → watertight mesh:** **Fidget** (manifold dual contouring, feature-preserving) or **libfive** — both produce watertight, manifold, hierarchical meshes, which is exactly the property nTop built its business on (source: https://github.com/libfive/libfive).
3. **Mesh → B-rep/STEP:** the genuinely hard step. Options, in order of pragmatism:
   - **OpenCascade sewing/healing** (`BRepBuilderAPI_Sewing`, `ShapeFix_*` classes) via pythonocc — robust for near-manifold STL→shell→solid, STEP export (LGPL-2.1+exc).
   - **Gmsh's discrete-geometry + reparametrisation pipeline** (already in ACES): STL → curvature-based surface patch segmentation → B-rep → STEP. Partially present today; needs to be promoted from "FEA check" to a dedicated reconstruction stage.
   - **Segment-and-fit:** detect planar/cylindrical/NURBS patches on the skin and fit analytic surfaces (no turnkey OSS package; build on OpenCascade + point-cloud segmentation from **Open3D**, MIT). This is what Inspire's PolyNURBS does commercially.
   - Skip pure "STL→STEP converter" claims: robust automatic conversion of organic shapes to clean B-rep is an unsolved problem in OSS; expect a semi-automatic pipeline (source: community consensus, e.g. https://news.ycombinator.com/item?id=32489862).

| Component | Licence | GPU | Docker | Priority |
|---|---|---|---|---|
| OpenVDB/NanoVDB | MPL-2.0 | Yes | Medium | **Must-have** |
| Fidget (or libfive) | MIT / MPL-2.0 | No | Easy | **Must-have** |
| OpenCascade (pythonocc) sewing + STEP | LGPL-2.1+exc | No | Easy | **Must-have** |
| Open3D (segmentation support) | MIT | Optional | Easy | Nice-to-have |

---

## 6. Consolidated gap table and roadmap

| # | Gap | Recommended addition | Licence | GPU | Docker-ability | Maturity | Priority |
|---|---|---|---|---|---|---|---|
| 1 | General RANS CFD + adjoint | **SU2** (adjoint/optimisation) + **OpenFOAM** (general/multiphase) | LGPL/GPL | Partial (Ginkgo/AmgX offload) | Official images | Very mature | **Must-have** |
| 2 | Reacting compressible flow (motors) | **PeleC** + **Cantera** + **RocketCEA** | BSD / BSD / GPL | PeleC: full GPU | Medium | Production (DOE) / mature | **Must-have** |
| 3 | MDO orchestration | **OpenMDAO** (+ pymoo, Ax/BoTorch, Dakota for UQ) | Apache/MIT/GPL | BoTorch yes | Easy (pip) | Very mature | **Must-have** |
| 4 | Multiphysics coupling | **preCICE** (ccx↔OpenFOAM/SU2) | LGPL-3 | n/a | Official | Mature | **Must-have** |
| 5 | Implicit geometry kernel | **Fidget**/**libfive** + **OpenVDB/NanoVDB** | MIT/MPL-2.0 | OpenVDB yes | Easy–medium | Stable / production | **Must-have** |
| 6 | Topopt → CAD reconstruction | OpenCascade sewing + gmsh reparam + Open3D | LGPL/GPL/MIT | No | Easy | Mature components, bespoke pipeline | **Must-have** |
| 7 | Stress / multi-load-case topopt | Extend "gendesign" + **NLopt (MMA)** / IPOPT | LGPL/EPL | Reuse GPU PCG | Easy | Mature | **Must-have** |
| 8 | System thermal | **OpenModelica + CoolProp** (+ Elmer for electronics cooling) | OSMC-PL/MIT/GPL | No | Easy | Mature | **Must-have** (heat rejection); Elmer nice-to-have if CHT covered by OpenFOAM |
| 9 | Parametric CAD / morphing | **CadQuery** + **PyGeM** | Apache/MIT | No | Easy | Mature | **Must-have** |
| 10 | Level-set topopt diversity | **OpenLSTO** / ParaLeSTO; dolfin-adjoint or Firedrake for exotic physics | Apache/LGPL | No | Easy | Research-grade | Nice-to-have |
| 11 | Multi-fidelity BO upgrade | Ax/BoTorch multi-fidelity + PhysicsNeMo FNO coupling | MIT | Yes | Easy | Mature | Nice-to-have |
| 12 | Facility/cabin thermal | EnergyPlus | BSD-3 | No | Official | Very mature | Nice-to-have |

### Suggested sequencing
1. **Sprint 1 (chassis/duct unblock):** OpenFOAM + SU2 Docker images, NLopt bindings, multi-load-case stress constraints in gendesign.
2. **Sprint 2 (geometry):** OpenVDB + Fidget implicit kernel, OpenCascade sewing pipeline, CadQuery parametrisation.
3. **Sprint 3 (orchestration):** OpenMDAO as the top-level driver; pymoo for Pareto studies; Ax/BoTorch replacing tabular GP; preCICE for ccx↔CFD FSI.
4. **Sprint 4 (propulsion/thermal):** Cantera + RocketCEA front-end, PeleC for chamber/nozzle reacting flow, OpenModelica+CoolProp heat-rejection loop.

### Non-recommendations (checked and rejected)
- **RapidCFD / SpeedIT / PARALUTION**: full-GPU OpenFOAM forks — abandoned, pinned to obsolete OpenFOAM versions (source: arXiv 2507.18268).
- **ToPy / PolyTop as production dependencies**: teaching codes; use only as benchmark references.
- **End-to-end GPU OpenFOAM (zeptoFOAM, NeoN)**: promising but pre-production as of 2025–26; revisit in 12 months (source: hivenet.com/post/openfoam-gpu-state-of-play).

---

## Key sources
- M2DO lab / OpenLSTO: https://m2do.ucsd.edu/software, https://github.com/M2DOLab/OpenLSTO
- SU2: https://su2code.github.io/docs/Software-Components/, https://arxiv.org/html/2507.23443
- OpenFOAM GPU state: https://arxiv.org/html/2507.18268v1, https://www.hivenet.com/post/openfoam-gpu-state-of-play, https://oa.tib.eu/renate/items/823e9cda-34cd-47d7-b5c3-d75ae2408c41
- PeleC: https://doi.org/10.1177/10943420221121151, https://www.ornl.gov/publication/pele-simulation-suite-reacting-flows-exascale
- Implicit kernels: https://github.com/libfive/libfive, https://github.com/mlightcad/awesome-cad, https://upfrontezine.substack.com/p/readers-write-me-letters-368
- MDO frameworks: https://openmdao.org/pubs/chung_top_opt_openmdao_2019_smo.pdf, https://ntrs.nasa.gov/api/citations/20190026794/downloads/20190026794.pdf, https://openscience.isae-supaero.fr/ (OpenMDAO/GEMSEO/CoSApp study)
