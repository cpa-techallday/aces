"""job32: the radar EM CLI trio — em-sample / em-campaign / em-collect."""
import json

import pytest
import yaml

from aces import cli


def _parse(argv):
    return cli.build_parser().parse_args(argv)


# ------------------------------------------------------------------ parser
def test_parser_em_trio_registered():
    ns = _parse(["em-sample", "--structure", "tile_coupon", "--out", "x"])
    assert ns.fn is cli.cmd_em_sample
    ns = _parse(["em-campaign", "--id", "e", "--out", "x"])
    assert ns.fn is cli.cmd_em_campaign
    ns = _parse(["em-collect", "d", "--out", "x"])
    assert ns.fn is cli.cmd_em_collect


def test_em_sample_requires_structure():
    with pytest.raises(SystemExit):
        _parse(["em-sample", "--out", "x"])


# ------------------------------------------------------------------ campaign
def test_em_campaign_validation(tmp_path):
    out = tmp_path / "exp.yaml"
    ns = _parse(["em-campaign", "--id", "emval", "--validation",
                 "--out", str(out)])
    assert ns.fn(ns) == 0
    exp = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert exp["solver"] == "aces-tile-em"
    assert exp["physics"] == "em"
    assert exp["tags"]["kind"] == "validation"
    assert exp["sweep"]["structure"] == ["patch_xband", "tile_coupon",
                                         "tile_multiport", "wr90_through"]


def test_em_campaign_calibration_sweep(tmp_path):
    out = tmp_path / "exp.yaml"
    ns = _parse(["em-campaign", "--id", "emcal", "--structure", "tile_coupon",
                 "tile_multiport", "--f0", "9.5e9", "10.5e9",
                 "--out", str(out)])
    assert ns.fn(ns) == 0
    exp = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert exp["tags"]["kind"] == "calibration"
    n = len(exp["sweep"]["structure"]) * len(exp["sweep"]["f0_hz"])
    assert n == 4
    assert exp["params"]["stackup"]["substrate_h_m"] == 0.254e-3


def test_em_campaign_bad_structure(tmp_path, capsys):
    ns = _parse(["em-campaign", "--id", "x", "--structure", "nonsense",
                 "--out", str(tmp_path / "e.yaml")])
    assert ns.fn(ns) == 2
    assert "unknown structure" in capsys.readouterr().out


def test_em_campaign_cpu_lane_zero_vram(tmp_path):
    """conformal-antenna SF-2: aces-tile-em (gprMax) is CPU/OpenMP — the emitted spec must
    declare vram_gb 0 (simlab cpu queue) and carry the host-RAM request in params, never as VRAM."""
    out = tmp_path / "exp.yaml"
    ns = _parse(["em-campaign", "--id", "emcpu", "--structure", "tile_coupon",
                 "--mem-gb", "6", "--out", str(out)])
    assert ns.fn(ns) == 0
    exp = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert exp["resources"]["vram_gb"] == 0.0
    assert exp["params"]["mem_gb"] == 6


def test_em_campaign_refuses_existing_out(tmp_path):
    out = tmp_path / "exp.yaml"
    out.write_text("x", encoding="utf-8")
    ns = _parse(["em-campaign", "--id", "x", "--out", str(out)])
    assert ns.fn(ns) == 2


# ------------------------------------------------------------------ collect
def _case_dir(root, name, status="FINISHED", row=None, evidence=True):
    d = root / name
    d.mkdir(parents=True)
    (d / "status.json").write_text(json.dumps({"status": status}),
                                   encoding="utf-8")
    if row is not None:
        (d / "measurement.json").write_text(json.dumps(row), encoding="utf-8")
    if evidence:
        (d / "evidence.json").write_text("{}", encoding="utf-8")
    return d


ROW = {"kind": "tile_insertion_loss_db", "frequency_hz": 10.5e9,
       "value": 0.102, "model_value": 0.099, "source": "simlab:old"}


def test_em_collect_gathers_and_stamps_provenance(tmp_path):
    _case_dir(tmp_path, "case1", row=dict(ROW))
    _case_dir(tmp_path, "case2", row=dict(ROW, value=0.11))
    out = tmp_path / "rows.json"
    ns = _parse(["em-collect", str(tmp_path), "--out", str(out)])
    assert ns.fn(ns) == 0
    rows = json.loads(out.read_text(encoding="utf-8"))
    assert len(rows) == 2
    assert rows[0]["source"] == f"simlab:{tmp_path.name}:case1"  # folder is provenance


def test_em_collect_skips_unfinished_no_meas_no_evidence(tmp_path, capsys):
    _case_dir(tmp_path, "running", status="RUNNING", row=dict(ROW))
    _case_dir(tmp_path, "nomeas")
    _case_dir(tmp_path, "noev", row=dict(ROW), evidence=False)
    _case_dir(tmp_path, "bad", row={"kind": "nonsense", "frequency_hz": 1e9,
                                    "value": 1, "model_value": 1})
    _case_dir(tmp_path, "good", row=dict(ROW))
    out = tmp_path / "rows.json"
    ns = _parse(["em-collect", str(tmp_path), "--out", str(out)])
    assert ns.fn(ns) == 0
    rows = json.loads(out.read_text(encoding="utf-8"))
    assert [r["source"] for r in rows] == [f"simlab:{tmp_path.name}:good"]
    txt = capsys.readouterr().out
    for name in ("running", "nomeas", "noev", "bad"):
        assert f"SKIPPED {name}" in txt


def test_em_collect_no_rows_returns_1(tmp_path):
    _case_dir(tmp_path, "running", status="RUNNING")
    ns = _parse(["em-collect", str(tmp_path), "--out", str(tmp_path / "r.json")])
    assert ns.fn(ns) == 1


def test_em_collect_not_an_experiment(tmp_path):
    ns = _parse(["em-collect", str(tmp_path / "nope"), "--out",
                 str(tmp_path / "r.json")])
    assert ns.fn(ns) == 2


# ------------------------------------------------------------------ sample
def test_em_sample_kicad_needs_pcb_and_net(tmp_path, capsys):
    ns = _parse(["em-sample", "--structure", "kicad_net",
                 "--out", str(tmp_path / "r.json")])
    assert ns.fn(ns) == 2
    assert "--pcb and --net" in capsys.readouterr().out


def test_em_sample_refuses_existing_out(tmp_path):
    out = tmp_path / "r.json"
    out.write_text("x", encoding="utf-8")
    ns = _parse(["em-sample", "--structure", "tile_coupon", "--out", str(out)])
    assert ns.fn(ns) == 2


def test_em_sample_bad_case_json(tmp_path):
    cj = tmp_path / "case.json"
    cj.write_text("[1, 2]", encoding="utf-8")
    ns = _parse(["em-sample", "--structure", "tile_coupon",
                 "--case-json", str(cj), "--out", str(tmp_path / "r.json")])
    assert ns.fn(ns) == 2
