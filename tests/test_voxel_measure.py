"""Voxel measurement layer: known-geometry fixtures, percolation, quarantine stamping."""
import json

import numpy as np
import pytest

from aces import cli
from aces.propose import RESEARCH_STAMP
from aces.propose.voxel_measure import measure_voxel_solid


def _plate() -> np.ndarray:
    """64^3 void with a solid plate spanning y-z at x=28..35 (8 voxels thick)."""
    g = np.zeros((64, 64, 64), dtype=bool)
    g[28:36, :, :] = True
    return g


def test_plate_wall_thickness_and_fraction():
    pitch = 1e-3
    f = measure_voxel_solid(_plate(), pitch, proposer="test", seed=0)
    assert f.solid_fraction == pytest.approx(8 / 64, abs=1e-6)
    # plate is 8 voxels = 8 mm thick; EDT-level quantisation may shave one voxel
    assert f.wall_min_m == pytest.approx(8 * pitch, abs=pitch)
    assert f.wall_mean_m == pytest.approx(8 * pitch, abs=2 * pitch)


def test_plate_blocks_x_but_not_y():
    f = measure_voxel_solid(_plate(), 1e-3, proposer="test", seed=0)
    assert f.neck_m[0] == 0.0  # plate spans both y and z faces: nothing passes along x
    assert f.neck_m[1] > 0.5 * 1e-3  # void is open along y
    assert f.isolated_void_fraction == 0.0


def test_enclosed_void_pocket_detected():
    """A solid sphere shell leaves an enclosed void pocket (AM powder trap)."""
    g = np.zeros((48, 48, 48), dtype=bool)
    c = 24.0
    x, y, z = np.meshgrid(*[np.arange(48) - c + 0.5] * 3, indexing="ij")
    r = np.sqrt(x ** 2 + y ** 2 + z ** 2)
    g |= (r >= 14.0) & (r <= 20.0)  # shell
    f = measure_voxel_solid(g, 1e-3, proposer="test", seed=0)
    pocket_volume = (4.0 / 3.0) * np.pi * 14.0 ** 3
    total_void = np.count_nonzero(~g)
    assert f.isolated_void_fraction == pytest.approx(pocket_volume / total_void, rel=0.2)


def test_empty_and_dense_grids_refused():
    with pytest.raises(ValueError, match="empty"):
        measure_voxel_solid(np.zeros((8, 8, 8), bool), 1e-3, "test", 0)
    with pytest.raises(ValueError, match="dense"):
        measure_voxel_solid(np.ones((8, 8, 8), bool), 1e-3, "test", 0)
    with pytest.raises(ValueError, match="3D"):
        measure_voxel_solid(np.zeros((8, 8), bool), 1e-3, "test", 0)


def test_cli_voxel_measure(tmp_path):
    np.save(tmp_path / "plate.npy", _plate())
    rc = cli.main(["voxel-measure", str(tmp_path / "plate.npy"), "--pitch-m", "0.001",
                   "--proposer", "tpms-parametric", "--seed", "7", "--out", str(tmp_path / "m")])
    assert rc == 0
    d = json.loads((tmp_path / "m" / "measurement.json").read_text())
    assert d["proposer"] == "tpms-parametric" and d["seed"] == 7
    assert d["solid_fraction"] == pytest.approx(0.125, abs=1e-3)
    assert d["neck_m"][0] == 0.0
    assert d["notes"] == []


def test_cli_research_quarantine_stamp(tmp_path, capsys):
    np.save(tmp_path / "plate.npy", _plate())
    # refused without the flag
    rc = cli.main(["voxel-measure", str(tmp_path / "plate.npy"), "--pitch-m", "0.001",
                   "--proposer", "nvidia-xcube", "--out", str(tmp_path / "q")])
    assert rc == 2
    assert "research-only" in capsys.readouterr().out
    assert not (tmp_path / "q").exists()
    # admitted with the flag, and stamped
    rc = cli.main(["voxel-measure", str(tmp_path / "plate.npy"), "--pitch-m", "0.001",
                   "--proposer", "nvidia-xcube", "--seed", "42", "--research",
                   "--out", str(tmp_path / "r")])
    assert rc == 0
    assert "RESEARCH ONLY" in capsys.readouterr().out
    d = json.loads((tmp_path / "r" / "measurement.json").read_text())
    assert RESEARCH_STAMP in d["notes"]
