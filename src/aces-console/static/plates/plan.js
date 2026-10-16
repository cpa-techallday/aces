// Plan plate: the brief is the mission setup (design 2026-10-15 §3 — "the plan IS the interface"). Describe the
// part or the mission, name attachment refs of files already staged, and the planner (aces_engineering.playbooks.plan,
// or the `aces golden plan` CLI behind POST /api/plan) answers the plan: class chip and confidence, the ordered stage
// cards with their solvers, gates and blocked gaps, and the spec sheet whose every edit re-validates against the
// playbook targets live. When the planner is not importable the plate says so in words and keeps the composer usable;
// it never fabricates a plan. Launch is disabled while this console has no job-submit path (spec 1 defers submit) and
// the button says why. During a run the stage cards follow the golden.runs stamps for a run that names this plan_id.
// Bindings: GET /api/plan/status, POST /api/plan, GET /api/plan/{id}, POST /api/plan/{id}/launch, /api/golden/runs;
// stamp golden.runs (notice). Deep link ?plate=plan&plan=<id>.
const PATHS = {
  status: "/api/plan/status",
  create: "/api/plan",
  plan: (id) => `/api/plan/${encodeURIComponent(id)}`,
  launch: (id) => `/api/plan/${encodeURIComponent(id)}/launch`,
  goldenRuns: "/api/golden/runs",
};
let R = null;

export async function mount(root, ctx) {
  const { el, block } = ctx.dom;
  R = {
    ctx, root, dead: false, busy: false,
    status: null, statusRes: null, plan: null, planRes: null, runs: null, runsRes: null,
    attachments: [], briefText: "",
    b: {
      composer: el("div"), plan: el("div"), stages: el("div"), spec: el("div"), run: el("div"),
      foot: el("div.plate-foot.mono"),
    },
  };
  root.append(
    block("brief · describe the part or the mission", [R.b.composer], { id: "plan-composer", coord: "the brief is the mission setup" }),
    block("plan", [R.b.plan], { id: "plan-head", coord: "class · confidence · warnings" }),
    block("stages · engineer-ordered, gates are data", [R.b.stages], { span2: true, id: "plan-stages" }),
    block("spec sheet · edits re-validate against the playbook targets", [R.b.spec], { id: "plan-spec" }),
    block("run", [R.b.run], { id: "plan-run", coord: "launch and stage progress" }),
    R.b.foot,
  );
  ctx.events.on("golden.runs", (st) => { if (st.changed) refetchRuns(); });
  renderComposer();
  renderPlan(); renderStages(); renderSpec(); renderRun(); renderFoot();
  const want = ctx.params.get("plan");
  await refetchStatus();
  if (R.dead) return;
  renderComposer(); renderRun(); renderFoot();
  if (want) await loadPlan(want);
}

export function unmount() { if (R) R.dead = true; R = null; }

// ---------- data ----------
async function refetchStatus() {
  const r = await R.ctx.api.get(PATHS.status);
  if (!R || R.dead) return;
  R.statusRes = r;
  R.status = r.ok ? r.data : null;
}

async function loadPlan(id) {
  const r = await R.ctx.api.get(PATHS.plan(id));
  if (!R || R.dead) return;
  R.planRes = r;
  R.plan = r.ok ? r.data : null;
  if (R.plan) history.replaceState(null, "", `?plate=plan&plan=${encodeURIComponent(R.plan.plan_id)}`);
  renderPlan(); renderStages(); renderSpec(); renderRun(); renderFoot();
}

async function refetchRuns() {
  const r = await R.ctx.api.get(PATHS.goldenRuns);
  if (!R || R.dead) return;
  R.runsRes = r;
  R.runs = r.ok ? r.data : null;
  renderRun(); renderStages();
}

async function submitBrief() {
  if (R.busy) return;
  const ta = R.b.composer.querySelector("textarea.brief");
  const brief = ta ? ta.value.trim() : "";
  if (!brief) { renderComposerError("the brief is empty: describe the part or the mission first"); return; }
  R.busy = true; renderComposer();
  const r = await R.ctx.api.post(PATHS.create, { brief, attachments: R.attachments });
  if (!R || R.dead) return;
  R.busy = false;
  R.planRes = r;
  R.plan = r.ok ? r.data : null;
  if (R.plan) {
    const u = new URL(location.href); u.searchParams.set("plate", "plan"); u.searchParams.set("plan", R.plan.plan_id);
    history.replaceState(null, "", u);
    refetchRuns();
  }
  renderComposer(); renderPlan(); renderStages(); renderSpec(); renderRun(); renderFoot();
}

// ---------- the composer ----------
function plannerLine() {
  const { dom } = R.ctx; const { el } = dom;
  const st = R.status;
  if (!st) {
    const abs = dom.absence(R.statusRes, "planner status");
    return abs || el("span.dim", { text: "planner status not reported yet" });
  }
  const p = st.planner || {};
  if (p.available) return el("span", [dom.ink("cyan", `planner: ${p.mode}`), el("span.dim", { text: ` · ${p.detail || ""}` })]);
  return el("span", [dom.ink("amber", "planner unavailable"), el("span.dim", { text: ` · ${p.detail || "no detail"}` })]);
}

function renderComposer() {
  const { dom } = R.ctx; const { el, clear, button } = dom;
  const box = R.b.composer; clear(box);
  const ta = el("textarea.brief", {
    rows: "5", placeholder: "a 1500 kg six-wheeled off-roader… / optimise this bracket for stiffness at half the mass…",
    "aria-label": "brief: describe the part or the mission", disabled: R.busy || null,
  });
  ta.value = R.briefText || (R.plan && R.plan.brief) || "";
  ta.addEventListener("input", () => { R.briefText = ta.value; });
  box.append(ta);
  // attachments are refs to files already staged (this console has no binary upload route; nothing is fabricated)
  const chips = el("div.chips");
  R.attachments.forEach((a, i) => {
    chips.append(el("span.chip", [el("span.mono", { text: a }), el("button.x", {
      type: "button", title: `drop ${a}`, "aria-label": `drop attachment ${a}`,
      onclick: () => { R.attachments.splice(i, 1); renderComposer(); },
    }, ["×"])]));
  });
  const input = el("input.type.short", { type: "text", placeholder: "attachment ref (run id or work-store path)",
    "aria-label": "attachment reference", style: { flex: "1 1 220px", width: "auto" } });
  const add = () => {
    const v = input.value.trim();
    if (v && !R.attachments.includes(v) && R.attachments.length < 32) R.attachments.push(v);
    input.value = ""; renderComposer();
    const again = R.b.composer.querySelector("input.type"); if (again) again.focus();
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } });
  box.append(el("div.actrow", [input, button("add ref", { onclick: add, title: "attachments are references to files already staged or uploaded elsewhere; this console itself has no binary upload route" })]));
  if (R.attachments.length) box.append(chips);
  box.append(el("div.actrow", [
    button(R.busy ? "planning…" : "Generate plan", { cls: "btn primary", disabled: R.busy, id: "plan-generate",
      title: "POST /api/plan: the planner classifies the brief, picks the playbook and resolves the stage parameters",
      onclick: submitBrief }),
    plannerLine(),
  ]));
  if (R.planRes && !R.planRes.ok) box.append(dom.absence(R.planRes, "planner") || dom.note("planner", "no reply", { fault: true }));
}

function renderComposerError(text) {
  const { dom } = R.ctx;
  R.b.composer.append(dom.note("brief not sent", text, { act: true }));
}

// ---------- the plan ----------
function classChip(p) {
  const { dom } = R.ctx;
  return dom.ink(p.class ? "cyan" : "chalk", p.class || "class not reported", `part class as the planner classified it${p.class_evidence && p.class_evidence.length ? `: ${p.class_evidence.join("; ")}` : ""}`);
}

function confidenceBadge(p) {
  const { dom, format } = R.ctx; const { el } = dom;
  if (p.confidence === null || p.confidence === undefined) return el("span.dim", { text: "confidence not reported" });
  // planner reports 0..1; format.pct expects 0..100
  const pctVal = p.confidence <= 1 ? p.confidence * 100 : p.confidence;
  const low = p.low_confidence || p.confidence < 0.5;
  return el("span.badge" + (low ? ".ink-amber" : ".ink-cyan"), {
    text: `${format.pct(pctVal)} confidence${low ? " · confirm the class before launch" : ""}`,
    title: "classifier confidence; low confidence asks for a human confirmation of the class (design §6)",
  });
}

function renderPlan() {
  const { dom, format } = R.ctx; const { el, clear, note } = dom;
  const box = R.b.plan; clear(box);
  const p = R.plan;
  if (!p) {
    if (R.planRes && !R.planRes.ok) { box.append(dom.absence(R.planRes, "plan") || note("plan", "no reply")); return; }
    box.append(note("no plan yet", "write the brief above and Generate plan; the planner's answer renders here"));
    return;
  }
  const k = (t) => el("span.k", { text: t }), b = (t, cls = "") => el("b" + (cls ? "." + cls : ""), { text: t === null || t === undefined || t === "" ? "not reported" : String(t) });
  box.append(el("div.line", [k("class"), classChip(p), k("confidence"), confidenceBadge(p), k("plan"), el("code.mono", { text: p.plan_id }), k("planner"), b(p.planner), k("created"), b(p.created_utc ? format.utc(p.created_utc) : null)]));
  if (Array.isArray(p.warnings) && p.warnings.length) {
    box.append(el("div.cap", { text: `warnings · ${p.warnings.length}` }));
    for (const w of p.warnings) box.append(note("planner warning", String(w), { act: true }));
  }
  const nBlocked = (Array.isArray(p.stages) ? p.stages : []).filter((s) => s.blocked).length;
  if (nBlocked) box.append(note(`${nBlocked} stage${nBlocked === 1 ? "" : "s"} blocked`, "a blocked stage names the missing solver; the plan is never silently skipped past it (design §6)", { fault: true }));
  R.ctx.coord(`${p.class || "class not reported"} · ${(p.stages || []).length} stages${nBlocked ? ` · ${nBlocked} blocked` : ""}`);
}

// ---------- the stage cards ----------
function gateText(g) {
  if (g === null || g === undefined || g === "") return "no gate recorded";
  if (typeof g === "string") return g;
  try { return JSON.stringify(g); } catch (e) { return String(g); }
}

function renderStages() {
  const { dom } = R.ctx; const { el, clear, note } = dom;
  const box = R.b.stages; clear(box);
  const p = R.plan;
  if (!p) { box.append(note("no stages", "stages render with the plan")); return; }
  const stages = Array.isArray(p.stages) ? p.stages : [];
  if (!stages.length) { box.append(note("the planner returned no stages", "the plan carries an empty stage list; nothing to order or gate", { act: true })); return; }
  const prog = stageProgress();
  const grid = el("div.stagecards");
  stages.forEach((s, i) => {
    const pr = prog[s.id] || prog[s.name] || null;
    const card = el("div.stagecard" + (s.blocked ? ".blocked" : ""), { role: "button", tabindex: "0", title: `${s.id}: show in the inspector` }, [
      el("div.nm", [el("span", [el("span.dim", { text: `${i + 1} ` }), String(s.name)]), s.blocked ? dom.ink("fault", "blocked") : pr ? dom.ink("cyan", pr) : dom.ink("chalk", "planned")]),
      el("div.val", [el("span", { text: "solver" }), el("b", { text: s.solver || "not resolved" })]),
      el("div.val", [el("span", { text: "purpose" }), el("b", { text: s.purpose || "not recorded" })]),
      el("div.val", [el("span", { text: "acceptance gate" }), el("b", { text: gateText(s.gate) })]),
      s.blocked ? el("div.tag", { text: `blocked: ${s.gap || "the gap is named in the plan's blocked list"}` }) : null,
      Array.isArray(s.evidence) && s.evidence.length ? el("div.tag", { text: `evidence: ${s.evidence.join(", ")}` }) : null,
    ]);
    const show = () => R.ctx.inspector.show(stageInspector(s, i), { name: `${i + 1} · ${s.name}`, coord: s.solver || "solver not resolved" });
    card.addEventListener("click", show);
    card.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); show(); } });
    grid.append(card);
  });
  box.append(grid);
}

function stageInspector(s, i) {
  const { dom } = R.ctx; const { block, kv, el } = dom;
  const params = s.params && typeof s.params === "object" ? Object.entries(s.params) : [];
  return [
    block("stage", kv([["order", String(i + 1)], ["id", s.id], ["solver", s.solver || "not resolved"], ["state", s.blocked ? "blocked" : "planned", s.blocked ? "fault" : null], ["gate", gateText(s.gate)], ["gap", s.gap || null]])),
    block("params", params.length ? kv(params.map(([key, v]) => [key, typeof v === "object" ? JSON.stringify(v) : String(v)])) : el("div.prose.dim", { text: "no parameters resolved from the brief for this stage" })),
  ];
}

// progress from the golden runs that name this plan_id (none do until the runner is merged; absence is drawn, not invented)
function stageProgress() {
  const out = {};
  const p = R.plan, runs = R.runs;
  if (!p || !runs) return out;
  const rows = Array.isArray(runs.runs) ? runs.runs : (Array.isArray(runs) ? runs : []);
  for (const r of rows) {
    if (!r || r.plan_id !== p.plan_id) continue;
    const stages = Array.isArray(r.stages) ? r.stages : [];
    for (const st of stages) {
      if (st && (st.id || st.name)) out[st.id || st.name] = st.status || st.state || "recorded";
    }
  }
  return out;
}

// ---------- the spec sheet: every edit re-validates against the targets ----------
function targetFor(p, m) {
  if (m && m.target !== undefined && m.target !== null) return m.target;
  const t = p.spec && p.spec.targets ? p.spec.targets : {};
  return m && m.name in t ? t[m.name] : undefined;
}

function targetText(t) {
  if (t === undefined || t === null) return "no target";
  if (typeof t === "number") return `= ${t}`;
  if (Array.isArray(t)) return `${t[0] ?? "…"} … ${t[1] ?? "…"}`;
  if (typeof t === "object") {
    const lo = t.min !== undefined ? t.min : "…", hi = t.max !== undefined ? t.max : "…";
    if (lo !== "…" || hi !== "…") return `${lo} … ${hi}`;
    try { return JSON.stringify(t); } catch (e) { return String(t); }
  }
  return String(t);
}

function targetVerdict(value, t) {
  // true inside, false outside, null no target or not a number (reported, not judged)
  if (t === undefined || t === null) return null;
  const v = Number(value);
  if (!Number.isFinite(v)) return null;
  if (typeof t === "number") return v === t;
  let lo = null, hi = null;
  if (Array.isArray(t)) { lo = Number(t[0]); hi = Number(t[1]); }
  else if (typeof t === "object") { lo = t.min !== undefined ? Number(t.min) : null; hi = t.max !== undefined ? Number(t.max) : null; }
  else return null;
  if (lo !== null && Number.isFinite(lo) && v < lo) return false;
  if (hi !== null && Number.isFinite(hi) && v > hi) return false;
  return true;
}

function renderSpec() {
  const { dom, format } = R.ctx; const { el, clear, note, table } = dom;
  const box = R.b.spec; clear(box);
  const p = R.plan;
  if (!p) { box.append(note("no spec sheet", "the metrics that matter for the class render with the plan")); return; }
  const metrics = p.spec && Array.isArray(p.spec.metrics) ? p.spec.metrics : [];
  const targets = p.spec && p.spec.targets && typeof p.spec.targets === "object" ? p.spec.targets : {};
  if (!metrics.length && !Object.keys(targets).length) { box.append(note("the plan carries no spec sheet", "the planner returned no metrics and no targets for this class", { act: true })); return; }
  const rows = metrics.length ? metrics : Object.keys(targets).map((name) => ({ name, value: null }));
  const state = {};   // name -> verdict (true/false/null), recomputed on every edit
  const derived = el("div.line");
  const redo = () => {
    const judged = Object.values(state).filter((v) => v !== null);
    const inside = judged.filter((v) => v === true).length;
    clear(derived);
    derived.append(
      el("span.k", { text: "targets" }),
      el("b", { text: judged.length ? `${inside} of ${judged.length} inside target` : "no numeric targets to judge" }),
      el("span.k", { text: "edited values are validated here only" }),
      el("b.dim", { text: "the playbook re-validates server-side before any launch" }),
    );
  };
  box.append(table([
    { th: "metric", key: "name", cls: "nm" },
    {
      th: "value", render: (m) => {
        const input = el("input.type.short", { type: "text", value: m.value === null || m.value === undefined ? "" : String(m.value), "aria-label": `${m.name} value` });
        const cell = el("span.spec-val", [input]);
        const check = () => {
          const verdict = targetVerdict(input.value, targetFor(p, m));
          state[m.name] = verdict;
          input.classList.toggle("out", verdict === false);
          input.title = verdict === false ? `outside the playbook target ${targetText(targetFor(p, m))}` : (verdict === true ? "inside the target" : "no numeric target for this metric; shown as edited");
          redo();
        };
        input.addEventListener("input", check);
        queueMicrotask(check);
        return cell;
      },
    },
    { th: "unit", render: (m) => m.unit || format.DASH },
    { th: "playbook target", render: (m) => el("code.mono", { text: targetText(targetFor(p, m)) }), cls: "body" },
    { th: "note", render: (m) => m.note || format.DASH, cls: "body" },
  ], rows, { empty: "no metrics", caption: "spec sheet: metrics of the plan with their playbook targets" }));
  box.append(derived);
}

// ---------- run: the launch action (disabled with its reason) and stage progress ----------
function renderRun() {
  const { dom } = R.ctx; const { el, clear, note, button } = dom;
  const box = R.b.run; clear(box);
  const p = R.plan;
  const launch = R.status && R.status.launch ? R.status.launch : { available: false, reason: "launch capability not reported by this backend" };
  const nBlocked = p ? (Array.isArray(p.stages) ? p.stages : []).filter((s) => s.blocked).length : 0;
  const why = !p ? "generate a plan first"
    : nBlocked ? `${nBlocked} stage${nBlocked === 1 ? " is" : "s are"} blocked: the missing solver${nBlocked === 1 ? "" : "s"} must land first`
    : launch.reason;
  box.append(el("div.actrow", [
    button("Launch", { cls: "btn primary", disabled: true, id: "plan-launch", title: why }),
    el("span.why", { text: why }),
  ]));
  if (!p) return;
  const rows = R.runs ? (Array.isArray(R.runs.runs) ? R.runs.runs : (Array.isArray(R.runs) ? R.runs : [])) : [];
  const linked = rows.filter((r) => r && r.plan_id === p.plan_id);
  if (linked.length) {
    box.append(el("div.line", [el("span.k", { text: "golden runs of this plan" }), el("b", { text: linked.map((r) => `${r.run_id || "?"} (${r.status || "status not recorded"})`).join(" · ") })]));
  } else {
    box.append(el("div.prose.dim", { text: "no golden run names this plan_id yet; when a run does, the stage cards follow its progress through the golden.runs stamps" }));
  }
}

function renderFoot() {
  const { dom, format } = R.ctx; const { clear } = dom;
  const items = [];
  const st = R.status;
  if (st && st.planner) items.push(`planner ${st.planner.available ? `available (${st.planner.mode})` : "unavailable"}`);
  if (st && st.plans_stored !== undefined) items.push(`${st.plans_stored} plan${st.plans_stored === 1 ? "" : "s"} stored (${st.storage || "in memory"})`);
  if (R.plan) items.push(`plan ${R.plan.plan_id} · ${(R.plan.stages || []).length} stages · created ${R.plan.created_utc ? format.ago(format.age(R.plan.created_utc)) : "time not recorded"}`);
  if (!items.length) items.push("plan status not loaded");
  clear(R.b.foot); R.b.foot.append(dom.facts(items));
}
