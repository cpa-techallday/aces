"""A stand-in `aces` CLI for the tests, run as [sys.executable, this file] through ACES_CAD_ACES_CMD.

FAKE_ACES_MODE=new (default) behaves like an aces-base that has `ledger record` and `project add-cad-source`; old
behaves like aces-base 0.1.0, which has neither. Every call is appended to FAKE_ACES_LOG as a JSON line
{"argv", "stdin"}. Output is fixture output; nothing here writes a real ledger.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _log(argv: list[str], stdin: str) -> None:
    path = os.environ.get("FAKE_ACES_LOG")
    if path:
        with open(path, "ab") as fh:
            fh.write((json.dumps({"argv": argv, "stdin": stdin}, ensure_ascii=False) + "\n").encode("utf-8"))


def _unscalar(value: str):
    """aces-base's scalar reading (aces/project.py _unscalar)."""
    if value == "null":
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _load(path: Path) -> dict:
    """The top-level scalars of aces.yaml, read as aces-base reads them (the last duplicate key wins)."""
    data = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or raw.startswith(" "):
            continue
        key, _, value = raw.partition(":")
        value = value.strip()
        data[key.strip()] = None if value == "" else ([] if value == "[]" else _unscalar(value))
    return data


def main(argv: list[str]) -> int:
    mode = os.environ.get("FAKE_ACES_MODE", "new")
    stdin = ""
    if argv[:2] == ["ledger", "record"] and "--help" not in argv:
        stdin = sys.stdin.buffer.read().decode("utf-8")
    _log(argv, stdin)
    if argv[:3] == ["project", "show", "--json"] and len(argv) == 4:
        path = Path(os.environ.get("ACES_WORK", str(Path.home() / "Work"))) / argv[3] / "aces.yaml"
        if not path.exists():
            print(f"aces: no project at {path}", file=sys.stderr)
            return 1
        data = _load(path)
        print(json.dumps({"name": data.get("name"), "class": data.get("class")}))
        return 0
    if argv[:2] == ["ledger", "record"]:
        if mode != "new":
            print("usage: aces ledger [-h] {verify,show} ...\naces ledger: error: argument action: invalid choice: "
                  "'record' (choose from 'verify', 'show')", file=sys.stderr)
            return 2
        if "--help" in argv:
            print("usage: aces ledger record [-h] [--json FILE|-] [--print {text,json}] KIND (fixture)")
            return 0
        if len(argv) >= 5 and argv[3:5] == ["--json", "-"]:
            print(json.dumps({"recorded": True, "n": 1, "kind": argv[2],
                              "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                              "hash": hashlib.sha256(stdin.encode("utf-8")).hexdigest(), "prev": "0" * 64,
                              "path": "fake-ledger.jsonl (fixture)"}))
            return 0
    if argv[:2] == ["project", "add-cad-source"]:
        if mode != "new":
            print("aces project: error: argument action: invalid choice: 'add-cad-source' (fixture)", file=sys.stderr)
            return 2
        if "--help" in argv:
            print("usage: aces project add-cad-source [-h] NAME REF (fixture)")
            return 0
        if len(argv) == 4:
            print(f"CAD source {argv[3]} recorded against {argv[2]}")
            return 0
    print(f"aces: error: unrecognised arguments (fake aces): {' '.join(argv)}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
