"""Scale models: take a full-size lattice and make something you can print today.

A 2.4 m facade panel with 8 mm beads does not go on a desktop printer. This
module scales the lattice graph down, keeps the struts thick enough to actually
survive a 0.4 mm nozzle, optionally adds a base plate, drops the result on the
build plate, and reports honestly how much the struts had to be fattened.

    from formgen import scale
    plan  = scale.plan_model(graph, printer="x1c", bead_mm=8.0)
    mesh  = scale.build_model(graph, plan)
    mesh.export("panel_model_1to17.stl")
"""

from __future__ import annotations

import numpy as np
import trimesh

# build volumes in mm (x, y, z), usable area only
PRINTERS = {
    "x1c":      {"name": "Bambu Lab X1C / X1E",   "volume": (256.0, 256.0, 256.0), "nozzle": 0.4},
    "p1s":      {"name": "Bambu Lab P1S / P1P",   "volume": (256.0, 256.0, 256.0), "nozzle": 0.4},
    "a1":       {"name": "Bambu Lab A1",          "volume": (256.0, 256.0, 256.0), "nozzle": 0.4},
    "a1mini":   {"name": "Bambu Lab A1 mini",     "volume": (180.0, 180.0, 180.0), "nozzle": 0.4},
    "h2d":      {"name": "Bambu Lab H2D",         "volume": (325.0, 320.0, 325.0), "nozzle": 0.4},
    "mk4":      {"name": "Prusa MK4 / MK3.9",     "volume": (250.0, 210.0, 220.0), "nozzle": 0.4},
    "xl":       {"name": "Prusa XL",              "volume": (360.0, 360.0, 360.0), "nozzle": 0.4},
    "ender3":   {"name": "Creality Ender 3",      "volume": (220.0, 220.0, 250.0), "nozzle": 0.4},
    "elegoo":   {"name": "Elegoo Neptune 4 Pro",  "volume": (225.0, 225.0, 265.0), "nozzle": 0.4},
    "sla":      {"name": "Resin (Mars/Saturn 8K)", "volume": (218.0, 123.0, 260.0), "nozzle": 0.05},
    "desk":     {"name": "Generic 200 mm desktop", "volume": (200.0, 200.0, 200.0), "nozzle": 0.4},
}

DEFAULT_PRINTER = "x1c"

# permutations of (x,y,z) -> which model axis lands on which build axis
_AXIS_ORDERS = [(0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)]


def printer_spec(printer) -> dict:
    """Accept a preset key, a name, or an explicit (x, y, z) volume tuple."""
    if isinstance(printer, (tuple, list)) and len(printer) == 3:
        return {"name": "custom", "volume": tuple(float(v) for v in printer), "nozzle": 0.4}
    if isinstance(printer, dict):
        return printer
    key = str(printer or DEFAULT_PRINTER).lower().replace(" ", "")
    if key in PRINTERS:
        return PRINTERS[key]
    raise ValueError(
        "unknown printer '{}' -- known: {}".format(printer, ", ".join(sorted(PRINTERS)))
    )


def _lattice_size(g) -> np.ndarray:
    if len(g.nodes) == 0:
        return np.zeros(3)
    return g.nodes.max(axis=0) - g.nodes.min(axis=0)


def plan_model(
    g,
    printer=DEFAULT_PRINTER,
    ratio: float | None = None,
    bead_mm: float = 8.0,
    min_strut_mm: float | None = None,
    margin: float = 0.92,
    base_mm: float = 0.0,
    reorient: bool = True,
    allow_enlarge: bool = False,
) -> dict:
    """Work out how to shrink *g* onto a build plate. Returns a plan dict.

    ratio         explicit reduction, e.g. 20 for 1:20. None = auto-fit.
    bead_mm       the real-world strut diameter being modelled.
    min_strut_mm  smallest strut the printer can actually make (default 3x nozzle).
    margin        fraction of the build volume to use (leaves skirt clearance).
    base_mm       thickness of an optional solid base plate under the model.
    reorient      allow swapping model axes so it fits at a bigger scale.
    allow_enlarge  let auto-fit scale *up* a form smaller than the plate.
    """
    spec = printer_spec(printer)
    vol = np.array(spec["volume"], dtype=float)
    nozzle = float(spec.get("nozzle", 0.4))
    if min_strut_mm is None:
        min_strut_mm = max(3.0 * nozzle, 1.0)

    size = _lattice_size(g)
    if not np.all(np.isfinite(size)) or size.max() <= 0:
        raise ValueError("lattice has no extent to scale")

    usable = vol * float(margin)
    usable[2] = max(usable[2] - base_mm, 1.0)

    # pick the axis order that lets us print biggest. A planar form (a flat
    # face, or skin mode on a single surface) has a zero-extent axis -- that
    # axis never constrains the fit, so keep it out of the division.
    planar = bool((size <= 1e-6).any())
    safe = np.maximum(size, 1e-6)
    best = None
    for order in (_AXIS_ORDERS if reorient else [(0, 1, 2)]):
        fit = float(np.min(usable / safe[list(order)]))
        if best is None or fit > best[1]:
            best = (order, fit)
    order, fit_scale = best
    enlarged = False
    if not allow_enlarge and fit_scale > 1.0:
        # the form already fits: a "scale model" of it is 1:1, not blown up
        fit_scale = 1.0
        enlarged = True

    if ratio is not None and float(ratio) > 0:
        s = 1.0 / float(ratio)
        fits = s <= fit_scale + 1e-9
    else:
        s = fit_scale
        fits = True

    model_size = size[list(order)] * s
    true_strut = bead_mm * s
    strut = max(true_strut, min_strut_mm)
    exaggeration = strut / true_strut if true_strut > 1e-9 else float("inf")

    lengths = g.lengths()
    model_len_min = float(lengths.min() * s) if len(lengths) else 0.0
    model_len_mean = float(lengths.mean() * s) if len(lengths) else 0.0

    warnings = []
    if planar:
        warnings.append(
            "the lattice is flat in one axis -- it will print as a plate, not a "
            "shell; use panel mode (or a --thickness) if you wanted depth")
    if enlarged and ratio is None:
        warnings.append(
            "form already fits the plate -- printing 1:1; pass --ratio or "
            "allow_enlarge=True to scale it up")
    if not fits:
        warnings.append(
            "1:{:g} does not fit {} -- needs at least 1:{:.0f}".format(
                ratio, spec["name"], 1.0 / fit_scale))
    if exaggeration > 1.01:
        warnings.append(
            "struts fattened {:.1f}x (true {:.2f} mm -> printable {:.2f} mm) "
            "-- the model reads as chunkier than the real lattice".format(
                exaggeration, true_strut, strut))
    if model_len_min < 2.5 * strut and model_len_min > 0:
        warnings.append(
            "shortest strut is only {:.1f} mm against a {:.1f} mm diameter -- "
            "nodes will blob together; print a bigger scale or a coarser --strut"
            .format(model_len_min, strut))
    if model_len_mean < 4.0 * strut:
        warnings.append(
            "mean strut {:.1f} mm vs {:.1f} mm diameter: expect a solid-ish surface"
            .format(model_len_mean, strut))

    return {
        "printer": spec["name"],
        "build_volume_mm": list(vol),
        "axis_order": list(order),
        "scale": float(s),
        "ratio": round(1.0 / s, 2) if s > 0 else None,
        "ratio_label": "1:{:g}".format(round(1.0 / s, 1)) if s > 0 else "?",
        "fits": bool(fits),
        "planar": planar,
        "max_scale_that_fits": float(fit_scale),
        "full_size_mm": [round(float(v), 1) for v in size],
        "model_size_mm": [round(float(v), 1) for v in model_size],
        "model_height_with_base_mm": round(float(model_size[2] + base_mm), 1),
        "bead_mm": float(bead_mm),
        "true_scaled_strut_mm": round(float(true_strut), 3),
        "model_strut_mm": round(float(strut), 3),
        "strut_exaggeration": round(float(exaggeration), 2),
        "min_strut_mm": float(min_strut_mm),
        "base_mm": float(base_mm),
        "model_strut_len_mm": {"min": round(model_len_min, 2),
                               "mean": round(model_len_mean, 2)},
        "warnings": warnings,
    }


def scaled_graph(g, plan: dict):
    """Return a copy of the lattice graph transformed into model space."""
    from .graph import LatticeGraph

    order = plan.get("axis_order", [0, 1, 2])
    pts = np.asarray(g.nodes, dtype=float)[:, order] * float(plan["scale"])
    pts = pts - pts.min(axis=0)
    pts[:, 0] -= (pts[:, 0].max()) / 2.0
    pts[:, 1] -= (pts[:, 1].max()) / 2.0
    pts[:, 2] += float(plan.get("base_mm", 0.0))
    out = LatticeGraph(nodes=pts, struts=list(g.struts),
                       meta=dict(g.meta, scale_model=plan))
    return out


def build_model(g, plan: dict, sections: int = 12, union: bool = False):
    """Build the printable scale-model mesh: struts + node balls + base plate."""
    from . import solids

    sg = scaled_graph(g, plan)
    mesh = solids.strut_solids(sg, strut_diameter=plan["model_strut_mm"],
                               sections=sections, union=union)
    base = float(plan.get("base_mm", 0.0))
    if base > 0:
        lo = sg.nodes.min(axis=0)
        hi = sg.nodes.max(axis=0)
        pad = plan["model_strut_mm"] * 1.5
        sx = float(hi[0] - lo[0] + 2 * pad)
        sy = float(hi[1] - lo[1] + 2 * pad)
        slab = trimesh.creation.box(extents=(sx, sy, base))
        slab.apply_translation([(hi[0] + lo[0]) / 2.0, (hi[1] + lo[1]) / 2.0, base / 2.0])
        mesh = trimesh.util.concatenate([mesh, slab]) if not union else \
            trimesh.boolean.union([mesh, slab], engine="manifold")
    # sit exactly on the plate
    mesh.apply_translation([0.0, 0.0, -float(mesh.bounds[0][2])])
    return mesh


def model_estimate(g, plan: dict, filament_density_g_cm3: float = 1.24,
                   filament_dia_mm: float = 1.75) -> dict:
    """Rough material use for the printed scale model (PLA by default)."""
    lengths = g.lengths() * float(plan["scale"])
    r = plan["model_strut_mm"] / 2.0
    vol_mm3 = float(np.sum(lengths) * np.pi * r * r)
    base = float(plan.get("base_mm", 0.0))
    if base > 0:
        sz = plan["model_size_mm"]
        vol_mm3 += sz[0] * sz[1] * base
    grams = vol_mm3 / 1000.0 * filament_density_g_cm3
    fil_area = np.pi * (filament_dia_mm / 2.0) ** 2
    return {
        "solid_volume_cm3": round(vol_mm3 / 1000.0, 1),
        "filament_g": round(grams, 1),
        "filament_m": round(vol_mm3 / fil_area / 1000.0, 1),
        "note": "solid volume, no infill discount -- slicer is the real answer",
    }


def suggest_ratio(g, printer=DEFAULT_PRINTER, margin: float = 0.92) -> float:
    """The tightest round ratio (1:N) that still fits the plate."""
    plan = plan_model(g, printer=printer, margin=margin)
    raw = 1.0 / plan["max_scale_that_fits"]
    for nice in (2, 2.5, 5, 10, 15, 20, 25, 30, 40, 50, 75, 100, 150, 200, 250,
                 500, 1000):
        if nice >= raw:
            return float(nice)
    return float(round(raw))
