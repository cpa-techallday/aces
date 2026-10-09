"""Register aces-tile-em in simlab's solvers.json (RADAR_EM.md tier item 3).

Follows scripts/rig/register_solver.py: dated backup first, preserves the
file's own indentation, idempotent (replaces an existing aces-tile-em entry,
touches nothing else).

    python register_em_solver.py [path-to-solvers.json]
Default: C:\\Users\\shedm\\cudax-sim-runtime\\simlab\\solvers.json
"""
import datetime
import json
import shutil
import sys

path = sys.argv[1] if len(sys.argv) > 1 else \
    r"C:\Users\shedm\cudax-sim-runtime\simlab\solvers.json"
raw = open(path, encoding="utf-8", newline="").read()
data = json.loads(raw)
lines = raw.splitlines()
indent = len(lines[1]) - len(lines[1].lstrip(" ")) if len(lines) > 1 else 2
stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
shutil.copy2(path, f"{path}.bak-{stamp}")

entry = {
    "module": "aces.domains.radar.em_adapter",
    "physics": ["em"],
    "precision": ["float64"],
    "image": "aces-em:0.1",
    "description": ("ACES radar EM verification tier: gprMax 4.0.1 FDTD (CPU/OpenMP) and Palace v0.14.0 "
                    "FEM (gmsh meshes) for the AERIS tile stackups — patch resonance/far-field, THRU "
                    "loss coupons, N-port through-lines, WR-90, KiCad-layout import (kicad_net), and "
                    "stitched via-transition coupons (via_coupon); writes measurement.json rows for "
                    "`aces calibrate`"),
    "validation": {
        "status": "pass",
        "date": "2026-10-09",
        "device": "aces-em:0.1 container (gprMax 4.0.1 CPU/OpenMP, Palace v0.14.0, gmsh) on Windows workstation Docker, OMP_NUM_THREADS=16",
        "dtype": "float64",
        "cases": [
            {"case": "patch_xband (gprMax)", "result": "PASS",
             "note": "mesh-converged resonance 10.0-10.1 GHz, two estimators within 0.3-1%"},
            {"case": "tile_coupon lambda_g/2 resonator (gprMax, job21 3-cell mesh)", "result": "PASS",
             "measured_db_per_m": 4.93, "model_db_per_m": 5.12, "ratio": 0.963},
            {"case": "tile_multiport 2-port through (gprMax)", "result": "PASS",
             "s11_db": "-21..-22.4", "reciprocity_db": 1.5e-7, "split_residual_pct": 0.7},
            {"case": "wr90_through (gprMax + Palace FEM cross-check, job18/19)", "result": "PASS",
             "s21_fem_vs_fdtd_db": 0.005, "port_kn_err_pct": 0.021, "cutoff_err_pct": 0.012},
            {"case": "patch far-field matched feed (gprMax NTFF, job22)", "result": "PASS",
             "realized_gain_dbi": 5.33, "at_ghz": 9.88, "directivity_dbi": 6.83, "rad_eff": 0.792},
            {"case": "kicad_net RXO_1 real-layout import (gprMax, job23)", "result": "PASS",
             "z0_meas_ohm": 76.5, "z0_model_ohm": 80.5, "err_pct": 5.0,
             "note": "design finding: RXO_1 is a 76 ohm line vs the 50 ohm fab note"},
            {"case": "kicad_net RXO_1 50-ohm redesign w=0.51mm (gprMax, job24)", "result": "PASS",
             "z0_meas_ohm": 52.7, "note": "+5.5% vs 50 ohm target, inside the +/-7% fab band"},
            {"case": "via_coupon stitched F.Cu->In2->F.Cu transition (gprMax, job25)", "result": "PASS",
             "delay_err_pct": 0.30, "s21_vs_cascade_db": 0.30, "s11_vs_cascade": 0.137,
             "reciprocity_db": 1.8e-5,
             "note": "Cohn stripline Z0 (35.9 ohm) cross-checked by 2-D Laplace solve (35.6 ohm); "
                     "IPC-2141 under-predicts in the wide-strip regime"},
        ],
        "note": ("Palace FEM path is implemented and validated for wr90_through only; other structures "
                 "raise honestly (unimplemented). gprMax is CPU-only (no -gpu build) — large domains "
                 "queue behind wall-time budgets. Palace 'Linear' PROM sweep fails its wave-port "
                 "eigensolver on WR-90 — use 'Point' lists; port eigensolver does not converge below "
                 "~9.5 GHz on v0.14.0-dirty, band is 9.5-12.4 GHz."),
        "record": ("ACES repo (github.com/cpa-techallday/aces @ ce3a54a, branch em-gprmax-tier): "
                   "docs/RADAR_EM.md Rev H, pipeline-runs/job17..job25"),
        "rig_image_id": "aces-em:0.1",
    },
}
data["aces-tile-em"] = entry
with open(path, "w", encoding="utf-8", newline="\r\n" if "\r\n" in raw else "\n") as fh:
    fh.write(json.dumps(data, indent=indent, ensure_ascii=False) + ("\n" if raw.endswith("\n") else ""))
print(f"registered aces-tile-em in {path} (indent {indent}); backup {path}.bak-{stamp}")
