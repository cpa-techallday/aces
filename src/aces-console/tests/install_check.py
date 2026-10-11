#!/usr/bin/env python3
"""The install check of aces-console (build spec 7.2; standard library only; Builder D).

Run by bin/install-test inside the clean Arch container after the packages are installed (and by
tests/test_install_check.py on a development machine against this source tree):

    python3 -I install_check.py --port 8070 --expect-ledger 3          # or --expect-ledger ">=7"

It starts the server (`aces-console serve --port N`, or the command given with --console-cmd-json), waits for
/api/health, and asserts, printing every fact it saw:

  1. the server binds 127.0.0.1 on the port (health.bind, health.port);
  2. GET / serves the page with the Content-Security-Policy of spec 3.1 (the importmap hash, frame-ancestors 'none'),
     X-Content-Type-Options: nosniff and Referrer-Policy: no-referrer, and no CORS header;
  3. GET /api/rig/status answers 200 with the envelope: with no rig reachable, data null and meta.error with the reason
     (the container of the install test has no route to the rig); a reachable rig is reported as such, never required;
  4. GET /api/ledger reports exactly the expected entry count (a literal) or at least it (">=N"), and in both cases the
     same count as `aces ledger verify --json`, whose chain must verify;
  5. a POST without the human headers is refused with 403 and counted in /api/health.counters.refused_posts;
  6. POST /api/system/stop {confirm: "stop"} (with the human headers) stops the server and the ledger grows by exactly
     one entry, kind console.server.stop, and still verifies;
  7. no reply body read here and no line of the server log carries the value of ACES_GC_TOKEN when that variable is set
     (the install test sets a random token before running this script and greps the logs a second time itself).

Checks 4 and 6 need the `ledgers` and `system` modules of the console backend (/api/health.data.modules). When a module
is not installed the check FAILS, naming the module, unless --allow-missing-modules is given: then the ledger count is
asserted from `aces ledger verify --json` alone and the server is stopped with SIGTERM, after which the ledger must not
have grown; both are printed as notes. tests/test_install_check.py passes the flag (a source tree may lack the modules);
bin/install-test does not.

Exit 0 when every assertion holds, 1 otherwise (the first failing fact is printed as FAIL), 2 on usage.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request


class Check(Exception):
    pass


def say(line: str) -> None:
    print(line, flush=True)


def http(url: str, method: str = "GET", body=None, headers=None, timeout: float = 10.0):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


def as_json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def ledger_verify(aces_cmd: list[str]) -> dict:
    proc = subprocess.run(aces_cmd + ["ledger", "verify", "--json"], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise Check(f"aces ledger verify --json exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[-300:]}")
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise Check(f"aces ledger verify --json printed no JSON: {proc.stdout[-300:]!r}") from None
    if not out.get("ok"):
        raise Check(f"the ledger does not verify: {out}")
    return out


def ledger_last_kind(aces_cmd: list[str]) -> str | None:
    proc = subprocess.run(aces_cmd + ["ledger", "show", "--json", "-n", "1"], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        return None
    try:
        rows = json.loads(proc.stdout)
    except ValueError:
        return None
    return rows[-1].get("kind") if isinstance(rows, list) and rows else None


def ledger_count(data: dict) -> tuple[int | None, str]:
    """The total the route reports: n_total (the contract), else total/count, else the length of a complete entries list."""
    for key in ("n_total", "total", "count"):
        if isinstance(data.get(key), int):
            return data[key], key
    entries = data.get("entries")
    if isinstance(entries, list):
        return len(entries), "len(entries)"
    return None, "none"


def expect(cond: bool, fact: str) -> None:
    if not cond:
        raise Check(fact)
    say(f"ok   {fact}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="aces-console install check (spec 7.2)")
    ap.add_argument("--port", type=int, default=8070)
    ap.add_argument("--expect-ledger", default="3", help='a literal count, or ">=N"')
    ap.add_argument("--console-cmd-json", default='["aces-console"]', help="JSON list: the command that takes `serve --port N`")
    ap.add_argument("--aces-cmd-json", default='["aces"]', help="JSON list: the aces CLI (ledger verify --json, ledger show)")
    ap.add_argument("--allow-missing-modules", action="store_true",
                    help="a backend without the ledgers or system module passes with the fallbacks described in the docstring")
    ap.add_argument("--start-timeout", type=float, default=60.0)
    args = ap.parse_args(argv)
    try:
        console_cmd = json.loads(args.console_cmd_json)
        aces_cmd = json.loads(args.aces_cmd_json)
        if not isinstance(console_cmd, list) or not isinstance(aces_cmd, list):
            raise ValueError("not a list")
    except ValueError as e:
        print(f"install_check: --console-cmd-json and --aces-cmd-json must be JSON lists ({e})", file=sys.stderr)
        return 2
    exp = args.expect_ledger.strip()
    at_least = exp.startswith(">=")
    try:
        expected_n = int(exp[2:] if at_least else exp)
    except ValueError:
        print(f"install_check: --expect-ledger must be an integer or >=N, got {exp!r}", file=sys.stderr)
        return 2
    url = f"http://127.0.0.1:{args.port}"
    token = os.environ.get("ACES_GC_TOKEN") or ""
    bodies: list[bytes] = []
    proc = None
    rc = 1
    try:
        say(f"== aces-console install check: port {args.port}, expected ledger entries {'>= ' if at_least else ''}{expected_n}, "
            f"token in the environment: {'yes (value never printed)' if token else 'no'}"
            f"{'; missing backend modules allowed' if args.allow_missing_modules else ''}")
        proc = subprocess.Popen(console_cmd + ["serve", "--port", str(args.port)], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        deadline = time.monotonic() + args.start_timeout
        health = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                raise Check(f"the server exited with status {proc.returncode} before answering: {out[-800:]}")
            try:
                st, hdrs, raw = http(url + "/api/health", timeout=2.0)
            except (urllib.error.URLError, OSError, TimeoutError):
                time.sleep(0.25)
                continue
            j = as_json(raw)
            if st == 200 and j and j.get("ok") and isinstance(j.get("data"), dict):
                bodies.append(raw)
                health = j["data"]
                break
            time.sleep(0.25)
        if health is None:
            raise Check(f"/api/health did not answer within {args.start_timeout:.0f} s")
        modules = health.get("modules") or {}
        say(f"server pid {health.get('pid')} version {health.get('version')} started {health.get('started_utc')} modules {modules}")
        # 1. loopback bind
        expect(health.get("bind") == "127.0.0.1", f"health.bind is 127.0.0.1 (got {health.get('bind')!r})")
        expect(health.get("port") == args.port, f"health.port is {args.port}")
        # 2. the page and its headers
        st, hdrs, raw = http(url + "/")
        bodies.append(raw)
        expect(st == 200 and "text/html" in hdrs.get("content-type", ""), f"GET / answers 200 text/html (got {st} {hdrs.get('content-type')})")
        csp = hdrs.get("content-security-policy", "")
        expect("script-src 'self' 'sha256-" in csp and "frame-ancestors 'none'" in csp and "connect-src 'self'" in csp,
               f"GET / carries the CSP with the importmap hash: {csp[:70]}...")
        expect(hdrs.get("x-content-type-options") == "nosniff" and hdrs.get("referrer-policy") == "no-referrer", "GET / carries nosniff and no-referrer")
        expect(not any(k.startswith("access-control-") for k in hdrs), "GET / sends no CORS header")
        # 3. the rig proxy answers honestly
        st, hdrs, raw = http(url + "/api/rig/status", timeout=30.0)
        bodies.append(raw)
        j = as_json(raw) or {}
        expect(st == 200 and j.get("ok") is True and "meta" in j, f"GET /api/rig/status answers 200 with the envelope (got {st})")
        meta = j.get("meta") or {}
        expect(hdrs.get("cache-control") == "no-store", "GET /api/rig/status carries Cache-Control: no-store")
        if j.get("data") is None:
            expect(meta.get("ok") is False and bool(meta.get("error")),
                   f"no rig from here: data null, meta.error {str(meta.get('error'))[:90]!r}, next_try_s {meta.get('next_try_s')}")
        else:
            say(f"note GRID CONTROL answered from this host (mode {str((j.get('data') or {}).get('mode'))!r}); the install test expects no rig, this machine has one")
        # 4. the ledger count against aces ledger verify --json
        v = ledger_verify(aces_cmd)
        before = int(v.get("entries"))
        ledgers_installed = modules.get("ledgers") == "installed"
        if ledgers_installed:
            st, hdrs, raw = http(url + "/api/ledger?n=5")
            bodies.append(raw)
            j = as_json(raw) or {}
            expect(st == 200 and j.get("ok") is True and isinstance(j.get("data"), dict), f"GET /api/ledger answers the envelope (got {st})")
            n_total, field = ledger_count(j.get("data") or {})
            expect(n_total == before, f"/api/ledger {field} {n_total} equals aces ledger verify --json ({before} entries, {v.get('message')})")
        elif args.allow_missing_modules:
            say(f"note modules.ledgers is {modules.get('ledgers')!r}: GET /api/ledger is not served by this backend; "
                f"the count is asserted from aces ledger verify --json alone ({before} entries, {v.get('message')})")
        else:
            raise Check(f"modules.ledgers is {modules.get('ledgers')!r}: GET /api/ledger is not served by this backend (spec 3.11)")
        if at_least:
            expect(before >= expected_n, f"ledger entries {before} >= {expected_n}")
        else:
            expect(before == expected_n, f"ledger entries {before} == {expected_n}")
        # 5. the POST guards
        refused_before = int((health.get("counters") or {}).get("refused_posts", 0))
        st, hdrs, raw = http(url + "/api/fabric/refresh", method="POST", body={})
        bodies.append(raw)
        j = as_json(raw) or {}
        expect(st == 403 and j.get("code") == "refused", f"POST without the human headers is refused with 403 {j.get('code')!r}")
        st, hdrs, raw = http(url + "/api/health")
        bodies.append(raw)
        h2 = (as_json(raw) or {}).get("data") or {}
        expect(int((h2.get("counters") or {}).get("refused_posts", 0)) == refused_before + 1, f"health.counters.refused_posts grew to {refused_before + 1}")
        # 6. stop
        human = {"Origin": url, "X-ACES-Intent": "human", "Content-Type": "application/json"}
        system_installed = modules.get("system") == "installed"
        if system_installed:
            st, hdrs, raw = http(url + "/api/system/stop", method="POST", body={"confirm": "stop"}, headers=human, timeout=30.0)
            bodies.append(raw)
            expect(st == 200, f"POST /api/system/stop {{confirm: stop}} answers 200 (got {st} {raw[:120]!r})")
        elif args.allow_missing_modules:
            say(f"note modules.system is {modules.get('system')!r}: POST /api/system/stop is not served by this backend; stopping with SIGTERM")
            proc.send_signal(signal.SIGTERM)
        else:
            raise Check(f"modules.system is {modules.get('system')!r}: POST /api/system/stop is not served by this backend (spec 3.12)")
        deadline = time.monotonic() + 15.0
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if proc.poll() is None:
            proc.kill()
            raise Check("the server did not exit within 15 s of the stop")
        say(f"server exited with status {proc.returncode}")
        try:
            st, hdrs, raw = http(url + "/api/health", timeout=2.0)
            raise Check(f"/api/health still answers {st} after the stop")
        except (urllib.error.URLError, OSError, ConnectionError):
            say("ok   /api/health no longer answers")
        v2 = ledger_verify(aces_cmd)
        after = int(v2.get("entries"))
        if system_installed:
            expect(after == before + 1, f"the ledger grew by exactly one entry ({before} -> {after}) and verifies ({v2.get('message')})")
            kind = ledger_last_kind(aces_cmd)
            expect(kind == "console.server.stop", f"the newest ledger entry is console.server.stop (got {kind!r})")
        else:
            expect(after == before, f"the ledger did not change on a SIGTERM stop ({before} -> {after}) and verifies ({v2.get('message')})")
        # 7. no token value anywhere this script saw
        if token:
            expect(all(token.encode("utf-8") not in b for b in bodies), f"the token value is in none of the {len(bodies)} reply bodies read")
            state = os.environ.get("ACES_STATE") or os.path.join(os.path.expanduser("~"), ".local", "state", "aces")
            log_path = os.path.join(state, "console", "server.log")
            if os.path.isfile(log_path):
                with open(log_path, "rb") as fh:
                    expect(token.encode("utf-8") not in fh.read(), f"the token value is not in {log_path}")
            else:
                say(f"note no server log at {log_path}")
        say("== aces-console install check passed")
        rc = 0
    except Check as e:
        say(f"FAIL {e}")
        rc = 1
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if proc is not None and proc.stdout:
            tail = proc.stdout.read()
            if tail.strip():
                if token:
                    tail = tail.replace(token, "[masked]")
                say("server output (tail): " + tail[-600:])
            proc.stdout.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
