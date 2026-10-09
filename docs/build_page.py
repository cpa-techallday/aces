"""Render VISION.md (and optionally the integrations catalog) to a single designed HTML page for publishing.

Usage: python build_page.py            -> writes docs/vision.html from ../VISION.md
The page follows the artifact page contract: no document skeleton (title + style first), colour tokens on :root with
dark-mode blocks, phone-width safe, tables scroll inside their own container.
"""
import io
import os
import re

import markdown

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "VISION.md")
OUT = os.path.join(HERE, "vision.html")

text = io.open(SRC, encoding="utf-8").read()

# the markdown title line becomes the page masthead; keep the rest
m = re.match(r"^# (.+?)\n", text)
title = m.group(1).strip() if m else "ACES OS Vision"
body_md = text[m.end():] if m else text

# fenced ascii diagrams are code blocks already; tables need the tables extension; toc for the sidebar
md = markdown.Markdown(extensions=["tables", "toc", "fenced_code", "sane_lists"], extension_configs={"toc": {"toc_depth": "2-2", "permalink": False}})
html_body = md.convert(body_md)
toc = md.toc

# wrap every table for horizontal scrolling at phone width
html_body = html_body.replace("<table>", '<div class="tablewrap"><table>').replace("</table>", "</table></div>")

page = f"""<title>ACES OS Vision</title>
<meta name="description" content="Vision and product plan for ACES OS: the ACES computational-engineering platform packaged as an operating system on Omarchy 4 Quattro with an ACES Server.">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:ital,wght@0,400;0,500;0,600;1,400&family=IBM+Plex+Mono:wght@400;500&family=Fraunces:opsz,wght@9..144,500;9..144,600&display=swap">
<style>
/* Layout: a navy drafting-sheet masthead, a sticky section index on wide screens, measured running text at ~70ch;
   Parameter Sky register: navy ground, cyan for live/links, amber reserved for decisions, hairline rules. */
:root {{
  --bg: #f4f6f9; --paper: #ffffff; --fg: #10192b; --fg-2: #3c4a63; --fg-3: #6b778c;
  --line: #d9dfe8; --accent: #0b7fa3; --accent-ink: #075f7a; --amber: #b7791f; --amber-bg: #fff5df;
  --navy: #0f1b33; --navy-ink: #dbe6f5; --code-bg: #eef2f7;
  --display: "Fraunces", Georgia, "Times New Roman", serif;
  --body: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, "Cascadia Mono", Consolas, monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg: #0c1424; --paper: #121d33; --fg: #e6edf7; --fg-2: #b7c3d6; --fg-3: #8794aa;
  --line: #27344c; --accent: #4cc3e6; --accent-ink: #8fdcf2; --amber: #e2b55a; --amber-bg: #2b2412;
  --navy: #0a1120; --navy-ink: #dbe6f5; --code-bg: #182440; color-scheme: dark;
}} }}
:root[data-theme="dark"] {{
  --bg: #0c1424; --paper: #121d33; --fg: #e6edf7; --fg-2: #b7c3d6; --fg-3: #8794aa;
  --line: #27344c; --accent: #4cc3e6; --accent-ink: #8fdcf2; --amber: #e2b55a; --amber-bg: #2b2412;
  --navy: #0a1120; --navy-ink: #dbe6f5; --code-bg: #182440; color-scheme: dark;
}}
body {{ background: var(--bg); color: var(--fg); font-family: var(--body); font-size: 16px; line-height: 1.55; margin: 0; }}
.mast {{ background: var(--navy); color: var(--navy-ink); padding-block: 40px 36px; padding-inline: 20px; }}
.mast .inner {{ max-width: 1120px; margin: 0 auto; }}
.mast .eyebrow {{ font-family: var(--mono); font-size: 12px; letter-spacing: .14em; text-transform: uppercase; color: #9fb3d1; }}
.mast h1 {{ font-family: var(--display); font-weight: 500; font-size: clamp(30px, 4.2vw, 46px); line-height: 1.1; margin: 10px 0 14px; text-wrap: balance; letter-spacing: -.01em; }}
.mast p {{ max-width: 70ch; margin: 0; color: #c7d3e6; font-size: 15px; }}
.mast .facts {{ display: flex; flex-wrap: wrap; gap: 10px 28px; margin-top: 22px; font-family: var(--mono); font-size: 12.5px; color: #9fb3d1; }}
.mast .facts b {{ color: #fff; font-weight: 500; }}
.wrap {{ max-width: 1120px; margin: 0 auto; padding-inline: 20px; padding-block: 28px 64px; display: grid; grid-template-columns: 240px minmax(0, 1fr); gap: 40px; }}
nav.toc {{ position: sticky; top: env(safe-area-inset-top, 0px); align-self: start; max-height: 100vh; overflow: auto; padding-top: 8px; font-size: 13.5px; }}
nav.toc .label {{ font-family: var(--mono); font-size: 11px; letter-spacing: .14em; text-transform: uppercase; color: var(--fg-3); margin-bottom: 8px; }}
nav.toc ul {{ list-style: none; margin: 0; padding: 0; border-left: 1px solid var(--line); }}
nav.toc li {{ margin: 0; }}
nav.toc a {{ display: block; padding: 5px 0 5px 12px; color: var(--fg-2); text-decoration: none; border-left: 2px solid transparent; margin-left: -1px; }}
nav.toc a:hover, nav.toc a:focus {{ color: var(--accent-ink); border-left-color: var(--accent); }}
main {{ min-width: 0; }}
main > p, main > ul, main > ol, main > blockquote {{ max-width: 72ch; }}
main h2 {{ font-family: var(--display); font-weight: 600; font-size: 27px; line-height: 1.15; margin: 44px 0 12px; padding-top: 14px; border-top: 1px solid var(--line); text-wrap: balance; }}
main h2:first-child {{ margin-top: 8px; border-top: 0; padding-top: 0; }}
main h3 {{ font-family: var(--body); font-weight: 600; font-size: 18px; margin: 28px 0 8px; }}
main p {{ margin: 0 0 12px; }}
main ul, main ol {{ padding-left: 1.3em; margin: 0 0 12px; }}
main li {{ margin: 4px 0; }}
main li p {{ margin: 0; }}
main a {{ color: var(--accent-ink); text-decoration: underline; text-decoration-thickness: 1px; text-underline-offset: 2px; }}
main strong {{ font-weight: 600; }}
main code {{ font-family: var(--mono); font-size: .88em; background: var(--code-bg); padding: 1px 5px; border-radius: 3px; }}
main pre {{ background: var(--navy); color: var(--navy-ink); font-family: var(--mono); font-size: 12.5px; line-height: 1.45; padding: 16px 18px; border-radius: 6px; overflow-x: auto; margin: 14px 0 18px; }}
main pre code {{ background: transparent; padding: 0; font-size: inherit; color: inherit; }}
.tablewrap {{ overflow-x: auto; margin: 14px 0 20px; border: 1px solid var(--line); border-radius: 6px; background: var(--paper); }}
table {{ border-collapse: collapse; width: 100%; font-size: 14px; min-width: 560px; }}
th, td {{ text-align: left; vertical-align: top; padding: 9px 12px; border-bottom: 1px solid var(--line); }}
th {{ font-family: var(--mono); font-size: 11.5px; letter-spacing: .08em; text-transform: uppercase; color: var(--fg-3); font-weight: 500; background: var(--code-bg); }}
tr:last-child td {{ border-bottom: 0; }}
td code {{ white-space: nowrap; }}
main h2[id="12-decisions"] + table, .decided {{ }}
.decisions-note {{ background: var(--amber-bg); border-left: 3px solid var(--amber); padding: 10px 14px; border-radius: 0 6px 6px 0; margin: 14px 0; font-size: 14.5px; max-width: 72ch; }}
.foot {{ border-top: 1px solid var(--line); margin-top: 48px; padding-top: 16px; font-family: var(--mono); font-size: 12px; color: var(--fg-3); }}
hr {{ border: 0; border-top: 1px solid var(--line); margin: 36px 0; }}
@media (max-width: 900px) {{
  .wrap {{ grid-template-columns: minmax(0, 1fr); gap: 18px; padding-inline: 16px; }}
  nav.toc {{ position: static; max-height: none; }}
  .mast {{ padding-inline: 16px; }}
}}
@media (prefers-reduced-motion: no-preference) {{ html {{ scroll-behavior: smooth; }} }}
a:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
</style>
<header class="mast"><div class="inner">
  <div class="eyebrow">ACES-OS-VISION-001 · Rev B · 2026-10-08</div>
  <h1>ACES OS: an engineering workstation operating system and its server</h1>
  <p>The ACES computational-engineering platform packaged as an operating system on Omarchy 4 "Quattro", with an ACES Server for simulation, models and video, sold as a product. Every hard fact traces to the research fact pack; targets and assumptions are marked.</p>
  <div class="facts"><span>Base <b>Omarchy 4.0.4 (MIT)</b></span><span>Model lane <b>DeepSeek-V4-Flash-0731 on GPUs 4–7</b></span><span>Simulation <b>GPUs 0–3, FP64</b></span><span>Creative <b>7 ArtCraft apps + ACES Video Studio (LTX-2.5)</b></span><span>Catalog <b>359 verified integrations, trial mode</b></span></div>
</div></header>
<div class="wrap">
  <nav class="toc" aria-label="Sections"><div class="label">Sections</div>{toc}</nav>
  <main>
    <div class="decisions-note"><strong>Decided 2026-10-08:</strong> DeepSeek-V4-Flash-0731 is the four-card model lane (GLM-5.2 stays the seven-card flagship mode); DARKNESS dual-boots; the seven ArtCraft apps ship on day one; the voxel kernel is standard in every install; GPU 6 is in the working set.</div>
    {html_body}
    <div class="foot">Source: C:\\Users\\shedm\\aces-os\\VISION.md · research: research/FACT_PACK.md, research/INTEGRATIONS.md · generated by docs/build_page.py</div>
  </main>
</div>
"""
io.open(OUT, "w", encoding="utf-8", newline="\n").write(page)
print("wrote", OUT, len(page), "bytes")
