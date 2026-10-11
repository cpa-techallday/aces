// Three.js viewport for the Objects plate. Two layers: the FIELD (data: voxel cells coloured by a
// scalar on a luminance ramp, the atlas's own brightness-is-magnitude rule) and the FIGURE (the
// geometry: an STL, a lathe of a contour), which fades up on request. Units are millimetres.
import * as THREE from "three";
import { OrbitControls } from "./vendor/OrbitControls.js";

const COL = {
  sky: 0x0a1428, grid: 0x1e2f4d, axisX: 0xdbe7f3, chalk: 0xdbe7f3, chalk2: 0xa9bbd0, live: 0x22d3ee, act: 0xf59e0b,
};

// luminance ramp on the chalk hue: deep navy → chalk → white (brightness = magnitude)
const RAMP = [[0.0, [0.09, 0.17, 0.32]], [0.35, [0.33, 0.47, 0.66]], [0.7, [0.86, 0.91, 0.95]], [1.0, [1, 1, 1]]];
export function rampColor(f) {
  const t = Math.max(0, Math.min(1, f));
  for (let i = 1; i < RAMP.length; i++) {
    if (t <= RAMP[i][0]) {
      const [t0, c0] = RAMP[i - 1], [t1, c1] = RAMP[i];
      const u = (t - t0) / (t1 - t0 || 1);
      return [c0[0] + (c1[0] - c0[0]) * u, c0[1] + (c1[1] - c0[1]) * u, c0[2] + (c1[2] - c0[2]) * u];
    }
  }
  return RAMP[RAMP.length - 1][1];
}
export function rampCSS() {
  return `linear-gradient(to top, ${RAMP.map(([t, c]) => `rgb(${c.map((v) => Math.round(v * 255)).join(",")}) ${Math.round(t * 100)}%`).join(", ")})`;
}

export class Viewer {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: "high-performance" });
    this.renderer.setClearColor(COL.sky, 1);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100000);
    this.camera.position.set(400, 300, 500);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.06;      // slow sidereal drift when the operator lets go
    this.controls.rotateSpeed = 0.7;
    this.controls.addEventListener("change", () => this.requestRender());
    this.scene.add(new THREE.HemisphereLight(0xdbe7f3, 0x0a1428, 0.9));
    const key = new THREE.DirectionalLight(0xffffff, 1.1); key.position.set(1, 1.4, 0.8); this.scene.add(key);
    const fill = new THREE.DirectionalLight(0x9fb4d8, 0.35); fill.position.set(-1, 0.4, -0.6); this.scene.add(fill);
    this.world = new THREE.Group(); this.scene.add(this.world);
    this.figure = new THREE.Group(); this.field = new THREE.Group();
    this.world.add(this.figure); this.world.add(this.field);
    this.gridGroup = new THREE.Group(); this.scene.add(this.gridGroup);
    this.layer = "field";
    this.figure.visible = false;
    this._raf = 0; this._animating = false;
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(canvas.parentElement || canvas);
    this.resize();
    this._tick = this._tick.bind(this);
    this._tick();
  }
  resize() {
    const r = (this.canvas.parentElement || this.canvas).getBoundingClientRect();
    const w = Math.max(10, Math.floor(r.width)), h = Math.max(10, Math.floor(r.height));
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h; this.camera.updateProjectionMatrix();
    this.requestRender();
  }
  // urgent: new geometry draws on the next tick even with a frame in flight (one extra frame at most, never a backlog)
  requestRender(urgent = false) { this._dirty = true; if (urgent) this._urgent = true; }
  _tick() {
    this.controls.update();
    const busy = this._gpuBusy();   // polled every tick, so a finished frame is counted even when nothing new is pending
    if (this._dirty && (!busy || this._urgent)) {
      this._dirty = false; this._urgent = false;
      const t0 = performance.now();
      this.renderer.render(this.scene, this.camera);
      this._lastRenderMs = performance.now() - t0;   // main-thread cost only: the GPU work is asynchronous
      this._fenceFrame();
    }
    requestAnimationFrame(this._tick);
  }
  // One frame in flight at a time. render() only queues commands, so a slow GPU (software GL, remote
  // desktop, a busy integrated chip) would otherwise accumulate a backlog of full redraws of a 500k-cell
  // field during a drag or a fade; the fence makes every redraw wait for the previous one to finish, except the first
  // frame of new geometry (requestRender(true)), which goes out at once so a loaded object never waits behind a stale frame.
  _fenceFrame() {
    const gl = this.renderer.getContext();
    if (!gl.fenceSync) return;
    if (this._sync) gl.deleteSync(this._sync);
    this._sync = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0);
    this._syncAt = performance.now();
    gl.flush();
  }
  _gpuBusy() {
    if (!this._sync) return false;
    const gl = this.renderer.getContext();
    const st = gl.clientWaitSync(this._sync, 0, 0);
    if (st === gl.TIMEOUT_EXPIRED && performance.now() - this._syncAt < 30000) return true;
    this._frameMs = performance.now() - this._syncAt;   // true GPU time of the last frame
    this._frames = (this._frames || 0) + 1;              // frames completed on the GPU (the verification harness waits on this)
    gl.deleteSync(this._sync); this._sync = null;
    return false;
  }
  clear() {
    for (const g of [this.figure, this.field, this.gridGroup]) {
      while (g.children.length) {
        const o = g.children.pop();
        o.traverse?.((c) => { c.geometry?.dispose?.(); if (c.material) (Array.isArray(c.material) ? c.material : [c.material]).forEach((m) => { m.map?.dispose?.(); m.dispose?.(); }); });
      }
    }
    this.bbox = null; this.fieldInfo = null; this.requestRender();
  }
  // The field stays on screen; the figure fades up over it on request (150 ms), and back down.
  setLayer(layer) {
    this.layer = layer;
    this.field.visible = true;
    const hasField = this.field.children.length > 0;
    const target = layer === "figure" ? (hasField ? 0.86 : 1) : (hasField ? 0 : 1);
    this._tweenFigure(target);
  }
  _figureOpacity() { return this._figOpacity === undefined ? (this.figure.visible ? 1 : 0) : this._figOpacity; }
  _applyFigureOpacity(o) {
    this._figOpacity = o;
    this.figure.visible = o > 0.005;
    this.figure.traverse((c) => {
      if (!c.material) return;
      const mats = Array.isArray(c.material) ? c.material : [c.material];
      for (const m of mats) {
        m.transparent = true;
        // a voxel figure shares faces with the field: offset it so it wins the depth test instead of z-fighting
        if (c.isMesh) { m.polygonOffset = true; m.polygonOffsetFactor = -1; m.polygonOffsetUnits = -2; }
        m.opacity = c.isLineSegments || c.isLine ? o * (c.userData.baseOpacity ?? 1) : o;
        m.depthWrite = o >= 0.99;
      }
    });
    this.requestRender();
  }
  _tweenFigure(target, ms = 150) {
    if (this._tween) cancelAnimationFrame(this._tween);
    const from = this._figureOpacity(), t0 = performance.now();
    // nothing to tween, or frames that take longer than the fade itself (measured through the fence): apply once
    if (Math.abs(from - target) < 0.001 || (this._frameMs || 0) > 100) { this._tween = 0; this._applyFigureOpacity(target); return; }
    let shown = from;
    const step = (now) => {
      const u = Math.min(1, (now - t0) / ms);
      const e = 1 - Math.pow(1 - u, 3);           // ease-out from the visible state
      const o = from + (target - from) * e;
      if (u >= 1 || Math.abs(o - shown) > 0.02) { shown = o; this._applyFigureOpacity(o); }
      this._tween = u < 1 ? requestAnimationFrame(step) : 0;
    };
    this._tween = requestAnimationFrame(step);
  }
  // graticule ground plane under the object + axes triad + bbox, sized from the object
  _frame(bbox) {
    const size = new THREE.Vector3().subVectors(bbox.max, bbox.min);
    const span = Math.max(size.x, size.y, size.z) || 1;
    const step = Math.pow(10, Math.floor(Math.log10(span / 8)));
    const ext = Math.ceil((span * 1.3) / step) * step;
    const cx = (bbox.min.x + bbox.max.x) / 2, cz = (bbox.min.z + bbox.max.z) / 2;
    const n = Math.round(ext / step) * 2;
    const grid = new THREE.GridHelper(ext * 2, n, COL.grid, COL.grid);
    grid.material.transparent = true; grid.material.opacity = 0.55;
    grid.position.set(cx, bbox.min.y - step * 0.05, cz);
    this.gridGroup.add(grid);
    // bbox outline
    const box = new THREE.Box3Helper(bbox, 0x3b5277);
    this.gridGroup.add(box);
    // axes triad at the bbox min corner: chalk hairlines named by letter, because colour is reserved for state
    // (amber acts, cyan is live); the letters keep a constant on-screen size whatever the object's scale
    const L = step * 2;
    const ax = (dir, name) => {
      const g = new THREE.BufferGeometry().setFromPoints([bbox.min.clone(), bbox.min.clone().add(dir.clone().multiplyScalar(L))]);
      this.gridGroup.add(new THREE.Line(g, new THREE.LineBasicMaterial({ color: COL.chalk2 })));
      const c = document.createElement("canvas"); c.width = c.height = 64;
      const x = c.getContext("2d"); x.fillStyle = "#a9bbd0"; x.font = "500 44px 'JetBrains Mono', monospace";
      x.textAlign = "center"; x.textBaseline = "middle"; x.fillText(name, 32, 34);
      const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), transparent: true, depthWrite: false, sizeAttenuation: false }));
      sp.position.copy(bbox.min.clone().add(dir.clone().multiplyScalar(L * 1.45)));
      sp.scale.setScalar(0.022);   // about 16 px tall in a 600 px viewport (fov 38)
      this.gridGroup.add(sp);
    };
    ax(new THREE.Vector3(1, 0, 0), "X"); ax(new THREE.Vector3(0, 1, 0), "Y"); ax(new THREE.Vector3(0, 0, 1), "Z");
    this.gridStep = step;
    this.bbox = bbox;
  }
  fit() {
    if (!this.bbox) return;
    const c = this.bbox.getCenter(new THREE.Vector3());
    const s = this.bbox.getSize(new THREE.Vector3()).length() || 1;
    this.controls.target.copy(c);
    const dir = new THREE.Vector3(0.9, 0.7, 1.1).normalize();
    this.camera.position.copy(c).add(dir.multiplyScalar(s * 1.15));
    this.camera.near = s / 500; this.camera.far = s * 50; this.camera.updateProjectionMatrix();
    this.controls.update(); this.requestRender();
  }
  // ---- figure: STL ----
  showSTL(parsed) {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(parsed.positions, 3));
    geo.setAttribute("normal", new THREE.BufferAttribute(parsed.normals, 3));
    const mat = new THREE.MeshStandardMaterial({ color: COL.chalk, metalness: 0.05, roughness: 0.75, flatShading: true, transparent: true });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.renderOrder = 2;
    this.figure.add(mesh);
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo, 25), new THREE.LineBasicMaterial({ color: 0x0a1428, transparent: true, opacity: 0.35 }));
    edges.userData.baseOpacity = 0.35;
    edges.renderOrder = 3;
    this.figure.add(edges);
    geo.computeBoundingBox();
    const bb = geo.boundingBox.clone();
    if (!this.bbox) this._frame(bb); else { this.bbox.union(bb); }
    this.requestRender(true);
    return { triangles: parsed.triangles, bbox: [bb.min.toArray(), bb.max.toArray()] };
  }
  // ---- figure: lathe of a (x, r) contour about the x axis (nozzle) ----
  showLathe(points, { segments = 96 } = {}) {
    // LatheGeometry revolves around Y: map contour x → Y, r → X then rotate so the axis is X.
    const pts = points.map((p) => new THREE.Vector2(p.r, p.x));
    const geo = new THREE.LatheGeometry(pts, segments);
    geo.rotateZ(-Math.PI / 2);
    const mat = new THREE.MeshStandardMaterial({ color: COL.chalk, metalness: 0.1, roughness: 0.6, side: THREE.DoubleSide, transparent: true });
    const lathe = new THREE.Mesh(geo, mat); lathe.renderOrder = 2;
    this.figure.add(lathe);
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(points.map((p) => new THREE.Vector3(p.x, p.r, 0))), new THREE.LineBasicMaterial({ color: COL.act, transparent: true }));
    line.renderOrder = 3;
    this.figure.add(line);
    geo.computeBoundingBox();
    const bb = geo.boundingBox.clone();
    if (!this.bbox) this._frame(bb); else this.bbox.union(bb);
    this.requestRender(true);
    return { bbox: [bb.min.toArray(), bb.max.toArray()], points: points.length };
  }
  // ---- field: voxel cells on a structured grid ----
  // vtk: {cells:[nx,ny,nz], origin, spacing, cellData}; field: name; mask: optional Uint8/Float32 of solid cells
  showVoxels(vtk, { field, maskName = "solid", thresholdPct = 0, maxCells = 600000 } = {}) {
    const [nx, ny, nz] = vtk.cells;
    const h = vtk.spacing[0];
    const data = vtk.cellData[field] ? vtk.cellData[field].array : null;
    const mask = vtk.cellData[maskName] ? vtk.cellData[maskName].array : null;
    const n = nx * ny * nz;
    // stats over solid cells
    let min = Infinity, max = -Infinity, cnt = 0;
    const vals = [];
    for (let i = 0; i < n; i++) {
      if (mask && mask[i] < 0.5) continue;
      const v = data ? data[i] : 1;
      if (!Number.isFinite(v)) continue;
      if (v < min) min = v; if (v > max) max = v; cnt++;
      if (cnt % 3 === 0 || cnt < 50000) vals.push(v);
    }
    vals.sort((a, b) => a - b);
    const q = (p) => (vals.length ? vals[Math.min(vals.length - 1, Math.floor(p * (vals.length - 1)))] : NaN);
    const cut = thresholdPct > 0 ? q(thresholdPct / 100) : -Infinity;
    // collect cells to draw (stride if too many)
    const idx = [];
    for (let i = 0; i < n; i++) {
      if (mask && mask[i] < 0.5) continue;
      const v = data ? data[i] : 1;
      if (v < cut) continue;
      idx.push(i);
    }
    const stride = Math.max(1, Math.ceil(idx.length / maxCells));
    const count = Math.ceil(idx.length / stride);
    const geo = new THREE.BoxGeometry(h * 0.96, h * 0.96, h * 0.96);
    const mat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.8, metalness: 0.0 });
    const inst = new THREE.InstancedMesh(geo, mat, count);
    const m = new THREE.Matrix4();
    const color = new THREE.Color();
    const span = max - min || 1;
    let k = 0;
    for (let j = 0; j < idx.length; j += stride) {
      const i = idx[j];
      const ix = i % nx, iy = Math.floor(i / nx) % ny, iz = Math.floor(i / (nx * ny));
      m.makeTranslation(vtk.origin[0] + (ix + 0.5) * h, vtk.origin[1] + (iy + 0.5) * h, vtk.origin[2] + (iz + 0.5) * h);
      inst.setMatrixAt(k, m);
      const f = data ? (data[i] - min) / span : 0.75;
      const c = rampColor(f);
      color.setRGB(c[0], c[1], c[2]);
      inst.setColorAt(k, color);
      k++;
    }
    inst.instanceMatrix.needsUpdate = true;
    if (inst.instanceColor) inst.instanceColor.needsUpdate = true;
    this.field.add(inst);
    const bb = new THREE.Box3(new THREE.Vector3(...vtk.origin), new THREE.Vector3(vtk.origin[0] + nx * h, vtk.origin[1] + ny * h, vtk.origin[2] + nz * h));
    if (!this.bbox) this._frame(bb); else this.bbox.union(bb);
    this.fieldInfo = { field, min, max, p50: q(0.5), p95: q(0.95), p99: q(0.99), cells: cnt, drawn: count, stride, cut: Number.isFinite(cut) ? cut : null };
    this.requestRender(true);
    return this.fieldInfo;
  }
  // ---- field from a dense npy grid (predicted fields): values on an (nx,ny,nz) C-order array; zero = void ----
  showGridField(shape, data, { h = 1, origin = [0, 0, 0], thresholdPct = 0, maxCells = 600000, zeroIsVoid = true } = {}) {
    const [nx, ny, nz] = shape;
    // convert C-order (i,j,k) with k fastest into a cellData-like array with i fastest
    const n = nx * ny * nz;
    const arr = new Float32Array(n);
    const mask = new Float32Array(n);
    for (let i = 0; i < nx; i++) for (let j = 0; j < ny; j++) for (let k = 0; k < nz; k++) {
      const v = data[(i * ny + j) * nz + k];
      const t = i + nx * (j + ny * k);
      arr[t] = v;
      mask[t] = zeroIsVoid ? (v !== 0 ? 1 : 0) : 1;
    }
    return this.showVoxels({ cells: [nx, ny, nz], spacing: [h, h, h], origin, cellData: { value: { array: arr, comps: 1 }, solid: { array: mask, comps: 1 } } }, { field: "value", maskName: "solid", thresholdPct, maxCells });
  }
  dispose() { this.ro.disconnect(); this.clear(); this.renderer.dispose(); }
}
