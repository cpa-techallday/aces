"""R&D evidence manifest for a run: what was run, on what, from which inputs, with what result."""
from __future__ import annotations

import datetime as dt
import json
import os
import platform
import socket
import subprocess
import sys
from typing import Any, Dict, Iterable, Optional

from .spec import sha256_file


def gpu_info() -> list:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=index,name,driver_version,memory.total,uuid,serial,pci.bus_id",
                              "--format=csv,noheader"], capture_output=True, text=True, timeout=20)
        if out.returncode != 0:
            return []
        rows = []
        for line in out.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 5:
                rows.append({"index": int(parts[0]), "name": parts[1], "driver": parts[2],
                             "memory": parts[3], "uuid": parts[4],
                             "serial": parts[5] if len(parts) > 5 else None,
                             "pci_bus_id": parts[6] if len(parts) > 6 else None})
        return rows
    except Exception:
        return []


def package_versions() -> Dict[str, str]:
    out = {"python": sys.version.split()[0]}
    for m in ("numpy", "scipy", "cupy", "optuna", "build123d", "trimesh", "skimage", "pyfr", "mlflow", "mpi4py"):
        try:
            mod = __import__(m)
            out[m] = str(getattr(mod, "__version__", "?"))
        except Exception:
            pass
    return out


def git_rev(path: str) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def environment() -> Dict[str, Any]:
    return {
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "in_container": os.path.exists("/.dockerenv"),
        "image": os.environ.get("SIMLAB_IMAGE"),
        "image_digest": os.environ.get("SIMLAB_IMAGE_DIGEST"),
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "gpus": gpu_info(),
        "packages": package_versions(),
    }


def manifest(spec, case: dict, run_dir: str, inputs: Iterable[str], started: dt.datetime,
             finished: dt.datetime, status: str, results: Optional[dict], error: Optional[str] = None) -> dict:
    hashes = {}
    for p in inputs:
        if p and os.path.isfile(p):
            hashes[os.path.basename(p)] = sha256_file(p)
    m = {
        "schema": "simlab-evidence/1",
        "experiment": {"id": spec.id, "project": spec.project, "owner": spec.owner, "physics": spec.physics,
                       "solver": spec.solver, "description": spec.description, "spec_sha256": spec.spec_sha256,
                       "hypothesis": getattr(spec, "hypothesis", None), "expected": getattr(spec, "expected", None),
                       "rd_activity": getattr(spec, "rd_activity", None), "company": getattr(spec, "company", None),
                       "tags": spec.tags},
        "case": case,
        "inputs_sha256": hashes,
        "geometry": spec.geometry.model_dump(),
        "environment": environment(),
        "git_rev": git_rev(spec.base_dir),
        "started_utc": started.isoformat(),
        "finished_utc": finished.isoformat(),
        "wall_s": (finished - started).total_seconds(),
        "status": status,
        "error": error,
        "results": results,
        "run_dir": os.path.abspath(run_dir),
    }
    with open(os.path.join(run_dir, "evidence.json"), "w", encoding="utf-8") as f:
        json.dump(m, f, indent=2, default=str)
    return m
