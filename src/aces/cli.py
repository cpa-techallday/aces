"""ACES command line.

  aces check SPEC.yaml                      validate a spec; list what is missing
  aces design SPEC.yaml --out DIR           search, report, evidence record
  aces sample --tpms gyroid --cell-m ...    STL of a periodic TPMS block
  aces export-simlab DIR --rank 0 ...       geometry + simlab experiment for verification
  aces form SPEC.yaml --out DIR             formgen lattice route (robot cell / printed formwork)
  aces rod SPEC.yaml --out DIR              rod-lattice route (ARC-1 process: placed rods, welded nodes)
  aces calibrate MEAS.csv --registry R.json  fit hx factors from CFD/test measurements
  aces cfd-sample --tpms gyroid ...         one periodic CFD calibration sample (lattice Boltzmann, FP64)
  aces cfd-campaign --out EXP.yaml ...      simlab experiment: a sweep of calibration samples for the rig
  aces cfd-collect EXP_DIR --out M.json     measurement rows from a finished simlab campaign
  aces processes                            manufacturing routes and what ACES checks for each
  aces ledger verify PATH                   check the evidence hash chain
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import yaml

from . import evidence, manufacturing, search, simlab, spec
from .domains import get as get_model
from .domains.form import route as form_route
from .domains.rod import route as rod_route
from .domains.hx import tpms
from .report import hx_report

DEFAULT_LEDGER = Path("out/evidence/ledger.jsonl")


def _load(path: str):
    data = yaml.safe_load(Path(path).read_text()) or {}
    model_cls = get_model(data.get("domain", ""))
    env, req = spec.parse(data, model_cls.requirements_model)
    # like every other path in a spec, the calibration file is relative to the spec's folder
    cf = getattr(req, "calibration_file", None)
    if cf and not Path(cf).is_absolute():
        req = req.model_copy(update={"calibration_file": str((Path(path).parent / cf).resolve())})
    return data, env, req, model_cls


def _fresh_out(out: str | Path) -> str | None:
    """Reason the output folder cannot take a new run, or None.

    A folder holding run.json belongs to an earlier run and its own evidence record; writing a new run over
    it would leave report.md, run.json and the ledger pointing at different results.
    """
    o = Path(out)
    if o.is_symlink():
        return f"{o} is a symbolic link; give a real folder for --out"
    if (o / "run.json").exists() or (o / "run.json").is_symlink():
        return f"{o} already holds a finished run (run.json); choose a new --out folder"
    for name in RUN_FILES:
        if (o / name).is_symlink():
            return f"{o / name} is a symbolic link; choose a new --out folder"
    return None


RUN_FILES = ("run.json", "report.md", "pareto.json", "evaluations.jsonl", "stages.log", "structure.json")


def _safe_write(path: Path, text: str) -> None:
    """Write a run file atomically, never through a link planted at the target.

    The text goes to a temporary file in the same folder, then replaces the target's directory entry, so a
    symbolic or hard link at the target is replaced rather than followed.
    """
    if path.is_symlink():
        raise PermissionError(f"refusing to write through the symbolic link {path}")
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def cmd_check(args) -> int:
    try:
        data = yaml.safe_load(Path(args.spec).read_text()) or {}
        if data.get("domain") == form_route.DOMAIN:
            env, _ = spec.parse(data, form_route.FormRequirements)
            print(f"OK: {env.domain} spec for project {env.project!r} is complete (formgen route).")
            return 0
        if data.get("domain") == rod_route.DOMAIN:
            env, _ = spec.parse(data, rod_route.RodRequirements)
            print(f"OK: {env.domain} spec for project {env.project!r} is complete (rod-lattice route).")
            return 0
        _, env, _, model_cls = _load(args.spec)
    except (spec.SpecError, KeyError, ValueError) as e:
        print(e)
        return 2
    print(f"OK: {env.domain} spec for project {env.project!r} is complete ({model_cls.id} {model_cls.version}).")
    return 0


def _rod_report(spec_d: dict, res: dict, record_hash: str, req) -> str:
    sm = res["summary"]
    fr, mi, rl, bd = sm.get("frame", {}), sm.get("micro", {}), sm.get("rod_length_mm", {}), sm.get("build", {})
    rc = sm.get("rod_check", {})
    lines = [
        f"# ACES rod-lattice run: {spec_d['project']}",
        "",
        f"- Route: `{req.process}` ({sm.get('process')}), params `{req.params}` rev {sm.get('params_rev')}",
        f"- Company: {spec_d['company']} - R&D activity: {spec_d['rd_activity']}",
        f"- Hypothesis: {spec_d['hypothesis']}",
        f"- Material values from: {req.material_source}",
        f"- Evidence record: `{record_hash}`",
        "",
        "| Quantity | Value |",
        "| --- | --- |",
    ]
    rows = [
        ("Stages run", ", ".join(f"{st['stage']} ({'ok' if st['exit'] == 0 else 'FAILED'})" for st in res["stages"])),
        ("Macro frame nodes / members", f"{fr.get('nodes')} / {fr.get('members')}" if fr else None),
        ("Frame FEA max utilisation (nominal 60 mm rods)",
         f"{fr.get('max_utilisation')} ({fr.get('over_unity')} over 1.0)" if fr else None),
        ("Rod-by-rod max utilisation (governs)",
         f"{rc.get('max_utilisation')} ({rc.get('over_unity')} of {rc.get('rods')} rods over 1.0)" if rc else None),
        ("Torsional stiffness", f"{fr.get('torsion_Nm_per_deg')} Nm/deg" if fr else None),
        ("Rods / nodes", f"{mi.get('rods')} / {mi.get('nodes')}" if mi else None),
        ("Rod length", f"{rl.get('min'):.1f}-{rl.get('max'):.1f} mm (limits {rl.get('limit_min')}-{rl.get('limit_max')})" if rl else None),
        ("Structure mass", f"{mi.get('mass_total_kg')} kg" if mi else None),
        ("Robot-weldable nodes / machined hubs", f"{mi.get('nodes_weldable')} / {mi.get('nodes_need_hub')}" if mi else None),
        ("Single-robot build time", f"{bd.get('hours_single_robot')} h" if bd else None),
    ]
    lines += [f"| {k} | {v} |" for k, v in rows if v is not None]
    lines.append("")
    if res["problems"]:
        lines += ["## Problems (not buildable as designed)", ""] + [f"- {p}" for p in res["problems"]] + [""]
    if res["warnings"]:
        lines += ["## Warnings", ""] + [f"- {w}" for w in res["warnings"]] + [""]
    lines += ["## Not checked", "",
              "Fatigue of welded nodes, measured joint efficiency, and weld-induced distortion. These need coupon "
              "tests before a build (study section 11). The rod-by-rod check combines each member's extreme axial "
              "force, moment, shear and torque as if they coincide, and checks lacing in compression either way; "
              "it does not model joint flexibility or local buckling of a node ball."]
    return "\n".join(lines) + "\n"


def cmd_rod(args) -> int:
    spec_path = Path(args.spec)
    data = yaml.safe_load(spec_path.read_text()) or {}
    try:
        if data.get("domain") != rod_route.DOMAIN:
            raise ValueError(f"`aces rod` needs domain: {rod_route.DOMAIN}")
        env, req = spec.parse(data, rod_route.RodRequirements)
    except (spec.SpecError, ValueError) as e:
        print(e)
        return 2
    if why := _fresh_out(args.out):
        print(why)
        return 2
    try:
        res = rod_route.run(req, args.out, spec_path.parent)
    except (FileNotFoundError, FileExistsError) as e:
        print(e)
        return 2
    canonical = env.model_dump(mode="json")
    rec = evidence.make_record(
        kind="aces.rod_run", spec=canonical, model_id=rod_route.DOMAIN, model_version="study",
        inputs={"requirements_resolved": req.model_dump(mode="json")},
        observations={"summary": res["summary"], "stages": res["stages"], "problems": res["problems"],
                      "warnings": res["warnings"]},
        calibration={}, artifacts=res["artifacts"],
    )
    ledger = Path(args.ledger)
    written = evidence.Ledger(ledger).append(rec)
    out = Path(args.out)
    _safe_write(out / "run.json", json.dumps({"record_hash": written["record_hash"], "spec_hash": written["spec_hash"],
                                              "ledger": str(ledger.resolve()), "model": written["model"]}, indent=2))
    _safe_write(out / "report.md", _rod_report(canonical, res, written["record_hash"], req))
    print(f"Report: {out / 'report.md'}\nEvidence: {ledger} ({written['record_hash'][:12]})")
    for p in res["problems"]:
        print(f"PROBLEM: {p}")
    return 1 if res["problems"] else 0


def cmd_calibrate(args) -> int:
    from .domains.hx import calibrate as hxcal

    try:
        meas = hxcal.load(args.measurements)
        factors, summary, refused = hxcal.fit(meas, boundary=args.boundary, min_points=args.min_points,
                                              max_scatter=args.max_scatter, resolution=args.resolution)
    except (ValueError, FileNotFoundError) as e:
        print(e)
        return 2
    for row in summary:
        val = f"{row['value']:.4f}" if "value" in row else "-"
        if row.get("form") == "power":
            val += f" x (Re/{row['re_ref']:.3g})^{row['re_exponent']:.3f}"
        elif row.get("form") == "onset":
            val += f" x (1 + (Re/{row['re_ref']:.3g})^{row['re_exponent']:.2f})"
        key = f"scatter_sd_log_{row['form']}" if row.get("form") in ("power", "onset") else "scatter_sd_log"
        sc = f"{row[key]:.3f}" if key in row else "-"
        print(f"{row['key']:<26} points {row['points']:>3}  conditions {row['conditions']:>3}  value {val:>8}  "
              f"scatter {sc:>6}  {row['status']}")
        for x in row.get("excluded", []):
            print(f"  EXCLUDED: {x['source']} at Re {x['reynolds']:.0f} ({x['regime']}: baseline not valid there)")
    for r in refused:
        print(f"REFUSED: {r}")
    if not factors:
        print("No factors written.")
        return 1
    reg = hxcal.update_registry(args.registry, factors)
    rec = evidence.make_record(
        kind="aces.calibration",
        spec={"company": spec.COMPANY, "project": args.project, "owner": args.owner,
              "rd_activity": args.rd_activity, "hypothesis": "Fast-tier hx correlations can be calibrated to "
              "the measurements within the stated scatter."},
        model_id="hx.two_fluid", model_version="calibration",
        inputs={"measurements": str(args.measurements), "boundary": args.boundary, "min_points": args.min_points,
                "resolution": args.resolution,
                "max_scatter": args.max_scatter},
        observations={"summary": summary, "refused": refused},
        calibration=reg.as_dict(),
        artifacts={"measurements": args.measurements, "registry": args.registry},
    )
    written = evidence.Ledger(args.ledger).append(rec)
    print(f"Registry: {args.registry} ({len(factors)} factor(s))\nEvidence: {args.ledger} ({written['record_hash'][:12]})")
    return 0 if not refused else 1


def cmd_processes(args) -> int:
    for p in manufacturing.PROCESSES.values():
        print(f"{p.id:<26} {p.status:<10} {p.name}")
        if p.domains:
            print(f"{'':<26} domains: {', '.join(p.domains)}")
        if p.checks:
            print(f"{'':<26} checks:  {'; '.join(p.checks)}")
        if p.notes:
            print(f"{'':<26} note:    {p.notes}")
    return 0


def _form_report(spec_d: dict, res: dict, record_hash: str, req=None) -> str:
    r = res["report"]
    st, est = r.get("stats", {}), r.get("estimate", {})
    seq = r.get("sequence", {})
    lines = [
        f"# ACES form run: {spec_d['project']}",
        "",
        f"- Route: `{spec_d['requirements']['process']}` via formgen ({spec_d['requirements']['mode']} mode)",
        f"- Company: {spec_d['company']} - R&D activity: {spec_d['rd_activity']}",
        f"- Hypothesis: {spec_d['hypothesis']}",
        f"- Evidence record: `{record_hash}`",
        "",
        "| Quantity | Value |",
        "| --- | --- |",
        f"| Nodes / struts | {st.get('nodes')} / {st.get('struts')} |",
        f"| Total strut length | {float(st.get('total_length_m') or 0):.1f} m |",
        f"| Printable steps / unreachable struts | {seq.get('printable_steps')} / {seq.get('unreachable_struts')} |",
        f"| Material (density from: {spec_d['requirements']['material']['source']}) | {est.get('material_mass_kg')} kg |",
        f"| Print time (at the spec's speed) | {est.get('total_time_h')} h |",
        "",
    ]
    if res["problems"]:
        lines += ["## Problems (route not buildable as generated)", ""] + [f"- {p}" for p in res["problems"]] + [""]
    if res["warnings"]:
        lines += ["## Warnings from formgen", ""] + [f"- {w}" for w in res["warnings"]] + [""]
    stc = res.get("structure")
    if stc:
        sreq = req.structure.model_dump() if req is not None else spec_d["requirements"]["structure"]
        lines += [
            "## Structural check (linear static, Pynite)",
            "",
            f"Material: {sreq['material']['name']} (E {sreq['material']['E_MPa']:g} MPa, strength "
            f"{sreq['material']['strength_MPa']:g} MPa; source: {sreq['material']['source']}), "
            f"material factor {sreq['material_factor']:g}, buckling K {sreq['effective_length_factor']:g}.",
            "",
            "| Load case | Max utilisation | Max deflection mm |",
            "| --- | --- | --- |",
        ] + [f"| {k} | {v['max_utilisation']:.3f} | {v['max_deflection_mm']:.2f} |" for k, v in stc["per_case"].items()] + [
            "",
            f"Governing: strut {stc['governing'].get('strut')} in `{stc['governing'].get('case')}`, "
            f"utilisation {stc['max_utilisation']:.3f}; {stc['over_unity']} strut-case(s) over 1.0. "
            f"Supports: {stc['support_nodes']} nodes.",
            "",
        ] + [f"- {n}" for n in stc["notes"]] + [""]
        nc = list(stc.get("not_checked") or ())
        if nc:
            lines += ["Not checked by this analysis: " + "; ".join(nc) + ".", ""]
        structural = ("Linear static only: no fatigue, creep, temperature or joint-strength checks. "
                      "Bead properties must come from tests on printed coupons.")
    else:
        structural = "No structural check was requested for this lattice (add a `structure` block to the spec)."
    lines += [
        "## Before running the cell",
        "",
        "JBI jobs are stubs. Set the TOOL file and user frame on the pendant and verify every job in "
        "MotoSim EG VRC before the robot moves. " + structural,
    ]
    return "\n".join(lines) + "\n"


def cmd_form(args) -> int:
    spec_path = Path(args.spec)
    data = yaml.safe_load(spec_path.read_text()) or {}
    try:
        if data.get("domain") != form_route.DOMAIN:
            raise ValueError(f"`aces form` needs domain: {form_route.DOMAIN}")
        env, req = spec.parse(data, form_route.FormRequirements)
    except (spec.SpecError, ValueError) as e:
        print(e)
        return 2
    if why := _fresh_out(args.out):
        print(why)
        return 2
    try:
        res = form_route.run(req, args.out, spec_path.parent)
    except ImportError as e:
        print(f"formgen dependencies missing ({e}); install with: pip install -e .[formgen]")
        return 2
    canonical = env.model_dump(mode="json")
    rec = evidence.make_record(
        kind="aces.form_run",
        spec=canonical,
        model_id=form_route.DOMAIN,
        model_version=__import__("formgen").__version__,
        inputs={"source": req.source, "options": res["report"].get("options", {}),
                "requirements_resolved": req.model_dump(mode="json")},
        observations={"stats": res["report"].get("stats"), "sequence": res["report"].get("sequence"),
                      "structure": res.get("structure"),
                      "estimate": res["report"].get("estimate"), "problems": res["problems"],
                      "warnings": res["warnings"]},
        calibration={},
        artifacts=res["artifacts"],
    )
    ledger = Path(args.ledger)
    written = evidence.Ledger(ledger).append(rec)
    out = Path(args.out)
    _safe_write(out / "run.json", json.dumps({"record_hash": written["record_hash"], "spec_hash": written["spec_hash"],
                                              "ledger": str(ledger.resolve()), "model": written["model"]}, indent=2))
    _safe_write(out / "report.md", _form_report(canonical, res, written["record_hash"], req))
    print(f"Report: {out / 'report.md'}\nEvidence: {ledger} ({written['record_hash'][:12]})")
    for p in res["problems"]:
        print(f"PROBLEM: {p}")
    return 1 if res["problems"] else 0


def cmd_design(args) -> int:
    try:
        data, env, req, model_cls = _load(args.spec)
    except (spec.SpecError, KeyError, FileNotFoundError, ValueError) as e:
        print(e)
        return 2
    if why := _fresh_out(args.out):
        print(why)
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
    _safe_write(out / "pareto.json", json.dumps([e.as_dict() for e in pareto], indent=2, default=str))
    _safe_write(out / "evaluations.jsonl", "".join(json.dumps(e.as_dict(), default=str) + "\n"
                                                   for e in res.evaluations))
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
    _safe_write(out / "run.json", json.dumps({
        "record_hash": written["record_hash"], "spec_hash": written["spec_hash"],
        "ledger": str(ledger.resolve()), "model": written["model"]}, indent=2))
    report = hx_report(canonical_spec, res, written["record_hash"], model.id, model.version)
    _safe_write(out / "report.md", report)
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
    for q in (manifest_path, run_dir / "pareto.json"):
        if q.is_symlink():
            print(f"{q} is a symbolic link; refusing to export from it.")
            return 2
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
    exp_path = run_dir / f"simlab_rank{args.rank}_{args.region}.yaml"
    for q in (stl, exp_path):
        if q.is_symlink():
            print(f"{q} is a symbolic link; refusing to write through it.")
            return 2
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
    path = simlab.write(exp, exp_path)
    print(f"Geometry: {stl}\nExperiment: {path}\nAdd the solver adapter's own params, then run `simlab validate` before submitting.")
    return 0


def cmd_ledger(args) -> int:
    ok, msg = evidence.Ledger(args.path).verify()
    print(("OK: " if ok else "BROKEN: ") + msg)
    return 0 if ok else 1



# ----------------------------------------------------------------------------- calibration CFD

def cmd_cfd_sample(args) -> int:
    from .cfd import lbm, periodic

    out = Path(args.out)
    if out.exists() or out.is_symlink():
        print(f"{out} exists; choose a new file")
        return 2
    s = periodic.Sample(tpms=args.tpms, wall_ratio=args.wall_ratio, cell_m=args.cell_m, fluid=args.fluid,
                        T_K=args.T, p_Pa=args.p, re_target=args.re, n=args.n, thermal=not args.no_thermal,
                        source=args.source)
    try:
        r = periodic.run(s, xp=lbm.backend(prefer_gpu=args.gpu), log=print)
    except ValueError as e:
        print(e)
        return 2
    _safe_write(out, json.dumps(periodic.as_dict(r), indent=2, default=float))
    d = r.diagnostics
    print(f"Re {d.get('re_achieved', float('nan')):.4g}  Mach {d.get('mach', float('nan')):.3f}  "
          f"Nu_H1 {d.get('nusselt_h1', float('nan')):.4g}  ({d.get('wall_s')} s)")
    for p in r.problems:
        print(f"NOT A MEASUREMENT: {p}")
    for n in r.notes:
        print(f"NOTE: {n}")
    print(f"Wrote {out}")
    return 0 if r.measurement is not None else 1


def cfd_vram_gb(n: int) -> float:
    """Device memory for one sample, from the measured peak of solve_flow plus the warm-start copy
    (about 330 FP64 values per node), with a 1.5x margin and 0.5 GB for the CuPy pool."""
    return round(1.5 * n ** 3 * 330 * 8 / 1e9 + 0.5, 1)


def cmd_cfd_campaign(args) -> int:
    out = Path(args.out)
    if out.exists() or out.is_symlink():
        print(f"{out} exists; choose a new file")
        return 2
    if args.validation:
        params = {"n": args.n}
        sweep = {"validate": ["square_duct", "plane_channel"]}
        desc = "Registry validation of aces-tpms-periodic against exact fully developed duct results."
        hyp = "The periodic LBM solver reproduces exact laminar fRe and Nu_H1 within 1 % at this resolution."
        expected = "square duct fRe 56.91 / Nu 3.608; plane channel fRe 96 / Nu 8.235"
        outputs = ["fRe_err_pct", "Nu_err_pct"]
    else:
        for k in args.tpms:
            if k not in tpms.KINDS:
                print(f"unknown TPMS {k!r}")
                return 2
        params = {"cell_m": args.cell_m, "fluid": args.fluid, "T_K": args.T, "p_Pa": args.p, "n": args.n,
                  "thermal": not args.no_thermal}
        sweep = {"tpms": list(args.tpms), "wall_ratio": list(args.wall_ratio), "re": list(args.re)}
        desc = (f"ACES calibration samples: periodic {', '.join(args.tpms)} unit cells, t/a {args.wall_ratio}, "
                f"Re {args.re}, {args.fluid} at {args.T:g} K, {args.n} voxels per cell.")
        hyp = ("Pressure drop and H1 heat transfer of TPMS cores differ from the straight-duct baselines by a factor "
               "that is constant enough over this range to calibrate the fast tier.")
        expected = "dpdx and h per case; fitted with `aces calibrate --boundary uniform_heat_flux`."
        outputs = ["re_achieved", "dpdx_Pa_m", "mass_flux_kg_m2s", "h_W_m2K", "nusselt_h1", "mach"]
    exp = simlab.experiment(
        exp_id=args.id, project=args.project, owner=args.owner, rd_activity=args.rd_activity, physics="cfd",
        solver="aces-tpms-periodic", description=desc, hypothesis=hyp, expected=expected, geometry_ref=None,
        params=params, outputs=outputs, vram_gb=cfd_vram_gb(args.n), sweep=sweep, system="hx",
        kind="validation" if args.validation else "calibration")
    exp["geometry"] = {"source": "parametric"}
    simlab.write(exp, out)
    n_cases = 1
    for v in sweep.values():
        n_cases *= len(v)
    print(f"Wrote {out}: {n_cases} case(s), {exp['resources']['vram_gb']} GB each. "
          f"Run `simlab validate` on the rig before submitting.")
    return 0


def cmd_cfd_collect(args) -> int:
    from .domains.hx import calibrate as hxcal

    root = Path(args.exp_dir)
    if not root.is_dir():
        print(f"{root} is not a simlab experiment folder")
        return 2
    out = Path(args.out)
    if out.exists() or out.is_symlink():
        print(f"{out} exists; choose a new file")
        return 2
    rows, skipped = [], []
    for case in sorted(p for p in root.iterdir() if p.is_dir()):
        status_p, meas_p = case / "status.json", case / "measurement.json"
        status = json.loads(status_p.read_text()).get("status") if status_p.exists() else None
        if status != "FINISHED":
            skipped.append(f"{case.name}: status {status}")
            continue
        if not meas_p.exists():
            res = json.loads((case / "results.json").read_text()) if (case / "results.json").exists() else {}
            skipped.append(f"{case.name}: not a measurement ({res.get('problems') or 'no measurement.json'})")
            continue
        if not (case / "evidence.json").exists():
            skipped.append(f"{case.name}: no evidence.json; not citable")
            continue
        m = json.loads(meas_p.read_text())
        m["source"] = f"simlab:{root.name}:{case.name}"  # the run folder is the provenance, whatever the row says
        try:
            hxcal.Measurement.model_validate(m)  # the same schema check `aces calibrate` applies
        except Exception as e:  # pydantic ValidationError
            skipped.append(f"{case.name}: invalid measurement row ({str(e).splitlines()[0]})")
            continue
        rows.append(m)
    _safe_write(out, json.dumps(rows, indent=2))
    print(f"{len(rows)} measurement(s) -> {out}")
    for s in skipped:
        print(f"SKIPPED {s}")
    return 0 if rows else 1


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

    s = sub.add_parser("form", help="formgen lattice route from a form.lattice spec")
    s.add_argument("spec")
    s.add_argument("--out", required=True)
    s.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    s.set_defaults(fn=cmd_form)

    s = sub.add_parser("rod", help="rod-lattice route from a rod.lattice spec")
    s.add_argument("spec")
    s.add_argument("--out", required=True)
    s.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    s.set_defaults(fn=cmd_rod)

    s = sub.add_parser("calibrate", help="fit hx calibration factors from measurements")
    s.add_argument("measurements", help="CSV or JSON measurement file (see aces.domains.hx.calibrate)")
    s.add_argument("--registry", required=True, help="calibration JSON to create or update")
    s.add_argument("--boundary", choices=("uniform_wall_temperature", "uniform_heat_flux"),
                   default="uniform_wall_temperature", help="must match the specs that will use the factors")
    s.add_argument("--min-points", type=int, default=3, help="distinct operating conditions needed per factor")
    s.add_argument("--max-scatter", type=float, default=0.15,
                   help="sample sd of log(ratio) above which a constant is refused")
    s.add_argument("--resolution", type=int, default=64, help="level-set resolution; must match the design model's")
    s.add_argument("--project", required=True)
    s.add_argument("--owner", required=True)
    s.add_argument("--rd-activity", required=True)
    s.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    s.set_defaults(fn=cmd_calibrate)

    s = sub.add_parser("cfd-sample", help="one periodic CFD calibration sample (FP64 lattice Boltzmann)")
    s.add_argument("--tpms", choices=tpms.KINDS, required=True)
    s.add_argument("--wall-ratio", type=float, required=True)
    s.add_argument("--cell-m", type=float, required=True)
    s.add_argument("--fluid", required=True, help="CoolProp fluid name")
    s.add_argument("--T", type=float, required=True, help="temperature, K")
    s.add_argument("--p", type=float, required=True, help="pressure, Pa")
    s.add_argument("--re", type=float, required=True, help="target Reynolds number (ACES definition)")
    s.add_argument("--n", type=int, default=32, help="voxels per cell edge")
    s.add_argument("--no-thermal", action="store_true")
    s.add_argument("--gpu", action="store_true", help="use CuPy when a GPU is available")
    s.add_argument("--source", default="local:cfd-sample", help="provenance id recorded in the measurement")
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_cfd_sample)

    s = sub.add_parser("cfd-campaign", help="simlab experiment for a sweep of calibration samples")
    s.add_argument("--id", required=True, help="simlab experiment id (filename-safe)")
    s.add_argument("--tpms", nargs="+", default=list(tpms.KINDS))
    s.add_argument("--wall-ratio", nargs="+", type=float, default=[0.08, 0.12, 0.16])
    s.add_argument("--re", nargs="+", type=float, default=[10, 30, 100, 300])
    s.add_argument("--cell-m", type=float, default=0.004)
    s.add_argument("--fluid", default="Air")
    s.add_argument("--T", type=float, default=320.0)
    s.add_argument("--p", type=float, default=2e5)
    s.add_argument("--n", type=int, default=96)
    s.add_argument("--no-thermal", action="store_true")
    s.add_argument("--validation", action="store_true", help="the duct validation cases instead of samples")
    s.add_argument("--project", default="ACES-calibration")
    s.add_argument("--owner", default="aaron")
    s.add_argument("--rd-activity", default="ACES fast-tier calibration of TPMS heat-exchanger cores")
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_cfd_campaign)

    s = sub.add_parser("cfd-collect", help="measurement rows from a finished simlab calibration campaign")
    s.add_argument("exp_dir", help="simlab experiment output folder (one subfolder per case)")
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_cfd_collect)

    s = sub.add_parser("processes", help="list manufacturing routes")
    s.set_defaults(fn=cmd_processes)

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
