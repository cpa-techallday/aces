"""Solver registry: which adapters exist, what physics they cover, which image runs them,
and the validation record that makes their results citable.

Rows come from two places, read again on every call (nothing is cached, so a deploy that only adds or changes a
drop-in file needs no restart):

  solvers.json      REGISTRY_PATH, the registry the rig has always had (the rig operator edits it by hand)
  solvers.d/*.json  DROPIN_DIR, drop-in files read in sorted file-name order. Each is a JSON object of rows; keys
                    starting with "_" are comments. A row in a drop-in file replaces a row of the same name in
                    solvers.json. One name defined in two drop-in files is an error that names both files.

Drop-ins let a deploy add solvers without rewriting solvers.json. Two consequences follow:
  * Writers that save load_registry() back to solvers.json (surrogate/cli.py `_register` does) leave stale copies of
    drop-in rows there. Those copies are ignored: on every read the drop-in row replaces them.
  * Rolling back the drop-in (deleting its file) does not remove such copies. After a rollback, a stale copy in
    solvers.json is what the registry serves for that name; check solvers.json for rows only a drop-in should define.
"""
from __future__ import annotations

import importlib
import json
import os
from typing import Dict

REGISTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "solvers.json")
DROPIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "solvers.d")   # independent of REGISTRY_PATH


def dropin_rows() -> Dict[str, dict]:
    """Every row of every solvers.d/*.json, files in sorted name order; {} when the directory does not exist.

    Keys starting with "_" are skipped (comments). A name defined in two drop-in files raises ValueError naming both
    files, so a conflict is never resolved silently by file order. Like glob's `*.json`, hidden files are ignored."""
    if not os.path.isdir(DROPIN_DIR):
        return {}
    rows: Dict[str, dict] = {}
    seen: Dict[str, str] = {}
    for fn in sorted(os.listdir(DROPIN_DIR)):
        path = os.path.join(DROPIN_DIR, fn)
        if fn.startswith(".") or not fn.endswith(".json") or not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"solvers.d/{fn} must be a JSON object of solver rows")
        for name, row in data.items():
            if name.startswith("_"):
                continue
            if name in seen:
                raise ValueError(f"solver {name!r} is defined in two drop-in files: solvers.d/{seen[name]} and "
                                 f"solvers.d/{fn}; remove it from one of them")
            if not isinstance(row, dict):
                raise ValueError(f"solvers.d/{fn}: row {name!r} must be a JSON object")
            seen[name] = fn
            rows[name] = row
    return rows


def load_registry() -> Dict[str, dict]:
    """solvers.json first, then the drop-in rows on top: a drop-in row replaces a same-named row."""
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        reg = json.load(f)
    reg.update(dropin_rows())
    return reg


def get(name: str) -> dict:
    reg = load_registry()
    if name not in reg:
        raise KeyError(f"unknown solver {name!r}; known: {', '.join(sorted(reg))}")
    return reg[name]


def adapter(name: str):
    entry = get(name)
    mod = importlib.import_module(entry["module"])
    return mod.Adapter()


def check_physics(name: str, physics: str) -> None:
    entry = get(name)
    if physics not in entry.get("physics", []):
        raise ValueError(f"solver {name} covers {entry.get('physics')} not {physics!r}")
