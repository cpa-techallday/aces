// Fabric plate: the rig as GRID CONTROL, its telemetry, the SIMLAB capacity table and rig-state report it, the
// fabric manifest the desktop holds, and the one action here (Refresh fabric). Every field names its source;
// a field no source reported is drawn as "not reported", never guessed. Bindings: /api/rig/status,
// /api/rig/telemetry, /api/rig/snapshot, /api/fabric, /api/sim/capacity, /api/rig/modes, POST /api/fabric/refresh;
// stamps rig.status, rig.telemetry, rig.snapshot, fabric.manifest, sim.capacity, rig.modes (spec 4.3).
import { Sky, drawFleet, drawTrace, fmtMiB } from "../sky.js";

const PATHS = { status: "/api/rig/status", telemetry: "/api/rig/telemetry", snapshot: "/api/rig/snapshot", fabric: "/api/fabric", capacity: "/api/sim/capacity", modes: "/api/rig/modes" };
let R = null;

export async function mount(root, ctx) {
  const { dom } = ctx;
  const { el, block } = dom;
  R = {
    ctx, root, res: {}, stamps: {}, status: null, telemetry: null, snapshot: null, fabric: null, capacity: null, modes: null,
    history: [], eye: null, sky: null, timer: null, refreshing: false, refreshResult: null, refreshPoll: null, dead: false,
    b: {
      mode: el("div"), queue: el("div.line"), manifest: el("div"), lanes: el("div"), cards: el("div.cards"),
      skyNote: el("div.note", { hidden: true }),
      canvas: el("canvas.sky", { "aria-label": "the GPUs of the rig charted over the last ten minutes: memory as the point size, utilisation as cyan", tabindex: "0" }),
      twin: el("div"), endpoints: el("div"), services: el("div"), foot: el("div.plate-foot.mono"),
    },
  };
  const skyWrap = el("div.sky-wrap", [R.b.canvas, R.b.skyNote]);
  // top to bottom (spec 4.3): the mode line and queue beside the manifest card with its one action, then the cards,
  // the fleet canvas, the rig-state tables, and the manifest's lanes and notes last
  root.append(
    block("mode · as GRID CONTROL reports it", [R.b.mode, R.b.queue], { id: "fab-mode" }),
    block("fabric manifest · held by this desktop", [R.b.manifest], { id: "fab-manifest" }),
    block("cards", [R.b.cards], { span2: true, id: "fab-cards", coord: "util, memory, temperature, derating: GRID CONTROL status · name, total, power, clock: telemetry · pool, lease, job, activity: SIMLAB capacity · processes: rig-state" }),
    block("fleet", [skyWrap, R.b.twin], { span2: true, id: "fab-fleet", coord: "time × GPU · last 10 min" }),
    block("rig-state snapshot · endpoints", [R.b.endpoints], { span2: true, id: "fab-endpoints" }),
    block("rig-state snapshot · services", [R.b.services], { span2: true, id: "fab-services" }),
    block("fabric manifest · lanes and notes as recorded", [R.b.lanes], { span2: true, id: "fab-lanes" }),
    R.b.foot,
  );
  R.sky = new Sky(R.b.canvas);
  R.sky.onPick((item) => { if (item && item.kind === "gpu") setEye(item.id); });
  R.sky.setRenderer((c, w, h, sky) => drawFleet(c, w, h, { gpus: skyRows(), history: R.history, now: Date.now() / 1000, eye: R.eye === null ? null : { kind: "gpu", id: R.eye }, modeText: modeText(), windowS: 600, beaconFresh: {} }, sky));
  if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) R.timer = setInterval(() => { if (R && R.sky) R.sky.draw(); }, 1000);

  ctx.events.on("rig.status", (st) => { take("status", st); renderMode(); renderCards(); renderSky(); renderFoot(); if (R.eye !== null) renderInspector(); });
  ctx.events.on("rig.telemetry", (st) => { R.stamps.telemetry = st; if (st.payload && st.payload.t) pushSample(st.payload); renderSky(); renderFoot(); if (R.eye === null) renderInspector(); });
  ctx.events.on("rig.snapshot", (st) => { R.stamps.snapshot = st; if (st.changed || st.ok === false) refetch("snapshot").then(() => { renderSnapshot(); renderFoot(); }); });
  ctx.events.on("fabric.manifest", (st) => { R.stamps.fabric = st; if (st.changed) refetch("fabric").then(renderManifest); else renderManifest(); });
  ctx.events.on("sim.capacity", (st) => { take("capacity", st); renderCards(); renderSky(); renderFoot(); if (R.eye !== null) renderInspector(); });
  // the server inlines GRID CONTROL's mode table in the rig.modes stamp (the dev harness the composed reply); keep the sim modes
  ctx.events.on("rig.modes", (st) => { R.stamps.modes = st; if (st.ok !== false && st.payload && typeof st.payload === "object") R.modes = "gridcontrol_modes" in st.payload ? { ...(R.modes || {}), ...st.payload } : { ...(R.modes || {}), gridcontrol_modes: st.payload }; renderMode(); });

  await Promise.all(Object.keys(PATHS).map(refetch));
  if (R.dead) return;
  renderAll();
  renderInspector();
}

export function unmount() {
  if (!R) return;
  R.dead = true;
  if (R.timer) clearInterval(R.timer);
  if (R.refreshPoll) clearInterval(R.refreshPoll);
  if (R.sky) R.sky.destroy();
  R = null;
}

// ---------- data ----------
function take(name, st) {
  R.stamps[name] = st;
  if (st.ok !== false && st.payload !== null && st.payload !== undefined) R[name] = st.payload;
}
async function refetch(name) {
  const r = await R.ctx.api.get(PATHS[name]);
  if (!R || R.dead) return;
  R.res[name] = r;
  if (r.ok) {
    if (r.data !== null && r.data !== undefined) R[name] = r.data;
    else if (!r.meta || r.meta.stale !== true) R[name] = null;
    if (name === "telemetry" && r.data && Array.isArray(r.data.history)) for (const s of r.data.history) pushSample(s);
  }
}
function pushSample(p) {
  if (!p || !Number.isFinite(p.t)) return;
  const lastT = R.history.length ? R.history[R.history.length - 1].t : -Infinity;
  if (p.t > lastT) { R.history.push(p); if (R.history.length > 240) R.history.shift(); }
}
function fetchedAge(name) {
  const st = R.stamps[name]; const res = R.res[name];
  const iso = (st && st.fetched_utc) || (res && res.meta && res.meta.fetched_utc) || null;
  return iso ? R.ctx.format.age(iso) : null;
}
function statusSwitchInfo() {
  const sw = R.ctx.events.last("rig.switch"); const p = sw && sw.payload;
  if (p && p.target && p.done !== true && p.idle !== true && (p.phase || p.startedAt)) return `${p.target} (${p.phase || "phase not reported"})`;
  return null;
}

// rows joined by GPU index; every field remembers which source reported it
function rows() {
  const st = R.status, tel = R.telemetry && R.telemetry.telemetry, cap = R.capacity, snap = R.snapshot;
  const idx = new Set();
  for (const arr of [st && st.gpus, tel && tel.gpus, cap && cap.gpus, snap && snap.gpus]) for (const g of arr || []) if (g && Number.isFinite(g.index)) idx.add(g.index);
  const find = (arr, i) => (arr || []).find((g) => g && g.index === i) || null;
  return [...idx].sort((a, b) => a - b).map((i) => {
    const s = find(st && st.gpus, i), t = find(tel && tel.gpus, i), c = find(cap && cap.gpus, i), n = find(snap && snap.gpus, i);
    const pick = (...cands) => { for (const [v, src] of cands) if (v !== null && v !== undefined) return [v, src]; return [null, null]; };
    const [name, nameSrc] = pick([t && t.name, "telemetry"], [c && c.name, "capacity"], [n && n.name, "rig-state"]);
    const [memUsedMiB, memSrc] = pick([s && s.mem_used_mb, "status"], [t && t.mem_used_mb, "telemetry"], [c && c.memUsedMiB, "capacity"], [n && n.memUsedMiB, "rig-state"]);
    const [memTotalMiB, totSrc] = pick([t && t.mem_total_mb, "telemetry"], [c && c.memTotalMiB, "capacity"], [n && n.memTotalMiB, "rig-state"]);
    const [utilPct, utilSrc] = pick([s && s.util, "status"], [t && t.util, "telemetry"], [c && c.utilPct, "capacity"], [n && n.utilPct, "rig-state"]);
    const [tempC, tempSrc] = pick([s && s.temp_c, "status"], [t && t.temp_c, "telemetry"], [c && c.tempC, "capacity"], [n && n.tempC, "rig-state"]);
    const [powerW, powSrc] = pick([t && t.power_w, "telemetry"], [c && c.powerW, "capacity"], [n && n.powerW, "rig-state"]);
    const [powerCapW] = pick([c && c.powerCapW, "capacity"], [n && n.powerCapW, "rig-state"]);
    const [clockMhz] = pick([t && t.clock_mhz, "telemetry"]);
    const [derated, derSrc] = pick([s && s.derated, "status"], [t && t.derated, "telemetry"], [c && c.derated, "capacity"]);
    const [processes, procSrc] = pick([c && c.processes, "capacity"], [n && n.processes, "rig-state"]);
    return {
      index: i, name, memUsedMiB, memTotalMiB, utilPct, tempC, powerW, powerCapW, clockMhz, derated, processes,
      in_pool: c ? !!c.in_pool : null, lease: c ? c.lease : null, job: c ? c.job : null, available: c ? c.available : null, activity: c ? (c.activity ?? null) : null, idle: c ? c.idle : n ? n.idle : null,
      sources: { name: nameSrc, memory: memSrc, total: totSrc, util: utilSrc, temp: tempSrc, power: powSrc, derated: derSrc, processes: procSrc, pool: c ? "capacity" : null },
    };
  });
}
// drawFleet (sky.js, copied unchanged) prints "not in the sim pool" for a row whose in_pool is falsy; when the
// capacity table is unavailable the pool is unknown, so the rows are drawn without pool rings and the plate note says so
function skyRows() {
  const known = !!R.capacity;
  return rows().map((g) => ({ ...g, in_pool: known ? g.in_pool : true }));
}
function modeText() {
  const s = R.status;
  if (!s || !s.mode) return "MODE NOT REPORTED";
  const pool = R.capacity && Array.isArray(R.capacity.pool) ? (R.capacity.pool.length ? R.capacity.pool.join(" ") : "none") : "not reported";
  const det = s.detected_mode && s.detected_mode !== s.mode ? ` · DETECTED ${String(s.detected_mode).toUpperCase()}` : "";
  return `MODE ${String(s.mode).toUpperCase()} · SIM POOL ${pool.toUpperCase()}${det}`;
}

// ---------- rendering ----------
function renderAll() { renderMode(); renderManifest(); renderCards(); renderSky(); renderSnapshot(); renderFoot(); }

function renderMode() {
  const { dom, format } = R.ctx; const { el, clear, absence, note } = dom;
  const box = R.b.mode; clear(box); clear(R.b.queue);
  const s = R.status;
  const abs = absence(R.stamps.status && R.stamps.status.ok === false ? { ok: true, meta: { ok: false, error: R.stamps.status.error, next_try_s: R.stamps.status.next_try_s, stale: !!s } } : R.res.status, "GRID CONTROL status");
  if (abs) box.append(abs);
  if (!s) { if (!abs) box.append(note("GRID CONTROL status not loaded", "no reply yet")); R.ctx.coord(""); return; }
  const gcm = R.modes && R.modes.gridcontrol_modes ? R.modes.gridcontrol_modes[s.mode] : null;
  const k = (t) => el("span.k", { text: t }), b = (t, cls = "") => el("b" + (cls ? "." + cls : ""), { text: t === null || t === undefined ? "not reported" : String(t) });
  const sw = statusSwitchInfo();
  box.append(el("div.line", [
    k("mode"), b(s.mode, "ink-amber"),
    k("label"), b(gcm ? gcm.label : R.modes ? "mode not in GRID CONTROL's mode table" : "mode table not loaded"),
    k("detected"), b(s.detected_mode, s.detected_mode === "transition" ? "ink-amber" : ""),
    k("auto switch"), b(s.auto_enabled === true ? "on" : s.auto_enabled === false ? "off" : null, s.auto_enabled === true ? "ink-amber" : ""),
    k("switching"), b(sw ? sw : s.switching ? (typeof s.switching === "string" ? s.switching : JSON.stringify(s.switching)) : "none", s.switching || sw ? "ink-amber" : ""),
    k("last switch"), b(s.last_switch_ts ? `${format.utc(s.last_switch_ts)} (${format.ago(format.age(s.last_switch_ts))})` : null),
  ]));
  if (s.mc_console || s.gridwatch) box.append(el("div.line", [k("mc_console"), b(s.mc_console || null), k("gridwatch"), b(s.gridwatch || null)]));
  const q = s.queue || {};
  R.b.queue.append(k("queue"));
  if (q.queued === null || q.queued === undefined || q.running === null || q.running === undefined) {
    R.b.queue.append(b(`queue count not reported${s.queue_note ? ": " + s.queue_note : ""}`));
    if (s.raw_queue) R.b.queue.append(el("span.dim", { text: `as received: ${JSON.stringify(s.raw_queue)}` }));
  } else R.b.queue.append(b(`${q.queued} queued · ${q.running} running`));
  R.ctx.coord(`mode ${s.mode}${s.detected_mode && s.detected_mode !== s.mode ? " · detected " + s.detected_mode : ""}`);
}

function renderManifest() {
  const { dom, format } = R.ctx; const { el, clear, absence, note, kv, table, button } = dom;
  const box = R.b.manifest; clear(box);
  const lanesBox = R.b.lanes; clear(lanesBox);
  const f = R.fabric; const abs = absence(R.res.fabric, "fabric manifest");
  if (abs) box.append(abs);
  if (!f) { if (!abs) box.append(note("fabric manifest not loaded", "no reply yet")); lanesBox.append(note("lanes", "no manifest loaded")); return; }
  const live = R.stamps.fabric && R.stamps.fabric.payload ? R.stamps.fabric.payload : null;
  const ageS = live && live.age_s !== null && live.age_s !== undefined ? live.age_s : f.age_s;
  const stale = live ? !!live.stale : !!f.stale;
  const maxAge = f.max_age_s ?? 900;
  const k = (t) => el("span.k", { text: t }), b = (t, cls = "") => el("b" + (cls ? "." + cls : ""), { text: t === null || t === undefined ? "not reported" : String(t) });
  if (!f.manifest) {
    box.append(note("no manifest", f.reason || "no manifest yet; run aces fabric refresh", { act: true, detail: f.path || null }));
    lanesBox.append(note("lanes", "no manifest: no lanes recorded", { detail: f.path || null }));
  } else {
    const m = f.manifest;
    box.append(el("div.line", [k("generated"), b(format.utc(m.generated)), k("age"), b(`${format.span(ageS)} of ${maxAge} s${stale ? " · stale" : ""}`, stale ? "ink-amber" : ""), k("path"), b(f.path)]));
    const health = m.health || {};
    box.append(el("div.cap", { text: "health as recorded" }));
    box.append(kv(Object.entries(health).map(([key, v]) => [key, dom.ink(/^unreachable/i.test(String(v)) ? "fault" : "chalk", String(v)), ""])));
    const mm = m.mode;
    box.append(el("div.cap", { text: "mode as recorded" }));
    if (!mm) box.append(el("div.prose.dim", { text: "mode: null (the refresh could not read GRID CONTROL)" }));
    else {
      const q = mm.queue || {};
      const qt = (q.queued === -1 || q.running === -1) ? `queue count not reported: GRID CONTROL reported ${JSON.stringify(q)}` : (q.queued !== undefined ? `${q.queued} queued · ${q.running} running` : "not recorded");
      box.append(kv([["name", String(mm.name), "act"], ["detected", String(mm.detected ?? "not recorded")], ["label", String(mm.label ?? "not recorded")], ["switching", mm.switching === null || mm.switching === undefined ? "none" : String(mm.switching)], ["queue", qt], ["auto switching", mm.auto_switching === undefined ? "not recorded" : String(mm.auto_switching)]]));
    }
    box.append(el("div.line", [k("lanes"), b(String((m.lanes || []).length)), k("notes"), b(String((m.notes || []).length))]));
    if (m.server && m.server.discovered) box.append(kv(Object.entries(m.server.discovered).map(([key, v]) => [`discovered ${key}`, String(v)])));
    lanesBox.append(table([
      { th: "lane", key: "id", cls: "nm" }, { th: "kind", key: "kind" },
      { th: "gpus", render: (l) => (Array.isArray(l.gpus) ? (l.gpus.length ? l.gpus.join(" ") : "none") : "not recorded") },
      { th: "model", render: (l) => l.model || "not recorded" },
      { th: "endpoint", render: (l) => l.endpoint || "no endpoint" },
      { th: "note", key: "note", cls: "body" },
    ], m.lanes || [], { empty: "no lanes in the manifest", caption: "lanes of the fabric manifest" }));
    if (Array.isArray(m.notes) && m.notes.length) {
      lanesBox.append(el("div.cap", { text: "notes as recorded" }));
      lanesBox.append(el("ul.prose", m.notes.map((n) => el("li", { text: String(n) }))));
    }
  }
  const btn = button("Refresh fabric", { disabled: R.refreshing, title: "runs aces fabric refresh on this desktop (six rig reads, one manifest write, one ledger entry); never a mode switch", onclick: refreshFabric });
  box.append(el("div.actrow", [btn, R.refreshResult ? el("span.result" + (R.refreshResult.cls ? "." + R.refreshResult.cls : ""), { text: R.refreshResult.text }) : null]));
}

async function refreshFabric() {
  if (!R || R.refreshing) return;
  R.refreshing = true; R.refreshResult = { cls: "live", text: "requesting…" }; renderManifest();
  const before = R.fabric && R.fabric.manifest ? R.fabric.manifest.generated : null;
  const r = await R.ctx.api.post("/api/fabric/refresh", {});
  if (!R || R.dead) return;
  if (r.ok) {
    R.refreshResult = { cls: "live", text: `refresh started (HTTP ${r.status}); the manifest age resets when aces fabric refresh completes` };
    let tries = 0;
    if (R.refreshPoll) clearInterval(R.refreshPoll);
    R.refreshPoll = setInterval(async () => {
      tries++;
      await refetch("fabric");
      if (!R || R.dead) return;
      const gen = R.fabric && R.fabric.manifest ? R.fabric.manifest.generated : null;
      if (gen && gen !== before) { R.refreshResult = { cls: "", text: `manifest regenerated ${R.ctx.format.utc(gen)}` }; clearInterval(R.refreshPoll); R.refreshPoll = null; R.refreshing = false; }
      else if (tries >= 30) { R.refreshResult = { cls: "act", text: "manifest not regenerated after 60 s; see the server log" }; clearInterval(R.refreshPoll); R.refreshPoll = null; R.refreshing = false; }
      renderManifest();
    }, 2000);
  } else {
    const retry = r.body && r.body.retry_after_s !== undefined ? `; retry after ${r.body.retry_after_s} s` : "";
    R.refreshResult = { cls: r.status === 403 || (r.status >= 500 && r.status !== 503) ? "fault" : "act", text: `${r.error} (HTTP ${r.status}${r.code ? ", " + r.code : ""})${retry}` };
    R.refreshing = false;
  }
  renderManifest();
}

function card(g) {
  const { dom, format } = R.ctx; const { el } = dom;
  const live = !!g.job || (g.utilPct || 0) > 0;
  let tagCls = "", tagText;
  if (g.job) { tagCls = "live"; tagText = `job ${g.job.experiment || ""} ${g.job.case_id || ""} · ${g.job.stage || "run"}`.replace(/\s+/g, " ").trim(); }
  else if (g.lease) { tagCls = "live"; tagText = `lease ${g.lease}`; }
  else if (g.derated === true) { tagCls = "fault"; tagText = "derated by GRID CONTROL"; }
  else if (g.activity) tagText = g.activity;
  else if (g.processes && g.processes.length) tagText = `${g.processes[0].processName || "process"} ${g.processes[0].pid || ""} · ${fmtMiB(g.processes[0].usedMemMiB || 0)}${g.processes.length > 1 ? " +" + (g.processes.length - 1) : ""}`;
  else tagText = R.capacity ? "activity not reported" : "activity not reported (SIMLAB capacity unavailable)";
  const used = g.memUsedMiB, total = g.memTotalMiB;
  const frac = used !== null && total ? Math.max(0, Math.min(1, used / total)) : 0;
  const meter = el("div.meter" + (live ? ".live" : ""), [el("i")]);
  meter.firstElementChild.style.setProperty("--w", (frac * 100).toFixed(1) + "%");
  const val = (key, v, cls = "") => el("div.val", [el("span", { text: key }), el("b" + (cls ? "." + cls : ""), { text: v })]);
  const btn = el("button.card" + (R.eye === g.index ? ".eye" : "") + (g.derated === true ? ".fault" : ""), { type: "button", "aria-pressed": R.eye === g.index ? "true" : "false", title: `GPU ${g.index}: open in the inspector`, onclick: () => setEye(g.index) }, [
    el("div.nm", [`GPU ${g.index}`, el("span" + (live ? ".ink-cyan" : ".dim"), { text: g.utilPct === null ? "util not reported" : `${g.utilPct} %` })]),
    meter,
    val("memory", `${used === null ? "not reported" : fmtMiB(used)} / ${total ? fmtMiB(total) : "total not reported"}`),
    val("temp", g.tempC === null ? format.DASH : `${g.tempC} °C`),
    val("power", g.powerW === null ? format.DASH : `${format.num(g.powerW)} W${g.powerCapW ? " / " + g.powerCapW + " W" : ""}`),
    val("pool", g.in_pool === null ? "not reported" : g.in_pool ? "in the sim pool" : "not in the sim pool"),
    el("div.tag" + (tagCls ? "." + tagCls : ""), { text: tagText }),
  ]);
  return btn;
}

function renderCards() {
  const { dom } = R.ctx; const { clear, absence, note } = dom;
  clear(R.b.cards);
  const rs = rows();
  if (!rs.length) {
    R.b.cards.append(absence(R.res.status, "GPU rows") || note("no GPU rows", "GRID CONTROL status, telemetry, SIMLAB capacity and rig-state returned no GPU rows"));
    return;
  }
  for (const g of rs) R.b.cards.append(card(g));
}

function renderSky() {
  const { dom, format } = R.ctx; const { el, clear } = dom;
  if (!R.sky) return;
  const rs = rows();
  const noteEl = R.b.skyNote; clear(noteEl);
  const telErr = R.stamps.telemetry && R.stamps.telemetry.ok === false ? R.stamps.telemetry.error : (R.res.telemetry && R.res.telemetry.ok === false ? R.res.telemetry.error : (R.res.telemetry && R.res.telemetry.meta && R.res.telemetry.meta.error) || null);
  const inWindow = R.history.filter((s) => Date.now() / 1000 - s.t <= 600).length;
  if (!rs.length) {
    noteEl.hidden = false; noteEl.className = "note fault";
    noteEl.append(el("span.cap", { text: "no fleet data" }), (R.res.status && R.res.status.error) || telErr || "waiting for the first status and telemetry samples");
  } else if (!inWindow) {
    noteEl.hidden = false; noteEl.className = "note";
    const newest = R.history.length ? R.history[R.history.length - 1].t : null;
    const why = telErr ? `GRID CONTROL telemetry: ${telErr}` : newest ? `the newest telemetry sample is from ${format.utc(newest)} (${format.ago(Date.now() / 1000 - newest)}); nothing within the last 10 min` : "the first telemetry sample draws the present points; trails grow from here";
    // the native append() prints a null child as the text "null": only real children go in
    noteEl.append(...[el("span.cap", { text: "no trail in the window" }), why, !R.capacity ? el("span.mono", { text: "sim pool membership not reported (SIMLAB capacity unavailable): no pool rings drawn" }) : null].filter((c) => c !== null));
  } else noteEl.hidden = true;
  R.sky.draw();
  // the hidden twin carries the same numbers as the canvas (spec 4.7)
  const twin = R.b.twin; clear(twin);
  twin.append(dom.table([
    { th: "GPU", render: (g) => `GPU ${g.index}` }, { th: "memory used", render: (g) => (g.memUsedMiB === null ? "not reported" : fmtMiB(g.memUsedMiB)) },
    { th: "memory total", render: (g) => (g.memTotalMiB ? fmtMiB(g.memTotalMiB) : "not reported") }, { th: "utilisation", render: (g) => (g.utilPct === null ? "not reported" : g.utilPct + " %") },
    { th: "state", render: (g) => (g.job ? "job" : g.lease ? "leased" : g.derated ? "derated" : g.activity || (g.in_pool === null ? "pool not reported" : g.in_pool ? "in the sim pool" : "not in the sim pool")) },
  ], rs, { caption: "the fleet canvas as a table", cls: "visually-hidden" }));
}

function renderSnapshot() {
  const { dom, format, vocab } = R.ctx; const { el, clear, absence, note, table } = dom;
  clear(R.b.endpoints); clear(R.b.services);
  const r = R.res.snapshot; const s = R.snapshot;
  const abs = absence(R.stamps.snapshot && R.stamps.snapshot.ok === false ? { ok: true, meta: { ok: false, error: R.stamps.snapshot.error, next_try_s: R.stamps.snapshot.next_try_s, stale: !!s } } : r, "rig-state snapshot");
  if (abs) { R.b.endpoints.append(abs); }
  if (!s) { if (!abs) R.b.endpoints.append(note("rig-state snapshot not loaded", "no reply yet")); R.b.services.append(note("services", "no snapshot")); return; }
  const cls = {}; for (const c of s.classification || []) cls[`${c.kind}:${c.name}`] = c;
  const chip = (kind, name) => { const c = cls[`${kind}:${name}`]; return c ? vocab.state("endpoint", c.verdict, { title: `${c.verdict}${c.rule ? " — " + c.rule : ""}${c.matched ? " (matched " + c.matched + ")" : ""}` }) : el("span.dim", { text: "classification not provided by the backend" }); };
  const degraded = Array.isArray(s.degraded) ? s.degraded : [];
  R.b.endpoints.append(el("div.line", [el("span.k", { text: "captured" }), el("b", { text: format.utc(s.capturedAtIso) }), el("span.k", { text: "degraded" }), el("b" + (degraded.length ? ".ink-amber" : ""), { text: String(degraded.length) }), el("span.k", { text: "allHealthy" }), el("b", { text: String(s.allHealthy) })]));
  R.b.endpoints.append(table([
    { th: "endpoint", key: "name", cls: "nm" }, { th: "url", key: "url" },
    { th: "reachable", render: (e) => String(e.reachable) }, { th: "latency ms", render: (e) => (e.latencyMs === null || e.latencyMs === undefined ? format.DASH : String(e.latencyMs)), num: true },
    { th: "expected model", render: (e) => [e.expectedModel || "not recorded", el("span.sub", { text: `matches: ${String(e.matchesExpected)}` })], cls: "body" },
    { th: "models served", render: (e) => ((e.models || []).map((m) => (typeof m === "string" ? m : m.id)).join(", ") || "none served"), cls: "body" },
    { th: "error", render: (e) => (e.error ? dom.ink("fault", e.error) : format.DASH) },
    { th: "classification", render: (e) => chip("endpoint", e.name) },
  ], s.endpoints || [], { empty: "no endpoints in the snapshot", caption: "rig-state endpoints" }));
  if (degraded.length) R.b.endpoints.append(el("div.prose.dim", { text: `degraded as rig-state lists them: ${degraded.join("; ")}` }));
  R.b.services.append(table([
    { th: "service", key: "name", cls: "nm" }, { th: "unit", key: "unit" },
    { th: "systemd state", render: (v) => dom.ink(v.activeState === "failed" ? "fault" : v.activeState === "active" ? "cyan" : "chalk", `${v.activeState}${v.subState ? " · " + v.subState : ""}`) },
    { th: "main pid", render: (v) => (v.mainPid ? String(v.mainPid) : "none"), num: true },
    { th: "active since", render: (v) => v.activeEnterIso || "not recorded" },
    { th: "classification", render: (v) => chip("service", v.name) },
  ], s.services || [], { empty: "no services in the snapshot", caption: "rig-state services" }));
}

function renderFoot() {
  const { dom, format } = R.ctx; const { clear } = dom;
  const items = [];
  const agel = (label, name) => { const a = fetchedAge(name); items.push(`${label} ${a === null ? (R.res[name] && R.res[name].ok === false ? "unavailable" : "age not reported") : format.ago(a)}`); };
  agel("GRID CONTROL status", "status"); agel("telemetry", "telemetry"); agel("rig-state", "snapshot"); agel("SIMLAB capacity", "capacity");
  items.push(`${R.history.length} telemetry samples held`);
  if (R.fabric && R.fabric.path) items.push(`manifest ${R.fabric.path}`);
  clear(R.b.foot); R.b.foot.append(dom.facts(items));
}

// ---------- inspector ----------
function setEye(i) { R.eye = R.eye === i ? null : i; renderCards(); renderSky(); renderInspector(); }

function renderInspector() {
  const { dom, format } = R.ctx; const { el, block, kv } = dom;
  const rs = rows();
  const g = R.eye === null ? null : rs.find((r) => r.index === R.eye) || null;
  const body = [];
  if (g) {
    const stateWord = g.job ? "job" : g.lease ? "leased" : g.derated === true ? "derated" : g.activity ? g.activity : g.in_pool === null ? "pool not reported" : g.in_pool ? (g.idle === false ? "busy (processes)" : "idle, not leased") : "not in the sim pool";
    body.push(block("state", kv([
      ["state", stateWord, g.job ? "live" : g.derated === true ? "fault" : ""],
      ["memory", `${g.memUsedMiB === null ? "not reported" : fmtMiB(g.memUsedMiB)} / ${g.memTotalMiB ? fmtMiB(g.memTotalMiB) : "total not reported"}`],
      ["utilisation", g.utilPct === null ? "not reported" : `${g.utilPct} %`], ["temperature", g.tempC === null ? "not reported" : `${g.tempC} °C`],
      ["power", g.powerW === null ? "not reported" : `${format.num(g.powerW)} W${g.powerCapW ? " / " + g.powerCapW + " W" : ""}`], ["clock", g.clockMhz === null ? "not reported" : `${g.clockMhz} MHz`],
      ["name", g.name || "not reported"], ["available to sim", g.available === null ? "not reported" : String(g.available)],
    ])));
    body.push(block("lease · job", g.job ? kv([["job", g.job.job_id, "live"], ["experiment", g.job.experiment], ["case", g.job.case_id], ["solver", g.job.solver], ["stage", g.job.stage || "run"], ["state (meta)", g.job.state], ["elapsed", format.dur(g.job.elapsed_s)], ["owner", g.job.owner]])
      : g.lease ? kv([["lease holder", g.lease, "live"]]) : el("div.prose.dim", { text: R.capacity ? "no lease and no queued job on this GPU" : "lease and job not reported: SIMLAB capacity unavailable" })));
    body.push(block("processes on the card", g.processes === null ? el("div.prose.dim", { text: "process list not reported (rig-state and capacity unavailable)" }) : g.processes.length ? el("div.procs", g.processes.map((p) => el("div", [el("span", [`${p.processName || "process"} `, el("b", { text: String(p.pid ?? "") })]), el("span", { text: fmtMiB(p.usedMemMiB || 0) })]))) : el("div.prose.dim", { text: "no processes hold memory on this card" })));
    body.push(block("sources", kv(Object.entries(g.sources).map(([k, v]) => [k, v || "none"]))));
    R.ctx.inspector.show(body, { name: `GPU ${g.index}`, coord: `${g.name || "name not reported"} · ${g.in_pool === null ? "pool not reported" : g.in_pool ? "in the sim pool" : "outside the sim pool"}${g.derated === true ? " · derated" : ""}` });
  } else {
    body.push(block("fleet", kv([
      ["cards", String(rs.length)], ["with a job", String(rs.filter((r) => r.job).length), rs.some((r) => r.job) ? "live" : ""], ["leased", String(rs.filter((r) => r.lease).length)],
      ["derated", String(rs.filter((r) => r.derated === true).length), rs.some((r) => r.derated === true) ? "fault" : ""],
      ["available to sim", R.capacity && Array.isArray(R.capacity.available) ? (R.capacity.available.join(" ") || "none") : "not reported"],
      ["memory in use", fmtMiB(rs.reduce((a, r) => a + (r.memUsedMiB || 0), 0))],
      ["capacity source", R.capacity ? R.capacity.source || "not reported" : "SIMLAB capacity unavailable"],
    ])));
    R.ctx.inspector.show(body, { name: "fleet", coord: `${rs.length} cards · ${modeText().toLowerCase()}`, ringed: false });
  }
  body.push(hostBlock());
  R.ctx.inspector.show(body, g ? null : undefined);
  drawHostTraces();
}

function hostBlock() {
  const { dom, format } = R.ctx; const { el, block, kv } = dom;
  const tel = R.telemetry && R.telemetry.telemetry;
  if (!tel) return block("host", el("div.prose.dim", { text: (R.res.telemetry && R.res.telemetry.ok === false && R.res.telemetry.error) || (R.res.telemetry && R.res.telemetry.meta && R.res.telemetry.meta.error) || "no telemetry yet" }));
  const c = tel.cpu || {}, m = tel.mem || {};
  const wrap = el("div");
  wrap.append(el("div.trace", [el("span", { text: "load 1" }), el("canvas#trCpu"), el("b", { text: `${format.num(c.load1)} / ${c.cores || format.DASH}` })]));
  wrap.append(el("div.trace", [el("span", { text: "ram" }), el("canvas#trMem"), el("b", { text: `${fmtMiB(m.used_mb || 0)} / ${fmtMiB(m.total_mb || 0)}` })]));
  wrap.append(kv((tel.disk || []).map((d) => [`disk ${d.mount}`, `${fmtMiB(d.avail_mb)} free of ${fmtMiB(d.total_mb)}`, d.total_mb && d.avail_mb / d.total_mb < 0.1 ? "fault" : ""])));
  if (tel.ts) wrap.append(kv([["sampled", format.utc(tel.ts)]]));
  return block("host · telemetry", wrap);
}
function drawHostTraces() {
  const tel = R.telemetry && R.telemetry.telemetry; if (!tel) return;
  const hist = R.history;
  setTimeout(() => {
    const cpu = document.getElementById("trCpu"), mem = document.getElementById("trMem");
    if (cpu) drawTrace(cpu, hist.map((s) => s.cpu), { min: 0, max: Math.max((tel.cpu && tel.cpu.cores) || 1, ...hist.map((s) => s.cpu || 0)) });
    if (mem) drawTrace(mem, hist.map((s) => s.mem), { min: 0, max: (tel.mem && tel.mem.total_mb) || null });
  }, 0);
}
