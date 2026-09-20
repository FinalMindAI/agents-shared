#!/usr/bin/env python3
"""
render_sop.py — Render sop.json (the SOP source of truth) into a styled,
interactive HTML page, and optionally a PDF (via headless Chrome).

  python3 render_sop.py <sop.json> [--html SOP.html] [--pdf "Name SOP.pdf"]
                        [--chrome /path/to/chrome] [--title "..."]

HTML features: sticky table-of-contents with scrollspy, collapsible step cards,
right-floated timestamp pills, zebra tables, warning/note callout boxes,
click-to-zoom screenshots, and a print stylesheet so the same file prints to a
clean PDF.
"""
import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------- inline markdown
def _inline(text):
    """Escape HTML, then apply a safe subset of inline Markdown."""
    out = html.escape(text, quote=False)
    # code spans first (protect their contents from further formatting)
    codes = []
    def _stash(m):
        codes.append(m.group(1))
        return f"\x00{len(codes)-1}\x00"
    out = re.sub(r"`([^`]+)`", _stash, out)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", out)
    out = re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{codes[int(m.group(1))]}</code>", out)
    return out


def _prose_html(md):
    """Block-level prose: paragraphs + simple ordered/unordered lists."""
    blocks, lines = [], md.split("\n")
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if line.strip() == "":
            i += 1
            continue
        if re.match(r"^\s*[-*]\s+", line):
            items = []
            while i < n and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(_inline(re.sub(r"^\s*[-*]\s+", "", lines[i])))
                i += 1
            blocks.append("<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>")
        elif re.match(r"^\s*\d+[.)]\s+", line):
            items = []
            while i < n and re.match(r"^\s*\d+[.)]\s+", lines[i]):
                items.append(_inline(re.sub(r"^\s*\d+[.)]\s+", "", lines[i])))
                i += 1
            blocks.append("<ol>" + "".join(f"<li>{x}</li>" for x in items) + "</ol>")
        else:
            para = []
            while i < n and lines[i].strip() != "" and not re.match(r"^\s*([-*]|\d+[.)])\s+", lines[i]):
                para.append(lines[i])
                i += 1
            blocks.append("<p>" + _inline(" ".join(para)) + "</p>")
    return "\n".join(blocks)


# ---------------------------------------------------------------- block rendering
def _table_html(b):
    head = "".join(f"<th>{_inline(h)}</th>" for h in b["headers"])
    body = ""
    for row in b["rows"]:
        cells = "".join(f"<td>{_inline(c)}</td>" for c in row)
        body += f"<tr>{cells}</tr>"
    return f'<div class="tbl-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _image_html(b):
    cap = _inline(b["caption"]) if b.get("caption") else ""
    figcap = f"<figcaption>{cap}</figcaption>" if cap else ""
    src = html.escape(b["src"], quote=True)
    return f'<figure class="shot"><img src="{src}" alt="{html.escape(b.get("caption",""), quote=True)}" loading="lazy">{figcap}</figure>'


def _callout_html(b):
    icon = "⚠" if b["variant"] == "warning" else "ⓘ"
    body = _prose_html(re.sub(r"^⚠\s*", "", b["md"]))
    return f'<div class="callout {b["variant"]}"><span class="callout-ico">{icon}</span><div class="callout-body">{body}</div></div>'


def _expected_html(b):
    return f'<div class="expected"><span class="expected-tag">Expected result</span><div>{_prose_html(b["md"])}</div></div>'


def _block_html(b):
    k = b["kind"]
    if k == "table":
        return _table_html(b)
    if k == "image":
        return _image_html(b)
    if k == "callout":
        return _callout_html(b)
    if k == "expected":
        return _expected_html(b)
    if k == "prose":
        return _prose_html(b["md"])
    return ""


def _ts_pills(timestamps):
    if not timestamps:
        return ""
    pills = "".join(f'<span class="ts">{html.escape(t)}</span>' for t in timestamps)
    return f'<span class="ts-group">{pills}</span>'


# ---------------------------------------------------------------- document assembly
def render_html(data, title=None):
    doc = data["doc"]
    title = title or data["meta"].get("title", "SOP")

    toc, body = [], []
    open_step = False

    def close_step():
        nonlocal open_step
        if open_step:
            body.append("</div></details>")
            open_step = False

    for b in doc:
        k = b["kind"]
        if k == "heading":
            close_step()
            lvl = b["level"]
            if lvl == 1:
                continue  # title rendered in masthead
            body.append(f'<h{lvl} id="{b["slug"]}" data-toc class="sec-h sec-h{lvl}">{_inline(b["text"])}</h{lvl}>')
            cls = "toc-h2" if lvl <= 2 else "toc-h3"
            toc.append(f'<a class="{cls}" href="#{b["slug"]}">{_inline(b["text"])}</a>')
        elif k == "step":
            close_step()
            num = html.escape(b["number"])
            body.append(
                f'<details class="step" id="{b["slug"]}" data-toc open>'
                f'<summary><span class="step-num">{num}</span>'
                f'<span class="step-title">{_inline(b["title"])}</span>'
                f'{_ts_pills(b["timestamps"])}</summary><div class="step-body">'
            )
            open_step = True
            toc.append(
                f'<a class="toc-step" href="#{b["slug"]}">'
                f'<span class="toc-num">{num}</span>{_inline(b["title"])}</a>'
            )
        else:
            body.append(_block_html(b))

    close_step()

    toc_html = "\n".join(toc)
    body_html = "\n".join(body)
    css = _CSS
    js = _JS
    return _PAGE.format(
        title=html.escape(title),
        css=css,
        js=js,
        toc=toc_html,
        body=body_html,
    )


# ---------------------------------------------------------------- PDF via Chrome
def _find_chrome():
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chrome"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


def render_pdf(html_path, pdf_path, chrome=None):
    chrome = chrome or _find_chrome()
    if not chrome:
        print("  ! Chrome/Chromium not found — skipping PDF "
              "(pass --chrome or install Google Chrome)", file=sys.stderr)
        return False
    html_path, pdf_path = Path(html_path).resolve(), Path(pdf_path).resolve()
    with tempfile.TemporaryDirectory() as ud:
        cmd = [
            chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
            f"--user-data-dir={ud}", "--no-pdf-header-footer",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=10000",
            f"--print-to-pdf={pdf_path}", html_path.as_uri(),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
    if pdf_path.exists() and pdf_path.stat().st_size > 1000:
        return True
    # retry without the newer flag name for older Chrome builds
    print("  ! first PDF attempt failed, retrying with legacy flags", file=sys.stderr)
    with tempfile.TemporaryDirectory() as ud:
        cmd = [chrome, "--headless", "--disable-gpu", f"--user-data-dir={ud}",
               "--print-to-pdf-no-header", f"--print-to-pdf={pdf_path}", html_path.as_uri()]
        subprocess.run(cmd, capture_output=True, text=True)
    return pdf_path.exists() and pdf_path.stat().st_size > 1000


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path, help="sop.json")
    ap.add_argument("--html", type=Path)
    ap.add_argument("--pdf", type=Path)
    ap.add_argument("--chrome")
    ap.add_argument("--title")
    args = ap.parse_args()

    data = json.loads(args.input.read_text(encoding="utf-8"))
    html_out = args.html or args.input.with_name("SOP.html")
    html_out.write_text(render_html(data, args.title), encoding="utf-8")
    print(f"  html -> {html_out}")

    if args.pdf:
        ok = render_pdf(html_out, args.pdf, args.chrome)
        print(f"  pdf  -> {args.pdf}" if ok else "  pdf  -> FAILED")


# ---------------------------------------------------------------- assets
_CSS = r"""
:root{
  --bg:#f6f7f9; --card:#fff; --ink:#1c2230; --muted:#5b6472; --line:#e3e7ee;
  --accent:#2f54eb; --accent-soft:#eef2ff; --warn-bg:#fff7e6; --warn-bd:#ffd591;
  --warn-ink:#874d00; --note-bg:#e6f4ff; --note-bd:#91caff; --note-ink:#0050b3;
  --ok-bg:#f0fbf3; --ok-bd:#b7eb8f; --ok-ink:#237804; --code:#f2f3f7;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
code{background:var(--code);border:1px solid var(--line);border-radius:4px;
  padding:.05em .4em;font:13px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace}
.layout{display:grid;grid-template-columns:300px minmax(0,1fr);gap:0;max-width:1280px;margin:0 auto}

/* masthead */
.masthead{grid-column:1/-1;background:linear-gradient(135deg,#1d2a52,#2f54eb);
  color:#fff;padding:34px 48px;border-radius:0 0 18px 18px}
.masthead h1{margin:0 0 6px;font-size:26px;letter-spacing:-.01em}
.masthead .sub{opacity:.82;font-size:14px}

/* sidebar TOC */
.sidebar{position:sticky;top:0;align-self:start;height:100vh;overflow:auto;
  padding:24px 14px 60px 28px}
.sidebar .toc-title{font-size:11px;text-transform:uppercase;letter-spacing:.08em;
  color:var(--muted);margin:6px 0 12px}
.sidebar a{display:block;color:var(--muted);font-size:13px;padding:4px 10px;
  border-left:2px solid transparent;border-radius:0 6px 6px 0;line-height:1.35}
.sidebar a:hover{color:var(--ink);background:var(--accent-soft);text-decoration:none}
.sidebar a.active{color:var(--accent);border-left-color:var(--accent);
  background:var(--accent-soft);font-weight:600}
.toc-h2{margin-top:14px;font-weight:700;color:var(--ink)!important;font-size:13px!important}
.toc-h3{padding-left:18px!important}
.toc-step{padding-left:18px!important}
.toc-num{display:inline-block;min-width:20px;color:var(--accent);font-weight:600;
  font-variant-numeric:tabular-nums;margin-right:6px}

/* content */
.content{padding:30px 48px 120px}
.sec-h{letter-spacing:-.01em;scroll-margin-top:18px}
.sec-h2{font-size:21px;margin:34px 0 10px;padding-bottom:6px;border-bottom:2px solid var(--line)}
.sec-h3{font-size:17px;margin:24px 0 8px;color:#2a3550}
.content p{margin:.5em 0}
.content ul,.content ol{margin:.4em 0 .8em;padding-left:1.4em}
.content li{margin:.2em 0}

/* step cards */
.step{background:var(--card);border:1px solid var(--line);border-radius:12px;
  margin:14px 0;box-shadow:0 1px 2px rgba(20,30,60,.04);scroll-margin-top:14px;overflow:hidden}
.step>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:12px;
  padding:14px 18px;user-select:none}
.step>summary::-webkit-details-marker{display:none}
.step>summary:hover{background:#fafbff}
.step[open]>summary{border-bottom:1px solid var(--line)}
.step-num{flex:none;width:30px;height:30px;border-radius:8px;background:var(--accent);
  color:#fff;font-weight:700;font-size:14px;display:grid;place-items:center;
  font-variant-numeric:tabular-nums}
.step-title{font-weight:650;font-size:15.5px;flex:1 1 auto}
.ts-group{flex:none;margin-left:auto;display:flex;gap:6px}
.ts{background:#11151f;color:#cfe0ff;font:12px/1 ui-monospace,Menlo,monospace;
  padding:5px 9px;border-radius:999px;letter-spacing:.02em}
.step-body{padding:6px 18px 18px}
.step-body>:first-child{margin-top:.3em}

/* tables */
.tbl-wrap{overflow-x:auto;margin:12px 0;border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:13.5px}
thead th{background:#f0f2f7;text-align:left;font-weight:650;color:#2a3550;
  padding:9px 12px;border-bottom:1px solid var(--line);white-space:nowrap;position:sticky;top:0}
tbody td{padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:top}
tbody tr:nth-child(even){background:#fafbfd}
tbody tr:last-child td{border-bottom:none}

/* callouts */
.callout{display:flex;gap:11px;margin:12px 0;padding:11px 14px;border-radius:10px;
  border:1px solid;font-size:14px}
.callout .callout-ico{flex:none;font-size:15px;line-height:1.5}
.callout .callout-body>:first-child{margin-top:0}
.callout .callout-body>:last-child{margin-bottom:0}
.callout.warning{background:var(--warn-bg);border-color:var(--warn-bd);color:var(--warn-ink)}
.callout.note{background:var(--note-bg);border-color:var(--note-bd);color:var(--note-ink)}
.callout.warning code,.callout.note code{background:rgba(255,255,255,.6)}

/* expected result */
.expected{display:flex;gap:11px;align-items:baseline;margin:12px 0;padding:10px 14px;
  background:var(--ok-bg);border:1px solid var(--ok-bd);border-radius:10px;color:#1c2230}
.expected-tag{flex:none;font-size:11px;font-weight:700;text-transform:uppercase;
  letter-spacing:.05em;color:var(--ok-ink);background:#fff;border:1px solid var(--ok-bd);
  border-radius:6px;padding:3px 7px}
.expected>div>:first-child{margin-top:0}.expected>div>:last-child{margin-bottom:0}

/* screenshots */
.shot{margin:14px 0;text-align:center}
.shot img{max-width:100%;border:1px solid var(--line);border-radius:10px;cursor:zoom-in;
  box-shadow:0 2px 10px rgba(20,30,60,.08)}
.shot figcaption{margin-top:7px;font-size:12.5px;color:var(--muted)}

/* lightbox */
#lightbox{position:fixed;inset:0;background:rgba(8,12,22,.86);display:none;
  align-items:center;justify-content:center;z-index:99;cursor:zoom-out;padding:30px}
#lightbox.open{display:flex}
#lightbox img{max-width:96%;max-height:96%;border-radius:8px;box-shadow:0 8px 40px rgba(0,0,0,.5)}

@media(max-width:880px){
  .layout{grid-template-columns:1fr}
  .sidebar{position:static;height:auto;border-bottom:1px solid var(--line)}
  .content{padding:20px}
}

/* ---- print / PDF ---- */
@media print{
  @page{margin:14mm 12mm}
  body{background:#fff;font-size:11pt}
  .layout{display:block;max-width:none}
  .sidebar,#lightbox{display:none!important}
  .masthead{border-radius:0;padding:0 0 14px;background:none;color:#000;
    border-bottom:3px solid var(--accent);margin-bottom:10px}
  .masthead .sub{opacity:1;color:#444}
  .content{padding:0}
  .step{break-inside:avoid;box-shadow:none;border-color:#ccc}
  .step>summary{cursor:default}
  details:not([open])>*{display:revert!important}  /* expand all for print */
  .tbl-wrap,table,figure,.callout,.expected{break-inside:avoid}
  thead th{position:static}
  .ts{background:#222!important;color:#fff!important;-webkit-print-color-adjust:exact;print-color-adjust:exact}
  .step-num{background:var(--accent)!important;-webkit-print-color-adjust:exact;print-color-adjust:exact}
  thead th,tbody tr:nth-child(even),.callout,.expected{-webkit-print-color-adjust:exact;print-color-adjust:exact}
  a{color:#000}
}
"""

_JS = r"""
// expand all step cards for printing, then restore
window.addEventListener('beforeprint',()=>document.querySelectorAll('details').forEach(d=>{d.dataset.wasopen=d.open;d.open=true;}));
window.addEventListener('afterprint',()=>document.querySelectorAll('details').forEach(d=>{d.open=(d.dataset.wasopen==='true');}));
// scrollspy
const links=[...document.querySelectorAll('.sidebar a')];
const map=new Map(links.map(a=>[a.getAttribute('href').slice(1),a]));
const obs=new IntersectionObserver(es=>{
  es.forEach(e=>{if(e.isIntersecting){
    links.forEach(l=>l.classList.remove('active'));
    const a=map.get(e.target.id); if(a){a.classList.add('active');
      a.scrollIntoView({block:'nearest'});}
  }});
},{rootMargin:'-10% 0px -80% 0px'});
document.querySelectorAll('[data-toc]').forEach(el=>obs.observe(el));
// lightbox
const lb=document.getElementById('lightbox'),lbimg=lb.querySelector('img');
document.querySelectorAll('.shot img').forEach(img=>img.addEventListener('click',()=>{
  lbimg.src=img.currentSrc||img.src;lb.classList.add('open');}));
lb.addEventListener('click',()=>lb.classList.remove('open'));
"""

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>{css}</style></head>
<body>
<div class="layout">
  <header class="masthead"><h1>{title}</h1>
    <div class="sub">Standard Operating Procedure · timestamps reference the source recording</div>
  </header>
  <nav class="sidebar"><div class="toc-title">Contents</div>{toc}</nav>
  <main class="content">{body}</main>
</div>
<div id="lightbox"><img alt=""></div>
<script>{js}</script>
</body></html>
"""

if __name__ == "__main__":
    main()
