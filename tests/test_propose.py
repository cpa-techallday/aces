"""Tier 0 proposer contract: licence gate, determinism, measurability, corpus provenance."""
import json

import pytest

from aces import cli
from aces.propose import LicenceError, Proposer, available, get, register
from aces.propose.tpms_parametric import TpmsParametricProposer, measure


class _Dummy:
    name = "dummy"
    licence = "internal"
    domain = "hx"

    def propose(self, req, n, seed):
        return []


def test_licence_gate_refuses_unknown_licence():
    bad = _Dummy()
    bad.name = "dummy-gpl"
    bad.licence = "GPL-3.0"
    with pytest.raises(LicenceError):
        register(bad)


def test_rejected_proposers_fail_loudly():
    with pytest.raises(LicenceError, match="non-commercial"):
        get("nvidia-xcube")
    with pytest.raises(LicenceError):
        class X(_Dummy):
            name = "nvidia-xcube"
        register(X())


def test_research_lane_requires_explicit_flag():
    # without the flag the quarantine holds, even after a research registration
    class X(_Dummy):
        name = "nvidia-xcube"
        licence = "Nvidia-Source-Code-License"

    register(X(), research=True)
    with pytest.raises(LicenceError, match="research-only"):
        get("nvidia-xcube")
    assert get("nvidia-xcube", research=True).name == "nvidia-xcube"
    # research=True never whitewashes a merely unknown licence
    bad = _Dummy()
    bad.name = "dummy-gpl-research"
    bad.licence = "GPL-3.0"
    with pytest.raises(LicenceError):
        register(bad, research=True)


def test_reference_proposer_registered():
    assert "tpms-parametric" in available(domain="hx")
    assert get("tpms-parametric").licence == "internal"


def test_proposals_deterministic_and_in_range():
    p = TpmsParametricProposer()
    a = [x.params for x in p.propose({}, 6, seed=7)]
    b = [x.params for x in p.propose({}, 6, seed=7)]
    c = [x.params for x in p.propose({}, 6, seed=8)]
    assert a == b and a != c
    for params in a:
        assert params["tpms"] in ("gyroid", "diamond", "primitive")
        assert 0.0 < params["wall_ratio"] < 0.6
        assert 0.002 < params["cell_m"] < 0.006


def test_proposals_are_measurable_on_domain_definitions():
    p = TpmsParametricProposer()
    prop = next(iter(p.propose({"kinds": ["gyroid"]}, 1, seed=3)))
    m = measure(prop)
    assert 0.0 < m["solid_fraction"] < 1.0
    assert m["wall_min_over_a"] > 0.0
    assert all(0.0 <= f <= 1.0 for f in m["isolated_fraction"])


def test_unknown_kind_refused():
    p = TpmsParametricProposer()
    with pytest.raises(ValueError):
        list(p.propose({"kinds": ["schwarzite"]}, 1, seed=1))


def test_corpus_rows_carry_provenance(tmp_path):
    rows = [{
        "source": "simlab:aces-cal-air-01:c000", "tpms": "gyroid", "cell_m": 0.004,
        "wall_ratio": 0.08, "fluid": "Air", "T_K": 320.0, "p_Pa": 200000.0,
        "mass_flux_kg_m2s": 0.0366, "dpdx_Pa_m": 20.39, "h_W_m2K": 76.57,
    }, {
        "source": "simlab:aces-cal-air-01:c001", "tpms": "gyroid", "cell_m": 0.004,
        "wall_ratio": 0.08, "fluid": "Air", "T_K": 320.0, "p_Pa": 200000.0,
        "mass_flux_kg_m2s": 0.11, "dpdx_Pa_m": 81.93, "h_W_m2K": 79.84,
    }]
    mp = tmp_path / "m.json"
    mp.write_text(json.dumps(rows))
    from aces.domains.hx import calibrate as hxcal
    from aces.propose import corpus as corpus_mod

    # the provenance gate itself: a source-less row never even loads (D18)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{k: v for k, v in rows[0].items() if k != "source"}]))
    with pytest.raises(ValueError, match="invalid rows"):
        hxcal.load(bad)

    built, skipped = corpus_mod.build_corpus(mp)
    assert len(built) == 2 and skipped == []
    r = built[0]
    assert r["source"] == "simlab:aces-cal-air-01:c000"
    assert r["licence"] == "internal-verified"
    assert r["reynolds"] > 0
    out = tmp_path / "corpus.jsonl"
    corpus_mod.write_jsonl(built, out)
    assert len(out.read_text().strip().splitlines()) == 2


def test_cli_propose_and_unknown_method(tmp_path, capsys):
    rc = cli.main(["propose", "--method", "tpms-parametric", "--n", "3", "--seed", "5",
                   "--kinds", "gyroid", "--out", str(tmp_path / "p")])
    assert rc == 0
    data = json.loads((tmp_path / "p" / "proposals.json").read_text())
    assert data["n"] == 3 and data["proposer"] == "tpms-parametric"
    assert all(pr["params"]["tpms"] == "gyroid" for pr in data["proposals"])
    assert "not verified" in data["note"]

    rc = cli.main(["propose", "--method", "nvidia-xcube", "--out", str(tmp_path / "q")])
    assert rc == 2
    assert "non-commercial" in capsys.readouterr().out
    assert not (tmp_path / "q").exists()


def test_cli_research_lane_stamps_every_proposal(tmp_path):
    from aces.propose import RESEARCH_STAMP, Proposal

    class _ResearchXcube:
        name = "nvidia-xcube-test"
        licence = "Nvidia-Source-Code-License"
        domain = "hx"

        def propose(self, req, n, seed):
            yield Proposal(proposer=self.name, domain="hx", seed=seed,
                           params={"tpms": "gyroid", "wall_ratio": 0.1, "cell_m": 0.004})

    from aces.propose import contract
    contract.RESEARCH_ONLY[_ResearchXcube.name] = "test entry"
    try:
        register(_ResearchXcube(), research=True)
        rc = cli.main(["propose", "--method", "nvidia-xcube-test", "--research",
                       "--n", "1", "--out", str(tmp_path / "r")])
        assert rc == 0
        data = json.loads((tmp_path / "r" / "proposals.json").read_text())
        assert data["research_only"] is True
        assert RESEARCH_STAMP in data["note"]
        assert all(RESEARCH_STAMP in pr["notes"] for pr in data["proposals"])

        rc = cli.main(["propose", "--method", "nvidia-xcube-test", "--out", str(tmp_path / "r2")])
        assert rc == 2
        assert not (tmp_path / "r2").exists()
    finally:
        contract.RESEARCH_ONLY.pop(_ResearchXcube.name, None)
        contract._REGISTRY.pop(_ResearchXcube.name, None)
