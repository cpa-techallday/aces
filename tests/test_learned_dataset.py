"""Clean-room learned-proposer dataset: rasterisation, conditioning, provenance gate."""
import json

import numpy as np
import pytest

from aces import cli
from aces.propose import learned


def _rows():
    return [{
        "kind": "hx_cell_measurement", "tpms": "gyroid", "wall_ratio": 0.08,
        "cell_m": 0.004, "fluid": "Air", "T_K": 320.0, "p_Pa": 200000.0,
        "reynolds": 26.7, "dpdx_Pa_m": 20.39, "h_W_m2K": 76.57,
        "thermal_boundary": "uniform_heat_flux",
        "source": "simlab:aces-cal-air-01:c000", "licence": "internal-verified",
    }, {
        "kind": "hx_cell_measurement", "tpms": "diamond", "wall_ratio": 0.16,
        "cell_m": 0.004, "fluid": "Air", "T_K": 320.0, "p_Pa": 200000.0,
        "reynolds": 97.0, "dpdx_Pa_m": 81.93, "h_W_m2K": 79.84,
        "thermal_boundary": "uniform_heat_flux",
        "source": "simlab:aces-cal-air-01:c017", "licence": "internal-verified",
    }]


def _write_corpus(tmp_path, rows):
    p = tmp_path / "corpus.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


def test_build_dataset_shapes_stats_and_provenance(tmp_path):
    rows = _rows()
    grids, cond, manifest = learned.build_dataset(rows, resolution=24)
    assert grids.shape == (2, 24, 24, 24) and grids.dtype == np.uint8
    assert cond.shape == (2, len(learned.KINDS) + len(learned.NUMERIC_FIELDS))
    assert 0.0 < grids[0].mean() < 1.0  # a wall sheet, not empty or dense
    # one-hot kind slots
    assert cond[0, 0] == 1.0 and cond[1, 1] == 1.0
    # log-Reynolds in the numeric block
    re_i = len(learned.KINDS) + learned.NUMERIC_FIELDS.index("reynolds")
    assert cond[0, re_i] == pytest.approx(np.log10(26.7))
    # normalisation stats over the numeric fields
    wr = manifest["normalisation"]["wall_ratio"]
    assert wr["mean"] == pytest.approx(0.12)
    # provenance: every row's source and licence ride along
    assert [r["source"] for r in manifest["rows"]] == [r["source"] for r in rows]
    assert all(r["licence"] == "internal-verified" for r in manifest["rows"])


def test_build_dataset_deterministic():
    a = learned.build_dataset(_rows(), resolution=16)
    b = learned.build_dataset(_rows(), resolution=16)
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])


def test_sourceless_row_refused(tmp_path):
    rows = _rows()
    rows[0]["source"] = ""
    p = _write_corpus(tmp_path, rows)
    with pytest.raises(ValueError, match="no source"):
        learned.load_corpus_rows(p)


def test_unknown_kind_refused():
    rows = _rows()
    rows[0]["tpms"] = "schwarzite"
    with pytest.raises(ValueError, match="unknown TPMS kind"):
        learned.build_dataset(rows, resolution=8)


def test_rasterize_matches_domain_definitions():
    # the training grid must be the same geometry the measurement layer sees
    from aces.domains.hx import tpms
    g = learned.rasterize_cell("gyroid", 0.12, 32)
    cell = tpms.unit_cell("gyroid", 0.12, resolution=32)
    assert g.mean() == pytest.approx(cell.solid_fraction, abs=0.05)


def test_cli_propose_dataset(tmp_path):
    p = _write_corpus(tmp_path, _rows())
    rc = cli.main(["propose-dataset", str(p), "--resolution", "16", "--out", str(tmp_path / "ds")])
    assert rc == 0
    grids = np.load(tmp_path / "ds" / "grids.npy")
    cond = np.load(tmp_path / "ds" / "cond.npy")
    manifest = json.loads((tmp_path / "ds" / "manifest.json").read_text())
    assert grids.shape == (2, 16, 16, 16)
    assert cond.shape[0] == 2
    assert manifest["n"] == 2 and manifest["resolution"] == 16
    assert manifest["rows"][0]["source"] == "simlab:aces-cal-air-01:c000"

    rc = cli.main(["propose-dataset", str(tmp_path / "missing.jsonl"), "--out", str(tmp_path / "x")])
    assert rc == 2
