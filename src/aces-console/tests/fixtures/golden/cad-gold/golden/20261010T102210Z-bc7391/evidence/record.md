# Contemporaneous record: golden-cad-gold-20261010T102210Z-bc7391

Created 2026-10-10T10:22:59.160903+00:00 by aces on aces-rehearsal · company - · R&D activity - · bundle sha256 `a134a66fddc5ba3c31c5ea0870b57d42b4117328594005702001bd15aaa60f82` · previous `91aaf1ad18778878b7779b6c3719e45c538e14217997aa76695ed01a41a17e71`

## Hypothesis

A SIMP-generated organic root mount meets vm_p99 <= 60 MPa and umax <= 0.035 mm at minimum mass, and a GP surrogate fitted to a 10-design sweep predicts the verified winner within 10 %.

## Expected outcome

the surrogate's winner meets every constraint on the real solver and agrees within tol

## Experiment

- project **cad-gold**, owner aces, physics structures; spec `/home/aces/Work/cad-gold/golden/20261010T102210Z-bc7391/inputs/spec.effective.json` sha256 `dbc94cbb292d66e9bc840a2b04c337d05d31c5923a1a6605934cd9d1f676c5b7`
- 13 case(s), 12 finished; sweep: volfrac ∈ [0.13, 0.38], rmin_mm ∈ [6.0, 12.0]
- solver `step-structures` (STEP -> centre-point voxelisation -> GPU voxel FEA -> von Mises / displacement fields (PNG, VTK), the CAD-to-result path), image `gendesign:0.3`, stages ['mesh', 'solve', 'post']
- solver validation status **pass** (2026-10-05)

## Geometry provenance

- source step, reference `domain.step`, sha256 `2a88ca85e746264aebf988afe530eb582b94402215d4ca64712a9719e5f77e92`

## Environment

- host aces-rehearsal: Linux-7.2.5-3-omarchy-x86_64-with-glibc2.44; python 3.14.7, numpy 2.4.6, scipy 1.17.1, optuna 5.0.0, build123d 0.11.1, trimesh 5.1.0, skimage 0.26.0

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
| m001 |  |  | 0.17699 | 53.634 | 0.031756 | 844.95 | 144.52 | FINISHED |
| x001 |  |  | 0.17699 | 56.879 | 0.032483 | 864.85 | 105.39 | FINISHED |

Per-case manifests (`evidence.json`) and their hashes:

- d000: evidence sha256 `4d9f0ee2062c5e444d03cec48e55e445ec40d8492cfdc1f14d133a8665999399`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `dbc94cbb292d…`, domain.step `2a88ca85e746…`, case.json `0a1d231763b1…`, part.stl `355f1e851811…`
- d001: evidence sha256 `ebdae4d98a5304268008e571d65d323bece18de13a5035b87274d483c0d4cfcf`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `dbc94cbb292d…`, domain.step `2a88ca85e746…`, case.json `6a23f2361121…`, part.stl `307fb0a3d9fb…`
- d002: evidence sha256 `51e5f830453de147c35d9dd26b181bc5e0524d0329a7e71cd7dd1b96b6ffda83`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `dbc94cbb292d…`, domain.step `2a88ca85e746…`, case.json `2606d90d97ad…`, part.stl `a28c22fa772c…`
- d003: evidence sha256 `85f83f122d08bc4229ad537974eaf3e7c0af507b8459d73a4424906036053e8c`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `dbc94cbb292d…`, domain.step `2a88ca85e746…`, case.json `35f04e5e10d2…`, part.stl `5538adecbe3d…`
- d004: evidence sha256 `016ddfc485a4411bab3eb63519260de04e72649cb02a501b451a7ec2cadfb2c7`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `dbc94cbb292d…`, domain.step `2a88ca85e746…`, case.json `d17f711b3b52…`, part.stl `37420a3fecbf…`
- d005: evidence sha256 `573f5cdd359790a75ab8bbc8e15b72c2baad4c6077758c88653bc3ede54925ad`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `dbc94cbb292d…`, domain.step `2a88ca85e746…`, case.json `360744a268c9…`, part.stl `2562428fb87f…`
- d006: evidence sha256 `59326493b6c1cfe82fb116a43ab80be27f29fee23a73d1923a38823e2e997c69`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `dbc94cbb292d…`, domain.step `2a88ca85e746…`, case.json `99123c6960a9…`
- d007: evidence sha256 `5f02f63aac44cd345b252fb3ba82718f48ce8b5313fef350c64d3b6d6c40ce80`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `dbc94cbb292d…`, domain.step `2a88ca85e746…`, case.json `06322464c127…`, part.stl `e79ea61a1a3d…`
- d008: evidence sha256 `d5ed52b1af8935f6687f88dcfd93ee3cdf87a37a012ca4d03fc04578fbd430af`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `dbc94cbb292d…`, domain.step `2a88ca85e746…`, case.json `50e611d4f35d…`, part.stl `cc09f05fbe82…`
- d009: evidence sha256 `61af7c91879a78bce25b5ac236743ef4951380dd7483280cfd58d4c7ffb07200`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `dbc94cbb292d…`, domain.step `2a88ca85e746…`, case.json `110c945a420e…`, part.stl `ea78e0095fc6…`
- v001: evidence sha256 `7d36076fe26580e393c0dd0e37e94be6aede5b88c268548e3ea22e70ac315fc9`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `dbc94cbb292d…`, domain.step `2a88ca85e746…`, case.json `41ba67824c6b…`, part.stl `de0f92f004eb…`
- m001: evidence sha256 `a9812775ae8aa9113a4f320a5c5c20cc59570a7ffe600ae78a21c9166fd3e20f`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `dbc94cbb292d…`, domain.step `2a88ca85e746…`, case.json `d4ad0c9468fa…`, part.stl `de0f92f004eb…`
- x001: evidence sha256 `49da181d5442d3bbbbd6da1e7839c228a62f009bdd8486000301a18656ed587a`, inputs spec.json `b6c8bbefb1e9…`, spec.effective.json `dbc94cbb292d…`, domain.step `2a88ca85e746…`, case.json `581e266632e6…`, part.stl `de0f92f004eb…`

## Note

PASS · verified-desktop · root-mount: volfrac 0.23 rmin_mm 11.55 · mass 0.177 kg · vm_p99_MPa 54.47 <= 60 · umax_mm 0.0312 <= 0.035 · predicted within 10 % (max 1.527 % on vm_p99_MPa) · check 2 mm: max gap +4.414 % on vm_p99_MPa, constraints met · conforming tet10 2 mm (ccx 2.23, desktop): max gap -5.706 % on vm_p99_MPa, constraints met · stages 48.37 s

Bundle file: `/home/aces/.local/share/aces/simlab/evidence/golden-cad-gold-20261010T102210Z-bc7391/20261010T102259Z_bundle.json`
