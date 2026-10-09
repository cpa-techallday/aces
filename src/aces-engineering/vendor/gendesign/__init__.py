"""gendesign: the open generative-design stack for the CUDA-X sim runtime (Phase 1 build).

Modules
-------
fea3d          voxel H8 linear-elastic FEA kernel, matrix-free Jacobi-PCG (CuPy on GPU, NumPy fallback)
filters        FFT density filter for topology optimisation
topopt3d       3-D SIMP topology optimiser          -> python -m gendesign.topopt3d case.json --out out/x
parametric     Optuna-driven parametric generative loop (single or multi-objective, resumable SQLite study)
evaluators     GPU voxel-FEA evaluator for parametric designs; mesh / build123d voxelisation
export         density field -> STL (marching cubes), reports, slice plots
fusion_bridge  push STL/STEP into the running Fusion design over the community bridge on :9876

Units everywhere: mm, N, MPa, kg.
"""

__version__ = "0.1.0"
