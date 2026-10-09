"""Consistency check: the same L-bracket built in build123d, voxelised, and evaluated on the
GPU must agree with the analytic voxel mask used by cases/bracket_param.py. Also writes the
STEP so the parametric path ends in native B-rep geometry.

    .venv\\Scripts\\python cases\\bracket_build123d_check.py --out out\\bracket_b3d
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bracket_param import ENVELOPE, HOLES, HOLE_R, Bracket  # noqa: E402
from gendesign.evaluators import build123d_to_mask, build123d_to_step  # noqa: E402
from gendesign.export import save_json  # noqa: E402


def bracket_b3d(p: dict):
    from build123d import Align, Box, BuildPart, BuildSketch, Cylinder, Locations, Mode, Plane, Polygon, extrude, make_face

    Lx, Ly, Lz = ENVELOPE
    g, gt = p["gusset"], p["gusset_t"]
    with BuildPart() as bp:
        Box(Lx, Ly, p["t_base"], align=(Align.MIN, Align.MIN, Align.MIN))
        Box(p["t_wall"], Ly, Lz, align=(Align.MIN, Align.MIN, Align.MIN))
        if g > 0 and gt > 0:
            # gusset: triangle in the x-z plane centred on y = 20, thickness gt (plane normal -Y)
            pl = Plane(origin=(0, 20.0 + gt / 2, 0), x_dir=(1, 0, 0), z_dir=(0, -1, 0))
            with BuildSketch(pl):
                Polygon((p["t_wall"], p["t_base"]), (p["t_wall"] + g, p["t_base"]), (p["t_wall"], p["t_base"] + g),
                        align=None)
            extrude(amount=gt)
        with Locations(*[(hx, hy, 0) for hx, hy in HOLES]):
            Cylinder(HOLE_R, p["t_base"] * 2 + 2, align=(Align.CENTER, Align.CENTER, Align.CENTER), mode=Mode.SUBTRACT)
    return bp.part


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out/bracket_b3d")
    ap.add_argument("--h", type=float, default=1.0)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    p = {"t_base": 6.0, "t_wall": 5.0, "gusset": 25.0, "gusset_t": 6.0}
    prob = Bracket(a.h)
    part = bracket_b3d(p)
    step = build123d_to_step(part, os.path.join(a.out, "bracket.step"))
    mask_b3d = build123d_to_mask(part, ENVELOPE, a.h)
    mask_an = prob.build(p)
    agree = float((mask_b3d == mask_an).mean())
    m_b3d = prob.evaluate(mask_b3d, p)
    m_an = prob.evaluate(mask_an, p)
    out = {"params": p, "step": step, "voxel_agreement": agree,
           "solid_b3d": int(mask_b3d.sum()), "solid_analytic": int(mask_an.sum()),
           "metrics_build123d": m_b3d, "metrics_analytic": m_an,
           "compliance_ratio": m_b3d["compliance_Nmm"] / m_an["compliance_Nmm"],
           "part_volume_mm3": float(part.volume)}
    save_json(os.path.join(a.out, "check.json"), out)
    print(f"voxel agreement {agree:.4%}  solids {out['solid_b3d']} vs {out['solid_analytic']}  "
          f"part volume {out['part_volume_mm3']:.0f} mm3 vs analytic voxels {m_an['volume_mm3']:.0f}")
    print(f"compliance build123d {m_b3d['compliance_Nmm']:.4g} vs analytic {m_an['compliance_Nmm']:.4g} "
          f"(ratio {out['compliance_ratio']:.4f})  vm {m_b3d['vm_max_MPa']:.1f} vs {m_an['vm_max_MPa']:.1f} MPa")
    print("STEP:", step)


if __name__ == "__main__":
    main()
