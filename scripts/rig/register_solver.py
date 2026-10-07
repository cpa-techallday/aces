"""Add aces-tpms-periodic to a simlab solvers.json, keeping a dated backup and the file's own indentation.

    python3 register_solver.py /path/to/simlab/solvers.json [rig_image_id]
Idempotent: replaces an existing aces-tpms-periodic entry, touches nothing else.

The validation status says what was actually run (audit M8): the workstation GPU and the rig CPU self-test
passed; the rig GPU run is pending. When the image id is given, the entry runs that exact image (docker
accepts an image id wherever it takes a tag), so a rebuilt `aces-cfd:0.1` can't silently replace the image
the validation record describes.
"""
import datetime
import json
import shutil
import sys

path = sys.argv[1]
rig_image = sys.argv[2] if len(sys.argv) > 2 else ""
raw = open(path, encoding="utf-8", newline="").read()
data = json.loads(raw)
lines = raw.splitlines()
indent = len(lines[1]) - len(lines[1].lstrip(" ")) if len(lines) > 1 else 2
stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
shutil.copy2(path, f"{path}.bak-{stamp}")

gpu = {"device": "NVIDIA RTX PRO 4000 Blackwell SFF (workstation), aces-cfd:0.1, CuPy 14.2, float64", "n": 24}
entry = {
    "module": "aces.cfd.simlab_adapter",
    "physics": ["cfd"],
    "precision": ["float64"],
    "image": rig_image or "aces-cfd:0.1",
    "image_tag": "aces-cfd:0.1",
    "description": ("ACES periodic TPMS calibration samples: FP64 D3Q19/D3Q7 TRT lattice Boltzmann, body-force "
                    "driven to a target Re, H1 heat transfer; writes measurement.json rows for `aces calibrate`"),
    "validation": {
        "status": "pass-workstation; rig GPU pending",
        "date": "2026-10-06",
        "device": gpu["device"],
        "dtype": "float64",
        "cases": [
            {"case": "square duct, fully developed laminar, 24 cells across (GPU)", "fRe": 56.814, "fRe_exact": 56.91,
             "fRe_err_pct": -0.17, "Nu_H1": 3.6251, "Nu_H1_exact": 3.608, "Nu_err_pct": 0.47},
            {"case": "plane channel, fully developed laminar, 24 cells across (GPU)", "fRe": 95.917, "fRe_exact": 96.0,
             "fRe_err_pct": -0.09, "Nu_H1": 8.2466, "Nu_H1_exact": 8.235, "Nu_err_pct": 0.14},
            {"case": "rig build, CPU self-test, 16 cells across (2026-10-07)", "square_duct_fRe_err_pct": -0.37,
             "square_duct_Nu_err_pct": 1.06, "plane_channel_fRe_err_pct": -0.19, "plane_channel_Nu_err_pct": 0.31},
        ],
        "note": ("Exact references: Shah & London (1978). Second-order convergence; permeability is viscosity-"
                 "independent (TRT 3/16); an advected shear wave checks the inertial terms (ACES tests). "
                 "Gyroid t/a 0.10 Re 20: friction ratio 3.4292 (n=48) / 3.4293 (n=64). The rig GPU validation run "
                 "is pending: on 2026-10-07 the rig was in mode general-flagship with no simulation GPUs. Re-run "
                 "cases/aces/aces-cfd-validation.yaml through the queue in a sim-lab mode and replace this record."),
        "record": "ACES repo (github.com/steeldragon666/aces @ 4c26956): docs/SIMLAB_CFD.md, python -m aces.cfd.selftest",
        "rig_image_id": rig_image,
    },
}
data["aces-tpms-periodic"] = entry
with open(path, "w", encoding="utf-8", newline="\r\n" if "\r\n" in raw else "\n") as fh:
    fh.write(json.dumps(data, indent=indent, ensure_ascii=False) + ("\n" if raw.endswith("\n") else ""))
print(f"registered aces-tpms-periodic in {path} (indent {indent}); backup {path}.bak-{stamp}")
