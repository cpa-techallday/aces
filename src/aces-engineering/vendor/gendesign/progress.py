"""Progress beacon for long solves: a small JSON file the console reads while a case runs.

The worker exports SIMLAB_PROGRESS_FILE=<run_dir>/progress.json for every stage; solvers call
`report(...)` at natural checkpoints (PCG every N iterations, every SIMP iteration, every PyFR
output interval, every FNO epoch). Writes are rate-limited and atomic (tmp + replace), and a
missing env var makes every call a no-op, so library code never pays for it elsewhere.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from typing import Any, Dict, Optional

_LAST = {"t": 0.0}
_STATE: Dict[str, Any] = {}


def enabled() -> bool:
    return bool(os.environ.get("SIMLAB_PROGRESS_FILE"))


def report(phase: str, step: Optional[int] = None, total: Optional[int] = None, min_interval_s: float = 1.0, **fields) -> None:
    """Record the current step of the running solver. `fields` are free-form numbers/strings
    (residual, compliance, t, ...); the last value of each key is kept across calls."""
    path = os.environ.get("SIMLAB_PROGRESS_FILE")
    if not path:
        return
    now = time.time()
    done = total is not None and step is not None and step >= total
    if now - _LAST["t"] < min_interval_s and not done and phase == _STATE.get("phase"):
        return
    _LAST["t"] = now
    _STATE.update({k: v for k, v in fields.items() if isinstance(v, (int, float, str, bool)) or v is None})
    _STATE.update({"phase": phase, "step": step, "total": total,
                   "fraction": (float(step) / float(total)) if (total and step is not None) else None,
                   "updated_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "pid": os.getpid()})
    if "started_utc" not in _STATE:
        _STATE["started_utc"] = _STATE["updated_utc"]
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_STATE, f)
        os.replace(tmp, path)
    except Exception:
        pass


def finish(phase: str = "done", **fields) -> None:
    report(phase, step=1, total=1, min_interval_s=0.0, **fields)
