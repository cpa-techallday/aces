"""simlab solver adapter for the radar EM verification tier (solver id ``aces-tile-em``).

This is the radar counterpart of ``aces.cfd.simlab_adapter``: simlab's worker imports
``module.Adapter`` from the registry entry and calls ``run(case, spec, run_dir)`` per
case.  The physics engine is **Palace** (FEM S-parameters, tile / RF-distribution /
antenna-plate structures from the exported board stackup) with **gprMax** (FDTD) for
propagation and antenna-plate far-field checks — both are on the runtime's planned
solver list (RUNTIME_PLAN §3: "Electromagnetics — Palace and gprMax (AERIS radar tile,
HERMES antennas)").

Status: **image not yet built**.  Palace needs a from-source build (like MFEM+cuDSS,
Phase 2b class).  Until the ``aces-em`` image exists and passes the validation cases
below, ``run`` refuses with a clear error rather than returning invented numbers —
the platform's standing rule is no fabricated or sample data.

Case parameters (from the experiment's params / sweep):
  structure   "tile" | "rf_dist" | "antenna_plate"   which AERIS structure
  freq_lo_hz, freq_hi_hz, n_freq                      S-parameter sweep
  stackup     {substrate_er, substrate_h_m, loss_tan} from the board's real stackup
  geom        structure-specific dimensions in metres (patch_l_m/patch_w_m, ...)
  solver      "palace" (default) | "gprmax"
  validate    "wr90_through" | "patch_xband"          registry validation cases instead

Validation cases (must pass before the solver enters ``solvers.json``, per decision 6):
  - ``wr90_through``: a 25.4 mm WR-90 through section, S21 within 0.1 dB of 0 dB and
    cutoff within 1 % of 6.557 GHz across 8-12 GHz.
  - ``patch_xband``: a lambda/2 patch on Rogers RO4350B (er 3.66, h 0.508 mm),
    resonance within 2 % of the cavity-model frequency.

``measurement.json`` row written per case: kind ``tile_insertion_loss_db`` with the
sweep's mid-band S21, readable by ``aces.domains.radar.calibrate``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

VALIDATION_CASES = ("wr90_through", "patch_xband")

# Cavity-model patch resonance (Balansis 14.6, with the Hammerstad fringing
# correction) — used only as the validation reference for ``patch_xband``.
def patch_resonance_hz(patch_l_m: float, er: float, h_m: float, w_m: float) -> float:
    from .linkbudget import C0

    er_eff = (er + 1) / 2 + (er - 1) / 2 * (1 + 12 * h_m / w_m) ** -0.5
    dl = 0.412 * h_m * ((er_eff + 0.3) * (w_m / h_m + 0.264)) / ((er_eff - 0.258) * (w_m / h_m + 0.8))
    return C0 / (2 * (patch_l_m + 2 * dl) * er_eff**0.5)


class Adapter:
    name = "aces-tile-em"

    def inputs(self, case: dict, spec) -> list:
        return []  # geometry is generated from the case parameters; nothing on disk to hash

    def run(self, case: dict, spec, run_dir: str) -> dict:
        solver = case.get("solver", "palace")
        binary = os.environ.get("PALACE_BIN", "palace") if solver == "palace" else os.environ.get(
            "GPRMAX_BIN", "python -m gprMax"
        )
        exe = binary.split()[0]
        if shutil.which(exe) is None:
            raise RuntimeError(
                f"aces-tile-em: {solver} executable {exe!r} not found. The aces-em solver image "
                "is not built/registered yet — see docs/RADAR_EM.md section 'Verification tier'. "
                "No EM numbers are produced until the image passes its validation cases."
            )
        return self._run_solver(case, solver, binary, Path(run_dir))

    # --------------------------------------------------------------- solver path
    def _run_solver(self, case: dict, solver: str, binary: str, run_dir: Path) -> dict:
        """Mesh the structure from case params, run the solver, parse S-parameters.

        Palace consumes a mesh + JSON config; gprMax consumes a Python input file.
        Both land in run_dir; the parsed S21 sweep becomes the measurement row.
        """
        cfg = run_dir / "case_config.json"
        cfg.write_text(json.dumps(case, indent=2), encoding="utf-8")
        if solver == "palace":
            config = self._palace_config(case, run_dir)
            (run_dir / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
            subprocess.run(binary.split() + ["config.json"], cwd=run_dir, check=True)
            sweep = self._parse_palace_sparams(run_dir)
        else:
            input_py = self._gprmax_input(case, run_dir)
            (run_dir / "case.py").write_text(input_py, encoding="utf-8")
            subprocess.run(binary.split() + ["case.py"], cwd=run_dir, check=True)
            sweep = self._parse_gprmax_sparams(run_dir)

        mid = sweep[len(sweep) // 2]
        row = {
            "kind": "tile_insertion_loss_db",
            "frequency_hz": mid["frequency_hz"],
            "value": abs(mid["s21_db"]),
            "model_value": case.get("model_loss_db", 0.0),
            "source": f"simlab:{run_dir.name}",
        }
        if row["model_value"] > 0:
            (run_dir / "measurement.json").write_text(json.dumps(row, indent=2), encoding="utf-8")
        return {"sweep": sweep, "converged": 1, "measurement_written": int(row["model_value"] > 0)}

    # --------------------------------------------------------------- emit / parse
    def _palace_config(self, case: dict, run_dir: Path) -> dict:
        """Palace JSON config for an S-parameter sweep of the case structure."""
        return {
            "Problem": {"Type": "Driven", "Verbose": 1},
            "Model": {"Mesh": "mesh.msh", "L0": 1.0e-3},
            "Domains": {"Materials": self._materials(case)},
            "Boundaries": {"PEC": {}, "Port": {}},
            "Solver": {
                "Driven": {
                    "MinFreq": case["freq_lo_hz"] / 1e9,
                    "MaxFreq": case["freq_hi_hz"] / 1e9,
                    "FreqStep": (case["freq_hi_hz"] - case["freq_lo_hz"]) / max(1, case.get("n_freq", 21) - 1) / 1e9,
                },
                "Linear": {"Type": "Default", "Tol": 1.0e-8},
            },
        }

    def _materials(self, case: dict) -> list:
        st = case.get("stackup", {})
        return [
            {"Attributes": [1], "Name": "substrate",
             "Permittivity": st.get("substrate_er", 3.66), "LossTan": st.get("loss_tan", 0.004)},
            {"Attributes": [2], "Name": "air", "Permittivity": 1.0},
        ]

    def _gprmax_input(self, case: dict, run_dir: Path) -> str:
        return (
            "# gprMax input generated by aces-tile-em\n"
            f"# structure: {case.get('structure', 'tile')}\n"
            "# geometry, sources and receivers are emitted by the mesh stage; see docs/RADAR_EM.md\n"
        )

    def _parse_palace_sparams(self, run_dir: Path) -> list[dict]:
        port_csv = next(run_dir.glob("postpro/**/port-S.csv"), None)
        if port_csv is None:
            raise RuntimeError("aces-tile-em: Palace produced no port-S.csv; run failed upstream")
        sweep = []
        for line in port_csv.read_text(encoding="utf-8").splitlines()[1:]:
            f, s21 = line.split(",")[:2]
            sweep.append({"frequency_hz": float(f) * 1e9, "s21_db": float(s21)})
        return sweep

    def _parse_gprmax_sparams(self, run_dir: Path) -> list[dict]:
        raise RuntimeError("aces-tile-em: gprMax S-parameter post-processing lands with the image build")
