"""Drive a real aces-console server process against a work store and print what every Builder B route returns:
shapes, counts, status words and honest absences, never secrets. Not a unittest module (discovery ignores it); the
companion of tests/verify_routes.py for the records, sim, ledger and system routes.

    python -I tests/verify_records.py fixtures 8073 --store <dir>   # replays tests/fixtures for the rig-side routes
    python -I tests/verify_records.py live     8074 --store <dir>   # read-only GETs against the configured SIMLAB console,
                                                                    # simlab-mcp (whoami, status) and MLflow
    python -I tests/verify_records.py offline  8075 --store <dir>   # every upstream on a closed loopback port

`--store` is a directory holding `Work/`, `.config/aces/` and `.local/state/aces/` (a desktop home, e.g. a copy of the
Omarchy VM's); `.config/aces` and `.local/state/aces` are copied to a temporary directory first so the run can never
mutate the store; `Work` and `.local/share` are read in place (no route writes under them). No token variable reaches
the server. The POSTs are the two local read-actions (bundle check, golden verify) and the final stop, which writes
one console.server.stop entry into the temporary ledger copy. Exit 0 when the server answered health, 1 otherwise.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
ACES_BASE = PKG.parent / "aces-base"
FIXTURES = PKG / "tests" / "fixtures"
GOLDEN_RUN = ("cad-gold", "20261010T102210Z-bc7391")
CAD_RUN = ("cad-vm", "20261010T101628Z-0ffb")
BUNDLE = ("cad-vm", "20261010T101736Z-plate")
EXP, CASE = "aces-cal-air-03d", "c000"
MLFLOW_RUN = "97cfbb614b98494b8d3aaa63e46cfd9e"


def shape(x, depth=0):
    if isinstance(x, dict):
        if depth >= 1:
            return "{" + ", ".join(sorted(x)[:16]) + ("..." if len(x) > 16 else "") + "}"
        return "{" + ", ".join(f"{k}: {shape(v, depth + 1)}" for k, v in list(x.items())[:18]) + ("..." if len(x) > 18 else "") + "}"
    if isinstance(x, list):
        return f"[{len(x)} x {shape(x[0], depth + 1) if x else 'empty'}]"
    if isinstance(x, str):
        return f"str({len(x)})" if len(x) > 70 else repr(x)
    return repr(x)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("fixtures", "live", "offline"))
    ap.add_argument("port", type=int)
    ap.add_argument("--store", required=True)
    ap.add_argument("--sse-seconds", type=float, default=4.0)
    ap.add_argument("--no-stop", action="store_true", help="skip the final POST /api/system/stop")
    ns = ap.parse_args()
    store = Path(ns.store)
    tmp = Path(tempfile.mkdtemp(prefix=f"aces-console-records-{ns.mode}-"))
    shutil.copytree(store / ".config" / "aces", tmp / "cfg")
    shutil.copytree(store / ".local" / "state" / "aces", tmp / "state")
    ledger = tmp / "state" / "ledger.jsonl"
    ledger_before = len(ledger.read_text(encoding="utf-8").splitlines()) if ledger.is_file() else 0
    if ns.mode == "offline":
        def closed() -> int:
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            p = s.getsockname()[1]
            s.close()
            return p
        (tmp / "cfg" / "aces.toml").write_text(
            "[server]\n" + "".join(f'{k} = "http://127.0.0.1:{closed()}{"/mcp" if k in ("rig_state", "simlab_mcp") else ""}"\n'
                                   for k in ("grid_control", "rig_state", "simlab_mcp", "simlab_console", "mlflow")) + "timeout_s = 2\n",
            encoding="utf-8")

    env = dict(os.environ)
    for k in ("ACES_GC_TOKEN", "SIMLAB_GC_TOKEN", "ACES_SIMLAB_TOKEN", "SIMLAB_MCP_TOKEN", "ACES_CONSOLE_STATIC",
              "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(k, None)
    env.update({"ACES_WORK": str(store / "Work"), "ACES_USER_CONFIG": str(tmp / "cfg"), "ACES_STATE": str(tmp / "state"),
                "XDG_DATA_HOME": str(store / ".local" / "share")})
    serve_args = ["serve", "--port", str(ns.port)]
    if ns.mode == "fixtures":
        env["ACES_CONSOLE_ALLOW_FIXTURES"] = "1"
        serve_args += ["--fixtures", str(FIXTURES)]
    paths = [str(PKG)] + ([str(ACES_BASE)] if (ACES_BASE / "aces" / "__init__.py").is_file() else [])
    prog = (f"import sys; sys.path[:0] = {json.dumps(paths)}; from aces_console.cli import main; "
            f"raise SystemExit(main({json.dumps(serve_args)}))")
    out = open(tmp / "serve.out", "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-I", "-c", prog], env=env, stdout=out, stderr=out, cwd=str(tmp))
    port = ns.port
    origin = f"http://127.0.0.1:{port}"

    def req(method, path, headers=None, body=None, timeout=330.0):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            r = conn.getresponse()
            data = r.read()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, data
        finally:
            conn.close()

    deadline = time.monotonic() + 30
    health = None
    while time.monotonic() < deadline:
        try:
            st, h, data = req("GET", "/api/health", timeout=2)
            if st == 200:
                health = json.loads(data)
                break
        except (OSError, http.client.HTTPException):
            pass
        if proc.poll() is not None:
            break
        time.sleep(0.3)
    print(f"== {ns.mode} mode on 127.0.0.1:{port}; server pid {proc.pid}; health answered: {health is not None}")
    if health is None:
        out.close()
        print((tmp / "serve.out").read_text(encoding="utf-8")[-2000:])
        proc.terminate()
        return 1
    d = health["data"]
    print(f"health.modules: {d['modules']}  fixture {d['fixture']}  work {store / 'Work'}")

    gp, gr = GOLDEN_RUN
    cp, cr = CAD_RUN
    bp, bn = BUNDLE
    routes = [
        "/api/projects", f"/api/projects/{gp}", f"/api/projects/{cp}", "/api/projects/golden-demo", "/api/projects/no-such-project",
        "/api/golden/runs", f"/api/golden/runs?project={gp}", f"/api/golden/run/{gp}/{gr}", f"/api/golden/events/{gp}/{gr}?after=0",
        f"/api/golden/file/{gp}/{gr}/report.html", f"/api/golden/file/{gp}/{gr}/winner/winner.stl",
        f"/api/golden/file/{gp}/{gr}/surrogate/round-1/model.joblib", f"/api/golden/file/{gp}/{gr}/../run.json",
        f"/api/golden/file/{gp}/{gr}/d000/fields.npz", f"/api/golden/file/{gp}/{gr}/inputs/domain.step",
        f"/api/cad/runs?project={cp}", f"/api/cad/run/{cp}/{cr}", f"/api/cad/file/{cp}/{cr}/drawings/plate-fa5cc45b.svg",
        f"/api/cad/file/{cp}/{cr}/run.json", f"/api/cad/bundles?project={bp}", f"/api/cad/bundle/{bp}/{bn}/source.json",
        f"/api/cad/bundle/{bp}/{bn}/part.step", f"/api/cad/notes/{cp}", f"/api/cad/drawings/{cp}", f"/api/cad/onshape/{cp}",
        f"/api/cad/onshape/{gp}", "/api/cad/runs?project=golden-demo",
        "/api/ledger", "/api/ledger?kind=golden.&n=2", "/api/ledger/verify", "/api/ledger/simlab", "/api/ledger/simlab/desktop",
        "/api/policy", "/api/doctor", "/api/system/packages", "/api/system/entries", "/api/system/links",
        "/api/sim/summary", "/api/sim/capacity", "/api/sim/experiments", f"/api/sim/experiments/{EXP}?root=queue",
        f"/api/sim/case/{EXP}/{CASE}?root=queue", f"/api/sim/file?eid={EXP}&root=queue&cid={CASE}&name=case.json",
        f"/api/sim/file?eid={EXP}&root=queue&cid={CASE}&name=nope.txt", "/api/sim/registry", "/api/sim/evidence",
        "/api/sim/evidence/verify", "/api/sim/audit", "/api/sim/whoami", f"/api/sim/status/{EXP}",
        "/api/mlflow/experiments", f"/api/mlflow/run/{MLFLOW_RUN}", "/api/mlflow/runs?experiment_id=11&max=5",
    ]
    for path in routes:
        t0 = time.monotonic()
        st, h, data = req("GET", path)
        ms = (time.monotonic() - t0) * 1000
        ctype = h.get("content-type", "")
        line = f"GET {path} -> {st} {ms:.0f}ms"
        body = None
        if h.get("content-disposition"):   # a served file, whatever its type
            line += (f" bytes={len(data)} ctype={ctype} csp={h.get('content-security-policy')} disp={h.get('content-disposition')} "
                     f"etag={'yes' if h.get('etag') else 'no'}")
        elif "json" in ctype and data:
            body = json.loads(data)
            if body.get("ok") is True and "meta" in body:
                m = body["meta"]
                line += f"\n    meta: ok={m['ok']} stale={m['stale']} error={m['error']!r} fixture={m['fixture']} source={m['source']}"
                line += f"\n    data: {shape(body['data'])}"
            else:
                line += f"\n    error: code={body.get('code')} error={body.get('error')!r}"
        else:
            line += (f" bytes={len(data)} ctype={ctype} csp={h.get('content-security-policy')} disp={h.get('content-disposition')} "
                     f"etag={'yes' if h.get('etag') else 'no'}")
        print(line)
        dd = body.get("data") if isinstance(body, dict) else None
        if not isinstance(dd, dict):
            continue
        if path == "/api/projects":
            for p in dd.get("projects", []):
                print(f"    project {p['name']:14} class={p.get('class')} counts={p.get('counts')} gate={p.get('onshape_gate')}")
            print(f"    reason={dd.get('reason')!r} ledger_note={dd.get('ledger_note')!r}")
        if path.startswith("/api/projects/") and "ledger" in dd:
            print(f"    detail: ledger {len(dd['ledger'])} golden_runs {len(dd['golden_runs'])} cad_runs {len(dd['cad_runs'])} bundles {len(dd['bundles'])}")
        if path.startswith("/api/golden/runs"):
            for r in dd.get("runs", []):
                print(f"    run {r['project']}/{r['run_id']} status={r['status']} verdict={r['verdict']} grade={r['grade']} wall_s={r['wall_s']} "
                      f"has_result={r['has_result']} stages={r['n_stages']} unreadable={r['unreadable']!r}")
            print(f"    reason={dd.get('reason')!r}")
        if path.startswith("/api/golden/run/"):
            print(f"    detail: status={dd['status']} grade={dd['grade']} verdict={dd['verdict']} stages={dd['n_stages']} "
                  f"total_s={(dd['run'].get('timings') or {}).get('total_s')} compute_used_rows={dd['compute_used_rows']} "
                  f"provenance={dd['provenance_n']} sections={dd['report_sections']} sweep_rows={(dd['sweep'] or {}).get('n')} "
                  f"figures={len(dd['figures'])} winner_files={len((dd['winner'] or {}).get('files') or [])} files={len(dd['files'])} "
                  f"events_tail={len(dd['events_tail'] or [])} absent={dd['absent']} read_errors={dd['read_errors']} notes={dd['notes']}")
        if path.startswith("/api/golden/events/"):
            print(f"    events: n_total={dd['n_total']} returned={len(dd['lines'])} eof={dd['eof']} status={dd['status']}")
        if path.startswith("/api/cad/runs"):
            print(f"    cad runs: counts={dd['counts']} note={dd.get('note')!r}")
            for r in dd["runs"][:4]:
                print(f"      {r['run_id']} status={r['status']} name={r['name']} sandbox={r['sandbox']} unreadable={r['unreadable']!r}")
        if path.startswith("/api/cad/run/"):
            print(f"    cad run: unreadable={dd['unreadable']!r} files={len(dd['files'])} with_sha={sum(1 for f in dd['files'] if f['sha256'])} "
                  f"subdirs={dd['subdirs']} drawings={[x['id'] for x in dd['drawings']]} status={(dd['summary'] or {}).get('status')}")
        if path.startswith("/api/cad/bundles"):
            for b in dd["bundles"]:
                print(f"    bundle {b['name']} has={b['has']} outcome={b['outcome_summary']}")
            print(f"    note={dd.get('note')!r}")
        if path.startswith("/api/cad/notes"):
            print(f"    notes: n={dd.get('n')} truncated={dd.get('truncated')} note={dd.get('note')!r}")
            for e in dd.get("entries", []):
                print(f"      {e['ts']} tags={e['tags']} run={e['run_id']} body={e['body'][:60]!r}")
        if path.startswith("/api/cad/drawings"):
            for x in dd["drawings"]:
                print(f"    drawing {x['id']} run={x['run_id']} views={x['views']} dims={x['n_dimensions']} hole_table={x['hole_table']} files={x['files']}")
            print(f"    note={dd.get('note')!r}")
        if path.startswith("/api/cad/onshape"):
            print(f"    onshape: connected={dd['connected']} class={dd['project_class']} gate={dd['onshape_gate']} local={dd['local']['present']}")
        if path.startswith("/api/ledger?") or path == "/api/ledger":
            print(f"    ledger: n_total={dd['n_total']} matching={dd['n_matching']} shown={len(dd['entries'])} kinds={dd['kinds']}")
            if dd["entries"]:
                e0 = dd["entries"][0]
                print(f"      newest n={e0['n']} {e0['ts']} {e0['kind']} payload keys={sorted((e0['payload'] or {}).keys())[:8]}")
        if path == "/api/ledger/verify":
            print(f"    verify: ok={dd['ok']} n={dd['n']} message={dd['message']!r}")
        if path == "/api/ledger/simlab":
            print(f"    rig chain: n={dd['n']} verify={dd['verify']} errors={dd['errors']} note={dd['note']!r}")
        if path == "/api/ledger/simlab/desktop":
            print(f"    desktop chain: exists={dd['exists']} n={dd['n_total']} verify={dd['verify']}")
            for e in dd.get("entries", []):
                print(f"      {e['ts'][:19]} {e['experiment']} sha={e['sha256'][:12]} prev={str(e['previous'])[:12]} bundle_present={e['bundle_present']}")
        if path == "/api/policy":
            print(f"    policy: mode={dd['policy'].get('mode')} classes={dd['policy'].get('classes')} overrides={len(dd['policy'].get('overrides') or [])} override_file={dd['override']}")
        if path == "/api/doctor":
            print(f"    doctor: text={'present' if dd.get('text') else None} note={dd.get('note')!r}")
        if path in ("/api/system/packages", "/api/system/entries"):
            print(f"    {path.split('/')[-1]}: n={len(dd.get('packages') or dd.get('entries') or [])} note={dd.get('note')!r}")
        if path == "/api/system/links":
            for l in dd["links"]:
                print(f"    link {l['id']:16} reach={l['reach']} openable={l['openable']} url={l['url']} note={l['note']!r}")
        if path == "/api/sim/summary":
            print(f"    summary: experiments={dd.get('experiments')} cases={dd.get('cases')} queue_counts={dd.get('queue_counts')} "
                  f"queue_jobs_total={dd.get('queue_jobs_total')} mode={dd.get('mode')} notes={dd.get('notes')}")
        if path == "/api/sim/capacity":
            print(f"    capacity: mode={dd.get('mode')} queue={dd.get('gridcontrol_queue')} note={dd.get('gridcontrol_queue_note')!r} "
                  f"activity={dd.get('activity_counts')} source={dd.get('source')!r} errors={dd.get('errors')}")
            for g in dd.get("gpus", []):
                print(f"      gpu {g['index']} activity={g['activity']!r} procs={[(p.get('pid'), p.get('usedMemMiB')) for p in g.get('processes') or []]}")
        if path == "/api/sim/experiments":
            print(f"    experiments: n={dd.get('experiments_n')} cases_n={dd.get('cases_n')} queue_n={dd.get('queue_n')} capped={dd.get('queue_capped')} "
                  f"rq={dd.get('queue_by_rq_status')} meta={dd.get('queue_by_meta_state')} queue_error={dd.get('queue_error')!r}")
        if path.startswith("/api/sim/experiments/"):
            print(f"    experiment: id={dd.get('id')} project={dd.get('project')} solver={dd.get('solver')} cases={len(dd.get('cases') or [])} reports={dd.get('reports')}")
        if path.startswith("/api/sim/case/"):
            print(f"    case: status={dd.get('status')} files={[f['name'] for f in dd.get('files') or []]} mlflow={shape(dd.get('mlflow'))}")
        if path == "/api/sim/registry":
            print(f"    registry: n={dd['n']} counts={dd['counts']}")
            for r in dd["solvers"]:
                print(f"      {r['name']:26} validation={r['validation_word']:12} lane={r['lane']!r:16} image_id={'yes' if r['image_id'] else 'no':3} citable={r['citable']} note={r['citable_note']!r}")
        if path == "/api/sim/evidence":
            print(f"    evidence: n={dd.get('n')}")
        if path == "/api/sim/evidence/verify":
            print(f"    verify: {dd}")
        if path == "/api/sim/audit":
            print(f"    audit: n={dd.get('n')} newest tool={(dd.get('audit') or [{}])[-1].get('tool')}")
        if path == "/api/sim/whoami":
            print(f"    whoami: principal={dd.get('principal')} policy={dd.get('policy')} tools={dd.get('n_tools')} token_source={dd.get('token_source')} server={dd.get('server')}")
        if path.startswith("/api/sim/status/"):
            print(f"    status: experiment={dd.get('experiment')} queue={dd.get('queue')} n={dd.get('n')} counts={dd.get('counts')} done={dd.get('done')} principal={dd.get('principal')}")
        if path == "/api/mlflow/experiments":
            print(f"    mlflow experiments: n={dd.get('n')} ids={[(e.get('experiment_id'), e.get('name')) for e in dd.get('experiments') or []][:12]}")
        if path.startswith("/api/mlflow/run/"):
            run = dd.get("run") or {}
            print(f"    mlflow run: experiment_id={(run.get('info') or {}).get('experiment_id')} status={(run.get('info') or {}).get('status')} "
                  f"params={len((run.get('data') or {}).get('params') or [])} metrics={len((run.get('data') or {}).get('metrics') or [])} "
                  f"tags={len((run.get('data') or {}).get('tags') or [])} ui_url={dd.get('ui_url')}")
        if path.startswith("/api/mlflow/runs"):
            print(f"    mlflow runs: n={dd.get('n')} first={shape((dd.get('runs') or [None])[0])}")

    human = {"Origin": origin, "X-ACES-Intent": "human", "Content-Type": "application/json"}
    for path in (f"/api/cad/bundle-check/{bp}/{bn}", f"/api/golden/verify/{gp}/{gr}"):
        t0 = time.monotonic()
        st, h, data = req("POST", path, headers=human, body=b"{}")
        body = json.loads(data)
        ms = (time.monotonic() - t0) * 1000
        if body.get("ok"):
            dd = body["data"]
            if "lines" in dd:
                print(f"POST {path} -> {st} {ms:.0f}ms exit={dd['exit']} all_match={dd['all_match']} checker={dd['checker']} lines={len(dd['lines'])}")
                for ln in dd["lines"]:
                    print(f"      {ln}")
            else:
                print(f"POST {path} -> {st} {ms:.0f}ms ok={dd['ok']} exit={dd['exit']} checks={len(dd['checks'])} counts={dd['counts']}")
        else:
            print(f"POST {path} -> {st} {ms:.0f}ms code={body.get('code')} error={body.get('error')!r}")
    st, h, data = req("POST", f"/api/golden/verify/{gp}/{gr}", headers={"Content-Type": "application/json"}, body=b"{}")
    print(f"POST verify without Origin/intent -> {st} {json.loads(data).get('code')}")

    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(f"GET /api/events?plate=evidence HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
    s.settimeout(0.5)
    buf = b""
    end = time.monotonic() + ns.sse_seconds
    while time.monotonic() < end:
        try:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        except socket.timeout:
            continue
    s.close()
    text = buf.decode("utf-8", "replace")
    _head, _, body_text = text.partition("\r\n\r\n")
    topics = {}
    for block in body_text.split("\n\n"):
        ev = None
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            if line.startswith("data:") and ev == "stamp":
                dd = json.loads(line[5:].strip())
                topics[dd["topic"]] = (dd["ok"], dd["error"], dd["payload"] is not None)
    print(f"SSE plate=evidence for {ns.sse_seconds} s: topics {sorted(topics)}")
    for t, v in sorted(topics.items()):
        print(f"    stamp {t:20} ok={v[0]} inline={v[2]} error={v[1]!r}")
    st, h, data = req("GET", "/api/health")
    hd = json.loads(data)["data"]
    mine = {k: v for k, v in hd["sources"].items() if k.split(".")[0] in ("sim", "mlflow", "projects", "golden", "cad", "ledger", "doctor", "system")}
    for t, row in sorted(mine.items()):
        print(f"    source {t:20} plates={row['plates']} polls={row['polls']} ok={row['ok']} error={row['error']!r}")

    if not ns.no_stop:
        st, h, data = req("POST", "/api/system/stop", headers=human, body=json.dumps({"confirm": "nope"}).encode())
        print(f"POST /api/system/stop confirm=nope -> {st} {json.loads(data).get('code')}")
        st, h, data = req("POST", "/api/system/stop", headers=human, body=json.dumps({"confirm": "stop"}).encode())
        print(f"POST /api/system/stop confirm=stop -> {st} {json.loads(data).get('data')}")
        try:
            proc.wait(15)
            print(f"server exited with {proc.returncode}")
        except subprocess.TimeoutExpired:
            proc.kill()
            print("server did not exit; killed")
    else:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
    out.close()
    log = tmp / "state" / "console" / "server.log"
    print(f"server.log lines: {len(log.read_text(encoding='utf-8').splitlines()) if log.is_file() else 'absent'}")
    print("serve.out tail:", (tmp / "serve.out").read_text(encoding="utf-8")[-600:].strip())
    ledger_after = len(ledger.read_text(encoding="utf-8").splitlines()) if ledger.is_file() else 0
    print(f"ledger entries: before {ledger_before}, after {ledger_after}" + ("" if ns.no_stop else " (the stop adds exactly one console.server.stop)"))
    if ledger.is_file():
        last = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
        print(f"ledger newest kind: {last['kind']} payload keys {sorted(last['payload'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
