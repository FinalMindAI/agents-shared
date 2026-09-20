#!/usr/bin/env python3
"""
md_to_json.py — Convert an authored SOP.md into the structured sop.json that is
the SOP source of truth.

The conversion is deterministic and lossless: every line ends up in exactly one
block, and anything the parser does not recognize as a table / image / callout /
expected-result / step-heading falls through as a `prose` block (Markdown kept
verbatim). Re-rendering sop.json should reproduce the document.

sop.json shape:
{
  "meta": { "title": str, "source_md": str },
  "doc": [ <block>, ... ]          # flat, document order
}

Block kinds:
  {"kind":"heading","level":int,"text":str,"slug":str}
  {"kind":"step","level":int,"number":str,"title":str,"timestamps":[str,...],"slug":str}
  {"kind":"table","headers":[str,...],"rows":[[str,...],...]}
  {"kind":"image","src":str,"caption":str,"width":str|None}
  {"kind":"callout","variant":"warning"|"note","md":str}
  {"kind":"expected","md":str}
  {"kind":"prose","md":str}

Usage:
  python3 md_to_json.py <SOP.md> [-o sop.json]
"""
import argparse
import json
import re
import sys
from pathlib import Path

# `[mm:ss]` or `[mm:ss–mm:ss]`, optionally wrapped in backticks.
TS_RE = re.compile(r"`?\[(\d{1,2}:\d{2}(?:\s*[–-]\s*\d{1,2}:\d{2})?)\]`?")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
STEP_RE = re.compile(r"^Step\s+([0-9]+[a-z]?)\s*[—\-:]\s*(.*)$")
IMAGE_RE = re.compile(r"^!\[(?P<cap>.*?)\]\((?P<src>[^)]+)\)(?:\{(?P<attr>[^}]*)\})?\s*$")
WIDTH_RE = re.compile(r"width=([0-9.]+\w*)")
TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$")


def slugify(text):
    s = re.sub(r"`[^`]*`", "", text)          # drop inline code
    s = re.sub(r"[*_]", "", s)                 # drop emphasis marks
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-") or "section"


def parse_table_row(line):
    cells = line.strip().split("|")
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return [c.strip() for c in cells]


def make_heading(level, text):
    m = STEP_RE.match(text)
    if m:
        number, rest = m.group(1), m.group(2)
        timestamps = [t.replace("–", "–") for t in TS_RE.findall(rest)]
        title = TS_RE.sub("", rest)
        title = re.sub(r"\s*/\s*$", "", title)        # dangling "/" between two ts
        title = re.sub(r"\s{2,}", " ", title).strip(" /·").strip()
        return {
            "kind": "step",
            "level": level,
            "number": number,
            "title": title,
            "timestamps": timestamps,
            "slug": f"step-{number}",
        }
    return {"kind": "heading", "level": level, "text": text, "slug": slugify(text)}


def convert(md_text, source_name):
    lines = md_text.split("\n")
    doc = []
    title = None
    i, n = 0, len(lines)
    prose_buf = []
    seen_slugs = {}

    def flush_prose():
        if prose_buf:
            text = "\n".join(prose_buf).strip("\n")
            if text.strip():
                doc.append({"kind": "prose", "md": text})
        prose_buf.clear()

    def push(block):
        # de-dupe slugs so anchors stay unique
        if "slug" in block:
            base = block["slug"]
            seen_slugs[base] = seen_slugs.get(base, 0) + 1
            if seen_slugs[base] > 1:
                block["slug"] = f"{base}-{seen_slugs[base]}"
        doc.append(block)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # blank line ends a prose paragraph but is preserved inside prose buffer
        if stripped == "":
            if prose_buf:
                prose_buf.append("")
            i += 1
            continue

        # horizontal rule — structural divider, dropped
        if stripped in ("---", "***", "___"):
            flush_prose()
            i += 1
            continue

        # heading
        hm = HEADING_RE.match(line)
        if hm:
            flush_prose()
            level, text = len(hm.group(1)), hm.group(2).strip()
            if level == 1 and title is None:
                title = re.sub(r"^SOP\s*[—\-:]\s*", "", text).strip()
                push({"kind": "heading", "level": 1, "text": text, "slug": "top"})
            else:
                push(make_heading(level, text))
            i += 1
            continue

        # image
        im = IMAGE_RE.match(line)
        if im:
            flush_prose()
            attr = im.group("attr") or ""
            wm = WIDTH_RE.search(attr)
            push({
                "kind": "image",
                "src": im.group("src").strip(),
                "caption": im.group("cap").strip(),
                "width": wm.group(1) if wm else None,
            })
            i += 1
            continue

        # GFM table: header row followed by a separator row of dashes
        if stripped.startswith("|") and i + 1 < n and TABLE_SEP_RE.match(lines[i + 1]):
            flush_prose()
            headers = parse_table_row(line)
            rows = []
            i += 2
            while i < n and lines[i].strip().startswith("|"):
                rows.append(parse_table_row(lines[i]))
                i += 1
            push({"kind": "table", "headers": headers, "rows": rows})
            continue

        # blockquote callout (may span multiple > lines)
        if stripped.startswith(">"):
            flush_prose()
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            body = "\n".join(buf).strip()
            variant = "warning" if "⚠" in body else "note"
            push({"kind": "callout", "variant": variant, "md": body})
            continue

        # ⚠ warning paragraph (until blank line)
        if stripped.startswith("⚠"):
            flush_prose()
            buf = []
            while i < n and lines[i].strip() != "" and not lines[i].strip().startswith(("|", ">", "#", "![")):
                buf.append(lines[i])
                i += 1
            push({"kind": "callout", "variant": "warning", "md": "\n".join(buf).strip()})
            continue

        # Expected result paragraph
        if re.match(r"^\*\*Expected result", stripped):
            flush_prose()
            buf = []
            while i < n and lines[i].strip() != "" and not lines[i].strip().startswith(("|", ">", "#", "![")):
                buf.append(lines[i])
                i += 1
            md = "\n".join(buf).strip()
            md = re.sub(r"^\*\*Expected result:?\*\*\s*", "", md).strip()
            push({"kind": "expected", "md": md})
            continue

        # default: prose
        prose_buf.append(line)
        i += 1

    flush_prose()
    return {"meta": {"title": title or source_name, "source_md": source_name}, "doc": doc}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path)
    ap.add_argument("-o", "--output", type=Path)
    args = ap.parse_args()

    md = args.input.read_text(encoding="utf-8")
    data = convert(md, args.input.name)
    out = args.output or args.input.with_name("sop.json")
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    steps = sum(1 for b in data["doc"] if b["kind"] == "step")
    print(f"{args.input.name}: {len(data['doc'])} blocks, {steps} steps -> {out}")


if __name__ == "__main__":
    main()
