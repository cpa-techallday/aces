"""Manufacturing advisor and cost estimator (decision D24, proposed).

Two questions a design sprint customer always asks next: "how do I make it?"
and "what does it cost at my volume?".  This module answers both from the same
part profile:

- ``suggest`` ranks feasible manufacturing routes for a part and says why the
  rest were excluded (the Xometry lesson: an automatic, explained
  recommendation at first contact is what converts new users).
- ``estimate`` prices one route at one quantity with an activity-based
  breakdown (material, machine time, labour, setup, tooling, post-processing).
- ``curve`` prices a route across quantities, so crossovers between routes
  (print one, cast ten thousand) are visible instead of asserted.

Every number carries ``low``/``high`` around the central estimate.  The bands
are deliberately wide at baseline: they are assumptions, not quotes, and they
narrow only when real bureau quotes and invoices are folded back through a
cost calibration file (same philosophy as the physics calibration registry).
All money is AUD, 2026 baseline, ex-GST.

A part profile can be written by hand (YAML, see ``PartSpec``) or pulled
straight from an ``aces design`` run's pareto.json, which already carries
mass, core volume, envelope and minimum wall for each candidate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# ----------------------------------------------------------------------------- materials
#
# Feedstock prices are 2026 Australian small-quantity assumptions (A$/kg),
# flagged ``baseline`` until replaced by supplier quotes.  Each material lists
# the feedstock forms each process buys.


@dataclass(frozen=True)
class Material:
    id: str
    name: str
    density_kg_m3: float
    # process id -> A$/kg of feedstock in the form that process consumes
    feedstock_aud_kg: dict[str, float]
    max_temp_C: float  # continuous service, rules out polymers for hot duty
    source: str = "baseline assumption; replace with supplier quote"


MATERIALS: dict[str, Material] = {
    m.id: m
    for m in (
        Material("alsi10mg", "AlSi10Mg (LPBF aluminium)", 2670.0,
                 {"lpbf": 95.0, "casting": 12.0}, 250.0),
        Material("al6061", "Aluminium 6061 (billet/plate)", 2700.0,
                 {"cnc_machining": 14.0}, 200.0),
        Material("316l", "Stainless 316L", 7990.0,
                 {"lpbf": 65.0, "cnc_machining": 9.0, "casting": 8.0,
                  "arc_lattice_rod_welding": 11.0}, 600.0),
        Material("ti64", "Ti-6Al-4V", 4430.0,
                 {"lpbf": 230.0, "cnc_machining": 60.0}, 400.0),
        Material("in718", "Inconel 718", 8190.0,
                 {"lpbf": 160.0, "cnc_machining": 55.0}, 700.0),
        Material("cucrzr", "CuCrZr (high-conductivity copper)", 8890.0,
                 {"lpbf": 130.0, "cnc_machining": 28.0}, 500.0),
        Material("abs", "ABS (filament/pellet)", 1050.0,
                 {"polymer_extrusion": 45.0, "robotic_lattice_extrusion": 18.0,
                  "printed_formwork": 45.0}, 90.0),
        Material("cf_pa", "Carbon-fibre nylon (CF-PA)", 1200.0,
                 {"polymer_extrusion": 130.0, "robotic_lattice_extrusion": 60.0,
                  "printed_formwork": 130.0}, 150.0),
        Material("petg", "PETG", 1270.0,
                 {"polymer_extrusion": 40.0, "robotic_lattice_extrusion": 12.0,
                  "printed_formwork": 40.0}, 80.0),
    )
}


# ----------------------------------------------------------------------------- process cost models


@dataclass(frozen=True)
class ProcessCost:
    """Activity-based cost model for one manufacturing route.

    Build time is modelled as ``time_h = fixed_h + per_cm3 * volume_cm3 +
    per_kg * mass_kg`` per part, with ``pack`` parts sharing one machine run
    for bed/chamber processes.  Setup and tooling are per-job costs amortised
    over the batch; tooling also wears out after ``tooling_life`` parts.
    """
    id: str
    # capacity / compatibility
    max_envelope_m: tuple[float, float, float]
    min_feature_m: float
    materials: tuple[str, ...]
    max_quantity_sane: int  # above this the route is technically possible but rarely sensible
    # rates (AUD, 2026 baseline)
    machine_rate_aud_h: float
    labour_rate_aud_h: float
    setup_h: float
    # per-part build time model
    build_fixed_h: float
    build_per_cm3_h: float
    build_per_kg_h: float
    pack: int  # typical parts per machine run at reference part size (100 cm3); scaled by volume
    # material yield: fraction of feedstock that ends up in the part (rest is support/swarf/melt loss)
    material_yield: float
    # post-processing: hours per part + consumables as a fraction of material cost
    post_h_per_part: float
    post_consumable_frac: float
    # tooling
    tooling_aud: float
    tooling_life: int
    # quality
    scrap_frac: float
    # uncertainty (half-width on the central estimate) until calibrated
    uncertainty_frac: float
    notes: str = ""


PROCESSES: dict[str, ProcessCost] = {
    p.id: p
    for p in (
        ProcessCost(
            "lpbf",
            max_envelope_m=(0.40, 0.40, 0.40), min_feature_m=0.0003,
            materials=("alsi10mg", "316l", "ti64", "in718", "cucrzr"),
            max_quantity_sane=500,
            machine_rate_aud_h=95.0, labour_rate_aud_h=85.0, setup_h=4.0,
            build_fixed_h=2.0, build_per_cm3_h=0.045, build_per_kg_h=0.0,
            pack=8, material_yield=0.55,
            post_h_per_part=1.5, post_consumable_frac=0.10,
            tooling_aud=0.0, tooling_life=10**9,
            scrap_frac=0.08, uncertainty_frac=0.35,
            notes="Bureau print + depowder + stress relief + support removal; "
                  "HIP and interface machining quoted per job and not included.",
        ),
        ProcessCost(
            "polymer_extrusion",
            max_envelope_m=(0.30, 0.30, 0.30), min_feature_m=0.0008,
            materials=("abs", "cf_pa", "petg"),
            max_quantity_sane=200,
            machine_rate_aud_h=6.0, labour_rate_aud_h=70.0, setup_h=0.5,
            build_fixed_h=0.3, build_per_cm3_h=0.05, build_per_kg_h=0.0,
            pack=4, material_yield=0.85,
            post_h_per_part=0.3, post_consumable_frac=0.02,
            tooling_aud=0.0, tooling_life=10**9,
            scrap_frac=0.10, uncertainty_frac=0.30,
            notes="On-site Raise3D class machine; low-temperature, low-pressure duty only.",
        ),
        ProcessCost(
            "robotic_lattice_extrusion",
            max_envelope_m=(3.0, 3.0, 2.5), min_feature_m=0.004,
            materials=("abs", "cf_pa", "petg"),
            max_quantity_sane=100,
            machine_rate_aud_h=25.0, labour_rate_aud_h=85.0, setup_h=3.0,
            build_fixed_h=1.0, build_per_cm3_h=0.02, build_per_kg_h=0.0,
            pack=1, material_yield=0.95,
            post_h_per_part=0.5, post_consumable_frac=0.02,
            tooling_aud=0.0, tooling_life=10**9,
            scrap_frac=0.05, uncertainty_frac=0.40,
            notes="HC10DTP cell via formgen; build time should come from the form run's own estimate "
                  "when one exists.",
        ),
        ProcessCost(
            "arc_lattice_rod_welding",
            max_envelope_m=(3.5, 2.5, 2.5), min_feature_m=0.003,
            materials=("316l",),
            max_quantity_sane=50,
            machine_rate_aud_h=40.0, labour_rate_aud_h=95.0, setup_h=8.0,
            build_fixed_h=2.0, build_per_cm3_h=0.0, build_per_kg_h=0.9,
            pack=1, material_yield=0.90,
            post_h_per_part=4.0, post_consumable_frac=0.05,
            tooling_aud=2500.0, tooling_life=200,  # fixtures
            scrap_frac=0.05, uncertainty_frac=0.45,
            notes="Rod route; weld hours from the rod run's build plan when available. "
                  "Fatigue of welded nodes is unassessed - coupon tests before any customer part.",
        ),
        ProcessCost(
            "printed_formwork",
            max_envelope_m=(3.0, 3.0, 2.5), min_feature_m=0.004,
            materials=("abs", "cf_pa", "petg"),
            max_quantity_sane=500,
            machine_rate_aud_h=25.0, labour_rate_aud_h=85.0, setup_h=3.0,
            build_fixed_h=1.0, build_per_cm3_h=0.02, build_per_kg_h=0.0,
            pack=1, material_yield=0.95,
            post_h_per_part=3.0, post_consumable_frac=0.30,  # fill, coating, curing labour
            tooling_aud=0.0, tooling_life=10**9,
            scrap_frac=0.05, uncertainty_frac=0.45,
            notes="Formwork print plus foam/concrete/render fill; fill material priced as consumable.",
        ),
        ProcessCost(
            "cnc_machining",
            max_envelope_m=(1.0, 0.6, 0.5), min_feature_m=0.0005,
            materials=("al6061", "316l", "ti64", "in718", "cucrzr"),
            max_quantity_sane=10_000,
            machine_rate_aud_h=110.0, labour_rate_aud_h=95.0, setup_h=6.0,
            build_fixed_h=0.5, build_per_cm3_h=0.004, build_per_kg_h=0.35,
            pack=1, material_yield=0.40,  # buys billet, chips the rest
            post_h_per_part=0.4, post_consumable_frac=0.03,
            tooling_aud=800.0, tooling_life=500,  # soft jaws, fixtures
            scrap_frac=0.03, uncertainty_frac=0.30,
            notes="3-axis jobbing-shop baseline; machining time scales with part mass as a proxy for "
                  "material removed. Needs machinable geometry - TPMS cores are not machinable.",
        ),
        ProcessCost(
            "casting",
            max_envelope_m=(1.5, 1.0, 1.0), min_feature_m=0.003,
            materials=("alsi10mg", "316l"),
            max_quantity_sane=100_000,
            machine_rate_aud_h=0.0, labour_rate_aud_h=85.0, setup_h=4.0,
            build_fixed_h=0.0, build_per_cm3_h=0.0, build_per_kg_h=0.5,  # pour/fettle labour per kg
            pack=1, material_yield=0.60,  # gating and risers are remelted but cost energy/labour
            post_h_per_part=0.8, post_consumable_frac=0.15,
            tooling_aud=9000.0, tooling_life=5_000,  # printed pattern + mould gear, amortised
            scrap_frac=0.10, uncertainty_frac=0.50,
            notes="Sand/investment with printed pattern; tooling dominates below a few hundred parts.",
        ),
    )
}


# ----------------------------------------------------------------------------- part profile


@dataclass(frozen=True)
class PartProfile:
    """What the advisor and estimator need to know about a part."""
    name: str
    envelope_m: tuple[float, float, float]
    volume_m3: float          # solid (feedstock-becomes-part) volume
    mass_kg: float
    min_feature_m: float
    max_temp_C: float = 25.0
    pressure_bar: float = 1.0
    tolerance_mm: float = 0.3
    complex_internal: bool = True   # internal channels/lattices: rules out subtractive for the core
    application: str = "industrial"  # industrial | aerospace | defence - scales QA allowance
    source: str = "hand-written profile"

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "PartProfile":
        env = d.get("envelope_m") or [d["length_m"], d["width_m"], d["height_m"]]
        vol = float(d.get("volume_m3") or d.get("core_volume_m3"))
        mass = float(d.get("mass_kg") or 0.0)
        if mass <= 0.0:
            raise ValueError("part profile needs mass_kg (or supply it via --from-run)")
        return PartProfile(
            name=str(d.get("name") or "part"),
            envelope_m=tuple(float(x) for x in env),
            volume_m3=vol,
            mass_kg=mass,
            min_feature_m=float(d.get("min_feature_m") or d.get("wall_min_m") or 0.001),
            max_temp_C=float(d.get("max_temp_C", 25.0)),
            pressure_bar=float(d.get("pressure_bar", 1.0)),
            tolerance_mm=float(d.get("tolerance_mm", 0.3)),
            complex_internal=bool(d.get("complex_internal", True)),
            application=str(d.get("application", "industrial")),
            source=str(d.get("source", "hand-written profile")),
        )

    @staticmethod
    def from_pareto(entry: dict[str, Any], name: str) -> "PartProfile":
        """Profile from one pareto.json entry of an ``aces design`` run (hx.two_fluid)."""
        p, m = entry["params"], entry["metrics"]
        return PartProfile.from_dict({
            "name": name,
            "envelope_m": [p["length_m"], p["width_m"], p["height_m"]],
            "volume_m3": m["core_volume_m3"] * m["solid_fraction"],
            "mass_kg": m["mass_kg"],
            "min_feature_m": m["wall_min_m"],
            "complex_internal": True,  # TPMS cores are never machinable internally
            "source": "aces design run pareto entry",
        })


QA_FACTOR = {"industrial": 1.0, "aerospace": 1.35, "defence": 1.5}


# ----------------------------------------------------------------------------- estimation


@dataclass(frozen=True)
class Estimate:
    process: str
    material: str
    quantity: int
    unit_aud: float
    unit_low_aud: float
    unit_high_aud: float
    batch_aud: float
    breakdown: dict[str, float]  # per-unit AUD by activity
    assumptions: tuple[str, ...] = field(default=())


def _build_hours(pc: ProcessCost, part: PartProfile) -> float:
    cm3 = part.volume_m3 * 1e6
    per_part = pc.build_fixed_h + pc.build_per_cm3_h * cm3 + pc.build_per_kg_h * part.mass_kg
    if pc.pack > 1:
        # bed/chamber processes: parts share a run; packing density scales with part size
        parts_per_run = max(1, min(pc.pack, int(pc.pack * (100.0 / max(cm3, 1.0)) ** 0.5)))
        return per_part / parts_per_run
    return per_part


def estimate(part: PartProfile, process_id: str, material_id: str, quantity: int) -> Estimate:
    pc, mat = PROCESSES[process_id], MATERIALS[material_id]
    if material_id not in pc.materials:
        raise ValueError(f"{material_id} is not available for {process_id}; choose from {pc.materials}")
    if quantity < 1:
        raise ValueError("quantity must be >= 1")
    feed = mat.feedstock_aud_kg.get(process_id)
    if feed is None:
        raise ValueError(f"no {process_id} feedstock price for {material_id}; add it to MATERIALS")

    q = float(quantity)
    build_h = _build_hours(pc, part)
    material = part.mass_kg / pc.material_yield * feed
    machine = build_h * pc.machine_rate_aud_h
    post = pc.post_h_per_part * pc.labour_rate_aud_h + pc.post_consumable_frac * material
    setup = pc.setup_h * (pc.machine_rate_aud_h + pc.labour_rate_aud_h) / q
    tooling = pc.tooling_aud / min(q, float(pc.tooling_life)) if pc.tooling_aud else 0.0
    direct = material + machine + post + setup + tooling
    unit = direct * (1.0 + pc.scrap_frac) * QA_FACTOR.get(part.application, 1.25)
    band = pc.uncertainty_frac
    assumptions = [
        "AUD 2026 baseline rates; material and machine rates are assumptions until calibrated to quotes",
        f"build time model: {build_h:.2f} h/part ({pc.notes})" if pc.notes else f"build time {build_h:.2f} h/part",
        f"QA/application factor {QA_FACTOR.get(part.application, 1.25):.2f} for {part.application}",
    ]
    return Estimate(
        process=process_id, material=material_id, quantity=quantity,
        unit_aud=unit, unit_low_aud=unit * (1 - band), unit_high_aud=unit * (1 + band),
        batch_aud=unit * q,
        breakdown={"material": material, "machine": machine, "post_processing": post,
                   "setup_amortised": setup, "tooling_amortised": tooling,
                   "scrap_and_qa": unit - direct},
        assumptions=tuple(assumptions),
    )


def curve(part: PartProfile, process_id: str, material_id: str,
          quantities: tuple[int, ...] = (1, 5, 10, 25, 50, 100, 250, 500, 1_000, 5_000, 10_000)
          ) -> list[Estimate]:
    return [estimate(part, process_id, material_id, q) for q in quantities]


# ----------------------------------------------------------------------------- advisor


@dataclass(frozen=True)
class Suggestion:
    process: str
    material: str
    feasible: bool
    unit_aud_at_target: float | None
    rank_score: float  # lower is better; feasible only
    reasons: tuple[str, ...]


def _exclusions(pc: ProcessCost, part: PartProfile, quantity: int) -> list[str]:
    out = []
    env_ok = all(e <= lim for e, lim in zip(sorted(part.envelope_m), sorted(pc.max_envelope_m)))
    if not env_ok:
        out.append(f"envelope {part.envelope_m} exceeds {pc.max_envelope_m}")
    if part.min_feature_m < pc.min_feature_m:
        out.append(f"min feature {part.min_feature_m * 1e3:.2f} mm below process limit "
                   f"{pc.min_feature_m * 1e3:.2f} mm")
    if quantity > pc.max_quantity_sane:
        out.append(f"quantity {quantity} above the sensible range for {pc.id} "
                   f"(>{pc.max_quantity_sane})")
    if pc.id == "cnc_machining" and part.complex_internal:
        out.append("internal channels/lattices are not machinable; CNC can only do housings and plates")
    if pc.id == "casting" and part.min_feature_m < 0.001:
        out.append("sub-mm walls are not castable")
    if part.tolerance_mm < 0.1 and pc.id in ("polymer_extrusion", "robotic_lattice_extrusion",
                                             "printed_formwork", "casting"):
        out.append(f"tolerance {part.tolerance_mm} mm tighter than {pc.id} holds without machining")
    if part.pressure_bar > 20 and pc.id in ("polymer_extrusion", "printed_formwork",
                                            "robotic_lattice_extrusion"):
        out.append(f"{part.pressure_bar:.0f} bar duty needs a fully dense metal route")
    return out


def suggest(part: PartProfile, quantity: int,
            preferred_materials: tuple[str, ...] | None = None) -> list[Suggestion]:
    """Rank (process, material) pairs for a part at a target quantity.

    Feasible pairs are ranked by central unit cost; excluded pairs are listed
    with the reasons, because "why not" is the DFM feedback a customer reads.
    """
    mats = preferred_materials or tuple(MATERIALS)
    out: list[Suggestion] = []
    for pc in PROCESSES.values():
        for mid in mats:
            mat = MATERIALS[mid]
            reasons = _exclusions(pc, part, quantity)
            if mid not in pc.materials:
                reasons.append(f"{mat.name} is not offered on {pc.id}")
            if mat.max_temp_C < part.max_temp_C:
                reasons.append(f"{mat.name} service limit {mat.max_temp_C:.0f} C < "
                               f"part duty {part.max_temp_C:.0f} C")
            if pc.id in ("polymer_extrusion",) and part.max_temp_C > 80:
                reasons.append("polymer extrusion is prototyping/low-duty only at this temperature")
            if reasons:
                out.append(Suggestion(pc.id, mid, False, None, math.inf, tuple(reasons)))
                continue
            est = estimate(part, pc.id, mid, quantity)
            out.append(Suggestion(pc.id, mid, True, est.unit_aud, est.unit_aud,
                                  (f"unit cost A${est.unit_aud:,.0f} (A${est.unit_low_aud:,.0f}-"
                                   f"{est.unit_high_aud:,.0f}) at qty {quantity}",)))
    return sorted(out, key=lambda s: (not s.feasible, s.rank_score))


def crossovers(part: PartProfile, pairs: tuple[tuple[str, str], ...],
               quantities: tuple[int, ...] = (1, 5, 10, 25, 50, 100, 250, 500, 1_000, 5_000, 10_000)
               ) -> dict[str, Any]:
    """Per-unit curves for each (process, material) pair and where the lead changes."""
    curves: dict[str, list[float]] = {}
    for pid, mid in pairs:
        try:
            curves[f"{pid}/{mid}"] = [e.unit_aud for e in curve(part, pid, mid, quantities)]
        except ValueError:
            continue
    leaders, prev = [], None
    for i, q in enumerate(quantities):
        if not curves:
            break
        lead = min(curves, key=lambda k: curves[k][i])
        if lead != prev:
            leaders.append({"quantity": q, "cheapest": lead, "unit_aud": curves[lead][i]})
            prev = lead
    return {"quantities": list(quantities), "curves": curves, "leaders": leaders}
