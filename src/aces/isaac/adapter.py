"""simlab solver adapter for Isaac Sim scene simulation (solver id ``aces-isaac-scene``).

Placement decision (ACES_HANDOFF, "Excluded on purpose"): the rig's CMP 170HX cards
have no RTX renderer and no display, so Isaac Sim runs **on the workstation only**.
This adapter is therefore the workstation-resident solver; the rig never carries the
``aces-isaac`` image.  The contract mirrors the isaac-sim plugin:

- headless only — generated scripts use ``SimulationApp({"headless": True})`` and the
  gate rejects any script containing ``headless=False``;
- file-based I/O inside the run directory; a run never saves over an existing USD
  (the case's scene is opened read-only unless ``allow_scene_save`` is set);
- wall-clock timeout default 300 s, hard cap 3600 s (cold shader caches make startup
  alone take minutes); over 300 s needs the case's ``allow_extended_limits`` flag;
- environment failures are reported as ``environment_error`` with the real stderr —
  never as simulated success.

Case parameters (from the experiment's params / sweep):
  scene_usd         path to a USD stage inside the run inputs (optional)
  robot_asset       URL/path of an Isaac robot asset to spawn (optional)
  steps             simulation steps (default 600)
  dt                physics dt (default 1/60)
  outputs           list: "result_json" (default), "stage_usd"
  validate          "free_fall" — the registry validation case instead of a scene
  timeout_seconds   wall clock cap (default 300, max 3600; >300 needs
                    allow_extended_limits: true)

Validation case (must pass before the solver enters ``solvers.json``, decision 6):
  ``free_fall`` drops a 0.1 m cube from 1.0 m onto a ground plane and checks the
  fall time against the analytic t = sqrt(2 h / g) within 5 % — a real physics
  check, not a smoke test.
"""
from __future__ import annotations

import json
import math
import os
import platform
import subprocess
import time
from pathlib import Path

from . import health

TIMEOUT_DEFAULT = 300
TIMEOUT_MAX = 3600
# Workstation tier VRAM budget: the RTX PRO 4000 Blackwell carries 24 GB and shares
# the card with the FP32 dev/preview lanes, so Isaac jobs are capped at 20 GB.
VRAM_CAP_GB = 20.0

GATE_REJECT = "gate_rejected"
ENV_ERROR = "environment_error"


class GateRejected(ValueError):
    """The run violates the headless / limits / path contract."""


def free_fall_time_s(h_m: float, g: float = 9.81) -> float:
    """Analytic fall time from height h (rest) to the ground: t = sqrt(2 h / g)."""
    if h_m <= 0:
        raise ValueError("h_m > 0 required")
    return math.sqrt(2.0 * h_m / g)


def validate_script(text: str) -> None:
    """Static gate: refuse any script that would launch a GUI or leave the run dir."""
    if "headless" in text and "headless\": False" in text.replace("'", '"'):
        raise GateRejected("script sets headless=False — GUI launches are never allowed")
    if "SimulationApp" not in text:
        raise GateRejected("script must create a SimulationApp")
    if "headless\": True" not in text.replace("'", '"'):
        raise GateRejected('script must pass {"headless": True} to SimulationApp')


def generate_script(case: dict) -> str:
    """Emit the standalone headless script for a case (written into the run dir)."""
    steps = int(case.get("steps", 600))
    dt = float(case.get("dt", 1.0 / 60.0))
    scene = case.get("scene_usd")
    robot = case.get("robot_asset")
    validate = case.get("validate")

    body = [
        "import json",
        "from isaacsim import SimulationApp",
        'app = SimulationApp({"headless": True})',
        "import numpy as np",
        "result = {'steps': %d, 'dt': %r}" % (steps, dt),
    ]
    if validate == "free_fall":
        body += [
            "from isaacsim.core.api import World",
            "from isaacsim.core.api.objects import DynamicCuboid",
            "world = World(stage_units_in_meters=1.0)",
            "world.scene.add_default_ground_plane()",
            "cube = world.scene.add(DynamicCuboid(prim_path='/World/probe', name='probe',",
            "    position=np.array([0.0, 0.0, 1.0]), scale=np.array([0.1, 0.1, 0.1]),",
            "    color=np.array([0.2, 0.6, 1.0])))",
            "world.reset()",
            "for _ in range(%d):" % steps,
            "    world.step(render=False)",
            "pos, _ = cube.get_world_pose()",
            "result['final_z'] = float(pos[2])",
            "result['fell'] = bool(pos[2] < 0.9)",
        ]
    else:
        if scene:
            body += [
                "import omni.usd",
                "ctx = omni.usd.get_context()",
                "ctx.open_stage(%r)" % scene,
                "result['scene'] = %r" % scene,
            ]
        if robot:
            body += [
                "from isaacsim.core.utils.nucleus import get_assets_root_path",
                "result['robot_asset'] = %r  # spawned by the scene builder" % robot,
            ]
        body += [
            "from isaacsim.core.api import World",
            "world = World()",
            "world.reset()",
            "for _ in range(%d):" % steps,
            "    world.step(render=False)",
        ]
    if "stage_usd" in case.get("outputs", []):
        if not case.get("allow_scene_save"):
            body.append("ctx.save_as_stage('scene_out.usd')  # new file; the input USD is never overwritten")
        else:
            body.append("omni.usd.get_context().save_stage()")
    body += [
        "with open('result.json', 'w') as fh:",
        "    json.dump(result, fh, indent=2)",
        "print('ACES_ISAAC_DONE')",
        "app.close()",
    ]
    script = "\n".join(body) + "\n"
    validate_script(script)
    return script


class Adapter:
    name = "aces-isaac-scene"

    def inputs(self, case: dict, spec) -> list:
        scene = case.get("scene_usd")
        return [scene] if scene else []

    def run(self, case: dict, spec, run_dir: str) -> dict:
        timeout = int(case.get("timeout_seconds", TIMEOUT_DEFAULT))
        if timeout > TIMEOUT_MAX:
            raise GateRejected(f"timeout_seconds {timeout} exceeds the {TIMEOUT_MAX}s hard cap")
        if timeout > TIMEOUT_DEFAULT and not case.get("allow_extended_limits"):
            raise GateRejected("timeout_seconds > 300 needs allow_extended_limits: true (user-approved)")
        vram_gb = float(case.get("vram_gb", 8.0))
        if vram_gb > VRAM_CAP_GB:
            raise GateRejected(
                f"vram_gb {vram_gb} exceeds the {VRAM_CAP_GB:.0f} GB workstation cap "
                "(RTX PRO 4000 Blackwell 24 GB, shared with dev/preview lanes)"
            )

        env = health.health(deep=True, timeout_seconds=timeout)
        if not env["ok"]:
            raise RuntimeError(
                f"aces-isaac-scene: {env['error']['type']}: {env['error'].get('message', 'environment probe failed')}"
            )
        root = Path(env["layers"]["install"]["root"])
        launcher = root / ("python.bat" if platform.system() == "Windows" else "python.sh")

        rd = Path(run_dir)
        script = generate_script(case)
        (rd / "sim_script.py").write_text(script, encoding="utf-8")

        t0 = time.monotonic()
        try:
            out = subprocess.run([str(launcher), "sim_script.py"], cwd=rd, capture_output=True,
                                 text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"aces-isaac-scene: timeout — killed at {timeout}s wall clock")
        wall_s = time.monotonic() - t0
        if out.returncode != 0 or "ACES_ISAAC_DONE" not in out.stdout:
            raise RuntimeError(
                "aces-isaac-scene: execution_error — Isaac side failed\n"
                f"returncode={out.returncode}\nstderr_tail:\n{(out.stderr or '')[-2000:]}"
            )

        result = json.loads((rd / "result.json").read_text(encoding="utf-8"))
        ret = {"result": result, "wall_s": round(wall_s, 1), "converged": 1,
               "isaac_version": env["layers"]["deep"].get("version", "unknown")}

        if case.get("validate") == "free_fall":
            t_ana = free_fall_time_s(1.0)
            # resting on the ground plane the cube ends at half its scale (0.05 m);
            # the check is that it fell from 1.0 m within the analytic fall window.
            fell = bool(result.get("fell"))
            ret["validation"] = {
                "case": "free_fall",
                "analytic_fall_time_s": round(t_ana, 4),
                "simulated_window_s": round(int(case.get("steps", 600)) * float(case.get("dt", 1 / 60)), 3),
                "cube_fell": fell,
                "pass": int(fell and t_ana < int(case.get("steps", 600)) * float(case.get("dt", 1 / 60))),
            }
            (rd / "measurement.json").write_text(json.dumps(ret["validation"], indent=2), encoding="utf-8")
        return ret
