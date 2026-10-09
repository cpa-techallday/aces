"""Push generated geometry into the running Fusion session.

Speaks the community Fusion360MCP add-in's socket protocol (newline-delimited JSON on
127.0.0.1:9876), the same adaptor formgen uses, so it needs no Autodesk licence for an
MCP server. Inside Fusion the names app, ui, design and adsk are pre-bound and the last
expression of the submitted code is returned.

    python -m gendesign.fusion_bridge out/cantilever3d/design.stl --name cantilever --new-doc
    python -m gendesign.fusion_bridge out/aerospike/aerospike.step --new-doc
"""
from __future__ import annotations

import argparse
import json
import os
import socket

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876


class FusionBridgeError(RuntimeError):
    pass


_NEW_DOC = """
doc=app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
d=adsk.fusion.Design.cast(app.activeProduct)
d.designType=adsk.fusion.DesignTypes.ParametricDesignType
__import__('json').dumps({'document':doc.name})
"""

_IMPORT_STL = """
d=adsk.fusion.Design.cast(app.activeProduct)
root=d.rootComponent
units=adsk.fusion.MeshUnits.MillimeterMeshUnit if {units!r}=='mm' else adsk.fusion.MeshUnits.CentimeterMeshUnit
if d.designType==adsk.fusion.DesignTypes.ParametricDesignType:
    bf=root.features.baseFeatures.add()
    bf.startEdit()
    mbs=root.meshBodies.add({path!r}, units, bf)
    bf.finishEdit()
else:
    mbs=root.meshBodies.add({path!r}, units)
names=[]
for i in range(mbs.count):
    mb=mbs.item(i)
    if {name!r}:
        mb.name={name!r} if mbs.count==1 else {name!r}+'_'+str(i+1)
    names.append(mb.name)
bb=mbs.item(0).boundingBox if mbs.count else None
__import__('json').dumps({{'imported':mbs.count,'names':names,'document':d.parentDocument.name,
  'bbox_mm':[bb.minPoint.x*10,bb.minPoint.y*10,bb.minPoint.z*10,bb.maxPoint.x*10,bb.maxPoint.y*10,bb.maxPoint.z*10] if bb else None}})
"""

_IMPORT_STEP = """
d=adsk.fusion.Design.cast(app.activeProduct)
im=app.importManager
opts=im.createSTEPImportOptions({path!r})
opts.isViewFit=True
ok=im.importToTarget(opts, d.rootComponent)
__import__('json').dumps({{'ok':bool(ok),'document':d.parentDocument.name,'occurrences':d.rootComponent.occurrences.count,'bodies':d.rootComponent.bRepBodies.count}})
"""


class FusionBridge:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 300.0):
        self.host, self.port, self.timeout = host, port, timeout

    def _send(self, kind: str, params: dict | None = None) -> dict:
        payload = json.dumps({"type": kind, "params": params or {}}) + "\n"
        try:
            s = socket.create_connection((self.host, self.port), timeout=8.0)
        except OSError as exc:
            raise FusionBridgeError(f"cannot reach the Fusion bridge on {self.host}:{self.port}; "
                                    "is Fusion running with the Fusion360MCP add-in started?") from exc
        try:
            s.settimeout(self.timeout)
            s.sendall(payload.encode("utf-8"))
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(65536)
                if not chunk:
                    raise FusionBridgeError("Fusion closed the connection")
                buf += chunk
        finally:
            s.close()
        msg = json.loads(buf.split(b"\n", 1)[0])
        if msg.get("status") == "error":
            raise FusionBridgeError(msg.get("message", "unknown Fusion error"))
        return msg.get("result", {})

    def _eval(self, code: str):
        res = self._send("execute_code", {"code": code})
        if not res.get("ok", False):
            raise FusionBridgeError(res.get("error_message") or res.get("error_kind") or "execute_code failed inside Fusion")
        return res.get("result")

    def _eval_json(self, code: str):
        raw = self._eval(code)
        return raw if isinstance(raw, (dict, list)) else json.loads(raw)

    def ping(self) -> bool:
        try:
            return bool(self._send("ping").get("pong"))
        except Exception:
            return False

    def new_document(self) -> dict:
        return self._eval_json(_NEW_DOC)

    def import_stl(self, path: str, name: str | None = None, units: str = "mm") -> dict:
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        return self._eval_json(_IMPORT_STL.format(path=path, name=name or "", units=units))

    def import_step(self, path: str) -> dict:
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        return self._eval_json(_IMPORT_STEP.format(path=path))


def main(argv=None):
    ap = argparse.ArgumentParser(description="import an STL (mm) or STEP into the running Fusion design")
    ap.add_argument("path")
    ap.add_argument("--name", default=None, help="mesh body name (STL only)")
    ap.add_argument("--new-doc", action="store_true", help="create a fresh parametric design first")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    a = ap.parse_args(argv)
    br = FusionBridge(port=a.port)
    if not br.ping():
        raise SystemExit("Fusion bridge not answering on :%d" % a.port)
    if a.new_doc:
        print("new document:", br.new_document())
    ext = os.path.splitext(a.path)[1].lower()
    if ext in (".step", ".stp"):
        print(br.import_step(a.path))
    else:
        print(br.import_stl(a.path, a.name))


if __name__ == "__main__":
    main()
