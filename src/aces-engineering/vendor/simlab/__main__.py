from __future__ import annotations

import argparse
import json
import os

from . import registry
from .run import run_experiment
from .spec import load


def main(argv=None):
    ap = argparse.ArgumentParser(prog="simlab", description="experiment runtime")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run an experiment.yaml directly (sweep fan-out, one process per GPU slot)")
    r.add_argument("spec")
    r.add_argument("--out", default="out/exp")
    r.add_argument("--gpus", default=None, help="comma list, e.g. 0,1,2,3 (one case per GPU at a time)")
    r.add_argument("--parallel", type=int, default=1, help="slots when no --gpus is given")
    r.add_argument("--docker", action="store_true", help="run each case in the solver's registered image")
    r.add_argument("--image", default=None, help="override the registered image")
    r.add_argument("--mlflow", default=None, help="MLflow tracking URI (default: spec or MLFLOW_TRACKING_URI)")

    v = sub.add_parser("validate", help="parse a spec and list its cases")
    v.add_argument("spec")

    sub.add_parser("solvers", help="list the solver registry")

    s = sub.add_parser("summary", help="print an experiment's summary table")
    s.add_argument("exp_dir")

    b = sub.add_parser("backfill", help="log finished run directories (results.json + evidence.json) to MLflow after the fact")
    b.add_argument("run_dirs", nargs="+")
    b.add_argument("--mlflow", default=None)

    e = sub.add_parser("enqueue", help="queue an experiment's cases (Phase 3: redis/rq, GPU leases)")
    e.add_argument("spec")
    e.add_argument("--out", default="out/queue")
    e.add_argument("--docker", action="store_true")
    e.add_argument("--image", default=None)
    e.add_argument("--mlflow", default=None)
    e.add_argument("--queue", default=None, help="force a queue name (default sim for GPU solvers, cpu otherwise)")
    e.add_argument("--lease-wait", type=int, default=6 * 3600, help="seconds a case may wait for a GPU lease")

    w = sub.add_parser("worker", help="run one rq worker in this process")
    w.add_argument("--queues", nargs="+", default=["sim", "cpu"])
    w.add_argument("--burst", action="store_true", help="exit when the queues are empty")
    w.add_argument("--name", default=None)

    ws = sub.add_parser("workers", help="spawn N rq workers (one per GPU slot) and wait for them")
    ws.add_argument("--n", type=int, default=1)
    ws.add_argument("--queues", nargs="+", default=["sim", "cpu"])
    ws.add_argument("--burst", action="store_true")

    qs = sub.add_parser("qstatus", help="queue status of an experiment (or all)")
    qs.add_argument("experiment_id", nargs="?")

    qr = sub.add_parser("qresults", help="collected results of a queued experiment")
    qr.add_argument("experiment_id")

    qc = sub.add_parser("qcancel", help="cancel/stop an experiment's jobs")
    qc.add_argument("experiment_id")

    sub.add_parser("capacity", help="GPU truth table (rig-state + GRID CONTROL + leases, or local nvidia-smi)")

    ex = sub.add_parser("export", help="Fusion -> STEP + parameter snapshot + experiment.yaml (workstation, bridge :9876)")
    ex.add_argument("rest", nargs=argparse.REMAINDER)

    rp = sub.add_parser("report", help="build report.md / .html / .docx / .pdf for a finished experiment directory")
    rp.add_argument("exp_dir")
    rp.add_argument("--formats", default="md,html,docx,pdf")
    rp.add_argument("--title", default=None)
    rp.add_argument("--doc-id", default=None)

    ev = sub.add_parser("evidence", help="Phase 6: build / verify / ls R&D evidence bundles (see simlab.evidence_bundle)")
    ev.add_argument("rest", nargs=argparse.REMAINDER)

    sg = sub.add_parser("surrogate", help="Phase 5: fit-tabular / train-fno / loop / predict (see simlab.surrogate.cli)")
    sg.add_argument("rest", nargs=argparse.REMAINDER)

    co = sub.add_parser("console", help="run the simlab console (operator UI + JSON API, default :8030)")
    co.add_argument("--host", default="0.0.0.0")
    co.add_argument("--port", type=int, default=8030)

    m = sub.add_parser("mcp", help="run the simlab MCP server (streamable HTTP)")
    m.add_argument("--host", default="0.0.0.0")
    m.add_argument("--port", type=int, default=8020)

    a = ap.parse_args(argv)
    if a.cmd == "backfill":
        from .tracking import Tracker

        for rd in a.run_dirs:
            with open(os.path.join(rd, "evidence.json"), "r", encoding="utf-8") as f:
                ev = json.load(f)
            res = ev.get("results") or {}
            exp = ev["experiment"]
            tr = Tracker(a.mlflow, exp["project"]).start(f"{exp['id']}/{ev['case'].get('case_id', 'c000')}",
                                                         {"experiment_id": exp["id"], "project": exp["project"], "owner": exp["owner"],
                                                          "physics": exp["physics"], "solver": exp["solver"], "backfilled": "true"})
            tr.log_params({k: v for k, v in ev["case"].items() if k != "case_id"})
            tr.log_metrics({k: v for k, v in res.items() if k != "artifacts"})
            for art in res.get("artifacts", []):
                local = art
                if not os.path.exists(local):  # container path: /work/out/... -> this run dir's parent tree
                    tail = art.split("/out/", 1)[-1] if "/out/" in art else os.path.basename(art)
                    cand = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(rd))), tail)
                    local = cand if os.path.exists(cand) else os.path.join(rd, os.path.basename(art))
                tr.log_artifact(local)
            tr.log_artifact(os.path.join(rd, "evidence.json"))
            rid = tr.end(ev.get("status", "FINISHED"))
            print(f"backfilled {rd} -> run {rid}")
        return
    if a.cmd == "run":
        gpus = [int(x) for x in a.gpus.split(",")] if a.gpus else None
        run_experiment(a.spec, a.out, gpus, a.docker, a.parallel, a.image, a.mlflow)
    elif a.cmd == "validate":
        spec = load(a.spec)
        registry.check_physics(spec.solver, spec.physics)
        print(json.dumps({"id": spec.id, "solver": spec.solver, "cases": spec.cases()}, indent=2))
    elif a.cmd == "solvers":
        for name, e_ in registry.load_registry().items():
            val = e_.get("validation", {})
            print(f"{name:22s} {','.join(e_['physics']):12s} image={e_.get('image')}  validation={val.get('status')}  {e_.get('description', '')}")
    elif a.cmd == "summary":
        with open(os.path.join(a.exp_dir, "summary.json"), "r", encoding="utf-8") as f:
            s_ = json.load(f)
        print(f"{s_['experiment']} ({s_['solver']}): {s_['n_cases'] - s_['n_failed']}/{s_['n_cases']} ok")
        for row in s_["rows"]:
            print("  " + " ".join(f"{k}={v}" for k, v in row.items() if k not in ("device",)))
    elif a.cmd == "enqueue":
        from .queue import enqueue_experiment

        print(json.dumps(enqueue_experiment(a.spec, a.out, a.docker, a.image, a.mlflow, a.queue, a.lease_wait), indent=2))
    elif a.cmd == "worker":
        from .queue import start_worker

        start_worker(a.queues, a.burst, a.name)
    elif a.cmd == "workers":
        from .queue import spawn_workers

        procs = spawn_workers(a.n, a.queues, a.burst)
        print(f"spawned {len(procs)} worker(s) on {a.queues}; pids {[p.pid for p in procs]}", flush=True)
        for p in procs:
            p.wait()
    elif a.cmd == "qstatus":
        from .queue import experiment_status, list_experiments

        if a.experiment_id:
            st = experiment_status(a.experiment_id)
            print(f"{st['experiment']} ({st['solver']}, queue {st['queue']}): {st['counts']} done={st['done']}")
            for j in st["jobs"]:
                m_ = j.get("meta", {})
                print(f"  {j['job_id']:50s} {j['status']:9s} state={m_.get('state')} gpu={m_.get('gpu')} "
                      f"{('waiting; available ' + str(m_.get('available'))) if m_.get('state') == 'waiting_for_gpu' else ''}"
                      + (f" ERROR {j['error'].strip().splitlines()[-1]}" if j.get("error") else ""))
        else:
            print("\n".join(list_experiments()) or "(no experiments queued)")
    elif a.cmd == "qresults":
        from .queue import experiment_results

        print(json.dumps(experiment_results(a.experiment_id), indent=2, default=str)[:20000])
    elif a.cmd == "qcancel":
        from .queue import cancel_experiment

        print(json.dumps(cancel_experiment(a.experiment_id), indent=2))
    elif a.cmd == "capacity":
        from .capacity import gpu_table, summary_lines
        from .leases import LeaseManager

        try:
            held = LeaseManager().held()
        except Exception as exc:
            held = {}
            print(f"(redis not reachable: {exc})")
        print("\n".join(summary_lines(gpu_table(leases=held))))
    elif a.cmd == "evidence":
        from .evidence_bundle import main as ev_main

        ev_main(a.rest)
    elif a.cmd == "surrogate":
        from .surrogate.cli import main as sur_main

        sur_main(a.rest)
    elif a.cmd == "export":
        from .fusion_export import main as export_main

        export_main(a.rest)
    elif a.cmd == "report":
        from .report import build_report

        out = build_report(a.exp_dir, [f.strip() for f in a.formats.split(",") if f.strip()], a.title, a.doc_id)
        print(json.dumps(out, indent=2))
    elif a.cmd == "console":
        from .console.app import main as console_main

        console_main(["--host", a.host, "--port", str(a.port)])
    elif a.cmd == "mcp":
        from .mcp_server import main as mcp_main

        mcp_main(["--host", a.host, "--port", str(a.port)])


if __name__ == "__main__":
    main()
