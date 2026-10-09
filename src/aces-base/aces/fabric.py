"""The lane manifest: what the ACES Server offers right now, as one document every client reads.

`aces fabric refresh` reads GRID CONTROL (/api/status, /api/modes) and the read-only rig-state MCP, maps the active
mode to lanes through /etc/aces/modes.json, and writes ~/.config/aces/fabric.json. It never writes to the server:
mode switching stays a human act in GRID CONTROL. Unknown endpoints stay null; nothing is invented.
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config, policy


def http_json(url: str, timeout: float, method: str = "GET", body: Any = None, headers: dict | None = None) -> Any:
    data = None
    hdrs = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (local LAN endpoints from config)
        raw = resp.read().decode("utf-8", "replace")
    # streamable-HTTP MCP servers answer with an SSE "data:" line
    if raw.lstrip().startswith("event:") or raw.lstrip().startswith("data:"):
        for line in raw.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        return None
    return json.loads(raw) if raw.strip() else None


def probe(url: str, timeout: float) -> tuple[bool, str]:
    """Reachability only: any HTTP answer counts, whatever the body (consoles serve HTML; MCP servers answer a bare
    GET with 404 or 406). Only a connection failure or timeout is 'unreachable'."""
    req = urllib.request.Request(url, method="GET", headers={"Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return True, f"http {resp.status}"
    except urllib.error.HTTPError as e:
        return True, f"http {e.code}"
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
        return False, str(getattr(e, "reason", e))[:80]


def rig_tool(cfg: dict, name: str, arguments: dict | None = None) -> Any:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}}
    reply = http_json(cfg["server"]["rig_state"], cfg["server"]["timeout_s"], "POST", body,
                      {"Accept": "application/json, text/event-stream"})
    if not isinstance(reply, dict):
        return None
    result = reply.get("result") or {}
    if isinstance(result, dict) and result.get("structuredContent"):
        return result["structuredContent"]
    for block in (result.get("content") or []) if isinstance(result, dict) else []:
        if block.get("type") == "text":
            try:
                return json.loads(block["text"])
            except json.JSONDecodeError:
                return block["text"]
    return result


def build_manifest(cfg: dict) -> dict[str, Any]:
    srv = cfg["server"]
    t = srv["timeout_s"]
    manifest: dict[str, Any] = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "server": {k: srv.get(k) for k in ("name", "host", "grid_control", "rig_state", "simlab_mcp", "simlab_console", "mlflow")},
        "health": {},
        "mode": None,
        "gpus": [],
        "lanes": [],
        "policy": {"mode": policy.load().get("mode"), "default_project_class": policy.load().get("default_project_class")},
        "notes": [],
    }
    # GRID CONTROL
    try:
        status = http_json(srv["grid_control"] + "/api/status", t)
        modes = http_json(srv["grid_control"] + "/api/modes", t) or {}
        manifest["health"]["grid_control"] = "ok"
        mode = status.get("mode")
        manifest["mode"] = {
            "name": mode,
            "detected": status.get("detected_mode"),
            "label": (modes.get(mode) or {}).get("label") if isinstance(modes, dict) else None,
            "switching": status.get("switching"),
            "queue": status.get("queue"),
        }
        manifest["gpus"] = [
            {"index": g.get("index"), "util": g.get("util"), "mem_used_mb": g.get("mem_used_mb"),
             "temp_c": g.get("temp_c"), "derated": g.get("derated")}
            for g in status.get("gpus", [])
        ]
        # consoles GRID CONTROL publishes about itself (mc_console is bound to the rig's loopback; gridwatch is LAN)
        discovered = {k: status.get(k) for k in ("mc_console", "gridwatch") if status.get(k)}
        if discovered:
            manifest["server"]["discovered"] = discovered
        if status.get("auto_enabled") is not None:
            manifest["mode"]["auto_switching"] = status.get("auto_enabled")
    except Exception as e:  # noqa: BLE001
        manifest["health"]["grid_control"] = f"unreachable: {str(e)[:80]}"
    # rig-state (read-only)
    try:
        gpus = rig_tool(cfg, "rig_gpus")
        manifest["health"]["rig_state"] = "ok"
        if isinstance(gpus, dict) and "gpus" in gpus:
            seen = {g["index"]: g for g in manifest["gpus"] if isinstance(g.get("index"), int)}
            for g in gpus["gpus"]:
                idx = g.get("index")
                if idx in seen:
                    seen[idx]["processes"] = g.get("processes")
                    seen[idx]["idle"] = g.get("idle")
    except Exception as e:  # noqa: BLE001
        manifest["health"]["rig_state"] = f"unreachable: {str(e)[:80]}"
    for name in ("simlab_mcp", "simlab_console", "mlflow"):
        if srv.get(name):
            ok, msg = probe(srv[name], t)
            manifest["health"][name] = msg if ok else f"unreachable: {msg}"
    # lanes from the modes table
    table = config.modes_table()
    mode_name = (manifest["mode"] or {}).get("name") if manifest["mode"] else None
    entry = table.get(mode_name) if mode_name else None
    if entry:
        for lane in entry.get("lanes", []):
            lane = dict(lane)
            lane.setdefault("class", "local")
            if lane.get("kind") == "sim" and not lane.get("endpoint"):
                lane["endpoint"] = srv.get("simlab_mcp")
            manifest["lanes"].append(lane)
    elif mode_name:
        manifest["notes"].append(f"mode {mode_name!r} has no entry in modes.json; no lanes derived")
    else:
        manifest["notes"].append("server mode unknown; no lanes derived")
    # lanes offered in every mode (CPU-resident services), only when the server actually answered
    if mode_name:
        for lane in (table.get("_always") or {}).get("lanes", []):
            lane = dict(lane)
            lane.setdefault("class", "local")
            manifest["lanes"].append(lane)
    for lane in manifest["lanes"]:
        if not lane.get("endpoint"):
            manifest["notes"].append(f"lane {lane.get('id')} has no endpoint configured yet")
    # local lanes declared in config
    for lane_id, lane in (cfg.get("lanes") or {}).items():
        lane = dict(lane)
        lane["id"] = lane_id
        lane.setdefault("class", "local")
        manifest["lanes"].append(lane)
    return manifest


def manifest_path(cfg: dict) -> Path:
    return Path(cfg["fabric"]["manifest"])


def refresh(cfg: dict) -> dict[str, Any]:
    m = build_manifest(cfg)
    config.write_json(manifest_path(cfg), m)
    return m


def read(cfg: dict) -> dict[str, Any] | None:
    return config.read_json(manifest_path(cfg), None)


def age_s(cfg: dict) -> float | None:
    p = manifest_path(cfg)
    if not p.exists():
        return None
    return time.time() - p.stat().st_mtime
