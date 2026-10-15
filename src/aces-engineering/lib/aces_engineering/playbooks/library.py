"""Playbook library: load the versioned per-class YAML playbooks shipped in this package.

The YAMLs are data (stage order and gates are data, not code): this module only reads them.
PyYAML is imported lazily so importing the package stays stdlib-only.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from . import CLASSES, SCHEMA

PLAYBOOK_DIR = Path(__file__).resolve().parent


class PlaybookError(ValueError):
    """A playbook file that is missing, unreadable, or not an aces-playbook document."""


def playbook_path(cls: str) -> Path:
    return PLAYBOOK_DIR / f"{cls}.yaml"


@lru_cache(maxsize=None)
def _load_raw(cls: str) -> dict:
    """The parsed playbook document (cached); callers get a deep copy via `load`."""
    import yaml

    if cls not in CLASSES:
        raise PlaybookError(f"unknown part class {cls!r} (known: {', '.join(CLASSES)})")
    path = playbook_path(cls)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PlaybookError(f"playbook not found: {path}") from None
    except yaml.YAMLError as exc:
        raise PlaybookError(f"{path.name}: cannot parse YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise PlaybookError(f"{path.name}: not a mapping")
    if data.get("schema") != SCHEMA:
        raise PlaybookError(f"{path.name}: schema must be {SCHEMA}, got {data.get('schema')!r}")
    if data.get("class") != cls:
        raise PlaybookError(f"{path.name}: class must be {cls!r}, got {data.get('class')!r}")
    return data


def load(cls: str) -> dict:
    """Load and minimally sanity-check the playbook for a class (a fresh deep copy per call)."""
    import copy

    return copy.deepcopy(_load_raw(cls))


def load_all() -> dict[str, dict]:
    """Every shipped playbook, keyed by class."""
    return {cls: load(cls) for cls in CLASSES}
