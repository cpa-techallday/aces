"""Drive a real aces-console server process against a work store and print what every route returns: shapes, status
words and honest absences, never secrets. Not a unittest module (discovery ignores it); a builder's and the
acceptance's tool.

    python -I tests/verify_routes.py fixtures 8071 --store <dir>     # replays tests/fixtures; stamps FIXTURE 2026-10-11
    python -I tests/verify_routes.py live     8072 --store <dir>     # read-only GETs against the configured rig

`--store` is a directory holding `Work/`, `.config/aces/` and `.local/state/aces/` (the shape of a desktop home, e.g.
a copy of the Omarchy VM's). Its `.config/aces` and `.local/state/aces` are copied to a temporary directory first, so the
run can never mutate the store; `Work` is read in place (no route writes under it). No token variable is passed to the
server, so no protected route is ever called with a credential; the only POST is a header-less one that the guards must
refuse. The exit status is 0 when the server answered health, 1 otherwise.
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

PKG = Path(__file__).resolve().parent.parent                     # src/aces-console
ACES_BASE = PKG.parent / "aces-base"
FIXTURES = PKG / "tests" / "fixtures"


def shape(x, depth=0):
    if isinstance(x, dict):
        if depth >= 1:
            return "{" + ", ".join(sorted(x)[:14]) + ("..." if len(x) > 14 else "") + "}"
        return "{" + ", ".join(f"{k}: {shape(v, depth + 1)}" for k, v in list(x.items())[:14]) + "}"
    if isinstance(x, list):
        return f"[{len(x)} x {shape(x[0], depth + 1) if x else 'empty'}]"
    if isinstance(x, str):
        return f"str({len(x)})" if len(x) > 60 else repr(x)
    return repr(x)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("fixtures", "live"))
    ap.add_argument("port", type=int)
    ap.add_argument("--store", required=True, help="directory with Work/, .config/aces/, .local/state/aces/")
    ap.add_argument("--sse-seconds", type=float, default=6.0)
    ns = ap.parse_args()
    store = Path(ns.store)
    tmp = Path(tempfile.mkdtemp(prefix=f"aces-console-{ns.mode}-"))
    shutil.copytree(store / ".config" / "aces", tmp / "cfg")
    shutil.copytree(store / ".local" / "state" / "aces", tmp / "state")
    ledger = tmp / "state" / "ledger.jsonl"
    ledger_before = len(ledger.read_text(encoding="utf-8").splitlines()) if ledger.is_file() else 0

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
    # python -I ignores PYTHONPATH: a source tree goes on sys.path inside the program (as cli.server_command does)
    paths = [str(PKG)] + ([str(ACES_BASE)] if (ACES_BASE / "aces" / "__init__.py").is_file() else [])
    prog = (f"import sys; sys.path[:0] = {json.dumps(paths)}; from aces_console.cli import main; "
            f"raise SystemExit(main({json.dumps(serve_args)}))")
    args = [sys.executable, "-I", "-c", prog]
    out = open(tmp / "serve.out", "w", encoding="utf-8")
    proc = subprocess.Popen(args, env=env, stdout=out, stderr=out, cwd=str(tmp))
    port = ns.port

    def req(method, path, headers=None, body=None, timeout=20.0):
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
    print(f"health: version {d['version']} aces {d['aces_version']} origin {d['origin']} poller {d['poller']} fixture {d['fixture']}")
    print(f"health.modules: {d['modules']}")
    print(f"health.static_dir {d['static_dir']} [{d['static_source']}] manifest {d['static_manifest_sha256']}")

    routes = ["/api/config", "/api/rig/status", "/api/rig/modes", "/api/rig/telemetry", "/api/rig/snapshot", "/api/rig/switch",
              "/api/rig/decisions", "/api/fabric", "/vocab.json", "/", "/favicon.ico", "/api/no/such/route", "/static/.hidden"]
    for path in routes:
        t0 = time.monotonic()
        st, h, data = req("GET", path)
        ms = (time.monotonic() - t0) * 1000
        ctype = h.get("content-type", "")
        line = f"GET {path} -> {st} {ms:.0f}ms cc={h.get('cache-control')} csp={'yes' if h.get('content-security-policy') else 'no'}"
        body = None
        if "json" in ctype and data:
            body = json.loads(data)
            if body.get("ok") is True and "meta" in body:
                m = body["meta"]
                line += (f"\n    meta: ok={m['ok']} stale={m['stale']} error={m['error']!r} next_try_s={m['next_try_s']} "
                         f"fixture={m['fixture']} source={m['source']}")
                line += f"\n    data: {shape(body['data'])}"
            elif "vocabularies" in body:
                line += f"\n    vocab: {sorted(body['vocabularies'])}"
            else:
                line += f"\n    error: code={body.get('code')} error={body.get('error')!r}"
        else:
            line += f" bytes={len(data)}"
        print(line)
        dd = body.get("data") if isinstance(body, dict) else None
        if path == "/api/rig/status" and dd:
            print(f"    status.mode={dd.get('mode')} detected={dd.get('detected_mode')} queue={dd.get('queue')} "
                  f"note={dd.get('queue_note')!r} gpus={len(dd.get('gpus') or [])} switching={dd.get('switching')}")
        if path == "/api/rig/modes" and dd:
            gm = dd.get("gridcontrol_modes") or {}
            sm = dd.get("sim_modes")
            print(f"    modes: gridcontrol={len(gm)} current={dd.get('current')} sim_modes={sorted(sm) if sm else None} errors={dd.get('errors')}")
        if path == "/api/rig/snapshot" and dd:
            basis = dd.get("classification_basis", {})
            print(f"    snapshot: capturedAtIso={dd.get('capturedAtIso')} degraded={len(dd.get('degraded') or [])} "
                  f"counts={basis.get('counts')} basis.mode={basis.get('mode')} sim_modes_error={basis.get('sim_modes_error')!r}")
            for r in dd.get("classification", []):
                print(f"      {r['kind']:8} {r['name']:18} {r['word']:12} {r['verdict']}")
        if path == "/api/rig/switch" and dd:
            print(f"    switch: target={dd.get('target')} phase={dd.get('phase')} done={dd.get('done')} ok={dd.get('ok')} idle={dd.get('idle')} log_lines={dd.get('log_lines')}")
        if path == "/api/rig/telemetry" and dd:
            print(f"    telemetry: present={dd.get('telemetry') is not None} history={len(dd.get('history') or [])} sources_used={dd.get('sources_used')}")
        if path == "/api/fabric" and dd:
            print(f"    fabric: manifest={'present' if dd.get('manifest') else None} age_s={dd.get('age_s')} stale={dd.get('stale')} "
                  f"reason={dd.get('reason')!r} modes_table={len(dd.get('modes_table') or {})} table_error={dd.get('modes_table_error')!r}")
        if path == "/api/config" and dd:
            print(f"    config.tokens={dd['tokens']} paths.work={dd['paths']['work']} server.grid_control={dd['server']['grid_control']}")

    st, h, data = req("POST", "/api/fabric/refresh", headers={"Content-Type": "application/json"}, body=b"{}")
    print(f"POST /api/fabric/refresh (no Origin, no intent header) -> {st} {json.loads(data).get('code')}")

    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(f"GET /api/events?plate=fabric HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
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
    head, _, body_text = text.partition("\r\n\r\n")
    topics: dict[str, tuple] = {}
    events = []
    for block in body_text.split("\n\n"):
        ev = None
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            if line.startswith("data:") and ev == "stamp":
                dd = json.loads(line[5:].strip())
                topics[dd["topic"]] = (dd["ok"], dd["error"], dd["changed"], dd["payload"] is not None, dd["fixture"])
            elif line.startswith("data:") and ev:
                events.append((ev, line[5:].strip()[:80]))
    print(f"SSE: {head.splitlines()[0] if head else '?'}; first body line {body_text.splitlines()[0] if body_text else '?'}")
    for ev in events:
        print(f"    event {ev[0]}: {ev[1]}")
    for t, v in sorted(topics.items()):
        print(f"    stamp {t:16} ok={v[0]} changed={v[2]} inline={v[3]} fixture={v[4]} error={v[1]!r}")

    st, h, data = req("GET", "/api/health")
    d = json.loads(data)["data"]
    print(f"health after SSE: clients={d['clients']} poller={d['poller']} counters={d['counters']}")
    for t, row in sorted(d["sources"].items()):
        print(f"    source {t:16} ok={row['ok']} polls={row['polls']} backoff_s={row['backoff_s']} next_try_s={row['next_try_s']} error={row['error']!r}")

    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()
    out.close()
    log = tmp / "state" / "console" / "server.log"
    print(f"server.log lines: {len(log.read_text(encoding='utf-8').splitlines()) if log.is_file() else 'absent'} ({log})")
    print("serve.out tail:", (tmp / "serve.out").read_text(encoding="utf-8")[-500:].strip())
    ledger_after = len(ledger.read_text(encoding="utf-8").splitlines()) if ledger.is_file() else 0
    print(f"ledger entries: before {ledger_before}, after {ledger_after} (a read-only run must not change it)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
