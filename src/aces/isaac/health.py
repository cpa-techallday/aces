"""Isaac Sim layered health probe for ACES.

Mirrors the isaac-sim plugin contract: GPU layer, install layer, then — only when
both pass — a deep layer that actually boots a minimal headless ``SimulationApp``.
Every layer reports structured results; failures are ``environment_error`` with the
official guidance, never silently normal: a deep layer that cannot run is reported as
failed, with the reason.  No GUI is ever launched.

Every probe runs through :func:`aces.proc.run` (a timeout kills the whole process group)
with :func:`isaac_env`: the minimal environment plus the variables Isaac needs.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import tempfile
from pathlib import Path

from .. import proc

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

# Passed through to Isaac on top of aces.proc's base set, when set.  Nothing is set here on anyone's behalf:
# OMNI_KIT_ACCEPT_EULA / ACCEPT_EULA reach Isaac only if the operator has accepted NVIDIA's EULA and set them.
# Nucleus credentials (OMNI_USER, OMNI_PASS) and other secrets are not passed: no run fetches remote assets.
ISAAC_ENV_KEEP = (
    "ISAAC_SIM_PATH", "ISAAC_SIM_PYTHON", "OMNI_KIT_ACCEPT_EULA", "ACCEPT_EULA",  # install, licence acceptance
    "CUDA_VISIBLE_DEVICES", "NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES", "CUDA_PATH",  # GPU choice
    "LD_LIBRARY_PATH", "VK_ICD_FILENAMES",                              # driver and Vulkan loader
    "XDG_RUNTIME_DIR", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME",  # Kit caches and config (Linux)
    "USER", "LOGNAME", "USERNAME",
    "SYSTEMDRIVE", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMW6432",      # Windows (names are case-insensitive)
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS",
)


def isaac_env() -> dict[str, str]:
    """The environment every Isaac subprocess gets: :func:`aces.proc.minimal_env` plus :data:`ISAAC_ENV_KEEP`."""
    return proc.minimal_env(keep=ISAAC_ENV_KEEP)


_deep_cache: dict[str, dict] = {}


def _err(message: str) -> dict:
    return {"ok": False, "error": {"type": "environment_error", "message": message}}


def probe_gpu() -> dict:
    """GPU layer: nvidia-smi first (name + driver + CUDA), degraded flag when NVML fails.

    A WMI-level check can prove Windows sees an adapter but not that Vulkan/CUDA
    works; when nvidia-smi fails the layer reports degraded=true with the detail,
    and :func:`health` reports the deep layer as failed (not run) with that reason.
    """
    try:
        out = proc.run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
                       env=isaac_env(), timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            gpus = [line.split(", ") for line in out.stdout.strip().splitlines()]
            return {"ok": True, "degraded": False,
                    "gpus": [{"name": n, "driver": d} for n, d in gpus]}
        return {"ok": False, "degraded": True,
                "detail": (out.stderr or out.stdout).strip() or "nvidia-smi failed",
                "guidance": GPU_GUIDANCE}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "degraded": True, "detail": str(e) or type(e).__name__, "guidance": GPU_GUIDANCE}


def probe_install() -> dict:
    """Install layer: ISAAC_SIM_PYTHON (pip install), ISAAC_SIM_PATH, then standard roots.

    A pip-installed Isaac Sim (``pip install isaacsim[all]``) has no python.bat root;
    point ``ISAAC_SIM_PYTHON`` at that environment's interpreter instead.  The
    interpreter is verified by actually importing isaacsim in it.
    """
    env_py = os.environ.get("ISAAC_SIM_PYTHON")
    if env_py:
        try:
            out = proc.run([env_py, "-c", "import isaacsim; print('ok')"], env=isaac_env(), timeout=120)
            if out.returncode == 0 and "ok" in out.stdout:
                return {"ok": True, "kind": "pip", "root": env_py, "launcher": env_py}
            return _err(f"ISAAC_SIM_PYTHON={env_py} cannot import isaacsim: {(out.stderr or '')[-400:]}")
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return _err(f"ISAAC_SIM_PYTHON={env_py} failed: {e}")

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
    """Deep layer: boot a minimal headless SimulationApp in a subprocess (cached per root).

    ``root`` is either an install root (workstation install with python.bat/python.sh)
    or, for pip installs, the python interpreter itself (``ISAAC_SIM_PYTHON``).
    """
    if root in _deep_cache:
        return _deep_cache[root]
    install = Path(root)
    if install.is_file():  # pip kind: root IS the interpreter
        launcher = str(install)
    else:
        launcher = str(install / ("python.bat" if platform.system() == "Windows" else "python.sh"))
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "deep_probe.py"
        script.write_text(DEEP_SCRIPT, encoding="utf-8")
        try:
            out = proc.run([str(launcher), str(script)], cwd=td, env=isaac_env(), timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            res = {"ok": False, "error": {"type": "timeout",
                   "message": f"deep probe exceeded {timeout_seconds}s wall clock; process group killed"}}
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
    # A degraded GPU layer (NVML unreachable from this shell, WMI-only evidence) does
    # not block the deep probe — the rule is to run SimulationApp and let it
    # arbitrate.  Only a hard GPU absence (nvidia-smi missing entirely) blocks it.
    ok = gpu["ok"] and install["ok"]
    if deep:
        if install["ok"] and (gpu["ok"] or gpu.get("degraded")):
            layers["deep"] = probe_deep(install["root"], timeout_seconds)
            ok = layers["deep"]["ok"] if gpu.get("degraded") else (ok and layers["deep"]["ok"])
        else:
            why = []
            if not gpu["ok"]:
                why.append(f"GPU layer failed ({gpu.get('detail') or 'nvidia-smi/NVML failed'})")
            if not install["ok"]:
                why.append(f"install layer failed ({install.get('error', {}).get('message', 'not found')})")
            layers["deep"] = {"ok": False, "skipped": True, "error": {
                "type": "environment_error", "message": "deep probe not run: " + "; ".join(why)}}
    result = {"ok": ok, "layers": layers}
    if not ok:
        if not install["ok"]:
            result["error"] = {"type": "environment_error", "message": install["error"]["message"]}
        elif not gpu["ok"] and not gpu.get("degraded"):
            result["error"] = {"type": "environment_error", "message": gpu.get("guidance", GPU_GUIDANCE)}
        else:
            result["error"] = layers.get("deep", {}).get("error", {"type": "environment_error"})
    return result
