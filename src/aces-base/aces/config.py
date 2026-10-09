"""Configuration paths and loading.

Defaults ship in /etc/aces (owned by the aces-base package); the user overrides them in ~/.config/aces.
Environment: ACES_ETC overrides /etc/aces (used by tests and by builds that run without root).
"""
from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any

ETC = Path(os.environ.get("ACES_ETC", "/etc/aces"))
USER_CONFIG = Path(os.environ.get("ACES_USER_CONFIG", Path.home() / ".config" / "aces"))
STATE = Path(os.environ.get("ACES_STATE", Path.home() / ".local" / "state" / "aces"))
WORK = Path(os.environ.get("ACES_WORK", Path.home() / "Work"))

DEFAULTS: dict[str, Any] = {
    "server": {
        "name": "rig",
        "host": "192.168.1.65",
        "grid_control": "http://192.168.1.65:3200",
        "rig_state": "http://192.168.1.65:8010/mcp",
        "simlab_mcp": "http://192.168.1.65:8022/mcp",
        "simlab_console": "http://192.168.1.65:8032",
        "mlflow": "http://192.168.1.65:5000",
        "webodm": "http://127.0.0.1:8090",
        "timeout_s": 4,
    },
    "fabric": {"manifest": str(USER_CONFIG / "fabric.json"), "max_age_s": 900},
    "lanes": {},
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _read_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as e:  # noqa: PERF203
        raise SystemExit(f"aces: cannot read {path}: {e}") from e


def load() -> dict[str, Any]:
    """Effective configuration: built-in defaults <- /etc/aces/aces.toml <- ~/.config/aces/aces.toml."""
    cfg = dict(DEFAULTS)
    cfg = _merge(cfg, _read_toml(ETC / "aces.toml"))
    cfg = _merge(cfg, _read_toml(USER_CONFIG / "aces.toml"))
    return cfg


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"aces: cannot read {path}: {e}") from e


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, path)


def modes_table() -> dict[str, Any]:
    """Mode -> lanes table: /etc/aces/modes.json overridden by ~/.config/aces/modes.json."""
    table = read_json(ETC / "modes.json", {}) or {}
    user = read_json(USER_CONFIG / "modes.json", {}) or {}
    return _merge(table, user)
