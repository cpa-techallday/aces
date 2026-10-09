"""aces golden: one case = one design (generate -> skin -> part.stl -> real solve) or one mesh solve (a finer
independent check of a part, or a device cross-check on the identical mask).

Jobs are plain picklable dicts so they run in spawned workers; run_design_case and run_mesh_case never raise for a
design failure (any exception becomes a FAILED outcome with design_status "error" and the traceback). Records are
written in the main process as each case finishes (contemporaneous), with the vendored simlab.evidence.manifest, whose
package_versions() would cost every spawned worker a build123d import. run_cases dispatches CPU cases to the pool when
one is given (else inline under one BLAS thread), runs GPU cases in this process, reports every finished case through
the design bar or one progress line, and keeps the in-flight progress.json beacons visible to the heartbeat."""
from __future__ import annotations

import contextlib
import datetime as _dt
import os
import threading
import time
import traceback
import warnings
from pathlib import Path
from typing import Callable

from . import GRADE_PREVIEW, METRICS, SCHEMA, clean, dump_json, utc_now

DESIGN_ROLES = ("sweep", "verify")
ROW_METRICS = ("mass_kg", "umax_mm", "vm_p99_MPa", "vm_max_MPa", "compliance_Nmm")
VENDORED_WARNING = r".*axes.*should not be.*None.*"   # gendesign.filters on NumPy 2 (fix upstream, never in vendor/)
_NOT_ARTIFACTS = {"progress.json", "case.json", "results.json", "status.json", "evidence.json"}


# ---------------------------------------------------------------------------------------------------------------- jobs

def make_job(run, eff: dict, domain: dict, case: dict) -> dict:
    """The picklable job for one case (section 10.5). Verify and level-1 check cases get the post files; sweep cases
    get a thumbnail; parity and level-2 check cases neither."""
    role, src = case["role"], case.get("source_case")
    return {
        "run_root": str(Path(run.root)), "case_id": case["case_id"], "case": dict(case), "eff": eff, "domain": domain,
        "post": role == "verify" or (role == "check" and case.get("level") == 1),
        "thumb": role == "sweep",
        "source_stl": str(Path(run.root) / src / "part.stl") if src else None,
        "h_mm": float(case["h_fea_mm"]),
        "reuse_mask_from": str(Path(run.root) / src) if (src and case.get("reuse_mask")) else None,
    }


class _Stages:
    def __init__(self) -> None:
        self.records: list[dict] = []

    @contextlib.contextmanager
    def stage(self, name: str, device: str):
        rec = {"stage": name, "status": "RUNNING", "started_utc": utc_now(), "finished_utc": None, "wall_s": None,
               "device": device}
        self.records.append(rec)
        t0 = time.time()
        try:
            yield rec
        except BaseException:
            rec["status"] = "FAILED"
            raise
        else:
            if rec["status"] == "RUNNING":
                rec["status"] = "FINISHED"
        finally:
            rec["finished_utc"] = utc_now()
            rec["wall_s"] = time.time() - t0

    def wall(self, name: str):
        for r in self.records:
            if r["stage"] == name:
                return r["wall_s"]
        return None


def _new_outcome(case: dict) -> dict:
    return {"case_id": case["case_id"], "role": case["role"], "status": "FAILED", "design_status": "error",
            "error": None, "results": None, "diagnostics": {"generator": None, "skin": None, "checks": None},
            "started_utc": None, "finished_utc": None, "wall_s": None, "stages": []}


def _reset_beacon() -> None:
    try:
        from gendesign import progress as _p

        _p._STATE.clear()
        _p._LAST["t"] = 0.0
    except Exception:
        pass


def _artifacts(case_dir: Path) -> list[str]:
    out = []
    for p in sorted(case_dir.rglob("*")):
        if p.is_file() and p.name not in _NOT_ARTIFACTS and not p.name.endswith(".tmp"):
            out.append(str(p.resolve()))
    return out


def _run(job: dict, body: Callable) -> dict:
    case = job["case"]
    out = _new_outcome(case)
    case_dir = Path(job["run_root"]) / job["case_id"]
    case_dir.mkdir(parents=True, exist_ok=True)
    prev = os.environ.get("SIMLAB_PROGRESS_FILE")
    os.environ["SIMLAB_PROGRESS_FILE"] = str(case_dir / "progress.json")
    _reset_beacon()
    stages = _Stages()
    t0 = time.time()
    out["started_utc"] = utc_now()
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning, message=VENDORED_WARNING)
            body(job, case_dir, out, stages)
    except Exception:
        out.update(status="FAILED", design_status="error", error=traceback.format_exc(), results=None)
    finally:
        try:
            from gendesign import progress as _p

            _p.finish("done" if out["status"] == "FINISHED" else "failed", status=out["status"])
        except Exception:
            pass
        if prev is None:
            os.environ.pop("SIMLAB_PROGRESS_FILE", None)
        else:
            os.environ["SIMLAB_PROGRESS_FILE"] = prev
    out["finished_utc"] = utc_now()
    out["wall_s"] = time.time() - t0
    out["stages"] = stages.records
    if out["results"] is not None:
        out["results"]["wall_s_total"] = out["wall_s"]
        out["results"]["artifacts"] = _artifacts(case_dir)
    return out


def _results(job: dict, gen_report, skin_rec, r: dict, stages: _Stages) -> dict:
    case, eff = job["case"], job["eff"]
    res = r["results"]
    device, backend = case.get("device", "cpu"), case.get("backend", "adapter")
    from .fea import BACKENDS, lane_of

    doc = {"schema": SCHEMA["case"], "case_id": case["case_id"], "role": case["role"], "round": case.get("round"),
           "level": case.get("level"), "source_case": case.get("source_case"),
           "design": {k: case[k] for k in eff["derived"]["variables"]} if case["role"] in DESIGN_ROLES else None}
    for k in METRICS:
        doc[k] = res[k]
    doc.update({
        "pcg_iters": res["pcg_iters"], "pcg_relres": res["pcg_relres"], "converged": True, "elements": res["elements"],
        "dof": res["dof"], "elem_mm": res["elem_mm"], "n_fixed_dofs": res["n_fixed_dofs"], "device": res["device"],
        "dtype": "float64", "backend": BACKENDS[backend], "lane": lane_of(device, backend), "grade": GRADE_PREVIEW,
        "generator": gen_report, "skin": skin_rec, "voxel": r["voxel"], "checks": r["checks"], "stress": r["stress"],
        "wall_s_generate": stages.wall("generate"), "wall_s_skin": stages.wall("skin"), "wall_s_voxel": r["wall_s_voxel"],
        "wall_s_solve": r["wall_s_solve"], "wall_s_post": r["wall_s_post"], "wall_s_total": None, "artifacts": [],
    })
    return doc


def _fmt(v) -> str:
    return "-" if v is None else f"{v:.4g}"


def _thumb(job: dict, case_dir: Path, out: dict, stages: _Stages) -> None:
    stl = case_dir / "part.stl"
    if not (job["thumb"] and stl.is_file()):
        return
    res = out.get("results") or {}
    label = f"{job['case_id']} · " + (f"{_fmt(res.get('mass_kg'))} kg" if out["status"] == "FINISHED" else out["design_status"])
    try:
        from . import render

        with stages.stage("thumb", "cpu"):
            render.thumb(stl, case_dir / "thumb.png", label=label)
    except Exception:
        pass  # a missing thumbnail never fails a design; the stage record says FAILED


def _design_body(job: dict, case_dir: Path, out: dict, stages: _Stages) -> None:
    import numpy as np
    from gendesign.export import write_binary_stl

    from . import fea, generator, organic

    eff, case = job["eff"], job["case"]
    dom = dict(job["domain"])
    dom["_root"] = job["run_root"]
    device, backend = case.get("device", "cpu"), case.get("backend", "adapter")
    params = {k: case[k] for k in eff["derived"]["variables"]}
    with stages.stage("generate", "cpu" if device == "cpu" else "gpu"):
        gen = generator.run_simp(dom, eff, params, case_dir, device=device)
    out["diagnostics"]["generator"] = gen["report"]
    if gen["status"] != "ok":
        out.update(design_status=gen["status"], error=gen["message"])
        return
    with stages.stage("skin", "cpu"):
        mesh, info = organic.skin(np.load(gen["density_path"]), dom, eff, params)
        dump_json(case_dir / "skin.json", info)
    skin_rec = {k: v for k, v in info.items() if k != "schema"}
    out["diagnostics"]["skin"] = skin_rec
    if info["status"] != "ok":
        out.update(design_status=info["status"], error=info["message"])
        return
    stl = case_dir / "part.stl"
    write_binary_stl(str(stl), mesh.vertices, mesh.faces, name=b"aces-golden organic skin")
    r = fea.solve_mesh(stl, dom, eff, case_dir, h_mm=job["h_mm"], device=device, backend=backend, post=job["post"])
    stages.records.extend(r["stages"])
    out["diagnostics"]["checks"] = r["checks"]
    if r["status"] == "ok":
        out["results"] = _results(job, gen["report"], skin_rec, r, stages)
        out.update(status="FINISHED", design_status="ok", error=None)
    else:
        out.update(design_status=r["status"], error=r["message"])
    _thumb(job, case_dir, out, stages)


def _mesh_body(job: dict, case_dir: Path, out: dict, stages: _Stages) -> None:
    from . import fea

    eff, case = job["eff"], job["case"]
    dom = dict(job["domain"])
    dom["_root"] = job["run_root"]
    reuse = job.get("reuse_mask_from")
    src = Path(job["source_stl"]) if job.get("source_stl") else None
    if reuse is None and (src is None or not src.is_file()):
        out.update(design_status="error", error=f"the source part {src} does not exist")
        return
    r = fea.solve_mesh(src, dom, eff, case_dir, h_mm=job["h_mm"], device=case.get("device", "cpu"),
                       backend=case.get("backend", "adapter"), post=job["post"],
                       reuse_mask_from=Path(reuse) if reuse else None)
    stages.records.extend(r["stages"])
    out["diagnostics"]["checks"] = r["checks"]
    if r["status"] == "ok":
        out["results"] = _results(job, None, None, r, stages)
        out.update(status="FINISHED", design_status="ok", error=None)
    else:
        out.update(design_status=r["status"], error=r["message"])


def run_design_case(job: dict) -> dict:
    """Worker entry point for a design: SIMP -> organic skin -> part.stl -> real solve (-> post files or thumb)."""
    return _run(job, _design_body)


def run_mesh_case(job: dict) -> dict:
    """Worker entry point for a mesh case: solve the source part.stl re-voxelised at the case's h, or the source case's
    mask (reuse_mask) on another device or backend."""
    return _run(job, _mesh_body)


# ------------------------------------------------------------------------------------------------------------ records

def _when(s: str) -> _dt.datetime:
    t = _dt.datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=_dt.timezone.utc)


def write_records(run, case: dict, outcome: dict, experiment) -> None:
    """results.json (FINISHED only), status.json and evidence.json (simlab.evidence.manifest, then the stages added as
    the vendored worker adds them) for one finished case. Main process only."""
    from simlab.evidence import manifest

    cid = case["case_id"]
    cdir = Path(run.root) / cid
    cdir.mkdir(parents=True, exist_ok=True)
    finished = outcome["status"] == "FINISHED" and outcome.get("results") is not None
    if finished:
        dump_json(cdir / "results.json", outcome["results"])
    dump_json(cdir / "status.json", {
        "status": outcome["status"], "design_status": outcome["design_status"], "mlflow_run_id": None,
        "wall_s": outcome["wall_s"], "error": outcome["error"], "stages": [s["stage"] for s in outcome["stages"]],
        "diagnostics": outcome["diagnostics"]})
    src = case.get("source_case")
    stl = (Path(run.root) / src if src else cdir) / "part.stl"
    inputs = [Path(run.root) / "inputs" / "spec.json", Path(run.root) / "inputs" / "spec.effective.json",
              Path(run.root) / "inputs" / "domain.step", cdir / "case.json", stl]
    ev = manifest(experiment, clean(case), str(cdir), [str(p) for p in inputs], _when(outcome["started_utc"]),
                  _when(outcome["finished_utc"]), outcome["status"], clean(outcome["results"]) if finished else None,
                  outcome["error"])
    ev["stages"] = outcome["stages"]
    dump_json(cdir / "evidence.json", ev, indent=2)


# --------------------------------------------------------------------------------------------------------- run_cases

def row_of(case: dict, outcome: dict, eff: dict) -> dict:
    """The pinned progress row of one finished case."""
    res = outcome.get("results") or {}
    err = outcome.get("error")
    if err:
        lines = [ln for ln in str(err).strip().splitlines() if ln.strip()]
        err = lines[-1][:240] if lines else None
    return {"case_id": case["case_id"], "role": case["role"], "status": outcome["status"],
            "design_status": outcome["design_status"],
            "params": {k: case[k] for k in eff["derived"]["variables"] if k in case},
            "metrics": {k: res.get(k) for k in ROW_METRICS}, "wall_s": outcome["wall_s"], "device": case.get("device"),
            "error": err}


def row_line(row: dict) -> str:
    """One line per finished case: `d003  volfrac 0.2113 rmin_mm 8.43 -> mass 0.1234 kg · vm_p99 55.1 MPa · umax
    0.03 mm · ok · 5.2 s` (a failed design shows its design status)."""
    head = " ".join(f"{k} {v:.4g}" for k, v in row["params"].items()) or row["role"]
    m = row["metrics"]
    t = f"{row['wall_s']:.1f} s" if row.get("wall_s") is not None else "- s"
    if row["status"] == "FINISHED":
        return (f"{row['case_id']}  {head} -> mass {_fmt(m.get('mass_kg'))} kg · vm_p99 {_fmt(m.get('vm_p99_MPa'))} MPa · "
                f"umax {_fmt(m.get('umax_mm'))} mm · ok · {t}")
    return f"{row['case_id']}  {head} -> {row['design_status']} · {t}"


def _inline(fn: Callable, job: dict) -> dict:
    from threadpoolctl import threadpool_limits

    with threadpool_limits(1), warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning, message=VENDORED_WARNING)
        return fn(job)


def _failed(case: dict, exc: BaseException) -> dict:
    out = _new_outcome(case)
    now = utc_now()
    out.update(error="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), started_utc=now,
               finished_utc=now, wall_s=0.0)
    return out


def run_cases(run, eff: dict, domain: dict, experiment, cases: list[dict], *, pool, progress, kind: str,
              bar=None) -> list[dict]:
    """Register, run and record cases; returns their outcomes in input order. kind "design" runs
    run_design_case, "mesh" runs run_mesh_case."""
    from concurrent.futures import FIRST_COMPLETED, wait

    if kind not in ("design", "mesh"):
        raise ValueError(f"kind must be design or mesh, not {kind!r}")
    fn = run_design_case if kind == "design" else run_mesh_case
    for c in cases:
        run.register_case(c)
    jobs = [make_job(run, eff, domain, c) for c in cases]
    outcomes: list[dict | None] = [None] * len(cases)
    inflight: set[str] = set()
    lock = threading.Lock()

    def beacons() -> list[Path]:
        with lock:
            ids = sorted(inflight)
        return [Path(run.root) / i / "progress.json" for i in ids]

    def finish(i: int, oc: dict) -> None:
        outcomes[i] = oc
        with lock:
            inflight.discard(cases[i]["case_id"])
        write_records(run, cases[i], oc, experiment)
        row = row_of(cases[i], oc, eff)
        if bar is not None:
            bar.done(row)
        else:
            progress.line(row_line(row))

    pending: dict = {}

    def drain(block: bool) -> None:
        if not pending:
            return
        done = [f for f in list(pending) if f.done()]
        if block and not done:
            done = list(wait(list(pending), return_when=FIRST_COMPLETED)[0])
        for f in done:
            i = pending.pop(f)
            try:
                oc = f.result()
            except Exception as exc:  # a worker died (BrokenProcessPool) or the task could not be pickled
                oc = _failed(cases[i], exc)
            finish(i, oc)

    label = "sweep" if cases and cases[0]["role"] == "sweep" else f"{kind} cases"
    with progress.watch(beacons, label):
        remote = [i for i, c in enumerate(cases) if pool is not None and c.get("device", "cpu") == "cpu"]
        for i in remote:
            with lock:
                inflight.add(cases[i]["case_id"])
            pending[pool.submit(fn, jobs[i])] = i
        remote_set = set(remote)
        for i in range(len(cases)):
            if i in remote_set:
                continue
            drain(False)
            with lock:
                inflight.add(cases[i]["case_id"])
            finish(i, _inline(fn, jobs[i]))
        while pending:
            drain(True)
    return [oc for oc in outcomes if oc is not None]
