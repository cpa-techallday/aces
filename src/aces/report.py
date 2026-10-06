"""Markdown report for a design run.  States what is and is not verified."""
from __future__ import annotations

from .search import SearchResult


def _mm(x: float) -> str:
    return f"{x * 1e3:.2f}"


def hx_report(spec: dict, result: SearchResult, ledger_hash: str | None, model_id: str, model_version: str) -> str:
    lines = [
        f"# ACES design run: {spec['project']}",
        "",
        f"- Domain: `{spec['domain']}` (model {model_id} {model_version})",
        f"- Company: {spec['company']} - R&D activity: {spec['rd_activity']}",
        f"- Hypothesis: {spec['hypothesis']}",
        f"- Evaluated designs: {len(result.distinct)} distinct; feasible: {len(result.feasible)}; "
        f"non-dominated feasible: {len(result.pareto)}",
    ]
    if ledger_hash:
        lines.append(f"- Evidence record: `{ledger_hash}`")
    lines += [
        "",
        "**Status: fast-tier ranking only. Not verified.** Every design below must be checked on a "
        "registered simlab solver before it is quoted, printed or sold.",
        "",
    ]
    if not result.pareto:
        lines += [
            "## No feasible design found",
            "",
            "No evaluated design met every constraint. Most-violated constraints across the run:",
            "",
        ]
        worst: dict[str, int] = {}
        for e in result.evaluations:
            for k, v in e.constraints.items():
                if v > 0:
                    worst[k] = worst.get(k, 0) + 1
        for k, n in sorted(worst.items(), key=lambda kv: -kv[1]):
            lines.append(f"- `{k}` violated in {n} of {len(result.evaluations)} designs")
        return "\n".join(lines) + "\n"

    lines += [
        "## Non-dominated feasible designs (lightest first)",
        "",
        "| # | TPMS | cell mm | min wall mm | neck mm (hot/cold) | L x W x H mm | duty kW | eff. | dp hot kPa | dp cold kPa | mass kg | Re hot / cold |",
        "| - | - | - | - | - | - | - | - | - | - | - | - |",
    ]
    ranked = sorted(result.pareto, key=lambda e: e.objectives["mass_kg"])
    for i, e in enumerate(ranked):
        p, m = e.params, e.metrics
        lines.append(
            f"| {i} | {p['tpms']} | {_mm(p['cell_m'])} | {_mm(m['wall_min_m'])} | "
            f"{_mm(m['hot']['neck_m'])} / {_mm(m['cold']['neck_m'])} | "
            f"{_mm(p['length_m'])} x {_mm(p['width_m'])} x {_mm(p['height_m'])} | {m['duty_W'] / 1e3:.2f} | "
            f"{m['effectiveness']:.3f} | {m['hot']['pressure_drop_Pa'] / 1e3:.2f} | {m['cold']['pressure_drop_Pa'] / 1e3:.2f} | "
            f"{m['mass_kg']:.3f} | {m['hot']['reynolds']:.0f} / {m['cold']['reynolds']:.0f} |"
        )
    warn = sorted({w for e in ranked for w in e.warnings})
    uncal = sorted({u for e in ranked for u in e.uncalibrated})
    lines += ["", "## Caveats carried by these designs", ""] + [f"- {w}" for w in warn]
    if uncal:
        lines += ["", "## Uncalibrated factors (still at 1.0)", ""] + [f"- `{u}`" for u in uncal]
    lines += [
        "",
        "## Next step",
        "",
        "Export the shortlist with `aces export-simlab` and verify on the rig. Feed the CFD results back "
        "into the calibration registry so the next run is calibrated.",
    ]
    return "\n".join(lines) + "\n"
