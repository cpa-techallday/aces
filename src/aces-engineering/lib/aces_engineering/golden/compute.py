"""Compute detection, the run plan and the compute-used table of aces golden.

`detect()` looks at everything this machine could compute with and never raises: the CPUs, memory and BLAS, the voxel
kernel's own element and patch tests, a CuPy GPU (with the same patch test run on the device and a free-memory floor),
NVIDIA Warp, torch and PhysicsNeMo (looked up, never imported), conforming-mesh solvers on PATH, and the rig's
simulation lanes as the fabric manifest lists them. It never refreshes the manifest and never contacts the rig: v0
reports each rig lane and says that golden-path submission is not wired yet.

`plan()` decides where the sweep, the verification solves and the device-parity re-solves run; `plan_check()` decides
the finer-resolution independent check levels once the coarse solve time is known. `ComputeLog` turns the records of a
run into the compute-used table (`compute_used.json`); every row is re-derived from the files it names, which is how
`aces golden verify` checks it.

Standard library only at module level; numpy, cupy, warp, psutil and threadpoolctl are imported inside the probes.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import math
import os
import platform
import shutil
import socket
import sys
import time
from pathlib import Path
from typing import Any

from . import EXIT_USAGE, ROLES, SCHEMA, GoldenError, clean, utc_now

GPU_SWEEP_MIN_CELLS = 40_000      # above this many cells per solve an auto run sweeps on a usable GPU
GPU_BYTES_PER_CELL = 700          # GPU memory a solve needs per grid cell (H8 FEA + SIMP arrays, FP64), measured rule
GPU_MIN_FREE_MIB = 512            # below this much free memory a GPU is present but not usable (detect sees no spec)
CHECK_MAX_CELLS = 2_000_000       # an independent-check level above this many cells is not run
JOB_MEM_GB = 0.6                  # memory one CPU sweep worker needs (auto --jobs rule)
PATCH_TOL = 1e-6                  # GPU patch test: tip displacement vs F L / (A E)

NOT_WIRED = "golden-path submission not wired yet (v0); nothing submitted"
PLAN_RIG_REASON = "golden-path submission not wired yet (v0)"
FNO_REASON = "a field (FNO) surrogate needs O(100) solved fields; v0 fits a tabular GP"
CONFORMING_REASON = "not wired in v0; the VISION sign-off path (second discretisation)"
LATTICE_REASON = "not used: cantilever demonstrator, not wired into the golden path (v0)"


# ----------------------------------------------------------------------------- small helpers

@contextlib.contextmanager
def _quiet():
    """Swallow Python-level prints of third-party probes (the Warp banner); fd-level output is the StdioGuard's job."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        yield buf


def _exc(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    return f"{type(exc).__name__}: {text}"[:200] if text else type(exc).__name__


def _installed(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:  # ValueError for a module without __spec__, ImportError for a broken parent
        return False


def _cells(envelope_mm, h: float) -> int:
    """Cells of the vendored grid at resolution h (the vendored code uses round(L / h) per axis)."""
    n = 1
    for length in envelope_mm:
        n *= max(1, int(round(float(length) / float(h))))
    return int(n)


def _need_mib(cells: int) -> int:
    return int(math.ceil(cells * GPU_BYTES_PER_CELL / 2 ** 20))


def _fits(free_mib, cells: int) -> bool:
    return free_mib is not None and float(free_mib) * 2 ** 20 >= cells * GPU_BYTES_PER_CELL


def _mem_reason(free_mib, cells: int) -> str:
    return f"GPU has {free_mib} MiB free; this solve needs about {_need_mib(cells)} MiB"


def _g(v, digits: int = 4) -> str:
    """A number with `digits` significant digits; None as '-'."""
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            return "-"
        return f"{v:.{digits}g}"
    return str(v)


# ----------------------------------------------------------------------------- detection

def _cpu_model() -> str | None:
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except Exception:
            pass
    return platform.processor() or platform.machine() or None


def _cpu() -> dict:
    out: dict[str, Any] = {"logical": os.cpu_count(), "usable": None, "model": None, "mem_total_gb": None,
                           "mem_available_gb": None, "numpy": None, "blas": [], "blas_threads_per_solve": 1}
    try:
        out["usable"] = len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except Exception:
        out["usable"] = out["logical"]
    try:
        out["model"] = _cpu_model()
    except Exception:
        pass
    try:
        import psutil

        vm = psutil.virtual_memory()
        out["mem_total_gb"] = round(vm.total / 1e9, 2)
        out["mem_available_gb"] = round(vm.available / 1e9, 2)
    except Exception as exc:
        out["mem_error"] = _exc(exc)
    try:
        import numpy

        out["numpy"] = numpy.__version__
    except Exception as exc:
        out["numpy"] = f"not available ({type(exc).__name__})"
    try:
        import threadpoolctl

        out["blas"] = [clean(row) for row in threadpoolctl.threadpool_info()]
    except Exception as exc:
        out["blas_error"] = _exc(exc)
    return out


def _kernel_selftest() -> dict:
    t0 = time.perf_counter()
    try:
        from gendesign.fea3d import self_test

        with _quiet():
            ok = bool(self_test(verbose=False))
        return {"passed": ok, "wall_s": round(time.perf_counter() - t0, 4), "source": "gendesign.fea3d.self_test"}
    except Exception as exc:
        return {"passed": False, "wall_s": round(time.perf_counter() - t0, 4), "source": "gendesign.fea3d.self_test",
                "error": _exc(exc)}


def _gpu_patch_test() -> dict:
    """The kernel's uniaxial bar (10 x 2 x 2 elements, x traction 100 N, symmetry supports) solved on the GPU: the tip
    displacement must match F L / (A E) to 1e-6 relative, exactly as gendesign.fea3d.self_test checks the CPU."""
    t0 = time.perf_counter()
    try:
        import numpy as np

        from gendesign.fea3d import VoxelFEA, to_numpy

        fea = VoxelFEA(10, 2, 2, 1.0, nu=0.3, dtype="float64", prefer_gpu=True)
        if fea.xp is np:
            return {"passed": False, "tip_rel_err": None, "wall_s": round(time.perf_counter() - t0, 4),
                    "error": "VoxelFEA(prefer_gpu=True) fell back to numpy"}
        fea.fix_box([0, 0, 0, 0, 2, 2], dofs=(0,))
        fea.fix_box([0, 0, 0, 10, 0, 2], dofs=(1,))
        fea.fix_box([0, 0, 0, 10, 2, 0], dofs=(2,))
        fea.add_traction_box("x", [10, 0, 0, 10, 2, 2], [100.0, 0, 0], axis="x")
        ee = fea.xp.ones(fea.ne, dtype=fea.dtype)
        u, iters, _res = fea.solve(ee, fea.loads["x"], tol=1e-12)
        e0 = 200000.0
        ux = to_numpy(u)[0::3] / e0
        tip = ux[to_numpy(fea.nodes_in_box([10, 0, 0, 10, 2, 2]))]
        exact = 100.0 * 10.0 / (4.0 * e0)
        rel = float(np.max(np.abs(tip - exact)) / exact)
        return {"passed": bool(rel <= PATCH_TOL), "tip_rel_err": rel, "wall_s": round(time.perf_counter() - t0, 4),
                "pcg_iters": int(iters)}
    except Exception as exc:
        return {"passed": False, "tip_rel_err": None, "wall_s": round(time.perf_counter() - t0, 4), "error": _exc(exc)}
    finally:
        try:
            import cupy

            cupy.get_default_memory_pool().free_all_blocks()
        except Exception:
            pass


def _gpu(device: str) -> dict:
    g: dict[str, Any] = {"cupy": None, "present": False, "usable": False, "reason": None, "name": None,
                         "cuda_runtime": None, "mem_free_mib": None, "mem_total_mib": None, "patch_test": None}
    if device == "cpu":
        g["cupy"] = "not probed"
        g["reason"] = "excluded by --device cpu"
        return g
    try:
        import cupy

        g["cupy"] = "importable"
    except Exception as exc:  # ImportError, or a CUDA library failing to load
        g["cupy"] = f"not available ({type(exc).__name__})"
        g["reason"] = "CuPy is not importable here (an optional extra; the packaged venv does not ship it)"
        return g
    try:
        from gendesign.fea3d import have_gpu

        g["present"] = bool(have_gpu())
    except Exception as exc:
        g["reason"] = f"the CUDA device query failed ({_exc(exc)})"
        return g
    if not g["present"]:
        g["reason"] = "CuPy is importable but sees no CUDA device"
        return g
    try:
        props = cupy.cuda.runtime.getDeviceProperties(cupy.cuda.Device().id)
        name = props["name"]
        g["name"] = name.decode() if isinstance(name, bytes) else str(name)
    except Exception:
        pass
    try:
        v = int(cupy.cuda.runtime.runtimeGetVersion())
        g["cuda_runtime"] = f"{v // 1000}.{(v % 1000) // 10}"
    except Exception:
        pass
    try:
        free, total = cupy.cuda.runtime.memGetInfo()
        g["mem_free_mib"] = int(free // 2 ** 20)
        g["mem_total_mib"] = int(total // 2 ** 20)
    except Exception as exc:
        g["mem_error"] = _exc(exc)
    g["patch_test"] = _gpu_patch_test()
    pt = g["patch_test"]
    free = g["mem_free_mib"]
    if not pt.get("passed"):
        detail = pt.get("error") or f"tip rel err {_g(pt.get('tip_rel_err'), 2)} above {PATCH_TOL:g}"
        g["reason"] = f"present, not usable: the GPU patch test failed ({detail})"
    elif free is None:
        g["reason"] = "present, not usable: free GPU memory could not be read"
    elif free < GPU_MIN_FREE_MIB:
        g["reason"] = f"present, not usable: {free} MiB free (the floor is {GPU_MIN_FREE_MIB} MiB)"
    else:
        g["usable"] = True
        g["reason"] = (f"patch test passed (tip rel err {_g(pt.get('tip_rel_err'), 2)}); {free} MiB free of "
                       f"{g['mem_total_mib']} MiB")
    return g


def _warp(gpu_usable: bool, device: str) -> dict:
    w: dict[str, Any] = {"importable": None, "usable": False, "version": None, "cuda_devices": None, "reason": None}
    if device == "cpu":
        w["reason"] = "excluded by --device cpu"
        return w
    if not gpu_usable:
        w["reason"] = ("not probed: Warp's apply_K runs on the CuPy device arrays and no usable CuPy GPU was found"
                       + ("" if _installed("warp") else "; warp-lang is not installed"))
        return w
    try:
        with _quiet():
            import warp

            try:
                warp.config.quiet = True
            except Exception:
                pass
            w["importable"] = True
            w["version"] = str(getattr(warp, "__version__", None) or getattr(warp.config, "version", "") or "") or None
            n = int(warp.get_cuda_device_count())
        w["cuda_devices"] = n
        w["usable"] = n >= 1
        w["reason"] = ("usable for the apply_K device-parity re-solve" if n >= 1
                       else "Warp is importable but sees no CUDA device")
    except Exception as exc:
        w["importable"] = bool(w["importable"])
        w["reason"] = f"not available ({_exc(exc)})"
    return w


def _manifest_location() -> tuple[Path, float]:
    """The fabric manifest path and its max age: aces.toml [fabric] manifest / max_age_s (aces-base layout), else
    $ACES_USER_CONFIG/fabric.json (default ~/.config/aces/fabric.json) and 900 s."""
    path: Path | None = None
    max_age = 900.0
    try:
        import aces_engineering

        fab = (aces_engineering.aces_config() or {}).get("fabric") or {}
        if fab.get("manifest"):
            path = Path(os.path.expanduser(str(fab["manifest"])))
        if fab.get("max_age_s") is not None:
            max_age = float(fab["max_age_s"])
    except (Exception, SystemExit):  # aces_config raises SystemExit on an unreadable aces.toml
        pass
    if path is None:
        user = os.environ.get("ACES_USER_CONFIG") or str(Path.home() / ".config" / "aces")
        path = Path(user) / "fabric.json"
    return path, max_age


def _config_simlab_mcp() -> str | None:
    try:
        import aces_engineering

        return ((aces_engineering.aces_config() or {}).get("server") or {}).get("simlab_mcp")
    except (Exception, SystemExit):
        return None


def _lane_status(lane: dict, stale: bool | None, health) -> str:
    if stale:
        return "stale-manifest"
    if not lane.get("endpoint"):
        return "no-endpoint"
    if str(health or "").startswith("http"):
        return "available"
    return "unreachable"


def _read_manifest() -> tuple[Path, float, dict | None, float | None, str | None]:
    path, max_age = _manifest_location()
    try:
        if not path.is_file():
            return path, max_age, None, None, None
        data = json.loads(path.read_text(encoding="utf-8"))
        age = max(0.0, time.time() - path.stat().st_mtime)
        return path, max_age, (data if isinstance(data, dict) else None), age, None
    except Exception as exc:
        return path, max_age, None, None, _exc(exc)


def _rig() -> dict:
    path, max_age, data, age, err = _read_manifest()
    m: dict[str, Any] = {"path": str(path), "present": data is not None, "generated": None, "age_s": None,
                         "stale": None, "mode": None}
    if err:
        m["error"] = err
    if data is not None:
        m["generated"] = data.get("generated")
        m["age_s"] = round(age, 1) if age is not None else None
        m["stale"] = bool(age is not None and age > max_age)
        mode = data.get("mode")
        m["mode"] = mode.get("name") if isinstance(mode, dict) else mode
    server = (data or {}).get("server") or {}
    health = (data or {}).get("health") or {}
    mcp = {"url": server.get("simlab_mcp") or _config_simlab_mcp(), "health": health.get("simlab_mcp")}
    lanes: list[dict] = []
    if data is None:
        why = "the fabric manifest could not be read: run aces fabric refresh" if err else \
            "no fabric manifest: run aces fabric refresh"
        lanes.append({"id": None, "kind": "sim", "gpus": None, "endpoint": None, "status": "no-manifest",
                      "use": "not used", "reason": f"{why}; {NOT_WIRED}"})
    else:
        for lane in data.get("lanes") or []:
            if not isinstance(lane, dict) or lane.get("kind") != "sim":
                continue
            lanes.append({"id": lane.get("id"), "kind": "sim", "gpus": lane.get("gpus"), "endpoint": lane.get("endpoint"),
                          "status": _lane_status(lane, m["stale"], mcp["health"]), "use": "not used",
                          "reason": NOT_WIRED})
        if not lanes:
            lanes.append({"id": None, "kind": "sim", "gpus": None, "endpoint": None, "status": "no-sim-lane",
                          "use": "not used",
                          "reason": f"the fabric manifest lists no sim lane (mode {m['mode']}); {NOT_WIRED}"})
    return {"manifest": m, "simlab_mcp": mcp, "lanes": lanes, "max_age_s": max_age}


def detect(*, device: str = "auto") -> dict:
    """What this machine can compute with, as the `compute.json` snapshot (without "plan"). Never raises: every probe
    is guarded and a failed probe is reported with its reason. `device` is the --device choice; "cpu" skips every GPU
    probe (reason "excluded by --device cpu")."""
    out: dict[str, Any] = {"schema": SCHEMA["compute"], "detected_utc": None, "host": None, "platform": None,
                           "python": sys.version.split()[0]}
    try:
        out["detected_utc"] = utc_now()
    except Exception:
        pass
    for key, fn in (("host", socket.gethostname), ("platform", platform.platform)):
        try:
            out[key] = fn()
        except Exception:
            out[key] = None
    probes = (("cpu", _cpu), ("kernel_selftest", _kernel_selftest), ("gpu", lambda: _gpu(device)))
    for key, fn in probes:
        try:
            out[key] = fn()
        except Exception as exc:  # belt and braces: the probes guard themselves
            out[key] = {"error": _exc(exc)}
    gpu_usable = bool((out.get("gpu") or {}).get("usable"))
    try:
        out["warp"] = _warp(gpu_usable, device)
    except Exception as exc:
        out["warp"] = {"importable": None, "usable": False, "version": None, "cuda_devices": None,
                       "reason": f"probe failed ({_exc(exc)})"}
    for mod in ("torch", "physicsnemo"):
        out[mod] = {"installed": _installed(mod), "use": "not used", "reason": FNO_REASON}
    try:
        out["conforming_mesh"] = {"ccx": shutil.which("ccx"), "code_aster": shutil.which("as_run"), "use": "not used",
                                  "reason": CONFORMING_REASON}
    except Exception as exc:
        out["conforming_mesh"] = {"ccx": None, "code_aster": None, "use": "not used",
                                  "reason": f"{CONFORMING_REASON} (lookup failed: {_exc(exc)})"}
    try:
        out["rig"] = _rig()
    except Exception as exc:
        out["rig"] = {"manifest": {"path": None, "present": False, "generated": None, "age_s": None, "stale": None,
                                   "mode": None},
                      "simlab_mcp": {"url": None, "health": None},
                      "lanes": [{"id": None, "kind": "sim", "gpus": None, "endpoint": None, "status": "no-manifest",
                                 "use": "not used", "reason": f"rig probe failed ({_exc(exc)}); {NOT_WIRED}"}]}
    return out


# ----------------------------------------------------------------------------- plans

def _derived(eff: dict) -> dict:
    d = eff.get("derived") or {}
    if not d.get("grid"):
        raise ValueError("compute.plan needs eff['derived']['grid'] (set by the pipeline after domain.build)")
    return d


def _jobs(jobs, cpu: dict, n: int) -> int:
    if jobs is None or (isinstance(jobs, str) and jobs.strip().lower() == "auto"):
        usable = int(cpu.get("usable") or cpu.get("logical") or 1)
        caps = [usable, max(1, int(n))]
        mem = cpu.get("mem_available_gb")
        if mem is not None:
            caps.append(int(math.floor(float(mem) / JOB_MEM_GB)))
        return max(1, min(caps))
    try:
        j = int(str(jobs).strip())
    except ValueError:
        raise GoldenError(EXIT_USAGE, f"--jobs must be a positive integer or auto, not {jobs!r}") from None
    if j < 1:
        raise GoldenError(EXIT_USAGE, f"--jobs must be a positive integer or auto, not {jobs!r}")
    return j


def plan(detected: dict, eff: dict, *, jobs: "int | str" = "auto", device: str = "auto") -> dict:
    """Where every solve of the run goes. Needs eff["derived"]["grid"] (ValueError when it is still null).

    sweep: a usable GPU when --device gpu (else GoldenError(EXIT_USAGE)), or with --device auto when a usable GPU has
    the memory and a solve is larger than GPU_SWEEP_MIN_CELLS cells; otherwise the CPU pool (the root-mount demo
    sweeps on the CPU on every host, so its sweeps are identical everywhere). verify: the sweep's device. parity:
    the final verify mask re-solved on the other devices (CuPy, Warp). A --device gpu run is refused here, before
    anything is computed, when a sweep solve or the level-1 check would not fit in the free GPU memory."""
    d = _derived(eff)
    if device not in ("auto", "cpu", "gpu"):
        raise GoldenError(EXIT_USAGE, f"--device must be auto, cpu or gpu, not {device!r}")
    env = d["grid"]["envelope_mm"]
    gen_cells = _cells(env, d["gen_mm"])
    fea_cells = _cells(env, d["fea_mm"])
    big = max(gen_cells, fea_cells)
    cpu = detected.get("cpu") or {}
    gpu = detected.get("gpu") or {}
    warp = detected.get("warp") or {}
    n = int(d.get("n") or (eff.get("sweep") or {}).get("n") or 1)
    free = gpu.get("mem_free_mib")
    name = gpu.get("name") or "the GPU"
    gpu_ok = bool(gpu.get("usable")) and device != "cpu"
    notes: list[str] = []
    j = _jobs(jobs, cpu, n)
    if device == "gpu":
        if not gpu.get("usable"):
            raise GoldenError(EXIT_USAGE, f"no usable CUDA GPU here ({gpu.get('reason') or 'not detected'})",
                              hint="run with --device auto (or cpu); aces golden compute shows what was detected")
        if not _fits(free, big):
            raise GoldenError(EXIT_USAGE, _mem_reason(free, big), hint="free GPU memory, or run with --device auto")
        mode = d.get("check_mode") or "auto"
        h1 = float(d["fea_mm"]) * float(d.get("check_factor") or 0.5)
        c1 = _cells(env, h1)
        if mode != "off" and c1 <= CHECK_MAX_CELLS and not _fits(free, c1):
            raise GoldenError(EXIT_USAGE, f"{_mem_reason(free, c1)} (the level-1 check at {h1:g} mm)",
                              hint="free GPU memory, run with --check off, or run with --device auto")
        sweep_dev, reason = "gpu", f"--device gpu: {name}"
    elif gpu_ok and big > GPU_SWEEP_MIN_CELLS:
        if _fits(free, big):
            sweep_dev = "gpu"
            reason = f"{big:,} cells per solve exceed {GPU_SWEEP_MIN_CELLS:,}: the sweep runs on {name}"
        else:
            sweep_dev = "cpu"
            reason = f"{_mem_reason(free, big)}: the sweep runs on the CPU pool"
    else:
        sweep_dev = "cpu"
        if device == "cpu":
            reason = "--device cpu"
        elif not gpu.get("usable"):
            reason = f"no usable GPU ({gpu.get('reason') or 'not detected'})"
        else:
            reason = (f"{big:,} cells per solve is at most {GPU_SWEEP_MIN_CELLS:,}: the CPU pool runs the sweep "
                      f"(identical sweeps on every host)")
    if sweep_dev == "gpu":
        j = 1  # GPU cases run one at a time in the main process
    parity: list[dict] = []
    if sweep_dev == "cpu":
        if gpu_ok and _fits(free, fea_cells):
            parity.append({"case_id": "p001", "device": "gpu", "backend": "adapter"})
            if warp.get("usable"):
                parity.append({"case_id": "p002", "device": "gpu", "backend": "warp"})
            else:
                notes.append(f"no Warp parity: {warp.get('reason') or 'Warp not usable'}")
        elif gpu_ok:
            notes.append(f"no GPU parity: {_mem_reason(free, fea_cells)}")
        else:
            notes.append("no device parity: " + ("--device cpu" if device == "cpu"
                                                  else f"no usable GPU ({gpu.get('reason') or 'not detected'})"))
    else:
        parity.append({"case_id": "p001", "device": "cpu", "backend": "adapter"})
        if warp.get("usable"):
            parity.append({"case_id": "p002", "device": "gpu", "backend": "warp"})
        else:
            notes.append(f"no Warp parity: {warp.get('reason') or 'Warp not usable'}")
    return {"sweep": {"device": sweep_dev, "jobs": int(j), "reason": reason},
            "verify": {"device": sweep_dev},
            "check_mode": d.get("check_mode") or "auto",
            "check_budget_s": float(d.get("check_budget_s") if d.get("check_budget_s") is not None else 60.0),
            "parity": parity,
            "rig": {"submit": False, "reason": PLAN_RIG_REASON},
            "cells": {"gen": gen_cells, "fea": fea_cells},
            "notes": notes}


def plan_check(detected: dict, eff: dict, *, coarse_solve_s: float | None, device: str = "auto") -> list[dict]:
    """The independent-check levels. Level 1 (h = fea_mm * check_factor, gating) runs on a usable GPU, else on the CPU
    when --check force or when the estimate coarse_solve_s * (fea_mm / h)^4 fits the budget. Level 2 (h = fea_mm *
    check_factor^2, informational) runs only on a usable GPU. A level that would need more GPU memory than is free is
    not put on the GPU (level 1 then takes the CPU route)."""
    d = _derived(eff)
    env = d["grid"]["envelope_mm"]
    fea = float(d["fea_mm"])
    cf = float(d.get("check_factor") or 0.5)
    mode = d.get("check_mode") or "auto"
    budget = float(d.get("check_budget_s") if d.get("check_budget_s") is not None else 60.0)
    gpu = detected.get("gpu") or {}
    free = gpu.get("mem_free_mib")
    gpu_ok = bool(gpu.get("usable")) and device != "cpu"
    out = []
    for level, h in ((1, fea * cf), (2, fea * cf * cf)):
        cells = _cells(env, h)
        row: dict[str, Any] = {"level": level, "h_mm": h, "device": None, "run": False, "gating": level == 1,
                               "est_s": None, "budget_s": budget, "reason": "", "cells": cells}
        if mode == "off":
            row["reason"] = "check disabled (--check off)"
        elif cells > CHECK_MAX_CELLS:
            row["reason"] = f"{cells:,} cells at {h:g} mm exceed the {CHECK_MAX_CELLS:,}-cell limit"
        elif gpu_ok and _fits(free, cells):
            row.update(device="gpu", run=True,
                       reason="finer check on the GPU" if level == 1 else "extra level because a GPU is present")
        elif level == 2:
            if gpu_ok:
                row["reason"] = f"GPU-only extra level: {_mem_reason(free, cells)}"
            else:
                row["reason"] = "GPU-only extra level: " + ("--device cpu" if device == "cpu" else "no usable GPU")
        else:
            est = float(coarse_solve_s) * (fea / h) ** 4 if coarse_solve_s else None
            row["est_s"] = est
            lead = f"{_mem_reason(free, cells)}; " if gpu_ok else ""
            if mode == "force":
                row.update(device="cpu", run=True, reason=f"{lead}forced on the CPU (--check force)")
            elif est is not None and est <= budget:
                row.update(device="cpu", run=True,
                           reason=f"{lead}CPU estimate {est:.1f} s is within the {budget:g} s budget")
            elif est is None:
                row["reason"] = f"{lead}no coarse solve time to estimate the CPU check from"
            else:
                who = lead if gpu_ok else ("--device cpu and " if device == "cpu" else "no usable GPU and ")
                row["reason"] = f"{who}the CPU estimate {est:.1f} s exceeds the {budget:g} s budget"
        out.append(row)
    return out


def lane_refusal(lane: str) -> str:
    """The one-line refusal for --lane (exit 2 before anything runs). Reads the fabric manifest only."""
    path, max_age, data, age, err = _read_manifest()
    if data is None:
        state = ("not in the fabric manifest (it could not be read)" if err
                 else "not in the fabric manifest (there is none: aces fabric refresh writes it)")
    else:
        found = [x for x in data.get("lanes") or [] if isinstance(x, dict) and x.get("id") == lane]
        if not found:
            state = "not in the fabric manifest"
        elif age is not None and age > max_age:
            state = "in a stale manifest"
        elif found[0].get("kind") != "sim":
            state = f"a {found[0].get('kind') or 'non-simulation'} lane in the fabric manifest, not a simulation lane"
        else:
            st = _lane_status(found[0], False, ((data.get("health") or {}).get("simlab_mcp")))
            state = {"available": "available in the fabric manifest",
                     "no-endpoint": "in the fabric manifest but has no endpoint"}.get(
                st, "in the fabric manifest but its simlab endpoint is unreachable")
    return (f"rig submission through lane '{lane}' is not wired into the golden path yet (v0); the lane is {state}; "
            f"nothing was submitted. Run without --lane to compute on this desktop (preview-grade); the report lists "
            f"the rig lanes it saw.")


def summary_lines(detected: dict, plan: dict | None) -> list[str]:
    """The human lines of `aces golden compute` and the stage-1 line. plan=None prints the detection only."""
    lines = []
    cpu = detected.get("cpu") or {}
    mem = ""
    if cpu.get("mem_available_gb") is not None:
        mem = f" · {_g(cpu.get('mem_available_gb'), 3)} of {_g(cpu.get('mem_total_gb'), 3)} GB free"
    lines.append(f"cpu: {cpu.get('model') or 'unknown model'} · {cpu.get('usable')} of {cpu.get('logical')} logical "
                 f"CPUs usable{mem} · numpy {cpu.get('numpy')} · BLAS 1 thread per solve")
    ks = detected.get("kernel_selftest") or {}
    lines.append(f"kernel: element and patch tests {'passed' if ks.get('passed') else 'FAILED'} "
                 f"({_g(ks.get('wall_s'), 2)} s, gendesign.fea3d.self_test)"
                 + (f": {ks['error']}" if ks.get("error") else ""))
    gpu = detected.get("gpu") or {}
    if gpu.get("usable"):
        lines.append(f"gpu: {gpu.get('name')} · CUDA {gpu.get('cuda_runtime')} · {gpu.get('reason')} · usable")
    else:
        what = gpu.get("name") or ("CuPy " + str(gpu.get("cupy")) if gpu.get("cupy") else "none")
        lines.append(f"gpu: {what} · not used: {gpu.get('reason')}")
    warp = detected.get("warp") or {}
    if warp.get("usable"):
        lines.append(f"warp: {warp.get('version')} · {warp.get('cuda_devices')} CUDA device(s) · {warp.get('reason')}")
    else:
        lines.append(f"warp: not used: {warp.get('reason')}")
    t = (detected.get("torch") or {}).get("installed")
    p = (detected.get("physicsnemo") or {}).get("installed")
    lines.append(f"fno: torch {'installed' if t else 'not installed'}, PhysicsNeMo {'installed' if p else 'not installed'}"
                 f"; not used: {FNO_REASON}")
    cm = detected.get("conforming_mesh") or {}
    lines.append(f"conforming mesh: ccx {cm.get('ccx') or 'not found'}, Code_Aster {cm.get('code_aster') or 'not found'}"
                 f"; not used: {CONFORMING_REASON}")
    rig = detected.get("rig") or {}
    man = rig.get("manifest") or {}
    for lane in rig.get("lanes") or []:
        if lane.get("id"):
            age = f", manifest {_g(man.get('age_s'), 3)} s old" if man.get("age_s") is not None else ""
            lines.append(f"rig: lane {lane['id']} {lane.get('status')}{age}; not used: {lane.get('reason')}")
        else:
            lines.append(f"rig: {lane.get('reason')}")
    if plan:
        sw = plan.get("sweep") or {}
        par = ", ".join(f"{p['case_id']} {p['device']}/{p['backend']}" for p in plan.get("parity") or []) or "none"
        lines.append(f"plan: sweep on {sw.get('device')} x{sw.get('jobs')} ({sw.get('reason')}) · verify on "
                     f"{(plan.get('verify') or {}).get('device')} · parity {par} · check {plan.get('check_mode')}, "
                     f"budget {_g(plan.get('check_budget_s'))} s · rig: {(plan.get('rig') or {}).get('reason')}")
        for note in plan.get("notes") or []:
            lines.append(f"plan: {note}")
    return lines


# ----------------------------------------------------------------------------- compute-used table

COLUMNS = ("step", "computation", "solver", "device", "resolution_mm", "size", "count", "wall_s", "wall_s_sum",
           "grade", "role", "source", "note")


def _row(step, computation, solver, device="-", resolution_mm=None, size=None, count=0, wall_s=None, wall_s_sum=None,
         grade="-", role=None, source=None, note="") -> dict:
    return {"step": step, "computation": computation, "solver": solver, "device": device,
            "resolution_mm": resolution_mm, "size": size, "count": count, "wall_s": wall_s, "wall_s_sum": wall_s_sum,
            "grade": grade, "role": role, "source": source, "note": note}


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)) else None


def _sum(vals):
    # math.fsum is exactly rounded on every Python; sum() of floats is compensated only from 3.12 on, so a table made on
    # one Python minor version would not re-derive bit for bit on another
    vs = [_num(v) for v in vals]
    vs = [v for v in vs if v is not None]
    return float(math.fsum(vs)) if vs else None


def _cases(run) -> list[dict]:
    try:
        cases = run.cases()
    except Exception:
        cases = None
    if cases is None:
        cases = run.read_json("cases.json", []) or []
    return [c for c in cases if isinstance(c, dict) and c.get("case_id")]


def _devices(solves: list[dict], jobs: int | None) -> str:
    names = []
    for s in solves:
        dev = s.get("device")
        if dev and dev not in names:
            names.append(str(dev))
    if not names:
        return "-"
    out = []
    for name in names:
        out.append(f"{name} x{jobs}" if name == "cpu/numpy" and jobs and jobs > 1 else name)
    return ", ".join(out)


def _not_run_reason(validation: dict | None, sweep: dict | None, what: str) -> str:
    if sweep is None:
        return "not run: the sweep did not run (no sweep.json)"
    if validation is None:
        return f"not run: no validation.json ({what} did not run)"
    if validation.get("outcome") == "thin-sweep":
        return "not run: thin sweep"
    return f"not run: {what}"


def _derive_rows(run, detected: dict, plan: dict | None) -> list[dict]:
    detected = detected or {}
    plan = plan or {}
    eff = run.read_json("inputs/spec.effective.json", {}) or {}
    d = eff.get("derived") or {}
    jobs = (plan.get("sweep") or {}).get("jobs")
    sweep_dev = (plan.get("sweep") or {}).get("device")
    validation = run.read_json("validation.json")
    sweep = run.read_json("sweep.json")
    cases = _cases(run)
    by_role: dict[str, list[dict]] = {r: [] for r in ROLES}
    for c in cases:
        by_role.setdefault(c.get("role"), []).append(c)
    rows: list[dict] = []

    # kernel
    ks = detected.get("kernel_selftest") or {}
    gp = (detected.get("gpu") or {}).get("patch_test")
    note = f"element checks and the uniaxial bar: {'passed' if ks.get('passed') else 'FAILED'}"
    if gp:
        note += (f"; GPU patch test {'passed' if gp.get('passed') else 'FAILED'} "
                 f"(tip rel err {_g(gp.get('tip_rel_err'), 2)})")
    walls = [ks.get("wall_s")] + ([gp.get("wall_s")] if gp else [])
    rows.append(_row("kernel", "element and patch tests",
                     "gendesign.fea3d.self_test" + ("; GPU patch test" if gp else ""),
                     "cpu; gpu" if gp else "cpu", 1.0, "10x2x2 bar (40 elements)", 1 + (1 if gp else 0),
                     _sum(walls), _sum(walls), "-", None,
                     "compute.json#kernel_selftest" + (", compute.json#gpu.patch_test" if gp else ""), note))

    # domain
    mesh = run.read_json("domain/mesh.json")
    fmesh = run.read_json("domain/fea/mesh.json")
    if mesh:
        meshes = [mesh] + ([fmesh] if fmesh else [])
        res = [m.get("elem_mm") for m in meshes]
        size = "; ".join(f"{m.get('cells')} cells, {m.get('solid_cells')} solid" for m in meshes)
        ratio = mesh.get("volume_ratio_voxel_over_brep")
        dom = run.read_json("domain/domain.json") or {}
        mesh_s = _sum(m.get("wall_s") for m in meshes)
        step_s = _num(dom.get("wall_s"))
        rows.append(_row("domain", "STEP -> voxel mask", "step-structures stage_mesh (Embree containment)", "cpu",
                         res[0] if len(res) == 1 else res, size, len(meshes), step_s if step_s is not None else mesh_s,
                         mesh_s, "-", None,
                         "domain/domain.json#wall_s, domain/mesh.json" + (", domain/fea/mesh.json" if fmesh else ""),
                         f"voxel/B-rep volume {_g(ratio)}; watertight tessellation {bool(mesh.get('watertight'))}; "
                         f"wall_s is the whole domain step (passive boxes, design-space SDF), wall_s_sum the "
                         f"stage_mesh records"))
    else:
        rows.append(_row("domain", "STEP -> voxel mask", "step-structures stage_mesh (Embree containment)",
                         source="domain/mesh.json", note="not run: no domain/mesh.json"))

    design_cases = by_role.get("sweep", []) + by_role.get("verify", [])
    # generate
    gens = []
    for c in design_cases:
        st = run.read_json(f"{c['case_id']}/status.json") or {}
        g = (st.get("diagnostics") or {}).get("generator")
        if isinstance(g, dict) and g:
            gens.append(g)
    if gens:
        parallel = sweep_dev == "cpu" and (jobs or 1) > 1
        total = _sum(g.get("wall_s") for g in gens)
        n_sweep = sum(1 for c in by_role.get("sweep", [])
                      if ((run.read_json(f"{c['case_id']}/status.json") or {}).get("diagnostics") or {}).get("generator"))
        rows.append(_row("generate", f"SIMP x {len(gens)}", "gendesign.topopt3d.run_case (OC, FFT filter)",
                         _devices(gens, jobs), gens[0].get("h_mm", d.get("gen_mm")),
                         f"{gens[0].get('elements')} elements", len(gens), None if parallel else total, total, "-",
                         "sweep, verify", "d###/status.json#diagnostics.generator, v###/status.json#diagnostics.generator",
                         f"{n_sweep} sweep and {len(gens) - n_sweep} verify designs"
                         + (f"; the {n_sweep} sweep designs ran concurrently in {jobs} workers, wall_s_sum is the "
                            f"sum over designs" if parallel and n_sweep else "")))
    else:
        rows.append(_row("generate", "SIMP x 0", "gendesign.topopt3d.run_case (OC, FFT filter)",
                         source="d###/status.json#diagnostics.generator",
                         note=_not_run_reason(validation, sweep, "no design reached the generator")))

    # skin
    skins = [s for s in (run.read_json(f"{c['case_id']}/skin.json") for c in design_cases) if isinstance(s, dict)]
    if skins:
        parallel = sweep_dev == "cpu" and (jobs or 1) > 1
        total = _sum(s.get("wall_s") for s in skins)
        dom = run.read_json("domain/domain.json") or {}
        up = int(((eff.get("generator") or {}).get("skin") or {}).get("upsample") or 1)
        shape = dom.get("shape")
        size = f"{int(math.prod(shape)) * up ** 3} field points" if shape else None
        ok = sum(1 for s in skins if s.get("status") == "ok")
        faces = sorted(int(s.get("faces") or 0) for s in skins if s.get("status") == "ok")
        med = faces[len(faces) // 2] if faces else None
        rows.append(_row("skin", f"organic skin x {len(skins)}", "golden.organic (EDT SDF, smooth unions, Taubin)",
                         "cpu", skins[0].get("field_h_mm", d.get("field_h_mm")), size, len(skins),
                         None if parallel else total, total, "-", "sweep, verify", "d###/skin.json, v###/skin.json",
                         f"{ok} of {len(skins)} watertight single-body skins; median {med} faces"))
    else:
        rows.append(_row("skin", "organic skin x 0", "golden.organic (EDT SDF, smooth unions, Taubin)",
                         source="d###/skin.json", note=_not_run_reason(validation, sweep, "no design reached the skin")))

    # sweep solves
    solves = [s for s in (run.read_json(f"{c['case_id']}/solve.json") for c in by_role.get("sweep", []))
              if isinstance(s, dict)]
    if solves:
        parallel = sweep_dev == "cpu" and (jobs or 1) > 1
        total = _sum(s.get("wall_s") for s in solves)
        rows.append(_row("solve", f"real FEA x {len(solves)}", "step-structures stage_solve, Jacobi-PCG FP64",
                         _devices(solves, jobs), d.get("fea_mm"),
                         f"{solves[0].get('elements')} elements, {solves[0].get('dof')} DOF", len(solves),
                         None if parallel else total, total, "preview-grade", "sweep", "d###/solve.json",
                         f"{sum(1 for s in solves if s.get('converged'))} of {len(solves)} converged"
                         + (f"; ran concurrently in {jobs} workers" if parallel else "")))
    else:
        rows.append(_row("solve", "real FEA x 0", "step-structures stage_solve, Jacobi-PCG FP64", role="sweep",
                         source="d###/solve.json", note=_not_run_reason(validation, sweep, "no design reached the solver")))

    # surrogate and optimise, per round
    rounds = (validation or {}).get("rounds") or []
    cvs, opts = [], []
    k = 1
    while True:
        cv = run.read_json(f"surrogate/round-{k}/cv.json")
        op = run.read_json(f"optimise/round-{k}.json")
        if cv is None and op is None:
            break
        if cv is not None:
            cvs.append(cv)
        if op is not None:
            opts.append(op)
        k += 1
    if cvs:
        worst = []
        for cv in cvs:
            per = cv.get("cv") or {}
            mo = max(per, key=lambda o: per[o].get("mape_pct") or 0.0) if per else None
            worst.append(f"round {cv.get('round')}: {cv.get('status')}, max CV MAPE "
                         f"{_g((per.get(mo) or {}).get('mape_pct'), 3)} % on {mo}, {cv.get('gp_warnings')} GP warnings")
        tot = _sum(cv.get("wall_s") for cv in cvs)
        rows.append(_row("surrogate", "GP K-fold per round", f"simlab.surrogate.tabular ({cvs[0].get('kind')})", "cpu",
                         None, ", ".join(f"n={cv.get('n')}" for cv in cvs), len(cvs), tot, tot, "-", None,
                         "surrogate/round-K/cv.json", "; ".join(worst)))
    else:
        rows.append(_row("surrogate", "GP K-fold per round", "simlab.surrogate.tabular", source="surrogate/round-K/cv.json",
                         note=_not_run_reason(validation, sweep, "the loop did not run")))
    if opts:
        methods = []
        for op in opts:
            if op.get("method") not in methods:
                methods.append(op.get("method"))
        tot = _sum(op.get("wall_s") for op in opts)
        rows.append(_row("optimise", " / ".join(str(m) for m in methods) + " per round", "golden.optimise", "cpu", None,
                         ", ".join(f"{op.get('n_candidates')} candidates" for op in opts), len(opts), tot, tot, "-",
                         None, "optimise/round-K.json",
                         "; ".join(f"round {op.get('round')}: {op.get('n_feasible')} predicted feasible"
                                   for op in opts)))
    else:
        rows.append(_row("optimise", "grid / lhs / TPE per round", "golden.optimise", source="optimise/round-K.json",
                         note=_not_run_reason(validation, sweep, "the loop did not run")))

    # verify solves
    vsolves = [(c, run.read_json(f"{c['case_id']}/solve.json")) for c in by_role.get("verify", [])]
    vsolves = [(c, s) for c, s in vsolves if isinstance(s, dict)]
    if vsolves:
        tot = _sum(s.get("wall_s") for _, s in vsolves)
        verdicts = []
        for r in rounds:
            verdicts.append(f"round {r.get('round')} {r.get('case_id')}: {'PASS' if r.get('passed') else 'FAIL'}")
        rows.append(_row("verify", f"real FEA x {len(vsolves)}", "step-structures stage_solve, Jacobi-PCG FP64",
                         _devices([s for _, s in vsolves], 1), d.get("fea_mm"),
                         f"{vsolves[0][1].get('elements')} elements, {vsolves[0][1].get('dof')} DOF", len(vsolves),
                         tot, tot, "preview-grade", "verify", "v###/solve.json", "; ".join(verdicts)))
    else:
        rows.append(_row("verify", "real FEA x 0", "step-structures stage_solve", role="verify", source="v###/solve.json",
                         note=_not_run_reason(validation, sweep, "no verify case was solved")))

    # independent check, one row per planned level
    ic = (validation or {}).get("independent_check") or {}
    levels = ic.get("levels") or []
    if levels:
        for lv in levels:
            cid = lv.get("case_id")
            s = run.read_json(f"{cid}/solve.json") if cid and lv.get("ran") else None
            if isinstance(s, dict):
                if lv.get("gating"):
                    grade = "independent check -> " + ("verified-desktop" if lv.get("passed") else "preview-grade")
                else:
                    grade = "informational (" + ("agrees" if lv.get("passed") else "does not agree") + ")"
                rows.append(_row("check", f"finer real FEA, level {lv.get('level')}", "step-structures stage_solve",
                                 s.get("device") or lv.get("device"), lv.get("h_mm"),
                                 f"{s.get('elements')} elements, {s.get('dof')} DOF", 1, _num(s.get("wall_s")),
                                 _num(s.get("wall_s")), grade, "check", f"{cid}/solve.json",
                                 f"estimate {_g(lv.get('est_s'), 3)} s, budget {_g(lv.get('budget_s'))} s"
                                 if lv.get("est_s") is not None else (lv.get("reason") or "")))
            else:
                rows.append(_row("check", f"finer real FEA, level {lv.get('level')}", "step-structures stage_solve",
                                 resolution_mm=lv.get("h_mm"), role="check",
                                 source="validation.json#independent_check.levels",
                                 note=f"not run: {lv.get('reason') or 'no solve record'}"))
    else:
        why = ic.get("reason") or "no validation.json"
        if not str(why).startswith("not run"):
            why = f"not run: {why}"
        rows.append(_row("check", "finer real FEA (levels 1, 2)", "step-structures stage_solve", role="check",
                         source="validation.json#independent_check", note=why))

    # device parity, one row per planned re-solve
    par = (validation or {}).get("device_parity") or []
    if par:
        for p in par:
            cid = p.get("case_id")
            s = run.read_json(f"{cid}/solve.json") if cid else None
            solver = "WarpApplyK in VoxelFEA.solve" if p.get("backend") == "warp" else "step-structures stage_solve"
            if isinstance(s, dict) and p.get("max_rel_diff") is not None:
                rows.append(_row("parity", f"{p.get('device')}/{p.get('backend')} re-solve of the verify mask", solver,
                                 s.get("device") or p.get("device"), d.get("fea_mm"),
                                 f"{s.get('elements')} elements, {s.get('dof')} DOF", 1, _num(s.get("wall_s")),
                                 _num(s.get("wall_s")),
                                 f"max rel diff {_g(p.get('max_rel_diff'), 2)}" + ("" if p.get("passed") else " (FAIL)"),
                                 "parity", f"{cid}/solve.json", "agrees" if p.get("passed") else
                                 f"device disagreement on {p.get('device')}/{p.get('backend')}"))
            else:
                rows.append(_row("parity", f"{p.get('device')}/{p.get('backend')} re-solve of the verify mask", solver,
                                 p.get("device") or "-", d.get("fea_mm"), role="parity",
                                 source="validation.json#device_parity",
                                 note=f"FAILED: {p.get('error') or p.get('reason') or 'no solve record'}"))
    else:
        parity_notes = [n for n in (plan.get("notes") or []) if "parity" in n]
        loop_passed = any(r.get("passed") for r in rounds)
        if validation is not None and validation.get("outcome") == "thin-sweep":
            why = "not run: thin sweep"
        elif validation is not None and not loop_passed:
            why = "not run: verification failed"
        elif parity_notes:
            why = "not run: " + "; ".join(parity_notes)
        else:
            why = _not_run_reason(validation, sweep, "no parity re-solve was planned")
        rows.append(_row("parity", "CuPy / Warp re-solve", "step-structures stage_solve / WarpApplyK", role="parity",
                         source="validation.json#device_parity", note=why))

    # rig lanes
    for lane in ((detected.get("rig") or {}).get("lanes") or []):
        rows.append(_row("rig", f"sim lane {lane.get('id')}" if lane.get("id") else "sim lanes", "simlab-mcp", "rig",
                         grade="not used", source="compute.json#rig.lanes",
                         note=f"{lane.get('status')}: {lane.get('reason')}"))

    # detected but not used
    rows.append(_row("warp-lattice", "rod-lattice generator", "gendesign.lattice_opt", "-", grade="not used",
                     source="compute.json#warp", note=LATTICE_REASON))
    t = (detected.get("torch") or {}).get("installed")
    p = (detected.get("physicsnemo") or {}).get("installed")
    if t or p:
        fno_note = (f"not used: torch {'installed' if t else 'not installed'}, PhysicsNeMo "
                    f"{'installed' if p else 'not installed'}; {FNO_REASON}")
    else:
        fno_note = f"not installed (torch, PhysicsNeMo); {FNO_REASON}"
    rows.append(_row("fno", "field surrogate", "simlab.surrogate.fno (torch, PhysicsNeMo)", "-", grade="not used",
                     source="compute.json#torch, compute.json#physicsnemo", note=fno_note))
    cm = detected.get("conforming_mesh") or {}
    found = [x for x in (cm.get("ccx"), cm.get("code_aster")) if x]
    cm_note = (f"available ({', '.join(found)}), not wired (v0): {CONFORMING_REASON}" if found
               else f"not available: neither ccx nor as_run is on PATH; {CONFORMING_REASON}")
    rows.append(_row("second-discretisation", "conforming-mesh solver", "CalculiX / Code_Aster", "-", grade="not used",
                     source="compute.json#conforming_mesh", note=cm_note))
    return [clean(r) for r in rows]


class ComputeLog:
    """The compute-used table. `from_run(run, detected, plan)` derives the rows from the run's records (and the
    detection snapshot), `rows()` returns them, `markdown()` renders them, `write(run)` writes compute_used.json."""

    def __init__(self) -> None:
        self._rows: list[dict] = []

    def from_run(self, run, detected: dict, plan: dict | None) -> "ComputeLog":
        self._rows = _derive_rows(run, detected, plan)
        return self

    def rows(self) -> list[dict]:
        return [dict(r) for r in self._rows]

    def markdown(self) -> str:
        return rows_markdown(self._rows)

    def write(self, run) -> Path:
        return run.write_json("compute_used.json", {"schema": SCHEMA["compute-used"], "rows": self._rows})


def rows_markdown(rows: list[dict]) -> str:
    """Markdown table of compute-used rows (shared with the report)."""
    head = ["step", "computation", "solver", "device", "resolution (mm)", "size", "count", "wall s", "sum s", "grade",
            "note"]
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        res = r.get("resolution_mm")
        res_s = ", ".join(_g(x) for x in res) if isinstance(res, list) else _g(res)
        cells = [r.get("step"), r.get("computation"), r.get("solver"), r.get("device"), res_s, r.get("size") or "-",
                 r.get("count"), _g(r.get("wall_s"), 3), _g(r.get("wall_s_sum"), 3), r.get("grade"), r.get("note") or ""]
        out.append("| " + " | ".join(str(c if c is not None else "-").replace("|", "/") for c in cells) + " |")
    return "\n".join(out) + "\n"


def rederive_problems(run) -> list[str]:
    """Audit helper: re-derive the compute-used rows from compute.json and the run's records and compare them with
    compute_used.json. Returns the differences (empty = every row re-derives from its source)."""
    doc = run.read_json("compute_used.json")
    if not isinstance(doc, dict):
        return ["compute_used.json is missing"]
    comp = run.read_json("compute.json")
    if not isinstance(comp, dict):
        return ["compute.json is missing"]
    plan_ = comp.get("plan")
    detected = {k: v for k, v in comp.items() if k != "plan"}
    fresh = _derive_rows(run, detected, plan_)
    have = doc.get("rows") or []
    problems = []
    if len(fresh) != len(have):
        problems.append(f"{len(have)} rows recorded, {len(fresh)} re-derived")
    for i, (a, b) in enumerate(zip(have, fresh)):
        if json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True):
            diff = [k for k in COLUMNS if a.get(k) != b.get(k)]
            problems.append(f"row {i + 1} ({a.get('step')}) differs on {', '.join(diff) or 'keys'}")
    return problems
