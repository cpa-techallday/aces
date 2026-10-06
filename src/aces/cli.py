"""ACES command line.

  aces check SPEC.yaml                      validate a spec; list what is missing
  aces design SPEC.yaml --out DIR           search, report, evidence record
  aces sample --tpms gyroid --cell-m ...    STL of a periodic TPMS block
  aces export-simlab DIR --rank 0 ...       geometry + simlab experiment for verification
  aces ledger verify PATH                   check the evidence hash chain
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from . import evidence, search, simlab, spec
from .domains import get as get_model
from .domains.hx import tpms
from .report import hx_report

DEFAULT_LEDGER = Path("out/evidence/ledger.jsonl")


def _load(path: str):
    data = yaml.safe_load(Path(path).read_text()) or {}
    model_cls = get_model(data.get("domain", ""))
    env, req = spec.parse(data, model_cls.requirements_model)
    return data, env, req, model_cls


def cmd_check(args) -> int:
    try:
        _, env, _, model_cls = _load(args.spec)
    except (spec.SpecError, KeyError, ValueError) as e:
        print(e)
        return 2
    print(f"OK: {env.domain} spec for project {env.project!r} is complete ({model_cls.id} {model_cls.version}).")
    return 0


def cmd_design(args) -> int:
    try:
        data, env, req, model_cls = _load(args.spec)
    except (spec.SpecError, KeyError, FileNotFoundError, ValueError) as e:
        print(e)
        return 2
    model = model_cls()
    try:
        model.registry(req)  # fail early on a missing or unsourced calibration file
    except (FileNotFoundError, ValueError) as e:
        print(e)
        return 2
    cfg = env.search
    trials = args.trials or cfg.trials
    res = search.run(model, req, trials=trials, seed=cfg.seed, population=cfg.population, bounds=cfg.bounds)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    canonical_spec = env.model_dump(mode="json")
    pareto = sorted(res.pareto, key=lambda e: e.objectives["mass_kg"])
    (out / "pareto.json").write_text(json.dumps([e.as_dict() for e in pareto], indent=2, default=str))
    with (out / "evaluations.jsonl").open("w") as fh:
        for e in res.evaluations:
            fh.write(json.dumps(e.as_dict(), default=str) + "\n")
    calib = model.registry(req).as_dict()
    rec = evidence.make_record(
        kind="aces.design_run",
        spec=canonical_spec,
        model_id=model.id,
        model_version=model.version,
        inputs={"trials": trials, "seed": cfg.seed, "population": cfg.population, "bounds": cfg.bounds,
                "resolution": model.resolution},
        observations={
            "evaluated": len(res.distinct),
            "feasible": len(res.feasible),
            "pareto": [{"params": e.params, "objectives": e.objectives} for e in pareto],
        },
        calibration=calib,
        artifacts={"pareto": out / "pareto.json", "evaluations": out / "evaluations.jsonl"},
    )
    ledger = Path(args.ledger)
    written = evidence.Ledger(ledger).append(rec)
    # The run manifest ties this directory to its own evidence record (not the ledger tail).
    (out / "run.json").write_text(json.dumps({
        "record_hash": written["record_hash"], "spec_hash": written["spec_hash"],
        "ledger": str(ledger.resolve()), "model": written["model"]}, indent=2))
    report = hx_report(canonical_spec, res, written["record_hash"], model.id, model.version)
    (out / "report.md").write_text(report)
    print(f"{len(res.distinct)} distinct designs evaluated, {len(res.feasible)} feasible, {len(pareto)} non-dominated.")
    print(f"Report: {out / 'report.md'}\nEvidence: {ledger} ({written['record_hash'][:12]})")
    return 0 if pareto else 1


def cmd_sample(args) -> int:
    verts, faces = tpms.sample_mesh(args.tpms, args.cell_m, args.wall_ratio * args.cell_m, args.cells, args.region, args.resolution)
    scale = 1000.0 if args.units == "mm" else 1.0
    p = tpms.write_stl(args.out, verts, faces, f"ACES {args.tpms} {args.region} a={args.cell_m} t/a={args.wall_ratio} units={args.units}",
                       scale=scale)
    print(f"{p}: {len(faces)} triangles, enclosed volume {tpms.mesh_volume(verts, faces):.4e} m3")
    return 0


def cmd_export_simlab(args) -> int:
    run_dir = Path(args.run)
    manifest_path = run_dir / "run.json"
    if not manifest_path.exists():
        print(f"{manifest_path} not found; export works on the output directory of `aces design`.")
        return 2
    manifest = json.loads(manifest_path.read_text())
    ledger = evidence.Ledger(args.ledger or manifest["ledger"])
    rec = ledger.find(manifest["record_hash"])
    if rec is None:
        print(f"Evidence record {manifest['record_hash'][:12]} not found in {ledger.path}; refusing to export.")
        return 2
    pareto_path = run_dir / "pareto.json"
    if evidence.file_sha256(pareto_path) != rec["artifacts"]["pareto"]["sha256"]:
        print(f"{pareto_path} no longer matches its evidence record (edited after the run); refusing to export.")
        return 2
    pareto = json.loads(pareto_path.read_text())
    if not pareto:
        print("No feasible designs in this run; nothing to verify.")
        return 1
    if not 0 <= args.rank < len(pareto):
        print(f"--rank must be 0..{len(pareto) - 1}")
        return 2
    d = pareto[args.rank]
    p = d["params"]
    run_spec = rec["spec"]
    scale = 1000.0 if args.units == "mm" else 1.0
    stl = run_dir / f"rank{args.rank}_{p['tpms']}_{args.region}_{args.units}.stl"
    verts, faces = tpms.sample_mesh(p["tpms"], p["cell_m"], p["wall_ratio"] * p["cell_m"], args.cells, args.region)
    tpms.write_stl(stl, verts, faces, f"ACES {p['tpms']} {args.region} units={args.units}", scale=scale)
    exp = simlab.experiment(
        exp_id=f"aces-{run_spec['project']}-{rec['record_hash'][:8]}-rank{args.rank}-{args.region}",
        project=run_spec["project"],
        owner=run_spec["owner"],
        rd_activity=run_spec["rd_activity"],
        physics=args.physics,
        solver=args.solver,
        description=(f"ACES verification of rank-{args.rank} {p['tpms']} core: periodic {args.cells}^3-cell "
                     f"{args.region} sample, a={p['cell_m']:.5f} m, t/a={p['wall_ratio']:.4f}."),
        hypothesis=("Fast-tier pressure drop and heat transfer for this core are within the calibration "
                    "tolerance of the solver result."),
        expected="Per-cell pressure gradient and Nusselt number to fit the hx calibration factors.",
        geometry_ref=str(stl),
        geometry_units=args.units,
        params={"aces_design": p, "aces_metrics": {k: v for k, v in d["metrics"].items() if k in ("hot", "cold")}},
        outputs=["pressure_gradient_Pa_m", "nusselt", "mass_flow_kg_s"],
        vram_gb=args.vram_gb,
        system="hx",
        aces_record=rec["record_hash"],
    )
    path = simlab.write(exp, run_dir / f"simlab_rank{args.rank}_{args.region}.yaml")
    print(f"Geometry: {stl}\nExperiment: {path}\nAdd the solver adapter's own params, then run `simlab validate` before submitting.")
    return 0


def cmd_ledger(args) -> int:
    ok, msg = evidence.Ledger(args.path).verify()
    print(("OK: " if ok else "BROKEN: ") + msg)
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="aces", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("check", help="validate a spec")
    s.add_argument("spec")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("design", help="run a design search")
    s.add_argument("spec")
    s.add_argument("--out", required=True)
    s.add_argument("--trials", type=int)
    s.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    s.set_defaults(fn=cmd_design)

    s = sub.add_parser("sample", help="write a TPMS block as STL")
    s.add_argument("--tpms", choices=tpms.KINDS, required=True)
    s.add_argument("--cell-m", type=float, required=True)
    s.add_argument("--wall-ratio", type=float, required=True)
    s.add_argument("--cells", type=int, default=3)
    s.add_argument("--region", choices=("solid", "fluid_a", "fluid_b"), default="solid")
    s.add_argument("--resolution", type=int, default=32)
    s.add_argument("--units", choices=("m", "mm"), default="mm", help="STL coordinate units")
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_sample)

    s = sub.add_parser("export-simlab", help="geometry + simlab experiment for one shortlisted design")
    s.add_argument("run", help="output directory of `aces design`")
    s.add_argument("--rank", type=int, default=0)
    s.add_argument("--region", choices=("fluid_a", "fluid_b", "solid"), default="fluid_a")
    s.add_argument("--cells", type=int, default=3)
    s.add_argument("--solver", required=True, help="registered simlab solver name")
    s.add_argument("--physics", default="cfd")
    s.add_argument("--vram-gb", type=float, required=True)
    s.add_argument("--units", choices=("m", "mm"), default="m", help="STL coordinate units (recorded in the experiment)")
    s.add_argument("--ledger", help="override the ledger path recorded in run.json")
    s.set_defaults(fn=cmd_export_simlab)

    s = sub.add_parser("ledger", help="evidence ledger tools")
    s.add_argument("action", choices=("verify",))
    s.add_argument("path", nargs="?", default=str(DEFAULT_LEDGER))
    s.set_defaults(fn=cmd_ledger)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
