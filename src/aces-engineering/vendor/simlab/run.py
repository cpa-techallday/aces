"""Experiment runner: fan the cases out, one worker process per GPU slot, locally or in Docker.

`build_case_command` is shared with the queue (Phase 3): it is the single place that knows how to
turn (spec, case, run_dir, gpu) into a subprocess, local python or `docker run --gpus device=n`.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple

from . import registry
from .spec import Experiment, load

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _posix(p: str) -> str:
    return p.replace("\\", "/")


_DIGESTS: dict = {}


def image_digest(image: str) -> Optional[str]:
    """Content id of a local image (sha256 of its config), plus the registry digest when it has one.
    Cached per process; None when docker is not reachable."""
    if image in _DIGESTS:
        return _DIGESTS[image]
    digest = None
    try:
        out = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}|{{join .RepoDigests \",\"}}", image],
                             capture_output=True, text=True, timeout=30)
        if out.returncode == 0:
            iid, _, repo = out.stdout.strip().partition("|")
            digest = iid + ((" " + repo) if repo else "")
    except Exception:
        digest = None
    _DIGESTS[image] = digest
    return digest


def build_case_command(spec: Experiment, case_path: str, run_dir: str, gpu: Optional[int], use_docker: bool,
                       image: Optional[str], mlflow_uri: Optional[str], out_root: Optional[str] = None,
                       container_name: Optional[str] = None, stage: Optional[str] = None) -> Tuple[List[str], dict]:
    """Return (argv, env) for one case. In docker mode the repo is mounted at /work and the output
    root at /work/out; SIMLAB_HOST_REPO / SIMLAB_HOST_OUT give the host paths when the caller itself
    runs inside a container that talks to the host's docker socket (sibling containers)."""
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", REPO)
    uri = mlflow_uri or spec.tracking.uri or env.get("MLFLOW_TRACKING_URI")
    if uri:
        env["MLFLOW_TRACKING_URI"] = uri
    out_root = os.path.abspath(out_root or os.path.dirname(os.path.dirname(os.path.abspath(run_dir))))
    stage_args = ["--stage", stage] if stage else []
    if not use_docker:
        if gpu is not None:
            env["CUDA_VISIBLE_DEVICES"] = str(gpu)
        return [sys.executable, "-m", "simlab.worker", spec.spec_path, case_path, run_dir] + stage_args, env
    image = image or registry.get(spec.solver).get("image")
    if not image:
        raise ValueError(f"solver {spec.solver} has no registered image")
    if env.get("MLFLOW_TRACKING_URI", "").startswith(("http://127.0.0.1", "http://localhost")):
        env["MLFLOW_TRACKING_URI"] = env["MLFLOW_TRACKING_URI"].replace("127.0.0.1", "host.docker.internal").replace("localhost", "host.docker.internal")
    host_repo = env.get("SIMLAB_HOST_REPO", REPO)
    host_out = env.get("SIMLAB_HOST_OUT", out_root)
    rel = lambda p: _posix(os.path.relpath(os.path.abspath(p), out_root))
    spec_in = _posix(os.path.relpath(spec.spec_path, REPO))
    # On the rig the shield firewall drops FORWARD traffic, so a sibling on the default bridge
    # cannot reach MLflow through the published host port; join the compose network instead and
    # talk to the mlflow service by name.
    sib_net = env.get("SIMLAB_SIBLING_NETWORK")
    if sib_net and env.get("SIMLAB_MLFLOW_URI_INNER"):
        env["MLFLOW_TRACKING_URI"] = env["SIMLAB_MLFLOW_URI_INNER"]
    cmd = ["docker", "run", "--rm"]
    if container_name:
        cmd += ["--name", container_name]
    if gpu is not None:
        cmd += ["--gpus", f"device={gpu}"]
    cmd += ["-v", f"{_posix(host_repo)}:/work", "-v", f"{_posix(host_out)}:/work/out", "-w", "/work",
            "-e", f"SIMLAB_IMAGE={image}", "-e", f"SIMLAB_IMAGE_DIGEST={image_digest(image) or ''}",
            "-e", f"MLFLOW_TRACKING_URI={env.get('MLFLOW_TRACKING_URI', '')}",
            "--add-host", "host.docker.internal:host-gateway"]
    if sib_net:
        cmd += ["--network", sib_net]
    cmd += ["--entrypoint", "python3",
            image, "-m", "simlab.worker", spec_in, "out/" + rel(case_path), "out/" + rel(run_dir)] + stage_args
    return cmd, env


def solver_stages(spec: Experiment) -> List[Tuple[str, str]]:
    """[(stage, queue)] for staged solvers, [] otherwise."""
    try:
        ad = registry.adapter(spec.solver)
    except Exception:
        return []
    st = getattr(ad, "stages", None)
    return list(st.items()) if st else []


def collect_row(case: dict, run_dir: str, gpu, rc: int, t0: float, spec: Experiment) -> dict:
    res = {}
    rp = os.path.join(run_dir, "results.json")
    if os.path.exists(rp):
        with open(rp, "r", encoding="utf-8") as f:
            res = {k: v for k, v in json.load(f).items() if k != "artifacts"}
    return {"case_id": case["case_id"], "gpu": gpu, "rc": rc, "wall_s_total": round(time.time() - t0, 1),
            **{k: v for k, v in case.items() if k != "case_id"}, **res}


def prepare_experiment(spec_path: str, out_root: str) -> Tuple[Experiment, str, List[dict]]:
    spec = load(spec_path)
    registry.check_physics(spec.solver, spec.physics)
    out_root = os.path.abspath(out_root)
    exp_dir = os.path.join(out_root, spec.id)
    os.makedirs(exp_dir, exist_ok=True)
    cases = spec.cases()
    with open(os.path.join(exp_dir, "cases.json"), "w", encoding="utf-8") as f:
        json.dump(cases, f, indent=2)
    with open(os.path.join(exp_dir, "spec.resolved.json"), "w", encoding="utf-8") as f:
        json.dump(spec.model_dump(), f, indent=2, default=str)
    return spec, exp_dir, cases


def write_summary(spec: Experiment, exp_dir: str, rows: List[dict]) -> dict:
    keys: List[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(os.path.join(exp_dir, "summary.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in keys})
    summary = {"experiment": spec.id, "solver": spec.solver, "n_cases": len(rows),
               "n_failed": sum(1 for r in rows if r.get("rc") != 0), "rows": rows}
    with open(os.path.join(exp_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    return summary


def run_experiment(spec_path: str, out_root: str, gpus: Optional[List[int]] = None, use_docker: bool = False,
                   parallel: int = 1, image: Optional[str] = None, mlflow_uri: Optional[str] = None) -> dict:
    spec, exp_dir, cases = prepare_experiment(spec_path, out_root)
    out_root = os.path.abspath(out_root)
    stages = solver_stages(spec)
    slots: List[Optional[int]] = list(gpus) if gpus else [None] * max(1, parallel)
    free = list(slots)
    lock = threading.Lock()
    print(f"[simlab] {spec.id}: {len(cases)} case(s), solver {spec.solver}, "
          f"{'docker ' + (image or registry.get(spec.solver).get('image', '?')) if use_docker else 'local python'}, slots {slots}", flush=True)

    def one(case: dict) -> dict:
        with lock:
            gpu = free.pop(0)
        cid = case["case_id"]
        run_dir = os.path.join(exp_dir, cid)
        os.makedirs(run_dir, exist_ok=True)
        case_path = os.path.join(run_dir, "case.json")
        with open(case_path, "w", encoding="utf-8") as f:
            json.dump(case, f, indent=2)
        t0 = time.time()
        try:
            stage_list = [st for st, _q in stages] or [None]
            with open(os.path.join(run_dir, "worker.log"), "w", encoding="utf-8") as log:
                rc = 0
                for st in stage_list:
                    cmd, env = build_case_command(spec, case_path, run_dir, gpu, use_docker, image, mlflow_uri, out_root, stage=st)
                    rc = subprocess.run(cmd, cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT).returncode
                    if rc != 0:
                        break
        finally:
            with lock:
                free.append(gpu)
        row = collect_row(case, run_dir, gpu, rc, t0, spec)
        print(f"[simlab]   {cid} rc={rc} gpu={gpu} {row['wall_s_total']}s " +
              " ".join(f"{k}={v:.5g}" for k, v in row.items() if isinstance(v, (int, float)) and k in spec.outputs), flush=True)
        return row

    with ThreadPoolExecutor(max_workers=len(slots)) as ex:
        rows = list(ex.map(one, cases))
    summary = write_summary(spec, exp_dir, rows)
    print(f"[simlab] done: {summary['n_cases'] - summary['n_failed']}/{summary['n_cases']} ok -> {exp_dir}", flush=True)
    return summary
