"""simlab solver adapter for Isaac Sim scene simulation (solver id ``aces-isaac-scene``).

Placement decision (ACES_HANDOFF, "Excluded on purpose"): the rig's CMP 170HX cards
have no RTX renderer and no display, so Isaac Sim runs **on the workstation only**.
This adapter is therefore the workstation-resident solver; the rig never carries the
``aces-isaac`` image.  The contract mirrors the isaac-sim plugin:

- headless only — generated scripts use ``SimulationApp({"headless": True})`` and the
  gate rejects any script containing ``headless=False``;
- file-based I/O inside the run directory: ``scene_usd`` must resolve to a file inside
  the run folder (links refused), and a run never saves over an existing USD
  (``outputs: ["stage_usd"]`` writes a new ``scene_out.usd`` in the run folder);
- wall-clock timeout default 300 s, hard cap 3600 s (cold shader caches make startup
  alone take minutes); over 300 s needs the case's ``allow_extended_limits`` flag.
  Isaac runs through :func:`aces.proc.run`, so a timeout kills the whole process group,
  with a minimal environment (:data:`aces.isaac.health.ISAAC_ENV_KEEP`);
- environment failures are reported as ``environment_error`` with the real stderr —
  never as simulated success.

Precision (CLAUDE.md rule 6): PhysX runs in float32.  Every result and validation record
says ``precision: float32`` and carries a warning; a request for float64 or a
``kind: verification`` run is refused.  This tier checks scenes and robotics; it never
signs off a verification solve.

Case parameters (from the experiment's params / sweep):
  scene_usd         path to a USD stage inside the run folder (optional)
  steps             simulation steps (default 600; free_fall default 720)
  dt                physics dt (default 1/60; free_fall 1/240 or finer)
  outputs           list: "result_json" (default), "stage_usd"
  validate          "free_fall" — the registry validation case instead of a scene
  timeout_seconds   wall clock cap (default 300, max 3600; >300 needs
                    allow_extended_limits: true)
Refused until supported: ``robot_asset`` (no robot is spawned yet) and
``allow_scene_save`` (the input USD is never saved over).

Validation case (must pass before the solver enters ``solvers.json``; D5 in
docs/DECISIONS.md: a registered simlab solver signs off, inherited from simlab decision 7):
  ``free_fall`` drops a 0.1 m cube (centre at 1.0 m) onto a ground plane at z = 0, records
  the cube's z(t) every physics step, and compares the time its bottom face reaches the
  plane with the analytic t = sqrt(2 d / g), d = 0.95 m, g = 9.80665 m/s^2.  It passes
  only within 5 %, with the run finished and the cube at rest at the end.
"""
from __future__ import annotations

import itertools
import json
import math
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path

from .. import proc, safeio
from . import health

TIMEOUT_DEFAULT = 300
TIMEOUT_MAX = 3600
# Workstation tier VRAM budget: the RTX PRO 4000 Blackwell carries 24 GB and shares
# the card with the FP32 dev/preview lanes, so Isaac jobs are capped at 20 GB.
VRAM_CAP_GB = 20.0

GATE_REJECT = "gate_rejected"
ENV_ERROR = "environment_error"

# PhysX integrates in float32 (rule 6: FP64 for every verification solve).
PRECISION = "float32"
FP32_WARNING = ("Isaac Sim / PhysX runs in float32: use this result for scene and robotics checks only, "
                "never as a verification solve (CLAUDE.md rule 6)")

# Standard acceleration of gravity, g_n = 9.80665 m/s^2 (3rd CGPM, 1901).
G_STANDARD = 9.80665

# free_fall validation case: the geometry of the test, not material data.
FREE_FALL_H_M = 1.0        # start height of the cube's centre
FREE_FALL_SIZE_M = 0.1     # cube edge (DynamicCuboid of size 1.0 scaled by 0.1)
FREE_FALL_GROUND_Z_M = 0.0  # ground plane height
# dt: the bottom face falls d = 0.95 m in sqrt(2 * 0.95 / 9.80665) = 0.440 s.  After contact the solver holds
# the cube on the plane, so the first sample at the plane is not on the free-fall curve and interpolation cannot
# recover sub-step timing: the measured contact carries up to one step of error (plus a half-step lead from the
# integrator).  At 1/60 s one step is 3.8 % of 0.44 s, which would eat most of the 5 % band; at 1/240 s it is
# 0.95 %, leaving over 4 % for real solver error.  Finer steps are accepted, coarser ones refused.
FREE_FALL_DT = 1.0 / 240.0
FREE_FALL_STEPS = 720      # 3.0 s at 1/240: the fall takes 0.44 s, the rest lets the cube settle
FREE_FALL_TOL_PCT = 5.0
# The bottom face counts as on the plane within 1 mm (PhysX rest offset and solver residual); crossing that band
# 1 mm early moves the contact time by 1e-3 m / 4.3 m/s = 0.2 ms (0.05 %).
CONTACT_TOL_M = 1e-3
# At rest: speed below 1 mm/s over the last 0.1 s of the run.
REST_SPEED_TOL_M_S = 1e-3
REST_WINDOW_S = 0.1
# The stage stores gravity as a 32-bit float: 9.80665 round-trips to within 2e-8 (relative).  Anything further off
# means the simulator did not use the stated g (Isaac's own default is 9.81).
GRAVITY_REL_TOL = 1e-6

RESULT_MAX_BYTES = 64 * 1024 * 1024


class GateRejected(ValueError):
    """The run violates the headless / limits / path / precision contract."""


def free_fall_time_s(h_m: float, g: float = G_STANDARD) -> float:
    """Analytic fall time over a distance h from rest: t = sqrt(2 h / g)."""
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


def _lookup(obj, *path):
    """``obj.a.b`` or ``obj["a"]["b"]``, None when any step is missing (simlab spec or plain dict)."""
    for key in path:
        if obj is None:
            return None
        obj = obj.get(key) if isinstance(obj, Mapping) else getattr(obj, key, None)
    return obj


def check_precision(case: dict, spec) -> None:
    """Refuse float64 requests and verification runs: PhysX is float32 (rule 6)."""
    for where, value in (("spec resources.precision", _lookup(spec, "resources", "precision")),
                         ("case precision", case.get("precision"))):
        if value is not None and str(value).lower() != PRECISION:
            raise GateRejected(
                f"{where} is {value!r}: aces-isaac-scene runs PhysX in float32 and cannot honour it. "
                "Submit with precision: float32 (rule 6: verification solves need FP64 and another solver)")
    for where, value in (("spec tags.kind", _lookup(spec, "tags", "kind")), ("spec kind", _lookup(spec, "kind")),
                         ("case kind", case.get("kind"))):
        if value == "verification":
            raise GateRejected(
                f"{where} is 'verification': aces-isaac-scene is a float32 scene/robotics tier and never signs off "
                "a verification solve (rule 6). Use a float64 simlab solver")


def _check_case(case: dict) -> None:
    if case.get("robot_asset") is not None:
        raise GateRejected("robot_asset is not supported yet: the adapter does not spawn robots. "
                           "Build the robot into scene_usd (inside the run folder) instead")
    if case.get("allow_scene_save"):
        raise GateRejected("allow_scene_save is not supported: a run never saves over its input USD; "
                           'outputs: ["stage_usd"] writes a new scene_out.usd in the run folder')
    if case.get("validate") == "free_fall" and "dt" in case and float(case["dt"]) > FREE_FALL_DT:
        raise GateRejected(f"the free_fall validation case needs dt <= 1/240 s (got {float(case['dt']):.6g} s): "
                           "one 1/60 s step is 3.8 % of the 0.44 s fall, too close to the 5 % band")


def _confine_scene(scene, run_dir: str | Path) -> Path:
    """Resolve ``scene_usd`` and refuse anything outside the run folder, missing, or reached through a link."""
    if not isinstance(scene, str) or not scene.strip():
        raise GateRejected(f"scene_usd must be a path inside the run folder, got {scene!r}")
    base = Path(run_dir)
    p = Path(scene) if Path(scene).is_absolute() else base / scene
    stops = {base, base.absolute()}
    for q in (p, *p.parents):  # every component below the run folder; links above it are not the case's
        if q in stops:
            break
        if safeio.is_link(q):
            raise GateRejected(f"scene_usd {scene!r} goes through a link ({q}); links are refused")
    try:
        resolved = p.resolve(strict=True)
        root = base.resolve(strict=True)
    except (OSError, RuntimeError):
        raise GateRejected(f"scene_usd {scene!r} not found in the run folder {base}") from None
    if not resolved.is_relative_to(root):
        raise GateRejected(f"scene_usd {scene!r} resolves outside the run folder {root}")
    if not resolved.is_file():
        raise GateRejected(f"scene_usd {scene!r} is not a file")
    return resolved


# The generated script writes its JSON the way aces.safeio does: exclusive create, never through a link.
# (Isaac's own Python can't import aces.)
_WRITE_RESULT = [
    "_fd = os.open('result.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o644)",
    "with os.fdopen(_fd, 'w', encoding='utf-8') as fh:",
    "    json.dump(result, fh, indent=1)",
]


def generate_script(case: dict) -> str:
    """Emit the standalone headless script for a case (written into the run dir)."""
    _check_case(case)
    validate = case.get("validate")
    free_fall = validate == "free_fall"
    steps = int(case.get("steps", FREE_FALL_STEPS if free_fall else 600))
    dt = float(case.get("dt", FREE_FALL_DT if free_fall else 1.0 / 60.0))
    scene = case.get("scene_usd")

    body = [
        "import json",
        "import os",
        "from isaacsim import SimulationApp",
        'app = SimulationApp({"headless": True})',
        "import numpy as np",
        "import omni.usd",
        "ctx = omni.usd.get_context()",
        f"result = {{'steps': {steps}, 'dt': {dt!r}, 'precision': {PRECISION!r}, 'finished': False}}",
    ]
    if free_fall:
        g, h, s, z0 = G_STANDARD, FREE_FALL_H_M, FREE_FALL_SIZE_M, FREE_FALL_GROUND_Z_M
        body += [
            "from pxr import Gf, UsdPhysics",
            "from isaacsim.core.api import World",
            "from isaacsim.core.api.objects import DynamicCuboid",
            f"world = World(stage_units_in_meters=1.0, physics_dt={dt!r}, rendering_dt={dt!r},",
            "              physics_prim_path='/physicsScene')",
            f"world.scene.add_default_ground_plane(z_position={z0!r}, restitution=0.0)",
            "phys = UsdPhysics.Scene.Get(ctx.get_stage(), '/physicsScene')",
            "if not phys:",
            "    raise RuntimeError('no physics scene at /physicsScene')",
            "phys.GetGravityDirectionAttr().Set(Gf.Vec3f(0.0, 0.0, -1.0))",
            f"phys.GetGravityMagnitudeAttr().Set({g!r})",
            "cube = world.scene.add(DynamicCuboid(prim_path='/World/probe', name='probe',",
            f"    position=np.array([0.0, 0.0, {h!r}]), scale=np.array([{s!r}, {s!r}, {s!r}]),",
            "    color=np.array([0.2, 0.6, 1.0])))",
            "world.reset()",
            "result['gravity_m_s2'] = float(phys.GetGravityMagnitudeAttr().Get())",
            "pos, _ = cube.get_world_pose()",
            "t_s = [float(world.current_time)]",
            "z_m = [float(pos[2])]",
            f"for _ in range({steps}):",
            "    world.step(render=False)",
            "    pos, _ = cube.get_world_pose()",
            "    t_s.append(float(world.current_time))",
            "    z_m.append(float(pos[2]))",
            "result['trajectory'] = {'t_s': t_s, 'z_m': z_m}",
            "result['final_linear_velocity_m_s'] = [float(v) for v in cube.get_linear_velocity()]",
        ]
    else:
        if scene:
            body += [
                f"ctx.open_stage({scene!r})",
                f"result['scene'] = {scene!r}",
            ]
        body += [
            "from isaacsim.core.api import World",
            f"world = World(physics_dt={dt!r}, rendering_dt={dt!r})",
            "world.reset()",
            f"for _ in range({steps}):",
            "    world.step(render=False)",
        ]
    body.append("result['finished'] = True")
    if "stage_usd" in case.get("outputs", []):
        body += [
            "_out = os.path.join(os.getcwd(), 'scene_out.usd')",
            "if os.path.lexists(_out):",
            "    raise SystemExit('scene_out.usd already exists in the run folder; refusing to overwrite it')",
            "ctx.save_as_stage(_out)  # a new file in the run folder; the input USD is never saved over",
            "result['stage_usd'] = 'scene_out.usd'",
        ]
    body += [*_WRITE_RESULT, "print('ACES_ISAAC_DONE')", "app.close()"]
    script = "\n".join(body) + "\n"
    validate_script(script)
    return script


def _finite(xs) -> bool:
    return isinstance(xs, list) and all(isinstance(x, (int, float)) and math.isfinite(x) for x in xs)


def analyse_free_fall(result: dict, *, g: float = G_STANDARD, h_m: float = FREE_FALL_H_M,
                      size_m: float = FREE_FALL_SIZE_M, ground_z_m: float = FREE_FALL_GROUND_Z_M) -> dict:
    """Validation record for the free_fall case from the run's ``result.json``.

    Contact time: the first sample whose bottom face (centre z - size/2) is within ``CONTACT_TOL_M`` of the plane,
    linearly interpolated with the sample before it to the plane itself (clamped to that step).  It is compared
    with t = sqrt(2 d / g), d the actual fall distance of the bottom face from its first sample, timed from that
    sample (the simulator clock may not read zero after reset).  ``converged``
    needs the run finished (every step recorded) and the cube at rest at the end.  ``pass`` needs the contact time
    within 5 %, ``converged`` and the simulator's gravity equal to the stated g.
    """
    reasons: list[str] = []
    steps = result.get("steps")
    rec: dict = {
        "case": "free_fall", "precision": PRECISION, "g_m_s2": g, "g_simulator_m_s2": result.get("gravity_m_s2"),
        "start_height_m": h_m, "cube_size_m": size_m, "ground_z_m": ground_z_m, "dt_s": result.get("dt"),
        "steps": steps, "tolerance_pct": FREE_FALL_TOL_PCT, "contact_tol_m": CONTACT_TOL_M,
        "rest_speed_tol_m_s": REST_SPEED_TOL_M_S, "rest_window_s": REST_WINDOW_S,
        "start_z_m": None, "fall_distance_m": None, "analytic_fall_time_s": None, "contact_time_s": None,
        "error_pct": None, "final_speed_m_s": None, "at_rest": False, "finished": False,
        "converged": 0, "pass": 0, "reasons": reasons, "warnings": [FP32_WARNING],
    }
    traj = result.get("trajectory") or {}
    t, z = traj.get("t_s"), traj.get("z_m")
    if not (_finite(t) and _finite(z) and len(t) == len(z) >= 2
            and all(b > a for a, b in itertools.pairwise(t))):
        reasons.append("no usable trajectory in result.json (t_s and z_m: equal-length finite lists, t increasing)")
        return rec

    rec["finished"] = result.get("finished") is True and (steps is None or len(z) == int(steps) + 1)
    if not rec["finished"]:
        reasons.append(f"run did not finish: {len(z) - 1} of {steps} steps recorded")

    # at rest: the fastest motion over the last REST_WINDOW_S, and the simulator's own final velocity
    i0 = next(i for i, ti in enumerate(t) if ti >= t[-1] - REST_WINDOW_S)
    i0 = min(i0, len(t) - 2)
    speed = max(abs(z[i + 1] - z[i]) / (t[i + 1] - t[i]) for i in range(i0, len(t) - 1))
    v_end = result.get("final_linear_velocity_m_s")
    if _finite(v_end) and v_end:
        speed = max(speed, math.sqrt(sum(v * v for v in v_end)))
    rec["final_speed_m_s"] = speed
    rec["at_rest"] = speed < REST_SPEED_TOL_M_S
    if not rec["at_rest"]:
        reasons.append(f"cube still moving at the end: {speed:.3g} m/s >= {REST_SPEED_TOL_M_S} m/s")
    rec["converged"] = int(rec["finished"] and rec["at_rest"])

    g_sim = result.get("gravity_m_s2")
    if not (isinstance(g_sim, (int, float)) and abs(g_sim - g) <= GRAVITY_REL_TOL * g):
        reasons.append(f"simulator gravity {g_sim!r} m/s^2 is not the stated g = {g} m/s^2")
        gravity_ok = False
    else:
        gravity_ok = True

    half = size_m / 2.0
    bottom = [zi - half - ground_z_m for zi in z]
    rec["start_z_m"] = z[0]
    d = bottom[0]
    if d <= CONTACT_TOL_M:
        reasons.append(f"cube started on the plane (bottom face {d:.4g} m above it)")
        return rec
    t_ana = free_fall_time_s(d, g)
    rec["fall_distance_m"] = d
    rec["analytic_fall_time_s"] = t_ana

    k = next((i for i, b in enumerate(bottom) if b <= CONTACT_TOL_M), None)
    if k is None:
        reasons.append(f"cube never reached the plane in {t[-1]:.3g} s (lowest bottom face {min(bottom):.4g} m)")
        return rec
    frac = min(1.0, bottom[k - 1] / (bottom[k - 1] - bottom[k]))
    t_c = t[k - 1] + frac * (t[k] - t[k - 1]) - t[0]  # from release: the first sample, at rest after reset
    rec["contact_time_s"] = t_c
    rec["error_pct"] = 100.0 * (t_c - t_ana) / t_ana
    timing_ok = abs(rec["error_pct"]) <= FREE_FALL_TOL_PCT
    if not timing_ok:
        reasons.append(f"contact at {t_c:.4f} s vs analytic {t_ana:.4f} s: {rec['error_pct']:+.2f} %, "
                       f"outside +/-{FREE_FALL_TOL_PCT} %")
    rec["pass"] = int(timing_ok and gravity_ok and rec["converged"] == 1)
    return rec


class Adapter:
    name = "aces-isaac-scene"
    precision = PRECISION

    def inputs(self, case: dict, spec) -> list:
        scene = case.get("scene_usd")
        return [scene] if scene else []

    def run(self, case: dict, spec, run_dir: str) -> dict:
        _check_case(case)
        check_precision(case, spec)
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
        if case.get("scene_usd") is not None and case.get("validate") != "free_fall":
            case = {**case, "scene_usd": str(_confine_scene(case["scene_usd"], run_dir))}

        env = health.health(deep=True, timeout_seconds=timeout)
        if not env["ok"]:
            raise RuntimeError(
                f"aces-isaac-scene: {env['error']['type']}: {env['error'].get('message', 'environment probe failed')}"
            )
        install = env["layers"]["install"]
        launcher = install["launcher"]  # install-root python.bat/python.sh, or the pip interpreter itself

        rd = Path(run_dir)
        script = generate_script(case)
        with safeio.OutDir(rd, mkdir=False) as out_dir:
            out_dir.write_text("sim_script.py", script)

        t0 = time.monotonic()
        try:
            out = proc.run([str(launcher), "sim_script.py"], cwd=rd, env=health.isaac_env(), timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"aces-isaac-scene: timeout — process group killed at {timeout}s wall clock") from None
        wall_s = time.monotonic() - t0
        if out.returncode != 0 or "ACES_ISAAC_DONE" not in (out.stdout or ""):
            raise RuntimeError(
                "aces-isaac-scene: execution_error — Isaac side failed\n"
                f"returncode={out.returncode}\nstderr_tail:\n{(out.stderr or '')[-2000:]}"
            )

        result = json.loads(safeio.read_text_limited(rd / "result.json", RESULT_MAX_BYTES))
        warnings = [FP32_WARNING]
        if case.get("validate") == "free_fall":
            validation = analyse_free_fall(result)
            converged = validation["converged"]
        else:
            validation = None
            converged = int(result.get("finished") is True)
            warnings.append("scene run: converged = 1 means every step completed; no physical convergence "
                            "criterion is checked")
        ret = {"result": {k: v for k, v in result.items() if k != "trajectory"}, "wall_s": round(wall_s, 1),
               "converged": converged, "precision": PRECISION, "warnings": warnings,
               "isaac_version": env["layers"]["deep"].get("version", "unknown"),
               "artifacts": [str(rd / "result.json")]}
        if validation is not None:
            ret["validation"] = validation
            with safeio.OutDir(rd, mkdir=False) as out_dir:
                ret["artifacts"].append(str(out_dir.write_text("measurement.json", json.dumps(validation, indent=2))))
        return ret
