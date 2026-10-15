"""rod.lattice: run a rod-lattice study (ARC-1) from an ACES spec, check it, record evidence.

The study scripts stay the engineering logic, and they are code, so the route only runs studies that are
committed under the repository's ``studies/`` folder with no local changes (audit H1).  ACES writes the study's
``cad/`` and ``scripts/`` exactly as committed (from git, not the working tree) into a new run folder, so the
original is never touched and every run names the commit it ran.  It runs the requested stages with the chosen
params file, each with a minimal environment and a time limit, then checks the outputs against the study's own
process limits:

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
import subprocess
import sys
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, field_validator

from ... import evidence, manufacturing, proc, safeio

DOMAIN = "rod.lattice"
DEFAULT_STAGES = ["process.py", "boom.py", "frame.py --dump", "micro.py --mesh", "rodcheck.py", "buildplan.py",
                  "budget.py"]
MAX_STAGES = 20
MAX_STAGE_TIMEOUT_S = 3600.0


class RodRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    process: str = Field(description="arc_lattice_rod_welding")
    study_dir: str = Field(description="study folder with cad/ and scripts/, relative to the spec; it must be "
                                       "committed under the repository's studies/ folder",
                           json_schema_extra={"aces_path": {}})
    params: str = Field("params.json", description="params file inside cad/")
    material_source: str = Field(min_length=3, description="where the params' material values come from")
    stages: list[str] = Field(default_factory=lambda: list(DEFAULT_STAGES), max_length=MAX_STAGES)
    max_utilisation: PositiveFloat = 1.0
    length_tolerance_mm: float = Field(0.5, ge=0.0, description="as micro.py: rods within this of a limit pass")
    stage_timeout_s: PositiveFloat = Field(1800, le=MAX_STAGE_TIMEOUT_S, description="per stage, seconds")

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


class StudyError(PermissionError):
    """The study is not one the route may run: outside studies/, not committed, or changed locally."""


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


# ----------------------------------------------------------------------------- the study, from git

@dataclass
class Study:
    path: Path          # the study folder in the working tree
    repo: Path          # the git checkout that holds it
    rel: str            # the study folder relative to the checkout (posix)
    commit: str         # the commit its files are taken from
    files: dict[str, str] = field(default_factory=dict)  # path inside the study -> blob id


def _git(args: list[str], repo: Path, **kw) -> subprocess.CompletedProcess:
    try:
        cp = evidence.git(args, repo, **kw)
    except (OSError, subprocess.SubprocessError) as e:
        raise StudyError(f"git could not check the study ({e})") from e
    if cp.returncode != 0:
        err = cp.stderr if isinstance(cp.stderr, str) else _text(cp.stderr)
        raise StudyError(f"git {args[0]} failed in {repo}: {err.strip()[:300]}")
    return cp


def check_study(req: RodRequirements, spec_dir: str | Path, *, out: str | Path | None = None,
                studies_root: str | Path | None = None) -> Study:
    """The study the spec names, if the route may run it (audit H1); StudyError otherwise.

    The study must sit under ``studies_root`` (default: the repository's ``studies/``), in a git checkout whose
    top folder holds that ``studies/``, with every file under its ``cad/`` and ``scripts/`` committed and no
    local change there (modified, staged or untracked).  ``studies_root`` exists so tests can use a temporary
    checkout; the CLI and the MCP server never set it.
    """
    root = Path(studies_root if studies_root is not None else evidence.REPO_ROOT / "studies").resolve()
    repo = root.parent
    study = (Path(spec_dir) / req.study_dir).resolve()
    if study == root or not study.is_relative_to(root):
        raise StudyError(f"study {study} is not under {root}; the rod route runs only studies committed there, "
                         f"because their scripts run as code")
    if out is not None:
        o = Path(out).resolve()
        if o.is_relative_to(study) or study.is_relative_to(o):
            raise FileExistsError(f"--out {o} overlaps the study {study}; write the run somewhere outside it")
    if not (repo / ".git").exists():
        raise StudyError(f"{repo} is not a git checkout, so the study can't be checked; run it from the ACES repo")
    rel = study.relative_to(repo).as_posix()
    commit = _git(["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], repo).stdout.strip()
    paths = [f"{rel}/cad", f"{rel}/scripts"]
    status = _git(["status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=all", "--",
                   *paths], repo).stdout
    changed = [e[3:] for e in status.split("\0") if len(e) > 3]
    if changed:
        more = f" and {len(changed) - 5} more" if len(changed) > 5 else ""
        raise StudyError(f"study {rel} has local changes ({', '.join(changed[:5])}{more}); commit or discard them "
                         f"first: the route runs only committed study code")
    tree = _git(["ls-tree", "-r", "-z", "--full-tree", commit, "--", *paths], repo).stdout
    files: dict[str, str] = {}
    for entry in tree.split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        mode, kind, blob = meta.split()
        inner = PurePosixPath(path).relative_to(rel)
        if "__pycache__" in inner.parts:
            continue
        if kind != "blob" or mode not in ("100644", "100755"):
            raise StudyError(f"study {rel}: {inner} is a {'link' if mode == '120000' else kind} in git; the route "
                             f"copies regular files only")
        files[inner.as_posix()] = blob
    if f"cad/{req.params}" not in files or not any(k.startswith("scripts/") for k in files):
        raise FileNotFoundError(f"{study} must contain cad/{req.params} and scripts/ (committed)")
    for stage in req.stages:
        if f"scripts/{stage.split()[0]}" not in files:
            raise FileNotFoundError(f"stage script {stage.split()[0]!r} is not in {study / 'scripts'} (committed)")
    return Study(study, repo, rel, commit, files)


def _blobs(study: Study) -> dict[str, bytes]:
    """The study's committed file contents, read raw from git (no filters, no working tree)."""
    ids = list(dict.fromkeys(study.files.values()))
    data = _git(["cat-file", "--batch"], study.repo, text=False, input=("\n".join(ids) + "\n").encode()).stdout
    out: dict[str, bytes] = {}
    pos = 0
    for blob in ids:
        nl = data.index(b"\n", pos)
        head = data[pos:nl].split()
        if len(head) != 3 or head[0].decode() != blob or head[1] != b"blob":
            raise StudyError(f"git cat-file returned an unexpected entry for {blob}")
        size = int(head[2])
        out[blob] = data[nl + 1:nl + 1 + size]
        pos = nl + 1 + size + 1
    return out


def _write_study(study: Study, work: safeio.OutDir) -> dict[str, bytes]:
    """Write the committed files into ``work``; returns path -> contents."""
    contents = _blobs(study)
    dirs: dict[tuple[str, ...], safeio.OutDir] = {(): work}
    with ExitStack() as stack:
        for path, blob in sorted(study.files.items()):
            *parents, name = PurePosixPath(path).parts
            for i in range(len(parents)):
                key = tuple(parents[:i + 1])
                if key not in dirs:
                    dirs[key] = stack.enter_context(dirs[key[:-1]].subdir(key[-1]))
            dirs[tuple(parents)].write_bytes(name, contents[blob])
    return {path: contents[blob] for path, blob in study.files.items()}


# ----------------------------------------------------------------------------- the run

def run(req: RodRequirements, out_dir: str | Path | safeio.OutDir, spec_dir: str | Path, log=print, *,
        studies_root: str | Path | None = None) -> dict[str, Any]:
    """Run the study's stages in a new run folder and check the results.

    ``out_dir`` is a new folder (a path, or a :class:`aces.safeio.OutDir` the caller opened with ``new=True``);
    every file the route writes goes through it.
    """
    out_path = out_dir.path if isinstance(out_dir, safeio.OutDir) else Path(out_dir)
    study = check_study(req, spec_dir, out=out_path, studies_root=studies_root)
    with ExitStack() as stack:
        out = out_dir if isinstance(out_dir, safeio.OutDir) else stack.enter_context(safeio.OutDir(out_dir, new=True))
        work_dir = stack.enter_context(out.subdir("work", new=True))
        files = _write_study(study, work_dir)
        work = work_dir.path
        P = json.loads(files[f"cad/{req.params}"])
        res_dir = work / P.get("output", {}).get("dir", "out")
        if not res_dir.resolve().is_relative_to(work.resolve()):
            raise StudyError(f"cad/{req.params} puts its output outside the run folder ({res_dir})")

        env = proc.minimal_env({"ARC1_PARAMS": req.params, "MPLBACKEND": "Agg"})
        stage_log: list[dict] = []
        problems: list[str] = []
        with out.open_append("stages.log") as fh:
            for stage in req.stages:
                script, *args = stage.split()
                log(f"[rod] {stage}")
                try:
                    r = proc.run([sys.executable, str(work / "scripts" / script), *args], cwd=work, env=env,
                                 timeout=req.stage_timeout_s)
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
        out = out.path

    pr = P.get("process", {})
    summary: dict[str, Any] = {"params_rev": P.get("rev"), "process": pr.get("name"),
                               "study": {"path": study.rel, "commit": study.commit}}
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
