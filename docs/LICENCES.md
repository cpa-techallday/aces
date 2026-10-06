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

## Development

| Package | Licence |
| --- | --- |
| pytest | MIT |

## Reached through simlab (separate processes and containers, not linked)

| Tool | Licence | Note |
| --- | --- | --- |
| PyFR | BSD-3-Clause | compressible CFD |
| XLB | Apache-2.0 | lattice-Boltzmann (planned) |
| OpenFOAM | GPL-3.0 | separate process only |
| CalculiX | GPL-2.0 | separate process only |
| NVIDIA PhysicsNeMo, Warp | Apache-2.0 | surrogates, differentiable kernels |

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
