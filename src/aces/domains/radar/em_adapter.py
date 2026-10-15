"""simlab solver adapter for the radar EM verification tier (solver id ``aces-tile-em``).

This is the radar counterpart of ``aces.cfd.simlab_adapter``: simlab's worker imports
``module.Adapter`` from the registry entry and calls ``run(case, spec, run_dir)`` per
case.  The physics engine is **Palace** (FEM S-parameters, tile / RF-distribution /
antenna-plate structures from the exported board stackup) with **gprMax** (FDTD) for
propagation and antenna-plate far-field checks — both are on the runtime's planned
solver list (RUNTIME_PLAN §3: "Electromagnetics — Palace and gprMax (AERIS radar tile,
HERMES antennas)").  gprMax is GPL-3.0, so it only ever runs as a separate process.

Status: **image not yet built**.  Palace needs a from-source build (like MFEM+cuDSS,
Phase 2b class).  Until the ``aces-em`` image exists and passes the validation cases
below, ``run`` refuses with a clear error rather than returning invented numbers —
the platform's standing rule is no fabricated or sample data.  The gprMax path stops
after its availability check: its input generation and S-parameter post-processing
land with the image build.

Case parameters (from the experiment's params / sweep); nothing has a default that is a
physical property (rules 1, 2):
  structure      "tile" | "rf_dist" | "antenna_plate"    which AERIS structure
  freq_lo_hz, freq_hi_hz, n_freq                          S-parameter sweep
  stackup        {substrate_er, loss_tan, source}         from the board's real stackup
  mesh           Gmsh mesh of the structure (from the board export), in or below the case folder
  mesh_unit_m    length unit of the mesh coordinates, metres (Palace ``L0``)
  attributes     {substrate: [...], air: [...], pec: [...], absorbing: [...] (optional)}
  ports          [{index, attributes, r_ohm, direction}]  lumped ports, at least two (S21); port 1 driven
  solver         "palace" (default) | "gprmax"
  timeout_s      solver wall-clock limit, seconds (default 4 h)
  model_loss_db  the fast tier's insertion loss for this case; without it no measurement row is written

Validation cases (must pass before the solver enters ``solvers.json``: D5 / rule 3, a
registered solver signs off, and the validation record is what registers it):
  - ``wr90_through``: a 25.4 mm WR-90 through section, S21 within 0.1 dB of 0 dB and
    cutoff within 1 % of 6.557 GHz across 8-12 GHz.
  - ``patch_xband``: a lambda/2 patch on Rogers RO4350B (er 3.66, h 0.508 mm),
    resonance within 2 % of the cavity-model frequency.

``measurement.json`` row written per converged case: kind ``tile_insertion_loss_db`` with the
sweep's mid-band insertion loss (-|S21| dB), readable by ``aces.domains.radar.calibrate``.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path

from ... import proc, safeio

VALIDATION_CASES = ("wr90_through", "patch_xband")
DEFAULT_TIMEOUT_S = 4 * 3600
# Palace's iterative solvers print a warning containing this when a solve stops at MaxIts without
# reaching Tol.  Confirm the wording against the image's pinned Palace version in its validation run.
NOT_CONVERGED = re.compile(r"did not converge|not converged|diverged", re.IGNORECASE)
S21_DB_HEADER = re.compile(r"\|\s*S\[2\]\[1\]\s*\|\s*\(dB\)")
F_GHZ_HEADER = re.compile(r"^\s*f\s*\(GHz\)")


# Cavity-model patch resonance (Balanis, Antenna Theory, ch. 14: rectangular patch with the
# Hammerstad fringing correction) — used only as the validation reference for ``patch_xband``.
def patch_resonance_hz(patch_l_m: float, er: float, h_m: float, w_m: float) -> float:
    from .linkbudget import C0

    er_eff = (er + 1) / 2 + (er - 1) / 2 * (1 + 12 * h_m / w_m) ** -0.5
    dl = 0.412 * h_m * ((er_eff + 0.3) * (w_m / h_m + 0.264)) / ((er_eff - 0.258) * (w_m / h_m + 0.8))
    return C0 / (2 * (patch_l_m + 2 * dl) * er_eff**0.5)


def palace_config(case: dict) -> dict:
    """A complete Palace driven-solve config for the case, or ValueError naming what the case lacks."""
    missing = []
    for k in ("mesh", "mesh_unit_m", "freq_lo_hz", "freq_hi_hz"):
        if case.get(k) in (None, ""):
            missing.append(k)
    st = case.get("stackup") or {}
    for k in ("substrate_er", "loss_tan", "source"):
        if st.get(k) in (None, ""):
            missing.append(f"stackup.{k}")
    att = case.get("attributes") or {}
    for k in ("substrate", "air", "pec"):
        if not att.get(k):
            missing.append(f"attributes.{k}")
    ports = case.get("ports") or []
    if len(ports) < 2:
        missing.append("ports (at least two lumped ports, for S21)")
    for i, p in enumerate(ports):
        for k in ("index", "attributes", "r_ohm", "direction"):
            if p.get(k) in (None, "", []):
                missing.append(f"ports[{i}].{k}")
    if missing:
        raise ValueError("aces-tile-em: the case is incomplete, so no Palace config is written; missing: "
                         + ", ".join(missing))
    lo, hi, n = float(case["freq_lo_hz"]), float(case["freq_hi_hz"]), int(case.get("n_freq", 21))
    if not (0 < lo < hi and n >= 2):
        raise ValueError("aces-tile-em: need 0 < freq_lo_hz < freq_hi_hz and n_freq >= 2")
    boundaries: dict = {
        "PEC": {"Attributes": [int(a) for a in att["pec"]]},
        "LumpedPort": [{"Index": int(p["index"]), "R": float(p["r_ohm"]),
                        "Attributes": [int(a) for a in p["attributes"]], "Direction": str(p["direction"]),
                        "Excitation": int(p["index"]) == 1} for p in ports],
    }
    if att.get("absorbing"):
        boundaries["Absorbing"] = {"Attributes": [int(a) for a in att["absorbing"]], "Order": 1}
    return {
        "Problem": {"Type": "Driven", "Verbose": 2, "Output": "postpro"},
        "Model": {"Mesh": str(case["mesh"]), "L0": float(case["mesh_unit_m"])},
        "Domains": {"Materials": [
            {"Attributes": [int(a) for a in att["substrate"]], "Permittivity": float(st["substrate_er"]),
             "LossTan": float(st["loss_tan"])},
            {"Attributes": [int(a) for a in att["air"]], "Permittivity": 1.0},
        ]},
        "Boundaries": boundaries,
        "Solver": {
            "Order": int(case.get("order", 2)),
            "Driven": {"MinFreq": lo / 1e9, "MaxFreq": hi / 1e9, "FreqStep": (hi - lo) / (n - 1) / 1e9,
                       "SaveStep": 0},
            "Linear": {"Type": "Default", "KSPType": "GMRES", "Tol": 1.0e-8, "MaxIts": 400},
        },
    }


def parse_palace_s21(text: str) -> list[dict]:
    """Rows of Palace's ``port-S.csv`` as {frequency_hz, s21_db}, choosing |S[2][1]| (dB) by its header."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise RuntimeError("aces-tile-em: port-S.csv is empty")
    header = [h.strip() for h in lines[0].split(",")]
    i_f = next((i for i, h in enumerate(header) if F_GHZ_HEADER.match(h)), None)
    i_s21 = next((i for i, h in enumerate(header) if S21_DB_HEADER.search(h)), None)
    if i_f is None or i_s21 is None:
        raise RuntimeError(f"aces-tile-em: port-S.csv has no 'f (GHz)' and '|S[2][1]| (dB)' columns: {header}")
    sweep = []
    for ln in lines[1:]:
        cells = ln.split(",")
        sweep.append({"frequency_hz": float(cells[i_f]) * 1e9, "s21_db": float(cells[i_s21])})
    return sweep


def palace_converged(log_text: str, sweep: list[dict], lo_hz: float, hi_hz: float) -> tuple[bool, list[str]]:
    """Convergence as the solver reported it: no non-convergence warning in its log, and a finite sweep
    that covers the requested band."""
    problems = []
    hits = [ln.strip() for ln in log_text.splitlines() if NOT_CONVERGED.search(ln)]
    if hits:
        problems.append(f"solver reported non-convergence ({len(hits)} line(s), first: {hits[0][:160]})")
    if not sweep:
        problems.append("no S-parameter rows")
    else:
        if not all(math.isfinite(r["s21_db"]) for r in sweep):
            problems.append("non-finite S21 in the sweep")
        fs = [r["frequency_hz"] for r in sweep]
        tol = 1e-6 * hi_hz
        if min(fs) > lo_hz + tol or max(fs) < hi_hz - tol:
            problems.append(f"sweep covers {min(fs) / 1e9:.4g}-{max(fs) / 1e9:.4g} GHz, not the requested "
                            f"{lo_hz / 1e9:.4g}-{hi_hz / 1e9:.4g} GHz")
    return not problems, problems


def gprmax_command() -> list[str]:
    """``GPRMAX_PYTHON -m gprMax``: gprMax runs in its own interpreter, as a separate process (GPL-3.0)."""
    return [os.environ.get("GPRMAX_PYTHON", "python"), "-m", "gprMax"]


def check_gprmax() -> list[str]:
    """The gprMax command if that interpreter exists and can import gprMax; RuntimeError otherwise.

    Finding ``python`` on PATH says nothing about gprMax, so the import itself is tried."""
    cmd = gprmax_command()
    if shutil.which(cmd[0]) is None:
        raise RuntimeError(f"aces-tile-em: gprMax interpreter {cmd[0]!r} not found (set GPRMAX_PYTHON)")
    try:
        cp = proc.run([cmd[0], "-c", "import gprMax"], env=proc.minimal_env(), timeout=120)
    except subprocess.TimeoutExpired:
        raise RuntimeError("aces-tile-em: importing gprMax timed out") from None
    if cp.returncode != 0:
        err = (cp.stderr or "").strip().splitlines()
        raise RuntimeError(f"aces-tile-em: gprMax is not importable by {cmd[0]!r}: {err[-1] if err else 'no output'}")
    return cmd


class Adapter:
    name = "aces-tile-em"

    def inputs(self, case: dict, spec) -> list:
        # The mesh named by the case is checked in run(); hashing it with the case inputs lands with the image.
        return []

    def run(self, case: dict, spec, run_dir: str) -> dict:
        precision = getattr(getattr(spec, "resources", None), "precision", "float64")
        if precision != "float64":
            raise ValueError("aces-tile-em runs in float64 only (rule 6)")
        solver = case.get("solver", "palace")
        if solver == "gprmax":
            check_gprmax()
            raise RuntimeError("aces-tile-em: gprMax input generation and S-parameter post-processing land with "
                               "the aces-em image build; no gprMax run is made until then (docs/RADAR_EM.md §4)")
        if solver != "palace":
            raise ValueError(f"aces-tile-em: unknown solver {solver!r}; use palace or gprmax")
        exe = os.environ.get("PALACE_BIN", "palace")
        if shutil.which(exe) is None:
            raise RuntimeError(
                f"aces-tile-em: palace executable {exe!r} not found. The aces-em solver image "
                "is not built/registered yet — see docs/RADAR_EM.md section 'Verification tier'. "
                "No EM numbers are produced until the image passes its validation cases."
            )
        return self._run_palace(case, exe, Path(run_dir))

    # --------------------------------------------------------------- solver path
    def _run_palace(self, case: dict, exe: str, run_dir: Path) -> dict:
        config = palace_config(case)  # refuses an incomplete case before anything runs
        mesh = Path(case["mesh"])
        mesh_path = mesh if mesh.is_absolute() else run_dir / mesh
        if safeio.is_link(mesh_path) or not mesh_path.is_file():
            raise FileNotFoundError(f"aces-tile-em: mesh {mesh_path} is missing or a link")
        out = safeio.OutDir(run_dir, mkdir=False)
        try:
            out.write_text("case_config.json", json.dumps(case, indent=2, default=str), overwrite=True)
            out.write_text("config.json", json.dumps(config, indent=2), overwrite=True)
            timeout = float(case.get("timeout_s", DEFAULT_TIMEOUT_S))
            try:
                cp = proc.run([exe, "config.json"], cwd=run_dir, timeout=timeout,
                              env=proc.minimal_env(keep=("OMP_NUM_THREADS", "CUDA_VISIBLE_DEVICES")))
            except subprocess.TimeoutExpired as e:
                out.write_text("palace.log", (e.output or "") + (e.stderr or ""), overwrite=True)  # text mode
                raise RuntimeError(f"aces-tile-em: Palace did not finish within {timeout:g} s; stopped") from None
            log = (cp.stdout or "") + (cp.stderr or "")
            out.write_text("palace.log", log, overwrite=True)
            if cp.returncode != 0:
                tail = "\n".join(log.strip().splitlines()[-5:])
                raise RuntimeError(f"aces-tile-em: Palace exited with {cp.returncode}:\n{tail}")
            port_csv = run_dir / "postpro" / "port-S.csv"
            if safeio.is_link(port_csv) or not port_csv.is_file():
                raise RuntimeError("aces-tile-em: Palace produced no postpro/port-S.csv; run failed upstream")
            sweep = parse_palace_s21(safeio.read_text_limited(port_csv, 50_000_000))
            converged, problems = palace_converged(log, sweep, float(case["freq_lo_hz"]), float(case["freq_hi_hz"]))

            written = False
            if converged:
                mid = sweep[len(sweep) // 2]
                loss_db = -mid["s21_db"]
                if loss_db < 0:
                    problems.append(f"S21 {mid['s21_db']:+.3f} dB at mid-band: gain from a passive structure")
                elif case.get("model_loss_db") is None:
                    problems.append("no model_loss_db in the case: no measurement row written")
                else:
                    row = {
                        "kind": "tile_insertion_loss_db",
                        "frequency_hz": mid["frequency_hz"],
                        "value": loss_db,
                        "model_value": float(case["model_loss_db"]),
                        "source": f"simlab:{run_dir.name}",
                    }
                    out.write_text("measurement.json", json.dumps(row, indent=2), overwrite=True)
                    written = True
        finally:
            out.close()
        return {"sweep": sweep, "converged": int(converged), "measurement_written": int(written),
                "problems": problems}
