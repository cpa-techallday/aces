// Projects plate: the desktop's projects ($ACES_WORK/<name>/aces.yaml) with their counts, and one project's
// detail: its aces.yaml fields as data, its golden runs, its CAD runs and its ledger entries. Bindings:
// /api/projects, /api/projects/{name} (spec 3.8, 4.3 "Work"); stamp `projects` (notice). Deep link ?project=<name>.
let R = null;

export async function mount(root, ctx) {
  const { dom } = ctx; const { el, block } = dom;
  R = { ctx, root, res: null, list: null, selected: ctx.params.get("project") || null, detailRes: null, detail: null, dead: false, b: { list: el("div"), detail: el("div") } };
  root.append(
    block("projects · $ACES_WORK/<name>/aces.yaml", [R.b.list], { span2: true, id: "proj-list" }),
    block("project", [R.b.detail], { span2: true, id: "proj-detail" }),
  );
  ctx.events.on("projects", (st) => { if (st.changed) load(); });
  await load();
}

export function unmount() { if (R) R.dead = true; R = null; }

async function load() {
  const r = await R.ctx.api.get("/api/projects");
  if (!R || R.dead) return;
  R.res = r; R.list = r.ok ? r.data : null;
  renderList();
  if (R.selected) await loadDetail(R.selected); else { renderDetail(); renderInspector(); }
}

async function loadDetail(name) {
  R.selected = name;
  renderList();
  const r = await R.ctx.api.get(`/api/projects/${encodeURIComponent(name)}`);
  if (!R || R.dead || R.selected !== name) return;
  R.detailRes = r; R.detail = r.ok ? r.data : null;
  renderDetail(); renderInspector();
}

function select(name) {
  if (R.selected === name) return;
  const u = new URL(location.href); u.searchParams.set("plate", "projects"); u.searchParams.set("project", name); history.replaceState(null, "", u);
  loadDetail(name);
}

function renderList() {
  const { dom, format } = R.ctx; const { el, clear, absence, note } = dom;
  const box = R.b.list; clear(box);
  const abs = absence(R.res, "projects");
  if (abs) box.append(abs);
  const d = R.list;
  if (!d) { if (!abs) box.append(note("projects not loaded", "no reply yet")); R.ctx.coord(""); return; }
  const rows = Array.isArray(d.projects) ? d.projects : [];
  if (d.reason) box.append(note("no work store on this machine", d.reason, { act: true, detail: d.root || null }));
  else if (!rows.length) box.append(note("no projects", `no aces.yaml under ${d.root || "$ACES_WORK"}`));
  R.ctx.coord(`${rows.length} project${rows.length === 1 ? "" : "s"}${d.reason ? " · " + d.reason : ""}`);
  if (!rows.length) return;
  const cards = el("div.cards");
  for (const p of rows) {
    const c = p.counts || {};
    cards.append(el("button.card" + (R.selected === p.name ? ".eye" : ""), { type: "button", "aria-pressed": R.selected === p.name ? "true" : "false", title: `${p.name}: open its runs and ledger entries`, onclick: () => select(p.name) }, [
      el("div.nm.name", { text: p.name }),
      el("div.val", [el("span", { text: "class" }), el("b.act", { text: `${p.class || "not recorded"}` })]),
      el("div.val", [el("span", { text: "created" }), el("b", { text: format.utcShort(p.created) })]),
      el("div.val", [el("span", { text: "golden runs" }), el("b", { text: String(c.golden_runs ?? "not counted") })]),
      el("div.val", [el("span", { text: "CAD runs" }), el("b", { text: String(c.cad_runs ?? "not counted") })]),
      el("div.val", [el("span", { text: "ledger entries" }), el("b", { text: String(c.ledger_entries ?? "not counted") })]),
      el("div.tag", { text: `onshape ${p.onshape_gate || "gate not evaluated"}` }),
    ]));
  }
  box.append(cards);
}

function renderDetail() {
  const { dom, format, vocab } = R.ctx; const { el, clear, absence, note, table, kv, button } = dom;
  const box = R.b.detail; clear(box);
  if (!R.selected) { box.append(note("no project selected", "select a project card to read its aces.yaml, golden runs, CAD runs and ledger entries")); return; }
  const abs = absence(R.detailRes, `project ${R.selected}`);
  if (abs) box.append(abs);
  const p = R.detail;
  if (!p) { if (!abs) box.append(note(`project ${R.selected}`, "loading")); return; }
  const k = (t) => el("span.k", { text: t }), b = (t, cls = "") => el("b" + (cls ? "." + cls : ""), { text: t === null || t === undefined || t === "" ? "not recorded" : String(t) });
  box.append(el("div.line", [k("name"), b(p.name), k("class"), b(p.class, "ink-amber"), k("class source"), b(p.class_source), k("created"), b(format.utc(p.created)), k("reclassified"), b(p.reclassified || "never"), k("onshape"), b(p.onshape_gate), k("path"), b(p.path)]));
  const quoted = (label, text) => el("div.prose", [el("span.cap", { text: label + " " }), el("span", { text: text ? `“${text}”` : "not recorded" })]);
  box.append(quoted("hypothesis (as written in aces.yaml)", p.hypothesis));
  box.append(el("div.line", [k("company"), b(p.company), k("R&D activity"), b(p.rd_activity)]));
  const listOf = (label, items, emptyText) => el("div", [el("div.cap", { text: `${label} · ${(items || []).length}` }), (items || []).length ? el("ul.prose.mono", items.map((x) => el("li", { text: String(x) }))) : el("div.prose.dim", { text: emptyText })]);
  box.append(el("div.plate-body", { style: { padding: "0", gap: "12px 24px" } }, [
    listOf("components", p.components, "no components recorded against this project"),
    listOf("evidence references", p.evidence, "no evidence references recorded"),
    listOf("CAD sources", p.cad_sources, "no CAD sources recorded"),
  ]));
  // golden runs
  const golden = Array.isArray(p.golden_runs) ? p.golden_runs : [];
  box.append(el("div.cap", { text: `golden runs · ${golden.length}` }));
  box.append(table([
    { th: "run", key: "run_id", cls: "nm" },
    { th: "created", render: (r) => format.utcShort(r.created_utc) }, { th: "finished", render: (r) => (r.finished_utc ? format.utcShort(r.finished_utc) : "not finished") },
    { th: "status", render: (r) => vocab.state("run", r.status) },
    { th: "outcome", key: "outcome" }, { th: "verdict", key: "verdict" }, { th: "grade", key: "grade" },
    { th: "exit", render: (r) => (r.exit_code === null || r.exit_code === undefined ? format.DASH : String(r.exit_code)), num: true },
    { th: "wall", render: (r) => format.dur(r.wall_s), num: true },
    { th: "result.json", render: (r) => (r.has_result ? "present" : "absent") },
    { th: "open", render: (r) => button("Evidence", { onclick: () => R.ctx.navigate("evidence", { project: p.name, run: r.run_id }), title: "open this run on the Evidence plate" }) },
  ], golden, { empty: `no golden runs under ${p.path ? p.path.replace(/aces\.yaml$/, "golden/") : "the project"}`, caption: `golden runs of ${p.name}` }));
  box.append(el("div.prose.dim", { text: "no grade is a sign-off; desktop grades are not rig results" }));
  // cad runs
  const cad = Array.isArray(p.cad_runs) ? p.cad_runs : [];
  box.append(el("div.cap", { text: `CAD runs · ${cad.length}` }));
  box.append(table([
    { th: "run", key: "run_id", cls: "nm" }, { th: "name", key: "name" },
    { th: "status", render: (r) => (r.unreadable ? vocab.state("cad", "unreadable", { title: r.unreadable }) : vocab.state("cad", r.status)) },
    { th: "started", render: (r) => format.utcShort(r.started) }, { th: "finished", render: (r) => (r.finished ? format.utcShort(r.finished) : "not finished") },
    { th: "sandbox", render: (r) => (r.sandbox ? `${r.sandbox.mode || "mode not recorded"} · confined ${String(r.sandbox.confined)} · ${r.sandbox.platform || "platform not recorded"}` : "not recorded") },
    { th: "unreadable", render: (r) => r.unreadable || format.DASH },
    { th: "open", render: (r) => button("Objects", { onclick: () => R.ctx.navigate("objects", { project: p.name, run: r.run_id }), title: "open this run on the Objects plate" }) },
  ], cad, { empty: `no CAD store in ${p.name}`, caption: `CAD runs of ${p.name}` }));
  // ledger
  const ledger = Array.isArray(p.ledger) ? p.ledger : [];
  box.append(el("div.cap", { text: `ledger entries naming this project · ${ledger.length}` }));
  box.append(table([
    { th: "n", key: "n", num: true }, { th: "recorded", render: (e) => format.utc(e.ts) }, { th: "kind", key: "kind", cls: "nm" },
    { th: "payload keys", render: (e) => (e.payload && typeof e.payload === "object" ? Object.keys(e.payload).join(", ") : format.DASH), cls: "body" },
    { th: "hash", render: (e) => format.shortHash(e.hash) }, { th: "prev", render: (e) => (e.prev && /^0+$/.test(e.prev) ? "genesis" : format.shortHash(e.prev)) },
  ], ledger, { empty: "no ledger entry carries this project's name", caption: `ledger entries of ${p.name}` }));
}

function renderInspector() {
  const { dom, format } = R.ctx; const { block, kv, el } = dom;
  const p = R.detail;
  if (!p) { R.ctx.inspector.show(block("projects", el("div.prose.dim", { text: R.list && R.list.reason ? R.list.reason : "select a project card" })), { name: "projects", coord: R.list ? `${(R.list.projects || []).length} on this desktop` : "", ringed: false }); return; }
  const c = p.counts || {};
  R.ctx.inspector.show([
    block("project", kv([["class", p.class, "act"], ["class source", p.class_source], ["created", format.utc(p.created)], ["reclassified", p.reclassified || "never"], ["onshape gate", p.onshape_gate], ["path", p.path]])),
    block("counts", kv([["golden runs", String(c.golden_runs ?? "not counted")], ["CAD runs", String(c.cad_runs ?? "not counted")], ["ledger entries", String(c.ledger_entries ?? "not counted")], ["components", String((p.components || []).length)], ["evidence refs", String((p.evidence || []).length)], ["CAD sources", String((p.cad_sources || []).length)]])),
  ], { name: p.name, coord: `${p.class || "class not recorded"} · ${p.path || ""}` });
}
