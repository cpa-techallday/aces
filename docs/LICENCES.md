# Licence register

ACES is proprietary to Carbon Project Australia. Every dependency must be listed here before it is added, with a
licence that allows commercial, closed-source distribution and defence use.

## Runtime dependencies

| Package | Licence | Use |
| --- | --- | --- |
| numpy | BSD-3-Clause | arrays |
| scipy | BSD-3-Clause | distance transforms, labelling |
| scikit-image | BSD-3-Clause | marching cubes, surface area |
| pydantic | MIT | spec validation |
| PyYAML | MIT | spec files |
| optuna | MIT | NSGA-II search |
| CoolProp | MIT | fluid properties |

## formgen route (extra `[formgen]`; verified pins in requirements-formgen.txt)

| Package | Licence | Use |
| --- | --- | --- |
| trimesh | MIT | mesh I/O, remeshing support |
| cascadio | MIT | STEP to mesh. Bundles OpenCASCADE (LGPL-2.1 with exception): review before shipping binaries |
| manifold3d | Apache-2.0 | boolean union |
| fast_simplification | MIT | quadric decimation |
| networkx | BSD-3-Clause | strut graph |
| shapely | BSD-3-Clause | 2D geometry |
| Pillow | MIT-CMU | preview images |
| tkinter (stdlib) | PSF | formgen Studio GUI |

## Design studies (extra `[studies]`)

| Package | Licence | Use |
| --- | --- | --- |
| PyNiteFEA | MIT | frame FEA in the ARC-1 study and `aces.structures` |
| matplotlib | Matplotlib licence (PSF-style, permissive) | plots |
| rtree | MIT | spatial index for trimesh queries |
| trimesh | MIT | meshes |

## MCP server (extra `[mcp]`)

| Package | Licence | Use |
| --- | --- | --- |
| mcp (Model Context Protocol Python SDK) | MIT | `aces-mcp` stdio server |

## Development

| Package | Licence |
| --- | --- |
| pytest | MIT |
| ruff | MIT |
| pip-audit | Apache-2.0 |

## Reached through simlab (separate processes and containers, not linked)

| Tool | Licence | Note |
| --- | --- | --- |
| PyFR | BSD-3-Clause | compressible CFD |
| XLB | Apache-2.0 | lattice-Boltzmann (planned; ACES now has its own periodic LBM for calibration samples) |
| OpenFOAM | GPL-3.0 | separate process only |
| CalculiX | GPL-2.0 | separate process only |
| NVIDIA PhysicsNeMo, Warp | Apache-2.0 | surrogates, differentiable kernels |
| CuPy | MIT | GPU arrays for `aces.cfd` inside the `aces-cfd` image (from simlab's gendesign base); optional, numpy otherwise |

## External tools run as separate processes (added 2026-10-07, audit R8/I5)

| Tool | Licence | Note |
| --- | --- | --- |
| Palace (AWS Labs) | Apache-2.0 | radar EM verification (`aces.domains.radar.em_adapter`); separate process |
| gprMax | GPL-3.0 | radar EM (FDTD); **separate process only** (rule 11), never imported into ACES |
| GitHub CLI (`gh`) | MIT | `aces compliance collect` reads repo evidence through it; separate process |
| NVIDIA Isaac Sim | NVIDIA proprietary licence (EULA), not open source | `aces.isaac` drives an operator-installed copy on DARKNESS as a separate process; ACES never redistributes it, ships it in an image or accepts the EULA for anyone. The audit read the EULA as revocable, with no redistribution, an export clause and military use "at your own risk". **Restricted: no C-UAS/AERIS or other defence use until a legal review is recorded in DECISIONS.md.** PhysX runs in FP32, so results are never verification (rule 6) |

## Planned

| Package | Licence | Use |
| --- | --- | --- |
| PicoGK | Apache-2.0 | voxel geometry kernel for full parts (attribution required) |
| NASA CEA | Apache-2.0 | combustion equilibrium (propulsion domain) |

## Excluded

| Item | Reason |
| --- | --- |
| FluidX3D | Licence bans commercial use, defence use and AI training |
| OpenSees | Non-commercial |
| cuPyNumeric | End of life |
| DrivAerNet++ dataset | CC BY-NC |
| Any CC-BY-NC or "research only" data or model weights | Not usable in a sold product |

## Research quarantine (D29)

Non-commercial research licences are admitted for **internal R&D only** while the work is research
(the owner's call, 2026-10-09: "the nvidia licenses are fine as it is only research currently").
The quarantine rules, enforced in `aces.propose.contract`:

- registration needs `research=True` (CLI: `aces propose --research`); without it the gate still refuses
- every proposal a quarantined proposer emits is stamped `RESEARCH ONLY` in its notes and the output JSON
- quarantined code and weights never enter a product image, a customer deliverable or a commercial sprint,
  and live outside the shipped package (on the rig under a separate research path)
- each quarantined item is replaced by the clean-room proposer (docs/TIER0.md) before Gate 2 (paying customers)

| Item | Licence | Quarantined use |
| --- | --- | --- |
| NVIDIA XCube (code and weights) | Nvidia Source Code License: non-commercial research use; commercial use needs a separate NVIDIA Research Licence. Verified 2026-10-08 (github.com/nv-tlabs/XCube) | Reference generator while training the clean-room latent-diffusion proposer on our own verified corpus |
| NVIDIA SCube (code and weights) | Nvidia Source Code License (same terms) | Same |
