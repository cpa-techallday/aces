# AI-Native Engineering Simulation Studios & the Ordering of Multi-Physics Optimisation Loops

**Research date:** 2026-10-15
**Purpose:** Inform the solver-sequencing architecture of ACES (autonomous engineering platform): given "optimise this design," classify the part, pick the metric set, and sequence solvers the way an elite engineering team would.
**Scope note:** Sources are 2023–2026 where possible. Vendor claims (speedups, accuracy) are reported as vendor claims, not independently verified.

---

## Executive summary

Across every credible AI-native engineering platform examined — Neural Concept, Luminary Cloud, PhysicsX, Ansys SimAI, SimScale, Monolith AI, Cadence, and the NVIDIA PhysicsNeMo ecosystem — the same six-stage skeleton recurs:

**Parametrise → Sample (DOE) → Simulate (high-fidelity batch) → Surrogate (train) → Optimise (multi-objective, on the surrogate) → Verify (high-fidelity re-check of the Pareto knee).**

The ordering of *physics disciplines* inside that loop follows one consistent principle: **run the cheap, constraint-dominated, geometry-freezing physics first; run the expensive, coupled, performance-scoring physics last.** In practice that means mass/structures and packaging first (they define the feasible geometry envelope), flow/CFD second (it sets the thermal and efficiency boundary conditions), thermal/heat-rejection third (it consumes flow results as boundary conditions), and efficiency/system-level scoring last (it is a composition of everything upstream). High-fidelity solvers are used to *build* and *gate* surrogates; surrogates are used to *search*; high-fidelity re-checks happen only on the final candidate set before sign-off. This mirrors exactly the ACES intuition of "strip mass first, then flow, then thermal, then efficiency scoring" — and this report found strong industry evidence for that ordering, plus the per-part-class variants in §6.

---

## 1. AI-native engineering studios: how each structures the loop

### 1.1 Neural Concept (Lausanne; "Shape" platform)

- **Positioning:** 3D deep-learning surrogates ("Geometric Convolutional Neural Networks") trained on a company's historical CAD+CAE data to predict full-field results (pressure, stress, temperature) from raw geometry in seconds, claiming ~1000× acceleration of design exploration (source: Neural Concept, https://www.neuralconcept.com/post/ai-simulation-for-engineering-smarter-modeling-and-better-insights).
- **Loop structure:** Their canonical pipeline is: (a) ingest existing simulation archive (the *sample+simulate* stages are often retroactive — mining past runs instead of commissioning a new DOE); (b) train a geometry-to-field surrogate; (c) run multi-objective shape optimisation directly against the surrogate with deep-learning-based optimisers (source: Neural Concept, https://www.neuralconcept.com/post/on-deep-learning-and-multi-objective-shape-optimization); (d) verify promising shapes with the classical solver.
- **Physics ordering observed:** Single-physics surrogates per discipline, chained by the engineer. Their public case studies show thermal surrogates for satellite design where the thermal constraint *gates* the structural/layout iteration (source: Neural Concept, https://www.neuralconcept.com/post/satellites-a-smarter-design-regarding-the-thermal-constraints) and aerodynamics surrogates for F1 (Williams Racing is a named user; a ~1-hour CFD run collapses to ~20 s) (source: Enterprise AI Case Studies, https://case-studies.ai/use-cases/engineering-and-research/ER-009-ai-surrogate-models-simulation/).
- **Commercial signal:** $100M Series B (October 2025) — the largest raise in this cohort, indicating the "train-on-your-archive, then optimise in real time" loop is the market-validated pattern (source: LinkedIn/F. Finocchiaro market roundup, https://www.linkedin.com/pulse/top-5-ai-trends-transforming-engineering-simulation-finocchiaro-lvage).
- **Partnership pattern:** Rescale HPC integration (2024) to industrialise the *simulate* stage at scale (source: Rescale, https://rescale.com/news/new-era-of-engineering-intelligence-as-neural-concepts-partners-with-rescale-to-deliver-3d-ai-with-hpc-scale/).

### 1.2 Luminary Cloud (San Mateo; "Physics AI Factory")

- **Positioning:** Cloud-native, GPU-resident CFD/CHT solver plus an end-to-end "Physics AI Factory" for building and deploying surrogates on top of it (source: Luminary Cloud, https://luminary.ai/resources/luminarys-physics-ai-factory-the-end-to-end-platform-to-build-deploy-physics-ai-models/).
- **Loop structure (the most explicit in the industry):** The reference pipeline, built with nTop and ESTECO and showcased at NVIDIA GTC 2025, is literally a five-node chain: **parametric implicit geometry (nTop) → automated variant generation → massively parallel GPU CFD (Luminary) → surrogate/Physics-AI training (NVIDIA stack) → orchestrated design-space exploration and optimisation (ESTECO VOLTA/modeFRONTIER)** (source: nTop webinar, https://www.ntop.com/resources/webinars/ai-accelerated-aircraft-design-optimization/; source: Redmond Spokesman/PR, https://redmondspokesman.com/2025/03/18/luminary-cloud-and-ntop-streamline-ai-physics-and-cut-engineering-design-time-from-months-to-hours-with-nvidia-technology/).
- **Physics coverage:** CFD + conjugate heat transfer (CHT) — i.e., they deliberately fuse flow and thermal into one solve rather than sequencing them separately, which is only practical because GPU residency makes CHT cheap (source: NVIDIA Developer, https://developer.nvidia.com/blog/faster-insights-from-luminary-clouds-engineering-simulations-with-nvidia-gpus/).
- **Reference surrogates:** SHIFT-Wing — a pre-trained Physics-AI model for transonic wing design giving aerodynamic predictions in seconds (source: Luminary Cloud, https://luminary.ai/resources/shift-wing-a-physics-ai-model-to-accelerate-aircraft-design-innovation/); with Northrop Grumman, a thruster-nozzle flow model ~3000× faster than the solver (source: Luminary Cloud, https://luminary.ai/resources/a-new-structural-advantage-how-physics-ai-is-transforming-spacecraft-design-at-northrop-grumman/).
- **Customer outcome:** Rune Aero cut early aircraft development cost ~80% using the CUDA-X-accelerated "interactive virtual wind tunnel" pattern (source: Morningstar/PR Newswire, https://www.morningstar.com/news/pr-newswire/20250318la44193/physics-ai-accelerates-aircraft-design-time-rune-aero-cuts-early-development-costs-80-with-luminary-cloud).

### 1.3 PhysicsX (London; Large Physics Models)

- **Positioning:** "Large Physics Models" (LPMs) — foundation-scale neural networks trained on billions of high-fidelity simulation data points — combined with classical solvers in a **hybrid orchestration layer**: AI inference proposes and scores, numerical solvers verify and ground-truth (source: Tech Funding News, https://techfundingnews.com/uk-deep-tech-startups-2025-scaling-hard-problems/; source: PhysicsX platform page, https://www.physicsx.ai/platform).
- **Loop structure:** Explicitly lifecycle-wide — generative design-space exploration in concept phase, surrogate-accelerated iteration in development, and twin/telemetry learning in operation, with models continuously retrained on real-world data (source: Startup Intros profile, https://startupintros.com/orgs/physicsx).
- **Throughput claim:** ~100,000 simulation evaluations per day versus weeks by traditional methods; integrates with Ansys, CATIA, Siemens NX (source: Growth Engineer profile, https://growthengineer.ai/startups/physicsx).
- **Lineage:** Founded by ex-DeepMind and ex-F1 (Robin Tuluie, ex-Renault/Mercedes F1; Jacomo Corbo, ex-McLaren/Ferrari race strategy) — the F1 development loop is the cultural template: many cheap evaluations, ruthless gating, expensive verification only on winners (source: Tech Funding News, ibid; LinkedIn market roundup, ibid).
- **Recent direction:** Physics AI for data-center power/thermal infrastructure with Siemens + NVIDIA — i.e., coupled electrical–thermal–flow problems where the AI layer sits *above* the individual solvers and sequences them (source: PhysicsX newsroom, https://www.physicsx.ai/newsroom/physicsx-and-siemens-collaborate-to-advance-data-center-power-infrastructure-with-physics-ai-powered-by-nvidia).

### 1.4 Ansys SimAI (now Synopsys-Ansys)

- **Positioning:** Data-driven, *non-parametric* AI prediction: train on prior 3D simulation results, then predict full fields for new geometry in ~30 s, claimed 10–100× faster on compute-heavy projects (source: Ansys/Synopsys product page, https://ansys.synopsys.com/products/ai/simai; source: CADIT, https://www.cadit.com/blog/ansys-launches-ansys-simai).
- **Loop structure:** Deliberately slot-in: the user keeps their existing solver stack (Fluent, mechanical), exports a training set, trains a SimAI model, and uses it for rapid design-phase exploration; solver re-runs remain the verification step. optiSLang supplies the DOE/sensitivity/optimisation layer feeding SimAI training data (source: SimuTech, https://simutechgroup.com/resources/blog/ai-flow-field-prediction-with-ansys-simai-pro/).
- **Key design choice:** *Non-parametric* input (geometry as geometry, not as a parameter vector) — this removes the "parametrise" bottleneck that limits classic DOE to ~10–30 variables and is the main architectural difference vs. ESTECO-style PIDO loops (source: Ansys SimAI Pro docs, https://simai-pro.docs.ansys.com/).

### 1.5 SimScale (Munich; cloud-native, "AI-native")

- **Positioning:** Browser-based CFD/FEA/thermal/EM platform repositioned as "AI-native" with two tiers: *Engineering AI* (agentic workflow automation) and *Physics AI* (surrogate inference) (source: SimScale, https://www.simscale.com/; source: SimScale press, https://www.simscale.com/press/engineering-ai-agent-open-to-community/).
- **Loop structure:** End-to-end cloud design-space exploration: parametrise in CAD/Onshape-style connectors → cloud DOE sweep → cloud solve → explore/optimise in-platform (source: SimScale blog, https://www.simscale.com/blog/design-space-exploration-and-performance-optimization-in-the-cloud/).
- **Foundation-model move:** May 2025 — "world's first foundation AI model for turbomachinery simulation," built with NVIDIA PhysicsNeMo and integrated via the NVIDIA Omniverse Blueprint — i.e., a *pre-trained* surrogate for a whole part class, so the *sample+simulate* stages are amortised across all users rather than repeated per project (source: DailyCADCAM, https://dailycadcam.com/simscale-launches-foundation-ai-model-for-turbomachinery-simulation/).

### 1.6 Monolith AI (London; no-code engineering ML)

- **Positioning:** No-code ML on *existing* engineering data — test-rig data, historical simulation results — to build self-learning predictive models that engineers use to understand, predict and optimise without writing code (source: Design News, https://www.designnews.com/artificial-intelligence/ai-tool-solves-engineering-problems-without-coding; source: Monolith AI, https://www.youtube.com/watch?v=-Z97Zdup9hg).
- **Loop structure:** Distinctive because it starts from *test* data, not just simulation: ingest test/CAE history → train tabular/field surrogates → use model-guided "next best test" (active learning) to minimise physical test campaigns → optimise against the learned model. This is the closest commercial analogue to a pure active-learning DOE loop.

### 1.7 NVIDIA PhysicsNeMo (née Modulus) — the substrate layer

- **Positioning:** Open-source PyTorch framework for physics-ML: FNO, MeshGraphNet/GNN, DoMINO, Transolver-style transformer surrogates, PINNs — the library underneath SimScale's foundation model, Luminary's stack, and many in-house surrogates (source: NVIDIA PhysicsNeMo GitHub, https://github.com/NVIDIA/physicsnemo; source: NVIDIA Developer, https://developer.nvidia.com/physicsnemo).
- **Practice pattern:** Official reference workflows codify the loop: e.g., "build an AI surrogate for structural/crash analysis: generate FEA training set → train surrogate → deploy for real-time inference" (source: NVIDIA PhysicsNeMo blog, https://nvidia.github.io/physicsnemo/blog/2026/03/17/structural-mechanics/); Siemens Energy grid-asset surrogate claims ~10,000× speedup with sub-second inference and <0.3 K error as a "virtual sensor" (source: Enterprise AI Case Studies, ibid).
- **Digital-twin packaging:** Omniverse Blueprint for real-time CAE digital twins wraps surrogate inference in an interactive visualisation layer — the "why is it good" presentation surface (source: chinaerospace/PR Newswire on Rune Aero, https://www.chinaerospace.com/article/65437).

### 1.8 Cadence (Fidelity CFD + Millennium platform)

- **Positioning:** Hardware/software co-designed acceleration (Millennium M1, GPU-resident) for CFD multiphysics — up to 100× vs CPU HPC — rather than surrogate replacement; plus Fidelity's optimisation modules (source: Forbes, https://www.forbes.com/sites/marcochiappetta/2024/02/01/cadence-unveils-millennium-m1-in-a-breakthrough-for-multiphysics-acceleration/; source: DEVELOP3D, https://develop3d.com/simulation/cadence-millennium-integrated-hardware-software-cfd-platform/).
- **Loop structure (turbomachinery reference):** Cadence's published low-pressure steam-turbine ML optimisation workflow is a textbook loop: **DOE sweep of CFD runs → validate that the design space "makes sense" → build surrogate → optimise on the surrogate → run additional CFD at proposed optima (adaptive enrichment) → converge** (source: Cadence Community, https://community.cadence.com/cadence_blogs_8/b/cfd/posts/machine-learning-optimization-of-a-low-pressure-steam-turbine-stage).
- **Philosophy:** Accelerate the *simulate* stage rather than replace it; surrogates are one tool among DOE/adjoint/ML inside Fidelity's Optimus-style loop (source: Turbomachinery Magazine, https://www.turbomachinerymag.com/view/cadence-releases-digital-twin-based-millennium-platform-for-system-design-analysis).

### 1.9 Digital-twin shops (the "Dunkermotoren pattern")

Drive/motor OEMs and their integrators increasingly build *component digital twins* that couple electromagnetic, thermal and structural models of a motor/drive unit, then wrap them in surrogate or reduced-order models so system designers can query "what-if" in real time. The public evidence for this pattern is strongest in the Siemens Energy / NVIDIA virtual-sensor deployments (surrogate as a live sensor substitute, <1 s inference) and in Cadence/Synopsys "digital twin CFD" positioning (sources: Enterprise AI Case Studies, ibid; Embedded, https://www.embedded.com/cadence-launches-digital-twin-cfd-multiphysics-analysis-platform/). The common structure: high-fidelity multiphysics offline → ROM/surrogate online → telemetry feedback to retrain — i.e., the optimisation loop extended into operations.

### 1.10 Cross-vendor synthesis

| Stage | Who does what |
|---|---|
| Parametrise | nTop implicit geometry; CAD-native parameters; SimAI/Neural Concept bypass with non-parametric geometry input |
| Sample | ESTECO/modeFRONTIER DOE (LHS/Sobol etc.); optiSLang; in-house sweeps; mining historical archives (Neural Concept, Monolith) |
| Simulate | Luminary GPU CFD/CHT; Cadence Millennium GPU CFD; Ansys/SimScale cloud solvers; Rescale HPC |
| Surrogate | PhysicsNeMo models (FNO/GNN/transformer); Neural Concept GCNN; SimAI non-parametric; Luminary SHIFT family; PhysicsX LPMs |
| Optimise | ESTECO VOLTA/modeFRONTIER MOO; vendor-internal Bayesian/evolutionary optimisers; NSGA-II on surrogates |
| Verify | Original solver re-run on Pareto-knee candidates; wind-tunnel/rig test for final gate |
| Present | Pareto dashboards (ESTECO, modeFRONTIER), interactive twins (Omniverse), auto-generated design reports |

---

## 2. How elite teams sequence multiphysics optimisation

### 2.1 Formula 1 — the archetype of a gated loop

- **The upgrade pipeline is a strict gate sequence, not a free iteration.** A 2025 Motorsport/Autosport breakdown of the Racing Bulls process gives five steps: (1) aerodynamicists conceive a change; (2) CFD evaluation of candidate geometries; (3) wind-tunnel verification of the survivors; (4) design/structural integration (the aero shape is frozen, then engineering makes it manufacturable and structurally sound); (5) track confirmation (source: Motorsport.com, https://www.motorsport.com/f1/news/how-f1-teams-actually-produce-car-upgrades-in-five-steps/10727631/; source: Autosport, https://www.autosport.com/f1/news/how-f1-teams-actually-produce-car-upgrades-in-five-steps/10727725/).
- **Physics order:** Aero (CFD) iterates massively and cheaply at the front; the wind tunnel is the *expensive gate* in the middle; structures/manufacturing follow the aero freeze; track testing is the final verification gate. Cost-cap and Aerodynamic Testing Restrictions ration CFD and tunnel time, which forces exactly the "cheap first, expensive last, surrogate in the middle" discipline (source: Trackside Careers, https://tracksidecareers.com/blog/formula-1-wind-tunnel).
- **Objective structure:** classic F1 aero optimisation is explicitly multi-objective — downforce and lift/drag ratio as objective functions, verified by tunnel and CFD (source: Honda R&D paper on F1 aero development methodology, https://www.f1-forecast.com/pdf/F1-Files/Honda/F1-SP2_20e.pdf).
- **AI insertion point:** Neural Concept's F1 work inserts the surrogate *inside* the CFD iteration step — evaluating thousands of shapes per day and sending only the best to full CFD/tunnel (source: Neural Concept, https://www.neuralconcept.com/post/formula-1-multiple-connected-components-and-long-range-aerodynamic-correlations; source: Enterprise AI Case Studies, ibid).

**Lesson for ACES:** the loop is *iterative within a stage, gated between stages*. Cheap physics iterates; expensive physics gates.

### 2.2 Aerospace (aircraft/spacecraft) — MDO culture

- Aerospace is the home of formal Multidisciplinary Design Optimisation. The literature is unambiguous that **sequential discipline-by-discipline optimisation is equivalent to coordinate descent and produces provably worse designs than coupled MDO** — e.g., a coupled aerostructural wing optimisation beat the sequential approach by ~11% weight (sources: Martins & Lambe MDO survey, https://fab.cba.mit.edu/classes/865.18/design/mdo/MDOSurvey.pdf; University of Twente thesis citing Grossman/Sobieszczanski-Sobieski, https://essay.utwente.nl/fileshare/98519/Koch_MA_ET_2.pdf; Martins keynote, https://websites.isae-supaero.fr/IMG/pdf/martins-keynote-2015-06-cmn-lisbon.pdf).
- **But coupled MDO is expensive; the practical elite pattern is hierarchical fidelity:** low-fidelity coupled sizing loops (geometry/mass/aero/structures together, cheap models) → high-fidelity single-discipline refinement → high-fidelity coupled verification (aerostructural MDA converged at each optimisation iteration, as in the MACH framework) (source: MDPI Aerospace — MACH XDSM, https://www.mdpi.com/2226-4310/7/8/118).
- **Modern variant:** Bayesian-optimisation-driven multidisciplinary search with hybrid MDF/IDF architectures — the surrogate handles the coupling so the expensive coupled solve is only called at acquisition-function-selected points (source: Springer, https://link.springer.com/article/10.1007/s00158-026-04411-w).
- **Aeroelastic caution:** for flexible wings, geometrically nonlinear aerostructural coupling must be in the loop from the start — sequencing aero-then-structure naively fails when the coupling is strong (source: Springer, https://link.springer.com/article/10.1007/s00158-025-04181-x).

**Lesson for ACES:** sequential staging is fine for *weakly coupled* parts; when coupling is strong (aeroelastic wings, CHT heat exchangers, thermally-stressed rotating parts), iterate the coupled pair as a unit — but still with fidelity escalation inside the pair.

### 2.3 EV / battery thermal

- Published EV battery-thermal optimisations follow: electrochemical/heat-generation model first (defines the heat load = boundary condition) → cooling architecture selection → CFD/thermal surrogate (typically Kriging/RBF/ANN on a DOE of cell spacing, channel geometry, flow rates) → multi-objective optimisation (max temp, temp uniformity, mass, pumping power) → CFD verification (sources: MDPI Batteries 2026, https://www.mdpi.com/2313-0105/12/3/86; Preprints/Batteries 2025 fin-PCM multi-objective surrogate study, https://www.preprints.org/manuscript/202509.0199; NASA/ADS immersion-cooling multi-objective design, https://ui.adsabs.harvard.edu/abs/2025IJTS..21009636A/abstract).
- **Physics order:** heat *generation* (electrical) → heat *rejection* (thermal/flow) → mass/packaging trade-off last, because mass is the objective that closes the loop against vehicle range.

### 2.4 Turbomachinery

- The canonical industry order is **meanline (1-D) preliminary design → throughflow/2-D → 3-D blade geometry → 3-D CFD verification → mechanical/structural check** — a pure fidelity ladder (sources: Concepts NREC, https://www.conceptsnrec.com/blog/use-of-optimization-in-turbomachinery-design-process; OUCI/ORC radial turbine workflow, https://ouci.dntb.gov.ua/en/works/lDqYgEq9/).
- AI-era variant: DOE on 3-D CFD → surrogate (NN/CNN on blade parameterisations) → multi-objective optimisation of a multi-stage compressor with CFD re-checks at the optima; high-dimensional expensive problems explicitly framed as "surrogate + adaptive refinement" (sources: MDPI Energies 2025 three-stage axial compressor, https://www.mdpi.com/1996-1073/18/17/4514; ASME Turbo Expo 2025 AI surrogates for axial compressors, https://asmedigitalcollection.asme.org/GT/proceedings/GT2025/88889/V012T34A013/1220815; Cadence steam-turbine loop, ibid).
- SimScale's foundation model (§1.5) is an attempt to pre-build the surrogate for the whole part class.

### 2.5 EM / antenna

- MBSE-style phased-array workflows: parametric model → DOE tool (element spacing etc.) → EM solver sweep → optimisation; EM is usually the *first and dominant* physics because the array factor/radiation is the product, with thermal/structural as downstream constraints (source: University of Oulu thesis on MBSE phased-array design with ModelCenter DOE, https://oulurepo.oulu.fi/bitstream/handle/10024/18192/nbnfioulu-202110199152.pdf).

### 2.6 The ordering principles that emerge

1. **Constraint physics before objective physics.** Anything that can *kill* a design cheaply (mass budget, stress allowables, packaging envelope, stall/surge margins) runs early as a filter.
2. **Upstream-before-downstream along the dependency chain.** Flow fields are boundary conditions for thermal; thermal fields are loads for structural distortion; geometry is the input to all. The order mass/structure → flow → thermal → efficiency scoring is the dependency order of the PDE coupling.
3. **Iterate cheap, gate expensive.** DOE + surrogate iterate; high-fidelity solver gates; physical test signs off.
4. **Couple when coupling is strong.** Weakly coupled disciplines can be staged; strongly coupled pairs (aeroelasticity, CHT, thermomechanical rotating parts) must iterate together, but still with a fidelity ladder inside.
5. **Fidelity escalation at every stage.** Meanline → 2-D → 3-D; coarse mesh → fine mesh; surrogate → solver → test. No stage jumps two fidelity levels without an intermediate gate.

---

## 3. Design-space exploration practice: what's table stakes in 2025–2026

### 3.1 DOE / sampling

- **Latin Hypercube Sampling (LHS)** remains the default industrial space-filling design — e.g., NASA's parametric combustor study drives the entire CAD→mesh→CFD pipeline from an optimal-LHS DOE driver (source: NASA NTRS, https://ntrs.nasa.gov/api/citations/20160000873/downloads/20160000873.pdf).
- Commercial PIDO tools expose LHS, Sobol/low-discrepancy sequences, full/fractional factorial, and uniform designs as standard DOE menus (source: ESTECO DOE technology page, https://engineering.esteco.com/technology/design-of-experiments/).
- **Verdict:** LHS + Sobol are table stakes; anyone shipping a DSE tool without them is not credible.

### 3.2 Surrogate types

| Surrogate | Role in 2025–26 | Evidence |
|---|---|---|
| Gaussian Process / Kriging | Default for ≤ ~20-dim tabular problems; gives uncertainty for free (required for Bayesian opt / active learning) | Kriging-based Bayesian OED work (source: X-MOL/Probab. Eng. Mech., https://www.x-mol.com/paper/eng/tag/157/journal/111672) |
| RBF | Classic engineering RSM, still bundled in PIDO tools | MDPI fast-RBF review citing Neural Concept/SimAI ecosystem, https://www.mdpi.com/2673-4591/131/1/40 |
| Tabular GBM (XGBoost-style) | Cheap baseline for scalar KPIs from parameter vectors; strong explainability story | Standard in Monolith-style no-code stacks (source: Design News, ibid) |
| GNN / MeshGraphNet | Geometry-respecting field surrogates on meshes | PhysicsNeMo framework (source: GitHub, ibid) |
| FNO / neural operators | Resolution-independent field surrogates | PhysicsNeMo; SimScale foundation model built on it (source: DailyCADCAM, ibid) |
| Transformers (Transolver, UPT, AB-UPT) | State of the art for industrial-scale geometries (automotive/aero) | Transolver-3, https://arxiv.org/html/2602.04940v2; AB-UPT for automotive aero, cited therein |
| GCNN (Neural Concept) | Direct CAD-geometry-to-field | Neural Concept, ibid |

**Verdict:** table stakes = GP/Kriging for tabular + one field-surrogate family (GNN or neural-operator). Transformers and foundation models are the 2026 frontier, not yet table stakes for a general platform — but architecting for pluggable surrogate backends is mandatory.

### 3.3 Active learning / Bayesian optimisation

- The closed loop — GP surrogate + acquisition function balancing exploration/exploitation to pick the next simulation — is now standard methodology for expensive objectives (sources: CMU course notes, https://ulissigroup.cheme.cmu.edu/F22-06-325/notes/bayesian_optimization.html; Frontiers closed-loop ML review, https://www.frontiersin.org/journals/chemistry/articles/10.3389/fchem.2026.1902772/full).
- High-dimensional active-learning surrogate refinement (variance/uncertainty-driven DOE augmentation) is published practice (source: NIH/PMC, https://pmc.ncbi.nlm.nih.gov/articles/PMC11236939/).
- Uncertainty quantification on the surrogate (MC dropout, quantile losses, ensemble spread) is what makes "trust the surrogate enough to optimise on it, distrust it enough to re-check" operational (source: arXiv 2603.00093, https://arxiv.org/pdf/2603.00093).
- **Verdict:** table stakes. An optimiser that cannot say "simulate *here* next" is a generation behind.

### 3.4 Multi-objective optimisation

- **NSGA-II and Pareto fronts are the baseline expectation**; ESTECO/modeFRONTIER has been Pareto-native for two decades, with MCDM (multi-criteria decision-making) tools layered on top for picking the knee point (sources: DEVELOP3D modeFRONTIER review, https://develop3d.com/reviews/review-esteco-modefrontier-2014-optimisation-simulation-software/; ESTECO design optimisation page, https://engineering.esteco.com/technology/design-optimization/).
- Industrial practice runs the MOO *on the surrogate* and re-verifies the Pareto-optimal candidates with the solver (Cadence steam-turbine loop; Neural Concept multi-objective shape optimisation — sources ibid).
- **Verdict:** NSGA-II-class evolutionary MOO + Pareto dashboard + knee-point decision support = table stakes.

---

## 4. Presenting "how good is it and why"

1. **Pareto dashboards** — interactive trade-off plots where each point carries full parameter set and predicted metrics; clicking a knee point drills into geometry and fields. This is the ESTECO/modeFRONTIER paradigm, now replicated in Luminary/ESTECO integrated flows (sources: DEVELOP3D, ibid; CVUT DASY paper describing Pareto-set result presentation, https://stc.fs.cvut.cz/history/2015/sbornik/papers/pdf/5527.pdf).
2. **Automated documentation** — PIDO tools auto-generate the report trail: images, videos, native/exported CAD of chosen variants (source: DEVELOP3D modeFRONTIER review, ibid). The "spec sheet" is generated from the optimisation database, not written by hand.
3. **Interactive digital twins as the presentation layer** — NVIDIA Omniverse Blueprint real-time twins let a stakeholder *move the geometry and watch fields update*, which is the most persuasive form of "why is it good" (source: chinaerospace/PR Newswire Rune Aero, ibid; Agaruda CFD→surrogate twin walkthrough, https://agaruda.io/news/from-cfd-to-surrogate-models-real-time-physics/).
4. **Feature importance / explanation of engineered characteristics** — SHAP on surrogates is published and demonstrated for engineering analysis (identifying which design variables drive which KPIs), though explicitly noted as *not yet widespread* in engineering practice — i.e., a differentiator, not yet table stakes (source: HAL paper "Shapley Additive Explanations for Knowledge Discovery via Surrogate Models", https://hal.science/hal-04034297v1/document; source: ISAE-SUPAERO copy, https://openscience.isae-supaero.fr/digitalCollection/DigitalCollectionAttachmentDownloadHandler.ashx?parentDocumentId=5844&documentId=11712&skipWatermark=true&skipCopyright=true). Broader XAI-for-surrogates surveys confirm explainability is the bridge between black-box ML and safety-conscious engineering sign-off (source: arXiv 2604.14240, https://arxiv.org/html/2604.14240).
5. **Verification delta reporting** — elite loops report surrogate prediction vs. high-fidelity re-check side by side (the "trust but verify" delta), because that delta is the licence to use the surrogate next time. This is embedded in adaptive-refinement loops (Cadence steam turbine; active-learning DOE augmentation, sources ibid).

**Implication for ACES:** the output artefact per run should be (a) an auto-generated spec sheet of the chosen design (parameters, predicted metrics, verified metrics, deltas), (b) a Pareto dashboard of explored trades, (c) a feature-importance/sensitivity panel explaining *why* the design is good in engineering terms, and (d) a confidence/verification statement.

---

## 5. Failure modes and anti-patterns found in the research

- **Sequential discipline optimisation on strongly coupled problems** — provably suboptimal (MDO literature, §2.2).
- **Optimising on a surrogate without a high-fidelity gate** — every credible vendor keeps the solver re-check; PhysicsX's whole architecture is "AI proposes, numerics disposes" (source: PhysicsX platform, ibid).
- **Non-differentiable CAE pipelines blocking gradient methods** — meshing and legacy solvers break differentiability; the 2025 research answer is differentiable surrogates standing in for the pipeline so gradient-based shape optimisation becomes usable (source: arXiv 2511.10761, https://arxiv.org/abs/2511.10761).
- **Over-parametrised DOE** — classic parametric DOE collapses beyond ~20–30 variables; the field-surrogate vendors (SimAI, Neural Concept) explicitly sell *non-parametric* geometry input as the escape (source: Ansys SimAI Pro docs, ibid).
- **Surrogate without uncertainty** — no acquisition function, no trust region, no way to know when you're extrapolating (active-learning literature, §3.3).

---

## 6. Distilled recommendation: canonical stage order for ACES

### 6.1 The canonical "optimise this part" loop

```
STAGE 0 — CLASSIFY & SPEC
  Part class → metric set, constraint set, physics chain, fidelity ladder.
  Output: optimisation problem card (objectives, hard constraints, budgets).

STAGE 1 — GEOMETRY / MASS / PACKAGING  (constraint physics, cheap)
  Parametric or implicit geometry model; mass, envelope, interface and
  manufacturability filters. Kill infeasible regions of the design space
  BEFORE any expensive solve. "Strip mass first."

STAGE 2 — PRIMARY PERFORMANCE PHYSICS  (the part's raison d'être)
  Structure → static/modal FEA; Flow device → CFD; Heat part → thermal/CHT;
  EM part → EM solve. DOE (LHS/Sobol) batch at LOW/MID fidelity.

STAGE 3 — SURROGATE + MULTI-OBJECTIVE SEARCH
  Train surrogate (GP/tabular for KPIs; GNN/operator for fields) with UQ.
  NSGA-II / Bayesian optimisation on the surrogate. Active-learning
  acquisition orders new solver runs only where uncertainty × payoff is high.

STAGE 4 — SECONDARY / DOWNSTREAM PHYSICS  (consumes Stage-2 fields)
  Thermal from flow; stress/distortion from thermal+pressure loads; fatigue
  from stress history. Applied to the Pareto set, not the whole space.
  If coupling is strong (CHT, aeroelastic, thermomech), fold Stage 2+4 into
  one coupled iteration and escalate fidelity inside the pair.

STAGE 5 — HIGH-FIDELITY VERIFICATION GATE
  Full-fidelity solver re-check of Pareto-knee candidates. Record
  surrogate-vs-solver deltas (this is the trust ledger).

STAGE 6 — EFFICIENCY SCORING & PRESENTATION
  System-level score = composition of verified metrics (e.g., performance
  per unit mass per unit cost). Emit: auto spec sheet, Pareto dashboard,
  feature-importance panel, verification statement.
```

**Core invariants:** (1) constraint physics before objective physics; (2) dependency-ordered physics (upstream fields are downstream boundary conditions); (3) iterate on surrogates, gate with solvers; (4) fidelity escalation within every stage; (5) every run ends in a verifiable artefact, not a naked number.

### 6.2 Variants per part class

| Part class | Stage order variant | First physics | Coupling warning | Reference pattern |
|---|---|---|---|---|
| **Load-bearing structure** (bracket, frame, chassis) | Mass/packaging → static FEA (topology/sizing) → modal/buckling gate → fatigue → manufacturing constraints → verification | Structures | Thermal distortion if hot environment — add thermomech coupled pass | Neural Concept structural surrogates; PhysicsNeMo crash/structural workflow (sources §1) |
| **Flow device / duct** (manifold, duct, wing, aero surface) | Packaging → CFD DOE (coarse) → surrogate → MOO (pressure loss, mass flow uniformity, drag/lift) → fine CFD gate → aeroacoustics if relevant | Flow (CFD) | If thin/flexible: aeroelastic coupling — iterate flow+structure together (§2.2) | Luminary SHIFT-Wing; F1 aero loop; nTop+Luminary+ESTECO chain |
| **Heat-rejection part** (heat sink, cold plate, HX, battery cooling) | Heat-load definition (from electrical/power source) → flow/network model → CHT DOE → surrogate → MOO (ΔT, uniformity, pumping power, mass) → CHT verification | Thermal/flow **coupled** (CHT) | CHT is intrinsically coupled — never sequence flow-then-thermal as one-shot; iterate | Luminary CFD+CHT; EV BTMS surrogate studies (§2.3); NVIDIA virtual-sensor twins |
| **EM / antenna part** (antenna, motor, actuator) | Envelope/interfaces → EM solve DOE (the EM performance IS the product) → surrogate → MOO (gain, S-params, losses) → thermal-from-losses → structural detuning check → verification | EM | Thermal detuning and structural distortion feed back into EM — gate with coupled re-check if power is high | Oulu phased-array MBSE+DOE (§2.5); PhysicsX-Siemens power infrastructure (§1.3) |
| **Rotating machinery** (pump, compressor, turbine, fan) | Duty point spec → **meanline/1-D sizing** → 2-D throughflow → 3-D blade CFD DOE → surrogate → MOO (efficiency, pressure ratio, surge margin) → structural/rotordynamics gate → full-annulus verification | Meanline aero (fidelity ladder!) | Blade stress + aero are coupled at high load; surge/stall margins are hard gates, not objectives | Concepts NREC fidelity ladder; Cadence steam-turbine ML loop; MDPI 3-stage compressor; SimScale turbomachinery foundation model (§1.5, §2.4) |

### 6.3 Build-order implications for ACES

1. **Ship Stage 0 (classify & spec) and Stage 6 (artefacts) first** — they are what make the loop feel like "an engineer ran it," and they are the differentiators no incumbent has nailed.
2. **Make surrogates pluggable and uncertainty-aware from day one** — GP/tabular GBM for KPIs now; GNN/operator field surrogates behind the same interface later (PhysicsNeMo-compatible).
3. **Hard-code the trust ledger:** no candidate leaves the loop without a high-fidelity verification delta. This is the single habit that separates elite loops from demo loops.
4. **Implement active-learning acquisition as the scheduler** — the solver queue should be ordered by uncertainty × expected improvement, not by user request order.
5. **Present Pareto + feature importance as the default result view** — SHAP/sensitivity on the surrogate is currently a differentiator (not yet table stakes) and cheap to add once surrogates exist.

---

## Appendix: source list (consolidated)

- Neural Concept — AI simulation & multi-objective shape optimisation: https://www.neuralconcept.com/post/ai-simulation-for-engineering-smarter-modeling-and-better-insights ; https://www.neuralconcept.com/post/on-deep-learning-and-multi-objective-shape-optimization ; satellite thermal: https://www.neuralconcept.com/post/satellites-a-smarter-design-regarding-the-thermal-constraints ; F1 aero: https://www.neuralconcept.com/post/formula-1-multiple-connected-components-and-long-range-aerodynamic-correlations
- Rescale × Neural Concept: https://rescale.com/news/new-era-of-engineering-intelligence-as-neural-concepts-partners-with-rescale-to-deliver-3d-ai-with-hpc-scale/
- Market/funding roundup (Neural Concept $100M B, PhysicsX $135M B, Rescale): https://www.linkedin.com/pulse/top-5-ai-trends-transforming-engineering-simulation-finocchiaro-lvage
- Luminary Cloud — Physics AI Factory: https://luminary.ai/resources/luminarys-physics-ai-factory-the-end-to-end-platform-to-build-deploy-physics-ai-models/ ; SHIFT-Wing: https://luminary.ai/resources/shift-wing-a-physics-ai-model-to-accelerate-aircraft-design-innovation/ ; Northrop Grumman nozzle: https://luminary.ai/resources/a-new-structural-advantage-how-physics-ai-is-transforming-spacecraft-design-at-northrop-grumman/ ; NVIDIA GPUs: https://developer.nvidia.com/blog/faster-insights-from-luminary-clouds-engineering-simulations-with-nvidia-gpus/
- nTop × Luminary × ESTECO workflow: https://www.ntop.com/resources/webinars/ai-accelerated-aircraft-design-optimization/ ; https://redmondspokesman.com/2025/03/18/luminary-cloud-and-ntop-streamline-ai-physics-and-cut-engineering-design-time-from-months-to-hours-with-nvidia-technology/
- Rune Aero virtual wind tunnel: https://www.morningstar.com/news/pr-newswire/20250318la44193/physics-ai-accelerates-aircraft-design-time-rune-aero-cuts-early-development-costs-80-with-luminary-cloud ; https://www.chinaerospace.com/article/65437
- PhysicsX — platform: https://www.physicsx.ai/platform ; Siemens data-center power: https://www.physicsx.ai/newsroom/physicsx-and-siemens-collaborate-to-advance-data-center-power-infrastructure-with-physics-ai-powered-by-nvidia ; CB Insights AI 100: https://www.physicsx.ai/newsroom/physicsx-named-to-the-2025-cb-insights-list-of-the-100-most-innovative-ai-startups ; background: https://techfundingnews.com/uk-deep-tech-startups-2025-scaling-hard-problems/ ; https://growthengineer.ai/startups/physicsx
- Ansys SimAI: https://ansys.synopsys.com/products/ai/simai ; https://simai-pro.docs.ansys.com/ ; https://www.cadit.com/blog/ansys-launches-ansys-simai ; https://simutechgroup.com/resources/blog/ai-flow-field-prediction-with-ansys-simai-pro/ ; https://www.designworldonline.com/ansys-simai-promises-to-finally-democratize-engineering-simulation-for-the-masses/
- SimScale: https://www.simscale.com/ ; https://www.simscale.com/blog/design-space-exploration-and-performance-optimization-in-the-cloud/ ; https://www.simscale.com/press/engineering-ai-agent-open-to-community/ ; turbomachinery foundation model: https://dailycadcam.com/simscale-launches-foundation-ai-model-for-turbomachinery-simulation/ ; automotive AI: https://www.businesswire.com/news/home/20240918388452/en/SimScale-Announces-Simulation-Platform-Enhancements-for-AI-Powered-Automotive-Design
- Monolith AI: https://www.designnews.com/artificial-intelligence/ai-tool-solves-engineering-problems-without-coding ; https://www.youtube.com/watch?v=-Z97Zdup9hg
- NVIDIA PhysicsNeMo: https://github.com/NVIDIA/physicsnemo ; https://developer.nvidia.com/physicsnemo ; structural surrogate workflow: https://nvidia.github.io/physicsnemo/blog/2026/03/17/structural-mechanics/ ; Siml.ai integration: https://developer.nvidia.com/blog/ai-powered-simulation-tools-for-surrogate-modeling-engineering-workflows-with-siml-ai-and-nvidia-physicsnemo/
- Siemens Energy surrogate / virtual sensor case-study compendium: https://case-studies.ai/use-cases/engineering-and-research/ER-009-ai-surrogate-models-simulation/
- Cadence Millennium / Fidelity: https://www.forbes.com/sites/marcochiappetta/2024/02/01/cadence-unveils-millennium-m1-in-a-breakthrough-for-multiphysics-acceleration/ ; https://develop3d.com/simulation/cadence-millennium-integrated-hardware-software-cfd-platform/ ; https://www.embedded.com/cadence-launches-digital-twin-cfd-multiphysics-analysis-platform/ ; steam-turbine ML loop: https://community.cadence.com/cadence_blogs_8/b/cfd/posts/machine-learning-optimization-of-a-low-pressure-steam-turbine-stage
- F1 process: https://www.motorsport.com/f1/news/how-f1-teams-actually-produce-car-upgrades-in-five-steps/10727631/ ; https://www.autosport.com/f1/news/how-f1-teams-actually-produce-car-upgrades-in-five-steps/10727725/ ; https://tracksidecareers.com/blog/formula-1-wind-tunnel ; Honda aero methodology: https://www.f1-forecast.com/pdf/F1-Files/Honda/F1-SP2_20e.pdf
- MDO literature: https://fab.cba.mit.edu/classes/865.18/design/mdo/MDOSurvey.pdf ; https://www.mdpi.com/2226-4310/7/8/118 ; https://link.springer.com/article/10.1007/s00158-025-04181-x ; https://link.springer.com/article/10.1007/s00158-026-04411-w ; https://essay.utwente.nl/fileshare/98519/Koch_MA_ET_2.pdf ; https://websites.isae-supaero.fr/IMG/pdf/martins-keynote-2015-06-cmn-lisbon.pdf
- Turbomachinery: https://www.conceptsnrec.com/blog/use-of-optimization-in-turbomachinery-design-process ; https://www.mdpi.com/1996-1073/18/17/4514 ; https://asmedigitalcollection.asme.org/GT/proceedings/GT2025/88889/V012T34A013/1220815 ; https://arxiv.org/html/2503.14369v1
- EV thermal: https://www.mdpi.com/2313-0105/12/3/86 ; https://www.preprints.org/manuscript/202509.0199 ; https://ui.adsabs.harvard.edu/abs/2025IJTS..21009636A/abstract
- EM/antenna MBSE+DOE: https://oulurepo.oulu.fi/bitstream/handle/10024/18192/nbnfioulu-202110199152.pdf
- DOE/surrogate/MOO methods: https://ntrs.nasa.gov/api/citations/20160000873/downloads/20160000873.pdf ; https://engineering.esteco.com/technology/design-of-experiments/ ; https://engineering.esteco.com/technology/design-optimization/ ; https://develop3d.com/reviews/review-esteco-modefrontier-2014-optimisation-simulation-software/ ; https://pmc.ncbi.nlm.nih.gov/articles/PMC11236939/ ; https://ulissigroup.cheme.cmu.edu/F22-06-325/notes/bayesian_optimization.html ; https://www.frontiersin.org/journals/chemistry/articles/10.3389/fchem.2026.1902772/full
- Explainability: https://hal.science/hal-04034297v1/document ; https://arxiv.org/html/2604.14240 ; https://arxiv.org/pdf/2603.00093
- Surrogate/differentiable pipelines & frontier models: https://arxiv.org/abs/2511.10761 ; https://arxiv.org/html/2602.04940v2 ; https://www.mdpi.com/2673-4591/131/1/40
- CFD→surrogate digital twin walkthrough: https://agaruda.io/news/from-cfd-to-surrogate-models-real-time-physics/
