"""Tests for the Isaac Sim tier: gate, analytic references, probe behaviour.

None of these need Isaac Sim installed: the adapter must refuse cleanly when the
environment is absent, the script generator and gate are pure functions, and the
launcher is replaced by a mock of ``aces.proc.run``. Every trajectory below is a
synthetic test input built from the analytic free-fall law, not a simulator result.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
from types import SimpleNamespace

import pytest

from aces import proc, safeio
from aces.cli import cmd_isaac_health
from aces.isaac import adapter, health

posix_only = pytest.mark.skipif(os.name != "posix", reason="symlink semantics checked on POSIX")


# ------------------------------------------------------------------- gate

def test_gate_rejects_gui_script():
    bad = "from isaacsim import SimulationApp\napp = SimulationApp({'headless': False})\n"
    with pytest.raises(adapter.GateRejected, match="headless"):
        adapter.validate_script(bad)


def test_gate_rejects_missing_headless_flag():
    bad = "from isaacsim import SimulationApp\napp = SimulationApp({})\n"
    with pytest.raises(adapter.GateRejected, match="headless"):
        adapter.validate_script(bad)


def test_generated_script_passes_gate():
    script = adapter.generate_script({"validate": "free_fall", "steps": 300})
    adapter.validate_script(script)  # must not raise
    compile(script, "sim_script.py", "exec")
    assert '{"headless": True}' in script
    assert "DynamicCuboid" in script
    assert "result.json" in script


def test_free_fall_script_records_trajectory_at_the_fine_dt_and_sets_g():
    # I1: z(t) every physics step, the dt actually applied to the World, and g stated
    script = adapter.generate_script({"validate": "free_fall"})
    assert f"physics_dt={adapter.FREE_FALL_DT!r}" in script
    assert f"range({adapter.FREE_FALL_STEPS})" in script
    assert "z_m.append(" in script and "t_s.append(" in script
    assert "result['trajectory']" in script
    assert "GetGravityMagnitudeAttr().Set(9.80665)" in script
    assert "result['gravity_m_s2']" in script


def test_free_fall_case_refuses_a_coarse_dt():
    # at 1/60 s one step is 3.8 % of the 0.44 s fall, too close to the 5 % band
    with pytest.raises(adapter.GateRejected, match="dt"):
        adapter.generate_script({"validate": "free_fall", "dt": 1 / 60})


def test_generated_scene_script_never_overwrites_input_usd():
    script = adapter.generate_script({"scene_usd": "/runs/c000/scene.usd", "outputs": ["stage_usd"]})
    compile(script, "sim_script.py", "exec")
    assert "ctx.save_as_stage(_out)" in script
    assert "'scene_out.usd'" in script
    assert "save_stage()" not in script


def test_ctx_is_defined_before_it_is_used():
    # I4: stage_usd output without a scene used `ctx` before any assignment
    script = adapter.generate_script({"outputs": ["stage_usd"]})
    assert "ctx = omni.usd.get_context()" in script
    assert script.index("ctx = omni.usd.get_context()") < script.index("ctx.save_as_stage")


def test_allow_scene_save_is_refused():
    with pytest.raises(adapter.GateRejected, match="allow_scene_save"):
        adapter.generate_script({"scene_usd": "/runs/c000/scene.usd", "outputs": ["stage_usd"],
                                 "allow_scene_save": True})


def test_robot_asset_is_refused_until_supported(tmp_path):
    with pytest.raises(adapter.GateRejected, match="robot_asset"):
        adapter.generate_script({"robot_asset": "/Isaac/Robots/test.usd"})
    with pytest.raises(adapter.GateRejected, match="robot_asset"):
        adapter.Adapter().run({"robot_asset": "/Isaac/Robots/test.usd"}, None, str(tmp_path))


def test_timeout_limits():
    with pytest.raises(adapter.GateRejected, match="hard cap"):
        adapter.Adapter().run({"timeout_seconds": 3601}, None, "x")
    with pytest.raises(adapter.GateRejected, match="allow_extended_limits"):
        adapter.Adapter().run({"timeout_seconds": 600}, None, "x")


def test_vram_cap():
    with pytest.raises(adapter.GateRejected, match="VRAM|vram"):
        adapter.Adapter().run({"vram_gb": 24.0}, None, "x")


# ------------------------------------------------------------------- precision (I2)

def test_float64_request_is_refused():
    spec = SimpleNamespace(resources=SimpleNamespace(precision="float64", vram_gb=8.0), tags={"kind": "scene"})
    with pytest.raises(adapter.GateRejected, match="float32"):
        adapter.Adapter().run({}, spec, "x")
    with pytest.raises(adapter.GateRejected, match="float32"):
        adapter.Adapter().run({"precision": "float64"}, None, "x")


def test_verification_kind_is_refused():
    spec = SimpleNamespace(resources=SimpleNamespace(precision="float32"), tags={"kind": "verification"})
    with pytest.raises(adapter.GateRejected, match="verification"):
        adapter.Adapter().run({}, spec, "x")
    with pytest.raises(adapter.GateRejected, match="verification"):
        adapter.Adapter().run({"kind": "verification"}, None, "x")


# ------------------------------------------------------------------- physics reference

def test_free_fall_time():
    # t = sqrt(2 h / g) (uniform acceleration from rest; Halliday, Resnick & Walker, Fundamentals of Physics,
    # ch. 2), with standard gravity g_n = 9.80665 m/s^2 (3rd CGPM, 1901). h = 1 m: sqrt(2 / 9.80665) = 0.451601 s
    assert adapter.G_STANDARD == 9.80665
    assert adapter.free_fall_time_s(1.0) == pytest.approx(0.451601, abs=1e-6)
    assert adapter.free_fall_time_s(2.0) == pytest.approx(math.sqrt(2) * adapter.free_fall_time_s(1.0))
    with pytest.raises(ValueError):
        adapter.free_fall_time_s(0.0)


# Test input: a synthetic trajectory, the analytic free fall of the cube's centre, with the cube held at rest on
# the plane once its bottom face reaches it (what a contact solver does). Not a simulator result.
def _result(*, g_eff=None, dt=None, steps=None, bounce_amp_m=0.0, gravity_m_s2=9.806650161743164, finished=True):
    g_eff = adapter.G_STANDARD if g_eff is None else g_eff
    dt = adapter.FREE_FALL_DT if dt is None else dt
    steps = adapter.FREE_FALL_STEPS if steps is None else steps
    half = adapter.FREE_FALL_SIZE_M / 2
    t_land = math.sqrt(2 * (adapter.FREE_FALL_H_M - half) / g_eff) if g_eff > 0 else math.inf
    t_s, z_m = [], []
    for i in range(steps + 1):
        t = i * dt
        if t < t_land:
            z = adapter.FREE_FALL_H_M - 0.5 * g_eff * t * t
        else:  # resting, or bouncing at 2 Hz when bounce_amp_m > 0
            z = half + bounce_amp_m * abs(math.sin(2 * math.pi * 2.0 * (t - t_land)))
        t_s.append(t)
        z_m.append(max(z, half))
    return {"steps": steps, "dt": dt, "precision": "float32", "finished": finished,
            "gravity_m_s2": gravity_m_s2, "trajectory": {"t_s": t_s, "z_m": z_m},
            "final_linear_velocity_m_s": [0.0, 0.0, 0.0]}


def test_free_fall_exact_passes():
    # Hand check: bottom face starts at 1.0 - 0.05 = 0.95 m, so t = sqrt(2 * 0.95 / 9.80665) = 0.440166 s.
    # Sampled every 1/240 s, the bottom face is at 0.95 - 4.903325 * 0.4375**2 = 0.011473 m at step 105 and on the
    # plane at step 106 (0.441667 s): the interpolated contact is 0.441667 s, +0.34 % from analytic.
    v = adapter.analyse_free_fall(_result())
    assert v["fall_distance_m"] == pytest.approx(0.95)
    assert v["analytic_fall_time_s"] == pytest.approx(0.440166, abs=1e-6)
    assert v["contact_time_s"] == pytest.approx(106 / 240, abs=1e-9)
    assert v["error_pct"] == pytest.approx(0.341, abs=0.01)
    assert v["g_m_s2"] == 9.80665
    assert v["cube_size_m"] == 0.1
    assert v["precision"] == "float32"
    assert v["converged"] == 1
    assert v["pass"] == 1
    assert v["reasons"] == []


def test_free_fall_ten_percent_slow_fails():
    # g_eff = g / 1.21 makes the fall take 1.1 x the analytic time: +10 %, outside the 5 % band
    v = adapter.analyse_free_fall(_result(g_eff=9.80665 / 1.21))
    assert v["error_pct"] == pytest.approx(10.0, abs=1.0)
    assert v["converged"] == 1
    assert v["pass"] == 0
    assert any("5" in r for r in v["reasons"])


def test_free_fall_never_lands_fails():
    v = adapter.analyse_free_fall(_result(g_eff=0.0))
    assert v["contact_time_s"] is None
    assert v["pass"] == 0
    assert any("never" in r for r in v["reasons"])


def test_free_fall_still_bouncing_is_not_converged():
    v = adapter.analyse_free_fall(_result(bounce_amp_m=0.02))
    assert abs(v["error_pct"]) < 5.0  # the first contact is still on time ...
    assert v["at_rest"] is False
    assert v["converged"] == 0  # ... but the cube is moving at the end
    assert v["pass"] == 0


def test_free_fall_unfinished_run_is_not_converged():
    r = _result(finished=False)
    assert adapter.analyse_free_fall(r)["converged"] == 0
    r = _result()
    r["trajectory"]["t_s"].pop()
    r["trajectory"]["z_m"].pop()  # one step short of the requested 720
    assert adapter.analyse_free_fall(r)["converged"] == 0


def test_free_fall_simulator_gravity_must_match_the_stated_g():
    # Isaac's default 9.81 m/s^2 left in place differs from 9.80665 by 3.4e-4 (relative)
    v = adapter.analyse_free_fall(_result(gravity_m_s2=9.81))
    assert v["pass"] == 0
    assert any("gravity" in r for r in v["reasons"])


def test_free_fall_missing_trajectory_fails():
    v = adapter.analyse_free_fall({"steps": 720, "dt": adapter.FREE_FALL_DT, "finished": True,
                                   "gravity_m_s2": 9.80665})
    assert v["pass"] == 0 and v["converged"] == 0
    assert any("trajectory" in r for r in v["reasons"])


# ------------------------------------------------------------------- adapter run (launcher mocked)

def _healthy(root):
    return lambda deep=False, timeout_seconds=300: {
        "ok": True,
        "layers": {"gpu": {"ok": True}, "install": {"ok": True, "root": str(root),
                                                    "launcher": str(root / "python.sh")},
                   "deep": {"ok": True, "version": "test-version"}}}


def test_adapter_runs_through_proc_with_minimal_env(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.setattr(health, "health", _healthy(tmp_path / "isaac"))
    monkeypatch.setenv("ACES_TEST_SECRET_TOKEN", "do-not-pass")
    monkeypatch.setenv("ISAAC_SIM_PATH", str(tmp_path / "isaac"))
    calls = []

    def fake_run(cmd, *, cwd=None, env=None, timeout=None, text=True, input=None, check=False):
        calls.append({"cmd": cmd, "cwd": cwd, "env": env, "timeout": timeout})
        with open(os.path.join(cwd, "result.json"), "w", encoding="utf-8") as fh:
            json.dump(_result(), fh)
        return subprocess.CompletedProcess(cmd, 0, "ACES_ISAAC_DONE\n", "")

    monkeypatch.setattr(proc, "run", fake_run)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("subprocess.run must not be used"))
    ret = adapter.Adapter().run({"validate": "free_fall"}, None, str(run_dir))

    assert len(calls) == 1
    env = calls[0]["env"]
    assert env is not None and "ACES_TEST_SECRET_TOKEN" not in env
    assert env["ISAAC_SIM_PATH"] == str(tmp_path / "isaac")
    assert calls[0]["timeout"] == adapter.TIMEOUT_DEFAULT
    assert ret["precision"] == "float32"
    assert any("float32" in w for w in ret["warnings"])
    assert ret["converged"] == 1
    assert ret["validation"]["pass"] == 1
    assert "trajectory" not in ret["result"]  # kept in result.json, not in the returned record
    meas = json.loads((run_dir / "measurement.json").read_text())
    assert meas["pass"] == 1 and meas["precision"] == "float32"
    assert (run_dir / "sim_script.py").is_file()


def test_adapter_timeout_goes_through_proc(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "health", _healthy(tmp_path))

    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(proc, "run", fake_run)
    with pytest.raises(RuntimeError, match="timeout"):
        adapter.Adapter().run({"validate": "free_fall"}, None, str(tmp_path))


@posix_only
def test_adapter_refuses_a_planted_measurement_link(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    victim = tmp_path / "victim.json"
    victim.write_text("keep")
    (run_dir / "measurement.json").symlink_to(victim)
    monkeypatch.setattr(health, "health", _healthy(tmp_path))

    def fake_run(cmd, *, cwd=None, **kw):
        with open(os.path.join(cwd, "result.json"), "w", encoding="utf-8") as fh:
            json.dump(_result(), fh)
        return subprocess.CompletedProcess(cmd, 0, "ACES_ISAAC_DONE\n", "")

    monkeypatch.setattr(proc, "run", fake_run)
    with pytest.raises(safeio.UnsafePathError):
        adapter.Adapter().run({"validate": "free_fall"}, None, str(run_dir))
    assert victim.read_text() == "keep"


# ------------------------------------------------------------------- scene confinement (I4)

def _no_env(monkeypatch):
    monkeypatch.setattr(health, "health", lambda deep=False, timeout_seconds=300: {
        "ok": False, "layers": {}, "error": {"type": "environment_error", "message": "no install"}})


def test_scene_outside_the_run_folder_is_refused(tmp_path, monkeypatch):
    _no_env(monkeypatch)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (tmp_path / "outside.usd").write_text("#usda 1.0\n")
    for scene in ("../outside.usd", str(tmp_path / "outside.usd"), "missing.usd"):
        with pytest.raises(adapter.GateRejected, match="scene_usd"):
            adapter.Adapter().run({"scene_usd": scene}, None, str(run_dir))


@posix_only
def test_scene_link_is_refused(tmp_path, monkeypatch):
    _no_env(monkeypatch)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "real.usd").write_text("#usda 1.0\n")
    (run_dir / "scene.usd").symlink_to(run_dir / "real.usd")
    with pytest.raises(adapter.GateRejected, match="link"):
        adapter.Adapter().run({"scene_usd": "scene.usd"}, None, str(run_dir))


def test_scene_inside_the_run_folder_passes_the_gate(tmp_path, monkeypatch):
    _no_env(monkeypatch)
    run_dir = tmp_path / "run"
    (run_dir / "in").mkdir(parents=True)
    (run_dir / "in" / "scene.usd").write_text("#usda 1.0\n")
    with pytest.raises(RuntimeError, match="environment_error"):  # past the gate, stopped by the probe
        adapter.Adapter().run({"scene_usd": "in/scene.usd"}, None, str(run_dir))


# ------------------------------------------------------------------- health probe

def test_probe_install_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("ISAAC_SIM_PATH", raising=False)
    monkeypatch.setattr(health, "_WINDOWS_ROOTS", (str(tmp_path / "nope"),))
    monkeypatch.setattr(health, "_LINUX_ROOTS", (str(tmp_path / "nope"),))
    monkeypatch.setattr("pathlib.Path.is_dir", lambda self: False)
    rep = health.probe_install()
    assert not rep["ok"]
    assert rep["error"]["type"] == "environment_error"
    assert "ISAAC_SIM_PATH" in rep["error"]["message"]


def test_probe_install_found(tmp_path, monkeypatch):
    launcher = "python.bat" if health.platform.system() == "Windows" else "python.sh"
    (tmp_path / launcher).write_text("rem\n")
    monkeypatch.setenv("ISAAC_SIM_PATH", str(tmp_path))
    rep = health.probe_install()
    assert rep["ok"]
    assert rep["root"] == str(tmp_path)


def test_health_skips_deep_when_install_missing(monkeypatch):
    monkeypatch.setattr(health, "probe_gpu", lambda: {"ok": True, "degraded": False, "gpus": []})
    monkeypatch.setattr(health, "probe_install", lambda: health._err("no install"))
    rep = health.health(deep=True)
    assert not rep["ok"]
    assert rep["layers"]["deep"]["skipped"] is True
    assert rep["error"]["type"] == "environment_error"


def test_nvml_failure_is_reported_and_the_deep_probe_arbitrates(monkeypatch):
    """NVML is invisible from automation shells on DARKNESS, so a degraded GPU layer lets the deep probe decide
    (upstream rule, 71dc594); the NVML failure is still reported, never hidden (I4)."""
    nvml = {"ok": False, "degraded": True, "detail": "Failed to initialize NVML: Unknown Error",
            "guidance": health.GPU_GUIDANCE}
    monkeypatch.setattr(health, "probe_gpu", lambda: nvml)
    monkeypatch.setattr(health, "probe_install", lambda: {"ok": True, "root": "/opt/isaacsim"})
    calls = []

    def deep(root, timeout_seconds=300, *, ok=True):
        calls.append(root)
        return {"ok": True, "version": "test-version"}

    monkeypatch.setattr(health, "probe_deep", deep)
    rep = health.health(deep=True)
    assert calls == ["/opt/isaacsim"] and rep["ok"] is True
    assert "NVML" in rep["layers"]["gpu"]["detail"]
    monkeypatch.setattr(health, "probe_deep", lambda root, timeout_seconds=300: {
        "ok": False, "error": {"type": "execution_error", "message": "SimulationApp did not boot"}})
    rep = health.health(deep=True)
    assert rep["ok"] is False and rep["error"]["message"] == "SimulationApp did not boot"


def test_no_install_means_no_deep_probe_with_the_reason(monkeypatch):
    monkeypatch.setattr(health, "probe_gpu", lambda: {"ok": True, "degraded": False})
    monkeypatch.setattr(health, "probe_install", lambda: {"ok": False, "error": {"message": "no Isaac Sim found"}})
    deep = health.health(deep=True)["layers"]["deep"]
    assert deep["ok"] is False and "no Isaac Sim found" in deep["error"]["message"]


def test_cli_prints_the_gpu_and_deep_layer_reasons(monkeypatch, capsys):
    monkeypatch.setattr(health, "probe_gpu", lambda: {
        "ok": False, "degraded": True, "detail": "Failed to initialize NVML: Unknown Error",
        "guidance": health.GPU_GUIDANCE})
    monkeypatch.setattr(health, "probe_install", lambda: {"ok": True, "root": "/opt/isaacsim"})
    monkeypatch.setattr(health, "probe_deep", lambda root, timeout_seconds=300: {
        "ok": False, "error": {"type": "execution_error", "message": "SimulationApp did not boot"}})
    assert cmd_isaac_health(SimpleNamespace(deep=True, timeout=300)) == 1
    out = capsys.readouterr().out
    assert "NVML" in out.split("GPU:", 1)[1].splitlines()[0]
    assert "Deep:    FAIL" in out and "did not boot" in out.split("Deep:", 1)[1].splitlines()[0]


def test_probe_gpu_goes_through_proc(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(proc, "run", fake_run)
    monkeypatch.setenv("ACES_TEST_SECRET_TOKEN", "do-not-pass")
    rep = health.probe_gpu()
    assert rep["ok"] is False and rep["degraded"] is True
    assert seen["env"] is not None and "ACES_TEST_SECRET_TOKEN" not in seen["env"]
    assert seen["timeout"] == 30


def test_probe_deep_timeout_goes_through_proc(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "_deep_cache", {})
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(proc, "run", fake_run)
    rep = health.probe_deep(str(tmp_path), timeout_seconds=7)
    assert rep["ok"] is False and rep["error"]["type"] == "timeout"
    assert seen["timeout"] == 7 and seen["env"] is not None


def test_adapter_refuses_without_install(tmp_path, monkeypatch):
    _no_env(monkeypatch)
    with pytest.raises(RuntimeError, match="environment_error"):
        adapter.Adapter().run({"validate": "free_fall"}, None, str(tmp_path))
