// DOM helpers for the aces console plates. Everything is built as nodes with textContent, never by
// interpolating upstream text into HTML, so rig data can never script against the console.
import { format } from "./format.js";

// el("div.card.eye#id", {title: "..."}, ["text", node, ...]) -> HTMLElement
export function el(spec, attrs = null, children = null) {
  const m = /^([a-z0-9-]+)?((?:[.#][\w-]+)*)$/i.exec(spec || "div");
  const tag = (m && m[1]) || "div";
  const node = document.createElement(tag);
  if (m && m[2]) for (const part of m[2].match(/[.#][\w-]+/g) || []) {
    if (part[0] === ".") node.classList.add(part.slice(1)); else node.id = part.slice(1);
  }
  if (attrs && typeof attrs === "object" && !(attrs instanceof Node) && !Array.isArray(attrs)) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className += (node.className ? " " : "") + v;
      else if (k === "text") node.textContent = String(v);
      else if (k === "html") node.innerHTML = v;   // only for markup this module itself produces
      else if (k === "dataset") Object.assign(node.dataset, v);
      else if (k === "style" && typeof v === "object") Object.assign(node.style, v);
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : String(v));
    }
  } else if (attrs !== null && attrs !== undefined) children = attrs;
  append(node, children);
  return node;
}

export function append(node, children) {
  if (children === null || children === undefined || children === false) return node;
  for (const c of Array.isArray(children) ? children : [children]) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }

export function icon(name, cls = "ic") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", cls); svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", "#i-" + name);
  svg.append(use);
  return svg;
}

// a titled block of a plate body: <section class="blk"><h3 class="cap">cap <span class="coord">coord</span></h3>…</section>
export function block(cap, children, { coord = null, span2 = false, id = null } = {}) {
  const head = el("h3.cap", [cap, coord ? el("span.coord", { text: coord }) : null]);
  return el("section.blk" + (span2 ? ".span2" : ""), { id }, [head, ...(Array.isArray(children) ? children : [children])]);
}

// key/value rows for the inspector and cards: rows = [[key, value, cls?], ...]; a null value row is dropped
export function kv(rows) {
  const wrap = el("div");
  for (const r of rows) {
    if (!r || r[1] === null || r[1] === undefined || r[1] === "") continue;
    const b = el("b", { class: r[2] || null });
    append(b, r[1]);
    wrap.append(el("div.kv", [el("span", { text: String(r[0]) }), b]));
  }
  return wrap;
}

// an honest note: <div class="note-inline"><span class="cap">cap</span><p>text</p><span class="mono">detail</span></div>
export function note(cap, text, { fault = false, act = false, detail = null } = {}) {
  return el("div.note-inline" + (fault ? ".fault" : act ? ".act" : ""), [
    el("span.cap", { text: cap }),
    text ? el("p", { text: String(text) }) : null,
    detail ? el("span.mono", { text: String(detail) }) : null,
  ]);
}

// the absence of an API result with its reason: r is the object api.get()/api.post() returned
export function absence(r, what) {
  if (!r) return note(`${what} not loaded`, "no reply yet");
  if (r.ok === false) {
    const http = r.status ? ` (HTTP ${r.status}${r.code ? ", " + r.code : ""})` : "";
    return note(`${what} unavailable`, `${r.error || "no reason given"}${http}`, { fault: r.status !== 503 && r.status !== 404 && r.status !== 0 ? true : false, act: r.status === 503 || r.status === 0 || r.status === 404, detail: r.source || null });
  }
  const m = r.meta || {};
  if (m.ok === false || m.error) {
    const retry = m.next_try_s !== null && m.next_try_s !== undefined ? `; retrying in ${format.num(m.next_try_s)} s` : "";
    const stale = m.stale ? " (showing the last good reply)" : "";
    // "not configured" and "not available" are absences with a reason (amber: something for the operator to do);
    // an unreachable or failing upstream is the faulted thing (red-orange on this card only)
    const soft = m.stale || /^(not configured|not available)/i.test(String(m.error || ""));
    return note(`${what}: ${m.error || "upstream reported an error"}`, `${stale}${retry}`.replace(/^; /, ""), { fault: !soft, act: soft, detail: m.source || null });
  }
  return null;
}

// a table with data-th on every cell so the 390 px sheet can stack rows as key/value pairs.
// cols: [{th, key?, cls?, render?(row) -> node|string, num?}], rows: array; empty: text when rows is empty
export function table(cols, rows, { empty = "no rows", caption = null, cls = "" } = {}) {
  const t = el("table.table" + (cls ? "." + cls : ""));
  if (caption) t.append(el("caption.visually-hidden", { text: caption }));
  const thead = el("thead"); const trh = el("tr");
  for (const c of cols) trh.append(el("th", { text: c.th, class: c.num ? "num" : null, scope: "col" }));
  thead.append(trh); t.append(thead);
  const tb = el("tbody");
  if (!rows || !rows.length) {
    tb.append(el("tr.empty", [el("td", { colspan: String(cols.length), text: empty })]));
  } else {
    for (const row of rows) {
      const tr = el("tr", { class: row && row.__cls ? row.__cls : null });
      if (row && row.__data) Object.assign(tr.dataset, row.__data);
      for (const c of cols) {
        const td = el("td", { class: [c.cls || "", c.num ? "num" : ""].join(" ").trim() || null, dataset: { th: c.th } });
        let v = c.render ? c.render(row) : (c.key ? row[c.key] : null);
        if (v === null || v === undefined || v === "") v = format.DASH;
        append(td, v);
        tr.append(td);
      }
      tb.append(tr);
    }
  }
  t.append(tb);
  return el("div.table-wrap", [t]);
}

// plate footer facts, each whole (the .facts pattern of styles.css)
export function facts(items) {
  const inner = el("div.items");
  for (const t of items) if (t) inner.append(el("span", [el("i.dot", { text: " · " }), t]));
  return el("div.facts", [inner]);
}

export function button(text, { cls = "btn", onclick = null, title = null, disabled = false, id = null } = {}) {
  return el("button", { class: cls, type: "button", title, disabled: disabled || null, id, onclick }, [text]);
}

// a text run with an ink class, for words that are data but not one of the status vocabularies
export function ink(name, text, title = null) {
  return el("span.ink-" + name, { text: String(text), title });
}

export const dom = { el, append, clear, icon, block, kv, note, absence, table, facts, button, ink };
export default dom;
