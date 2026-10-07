"""Manufacturing advisor and cost estimator (decision D24, proposed; audit H2 fixes, 2026-10-07).

Estimate only: not a quote, not verified.

Two questions a design sprint customer asks next: "how do I make it?" and "what does it cost at my
volume?".  This module answers both from one part profile and one rates file:

- ``suggest`` ranks the routes ACES can make today (``available`` in ``aces.manufacturing``) for a part and
  says why the rest were excluded.  Planned routes are listed as planned and never recommended.
- ``estimate`` prices one route and material at one quantity, with an activity-based breakdown.
- ``curve`` and ``crossovers`` price routes across quantities, each stopping at its route's quantity limit.

Inputs
------
* A part profile (``PartProfile``): bounding box, solid volume, smallest feature, duty temperature and
  pressure.  Written by hand (YAML ``part:`` block) or taken from a verified ``aces design`` run.  Mass is
  never an input: it is the solid volume times each material's density.
* A rates file (``load_rates``, YAML or JSON): every rate, build volume and material property the estimate
  uses, each as ``{value, source, date}`` where the source is a quote, invoice or datasheet.  ACES ships no
  rates, prices or material properties (rules 1 and 2).  Anything the file lacks is reported as missing,
  never defaulted.  The shape is meant to be extended by the cost-calibration registry (D25), which can add
  keys such as the quotes behind a value without changing what is read here.

Cost model (per part, for one route and material at quantity q)
---------------------------------------------------------------
::

    mass m        = solid volume x density
    parts/build n = 1; for additive (plate) routes, the most parts that fit side by side on the plate in one
                    layer, best axis-aligned orientation, with part_gap_m between them
    builds b      = ceil(q / n)
    build hours H = b x build_h_per_build + q x (build_h_per_cm3 x volume_cm3 + build_h_per_kg x m)
    machine       = machine_aud_h x H / q
    build labour  = labour_aud_h x attended_fraction x H / q
    material      = m / material_yield x feedstock price
    post          = post_h_per_part x labour_aud_h + post_consumable_frac x material
    setup         = setup_h x (machine_aud_h + labour_aud_h) / q          (one setup per job)
    tooling       = ceil(q / tooling_life) x tooling_aud / q                (whole tooling sets)
    unit          = (sum of the above) x (1 + scrap_frac) x qa_factor[application]
    band          = unit x (1 -/+ uncertainty_frac)

Scope: build time ignores orientation, build height and supports; parts are not stacked; scrap is applied to
every direct cost.  Lead times (D26) are not modelled.
"""
from __future__ import annotations

import datetime as _dt
import itertools
import json
import math
import numbers
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import manufacturing

DISCLAIMER = "Estimate only: not a quote, not verified."
DEFAULT_QUANTITIES = (1, 5, 10, 25, 50, 100, 250, 500, 1_000, 5_000, 10_000)

# Exclusion margins: ACES policy, not taken from a standard (Aaron to confirm).  A material is offered for a
# part only if its sourced service temperature is at least the duty temperature plus TEMP_MARGIN_K, and its
# sourced service pressure at least PRESSURE_FACTOR times the duty pressure.
TEMP_MARGIN_K = 10.0
PRESSURE_FACTOR = 1.5
MARGINS_TEXT = (f"materials need a sourced service temperature of at least duty + {TEMP_MARGIN_K:g} K and a "
                f"sourced service pressure of at least {PRESSURE_FACTOR:g} x duty pressure (ACES policy margins)")

RATES_FORMAT_KEY = "aces_cost_rates"
RATES_MAX_BYTES = 1 << 20
NESTED_FAMILIES = ("additive",)  # plate routes: several parts can share one build

# Every route needs these in the rates file: name -> (check, what it is, with units).
PROCESS_FIELDS: dict[str, tuple[str, str]] = {
    "build_volume_m": ("vec3", "largest part the machine or cell builds, [x, y, z] m"),
    "part_gap_m": ("nonneg", "gap between parts nested on one plate, m (additive routes only)"),
    "min_feature_m": ("positive", "smallest wall or feature the route holds, m"),
    "tolerance_mm": ("positive", "as-built tolerance the route holds, mm"),
    "max_quantity": ("count", "largest batch the route is costed for, parts"),
    "machine_aud_h": ("nonneg", "machine rate, currency/h"),
    "labour_aud_h": ("nonneg", "labour rate, currency/h"),
    "attended_fraction": ("unit", "share of build hours an operator attends, 0-1"),
    "setup_h": ("nonneg", "setup hours per job (machine and operator)"),
    "build_h_per_build": ("nonneg", "fixed hours per build (a plate, or one part on single-part routes)"),
    "build_h_per_cm3": ("nonneg", "build hours per cm3 of solid part"),
    "build_h_per_kg": ("nonneg", "build hours per kg of part"),
    "material_yield": ("yield", "share of bought feedstock that ends up in the part, 0-1"),
    "post_h_per_part": ("nonneg", "post-processing labour hours per part"),
    "post_consumable_frac": ("nonneg", "post-processing consumables as a share of material cost"),
    "tooling_aud": ("nonneg", "cost of one tooling or fixture set (0 if none)"),
    "tooling_life": ("count", "parts one tooling set makes"),
    "scrap_frac": ("frac", "scrap allowance, 0-1"),
    "uncertainty_frac": ("frac", "half-width of the cost band, 0-1"),
}
MATERIAL_FIELDS: dict[str, tuple[str, str]] = {
    "density_kg_m3": ("positive", "density, kg/m3"),
    "max_service_temp_C": ("finite", "highest continuous service temperature, deg C"),
    "max_service_pressure_bar": ("nonneg", "pressure rating for parts in this material, bar"),
}


class CostInputError(ValueError):
    """An input to the estimator is missing or invalid; ``problems`` lists each one."""

    def __init__(self, problems: str | list[str]):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        if len(self.problems) == 1:
            msg = self.problems[0]
        else:
            msg = "cost inputs are incomplete or invalid:\n  - " + "\n  - ".join(self.problems)
        super().__init__(msg)


# ----------------------------------------------------------------------------- sourced values


def _is_num(x: Any) -> bool:
    return isinstance(x, numbers.Real) and not isinstance(x, bool) and math.isfinite(float(x))


def _fmt(v: Any) -> str:
    if isinstance(v, tuple | list):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _check(kind: str, v: Any) -> tuple[Any, str | None]:
    """(normalised value, problem or None) for one value of the given kind."""
    if kind == "vec3":
        if not isinstance(v, list | tuple) or len(v) != 3 or not all(_is_num(x) and x > 0 for x in v):
            return None, "must be a list of three positive lengths"
        return tuple(float(x) for x in v), None
    if kind == "count":
        if isinstance(v, bool) or not isinstance(v, numbers.Integral) or v < 1:
            return None, "must be a whole number of at least 1"
        return int(v), None
    if not _is_num(v):
        return None, "must be a finite number"
    x = float(v)
    ok = {"positive": x > 0, "nonneg": x >= 0, "unit": 0 <= x <= 1, "yield": 0 < x <= 1,
          "frac": 0 <= x < 1, "finite": True}[kind]
    limits = {"positive": "must be above 0", "nonneg": "must not be negative", "unit": "must be between 0 and 1",
              "yield": "must be above 0 and at most 1", "frac": "must be at least 0 and below 1"}
    return (x, None) if ok else (None, limits.get(kind, "is out of range"))


@dataclass(frozen=True)
class Sourced:
    """One input value with the quote, invoice or datasheet it came from, and its date."""
    key: str
    value: Any
    source: str
    date: str

    def as_dict(self) -> dict[str, Any]:
        return {"value": list(self.value) if isinstance(self.value, tuple) else self.value,
                "source": self.source, "date": self.date}

    def cite(self) -> str:
        return format_source(self.key, self.as_dict())


def format_source(key: str, entry: dict[str, Any]) -> str:
    """``key = value (source; date)`` for one entry of ``Estimate.rates_used``."""
    return f"{key} = {_fmt(entry['value'])} ({entry['source']}; {entry['date']})"


def _sourced(key: str, raw: Any, kind: str, problems: list[str]) -> Sourced | None:
    if not isinstance(raw, dict):
        problems.append(f"{key}: give {{value, source, date}} (the quote, invoice or datasheet and its date)")
        return None
    lacking = [k for k in ("value", "source", "date") if raw.get(k) in (None, "")]
    if lacking:
        problems.append(f"{key}: missing {', '.join(lacking)}")
        return None
    source = str(raw["source"]).strip()
    if len(source) < 3:
        problems.append(f"{key}: source must name the quote, invoice or datasheet")
        return None
    date = raw["date"]
    if isinstance(date, _dt.datetime):
        date = date.date()
    if not isinstance(date, _dt.date):
        try:
            date = _dt.date.fromisoformat(str(date))
        except ValueError:
            problems.append(f"{key}: date must be YYYY-MM-DD, got {raw['date']!r}")
            return None
    value, why = _check(kind, raw["value"])
    if why:
        problems.append(f"{key}: value {why} (got {raw['value']!r})")
        return None
    return Sourced(key, value, source, date.isoformat())


# ----------------------------------------------------------------------------- rates file


@dataclass(frozen=True)
class Material:
    """A material as the rates file gives it: sourced properties and feedstock prices by route."""
    id: str
    name: str
    props: dict[str, Sourced]
    feedstock_aud_kg: dict[str, Sourced]  # route id -> price per kg of the feedstock that route buys

    def missing(self) -> list[str]:
        return [k for k in MATERIAL_FIELDS if k not in self.props]


@dataclass(frozen=True)
class ProcessCost:
    """The sourced rates and limits for one manufacturing route."""
    id: str
    rates: dict[str, Sourced]

    @property
    def nested(self) -> bool:
        return manufacturing.get(self.id).family in NESTED_FAMILIES

    def required(self) -> tuple[str, ...]:
        return tuple(k for k in PROCESS_FIELDS if k != "part_gap_m" or self.nested)

    def missing(self) -> list[str]:
        return [k for k in self.required() if k not in self.rates]

    def v(self, name: str) -> Any:
        return self.rates[name].value


@dataclass(frozen=True)
class Rates:
    origin: str
    currency: str
    qa_factor: dict[str, Sourced]
    materials: dict[str, Material]
    processes: dict[str, ProcessCost]
    basis: str = ""

    def process(self, process_id: str) -> ProcessCost:
        if process_id not in manufacturing.PROCESSES:
            raise CostInputError(f"unknown manufacturing route {process_id!r}; known: {sorted(manufacturing.PROCESSES)}")
        return self.processes.get(process_id) or ProcessCost(process_id, {})

    def material(self, material_id: str) -> Material:
        try:
            return self.materials[material_id]
        except KeyError:
            raise CostInputError(f"material {material_id!r} is not in the rates file {self.origin}; "
                                 f"it has {sorted(self.materials) or 'no materials'}") from None

    def missing(self) -> list[str]:
        """What the rates file lacks for the routes ACES can make today and for its materials."""
        out = []
        for pid, proc in manufacturing.PROCESSES.items():
            if proc.status != "available":
                continue
            lacking = self.process(pid).missing()
            if lacking:
                out.append(f"processes.{pid}: " + ", ".join(lacking))
        for mid, mat in self.materials.items():
            if mat.missing():
                out.append(f"materials.{mid}: " + ", ".join(mat.missing()))
            if not mat.feedstock_aud_kg:
                out.append(f"materials.{mid}.feedstock_aud_kg: no route prices")
        if not self.materials:
            out.append("materials: none given")
        if not self.qa_factor:
            out.append("qa_factor: none given (one per application, e.g. industrial)")
        return out


RATES_HELP = (
    "aces cost needs --rates FILE (YAML or JSON) with `aces_cost_rates: 1`, a currency, and every rate as\n"
    "{value, source, date}, where the source is a quote, invoice or datasheet.  ACES ships no rates.\n"
    "  qa_factor.<application>          e.g. industrial\n"
    "  materials.<id>: name, " + ", ".join(MATERIAL_FIELDS) + ",\n"
    "                  feedstock_aud_kg.<route>\n"
    "  processes.<route>: " + ", ".join(PROCESS_FIELDS) + "\n"
    "See tests/cost_rates_test_input.yaml for the shape (its values are test inputs, not rates)."
)


def rates_from_dict(d: Any, origin: str = "<rates>") -> Rates:
    """Validate a rates mapping.  Malformed entries are errors; absent entries are left missing."""
    if not isinstance(d, dict):
        raise CostInputError(f"{origin}: expected a mapping")
    problems: list[str] = []
    if d.get(RATES_FORMAT_KEY) != 1:
        problems.append(f"{origin}: not an ACES cost rates file (`{RATES_FORMAT_KEY}: 1` missing)")
    allowed = {RATES_FORMAT_KEY, "currency", "basis", "qa_factor", "materials", "processes", "notes"}
    problems += [f"unknown top-level key {k!r}" for k in d if k not in allowed]
    currency = d.get("currency")
    if not isinstance(currency, str) or not currency.strip():
        problems.append("currency: missing (e.g. AUD)")

    qa: dict[str, Sourced] = {}
    raw_qa = d.get("qa_factor") or {}
    if not isinstance(raw_qa, dict):
        problems.append("qa_factor: expected a mapping of application -> {value, source, date}")
        raw_qa = {}
    for app, raw in raw_qa.items():
        s = _sourced(f"qa_factor.{app}", raw, "positive", problems)
        if s:
            qa[str(app)] = s

    materials: dict[str, Material] = {}
    raw_mats = d.get("materials") or {}
    if not isinstance(raw_mats, dict):
        problems.append("materials: expected a mapping of material id -> entry")
        raw_mats = {}
    for mid, raw in raw_mats.items():
        key = f"materials.{mid}"
        if not isinstance(raw, dict):
            problems.append(f"{key}: expected a mapping")
            continue
        extra = set(raw) - set(MATERIAL_FIELDS) - {"name", "feedstock_aud_kg", "notes"}
        problems += [f"{key}: unknown key {k!r}" for k in sorted(extra)]
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            problems.append(f"{key}.name: missing")
            name = str(mid)
        props = {}
        for f, (kind, _) in MATERIAL_FIELDS.items():
            if f in raw:
                s = _sourced(f"{key}.{f}", raw[f], kind, problems)
                if s:
                    props[f] = s
        feeds = {}
        raw_feed = raw.get("feedstock_aud_kg") or {}
        if not isinstance(raw_feed, dict):
            problems.append(f"{key}.feedstock_aud_kg: expected a mapping of route -> {{value, source, date}}")
            raw_feed = {}
        for pid, rf in raw_feed.items():
            if pid not in manufacturing.PROCESSES:
                problems.append(f"{key}.feedstock_aud_kg: unknown route {pid!r}")
                continue
            s = _sourced(f"{key}.feedstock_aud_kg.{pid}", rf, "nonneg", problems)
            if s:
                feeds[pid] = s
        materials[str(mid)] = Material(str(mid), name, props, feeds)

    processes: dict[str, ProcessCost] = {}
    raw_procs = d.get("processes") or {}
    if not isinstance(raw_procs, dict):
        problems.append("processes: expected a mapping of route id -> entry")
        raw_procs = {}
    for pid, raw in raw_procs.items():
        key = f"processes.{pid}"
        if pid not in manufacturing.PROCESSES:
            problems.append(f"{key}: unknown route (aces processes lists them)")
            continue
        if not isinstance(raw, dict):
            problems.append(f"{key}: expected a mapping")
            continue
        problems += [f"{key}: unknown key {k!r}" for k in sorted(set(raw) - set(PROCESS_FIELDS) - {"notes"})]
        vals = {}
        for f, (kind, _) in PROCESS_FIELDS.items():
            if f in raw:
                s = _sourced(f"{key}.{f}", raw[f], kind, problems)
                if s:
                    vals[f] = s
        processes[str(pid)] = ProcessCost(str(pid), vals)

    if problems:
        raise CostInputError(problems)
    return Rates(origin, currency.strip(), qa, materials, processes, str(d.get("basis") or ""))


def parse_rates_text(text: str, origin: str) -> Rates:
    try:
        data = json.loads(text) if origin.lower().endswith(".json") else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError, ValueError) as e:  # ValueError: e.g. date 2026-13-01
        raise CostInputError(f"{origin}: not valid {'JSON' if origin.lower().endswith('.json') else 'YAML'}: "
                             f"{e}") from e
    return rates_from_dict(data, origin)


def load_rates(path: str | Path) -> Rates:
    """Read and validate a rates file (links and files over 1 MB are refused)."""
    from . import safeio

    try:
        text = safeio.read_text_limited(path, RATES_MAX_BYTES)
    except (OSError, ValueError) as e:
        raise CostInputError(f"cannot read rates file {path}: {e}") from e
    return parse_rates_text(text, str(path))


# ----------------------------------------------------------------------------- part profile

PART_REQUIRED = ("envelope_m", "volume_m3", "min_feature_m", "max_temp_C", "pressure_bar")
PART_OPTIONAL = ("name", "tolerance_mm", "complex_internal", "application", "domain", "source", "notes")


@dataclass(frozen=True)
class PartProfile:
    """What the advisor and estimator need to know about a part.

    ``envelope_m`` is the bounding box and ``volume_m3`` the solid volume (the feedstock that becomes part).
    ``max_temp_C`` and ``pressure_bar`` are the duty the material must cover.  ``tolerance_mm`` is optional:
    when it is not given, route tolerances are not checked and the output says so.
    """
    name: str
    envelope_m: tuple[float, float, float]
    volume_m3: float
    min_feature_m: float
    max_temp_C: float
    pressure_bar: float
    tolerance_mm: float | None = None
    complex_internal: bool = True   # internal channels/lattices: rules out subtractive routes
    application: str = "industrial"  # picks the QA factor from the rates file
    domain: str | None = None        # design model the part came from; only routes that make it are offered
    source: str = "hand-written profile"
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        problems = []
        if not isinstance(self.name, str) or not self.name.strip():
            problems.append("name must be a non-empty string")
        env = self.envelope_m
        env_ok = isinstance(env, list | tuple) and len(env) == 3 and all(_is_num(x) and x > 0 for x in env)
        if env_ok:
            object.__setattr__(self, "envelope_m", tuple(float(x) for x in env))
        else:
            problems.append(f"envelope_m must be three positive finite lengths in m, got {env!r}")
        if not (_is_num(self.volume_m3) and self.volume_m3 > 0):
            problems.append(f"volume_m3 must be a positive finite number, got {self.volume_m3!r}")
        elif env_ok and self.volume_m3 > math.prod(self.envelope_m) * (1 + 1e-9):
            problems.append(f"volume_m3 {self.volume_m3:g} is larger than its bounding box "
                            f"({math.prod(self.envelope_m):g} m3)")
        if not (_is_num(self.min_feature_m) and self.min_feature_m > 0):
            problems.append(f"min_feature_m must be a positive finite number, got {self.min_feature_m!r}")
        if not (_is_num(self.max_temp_C) and self.max_temp_C > -273.15):
            problems.append(f"max_temp_C must be a finite temperature in deg C, got {self.max_temp_C!r}")
        if not (_is_num(self.pressure_bar) and self.pressure_bar >= 0):
            problems.append(f"pressure_bar must be a finite number of at least 0, got {self.pressure_bar!r}")
        if self.tolerance_mm is not None and not (_is_num(self.tolerance_mm) and self.tolerance_mm > 0):
            problems.append(f"tolerance_mm must be a positive finite number, got {self.tolerance_mm!r}")
        if not isinstance(self.complex_internal, bool):
            problems.append(f"complex_internal must be true or false, got {self.complex_internal!r}")
        if not isinstance(self.application, str) or not self.application.strip():
            problems.append("application must be a non-empty string")
        if self.domain is not None and not isinstance(self.domain, str):
            problems.append("domain must be a design model id such as hx.two_fluid")
        object.__setattr__(self, "notes", tuple(str(n) for n in (self.notes or ())))
        if problems:
            raise CostInputError(problems)

    @property
    def volume_cm3(self) -> float:
        return self.volume_m3 * 1e6

    @staticmethod
    def from_dict(d: dict[str, Any]) -> PartProfile:
        if not isinstance(d, dict):
            raise CostInputError("part profile must be a mapping (a `part:` block)")
        problems = []
        if "mass_kg" in d:
            problems.append("mass_kg is not an input: mass is volume_m3 times each material's density from "
                            "the rates file; remove it")
        problems += [f"{k}: missing" for k in PART_REQUIRED if d.get(k) is None]
        problems += [f"unknown key {k!r} (allowed: {', '.join(PART_REQUIRED + PART_OPTIONAL)})"
                     for k in d if k not in PART_REQUIRED + PART_OPTIONAL + ("mass_kg",)]
        if problems:
            raise CostInputError(problems)
        return PartProfile(**{k: d[k] for k in PART_REQUIRED + PART_OPTIONAL if k in d and d[k] is not None})

    @staticmethod
    def from_pareto(entry: dict[str, Any], name: str, *, spec: dict[str, Any]) -> PartProfile:
        """Profile from one pareto.json entry of a verified ``aces design`` run (hx.two_fluid).

        ``spec`` is the run's spec from its evidence record: the duty temperature and pressure come from its
        fluid streams, so a hot or pressurised design cannot be costed as if it ran at room conditions.
        """
        try:
            p, m = entry["params"], entry["metrics"]
            req = spec["requirements"]
            hot, cold, mat, mfg = req["hot"], req["cold"], req["material"], req["manufacturing"]
            t_max_C = max(float(hot["inlet_temperature_K"]), float(cold["inlet_temperature_K"])) - 273.15
            p_bar = max(float(hot["inlet_pressure_Pa"]), float(cold["inlet_pressure_Pa"])) / 1e5
            envelope = [p["length_m"], p["width_m"], p["height_m"]]
            volume = float(m["core_volume_m3"]) * float(m["solid_fraction"])
            wall = m["wall_min_m"]
        except (KeyError, TypeError, ValueError) as e:
            raise CostInputError(f"design run entry or spec lacks {e} (not an hx.two_fluid run?)") from e
        notes = (
            "core only: headers, ports and manifolds are not in the volume or bounding box",
            (f"duty from the run's spec: hottest inlet {t_max_C:.1f} C; highest inlet pressure {p_bar:.2f} bar "
             "absolute, used as the duty pressure (conservative against gauge)"),
            (f"the design was sized for {mat.get('name')} ({mat.get('source')}) on {mfg.get('process')}; "
             "in another material its heat transfer and strength change: re-run aces design before relying on it"),
        )
        return PartProfile(
            name=name, envelope_m=tuple(envelope), volume_m3=volume, min_feature_m=wall,
            max_temp_C=t_max_C, pressure_bar=p_bar, tolerance_mm=None,
            complex_internal=True,  # TPMS cores are never machinable internally
            domain=spec.get("domain"), source="verified aces design run (pareto entry and evidence record)",
            notes=notes,
        )


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
    breakdown: dict[str, float]  # per-unit cost by activity
    assumptions: tuple[str, ...] = field(default=())
    mass_kg: float = 0.0
    parts_per_build: int = 1
    builds: int = 1
    status: str = "available"  # the route's status in aces.manufacturing
    rates_used: dict[str, dict[str, Any]] = field(default_factory=dict)  # key -> {value, source, date}


def check_quantity(quantity: Any) -> int:
    if isinstance(quantity, bool) or not isinstance(quantity, numbers.Integral) or quantity < 1:
        raise CostInputError(f"quantity must be a whole number of at least 1, got {quantity!r}")
    return int(quantity)


def check_quantities(quantities: Any) -> tuple[int, ...]:
    qs = tuple(quantities or ())
    if not qs:
        raise CostInputError("give at least one quantity")
    return tuple(sorted({check_quantity(q) for q in qs}))


def _fits(envelope: tuple[float, ...], volume: tuple[float, ...]) -> bool:
    return all(e <= v for e, v in zip(sorted(envelope), sorted(volume)))


def parts_per_build(part: PartProfile, pc: ProcessCost) -> int:
    """Parts per build: 1 on single-part routes; on plate routes, the most that fit in one layer.

    For each axis-aligned orientation whose height fits, n parts of width b fit across a plate of width B
    when n*b + (n-1)*gap <= B, so n = floor((B + gap) / (b + gap)).  0 means the part does not fit.
    """
    bv = pc.v("build_volume_m")
    if not pc.nested:
        return 1 if _fits(part.envelope_m, bv) else 0
    gap = pc.v("part_gap_m")
    best = 0
    for bx, by, bz in set(itertools.permutations(part.envelope_m)):
        if bz <= bv[2]:
            n = math.floor((bv[0] + gap) / (bx + gap) + 1e-9) * math.floor((bv[1] + gap) / (by + gap) + 1e-9)
            best = max(best, n)
    return best


def _inputs_problems(part: PartProfile, process_id: str, material_id: str, rates: Rates) -> list[str]:
    pc, mat = rates.process(process_id), rates.material(material_id)
    problems = [f"rates file lacks processes.{process_id}.{k} ({PROCESS_FIELDS[k][1]})" for k in pc.missing()]
    if "density_kg_m3" not in mat.props:
        problems.append(f"rates file lacks materials.{material_id}.density_kg_m3")
    if process_id not in mat.feedstock_aud_kg:
        problems.append(f"{material_id} is not offered on {process_id}: rates file has no "
                        f"materials.{material_id}.feedstock_aud_kg.{process_id}")
    if part.application not in rates.qa_factor:
        problems.append(f"rates file lacks qa_factor.{part.application}")
    return problems


def estimate(part: PartProfile, process_id: str, material_id: str, quantity: int, *, rates: Rates) -> Estimate:
    """Price one route and material at one quantity (see the module docstring for the arithmetic)."""
    q = check_quantity(quantity)
    problems = _inputs_problems(part, process_id, material_id, rates)  # also refuses unknown ids
    proc = manufacturing.get(process_id)
    if problems:
        raise CostInputError(problems)
    pc, mat = rates.process(process_id), rates.material(material_id)
    limit = pc.rates["max_quantity"]
    if q > limit.value:
        raise CostInputError(f"quantity {q} is above the {process_id} limit of {limit.value} parts ({limit.source})")
    n = parts_per_build(part, pc)
    if n < 1:
        raise CostInputError(f"part bounding box {part.envelope_m} m does not fit the {process_id} build volume "
                             f"{pc.v('build_volume_m')} m")
    density, feed, qa = mat.props["density_kg_m3"], mat.feedstock_aud_kg[process_id], rates.qa_factor[part.application]

    mass = part.volume_m3 * density.value
    builds = math.ceil(q / n)
    hours = builds * pc.v("build_h_per_build") + q * (pc.v("build_h_per_cm3") * part.volume_cm3
                                                      + pc.v("build_h_per_kg") * mass)
    machine = pc.v("machine_aud_h") * hours / q
    build_labour = pc.v("labour_aud_h") * pc.v("attended_fraction") * hours / q
    material = mass / pc.v("material_yield") * feed.value
    post = pc.v("post_h_per_part") * pc.v("labour_aud_h") + pc.v("post_consumable_frac") * material
    setup = pc.v("setup_h") * (pc.v("machine_aud_h") + pc.v("labour_aud_h")) / q
    tooling_sets = math.ceil(q / pc.v("tooling_life")) if pc.v("tooling_aud") > 0 else 0
    tooling = tooling_sets * pc.v("tooling_aud") / q
    direct = material + machine + build_labour + post + setup + tooling
    unit = direct * (1.0 + pc.v("scrap_frac")) * qa.value
    band = pc.v("uncertainty_frac")

    assumptions = []
    if proc.status != "available":
        assumptions.append(f"{process_id} is {proc.status} in aces.manufacturing, not available: costed for "
                           "comparison only and never recommended")
    assumptions += [
        (f"mass {mass:.4g} kg = solid volume {part.volume_m3:.4g} m3 x density {density.value:g} kg/m3 "
         f"({mat.name})"),
        f"{n} part(s) per build" + (" (one layer on the plate, best axis-aligned orientation)" if pc.nested else "")
        + f"; {builds} build(s) for {q} part(s); {hours:.4g} build hours in all",
        "build time is linear in volume and mass; it ignores orientation, build height and supports",
        "one setup per job" + (f"; {tooling_sets} tooling set(s) of {pc.v('tooling_life')} parts"
                               if tooling_sets else ""),
        f"QA/application factor {qa.value:g} for {part.application}; scrap {pc.v('scrap_frac'):g} on all direct costs",
    ]
    used = {s.key: s.as_dict() for s in [pc.rates[k] for k in pc.required()] + [density, feed, qa]}
    return Estimate(
        process=process_id, material=material_id, quantity=q,
        unit_aud=unit, unit_low_aud=unit * (1 - band), unit_high_aud=unit * (1 + band),
        batch_aud=unit * q,
        breakdown={"material": material, "machine": machine, "build_labour": build_labour,
                   "post_processing": post, "setup_amortised": setup, "tooling_amortised": tooling,
                   "scrap_and_qa": unit - direct},
        assumptions=tuple(assumptions), mass_kg=mass, parts_per_build=n, builds=builds,
        status=proc.status, rates_used=used,
    )


def curve(part: PartProfile, process_id: str, material_id: str,
          quantities: tuple[int, ...] = DEFAULT_QUANTITIES, *, rates: Rates) -> list[Estimate]:
    """Estimates at each quantity up to the route's quantity limit; the curve stops there."""
    qs = check_quantities(quantities)
    lim = rates.process(process_id).rates.get("max_quantity")
    if lim is not None:
        qs = tuple(q for q in qs if q <= lim.value)
    return [estimate(part, process_id, material_id, q, rates=rates) for q in qs]


# ----------------------------------------------------------------------------- advisor


@dataclass(frozen=True)
class Suggestion:
    process: str
    material: str
    feasible: bool
    unit_aud_at_target: float | None
    rank_score: float  # lower is better; feasible only
    reasons: tuple[str, ...]


def _route_exclusions(pc: ProcessCost, proc: manufacturing.Process, part: PartProfile, quantity: int) -> list[str]:
    out = []
    if part.domain and part.domain not in proc.domains:
        out.append(f"{pc.id} does not make {part.domain} parts (aces.manufacturing)")
    bv = pc.rates["build_volume_m"]
    if not _fits(part.envelope_m, bv.value):
        out.append(f"bounding box {tuple(round(x, 4) for x in part.envelope_m)} m exceeds the build volume "
                   f"{_fmt(bv.value)} m ({bv.source})")
    mf = pc.rates["min_feature_m"]
    if part.min_feature_m < mf.value:
        out.append(f"min feature {part.min_feature_m * 1e3:.2f} mm below the route limit "
                   f"{mf.value * 1e3:.2f} mm ({mf.source})")
    mq = pc.rates["max_quantity"]
    if quantity > mq.value:
        out.append(f"quantity {quantity} above the {pc.id} limit of {mq.value} ({mq.source})")
    if proc.family == "subtractive" and part.complex_internal:
        out.append("internal channels/lattices are not machinable; subtractive routes only make housings and plates")
    tol = pc.rates["tolerance_mm"]
    if part.tolerance_mm is not None and part.tolerance_mm < tol.value:
        out.append(f"tolerance {part.tolerance_mm:g} mm tighter than {pc.id} holds ({tol.value:g} mm, {tol.source})")
    return out


def _material_exclusions(mat: Material, part: PartProfile) -> list[str]:
    out = []
    if "density_kg_m3" not in mat.props:
        out.append(f"rates file lacks materials.{mat.id}.density_kg_m3")
    t = mat.props.get("max_service_temp_C")
    if t is None:
        out.append(f"rates file lacks materials.{mat.id}.max_service_temp_C: the {part.max_temp_C:.0f} C duty "
                   "cannot be checked")
    elif t.value < part.max_temp_C + TEMP_MARGIN_K:
        out.append(f"{mat.name} service temperature {t.value:g} C ({t.source}) does not cover the "
                   f"{part.max_temp_C:.0f} C duty + {TEMP_MARGIN_K:g} K margin")
    if part.pressure_bar > 0:
        p = mat.props.get("max_service_pressure_bar")
        if p is None:
            out.append(f"rates file lacks materials.{mat.id}.max_service_pressure_bar: the "
                       f"{part.pressure_bar:g} bar duty cannot be checked")
        elif p.value < PRESSURE_FACTOR * part.pressure_bar:
            out.append(f"{mat.name} service pressure {p.value:g} bar ({p.source}) does not cover "
                       f"{PRESSURE_FACTOR:g} x the {part.pressure_bar:g} bar duty")
    return out


def suggest(part: PartProfile, quantity: int, preferred_materials: tuple[str, ...] | None = None, *,
            rates: Rates) -> list[Suggestion]:
    """Rank (route, material) pairs for a part at a target quantity.

    Only routes ``available`` in ``aces.manufacturing`` can be feasible; planned routes are listed as
    planned, not available.  Feasible pairs are ranked by central unit cost; the rest carry every reason
    they were excluded (the DFM feedback a customer reads), including inputs the rates file lacks.
    """
    q = check_quantity(quantity)
    mats = tuple(preferred_materials) if preferred_materials else tuple(rates.materials)
    for mid in mats:
        rates.material(mid)  # unknown ids are an input error, not an exclusion
    if part.application not in rates.qa_factor:
        raise CostInputError(f"rates file lacks qa_factor.{part.application} (the part's application)")
    out: list[Suggestion] = []
    for pid, proc in manufacturing.PROCESSES.items():
        pc = rates.process(pid)
        for mid in mats:
            mat = rates.materials[mid]
            if proc.status != "available":
                reason = f"{pid} is {proc.status}, not available in ACES: never recommended ({proc.notes})"
                out.append(Suggestion(pid, mid, False, None, math.inf, (reason,)))
                continue
            if pid not in mat.feedstock_aud_kg:
                reason = f"{mat.name} is not offered on {pid} (no feedstock price in the rates file)"
                out.append(Suggestion(pid, mid, False, None, math.inf, (reason,)))
                continue
            reasons = []
            lacking = pc.missing()
            if lacking:
                reasons.append(f"rates file lacks processes.{pid}: {', '.join(lacking)}")
            else:
                reasons += _route_exclusions(pc, proc, part, q)
            reasons += _material_exclusions(mat, part)
            if not reasons:
                try:
                    est = estimate(part, pid, mid, q, rates=rates)
                except CostInputError as e:
                    reasons += e.problems
            if reasons:
                out.append(Suggestion(pid, mid, False, None, math.inf, tuple(reasons)))
                continue
            summary = (f"unit cost {rates.currency} {est.unit_aud:,.0f} ({est.unit_low_aud:,.0f}-"
                       f"{est.unit_high_aud:,.0f}) at qty {q}, {est.mass_kg:.3g} kg")
            out.append(Suggestion(pid, mid, True, est.unit_aud, est.unit_aud, (summary,)))
    return sorted(out, key=lambda s: (not s.feasible, s.rank_score))


def crossovers(part: PartProfile, pairs: tuple[tuple[str, str], ...],
               quantities: tuple[int, ...] = DEFAULT_QUANTITIES, *, rates: Rates) -> dict[str, Any]:
    """Per-unit curves for each (route, material) pair and where the lead changes.

    A curve holds None past its route's quantity limit.  Routes that are not available (planned) are
    listed under ``planned`` and never lead; pairs that cannot be costed are listed under ``skipped``.
    """
    qs = check_quantities(quantities)
    curves: dict[str, list[float | None]] = {}
    planned: list[str] = []
    skipped: dict[str, str] = {}
    for pid, mid in pairs:
        key = f"{pid}/{mid}"
        try:
            by_q = {e.quantity: e.unit_aud for e in curve(part, pid, mid, qs, rates=rates)}
        except CostInputError as e:
            skipped[key] = str(e)
            continue
        curves[key] = [by_q.get(q) for q in qs]
        if manufacturing.get(pid).status != "available":
            planned.append(key)
    leaders: list[dict[str, Any]] = []
    prev: str | None = "<start>"
    for i, q in enumerate(qs):
        live = {k: v[i] for k, v in curves.items() if k not in planned and v[i] is not None}
        lead = min(live, key=live.__getitem__) if live else None
        if lead != prev:
            leaders.append({"quantity": q, "cheapest": lead, "unit_aud": live.get(lead) if lead else None})
            prev = lead
    return {"quantities": list(qs), "curves": curves, "leaders": leaders, "planned": planned, "skipped": skipped}


def material_checks_used(part: PartProfile, material_id: str, rates: Rates) -> dict[str, dict[str, Any]]:
    """The sourced material limits a suggestion relied on (for the output's list of sources)."""
    mat = rates.material(material_id)
    keys = ["max_service_temp_C"] + (["max_service_pressure_bar"] if part.pressure_bar > 0 else [])
    return {mat.props[k].key: mat.props[k].as_dict() for k in keys if k in mat.props}
