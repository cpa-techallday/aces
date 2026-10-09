"""ACES command line.

  aces check SPEC.yaml                      validate a spec; list what is missing
  aces design SPEC.yaml --out DIR           search, report, evidence record
  aces sample --tpms gyroid --cell-m ...    STL of a periodic TPMS block
  aces export-simlab DIR --rank 0 ...       geometry + simlab experiment for verification
  aces form SPEC.yaml --out DIR             formgen lattice route (robot cell / printed formwork)
  aces rod SPEC.yaml --out DIR              rod-lattice route (ARC-1 process: placed rods, welded nodes)
  aces calibrate MEAS.csv --registry R.json  fit hx factors from CFD/test measurements
  aces radar-calibrate ROWS.json --registry R.json  fit radar factors from EM-sim/bench rows
  aces cfd-sample --tpms gyroid ...         one periodic CFD calibration sample (lattice Boltzmann, FP64)
  aces cfd-campaign --out EXP.yaml ...      simlab experiment: a sweep of calibration samples for the rig
  aces cfd-collect EXP_DIR --out M.json     measurement rows from a finished simlab campaign
  aces processes                            manufacturing routes and what ACES checks for each
  aces cost PART.yaml --rates RATES.yaml    route suggestions + per-unit cost estimates (not quotes)
  aces compliance collect|validate|index    ISO 27001 evidence package (API-first, ledger-chained)
  aces ledger verify|head|seal PATH         check the hash chain; print or sign the head; seal a torn line
  aces isaac-health [--deep]                layered Isaac Sim probe (GPU / install / SimulationApp)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

from . import compliance, costing, evidence, manufacturing, search, simlab, spec
from . import safeio
from .domains import get as get_model
from .domains.form import route as form_route
from .domains.rod import route as rod_route
from .domains.hx import tpms
from .report import hx_report

DEFAULT_LEDGER = Path("out/evidence/ledger.jsonl")


def _confined_by_server(data: dict, spec_dir: Path) -> None:
    """Under ``aces-mcp`` (``ACES_PATH_ROOT`` set), re-check every path in the spec at load time."""
    root = os.environ.get("ACES_PATH_ROOT")
    if not root:
        return
    from .mcp_server import confine_spec_paths

    r = Path(root).resolve()

    def check(p: str) -> str:
        q = Path(p).resolve()
        if not q.is_relative_to(r) or ".git" in q.relative_to(r).parts:
            raise spec.SpecError([f"{p} is outside the ACES MCP root {r}"])
        return str(q)

    confine_spec_paths(data, spec_dir, check)


def _load(path: str):
    data = yaml.safe_load(safeio.read_text_limited(Path(path), 4 * 1024 * 1024)) or {}
    if isinstance(data, dict):
        _confined_by_server(data, Path(path).parent)
    model_cls = get_model(data.get("domain", ""))
    env, req = spec.parse(data, model_cls.requirements_model)
    # like every other path in a spec, the calibration file is relative to the spec's folder
    cf = getattr(req, "calibration_file", None)
    if cf and not Path(cf).is_absolute():
        req = req.model_copy(update={"calibration_file": str((Path(path).parent / cf).resolve())})
    # bounds may only narrow the model's own ranges (audit M5); say so here, before any search starts
    try:
        search.check_bounds(model_cls().parameters(req), env.search.bounds)
    except ValueError as e:
        raise spec.SpecError([str(e)]) from None
    return data, env, req, model_cls


def _fresh_out(out: str | Path) -> str | None:
    """Reason the output folder cannot take a new run, or None.

    A run needs a new folder (audit M1): a folder holding run.json belongs to an earlier run and its own
    evidence record, and anything already in a folder (including a planted link) is not this run's.
    """
    o = Path(out)
    if safeio.is_link(o):
        return f"{o} is a symbolic link; give a real folder for --out"
    if (o / "run.json").exists() or safeio.is_link(o / "run.json"):
        return f"{o} already holds a finished run (run.json); choose a new --out folder"
    if o.exists():
        return f"{o} already exists; a run needs a new --out folder"
    return None


def _new_run_dir(out: str | Path) -> safeio.OutDir | str:
    """The new run folder, opened once (write every run file through it), or why it can't be used."""
    if why := _fresh_out(out):
        return why
    try:
        return safeio.OutDir(out, new=True)
    except OSError as e:
        return str(e)


def _safe_write(path: Path, text: str) -> None:
    """Write one run file through :mod:`aces.safeio` (audit M1).

    The file must be new, and neither it nor its folder may be a link: :class:`safeio.UnsafePathError` for a
    link (so a run folder swapped for a link mid-run is refused), FileExistsError for an existing file.
    """
    safeio.write_text(Path(path), text)


def cmd_check(args) -> int:
    try:
        data = yaml.safe_load(safeio.read_text_limited(Path(args.spec), 4 * 1024 * 1024)) or {}
        if isinstance(data, dict):
            _confined_by_server(data, Path(args.spec).parent)
        if data.get("domain") == form_route.DOMAIN:
            env, _ = spec.parse(data, form_route.FormRequirements)
            print(f"OK: {env.domain} spec for project {env.project!r} is complete (formgen route).")
            return 0
        if data.get("domain") == rod_route.DOMAIN:
            env, req = spec.parse(data, rod_route.RodRequirements)
            study = rod_route.check_study(req, Path(args.spec).parent)  # committed under studies/ (audit H1)
            print(f"OK: {env.domain} spec for project {env.project!r} is complete (rod-lattice route; study "
                  f"{study.rel} at {study.commit[:12]}).")
            return 0
        _, env, _, model_cls = _load(args.spec)
    except (spec.SpecError, KeyError, ValueError, OSError, rod_route.StudyError) as e:
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
    try:
        data = yaml.safe_load(safeio.read_text_limited(spec_path, 4 * 1024 * 1024)) or {}
        if isinstance(data, dict):
            _confined_by_server(data, spec_path.parent)
        if data.get("domain") != rod_route.DOMAIN:
            raise ValueError(f"`aces rod` needs domain: {rod_route.DOMAIN}")
        env, req = spec.parse(data, rod_route.RodRequirements)
        # only committed, unchanged studies under studies/ run (audit H1); refuse before making the run folder
        rod_route.check_study(req, spec_path.parent, out=args.out)
    except (spec.SpecError, ValueError, OSError, rod_route.StudyError) as e:
        print(e)
        return 2
    od = _new_run_dir(args.out)
    if isinstance(od, str):
        print(od)
        return 2
    with od:
        try:
            res = rod_route.run(req, od, spec_path.parent)
        except (FileNotFoundError, FileExistsError, rod_route.StudyError) as e:
            print(e)
            return 2
        canonical = env.model_dump(mode="json")
        rec = evidence.make_record(
            kind="aces.rod_run", spec=canonical, model_id=rod_route.DOMAIN, model_version="study",
            inputs={"requirements_resolved": req.model_dump(mode="json")},
            observations={"summary": res["summary"], "stages": res["stages"], "problems": res["problems"],
                          "warnings": res["warnings"]},
            calibration={}, artifacts=res["artifacts"], root=od.path,
        )
        ledger = Path(args.ledger)
        written = evidence.Ledger(ledger).append(rec)
        od.write_text("run.json", json.dumps({"record_hash": written["record_hash"], "spec_hash": written["spec_hash"],
                                              "ledger": str(ledger.resolve()), "model": written["model"]}, indent=2))
        od.write_text("report.md", _rod_report(canonical, res, written["record_hash"], req))
    out = Path(args.out)
    print(f"Report: {out / 'report.md'}\nEvidence: {ledger} ({written['record_hash'][:12]})")
    for p in res["problems"]:
        print(f"PROBLEM: {p}")
    return 1 if res["problems"] else 0


def cmd_calibrate(args) -> int:
    from .domains.hx import calibrate as hxcal

    try:
        meas = hxcal.load(args.measurements)
        # the Nusselt rows' own boundary (H1 for CFD rows); --boundary only for rows that carry none
        boundary, _ = hxcal.thermal_boundary(meas, args.boundary)
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
    try:
        reg = hxcal.update_registry(args.registry, factors)
    except (ValueError, OSError) as e:  # an existing registry that fails its checks, or an unsafe path
        print(f"Registry {args.registry} not updated: {e}")
        return 2
    rec = evidence.make_record(
        kind="aces.calibration",
        spec={"company": spec.COMPANY, "project": args.project, "owner": args.owner,
              "rd_activity": args.rd_activity, "hypothesis": "Fast-tier hx correlations can be calibrated to "
              "the measurements within the stated scatter."},
        model_id="hx.two_fluid", model_version="calibration",
        inputs={"measurements": str(args.measurements), "boundary": boundary, "min_points": args.min_points,
                "resolution": args.resolution,
                "max_scatter": args.max_scatter},
        observations={"summary": summary, "refused": refused},
        calibration=reg.as_dict(),
        artifacts={"measurements": args.measurements, "registry": args.registry},
    )
    written = evidence.Ledger(args.ledger).append(rec)
    print(f"Registry: {args.registry} ({len(factors)} factor(s))\nEvidence: {args.ledger} ({written['record_hash'][:12]})")
    return 0 if not refused else 1


def cmd_radar_calibrate(args) -> int:
    """Fit the radar fast tier's factors from EM-sim or bench rows (D18), like `aces calibrate` for hx."""
    from .domains.radar import calibrate as rcal
    from .domains.radar.model import DOMAIN as RADAR_DOMAIN

    try:
        rows = rcal.read_rows(args.measurements)
        factors, summary, refused = rcal.fit(rows, min_points=args.min_points, max_scatter=args.max_scatter,
                                             min_held_out=args.min_held_out, holdout_tol=args.holdout_tol)
    except (ValueError, OSError) as e:
        print(e)
        return 2
    for row in summary:
        val = f"{row['value']:.4f}" if "value" in row else "-"
        sc = f"{row['scatter_sd_log']:.3f}" if "scatter_sd_log" in row else "-"
        print(f"{row['key']:<26} points {row['points']:>3}  conditions {row['conditions']:>3}  value {val:>8}  "
              f"scatter {sc:>6}  held-out {len(row.get('held_out', [])):>2}  {row['status']}")
        for c in row.get("held_out", []):
            print(f"  HELD-OUT {c['source']} at {c['frequency_hz'] / 1e9:.4g} GHz: measured {c['measured_db']:.3f} dB, "
                  f"predicted {c['predicted_db']:.3f} dB ({c['error']:+.1%}){'' if c['ok'] else '  FAIL'}")
        for s in row.get("held_out_not_independent", []):
            print(f"  NOT INDEPENDENT: held-out row {s} is at a fitted condition")
    for r in refused:
        print(f"REFUSED: {r}")
    if not factors:
        print("No factors written.")
        return 1
    try:
        reg = rcal.update_registry(args.registry, factors)
    except (OSError, ValueError) as e:
        print(e)
        return 2
    rec = evidence.make_record(
        kind="aces.calibration",
        spec={"company": spec.COMPANY, "project": args.project, "owner": args.owner,
              "rd_activity": args.rd_activity, "hypothesis": "Fast-tier radar loss, noise and gain can be "
              "calibrated to EM and bench measurements within the stated scatter, and the factors predict "
              "held-out rows."},
        model_id=RADAR_DOMAIN, model_version="calibration",
        inputs={"measurements": str(args.measurements), "min_points": args.min_points,
                "max_scatter": args.max_scatter, "min_held_out": args.min_held_out,
                "holdout_tol": args.holdout_tol},
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


def _cost_part(args) -> costing.PartProfile:
    """Part profile from --from-run (a verified design run's pareto entry) or a YAML part file.

    Raises costing.CostInputError or evidence.EvidenceError with a message for the user.
    """
    import hashlib

    from . import safeio

    if args.from_run:
        run_dir = Path(args.from_run)
        # the record is recomputed and the whole chain verified before anything in the folder is trusted (M2)
        _, rec = evidence.load_verified_run(run_dir, args.ledger or str(DEFAULT_LEDGER), artifacts=("pareto",))
        run_spec = rec.get("spec") or {}
        if rec.get("kind") != "aces.design_run" or run_spec.get("domain") != "hx.two_fluid":
            raise costing.CostInputError(f"--from-run costs hx.two_fluid design runs; {run_dir} holds "
                                         f"{rec.get('kind')} for {run_spec.get('domain')!r}")
        pareto_path = run_dir / "pareto.json"
        try:
            text = safeio.read_text_limited(pareto_path, 64 << 20)
        except (OSError, ValueError) as e:
            raise costing.CostInputError(f"cannot read {pareto_path}: {e}") from e
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != rec["artifacts"]["pareto"]["sha256"]:
            raise evidence.EvidenceError(f"{pareto_path} no longer matches its evidence record")
        pareto = json.loads(text)  # the bytes verified above are what `aces design` wrote
        if not isinstance(pareto, list) or not pareto:
            raise costing.CostInputError(f"no designs in {pareto_path}; nothing to cost")
        if not 0 <= args.rank < len(pareto):
            raise costing.CostInputError(f"--rank must be 0..{len(pareto) - 1}")
        return costing.PartProfile.from_pareto(pareto[args.rank], f"{run_dir.name}-rank{args.rank}", spec=run_spec)
    try:
        data = yaml.safe_load(safeio.read_text_limited(args.part, 1 << 20))
    except (OSError, ValueError) as e:
        raise costing.CostInputError(f"cannot read part profile {args.part}: {e}") from e
    except yaml.YAMLError as e:
        raise costing.CostInputError(f"{args.part}: not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise costing.CostInputError(f"{args.part}: expected a mapping with a `part:` block")
    return costing.PartProfile.from_dict(data.get("part", data))


def cmd_cost(args) -> int:
    from . import safeio

    if bool(args.from_run) == bool(args.part):
        print("give a part profile YAML or --from-run DIR (the output folder of `aces design`), not both")
        return 2
    if not args.rates:
        print(costing.RATES_HELP)
        return 2
    if args.out and (safeio.is_link(args.out) or Path(args.out).exists()):
        print(f"{args.out} exists; choose a new file")
        return 2
    if args.out and not Path(args.out).parent.is_dir():
        print(f"cannot write {args.out}: its folder does not exist")
        return 2
    try:
        rates = costing.load_rates(args.rates)
        part = _cost_part(args)
        quantities = costing.check_quantities(args.quantities)
        target = costing.check_quantity(args.quantity)
        if args.top < 1:
            raise costing.CostInputError("--top must be at least 1")
        sug = costing.suggest(part, target, tuple(args.materials or ()), rates=rates)
    except (costing.CostInputError, evidence.EvidenceError, OSError) as e:
        print(e)
        return 2
    cur = rates.currency
    tol = f"{part.tolerance_mm:g} mm" if part.tolerance_mm is not None else "not given (route tolerances not checked)"
    print(costing.DISCLAIMER)
    print(f"Part: {part.name}  bounding box {tuple(round(x, 4) for x in part.envelope_m)} m, solid volume "
          f"{part.volume_cm3:,.1f} cm3, min feature {part.min_feature_m * 1e3:.2f} mm")
    print(f"Duty: {part.max_temp_C:.1f} C, {part.pressure_bar:g} bar; tolerance {tol}; application {part.application}")
    print(f"Profile from: {part.source}")
    for n in part.notes:
        print(f"Note: {n}")
    print(f"Rates from: {rates.origin} ({cur}{', ' + rates.basis if rates.basis else ''}); every value used is "
          "listed under Rate sources")
    print(f"Margins: {costing.MARGINS_TEXT}\n")

    feasible = [s for s in sug if s.feasible]
    print(f"## Route suggestion at qty {target}")
    for s in feasible[: args.top]:
        print(f"  {s.process}/{s.material}: {s.reasons[0]}")
    if not feasible:
        print("  no feasible route at this quantity" + ("" if args.verbose else "; --verbose lists why"))
    planned = [p.id for p in manufacturing.PROCESSES.values() if p.status != "available"]
    if planned:
        print(f"Planned, not available (never recommended): {', '.join(planned)}")
    missing = rates.missing()
    if missing:
        print("\n## Missing inputs (the rates file needs these before those routes or materials can be costed)")
        for m in missing:
            print(f"  {m}")
    if args.verbose:
        print("\n## Excluded (the DFM feedback; pairs with no feedstock price in the rates file are not listed)")
        for s in sug:
            if not s.feasible and s.process not in planned and "is not offered on" not in s.reasons[0]:
                print(f"  {s.process}/{s.material}: {'; '.join(s.reasons)}")

    pairs = tuple((s.process, s.material) for s in feasible[: args.top])
    co, details, sources = None, [], {}
    if pairs:
        co = costing.crossovers(part, pairs, quantities, rates=rates)
        print(f"\n## Unit cost by quantity ({cur}/part, central estimate; '-' is past the route's quantity limit)")
        print("| route | " + " | ".join(f"{q:,}" for q in co["quantities"]) + " |")
        print("| --- | " + " | ".join("---:" for _ in co["quantities"]) + " |")
        for route, vals in co["curves"].items():
            print(f"| {route} | " + " | ".join("-" if v is None else f"{v:,.0f}" for v in vals) + " |")
        print("\n## Crossover points")
        for lead in co["leaders"]:
            if lead["cheapest"] is None:
                print(f"  qty {lead['quantity']:>6,}: no suggested route is costed at this quantity")
            else:
                print(f"  qty {lead['quantity']:>6,}: cheapest is {lead['cheapest']} at {cur} "
                      f"{lead['unit_aud']:,.0f}/part")
        details = [e for pid, mid in pairs for e in costing.curve(part, pid, mid, quantities, rates=rates)]
        for e in details:
            sources.update(e.rates_used)
        for _, mid in pairs:
            sources.update(costing.material_checks_used(part, mid, rates))
        print("\n## Rate sources (every value used above)")
        for k in sorted(sources):
            print(f"  {costing.format_source(k, sources[k])}")
    print(f"\n{costing.DISCLAIMER}")

    if args.out:
        report = {
            "kind": "aces.cost_estimate",
            "disclaimer": f"{costing.DISCLAIMER} Every rate is the rates file's, listed with its source.",
            "rates_file": rates.origin,
            "currency": cur,
            "margins": costing.MARGINS_TEXT,
            "part": dict(vars(part)),
            "quantity_target": target,
            "planned_routes": planned,
            "missing_inputs": missing,
            "suggestions": [{"process": s.process, "material": s.material, "feasible": s.feasible,
                             "unit_aud": s.unit_aud_at_target, "reasons": list(s.reasons)} for s in sug],
            "crossovers": co,
            "detail": {
                f"{e.process}/{e.material}@{e.quantity}": {
                    "unit_aud": e.unit_aud, "unit_low_aud": e.unit_low_aud, "unit_high_aud": e.unit_high_aud,
                    "batch_aud": e.batch_aud, "mass_kg": e.mass_kg, "parts_per_build": e.parts_per_build,
                    "builds": e.builds, "breakdown": e.breakdown, "assumptions": list(e.assumptions)}
                for e in details},
            "rate_sources": sources,
        }
        try:
            safeio.write_text(args.out, json.dumps(report, indent=2), overwrite=False)
        except OSError as e:
            print(f"cannot write {args.out}: {e}")
            return 2
        print(f"Wrote {args.out}")
    return 0 if feasible else 1


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
    try:
        data = yaml.safe_load(safeio.read_text_limited(spec_path, 4 * 1024 * 1024)) or {}
        if isinstance(data, dict):
            _confined_by_server(data, spec_path.parent)
        if data.get("domain") != form_route.DOMAIN:
            raise ValueError(f"`aces form` needs domain: {form_route.DOMAIN}")
        env, req = spec.parse(data, form_route.FormRequirements)
    except (spec.SpecError, ValueError, OSError) as e:
        print(e)
        return 2
    try:
        import formgen.pipeline  # noqa: F401 - optional dependency group [formgen]; check before making the folder
    except ImportError as e:
        print(f"formgen dependencies missing ({e}); install with: pip install -e .[formgen]")
        return 2
    od = _new_run_dir(args.out)
    if isinstance(od, str):
        print(od)
        return 2
    with od:
        res = form_route.run(req, od, spec_path.parent)
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
            root=od.path,
        )
        ledger = Path(args.ledger)
        written = evidence.Ledger(ledger).append(rec)
        od.write_text("run.json", json.dumps({"record_hash": written["record_hash"], "spec_hash": written["spec_hash"],
                                              "ledger": str(ledger.resolve()), "model": written["model"]}, indent=2))
        od.write_text("report.md", _form_report(canonical, res, written["record_hash"], req))
    out = Path(args.out)
    print(f"Report: {out / 'report.md'}\nEvidence: {ledger} ({written['record_hash'][:12]})")
    for p in res["problems"]:
        print(f"PROBLEM: {p}")
    return 1 if res["problems"] else 0


class DesignContract:
    """What `aces design` needs from a domain model on top of the DesignModel protocol (audit R1).

    Every model gives ``id``, ``version``, ``requirements_model``, ``parameters(req)``, ``evaluate(params, req)``
    and ``registry(req)``.  Each Evaluation carries its own objectives, constraints, metrics, ``warnings`` and
    ``uncalibrated`` keys; they reach pareto.json and evaluations.jsonl (both hashed into the record) and the
    report.  On top of that a model states, as ``rank_objective``, ``run_inputs()`` and ``report(...)``:

    rank_objective  the objective (minimised) that orders pareto.json, the record and the report
    run_inputs      model settings recorded in the record's inputs (hx: its level-set resolution); {} if none
    report          report(spec, SearchResult, record_hash) -> markdown that says "Not verified" (rule 3)

    hx.two_fluid predates the contract, so its terms are stated here, unchanged.
    """

    __slots__ = ("rank_objective", "report", "run_inputs")

    def __init__(self, rank_objective: str, run_inputs: dict, report) -> None:
        self.rank_objective, self.run_inputs, self.report = rank_objective, dict(run_inputs), report

    @classmethod
    def of(cls, model) -> DesignContract:
        if all(hasattr(model, a) for a in ("rank_objective", "run_inputs", "report")):
            return cls(model.rank_objective, model.run_inputs(), model.report)
        if model.id == "hx.two_fluid":
            return cls("mass_kg", {"resolution": model.resolution},
                       lambda s, r, h: hx_report(s, r, h, model.id, model.version))
        raise ValueError(f"{model.id} does not state its design contract (rank_objective, run_inputs, report)")


def cmd_design(args) -> int:
    try:
        _, env, req, model_cls = _load(args.spec)
    except (spec.SpecError, KeyError, OSError, ValueError) as e:
        print(e)
        return 2
    if why := _fresh_out(args.out):
        print(why)
        return 2
    model = model_cls()
    try:
        contract = DesignContract.of(model)
        model.registry(req)  # fail early on a missing or unsourced calibration file
    except (FileNotFoundError, ValueError) as e:
        print(e)
        return 2
    cfg = env.search
    trials = args.trials or cfg.trials
    res = search.run(model, req, trials=trials, seed=cfg.seed, population=cfg.population, bounds=cfg.bounds)
    if contract.rank_objective not in res.objective_names:
        print(f"{model.id} ranks by {contract.rank_objective!r}, which is not one of its objectives "
              f"{res.objective_names}; nothing written")
        return 2
    out = Path(args.out)
    canonical_spec = env.model_dump(mode="json")
    pareto = sorted(res.pareto, key=lambda e: e.objectives[contract.rank_objective])
    run_dir = _new_run_dir(out)  # still new after the search, or refused (audit M1)
    if isinstance(run_dir, str):
        print(run_dir)
        return 2
    with run_dir:
        run_dir.write_text("pareto.json", json.dumps([e.as_dict() for e in pareto], indent=2, default=str),
                           overwrite=True)
        run_dir.write_text("evaluations.jsonl", "".join(json.dumps(e.as_dict(), default=str) + "\n"
                                                        for e in res.evaluations), overwrite=True)
        calib = model.registry(req).as_dict()
        rec = evidence.make_record(
            kind="aces.design_run",
            spec=canonical_spec,
            model_id=model.id,
            model_version=model.version,
            inputs={"trials": trials, "seed": cfg.seed, "population": cfg.population, "bounds": cfg.bounds,
                    **contract.run_inputs},
            observations={
                "evaluated": len(res.distinct),
                "feasible": len(res.feasible),
                "pareto": [{"params": e.params, "objectives": e.objectives} for e in pareto],
            },
            calibration=calib,
            artifacts={"pareto": (run_dir.path / "pareto.json", run_dir.sha256("pareto.json")),
                       "evaluations": (run_dir.path / "evaluations.jsonl", run_dir.sha256("evaluations.jsonl"))},
        )
        ledger = Path(args.ledger)
        written = evidence.Ledger(ledger).append(rec)
        # The run manifest ties this directory to its own evidence record (not the ledger tail).
        run_dir.write_text("run.json", json.dumps({
            "record_hash": written["record_hash"], "spec_hash": written["spec_hash"],
            "ledger": str(ledger.resolve()), "model": written["model"]}, indent=2), overwrite=True)
        run_dir.write_text("report.md", contract.report(canonical_spec, res, written["record_hash"]), overwrite=True)
    print(f"{len(res.distinct)} distinct designs evaluated, {len(res.feasible)} feasible, {len(pareto)} non-dominated.")
    print(f"Report: {out / 'report.md'}\nEvidence: {ledger} ({written['record_hash'][:12]})")
    return 0 if pareto else 1


def cmd_sample(args) -> int:
    verts, faces = tpms.sample_mesh(args.tpms, args.cell_m, args.wall_ratio * args.cell_m, args.cells, args.region, args.resolution)
    scale = 1000.0 if args.units == "mm" else 1.0
    try:
        p = tpms.write_stl(args.out, verts, faces,
                           f"ACES {args.tpms} {args.region} a={args.cell_m} t/a={args.wall_ratio} units={args.units}",
                           scale=scale)
    except FileExistsError:
        print(f"{args.out} exists; choose a new file")
        return 2
    print(f"{p}: {len(faces)} triangles, enclosed volume {tpms.mesh_volume(verts, faces):.4e} m3")
    return 0


def cmd_export_simlab(args) -> int:
    import hashlib

    run_dir = Path(args.run)
    try:
        simlab.check_name("solver", args.solver)
        _, rec = evidence.load_verified_run(run_dir, args.ledger or str(DEFAULT_LEDGER))
    except evidence.EvidenceError as e:
        print(f"{e}; refusing to export.")
        return 2
    except ValueError as e:
        print(e)
        return 2
    if (rec.get("model") or {}).get("id") != "hx.two_fluid":
        print(f"export-simlab writes TPMS core geometry, so it takes hx.two_fluid runs only; this run is "
              f"{(rec.get('model') or {}).get('id')!r}.")
        return 2
    # read the bytes once and check them against the record, so the file can't change between check and use
    raw = (run_dir / "pareto.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != rec["artifacts"]["pareto"]["sha256"]:
        print(f"{run_dir / 'pareto.json'} no longer matches its evidence record; refusing to export.")
        return 2
    pareto = json.loads(raw)
    if not pareto:
        print("No feasible designs in this run; nothing to verify.")
        return 1
    if not 0 <= args.rank < len(pareto):
        print(f"--rank must be 0..{len(pareto) - 1}")
        return 2
    d = pareto[args.rank]
    p = d["params"]
    run_spec = rec["spec"]
    try:
        simlab.check_name("project", run_spec["project"])
    except ValueError as e:
        print(f"{e}; refusing to export.")
        return 2
    scale = 1000.0 if args.units == "mm" else 1.0
    stl_name = f"rank{args.rank}_{p['tpms']}_{args.region}_{args.units}.stl"
    exp_name = f"simlab_rank{args.rank}_{args.region}.yaml"
    try:
        od = safeio.OutDir(run_dir, mkdir=False)
    except OSError as e:
        print(f"{e}; refusing to export.")
        return 2
    with od:
        for name in (stl_name, exp_name):
            if od.exists(name):
                print(f"{run_dir / name} exists (an earlier export, or a planted link); export never overwrites. "
                      f"Move it away first, or export another rank or region.")
                return 2
        try:
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
                geometry_ref=str(od.path / stl_name),
                geometry_units=args.units,
                params={"aces_design": p,
                        "aces_metrics": {k: v for k, v in d["metrics"].items() if k in ("hot", "cold")}},
                outputs=["pressure_gradient_Pa_m", "nusselt", "mass_flow_kg_s"],
                vram_gb=args.vram_gb,
                system="hx",
                aces_record=rec["record_hash"],
            )
        except ValueError as e:
            print(f"{e}; refusing to export.")
            return 2
        verts, faces = tpms.sample_mesh(p["tpms"], p["cell_m"], p["wall_ratio"] * p["cell_m"], args.cells, args.region)
        stl = tpms.write_stl(stl_name, verts, faces, f"ACES {p['tpms']} {args.region} units={args.units}",
                             scale=scale, out=od)
        path = simlab.write(exp, exp_name, out=od)
    print(f"Geometry: {stl}\nExperiment: {path}\nAdd the solver adapter's own params, then run `simlab validate` before submitting.")
    return 0


def cmd_ledger(args) -> int:
    led = evidence.Ledger(args.path)
    if args.action == "seal":
        try:
            rec = led.seal()
        except evidence.LedgerError as e:
            print(f"NOT SEALED: {e}")
            return 1
        print(f"Sealed the interrupted last line ({rec['sealed_line']['bytes']} bytes, sha256 "
              f"{rec['sealed_line']['sha256'][:12]}); it stays in the file as written. Seal record "
              f"{rec['record_hash'][:12]}.")
        return 0
    if args.action == "head":
        try:
            n, head = led.sign_head() if args.sign else led.head()
        except evidence.LedgerError as e:
            print(f"BROKEN: {e}")
            return 1
        print(f"records {n}\nhead {head}")
        if args.sign:
            print(f"Signed head written to {led.head_path}.")
        print(f"Keep a copy of these where the ledger's writers can't change them; later check with:\n"
              f"  aces ledger verify {args.path} --expect-head {head} --min-records {n}")
        return 0
    expect = args.expect_head
    if expect is not None and not (len(expect) == 64 and all(c in "0123456789abcdef" for c in expect)):
        print("--expect-head takes the full 64-character head hash that `aces ledger head` prints")
        return 2
    if args.min_records is not None and args.min_records < 1:
        print("--min-records must be 1 or more")
        return 2
    ok, msg = led.verify(expect_head=expect, min_records=args.min_records)
    print(("OK: " if ok else "BROKEN: ") + msg)
    return 0 if ok else 1


def cmd_isaac_health(args) -> int:
    """Layered Isaac Sim probe; prints structured JSON and a one-line verdict."""
    from .isaac import health as isaac_health

    rep = isaac_health.health(deep=args.deep, timeout_seconds=args.timeout)
    gpu = rep["layers"]["gpu"]
    if gpu["ok"]:
        names = ", ".join(f"{g['name']} (driver {g['driver']})" for g in gpu["gpus"])
        print(f"GPU:     OK — {names}")
    else:
        print(f"GPU:     FAIL — {gpu.get('detail', '')} {gpu.get('guidance', '')}")
    inst = rep["layers"]["install"]
    if inst["ok"]:
        print(f"Install: OK — {inst['root']}")
    else:
        print(f"Install: FAIL — {inst['error']['message']}")
    if "deep" in rep["layers"]:
        deep = rep["layers"]["deep"]
        if deep.get("ok"):
            print(f"Deep:    OK — SimulationApp booted, Isaac Sim {deep.get('version', '?')}")
        else:
            print(f"Deep:    FAIL — {deep.get('error', {}).get('message', 'deep probe failed')}")
    print(json.dumps(rep, indent=1))
    return 0 if rep["ok"] else 1
# ----------------------------------------------------------------------------- ISO 27001 compliance

def cmd_compliance(args) -> int:
    from . import safeio  # local: the shared import line belongs to every agent

    if args.action in ("validate", "index"):
        try:
            findings = compliance.validate(args.dir) if args.action == "validate" else None
            text = compliance.index(args.dir) if args.action == "index" else ""
        except (OSError, ValueError) as e:
            print(f"cannot read the evidence folder: {e}")
            return 2
    if args.action == "validate":
        bad = [f for f in findings if f.level != "ok"]
        for f in findings:
            if f.level == "ok" and not args.verbose:
                continue
            print(f"{f.level.upper():<8} {f.control:<7} {f.detail}")
        print(f"\n{len(bad)} gap(s)/stale/invalid item(s)" if bad else "No gaps: package is complete and current.")
        return 1 if bad else 0
    if args.action == "index":
        out = Path(args.dir) / "INDEX.md"
        try:
            safeio.write_text(out, text)  # exclusive create, never through a link (audit C4)
        except FileExistsError:
            print(f"{out} exists; remove it or validate instead")
            return 2
        except OSError as e:
            print(f"refusing to write {out}: {e}")
            return 2
        print(f"Wrote {out}")
        return 0
    # collect: every input is checked before anything runs (audit C1)
    try:
        compliance.check_ledger_path(args.ledger, must_exist=args.with_aces)
        collectors = []
        if args.repo:
            collectors += compliance.github_collectors(args.repo)
        if args.with_aces:
            collectors += compliance.aces_collectors(args.ledger)
    except (OSError, ValueError) as e:
        print(f"refusing to collect: {e}")
        return 2
    if not collectors:
        print("nothing to collect: pass --repo owner/name and/or --with-aces")
        return 2
    try:
        manifest = compliance.collect(args.dir, collectors, dry_run=args.dry_run)
    except (OSError, ValueError) as e:
        print(f"collection stopped: {e}")
        return 2
    ok = [r for r in manifest["results"] if r["ok"] and r.get("sha256")]
    failed = [r for r in manifest["results"] if not r["ok"] and not r.get("optional")]
    warned = [r for r in manifest["results"] if not r["ok"] and r.get("optional")]
    for r in manifest["results"]:
        if r["ok"]:
            state = "ok" if r.get("sha256") else "skip"
        else:
            state = "WARN" if r.get("optional") else "FAILED"
        print(f"{state:<7} {r['control']:<8} {r['name']}" + (f" ({r['error']})" if r.get("error") and not r["ok"] else ""))
    if not args.dry_run:
        rec = evidence.make_record(
            kind="aces.compliance_collection",
            spec={"company": spec.COMPANY, "project": "iso27001-evidence", "owner": args.owner,
                  "rd_activity": "n/a", "hypothesis": "n/a (compliance evidence collection)"},
            model_id="aces.compliance", model_version="0.2",
            inputs={"repo": args.repo, "with_aces": args.with_aces, "run_dir": manifest["run_dir"]},
            observations={"files": [{"file": r["file"], "control": r["control"], "sha256": r["sha256"]}
                                    for r in ok],
                          "failed": [{"name": r["name"], "error": r["error"]} for r in failed],
                          "warnings": [{"name": r["name"], "error": r["error"]} for r in warned]},
            calibration={}, artifacts={"manifest": Path(manifest["manifest_file"])},
        )
        written = evidence.Ledger(args.ledger).append(rec)
        print(f"\nEvidence: {manifest['evidence_dir']}")
        print(f"{len(ok)} file(s) collected, {len(failed)} failed, {len(warned)} optional failed (warning). "
              f"Ledger: {args.ledger} ({written['record_hash'][:12]})")
    return 1 if failed else 0




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
        expected = ("dpdx and H1 h per case (rows carry boundary uniform_heat_flux); fitted with `aces calibrate`, "
                    "which takes the boundary from the rows.")
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


CFD_SOLVER = "aces-tpms-periodic"
EVIDENCE_MAX_BYTES = 8_000_000
_CASE_KEYS = ("tpms", "wall_ratio", "cell_m", "fluid", "T_K", "p_Pa")


def _same(a, b) -> bool:
    num = (int, float)
    if isinstance(a, num) and isinstance(b, num) and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(float(a) - float(b)) <= 1e-9 * max(abs(float(a)), abs(float(b)))
    return a == b


def _cfd_evidence(case: Path, exp_id: str, row: dict) -> tuple[str | None, str | None]:
    """(sha256 of evidence.json, None) when it makes ``row`` citable, else (None, why not) (audit L3).

    simlab's per-run evidence.json holds the experiment spec, input hashes, image digest and device. The check reads
    the spec ACES wrote (``id``, ``solver``, ``params``, ``sweep``) from its ``spec`` member, or from the top level,
    and needs it to name this experiment and solver, any ``case_id`` to name this case, and every sample parameter it
    fixes (params) or lists (sweep) to agree with the row.
    """
    import hashlib

    from .safeio import is_link

    p = case / "evidence.json"
    if not p.exists():
        return None, "no evidence.json; not citable"
    if is_link(p):
        return None, "evidence.json is a link; not citable"
    if p.stat().st_size > EVIDENCE_MAX_BYTES:
        return None, f"evidence.json is larger than {EVIDENCE_MAX_BYTES:,} bytes"
    raw = p.read_bytes()
    try:
        ev = json.loads(raw)
    except ValueError:
        return None, "evidence.json is not valid JSON"
    if not isinstance(ev, dict):
        return None, "evidence.json is not a JSON object"
    sp = ev["spec"] if isinstance(ev.get("spec"), dict) else ev
    if sp.get("id") is None:
        return None, "evidence.json does not name its experiment (no spec id)"
    if sp.get("id") != exp_id:
        return None, f"evidence.json belongs to experiment {sp.get('id')!r}, not {exp_id!r}"
    if sp.get("solver") != CFD_SOLVER:
        return None, f"evidence.json is for solver {sp.get('solver')!r}, not {CFD_SOLVER}"
    for where in (ev, sp):
        cid = where.get("case_id")
        if cid is not None and str(cid) != case.name:
            return None, f"evidence.json is for case {cid!r}, not {case.name!r}"
    params = sp.get("params") if isinstance(sp.get("params"), dict) else {}
    sweep = sp.get("sweep") if isinstance(sp.get("sweep"), dict) else {}
    for k in _CASE_KEYS:
        if k in params and not _same(params[k], row.get(k)):
            return None, f"evidence params {k}={params[k]!r}, but the row has {row.get(k)!r}"
        if isinstance(sweep.get(k), list) and not any(_same(v, row.get(k)) for v in sweep[k]):
            return None, f"evidence sweep {k} {sweep[k]} does not include the row's {row.get(k)!r}"
    return hashlib.sha256(raw).hexdigest(), None


def cmd_cfd_collect(args) -> int:
    from .cfd import periodic
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
        m = json.loads(meas_p.read_text())
        if not isinstance(m, dict):
            skipped.append(f"{case.name}: measurement.json is not a JSON object")
            continue
        sha, why = _cfd_evidence(case, root.name, m)
        if why:
            skipped.append(f"{case.name}: {why}")
            continue
        if m.get("boundary") is None:  # rows written before they carried it; the evidence names the H1-only solver
            m["boundary"] = periodic.BOUNDARY
        elif m["boundary"] != periodic.BOUNDARY:
            skipped.append(f"{case.name}: row says boundary {m['boundary']}, but {CFD_SOLVER} measures "
                           f"{periodic.BOUNDARY} (H1) only")
            continue
        m["evidence_sha256"] = sha  # pins the evidence the row was accepted on
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


def cmd_propose(args) -> int:
    from .propose import LicenceError, RESEARCH_ONLY, RESEARCH_STAMP, get
    from .propose.tpms_parametric import measure
    try:
        proposer = get(args.method, research=args.research)
    except (KeyError, LicenceError) as e:
        print(f"REFUSED: {e}")
        return 2
    req = {}
    if args.cell_m is not None:
        req["cell_m"] = args.cell_m
    if args.kinds:
        req["kinds"] = args.kinds.split(",")
    try:
        proposals = [measure(p) for p in proposer.propose(req, args.n, args.seed)]
    except ValueError as e:
        print(f"REFUSED: {e}")
        return 2
    research_lane = proposer.name in RESEARCH_ONLY
    if research_lane:
        for p in proposals:
            if RESEARCH_STAMP not in p["notes"]:
                p["notes"].append(RESEARCH_STAMP)
    with safeio.OutDir(args.out) as od:
        od.write_text("proposals.json", json.dumps(
            {"proposer": proposer.name, "licence": proposer.licence, "domain": proposer.domain,
             "seed": args.seed, "n": len(proposals),
             "research_only": research_lane,
             "note": ("Tier 0 proposals: suggestions only — measured, not verified. A registered solver signs off (D5)."
                      + (f" {RESEARCH_STAMP}." if research_lane else "")),
             "proposals": proposals}, indent=2))
    warned = sum(1 for p in proposals if p["notes"])
    print(f"{len(proposals)} proposal(s) from {proposer.name} ({proposer.licence}) -> {args.out}/proposals.json"
          + (" [RESEARCH ONLY]" if research_lane else "")
          + (f"; {warned} carry warnings" if warned else ""))
    return 0


def cmd_corpus(args) -> int:
    from .propose import corpus as corpus_mod
    rows, skipped = corpus_mod.build_corpus(args.measurements, boundary=args.boundary)
    if not rows:
        print(f"no usable rows in {args.measurements} (skipped {len(skipped)})")
        return 1
    corpus_mod.write_jsonl(rows, args.out)
    print(f"{len(rows)} corpus row(s) -> {args.out}; skipped {len(skipped)}")
    for s in skipped[:10]:
        print(f"  skipped: {s}")
    return 0


def cmd_voxel_measure(args) -> int:
    """Measure a voxelised solid (e.g. a Tier-0 generated grid) on ACES definitions."""
    import numpy as np

    from .propose import RESEARCH_ONLY, RESEARCH_STAMP
    from .propose.voxel_measure import measure_voxel_solid

    if args.proposer in RESEARCH_ONLY and not args.research:
        print(f"REFUSED: proposer {args.proposer!r} is research-only (D29): "
              f"pass --research for internal R&D use; never in a product or customer deliverable")
        return 2
    try:
        grid = np.load(args.grid)
    except (OSError, ValueError) as e:
        print(f"REFUSED: cannot load {args.grid}: {e}")
        return 2
    notes: list[str] = list(args.note or [])
    if args.proposer in RESEARCH_ONLY:
        notes.append(RESEARCH_STAMP)
    try:
        facts = measure_voxel_solid(grid != 0, args.pitch_m, args.proposer, args.seed,
                                    notes=tuple(notes))
    except ValueError as e:
        print(f"REFUSED: {e}")
        return 2
    with safeio.OutDir(args.out) as od:
        od.write_text("measurement.json", json.dumps(facts.to_json(), indent=2))
    print(f"voxel measurement -> {args.out}/measurement.json "
          f"(solid {facts.solid_fraction:.3f}, wall min {facts.wall_min_m * 1e3:.3f} mm, "
          f"neck min {min(facts.neck_m) * 1e3:.3f} mm, isolated void {facts.isolated_void_fraction:.3f})"
          + (" [RESEARCH ONLY]" if args.proposer in RESEARCH_ONLY else ""))
    return 0


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
    s.add_argument("--ledger", help=f"the ledger the run was recorded in (default {DEFAULT_LEDGER}); a run.json "
                   "naming another ledger is refused unless that ledger is given here")
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
    s.add_argument("--boundary", choices=("uniform_wall_temperature", "uniform_heat_flux"), default=None,
                   help="thermal boundary of rows with h that carry no 'boundary' column (CFD rows are H1 = "
                        "uniform_heat_flux and carry it); refused if it disagrees with the rows")
    s.add_argument("--min-points", type=int, default=3, help="distinct operating conditions needed per factor")
    s.add_argument("--max-scatter", type=float, default=0.15,
                   help="sample sd of log(ratio) above which a constant is refused")
    s.add_argument("--resolution", type=int, default=64, help="level-set resolution; must match the design model's")
    s.add_argument("--project", required=True)
    s.add_argument("--owner", required=True)
    s.add_argument("--rd-activity", required=True)
    s.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    s.set_defaults(fn=cmd_calibrate)

    s = sub.add_parser("radar-calibrate", help="fit radar calibration factors from EM-sim or bench rows")
    s.add_argument("measurements", help="JSON or CSV rows (see aces.domains.radar.calibrate)")
    s.add_argument("--registry", required=True, help="calibration JSON to create or update")
    s.add_argument("--min-points", type=int, default=3, help="distinct frequencies needed per factor")
    s.add_argument("--max-scatter", type=float, default=0.15,
                   help="sample sd of ln(ratio) above which a constant is refused")
    s.add_argument("--min-held-out", type=int, default=1,
                   help="held-out rows at unseen frequencies each factor must predict")
    s.add_argument("--holdout-tol", type=float, default=0.10,
                   help="largest held-out error, as a fraction of the linear quantity")
    s.add_argument("--project", required=True)
    s.add_argument("--owner", required=True)
    s.add_argument("--rd-activity", required=True)
    s.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    s.set_defaults(fn=cmd_radar_calibrate)

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

    s = sub.add_parser("cost", help="manufacturing route suggestion + unit cost across quantities (estimate only: "
                                    "not a quote, not verified)")
    s.add_argument("part", nargs="?", help="YAML part profile with a part: block (see aces.costing.PartProfile)")
    s.add_argument("--rates", help="rates file (YAML/JSON): every rate, build volume and material property as "
                                   "{value, source, date}; required")
    s.add_argument("--from-run", help="output folder of `aces design` (hx.two_fluid); cost a verified pareto entry")
    s.add_argument("--ledger", help=f"with --from-run: the ledger the run was recorded in (default {DEFAULT_LEDGER})")
    s.add_argument("--rank", type=int, default=0, help="pareto rank to cost with --from-run")
    s.add_argument("--quantity", type=int, default=10, help="target quantity for the route suggestion")
    s.add_argument("--quantities", type=int, nargs="+", default=list(costing.DEFAULT_QUANTITIES),
                   help="quantities for the cost curves (each curve stops at its route's quantity limit)")
    s.add_argument("--materials", nargs="+", help="restrict candidate materials (ids from the rates file)")
    s.add_argument("--top", type=int, default=4, help="how many routes to curve and report")
    s.add_argument("--verbose", action="store_true", help="list excluded routes and why")
    s.add_argument("--out", help="write the full estimate as JSON (new file only)")
    s.set_defaults(fn=cmd_cost)

    s = sub.add_parser("ledger", help="evidence ledger tools: verify, head (print or sign it), seal",
                       description="verify: check the hash chain (and the anchors given). head: print the record "
                                   "count and head hash to keep elsewhere; --sign writes the HMAC-signed head "
                                   "(key from ACES_LEDGER_HMAC_KEY). seal: close an interrupted last line with a "
                                   "seal record; the line itself is kept as written.")
    s.add_argument("action", choices=("verify", "head", "seal"))
    s.add_argument("path", nargs="?", default=str(DEFAULT_LEDGER))
    s.add_argument("--expect-head", help="verify: a head hash kept from `aces ledger head`; fail unless the chain "
                                         "still holds it")
    s.add_argument("--min-records", type=int, help="verify: fail if the ledger holds fewer records")
    s.add_argument("--sign", action="store_true", help="head: write <ledger>.head, signed with "
                                                       "ACES_LEDGER_HMAC_KEY")
    s.set_defaults(fn=cmd_ledger)

    s = sub.add_parser("isaac-health", help="layered Isaac Sim probe (GPU / install / SimulationApp)")
    s.add_argument("--deep", action="store_true", help="also boot a minimal headless SimulationApp")
    s.add_argument("--timeout", type=int, default=300, help="deep-probe wall clock, seconds")
    s.set_defaults(fn=cmd_isaac_health)
    s = sub.add_parser("compliance", help="ISO 27001 evidence collection (API-first, ledger-chained)")
    s.add_argument("action", choices=("collect", "validate", "index"))
    s.add_argument("dir", help="evidence folder; each collect adds a new run-*/ folder with its manifest")
    s.add_argument("--repo", help="GitHub repo as owner/name (letters, digits, '_', '.', '-'); "
                                  "enables the repo collectors")
    s.add_argument("--with-aces", action="store_true",
                   help="also collect ACES ledger evidence (verify result, record count and head hash only)")
    s.add_argument("--dry-run", action="store_true", help="list what would be collected, write nothing")
    s.add_argument("--owner", default="aaron")
    s.add_argument("--verbose", action="store_true", help="validate: show ok controls too")
    s.add_argument("--ledger", default=str(DEFAULT_LEDGER),
                   help="ACES ledger: a regular file, not a link (must exist with --with-aces); "
                        "collect appends a record of the run")
    s.set_defaults(fn=cmd_compliance)

    s = sub.add_parser("propose", help="Tier 0: generate and measure geometry proposals (suggestions only; "
                                       "a verified solver signs off)")
    s.add_argument("--method", default="tpms-parametric",
                   help="registered proposer (aces propose lists it; unknown or licence-blocked names are refused)")
    s.add_argument("--n", type=int, default=8, help="number of proposals")
    s.add_argument("--seed", type=int, default=1, help="deterministic sampling seed")
    s.add_argument("--cell-m", type=float, default=None, help="TPMS cell size in metres (default 0.004)")
    s.add_argument("--kinds", default=None, help="comma-separated TPMS kinds (default: gyroid,diamond,primitive)")
    s.add_argument("--out", required=True, help="output folder (created; never overwritten)")
    s.add_argument("--research", action="store_true",
                   help="admit a research-lane proposer (D29): internal R&D only; every proposal "
                        "is stamped RESEARCH ONLY and must never reach a product image or customer deliverable")
    s.set_defaults(fn=cmd_propose)

    s = sub.add_parser("corpus", help="training corpus (JSONL, with provenance) from verified measurement rows")
    s.add_argument("measurements", help="measurement JSON from aces cfd-collect")
    s.add_argument("--out", required=True, help="corpus JSONL path")
    s.add_argument("--boundary", default="uniform_heat_flux",
                   choices=("uniform_wall_temperature", "uniform_heat_flux"),
                   help="thermal boundary the rows were measured under")
    s.set_defaults(fn=cmd_corpus)

    s = sub.add_parser("voxel-measure", help="measure a voxelised solid (.npy) on ACES definitions "
                                             "(solid fraction, min wall, neck, enclosed void)")
    s.add_argument("grid", help=".npy voxel grid; nonzero voxels are solid")
    s.add_argument("--pitch-m", type=float, required=True, help="voxel edge length in metres")
    s.add_argument("--proposer", required=True, help="name of the proposer that emitted the grid")
    s.add_argument("--seed", type=int, default=0, help="proposer seed for provenance")
    s.add_argument("--note", action="append", default=None, help="extra provenance note (repeatable)")
    s.add_argument("--research", action="store_true",
                   help="required when --proposer is a research-quarantined name (D29); stamps the "
                        "measurement RESEARCH ONLY")
    s.add_argument("--out", required=True, help="output folder (created; never overwritten)")
    s.set_defaults(fn=cmd_voxel_measure)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except safeio.UnsafePathError as e:  # a planted link, or a folder swapped mid-run (audit M1)
        print(f"REFUSED: {e}")
        return 2
    except evidence.LedgerError as e:  # e.g. an interrupted append: nothing was recorded (audit M3)
        print(f"LEDGER: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
