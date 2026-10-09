"""Lanes: list them from the manifest and probe a model lane.

`aces lane test <id>` is the qualification probe for a model lane: it lists models, runs one short chat completion,
then one request with a tool definition to see whether the server returns a structured tool call. It reports what
it measured and records the result in the ledger. It does not judge; the thresholds are the operator's.
"""
from __future__ import annotations

import time
import urllib.error
from typing import Any

from . import fabric, ledger


def lanes(cfg: dict) -> list[dict[str, Any]]:
    m = fabric.read(cfg)
    return list((m or {}).get("lanes") or [])


def find(cfg: dict, lane_id: str) -> dict[str, Any]:
    for lane in lanes(cfg):
        if lane.get("id") == lane_id:
            return lane
    raise SystemExit(f"aces: no lane {lane_id!r} in the manifest (run `aces fabric refresh`; `aces lane list`)")


def test_model_lane(cfg: dict, lane: dict[str, Any], prompt: str = "Reply with the single word OK.") -> dict[str, Any]:
    endpoint = lane.get("endpoint")
    if not endpoint:
        return {"lane": lane.get("id"), "ok": False, "error": "lane has no endpoint configured"}
    base = endpoint.rstrip("/")
    timeout = max(30, cfg["server"]["timeout_s"] * 5)
    result: dict[str, Any] = {"lane": lane.get("id"), "endpoint": base, "ok": False}
    try:
        t0 = time.perf_counter()
        models = fabric.http_json(base + "/models", timeout)
        result["models"] = [m.get("id") for m in (models or {}).get("data", [])][:10]
        result["models_latency_s"] = round(time.perf_counter() - t0, 3)
    except Exception as e:  # noqa: BLE001
        result["error"] = f"models: {str(e)[:120]}"
        return result
    model = lane.get("model") or (result["models"][0] if result["models"] else None)
    if not model:
        result["error"] = "no model id to test"
        return result
    result["model"] = model
    try:
        t0 = time.perf_counter()
        reply = fabric.http_json(base + "/chat/completions", timeout, "POST", {
            "model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 32, "temperature": 0,
        })
        dt = time.perf_counter() - t0
        choice = (reply.get("choices") or [{}])[0]
        text = ((choice.get("message") or {}).get("content") or "").strip()
        usage = reply.get("usage") or {}
        result["completion"] = {"latency_s": round(dt, 3), "text": text[:80], "usage": usage}
        if usage.get("completion_tokens") and dt > 0:
            result["completion"]["tokens_per_s"] = round(usage["completion_tokens"] / dt, 1)
    except Exception as e:  # noqa: BLE001
        result["error"] = f"completion: {str(e)[:120]}"
        return result
    try:
        t0 = time.perf_counter()
        reply = fabric.http_json(base + "/chat/completions", timeout, "POST", {
            "model": model, "max_tokens": 128, "temperature": 0,
            "messages": [{"role": "user", "content": "What is the rig capacity? Use the tool."}],
            "tools": [{"type": "function", "function": {
                "name": "rig_capacity", "description": "Return free GPUs for the active mode",
                "parameters": {"type": "object", "properties": {"mode": {"type": "string"}}, "required": []}}}],
            "tool_choice": "auto",
        })
        dt = time.perf_counter() - t0
        choice = (reply.get("choices") or [{}])[0]
        calls = (choice.get("message") or {}).get("tool_calls") or []
        result["tool_call"] = {"latency_s": round(dt, 3), "structured": bool(calls),
                               "name": (calls[0].get("function") or {}).get("name") if calls else None}
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        result["tool_call"] = {"error": f"http {e.code}", "server_says": body[:240]}
    except Exception as e:  # noqa: BLE001
        result["tool_call"] = {"error": str(e)[:120]}
    result["ok"] = "error" not in result
    result["tool_calling"] = bool((result.get("tool_call") or {}).get("structured"))
    ledger.append("lane.test", result)
    return result


def bench(cfg: dict) -> dict[str, Any]:
    """Probe every model lane in the manifest (the "ACES 0.1 Bench" lane qualification) and return a report.

    Lanes without an endpoint are listed as untested rather than skipped silently. Every probe is a ledger entry.
    """
    from datetime import datetime, timezone

    m = fabric.read(cfg) or {}
    results: list[dict[str, Any]] = []
    for lane in m.get("lanes") or []:
        if lane.get("kind") != "model":
            continue
        if lane.get("endpoint"):
            results.append(test_model_lane(cfg, lane))
        else:
            results.append({"lane": lane.get("id"), "model": lane.get("model"), "endpoint": None, "ok": False,
                            "error": "no endpoint configured", "tool_calling": False})
    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "server": (m.get("server") or {}).get("host"),
        "mode": (m.get("mode") or {}).get("name"),
        "manifest_generated": m.get("generated"),
        "results": results,
    }


def bench_markdown(report: dict[str, Any]) -> str:
    """One dated section with a table, suitable for appending to docs/BENCHMARKS.md."""
    lines = [
        f"## {report['generated']}  server {report.get('server')}  mode {report.get('mode')}",
        "",
        f"Manifest generated {report.get('manifest_generated')}. Probe: list models, one 32-token completion at temperature 0, "
        "one request with a tool definition. Tool calling counts only when the server returns a structured tool call.",
        "",
        "| Lane | Model | Endpoint | Models list | Completion | Reply | Tool call |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in report["results"]:
        c = r.get("completion") or {}
        t = r.get("tool_call") or {}
        if r.get("error") and not c:
            completion = f"failed: {r['error']}"
        elif c:
            tps = c.get("tokens_per_s")
            completion = f"{c.get('latency_s')} s" + (f", {tps} tok/s" if tps else "")
        else:
            completion = "n/a"
        reply = (c.get("text") or "").replace("|", "/").replace("\n", " ")[:40] if c else ""
        if t.get("structured"):
            tool = f"structured ({t.get('name')}) in {t.get('latency_s')} s"
        elif t.get("error"):
            tool = f"rejected: {t.get('error')}" + (f" ({t.get('server_says')[:90]})" if t.get("server_says") else "")
        elif t:
            tool = f"no structured call (answered in text, {t.get('latency_s')} s)"
        else:
            tool = "not probed"
        models = f"{r.get('models_latency_s')} s" if r.get("models_latency_s") is not None else "n/a"
        lines.append(f"| {r.get('lane')} | {r.get('model')} | {r.get('endpoint')} | {models} | {completion} | {reply} | {tool} |")
    lines.append("")
    return "\n".join(lines) + "\n"
