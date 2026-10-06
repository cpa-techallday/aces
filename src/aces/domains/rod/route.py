"""rod.lattice: run a rod-lattice study (ARC-1) from an ACES spec, check it, record evidence.

The study scripts stay the engineering logic.  ACES copies the study's ``cad/`` and
``scripts/`` into the run folder (so the original is never touched and every run is
reproducible from its own copy), runs the requested stages with the chosen params
file, then checks the outputs against the study's own process limits:

  problems (route not buildable as designed, or not checked)
    - rod-by-rod utilisation above the spec's limit (rodcheck.py: the frame FEA forces
      mapped onto every chord, batten and diagonal, each against its own length)
    - without rodcheck.py in the stages: frame FEA utilisation above the limit, and the
      same utilisation rescaled to the weakest rod's capacity (a conservative bound)
    - rods shorter than the process minimum (node balls merge)
    - a requested stage that fails or times out
    - a check whose output is missing (a run never passes by skipping a check)
  warnings (buildable, with caveats)
    - rods longer than the process maximum (capacity reduced; check the L/d rule)
    - nodes over the valence limit or under the weld-access angle (machined hubs)
    - rods that need a fixture in the build sequence
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, field_validator

from ... import manufacturing

DOMAIN = "rod.lattice"
DEFAULT_STAGES = ["process.py", "boom.py", "frame.py --dump", "micro.py --mesh", "rodcheck.py", "buildplan.py",
                  "budget.py"]


class RodRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    process: str = Field(description="arc_lattice_rod_welding")
    study_dir: str = Field(description="study folder with cad/ and scripts/, relative to the spec")
    params: str = Field("params.json", description="params file inside cad/")
    material_source: str = Field(min_length=3, description="where the params' material values come from")
    stages: list[str] = Field(default_factory=lambda: list(DEFAULT_STAGES))
    max_utilisation: PositiveFloat = 1.0
    length_tolerance_mm: float = Field(0.5, ge=0.0, description="as micro.py: rods within this of a limit pass")
    stage_timeout_s: PositiveFloat = 1800

    @field_validator("process")
    @classmethod
    def _route(cls, v: str) -> str:
        manufacturing.require(v, DOMAIN)
        return v

    @field_validator("params")
    @classmethod
    def _params(cls, v: str) -> str:
        if not _bare(v):
            raise ValueError(f"params must be a file name inside cad/, got {v!r}")
        return v

    @field_validator("stages")
    @classmethod
    def _stages(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("stages must name at least one script")
        for stage in v:
            parts = stage.split()
            if not parts or not _bare(parts[0]) or not parts[0].endswith(".py"):
                raise ValueError(f"stage {stage!r} must start with a .py file name inside scripts/")
            if any(c in a for a in parts[1:] for c in ("/", "\\", ":", "..")):
                raise ValueError(f"stage {stage!r} arguments may not reference paths")
        return v


WORK_MARKER = ".aces-rod-work"


def _bare(name: str) -> bool:
    """A plain file name: no directory separators, no parent references."""
    return bool(name) and name not in (".", "..") and not any(c in name for c in "/\\:") and ".." not in name


def _text(b) -> str:
    """TimeoutExpired carries bytes even when the run asked for text."""
    if b is None:
        return ""
    return b.decode("utf-8", errors="replace") if isinstance(b, bytes) else str(b)


def _read_json(p: Path, problems: list[str]) -> Any:
    """Read a stage output; a missing or malformed file is a problem, never a crash."""
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError) as e:
        problems.append(f"could not read {p.name}: {e}")
        return None


def _rod_lengths(out: Path) -> np.ndarray | None:
    nodes_p, rods_p = out / "micro_nodes.npy", out / "micro_rods.json"
    if not (nodes_p.exists() and rods_p.exists()):
        return None
    nodes = np.load(nodes_p, allow_pickle=False)
    rods = json.loads(rods_p.read_text())["rods"]
    if not rods:
        raise ValueError("micro_rods.json lists no rods")
    ij = np.array([[r[0], r[1]] for r in rods], dtype=int)
    return np.linalg.norm(nodes[ij[:, 0]] - nodes[ij[:, 1]], axis=1)


def run(req: RodRequirements, out_dir: str | Path, spec_dir: str | Path, log=print) -> dict[str, Any]:
    study = (Path(spec_dir) / req.study_dir).resolve()
    if not (study / "cad" / req.params).exists() or not (study / "scripts").is_dir():
        raise FileNotFoundError(f"{study} must contain cad/{req.params} and scripts/")
    for stage in req.stages:
        if not (study / "scripts" / stage.split()[0]).is_file():
            raise FileNotFoundError(f"stage script {stage.split()[0]!r} is not in {study / 'scripts'}")
    out = Path(out_dir).resolve()
    work = out / "work"
    if out.is_relative_to(study) or study.is_relative_to(work):
        raise FileExistsError(f"--out {out} overlaps the study {study}; the route copies the study, so write "
                              f"the run somewhere outside it")
    if work.exists() or work.is_symlink():
        # only ever delete a folder this route created
        if work.is_symlink() or not (work / WORK_MARKER).is_file():
            raise FileExistsError(f"{work} exists and was not created by the rod route; choose another --out")
        shutil.rmtree(work)
    work.mkdir(parents=True)
    (work / WORK_MARKER).write_text("created by aces rod; safe to delete\n")
    shutil.copytree(study / "cad", work / "cad")
    shutil.copytree(study / "scripts", work / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    P = json.loads((work / "cad" / req.params).read_text())
    res_dir = work / P.get("output", {}).get("dir", "out")

    env = dict(os.environ, ARC1_PARAMS=req.params, MPLBACKEND="Agg")
    stage_log: list[dict] = []
    problems: list[str] = []
    log_p = out / "stages.log"
    if log_p.is_symlink():
        raise FileExistsError(f"{log_p} is a symbolic link; refusing to write through it")
    with log_p.open("w") as fh:
        for stage in req.stages:
            script, *args = stage.split()
            log(f"[rod] {stage}")
            try:
                r = subprocess.run([sys.executable, str(work / "scripts" / script), *args], cwd=work, env=env,
                                   capture_output=True, text=True, timeout=req.stage_timeout_s)
            except subprocess.TimeoutExpired as e:
                fh.write(f"===== {stage} (timed out after {req.stage_timeout_s:g} s)\n{_text(e.stdout)}\n"
                         f"{_text(e.stderr)}\n")
                stage_log.append({"stage": stage, "exit": None, "timeout_s": req.stage_timeout_s})
                problems.append(f"stage `{stage}` timed out after {req.stage_timeout_s:g} s; see stages.log")
                break
            fh.write(f"===== {stage} (exit {r.returncode})\n{r.stdout}\n{r.stderr}\n")
            stage_log.append({"stage": stage, "exit": r.returncode})
            if r.returncode != 0:
                problems.append(f"stage `{stage}` failed (exit {r.returncode}); see stages.log")
                break

    pr = P.get("process", {})
    summary: dict[str, Any] = {"params_rev": P.get("rev"), "process": pr.get("name")}
    warnings: list[str] = []

    fr: dict[str, Any] | None = None
    raw = _read_json(res_dir / "arc1_frame.json", problems)
    if isinstance(raw, dict) and isinstance(raw.get("result"), dict) \
            and isinstance(raw["result"].get("max_utilisation"), (int, float)):
        fr = raw["result"]
        summary["frame"] = {k: fr.get(k) for k in ("nodes", "members", "frame_mass_kg", "torsion_Nm_per_deg",
                                                   "capacity_comp_N", "max_utilisation", "over_unity")}
    else:
        problems.append("frame check not run: arc1_frame.json is missing or has no max_utilisation "
                        "(structural utilisation unknown)")

    ms: dict[str, Any] | None = None
    raw = _read_json(res_dir / "micro_stats.json", problems)
    if isinstance(raw, dict):
        ms = raw
        summary["micro"] = {k: ms.get(k) for k in ("nodes", "rods", "rod_total_m", "mass_total_kg", "weld_hours",
                                                   "nodes_weldable", "nodes_need_hub")}
    else:
        problems.append("rod check not run: micro_stats.json is missing (rod lengths and capacities unknown)")

    rc = _read_json(res_dir / "rod_check.json", problems)
    if not (isinstance(rc, dict) and isinstance(rc.get("max_utilisation"), (int, float))):
        rc = None
    if rc is not None:
        summary["rod_check"] = {k: rc.get(k) for k in ("rods", "max_utilisation", "over_unity",
                                                       "members_with_rods_over_unity", "by_kind")}
        if rc["max_utilisation"] > req.max_utilisation:
            w = (rc.get("worst") or [{}])[0]
            problems.append(f"rod-by-rod utilisation {rc['max_utilisation']:.2f} > {req.max_utilisation:g} "
                            f"({rc.get('over_unity')} rod(s) in {rc.get('members_with_rods_over_unity')} member(s); "
                            f"worst: {w.get('kind')} of a {w.get('class')} boom in {w.get('case')})")
        if fr is not None and fr["max_utilisation"] > req.max_utilisation:
            warnings.append(f"frame FEA utilisation with nominal 60 mm rods is {fr['max_utilisation']:.2f}; the "
                            f"rod-by-rod check ({rc['max_utilisation']:.2f}) uses each rod's own length and governs")
    elif fr is not None and fr["max_utilisation"] > req.max_utilisation:
        problems.append(f"frame FEA utilisation {fr['max_utilisation']:.2f} > {req.max_utilisation:g} "
                        f"({fr.get('over_unity')} member(s) over unity)")

    # Without rodcheck.py: the frame FEA divides member force by the capacity of a NOMINAL-length rod, and
    # micro.py sizes each rod from its own length, so the weakest rod can carry less.  Rescale conservatively
    # (as if the weakest rod sat in the most loaded member) and report it.
    if rc is None and fr is not None and ms is not None:
        pc = fr.get("capacity_comp_N")
        if pc is None:
            rules = _read_json(res_dir / "process_rules.json", problems)
            pc = rules.get("compression_capacity_N") if isinstance(rules, dict) else None
        cmin = (ms.get("capacity_N") or {}).get("min") if isinstance(ms.get("capacity_N"), dict) else None
        if not isinstance(pc, (int, float)) or not isinstance(cmin, (int, float)) or pc <= 0:
            problems.append("weakest-rod check not run: the nominal or per-rod compression capacity is missing")
        elif cmin <= 0:
            problems.append(f"weakest rod has no compression capacity ({cmin} N)")
        else:
            u_adj = fr["max_utilisation"] * pc / cmin
            summary["frame"]["utilisation_weakest_rod"] = u_adj
            if cmin < pc and u_adj > req.max_utilisation:
                problems.append(f"utilisation rescaled to the weakest rod's capacity ({cmin:.0f} N vs nominal "
                                f"{pc:.0f} N) is {u_adj:.2f} > {req.max_utilisation:g} (conservative bound; add "
                                f"rodcheck.py to the stages for the rod-by-rod check)")

    if ms is not None:
        if ms.get("nodes_need_hub"):
            warnings.append(f"{ms['nodes_need_hub']} node(s) exceed the valence limit or the weld-access angle "
                            f"and need a machined hub")
    try:
        lengths = _rod_lengths(res_dir)
    except (OSError, ValueError, KeyError, IndexError, TypeError) as e:
        problems.append(f"could not read the rod geometry: {e}")
        lengths = None
    if lengths is not None:
        lo, hi = pr.get("strut_len_min_mm"), pr.get("strut_len_max_mm")
        summary["rod_length_mm"] = {"min": float(lengths.min()), "max": float(lengths.max()),
                                    "limit_min": lo, "limit_max": hi}
        tol = req.length_tolerance_mm
        if lo is not None and (short := int((lengths < lo - tol).sum())):
            problems.append(f"{short} rod(s) shorter than the {lo:g} mm process minimum")
        if hi is not None and (long_ := int((lengths > hi + tol).sum())):
            if rc is not None:
                warnings.append(f"{long_} rod(s) longer than the {hi:g} mm bay (battens and diagonals, by the boom "
                                f"geometry); each is checked at its own length in the rod-by-rod check")
            else:
                warnings.append(f"{long_} rod(s) longer than the {hi:g} mm target maximum (lower capacity; "
                                f"check against the L/d rule)")

    bp = _read_json(res_dir / "buildplan.json", problems)
    if isinstance(bp, dict):
        summary["build"] = {k: bp.get(k) for k in ("sequence_placeable", "needs_fixture", "cross_module_rods",
                                                   "hours_single_robot", "machined_hubs")}
        if bp.get("needs_fixture"):
            warnings.append(f"{bp['needs_fixture']} rod(s) cannot be placed from the growing structure and "
                            f"need a fixture")

    artifacts = {"stages_log": str(out / "stages.log")}
    for name in ("process_rules.json", "boom_calibration.json", "arc1_frame.json", "micro_stats.json",
                 "rod_check.json", "buildplan.json", "arc1_lattice.stl"):
        if (res_dir / name).exists():
            artifacts[name] = str(res_dir / name)
    artifacts["params"] = str(work / "cad" / req.params)
    return {"summary": summary, "stages": stage_log, "problems": problems, "warnings": warnings,
            "artifacts": artifacts}
