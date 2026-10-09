"""simlab: the experiment runtime (Phase 2 of CPA-SIM-RUNTIME-001).

experiment.yaml -> validated spec -> cases (sweep fan-out) -> one worker process per GPU
-> results.json + evidence.json per run -> MLflow (params, metrics, artifacts) -> summary.

    python -m simlab run cases/exp_cantilever_volfrac.yaml --out out/exp --gpus 0
    python -m simlab run cases/exp_cylinder.yaml --out out/exp --gpus 0 --docker
    python -m simlab solvers
    python -m simlab summary out/exp/<experiment id>
"""

__version__ = "0.2.0"
