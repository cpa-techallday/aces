"""Tier 0 training pipeline: data split, reference model, evaluation harness."""
import json

import numpy as np
import pytest

from aces import cli
from aces.propose import learned
from aces.propose.tier0 import data as tdata
from aces.propose.tier0 import evaluate as teval
from aces.propose.tier0 import model as tmodel
from aces.propose.tier0 import train as ttrain


def _rows():
    """12 rows: 3 kinds x 4 wall_ratios, mirrors the real corpus shape."""
    rows = []
    for k_i, kind in enumerate(("gyroid", "diamond", "primitive")):
        for w_i, wr in enumerate((0.08, 0.12, 0.16, 0.20)):
            rows.append({
                "kind": "hx_cell_measurement", "tpms": kind, "wall_ratio": wr,
                "cell_m": 0.004, "fluid": "Air", "T_K": 320.0, "p_Pa": 200000.0,
                "reynolds": 10.0 * (w_i + 1), "dpdx_Pa_m": 20.0, "h_W_m2K": 75.0,
                "thermal_boundary": "uniform_heat_flux",
                "source": f"simlab:test-campaign:{kind}-{wr}", "licence": "internal-verified",
            })
    return rows


def _dataset(tmp_path, resolution=16):
    rows = _rows()
    grids, cond, manifest = learned.build_dataset(rows, resolution=resolution)
    d = tmp_path / "ds"
    d.mkdir()
    np.save(d / "grids.npy", grids)
    np.save(d / "cond.npy", cond)
    (d / "manifest.json").write_text(json.dumps(manifest))
    return d, grids, cond, manifest


# -- data -----------------------------------------------------------------

def test_load_and_normalise(tmp_path):
    d, grids, cond, manifest = _dataset(tmp_path)
    g2, c2, m2 = tdata.load_dataset(d)
    assert np.array_equal(grids, g2) and np.array_equal(cond, c2)
    cn = tdata.normalise(c2, m2)
    nk = len(m2["kinds"])
    assert cn[:, nk:].mean() == pytest.approx(0.0, abs=1e-5)
    assert cn[:, nk:].std() == pytest.approx(1.0, abs=0.35)  # per-field z-scored
    with pytest.raises(ValueError, match="missing"):
        tdata.load_dataset(tmp_path)


def test_split_deterministic_and_stratified(tmp_path):
    _, _, _, manifest = _dataset(tmp_path)
    tr1, va1 = tdata.split(manifest, val_every=4)
    tr2, va2 = tdata.split(manifest, val_every=4)
    assert tr1 == tr2 and va1 == va2
    kinds_va = {manifest["rows"][i]["tpms"] for i in va1}
    assert kinds_va == {"gyroid", "diamond", "primitive"}  # every kind held out
    assert len(va1) == 3 and len(tr1) == 9
    with pytest.raises(ValueError, match="val_every"):
        tdata.split(manifest, val_every=2)


def test_cond_accessors(tmp_path):
    _, _, cond, manifest = _dataset(tmp_path)
    assert tdata.kind_of(manifest, cond[0]) == "gyroid"
    assert tdata.wall_ratio_of(manifest, cond[1]) == pytest.approx(0.12)
    assert tdata.reynolds_of(manifest, cond[1]) == pytest.approx(20.0)
    assert tdata.cell_m_of(manifest, cond[0]) == pytest.approx(0.004)


# -- model ----------------------------------------------------------------

def test_fit_reconstructs_training_rows(tmp_path):
    _, grids, cond, manifest = _dataset(tmp_path)
    cn = tdata.normalise(cond, manifest)
    m = tmodel.fit(grids, cn, manifest)
    # with k = n-1 components the PCA basis spans the centred data: BCE small
    assert m.bce(grids, cn) < 0.35
    with pytest.raises(ValueError, match="fewer than 3"):
        tmodel.fit(grids[:2], cn[:2], {**manifest, "rows": manifest["rows"][:2]})


def test_sample_shapes_validity_and_determinism(tmp_path):
    _, grids, cond, manifest = _dataset(tmp_path)
    cn = tdata.normalise(cond, manifest)
    m = tmodel.fit(grids, cn, manifest)
    a = m.sample(cn[0], "gyroid", 0.08, 4, seed=7)
    b = m.sample(cn[0], "gyroid", 0.08, 4, seed=7)
    assert a.shape == (4, 16, 16, 16) and a.dtype == np.uint8
    assert np.array_equal(a, b)  # seeded
    sf = m.target_solid_fraction("gyroid", 0.08)
    assert abs(a[0].mean() - sf) < 0.05  # binarisation matches the corpus anchor
    # wall_ratio trend is respected: thicker wall -> denser sample
    thin = m.sample(cn[0], "gyroid", 0.08, 1, seed=1)[0].mean()
    thick = m.sample(cn[0], "gyroid", 0.20, 1, seed=1)[0].mean()
    assert thick > thin


def test_save_load_roundtrip(tmp_path):
    _, grids, cond, manifest = _dataset(tmp_path)
    cn = tdata.normalise(cond, manifest)
    m = tmodel.fit(grids, cn, manifest)
    p = tmp_path / "model.npz"
    m.save(p, {"kind": "test"})
    m2 = tmodel.PCAConditionalModel.load(p)
    assert m2.bce(grids, cn) == pytest.approx(m.bce(grids, cn))
    assert m2.target_solid_fraction("diamond", 0.16) == pytest.approx(
        m.target_solid_fraction("diamond", 0.16))


# -- evaluation harness ----------------------------------------------------

def test_spearman_rho():
    assert teval.spearman_rho([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert teval.spearman_rho([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    assert teval.spearman_rho([1, 2, 3], [1, 3, 2]) == pytest.approx(0.5)
    with pytest.raises(ValueError, match=">= 3"):
        teval.spearman_rho([1, 2], [1, 2])
    with pytest.raises(ValueError, match="constant"):
        teval.spearman_rho([1, 1, 1], [1, 2, 3])


def test_evaluate_end_to_end(tmp_path):
    d, grids, cond, manifest = _dataset(tmp_path)
    run = tmp_path / "run"
    run.mkdir()

    class _OD:  # minimal OutDir stand-in for train.train
        def write_bytes(self, name, data):
            (run / name).write_bytes(data)
        def write_text(self, name, text):
            (run / name).write_text(text)

    metrics = ttrain.train(str(d), _OD(), val_every=4)
    assert (run / "model.npz").is_file() and (run / "model.json").is_file()
    assert metrics["n_train"] == 9 and metrics["n_val"] == 3
    tm = json.loads((run / "model.json").read_text())
    assert tm["held_out_sources"] and tm["train_sources"]
    assert all(s not in tm["train_sources"] for s in tm["held_out_sources"])

    report = teval.evaluate(str(run), str(d), n_per=1, val_every=4)
    assert report["val_bce"] > 0
    v = report["validity"]
    assert v["n_samples"] == 12
    assert v["solid_fraction_abs_err_mean"] < 0.10  # samples track requested wall_ratio
    # ranking: each kind has 4 distinct wall_ratios, so all three must rank well
    for kind in ("gyroid", "diamond", "primitive"):
        assert report["ranking"][kind]["spearman_rho"] > 0.8


# -- CLI -------------------------------------------------------------------

def test_cli_tier0_train_and_eval(tmp_path):
    rows = _rows()
    p = tmp_path / "corpus.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert cli.main(["propose-dataset", str(p), "--resolution", "16",
                     "--out", str(tmp_path / "ds")]) == 0
    assert cli.main(["tier0-train", str(tmp_path / "ds"), "--val-every", "4",
                     "--out", str(tmp_path / "run")]) == 0
    assert (tmp_path / "run" / "model.npz").is_file()
    assert cli.main(["tier0-eval", str(tmp_path / "run"), "--dataset", str(tmp_path / "ds"),
                     "--samples", "1", "--val-every", "4",
                     "--out", str(tmp_path / "eval")]) == 0
    report = json.loads((tmp_path / "eval" / "eval_report.json").read_text())
    assert report["validity"]["n_samples"] == 12
    # refusal paths
    assert cli.main(["tier0-train", str(tmp_path / "missing"), "--out",
                     str(tmp_path / "r2")]) == 2
    assert cli.main(["tier0-eval", str(tmp_path / "run"), "--dataset",
                     str(tmp_path / "missing"), "--out", str(tmp_path / "e2")]) == 2
