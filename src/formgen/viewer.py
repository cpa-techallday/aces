"""Standalone interactive HTML viewer for a lattice (three.js core from cdnjs)."""

from __future__ import annotations

import json

import numpy as np

_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  html,body{margin:0;height:100%;background:#0e1116;color:#dce3ec;
    font:13px/1.5 -apple-system,'Segoe UI',Roboto,sans-serif;overflow:hidden}
  #hud{position:fixed;top:12px;left:14px;background:rgba(14,17,22,.82);
    border:1px solid #2a3342;border-radius:10px;padding:10px 14px;max-width:330px}
  #hud h1{font-size:14px;margin:0 0 4px;color:#7fd1b9}
  #hud .k{color:#8b98ab}
  #hud label{display:block;margin-top:6px;cursor:pointer}
  #seq{width:100%;margin-top:4px}
  a{color:#6ab0f3}
</style>
</head>
<body>
<div id="hud">
  <h1>__TITLE__</h1>
  <div><span class="k">nodes</span> __NNODES__ · <span class="k">struts</span> __NSTRUTS__
   · <span class="k">total</span> __TOTLEN__ m</div>
  <div><span class="k">bbox</span> __BBOX__ mm</div>
  <label><input id="animate" type="checkbox"> play print sequence</label>
  <input id="seq" type="range" min="0" max="__NSTRUTS__" value="__NSTRUTS__">
  <div class="k">drag = orbit · wheel = zoom · shift-drag = pan</div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script>
const DATA = __DATA__;
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0e1116);
const W = () => innerWidth, H = () => innerHeight;
const camera = new THREE.PerspectiveCamera(50, W()/H(), 1, 1e6);
const renderer = new THREE.WebGLRenderer({antialias:true});
renderer.setSize(W(), H()); renderer.setPixelRatio(devicePixelRatio);
document.body.appendChild(renderer.domElement);

const nodes = DATA.nodes, struts = DATA.struts, order = DATA.print_order;
const box = new THREE.Box3();
nodes.forEach(p => box.expandByPoint(new THREE.Vector3(p[0], p[2], -p[1])));
const center = box.getCenter(new THREE.Vector3());
const size = box.getSize(new THREE.Vector3()).length();

const KIND_COLOR = {outer:0x7fd1b9, inner:0x4d8fd1, tie:0xd1a24d, web:0xb35fc9,
                    skin:0x7fd1b9, infill:0x8b98ab};
const pos = new Float32Array(struts.length * 6);
const col = new Float32Array(struts.length * 6);
const rank = new Array(struts.length).fill(0);
order.forEach((st, k) => { if (st.strut !== undefined) rank[st.strut] = k; });
struts.forEach((s, k) => {
  const a = nodes[s[0]], b = nodes[s[1]];
  pos.set([a[0],a[2],-a[1], b[0],b[2],-b[1]], k*6);
  const c = new THREE.Color(KIND_COLOR[s[2]] ?? 0xffffff);
  col.set([c.r,c.g,c.b, c.r,c.g,c.b], k*6);
});
const geo = new THREE.BufferGeometry();
geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
const lines = new THREE.LineSegments(geo,
  new THREE.LineBasicMaterial({vertexColors:true}));
scene.add(lines);

const grid = new THREE.GridHelper(size*1.4, 20, 0x2a3342, 0x1a212c);
grid.position.set(center.x, box.min.y, center.z);
scene.add(grid);

let theta = 0.9, phi = 1.05, radius = size * 1.35;
let panX = 0, panY = 0;
function updateCam(){
  camera.position.set(
    center.x + panX + radius*Math.sin(phi)*Math.cos(theta),
    center.y + panY + radius*Math.cos(phi),
    center.z + radius*Math.sin(phi)*Math.sin(theta));
  camera.lookAt(center.x + panX, center.y + panY, center.z);
}
let drag = null;
addEventListener('pointerdown', e => drag = {x:e.clientX, y:e.clientY, shift:e.shiftKey});
addEventListener('pointerup', () => drag = null);
addEventListener('pointermove', e => {
  if(!drag) return;
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  if (drag.shift){ panX -= dx * radius/900; panY += dy * radius/900; }
  else { theta += dx*0.006; phi = Math.min(2.9, Math.max(0.15, phi - dy*0.006)); }
  drag = {x:e.clientX, y:e.clientY, shift:e.shiftKey};
});
addEventListener('wheel', e => { radius = Math.max(size*0.15, radius * (1 + Math.sign(e.deltaY)*0.08)); });
addEventListener('resize', () => { camera.aspect = W()/H(); camera.updateProjectionMatrix(); renderer.setSize(W(), H()); });

const seqEl = document.getElementById('seq');
const animEl = document.getElementById('animate');
function applyCut(n){ geo.setDrawRange(0, struts.length*2);
  // reorder-free cut: hide struts with rank >= n by collapsing them
  for (let k = 0; k < struts.length; k++){
    const show = rank[k] < n;
    const a = nodes[struts[k][0]], b = nodes[struts[k][1]];
    if (show){ pos.set([a[0],a[2],-a[1], b[0],b[2],-b[1]], k*6); }
    else { pos.set([0,-1e9,0, 0,-1e9,0], k*6); }
  }
  geo.attributes.position.needsUpdate = true;
}
seqEl.addEventListener('input', () => applyCut(+seqEl.value));
let t0 = performance.now();
function loop(now){
  requestAnimationFrame(loop);
  if (animEl.checked){
    const n = Math.floor(((now - t0)/12) % (struts.length + 60));
    seqEl.value = Math.min(n, struts.length); applyCut(+seqEl.value);
  }
  updateCam(); renderer.render(scene, camera);
}
requestAnimationFrame(loop);
</script>
</body>
</html>
"""


def save_viewer(path: str, g, steps, title: str = "formgen lattice") -> None:
    stats = g.stats()
    data = {
        "nodes": np.round(g.nodes, 2).tolist(),
        "struts": [[i, j, k] for i, j, k in g.struts],
        "print_order": [s for s in steps if "unreachable" not in s],
    }
    bbox = stats["bbox_mm"]
    bbox_s = "—"
    if bbox["min"]:
        ext = [round(b - a) for a, b in zip(bbox["min"], bbox["max"])]
        bbox_s = " × ".join(str(e) for e in ext)
    html = (
        _TEMPLATE
        .replace("__TITLE__", title)
        .replace("__NNODES__", str(stats["nodes"]))
        .replace("__NSTRUTS__", str(stats["struts"]))
        .replace("__TOTLEN__", f"{stats['total_length_m']:.1f}")
        .replace("__BBOX__", bbox_s)
        .replace("__DATA__", json.dumps(data))
    )
    with open(path, "w") as f:
        f.write(html)
