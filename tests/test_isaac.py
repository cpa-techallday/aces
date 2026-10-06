"""Tests for the Isaac Sim tier: gate, analytic references, probe behaviour.

None of these need Isaac Sim installed — the adapter must refuse cleanly when the
environment is absent, and the script generator + gate are pure functions.
"""
from __future__ import annotations

import math

import pytest

from aces.isaac import adapter, health


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
    assert '{"headless": True}' in script
    assert "DynamicCuboid" in script
    assert "result.json" in script


def test_generated_scene_script_never_overwrites_input_usd():
    script = adapter.generate_script({"scene_usd": "in/scene.usd", "outputs": ["stage_usd"]})
    assert "save_as_stage('scene_out.usd')" in script
    assert "save_stage()" not in script


def test_timeout_limits():
    with pytest.raises(adapter.GateRejected, match="hard cap"):
        adapter.Adapter().run({"timeout_seconds": 3601}, None, "x")
    with pytest.raises(adapter.GateRejected, match="allow_extended_limits"):
        adapter.Adapter().run({"timeout_seconds": 600}, None, "x")


# ------------------------------------------------------------------- physics reference

def test_free_fall_time():
    assert adapter.free_fall_time_s(1.0) == pytest.approx(math.sqrt(2 / 9.81), rel=1e-9)
    assert adapter.free_fall_time_s(2.0) == pytest.approx(math.sqrt(2) * adapter.free_fall_time_s(1.0))
    with pytest.raises(ValueError):
        adapter.free_fall_time_s(0.0)


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


def test_adapter_refuses_without_install(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "health",
                        lambda deep=False, timeout_seconds=300: {
                            "ok": False,
                            "layers": {},
                            "error": {"type": "environment_error", "message": "no install"}})
    with pytest.raises(RuntimeError, match="environment_error"):
        adapter.Adapter().run({"validate": "free_fall"}, None, str(tmp_path))
