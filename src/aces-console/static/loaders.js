// Pure data loaders for the SIMLAB console viewer. No DOM, no Three.js: they turn the files the
// runtime writes into typed arrays the viewer can draw.
//
//  parseBinarySTL(buffer)  -> { positions: Float32Array(n*9), normals: Float32Array(n*9), triangles: n, bbox }
//  parseLegacyVTK(buffer)  -> { dims:[nx+1,ny+1,nz+1], cells:[nx,ny,nz], origin, spacing, cellData:{name:Float32Array}, pointData:{name:{array, comps}} }
//
// VTK: simlab/post.py writes "# vtk DataFile Version 3.0 / BINARY / DATASET STRUCTURED_POINTS" with
// big-endian float32 arrays; cell arrays are stored i-fastest (x), then j, then k. A cell (i,j,k)
// therefore lives at index i + nx*(j + ny*k).

export function parseBinarySTL(buffer) {
  const dv = new DataView(buffer);
  const n = dv.getUint32(80, true);
  if (84 + n * 50 > buffer.byteLength) throw new Error(`STL: header says ${n} triangles, file too short`);
  const positions = new Float32Array(n * 9);
  const normals = new Float32Array(n * 9);
  const bbox = { min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] };
  let off = 84;
  for (let t = 0; t < n; t++) {
    const nx = dv.getFloat32(off, true), ny = dv.getFloat32(off + 4, true), nz = dv.getFloat32(off + 8, true);
    off += 12;
    for (let v = 0; v < 3; v++) {
      const x = dv.getFloat32(off, true), y = dv.getFloat32(off + 4, true), z = dv.getFloat32(off + 8, true);
      off += 12;
      const base = t * 9 + v * 3;
      positions[base] = x; positions[base + 1] = y; positions[base + 2] = z;
      normals[base] = nx; normals[base + 1] = ny; normals[base + 2] = nz;
      if (x < bbox.min[0]) bbox.min[0] = x; if (x > bbox.max[0]) bbox.max[0] = x;
      if (y < bbox.min[1]) bbox.min[1] = y; if (y > bbox.max[1]) bbox.max[1] = y;
      if (z < bbox.min[2]) bbox.min[2] = z; if (z > bbox.max[2]) bbox.max[2] = z;
    }
    off += 2; // attribute byte count
  }
  // STL normals are often zero; recompute flat normals from the winding where needed
  for (let t = 0; t < n; t++) {
    const b = t * 9;
    if (normals[b] === 0 && normals[b + 1] === 0 && normals[b + 2] === 0) {
      const ax = positions[b + 3] - positions[b], ay = positions[b + 4] - positions[b + 1], az = positions[b + 5] - positions[b + 2];
      const bx = positions[b + 6] - positions[b], by = positions[b + 7] - positions[b + 1], bz = positions[b + 8] - positions[b + 2];
      let cx = ay * bz - az * by, cy = az * bx - ax * bz, cz = ax * by - ay * bx;
      const l = Math.hypot(cx, cy, cz) || 1;
      cx /= l; cy /= l; cz /= l;
      for (let v = 0; v < 3; v++) { normals[b + v * 3] = cx; normals[b + v * 3 + 1] = cy; normals[b + v * 3 + 2] = cz; }
    }
  }
  return { positions, normals, triangles: n, bbox };
}

function readLine(bytes, pos) {
  let end = pos;
  while (end < bytes.length && bytes[end] !== 10) end++;
  const line = new TextDecoder("latin1").decode(bytes.subarray(pos, end)).replace(/\r$/, "");
  return { line, next: Math.min(end + 1, bytes.length) };
}

function bigEndianFloats(buffer, offset, count) {
  const out = new Float32Array(count);
  const dv = new DataView(buffer, offset, count * 4);
  for (let i = 0; i < count; i++) out[i] = dv.getFloat32(i * 4, false);
  return out;
}

export function parseLegacyVTK(buffer) {
  const bytes = new Uint8Array(buffer);
  let pos = 0;
  const hdr = [];
  // four header lines: version, title, BINARY|ASCII, DATASET ...
  for (let i = 0; i < 4; i++) { const r = readLine(bytes, pos); hdr.push(r.line); pos = r.next; }
  if (!/^# vtk DataFile/.test(hdr[0])) throw new Error("not a legacy VTK file");
  if (hdr[2].trim() !== "BINARY") throw new Error(`VTK: expected BINARY data, got ${hdr[2]}`);
  if (!/STRUCTURED_POINTS/.test(hdr[3])) throw new Error(`VTK: expected STRUCTURED_POINTS, got ${hdr[3]}`);
  const out = { title: hdr[1], dims: null, cells: null, origin: [0, 0, 0], spacing: [1, 1, 1], cellData: {}, pointData: {} };
  let section = null; // "cell" | "point"
  let sectionCount = 0;
  while (pos < bytes.length) {
    const r = readLine(bytes, pos);
    const line = r.line.trim();
    pos = r.next;
    if (!line) continue;
    const parts = line.split(/\s+/);
    const key = parts[0].toUpperCase();
    if (key === "DIMENSIONS") {
      out.dims = parts.slice(1, 4).map(Number);
      out.cells = out.dims.map((d) => Math.max(d - 1, 1));
    } else if (key === "ORIGIN") {
      out.origin = parts.slice(1, 4).map(Number);
    } else if (key === "SPACING" || key === "ASPECT_RATIO") {
      out.spacing = parts.slice(1, 4).map(Number);
    } else if (key === "CELL_DATA") {
      section = "cell"; sectionCount = Number(parts[1]);
    } else if (key === "POINT_DATA") {
      section = "point"; sectionCount = Number(parts[1]);
    } else if (key === "SCALARS") {
      const name = parts[1];
      const comps = parts[3] ? Number(parts[3]) : 1;
      const lut = readLine(bytes, pos); // LOOKUP_TABLE default
      if (!/^LOOKUP_TABLE/i.test(lut.line.trim())) throw new Error(`VTK: SCALARS ${name} without LOOKUP_TABLE`);
      pos = lut.next;
      const count = sectionCount * comps;
      const arr = bigEndianFloats(buffer, pos, count);
      pos += count * 4;
      (section === "point" ? out.pointData : out.cellData)[name] = { array: arr, comps };
    } else if (key === "VECTORS") {
      const name = parts[1];
      const count = sectionCount * 3;
      const arr = bigEndianFloats(buffer, pos, count);
      pos += count * 4;
      (section === "point" ? out.pointData : out.cellData)[name] = { array: arr, comps: 3 };
    } else if (key === "LOOKUP_TABLE" || key === "METADATA" || key === "INFORMATION") {
      // tolerated, no payload we use
    }
  }
  if (!out.dims) throw new Error("VTK: no DIMENSIONS");
  return out;
}

// Summaries used by legends: min / max / percentiles of a scalar, ignoring NaN and (optionally) masked cells.
export function fieldStats(array, mask) {
  let min = Infinity, max = -Infinity, n = 0;
  const vals = [];
  for (let i = 0; i < array.length; i++) {
    if (mask && !mask[i]) continue;
    const v = array[i];
    if (!Number.isFinite(v)) continue;
    if (v < min) min = v;
    if (v > max) max = v;
    n++;
    if (vals.length < 200000 || (i % 7) === 0) vals.push(v);
  }
  vals.sort((a, b) => a - b);
  const q = (p) => (vals.length ? vals[Math.min(vals.length - 1, Math.floor(p * (vals.length - 1)))] : NaN);
  return { min, max, n, p50: q(0.5), p95: q(0.95), p99: q(0.99) };
}

// Convert a vector field (3 comps) to its magnitude.
export function magnitude(array) {
  const n = array.length / 3;
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) out[i] = Math.hypot(array[i * 3], array[i * 3 + 1], array[i * 3 + 2]);
  return out;
}

export async function fetchBuffer(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} for ${url}`);
  return await r.arrayBuffer();
}

// ---------- npz / npy (numpy) ----------
// np.savez writes a zip of .npy members (stored or deflated); np.savez_compressed deflates.
async function inflateRaw(bytes) {
  if (typeof DecompressionStream === "undefined") throw new Error("this browser cannot inflate npz members");
  const ds = new DecompressionStream("deflate-raw");
  const stream = new Blob([bytes]).stream().pipeThrough(ds);
  return new Uint8Array(await new Response(stream).arrayBuffer());
}
export function parseNPY(buffer) {
  const bytes = new Uint8Array(buffer);
  if (bytes[0] !== 0x93 || String.fromCharCode(...bytes.subarray(1, 6)) !== "NUMPY") throw new Error("not an npy file");
  const major = bytes[6];
  const dv = new DataView(buffer);
  const hlen = major === 1 ? dv.getUint16(8, true) : dv.getUint32(8, true);
  const hstart = major === 1 ? 10 : 12;
  const header = new TextDecoder("latin1").decode(bytes.subarray(hstart, hstart + hlen));
  const descr = /'descr':\s*'([^']+)'/.exec(header)[1];
  const fortran = /'fortran_order':\s*(True|False)/.exec(header)[1] === "True";
  const shape = (/'shape':\s*\(([^)]*)\)/.exec(header)[1]).split(",").map((s) => s.trim()).filter(Boolean).map(Number);
  const off = hstart + hlen;
  const count = shape.reduce((a, b) => a * b, 1);
  const little = descr[0] !== ">";
  const kind = descr.slice(-2);
  let data;
  const slice = buffer.slice(off, off + count * ({ f4: 4, f8: 8, i4: 4, i8: 8, u1: 1, b1: 1, i2: 2, u2: 2, u4: 4, i1: 1 }[kind] || 4));
  if (kind === "f4") data = little ? new Float32Array(slice) : swap32(slice, Float32Array);
  else if (kind === "f8") data = little ? new Float64Array(slice) : swap64(slice);
  else if (kind === "i4") data = new Int32Array(slice);
  else if (kind === "u4") data = new Uint32Array(slice);
  else if (kind === "i2") data = new Int16Array(slice);
  else if (kind === "u2") data = new Uint16Array(slice);
  else if (kind === "u1" || kind === "b1") data = new Uint8Array(slice);
  else if (kind === "i1") data = new Int8Array(slice);
  else if (kind === "i8") { const b = new BigInt64Array(slice); data = Float64Array.from(b, Number); }
  else throw new Error(`npy dtype ${descr} not supported`);
  return { shape, fortran, descr, data };
}
function swap32(buf, T) { const u = new Uint8Array(buf); for (let i = 0; i < u.length; i += 4) { const a = u[i], b = u[i + 1]; u[i] = u[i + 3]; u[i + 1] = u[i + 2]; u[i + 2] = b; u[i + 3] = a; } return new T(buf); }
function swap64(buf) { const u = new Uint8Array(buf); for (let i = 0; i < u.length; i += 8) { for (let k = 0; k < 4; k++) { const t = u[i + k]; u[i + k] = u[i + 7 - k]; u[i + 7 - k] = t; } } return new Float64Array(buf); }
// ZIP64 extra field (id 0x0001): the 8-byte sizes that replace 0xFFFFFFFF placeholders, uncompressed first, then compressed.
function zip64Sizes(dv, start, xlen, needU, needC) {
  let p = start;
  const end = start + xlen;
  const out = { usize: null, csize: null };
  while (p + 4 <= end) {
    const id = dv.getUint16(p, true), sz = dv.getUint16(p + 2, true);
    if (id === 0x0001) {
      let q = p + 4;
      if (needU && q + 8 <= end) { out.usize = Number(dv.getBigUint64(q, true)); q += 8; }
      if (needC && q + 8 <= end) { out.csize = Number(dv.getBigUint64(q, true)); q += 8; }
    }
    p += 4 + sz;
  }
  return out;
}
export async function parseNPZ(buffer) {
  const dv = new DataView(buffer);
  const bytes = new Uint8Array(buffer);
  const out = {};
  let pos = 0;
  while (pos + 30 <= buffer.byteLength && dv.getUint32(pos, true) === 0x04034b50) {
    const method = dv.getUint16(pos + 8, true);
    const flags = dv.getUint16(pos + 6, true);
    let csize = dv.getUint32(pos + 18, true);
    let usize = dv.getUint32(pos + 22, true);
    const nlen = dv.getUint16(pos + 26, true);
    const xlen = dv.getUint16(pos + 28, true);
    const name = new TextDecoder("latin1").decode(bytes.subarray(pos + 30, pos + 30 + nlen));
    if (csize === 0xffffffff || usize === 0xffffffff) {
      // numpy writes every member with force_zip64: the real sizes live in the ZIP64 extra field
      const z = zip64Sizes(dv, pos + 30 + nlen, xlen, usize === 0xffffffff, csize === 0xffffffff);
      if (usize === 0xffffffff) usize = z.usize;
      if (csize === 0xffffffff) csize = z.csize;
      if (csize === null || usize === null) throw new Error(`npz member ${name}: ZIP64 sizes missing`);
    }
    const dstart = pos + 30 + nlen + xlen;
    if (flags & 8) throw new Error("npz with data descriptors is not supported");
    const raw = bytes.subarray(dstart, dstart + csize);
    let member;
    if (method === 0) member = raw.slice().buffer;
    else if (method === 8) member = (await inflateRaw(raw)).buffer;
    else throw new Error(`npz member ${name}: compression ${method} not supported`);
    out[name.replace(/\.npy$/, "")] = parseNPY(member);
    pos = dstart + csize;
  }
  return out;
}

// ---------- CSV (PyFR residual / forces, nozzle contours) ----------
export function parseCSV(text) {
  const lines = text.split(/\r?\n/).filter((l) => l.trim() && !l.startsWith("#"));
  if (!lines.length) return { columns: [], rows: [] };
  const columns = lines[0].split(",").map((s) => s.trim());
  const numeric = columns.map(() => true);
  const rows = lines.slice(1).map((l) => l.split(",").map((s, i) => { const v = Number(s); if (!Number.isFinite(v)) numeric[i] = false; return Number.isFinite(v) ? v : s.trim(); }));
  // a first line that is all numbers is data, not a header
  if (columns.every((c) => Number.isFinite(Number(c)))) { rows.unshift(columns.map(Number)); return { columns: columns.map((_, i) => `col${i}`), rows }; }
  return { columns, rows };
}
export function column(csv, name) {
  const i = csv.columns.indexOf(name);
  return i < 0 ? [] : csv.rows.map((r) => r[i]);
}
