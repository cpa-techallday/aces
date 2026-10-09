"""`simlab export`: pull geometry and a parameter snapshot out of the running Fusion session and
write a case folder the runtime can hash and run.

    python -m simlab export --out cases/my_part --id my-part-check --solver step-structures \
        [--component NAME] [--stl] [--params params.json]

Writes  <out>/geometry.step  (+ geometry.stl)        the exact B-rep that was analysed
        <out>/fusion_snapshot.json                     document, version, user parameters, bodies, hashes
        <out>/experiment.yaml                          geometry.source=fusion, geometry.ref=geometry.step

Talks to Fusion through the community bridge on :9876 (gendesign.fusion_bridge). The export is
read-only for the design: nothing is modified, nothing is saved.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from typing import Optional

from gendesign.fusion_bridge import FusionBridge

from .spec import sha256_file

_SNAPSHOT = """
import json
d=adsk.fusion.Design.cast(app.activeProduct)
root=d.rootComponent
doc=d.parentDocument
um=d.unitsManager
def bb(b):
    try:
        x=b.boundingBox
        return [x.minPoint.x*10,x.minPoint.y*10,x.minPoint.z*10,x.maxPoint.x*10,x.maxPoint.y*10,x.maxPoint.z*10]
    except Exception:
        return None
def mat(b):
    try:
        return b.material.name if b.material else None
    except Exception:
        return None
ups=[{'name':p.name,'expression':p.expression,'value_cm':p.value,'unit':p.unit,'comment':p.comment} for p in d.userParameters]
bodies=[]
def walk(comp, path):
    for b in comp.bRepBodies:
        bodies.append({'component':path,'name':b.name,'volume_mm3':b.volume*1000.0,'area_mm2':b.area*100.0,
                       'bbox_mm':bb(b),'material':mat(b),'visible':b.isVisible})
for b in root.bRepBodies:
    bodies.append({'component':root.name,'name':b.name,'volume_mm3':b.volume*1000.0,'area_mm2':b.area*100.0,'bbox_mm':bb(b),'material':mat(b),'visible':b.isVisible})
occs=[]
for o in root.allOccurrences:
    occs.append({'name':o.name,'component':o.component.name,'bodies':o.component.bRepBodies.count,'visible':o.isVisible})
    walk(o.component, o.name)
try:
    ver=doc.dataFile.versionNumber if doc.dataFile else None
    vid=doc.dataFile.id if doc.dataFile else None
except Exception:
    ver=None; vid=None
json.dumps({'document':doc.name,'saved':doc.isSaved,'version':ver,'data_id':vid,
  'design_type':('parametric' if d.designType==adsk.fusion.DesignTypes.ParametricDesignType else 'direct'),
  'units':um.defaultLengthUnits,'timeline_count':(d.timeline.count if d.designType==adsk.fusion.DesignTypes.ParametricDesignType else None),
  'user_parameters':ups,'bodies':bodies,'occurrences':occs,'root_component':root.name})
"""

_EXPORT_STEP = """
import json
d=adsk.fusion.Design.cast(app.activeProduct)
root=d.rootComponent
target=root
if {component!r}:
    target=None
    for o in root.allOccurrences:
        if o.name=={component!r} or o.component.name=={component!r}:
            target=o.component; break
    if target is None:
        raise RuntimeError('component not found: '+{component!r})
em=d.exportManager
opts=em.createSTEPExportOptions({path!r}, target)
ok=em.execute(opts)
json.dumps({{'ok':bool(ok),'target':target.name,'bodies':target.bRepBodies.count}})
"""

_EXPORT_STL = """
import json
d=adsk.fusion.Design.cast(app.activeProduct)
root=d.rootComponent
target=root
if {component!r}:
    target=None
    for o in root.allOccurrences:
        if o.name=={component!r} or o.component.name=={component!r}:
            target=o; break
    if target is None:
        raise RuntimeError('component not found: '+{component!r})
em=d.exportManager
opts=em.createSTLExportOptions(target, {path!r})
opts.meshRefinement=adsk.fusion.MeshRefinementSettings.MeshRefinementHigh
opts.isBinaryFormat=True
opts.sendToPrintUtility=False
ok=em.execute(opts)
json.dumps({{'ok':bool(ok)}})
"""


def snapshot(bridge: FusionBridge) -> dict:
    return bridge._eval_json(_SNAPSHOT)


def export_step(bridge: FusionBridge, path: str, component: Optional[str] = None) -> dict:
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return bridge._eval_json(_EXPORT_STEP.format(path=path, component=component or ""))


def export_stl(bridge: FusionBridge, path: str, component: Optional[str] = None) -> dict:
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return bridge._eval_json(_EXPORT_STL.format(path=path, component=component or ""))


def export_case(out_dir: str, exp_id: str, solver: str = "step-structures", physics: str = "structures",
                project: str = "default", owner: str = "aaron", component: Optional[str] = None,
                stl: bool = False, params: Optional[dict] = None, sweep: Optional[dict] = None,
                outputs: Optional[list] = None, description: str = "", bridge: Optional[FusionBridge] = None,
                step_name: str = "geometry.step", write_spec: bool = True) -> dict:
    """Export STEP (+STL) and the parameter snapshot; write experiment.yaml. Returns the snapshot."""
    import yaml

    br = bridge or FusionBridge()
    if not br.ping():
        raise RuntimeError("Fusion bridge not answering on :9876 (Fusion running with the Fusion360MCP add-in?)")
    os.makedirs(out_dir, exist_ok=True)
    step_path = os.path.join(out_dir, step_name)
    res = export_step(br, step_path, component)
    if not res.get("ok") or not os.path.isfile(step_path):
        raise RuntimeError(f"STEP export failed: {res}")
    snap = snapshot(br)
    snap["exported_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    snap["export"] = {"component": component or snap.get("root_component"), "step": os.path.basename(step_path),
                      "step_sha256": sha256_file(step_path), "step_bytes": os.path.getsize(step_path)}
    if stl:
        stl_path = os.path.join(out_dir, os.path.splitext(step_name)[0] + ".stl")
        export_stl(br, stl_path, component)
        snap["export"]["stl"] = os.path.basename(stl_path)
        snap["export"]["stl_sha256"] = sha256_file(stl_path)
    snap_path = os.path.join(out_dir, os.path.splitext(step_name)[0] + "_snapshot.json" if step_name != "geometry.step" else "fusion_snapshot.json")
    with open(snap_path, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2)
    if write_spec:
        spec = {
            "id": exp_id, "project": project, "owner": owner, "physics": physics, "solver": solver,
            "description": description or f"Exported from Fusion document '{snap['document']}' (version {snap.get('version')}) on {snap['exported_utc'][:19]}Z",
            "geometry": {"source": "fusion", "ref": os.path.basename(step_path)},
            "params": params or {}, "sweep": sweep or {},
            "outputs": outputs or ["compliance_Nmm", "umax_mm", "vm_max_MPa", "mass_kg", "converged"],
            "resources": {"precision": "float64", "vram_gb": 8},
            "tags": {"fusion_document": str(snap["document"]), "fusion_version": str(snap.get("version")),
                     "fusion_snapshot": os.path.basename(snap_path)},
        }
        with open(os.path.join(out_dir, "experiment.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(spec, f, sort_keys=False)
    return snap


def main(argv=None):
    ap = argparse.ArgumentParser(description="export the active Fusion design into a simlab case folder")
    ap.add_argument("--out", required=True)
    ap.add_argument("--id", required=True, help="experiment id")
    ap.add_argument("--solver", default="step-structures")
    ap.add_argument("--physics", default="structures")
    ap.add_argument("--project", default="default")
    ap.add_argument("--owner", default="aaron")
    ap.add_argument("--component", default=None, help="occurrence/component name; default = whole root")
    ap.add_argument("--stl", action="store_true", help="also export a high-refinement STL")
    ap.add_argument("--params", default=None, help="JSON file with the solver params block (supports, loads, elem_mm...)")
    ap.add_argument("--description", default="")
    a = ap.parse_args(argv)
    params = None
    if a.params:
        with open(a.params, "r", encoding="utf-8") as f:
            params = json.load(f)
    snap = export_case(a.out, a.id, a.solver, a.physics, a.project, a.owner, a.component, a.stl, params,
                       description=a.description)
    print(json.dumps({"document": snap["document"], "version": snap.get("version"), "user_parameters": len(snap["user_parameters"]),
                      "bodies": len(snap["bodies"]), "export": snap["export"], "out": os.path.abspath(a.out)}, indent=2))


if __name__ == "__main__":
    main()
