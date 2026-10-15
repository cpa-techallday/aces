"""One pipeline, three front-ends.

`formgen.cli`, `formgen.studio` (the GUI) and the Fusion add-in all call
:func:`run` so they can never drift apart.

    from formgen.pipeline import Options, run
    res = run(Options(source="fusion", model=True, printer="x1c"))
    print(res["outputs"]["model_stl"])
"""

from __future__ import annotations

import contextlib
import glob
import json
import os
import time
from dataclasses import dataclass, asdict

from . import export, preview, scale, solids, viewer
from .lattice import build_lattice
from .loader import load_mesh
from .remesh import remesh_to_edge_length
from .sequence import order_struts

# module-level alias: inside the Options class body the name `scale` is shadowed
# by the `scale:` unit-override field, so grab the default up here.
_DEFAULT_PRINTER = scale.DEFAULT_PRINTER


@dataclass
class Options:
    # -- source ------------------------------------------------------------
    source: str = "file"              # "file" | "fusion"
    input: str = ""                   # path, when source == "file"
    fusion_ref: str | None = None     # body ref; None = current selection
    fusion_refinement: str = "High"
    name: str | None = None           # sub-body substring for multi-body files
    scale: float | None = None        # unit multiplier override

    # -- lattice -----------------------------------------------------------
    mode: str = "panel"               # skin | panel | infill
    strut: float = 60.0               # target strut length, mm
    thickness: float = 80.0           # panel depth, mm
    strut_dia: float = 8.0            # printed bead diameter, mm
    density: float = 1.10             # g/cm3 (ABS-CF ~ 1.10)
    speed: float = 25.0               # extrusion speed, mm/s
    union: bool = False               # boolean-union the full-size preview
    clamp_thickness: bool = True      # keep the inner skin inside the wall
    stitch: bool = True               # tie separated islands together
    max_stitch: float | None = None   # refuse links longer than this (mm)

    # -- outputs -----------------------------------------------------------
    out: str = "out"
    stem: str | None = None
    want_preview: bool = True         # full-size strut solids (STL + GLB)
    want_viewer: bool = True
    want_png: bool = True
    want_waypoints: bool = True
    want_jbi: bool = False
    dout: int = 1
    tool: int = 0
    user_frame: int = 1

    # -- scale model -------------------------------------------------------
    model: bool = False
    printer: str = _DEFAULT_PRINTER
    ratio: float | None = None        # None = auto-fit the plate
    min_strut: float | None = None    # None = 3 x nozzle
    base_mm: float = 0.0
    model_union: bool = False
    model_sections: int = 12
    margin: float = 0.92
    allow_enlarge: bool = False   # let auto-fit scale a small form up


def _default_log(msg: str) -> None:
    print(msg, flush=True)


def run(opts: Options, log=_default_log, *, out=None) -> dict:
    """Run the whole chain and return a result dict of paths + numbers.

    Every output goes through :mod:`aces.safeio` (audit M1): files are created relative to the output folder
    opened once, and never through a link.  ``out`` is an open :class:`aces.safeio.OutDir`; ACES passes a new
    run folder, and then nothing in it is ever overwritten.  Without it, formgen opens ``opts.out`` itself and
    replaces its own earlier outputs by atomic rename (a re-run into the same folder still works).
    """
    from aces import safeio

    with contextlib.ExitStack() as stack:
        if out is None:
            out = stack.enter_context(safeio.OutDir(opts.out))
            return _run(opts, log, out, overwrite=True, stack=stack)
        return _run(opts, log, out, overwrite=False, stack=stack)


def _run(opts: Options, log, out, *, overwrite: bool, stack: contextlib.ExitStack) -> dict:
    t0 = time.time()
    outputs: dict = {}
    source_info: dict = {}

    def write(name: str, data: str | bytes) -> str:
        if isinstance(data, bytes):
            out.write_bytes(name, data, overwrite=overwrite)
        else:
            out.write_text(name, data, overwrite=overwrite)
        return os.path.join(opts.out, name)

    # ---------------------------------------------------------------- load
    if opts.source == "fusion":
        from . import fusion as fusion_adaptor

        log("[1/7] pulling geometry from the running Fusion 360 session")
        # Fusion writes this export itself; the folder is created through the run folder's handle.
        export_dir = stack.enter_context(out.subdir("_fusion_export"))
        mesh, source_info = fusion_adaptor.load_from_fusion(
            ref=opts.fusion_ref,
            dest=str(export_dir.path),
            refinement=opts.fusion_refinement,
        )
        stem = opts.stem or _safe_stem(source_info.get("ref") or "fusion_body")
        log("      body '{}'  {} verts / {} faces  {} mm".format(
            source_info.get("ref"), len(mesh.vertices), len(mesh.faces),
            [round(float(v), 1) for v in mesh.extents]))
        if source_info.get("unit_correction"):
            log("      unit correction applied: x{}".format(source_info["unit_correction"]))
    else:
        if not opts.input:
            raise ValueError("no input file given")
        log("[1/7] loading {}".format(opts.input))
        mesh = load_mesh(opts.input, name=opts.name, scale=opts.scale)
        stem = opts.stem or os.path.splitext(os.path.basename(opts.input))[0]
        source_info = {"source": os.path.abspath(opts.input)}
        log("      {} verts, {} faces, extents {} mm, watertight={}".format(
            len(mesh.vertices), len(mesh.faces),
            [round(float(v), 1) for v in mesh.extents], mesh.is_watertight))

    # -------------------------------------------------------------- remesh
    log("[2/7] remeshing to ~{:g} mm struts".format(opts.strut))
    re = remesh_to_edge_length(mesh, opts.strut)
    mean_edge = float(re.edges_unique_length.mean())
    log("      {} verts, {} faces, mean edge {:.1f} mm".format(
        len(re.vertices), len(re.faces), mean_edge))
    if mean_edge < 0.7 * opts.strut or mean_edge > 1.4 * opts.strut:
        log("      ! remesher landed on {:.1f} mm, not the requested {:g} mm -- thin "
            "walls or sharp features are holding the triangles small".format(
                mean_edge, opts.strut))

    # ---------------------------------------------------------- tessellate
    log("[3/7] tessellating mode={}".format(opts.mode))
    g = build_lattice(re, mode=opts.mode, thickness=opts.thickness,
                      infill_cell=opts.thickness,
                      clamp_thickness=opts.clamp_thickness)
    tm = g.meta.get("thickness_measured_mm")
    if tm:
        log("      wall thickness measured: min {} / median {} / max {} mm".format(
            tm["min"], tm["median"], tm["max"]))
        nc = g.meta.get("thickness_clamped_vertices", 0)
        if nc:
            ap = g.meta.get("thickness_applied_mm", {})
            log("      ! panel depth {:g} mm exceeds the wall at {} of {} vertices "
                "({:.0%}) -- clamped to {:g}x local thickness, applied {}-{} mm".format(
                    opts.thickness, nc, len(re.vertices),
                    g.meta.get("thickness_clamped_fraction", 0), 0.45,
                    ap.get("min"), ap.get("max")))
    g.meta.update({"source": source_info, "strut_target_mm": opts.strut})
    dropped = g.prune_short(max(0.05 * opts.strut, 0.25))
    if dropped:
        log("      dropped {} degenerate (near-zero-length) struts".format(dropped))
    comps = g.components()
    if len(comps) > 1:
        log("      {} disconnected islands ({})".format(
            len(comps), ", ".join(str(len(c)) for c in comps[:8])))
        if opts.stitch:
            links = g.stitch_components(max_span=opts.max_stitch)
            if links:
                log("      stitched {} link(s), longest {:.0f} mm".format(
                    len(links), max(d for _, _, d in links)))
            left = len(g.components())
            if left > 1:
                log("      ! {} island(s) still separate".format(left))
    stats = g.stats()
    log("      {} nodes, {} struts, {:.1f} m total ({})".format(
        stats["nodes"], stats["struts"], stats["total_length_m"], stats["kinds"]))

    # ------------------------------------------------------------ sequence
    log("[4/7] sequencing for spatial printing")
    steps = order_struts(g)
    unreachable = next((s["unreachable"] for s in steps if "unreachable" in s), [])
    log("      {} printable steps, {} unreachable".format(
        len(steps) - (1 if unreachable else 0), len(unreachable)))

    # -------------------------------------------------------------- export
    log("[5/7] exporting full-size outputs")
    n = lambda suffix: "{}_{}".format(stem, suffix)  # noqa: E731

    outputs["lattice_json"] = write(n("lattice.json"), g.to_json(order=steps))

    if opts.want_preview:
        solid = solids.strut_solids(g, strut_diameter=opts.strut_dia, union=opts.union)
        outputs["preview_stl"] = write(n("lattice.stl"), solid.export(file_type="stl"))
        outputs["preview_glb"] = write(n("lattice.glb"), solid.export(file_type="glb"))

    if opts.want_waypoints:
        text, n_wp = export.waypoints_csv(g, steps)
        outputs["waypoints_csv"] = write(n("waypoints.csv"), text)
        log("      {} robot waypoints".format(n_wp))

    if opts.want_viewer:
        outputs["viewer_html"] = write(n("viewer.html"), viewer.render_viewer(
            g, steps, title="{} - {} lattice".format(stem, opts.mode)))

    if opts.want_png:
        outputs["preview_png"] = write(n("preview.png"), preview.render_png(
            g, title="{} - {} - {} struts".format(stem, opts.mode, stats["struts"])))

    if opts.want_jbi:
        base = stem.upper()[:6]
        if overwrite:  # a re-run into the same folder: drop numbered jobs left from the old lattice
            for f in glob.glob(os.path.join(glob.escape(str(out.path)), glob.escape(base) + "_[0-9][0-9][0-9].JBI")):
                try:
                    os.remove(f)  # removes a link itself, never its target
                except OSError:
                    pass
        jobs = [write("{}_{:03d}.JBI".format(base, ci), text)
                for ci, text in export.jbi_jobs(g, steps, job_name=base.replace("-", ""),
                                                dout_extrude=opts.dout, tool=opts.tool,
                                                user_frame=opts.user_frame, speed_extrude=opts.speed)]
        outputs["jbi"] = jobs
        log("      {} JBI job file(s)".format(len(jobs)))

    # --------------------------------------------------------- scale model
    plan = None
    model_est = None
    if opts.model:
        log("[6/7] building printable scale model")
        plan = scale.plan_model(g, printer=opts.printer, ratio=opts.ratio,
                                bead_mm=opts.strut_dia, min_strut_mm=opts.min_strut,
                                margin=opts.margin, base_mm=opts.base_mm,
                                allow_enlarge=opts.allow_enlarge)
        log("      {} on {}  ->  {} mm  (strut {:.2f} mm)".format(
            plan["ratio_label"], plan["printer"],
            " x ".join(str(v) for v in plan["model_size_mm"]), plan["model_strut_mm"]))
        for w in plan["warnings"]:
            log("      ! " + w)
        model_mesh = scale.build_model(g, plan, sections=opts.model_sections,
                                       union=opts.model_union)
        tag = plan["ratio_label"].replace(":", "to").replace(".", "p")
        model_path = write(n("model_{}.stl".format(tag)), model_mesh.export(file_type="stl"))
        outputs["model_stl"] = model_path
        outputs["model_glb"] = write(n("model_{}.glb".format(tag)), model_mesh.export(file_type="glb"))
        if opts.want_png:
            outputs["model_png"] = write(n("model_{}.png".format(tag)), preview.render_png(
                scale.scaled_graph(g, plan), title="scale model {} - {} mm".format(
                    plan["ratio_label"], " x ".join(str(v) for v in plan["model_size_mm"]))))
        model_est = scale.model_estimate(g, plan)
        log("      {}  ~{} g filament, {} m".format(
            os.path.basename(model_path), model_est["filament_g"], model_est["filament_m"]))
    else:
        log("[6/7] scale model skipped")

    # ------------------------------------------------------------ estimate
    log("[7/7] estimating the full-size build")
    est = g.estimate(bead_mm=opts.strut_dia, density_g_cm3=opts.density,
                     print_speed_mm_s=opts.speed)
    report = {
        "formgen": "report/1",
        "stem": stem,
        "options": {k: v for k, v in asdict(opts).items()},
        "source": source_info,
        "stats": stats,
        "sequence": {"printable_steps": len(steps) - (1 if unreachable else 0),
                     "unreachable_struts": len(unreachable)},
        "estimate": est,
        "scale_model": plan,
        "model_estimate": model_est,
        "outputs": outputs,
        "seconds": round(time.time() - t0, 1),
    }
    outputs["report_json"] = write(n("estimate.json"), json.dumps(report, indent=2))
    log("      full size: {} kg material, {} h print".format(
        est["material_mass_kg"], est["total_time_h"]))
    log("done in {:.1f}s -> {}".format(time.time() - t0, os.path.abspath(opts.out)))

    report["outputs"] = outputs
    return report


def _safe_stem(ref: str) -> str:
    s = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(ref))
    return s.strip("_")[-48:] or "fusion_body"
