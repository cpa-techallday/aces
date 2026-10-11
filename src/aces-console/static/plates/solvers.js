// Solvers plate: the rig's solver registry as the SIMLAB console reports it (validation status with the registry
// vocabulary, the image id or "not recorded", the citability note of spec 3.6, the lane column that reads "not recorded"
// while the rig records no queue on any row), the GPU capacity with the activity labels of spec 3.6, and the mode
// pools (the sim mode table beside GRID CONTROL's mode table). Bindings: /api/sim/registry, /api/sim/capacity,
// /api/rig/modes; stamps sim.registry (notice), sim.capacity and rig.modes (inline). Deep link ?plate=solvers&solver=<name>.
const PATHS = { registry: "/api/sim/registry", capacity: "/api/sim/capacity", modes: "/api/rig/modes" };
let R = null;

export async function mount(root, ctx) {
  const { dom } = ctx; const { el, block } = dom;
  R = {
    ctx, root, res: {}, stamps: {}, registry: null, capacity: null, modes: null, solver: ctx.params.get("solver") || null, dead: false,
    b: { registry: el("div"), capacity: el("div"), modes: el("div"), foot: el("div.plate-foot.mono") },
  };
  root.append(
    block("registry · SIMLAB console /api/registry", [R.b.registry], { span2: true, id: "sol-registry", coord: "validation.status · image id · citability · lane" }),
    block("capacity · SIMLAB console /api/capacity", [R.b.capacity], { span2: true, id: "sol-capacity", coord: "activity labels: job · lease · outside the queue (operator hand-run) · rests by mode · not in the sim pool · derated · idle" }),
    block("modes · sim pools (capacity.modes) beside GRID CONTROL's mode table", [R.b.modes], { span2: true, id: "sol-modes" }),
    R.b.foot,
  );
  ctx.events.on("sim.registry", (st) => { if (st.changed) refetch("registry").then(() => { renderRegistry(); renderFoot(); renderInspector(); }); });
  ctx.events.on("sim.capacity", (st) => { R.stamps.capacity = st; if (st.ok !== false && st.payload) R.capacity = st.payload; renderCapacity(); renderModes(); renderFoot(); });
  ctx.events.on("rig.modes", (st) => { R.stamps.modes = st; if (st.ok !== false && st.payload && typeof st.payload === "object") R.modes = "gridcontrol_modes" in st.payload ? { ...(R.modes || {}), ...st.payload } : { ...(R.modes || {}), gridcontrol_modes: st.payload }; renderModes(); }); // the stamp inlines GRID CONTROL's mode table only
  await Promise.all(Object.keys(PATHS).map(refetch));
  if (R.dead) return;
  renderRegistry(); renderCapacity(); renderModes(); renderFoot(); renderInspector();
}

export function unmount() { if (R) R.dead = true; R = null; }

async function refetch(name) {
  const r = await R.ctx.api.get(PATHS[name]);
  if (!R || R.dead) return;
  R.res[name] = r;
  if (r.ok) { if (r.data !== null && r.data !== undefined) R[name] = r.data; else if (!r.meta || r.meta.stale !== true) R[name] = null; }
}

function chip(vocabulary, word) {
  const { vocab, dom } = R.ctx;
  if (word === null || word === undefined || word === "") return vocabulary === "registry" ? vocab.state("registry", "not recorded") : dom.el("span.dim", { text: "not recorded" });
  return vocab.known(vocabulary, word) ? vocab.state(vocabulary, word) : dom.el("span", { text: String(word), title: `${word}: not a word of the ${vocabulary} vocabulary; shown as received` });
}

// the registry rows as a list, whatever the backend's container (an object keyed by name, or a list of rows)
function rows() {
  const d = R.registry;
  if (!d) return [];
  const src = d.solvers || d.registry || d.rows || d;
  if (Array.isArray(src)) return src.map((r) => ({ ...r, name: r.name || r.solver || r.id || "(no name)" }));
  if (src && typeof src === "object") return Object.entries(src).filter(([k, v]) => !k.startsWith("_") && v && typeof v === "object").map(([k, v]) => ({ ...v, name: v.name || k }));
  return [];
}

function citability(r) {
  // the backend's note when it sent one (spec 3.6); otherwise derived from the row itself with the same rule
  if (r.citable_note) return { citable: r.citable === true, note: r.citable_note };
  const v = r.validation || {};
  if (v.status === "pass" && v.image_id) return { citable: true, note: "pass" };
  if (v.status === "pass") return { citable: false, note: "pass; image id not recorded, not citable against a running image" };
  return { citable: false, note: v.status ? String(v.status) : "not recorded" };
}

function select(name) {
  R.solver = R.solver === name ? null : name;
  const u = new URL(location.href); u.searchParams.set("plate", "solvers"); if (R.solver) u.searchParams.set("solver", R.solver); else u.searchParams.delete("solver"); history.replaceState(null, "", u);
  renderRegistry(); renderInspector();
}

function renderRegistry() {
  const { dom, format } = R.ctx; const { el, clear, absence, note, table, button } = dom;
  const box = R.b.registry; clear(box);
  const abs = absence(R.res.registry, "solver registry");
  if (abs) box.append(abs);
  const rs = rows();
  if (!R.registry) { if (!abs) box.append(note("registry not loaded", "no reply yet")); R.ctx.coord(""); return; }
  const nPass = rs.filter((r) => (r.validation || {}).status === "pass").length, nFail = rs.filter((r) => (r.validation || {}).status === "fail").length;
  const nImage = rs.filter((r) => (r.validation || {}).image_id).length, nLane = rs.filter((r) => r.queue !== null && r.queue !== undefined).length;
  box.append(el("div.line", [el("span.k", { text: "solvers" }), el("b", { text: String(rs.length) }), el("span.k", { text: "pass" }), el("b", { text: String(nPass) }), el("span.k", { text: "fail" }), el("b" + (nFail ? ".ink-fault" : ""), { text: String(nFail) }),
    el("span.k", { text: "image id recorded" }), el("b", { text: `${nImage} of ${rs.length}` }), el("span.k", { text: "lane recorded" }), el("b", { text: nLane ? `${nLane} of ${rs.length}` : `none of ${rs.length}: every lane reads "not recorded"` })]));
  box.append(table([
    { th: "solver", render: (r) => button(r.name, { title: `${r.name}: validation cases and notes in the inspector`, onclick: () => select(r.name) }), cls: "nm" },
    { th: "physics", render: (r) => (Array.isArray(r.physics) ? r.physics.join(", ") : r.physics || "not recorded") },
    { th: "precision", render: (r) => (Array.isArray(r.precision) ? r.precision.join(", ") : r.precision || "not recorded") },
    { th: "image", render: (r) => r.image || "not recorded" },
    { th: "image id", render: (r) => ((r.validation || {}).image_id ? el("code.mono", { text: String(r.validation.image_id) }) : el("span.dim", { text: "not recorded" })), cls: "body" },
    { th: "validation", render: (r) => chip("registry", (r.validation || {}).status) },
    { th: "validated", render: (r) => (r.validation || {}).date || "not recorded" },
    { th: "cases", render: (r) => String(((r.validation || {}).cases || []).length), num: true },
    { th: "citability", render: (r) => { const c = citability(r); return el("span" + (c.citable ? "" : ".dim"), { text: c.note }); }, cls: "body" },
    { th: "lane", render: (r) => (r.queue === null || r.queue === undefined ? chip("registry", null) : (r.lane !== undefined && r.lane !== "not recorded" ? String(r.lane) : String(r.queue))) },
    { th: "stages", render: (r) => (Array.isArray(r.stages) ? (r.stages.length ? String(r.stages.length) : "none") : "none"), num: true },
    { th: "module", render: (r) => el("code.mono", { text: r.module || "not recorded" }), cls: "body" },
  ], rs.map((r) => ({ ...r, __cls: R.solver === r.name ? "eye" : null })), { empty: "the console's registry lists no solvers", caption: "solver registry", cls: "wrapnames" }));
  box.append(el("div.prose.dim", { text: "citable: a pass whose validation names the image id it ran on (cudax docs/GOLDEN_RIG.md); a pass without an image id is shown as a pass that cannot be cited against a running image. The lane is the row's queue as recorded; nothing is assumed when it is absent." }));
  R.ctx.coord(`${rs.length} solvers · ${nPass} pass · ${nFail} fail · image id on ${nImage}`);
}

function activityOf(g, cap) {
  if (g.activity) return g.activity;   // the backend's label (spec 3.6)
  if (g.job) return "job";
  if (g.lease) return "lease";
  if (g.in_pool && (g.processes || []).length) return "outside the queue (operator hand-run)";
  const rest = (((cap || {}).modes || {})[(cap || {}).mode] || {}).rest || [];
  if (rest.includes(g.index)) return `rests by mode ${cap.mode}`;
  if (!g.in_pool) return "not in the sim pool";
  if (g.derated) return "derated";
  return "idle";
}

function renderCapacity() {
  const { dom, format } = R.ctx; const { el, clear, absence, note, table } = dom;
  const box = R.b.capacity; clear(box);
  const st = R.stamps.capacity;
  const abs = absence(st && st.ok === false ? { ok: true, meta: { ok: false, error: st.error, next_try_s: st.next_try_s, stale: !!R.capacity } } : R.res.capacity, "SIMLAB capacity");
  if (abs) box.append(abs);
  const c = R.capacity;
  if (!c) { if (!abs) box.append(note("capacity not loaded", "no reply yet")); return; }
  const k = (t) => el("span.k", { text: t }), b = (t, cls = "") => el("b" + (cls ? "." + cls : ""), { text: t === null || t === undefined ? "not reported" : String(t) });
  const list = (a) => (Array.isArray(a) ? (a.length ? a.join(" ") : "none") : null);
  const gq = c.gridcontrol_queue || {};
  const qText = gq.queued === null || gq.queued === undefined || gq.running === null || gq.running === undefined
    ? `queue count not reported${c.queue_note ? ": " + c.queue_note : ""}` : `${gq.queued} queued · ${gq.running} running`;
  box.append(el("div.line", [k("source"), b(c.source), k("mode"), b(c.mode, "ink-amber"), k("sim pool"), b(list(c.pool)), k("available"), b(list(c.available), Array.isArray(c.available) && c.available.length ? "ink-cyan" : ""), k("derated"), b(list(c.derated), Array.isArray(c.derated) && c.derated.length ? "ink-fault" : ""), k("GRID CONTROL queue"), b(qText), k("sampled"), b(c.ts ? format.utc(c.ts) : null)]));
  if (Array.isArray(c.errors) && c.errors.length) box.append(note("errors, as the console reports them", c.errors.map(String).join("; "), { act: true }));
  const gpus = Array.isArray(c.gpus) ? c.gpus : [];
  const nProc = gpus.reduce((a, g) => a + ((g.processes || []).length), 0), nLease = gpus.filter((g) => g.lease).length, nJob = gpus.filter((g) => g.job).length;
  box.append(el("div.line", [k("gpus"), b(gpus.length), k("in the pool"), b(gpus.filter((g) => g.in_pool).length), k("available"), b(gpus.filter((g) => g.available).length), k("processes"), b(nProc), k("leases"), b(nLease), k("jobs"), b(nJob)]));
  box.append(table([
    { th: "gpu", render: (g) => `GPU ${g.index}`, cls: "nm" }, { th: "name", render: (g) => g.name || "not reported" },
    { th: "pool", render: (g) => (g.in_pool ? "in the sim pool" : "not in the sim pool") }, { th: "available", render: (g) => String(g.available) },
    { th: "activity", render: (g) => { const a = activityOf(g, c); return el("span" + (a === "job" || a === "lease" ? ".ink-cyan" : a === "derated" ? ".ink-fault" : ""), { text: a }); }, cls: "body" },
    { th: "lease", render: (g) => g.lease || "none" },
    { th: "job", render: (g) => (g.job ? `${g.job.experiment || ""} ${g.job.case_id || ""} · ${g.job.stage || "run"} · ${g.job.state || "state not reported"}`.trim() : "none"), cls: "body" },
    { th: "processes", render: (g) => ((g.processes || []).length ? (g.processes || []).map((p) => `${p.processName || "process"} ${p.pid ?? ""} · ${format.mib(p.usedMemMiB)}`).join("; ") : "none"), cls: "body" },
    { th: "memory", render: (g) => `${format.mib(g.memUsedMiB)} / ${format.mib(g.memTotalMiB)}` }, { th: "util", render: (g) => format.pct(g.utilPct), num: true },
    { th: "temp", render: (g) => (g.tempC === null || g.tempC === undefined ? format.DASH : `${g.tempC} °C`), num: true },
    { th: "power", render: (g) => (g.powerW === null || g.powerW === undefined ? format.DASH : `${format.num(g.powerW)} W${g.powerCapW ? " / " + g.powerCapW + " W" : ""}`), num: true },
  ], gpus, { empty: "the console reports no GPU rows", caption: "GPU capacity as the SIMLAB console reports it" }));
}

function renderModes() {
  const { dom } = R.ctx; const { el, clear, absence, note, table } = dom;
  const box = R.b.modes; clear(box);
  const st = R.stamps.modes;
  const abs = absence(st && st.ok === false ? { ok: true, meta: { ok: false, error: st.error, next_try_s: st.next_try_s, stale: !!R.modes } } : R.res.modes, "GRID CONTROL modes");
  if (abs) box.append(abs);
  const m = R.modes;
  const sim = (m && m.sim_modes) || (R.capacity && R.capacity.modes) || null;
  const gc = (m && m.gridcontrol_modes) || {};
  const current = (m && m.current) || (R.capacity && R.capacity.mode) || null;
  if (!sim && !Object.keys(gc).length) { if (!abs) box.append(note("modes not loaded", "neither the sim mode table nor GRID CONTROL's mode table has answered yet")); return; }
  const names = [...new Set([...Object.keys(sim || {}), ...Object.keys(gc)])].sort();
  const list = (a) => (Array.isArray(a) ? (a.length ? a.join(", ") : "none") : "not in the sim mode table");
  box.append(el("div.line", [el("span.k", { text: "current" }), el("b.ink-amber", { text: current || "not reported" }), el("span.k", { text: "sim modes" }), el("b", { text: sim ? String(Object.keys(sim).length) : (m && m.sim_modes_note) || "not reported" }), el("span.k", { text: "GRID CONTROL modes" }), el("b", { text: Object.keys(gc).length ? String(Object.keys(gc).length) : "not reported" })]));
  box.append(table([
    { th: "mode", render: (n) => el("span" + (n === current ? ".ink-amber" : ""), { text: n + (n === current ? " (current)" : "") }), cls: "nm" },
    { th: "label", render: (n) => (sim && sim[n] && sim[n].label) || (gc[n] && gc[n].label) || "not recorded", cls: "body" },
    { th: "sim gpus", render: (n) => (sim && sim[n] ? list(sim[n].sim_gpus) : "not in the sim mode table") },
    { th: "llm gpus", render: (n) => (sim && sim[n] && "llm_gpus" in sim[n] ? list(sim[n].llm_gpus) : "not recorded") },
    { th: "rest", render: (n) => (sim && sim[n] && "rest" in sim[n] ? list(sim[n].rest) : "not recorded") },
    { th: "keep services", render: (n) => (sim && sim[n] && "keep_services" in sim[n] ? list(sim[n].keep_services) : "not recorded"), cls: "body" },
    { th: "stop services", render: (n) => (sim && sim[n] && "stop_services" in sim[n] ? list(sim[n].stop_services) : "not recorded"), cls: "body" },
    { th: "switchable (GRID CONTROL)", render: (n) => (gc[n] ? `${gc[n].switchable}${gc[n].switchable_reported === false ? " (not reported)" : ""}` : "not in GRID CONTROL's table") },
    { th: "pending (GRID CONTROL)", render: (n) => (gc[n] && gc[n].pending ? el("span.ink-amber", { text: String(gc[n].pending) }) : "none"), cls: "body" },
  ], names, { empty: "no modes reported", caption: "mode pools", cls: "wrapnames" }));
  box.append(el("div.prose.dim", { text: "the sim mode table (cudax simlab/modes.json through the console's capacity.modes) says which GPUs simulate, which hold LLM weights, which rest and which services stop; GRID CONTROL's table says what it will switch to. The switch itself is on the Ops plate." }));
}

function renderFoot() {
  const { dom, format } = R.ctx; const { clear } = dom;
  const items = [];
  const agel = (label, name) => { const st = R.stamps[name]; const r = R.res[name]; const iso = (st && st.fetched_utc) || (r && r.meta && r.meta.fetched_utc) || null; const a = iso ? format.age(iso) : null; items.push(`${label} ${a === null ? (r && r.ok === false ? "unavailable" : "age not reported") : format.ago(a)}`); };
  agel("registry", "registry"); agel("capacity", "capacity"); agel("modes", "modes");
  clear(R.b.foot); R.b.foot.append(dom.facts(items));
}

function renderInspector() {
  const { dom, format } = R.ctx; const { block, kv, el } = dom;
  const r = R.solver ? rows().find((x) => x.name === R.solver) : null;
  if (r) {
    const v = r.validation || {};
    const cases = Array.isArray(v.cases) ? v.cases : [];
    const c = citability(r);
    R.ctx.inspector.show([
      block("solver", kv([["module", r.module], ["image", r.image], ["physics", Array.isArray(r.physics) ? r.physics.join(", ") : r.physics], ["precision", Array.isArray(r.precision) ? r.precision.join(", ") : r.precision], ["description", r.description]])),
      block("validation", kv([["status", chip("registry", v.status)], ["date", v.date || "not recorded"], ["image id", v.image_id || "not recorded"], ["citability", c.note, c.citable ? "" : "dim"], ["lane", r.queue === null || r.queue === undefined ? "not recorded" : String(r.queue)], ["note", v.note || "none"]])),
      block(`validation cases · ${cases.length}`, cases.length ? el("ul.prose", cases.map((cs) => el("li", { text: typeof cs === "string" ? cs : `${cs.case || cs.see || "case"}${cs.note ? " — " + cs.note : ""}${cs.record ? " (" + cs.record + ")" : ""}` }))) : el("div.prose.dim", { text: "no validation cases recorded" })),
      r.golden ? block("golden", kv(Object.entries(r.golden).map(([k, val]) => [k, typeof val === "object" ? JSON.stringify(val) : String(val)]))) : null,
    ].filter(Boolean), { name: r.name, coord: `${v.status || "validation not recorded"} · ${v.image_id ? "image id recorded" : "image id not recorded"}` });
    return;
  }
  const rs = rows(); const c = R.capacity;
  R.ctx.inspector.show([
    block("registry", kv([["solvers", String(rs.length)], ["pass", String(rs.filter((x) => (x.validation || {}).status === "pass").length)], ["fail", String(rs.filter((x) => (x.validation || {}).status === "fail").length)], ["image id recorded", String(rs.filter((x) => (x.validation || {}).image_id).length)], ["lane recorded", String(rs.filter((x) => x.queue !== null && x.queue !== undefined).length)]])),
    block("capacity", c ? kv([["mode", c.mode, "act"], ["pool", Array.isArray(c.pool) ? c.pool.join(" ") || "none" : "not reported"], ["available", Array.isArray(c.available) ? c.available.join(" ") || "none" : "not reported"], ["source", c.source]]) : el("div.prose.dim", { text: "capacity not loaded" })),
  ], { name: "solvers", coord: "select a solver row for its validation cases", ringed: false });
}
