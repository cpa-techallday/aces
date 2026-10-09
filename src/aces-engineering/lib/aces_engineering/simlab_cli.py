"""aces-simlab: the vendored simlab command line, run from the aces-engineering venv with desktop defaults.

  aces-simlab validate SPEC             parse an experiment.yaml and list its cases
  aces-simlab run SPEC --out DIR        run the cases here, one local worker process per slot (CPU unless CuPy + a GPU)
  aces-simlab solvers | summary DIR     the solver registry (validation records); an experiment's summary table
  aces-simlab report DIR [--formats md,html,docx,pdf]   report files (pdf needs LibreOffice's soffice)
  aces-simlab evidence build|verify|ls  evidence bundles and the hash-chained ledger (see also aces-evidence)
  aces-simlab surrogate fit-tabular|loop|predict ...    tabular surrogates on the CPU (train-fno needs torch)
  aces-simlab export ...                Fusion -> STEP + snapshot + experiment.yaml over the Fusion bridge on :9876
  aces-simlab backfill RUN_DIR...       log finished runs to MLflow (mlflow is not in this venv: a warning, no-op)
  aces-simlab capacity [--local]        GPU truth table: rig-state + GRID CONTROL from the aces config (no leases)
  aces-simlab remote tools|call|info    MCP client for the rig's simlab and rig-state servers (see `remote --help`)

Desktop defaults: evidence in ~/.local/share/aces/simlab/evidence (SIMLAB_EVIDENCE_ROOT overrides); solver
registrations in a per-user copy of the registry (ACES_SIMLAB_REGISTRY); `surrogate` resolves its out/ and cases/
defaults against the current directory. The queue, worker, console and MCP-server commands run on the rig.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import aces_config, ensure_user_registry, prepare_env

QUEUE_MSG = ("it drives the rig's redis/rq queue: rq and redis are not in this venv and the rig's redis is firewalled "
             "from the LAN. From the desktop use the simlab MCP: aces-simlab remote call {tool} '{{...}}'")
RIG_ONLY = {
    "enqueue": QUEUE_MSG.format(tool="sim_submit"),
    "qstatus": QUEUE_MSG.format(tool="sim_status"),
    "qresults": QUEUE_MSG.format(tool="sim_results"),
    "qcancel": QUEUE_MSG.format(tool="sim_cancel"),
    "worker": "runs an rq worker on the rig (one per leased GPU); there is no desktop queue",
    "workers": "spawns the rig's rq workers; there is no desktop queue",
    "console": "the SIMLAB console runs on the rig (server.simlab_console); open it with `aces-console simlab_console`",
    "mcp": "the simlab MCP server runs on the rig (server.simlab_mcp); call it with `aces-simlab remote ...`",
}
REGISTERS = {"fit-tabular", "train-fno", "loop"}


def capacity(argv: list) -> int:
    ap = argparse.ArgumentParser(prog="aces-simlab capacity",
                                 description="GPU truth table from rig-state and GRID CONTROL (URLs from the aces config)")
    ap.add_argument("--local", action="store_true", help="local nvidia-smi only; do not contact the rig")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    srv = aces_config().get("server") or {}
    if a.local:
        os.environ["SIMLAB_RIG_STATE_URL"] = ""
        os.environ["SIMLAB_GRIDCONTROL_URL"] = ""
    else:
        os.environ.setdefault("SIMLAB_RIG_STATE_URL", srv.get("rig_state") or "")
        os.environ.setdefault("SIMLAB_GRIDCONTROL_URL", srv.get("grid_control") or "")
    from simlab.capacity import gpu_table, summary_lines

    table = gpu_table(leases={})
    note = "leases are held in the rig's redis, which the desktop cannot read: 'available' ignores them"
    if a.json:
        print(json.dumps({**table, "note": note}, indent=2, default=str))
    else:
        print("\n".join(summary_lines(table) + [f"  note: {note}"]))
    return 0


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd in RIG_ONLY:
        print(f"aces-simlab {cmd}: not available on the desktop; {RIG_ONLY[cmd]}", file=sys.stderr)
        return 2
    if cmd == "run" and "--docker" in argv:
        print("aces-simlab run --docker: docker mode mounts a cudax-sim-runtime checkout into the solver image and is not "
              "supported from the packaged layout; run locally, or submit to the rig with aces-simlab remote call sim_submit",
              file=sys.stderr)
        return 2
    prepare_env()
    if cmd == "capacity":
        return capacity(argv[1:])
    if cmd == "remote":
        from .mcp_client import main as remote_main

        return remote_main(argv[1:])
    if cmd == "surrogate":
        import simlab.surrogate.cli as sur

        sur.REPO = os.getcwd()  # out/surrogate/<name> and cases/ resolve here, as they do in a cudax-sim-runtime checkout
        if len(argv) > 1 and argv[1] in REGISTERS:
            ensure_user_registry()
    from simlab.__main__ import main as simlab_main

    simlab_main(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
