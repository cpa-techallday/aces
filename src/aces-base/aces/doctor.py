"""`aces doctor`: what is configured, what is reachable, what is missing. Informational; prints a table."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Any

from . import config, fabric, ledger, policy


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=8, check=False)
        return out.returncode, (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr).strip() else ""
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)[:60]


def checks(cfg: dict, offline: bool = False) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    rows.append(("python", "ok", sys.version.split()[0]))
    rows.append(("config", "ok" if (config.ETC / "aces.toml").exists() else "defaults",
                 f"{config.ETC / 'aces.toml'}" + (" + user overrides" if (config.USER_CONFIG / 'aces.toml').exists() else "")))
    pol = policy.load()
    rows.append(("policy", "ok", f"mode={pol.get('mode')} default_class={pol.get('default_project_class')} overrides={len(pol.get('overrides', []))}"))
    ok, n, msg = ledger.verify()
    rows.append(("ledger", "ok" if ok else "FAIL", f"{n} entries, {msg}"))
    age = fabric.age_s(cfg)
    if age is None:
        rows.append(("fabric", "missing", "run `aces fabric refresh`"))
    else:
        stale = age > cfg["fabric"]["max_age_s"]
        rows.append(("fabric", "stale" if stale else "ok", f"manifest age {int(age)} s"))
    srv = cfg["server"]
    if offline:
        rows.append(("server", "skipped", "offline"))
    else:
        for name in ("grid_control", "rig_state", "simlab_mcp", "simlab_console", "mlflow"):
            url = srv.get(name)
            if not url:
                rows.append((name, "unset", ""))
                continue
            ok, msg = fabric.probe(url, srv["timeout_s"])
            rows.append((name, "ok" if ok else "unreachable", f"{url} ({msg})"))
    rows.append(("docker", "ok" if shutil.which("docker") else "missing", _run(["docker", "version", "--format", "{{.Server.Version}}"])[1] if shutil.which("docker") else "install docker"))
    if shutil.which("nvidia-smi"):
        rc, line = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])
        rows.append(("gpu", "ok" if rc == 0 else "error", line))
    else:
        rows.append(("gpu", "none", "nvidia-smi not found (fine on a laptop)"))
    omarchy = shutil.which("omarchy") or os.environ.get("OMARCHY_PATH")
    rows.append(("omarchy", "ok" if omarchy else "absent", str(omarchy or "not an Omarchy desktop; ACES runs engine-first")))
    for agent in ("claude", "codex", "opencode", "hermes"):
        if shutil.which(agent):
            rows.append(("agent", "ok", agent))
            break
    else:
        rows.append(("agent", "none", "no coding agent CLI on PATH"))
    skills = os.path.expanduser("~/.claude/skills/aces-engineering")
    rows.append(("skills", "ok" if os.path.exists(skills) else "absent", skills))
    rows.append(("work", "ok" if config.WORK.exists() else "absent", str(config.WORK)))
    rows.append(golden_row())
    return rows


def golden_row() -> tuple[str, str, str]:
    """Informational: is `aces golden` runnable here, and the last golden.run in the ledger (date, project, run id,
    verdict) when there is one."""
    exe = shutil.which("aces-golden")
    env_cmd = os.environ.get("ACES_GOLDEN_CMD")
    last = None
    try:
        last = next((e for e in reversed(ledger.entries()) if e.get("kind") == "golden.run"), None)
    except SystemExit:  # an unreadable ledger is reported by the ledger row
        pass
    tail = ""
    if last:
        p = last.get("payload") if isinstance(last.get("payload"), dict) else {}
        tail = (f"; last run {str(last.get('ts') or '-')[:10]} {p.get('project') or '-'}/{p.get('run_id') or '-'} "
                f"{p.get('verdict') or '-'}")
    if exe or env_cmd:
        return ("golden", "ok", (exe or f"ACES_GOLDEN_CMD={env_cmd}") + tail)
    return ("golden", "absent", "install aces-engineering" + tail)


def render(rows: list[tuple[str, str, str]]) -> str:
    w = max(len(r[0]) for r in rows)
    s = max(len(r[1]) for r in rows)
    return "\n".join(f"{name:<{w}}  {status:<{s}}  {detail}" for name, status, detail in rows)


def run(cfg: dict, offline: bool = False) -> int:
    rows = checks(cfg, offline)
    print(render(rows))
    bad = [r for r in rows if r[1] in ("FAIL",)]
    return 1 if bad else 0
