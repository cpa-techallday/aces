"""The ARC-1 study regenerates its first stages from cad/params.json in a clean copy."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from aces import manufacturing

STUDY = Path(__file__).resolve().parents[1] / "studies" / "arc1_chassis"


def test_study_route_is_listed_but_not_selectable():
    p = manufacturing.get("arc_lattice_rod_welding")
    assert p.status == "study"
    with pytest.raises(ValueError, match="study"):
        manufacturing.require("arc_lattice_rod_welding", "studies/arc1_chassis")


def test_arc1_first_stages_regenerate(tmp_path):
    pytest.importorskip("Pynite")
    work = tmp_path / "arc1"
    shutil.copytree(STUDY / "cad", work / "cad")
    shutil.copytree(STUDY / "scripts", work / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    for args in (["process.py"], ["boom.py"], ["frame.py", "--dump"]):
        r = subprocess.run([sys.executable, str(work / "scripts" / args[0]), *args[1:]], cwd=work,
                           capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stderr[-2000:]
    for name in ("process_rules.json", "boom_calibration.json", "arc1_frame.json"):
        assert (work / "out" / name).stat().st_size > 0
