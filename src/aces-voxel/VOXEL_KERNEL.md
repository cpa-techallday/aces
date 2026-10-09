# The ACES voxel kernel

Decision 4 (Aaron, 2026-10-08): the voxel kernel is standard in every ACES OS install. It ships as `aces-voxel`
(the `aces-voxel` command, this description and an agent skill) on top of `aces-engineering`, whose venv holds the
kernel code: the vendored `gendesign` and `simlab` modules from cudax-sim-runtime (commit and content hash in
`/opt/aces/engineering/SNAPSHOT`). This page says what the kernel does as of that snapshot, what it does not do, and
which module does what. It describes code that exists; it promises nothing.

## Source modules

| Module (vendored) | Role |
|---|---|
| `gendesign/fea3d.py` | `VoxelFEA`: structured grid of cube elements, matrix-free stiffness product, Jacobi-PCG, supports, loads, stresses; `self_test()` |
| `gendesign/filters.py` | `FFTDensityFilter`: linear (cone) density filter as a zero-padded FFT convolution, and its transpose for the chain rule |
| `gendesign/topopt3d.py` | 3-D SIMP: material interpolation, optimality-criteria update, weighted load cases, passive regions, outputs |
| `gendesign/evaluators.py` | `VoxelFEAEvaluator` (score a voxel mask on a fixed grid), `voxelize_mesh`, `build123d_to_mask`, `build123d_to_step` |
| `gendesign/export.py` | density to STL (marching cubes), binary STL writer, slice plots |
| `gendesign/lattice_opt.py`, `gendesign/warp_fea.py` | rod-lattice density with Warp gradients; Warp `apply_K` kernel (GPU only) |
| `simlab/solvers/step_structures.py` | STEP to voxels to FEA to fields, as `mesh`, `solve` and `post` stages |
| `simlab/post.py` | field slice plots, legacy-VTK structured-points writer, per-case Markdown |
| `simlab/solvers/gendesign_beam_check.py`, `gendesign_topopt.py` | registry adapters with validation records (`aces-simlab solvers`) |

Units everywhere: mm, N, MPa, kg.

## What is standard (in the code today)

- **Element.** 8-node trilinear hexahedron (H8) on a uniform grid of cubes of side `h`; element stiffness by full
  2x2x2 Gauss integration, isotropic linear elasticity (E, nu), small strain, engineering shear strains. The
  stiffness is assembled once with E = 1 and scaled per element, which is what SIMP needs.
- **Solver.** Matrix-free element-by-element product (gather 24 DOFs, 24x24 product, scatter-add) inside a
  Jacobi-preconditioned conjugate gradient. Every 100 iterations the residual is recomputed from the solution, and
  convergence is accepted only when the true relative residual passes the tolerance; every solve reports its
  iterations, true residual and a converged flag. Default tolerance 1e-8.
- **Precision.** FP64 by default everywhere. FP32 Jacobi-PCG was measured to converge falsely on stiff or slender
  problems (the recurrence claimed 1e-9 while the true residual was 0.4 to 1.7), so FP32 is an explicit, flagged option.
- **Backends.** NumPy on the CPU, CuPy on a CUDA GPU: the same code path, chosen at run time (`get_xp`). This
  package installs NumPy only; desktop runs are preview-grade, rig runs sign off (VISION.md section 3.4).
- **Boundary conditions.** Supports fix chosen DOFs of the nodes inside an axis-aligned box. Loads are either a
  total force spread evenly over the nodes in a box (`add_load_box`, used by `topopt3d` cases) or a consistent
  uniform traction on a grid-aligned face (`add_traction_box`: each loaded element face gives a quarter of its share
  to each of its four nodes; equal nodal forces were measured to over-deflect corners by about 12 %).
- **Results.** Displacements, compliance, element-centre strains and von Mises stress, maximum displacement.
- **SIMP.** `E = Emin + x^p (1 - Emin)`, p = 3 and Emin = 1e-9 (FP64) by default; density filter of radius `rmin`
  (default 1.5 h) with the filtered field as the physical density and the sensitivities chained through the filter's
  transpose; optimality-criteria update with move limit 0.2 and bisection on the volume multiplier; several load
  cases with weights and warm-started solves; passive solid and void regions (boxes, cylinders); stop on maximum
  change below `change_tol` (0.01) or the iteration limit. Outputs `density.npy`, `design.stl`, `slices.png` and
  `report.json` with the full iteration history, including any unconverged solve.
- **Voxelisation.** Centre-point containment of a closed mesh (`trimesh` `contains`): a cell is solid when its centre
  is inside. Open meshes fall back to a surface-dilated fill that over-fills by about one cell, and the STEP path
  says so. STEP parts are tessellated with build123d/OpenCASCADE at `min(0.05 mm, h/10)`; touching solids are fused first.
  A centre lying exactly on a face is a tie that trimesh's ray backend decides: the workstation used Embree
  (`embreex`), so aces-engineering ships it too. Measured on the L-bracket at 1 mm: 26,694 solid cells with Embree
  (workstation and Linux alike), 26,616 with trimesh's rtree fallback; all 78 differing cells sit on the 45-degree
  gusset face x + z = 36 mm, which passes through cell centres. Embree also took 0.4 s where the fallback took 93 s.
- **Export.** Marching cubes at iso 0.5 on the density field (scikit-image), optional Taubin smoothing, binary STL;
  fields as legacy VTK structured points (ParaView), PNG slices through the stress hot spot.

## Validation on record (simlab registry, 2026-10-05, workstation GPU, FP64 unless stated)

| Check | Result |
|---|---|
| element self-test | KE symmetric, rigid-body null space, uniaxial bar exact to 1e-6 (tip displacement and 25 MPa stress) |
| solid cantilever 100x10x10 mm, 100 N end traction (`gendesign-beam-check`) | -1.30 % vs Timoshenko at 1 mm voxels (401 PCG iterations), -0.84 % at 0.5 mm; stiff, as a fully clamped root face should be |
| cantilever SIMP, 1 mm voxels, volfrac 0.2/0.3/0.4 (`gendesign-topopt`) | compliance 3.589 / 1.956 / 1.286 N.mm, volume held; FP32 within 0.1 % |
| wall bracket, 240k elements | rig FP64 (CMP 170HX) and workstation FP32 give the same topology; 56 s vs 106 s |
| L-bracket STEP round trip (`step-structures`) | 26,694 solid voxels, identical to the direct build123d mask |

The aces-engineering package check re-runs the 1 mm cantilever on the CPU and requires the workstation's tip
deflection (0.1989054912444315 mm) to 1e-9 relative; on 2026-10-09 it matched to 8.3e-13 in 401 iterations.

## What it is not (yet)

- **No projection.** No Heaviside projection or beta-continuation: designs keep grey transition zones, and the STL
  is the 0.5 iso-surface of a filtered field. (Phase 2 list in cudax-sim-runtime's generative design guide.)
- **One optimiser, one objective.** Optimality criteria only (no MMA), compliance only (weighted over load cases),
  one volume constraint. No stress, buckling, frequency or displacement objectives or constraints.
- **One filter.** Linear density filter only: no PDE (Helmholtz) filter, no sensitivity-filter variant, no robust
  (eroded/dilated) formulation, no minimum-size control beyond the filter radius.
- **No manufacturing constraints.** No overhang, draw-direction, 2.5-axis or symmetry constraints.
- **One preconditioner.** Jacobi only; no multigrid (geometric multigrid PCG is on the Phase 2 list), no direct
  solver. Iteration counts grow with grid size and slenderness.
- **Linear statics only.** No geometric or material nonlinearity, contact, dynamics, modal analysis, thermal
  coupling, anisotropy or multiple materials.
- **Uniform structured grid.** No adaptive, octree or body-fitted meshes; curved surfaces are staircases at `h`.
- **Element behaviour.** Fully integrated H8 elements are stiff in bending with few elements through a thickness
  (shear locking): the beam check above quantifies it. No reduced integration or incompatible modes.
- **Boundary conditions.** Boxes and cylinders on the grid; no springs, no prescribed non-zero displacements.
- **Stresses.** Element-centre values, not recovered or smoothed; values next to load and support boxes are not
  reliable (the evaluator's `vm_exclude` boxes leave them out of the maximum).
- **Lattices need a GPU.** `lattice_opt` and `warp_fea` require CuPy, NVIDIA Warp and a CUDA device; on a CPU-only
  desktop `aces-voxel lattice` stops and says so.
- **Licence.** The kernel's source licence is not decided (decision 4: before the first external install).

## Commands

| Command | Routed to |
|---|---|
| `aces-voxel fea --self-test` | `gendesign.fea3d.self_test` |
| `aces-voxel fea CASE.json [--out DIR]` | `simlab.solvers.step_structures.Adapter.run` (mesh, solve, post) with the case keys of that adapter: `step_file`, `elem_mm`, `envelope_mm`, `origin_mm`, `margin_mm`, `E`, `nu`, `rho_kg_mm3`, `supports`, `loads` (with `axis` for a face traction), `vm_exclude`, `tol`, `maxiter`, `dtype` |
| `aces-voxel topopt CASE.json [--out DIR] [--iters N] [--dtype float64]` | `gendesign.topopt3d.main` (`--cpu` added when there is no CUDA GPU) |
| `aces-voxel lattice [--out DIR] [--iters N] [--rods N] [--volfrac F]` | `gendesign.lattice_opt.main` (GPU only) |
| `aces-voxel export DENSITY.npy [--h MM] --out PART.stl [--level 0.5] [--smooth N] [--slices PNG]` | `gendesign.export.density_to_stl`, `plot_slices` (`--h` read from a `report.json` next to the density when omitted) |

The agent skill is installed at `/usr/share/aces/voxel/skills/aces-voxel/SKILL.md`. `aces-omarchy-setup` links only
its own skill today; link this one by hand (`ln -s /usr/share/aces/voxel/skills/aces-voxel ~/.claude/skills/`).

Case formats: `/usr/share/aces/engineering/examples/cantilever3d.json` (SIMP) and the docstring of
`simlab/solvers/step_structures.py` (STEP FEA). For sweeps, evidence and reports use `aces-simlab` with the
`gendesign-topopt`, `gendesign-beam-check` and `step-structures` solvers.
