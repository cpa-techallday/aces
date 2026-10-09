"""The `aces` command."""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__, config, doctor, fabric, lane, ledger, policy, project


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
    return 2


def cmd_ledger(args, cfg) -> int:
    if args.action == "verify":
        ok, n, msg = ledger.verify()
        print(f"{'ok' if ok else 'FAIL'}: {n} entries, {msg} ({ledger.path()})")
        return 0 if ok else 1
    if args.action == "show":
        for e in ledger.entries()[-args.n:]:
            print(f"{e.get('ts')} {e.get('kind'):<20} {e.get('operator')}@{e.get('host')} {json.dumps(e.get('payload'), ensure_ascii=False)[:160]}")
        return 0
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
    return 2


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

    le = sub.add_parser("ledger", help="the operator ledger")
    les = le.add_subparsers(dest="action", required=True)
    les.add_parser("verify")
    s = les.add_parser("show"); s.add_argument("-n", type=int, default=20)

    la = sub.add_parser("lane", help="lanes from the manifest")
    las = la.add_subparsers(dest="action", required=True)
    las.add_parser("list")
    s = las.add_parser("test"); s.add_argument("lane")

    d = sub.add_parser("doctor", help="check the installation")
    d.add_argument("--offline", action="store_true")
    return p


def main(argv=None) -> int:
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
