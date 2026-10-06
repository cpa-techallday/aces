"""Live Fusion 360 adaptor.

Talks to the Fusion360MCP add-in socket bridge (newline-delimited JSON on
127.0.0.1:9876) so formgen can pull geometry straight out of a *running*
Fusion session -- no manual export step.

Verified wire protocol::

    -> {"type": "execute_code", "params": {"code": "..."}}\\n
    <- {"status": "success", "result": {"executed": true, "result": "<repl value>",
                                        "ok": true, "output": "", "deltas": {...}}}\\n

Fusion's exportManager writes STL in **millimetres**, which is exactly what the
rest of formgen expects; :func:`load_from_fusion` still cross-checks the
exported mesh against the body's API bounding box and rescales if they disagree.

Usage::

    from formgen import fusion
    br = fusion.FusionBridge()
    br.ping()
    bodies = br.list_bodies()                 # every visible body in the design
    mesh, info = fusion.load_from_fusion(ref=bodies[0]["ref"])
"""

from __future__ import annotations

import json
import os
import socket
import tempfile

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876


class FusionError(RuntimeError):
    """Raised when Fusion is unreachable or a command fails inside Fusion."""


# --------------------------------------------------------------------------
# code that runs *inside* Fusion (names app/ui/design/adsk are pre-bound by the
# add-in; the last expression is returned REPL-style)
# --------------------------------------------------------------------------

_LIST_BODIES = """
out=[]
root=design.rootComponent
for b in root.bRepBodies:
    bb=b.boundingBox
    out.append({'ref':b.name,'name':b.name,'component':root.name,'type':'brep',
        'visible':bool(b.isVisible),
        'bbox_mm':[bb.minPoint.x*10,bb.minPoint.y*10,bb.minPoint.z*10,
                   bb.maxPoint.x*10,bb.maxPoint.y*10,bb.maxPoint.z*10]})
for b in root.meshBodies:
    bb=b.boundingBox
    out.append({'ref':b.name,'name':b.name,'component':root.name,'type':'mesh',
        'visible':bool(b.isVisible),
        'bbox_mm':[bb.minPoint.x*10,bb.minPoint.y*10,bb.minPoint.z*10,
                   bb.maxPoint.x*10,bb.maxPoint.y*10,bb.maxPoint.z*10]})
for occ in root.allOccurrences:
    for b in occ.bRepBodies:
        bb=b.boundingBox
        out.append({'ref':occ.fullPathName+'/'+b.name,'name':b.name,
            'component':occ.name,'type':'brep',
            'visible':bool(b.isVisible and occ.isLightBulbOn),
            'bbox_mm':[bb.minPoint.x*10,bb.minPoint.y*10,bb.minPoint.z*10,
                       bb.maxPoint.x*10,bb.maxPoint.y*10,bb.maxPoint.z*10]})
__import__('json').dumps(out)
"""

_SELECTION = """
out=[]
root=design.rootComponent
sel=ui.activeSelections
for k in range(sel.count):
    e=sel.item(k).entity
    if hasattr(e,'boundingBox') and hasattr(e,'objectType') and 'Body' in e.objectType:
        bb=e.boundingBox
        par=e.assemblyContext
        ref=(par.fullPathName+'/'+e.name) if par else e.name
        out.append({'ref':ref,'name':e.name,'type':'brep',
            'component':par.name if par else root.name,'visible':True,
            'bbox_mm':[bb.minPoint.x*10,bb.minPoint.y*10,bb.minPoint.z*10,
                       bb.maxPoint.x*10,bb.maxPoint.y*10,bb.maxPoint.z*10]})
__import__('json').dumps(out)
"""

_EXPORT_TMPL = """
target=None
root=design.rootComponent
_ref={ref!r}
for b in root.bRepBodies:
    if b.name==_ref: target=b
for b in root.meshBodies:
    if b.name==_ref: target=b
if target is None:
    for occ in root.allOccurrences:
        for b in occ.bRepBodies:
            if occ.fullPathName+'/'+b.name==_ref: target=b
if target is None:
    raise RuntimeError('body not found: '+_ref)
bb=target.boundingBox
em=design.exportManager
opts=em.createSTLExportOptions(target, {path!r})
opts.meshRefinement=adsk.fusion.MeshRefinementSettings.MeshRefinement{refine}
em.execute(opts)
__import__('json').dumps({{'path':{path!r},
    'bbox_mm':[bb.minPoint.x*10,bb.minPoint.y*10,bb.minPoint.z*10,
               bb.maxPoint.x*10,bb.maxPoint.y*10,bb.maxPoint.z*10]}})
"""

_EXPORT_DESIGN_STEP = """
em=design.exportManager
em.execute(em.createSTEPExportOptions({path!r}))
{path!r}
"""


def _bbox_size(bbox) -> list:
    return [round(bbox[3] - bbox[0], 3), round(bbox[4] - bbox[1], 3),
            round(bbox[5] - bbox[2], 3)]


class FusionBridge:
    """Newline-delimited-JSON client for the Fusion360MCP add-in socket."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 timeout: float = 300.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    # -- transport ---------------------------------------------------------
    def _send(self, kind: str, params: dict | None = None) -> dict:
        payload = json.dumps({"type": kind, "params": params or {}}) + "\n"
        try:
            s = socket.create_connection((self.host, self.port), timeout=8.0)
        except OSError as exc:
            raise FusionError(
                "cannot reach the Fusion bridge on {}:{} -- is Fusion 360 running "
                "with the Fusion360MCP add-in started?".format(self.host, self.port)
            ) from exc
        try:
            s.settimeout(self.timeout)
            s.sendall(payload.encode("utf-8"))
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(65536)
                if not chunk:
                    raise FusionError("Fusion closed the connection")
                buf += chunk
        finally:
            s.close()
        msg = json.loads(buf.split(b"\n", 1)[0])
        if msg.get("status") == "error":
            raise FusionError(msg.get("message", "unknown Fusion error"))
        return msg.get("result", {})

    def _eval(self, code: str):
        """Run python inside Fusion, return the last expression's value."""
        res = self._send("execute_code", {"code": code})
        if not res.get("ok", False):
            raise FusionError(
                res.get("error_message") or res.get("error_kind")
                or "execute_code failed inside Fusion"
            )
        return res.get("result")

    def _eval_json(self, code: str):
        raw = self._eval(code)
        if isinstance(raw, (dict, list)):
            return raw
        return json.loads(raw)

    # -- queries -----------------------------------------------------------
    def ping(self) -> bool:
        try:
            return bool(self._send("ping").get("pong"))
        except Exception:
            return False

    def scene(self) -> dict:
        return self._send("get_scene_info")

    def design_name(self) -> str:
        try:
            return str(self.scene().get("design_name") or "fusion_design")
        except Exception:
            return "fusion_design"

    def list_bodies(self, visible_only: bool = True) -> list:
        bodies = self._eval_json(_LIST_BODIES)
        for b in bodies:
            b["size_mm"] = _bbox_size(b["bbox_mm"])
        if visible_only:
            bodies = [b for b in bodies if b.get("visible")]
        return bodies

    def selection(self) -> list:
        """Bodies currently selected in the Fusion window (may be empty)."""
        sel = self._eval_json(_SELECTION)
        for b in sel:
            b["size_mm"] = _bbox_size(b["bbox_mm"])
        return sel

    # -- export ------------------------------------------------------------
    def export_body(self, ref: str, path: str, refinement: str = "High"):
        """Export one body to STL (mm); returns (path, info)."""
        path = os.path.abspath(path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if refinement not in ("Low", "Medium", "High"):
            refinement = "High"
        info = self._eval_json(
            _EXPORT_TMPL.format(ref=ref, path=path, refine=refinement)
        )
        if not os.path.exists(path):
            raise FusionError(
                "Fusion reported success but {} was not written".format(path))
        info["ref"] = ref
        info["size_mm"] = _bbox_size(info["bbox_mm"])
        return path, info

    def export_design_step(self, path: str) -> str:
        path = os.path.abspath(path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._eval(_EXPORT_DESIGN_STEP.format(path=path))
        return path


# --------------------------------------------------------------------------
# high-level helpers
# --------------------------------------------------------------------------

def available(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> bool:
    """True when a live Fusion bridge answers a ping."""
    return FusionBridge(host, port).ping()


def pull(ref: str | None = None, dest: str | None = None,
         bridge: "FusionBridge | None" = None, refinement: str = "High"):
    """Export a body from the running Fusion to STL.

    ref=None -> use the current Fusion selection, else the largest visible body.
    Returns (stl_path, info).
    """
    br = bridge or FusionBridge()
    if not br.ping():
        raise FusionError(
            "no live Fusion session -- start Fusion 360 and the Fusion360MCP add-in")
    if ref is None:
        picked = br.selection()
        if not picked:
            bodies = br.list_bodies(visible_only=True)
            if not bodies:
                raise FusionError("the open design has no visible bodies to export")
            picked = [max(bodies, key=lambda b: max(b["size_mm"]))]
        ref = picked[0]["ref"]
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(ref))[-60:]
    if dest is None:
        dest = os.path.join(tempfile.gettempdir(), "formgen_fusion", safe + ".stl")
    elif os.path.isdir(dest) or not os.path.splitext(dest)[1]:
        # a directory (existing or not) -- name the file after the body
        os.makedirs(dest, exist_ok=True)
        dest = os.path.join(dest, safe + ".stl")
    return br.export_body(ref, dest, refinement=refinement)


def load_from_fusion(ref: str | None = None, dest: str | None = None,
                     bridge: "FusionBridge | None" = None,
                     refinement: str = "High"):
    """Pull a body out of the running Fusion and return (trimesh_in_mm, info).

    The exported mesh is cross-checked against the body's Fusion bounding box;
    if the file came back in other units it is rescaled by the clean factor.
    """
    from .loader import load_mesh

    path, info = pull(ref=ref, dest=dest, bridge=bridge, refinement=refinement)
    mesh = load_mesh(path)
    want = info["size_mm"]
    got = [float(v) for v in mesh.extents]
    ratios = [w / g for w, g in zip(want, got) if g > 1e-6 and w > 1e-6]
    if ratios:
        k = sum(ratios) / len(ratios)
        spread = max(ratios) - min(ratios)
        # only correct a consistent, clean unit factor -- never noise
        if spread < 0.01 * k and abs(k - 1.0) > 0.02 and 0.001 < k < 1000:
            mesh.apply_scale(k)
            info["unit_correction"] = round(k, 6)
    info["source"] = path
    return mesh, info
