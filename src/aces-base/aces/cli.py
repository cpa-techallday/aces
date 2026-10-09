"""The `aces` command.

`aces golden ...` is a passthrough: it runs `aces-golden` (shipped by aces-engineering) with the remaining arguments,
or the command named by ACES_GOLDEN_CMD (a JSON array of strings, or one path), and exits 127 naming the package to
install when neither exists. It is handled before argparse, so options such as `--demo` reach aces-golden untouched.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

from . import __version__, config, doctor, fabric, lane, ledger, policy, project

GOLDEN_MISSING = "aces: aces golden needs aces-golden from the aces-engineering package: sudo pacman -S aces-engineering"


def _print_json(data) -> None:
    print(json.dumps(data, indent=1, ensure_ascii=False))


def cmd_fabric(args, cfg) -> int:
    if args.action == "refresh":
        m = fabric.refresh(cfg)
        print(f"manifest written: {fabric.manifest_path(cfg)}")
        for k, v in m["health"].items():
            print(f"  {k}: {v}")
        mode = m.get("mode") or {}
        print(f"  mode: {mode.get('name')} ({mode.get('label') or 'no label'}; detected {mode.get('detected')})")
        print(f"  lanes: {', '.join(l.get('id', '?') for l in m['lanes']) or 'none'}")
        for note in m.get("notes", []):
            print(f"  note: {note}")
        return 0
    m = fabric.read(cfg)
    if m is None:
        print("no manifest yet; run `aces fabric refresh`")
        return 1
    if args.json:
        _print_json(m)
        return 0
    mode = m.get("mode") or {}
    print(f"generated {m.get('generated')}  server {m['server'].get('name')} ({m['server'].get('host')})")
    print(f"mode: {mode.get('name')}  label: {mode.get('label')}  detected: {mode.get('detected')}")
    for k, v in m.get("health", {}).items():
        print(f"health {k}: {v}")
    if m.get("gpus"):
        print("gpu  util  mem_mb  temp  derated")
        for g in m["gpus"]:
            print(f"{g.get('index'):>3}  {str(g.get('util')):>4}  {str(g.get('mem_used_mb')):>6}  {str(g.get('temp_c')):>4}  {g.get('derated')}")
    print("lanes:")
    for l in m.get("lanes", []):
        print(f"  {l.get('id'):<18} {l.get('kind', ''):<6} gpus={l.get('gpus', [])} endpoint={l.get('endpoint')} class={l.get('class')}")
    for note in m.get("notes", []):
        print(f"note: {note}")
    return 0


def cmd_policy(args, cfg) -> int:
    if args.action == "show":
        pol = policy.load()
        if args.json:
            _print_json(pol)
            return 0
        print(f"mode: {pol.get('mode')}")
        print(f"default project class: {pol.get('default_project_class')}")
        print(f"classes: {', '.join(pol.get('classes', []))}")
        print(f"end-use gate: {pol.get('end_use_gate')}")
        ov = pol.get("overrides", [])
        print(f"overrides: {len(ov)}")
        for e in ov[-10:]:
            p = e.get("payload", {})
            print(f"  {e.get('ts')} {e.get('operator')} {p.get('component')}: {p.get('reason')}")
        return 0
    if args.action == "mode":
        e = policy.set_mode(args.mode, args.reason or "")
        print(f"policy mode set to {args.mode} (ledger {e['hash'][:12]})")
        return 0
    if args.action == "override":
        e = policy.override(args.component, args.reason, args.terms)
        print(f"override recorded for {args.component} (ledger {e['hash'][:12]})")
        return 0
    return 2


def cmd_project(args, cfg) -> int:
    if args.action == "new":
        p = project.new(args.name, args.cls, args.company, args.rd_activity, args.hypothesis)
        print(f"created {p}")
        return 0
    if args.action == "show":
        data = project.show(args.name)
        _print_json(data) if args.json else print(project._dump(data), end="")
        return 0
    if args.action == "classify":
        e = project.classify(args.name, args.cls, args.reason)
        comps = e["payload"].get("components_to_reread") or []
        print(f"{args.name}: {e['payload']['from']} -> {e['payload']['to']} (ledger {e['hash'][:12]})")
        print("end-use gate: re-read the terms of " + (", ".join(comps) if comps else "no recorded components"))
        return 0
    if args.action == "list":
        for p in project.list_projects():
            d = project._load(p)
            print(f"{d.get('name'):<24} {d.get('class'):<12} {p}")
        return 0
    if args.action == "add-component":
        project.add_component(args.name, args.component)
        print(f"{args.component} recorded against {args.name}")
        return 0
    if args.action == "add-evidence":
        added = project.add_evidence(args.name, args.ref)
        print(f"evidence {args.ref} {'recorded against' if added else 'was already recorded against'} {args.name}")
        return 0
    if args.action == "add-cad-source":
        added = project.add_cad_source(args.name, args.ref)
        print(f"CAD source {args.ref} {'recorded against' if added else 'was already recorded against'} {args.name}")
        return 0
    return 2


def _read_payload(src: str) -> str:
    if src == "-":
        return sys.stdin.buffer.read().decode("utf-8")
    with open(src, encoding="utf-8") as fh:
        return fh.read()


def cmd_ledger_record(args) -> int:
    """`aces ledger record KIND --json FILE|-`: one entry from a tool outside aces-base (e.g. golden.run)."""
    try:
        text = _read_payload(args.json_file)
    except (OSError, UnicodeDecodeError) as e:
        print(f"aces: cannot read {args.json_file}: {getattr(e, 'strerror', None) or e}", file=sys.stderr)
        return 2
    try:
        payload = json.loads(text)
        n, entry = ledger.record(args.kind, payload)
    except ValueError as e:  # invalid JSON (JSONDecodeError is a ValueError), or the kind or payload was refused
        print(f"aces: {e}", file=sys.stderr)
        return 2
    except TimeoutError as e:
        print(f"aces: {e}", file=sys.stderr)
        return 1
    if args.print_fmt == "json":
        print(json.dumps({"recorded": True, "n": n, "kind": entry["kind"], "ts": entry["ts"], "hash": entry["hash"],
                          "prev": entry["prev"], "path": str(ledger.path())}, ensure_ascii=False))
    else:
        print(f"recorded {entry['kind']} as ledger entry {n} ({entry['hash'][:12]}...) in {ledger.path()}")
    return 0


def cmd_ledger(args, cfg) -> int:
    if args.action == "verify":
        ok, n, msg = ledger.verify()
        if args.json:
            print(json.dumps({"ok": ok, "entries": n, "message": msg, "path": str(ledger.path())}, ensure_ascii=False))
        else:
            print(f"{'ok' if ok else 'FAIL'}: {n} entries, {msg} ({ledger.path()})")
        return 0 if ok else 1
    if args.action == "show":
        shown = [dict(e, n=i) for i, e in enumerate(ledger.entries(), 1)]
        if args.kind:
            shown = [e for e in shown if e.get("kind") == args.kind]
        if args.n > 0:
            shown = shown[-args.n:]
        if args.json:
            _print_json(shown)
            return 0
        for e in shown:
            print(f"{e.get('ts')} {e.get('kind'):<20} {e.get('operator')}@{e.get('host')} {json.dumps(e.get('payload'), ensure_ascii=False)[:160]}")
        return 0
    if args.action == "record":
        return cmd_ledger_record(args)
    return 2


def cmd_lane(args, cfg) -> int:
    if args.action == "list":
        ls = lane.lanes(cfg)
        if not ls:
            print("no lanes in the manifest; run `aces fabric refresh`")
            return 1
        for l in ls:
            print(f"{l.get('id'):<18} {l.get('kind', ''):<6} model={l.get('model')} endpoint={l.get('endpoint')} gpus={l.get('gpus', [])}")
        return 0
    if args.action == "test":
        l = lane.find(cfg, args.lane)
        if l.get("kind") != "model":
            print(f"lane {args.lane} is kind {l.get('kind')}; only model lanes are tested here")
            return 1
        r = lane.test_model_lane(cfg, l)
        _print_json(r)
        return 0 if r.get("ok") else 1
    if args.action == "bench":
        if fabric.read(cfg) is None or args.refresh:
            fabric.refresh(cfg)
        report = lane.bench(cfg)
        if args.json:
            _print_json(report)
        else:
            print(lane.bench_markdown(report), end="")
        if args.out:
            from pathlib import Path
            p = Path(args.out)
            p.parent.mkdir(parents=True, exist_ok=True)
            header = "# ACES lane benchmarks\n\nAppended by `aces lane bench`; every row is also a `lane.test` ledger entry.\n\n"
            text = p.read_text(encoding="utf-8") if p.exists() else header
            p.write_text(text + lane.bench_markdown(report), encoding="utf-8")
            print(f"appended to {p}")
        usable = [r for r in report["results"] if r.get("tool_calling")]
        print(f"{len(report['results'])} model lane(s) probed; {len(usable)} return structured tool calls")
        return 0
    return 2


def golden_command() -> list[str] | None:
    """The aces-golden command line: ACES_GOLDEN_CMD (a JSON array of strings, or one path), else aces-golden on
    PATH, else None. Raises ValueError for an ACES_GOLDEN_CMD that is neither."""
    raw = os.environ.get("ACES_GOLDEN_CMD", "")
    if raw.strip():
        if raw.lstrip().startswith("["):
            cmd = json.loads(raw)
            if not (isinstance(cmd, list) and cmd and all(isinstance(c, str) and c for c in cmd)):
                raise ValueError("expected a non-empty JSON array of strings")
            return cmd
        return [raw]
    found = shutil.which("aces-golden")
    return [found] if found else None


def cmd_golden(rest: list[str]) -> int:
    """Run aces-golden with `rest`. On POSIX without ACES_GOLDEN_CMD it replaces this process (exec), so the
    terminal, signals and the exit status are aces-golden's own; otherwise it runs as a child and its exit status is
    returned: a Ctrl-C reaches the child from the same console, so this process keeps waiting while the child writes
    its interrupted run records, then returns the child's status (130). 127 when there is nothing to run."""
    try:
        cmd = golden_command()
    except ValueError as e:  # includes JSONDecodeError
        print(f"aces: ACES_GOLDEN_CMD must be a JSON array of strings or one path ({e})", file=sys.stderr)
        return 2
    if not cmd:
        print(GOLDEN_MISSING, file=sys.stderr)
        return 127
    if os.name == "posix" and not os.environ.get("ACES_GOLDEN_CMD"):
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            os.execv(cmd[0], cmd + rest)
        except OSError as e:
            print(f"aces: cannot run {cmd[0]}: {e}", file=sys.stderr)
            return 127
    try:
        p = subprocess.Popen(cmd + rest)
    except FileNotFoundError:
        print(f"aces: cannot run {cmd[0]}: not found", file=sys.stderr)
        return 127
    except OSError as e:
        print(f"aces: cannot run {cmd[0]}: {e}", file=sys.stderr)
        return 126
    # not subprocess.call: on Ctrl-C it gives the child 0.25 s and kills it, leaving its run records half written
    while True:
        try:
            return p.wait()
        except KeyboardInterrupt:
            continue


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aces", description="ACES OS command line")
    p.add_argument("--version", action="version", version=f"aces {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fabric", help="lane manifest from the ACES Server")
    f.add_argument("action", choices=["refresh", "show"])
    f.add_argument("--json", action="store_true")

    po = sub.add_parser("policy", help="operator policy and overrides")
    pos = po.add_subparsers(dest="action", required=True)
    s = pos.add_parser("show"); s.add_argument("--json", action="store_true")
    s = pos.add_parser("mode"); s.add_argument("mode", choices=list(policy.MODES)); s.add_argument("--reason", default="")
    s = pos.add_parser("override"); s.add_argument("component"); s.add_argument("--reason", required=True); s.add_argument("--terms", default=None)

    pr = sub.add_parser("project", help="projects in ~/Work")
    prs = pr.add_subparsers(dest="action", required=True)
    s = prs.add_parser("new"); s.add_argument("name"); s.add_argument("--class", dest="cls", default=None)
    s.add_argument("--company", default=None); s.add_argument("--rd-activity", dest="rd_activity", default=None); s.add_argument("--hypothesis", default=None)
    s = prs.add_parser("show"); s.add_argument("name"); s.add_argument("--json", action="store_true")
    s = prs.add_parser("classify"); s.add_argument("name"); s.add_argument("cls"); s.add_argument("--reason", required=True)
    prs.add_parser("list")
    s = prs.add_parser("add-component"); s.add_argument("name"); s.add_argument("component")
    s = prs.add_parser("add-evidence", help="record an evidence reference (e.g. an aces golden run) in aces.yaml")
    s.add_argument("name"); s.add_argument("ref")
    s = prs.add_parser("add-cad-source", help="record a CAD source (e.g. cad/part.step) in aces.yaml")
    s.add_argument("name"); s.add_argument("ref")

    le = sub.add_parser("ledger", help="the operator ledger")
    les = le.add_subparsers(dest="action", required=True)
    s = les.add_parser("verify"); s.add_argument("--json", action="store_true")
    s = les.add_parser("show"); s.add_argument("-n", type=int, default=20, help="the last N entries (0: all)")
    s.add_argument("--kind", default=None, help="only entries of this kind")
    s.add_argument("--json", action="store_true", help="a JSON array; each entry carries its 1-based position n")
    s = les.add_parser("record", help="append one entry for a tool outside aces-base (e.g. golden.run)")
    s.add_argument("kind", help="dotted lower-case words, e.g. golden.run; policy., project., lane. and ledger. are reserved")
    s.add_argument("--json", dest="json_file", required=True, metavar="FILE|-",
                   help="the payload, a JSON object of at most 65536 bytes (- reads stdin)")
    s.add_argument("--print", dest="print_fmt", choices=["text", "json"], default="text")

    la = sub.add_parser("lane", help="lanes from the manifest")
    las = la.add_subparsers(dest="action", required=True)
    las.add_parser("list")
    s = las.add_parser("test"); s.add_argument("lane")
    s = las.add_parser("bench", help="probe every model lane and record the table (ACES 0.1 Bench)")
    s.add_argument("--out", default=None, help="markdown file to append to (e.g. docs/BENCHMARKS.md)")
    s.add_argument("--refresh", action="store_true", help="refresh the manifest first")
    s.add_argument("--json", action="store_true")

    d = sub.add_parser("doctor", help="check the installation")
    d.add_argument("--offline", action="store_true")

    g = sub.add_parser("golden", help="the golden path: aces-golden from aces-engineering (run, show, verify, compute, spec, init, report)",
                       description="Passes everything after `golden` to aces-golden (or ACES_GOLDEN_CMD); see `aces golden --help`.")
    g.add_argument("args", nargs=argparse.REMAINDER)
    return p


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["golden"]:  # before argparse: REMAINDER cannot pass leading options such as --demo
        return cmd_golden(argv[1:])
    args = build_parser().parse_args(argv)
    cfg = config.load()
    try:
        if args.cmd == "fabric":
            return cmd_fabric(args, cfg)
        if args.cmd == "policy":
            return cmd_policy(args, cfg)
        if args.cmd == "project":
            return cmd_project(args, cfg)
        if args.cmd == "ledger":
            return cmd_ledger(args, cfg)
        if args.cmd == "lane":
            return cmd_lane(args, cfg)
        if args.cmd == "doctor":
            return doctor.run(cfg, args.offline)
    except KeyboardInterrupt:
        return 130
    return 2


if __name__ == "__main__":
    sys.exit(main())
