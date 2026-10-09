"""Experiment-level R&D evidence bundle: hypothesis -> experiment -> observation, with every input
hashed, every image and GPU identified, every case's manifest attached, a human-readable
contemporaneous record, and a tamper-evident hash chain (ledger) across bundles.

    python -m simlab evidence build out/exp/<experiment> [--company carbon] [--operator aaron] [--push drop|nabu:<evidence.json>|http://host/endpoint]
    python -m simlab evidence verify            # recompute every bundle hash and the chain
    python -m simlab evidence ls

Bundle = out/evidence/<experiment>/<utc-stamp>_bundle.json (+ record.md next to it).
Ledger = out/evidence/ledger.jsonl, one line per bundle: ts, experiment, path, sha256, previous.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import socket
import urllib.request
from typing import Dict, List, Optional

from . import registry
from .spec import sha256_file

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVIDENCE_ROOT = os.environ.get("SIMLAB_EVIDENCE_ROOT", os.path.join(REPO, "out", "evidence"))


def _load(path: str, default=None):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def canonical_sha256(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def ledger_path() -> str:
    return os.path.join(EVIDENCE_ROOT, "ledger.jsonl")


def read_ledger() -> List[dict]:
    p = ledger_path()
    if not os.path.exists(p):
        return []
    with open(p, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def build_bundle(exp_dir: str, operator: Optional[str] = None, company: Optional[str] = None, note: Optional[str] = None) -> dict:
    exp_dir = os.path.abspath(exp_dir)
    spec = _load(os.path.join(exp_dir, "spec.resolved.json"), {})
    cases = _load(os.path.join(exp_dir, "cases.json"), [])
    if not spec or not cases:
        raise FileNotFoundError(f"{exp_dir} has no spec.resolved.json / cases.json (not a simlab experiment directory)")
    try:
        solver = registry.get(spec.get("solver", ""))
    except Exception:
        solver = {}
    # geometry provenance chain: Fusion snapshot -> STEP -> (mesh) -> results
    geom = dict(spec.get("geometry") or {})
    snap = None
    snap_name = (spec.get("tags") or {}).get("fusion_snapshot")
    if snap_name and spec.get("spec_path"):
        sp = os.path.join(os.path.dirname(spec["spec_path"]), snap_name)
        snap = _load(sp)
        if snap:
            geom["fusion_snapshot"] = {"file": sp, "sha256": sha256_file(sp), "document": snap.get("document"), "version": snap.get("version"),
                                       "design_type": snap.get("design_type"), "exported_utc": snap.get("exported_utc"),
                                       "user_parameters": snap.get("user_parameters"), "bodies": [{k: b.get(k) for k in ("component", "name", "volume_mm3")} for b in (snap.get("bodies") or [])],
                                       "step_sha256": (snap.get("export") or {}).get("step_sha256")}
    observations = []
    envs: Dict[str, dict] = {}
    images = {}
    gpus = {}
    for c in cases:
        cid = c["case_id"]
        rd = os.path.join(exp_dir, cid)
        ev = _load(os.path.join(rd, "evidence.json"), {}) or {}
        st = _load(os.path.join(rd, "status.json"), {}) or {}
        res = _load(os.path.join(rd, "results.json"), {}) or {}
        env = ev.get("environment") or {}
        host = env.get("host")
        if host:
            envs[host] = {"platform": env.get("platform"), "packages": env.get("packages")}
        if env.get("image"):
            images[env["image"]] = env.get("image_digest")
        for g in env.get("gpus") or []:
            gpus[g.get("uuid") or f"{host}:{g.get('index')}"] = {"name": g.get("name"), "driver": g.get("driver"), "memory": g.get("memory"),
                                                                 "serial": g.get("serial"), "host": host}
        observations.append({
            "case_id": cid, "parameters": {k: v for k, v in c.items() if k != "case_id"},
            "status": st.get("status") or ev.get("status"), "started_utc": ev.get("started_utc"), "finished_utc": ev.get("finished_utc"),
            "wall_s": ev.get("wall_s"), "results": {k: v for k, v in res.items() if k != "artifacts"},
            "artifacts": [{"path": a, "sha256": sha256_file(a) if os.path.isfile(a) else None} for a in res.get("artifacts", [])],
            "inputs_sha256": ev.get("inputs_sha256"), "stages": ev.get("stages"), "mlflow_run_id": st.get("mlflow_run_id"),
            "evidence_sha256": sha256_file(os.path.join(rd, "evidence.json")) if os.path.exists(os.path.join(rd, "evidence.json")) else None,
            "error": st.get("error"),
        })
    n_ok = sum(1 for o in observations if o["status"] == "FINISHED")
    outputs = spec.get("outputs") or []
    summary_rows = []
    for o in observations:
        summary_rows.append({"case_id": o["case_id"], **{k: o["parameters"].get(k) for k in (spec.get("sweep") or {})},
                             **{k: o["results"].get(k) for k in outputs}})
    bundle = {
        "schema": "simlab-evidence-bundle/1",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "created_by": {"operator": operator or spec.get("owner"), "host": socket.gethostname(), "tool": "simlab evidence build"},
        "company": company or spec.get("company"),
        "hypothesis": spec.get("hypothesis"),
        "expected": spec.get("expected"),
        "rd_activity": spec.get("rd_activity"),
        "experiment": {"id": spec.get("id"), "project": spec.get("project"), "owner": spec.get("owner"), "physics": spec.get("physics"),
                       "description": spec.get("description"), "spec_path": spec.get("spec_path"), "spec_sha256": spec.get("spec_sha256"),
                       "params": spec.get("params"), "sweep": spec.get("sweep"), "outputs": outputs, "resources": spec.get("resources"),
                       "tags": spec.get("tags"), "n_cases": len(cases), "n_finished": n_ok, "exp_dir": exp_dir},
        "method": {"solver": spec.get("solver"), "solver_description": solver.get("description"), "image": solver.get("image"),
                   "stages": solver.get("stages"), "validation": solver.get("validation"), "surrogate": solver.get("surrogate")},
        "geometry": geom,
        "environment": {"hosts": envs, "images": images, "gpus": gpus},
        "observations": observations,
        "summary": summary_rows,
        "note": note,
    }
    return bundle


def write_bundle(bundle: dict, previous: Optional[str] = None) -> dict:
    exp_id = bundle["experiment"]["id"]
    ledger = read_ledger()
    prev = previous if previous is not None else (ledger[-1]["sha256"] if ledger else None)
    bundle["previous_sha256"] = prev
    body_hash = canonical_sha256({k: v for k, v in bundle.items() if k not in ("bundle_sha256",)})
    bundle["bundle_sha256"] = body_hash
    d = os.path.join(EVIDENCE_ROOT, exp_id)
    os.makedirs(d, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(d, f"{stamp}_bundle.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2, default=str)
    md_path = os.path.join(d, f"{stamp}_record.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_record(bundle, path))
    entry = {"ts": bundle["created_utc"], "experiment": exp_id, "path": path, "record": md_path, "sha256": body_hash, "previous": prev,
             "n_cases": bundle["experiment"]["n_cases"], "n_finished": bundle["experiment"]["n_finished"], "company": bundle.get("company")}
    os.makedirs(EVIDENCE_ROOT, exist_ok=True)
    with open(ledger_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def render_record(b: dict, path: str) -> str:
    e, m = b["experiment"], b["method"]
    lines = [f"# Contemporaneous record: {e['id']}", "",
             f"Created {b['created_utc']} by {b['created_by']['operator']} on {b['created_by']['host']} · company {b.get('company') or '-'} · "
             f"R&D activity {b.get('rd_activity') or '-'} · bundle sha256 `{b['bundle_sha256']}` · previous `{b.get('previous_sha256') or 'none'}`", "",
             "## Hypothesis", "", b.get("hypothesis") or "(none recorded in the spec: add `hypothesis:` to experiment.yaml)", "",
             "## Expected outcome", "", b.get("expected") or "(none recorded)", "",
             "## Experiment", "",
             f"- project **{e['project']}**, owner {e['owner']}, physics {e['physics']}; spec `{e['spec_path']}` sha256 `{e['spec_sha256']}`",
             f"- {e['n_cases']} case(s), {e['n_finished']} finished; sweep: " + (", ".join(f"{k} ∈ {v}" for k, v in (e.get('sweep') or {}).items()) or "none"),
             f"- solver `{m['solver']}` ({m.get('solver_description')}), image `{m.get('image')}`, stages {list((m.get('stages') or {}).keys()) or 'single'}",
             f"- solver validation status **{(m.get('validation') or {}).get('status', 'none')}** ({(m.get('validation') or {}).get('date', '')})", ""]
    g = b.get("geometry") or {}
    if g:
        lines += ["## Geometry provenance", "", f"- source {g.get('source')}, reference `{g.get('ref')}`, sha256 `{g.get('sha256')}`"]
        fs = g.get("fusion_snapshot")
        if fs:
            lines += [f"- Fusion document **{fs.get('document')}** version {fs.get('version')} ({fs.get('design_type')}), exported {str(fs.get('exported_utc', ''))[:19]}Z, STEP sha256 `{fs.get('step_sha256')}`",
                      "- user parameters at export: " + ", ".join(f"{u['name']} = {u['expression']}" for u in (fs.get("user_parameters") or [])) ]
        lines.append("")
    env = b.get("environment") or {}
    lines += ["## Environment", ""]
    for img, dig in (env.get("images") or {}).items():
        lines.append(f"- image `{img}` digest `{dig or 'not recorded'}`")
    for uuid, gg in (env.get("gpus") or {}).items():
        lines.append(f"- GPU {gg.get('name')} on {gg.get('host')}, uuid `{uuid}`, serial {gg.get('serial') or 'n/a'}, driver {gg.get('driver')}")
    for host, hh in (env.get("hosts") or {}).items():
        pk = hh.get("packages") or {}
        lines.append(f"- host {host}: {hh.get('platform')}; " + ", ".join(f"{k} {v}" for k, v in list(pk.items())[:8]))
    lines += ["", "## Observations", ""]
    cols = ["case"] + list((e.get("sweep") or {}).keys()) + list(e.get("outputs") or []) + ["status"]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("|" + "---|" * len(cols))
    for o in b["observations"]:
        vals = [o["case_id"]] + [o["parameters"].get(k) for k in (e.get("sweep") or {})] + [o["results"].get(k) for k in (e.get("outputs") or [])] + [o["status"]]
        lines.append("| " + " | ".join(_fmt(v) for v in vals) + " |")
    lines += ["", "Per-case manifests (`evidence.json`) and their hashes:", ""]
    for o in b["observations"]:
        lines.append(f"- {o['case_id']}: evidence sha256 `{o.get('evidence_sha256')}`, inputs " +
                     ", ".join(f"{k} `{str(v)[:12]}…`" for k, v in (o.get("inputs_sha256") or {}).items()) +
                     (f", MLflow run {o['mlflow_run_id']}" if o.get("mlflow_run_id") else ""))
    if b.get("note"):
        lines += ["", "## Note", "", b["note"]]
    lines += ["", f"Bundle file: `{path}`", ""]
    return "\n".join(lines)


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.5g}"
    return "" if v is None else str(v)


def verify_ledger() -> dict:
    ledger = read_ledger()
    prev = None
    problems = []
    for i, ent in enumerate(ledger):
        b = _load(ent["path"])
        if b is None:
            problems.append({"line": i, "problem": "bundle file missing", "path": ent["path"]})
            prev = ent["sha256"]
            continue
        recomputed = canonical_sha256({k: v for k, v in b.items() if k != "bundle_sha256"})
        if recomputed != ent["sha256"] or b.get("bundle_sha256") != ent["sha256"]:
            problems.append({"line": i, "problem": "hash mismatch (bundle edited?)", "path": ent["path"]})
        if b.get("previous_sha256") != prev or ent.get("previous") != prev:
            problems.append({"line": i, "problem": "chain break", "path": ent["path"]})
        prev = ent["sha256"]
    return {"entries": len(ledger), "ok": not problems, "problems": problems, "head": prev}


# ---------------- evidence engine delivery ----------------

def to_nabu_record(bundle: dict, company: Optional[str] = None) -> dict:
    """The record shape of Nabu's app/data/evidence.json (id, company, agentId, agent, title, body, engine, model, cost, costTag, ts)."""
    e, m = bundle["experiment"], bundle["method"]
    outs = e.get("outputs") or []
    rows = bundle.get("summary") or []
    head = "; ".join(f"{r['case_id']}: " + ", ".join(f"{k}={_fmt(r.get(k))}" for k in outs[:4]) for r in rows[:3])
    body = (f"simlab experiment {e['id']} ({e['project']}, {e['physics']}) · solver {m['solver']} [{(m.get('validation') or {}).get('status', 'none')}] "
            f"image {m.get('image')} · {e['n_finished']}/{e['n_cases']} cases finished · hypothesis: {bundle.get('hypothesis') or '-'} · "
            f"{head} · bundle sha256 {bundle['bundle_sha256']} · previous {bundle.get('previous_sha256') or 'none'}")
    return {"id": "ev_sim_" + bundle["bundle_sha256"][:8], "company": company or bundle.get("company") or "carbon",
            "agentId": "ag_simlab", "agent": "simlab", "title": f"[simlab] {e['id']}: {e['n_finished']}/{e['n_cases']} cases, {m['solver']}",
            "body": body[:1200], "engine": "simlab", "model": f"{m['solver']} · {m.get('image')}", "cost": 0, "costTag": "cost:0",
            "ts": bundle["created_utc"], "bundle": bundle["bundle_sha256"], "rd_activity": bundle.get("rd_activity")}


def push(bundle: dict, target: str, company: Optional[str] = None) -> dict:
    rec = to_nabu_record(bundle, company)
    if target.startswith("http://") or target.startswith("https://"):
        req = urllib.request.Request(target, data=json.dumps(rec).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            return {"pushed": target, "status": r.status}
    if target.startswith("nabu:"):
        path = target[5:]
        data = _load(path, [])
        if any(x.get("id") == rec["id"] for x in data):
            return {"pushed": path, "status": "already present"}
        data.append(rec)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
        return {"pushed": path, "status": "appended", "records": len(data)}
    # default: outbox file drop for the evidence engine's ingest
    drop = os.path.join(EVIDENCE_ROOT, "outbox") if target == "drop" else target
    os.makedirs(drop, exist_ok=True)
    p = os.path.join(drop, rec["id"] + ".json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
    return {"pushed": p, "status": "dropped"}


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(prog="simlab evidence")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("exp_dir")
    b.add_argument("--company", default=None)
    b.add_argument("--operator", default=None)
    b.add_argument("--note", default=None)
    b.add_argument("--push", default=None, help="drop | nabu:<path to evidence.json> | http(s)://endpoint | <outbox dir>")
    sub.add_parser("verify")
    sub.add_parser("ls")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        bundle = build_bundle(a.exp_dir, a.operator, a.company, a.note)
        entry = write_bundle(bundle)
        out = {"ledger_entry": entry}
        if a.push:
            out["push"] = push(bundle, a.push, a.company)
        print(json.dumps(out, indent=2))
    elif a.cmd == "verify":
        print(json.dumps(verify_ledger(), indent=2))
    else:
        for ent in read_ledger():
            print(f"{ent['ts'][:19]}  {ent['experiment']:34s} {ent['n_finished']}/{ent['n_cases']}  {ent['sha256'][:16]}  prev {str(ent['previous'])[:16]}")


if __name__ == "__main__":
    main()
