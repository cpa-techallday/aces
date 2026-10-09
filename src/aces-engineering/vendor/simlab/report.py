"""Experiment report: Markdown -> HTML / DOCX / PDF with the evidence manifests appended.

    python -m simlab report out/exp/<experiment> [--formats md,html,docx,pdf] [--title ...] [--doc-id ...]

Inputs are what the runtime already writes: spec.resolved.json, cases.json, summary.json and per
case results.json / evidence.json / case.md / PNG artifacts; plus the Fusion snapshot when the
experiment was exported with `simlab export`, and the solver's validation record from the registry.
DOCX is built with python-docx from the Markdown subset this module emits; PDF comes from
LibreOffice headless (soffice --convert-to pdf), the path the other CPA reports use.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
from typing import Dict, List, Optional

from . import registry

SOFFICE_CANDIDATES = [r"C:\Program Files\LibreOffice\program\soffice.exe", "/usr/bin/soffice", "/usr/bin/libreoffice", "soffice"]


def _load(path: str, default=None):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def _fmt(v) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return f"{v:.5g}"
    if v is None:
        return ""
    return str(v)


def _table(headers: List[str], rows: List[List]) -> List[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        out.append("| " + " | ".join(_fmt(c) for c in r) + " |")
    return out


def collect(exp_dir: str) -> dict:
    exp_dir = os.path.abspath(exp_dir)
    spec = _load(os.path.join(exp_dir, "spec.resolved.json"), {})
    cases = _load(os.path.join(exp_dir, "cases.json"), [])
    summary = _load(os.path.join(exp_dir, "summary.json"), {})
    per_case = []
    for c in cases:
        cid = c["case_id"]
        rd = os.path.join(exp_dir, cid)
        res = _load(os.path.join(rd, "results.json"), {}) or {}
        ev = _load(os.path.join(rd, "evidence.json"), {}) or {}
        st = _load(os.path.join(rd, "status.json"), {}) or {}
        md = ""
        if os.path.exists(os.path.join(rd, "case.md")):
            with open(os.path.join(rd, "case.md"), "r", encoding="utf-8") as f:
                md = f.read()
        figs = [a for a in res.get("artifacts", []) if str(a).lower().endswith(".png") and os.path.exists(a)]
        if not figs:
            figs = [os.path.join(rd, n) for n in sorted(os.listdir(rd)) if n.lower().endswith(".png")] if os.path.isdir(rd) else []
        per_case.append({"case": c, "run_dir": rd, "results": res, "evidence": ev, "status": st, "md": md, "figures": figs})
    snapshot = None
    snap_name = (spec.get("tags") or {}).get("fusion_snapshot")
    spec_path = spec.get("spec_path")
    if snap_name and spec_path:
        snapshot = _load(os.path.join(os.path.dirname(spec_path), snap_name))
    try:
        solver = registry.get(spec.get("solver", ""))
    except Exception:
        solver = {}
    return {"exp_dir": exp_dir, "spec": spec, "cases": cases, "summary": summary, "per_case": per_case,
            "snapshot": snapshot, "solver": solver}


def render_markdown(data: dict, title: Optional[str] = None, doc_id: Optional[str] = None, fig_dir: Optional[str] = None) -> str:
    spec, per_case, solver = data["spec"], data["per_case"], data["solver"]
    exp_id = spec.get("id", os.path.basename(data["exp_dir"]))
    title = title or f"Simulation report: {exp_id}"
    doc_id = doc_id or f"CPA-SIM-RPT-{exp_id.upper().replace('_', '-')}"
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    outputs = spec.get("outputs") or []
    n_ok = sum(1 for p in per_case if p["status"].get("status") == "FINISHED")
    md: List[str] = [f"# {title}", "",
                     f"Doc: {doc_id} · project **{spec.get('project')}** · owner {spec.get('owner')} · generated {now} · runtime CPA-SIM-RUNTIME-001", "",
                     "## 1. Summary", "",
                     f"Experiment `{exp_id}` ran {len(per_case)} case(s) with solver `{spec.get('solver')}` "
                     f"({spec.get('physics')}); {n_ok} finished, {len(per_case) - n_ok} failed.", ""]
    if spec.get("description"):
        md += [spec["description"], ""]
    # headline numbers
    if outputs and per_case:
        rows = []
        for k in outputs:
            vals = [p["results"].get(k) for p in per_case if isinstance(p["results"].get(k), (int, float))]
            if vals:
                rows.append([k, min(vals), max(vals)])
        if rows:
            md += _table(["output", "min", "max"], rows) + [""]
    # 2. definition
    md += ["## 2. Experiment definition", ""]
    md += _table(["item", "value"], [
        ["solver", f"`{spec.get('solver')}` ({solver.get('description', '')})"],
        ["image", solver.get("image", "")],
        ["precision", (spec.get("resources") or {}).get("precision", "")],
        ["stages", ", ".join((solver.get("stages") or {}).keys()) or "single"],
        ["sweep", ", ".join(f"{k} ∈ {v}" for k, v in (spec.get("sweep") or {}).items()) or "none"],
    ]) + [""]
    params = spec.get("params") or {}
    if params:
        md += ["Base parameters:", ""] + _table(["parameter", "value"], [[k, json.dumps(v) if isinstance(v, (dict, list)) else v] for k, v in params.items()]) + [""]
    # 3. geometry
    geom = spec.get("geometry") or {}
    md += ["## 3. Geometry provenance", ""]
    md += _table(["item", "value"], [["source", geom.get("source")], ["reference", geom.get("ref")], ["sha256", geom.get("sha256")]]) + [""]
    snap = data.get("snapshot")
    if snap:
        md += [f"Exported from Fusion document **{snap.get('document')}** (version {snap.get('version')}, {snap.get('design_type')}, "
               f"units {snap.get('units')}, exported {str(snap.get('exported_utc', ''))[:19]}Z); STEP sha256 `{(snap.get('export') or {}).get('step_sha256', '')[:16]}…`.", ""]
        ups = snap.get("user_parameters") or []
        if ups:
            md += ["User parameters at export:", ""] + _table(["name", "expression", "value", "unit", "comment"],
                                                              [[u["name"], u["expression"], _fmt(u.get("value_cm", 0) * 10.0) if u.get("unit") == "mm" else _fmt(u.get("value_cm")), u.get("unit"), u.get("comment", "")] for u in ups]) + [""]
        bodies = snap.get("bodies") or []
        if bodies:
            md += ["Bodies:", ""] + _table(["component", "body", "volume mm³", "material", "bbox mm"],
                                           [[b.get("component"), b.get("name"), _fmt(b.get("volume_mm3")), b.get("material") or "", " ".join(_fmt(x) for x in (b.get("bbox_mm") or []))] for b in bodies]) + [""]
    # 4. results table
    md += ["## 4. Results", ""]
    sweep_keys = list((spec.get("sweep") or {}).keys())
    cols = ["case"] + sweep_keys + [k for k in outputs if k not in sweep_keys] + ["status"]
    rows = []
    for p in per_case:
        r = [p["case"]["case_id"]] + [p["case"].get(k) for k in sweep_keys] + [p["results"].get(k) for k in outputs if k not in sweep_keys] + [p["status"].get("status", "")]
        rows.append(r)
    md += _table(cols, rows) + [""]
    # 5. per case
    md += ["## 5. Cases", ""]
    for p in per_case:
        cid = p["case"]["case_id"]
        if p["md"]:
            body = p["md"]
            # make image links point at the copied figures
            body = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", lambda m_: f"![{m_.group(1)}]({_fig_rel(fig_dir, p['run_dir'], m_.group(2), cid)})", body)
            md += [body, ""]
        else:
            md += [f"### case {cid}", ""] + _table(["parameter", "value"], [[k, v] for k, v in p["case"].items() if k != "case_id"]) + [""]
            if p["results"]:
                md += _table(["output", "value"], [[k, v] for k, v in p["results"].items() if k != "artifacts" and isinstance(v, (int, float, str))]) + [""]
            for fpath in p["figures"]:
                md += [f"![{os.path.basename(fpath)}]({_fig_rel(fig_dir, p['run_dir'], fpath, cid)})", ""]
        if p["status"].get("status") != "FINISHED":
            md += ["```", str(p["status"].get("error", ""))[-1500:], "```", ""]
    # 6. validation record
    val = solver.get("validation") or {}
    md += ["## 6. Solver validation record", "",
           f"`{spec.get('solver')}` validation status **{val.get('status', 'none')}** ({val.get('date', '')}). {val.get('note', '')}", ""]
    for c in val.get("cases", []):
        md += [f"- {json.dumps(c, ensure_ascii=False)}"]
    md += [""]
    # Appendix A evidence
    md += ["## Appendix A. Evidence manifests", "",
           "One manifest per case (`evidence.json` in the run directory): inputs hashed, environment, timings. "
           "These are the contemporaneous records for the R&D evidence engine.", ""]
    for p in per_case:
        ev = p["evidence"]
        if not ev:
            continue
        env = ev.get("environment") or {}
        gpus = env.get("gpus") or []
        gpu = gpus[0] if gpus else {}
        rows = [["case", p["case"]["case_id"]], ["status", ev.get("status")],
                ["started / finished (UTC)", f"{str(ev.get('started_utc', ''))[:19]} / {str(ev.get('finished_utc', ''))[:19]}"],
                ["wall s", ev.get("wall_s")],
                ["host / container", f"{env.get('host')} / {'yes' if env.get('in_container') else 'no'} {env.get('image') or ''}"],
                ["GPU", f"{gpu.get('name', 'none')} driver {gpu.get('driver', '')} {gpu.get('memory', '')}"],
                ["packages", ", ".join(f"{k} {v}" for k, v in (env.get("packages") or {}).items())],
                ["git rev", ev.get("git_rev") or "not a git checkout"],
                ["MLflow run", p["status"].get("mlflow_run_id") or "not logged"]]
        for name, h in (ev.get("inputs_sha256") or {}).items():
            rows.append([f"sha256 {name}", h])
        for s in ev.get("stages") or []:
            rows.append([f"stage {s.get('stage')}", f"{s.get('status')} {_fmt(s.get('wall_s'))} s"])
        md += _table(["field", "value"], rows) + [""]
    md += ["## Appendix B. Reproduction", "", "```",
           f"python -m simlab run {spec.get('spec_path')} --out <out>   # or: python -m simlab enqueue ... / sim_submit via simlab-mcp",
           f"python -m simlab report {data['exp_dir']}", "```", ""]
    return "\n".join(md)


def _fig_rel(fig_dir: Optional[str], run_dir: str, path: str, cid: str) -> str:
    src = path if os.path.isabs(path) else os.path.join(run_dir, path)
    if fig_dir and os.path.exists(src):
        os.makedirs(fig_dir, exist_ok=True)
        dst = os.path.join(fig_dir, f"{cid}_{os.path.basename(src)}")
        if os.path.abspath(src) != os.path.abspath(dst):
            shutil.copyfile(src, dst)
        return "figures/" + os.path.basename(dst)
    return src.replace("\\", "/")


# ---------------- DOCX from the Markdown subset we emit ----------------

def markdown_to_docx(md: str, path: str, base_dir: str) -> str:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt

    doc = Document()
    styles = doc.styles
    styles["Normal"].font.name = "Calibri"
    styles["Normal"].font.size = Pt(10)
    lines = md.splitlines()
    i = 0
    in_code = False
    code_buf: List[str] = []
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            if in_code:
                p = doc.add_paragraph()
                run = p.add_run("\n".join(code_buf))
                run.font.name = "Consolas"
                run.font.size = Pt(8)
                code_buf = []
                in_code = False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|\s*-+", lines[i + 1]):
            headers = [c.strip() for c in line.strip().strip("|").split("|")]
            rows = []
            i += 2
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            t = doc.add_table(rows=1 + len(rows), cols=len(headers))
            t.style = "Light Grid Accent 1"
            for j, h in enumerate(headers):
                cell = t.rows[0].cells[j]
                cell.text = ""
                r = cell.paragraphs[0].add_run(_strip_md(h))
                r.bold = True
                r.font.size = Pt(8)
            for ri, row in enumerate(rows, start=1):
                for j in range(len(headers)):
                    cell = t.rows[ri].cells[j]
                    cell.text = ""
                    r = cell.paragraphs[0].add_run(_strip_md(row[j]) if j < len(row) else "")
                    r.font.size = Pt(8)
            doc.add_paragraph()
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            level = len(m.group(1))
            doc.add_heading(_strip_md(m.group(2)), level=0 if level == 1 else min(level - 1, 3))
            i += 1
            continue
        m = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)", line.strip())
        if m:
            img = m.group(2)
            img = img if os.path.isabs(img) else os.path.join(base_dir, img)
            if os.path.exists(img):
                doc.add_picture(img, width=Inches(6.3))
                cap = doc.add_paragraph(m.group(1))
                cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                cap.runs[0].font.size = Pt(8)
                cap.runs[0].italic = True
            i += 1
            continue
        if line.startswith("- "):
            doc.add_paragraph(_strip_md(line[2:]), style="List Bullet")
            i += 1
            continue
        if line.strip():
            _add_inline(doc.add_paragraph(), line)
        i += 1
    doc.save(path)
    return path


def _strip_md(s: str) -> str:
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"\*\*([^*]*)\*\*", r"\1", s)
    return s


def _add_inline(p, text: str) -> None:
    from docx.shared import Pt

    pos = 0
    for m in re.finditer(r"(\*\*[^*]+\*\*|`[^`]+`)", text):
        if m.start() > pos:
            p.add_run(text[pos:m.start()])
        tok = m.group(0)
        if tok.startswith("**"):
            p.add_run(tok[2:-2]).bold = True
        else:
            r = p.add_run(tok[1:-1])
            r.font.name = "Consolas"
            r.font.size = Pt(9)
        pos = m.end()
    if pos < len(text):
        p.add_run(text[pos:])


def to_html(md: str, path: str, title: str) -> str:
    try:
        import markdown as mdlib

        body = mdlib.markdown(md, extensions=["tables", "fenced_code"])
    except Exception:
        body = "<pre>" + md.replace("&", "&amp;").replace("<", "&lt;") + "</pre>"
    css = ("body{font-family:Segoe UI,Arial,sans-serif;max-width:1000px;margin:32px auto;padding:0 16px;color:#222}"
           "table{border-collapse:collapse;font-size:13px;margin:8px 0}td,th{border:1px solid #bbb;padding:3px 8px}"
           "th{background:#eef}img{max-width:100%}code{background:#f4f4f4;padding:1px 4px}pre{background:#f4f4f4;padding:8px;overflow:auto}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title><style>{css}</style></head><body>{body}</body></html>")
    return path


def to_pdf(docx_path: str, out_dir: str) -> Optional[str]:
    exe = next((c for c in SOFFICE_CANDIDATES if os.path.exists(c) or shutil.which(c)), None)
    if not exe:
        return None
    try:
        subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", out_dir, docx_path],
                       capture_output=True, text=True, timeout=300, check=False)
    except Exception:
        return None
    pdf = os.path.join(out_dir, os.path.splitext(os.path.basename(docx_path))[0] + ".pdf")
    return pdf if os.path.exists(pdf) else None


def build_report(exp_dir: str, formats=("md", "html", "docx", "pdf"), title: Optional[str] = None,
                 doc_id: Optional[str] = None) -> Dict[str, Optional[str]]:
    data = collect(exp_dir)
    rep_dir = os.path.join(data["exp_dir"], "report")
    fig_dir = os.path.join(rep_dir, "figures")
    os.makedirs(rep_dir, exist_ok=True)
    md = render_markdown(data, title, doc_id, fig_dir)
    out: Dict[str, Optional[str]] = {}
    md_path = os.path.join(rep_dir, "report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    out["md"] = md_path
    ttl = title or f"Simulation report: {data['spec'].get('id', '')}"
    if "html" in formats:
        out["html"] = to_html(md, os.path.join(rep_dir, "report.html"), ttl)
    docx_path = None
    if "docx" in formats or "pdf" in formats:
        docx_path = markdown_to_docx(md, os.path.join(rep_dir, "report.docx"), rep_dir)
        out["docx"] = docx_path if "docx" in formats else None
    if "pdf" in formats and docx_path:
        out["pdf"] = to_pdf(docx_path, rep_dir)
    return out
