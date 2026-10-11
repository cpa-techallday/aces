// Formatting for the aces console: ages, bytes, durations, UTC stamps, numbers. Pure functions, no DOM.
// Absence is a dash ("—"); nothing here invents a value for a missing one.

export const DASH = "—";

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// seconds since an ISO string or an epoch (s or ms); null when it cannot be read
export function age(ts) {
  const t = epochS(ts);
  return t === null ? null : Date.now() / 1000 - t;
}

// ISO string, epoch seconds or epoch milliseconds -> epoch seconds (null when unreadable)
export function epochS(ts) {
  if (ts === null || ts === undefined || ts === "") return null;
  if (typeof ts === "number") return Number.isFinite(ts) ? (ts > 1e11 ? ts / 1000 : ts) : null;
  const t = Date.parse(String(ts));
  return Number.isFinite(t) ? t / 1000 : null;
}

// "now" | "12 s ago" | "3 min ago" | "1.2 h ago" | "2.4 d ago" | "—"
export function ago(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return DASH;
  const s = Math.max(0, seconds);
  if (s < 1.5) return "now";
  if (s < 60) return Math.round(s) + " s ago";
  if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 86400) return (s / 3600).toFixed(1) + " h ago";
  return (s / 86400).toFixed(1) + " d ago";
}

// a span of seconds as "4 s" | "3 min" | "1.2 h" | "2.4 d"
export function span(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return DASH;
  const s = Math.max(0, seconds);
  if (s < 60) return Math.round(s) + " s";
  if (s < 3600) return Math.round(s / 60) + " min";
  if (s < 86400) return (s / 3600).toFixed(1) + " h";
  return (s / 86400).toFixed(1) + " d";
}

// a duration in seconds as the console writes it: 4.2 s · 3 m 05 s · 1.4 h
export function dur(s) {
  if (s === null || s === undefined || !Number.isFinite(s)) return DASH;
  if (s < 60) return s.toFixed(s < 10 ? 1 : 0) + " s";
  if (s < 3600) return Math.floor(s / 60) + " m " + String(Math.round(s % 60)).padStart(2, "0") + " s";
  return (s / 3600).toFixed(1) + " h";
}

// an ISO string or epoch as "2026-10-11 09:55:13Z" (always UTC, never the browser's zone); unreadable input is returned as text
export function utc(ts) {
  const t = epochS(ts);
  if (t === null) return ts === null || ts === undefined || ts === "" ? DASH : String(ts);
  const d = new Date(t * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}Z`;
}

// a shorter UTC stamp for table cells: "10-11 09:55Z"
export function utcShort(ts) {
  const t = epochS(ts);
  if (t === null) return ts ? String(ts) : DASH;
  const d = new Date(t * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}Z`;
}

export function bytes(b) {
  if (b === null || b === undefined || !Number.isFinite(b)) return DASH;
  if (b >= 1073741824) return (b / 1073741824).toFixed(2) + " GB";
  if (b >= 1048576) return (b / 1048576).toFixed(1) + " MB";
  if (b >= 1024) return Math.round(b / 1024) + " kB";
  return b + " B";
}

export function mib(m) {
  if (m === null || m === undefined || !Number.isFinite(m)) return DASH;
  return m >= 1024 ? (m / 1024).toFixed(m >= 10240 ? 0 : 1) + " GB" : Math.round(m) + " MiB";
}

// numbers to d significant digits; integers as they are; tiny or huge values in exponent form
export function num(v, d = 3) {
  if (v === null || v === undefined || Number.isNaN(v)) return DASH;
  if (typeof v !== "number") return String(v);
  const a = Math.abs(v);
  if (a !== 0 && (a < 1e-3 || a >= 1e6)) return v.toExponential(2);
  if (Number.isInteger(v)) return String(v);
  return v.toPrecision(d).replace(/\.?0+$/, (m) => (m.includes(".") ? "" : m));
}

export function pct(v, d = 0) {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH;
  return v.toFixed(d) + " %";
}

export function shortHash(h, n = 12) {
  return h ? String(h).slice(0, n) : DASH;
}

// a URL without its scheme, as the foot strips print sources
export function noScheme(u) {
  return u ? String(u).replace(/^https?:\/\//, "") : "";
}

// a value or the dash
export function or(v, fallback = DASH) {
  return v === null || v === undefined || v === "" ? fallback : v;
}

export const format = { DASH, esc, age, epochS, ago, span, dur, utc, utcShort, bytes, mib, num, pct, shortHash, noScheme, or };
export default format;
