"""Redis/rq queue: cases become jobs; a job takes a GPU lease (idle per rig-state, not derated per
GRID CONTROL, released by the active mode, not leased by anyone) before it runs, refreshes the lease
while the case runs, and releases it afterwards. CPU-only solvers (vram_gb 0) go to the `cpu` queue.

    python -m simlab enqueue cases/exp_cantilever_volfrac.yaml --out out/queue [--docker]
    python -m simlab workers --n 4            # one rq worker per GPU slot (SimpleWorker on Windows)
    python -m simlab qstatus cantilever-volfrac-sweep
    python -m simlab capacity
"""
from __future__ import annotations

import datetime as dt
import json
import os
import platform
import subprocess
import sys
import time
import uuid
from typing import List, Optional

from rq import Queue
from rq.job import Job

from . import registry
from .capacity import gpu_table
from .leases import LeaseManager, get_redis, owner_id
from .run import REPO, build_case_command, collect_row, prepare_experiment, solver_stages, write_summary
from .spec import load

QUEUE_GPU = "sim"
QUEUE_CPU = "cpu"
EXP_KEY = "simlab:exp:"
CANCEL_KEY = "simlab:cancel:"


def _cancel_requested(r, job_id: str) -> bool:
    """True once sim_cancel (cancel_experiment) has flagged this started job."""
    return bool(r.exists(CANCEL_KEY + job_id))


def enqueue_experiment(spec_path: str, out_root: str, use_docker: bool = False, image: Optional[str] = None,
                       mlflow_uri: Optional[str] = None, queue_name: Optional[str] = None,
                       lease_wait_s: int = 6 * 3600, job_timeout_s: Optional[int] = None) -> dict:
    spec, exp_dir, cases = prepare_experiment(spec_path, out_root)
    needs_gpu = float(spec.resources.vram_gb) > 0
    qname = queue_name or (QUEUE_GPU if needs_gpu else QUEUE_CPU)
    stages = solver_stages(spec) or [(None, qname)]
    r = get_redis()
    # One experiment id, one spec: a second spec with the same id (two principals can each store one) would
    # overwrite this record and reuse the run directory, hiding the first experiment's jobs. Re-submitting the same
    # spec file is still allowed.
    prev_spec = r.hget(EXP_KEY + spec.id, "spec")
    if prev_spec:
        prev_path = prev_spec.decode() if isinstance(prev_spec, bytes) else str(prev_spec)
        if os.path.abspath(prev_path) != os.path.abspath(spec.spec_path):
            raise ValueError(f"experiment id {spec.id!r} is already used by {prev_path}; give this spec its own id")
    timeout = int(job_timeout_s or (spec.resources.wall_h * 3600 + lease_wait_s + 600))
    ids: List[str] = []
    stage_names = [st for st, _q in stages if st]
    for case in cases:
        run_dir = os.path.join(exp_dir, case["case_id"])
        os.makedirs(run_dir, exist_ok=True)
        case_path = os.path.join(run_dir, "case.json")
        with open(case_path, "w", encoding="utf-8") as f:
            json.dump(case, f, indent=2)
        prev = None
        tag = uuid.uuid4().hex[:6]
        for stage, sq in stages:
            # a stage runs on the queue its solver declares (mesh/post on cpu, solve on sim)
            stage_q = queue_name or (sq if stage else qname)
            stage_gpu = needs_gpu and (stage_q == QUEUE_GPU)
            job_id = f"{spec.id}--{case['case_id']}--{stage or 'run'}--{tag}"  # rq allows letters, digits, _ and - only
            job = Queue(stage_q, connection=r).enqueue(
                run_case_job, args=(spec.spec_path, case_path, run_dir, use_docker, image, mlflow_uri, stage_gpu,
                                    lease_wait_s, os.path.abspath(out_root), stage),
                job_id=job_id, job_timeout=timeout, result_ttl=14 * 86400, failure_ttl=14 * 86400,
                depends_on=prev,
                meta={"experiment": spec.id, "case_id": case["case_id"], "run_dir": run_dir, "solver": spec.solver,
                      "needs_gpu": stage_gpu, "state": "queued", "stage": stage,
                      "final": (stage is None) or (stage == stage_names[-1])})
            ids.append(job_id)
            prev = job
    rec = {"spec": spec.spec_path, "exp_dir": exp_dir, "queue": qname, "jobs": json.dumps(ids),
           "submitted_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "n": len(cases), "solver": spec.solver,
           "stages": json.dumps(stage_names)}
    r.hset(EXP_KEY + spec.id, mapping=rec)
    r.sadd("simlab:experiments", spec.id)
    return {"experiment": spec.id, "queue": qname, "jobs": ids, "exp_dir": exp_dir, "needs_gpu": needs_gpu,
            "stages": stage_names, "n_cases": len(cases)}


def run_case_job(spec_path: str, case_path: str, run_dir: str, use_docker: bool, image: Optional[str],
                 mlflow_uri: Optional[str], needs_gpu: bool, lease_wait_s: int, out_root: str,
                 stage: Optional[str] = None) -> dict:
    """Executed inside an rq worker."""
    from rq import get_current_job

    job = get_current_job()
    spec = load(spec_path)
    with open(case_path, "r", encoding="utf-8") as f:
        case = json.load(f)
    owner = owner_id(job.id if job else "local")
    lm = LeaseManager()
    gpu = None

    def note(**kw):
        if job is not None:
            job.meta.update(kw)
            job.meta["updated_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
            job.save_meta()

    if needs_gpu:
        deadline = time.time() + lease_wait_s
        poll = 5.0
        while gpu is None:
            table = gpu_table(leases=lm.held())
            gpu = lm.acquire_any(table["available"], owner)
            if gpu is None:
                if time.time() > deadline:
                    note(state="failed", reason="no GPU lease within wait")
                    raise RuntimeError(f"no GPU lease available within {lease_wait_s}s (mode {table['mode']}, pool {table['pool']})")
                note(state="waiting_for_gpu", mode=table["mode"], pool=table["pool"], available=table["available"])
                if job is not None and _cancel_requested(lm.r, job.id):
                    note(state="cancelled", reason="cancelled while waiting for a GPU lease")
                    raise RuntimeError("cancelled by request while waiting for a GPU lease")
                time.sleep(poll)
                poll = min(poll * 1.5, 60.0)
    note(state="running", gpu=gpu, owner=owner)
    t0 = time.time()
    rc = -1
    cancelled = False
    cname = container_name_for(job.id if job else f"{spec.id}-{case['case_id']}")
    try:
        cmd, env = build_case_command(spec, case_path, run_dir, gpu, use_docker, image, mlflow_uri, out_root,
                                      container_name=cname if use_docker else None, stage=stage)
        log_name = "worker.log" if not stage else "worker_" + stage + ".log"
        with open(os.path.join(run_dir, log_name), "w", encoding="utf-8") as log:
            proc = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
            last = time.time()
            while proc.poll() is None:
                time.sleep(2.0)
                if job is not None and lm.r.exists(CANCEL_KEY + job.id):
                    cancelled = True
                    if use_docker:
                        subprocess.run(["docker", "kill", cname], capture_output=True)
                    proc.terminate()
                    try:
                        proc.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    break
                if time.time() - last > 30:
                    if gpu is not None:
                        lm.refresh(gpu, owner)
                    note(state="running", elapsed_s=round(time.time() - t0, 1))
                    last = time.time()
            rc = proc.returncode if proc.returncode is not None else -9
    finally:
        if gpu is not None:
            lm.release(gpu, owner)
    row = collect_row(case, run_dir, gpu, rc, t0, spec)
    row["cancelled"] = cancelled
    if stage:
        row["stage"] = stage
    note(state="cancelled" if cancelled else ("finished" if rc == 0 else "failed"), rc=rc, elapsed_s=row["wall_s_total"])
    if cancelled:
        raise RuntimeError("cancelled by request")
    if rc != 0:
        raise RuntimeError(("stage " + stage + " " if stage else "") + "exited with " + str(rc) + "; see " + run_dir)
    return row


def container_name_for(job_id: str) -> str:
    return "simlab-" + "".join(c if c.isalnum() or c in "-_." else "-" for c in job_id)[:60]


def experiment_status(exp_id: str) -> dict:
    r = get_redis()
    rec = r.hgetall(EXP_KEY + exp_id)
    if not rec:
        raise KeyError(f"unknown experiment {exp_id!r}")
    rec = {k.decode(): v.decode() for k, v in rec.items()}
    ids = json.loads(rec["jobs"])
    jobs = []
    for jid in ids:
        try:
            j = Job.fetch(jid, connection=r)
            status = j.get_status(refresh=True)
            status = getattr(status, "value", str(status))  # rq returns a str-Enum; keep plain strings for JSON/MCP
            jobs.append({"job_id": jid, "status": status, "meta": j.meta,
                         "result": j.result if status == "finished" else None,
                         "error": (j.latest_result().exc_string[-800:] if j.latest_result() and j.latest_result().exc_string else None)})
        except Exception as exc:
            jobs.append({"job_id": jid, "status": "unknown", "error": str(exc)})
    counts = {}
    for j in jobs:
        counts[j["status"]] = counts.get(j["status"], 0) + 1
    return {"experiment": exp_id, "queue": rec.get("queue"), "exp_dir": rec.get("exp_dir"), "spec": rec.get("spec"), "submitted_utc": rec.get("submitted_utc"),
            "solver": rec.get("solver"), "n": int(rec.get("n", 0)), "counts": counts, "jobs": jobs,
            "done": all(j["status"] in ("finished", "failed", "canceled", "stopped", "unknown") for j in jobs)}


def experiment_results(exp_id: str, case_id: Optional[str] = None) -> dict:
    st = experiment_status(exp_id)
    rows = []
    for j in st["jobs"]:
        cid = j.get("meta", {}).get("case_id")
        if case_id and cid != case_id:
            continue
        if not j.get("meta", {}).get("final", True):
            continue
        run_dir = j.get("meta", {}).get("run_dir")
        res = None
        if run_dir and os.path.exists(os.path.join(run_dir, "results.json")):
            with open(os.path.join(run_dir, "results.json"), "r", encoding="utf-8") as f:
                res = json.load(f)
        rows.append({"case_id": cid, "status": j["status"], "run_dir": run_dir, "results": res,
                     "artifacts": (res or {}).get("artifacts", [])})
    if st["done"] and st["exp_dir"]:
        try:
            spec = load(st["spec"]) if (st.get("spec")) else None
        except Exception:
            spec = None
        finished = [j["result"] for j in st["jobs"] if j.get("result") and j.get("meta", {}).get("final", True)]
        if spec and finished:
            write_summary(spec, st["exp_dir"], finished)
    return {"experiment": exp_id, "done": st["done"], "counts": st["counts"], "cases": rows}


def cancel_experiment(exp_id: str) -> dict:
    r = get_redis()
    st = experiment_status(exp_id)
    out = {}
    for j in st["jobs"]:
        try:
            job = Job.fetch(j["job_id"], connection=r)
            status = getattr(job.get_status(), "value", str(job.get_status()))
            if status in ("queued", "deferred", "scheduled"):
                job.cancel()
                out[j["job_id"]] = "canceled"
            elif status == "started":
                # the job's own loop sees this flag, kills its container/subprocess and releases the lease
                # (works for SimpleWorker on Windows and the forking worker on Linux alike)
                r.set(CANCEL_KEY + job.id, "1", ex=86400)
                out[j["job_id"]] = "stop requested"
            else:
                out[j["job_id"]] = status
        except Exception as exc:
            out[j["job_id"]] = f"error: {exc}"
    return out


def list_experiments() -> List[str]:
    return sorted(x.decode() for x in get_redis().smembers("simlab:experiments"))


def start_worker(queues: List[str], burst: bool = False, name: Optional[str] = None) -> None:
    r = get_redis()
    qs = [Queue(q, connection=r) for q in queues]
    if platform.system() == "Windows":
        from rq.worker import SimpleWorker as W
    else:
        from rq import Worker as W
    w = W(qs, connection=r, name=name)
    w.work(burst=burst, with_scheduler=False)


def spawn_workers(n: int, queues: List[str], burst: bool = False) -> List[subprocess.Popen]:
    procs = []
    for i in range(n):
        cmd = [sys.executable, "-m", "simlab", "worker", "--queues", *queues] + (["--burst"] if burst else [])
        procs.append(subprocess.Popen(cmd, cwd=REPO, env=dict(os.environ, PYTHONPATH=REPO)))
    return procs
