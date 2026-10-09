"""aces golden: the deliverable. The STL that was solved is the STL that ships: <label>/<label>.stl is a byte copy of
the verify case's part.stl, with <label>/<label>.json (aces-golden-winner/1) describing it. The label is "winner" on
PASS and "candidate" otherwise. No STEP and no 3MF: the part is an organic mesh (marching cubes of an implicit field),
and a STEP of it would be a faceted pretence of a B-rep."""
from __future__ import annotations

import shutil
from pathlib import Path

from . import METRICS, SCHEMA, read_json, sha256_file

STEP_REASON = ("the part is an organic mesh (marching cubes of an implicit field); a STEP would be a faceted pretence "
               "of a B-rep")
RENDER_FILES = (("iso", "{label}_iso.png"), ("stress", "{label}_stress.png"), ("density_slices", "density_slices.png"),
                ("vm_slices", "vm_slices.png"), ("disp_slices", "disp_slices.png"), ("cutaway", "{label}_cutaway.png"))


def _rel(run, p: Path) -> str:
    return Path(p).resolve().relative_to(Path(run.root).resolve()).as_posix()


def existing_renders(run, label: str) -> dict:
    """The renders already in <label>/ (winner_figures records them again after it draws them)."""
    ldir = Path(run.root) / label
    out = {}
    for name, pattern in RENDER_FILES:
        p = ldir / pattern.format(label=label)
        if p.is_file():
            out[name] = _rel(run, p)
    return out


def export_winner(run, case_id: str, eff: dict, *, label: str = "winner") -> dict:
    """Byte-copy <case>/part.stl to <label>/<label>.stl and write <label>/<label>.json (section 8.7). A case that
    produced no part gets a record that says so (stl null) instead of a file."""
    import trimesh

    from .organic import mesh_stats

    cdir = Path(run.root) / case_id
    ldir = Path(run.root) / label
    ldir.mkdir(parents=True, exist_ok=True)
    res = read_json(cdir / "results.json", {}) or {}
    case = read_json(cdir / "case.json", {}) or {}
    status = read_json(cdir / "status.json", {}) or {}
    val = read_json(Path(run.root) / "validation.json", {}) or {}
    params = res.get("design") or {k: case[k] for k in eff["derived"]["variables"] if k in case}
    src = cdir / "part.stl"
    doc = {"schema": SCHEMA["winner"], "label": label, "case_id": case_id, "params": params,
           "metrics": {k: res.get(k) for k in METRICS} if res else None, "grade": val.get("grade"),
           "verdict": val.get("verdict"), "stl": None, "mass_kg": res.get("mass_kg"), "density": None,
           "step": {"written": False, "reason": STEP_REASON}, "renders": {}}
    if not src.is_file():
        doc["note"] = (f"not exported: {case_id} produced no part "
                       f"({status.get('design_status') or 'no status recorded'})")
        run.write_json(f"{label}/{label}.json", doc)
        return doc
    dst = ldir / f"{label}.stl"
    shutil.copyfile(src, dst)
    st = mesh_stats(trimesh.load(str(dst), force="mesh"))
    doc["stl"] = {"path": _rel(run, dst), "sha256": sha256_file(dst), "faces": st["faces"], "vertices": st["vertices"],
                  "watertight": st["watertight"], "solids": st["solids"], "cavities": st["cavities"],
                  "mesh_volume_mm3": st["mesh_volume_mm3"], "area_mm2": st["area_mm2"], "bbox_mm": st["bbox_mm"],
                  "genus": st["genus"]}
    dens = cdir / "generator" / "density.npy"
    if dens.is_file():
        doc["density"] = {"path": _rel(run, dens), "sha256": sha256_file(dens)}
    doc["renders"] = existing_renders(run, label)
    run.write_json(f"{label}/{label}.json", doc)
    return doc
