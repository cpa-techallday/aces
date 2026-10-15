"""formgen CLI.

    # from a file
    python -m formgen.cli panel.step --mode panel --strut 60 --thickness 80 -o out/

    # straight from the running Fusion 360 (current selection), plus a
    # printable 1:N model sized for a Bambu X1C
    python -m formgen.cli --from-fusion --model --printer x1c -o out/

    # what is open in Fusion right now?
    python -m formgen.cli --list-fusion-bodies
"""

from __future__ import annotations

import argparse
import os
import sys

from . import scale
from .pipeline import Options, run


def _fix_console() -> None:
    """Windows consoles default to cp1252 and choke on non-ASCII output."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="formgen",
        description="Fusion form -> triangulated tessellated lattice -> "
                    "robot job + printable scale model",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("input", nargs="?", default=None,
                   help="STEP/STL/OBJ/PLY/3MF/GLB file (omit with --from-fusion)")
    p.add_argument("-o", "--out", default="out", help="output directory")
    p.add_argument("--name", default=None,
                   help="pick sub-body by name substring (multi-body files)")
    p.add_argument("--stem", default=None, help="output filename stem")

    f = p.add_argument_group("live Fusion 360")
    f.add_argument("--from-fusion", action="store_true",
                   help="pull geometry from the running Fusion session")
    f.add_argument("--fusion-body", default=None,
                   help="body ref to export (default: current Fusion selection)")
    f.add_argument("--fusion-refinement", default="High",
                   choices=["Low", "Medium", "High"], help="STL tessellation quality")
    f.add_argument("--list-fusion-bodies", action="store_true",
                   help="list bodies in the open design and exit")

    l = p.add_argument_group("lattice")
    l.add_argument("--mode", choices=["skin", "panel", "infill"], default="panel")
    l.add_argument("--strut", type=float, default=60.0, help="target strut length mm")
    l.add_argument("--thickness", type=float, default=80.0,
                   help="panel depth mm (panel/infill cell)")
    l.add_argument("--scale", type=float, default=None, help="unit multiplier override")
    l.add_argument("--strut-dia", type=float, default=8.0,
                   help="bead/strut diameter mm for solids+estimate")
    l.add_argument("--density", type=float, default=1.10,
                   help="material g/cm3 (ABS-CF ~1.10)")
    l.add_argument("--speed", type=float, default=25.0, help="extrusion speed mm/s")
    l.add_argument("--no-clamp", action="store_true",
                   help="let the inner skin punch through thin walls (old behaviour)")
    l.add_argument("--no-stitch", action="store_true",
                   help="leave disconnected islands unlinked (they will not "
                        "get a print order)")
    l.add_argument("--max-stitch", type=float, default=None,
                   help="refuse stitch links longer than this (mm)")
    l.add_argument("--union", action="store_true",
                   help="boolean-union the full-size preview solid (slow)")

    o = p.add_argument_group("outputs")
    o.add_argument("--no-preview", action="store_true",
                   help="skip the full-size strut solid (much faster)")
    o.add_argument("--no-viewer", action="store_true")
    o.add_argument("--no-png", action="store_true", help="skip the 3-view PNG")
    o.add_argument("--no-waypoints", action="store_true")
    o.add_argument("--jbi", action="store_true",
                   help="also write Yaskawa INFORM .JBI job files")
    o.add_argument("--dout", type=int, default=1, help="extruder DOUT number")
    o.add_argument("--tool", type=int, default=0, help="tool file number")
    o.add_argument("--user-frame", type=int, default=1, help="user frame number")

    m = p.add_argument_group("printable scale model")
    m.add_argument("--model", action="store_true",
                   help="also write a desktop-printable scale model STL")
    m.add_argument("--printer", default=scale.DEFAULT_PRINTER,
                   help="build volume preset: " + ", ".join(sorted(scale.PRINTERS)))
    m.add_argument("--ratio", type=float, default=None,
                   help="reduction, e.g. 20 for 1:20 (default: auto-fit the plate)")
    m.add_argument("--min-strut", type=float, default=None,
                   help="thinnest printable strut mm (default 3x nozzle)")
    m.add_argument("--base", type=float, default=0.0,
                   help="solid base plate thickness mm under the model")
    m.add_argument("--model-union", action="store_true",
                   help="boolean-union the model into one watertight solid")
    m.add_argument("--margin", type=float, default=0.92,
                   help="fraction of the build volume to use")
    m.add_argument("--enlarge", action="store_true",
                   help="allow auto-fit to scale a small form UP to fill the plate")
    m.add_argument("--list-printers", action="store_true")
    return p


def main(argv=None) -> int:
    _fix_console()
    args = build_parser().parse_args(argv)

    if args.list_printers:
        for key in sorted(scale.PRINTERS):
            s = scale.PRINTERS[key]
            print("  {:<9} {:<28} {:.0f} x {:.0f} x {:.0f} mm".format(
                key, s["name"], *s["volume"]))
        return 0

    if args.list_fusion_bodies:
        from . import fusion

        br = fusion.FusionBridge()
        if not br.ping():
            print("no live Fusion bridge on 127.0.0.1:9876 -- start Fusion 360 "
                  "and the Fusion360MCP add-in", file=sys.stderr)
            return 2
        print("design: {}".format(br.design_name()))
        sel = br.selection()
        if sel:
            print("selected right now:")
            for b in sel:
                print("  * {}   {} mm".format(b["ref"], b["size_mm"]))
        print("visible bodies:")
        for b in br.list_bodies():
            print("  {:<52} {} mm".format(b["ref"][:52], b["size_mm"]))
        return 0

    if not args.from_fusion and not args.input:
        print("give an input file, or --from-fusion (see --help)", file=sys.stderr)
        return 2
    if args.input and not os.path.exists(args.input):
        print("no such file: {}".format(args.input), file=sys.stderr)
        return 2

    opts = Options(
        source="fusion" if args.from_fusion else "file",
        input=args.input or "",
        fusion_ref=args.fusion_body,
        fusion_refinement=args.fusion_refinement,
        name=args.name,
        scale=args.scale,
        mode=args.mode,
        strut=args.strut,
        thickness=args.thickness,
        strut_dia=args.strut_dia,
        density=args.density,
        speed=args.speed,
        union=args.union,
        stitch=not args.no_stitch,
        clamp_thickness=not args.no_clamp,
        max_stitch=args.max_stitch,
        out=args.out,
        stem=args.stem,
        want_preview=not args.no_preview,
        want_viewer=not args.no_viewer,
        want_png=not args.no_png,
        want_waypoints=not args.no_waypoints,
        want_jbi=args.jbi,
        dout=args.dout,
        tool=args.tool,
        user_frame=args.user_frame,
        model=args.model,
        printer=args.printer,
        ratio=args.ratio,
        min_strut=args.min_strut,
        base_mm=args.base,
        model_union=args.model_union,
        margin=args.margin,
        allow_enlarge=args.enlarge,
    )

    try:
        run(opts)
    except Exception as exc:  # keep the console useful, not a traceback wall
        print("formgen failed: {}: {}".format(type(exc).__name__, exc), file=sys.stderr)
        if os.environ.get("FORMGEN_DEBUG"):
            raise
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
