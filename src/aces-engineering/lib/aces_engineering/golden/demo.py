"""aces golden: the demo specs and the template for a user STEP.

`root-mount` is the gate: a six-foot root mount computed inside a disc design space (the disc is only the input
domain; the part is SIMP topology optimisation under an organic implicit skin, solved on the real voxel FEA). `bone`
is the optional showcase: the same mount with a gyroid sheet core inside a 4 mm shell, solved at 2 mm, with its own
calibrated allowable and umax limit (GPU recommended; never in a gate). `template` is the starting spec for
`aces golden init --step`: it fails validation until
the user fills in the supports, loads and material, because there are no honest defaults for those.

build123d is imported only inside root_mount_domain: importing this module stays quick."""
from __future__ import annotations

import copy
import json
import re
from pathlib import PurePath

from . import DEMOS, EXIT_USAGE, GoldenError

# Section 6.4 of the build spec, verbatim: `aces golden spec --demo` prints this document.
ROOT_MOUNT_JSON = r"""{
 "schema": "aces-golden-spec/1",
 "name": "root-mount",
 "description": "Six-foot root mount computed inside a disc design space 100 mm across and 40 mm tall: six bolted feet on a 36 mm ring and a 30 kN load on a top cup. The disc is only the input domain; the part is computed (SIMP topology optimisation, organic implicit skin) and solved on the real voxel FEA. Demo inputs.",
 "units": "mm-N-MPa-kg",
 "hypothesis": "A SIMP-generated organic root mount meets vm_p99 <= 60 MPa and umax <= 0.035 mm at minimum mass, and a GP surrogate fitted to a 10-design sweep predicts the verified winner within 10 %.",
 "domain": {
  "source": "build123d",
  "build123d": {"ref": "aces_engineering.golden.demo:root_mount_domain", "kwargs": {"radius_mm": 50.0, "height_mm": 40.0}},
  "origin_mm": [0, 0, 0], "envelope_mm": [100, 100, 40],
  "keep_in": [
   {"name": "foot-1", "cyl": {"axis": "z", "center": [82, 70], "r": 9.0, "span": [0, 6]}, "round_mm": 1.0},
   {"name": "foot-2", "cyl": {"axis": "z", "center": [50, 86], "r": 9.0, "span": [0, 6]}, "round_mm": 1.0},
   {"name": "foot-3", "cyl": {"axis": "z", "center": [18, 70], "r": 9.0, "span": [0, 6]}, "round_mm": 1.0},
   {"name": "foot-4", "cyl": {"axis": "z", "center": [18, 34], "r": 9.0, "span": [0, 6]}, "round_mm": 1.0},
   {"name": "foot-5", "cyl": {"axis": "z", "center": [50, 14], "r": 9.0, "span": [0, 6]}, "round_mm": 1.0},
   {"name": "foot-6", "cyl": {"axis": "z", "center": [82, 34], "r": 9.0, "span": [0, 6]}, "round_mm": 1.0},
   {"name": "cup", "cyl": {"axis": "z", "center": [50, 50], "r": 15.0, "span": [34, 40]}, "round_mm": 1.0}
  ],
  "keep_out": [],
  "patch_keep_in_mm": 0,
  "clip_to_domain": true
 },
 "supports": [
  {"name": "foot-1", "box": [76, 64, 0, 88, 76, 0], "dofs": [0, 1, 2]},
  {"name": "foot-2", "box": [44, 80, 0, 56, 92, 0], "dofs": [0, 1, 2]},
  {"name": "foot-3", "box": [12, 64, 0, 24, 76, 0], "dofs": [0, 1, 2]},
  {"name": "foot-4", "box": [12, 28, 0, 24, 40, 0], "dofs": [0, 1, 2]},
  {"name": "foot-5", "box": [44, 8, 0, 56, 20, 0], "dofs": [0, 1, 2]},
  {"name": "foot-6", "box": [76, 28, 0, 88, 40, 0], "dofs": [0, 1, 2]}
 ],
 "loads": [{"name": "cup-down", "box": [40, 40, 40, 60, 60, 40], "force": [0, 0, -30000], "axis": "z", "case": "down"}],
 "material": {"name": "AlSi10Mg (LPBF), nominal", "E_MPa": 70000, "nu": 0.33, "rho_kg_mm3": 2.67e-6, "allowable_vm_MPa": 60,
  "source": "Nominal handbook values for AlSi10Mg: E 70 GPa, Poisson 0.33, density 2.67 g/cm3. The 60 MPa allowable is a demo design limit chosen so the stress constraint is active; it is not a material strength. Demo inputs, not a qualified material card."},
 "objective": {"output": "mass_kg", "sense": "min"},
 "constraints": [{"output": "vm_p99_MPa", "op": "<=", "limit": "allowable"}, {"output": "umax_mm", "op": "<=", "limit": 0.035}],
 "generator": {"kind": "simp-organic",
  "variables": {"volfrac": {"lo": 0.13, "hi": 0.38}, "rmin_mm": {"lo": 6.0, "hi": 12.0}},
  "fixed": {"penal": 3.0},
  "iterations": 25, "change_tol": 0.01, "move": 0.2, "solver_tol": 1e-4,
  "skin": {"upsample": 2, "sigma_vox": 1.0, "blend_mm": 5.0, "clip_blend_mm": 2.0, "taubin_iters": 5, "iso": 0.5}},
 "resolution": {"gen_mm": 4.0, "fea_mm": 4.0, "check_factor": 0.5, "stress_exclude_mm": 6.0},
 "sweep": {"n": 10, "seed": 7},
 "surrogate": {"kind": "gp", "folds": 5, "min_points": 8, "r2_flag": 0.9, "log_outputs": "auto-positive"},
 "optimise": {"method": "auto", "kappa": 1.0, "cv_margin": true, "rounds": 3},
 "verify": {"tol": 0.10, "check_objective": true, "check": "auto", "check_budget_s": 60},
 "solver": {"dtype": "float64", "tol": 1e-8, "maxiter": 20000}
}
"""

BONE_DESCRIPTION = ("Six-foot root mount computed inside a disc design space 100 mm across and 40 mm tall, grown like "
                    "bone: the SIMP skin is a 4 mm shell around a gyroid sheet core (a TPMS lattice), one implicit field "
                    "blended into the feet and the cup, solved on the real voxel FEA at 2 mm. The disc is only the input "
                    "domain. Demo inputs; GPU recommended.")
BONE_HYPOTHESIS = ("A SIMP-generated organic root mount with a gyroid sheet core inside a 4 mm shell meets vm_p99 <= "
                   "75 MPa and umax <= 0.042 mm at minimum mass, and a GP surrogate fitted to a 16-design sweep predicts "
                   "the verified winner within 10 %.")
# Calibrated 2026-10-09 on the workstation CPU (B1): with the root-mount limits (60 MPa, 0.035 mm) no bone design was
# predicted feasible with the 10 % margin in any of 3 rounds (seed 7), because the gyroid-cored body is less stiff
# per kilogram than the plain SIMP skin. Only the allowable and the umax limit were changed, as the build spec allows.
BONE_ALLOWABLE_MPA = 75.0
BONE_UMAX_MM = 0.042
BONE_MATERIAL_SOURCE = ("Nominal handbook values for AlSi10Mg: E 70 GPa, Poisson 0.33, density 2.67 g/cm3. The 75 MPa "
                        "allowable is a demo design limit for the gyroid-cored showcase (the root mount's 60 MPa leaves "
                        "no design predicted feasible at this stiffness); it is not a material strength. Demo inputs, "
                        "not a qualified material card.")


def spec(name: str = "root-mount") -> dict:
    """A fresh copy of a demo spec: "root-mount" (section 6.4) or "bone" (section 6.5)."""
    base = json.loads(ROOT_MOUNT_JSON)
    if name == "root-mount":
        return base
    if name == "bone":
        bone = copy.deepcopy(base)
        bone["name"] = "root-mount-bone"
        bone["description"] = BONE_DESCRIPTION
        bone["hypothesis"] = BONE_HYPOTHESIS
        bone["material"] = dict(bone["material"], allowable_vm_MPa=BONE_ALLOWABLE_MPA, source=BONE_MATERIAL_SOURCE)
        bone["constraints"] = [{"output": "vm_p99_MPa", "op": "<=", "limit": "allowable"},
                               {"output": "umax_mm", "op": "<=", "limit": BONE_UMAX_MM}]
        gen = bone["generator"]
        gen["variables"] = {"volfrac": {"lo": 0.28, "hi": 0.51}, "rmin_mm": {"lo": 6.0, "hi": 12.0},
                            "cell_mm": {"lo": 18.0, "hi": 26.0}, "wall_mm": {"lo": 4.0, "hi": 6.0}}
        gen["fixed"] = {"penal": 3.0, "shell_mm": 4.0}
        gen["infill"] = {"kind": "gyroid", "blend_mm": 1.5}
        gen["skin"] = dict(gen["skin"], upsample=4)
        bone["resolution"] = {"gen_mm": 4.0, "fea_mm": 2.0, "check_factor": 0.5, "stress_exclude_mm": 6.0}
        bone["sweep"] = {"n": 16, "seed": 7}
        return bone
    raise GoldenError(EXIT_USAGE, f"unknown demo {name!r}: the demos are {', '.join(DEMOS)}",
                      hint="aces golden spec --demo root-mount")


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)[:48].strip("-")
    return s if s and s[0].isalnum() else ("part-" + s).strip("-")[:48] or "part"


def template(step_ref: str) -> dict:
    """The starting spec for a user STEP design space (`aces golden init --step`). The grid is derived from the STEP;
    supports, loads, keep-ins and the material are left empty (and the material numbers null) on purpose, so the spec
    fails validation, naming the first missing field, until the user fills them in."""
    demo = json.loads(ROOT_MOUNT_JSON)
    return {
        "schema": "aces-golden-spec/1",
        "name": _slug(PurePath(str(step_ref).replace("\\", "/")).stem),
        "description": "",
        "units": "mm-N-MPa-kg",
        "hypothesis": None,
        "domain": {"source": "step", "step_file": step_ref, "margin_mm": 0.0, "keep_in": [], "keep_out": [],
                   "patch_keep_in_mm": None, "clip_to_domain": True},
        "supports": [],
        "loads": [],
        "material": {"name": "", "E_MPa": None, "nu": None, "rho_kg_mm3": None, "allowable_vm_MPa": None, "source": ""},
        "objective": {"output": "mass_kg", "sense": "min"},
        "constraints": [{"output": "vm_p99_MPa", "op": "<=", "limit": "allowable"}],
        "generator": demo["generator"],
        "resolution": demo["resolution"],
        "sweep": demo["sweep"],
        "verify": demo["verify"],
    }


def root_mount_domain(radius_mm: float = 50.0, height_mm: float = 40.0):
    """The root mount's design space: a disc centred on (50, 50) standing on z = 0 (an input domain, not the part)."""
    from build123d import Align, Cylinder, Pos

    return Pos(50, 50, 0) * Cylinder(radius=radius_mm, height=height_mm, align=(Align.CENTER, Align.CENTER, Align.MIN))
