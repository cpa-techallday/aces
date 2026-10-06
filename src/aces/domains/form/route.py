"""form.lattice: run formgen from an ACES spec and record the result as evidence."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, field_validator

from ... import manufacturing

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
    source: str = Field(description="geometry file (relative to the spec) or 'fusion' for the live selection")
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


def run(req: FormRequirements, out_dir: str | Path, spec_dir: str | Path, log=print) -> dict[str, Any]:
    """Run formgen and return (report, artifacts, problems)."""
    from formgen.pipeline import run as formgen_run

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    def _log(msg: str) -> None:
        lines.append(msg)
        log(msg)

    report = formgen_run(options(req, out, Path(spec_dir)), log=_log)
    (out / "formgen.log").write_text("\n".join(lines) + "\n")
    outputs = report.get("outputs", {})
    artifacts: dict[str, str] = {"log": str(out / "formgen.log")}
    for key in ("report_json", "lattice_json", "waypoints_csv", "preview_stl", "model_stl"):
        if outputs.get(key):
            artifacts[key] = outputs[key]
    for i, job in enumerate(outputs.get("jbi") or []):
        artifacts[f"jbi_{i:03d}"] = job

    problems = []
    unreachable = report.get("sequence", {}).get("unreachable_struts", 0)
    if unreachable:
        problems.append(f"{unreachable} strut(s) cannot be printed in any anchored order")
    islands = sum(1 for line in lines if "island(s) still separate" in line)
    if islands:
        problems.append("lattice still has separate islands after stitching")
    warnings = [line.strip().lstrip("! ").strip() for line in lines if line.strip().startswith("!")]
    return {"report": report, "artifacts": artifacts, "problems": problems, "warnings": warnings}
