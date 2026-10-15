"""Markdown report for a radar design run.  States what is and is not verified."""
from __future__ import annotations

from typing import Any

SOURCED_INPUTS = (
    ("element_gain_db", "Element gain", "dBi"),
    ("element_pattern_exponent", "Element pattern exponent n (cos^n scan loss)", ""),
    ("antenna_noise_temperature_k", "Antenna noise temperature", "K"),
    ("system_losses_db", "System losses", "dB"),
    ("tx_line_loss_db", "TX line loss", "dB"),
    ("pa_efficiency", "PA efficiency", ""),
    ("duty_max", "PA rated duty", ""),
    ("support_dc_w_per_element", "Support electronics DC per element", "W"),
)

EXPORT_CONTROL = ("**Export control:** radar design data may be controlled under the DSGL (rule 12). Keep this "
                  "run folder on the LAN until the radar work has been classified.")


def _inputs_table(req: dict[str, Any]) -> list[str]:
    lines = ["## Inputs and their sources", "", "| Input | Value | Source |", "| --- | --- | --- |"]
    for key, label, units in SOURCED_INPUTS:
        v = req.get(key) or {}
        lines.append(f"| {label} | {v.get('value')} {units} | {v.get('source', 'MISSING')} |".replace("  |", " |"))
    for s in req.get("rx_chain") or ():
        p1 = f", OP1dB {s['op1db_dbm']} dBm" if s.get("op1db_dbm") is not None else ""
        lines.append(f"| RX stage {s.get('name')} | G {s.get('gain_db')} dB, NF {s.get('nf_db')} dB{p1} | "
                     f"{s.get('source')} |")
    det = req.get("detection") or {}
    lines += [
        (f"| Detection | Pd {det.get('pd', 0.9)}, Pfa {det.get('pfa', 1e-6)}, {det.get('cpi_pulses', 1)}-pulse CPI, "
         f"{det.get('integration')} | requirement |"),
        f"| Manufacturing route | {(req.get('manufacturing') or {}).get('process')} | aces.manufacturing |",
        "",
    ]
    return lines


def radar_report(spec: dict, result, ledger_hash: str | None, model_id: str, model_version: str) -> str:
    req = spec.get("requirements") or {}
    lines = [
        f"# ACES design run: {spec['project']}",
        "",
        f"- Domain: `{spec['domain']}` (model {model_id} {model_version})",
        f"- Company: {spec['company']} - R&D activity: {spec['rd_activity']}",
        f"- Hypothesis: {spec['hypothesis']}",
        (f"- Evaluated designs: {len(result.distinct)} distinct; feasible: {len(result.feasible)}; "
         f"non-dominated feasible: {len(result.pareto)}"),
    ]
    if ledger_hash:
        lines.append(f"- Evidence record: `{ledger_hash}`")
    lines += [
        "",
        ("**Status: fast-tier ranking only. Not verified.** Every design below must be checked on the EM solver "
         "(`aces-tile-em`, once registered) and on the bench before it is quoted, built or sold."),
        "",
        EXPORT_CONTROL,
        "",
    ]
    lines += _inputs_table(req)
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
        # only the caveats every design carries; per-design warnings are in evaluations.jsonl
        common = set.intersection(*(set(e.warnings) for e in result.evaluations)) if result.evaluations else set()
        lines += ["", "## Caveats carried by every design", ""] + [f"- {w}" for w in sorted(common)]
        return "\n".join(lines) + "\n"

    lines += [
        "## Non-dominated feasible designs (longest scan-edge range first)",
        "",
        ("| # | elements | pitch (wavelengths) | taper | phase bits | aperture mm | pulse us | PRF kHz | duty | "
         "peak W | EIRP dBW | range km (broadside / scan edge) | blind m | resolution m | W/cm2 | DC kW |"),
        "| - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - |",
    ]
    ranked = sorted(result.pareto, key=lambda e: e.objectives["neg_detection_range_km"])
    for i, e in enumerate(ranked):
        p, m = e.params, e.metrics
        lines.append(
            f"| {i} | {m['n_elements']} | {p['pitch_lambda']:.3f} | {p['taper']} | {p['phase_bits']} | "
            f"{m['aperture_x_mm']} x {m['aperture_y_mm']} | {p['pulse_width_s'] * 1e6:.1f} | {p['prf_hz'] / 1e3:.2f} | "
            f"{m['duty']:.3f} | {m['peak_power_w']:.0f} | {m['eirp_dbw']} | {m['detection_range_km']} / "
            f"{m['detection_range_scan_edge_km']} | {m['blind_range_m']:.0f} | {m['range_resolution_m']} | "
            f"{m['dissipation_w_cm2']} | {m['dc_power_w'] / 1e3:.2f} |"
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
        ("Verify the shortlist on the EM tier (`aces-tile-em`) once its image passes its validation cases, and fit "
         "the radar factors from those rows with `aces radar-calibrate` so the next run is calibrated."),
    ]
    return "\n".join(lines) + "\n"
