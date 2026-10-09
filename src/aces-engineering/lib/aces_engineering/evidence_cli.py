"""aces-evidence: verify simlab R&D evidence bundles and their hash-chained ledger (simlab.evidence_bundle).

  aces-evidence verify [ROOT]         recompute every bundle hash and the chain of ROOT/ledger.jsonl
  aces-evidence bundle FILE...        check one bundle file against its own bundle_sha256 (no ledger needed)
  aces-evidence ls [ROOT]             list the ledger
  aces-evidence build EXP_DIR [--company C] [--operator O] [--note N] [--push TARGET]
                                      write a bundle for a finished experiment into the default root and chain it

ROOT defaults to $SIMLAB_EVIDENCE_ROOT, else ~/.local/share/aces/simlab/evidence. A ledger copied from another
machine (the rig, the workstation) names bundle paths on that machine; `verify` then looks for each bundle at
ROOT/<experiment>/<file name> and reports how many entries it relocated. Relocation never touches the hashes: the
bundle content, its bundle_sha256 and the previous-hash links are checked exactly as simlab checks them.
Exit status 0 when everything verifies, 1 otherwise.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile

from . import prepare_env


def _root(arg: str | None) -> str:
    return os.path.abspath(arg or os.environ["SIMLAB_EVIDENCE_ROOT"])


def verify_result(root: str) -> dict:
    """The verification `verify` prints, as a dict: {"root", "relocated", "entries", "ok", "problems", "head"}, or
    {"root", "entries": 0, "ok": False, "problems": [...]} when ROOT has no ledger. Callers (aces golden) read it
    directly instead of capturing stdout. Leaves simlab.evidence_bundle.EVIDENCE_ROOT pointing at ROOT."""
    import simlab.evidence_bundle as eb

    eb.EVIDENCE_ROOT = root
    entries = eb.read_ledger()
    if not entries:
        return {"root": root, "entries": 0, "ok": False, "problems": [{"problem": "no ledger.jsonl here"}]}
    relocated = 0
    for ent in entries:
        if not os.path.isfile(ent["path"]):
            cand = os.path.join(root, ent["experiment"], re.split(r"[\\/]", ent["path"])[-1])
            if os.path.isfile(cand):
                ent["path"] = cand
                relocated += 1
    with tempfile.TemporaryDirectory() as td:  # verify_ledger reads EVIDENCE_ROOT/ledger.jsonl: hand it the relocated copy
        with open(os.path.join(td, "ledger.jsonl"), "w", encoding="utf-8") as f:
            f.writelines(json.dumps(e) + "\n" for e in entries)
        eb.EVIDENCE_ROOT = td
        try:
            res = eb.verify_ledger()
        finally:
            eb.EVIDENCE_ROOT = root
    return {"root": root, "relocated": relocated, **res}


def verify(root: str) -> int:
    res = verify_result(root)
    print(json.dumps(res, indent=2))
    return 0 if res["ok"] else 1


def check_bundle(paths: list) -> int:
    from simlab.evidence_bundle import canonical_sha256

    status = 0
    for p in paths:
        with open(p, "r", encoding="utf-8") as f:
            b = json.load(f)
        got = canonical_sha256({k: v for k, v in b.items() if k != "bundle_sha256"})
        ok = got == b.get("bundle_sha256")
        status |= 0 if ok else 1
        exp = (b.get("experiment") or {}).get("id")
        print(f"{'ok      ' if ok else 'MISMATCH'} {p}\n         experiment {exp}, bundle_sha256 {b.get('bundle_sha256')}, "
              f"recomputed {got}, previous {b.get('previous_sha256')}")
    return status


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    prepare_env()
    cmd, rest = argv[0], argv[1:]
    if cmd == "verify":
        return verify(_root(rest[0] if rest else None))
    if cmd == "bundle":
        if not rest:
            print("aces-evidence bundle: give one or more bundle files", file=sys.stderr)
            return 2
        return check_bundle(rest)
    if cmd in ("ls", "build"):
        import simlab.evidence_bundle as eb

        if cmd == "ls":
            eb.EVIDENCE_ROOT = _root(rest[0] if rest else None)
            rest = []
        eb.main([cmd] + rest)
        return 0
    print(f"aces-evidence: unknown command {cmd!r} (verify, bundle, ls, build)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
