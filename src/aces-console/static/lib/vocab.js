// Status vocabularies and the Four Inks (spec 4.4). The table is served at /vocab.json by the backend
// (aces_console/vocab.json); this module never hard-codes a word. Until the table is loaded, or when a word is
// not in its vocabulary, the ink is chalk (exists, neutral): nothing is ever painted green, and an unknown word
// is never a fault. Every status word is rendered with the label of the vocabulary it belongs to, so the
// console's FINISHED, rq's finished and golden's done are never read as one scale.

const INKS = ["chalk", "cyan", "amber", "fault"];
// words that are terminal successes draw the check glyph (spec 4.4: "a finished/pass state is chalk with a check glyph")
const CHECK_WORDS = new Set(["FINISHED", "finished", "done", "ok", "pass", "reachable"]);

let TABLE = null;        // the parsed /vocab.json
let LOAD_ERROR = null;   // one line when the table could not be loaded

function patternOf(word) {
  // "<mode>" style placeholders in a vocabulary word match any run of characters
  if (!/<[a-z_]+>/i.test(word)) return null;
  const re = word.split(/<[a-z_]+>/i).map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("(.+)");
  return new RegExp("^" + re + "$");
}

function entry(vocabulary) {
  if (!TABLE || !TABLE.vocabularies) return null;
  const v = TABLE.vocabularies;
  if (v[vocabulary]) return v[vocabulary];
  for (const e of Object.values(v)) if (e && e.label === vocabulary) return e;
  return null;
}

export async function load(url = "/vocab.json") {
  try {
    const r = await fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const j = await r.json();
    if (!j || typeof j.vocabularies !== "object") throw new Error("no vocabularies table in the reply");
    TABLE = j; LOAD_ERROR = null;
  } catch (e) {
    TABLE = null; LOAD_ERROR = `vocab.json unavailable: ${e.message}`;
  }
  return { table: TABLE, error: LOAD_ERROR };
}

export function table() { return TABLE; }
export function loadError() { return LOAD_ERROR; }

// ink(vocabulary, word) -> "chalk" | "cyan" | "amber" | "fault"
export function ink(vocabulary, word) {
  const e = entry(vocabulary);
  if (!e || word === null || word === undefined) return "chalk";
  const w = String(word);
  for (const name of INKS) {
    const words = e[name] || [];
    for (const cand of words) {
      if (cand === w) return name;
      const re = patternOf(cand);
      if (re && re.test(w)) return name;
    }
  }
  return "chalk";
}

// label(vocabulary) -> "rq" | "meta" | "case" | "run" | "cad" | "console" | "registry" | "endpoint"
export function label(vocabulary) {
  const e = entry(vocabulary);
  return e && e.label ? e.label : String(vocabulary);
}

// known(vocabulary, word) -> whether the word belongs to the vocabulary (exact or by placeholder)
export function known(vocabulary, word) {
  const e = entry(vocabulary);
  if (!e || word === null || word === undefined) return false;
  const w = String(word);
  for (const name of INKS) for (const cand of e[name] || []) {
    if (cand === w) return true;
    const re = patternOf(cand);
    if (re && re.test(w)) return true;
  }
  return false;
}

export function vocabularies() { return TABLE ? Object.keys(TABLE.vocabularies) : []; }

// the status chip: <span class="state ink-<ink>" data-vocab data-word><glyph/>word<small class="voc">label</small></span>
export function state(vocabulary, word, { title = null } = {}) {
  const w = word === null || word === undefined ? "not recorded" : String(word);
  const k = ink(vocabulary, w);
  const e = entry(vocabulary);
  const span = document.createElement("span");
  span.className = "state ink-" + k;
  if (/\(by design\)$/.test(w)) span.classList.add("by-design");
  if (/^not run\b/.test(w)) span.classList.add("notrun");
  span.dataset.vocab = e ? (Object.keys(TABLE.vocabularies).find((key) => TABLE.vocabularies[key] === e) || vocabulary) : vocabulary;
  span.dataset.word = w;
  span.dataset.ink = k;
  span.title = title || `${e && e.name ? e.name : vocabulary}: ${w}${e && e.source ? " (" + e.source + ")" : ""}${known(vocabulary, w) ? "" : " — not a word of this vocabulary"}`;
  if (CHECK_WORDS.has(w) && k === "chalk") {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "ic check"); svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use"); use.setAttribute("href", "#i-check");
    svg.append(use); span.append(svg);
  } else {
    const glyph = document.createElement("i");
    glyph.className = "ring-ic " + (k === "cyan" ? "live" : k === "amber" ? "act" : k === "fault" ? "fault" : "pending");
    span.append(glyph);
  }
  span.append(document.createTextNode(w));
  const voc = document.createElement("small");
  voc.className = "voc"; voc.textContent = label(vocabulary);
  span.append(voc);
  return span;
}

export const vocab = { load, table, loadError, ink, label, known, vocabularies, state, INKS };
export default vocab;
