// Pipeline plate: the rig's simulation work as the SIMLAB console reports it. The summary strip prints its two scale
// counts under their own names (cases: the console's case scale; queue jobs: rq's job scale) and never adds them; the
// experiments table, the experiment detail with its cases and the case detail with rq status and meta state side by
// side under their own headings (two vocabularies that disagree in the 2026-10-11 capture and are never merged), the
// case files through the console's own file route, and the MLflow run card. Bindings: /api/sim/summary,
// /api/sim/experiments, /api/sim/experiments/{eid}?root=, /api/sim/case/{eid}/{cid}?root=, /api/sim/file?...,
// /api/sim/whoami, /api/mlflow/experiments; stamps sim.summary, sim.experiments (notices), sim.capacity (inline),
// sim.whoami, mlflow.experiments (spec 4.3 Pipeline, 3.6, 3.7). Deep link ?plate=pipeline&experiment=<id>&root=<root>&case=<cid>.
const PATHS = { summary: "/api/sim/summary", experiments: "/api/sim/experiments", whoami: "/api/sim/whoami", mlflow: "/api/mlflow/experiments", capacity: "/api/sim/capacity" };
let R = null;

export async function mount(root, ctx) {
  const { dom } = ctx; const { el, block } = dom;
  R = {
    ctx, root, res: {}, summary: null, experiments: null, whoami: null, mlflow: null, capacity: null,
    eid: ctx.params.get("experiment") || null, eroot: ctx.params.get("root") || "queue", cid: ctx.params.get("case") || null,
    expRes: null, exp: null, caseRes: null, kase: null, loading: {}, dead: false,
    b: { summary: el("div"), queue: el("div"), experiments: el("div"), exp: el("div"), kase: el("div"), mlflow: el("div"), foot: el("div.plate-foot.mono") },
  };
  root.append(
    block("summary · SIMLAB console /api/summary", [R.b.summary], { id: "pipe-summary" }),
    block("queue · two scales, never merged", [R.b.queue], { id: "pipe-queue", coord: "rq job status · job meta state" }),
    block("experiments · SIMLAB console /api/experiments", [R.b.experiments], { span2: true, id: "pipe-experiments", coord: "the console caps its job list at 300" }),
    block("experiment", [R.b.exp], { span2: true, id: "pipe-experiment" }),
    block("case", [R.b.kase], { span2: true, id: "pipe-case" }),
    block("MLflow · experiments/search, as MLflow reports them", [R.b.mlflow], { span2: true, id: "pipe-mlflow" }),
    R.b.foot,
  );
  ctx.events.on("sim.summary", (st) => { if (st.changed) refetch("summary").then(() => { renderSummary(); renderFoot(); }); });
  ctx.events.on("sim.experiments", (st) => { if (st.changed) refetch("experiments").then(() => { renderQueue(); renderExperiments(); renderExp(); renderCase(); renderFoot(); }); });
  ctx.events.on("sim.capacity", (st) => { if (st.ok !== false && st.payload) { R.capacity = st.payload; renderSummary(); } });
  ctx.events.on("sim.whoami", (st) => { if (st.changed) refetch("whoami").then(renderFoot); });
  ctx.events.on("mlflow.experiments", (st) => { if (st.changed) refetch("mlflow").then(renderMlflow); });
  await Promise.all(Object.keys(PATHS).map(refetch));
  if (R.dead) return;
  renderSummary(); renderQueue(); renderExperiments(); renderMlflow(); renderFoot();
  if (R.eid) await loadExperiment(R.eid, R.eroot, R.cid); else { renderExp(); renderCase(); renderInspector(); }
}

export function unmount() { if (R) R.dead = true; R = null; }

// ---------- data ----------
async function refetch(name) {
  const r = await R.ctx.api.get(PATHS[name]);
  if (!R || R.dead) return;
  R.res[name] = r;
  if (r.ok) { if (r.data !== null && r.data !== undefined) R[name] = r.data; else if (!r.meta || r.meta.stale !== true) R[name] = null; }
}

// spec 3.6 names the detail route /api/sim/experiments/{eid}; spec 4.3 names it /api/sim/experiment/{eid}: the first is
// asked, and only a missing route (404 not-found) makes the plate ask the second
async function getFirst(paths) {
  let r = null;
  for (const p of paths) {
    r = await R.ctx.api.get(p);
    if (!(r.ok === false && r.status === 404 && r.code === "not-found")) return r;
  }
  return r;
}

async function loadExperiment(eid, eroot, cid = null) {
  R.eid = eid; R.eroot = eroot || "queue"; R.cid = cid; R.exp = null; R.expRes = null; R.kase = null; R.caseRes = null;
  renderExperiments(); renderExp(); renderCase();
  const q = `?root=${encodeURIComponent(R.eroot)}`;
  const r = await getFirst([`/api/sim/experiments/${encodeURIComponent(eid)}${q}`, `/api/sim/experiment/${encodeURIComponent(eid)}${q}`]);
  if (!R || R.dead || R.eid !== eid) return;
  R.expRes = r; R.exp = r.ok ? r.data : null;
  renderExp(); renderInspector();
  if (cid) await loadCase(eid, cid); else renderCase();
}

async function loadCase(eid, cid) {
  R.cid = cid; R.kase = null; R.caseRes = null;
  renderExp(); renderCase();
  const r = await R.ctx.api.get(`/api/sim/case/${encodeURIComponent(eid)}/${encodeURIComponent(cid)}?root=${encodeURIComponent(R.eroot)}`);
  if (!R || R.dead || R.eid !== eid || R.cid !== cid) return;
  R.caseRes = r; R.kase = r.ok ? r.data : null;
  renderCase(); renderInspector();
}

function select(eid, eroot, cid = null) {
  const u = new URL(location.href); u.searchParams.set("plate", "pipeline"); u.searchParams.set("experiment", eid); u.searchParams.set("root", eroot || "queue");
  if (cid) u.searchParams.set("case", cid); else u.searchParams.delete("case");
  history.replaceState(null, "", u);
  if (cid && R.eid === eid && R.exp) loadCase(eid, cid); else loadExperiment(eid, eroot, cid);
}

// a status word drawn with its vocabulary label when the vocabulary knows it, else as plain text (never an invented word)
function chip(vocabulary, word) {
  const { vocab, dom } = R.ctx;
  if (word === null || word === undefined || word === "") return dom.el("span.dim", { text: "not recorded" });
  return vocab.known(vocabulary, word) ? vocab.state(vocabulary, word) : dom.el("span", { text: String(word), title: `${word}: not a word of the ${vocabulary} vocabulary; shown as received` });
}

function jobsOf(eid, cid = null) {
  const q = R.experiments && Array.isArray(R.experiments.queue) ? R.experiments.queue : [];
  return q.filter((j) => (j.experiment === eid || (j.meta && j.meta.experiment === eid)) && (cid === null || (j.meta && j.meta.case_id === cid)));
}

// ---------- rendering ----------
function renderSummary() {
  const { dom, format } = R.ctx; const { el, clear, absence, note } = dom;
  const box = R.b.summary; clear(box);
  const abs = absence(R.res.summary, "SIMLAB summary");
  if (abs) box.append(abs);
  const s = R.summary;
  if (!s) { if (!abs) box.append(note("summary not loaded", "no reply yet")); R.ctx.coord(""); return; }
  const k = (t) => el("span.k", { text: t }), b = (t, cls = "") => el("b" + (cls ? "." + cls : ""), { text: t === null || t === undefined ? "not reported" : String(t) });
  // the two scales, each under its own name: never one number
  const scales = el("div.cards");
  scales.append(el("div.card", [
    el("div.nm", ["cases", el("span.dim", { text: "console case scale" })]),
    el("div.val", [el("span", { text: "cases" }), b(s.cases)]),
    el("div.val", [el("span", { text: "done" }), b(s.cases_done)]),
    el("div.val", [el("span", { text: "failed" }), b(s.cases_failed, s.cases_failed ? "fault" : "")]),
    el("div.val", [el("span", { text: "experiments" }), b(s.experiments)]),
    el("div.tag", { text: "counted by the console over its experiment directories" }),
  ]));
  const qc = s.queue_counts || {};
  const jobs = Object.values(qc).reduce((a, v) => a + (Number.isFinite(v) ? v : 0), 0);
  scales.append(el("div.card", [
    el("div.nm", ["queue jobs", el("span.dim", { text: "rq job scale" })]),
    el("div.val", [el("span", { text: "jobs" }), b(Object.keys(qc).length ? jobs : null)]),
    ...Object.entries(qc).map(([st, n]) => el("div.val", [el("span", { text: `rq ${st}` }), b(n, st === "failed" ? "fault" : st === "started" ? "live" : "")])),
    el("div.tag", { text: "counted by rq over its job registries; a case may have several jobs, so this is a different count from the cases" }),
  ]));
  box.append(scales);
  const cap = R.capacity;
  box.append(el("div.line", [
    k("mode"), b(s.mode, "ink-amber"), k("sim pool"), b(Array.isArray(s.pool) ? (s.pool.length ? s.pool.join(" ") : "none") : null),
    k("available"), b(Array.isArray(s.available) ? (s.available.length ? s.available.join(" ") : "none") : null, Array.isArray(s.available) && s.available.length ? "ink-cyan" : ""),
    k("gpus"), b(s.gpus), k("solvers"), b(s.solvers), k("ledger entries (rig)"), b(s.ledger),
    k("capacity now"), b(cap && Array.isArray(cap.available) ? `${cap.available.length} available of ${(cap.pool || []).length} in the pool` : "no capacity stamp yet"),
  ]));
  box.append(el("div.line", [k("mlflow"), b(s.mlflow ? `${s.mlflow} (as the console reports it; not used as a link)` : null), k("gridcontrol"), b(s.gridcontrol), k("console host"), b(s.host), k("sampled"), b(s.ts ? format.utc(s.ts) : null)]));
  R.ctx.coord(`${format.or(s.cases, "?")} cases · ${Object.keys(qc).length ? jobs : "?"} queue jobs · ${format.or(s.experiments, "?")} experiments`);
}

function renderQueue() {
  const { dom } = R.ctx; const { el, clear, absence, note, table } = dom;
  const box = R.b.queue; clear(box);
  const abs = absence(R.res.experiments, "SIMLAB experiments and queue");
  if (abs) box.append(abs);
  const d = R.experiments;
  if (!d) { if (!abs) box.append(note("queue not loaded", "no reply yet")); return; }
  if (d.queue_error) box.append(note("queue error, as the console reports it", String(d.queue_error), { act: true }));
  const byRq = d.queue_by_rq_status || {}, byMeta = d.queue_by_meta_state || {};
  const rows = [];
  for (const [w, n] of Object.entries(byRq)) rows.push({ scale: "rq", word: w, n });
  for (const [w, n] of Object.entries(byMeta)) rows.push({ scale: "meta", word: w, n });
  const nRq = Object.values(byRq).reduce((a, v) => a + v, 0), nMeta = Object.values(byMeta).reduce((a, v) => a + v, 0);
  box.append(table([
    { th: "scale", render: (r) => el("b", { text: r.scale === "rq" ? "rq job status" : "job meta state" }) },
    { th: "word", render: (r) => chip(r.scale, r.word) },
    { th: "jobs", key: "n", num: true },
  ], rows, { empty: `no jobs in the console's queue list${Array.isArray(d.queue) ? "" : " (queue not reported)"}`, caption: "queue jobs by rq status and by meta state" }));
  const disagree = Object.keys(byRq).length && Object.keys(byMeta).length && JSON.stringify(byRq) !== JSON.stringify(byMeta);
  box.append(el("div.prose.dim", { text: disagree
    ? `the two scales disagree (rq ${nRq} jobs: ${Object.entries(byRq).map(([w, n]) => `${w} ${n}`).join(", ")}; meta ${nMeta}: ${Object.entries(byMeta).map(([w, n]) => `${w} ${n}`).join(", ")}); both are shown as received and never merged`
    : `${Array.isArray(d.queue) ? d.queue.length : 0} jobs listed by the console` }));
}

function renderExperiments() {
  const { dom, format } = R.ctx; const { el, clear, absence, note, table, button } = dom;
  const box = R.b.experiments; clear(box);
  const d = R.experiments;
  if (!d) { const abs = absence(R.res.experiments, "experiments"); box.append(abs || note("experiments not loaded", "no reply yet")); return; }
  const rows = Array.isArray(d.experiments) ? d.experiments : [];
  const jobs = Array.isArray(d.queue) ? d.queue.length : null;
  box.append(el("div.line", [el("span.k", { text: "experiments" }), el("b", { text: String(rows.length) }), el("span.k", { text: "queue jobs listed" }), el("b", { text: jobs === null ? "not reported" : `${jobs}${jobs >= 300 ? " (the console's cap: older jobs are not listed)" : ""}` }), el("span.k", { text: "listed" }), el("b", { text: d.ts ? format.utc(d.ts) : "not reported" })]));
  box.append(table([
    { th: "experiment", render: (e) => button(e.id || "(no id)", { cls: "btn", title: `open ${e.id}: cases, jobs and files`, onclick: () => select(e.id, e.root || "queue") }), cls: "nm" },
    { th: "project", key: "project" }, { th: "physics", key: "physics" }, { th: "solver", key: "solver" }, { th: "image", key: "image" },
    { th: "cases", render: (e) => `${format.or(e.n_done, "?")} of ${format.or(e.n_cases, "?")} done${e.n_failed ? ` · ${e.n_failed} failed` : ""}${e.n_running ? ` · ${e.n_running} running` : ""}`, cls: "body" },
    { th: "stages", render: (e) => (Array.isArray(e.stages) ? (e.stages.length ? String(e.stages.length) : "none") : "not recorded"), num: true },
    { th: "validation", render: (e) => (e.validation === null || e.validation === undefined ? "not recorded" : String(e.validation)) },
    { th: "root", key: "root" },
    { th: "updated", render: (e) => format.utcShort(e.updated) },
  ], rows.map((e) => ({ ...e, __cls: R.eid === e.id ? "eye" : null })), { empty: "the console lists no experiments", caption: "experiments as the SIMLAB console lists them" }));
}

function stageNames(stages) {
  return (Array.isArray(stages) ? stages : []).map((s) => (typeof s === "string" ? s : (s && (s.name || s.id)) || JSON.stringify(s)));
}

function renderExp() {
  const { dom, format } = R.ctx; const { el, clear, absence, note, table, kv, button } = dom;
  const box = R.b.exp; clear(box);
  if (!R.eid) { box.append(note("no experiment selected", "select an experiment row to read its cases, jobs and files")); return; }
  const abs = absence(R.expRes, `experiment ${R.eid}`);
  if (abs) box.append(abs);
  const e = R.exp;
  if (!e) { if (!abs) box.append(note(`experiment ${R.eid}`, "loading")); return; }
  const k = (t) => el("span.k", { text: t }), b = (t, cls = "") => el("b" + (cls ? "." + cls : ""), { text: t === null || t === undefined || t === "" ? "not recorded" : String(t) });
  box.append(el("div.line", [k("id"), b(e.id), k("project"), b(e.project), k("physics"), b(e.physics), k("solver"), b(e.solver), k("image"), b(e.image), k("root"), b(e.root), k("validation"), b(e.validation), k("updated"), b(e.updated ? format.utc(e.updated) : null)]));
  box.append(el("div.line", [k("cases"), b(`${format.or(e.n_done, "?")} done · ${format.or(e.n_failed, "?")} failed · ${format.or(e.n_running, "?")} running of ${format.or(e.n_cases, "?")}`), k("stale"), b(e.n_stale), k("exp_dir"), b(e.exp_dir), k("spec"), b(e.spec_path)]));
  const quoted = (label, text) => el("div.prose", [el("span.cap", { text: label + " " }), el("span", { text: text ? `“${text}”` : "not recorded" })]);
  box.append(quoted("description (as written in the spec)", e.description));
  box.append(quoted("hypothesis", e.hypothesis));
  if (e.sweep && typeof e.sweep === "object") box.append(el("div", [el("div.cap", { text: "sweep" }), kv(Object.entries(e.sweep).map(([key, v]) => [key, Array.isArray(v) ? v.join(", ") : String(v)]))]));
  if (Array.isArray(e.outputs)) box.append(el("div.line", [k("outputs"), b(e.outputs.length ? e.outputs.join(", ") : "none")]));
  // stages: the spec's stage rows (styles.css .stages/.stage-row) show each stage with the cases that reached it
  const names = stageNames(e.stages);
  const cases = Array.isArray(e.cases) ? e.cases : [];
  if (names.length) {
    const strip = el("div.stages");
    for (const nm of names) {
      const reached = cases.filter((c) => c.current_stage === nm || (Array.isArray(c.stages) && stageNames(c.stages).includes(nm))).length;
      const seg = el("div.seg-item", [el("div.nm", [nm, el("b", { text: `${reached} of ${cases.length}` })]), el("div.bar" + (reached ? "" : ".pending"), [el("i")])]);
      seg.querySelector("i").style.setProperty("--p", cases.length ? String(reached / cases.length) : "0");
      strip.append(el("div.stage-row", [el("span.mono.dim", { text: nm }), el("div.segs", [seg])]));
    }
    box.append(el("div.cap", { text: `stages · ${names.length}` }), strip);
  } else box.append(el("div.prose.dim", { text: "no stages recorded for this experiment (a single-stage solver)" }));
  // reports through the file route
  const reports = Array.isArray(e.reports) ? e.reports : [];
  box.append(el("div.cap", { text: `reports · ${reports.length}` }));
  if (reports.length) box.append(el("ul.prose.mono", reports.map((rp) => { const nm = typeof rp === "string" ? rp : (rp.name || rp.path || JSON.stringify(rp)); return el("li", [el("a", { href: `/api/sim/file?eid=${encodeURIComponent(e.id)}&root=${encodeURIComponent(e.root || R.eroot)}&report=${encodeURIComponent(nm)}`, target: "_blank", rel: "noopener", text: nm })]); })));
  else box.append(el("div.prose.dim", { text: "no reports recorded by the console for this experiment" }));
  // cases
  box.append(el("div.cap", { text: `cases · ${cases.length}` }));
  box.append(table([
    { th: "case", render: (c) => button(c.case_id, { title: `open case ${c.case_id}`, onclick: () => select(e.id, e.root || R.eroot, c.case_id) }), cls: "nm" },
    { th: "status", render: (c) => chip("console", c.status) },
    { th: "stage", render: (c) => c.current_stage || (names.length ? "none" : "single stage") },
    { th: "progress", render: (c) => (c.progress ? `${c.progress.phase || "phase not recorded"} · ${c.progress.step ?? "?"} of ${c.progress.total ?? "?"}${Number.isFinite(c.progress.fraction) ? ` (${Math.round(c.progress.fraction * 100)} %)` : ""}` : "not recorded"), cls: "body" },
    { th: "wall", render: (c) => format.dur(c.wall_s), num: true },
    { th: "updated", render: (c) => (c.progress && c.progress.updated_utc ? format.utcShort(c.progress.updated_utc) : format.DASH) },
  ], cases.map((c) => ({ ...c, __cls: R.cid === c.case_id ? "eye" : null })), { empty: "the console lists no cases for this experiment", caption: `cases of ${e.id}` }));
  // jobs of this experiment: rq and meta side by side
  const jobs = jobsOf(e.id);
  box.append(el("div.cap", { text: `queue jobs of this experiment · ${jobs.length} (rq and meta, side by side)` }));
  box.append(jobsTable(jobs, true));
}

function jobsTable(jobs, withCase) {
  const { dom, format } = R.ctx; const { el, table } = dom;
  const cols = [];
  if (withCase) cols.push({ th: "case", render: (j) => (j.meta && j.meta.case_id) || format.DASH, cls: "nm" });
  cols.push(
    { th: "job", render: (j) => el("code.mono", { text: j.job_id || "(no id)" }), cls: "body" },
    { th: "rq status", render: (j) => chip("rq", j.status) },
    { th: "meta state", render: (j) => chip("meta", j.meta ? j.meta.state : null) },
    { th: "stage", render: (j) => (j.meta && j.meta.stage) || "run" },
    { th: "gpu", render: (j) => (j.meta && j.meta.gpu !== null && j.meta.gpu !== undefined ? String(j.meta.gpu) : "none"), num: true },
    { th: "elapsed", render: (j) => format.dur(j.meta ? j.meta.elapsed_s : null), num: true },
    { th: "rc", render: (j) => (j.meta && j.meta.rc !== null && j.meta.rc !== undefined ? String(j.meta.rc) : format.DASH), num: true },
    { th: "enqueued", render: (j) => format.utcShort(j.enqueued_at) }, { th: "ended", render: (j) => (j.ended_at ? format.utcShort(j.ended_at) : "not ended") },
    { th: "queue", render: (j) => j.queue ?? "not recorded" },
  );
  return table(cols, jobs, { empty: "no job of the console's queue list belongs here (the list holds at most 300 jobs)", caption: "queue jobs with their rq status and meta state" });
}

function renderCase() {
  const { dom, format } = R.ctx; const { el, clear, absence, note, table, kv } = dom;
  const box = R.b.kase; clear(box);
  if (!R.eid || !R.cid) { box.append(note("no case selected", "select a case of the experiment to read its results, files, jobs and MLflow run")); renderMlflowRun(null); return; }
  const abs = absence(R.caseRes, `case ${R.eid}/${R.cid}`);
  if (abs) box.append(abs);
  const c = R.kase;
  if (!c) { if (!abs) box.append(note(`case ${R.cid}`, "loading")); return; }
  const k = (t) => el("span.k", { text: t }), b = (t, cls = "") => el("b" + (cls ? "." + cls : ""), { text: t === null || t === undefined || t === "" ? "not recorded" : String(t) });
  box.append(el("div.line", [k("experiment"), b(R.eid), k("case"), b(c.case_id), k("status"), chip("console", c.status), k("wall"), b(format.dur(c.wall_s)), k("run_dir"), b(c.run_dir), k("progress"), b(c.progress ? `${c.progress.phase || "phase not recorded"} · ${format.ago(c.progress_age_s)}` : null)]));
  if (c.error) box.append(note("error, as the worker recorded it", String(c.error), { fault: true }));
  box.append(el("div.plate-body", { style: { padding: "0", gap: "12px 24px" } }, [
    el("div", [el("div.cap", { text: "params" }), kv(Object.entries(c.params || {}).map(([key, v]) => [key, typeof v === "number" ? format.num(v, 6) : String(v)]))]),
    el("div", [el("div.cap", { text: "results" }), c.results && typeof c.results === "object" && Object.keys(c.results).length ? kv(Object.entries(c.results).filter(([key]) => key !== "artifacts").map(([key, v]) => [key, typeof v === "number" ? format.num(v, 6) : (v === "" ? "(empty)" : String(v))])) : el("div.prose.dim", { text: "no results recorded" })]),
  ]));
  const q = `eid=${encodeURIComponent(R.eid)}&root=${encodeURIComponent(R.eroot)}&cid=${encodeURIComponent(c.case_id)}`;
  const files = Array.isArray(c.files) ? c.files : [];
  box.append(el("div.cap", { text: `files · ${files.length} (served through this console's file route, sandboxed)` }));
  box.append(table([
    { th: "file", render: (f) => el("a", { href: `/api/sim/file?${q}&name=${encodeURIComponent(f.name)}`, target: "_blank", rel: "noopener", text: f.name }), cls: "nm" },
    { th: "bytes", render: (f) => format.bytes(f.bytes), num: true }, { th: "modified", render: (f) => format.utcShort(f.mtime) },
  ], files, { empty: "the console lists no files for this case", caption: `files of case ${c.case_id}` }));
  const arts = Array.isArray(c.artifacts) ? c.artifacts : [];
  box.append(el("div.line", [k("artifacts"), b(arts.length ? arts.join(", ") : "none recorded")]));
  const ev = c.evidence;
  if (ev && typeof ev === "object") {
    const env = ev.environment || {};
    box.append(el("div.cap", { text: `evidence · ${ev.schema || "schema not recorded"}` }));
    box.append(kv([["experiment", ev.experiment ? `${ev.experiment.id} · ${ev.experiment.project || "project not recorded"} · owner ${ev.experiment.owner || "not recorded"} · company ${ev.experiment.company || "not recorded"}` : "not recorded"],
      ["image", env.image ? `${env.image}${env.image_digest ? " · " + String(env.image_digest).split(" ")[0] : ""}` : "not recorded"], ["host", env.host ? `${env.host} · ${env.platform || ""}` : "not recorded"],
      ["gpus", Array.isArray(env.gpus) && env.gpus.length ? env.gpus.map((g) => `${g.index} ${g.name} (${g.memory || "memory not recorded"})`).join("; ") : "none recorded"],
      ["started", ev.started_utc ? format.utc(ev.started_utc) : null], ["finished", ev.finished_utc ? format.utc(ev.finished_utc) : null], ["inputs sha256", ev.inputs_sha256 ? Object.entries(ev.inputs_sha256).map(([f, h]) => `${f} ${format.shortHash(h)}`).join("; ") : "not recorded"]]));
  } else box.append(el("div.prose.dim", { text: "no evidence record attached to this case by the console" }));
  if (c.case_md) box.append(el("div.prose", [el("span.cap", { text: "case notes (as written) " }), el("span", { text: `“${c.case_md}”` })]));
  const jobs = jobsOf(R.eid, c.case_id);
  box.append(el("div.cap", { text: `queue jobs of this case · ${jobs.length} (rq status and meta state, side by side)` }));
  box.append(jobsTable(jobs, false));
  renderMlflowRun(c);
}

function renderMlflowRun(c) {
  const { dom, format } = R.ctx; const { el, note, kv, table } = dom;
  const old = R.b.kase.querySelector("#pipe-mlflow-run"); if (old) old.remove();
  if (!c) return;
  const wrap = el("div#pipe-mlflow-run");
  const m = c.mlflow;
  wrap.append(el("div.cap", { text: `MLflow run · ${c.mlflow_run_id ? c.mlflow_run_id : "no run id recorded on this case"}` }));
  if (!c.mlflow_run_id) wrap.append(el("div.prose.dim", { text: "the case record carries no mlflow_run_id; nothing to read from MLflow" }));
  else if (!m) wrap.append(el("div.prose.dim", { text: "the backend attached no mlflow block to this case (spec 3.6: {run, ui_url, error})" }));
  else if (!m.run) wrap.append(note("MLflow run not available", m.error || "no run and no reason given", { act: true }));
  else {
    const info = m.run.info || {}, data = m.run.data || {};
    wrap.append(kv([["run name", info.run_name], ["status (as MLflow reports it)", info.status], ["experiment id", info.experiment_id], ["started", info.start_time ? format.utc(info.start_time) : null], ["ended", info.end_time ? format.utc(info.end_time) : null], ["lifecycle", info.lifecycle_stage], ["artifact uri", info.artifact_uri]]));
    const metrics = Array.isArray(data.metrics) ? data.metrics : [];
    wrap.append(el("div.cap", { text: `metrics · ${metrics.length}` }));
    wrap.append(table([{ th: "metric", key: "key", cls: "nm" }, { th: "value", render: (x) => format.num(x.value, 6), num: true }, { th: "step", render: (x) => String(x.step ?? format.DASH), num: true }], metrics, { empty: "no metrics", caption: "MLflow metrics" }));
    const params = Array.isArray(data.params) ? data.params : [];
    wrap.append(el("div.cap", { text: `params · ${params.length}` }));
    wrap.append(table([{ th: "param", key: "key", cls: "nm" }, { th: "value", render: (x) => el("code.mono", { text: String(x.value) }), cls: "body" }], params, { empty: "no params", caption: "MLflow params" }));
    const tags = Array.isArray(data.tags) ? data.tags : [];
    wrap.append(el("div.cap", { text: `tags · ${tags.length}` }));
    wrap.append(table([{ th: "tag", key: "key", cls: "nm" }, { th: "value", render: (x) => String(x.value), cls: "body" }], tags, { empty: "no tags", caption: "MLflow tags" }));
    if (m.ui_url) wrap.append(el("div.line", [el("span.k", { text: "MLflow UI" }), el("a", { href: m.ui_url, target: "_blank", rel: "noopener", text: format.noScheme(m.ui_url) }), el("span.dim", { text: "a link to the rig's MLflow page; this console proxies the REST API only" })]));
  }
  R.b.kase.append(wrap);
}

function renderMlflow() {
  const { dom, format } = R.ctx; const { el, clear, absence, note, table } = dom;
  const box = R.b.mlflow; clear(box);
  const abs = absence(R.res.mlflow, "MLflow experiments");
  if (abs) box.append(abs);
  const d = R.mlflow;
  if (!d) { if (!abs) box.append(note("MLflow experiments not loaded", "no reply yet")); return; }
  const rows = Array.isArray(d.experiments) ? d.experiments : [];
  box.append(table([
    { th: "id", key: "experiment_id", num: true }, { th: "experiment", key: "name", cls: "nm" }, { th: "lifecycle", key: "lifecycle_stage" },
    { th: "artifacts", key: "artifact_location", cls: "body" }, { th: "created", render: (e) => format.utcShort(e.creation_time) }, { th: "updated", render: (e) => format.utcShort(e.last_update_time) },
  ], rows, { empty: "MLflow lists no experiments", caption: "MLflow experiments" }));
  box.append(el("div.prose.dim", { text: "MLflow's UI is a link from the Ops plate; its REST API is read here. A run opens from its case (the MLflow run card above)." }));
}

function renderFoot() {
  const { dom, format } = R.ctx; const { clear } = dom;
  const items = [];
  const agel = (label, name) => { const r = R.res[name]; const a = r && r.meta && r.meta.fetched_utc ? format.age(r.meta.fetched_utc) : null; items.push(`${label} ${a === null ? (r && r.ok === false ? "unavailable" : "age not reported") : format.ago(a)}`); };
  agel("summary", "summary"); agel("experiments", "experiments");
  const w = R.whoami;
  if (w) items.push(`simlab-mcp principal ${w.principal || "not reported"} · policy ${w.policy || "not reported"} · ${Array.isArray(w.tools) ? w.tools.length + " tools" : "tools not reported"} · submit, sweep and cancel from this console are deferred (spec 1)`);
  else if (R.res.whoami) items.push(`simlab-mcp whoami: ${(R.res.whoami.meta && R.res.whoami.meta.error) || R.res.whoami.error || "not reported"}`);
  clear(R.b.foot); R.b.foot.append(dom.facts(items));
}

function renderInspector() {
  const { dom, format } = R.ctx; const { block, kv, el } = dom;
  const e = R.exp, c = R.kase;
  if (c) {
    const res = c.results || {};
    R.ctx.inspector.show([
      block("case", kv([["status", chip("console", c.status)], ["wall", format.dur(c.wall_s)], ["run_dir", c.run_dir], ["mlflow run", c.mlflow_run_id || "none recorded"], ["files", String((c.files || []).length)]])),
      block("results", Object.keys(res).length ? kv(Object.entries(res).filter(([key]) => key !== "artifacts").slice(0, 12).map(([key, v]) => [key, typeof v === "number" ? format.num(v, 6) : String(v)])) : el("div.prose.dim", { text: "no results" })),
    ], { name: `${R.eid} / ${c.case_id}`, coord: `${c.status || "status not recorded"} · ${format.dur(c.wall_s)}` });
    return;
  }
  if (e) {
    R.ctx.inspector.show([
      block("experiment", kv([["project", e.project], ["physics", e.physics], ["solver", e.solver], ["image", e.image], ["cases", `${format.or(e.n_done, "?")} done of ${format.or(e.n_cases, "?")}`], ["failed", String(e.n_failed ?? "not recorded")], ["validation", e.validation ?? "not recorded"], ["root", e.root]])),
      block("jobs in the queue list", kv([["jobs", String(jobsOf(e.id).length)], ["scales", "rq status and meta state, shown side by side"]])),
    ], { name: e.id, coord: `${e.project || ""} · ${e.solver || ""}` });
    return;
  }
  const s = R.summary;
  R.ctx.inspector.show(block("SIMLAB console", s ? kv([["cases (console scale)", String(s.cases)], ["queue jobs (rq scale)", String(Object.values(s.queue_counts || {}).reduce((a, v) => a + v, 0))], ["experiments", String(s.experiments)], ["mode", s.mode, "act"], ["solvers", String(s.solvers)]]) : el("div.prose.dim", { text: (R.res.summary && (R.res.summary.error || (R.res.summary.meta && R.res.summary.meta.error))) || "summary not loaded" })), { name: "pipeline", coord: "select an experiment, then a case", ringed: false });
}
