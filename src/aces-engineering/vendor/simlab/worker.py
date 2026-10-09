"""One case, one process (one GPU): run the adapter (or one of its stages), write results.json +
evidence.json, log to MLflow.

    python -m simlab.worker <spec.yaml> <case.json> <run_dir> [--stage mesh|solve|post]

Staged adapters expose `stages` (ordered dict stage -> queue) and `run_stage(stage, case, spec, run_dir)`.
Each stage writes stage_<name>.json; the last stage (or an unstaged run) writes results.json,
evidence.json (with per-stage timings) and status.json, and makes the MLflow run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import traceback

from . import registry
from .evidence import manifest
from .spec import load
from .tracking import Tracker


def stages_of(adapter) -> list:
    st = getattr(adapter, "stages", None)
    return list(st) if st else []


def run_case(spec_path: str, case_path: str, run_dir: str, stage: str | None = None) -> int:
    spec = load(spec_path)
    with open(case_path, "r", encoding="utf-8") as f:
        case = json.load(f)
    os.makedirs(run_dir, exist_ok=True)
    adapter = registry.adapter(spec.solver)
    stages = stages_of(adapter)
    final = stage is None or not stages or stage == stages[-1]
    os.environ["SIMLAB_PROGRESS_FILE"] = os.path.join(run_dir, "progress.json")   # beacon for the console
    try:
        from gendesign import progress as _progress

        _progress.report(stage or "run", min_interval_s=0.0, solver=spec.solver, case_id=case.get("case_id"))
    except Exception:
        pass
    started = dt.datetime.now(dt.timezone.utc)
    status, results, error = "FINISHED", None, None
    try:
        if stage is not None and stages:
            if stage not in stages:
                raise ValueError(f"{spec.solver} has stages {stages}, not {stage!r}")
            results = adapter.run_stage(stage, case, spec, run_dir)
        else:
            results = adapter.run(case, spec, run_dir)
    except Exception:
        status, error = "FAILED", traceback.format_exc()
        print(error, file=sys.stderr)
    finished = dt.datetime.now(dt.timezone.utc)
    if stage is not None and stages:
        with open(os.path.join(run_dir, f"stage_{stage}.json"), "w", encoding="utf-8") as f:
            json.dump({"stage": stage, "status": status, "started_utc": started.isoformat(), "finished_utc": finished.isoformat(),
                       "wall_s": (finished - started).total_seconds(), "error": error,
                       "result": results if isinstance(results, dict) and "artifacts" not in results else None}, f, indent=2, default=str)
        if not final:
            return 0 if status == "FINISHED" else 1
    # ---- final: results, evidence, tracking ----
    inputs = [spec.spec_path] + list(adapter.inputs(case, spec))
    if spec.geometry.ref:
        inputs.append(spec.resolve(spec.geometry.ref))
    stage_records = []
    for s in stages:
        p = os.path.join(run_dir, f"stage_{s}.json")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                rec = json.load(f)
            rec.pop("result", None)
            stage_records.append(rec)
            if rec.get("status") != "FINISHED" and status == "FINISHED":
                status, error = "FAILED", f"stage {s} failed: {rec.get('error')}"
    if stage_records:
        started = dt.datetime.fromisoformat(stage_records[0]["started_utc"])
    tracker = Tracker(spec.tracking.uri, spec.tracking.experiment or spec.project)
    tags = {"experiment_id": spec.id, "project": spec.project, "owner": spec.owner, "physics": spec.physics,
            "solver": spec.solver, "case_id": case.get("case_id", "c000"), **spec.tags}
    tracker.start(f"{spec.id}/{case.get('case_id', 'c000')}", tags)
    tracker.log_params({k: v for k, v in case.items() if k != "case_id"})
    if results is not None and status == "FINISHED":
        with open(os.path.join(run_dir, "results.json"), "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str)
        tracker.log_metrics({k: v for k, v in results.items() if k != "artifacts"})
        for a in results.get("artifacts", []):
            tracker.log_artifact(a)
    if spec.evidence:
        ev = manifest(spec, case, run_dir, inputs, started, finished, status, results if status == "FINISHED" else None, error)
        if stage_records:
            ev["stages"] = stage_records
            with open(os.path.join(run_dir, "evidence.json"), "w", encoding="utf-8") as f:
                json.dump(ev, f, indent=2, default=str)
        tracker.log_artifact(os.path.join(run_dir, "evidence.json"))
        tracker.set_tags({"gpu": ",".join(g["name"] for g in ev["environment"]["gpus"][:1]) or "none"})
    run_id = tracker.end(status)
    try:
        _progress.finish("done" if status == "FINISHED" else "failed", status=status)
    except Exception:
        pass
    with open(os.path.join(run_dir, "status.json"), "w", encoding="utf-8") as f:
        json.dump({"status": status, "mlflow_run_id": run_id, "wall_s": (finished - started).total_seconds(),
                   "error": error, "stages": [r["stage"] for r in stage_records]}, f, indent=2)
    return 0 if status == "FINISHED" else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("spec")
    ap.add_argument("case")
    ap.add_argument("run_dir")
    ap.add_argument("--stage", default=None)
    a = ap.parse_args(argv)
    return run_case(a.spec, a.case, a.run_dir, a.stage)


if __name__ == "__main__":
    raise SystemExit(main())
