"""aces-gendesign: the vendored gendesign stack from the aces-engineering venv (CPU paths; GPU when CuPy and a GPU exist).

  aces-gendesign list                   evaluators and solver adapters, and what runs on this machine
  aces-gendesign selftest [--out DIR]   the packaged self-test (also run by the package build)
  aces-gendesign topopt CASE.json [--out DIR] [--iters N] [--dtype float64]     3-D SIMP (gendesign.topopt3d)
  aces-gendesign parametric FILE.py:Class --trials N --out DIR [--export N] [--kwargs JSON]   Optuna TPE / NSGA-II
  aces-gendesign nozzle --gamma G --eps E --re MM [--trunc F] [--out DIR]       Angelino aerospike contour (+ STEP)
  aces-gendesign fusion PATH [--new-doc] [--name N]   push an STL or STEP into Fusion over the bridge on :9876

Examples ship in /usr/share/aces/engineering/examples. The voxel kernel itself (fea, topopt, lattice, export) has
its own command, aces-voxel (package aces-voxel).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import optional_status, prepare_env

EVALUATORS = [
    ("gendesign.fea3d.VoxelFEA", "voxel H8 linear-elastic FEA, matrix-free Jacobi-PCG", "numpy (CPU) or cupy (GPU)"),
    ("gendesign.evaluators.VoxelFEAEvaluator", "score a boolean voxel mask: compliance, u_max, VM max, mass", "numpy"),
    ("gendesign.evaluators.voxelize_mesh", "closed mesh -> voxel mask by centre-point containment", "trimesh + embreex (Embree rays)"),
    ("gendesign.evaluators.build123d_to_mask", "build123d part -> voxel mask (via STL)", "build123d"),
    ("gendesign.topopt3d.run_case", "3-D SIMP, OC update, FFT density filter, passive regions", "numpy"),
    ("gendesign.parametric.run_study", "Optuna TPE (one objective) / NSGA-II (several), SQLite study", "optuna"),
    ("gendesign.nozzle.angelino_contour", "aerospike plug contour; STEP via build123d; NASA CEA via rocketcea", "numpy"),
    ("gendesign.lattice_opt / warp_fea", "differentiable rod lattice, Warp gradients", "cupy + warp + NVIDIA GPU"),
    ("simlab.surrogate.tabular", "GP / extra-trees surrogates with K-fold error", "scikit-learn, joblib"),
    ("simlab.surrogate.fno", "FNO field surrogate", "torch (+ physicsnemo)"),
]


def runs_here(entry: dict, opt: dict) -> str:
    """Whether a registry entry can run on this machine: its module imports and its known external needs are met."""
    import shutil

    try:
        __import__(entry["module"])
    except Exception as exc:
        return f"not here: {type(exc).__name__}: {str(exc)[:70]}"
    if entry["module"].endswith("pyfr_solver"):
        missing = [n for n, ok in (("pyfr CLI", shutil.which("pyfr")), ("gmsh", opt["gmsh"] == "importable")) if not ok]
        return "runs here" if not missing else f"rig image only (missing: {', '.join(missing)})"
    sur = entry.get("surrogate")
    if sur:
        if sur.get("kind") == "field" and opt["torch"] != "importable":
            return "rig image only (FNO needs torch)"
        if not os.path.isdir(sur.get("path") or ""):
            return f"model files not on this machine ({sur.get('path')})"
    return "runs here"


def cmd_list() -> int:
    from gendesign.fea3d import have_gpu
    from simlab import registry

    opt = optional_status()
    print(f"compute here: {'CuPy + CUDA GPU' if have_gpu() else 'CPU (NumPy); cupy ' + opt['cupy']}")
    print("\nevaluators:")
    for name, what, needs in EVALUATORS:
        print(f"  {name:42s} {what}\n  {'':42s} needs: {needs}")
    print("\nsolver adapters (simlab registry; `aces-simlab run` runs the ones marked 'runs here' locally):")
    for name, entry in registry.load_registry().items():
        val = (entry.get("validation") or {}).get("status")
        print(f"  {name:26s} validation={str(val):5s} image={entry.get('image')}  {runs_here(entry, opt)}")
    print("\noptional packages (not installed by aces-engineering): " + ", ".join(f"{k} {v}" for k, v in opt.items()))
    return 0


def cmd_parametric(argv: list) -> int:
    ap = argparse.ArgumentParser(prog="aces-gendesign parametric")
    ap.add_argument("problem", help="path/to/module.py:ClassName (a gendesign.parametric.ParametricProblem)")
    ap.add_argument("--trials", type=int, default=24)
    ap.add_argument("--out", required=True)
    ap.add_argument("--export", type=int, default=4, help="Pareto designs to rebuild and export")
    ap.add_argument("--kwargs", default="{}", help="JSON keyword arguments for the problem class")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    from gendesign.parametric import export_pareto, run_study
    from simlab.surrogate.loop import load_problem

    mod, cls = a.problem.rsplit(":", 1)
    base = os.path.dirname(os.path.abspath(mod)) if mod.endswith(".py") else os.getcwd()
    prob = load_problem(f"{os.path.basename(mod)}:{cls}", base, **json.loads(a.kwargs))
    study = run_study(prob, a.trials, a.out, seed=a.seed)
    print(json.dumps({"out": a.out, "pareto": len(study.best_trials),
                      "exported": export_pareto(prob, study, a.out, max_n=a.export)}, indent=2))
    return 0


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    prepare_env()
    cmd, rest = argv[0], argv[1:]
    if cmd == "list":
        return cmd_list()
    if cmd == "selftest":
        from .selftest import main as selftest_main

        return selftest_main(rest)
    if cmd == "topopt":
        from gendesign.fea3d import have_gpu
        from gendesign.topopt3d import main as topopt_main

        topopt_main(rest + ([] if have_gpu() or "--cpu" in rest else ["--cpu"]))
        return 0
    if cmd == "parametric":
        return cmd_parametric(rest)
    if cmd == "nozzle":
        from gendesign.nozzle import main as nozzle_main

        nozzle_main(rest)
        return 0
    if cmd == "fusion":
        from gendesign.fusion_bridge import main as fusion_main

        fusion_main(rest)
        return 0
    print(f"aces-gendesign: unknown command {cmd!r} (list, selftest, topopt, parametric, nozzle, fusion)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
