# Contemporaneous record: golden-golden-demo-20261010T084920Z-b3ac99

Created 2026-10-10T08:50:42.332025+00:00 by aces on aces-rehearsal · company - · R&D activity - · bundle sha256 `91aaf1ad18778878b7779b6c3719e45c538e14217997aa76695ed01a41a17e71` · previous `8bf243856b58185d3812d0de65afb664dac8eefc3a922d6625debee45b5e4492`

## Hypothesis

A SIMP-generated organic root mount meets vm_p99 <= 60 MPa and umax <= 0.035 mm at minimum mass, and a GP surrogate fitted to a 10-design sweep predicts the verified winner within 10 %.

## Expected outcome

the surrogate's winner meets every constraint on the real solver and agrees within tol

## Experiment

- project **golden-demo**, owner aces, physics structures; spec `/home/aces/Work/golden-demo/golden/20261010T084920Z-b3ac99/inputs/spec.effective.json` sha256 `acc99c6a2671265e41521b81d1ce216ba0c61d4f3759665286e456c317045b52`
- 14 case(s), 13 finished; sweep: volfrac ∈ [0.13, 0.38], rmin_mm ∈ [6.0, 12.0]
- solver `step-structures` (STEP -> centre-point voxelisation -> GPU voxel FEA -> von Mises / displacement fields (PNG, VTK), the CAD-to-result path), image `gendesign:0.3`, stages ['mesh', 'solve', 'post']
- solver validation status **pass** (2026-10-05)

## Geometry provenance

- source step, reference `domain.step`, sha256 `2494aed7db9a36629d6d498482afe4eb3569b47876c718eb97d8b3d45899bcef`

## Environment

- image `aces-golden:0.1` digest `sha256:d45698807541b49195d75de749d193ef33c5dc2803f0ccb0980d0e489e5f1201 aces-golden@sha256:d45698807541b49195d75de749d193ef33c5dc2803f0ccb0980d0e489e5f1201`
- GPU NVIDIA CMP 170HX on dff3f8b4dc43, uuid `GPU-787b2119-ba37-7d54-a632-543b3a9a6e36`, serial 1322321006368, driver 610.43.03
- GPU NVIDIA CMP 170HX on dff3f8b4dc43, uuid `GPU-73a17e95-caad-94ee-facf-5be93547c1e4`, serial 1322321009743, driver 610.43.03
- GPU NVIDIA CMP 170HX on dff3f8b4dc43, uuid `GPU-46ef0ebe-6262-8adf-4d0e-06befd2f10db`, serial 1322621048253, driver 610.43.03
- GPU NVIDIA CMP 170HX on dff3f8b4dc43, uuid `GPU-f9f3f3be-8085-c53b-3e22-68f3c412749d`, serial 1322421043203, driver 610.43.03
- GPU NVIDIA CMP 170HX on dff3f8b4dc43, uuid `GPU-3ab70cf0-0fd1-ee23-94e1-cf7cca85b6fd`, serial 1322421043362, driver 610.43.03
- GPU NVIDIA CMP 170HX on dff3f8b4dc43, uuid `GPU-21430bdd-e4c0-9d16-78e4-ee0aa5ac9f9b`, serial 1322621121733, driver 610.43.03
- GPU NVIDIA CMP 170HX on dff3f8b4dc43, uuid `GPU-21c3c635-4039-f2fd-9a09-a808b9af3ec8`, serial 1322221064269, driver 610.43.03
- GPU NVIDIA CMP 170HX on dff3f8b4dc43, uuid `GPU-812ab3db-8ebe-a4cd-d686-a99c98d07fb9`, serial 1322321009958, driver 610.43.03
- host aces-rehearsal: Linux-7.2.5-3-omarchy-x86_64-with-glibc2.44; python 3.14.7, numpy 2.4.6, scipy 1.17.1, optuna 5.0.0, build123d 0.11.1, trimesh 5.1.0, skimage 0.26.0
- host ae0378832cff: Linux-7.0.0-30-generic-x86_64-with-glibc2.39; python 3.12.3, numpy 2.4.6, scipy 1.17.1, cupy 14.2.0, optuna 5.0.0, build123d 0.11.1, trimesh 5.1.0, skimage 0.26.0
- host 084bf159a645: Linux-7.0.0-30-generic-x86_64-with-glibc2.39; python 3.12.3, numpy 2.4.6, scipy 1.17.1, cupy 14.2.0, optuna 5.0.0, build123d 0.11.1, trimesh 5.1.0, skimage 0.26.0
- host dff3f8b4dc43: Linux-7.0.0-30-generic-x86_64-with-glibc2.39; python 3.12.3, numpy 2.4.6, scipy 1.17.1, cupy 14.2.0, optuna 5.0.0, build123d 0.11.1, trimesh 5.1.0, skimage 0.26.0

## Observations

| case | volfrac | rmin_mm | mass_kg | vm_p99_MPa | umax_mm | compliance_Nmm | vm_max_MPa | status |
|---|---|---|---|---|---|---|---|---|
| d000 | 0.18807 | 9.5681 | 0.13901 | 63.076 | 0.039496 | 1062 | 74.664 | FINISHED |
| d001 | 0.23677 | 10.655 | 0.18459 | 51.568 | 0.030313 | 800.58 | 60.519 | FINISHED |
| d002 | 0.34331 | 7.2787 | 0.28398 | 38.995 | 0.024385 | 620.98 | 41.778 | FINISHED |
| d003 | 0.36522 | 10.099 | 0.30431 | 38.269 | 0.024086 | 612.73 | 41.033 | FINISHED |
| d004 | 0.15348 | 6.6882 | 0.11283 | 79.453 | 0.05092 | 1365.4 | 83.383 | FINISHED |
| d005 | 0.22779 | 7.8579 | 0.17952 | 53.097 | 0.0313 | 825.07 | 57.787 | FINISHED |
| d006 | 0.16005 | 10.961 |  |  |  |  |  | FAILED |
| d007 | 0.29153 | 11.632 | 0.2417 | 40.652 | 0.02644 | 684.29 | 49.685 | FINISHED |
| d008 | 0.26551 | 6.2325 | 0.21478 | 42.396 | 0.027364 | 707.95 | 50.376 | FINISHED |
| d009 | 0.31248 | 8.8144 | 0.2577 | 39.356 | 0.025477 | 653.54 | 45.957 | FINISHED |
| v001 | 0.23 | 11.55 | 0.17699 | 54.475 | 0.031198 | 828.01 | 64.49 | FINISHED |
| x001 |  |  | 0.17699 | 56.879 | 0.032483 | 864.85 | 105.39 | FINISHED |
| x002 |  |  | 0.17699 | 56.287 | 0.033146 | 888.96 | 178.37 | FINISHED |
| m001 |  |  | 0.17699 | 53.634 | 0.031756 | 844.95 | 144.52 | FINISHED |

Per-case manifests (`evidence.json`) and their hashes:

- d000: evidence sha256 `e9084d5030941a6646263046345bdddbd9379a7b9f8285826836951270661244`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `acc99c6a2671…`, domain.step `2494aed7db9a…`, case.json `0a1d231763b1…`, part.stl `355f1e851811…`
- d001: evidence sha256 `f4506d7989e86e6c48524623c173be94368fecf9ef3cf209641cac96206cd2ae`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `acc99c6a2671…`, domain.step `2494aed7db9a…`, case.json `6a23f2361121…`, part.stl `307fb0a3d9fb…`
- d002: evidence sha256 `b0631723e7df944a8f6bdb01c1a7de80d99945a5da95116cbea1252d66c9eba6`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `acc99c6a2671…`, domain.step `2494aed7db9a…`, case.json `2606d90d97ad…`, part.stl `a28c22fa772c…`
- d003: evidence sha256 `6de8487e6322f1ae31430dff28d4451730f9b94ac7f400a6e18781b9507b67d7`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `acc99c6a2671…`, domain.step `2494aed7db9a…`, case.json `35f04e5e10d2…`, part.stl `5538adecbe3d…`
- d004: evidence sha256 `5b3d827c9aaa66dfc1fc7572ff140ae0dd64ad3a50c7703546f671662e540bce`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `acc99c6a2671…`, domain.step `2494aed7db9a…`, case.json `d17f711b3b52…`, part.stl `37420a3fecbf…`
- d005: evidence sha256 `126a004d7600648ef385d20fba9980a4ca10d4e9b7c61fb98795c3f7e56edaa1`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `acc99c6a2671…`, domain.step `2494aed7db9a…`, case.json `360744a268c9…`, part.stl `2562428fb87f…`
- d006: evidence sha256 `49ef718ff2a0cbcfd0122fc2882f79f0a5b4d93907a1d4e5cb9174ac0b6fc2ad`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `acc99c6a2671…`, domain.step `2494aed7db9a…`, case.json `99123c6960a9…`
- d007: evidence sha256 `d4c3596a58874faa11d2899d51c174497f79827ac196ac80d3a8218984e2b21c`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `acc99c6a2671…`, domain.step `2494aed7db9a…`, case.json `06322464c127…`, part.stl `e79ea61a1a3d…`
- d008: evidence sha256 `80b35308707c0ea35a17d5880997c313bcee7e1ac9d20f4cf6216eb1809b716c`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `acc99c6a2671…`, domain.step `2494aed7db9a…`, case.json `50e611d4f35d…`, part.stl `cc09f05fbe82…`
- d009: evidence sha256 `1cd9993e8fc18d46538ad5e60ef3699fb61dd77e6c147ddb376ce7d4eafc6ce0`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `acc99c6a2671…`, domain.step `2494aed7db9a…`, case.json `110c945a420e…`, part.stl `ea78e0095fc6…`
- v001: evidence sha256 `594613ce501956b506fa9c5e9c9c62336b9863d5655d968e5b4b54bc833b430a`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `acc99c6a2671…`, domain.step `2494aed7db9a…`, case.json `41ba67824c6b…`, part.stl `de0f92f004eb…`
- x001: evidence sha256 `04aac08a68e3ebf3828a78be1624e52dd35cb7fe43d8ad52dc506ae1b263c709`, inputs golden-20261010T084920Z-b3ac99-check.yaml `212d143f4855…`, spec.effective.json `acc99c6a2671…`, domain.json `a2320df10bb6…`, v001.part.stl `de0f92f004eb…`, MLflow run f7ebba668de048dba35b9d4153a77cab
- x002: evidence sha256 `eb821c1dea5a056d0365075bb5462f504e97b5df136535e7b32d07d609e12672`, inputs golden-20261010T084920Z-b3ac99-check.yaml `212d143f4855…`, spec.effective.json `acc99c6a2671…`, domain.json `a2320df10bb6…`, v001.part.stl `de0f92f004eb…`, MLflow run 7531f2e9a90e4013a41f9965c404e3ba
- m001: evidence sha256 `95db6726d376ca8545086699128538efc6ac7288cb113627a06f7a04765ce3cb`, inputs golden-20261010T084920Z-b3ac99-conf.yaml `d1b55760ea48…`, spec.effective.json `acc99c6a2671…`, domain.json `a2320df10bb6…`, v001.part.stl `de0f92f004eb…`, MLflow run 97cfbb614b98494b8d3aaa63e46cfd9e

## Note

PASS · rig-verified · root-mount: volfrac 0.23 rmin_mm 11.55 · mass 0.177 kg · vm_p99_MPa 54.47 <= 60 · umax_mm 0.0312 <= 0.035 · predicted within 10 % (max 1.527 % on vm_p99_MPa) · check 2 mm on the rig (GPU 0): max gap +4.414 % on vm_p99_MPa, constraints met · conforming tet10 2 mm (ccx 2.23, rig): max gap -4.95 % on compliance_Nmm, constraints met · stages 81.93 s

Bundle file: `/home/aces/.local/share/aces/simlab/evidence/golden-golden-demo-20261010T084920Z-b3ac99/20261010T085042Z_bundle.json`
