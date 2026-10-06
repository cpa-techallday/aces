"""Isaac Sim layered health probe for ACES.

Mirrors the isaac-sim plugin contract: GPU layer, install layer, then — only when
both pass — a deep layer that actually boots a minimal headless ``SimulationApp``.
Every layer reports structured results; failures are ``environment_error`` with the
official guidance, never silently normal.  No GUI is ever launched.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

GPU_GUIDANCE = (
    "NVIDIA driver/GPU not detected. Install the NVIDIA driver per the Isaac Sim "
    "requirements page (docs.isaacsim.omniverse.nvidia.com -> Requirements)."
)
INSTALL_GUIDANCE = (
    "Isaac Sim 5.0+ workstation install not found. Install per the Isaac Sim download "
    "page, or set ISAAC_SIM_PATH to the install root (the directory containing "
    "python.bat on Windows / python.sh on Linux)."
)

_WINDOWS_ROOTS = (
    r"%USERPROFILE%\isaacsim",
    r"%USERPROFILE%\IsaacSim",
    r"C:\isaacsim",
    r"C:\isaac-sim",
)
_LINUX_ROOTS = (
    "~/isaacsim",
    "~/IsaacSim",
    "~/.local/share/ov/pkg",
    "/opt/isaacsim",
    "/opt/isaac-sim",
)

_deep_cache: dict[str, dict] = {}


def _err(message: str) -> dict:
    return {"ok": False, "error": {"type": "environment_error", "message": message}}


def probe_gpu() -> dict:
    """GPU layer: nvidia-smi first (name + driver + CUDA), degraded flag when NVML fails.

    A WMI-level check can prove Windows sees an adapter but not that Vulkan/CUDA
    works; when nvidia-smi fails the layer reports degraded=true and the deep
    probe is the arbiter.
    """
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode == 0 and out.stdout.strip():
            gpus = [line.split(", ") for line in out.stdout.strip().splitlines()]
            return {"ok": True, "degraded": False,
                    "gpus": [{"name": n, "driver": d} for n, d in gpus]}
        return {"ok": False, "degraded": True,
                "detail": (out.stderr or out.stdout).strip() or "nvidia-smi failed",
                "guidance": GPU_GUIDANCE}
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "degraded": True, "detail": str(e), "guidance": GPU_GUIDANCE}


def probe_install() -> dict:
    """Install layer: ISAAC_SIM_PATH, then the standard roots for this OS."""
    candidates: list[Path] = []
    env = os.environ.get("ISAAC_SIM_PATH")
    if env:
        candidates.append(Path(env))
    if platform.system() == "Windows":
        candidates += [Path(os.path.expandvars(p)) for p in _WINDOWS_ROOTS]
        ov = Path(os.environ.get("LOCALAPPDATA", "")) / "ov" / "pkg"
        if ov.is_dir():
            candidates += sorted(ov.glob("isaac-sim-*"))
        launcher = "python.bat"
    else:
        candidates += [Path(p).expanduser() for p in _LINUX_ROOTS if "pkg" not in p]
        pkg = Path("~/.local/share/ov/pkg").expanduser()
        if pkg.is_dir():
            candidates += sorted(pkg.glob("isaac-sim-*"))
        launcher = "python.sh"

    for root in candidates:
        if (root / launcher).is_file():
            return {"ok": True, "root": str(root), "launcher": str(root / launcher)}
    return {**_err(INSTALL_GUIDANCE), "searched": [str(c) for c in candidates]}


DEEP_SCRIPT = """import json, sys
from isaacsim import SimulationApp
app = SimulationApp({"headless": True})
try:
    from omni.isaac.core.utils.extensions import get_isaac_sim_version  # best effort
    ver = get_isaac_sim_version()
except Exception:
    ver = "unknown"
print("ISAAC_DEEP_OK " + json.dumps({"version": ver}))
app.close()
"""


def probe_deep(root: str, timeout_seconds: int = 300) -> dict:
    """Deep layer: boot a minimal headless SimulationApp in a subprocess (cached per root)."""
    if root in _deep_cache:
        return _deep_cache[root]
    install = Path(root)
    launcher = install / ("python.bat" if platform.system() == "Windows" else "python.sh")
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "deep_probe.py"
        script.write_text(DEEP_SCRIPT, encoding="utf-8")
        try:
            out = subprocess.run([str(launcher), str(script)], capture_output=True, text=True,
                                 timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            res = {"ok": False, "error": {"type": "timeout",
                   "message": f"deep probe exceeded {timeout_seconds}s wall clock"}}
            _deep_cache[root] = res
            return res
    for line in out.stdout.splitlines():
        if line.startswith("ISAAC_DEEP_OK "):
            res = {"ok": True, **json.loads(line[len("ISAAC_DEEP_OK "):])}
            _deep_cache[root] = res
            return res
    res = {"ok": False, "error": {"type": "environment_error",
           "message": "SimulationApp failed to initialise",
           "stderr_tail": (out.stderr or "")[-2000:], "returncode": out.returncode}}
    _deep_cache[root] = res
    return res


def health(deep: bool = False, timeout_seconds: int = 300) -> dict:
    """Full layered probe.  Deep only runs when GPU and install both pass."""
    gpu = probe_gpu()
    install = probe_install()
    layers: dict = {"gpu": gpu, "install": install}
    ok = gpu["ok"] and install["ok"]
    if deep:
        if ok:
            layers["deep"] = probe_deep(install["root"], timeout_seconds)
            ok = ok and layers["deep"]["ok"]
        else:
            layers["deep"] = {"skipped": True}
    result = {"ok": ok, "layers": layers}
    if not ok:
        if not gpu["ok"]:
            result["error"] = {"type": "environment_error", "message": gpu.get("guidance", GPU_GUIDANCE)}
        elif not install["ok"]:
            result["error"] = {"type": "environment_error", "message": install["error"]["message"]}
        else:
            result["error"] = layers.get("deep", {}).get("error", {"type": "environment_error"})
    return result
