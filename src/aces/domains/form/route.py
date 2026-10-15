"""form.lattice: run formgen from an ACES spec and record the result as evidence.

Every file of a run (formgen's outputs, ``formgen.log``, ``structure.json``) is written through one
:class:`aces.safeio.OutDir` on a new run folder: never through a link, never over an existing file (audit M1).
"""
from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, field_validator

from ... import manufacturing, safeio
from ...structures import StructureSpec
from ...structures import analyse as frame_analyse

DOMAIN = "form.lattice"


class FormMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    density_g_cm3: PositiveFloat
    source: str = Field(min_length=3, description="datasheet the density comes from")


class Robot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    print_speed_mm_s: PositiveFloat
    jbi: bool = True
    tool: int = Field(0, ge=0)
    user_frame: int = Field(1, ge=0)
    dout: int = Field(1, ge=1, description="extruder enable output OT#(n)")


class ScaleModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    printer: str
    ratio: PositiveFloat | None = None  # None = largest that fits the plate
    base_mm: float = Field(0.0, ge=0.0)
    min_strut_mm: PositiveFloat | None = None


class FormRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    process: str = Field(description="robotic_lattice_extrusion or printed_formwork")
    source: str = Field(description="geometry file (relative to the spec) or 'fusion' for the live selection",
                        json_schema_extra={"aces_path": {"not_paths": ["fusion"]}})
    fusion_body: str | None = None
    body_name: str | None = None
    unit_scale: PositiveFloat | None = None
    mode: Literal["skin", "panel", "infill"]
    strut_mm: PositiveFloat
    thickness_mm: PositiveFloat
    bead_mm: PositiveFloat
    material: FormMaterial
    robot: Robot
    scale_model: ScaleModel | None = None
    max_stitch_mm: PositiveFloat | None = None
    structure: StructureSpec | None = Field(None, description="optional static check of the printed lattice")

    @field_validator("process")
    @classmethod
    def _route(cls, v: str) -> str:
        manufacturing.require(v, DOMAIN)
        return v


def options(req: FormRequirements, out_dir: Path, spec_dir: Path):
    """Translate the spec into formgen pipeline options."""
    from formgen.pipeline import Options  # optional dependency group [formgen]

    from_fusion = req.source == "fusion"
    src = "" if from_fusion else str((spec_dir / req.source).resolve())
    sm = req.scale_model
    kw: dict[str, Any] = dict(
        source="fusion" if from_fusion else "file",
        input=src,
        fusion_ref=req.fusion_body,
        name=req.body_name,
        scale=req.unit_scale,
        mode=req.mode,
        strut=req.strut_mm,
        thickness=req.thickness_mm,
        strut_dia=req.bead_mm,
        density=req.material.density_g_cm3,
        speed=req.robot.print_speed_mm_s,
        max_stitch=req.max_stitch_mm,
        out=str(out_dir),
        want_jbi=req.robot.jbi,
        dout=req.robot.dout,
        tool=req.robot.tool,
        user_frame=req.robot.user_frame,
        model=sm is not None,
    )
    if sm is not None:
        kw.update(printer=sm.printer, ratio=sm.ratio, base_mm=sm.base_mm, min_strut=sm.min_strut_mm)
    return Options(**kw)


def run(req: FormRequirements, out_dir: str | Path | safeio.OutDir, spec_dir: str | Path,
        log=print) -> dict[str, Any]:
    """Run formgen and return (report, artifacts, problems).

    ``out_dir`` is a new folder: a path, or a :class:`aces.safeio.OutDir` the caller opened with ``new=True``.
    """
    from formgen.pipeline import run as formgen_run

    with ExitStack() as stack:
        od = out_dir if isinstance(out_dir, safeio.OutDir) else stack.enter_context(safeio.OutDir(out_dir, new=True))
        return _run(req, od, Path(spec_dir), log, formgen_run)


def _run(req: FormRequirements, od: safeio.OutDir, spec_dir: Path, log, formgen_run) -> dict[str, Any]:
    out = od.path
    lines: list[str] = []

    def _log(msg: str) -> None:
        lines.append(msg)
        log(msg)

    report = formgen_run(options(req, out, spec_dir), log=_log, out=od)
    od.write_text("formgen.log", "\n".join(lines) + "\n")
    outputs = report.get("outputs", {})
    artifacts: dict[str, str] = {"log": str(out / "formgen.log")}
    for key in ("report_json", "lattice_json", "waypoints_csv", "preview_stl", "model_stl"):
        if outputs.get(key):
            artifacts[key] = outputs[key]
    for i, job in enumerate(outputs.get("jbi") or []):
        artifacts[f"jbi_{i:03d}"] = job

    problems = []
    structure = None
    if req.structure is not None and outputs.get("lattice_json"):
        structure = check_structure(req, Path(outputs["lattice_json"]))
        od.write_text("structure.json", json.dumps(structure, indent=2, default=float))
        artifacts["structure_json"] = str(out / "structure.json")
        if structure["max_utilisation"] > 1.0:
            problems.append(f"structural utilisation {structure['max_utilisation']:.2f} > 1.0 "
                            f"({structure['over_unity']} strut-case(s) over unity)")
        lim = req.structure.max_deflection_mm
        if lim is not None and structure["max_deflection_mm"] > lim:
            problems.append(f"deflection {structure['max_deflection_mm']:.1f} mm exceeds the {lim:g} mm limit")
    unreachable = report.get("sequence", {}).get("unreachable_struts", 0)
    if unreachable:
        problems.append(f"{unreachable} strut(s) cannot be printed in any anchored order")
    islands = sum(1 for line in lines if "island(s) still separate" in line)
    if islands:
        problems.append("lattice still has separate islands after stitching")
    warnings = [line.strip().lstrip("! ").strip() for line in lines if line.strip().startswith("!")]
    return {"report": report, "artifacts": artifacts, "problems": problems, "warnings": warnings,
            "structure": structure}


def check_structure(req: FormRequirements, lattice_json: Path) -> dict:
    """Static check of the generated lattice; pressure acts on the outer (or only) skin."""
    lat = json.loads(lattice_json.read_text())
    nodes = lat["nodes"]
    struts = [(i, j) for i, j, _ in lat["struts"]]
    skin = {i for i, j, k in lat["struts"] if k in ("outer", "skin") for i in (i, j)}
    res = frame_analyse(nodes, struts, req.bead_mm, req.material.density_g_cm3 * 1000.0, req.structure,
                        loaded_nodes=sorted(skin) or None)
    return res.as_dict()
