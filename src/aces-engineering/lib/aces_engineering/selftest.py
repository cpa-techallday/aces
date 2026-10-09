"""aces-gendesign selftest: the aces-engineering stack on the CPU, offline; also the package's check().
Hard checks: imports; fea3d.self_test; a `simlab run` of examples/exp_beam_validation.yaml at 1 mm (local worker,
FP64) reproducing the workstation record to 1e-9, with an evidence bundle verified in a temporary root and an
md/html/docx report; 8 SIMP iterations of examples/cantilever3d.json at 2 mm with STL and slices; a build123d
STEP round trip to a voxel mask. Reported only: bracket voxel counts vs the workstation, rig-only and optional modules.
References (workstation DARKNESS 2026-10-05, untracked outputs in cudax-sim-runtime/out/): exp/beam-validation-v1/
c000/results.json delta_fea_mm 0.1989054912444315; bracket_b3d/check.json solid_b3d 26694, solid_analytic 26766."""
from __future__ import annotations

import argparse
import contextlib, importlib, io  # noqa: E401
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from . import LIB, SHARE, optional_status

BEAM_REF = 0.1989054912444315
BRACKET_REF = (26694, 26766)  # solid_b3d, solid_analytic; voxel_agreement there 0.9995
DESKTOP_MODULES = [
    "simlab.__main__", "simlab.spec", "simlab.registry", "simlab.run", "simlab.worker", "simlab.evidence",
    "simlab.evidence_bundle", "simlab.report", "simlab.tracking", "simlab.capacity", "simlab.fusion_export",
    "simlab.post", "simlab.meshing", "simlab.surrogate.cli", "simlab.surrogate.dataset", "simlab.surrogate.tabular",
    "simlab.surrogate.loop", "simlab.surrogate.fno", "simlab.solvers.gendesign_topopt",
    "simlab.solvers.gendesign_beam_check", "simlab.solvers.gendesign_parametric", "simlab.solvers.nozzle_angelino",
    "simlab.solvers.step_structures", "simlab.solvers.surrogate_predict", "simlab.solvers.pyfr_solver",
    "gendesign.fea3d", "gendesign.filters", "gendesign.topopt3d", "gendesign.export", "gendesign.evaluators",
    "gendesign.parametric", "gendesign.nozzle", "gendesign.fusion_bridge", "gendesign.progress",
    "gendesign.lattice_opt", "gendesign.warp_fea", "aces_engineering.simlab_cli", "aces_engineering.evidence_cli",
    "aces_engineering.mcp_client", "aces_engineering.gendesign_cli",
    "numpy", "scipy", "skimage.measure", "trimesh", "rtree", "optuna", "matplotlib", "yaml", "pydantic", "docx",
    "markdown", "sklearn.gaussian_process", "sklearn.ensemble", "joblib", "build123d", "embreex"]
RIG_ONLY_MODULES = ["simlab.leases", "simlab.queue", "simlab.mcp_server"]


def examples_dir() -> Path:
    cands = [os.environ.get("ACES_ENGINEERING_EXAMPLES"), SHARE / "examples", LIB.parent / "vendor" / "examples"]
    return next((Path(p) for p in cands if p and (Path(p) / "cantilever3d.json").is_file()), None) or sys.exit("no examples")


def _try_import(m: str) -> str:
    try:
        importlib.import_module(m)
        return "imports"
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def check_imports(res: dict) -> None:
    bad = {m: r for m in DESKTOP_MODULES if (r := _try_import(m)) != "imports"}
    res["imports"] = (not bad, f"{len(DESKTOP_MODULES) - len(bad)}/{len(DESKTOP_MODULES)} import" + (f"; FAILED {bad}" if bad else ""))
    res["rig-only modules (by design)"] = (None, "; ".join(f"{m}: {_try_import(m)}" for m in RIG_ONLY_MODULES))
    res["optional packages"] = (None, ", ".join(f"{k} {v}" for k, v in optional_status().items()))


def check_beam(res: dict, ex: Path, work: Path) -> None:
    import yaml
    import simlab.evidence_bundle as eb
    from simlab.report import build_report
    from simlab.run import run_experiment
    from .evidence_cli import verify

    spec = yaml.safe_load((ex / "exp_beam_validation.yaml").read_text(encoding="utf-8"))
    spec["id"], spec["sweep"] = "selftest-beam-validation", {"elem_mm": [1.0]}
    (work / "beam.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    exp_dir, t0 = work / "exp" / spec["id"], time.time()
    summary = run_experiment(str(work / "beam.yaml"), str(work / "exp"))
    row = summary["rows"][0]
    got = row.get("delta_fea_mm")
    rel = abs(got - BEAM_REF) / BEAM_REF if isinstance(got, float) else float("inf")
    res["beam"] = (summary["n_failed"] == 0 and rel <= 1e-9,
                   f"delta_fea {got!r} mm vs workstation {BEAM_REF!r}: rel diff {rel:.2e}; {row.get('pcg_iters')} PCG its "
                   f"(workstation 401), relres {row.get('pcg_relres', float('nan')):.2e}, {row.get('device')}, {time.time() - t0:.1f} s")
    eb.EVIDENCE_ROOT = str(work / "evidence")
    entry = eb.write_bundle(eb.build_bundle(str(exp_dir), operator="selftest", note="aces-engineering selftest"))
    with contextlib.redirect_stdout(io.StringIO()) as out:
        rc = verify(eb.EVIDENCE_ROOT)
    head = json.loads(out.getvalue()).get("head", "")
    res["evidence"] = (rc == 0 and head == entry["sha256"], f"bundle {entry['sha256'][:16]}..., chain head {head[:16]}..., verify rc={rc}")
    rep = build_report(str(exp_dir), ["md", "html", "docx"])
    ok = all(rep.get(k) and os.path.isfile(rep[k]) for k in ("md", "html", "docx"))
    res["report"] = (ok, ", ".join(f"{k} {os.path.getsize(rep[k])} B" for k in ("md", "html", "docx") if rep.get(k)))


def check_simp(res: dict, ex: Path, work: Path) -> None:
    from gendesign.topopt3d import run_case

    case = json.loads((ex / "cantilever3d.json").read_text(encoding="utf-8"))
    case.update({"elem_mm": 2.0, "rmin_mm": 4.0})
    t0 = time.time()
    rep = run_case(case, str(work / "simp"), dtype="float64", prefer_gpu=False, iters=8, quiet=True)
    vol, c, stl = rep["final"]["vol"], rep["final"]["compliance_Nmm"], rep["outputs"]["stl"]
    ok = abs(vol - case["volfrac"]) < 0.01 and c > 0 and rep["solves_unconverged"] == 0 and stl["faces"] > 0
    res["simp"] = (ok and bool(rep["outputs"]["slices"]), f"{rep['grid']['elements']} elements, {rep['iterations_run']} its, "
                   f"vol {vol:.4f}, compliance {c:.5g} N.mm, STL {stl['faces']} faces, {rep['device']}, {time.time() - t0:.1f} s")


def check_step(res: dict, ex: Path, work: Path) -> None:
    sys.path.insert(0, str(ex))
    sys.dont_write_bytecode = True  # the examples live in /usr/share: no __pycache__ there, even as root
    import bracket_build123d_check as bb
    from build123d import import_step
    from gendesign.evaluators import build123d_to_mask, build123d_to_step

    p, t0 = {"t_base": 6.0, "t_wall": 5.0, "gusset": 25.0, "gusset_t": 6.0}, time.time()
    part = bb.bracket_b3d(p)
    mask = build123d_to_mask(part, bb.ENVELOPE, 1.0)
    step = build123d_to_step(part, str(work / "bracket.step"))
    n_rt = int(build123d_to_mask(import_step(step), bb.ENVELOPE, 1.0).sum())
    analytic = bb.Bracket(1.0).build(p)
    n, n_an, agree = int(mask.sum()), int(analytic.sum()), float((mask == analytic).mean())
    res["step"] = (n_rt > 0, f"STEP {os.path.getsize(step)} B, re-imported mask {n_rt} voxels (direct {n}), {time.time() - t0:.1f} s")
    same = (n, n_an) == BRACKET_REF
    res["bracket vs workstation"] = (None, f"{'same' if same else 'DIFFERENT'}: solid_b3d {n} (ws 26694), analytic {n_an} "
                                           f"(ws 26766), agreement {agree:.4f} (ws 0.9995)")


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(prog="aces-gendesign selftest", description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=None, help="keep the outputs here (default: a temporary directory)")
    a = ap.parse_args(argv)
    os.environ.pop("MLFLOW_TRACKING_URI", None)  # offline: tracking stays a no-op
    (work := Path(a.out or tempfile.mkdtemp(prefix="aces-selftest-")).resolve()).mkdir(parents=True, exist_ok=True)
    from gendesign.fea3d import self_test

    check_imports(res := {})
    res["element"] = (bool(self_test(verbose=False)), "fea3d.self_test: KE symmetry, rigid-body modes, uniaxial patch test")
    ex = examples_dir()
    for name, fn in (("beam", check_beam), ("simp", check_simp), ("step", check_step)):
        try:
            fn(res, ex, work)
        except Exception as exc:
            res[name] = (False, f"{type(exc).__name__}: {exc}")
    if not a.out:
        shutil.rmtree(work, ignore_errors=True)
    for name, (ok, msg) in res.items():
        print(f"{'PASS' if ok else ('INFO' if ok is None else 'FAIL')}  {name:30s} {msg}")
    failed = [n for n, (ok, _) in res.items() if ok is False]
    print(f"aces-engineering selftest: {'FAILED ' + ', '.join(failed) if failed else 'all hard checks passed'}{f' (outputs in {work})' if a.out else ''}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
