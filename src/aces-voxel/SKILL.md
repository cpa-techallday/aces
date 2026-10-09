---
name: aces-voxel
description: Run the ACES voxel kernel on an ACES OS desktop - voxel H8 FEA of STEP parts, 3-D SIMP topology optimisation, density-to-STL export - through aces-voxel, aces-gendesign and aces-simlab; keep FP64, treat desktop results as preview-grade, and send sign-off runs to the rig through simlab.
---

# ACES voxel kernel

The kernel is the vendored `gendesign` + `simlab` code in the aces-engineering venv. Read
`/usr/share/doc/aces-voxel/VOXEL_KERNEL.md` before relying on a number: it lists what the kernel does (H8 elements,
Jacobi-PCG with true-residual acceptance, SIMP with OC and a density filter) and what it does not (no projection, no
stress or buckling constraints, linear statics only, Jacobi only, lattices need a GPU).

## Commands

| Task | Command |
|---|---|
| Kernel self-test | `aces-voxel fea --self-test` |
| Whole stack self-test | `aces-gendesign selftest` |
| STEP part under loads | `aces-voxel fea case.json --out out/part` (keys: `step_file`, `elem_mm`, `supports`, `loads` with `axis` for face tractions, `vm_exclude`, `E`, `nu`, `dtype`) |
| Topology optimisation | `aces-voxel topopt case.json --out out/x` (example: `/usr/share/aces/engineering/examples/cantilever3d.json`) |
| Density to STL | `aces-voxel export out/x/density.npy --out out/x/part.stl --slices out/x/slices.png` |
| Parametric study | `aces-gendesign parametric problem.py:Class --trials 24 --out out/study` |
| What runs here | `aces-gendesign list` |
| Sweep with evidence | `aces-simlab run spec.yaml --out out/exp` then `aces-simlab evidence build out/exp/<id>` and `aces-evidence verify` |
| Push to Fusion | `aces-gendesign fusion out/x/design.stl --new-doc` (Fusion bridge on :9876) |
| End-to-end generative loop | `aces golden run --demo` (see the aces-engineering skill) |

## Rules

1. **FP64.** Keep `dtype` float64. FP32 Jacobi-PCG converges falsely on stiff and slender parts; use it only when a
   run says so explicitly, and never cite it.
2. **Check convergence.** Every solve reports `converged`, `pcg_iters` and `pcg_relres`. A run with an unconverged
   solve is not a result: say so and change the tolerance, the iteration limit or the grid.
3. **Preview here, sign off on the rig.** Desktop runs are preview-grade. A number that goes into a report or a
   decision comes from a rig run (`aces-simlab remote call sim_submit '{...}'`, FP64 on a leased GPU) with an evidence
   bundle. Never switch the rig's GPU mode; if no simulation GPU is free, say so.
4. **Face loads are tractions.** Give `axis` on a face load in `fea` cases (consistent nodal shares); equal nodal
   forces over-deflect corners.
5. **Stresses near load and support boxes are not reliable.** List those zones in `vm_exclude` and say that you did.
6. **Lattices need a GPU.** `aces-voxel lattice` stops on a CPU-only desktop; run it on the rig.
7. **Record what you ran.** For project work, `aces project add-component <project> aces-voxel` and keep the run
   directories; report measured numbers with their convergence data, not estimates.
