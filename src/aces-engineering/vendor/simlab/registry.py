"""Solver registry: which adapters exist, what physics they cover, which image runs them,
and the validation record that makes their results citable."""
from __future__ import annotations

import importlib
import json
import os
from typing import Dict

REGISTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "solvers.json")


def load_registry() -> Dict[str, dict]:
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


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
