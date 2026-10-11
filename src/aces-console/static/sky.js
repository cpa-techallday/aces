// Canvas plates for the SIMLAB console: a chart surface with a graticule, magnitude-scaled points,
// rings for state, leader-line annotations and hit testing. Every mark is data: a point is a GPU,
// a case or a record; its diameter is its magnitude on one fixed ramp; a ring is a state.

const CSS = {};
function tok(name) {
  if (!CSS[name]) CSS[name] = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return CSS[name];
}
export const C = {
  chalk: () => tok("--chalk"), chalk2: () => tok("--chalk-2"), chalk3: () => tok("--chalk-3"),
  live: () => tok("--live"), act: () => tok("--act"), fault: () => tok("--fault"),
  line: () => tok("--line-2"), line3: () => tok("--line-3"), plate: () => tok("--plate"),
};
const MONO = () => `${tok("--f-mono") || "JetBrains Mono, monospace"}`;
const NAME = () => `${tok("--f-name") || "EB Garamond, serif"}`;

// One fixed magnitude ramp for every plate: radius in CSS px from a 0..1 fraction (sqrt so area ~ value).
export function magRadius(frac, lo = 1.6, hi = 9) {
  const f = Math.max(0, Math.min(1, Number.isFinite(frac) ? frac : 0));
  return lo + (hi - lo) * Math.sqrt(f);
}

export class Sky {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.hits = [];
    this.hover = null;
    this.renderer = null;
    this.pickCb = null;
    this.hoverCb = null;
    this.w = 0; this.h = 0;
    this._raf = 0;
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(canvas.parentElement || canvas);
    canvas.addEventListener("pointermove", (e) => this._move(e));
    canvas.addEventListener("pointerleave", () => { if (this.hover) { this.hover = null; this.hoverCb && this.hoverCb(null); this.draw(); } });
    canvas.addEventListener("click", (e) => { const h = this._hit(e); this.pickCb && this.pickCb(h ? h.item : null, e); });
    canvas.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && this.hover) { e.preventDefault(); this.pickCb && this.pickCb(this.hover.item, e); } });
    this.resize();
  }
  resize() {
    const r = (this.canvas.parentElement || this.canvas).getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.w = Math.max(10, Math.floor(r.width));
    this.h = Math.max(10, Math.floor(r.height));
    this.canvas.width = Math.floor(this.w * dpr);
    this.canvas.height = Math.floor(this.h * dpr);
    this.canvas.style.width = this.w + "px";
    this.canvas.style.height = this.h + "px";
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.draw();
  }
  setRenderer(fn) { this.renderer = fn; this.draw(); }
  onPick(cb) { this.pickCb = cb; }
  onHover(cb) { this.hoverCb = cb; }
  draw() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => {
      this._raf = 0;
      const ctx = this.ctx;
      ctx.clearRect(0, 0, this.w, this.h);
      this.hits = [];
      if (this.renderer) this.renderer(ctx, this.w, this.h, this);
    });
  }
  hit(x, y, r, item) { this.hits.push({ x, y, r: Math.max(r, 7), item }); }
  _pos(e) { const b = this.canvas.getBoundingClientRect(); return { x: e.clientX - b.left, y: e.clientY - b.top }; }
  _hit(e) {
    const p = this._pos(e);
    let best = null, bd = Infinity;
    for (const h of this.hits) {
      const d = Math.hypot(h.x - p.x, h.y - p.y);
      if (d <= h.r + 2 && d < bd) { bd = d; best = h; }
    }
    return best;
  }
  _move(e) {
    const h = this._hit(e);
    const id = h ? JSON.stringify(h.item) : null;
    const prev = this.hover ? JSON.stringify(this.hover.item) : null;
    this.canvas.style.cursor = h ? "pointer" : "default";
    if (id !== prev) { this.hover = h; this.hoverCb && this.hoverCb(h ? h.item : null); this.draw(); }
  }
  destroy() { this.ro.disconnect(); }
}

// ---------- primitives ----------
export function label(ctx, text, x, y, { size = 11, color = C.chalk3(), align = "left", baseline = "alphabetic", font = MONO(), weight = 500, tracking = 0 } = {}) {
  ctx.save();
  ctx.font = `${weight} ${size}px ${font}`;
  ctx.fillStyle = color;
  ctx.textAlign = align;
  ctx.textBaseline = baseline;
  if (tracking) { ctx.letterSpacing = `${tracking}px`; }
  ctx.fillText(text, x, y);
  ctx.restore();
}
export function nameLabel(ctx, text, x, y, opts = {}) {
  label(ctx, text, x, y, { font: NAME(), size: opts.size || 15, weight: 400, color: opts.color || C.chalk(), align: opts.align || "left", baseline: opts.baseline || "alphabetic", italic: true });
  // italic: canvas font string needs the style prefix
}
export function italic(ctx, text, x, y, { size = 15, color = C.chalk(), align = "left", baseline = "alphabetic" } = {}) {
  ctx.save();
  ctx.font = `italic 400 ${size}px ${NAME()}`;
  ctx.fillStyle = color; ctx.textAlign = align; ctx.textBaseline = baseline;
  ctx.fillText(text, x, y);
  ctx.restore();
}
export function point(ctx, x, y, r, { fill = C.chalk(), alpha = 1, ring = null, ringWidth = 1.5, ringGap = 3, dashed = false, outline = false } = {}) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2);
  if (outline) { ctx.lineWidth = 1; ctx.strokeStyle = fill; ctx.stroke(); } else { ctx.fillStyle = fill; ctx.fill(); }
  ctx.restore();
  if (ring) {
    ctx.save();
    ctx.beginPath(); ctx.arc(x, y, r + ringGap, 0, Math.PI * 2);
    ctx.lineWidth = ringWidth; ctx.strokeStyle = ring;
    if (dashed) ctx.setLineDash([3, 3]);
    ctx.stroke();
    ctx.restore();
  }
}
// ring split into n segments, each with its own colour (stage state around a case)
export function segRing(ctx, x, y, r, colors, { width = 2, gap = 0.18 } = {}) {
  const n = colors.length;
  if (!n) return;
  const span = (Math.PI * 2) / n;
  ctx.save();
  ctx.lineWidth = width;
  for (let i = 0; i < n; i++) {
    const a0 = -Math.PI / 2 + i * span + gap / 2, a1 = -Math.PI / 2 + (i + 1) * span - gap / 2;
    ctx.beginPath(); ctx.arc(x, y, r, a0, a1);
    ctx.strokeStyle = colors[i]; ctx.stroke();
  }
  ctx.restore();
}
export function leader(ctx, x1, y1, x2, y2, text, { color = C.chalk2(), size = 11 } = {}) {
  ctx.save();
  ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.globalAlpha = 0.9;
  ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.lineTo(x2 + 6, y2); ctx.stroke();
  ctx.restore();
  if (text) label(ctx, text, x2 + 10, y2, { color, size, baseline: "middle" });
}
export function graticule(ctx, box, { xTicks = [], yTicks = [], xLabels = [], yLabels = [], xAxisLabel = "", yAxisLabel = "" } = {}) {
  const { x0, y0, x1, y1 } = box;
  ctx.save();
  ctx.strokeStyle = C.line(); ctx.lineWidth = 1;
  ctx.beginPath();
  for (const t of xTicks) { const x = Math.round(t) + 0.5; ctx.moveTo(x, y0); ctx.lineTo(x, y1); }
  for (const t of yTicks) { const y = Math.round(t) + 0.5; ctx.moveTo(x0, y); ctx.lineTo(x1, y); }
  ctx.stroke();
  ctx.strokeStyle = C.line3();
  ctx.beginPath(); ctx.moveTo(x0 + 0.5, y0); ctx.lineTo(x0 + 0.5, y1 + 0.5); ctx.lineTo(x1, y1 + 0.5); ctx.stroke();
  ctx.restore();
  xLabels.forEach(({ x, text }) => label(ctx, text, x, y1 + 16, { align: "center", size: 10 }));
  yLabels.forEach(({ y, text, color }) => label(ctx, text, x0 - 8, y, { align: "right", baseline: "middle", size: 10, color }));
  if (xAxisLabel) label(ctx, xAxisLabel, x1, y1 + 30, { align: "right", size: 10, tracking: 1 });
  if (yAxisLabel) { ctx.save(); ctx.translate(x0 - 46, y0); ctx.rotate(-Math.PI / 2); label(ctx, yAxisLabel, 0, 0, { align: "right", size: 10, tracking: 1 }); ctx.restore(); }
}
export function niceTicks(min, max, n = 5) {
  if (!(max > min)) return [min];
  const span = max - min, raw = span / n, p = Math.pow(10, Math.floor(Math.log10(raw)));
  const m = raw / p, step = (m >= 5 ? 5 : m >= 2 ? 2 : 1) * p;
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}
export function fmt(v, d = 3) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  if (typeof v !== "number") return String(v);
  const a = Math.abs(v);
  if (a !== 0 && (a < 1e-3 || a >= 1e6)) return v.toExponential(2);
  if (Number.isInteger(v)) return String(v);
  return v.toPrecision(d).replace(/\.?0+$/, (m) => (m.includes(".") ? "" : m));
}
export function fmtMiB(mib) { return mib >= 1024 ? (mib / 1024).toFixed(mib >= 10240 ? 0 : 1) + " GB" : Math.round(mib) + " MiB"; }
export function fmtDur(s) {
  if (s === null || s === undefined || !Number.isFinite(s)) return "—";
  if (s < 60) return s.toFixed(s < 10 ? 1 : 0) + " s";
  if (s < 3600) return Math.floor(s / 60) + " m " + String(Math.round(s % 60)).padStart(2, "0") + " s";
  return (s / 3600).toFixed(1) + " h";
}

// ---------- the fleet plate: time × GPU ----------
export function drawFleet(ctx, w, h, d, sky) {
  const gpus = d.gpus || [];
  const n = Math.max(gpus.length, 8);
  const narrow = w < 640;
  const left = narrow ? 70 : 88, right = narrow ? Math.max(120, Math.round(w * 0.36)) : 200, top = 26, bottom = 30;
  const x0 = left, x1 = w - right, y0 = top, y1 = h - bottom;
  const rowH = (y1 - y0) / n;
  const win = d.windowS || 600;
  const now = d.now || Date.now() / 1000;
  const X = (t) => x1 - ((now - t) / win) * (x1 - x0);
  // time ticks: every 2 minutes, or every 5 on a narrow plot
  const stepMin = (x1 - x0) < 420 ? 5 : 2;
  const xTicks = [], xLabels = [];
  for (let m = 0; m <= win / 60; m += stepMin) { const x = X(now - m * 60); xTicks.push(x); xLabels.push({ x, text: m === 0 ? "now" : `−${m} min` }); }
  const yTicks = [], yLabels = [];
  for (let i = 0; i <= n; i++) yTicks.push(y0 + i * rowH);
  gpus.forEach((g, i) => {
    const cy = y0 + (i + 0.5) * rowH;
    yLabels.push({ y: cy, text: `GPU ${g.index}`, color: g.derated ? C.fault() : (g.in_pool ? C.chalk2() : C.chalk3()) });
  });
  // pool bands first (very faint), so the graticule sits above them
  gpus.forEach((g, i) => {
    if (!g.in_pool) return;
    ctx.save(); ctx.globalAlpha = 0.035; ctx.fillStyle = C.chalk();
    ctx.fillRect(x0, y0 + i * rowH, x1 - x0, rowH); ctx.restore();
  });
  graticule(ctx, { x0, y0, x1, y1 }, { xTicks, yTicks, xLabels, yLabels, xAxisLabel: "TIME", yAxisLabel: "" });
  label(ctx, d.modeText || "", x0, y0 - 10, { size: 10, tracking: 1 });
  // history trails
  const hist = d.history || [];
  for (const s of hist) {
    if (now - s.t > win) continue;
    const x = X(s.t);
    for (const g of s.gpus || []) {
      const row = gpus.findIndex((q) => q.index === g.i);
      if (row < 0) continue;
      const cy = y0 + (row + 0.5) * rowH;
      const total = gpus[row].memTotalMiB || 65536;
      const r = magRadius((g.m || 0) / total, 1.6, 7) * 0.6;
      const a = 0.3 + 0.6 * Math.min(1, (g.u || 0) / 100);
      point(ctx, x, cy, r, { alpha: a, fill: (g.u || 0) > 0 ? C.live() : C.chalk() });
    }
  }
  // present points at the right edge
  gpus.forEach((g, i) => {
    const cy = y0 + (i + 0.5) * rowH;
    const total = g.memTotalMiB || 65536;
    const frac = (g.memUsedMiB || 0) / total;
    const r = magRadius(frac, 3.4, 12);
    const util = g.utilPct || 0;
    const live = !!g.job || util > 0;
    const isEye = d.eye && d.eye.kind === "gpu" && d.eye.id === g.index;
    const px = x1;
    const fill = live ? C.live() : C.chalk();
    const alpha = 0.7 + 0.3 * Math.min(1, util / 100);
    point(ctx, px, cy, r, { fill, alpha: live ? 1 : alpha });
    if (g.job || g.lease) point(ctx, px, cy, r, { fill: "transparent", alpha: 0, ring: C.live(), ringGap: 3.5 });
    if (g.derated) point(ctx, px, cy, r, { fill: "transparent", alpha: 0, ring: C.fault(), ringGap: g.job ? 7 : 3.5 });
    if (!g.in_pool && !g.derated) point(ctx, px, cy, r, { fill: "transparent", alpha: 0, ring: C.chalk3(), ringGap: 3.5, dashed: true });
    if (isEye) point(ctx, px, cy, r, { fill: "transparent", alpha: 0, ring: C.act(), ringGap: g.derated || g.job ? 10.5 : 7, ringWidth: 1.5 });
    if (d.beaconFresh && d.beaconFresh[g.index]) { ctx.save(); ctx.strokeStyle = C.live(); ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(px + r + 14, cy - 5); ctx.lineTo(px + r + 14, cy + 5); ctx.stroke(); ctx.restore(); }
    sky.hit(px, cy, r + 8, { kind: "gpu", id: g.index });
    // annotation
    const tx = px + 24;
    let text = null, color = C.chalk2();
    if (g.job) {
      const j = g.job;
      text = `${j.experiment || ""} ${j.case_id || ""} · ${j.solver || ""} · ${j.stage || "run"}${j.elapsed_s ? " · " + fmtDur(j.elapsed_s) : ""}`.trim();
      color = C.live();
    } else if (g.lease) { text = `lease ${g.lease}`; color = C.live(); }
    else if (g.derated) { text = "derated by GRID CONTROL"; color = C.fault(); }
    else if (g.processes && g.processes.length) {
      const p = g.processes[0];
      text = `${p.processName || "process"} ${p.pid || ""} · ${fmtMiB(p.usedMemMiB || 0)}${g.processes.length > 1 ? " +" + (g.processes.length - 1) : ""}`;
      color = C.chalk3();
    } else if (!g.in_pool) { text = "not in the sim pool"; color = C.chalk3(); }
    if (text) {
      ctx.save(); ctx.strokeStyle = color; ctx.globalAlpha = 0.7; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(px + r + 4, cy); ctx.lineTo(tx - 4, cy); ctx.stroke(); ctx.restore();
      ctx.save(); ctx.font = `500 10.5px ${MONO()}`; const maxW = w - tx - 12;
      let t = text; while (t.length > 4 && ctx.measureText(t).width > maxW) t = t.slice(0, -2);
      if (t !== text) t = t.slice(0, -1) + "…";
      ctx.restore();
      label(ctx, t, tx, cy, { color, size: 10.5, baseline: "middle" });
    }
    // memory / util readout under the GPU label
    label(ctx, narrow ? fmtMiB(g.memUsedMiB || 0) : `${fmtMiB(g.memUsedMiB || 0)} · ${util} %`, x0 - 8, cy + 11, { align: "right", size: 9, color: C.chalk3() });
  });
  if (!gpus.length) return;
}

// ---------- the constellation plate: cases on parameter coordinates ----------
export function drawConstellation(ctx, w, h, d, sky) {
  const cases = d.cases || [];
  const left = 74, right = 28, top = 26, bottom = 46;
  const x0 = left, y0 = top, x1 = w - right, y1 = h - bottom;
  const xs = d.xValues, ys = d.yValues;          // arrays aligned with cases: numbers or category indices
  const xCats = d.xCats || null, yCats = d.yCats || null; // category labels when not numeric
  const xmin = Math.min(...xs), xmax = Math.max(...xs), ymin = Math.min(...ys), ymax = Math.max(...ys);
  const padx = xmin === xmax ? 1 : (xmax - xmin) * 0.12, pady = ymin === ymax ? 1 : (ymax - ymin) * 0.14;
  const X = (v) => x0 + ((v - (xmin - padx)) / ((xmax + padx) - (xmin - padx))) * (x1 - x0);
  const Y = (v) => y1 - ((v - (ymin - pady)) / ((ymax + pady) - (ymin - pady))) * (y1 - y0);
  const xt = xCats ? xCats.map((_, i) => i) : niceTicks(xmin - padx, xmax + padx, 6);
  const yt = yCats ? yCats.map((_, i) => i) : niceTicks(ymin - pady, ymax + pady, 5);
  graticule(ctx, { x0, y0, x1, y1 }, {
    xTicks: xt.map(X), yTicks: yt.map(Y),
    xLabels: xt.map((v) => ({ x: X(v), text: xCats ? String(xCats[v]).slice(0, 18) : fmt(v) })),
    yLabels: yt.map((v) => ({ y: Y(v), text: yCats ? String(yCats[v]).slice(0, 12) : fmt(v) })),
    xAxisLabel: (d.xLabel || "").toUpperCase(), yAxisLabel: (d.yLabel || "").toUpperCase(),
  });
  // constellation line in sweep order
  const order = cases.map((c, i) => i).sort((a, b) => xs[a] - xs[b] || ys[a] - ys[b]);
  if (order.length > 1) {
    ctx.save(); ctx.strokeStyle = C.line3(); ctx.lineWidth = 1; ctx.setLineDash([2, 4]);
    ctx.beginPath();
    order.forEach((i, k) => { const x = X(xs[i]), y = Y(ys[i]); if (k === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); });
    ctx.stroke(); ctx.restore();
  }
  const sz = d.sizeValues || cases.map(() => 0.5);
  const smin = Math.min(...sz.filter(Number.isFinite)), smax = Math.max(...sz.filter(Number.isFinite));
  cases.forEach((c, i) => {
    const x = X(xs[i]), y = Y(ys[i]);
    const frac = Number.isFinite(sz[i]) && smax > smin ? (sz[i] - smin) / (smax - smin) : 0.5;
    const r = magRadius(frac, 3, 11);
    const st = c.status;
    const isEye = d.eye && d.eye.kind === "case" && d.eye.id === c.case_id && d.eye.exp === d.expKey;
    if (st === "RUNNING") point(ctx, x, y, r, { fill: C.live() });
    else if (st === "FINISHED") point(ctx, x, y, r, { fill: C.chalk() });
    else if (st === "FAILED") point(ctx, x, y, r, { fill: C.chalk3(), alpha: 0.6, ring: C.fault(), ringGap: 3 });
    else if (st === "STALE") point(ctx, x, y, r, { fill: C.chalk3(), alpha: 0.6, ring: C.fault(), ringGap: 3, dashed: true });
    else point(ctx, x, y, r, { fill: C.chalk3(), outline: true });
    // stage ring segments
    if (c.stages && c.stages.length) {
      const cols = c.stages.map((s) => s.status === "FINISHED" ? C.chalk2() : s.status === "FAILED" ? C.fault() : (c.current_stage === s.stage && st === "RUNNING") ? C.live() : C.line3());
      segRing(ctx, x, y, r + (st === "FAILED" || st === "STALE" ? 6.5 : 3.5), cols);
    }
    if (isEye) point(ctx, x, y, r, { fill: "transparent", alpha: 0, ring: C.act(), ringGap: (c.stages && c.stages.length ? 8 : 5) + (st === "FAILED" ? 3 : 0) });
    label(ctx, c.case_id, x + r + 6, y - r - 2, { size: 10, color: isEye ? C.act() : C.chalk3() });
    sky.hit(x, y, r + 6, { kind: "case", id: c.case_id, exp: d.expKey });
  });
}

// ---------- the evidence chain: records on a time axis joined by their hashes ----------
export function drawChain(ctx, w, h, d, sky) {
  const recs = d.records || [];
  const left = 64, right = 28, top = 22, bottom = 34;
  const x0 = left, y0 = top, x1 = w - right, y1 = h - bottom;
  if (!recs.length) return;
  const ts = recs.map((r) => r.t);
  const tmin = Math.min(...ts), tmax = Math.max(...ts);
  const span = tmax - tmin || 3600;
  const X = (t) => x0 + 16 + ((t - tmin) / span) * (x1 - x0 - 32);
  const companies = [...new Set(recs.map((r) => r.company || "—"))];
  const rowH = (y1 - y0) / Math.max(companies.length, 1);
  const Y = (c) => y0 + (companies.indexOf(c || "—") + 0.5) * rowH;
  const nt = Math.max(2, Math.floor((x1 - x0) / 130));
  const tt = niceTicks(tmin, tmax, nt).filter((t) => t >= tmin - 1 && t <= tmax + 1);
  const sameDay = tmax - tmin < 20 * 3600;
  const tlabel = (t) => sameDay
    ? new Date(t * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : new Date(t * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" }) + " " + new Date(t * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  graticule(ctx, { x0, y0, x1, y1 }, {
    xTicks: tt.map(X), yTicks: companies.map((_, i) => y0 + i * rowH).concat([y1]),
    xLabels: tt.map((t) => ({ x: X(t), text: tlabel(t) })),
    yLabels: companies.map((c) => ({ y: Y(c), text: c })),
    xAxisLabel: "RECORDED", yAxisLabel: "",
  });
  // chain line
  for (let i = 1; i < recs.length; i++) {
    const a = recs[i - 1], b = recs[i];
    const ok = d.brokenAt === null || d.brokenAt === undefined || i < d.brokenAt;
    ctx.save(); ctx.strokeStyle = ok ? C.chalk2() : C.fault(); ctx.lineWidth = ok ? 1 : 1.5; if (!ok) ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(X(a.t), Y(a.company)); ctx.lineTo(X(b.t), Y(b.company)); ctx.stroke(); ctx.restore();
  }
  const nmax = Math.max(...recs.map((r) => r.n_cases || 1));
  // label placement: records seconds apart would overprint, so each label takes the first free row
  // (above the point, then below, then one row further) and never runs past the plot edges
  const placed = [];
  ctx.save(); ctx.font = `500 10px ${MONO()}`;
  const widths = recs.map((r) => ctx.measureText(r.experiment || "").width);
  ctx.restore();
  recs.forEach((r, i) => {
    const x = X(r.t), y = Y(r.company);
    const rad = magRadius((r.n_cases || 1) / nmax, 3, 9);
    const isEye = d.eye && d.eye.kind === "record" && d.eye.id === i;
    const broken = d.brokenAt !== null && d.brokenAt !== undefined && i >= d.brokenAt;
    point(ctx, x, y, rad, { fill: broken ? C.chalk3() : C.chalk(), ring: broken ? C.fault() : (r.delivered ? C.chalk2() : null), ringGap: 3 });   // delivered is past, so chalk: cyan is live only
    if (isEye) point(ctx, x, y, rad, { fill: "transparent", alpha: 0, ring: C.act(), ringGap: 7 });
    const w = widths[i];
    const lx = Math.max(x0 + 4 + w / 2, Math.min(x1 - 4 - w / 2, x));
    const rows = [y - rad - 8, y + rad + 15, y - rad - 20, y + rad + 27, y - rad - 32];
    let ly = rows[0];
    for (const cand of rows) {
      const clash = placed.some((p) => Math.abs(p.y - cand) < 11 && Math.abs(p.x - lx) < (p.w + w) / 2 + 6);
      if (!clash) { ly = cand; break; }
    }
    placed.push({ x: lx, y: ly, w });
    label(ctx, r.experiment || "", lx, ly, { size: 10, align: "center", color: isEye ? C.act() : C.chalk3() });
    sky.hit(x, y, rad + 6, { kind: "record", id: i });
  });
}

// ---------- small trace strip (host load, memory, residuals) ----------
export function drawTrace(canvas, values, { min = null, max = null, live = false, log = false } = {}) {
  const ctx = canvas.getContext("2d");
  const r = canvas.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = Math.max(10, r.width), h = Math.max(10, r.height);
  canvas.width = w * dpr; canvas.height = h * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const vs = values.filter((v) => Number.isFinite(v) && (!log || v > 0)).map((v) => (log ? Math.log10(v) : v));
  if (!vs.length) { label(ctx, "no samples yet", 0, h / 2, { size: 9, baseline: "middle" }); return; }
  const lo = min !== null ? (log ? Math.log10(min) : min) : Math.min(...vs);
  const hi = max !== null ? (log ? Math.log10(max) : max) : Math.max(...vs);
  const Y = (v) => h - 2 - ((v - lo) / ((hi - lo) || 1)) * (h - 4);
  ctx.save(); ctx.strokeStyle = C.line(); ctx.beginPath(); ctx.moveTo(0, h - 0.5); ctx.lineTo(w, h - 0.5); ctx.stroke(); ctx.restore();
  ctx.save(); ctx.strokeStyle = C.chalk2(); ctx.lineWidth = 1.2; ctx.beginPath();
  vs.forEach((v, i) => { const x = (i / Math.max(vs.length - 1, 1)) * (w - 4) + 2; const y = Y(v); if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); });
  ctx.stroke(); ctx.restore();
  const lx = w - 2, ly = Y(vs[vs.length - 1]);
  point(ctx, lx, ly, 2.2, { fill: live ? C.live() : C.chalk() });
}
