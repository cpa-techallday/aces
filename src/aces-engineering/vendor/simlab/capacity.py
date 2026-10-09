"""GPU capacity truth table: rig-state (idle, memory, processes) joined with GRID CONTROL (mode,
derated) and the redis leases. Falls back to local nvidia-smi when no rig endpoints are set.

Environment:
  SIMLAB_RIG_STATE_URL     e.g. http://192.168.1.65:8010/mcp   (read-only rig-state MCP)
  SIMLAB_GRIDCONTROL_URL   e.g. http://192.168.1.65:3200       (GET /api/status, /api/modes)
  SIMLAB_GPU_POOL          explicit comma list overriding the mode's pool, e.g. 0,1,2,3
  SIMLAB_MODE              mode name when GRID CONTROL is not reachable (default: workstation)
  SIMLAB_IDLE_MEM_MIB      memory threshold for 'idle' on the local probe (default 4096)
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import urllib.request
from typing import Dict, List, Optional

MODES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modes.json")


def load_modes() -> dict:
    with open(MODES_PATH, "r", encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}


def _get_json(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def rig_state_gpus(url: str, timeout: float = 15.0) -> List[dict]:
    """Call rig-state's rig_gpus tool (streamable HTTP, stateless) and return its GPU rows."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "rig_gpus", "arguments": {}}}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json",
                                                           "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read().decode("utf-8")
    payload = None
    for line in text.splitlines():
        if line.startswith("data:"):
            payload = json.loads(line[5:].strip())
            break
    if payload is None:
        payload = json.loads(text)
    return payload["result"]["structuredContent"]["gpus"]


def gridcontrol_status(url: str) -> dict:
    return _get_json(url.rstrip("/") + "/api/status")


def gridcontrol_modes(url: str) -> dict:
    return _get_json(url.rstrip("/") + "/api/modes")


def local_gpus(idle_mem_mib: Optional[int] = None) -> List[dict]:
    """Same row shape as rig-state, from the local nvidia-smi."""
    idle_mem = int(idle_mem_mib or os.environ.get("SIMLAB_IDLE_MEM_MIB", 4096))
    try:
        q = subprocess.run(["nvidia-smi", "--query-gpu=index,name,memory.used,memory.total,utilization.gpu,temperature.gpu,uuid",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=20)
        a = subprocess.run(["nvidia-smi", "--query-compute-apps=gpu_uuid,pid,used_memory,process_name",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=20)
    except Exception:
        return []
    procs: Dict[str, list] = {}
    for line in a.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4:
            procs.setdefault(parts[0], []).append({"pid": int(parts[1]) if parts[1].isdigit() else parts[1],
                                                    "usedMemMiB": int(parts[2]) if parts[2].isdigit() else 0,
                                                    "processName": parts[3]})
    # On Windows (WDDM) nvidia-smi lists every process holding a GPU context (browsers, the shell,
    # Fusion...) with memory "[N/A]", so the process list says nothing about compute load; the
    # memory threshold is the only usable idle rule there. On Linux both rules apply.
    wddm = platform.system() == "Windows"
    rows = []
    for line in q.stdout.strip().splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) < 7:
            continue
        used, total = int(p[2]), int(p[3])
        pr = procs.get(p[6], [])
        rows.append({"index": int(p[0]), "name": p[1], "memUsedMiB": used, "memTotalMiB": total,
                     "memUsedPct": round(100.0 * used / max(1, total), 2), "utilPct": int(p[4]) if p[4].isdigit() else 0,
                     "tempC": int(p[5]) if p[5].isdigit() else 0, "processes": [] if wddm else pr,
                     "idle": (wddm or len(pr) == 0) and (used < idle_mem)})
    return rows


def gpu_table(rig_state_url: Optional[str] = None, gridcontrol_url: Optional[str] = None,
              pool: Optional[List[int]] = None, leases: Optional[Dict[int, str]] = None,
              mode: Optional[str] = None) -> dict:
    rig_state_url = rig_state_url if rig_state_url is not None else os.environ.get("SIMLAB_RIG_STATE_URL")
    gridcontrol_url = gridcontrol_url if gridcontrol_url is not None else os.environ.get("SIMLAB_GRIDCONTROL_URL")
    errors = []
    gpus: List[dict] = []
    source = "local nvidia-smi"
    if rig_state_url:
        try:
            gpus = rig_state_gpus(rig_state_url)
            source = f"rig-state {rig_state_url}"
        except Exception as exc:
            errors.append(f"rig-state: {exc}")
    if not gpus:
        gpus = local_gpus()
    gc = None
    if gridcontrol_url:
        try:
            gc = gridcontrol_status(gridcontrol_url)
        except Exception as exc:
            errors.append(f"gridcontrol: {exc}")
    derated = {g["index"] for g in gc.get("gpus", []) if g.get("derated")} if gc else set()
    mode = mode or (gc.get("mode") if gc else None) or os.environ.get("SIMLAB_MODE", "workstation")
    if pool is None:
        env_pool = os.environ.get("SIMLAB_GPU_POOL")
        pool = [int(x) for x in env_pool.split(",") if x.strip()] if env_pool else load_modes().get(mode, {}).get("sim_gpus", [])
    leases = leases or {}
    rows = []
    for g in gpus:
        i = int(g["index"])
        row = dict(g)
        row["derated"] = i in derated
        row["in_pool"] = i in pool
        row["lease"] = leases.get(i)
        row["available"] = bool(row["in_pool"] and g.get("idle") and not row["derated"] and leases.get(i) is None)
        rows.append(row)
    return {"source": source, "mode": mode, "pool": pool, "derated": sorted(derated),
            "gridcontrol_queue": gc.get("queue") if gc else None,
            "gpus": rows, "available": [r["index"] for r in rows if r["available"]], "errors": errors}


def summary_lines(table: dict) -> List[str]:
    out = [f"source {table['source']} | mode {table['mode']} | pool {table['pool']} | derated {table['derated']} | available {table['available']}"]
    for g in table["gpus"]:
        flag = "AVAILABLE" if g["available"] else ("lease:" + str(g["lease"]) if g.get("lease") else ("derated" if g["derated"] else ("busy" if not g.get("idle") else "not in pool")))
        out.append(f"  GPU{g['index']} {g.get('name', '?')[:22]:22s} {g.get('memUsedMiB', 0):>6}/{g.get('memTotalMiB', 0)} MiB util {g.get('utilPct', 0):>3}%  {flag}")
    if table["errors"]:
        out.append("  errors: " + "; ".join(table["errors"]))
    return out
