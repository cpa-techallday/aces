"""MLflow tracking that degrades to a no-op when MLflow or the server is unavailable."""
from __future__ import annotations

import os
import warnings
from typing import Any, Dict, Optional


class Tracker:
    def __init__(self, uri: Optional[str], experiment: str, enabled: bool = True):
        self.ok = False
        self.mlflow = None
        self.run = None
        uri = uri or os.environ.get("MLFLOW_TRACKING_URI")
        if not enabled or not uri:
            return
        try:
            os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
            import mlflow

            mlflow.set_tracking_uri(uri)
            mlflow.set_experiment(experiment)
            self.mlflow = mlflow
            self.ok = True
        except Exception as exc:  # server down, package missing: keep running, say so once
            warnings.warn(f"MLflow tracking disabled: {exc}")

    def start(self, run_name: str, tags: Optional[Dict[str, str]] = None):
        if self.ok:
            try:
                self.run = self.mlflow.start_run(run_name=run_name, tags=tags or {})
            except Exception as exc:
                warnings.warn(f"MLflow start_run failed: {exc}")
                self.ok = False
        return self

    def log_params(self, params: Dict[str, Any]) -> None:
        if self.ok:
            flat = {k: (v if isinstance(v, (int, float, str, bool)) else str(v)) for k, v in params.items()}
            try:
                self.mlflow.log_params({k: str(v)[:500] for k, v in flat.items()})
            except Exception as exc:
                warnings.warn(f"MLflow log_params failed: {exc}")

    def log_metrics(self, metrics: Dict[str, Any], step: Optional[int] = None) -> None:
        if self.ok:
            nums = {k: float(v) for k, v in metrics.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
            try:
                if nums:
                    self.mlflow.log_metrics(nums, step=step)
            except Exception as exc:
                warnings.warn(f"MLflow log_metrics failed: {exc}")

    def log_artifact(self, path: str) -> None:
        if self.ok and os.path.exists(path):
            try:
                if os.path.isdir(path):
                    self.mlflow.log_artifacts(path)
                else:
                    self.mlflow.log_artifact(path)
            except Exception as exc:
                warnings.warn(f"MLflow log_artifact failed: {exc}")

    def set_tags(self, tags: Dict[str, str]) -> None:
        if self.ok:
            try:
                self.mlflow.set_tags(tags)
            except Exception:
                pass

    def end(self, status: str = "FINISHED") -> Optional[str]:
        run_id = None
        if self.ok and self.run is not None:
            try:
                run_id = self.run.info.run_id
                self.mlflow.end_run(status=status)
            except Exception:
                pass
        return run_id
