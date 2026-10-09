"""aces golden: the generative sweep. A seeded Latin hypercube over the generator's variables; every design is
generated, skinned and solved for real (case.run_cases), then tabulated.

CasePool is a spawn-context process pool whose workers pin BLAS to one thread, ignore Ctrl-C (the main process
cancels them) and never print. With one BLAS thread everywhere a design is bit-identical in a worker and inline, so
sweep.csv (which carries no wall times) is byte-identical for any --jobs on the same machine."""
from __future__ import annotations

import contextlib
import csv
import io
import os
import statistics
import time
import warnings

from . import SCHEMA, read_json, sha256_bytes, sha256_file

VENDORED_WARNING = r".*axes.*should not be.*None.*"
TAIL_COLUMNS = ("mass_kg", "mass_voxel_kg", "compliance_Nmm", "umax_mm", "vm_max_MPa", "vm_p99_MPa", "vm_p95_MPa",
                "pcg_iters", "pcg_relres", "converged", "solid_cells", "faces", "genus", "islands_removed", "device",
                "h_fea_mm", "grade")


def sample(variables: dict[str, list[float]], n: int, seed: int) -> list[dict]:
    """n designs from a Latin hypercube (random-cd optimised, numpy default_rng(seed)), scaled to the bounds, rounded
    to 6 decimals, variables in spec order."""
    import numpy as np
    from scipy.stats import qmc

    names = list(variables)
    u = qmc.LatinHypercube(d=len(names), optimization="random-cd", rng=np.random.default_rng(int(seed))).random(int(n))
    out = []
    for i in range(int(n)):
        row = {}
        for j, k in enumerate(names):
            lo, hi = float(variables[k][0]), float(variables[k][1])
            row[k] = float(round(lo + u[i, j] * (hi - lo), 6))
        out.append(row)
    return out


# --------------------------------------------------------------------------------------------------------------- pool

def _worker_init() -> None:
    import signal

    with contextlib.suppress(ValueError, OSError):
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[k] = "1"
    os.environ["MPLBACKEND"] = "Agg"
    os.environ["MLFLOW_DISABLE_AGENT_HINT"] = "1"
    with contextlib.suppress(Exception):
        import aces_engineering

        aces_engineering.prepare_env()
    warnings.filterwarnings("ignore", category=DeprecationWarning, message=VENDORED_WARNING)


def _call(fn, job):
    from threadpoolctl import threadpool_limits

    with threadpool_limits(1):
        return fn(job)


class CasePool:
    """ProcessPoolExecutor(max_workers=jobs, spawn context) for case jobs; every task body runs under
    threadpool_limits(1)."""

    def __init__(self, jobs: int):
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor

        self.jobs = max(1, int(jobs))
        self._ex = ProcessPoolExecutor(max_workers=self.jobs, mp_context=multiprocessing.get_context("spawn"),
                                       initializer=_worker_init)
        self._closed = False

    def submit(self, fn, job):
        return self._ex.submit(_call, fn, job)

    def map(self, fn, jobs, *, on_done=None) -> list:
        """Results in input order; on_done(index, result) is called in this thread as each finishes."""
        from concurrent.futures import as_completed

        jobs = list(jobs)
        futs = {self.submit(fn, j): i for i, j in enumerate(jobs)}
        out: list = [None] * len(jobs)
        for f in as_completed(futs):
            i = futs[f]
            out[i] = f.result()
            if on_done is not None:
                on_done(i, out[i])
        return out

    def cancel(self) -> None:
        """Drop queued work and terminate the workers (Ctrl-C)."""
        if self._closed:
            return
        self._closed = True
        procs = list((getattr(self._ex, "_processes", None) or {}).values())
        with contextlib.suppress(Exception):
            self._ex.shutdown(wait=False, cancel_futures=True)
        for p in procs:
            with contextlib.suppress(Exception):
                if p.is_alive():
                    p.terminate()
        for p in procs:
            with contextlib.suppress(Exception):
                p.join(timeout=5)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._ex.shutdown(wait=True)

    def __enter__(self) -> "CasePool":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self.cancel()
        else:
            self.close()
        return False


# -------------------------------------------------------------------------------------------------------------- sweep

def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(float(v))
    return str(v)


def write_table(run, eff: dict, outcomes: list[dict]) -> dict:
    """sweep.csv (deterministic: no wall times, LF, floats repr) and sweep.json for the sweep outcomes."""
    variables = list(eff["derived"]["variables"])
    columns = ["case_id", "status", "design_status"] + variables + list(TAIL_COLUMNS)
    cases = {c["case_id"]: c for c in (run.cases() or [])}
    rows = []
    for oc in outcomes:
        cid = oc["case_id"]
        case = cases.get(cid) or read_json(run.path(cid, "case.json"), {}) or {}
        res = oc.get("results") or {}
        skin = (oc.get("diagnostics") or {}).get("skin") or {}
        row = {"case_id": cid, "status": oc["status"], "design_status": oc["design_status"]}
        for k in variables:
            row[k] = case.get(k)
        for k in ("mass_kg", "mass_voxel_kg", "compliance_Nmm", "umax_mm", "vm_max_MPa", "vm_p99_MPa", "vm_p95_MPa",
                  "pcg_iters", "pcg_relres", "converged"):
            row[k] = res.get(k)
        row["solid_cells"] = (res.get("voxel") or {}).get("solid_cells")
        row["faces"] = skin.get("faces")
        row["genus"] = skin.get("genus")
        row["islands_removed"] = skin.get("islands_removed")
        row["device"] = case.get("device")
        row["h_fea_mm"] = case.get("h_fea_mm")
        row["grade"] = res.get("grade")
        rows.append(row)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for row in rows:
        w.writerow([_cell(row[c]) for c in columns])
    text = buf.getvalue()
    run.write_text("sweep.csv", text)
    csv_sha = sha256_bytes(text.encode("utf-8"))
    by_id = {oc["case_id"]: oc for oc in outcomes}
    json_rows = []
    for row in rows:
        oc = by_id[row["case_id"]]
        res = oc.get("results") or {}
        json_rows.append(dict(row, wall_s=oc.get("wall_s"), wall_s_generate=res.get("wall_s_generate"),
                              wall_s_solve=res.get("wall_s_solve"), error=oc.get("error")))
    doc = {"schema": SCHEMA["sweep"],
           "sampler": {"kind": "lhs", "optimization": "random-cd", "seed": int(eff["derived"]["seed"]),
                       "n": int(eff["derived"]["n"])},
           "variables": eff["derived"]["variables"], "outputs": eff["derived"]["outputs"], "columns": columns,
           "csv_sha256": csv_sha, "n": len(rows), "n_ok": sum(1 for r in rows if r["status"] == "FINISHED"),
           "rows": json_rows}
    run.write_json("sweep.json", doc)
    return {"csv_sha256": csv_sha, "json_sha256": sha256_file(run.path("sweep.json")), "rows": json_rows,
            "columns": columns, "n": doc["n"], "n_ok": doc["n_ok"]}


def run_sweep(run, eff: dict, domain: dict, experiment, pool, plan: dict, progress) -> dict:
    """Sample, run every design for real (CPU cases on `pool` when given), tabulate. Returns {"rows", "n", "n_ok",
    "csv_sha256", "json_sha256", "wall_s", "wall_s_sum", "median_solve_s"}."""
    from .case import run_cases

    d = eff["derived"]
    t0 = time.time()
    points = sample(d["variables"], d["n"], d["seed"])
    device = plan["sweep"]["device"]
    cases = [{"case_id": f"d{i:03d}", "role": "sweep", **p, "h_gen_mm": float(d["gen_mm"]), "h_fea_mm": float(d["fea_mm"]),
              "device": device, "backend": "adapter", "source_case": None, "reuse_mask": False, "round": None,
              "level": None, "seed": int(d["seed"])} for i, p in enumerate(points)]
    bar = progress.designs(len(cases), "sweep")
    try:
        outcomes = run_cases(run, eff, domain, experiment, cases, pool=pool, progress=progress, kind="design", bar=bar)
    finally:
        bar.close()
    table = write_table(run, eff, outcomes)
    solves = [oc["results"]["wall_s_solve"] for oc in outcomes
              if oc["status"] == "FINISHED" and (oc.get("results") or {}).get("wall_s_solve") is not None]
    return {"rows": table["rows"], "n": table["n"], "n_ok": table["n_ok"], "csv_sha256": table["csv_sha256"],
            "json_sha256": table["json_sha256"], "wall_s": time.time() - t0,
            "wall_s_sum": float(sum(oc["wall_s"] or 0.0 for oc in outcomes)),
            "median_solve_s": float(statistics.median(solves)) if solves else None}
