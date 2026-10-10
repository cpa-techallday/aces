"""simlab-mcp: the tools agents use to run experiments on the rig (streamable HTTP, like rig-state).

    python -m simlab mcp --host 0.0.0.0 --port 8020

Submitting is allowed (it is a compute job gated by leases and the active mode); switching modes is
deliberately not a tool. Reads are safe at any time.
"""
from __future__ import annotations

import argparse
import contextvars
import datetime as dt
import functools
import json
import os
import time
from typing import Any, Dict, List, Optional

from mcp.server import MCPServer

from . import registry
from .capacity import gpu_table, summary_lines
from .leases import LeaseManager
from .queue import cancel_experiment, enqueue_experiment, experiment_results, experiment_status, list_experiments
from .spec import load

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.environ.get("SIMLAB_QUEUE_OUT", os.path.join(REPO, "out", "queue"))

# ---- Phase 6: tool tiers, principals and an audit trail ----
# SIMLAB_MCP_POLICY  read | submit | full   (default full; give agents `submit`, never `full`)
# SIMLAB_MCP_TOKENS  "name:token,name2:token2" -> requests need Authorization: Bearer <token>; the name is the
#                    principal written to the audit log. Unset = open on the LAN, principal "anonymous".
POLICY = os.environ.get("SIMLAB_MCP_POLICY", "full").strip().lower()
TIERS = {
    "read": {"rig_capacity", "sim_list_solvers", "sim_validate", "sim_status", "sim_results", "sim_report", "sim_list_experiments",
             "sim_report_files", "sim_evidence", "sim_whoami", "sim_list_specs", "sim_get_file"},
    "submit": {"sim_submit", "sim_sweep", "sim_put_spec", "sim_put_file"},
    "full": {"sim_cancel"},
}
ALLOWED = set(TIERS["read"]) | (TIERS["submit"] if POLICY in ("submit", "full") else set()) | (TIERS["full"] if POLICY == "full" else set())
AUDIT_PATH = os.environ.get("SIMLAB_MCP_AUDIT", os.path.join(REPO, "out", "mcp_audit.jsonl"))
PRINCIPAL: contextvars.ContextVar = contextvars.ContextVar("simlab_principal", default="anonymous")


def _tokens() -> Dict[str, str]:
    raw = os.environ.get("SIMLAB_MCP_TOKENS", "")
    out = {}
    for part in raw.split(","):
        if ":" in part:
            name, tok = part.split(":", 1)
            out[tok.strip()] = name.strip()
    return out


def _audit_arg(k: str, v: Any) -> Any:
    if k == "content_b64":   # file bytes never go into the audit log, only their size
        return f"<{len(v) if isinstance(v, str) else 0} base64 chars>"
    return v if isinstance(v, (int, float, bool, type(None))) else str(v)[:300]


def audit(tool: str, args: dict, ok: bool, ms: float, error: Optional[str] = None, result_note: Optional[str] = None) -> None:
    rec = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "principal": PRINCIPAL.get(), "policy": POLICY, "tool": tool,
           "args": {k: _audit_arg(k, v) for k, v in (args or {}).items()},
           "ok": ok, "ms": round(ms, 1), "error": (error or "")[:400] or None, "note": result_note}
    try:
        os.makedirs(os.path.dirname(AUDIT_PATH), exist_ok=True)
        with open(AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def tool(name: str, description: str):
    """Register a tool only if the policy allows it, and wrap it with the audit log."""
    def deco(fn):
        if name not in ALLOWED:
            return fn

        @functools.wraps(fn)
        def wrapped(*a, **kw):
            t0 = time.time()
            try:
                res = fn(*a, **kw)
                note = None
                if isinstance(res, dict):
                    keys = {k: res[k] for k in ("experiment", "jobs", "available", "policy", "spec_path", "sha256", "replaced",
                                                "name", "complete", "bytes", "existed", "path") if k in res}
                    if isinstance(res.get("ledger_entry"), dict):
                        keys["bundle_sha256"] = res["ledger_entry"].get("sha256")
                    note = str(keys)[:200]
                audit(name, kw, True, 1e3 * (time.time() - t0), result_note=note)
                return res
            except Exception as exc:
                audit(name, kw, False, 1e3 * (time.time() - t0), error=f"{type(exc).__name__}: {exc}")
                raise

        return server.tool(name=name, description=description)(wrapped)

    return deco


server = MCPServer(
    name="simlab",
    version="0.4.0",
    instructions=(
        "Experiment runtime for the CMP 170HX rig. rig_capacity tells you which GPUs the active GRID CONTROL "
        "mode has released and which are idle and unleased; sim_put_spec stores an experiment.yaml you send as text "
        "(validated, confined to the spec store, one folder per principal) and returns the spec_path that "
        "sim_validate / sim_submit / sim_sweep take, so nothing has to be copied onto the rig by hand; sim_put_file "
        "uploads an input file (a mesh, a mask, JSON) next to your specs in base64 chunks of at most 2 MiB, and "
        "sim_get_file reads any file of an experiment's run directory back the same way, with its sha256; sim_submit / "
        "sim_sweep queue experiment.yaml cases "
        "(one GPU lease per case; staged solvers run mesh/post on the cpu queue and solve under the lease); "
        "sim_status / sim_results / sim_report read back; sim_report_files writes md/html/docx/pdf. Fusion export "
        "(simlab export) runs on the workstation where Fusion is, not here. Mode switching is not a tool: ask "
        "through GRID CONTROL's decision log. Every call is written to an audit log with the caller's principal; "
        f"this server runs with policy '{POLICY}' (read | submit | full)."
    ),
)


@tool("rig_capacity", "GPU truth table: rig-state idle/memory/processes joined with GRID CONTROL mode and derated flags and the redis leases; 'available' lists the GPUs a new case could take now. mode/pool preview what another mode (sim-lab-4, sim-lab-8) or an explicit GPU list would release, without changing anything.")
def rig_capacity(mode: Optional[str] = None, pool: Optional[List[int]] = None) -> Dict[str, Any]:
    table = gpu_table(leases=LeaseManager().held(), mode=mode, pool=pool)
    table["text"] = "\n".join(summary_lines(table))
    return table


@tool("sim_list_solvers", "Solver registry: physics covered, image, precision and the validation record that makes results citable.")
def sim_list_solvers() -> Dict[str, Any]:
    return registry.load_registry()


@tool("sim_validate", "Validate an experiment.yaml (path relative to the repo) and list the cases its sweep expands to, without running anything.")
def sim_validate(spec_path: str) -> Dict[str, Any]:
    spec = load(os.path.join(REPO, spec_path) if not os.path.isabs(spec_path) else spec_path)
    registry.check_physics(spec.solver, spec.physics)
    return {"id": spec.id, "solver": spec.solver, "physics": spec.physics, "n_cases": len(spec.cases()), "cases": spec.cases()}


@tool("sim_put_spec", "Store an experiment.yaml sent as text so it can be validated and queued from anywhere: the content is parsed and checked like sim_validate (a YAML mapping that is a valid experiment whose solver covers its physics), written atomically under the spec store (SIMLAB_SPEC_ROOT, default <repo>/cases/uploaded/<principal>/<name>), and the returned spec_path is what sim_validate, sim_submit and sim_sweep take. name defaults to <id>.yaml and must be a bare file name; an existing file is kept unless overwrite=true. A geometry.ref that is not present next to the stored spec is reported as a warning (upload it first with sim_put_file). Nothing is queued.")
def sim_put_spec(content: str, name: Optional[str] = None, overwrite: bool = False) -> Dict[str, Any]:
    from .specstore import put_spec

    return put_spec(content, name=name, principal=PRINCIPAL.get(), overwrite=overwrite)


@tool("sim_list_specs", "Specs stored by sim_put_spec (newest first): spec_path, principal, id, solver, physics, case count, sha256. principal limits the list to one caller's folder.")
def sim_list_specs(principal: Optional[str] = None) -> List[Dict[str, Any]]:
    from .specstore import list_specs

    return list_specs(principal=principal)


@tool("sim_put_file", "Upload one file next to your specs (<spec store>/<principal>/<name>, e.g. name golden/<run>/part.stl) so an experiment.yaml can refer to it by a path relative to the spec. Send standard base64 in chunks of at most 2 MiB decoded (2097152 bytes): every call carries total_bytes and the sha256 of the whole file; offset 0 starts or restarts, each later call continues exactly where the last ended. The call that completes the file checks the hash (a mismatch discards the partial); an existing file with the same sha256 is kept (existed=true), a different one is refused unless overwrite=true. name: 1 to 4 components of letters, digits, dot, dash, underscore; never .yaml/.yml (use sim_put_spec). Files up to 256 MiB.")
def sim_put_file(name: str, content_b64: str, total_bytes: int, sha256: str, offset: int = 0,
                 overwrite: bool = False) -> Dict[str, Any]:
    from .filestore import put_file

    return put_file(name, content_b64, total_bytes=total_bytes, sha256=sha256, offset=offset,
                    principal=PRINCIPAL.get(), overwrite=overwrite)


@tool("sim_get_file", "Read one file of an experiment's run directory (path relative to it, e.g. c000/results.json or c000/golden/x001/fields.npz) in chunks: returns size, the sha256 of the whole file (recomputed on every call), offset, length, eof, mtime_utc and the chunk as base64 (max_bytes at most 2 MiB). Paths that resolve outside the run directory, or to anything but a regular file, are refused.")
def sim_get_file(experiment_id: str, path: str, offset: int = 0, max_bytes: int = 2097152) -> Dict[str, Any]:
    from .filestore import get_file

    return get_file(experiment_id, path, offset=offset, max_bytes=max_bytes)


@tool("sim_submit", "Queue every case of an experiment.yaml. Each GPU case waits for a lease on a released, idle, non-derated GPU. docker=true runs cases inside the solver's registered image (required on the rig). lease_wait_s bounds that wait per case (clamped to 60..21600 s; default 6 h); a job still waiting when sim_cancel is called stops waiting.")
def sim_submit(spec_path: str, docker: bool = True, out_root: Optional[str] = None, mlflow_uri: Optional[str] = None,
               lease_wait_s: Optional[int] = None) -> Dict[str, Any]:
    p = os.path.join(REPO, spec_path) if not os.path.isabs(spec_path) else spec_path
    kw: Dict[str, Any] = {}
    if lease_wait_s is not None:
        kw["lease_wait_s"] = max(60, min(21600, int(lease_wait_s)))
    return enqueue_experiment(p, out_root or DEFAULT_OUT, use_docker=docker, mlflow_uri=mlflow_uri, **kw)


@tool("sim_sweep", "Fan-out helper: take an experiment.yaml, replace its sweep with the given grid (parameter -> list of values), write the derived spec next to the original with a new id, and queue it.")
def sim_sweep(spec_path: str, grid: Dict[str, List[Any]], new_id: Optional[str] = None, docker: bool = True,
              out_root: Optional[str] = None) -> Dict[str, Any]:
    import yaml

    from .specstore import put_spec, store_dir

    p = os.path.join(REPO, spec_path) if not os.path.isabs(spec_path) else spec_path
    src = load(p)
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    data["sweep"] = grid
    data["id"] = new_id or f"{data['id']}-sweep-{abs(hash(json.dumps(grid, sort_keys=True))) % 10**6:06d}"
    # The derived spec goes into the caller's own folder of the spec store (validated like any upload, so a bad
    # new_id is refused before anything is written), never next to a file another principal owns. A relative
    # geometry.ref is re-pointed so it still resolves from the new folder.
    if src.geometry.ref:
        data["geometry"] = {**(data.get("geometry") or {}),
                            "ref": os.path.relpath(src.resolve(src.geometry.ref), store_dir(PRINCIPAL.get())).replace(os.sep, "/")}
    stored = put_spec(yaml.safe_dump(data, sort_keys=False), principal=PRINCIPAL.get(), overwrite=False)
    res = enqueue_experiment(stored["abs_path"], out_root or DEFAULT_OUT, use_docker=docker)
    res["derived_spec"] = stored["spec_path"]
    return res


@tool("sim_status", "Queue position, lease state, progress and outcome of every case of an experiment.")
def sim_status(experiment_id: str) -> Dict[str, Any]:
    st = experiment_status(experiment_id)
    for j in st["jobs"]:
        j.pop("result", None)
    return st


@tool("sim_results", "results.json of each finished case plus artifact paths; writes summary.csv/json when the experiment is done.")
def sim_results(experiment_id: str, case_id: Optional[str] = None) -> Dict[str, Any]:
    return experiment_results(experiment_id, case_id)


@tool("sim_report", "Markdown report of an experiment: cases, key outputs, failures, run directories.")
def sim_report(experiment_id: str) -> str:
    res = experiment_results(experiment_id)
    st = experiment_status(experiment_id)
    lines = [f"# {experiment_id}", "", f"solver: {st['solver']} · queue: {st['queue']} · submitted: {st['submitted_utc']} · "
             f"done: {res['done']} · counts: {res['counts']}", ""]
    keys: List[str] = []
    for c in res["cases"]:
        for k, v in (c["results"] or {}).items():
            if isinstance(v, (int, float)) and k not in keys:
                keys.append(k)
    keys = keys[:10]
    lines.append("| case | status | " + " | ".join(keys) + " |")
    lines.append("|---|---|" + "---|" * len(keys))
    for c in res["cases"]:
        r = c["results"] or {}
        lines.append(f"| {c['case_id']} | {c['status']} | " + " | ".join(f"{r.get(k, ''):.5g}" if isinstance(r.get(k), (int, float)) else "" for k in keys) + " |")
    lines += ["", "Run directories:"] + [f"- {c['run_dir']}" for c in res["cases"]]
    return "\n".join(lines)


@tool("sim_report_files", "Build the experiment report files (report.md/.html/.docx/.pdf in <exp_dir>/report): experiment definition, geometry provenance incl. the Fusion snapshot, results table, figures, solver validation record, evidence appendix. formats: comma list of md,html,docx,pdf.")
def sim_report_files(experiment_id: str, formats: str = "md,html,docx,pdf", title: Optional[str] = None,
                     doc_id: Optional[str] = None) -> Dict[str, Any]:
    from .report import build_report

    st = experiment_status(experiment_id)
    if not st.get("exp_dir"):
        raise ValueError(f"experiment {experiment_id} has no run directory")
    return build_report(st["exp_dir"], [f.strip() for f in formats.split(",") if f.strip()], title, doc_id)


@tool("sim_evidence", "Build the R&D evidence bundle of a finished experiment (hypothesis, method with solver validation, geometry provenance, environment with image digests and GPU ids, per-case manifests), write the contemporaneous record, append it to the hash-chained ledger; optionally deliver a record to the evidence engine (push: drop | nabu:<evidence.json> | http endpoint).")
def sim_evidence(experiment_id: str, company: Optional[str] = None, note: Optional[str] = None, push: Optional[str] = None) -> Dict[str, Any]:
    from .evidence_bundle import build_bundle, push as push_record, write_bundle

    st = experiment_status(experiment_id)
    if not st.get("done"):
        raise ValueError(f"experiment {experiment_id} is not finished ({st.get('counts')})")
    bundle = build_bundle(st["exp_dir"], operator=PRINCIPAL.get(), company=company, note=note)
    entry = write_bundle(bundle)
    out = {"ledger_entry": entry, "hypothesis": bundle.get("hypothesis"), "n_finished": bundle["experiment"]["n_finished"]}
    if push:
        out["push"] = push_record(bundle, push, company)
    return out


@tool("sim_whoami", "The principal this call is attributed to, the server's tool policy and the tools it exposes.")
def sim_whoami() -> Dict[str, Any]:
    return {"principal": PRINCIPAL.get(), "policy": POLICY, "tools": sorted(ALLOWED), "audit_log": AUDIT_PATH}


@tool("sim_cancel", "Cancel queued cases and request stop of running ones for an experiment.")
def sim_cancel(experiment_id: str) -> Dict[str, Any]:
    return cancel_experiment(experiment_id)


@tool("sim_list_experiments", "Experiments known to the queue.")
def sim_list_experiments() -> List[str]:
    return list_experiments()


def main(argv=None):
    ap = argparse.ArgumentParser(description="simlab MCP server (streamable HTTP)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8020)
    a = ap.parse_args(argv)
    from mcp.server.transport_security import TransportSecuritySettings

    sec = TransportSecuritySettings(enable_dns_rebinding_protection=False)  # LAN-internal service
    app = server.streamable_http_app(stateless_http=True, json_response=True, transport_security=sec, host=a.host)
    tokens = _tokens()

    class PrincipalMiddleware:
        """Bearer-token check (when SIMLAB_MCP_TOKENS is set) and principal attribution for the audit log."""

        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.inner(scope, receive, send)
            principal = "anonymous"
            if tokens:
                auth = dict(scope.get("headers") or {}).get(b"authorization", b"").decode()
                tok = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
                principal = tokens.get(tok)
                if principal is None:
                    audit("auth", {"path": scope.get("path")}, False, 0.0, error="missing or unknown bearer token")
                    body = b'{"error":"unauthorized: simlab-mcp requires Authorization: Bearer <token>"}'
                    await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                    await send({"type": "http.response.body", "body": body})
                    return
            token = PRINCIPAL.set(principal)
            try:
                return await self.inner(scope, receive, send)
            finally:
                PRINCIPAL.reset(token)

    import uvicorn

    print(f"[simlab-mcp] policy={POLICY} tools={sorted(ALLOWED)} auth={'bearer (' + str(len(tokens)) + ' principals)' if tokens else 'open'} audit={AUDIT_PATH}", flush=True)
    uvicorn.run(PrincipalMiddleware(app), host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
