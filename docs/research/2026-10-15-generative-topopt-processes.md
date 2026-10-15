# Generative Design & Topology Optimisation as an End-to-End Engineering Process

**Research date:** 2026-10-15
**Purpose:** Define the "structural strip-first" stage of the ACES autonomous engineering playbooks — i.e., how the world's best engineering organisations actually run generative design (GD) and topology optimisation (TO) from problem statement to verified, manufacturable part.
**Scope:** Process models of nTop (nTopology), Autodesk Fusion generative, Altair Inspire/OptiStruct, Siemens NX/Simcenter HEEDS, Ansys Discovery/Mechanical/Granta, Dassault CATIA/3DEXPERIENCE (Tosca), ParaMatters, Frustum, and publicised aerospace / F1 practice. Sources are 2020–2026 where available; older canonical sources (Frustum, Airbus bionic partition) are retained because they remain the authoritative descriptions of those workflows.

---

## 0. The universal skeleton (what everyone actually does)

Across all nine toolchains and all aerospace/motorsport case studies, the real process — stripped of marketing — is the same nine-phase skeleton. Differences between vendors are mostly *how much of each phase is automated and where the human judgement gates sit*.

1. **Functional decomposition** — isolate the part from its system, freeze its interfaces (mounting faces, bolt holes, bearing seats, fluid ports).
2. **Design space definition** — build a maximal envelope volume; declare preserve regions (kept verbatim) and obstacle / keep-out regions (must stay empty).
3. **Physics capture** — apply materials, constraints, and *all credible load cases* (operational, shock, fatigue, thermal, gravity); run a **baseline FEA on the un-optimised design space** to validate the boundary conditions before any optimisation.
4. **Problem formulation** — objective (min compliance / min mass / max stiffness / max natural frequency), constraints (volume fraction or mass target, stress cap, displacement cap, safety factor), manufacturing constraints (draw direction, min member size, symmetry, overhang angle).
5. **Optimisation solve** — density/level-set/evolutionary iterations with FEA inside the loop; convergence monitored on objective and constraint histories.
6. **Result interpretation** — read the density field, pick a threshold (typically 0.3–0.5), confirm the load path is continuous and recognisable. A broken/scattered density plot signals a bad setup, not a good design (source: arched.ai OptiStruct workflow, https://arched.ai/resources/altair-optistruct-topology-optimization).
7. **Geometry reconstruction / healing** — isosurface extraction → smoothing (implicit or faceted) → optional NURBS/PolyNURBS/subdivision reconstruction → merge with preserved interface geometry.
8. **Validation re-analysis** — re-mesh the *reconstructed* geometry (conforming mesh), re-apply the *same* loads and constraints, solve in a second pass — often a different solver — and check stress, displacement, buckling, natural frequency, and factor of safety against acceptance criteria.
9. **Manufacturability & release** — DfAM/DfM checks (overhang, wall thickness, tool access, powder removal), build/process simulation for AM, physical test correlation for flight or safety parts.

The critical, non-obvious truth every serious source repeats: **the optimiser's own FEA numbers are not the answer**. The raw density-field result has stair-stepped boundaries, artificial stress concentrations, and mesh-resolution artefacts; the validated number only exists after reconstruction and re-analysis (source: "A design validation always needs to be done on the reinterpreted model as the results reported during the TO are not usually very accurate" — Springer/OAPEN, Guide to Additive Manufacturing, https://library.oapen.org/bitstream/id/5deafd1f-54b5-40d0-9ba3-9bd05cfb8290/978-3-031-05863-9.pdf).

---

## 1. Process models, tool by tool

### 1.1 nTop (nTopology) — implicit/field-first, workflow-as-product

**Order of operations** (source: nTop blog, "How to get the most out of topology optimization", https://www.ntop.com/resources/blog/how-to-get-the-most-out-of-topology-optimization/; nTop 3.0 release, https://www.ntop.com/resources/blog/ntopology-3-0-release-announcement/):

1. Import CAD part → convert to **implicit body** (signed-distance-field representation). Note: nTop does *not* preserve BC regions by default; they must be encoded as geometric constraints (source: UPC comparative study, https://upcommons.upc.edu/bitstreams/730d0796-e684-45ba-87a9-5cc0057bfea1/download).
2. Define design domain, loads, constraints, multiple load cases, and objectives (stress, displacement, stiffness, weight) as reusable **blocks** in a workflow graph.
3. Run **density-based (SIMP-style) topology optimisation** → output is an element-density field in [0,1].
4. Choose a **density threshold** to generate the part; iterate threshold if validation fails.
5. **Automated geometry reconstruction + smoothing** (implicit operations, a few blocks — no manual mesh repair). Optional downstream stages in the *same workflow*: latticing, variable shelling, overhang constraint for support-free AM.
6. **Validation**: loads/BCs carry over automatically because the workflow is associative; validation can run in nTop's built-in FEA or be exported with loads intact to Ansys Mechanical, Abaqus, Nastran, or LS-DYNA (source: nTop blog above).
7. **Manufacturing**: direct slicing to select AM machines via the nTop Implicit File (e.g. EOSPRINT) — bypassing STL entirely (source: nTop NASA EXCITE case, https://www.ntop.com/resources/blog/optimizing-the-mass-and-natural-frequency-of-the-nasa-excite-bracket-with-field-optimization/).

**Signature practice — NASA EXCITE A15 bracket (2023):** three stages: (1) coarse-mesh TO to *refine the design space*; (2) **Field Optimization** (shell-and-infill optimisation with gyroid TPMS infill, min/max thickness set by the AM machine's limits) on the reduced space; (3) add powder-escape hole, run static + modal validation, export implicit file direct to EOS M290 build prep (source: same nTop EXCITE article).

**Motorsport practice — as pro engineering, Le Mans Hypercar differential cap:** chosen explicitly for **multi-load-case simultaneous optimisation** (static + dynamic + fatigue + thermal); reusable workflow feeds load cases into FEA, 10x faster iteration loop, designed for AM to eliminate £30k casting tooling (source: nTop case study, https://www.ntop.com/resources/case-studies/reducing-weight-to-help-win-and-withstand-the-rigors-of-a-24-hour-le-mans-race/).

**Process philosophy:** "You design processes, not just parts" — workflows replay automatically when inputs change; nTopCL exposes the workflow to Python/Matlab and to MDO frameworks like modeFRONTIER (source: nTop 3.0 announcement, http://en.51shape.com/?p=3530).

### 1.2 Autodesk Fusion — generative (level-set, cloud, multi-candidate) and Shape Optimization (SIMP)

Fusion ships *two distinct engines*, and the distinction matters for playbook design (source: Springer chapter on SNCF Réseau AM, https://link.springer.com/chapter/10.1007/978-3-032-04774-8_70):

- **Topology / Shape Optimization** (local, simulation study): SIMP-based; starts from a full design space and *removes* material; deterministic — one optimum per constraint set; result must be edited afterwards; a static stress study still has to be run afterwards (source: Autodesk University lab doc MFG3734 by Tobias Jäger, https://static.au-uw2-prd.autodesk.com/MFG3734_Generative_Design_Topology_Optimization_Session_Documents_Elara_Aerospace_1756469057459001Q90g.pdf).
- **Generative Design** (cloud): **level-set method (LSM)**; starts from preserved + obstacle geometry in a mostly empty design space and *grows* material where needed; produces **many candidates** across materials and manufacturing methods in one run; manufacturing-awareness (additive orientation/overhang, 2/3/5-axis milling, casting) makes post-editing optional (source: same Springer chapter).

**Documented step order (Fusion Generative Design):**

1. Model/import base component and connection points.
2. Define **preserve geometry** (mounting interfaces, bolt cylinders, pads) and **obstacle geometry** (keep-outs, wrench clearance, envelopes) as separate bodies.
3. Optionally define a **starting shape**.
4. Apply structural constraints and **load cases** (operational, shock, fatigue — algorithm optimises for all simultaneously).
5. Select candidate **materials** (multiple per study).
6. Set objectives: minimize mass, **safety-factor target** (e.g. 2.0), max displacement limit.
7. Set **manufacturing constraints** (additive with build orientation + max overhang angle + min thickness; milling with tool diameter and access direction; symmetry planes).
8. Generate outcomes in the cloud; filter and compare outcomes by mass, FoS, displacement, cost.
9. Export selected outcome (convergent/TSpline body) back into the design context.
10. **Validate by analysis and physical test** — explicitly flagged as a required final step, not automatic (source: virse.ai workflow summary of Autodesk guidance, https://www.virse.ai/blog/best-ai-for-product-design; getrenewedtech guide, https://uk.getrenewedtech.com/2025/02/08/fusion-360-generative-design-letting-the-software-optimise-your-parts/; AU lab doc above).

**Warning embedded in vendor-neutral reviews:** result quality depends entirely on study definition — wrong constraints, incomplete load cases, or unrealistic manufacturing assumptions produce misleading geometry (source: virse.ai, above).

### 1.3 Altair Inspire / OptiStruct — density SIMP with manufacturing constraints, dual-solver validation

**OptiStruct engine:** SIMP — per-element pseudo-density ρ∈[0,1], stiffness penalised as E(ρ)=ρ^p·E₀ with p typically 3; objective typically weighted compliance subject to volume-fraction constraint; adjoint sensitivities; MMA-class updates; manufacturing constraints built in (MINDIM minimum member size, draw direction, symmetry, checkerboard-free density control) (source: SimTool OptiStruct article, https://www.simtool.com/article/altair-optistruct-topology-optimization-generating-manufacturable-lightweight-structures/; ISSC benchmark committee chapter, https://link.springer.com/chapter/10.1007/978-981-95-2668-0_7).

**Documented Inspire/OptiStruct order of operations:**

1. Geometry prep; split model into **design space vs non-design space** — non-design holds bolt holes, mounts, load points; the two regions **must share nodes** at the interface (source: arched.ai, above).
2. Baseline structural analysis on the un-optimised part to confirm loads/BCs behave (Inspire workflow; OptiStruct solver) (source: Altair Community workflow post, https://community.altair.com/discussion/37119/inspire-workflow-structural-analysis-topology-optimization-implicit-modeling).
3. Industrial best practice: run **unconstrained runs first** (only min/max member thickness, bounded to 3–12× average element size) to discover the natural load path, *then* constrained runs with manufacturing constraints (symmetry, single/split draw, extrusion) — log and compare all runs (source: DiVA thesis on Drill Steel Support, http://www.diva-portal.org/smash/get/diva2:1257343/FULLTEXT01.pdf).
4. Optimisation with objectives (min mass / max stiffness), stress/displacement constraints, gravity, temperature loads; multi-load-case support (source: Altair media licence deck, https://altair.com/docs/default-source/news-documents/ca-1318-media-license-altair-v2.pdf).
5. Interpret the **density plot at threshold 0.3–0.5**, confirm continuous load paths; re-run with adjusted setup if scattered (source: arched.ai, above).
6. Geometry extraction: **OSSmooth** or HyperMesh iso-surface → smooth STL/IGES; or Inspire **Fit/PolyNURBS** automatic smoothing to an editable CAD body.
7. **Quick validation solve on the extracted geometry before CAD commitment** to catch smoothing-induced stress concentrations (source: arched.ai; SimTool: "Altair SimSolid can then perform rapid FEA on the smoothed geometry to validate the optimized design before detailed modeling").
8. Lattice/hybrid stage (optional): combine TO result with implicit lattice infill; analysis of lattice-bearing models switches solver to **SimSolid** (meshless) (source: Altair Community workflow post, above).
9. Export Parasolid/STEP/STL; hand to Inspire Print3D for orientation/support optimisation (source: SimTool, above).

**Industrial framing:** Altair positions TO at the *start* of design, not the end ("other technologies implement it towards the end; Altair optimization can be used at the start") (source: TrueInsight, https://www.trueinsight.io/topology-optimization). A documented 6-step industrial casting playbook: identify loads/stresses → optimise → **verify manufacturability** → prototype → estimate value → propose (source: Modern Casting, https://www.moderncasting.com/articles/2022/04/12/6-steps-optimized-aerospace-casting).

### 1.4 Siemens NX / Simcenter HEEDS — feature-based TO inside PLM, SHERPA for parameter exploration

**NX Topology Optimizer order** (source: engineering.com review, https://www.engineering.com/siemens-nx-delivers-simulation-driven-design-enhancements/; Siemens lighter.nu deck, https://lighter.nu/sites/default/files/2022-12/heine_siemens.pdf):

1. In NX modelling environment, create the optimisation as a **parametric NX feature** — upstream geometry associativity (referenced geometry tracked) and downstream associativity (features update when the TO feature updates).
2. Set optimisation constraints (e.g. mass ≤ 2.5 kg) and analysis loads **per subcase**.
3. Solve → result is a **mixed convergent body**: mesh faces trimmed with traditional face types, directly editable, reusable downstream without reverse engineering.
4. **Validate in Simcenter 3D with the same setup, then verify against a preferred reference solver** — Simcenter Nastran, Samcef, Abaqus, ANSYS, MSC Nastran ("does it pass acceptance criteria; verify the optimized results meet the requirements when compared to a known reference solver") (source: lighter.nu deck, above).
5. PLM traceability: optimised design space imported back into the baseline NX model so changes are tracked concept→optimised; validation reruns static analysis on before/after models (source: DiVA thesis, https://www.diva-portal.org/smash/get/diva2:1990083/FULLTEXT01.pdf).

**HEEDS / NX Design Space Explorer:** not a topology tool — a **process-automation + multi-objective parameter optimisation** layer. Order: baseline simulation results → tag input/output parameters per portal → define design space (continuous variables with resolution; coarse/refined resolution rule: RefinedResolution = n·(CoarseResolution−1)+1) → objectives/constraints defined *only from simulation responses* → run **SHERPA** hybrid adaptive search (MO-SHERPA for Pareto) across distributed compute → history/parallel-coordinate review (source: MSU HEEDS lab guide, https://www.egr.msu.edu/classes/me475/averillr/Lab1/HEEDS.pdf; KTH thesis, https://kth.diva-portal.org/smash/get/diva2:1939649/FULLTEXT01.pdf; LiU thesis, https://liu.diva-portal.org/smash/get/diva2:1238247/FULLTEXT01.pdf). Typical pairing: NX generates lattice variants, Multimech homogenises, Nastran solves, HEEDS explores thickness ranges (source: Siemens blog, https://blogs.sw.siemens.com/nx-design/design-for-purpose-heeds-design-exploration-study/).

### 1.5 Ansys — Discovery for exploration, Mechanical for production, SpaceClaim for healing, Granta for materials

**Ansys Mechanical topology order** (source: Ansys TO whitepaper PDF, https://hmediastorageansi.blob.core.chinacloudapi.cn/t-1-resource/fa213b12-52fd-e6af-abc4-56a22b27efba.pdf; DiVA thesis, https://www.diva-portal.org/smash/get/diva2:1582937/FULLTEXT01.pdf; MDPI comparative study Tyflopoulos 2022, https://www.mdpi.com/2076-3417/12/2/611):

1. Geometry in SpaceClaim (or imported STEP/Parasolid/CATPart).
2. Static structural (+ modal) setup in Workbench; **multiple load cases with weighting factors**.
3. Topology optimisation solve — **SIMP default; level-set selectable** (Ansys is the only mainstream tool in comparative studies offering both; level-set gives cleaner boundaries) (source: UPC comparative study, above).
4. Optional **lattice optimisation** within a design region, including varying unit-cell thickness as a design variable.
5. Result is a faceted mesh → export STL → **SpaceClaim clean-up**: smoothing/wrapping tools flatten sharp areas that would be stress concentrations, restore bolt holes and mating surfaces using the original un-optimised geometry as reference framework.
6. **Automatic validation study**: Workbench copies all upstream inputs (BCs, load cases, mesh properties) into a new Mechanical system; re-solve the solid CAD and check design objectives (source: MDPI Tyflopoulos; Ansys ACT extension guide, https://oss.jishulink.com/upload/201812/d2b9c052fef6439bb44f8389ea270c03.pdf).
7. **Discovery Live layer**: real-time interactive TO for concept screening with instant resurfacing/validation of TO results, beta conversion of faceted TO output to analytical CAD (source: Simutech 2019 R2 highlights, https://simutechgroup.com/ansys-2019-r2-highlights/).
8. Surrounding stack: optiSLang for sensitivity/robustness/surrogate DoE; Granta MI for materials data/traceability; Additive Suite for build-process simulation (residual stress, distortion compensation) (source: Quora/Ansys product usage summary, https://www.quora.com/Which-products-of-ANSYS-are-useful-for-mechanical-engineers).

### 1.6 Dassault CATIA / 3DEXPERIENCE (Tosca engine) — concept shape → concept validation → CBD reconstruction

**Documented 3DEXPERIENCE step order** (source: TECHNIA guide, https://blog.technia.com/en/simulation/from-concept-to-performance-a-5-minute-guide-to-topology-optimization-in-3dexperience; EDS Technologies FGD case, https://edstechnologies.com/newsletter/2022/september/edst-functional-generative-design.pdf; GoEngineer feature matrix, https://www.goengineer.com/3dexperience/design/generative-design):

1. Structural Generative Design app; import part as linked copy; partition the model (design envelope vs preserved regions, e.g. hub/rim).
2. Assign material; define strategy (e.g. "maximise stiffness for a given mass").
3. Apply physics: bearing loads, clamp restraints, **load cases** for different operating conditions.
4. Run Tosca optimisation (mass target e.g. 30–35%, cyclic symmetry, local or cloud cores/cycles) → **topology density distribution** concept.
5. **Concept Shape** generation — user sets the cutting threshold; shape generated independently of the FE model.
6. **Concept Validation** — re-run the original loading on the concept shape; optional buckling/frequency analyses.
7. **Trade-off study** — vary parameters across variants, compare KPIs (displacement, stress, weight), rank, pick.
8. **CAD reconstruction** in Functional Generative Design: density distribution auto-converted to **subdivision surface** ("CRTIR", single click), load cases auto-regenerated for continuous validation, surfaces combined into a reconstructed solid (CBD) with no user interaction; AI-driven reconstruction assistants for milling/casting/rib-based designs (source: EDS case; GoEngineer).
9. Two-tier licensing mirrors process reality: Function-Driven Generative Designer (linear, designer tier) for most use-cases; Structural Generative Designer (full Abaqus nonlinear, fatigue, dynamic) for **final detailed validation** by specialist teams (source: GoEngineer).

Measured industrial turnaround for one bracket across three manufacturing variants (HJM/milling/casting): simulation setup ~2 h once, reused for all variants; optimisation ~2–3.5 h each; concept validation 15 min; CBD detail design ~1 h; detail validation 15 min — **whole multi-process campaign within a single day** (source: EDS Technologies case, above).

### 1.7 ParaMatters CogniCAD / CogniDesign — autonomous cloud pipeline for AM

- Cloud platform: import CAD → define loads and design criteria → compute; positioning is the "one click" end of the autonomy spectrum (source: 3D Printing Industry founder interviews, https://3dprintingindustry.com/news/defining-generative-design-software-implementation-ntopology-frustum-paramatters-134663/).
- Optimisation feature set: stress- and deformation-driven feature generation informed by fatigue considerations, advanced TO, **vibration/modal characteristics**, pre-programmed metamaterial lattices and **bone-like mesostructures**, multimaterial (beta); workflow explicitly targeted at AM-ready parts (source: same 3DPI article; real-j.mtak.hu review, https://real-j.mtak.hu/20639/2/dms_vol10_nr2_2020.pdf).
- Current successor (Cognitive Design Systems) adds automated DoE, design-space exploration with KPI trade-off dashboards, and implicit geometry (source: https://www.cognitive-design-systems.com/generative-design).

### 1.8 Frustum Generate (→ PTC Creo Generative Design) — voxel-based, print-ready by construction

- Cloud app combining a **voxel-based design algorithm with FEA**: upload STEP → pick faces → input loads and target weight reduction → organic optimised shape (source: engineering.com, https://www.engineering.com/topology-optimization-for-3d-printing-hits-the-cloud-with-generate/).
- Key design property: **all outputs watertight** — no intersecting polygons, instantly printable; manufacturing constraints for CNC axes (eliminating unmachinable undercuts) were on the roadmap even in 2016 (source: same).
- Pedigree: founded by Jesse Coors-Blankenship, lead optimisation engineer on the **Airbus–Autodesk** generative projects; acquired by PTC Nov 2018 and became Creo Generative Design's engine — a pipeline "that keeps TO tightly coupled to printable geometry and manufacturing constraints" (source: real.mtak.hu review, https://real.mtak.hu/148812/1/17_Szabo.pdf; Novedge design-software history, https://novedge.com/blogs/design-news/design-software-history-from-structural-optimization-to-generative-design-the-evolution-of-topology-optimization-geometry-kernels-and-industrial-workflows).

### 1.9 Aerospace & F1 practice — how the best strip mass then verify

**Airbus bionic partition (A320/A350, with Autodesk + APWorks):** bottom-up **slime-mould growth algorithm** connects critical structural points into a redundant network; a second algorithm stabilises the network; a **genetic algorithm** then optimises (minimise weight, maximise structural performance, avoid large holes); **tens of thousands of designs generated and FEA-evaluated**, Pareto set extracted; inner micro-lattice added to the macro strut network, explicitly designed to be **support-free for metal AM**; printed in Scalmalloy; ~45% weight reduction (65 kg → 35 kg), >100 printed pieces assembled, buy-to-fly ~95% (source: danilnagy.com project page, https://www.danilnagy.com/a320-bionic-partition/; develop3d, https://develop3d.com/product-design/weight-watchers-light-rider-3d-printing-topology-optimisation-design/; REAL 2016 report, https://3dprintingindustry.com/news/real-2016-airbus-autodesk-demonstrate-why-generative-design-equals-efficient-design-73601/; Jagiellonian review, http://jbc.bj.uj.edu.pl/Content/935954/NDIGOC102679_17_2021.pdf).

**Airbus / Altair production TO:** >40% weight reduction on A380 leading-edge ribs with OptiStruct-class SIMP TO, cited as the canonical proof that TO finds gains "even where products are already considered highly sophisticated" (source: Fuerle/Zhou/Fleury, Sim-AM proceedings, https://core.ac.uk/download/141514031.pdf). Airbus's published F&DT (fatigue & damage tolerance) stress-justification approach for AM parts shows the verification layer that follows (Mardaras et al., Procedia Structural Integrity 2017 — cited in Nottingham thesis, https://eprints.nottingham.ac.uk/72088/1/Alok%20Gupta%20Thesis%20Master%20Copy%20V2.pdf).

**NASA EXCITE A15 benchmark:** see §1.1 — TO to refine design space → shell-and-infill field optimisation with gyroid TPMS → static + modal validation → direct implicit slicing to EOS (source: nTop, above).

**SpaceX:** public detail is thin by design; reported 40% weight reduction on titanium engine mounts using **lattice structures** and a strict DfAM posture (design *for* the print process, consolidate parts to remove joints/failure points) (source: titanium-supplier.com industry summary, https://titanium-supplier.com/the-true-cost-of-titanium-3d-printing/; dreaming3d analysis, https://dreaming3d.net/blogs/news/how-spacex-3d-prints-the-most-powerful-rocket-engine-on-earth).

**Motorsport/F1:** as pro engineering's ATOMS programme (§1.1) is the clearest public process: multi-load-case TO (static + dynamic + fatigue + thermal), reusable workflow, test-correlate-validate loop at 10× speed, AM to kill tooling cost (source: nTop case study, above). FIA's 2026 F1 regulations explicitly enumerate approved AM alloys (incl. Scalmalloy, A20X) and *restrict* AM on heat-exchanger cores — i.e., governing bodies now write rules around AM'd optimised parts (source: VoxelMatters, https://www.voxelmatters.com/exploring-additive-manufacturing-in-the-2026-formula-1-technical-regulations/).

**Regulated-domain acceptance criterion example:** FAR 23.303 factor of safety 1.5 used as the pass/fail gate in a generative aircraft-concept study — the optimiser's output was *rejected* (FoS 0.75) and the geometry adapted until validation passed (source: UPC aircraft GD thesis, https://upcommons.upc.edu/bitstreams/ebd4c0d0-d276-48be-af26-e2ae81240418/download).

---

## 2. Optimisation methods — what each tool uses and when to choose each

| Method | Mechanism | Strengths | Weaknesses | Used by | Choose when |
|---|---|---|---|---|---|
| **SIMP (density)** | ρ∈[0,1] per element, E=ρ^p·E₀, penalisation p≈3, adjoint sensitivities, MMA/OC updates | Fast, robust convergence, simple sensitivity analysis, scales to millions of elements, easy multi-physics and constraint extension | Grey intermediate densities need thresholding; penalisation tuning; compliance-first (stress constraints harder) | OptiStruct, nTop, Ansys (default), NX, Fusion Shape Opt, Tosca, Nastran, SolidWorks | Default for industrial structural TO; large models; multi-load-case; when gradient info needed (source: arXiv 2605.04735, https://arxiv.org/html/2605.04735v1; Neural Concept, https://www.neuralconcept.com/post/what-is-topology-optimization-a-guide-to-efficient-design-techniques) |
| **Level-set (LSM)** | Boundary = zero contour of implicit function, evolved via Hamilton–Jacobi | Crisp smooth boundaries, no grey regions, better boundary stress control | Cannot nucleate holes (topology depends on initial design), frequent re-initialisation, more iterations | Ansys (selectable), Autodesk Fusion **Generative Design** (source: Springer SNCF chapter), research codes | When clean boundary geometry and surface stress accuracy matter; GD-style growth from preserve/obstacle regions |
| **BESO / ESO** | Discrete 0/1 elements removed/added by sensitivity ranking | Direct solid–void designs, stable convergence, intuitive, often faster than SIMP in practice, better at extreme mass reduction | Convergence proof hard; parameter tuning (addition ratio, filters); checkerboarding without filters | Abaqus–Matlab research stacks, Z88Arion; conceptually in Tosca heritage | When deterministic discrete answers and direct manufacturability are wanted; extreme lightweighting (>60–70% removal) where SIMP's stress distribution degrades (source: MDPI UAV bracket SIMP-vs-BESO, https://www.mdpi.com/2076-3417/15/23/12477) |
| **Lattice / TPMS infill** | Replace interior with periodic unit cells (gyroid, Schwarz Diamond, BCC/FCC struts), grade wall thickness by field | Massive stiffness-to-weight, smooth curvature (gyroid) cuts stress concentrations, support-free printable, field-driven grading | Needs implicit/voxel geometry engine; FEA cost high without homogenisation or meshless solvers | nTop (TPMS + Field Optimization), Fusion volumetric lattices, NX Lattice command, CATIA Lattice Designer, Altair Sulis/Inspire, Materialise | After macro-TO, to strip remaining interior mass; when vibration/energy-absorption properties matter; AM-only parts (source: MDPI UAV TPMS wing, https://www.mdpi.com/2226-4310/13/1/50; Prisecaru 2026 software review, https://www.mdpi.com/2504-477X/10/1/32) |
| **Multi-load-case** | Weighted compliance across load cases simultaneously | Matches real duty cycles; avoids a part that is optimal for one load and fails another | More solve cost; weighting is a judgement call | nTop, Inspire, Ansys (with per-case weights), 3DEXPERIENCE load cases | Almost always in production — single-load-case optimisation is a concept tool only (source: UPC comparative study; as pro Le Mans case) |
| **Multi-physics objectives** | Thermal-structural, modal/frequency, fatigue, fluid (Tosca Fluid, Fusion Fluid Path) | Handles real aerospace/energy environments (nacelles, rocket structures) | Formulation complexity; solver coupling cost | Ansys, Tosca, nTop, Fusion (fluid path GD) | Engine/thermal parts, vibration-critical brackets (NASA EXCITE maximised natural frequency), intake ducts, cooling manifolds (source: MDPI cooling-manifold two-stage GD, https://www.mdpi.com/2079-9292/15/4/765; CJA thermo-elastic bracket refs, http://www.jasp.com.cn/hkdlxb/article/pdf/preview/20250354.pdf) |
| **Growth / agent-based + GA** | Slime-mould-style network growth between connection points, then genetic algorithm Pareto search | Explores topology space beyond density methods; naturally redundant, damage-tolerant networks | Bespoke research tooling; thousands of FEA evaluations (cloud) | Airbus/Autodesk bionic partition | Halo/exploration projects where a portfolio of Pareto designs is the deliverable (source: danilnagy.com) |

Practical selection heuristic distilled from the comparisons: start with **SIMP for the macro load path**; switch to **level-set or GD-growth when boundary quality or candidate diversity matters**; use **BESO-style discrete methods for extreme reduction targets**; always follow with **lattice/shell-infill optimisation** for AM parts; consider **sequential SIMP→level-set** (SIMP initialisation, level-set boundary refinement) as the emerging best-of-both (source: arXiv 2605.04735, above; RMIT thesis method comparison, https://research-repository.rmit.edu.au/ndownloader/files/50795304).

---

## 3. The post-optimisation pipeline (density field → manufacturable, verified part)

### 3.1 Interpretation and extraction

- **Density thresholding:** typical visualisation/extraction threshold 0.3–0.5; sweep the threshold to confirm the load path is continuous before extracting. Threshold choice trades mass vs performance and is a stated engineering-judgement gate (source: arched.ai; IJIIRD review, https://ijiird.com/VBK26/ME075.pdf).
- **Isosurface extraction:** OptiStruct → OSSmooth/HyperMesh ISO surface → STL/IGES; Ansys → STL export; nTop → implicit threshold operation (no mesh needed); NX → convergent body directly; 3DEXPERIENCE → concept shape from cutting threshold (sources as in §1).

### 3.2 Geometry healing and reconstruction — three industrial strategies

1. **Stay implicit/mesh (AM-only path):** smooth the density field with implicit operations (nTop), optionally lattice/shell it, then **direct slicing** via implicit file (nTop Implicit File → EOSPRINT; Materialise Magics accepts implicit/hybrid inputs) — eliminates STL repair entirely (source: Prisecaru 2026; nTop EXCITE).
2. **Hybrid convergent bodies (CAD path):** Siemens NX Convergent Modeling lets facet-heavy TO bodies be booleaned, shelled, and dimensioned alongside precise B-reps; mesh faces trimmed with traditional face types (source: Novedge history; Siemens lighter.nu deck).
3. **NURBS/subdivision reconstruction (parametric path):** PolyNURBS (Inspire Fit), subdivision surfaces (3DEXPERIENCE CRTIR), SpaceClaim smoothing/wrapping, or manual surfacing over the mesh as a tracing template. Academic work quantifies why this step is not cosmetic: NURBS reconstruction **reduced peak stress 37%** vs direct SIMP output in a scooter-neck case; a two-stage NURBS + fixed-grid shape-optimisation framework kept compliance deviation within **0.5–2.6%** of the raw TO result (source: Scilit/CMES Gouzi work, https://www.scilit.com/scholars/019f2933551672809769c35488da68fb; Tang 2026, https://www.sciopen.com/article/10.32604/cmes.2026.079578). Fully automated STL reconstruction and naive edge-based NURBS both **failed** on non-manifold geometry — a hybrid region-decomposition approach was the only one that produced a watertight, FEA-compatible CAD model (source: Gouzi, above). Smoothing's explicit purpose: flatten sharp areas that are stress-concentration/singularity sites, and restore functional interfaces (bolt holes, mating surfaces) using the original geometry as reference (source: MDPI Tyflopoulos; Ansys whitepaper).

### 3.3 Verification — how the best actually verify

1. **Re-mesh and re-solve the reconstructed solid** with the *same* loads/BCs — Ansys automates this as a linked validation system; 3DEXPERIENCE regenerates load cases automatically on the concept shape; nTop carries BCs associatively (sources in §1).
2. **Second-solver / meshless cross-check:** Altair validates the smoothed geometry in **SimSolid** (meshless) before detailed modelling; Siemens verifies against a **reference solver** (Nastran/Samcef/Abaqus/ANSYS); lattice-bearing models are analysed meshless because conformal lattice meshing is impractical (sources: SimTool; Altair Community; Siemens deck).
3. **Extended validation physics:** static + modal (natural frequency targets), **buckling**, fatigue (nCode/fe-safe/Airbus F&DT justification), and for AM the **build-process simulation** (residual stress, distortion compensation, support/orientation optimisation) (sources: TECHNIA; Ansys additive stack; AMGTA LPT bracket LCA, https://www.metal-am.com/amgta-shows-additive-manufacturings-role-in-lightweighting-aircraft-engine-bracket/).
4. **Acceptance gates:** explicit safety-factor thresholds (e.g. FAR 23.303 FoS ≥ 1.5), displacement caps, frequency floors; aerospace GD results "are always validated with independent analysis, and often with physical testing" (source: getleo.ai aerospace tools review, https://www.getleo.ai/blog/best-generative-design-tools-aerospace; UPC thesis FoS rejection example).
5. **Physical correlation:** instrumented prototype tests (three-point flexural tests of MJF-printed TO beams; stress measurements within 8% of simulation on truck brackets) close the loop (source: UPC comparative study; IJIIRD review).

---

## 4. What "biological / organic generative design" actually means

Four distinct technical meanings, often conflated in marketing:

1. **Topology optimisation itself** produces branch-like forms because the optimal load path under a volume constraint resembles trabecular bone — Wolff's-law-like material placement along principal stress directions. Density plots overlaid with first-principal-stress vectors are used industrially to confirm "bone-like" load paths before committing (source: lightweight-structures.de composite workflow, https://www.lightweight-structures.de/article/view/175/112).
2. **Trabecular/Voronoi infill:** stochastic Voronoi foams mimicking cancellous bone microstructure; used in implants for stiffness matching (avoiding stress shielding) and osseointegration (source: PMC hip-implant lattice study, https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7557818/).
3. **TPMS lattices (gyroid, Schwarz Diamond, Primitive):** defined by implicit level-set equations (e.g. gyroid: sin x·cos y + sin y·cos z + sin z·cos x = c); zero-mean-curvature surfaces found in butterfly wings and beetle shells; smooth curvature removes stress concentrations, good energy absorption, **self-supporting in AM**; gyroid generally best mechanical performer (sources: PMC hip study; Teng 2022 lattice comparison, https://pmc.ncbi.nlm.nih.gov/articles/PMC9324303/; Bora/Shukla graded gyroid scaffold in nTop + Abaqus, https://www.sciencedirect.com/science/article/abs/pii/S2214785323026810).
4. **Growth algorithms:** slime-mould/agent-based network growth + genetic-algorithm refinement (Airbus bionic partition); "mammal bone growth dynamics" cited as the second biological inspiration in the same project (source: danilnagy.com; Jagiellonian review).

**Which tools implement it:** nTop (implicit TPMS library, field-graded wall thickness, shell-and-infill Field Optimization); Autodesk Fusion (volumetric lattices: gyroid, Schwarz, Octet); Siemens NX (Lattice command, homogeneous + graded); CATIA/3DEXPERIENCE Lattice Designer (TPMS wizards); Altair Sulis/Inspire (TPMS/strut + performance zones); Materialise Magics/3-matic (offset/repair/smoothing of TPMS meshes); ParaMatters (bone-like mesostructures); research/Hyperganic/OpenVCAD for voxel FGM (source: Prisecaru 2026 comparative review of 31 platforms, https://www.mdpi.com/2504-477X/10/1/32).

---

## 5. Distilled best-practice stage list for the ACES structural playbook

Each stage lists **entry criteria → actions → exit criteria → evidence produced**. This is the "structural strip-first" spine: strip mass with TO first, then refine, then verify.

**Stage 0 — Functional decomposition & interface freeze**
- Entry: part description (e.g. chassis bracket, motor mount, duct support); system context available.
- Actions: isolate part from assembly; identify and freeze all interfaces (mounts, holes, ports, mating faces); catalogue these as *non-negotiable preserve regions*.
- Exit: interface list signed off; every load path into/out of the part identified.
- Evidence: interface/preserve-region specification; assembly context model.

**Stage 1 — Design space & keep-out definition**
- Entry: Stage 0 complete.
- Actions: build maximal envelope volume; define design space (material allowed) vs obstacle/keep-out zones (clearance, adjacent hardware, tool/wrench access); add symmetry planes if physics allows.
- Exit: design space reviewed against packaging; obstacles validated against assembly model.
- Evidence: design-space model with tagged preserve/obstacle bodies.

**Stage 2 — Loads, constraints & baseline FEA**
- Entry: design space frozen.
- Actions: assign candidate materials; define **all credible load cases** (operational, limit, shock, fatigue spectra, thermal, gravity, vibration); run baseline static (+modal) FEA on the *full* design space; sanity-check reactions and stress hotspots against hand calcs.
- Exit: baseline FEA converges; load cases peer-reviewed; missing load case = stop.
- Evidence: baseline FEA report (stress, displacement, FoS, natural frequency); load-case register.

**Stage 3 — Problem formulation**
- Entry: validated baseline.
- Actions: choose objective (min compliance / min mass / max stiffness / max frequency), constraints (volume fraction or mass target, stress cap, displacement cap, FoS target e.g. ≥1.5–2.0 per domain standard), manufacturing constraints (process selection: AM overhang/min thickness; milling draw direction/tool; casting draft; min/max member size 3–12× element size).
- Exit: formulation reviewed; acceptance criteria written down *before* solving.
- Evidence: optimisation specification sheet (objective, constraints, acceptance gates).

**Stage 4 — Primary topology optimisation (the "strip")**
- Entry: Stage 3 signed off.
- Actions: SIMP density solve (default) with manufacturing constraints; monitor convergence of objective + constraint histories; industrial tip — run an *unconstrained* pass first to discover the natural load path, then constrained passes; multi-load-case always.
- Exit: converged density field; objective stable; constraints satisfied.
- Evidence: convergence plots; density field; per-load-case response table.

**Stage 5 — Result interpretation gate**
- Entry: converged solve.
- Actions: sweep density threshold 0.3–0.5; confirm continuous, recognisable load paths; overlay principal stress vectors if available; if scattered/broken → return to Stage 2/3 (bad space or missing load case), not forward.
- Exit: threshold selected; load path continuity confirmed by an engineer or rule-based check.
- Evidence: threshold-sweep images; load-path assessment note.

**Stage 6 — Secondary optimisation: shell & lattice infill (AM parts)**
- Entry: macro topology accepted; part is AM-bound.
- Actions: convert TO result to thin shell + TPMS/strut infill; field-grade wall/strut thickness by stress field; respect machine limits (min wall, powder escape holes); optionally re-optimise shell/infill thickness (nTop Field Optimization pattern).
- Exit: infill manufacturable (self-supporting, drainable); mass target met.
- Evidence: shell/infill parameter set; updated mass estimate.

**Stage 7 — Geometry reconstruction & healing**
- Entry: final optimised field/implicit body.
- Actions: choose path per §3.2 — implicit smoothing (AM), convergent body (hybrid), or NURBS/subdivision reconstruction (parametric); restore preserved interfaces exactly; smooth sharp transitions; verify watertightness/manifoldness.
- Exit: watertight solid; interfaces dimensionally exact vs Stage 0 spec; **geometry gate: reconstruction must not alter performance materially** (target: compliance deviation within low single-digit % — cf. 0.5–2.6% benchmark, Tang 2026).
- Evidence: reconstructed CAD; interface deviation report; manifold check log.

**Stage 8 — Validation re-analysis (the "verify")**
- Entry: healed geometry.
- Actions: conforming-mesh FEA with *identical* loads/BCs; second-solver or meshless cross-check; extend to modal, buckling, fatigue as domain requires; compare against Stage 3 acceptance criteria.
- Exit: all acceptance gates pass on the *reconstructed* geometry; if FoS/stress/frequency fail → loop to Stage 4 (adjust target) or Stage 7 (adjust smoothing), never ship the raw TO numbers.
- Evidence: validation FEA report (vs baseline and vs acceptance criteria); solver cross-check delta.

**Stage 9 — Manufacturability & process verification**
- Entry: validated design.
- Actions: DfAM/DfM audit (overhang, min wall, tool access, draft, powder removal); AM build-process simulation (orientation, supports, distortion, residual stress) where applicable; cost/buy-to-fly estimate.
- Exit: build plan feasible; distortion-compensated geometry if needed.
- Evidence: manufacturability checklist; build simulation report; cost estimate.

**Stage 10 — Physical test & correlation (flight/safety parts)**
- Entry: prototype/manufactured part.
- Actions: instrumented static/fatigue test; correlate strain/deflection vs validation FEA (industrial norm: within ~8–10%).
- Exit: correlation accepted; design released to PLM with full digital thread.
- Evidence: test report; correlation memo; released CAD + process definition.

### Cross-cutting principles for ACES playbook design

1. **Never trust the optimiser's own FEA numbers** — the deliverable of Stage 4 is a *hypothesis*, validated only in Stage 8 (every vendor's own docs say this: Ansys, Altair, nTop, Springer guide).
2. **Baseline before optimise** — Stage 2 FEA on the full design space is what catches bad BCs cheaply (Inspire and DiVA workflows both mandate it).
3. **Unconstrained pass first** — discover the natural load path before imposing manufacturing constraints (Altair industrial practice).
4. **Multi-load-case by default** — single-load-case optimisation is a concept tool; production parts optimise across the duty cycle simultaneously (nTop/Inspire/Ansys all support; Le Mans case shows why).
5. **Reconstruction is a performance-affecting operation, not cosmetics** — budget a performance deviation allowance and re-verify (37% peak-stress swing documented, Gouzi).
6. **Keep the thread associative** — the winning tools (NX feature-based, nTop workflows, Ansys linked validation, 3DEXPERIENCE auto-regenerated load cases) all preserve BCs/materials/loads from optimisation into validation; ACES should do the same programmatically.
7. **Method ladder** — SIMP for macro layout → level-set/growth for boundary quality or diversity → shell+lattice for final mass strip → second-solver verification. Sequential SIMP→level-set is the emerging research consensus (arXiv 2605.04735).
8. **Write acceptance criteria before solving** — FoS target, displacement cap, frequency floor, mass budget; the UPC study shows an optimiser result being *rejected* at the gate (FoS 0.75 < 1.5) and reworked — that is the correct behaviour to automate.

---

## Appendix — source register (primary citations used)

- nTop TO guide: https://www.ntop.com/resources/blog/how-to-get-the-most-out-of-topology-optimization/
- nTop 3.0 release: https://www.ntop.com/resources/blog/ntopology-3-0-release-announcement/
- nTop NASA EXCITE Field Optimization: https://www.ntop.com/resources/blog/optimizing-the-mass-and-natural-frequency-of-the-nasa-excite-bracket-with-field-optimization/
- nTop / as pro Le Mans case: https://www.ntop.com/resources/case-studies/reducing-weight-to-help-win-and-withstand-the-rigors-of-a-24-hour-le-mans-race/
- UPC software comparison (SIMP defaults, level-set in Ansys, multi-load-case matrix): https://upcommons.upc.edu/bitstreams/730d0796-e684-45ba-87a9-5cc0057bfea1/download
- Tyflopoulos et al. 2022, MDPI Applied Sciences (Ansys validation loop, SpaceClaim smoothing): https://www.mdpi.com/2076-3417/12/2/611
- Springer SNCF chapter (Fusion TO=SIMP, GD=level-set): https://link.springer.com/chapter/10.1007/978-3-032-04774-8_70
- Autodesk University MFG3734 lab (Fusion step order, FoS targets, manufacturing constraints): https://static.au-uw2-prd.autodesk.com/MFG3734_Generative_Design_Topology_Optimization_Session_Documents_Elara_Aerospace_1756469057459001Q90g.pdf
- arched.ai OptiStruct workflow (threshold 0.3–0.5, shared nodes, OSSmooth, pre-CAD validation): https://arched.ai/resources/altair-optistruct-topology-optimization
- SimTool OptiStruct SIMP internals + SimSolid validation: https://www.simtool.com/article/altair-optistruct-topology-optimization-generating-manufacturable-lightweight-structures/
- DiVA Drill Steel Support (unconstrained-first, member size 3–12× element, draw directions): http://www.diva-portal.org/smash/get/diva2:1257343/FULLTEXT01.pdf
- Altair Community Inspire workflow (baseline FEA → TO → PolyNURBS → lattice → SimSolid): https://community.altair.com/discussion/37119/inspire-workflow-structural-analysis-topology-optimization-implicit-modeling
- Siemens NX TO / convergent body / reference-solver verification: https://lighter.nu/sites/default/files/2022-12/heine_siemens.pdf ; https://www.engineering.com/siemens-nx-delivers-simulation-driven-design-enhancements/
- MSU HEEDS guide (SHERPA, resolution rule): https://www.egr.msu.edu/classes/me475/averillr/Lab1/HEEDS.pdf
- TECHNIA 3DEXPERIENCE TO guide (concept shape, concept validation, trade-off, CBD): https://blog.technia.com/en/simulation/from-concept-to-performance-a-5-minute-guide-to-topology-optimization-in-3dexperience
- EDS Technologies FGD case (Tosca, CRTIR subdivision, same-day turnaround table): https://edstechnologies.com/newsletter/2022/september/edst-functional-generative-design.pdf
- GoEngineer 3DEXPERIENCE matrix (GDE vs Structural tier, reconstruction assistants): https://www.goengineer.com/3dexperience/design/generative-design
- Ansys TO whitepaper (STL cleanup, SpaceClaim, linked validation): https://hmediastorageansi.blob.core.chinacloudapi.cn/t-1-resource/fa213b12-52fd-e6af-abc4-56a22b27efba.pdf
- Frustum Generate: https://www.engineering.com/topology-optimization-for-3d-printing-hits-the-cloud-with-generate/
- ParaMatters/Frustum/nTop founder definitions: https://3dprintingindustry.com/news/defining-generative-design-software-implementation-ntopology-frustum-paramatters-134663/
- Airbus bionic partition: https://www.danilnagy.com/a320-bionic-partition/ ; https://develop3d.com/product-design/weight-watchers-light-rider-3d-printing-topology-optimisation-design/ ; http://jbc.bj.uj.edu.pl/Content/935954/NDIGOC102679_17_2021.pdf
- Airbus A380 ribs >40% (Fuerle/Zhou/Fleury): https://core.ac.uk/download/141514031.pdf
- SpaceX lattice engine mounts (secondary source): https://titanium-supplier.com/the-true-cost-of-titanium-3d-printing/
- FIA 2026 F1 AM regulations: https://www.voxelmatters.com/exploring-additive-manufacturing-in-the-2026-formula-1-technical-regulations/
- SIMP/BESO comparison UAV bracket: https://www.mdpi.com/2076-3417/15/23/12477
- Method comparison (RMIT thesis): https://research-repository.rmit.edu.au/ndownloader/files/50795304
- Sequential SIMP→level-set: https://arxiv.org/html/2605.04735v1
- NURBS reconstruction performance: https://www.sciopen.com/article/10.32604/cmes.2026.079578 ; https://www.scilit.com/scholars/019f2933551672809769c35488da68fb
- Lattice/TPMS software review (Prisecaru 2026): https://www.mdpi.com/2504-477X/10/1/32
- TPMS mechanics: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7557818/ ; https://pmc.ncbi.nlm.nih.gov/articles/PMC9324303/
- Graded gyroid bone scaffold (nTop+Abaqus): https://www.sciencedirect.com/science/article/abs/pii/S2214785323026810
- Novedge geometry-kernel history (convergent modelling, implicit kernels, Frustum→PTC): https://novedge.com/blogs/design-news/design-software-history-from-structural-optimization-to-generative-design-the-evolution-of-topology-optimization-geometry-kernels-and-industrial-workflows
- UPC aircraft GD thesis (FAR 23 FoS gate, outcome weighting): https://upcommons.upc.edu/bitstreams/ebd4c0d0-d276-48be-af26-e2ae81240418/download
- Springer/OAPEN AM guide ("validation always needed on reinterpreted model"): https://library.oapen.org/bitstream/id/5deafd1f-54b5-40d0-9ba3-9bd05cfb8290/978-3-031-05863-9.pdf
- Politecnico di Torino thesis (smoothing purpose, remesh to validate): https://webthesis.biblio.polito.it/7768/1/tesi.pdf
