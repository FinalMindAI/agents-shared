#!/usr/bin/env python3
"""Pass 2 of convert-video-to-sop: diff consecutive frames, classify each
change, and densify (pull extra frames) where detail was lost.

For each artifacts/<name>/ dir produced by extract.sh, emits diffs.json with
one entry per consecutive frame pair per frame-suffix (L/R halves or F):

  - pct_changed: % of pixels changed (after noise threshold)
  - bbox: [x, y, w, h] of the changed region
  - change: classification —
      none                 — below noise floor (fallback frame, nothing happened)
      app-or-window-switch — title band changed heavily; big diff but ONE atomic
                             action, not worth densifying
      scroll-or-reflow     — big pixel diff but most OCR lines persisted;
                             content moved, didn't change
      bulk-change          — title stable, content genuinely replaced; the
                             interval gets densified with 1s-interval frames
      localized            — small change; crop covers it
  - crop: small PNG of the changed region from the *after* frame (localized
    only; skipped when changes are dispersed across the frame)
  - ocr_added / ocr_removed: line-level diff of the tesseract sidecars

Densify: bulk-change pairs more than DENSIFY_MIN_GAP apart get intermediate
frames extracted at DENSIFY_STEP intervals (same naming scheme as extract.sh,
so everything stays idempotent), then the diff is recomputed. One round only;
densified pairs land ~1s apart and are never re-suggested.

Usage: python3 diff.py <artifacts-dir> [more dirs...] [--no-densify]
The source video and capture geometry come from each dir's params.json.
Tunables (env): DENSIFY_MAX_FRAMES=120
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops

PIXEL_THRESH = 24       # per-pixel delta (0-255) below this is noise
MIN_PCT = 0.05          # below this % changed -> "none"
BULK_PCT = 25           # above this % changed -> candidate for app-switch/scroll/bulk
TITLE_BAND = 90         # px; top strip used to detect app/window switches
TITLE_SWITCH_PCT = 30   # title-band % changed above this -> app/window switch
SCROLL_OVERLAP = 0.5    # >=50% of OCR lines persisted -> scroll, not new content
PAD = 60                # context padding around the changed bbox, px
MAX_OCR_LINES = 40      # cap added/removed line lists
DENSIFY_STEP = 1.0      # seconds between densified frames
DENSIFY_MIN_GAP = 3.0   # only densify pairs further apart than this
DENSIFY_MAX_FRAMES = int(os.environ.get("DENSIFY_MAX_FRAMES", 120))


def ocr_lines(path: Path) -> list:
    if not path.exists():
        return []
    lines = [" ".join(l.split()) for l in path.read_text(errors="replace").splitlines()]
    return [l for l in lines if len(l) > 2]


def pct_nonzero(img) -> float:
    hist = img.histogram()
    return 100.0 * sum(hist[1:]) / (img.width * img.height)


def diff_pair(prev_png: Path, next_png: Path, prev_txt: Path, next_txt: Path,
              crop_dir: Path) -> dict:
    a = Image.open(prev_png).convert("L")
    b = Image.open(next_png).convert("L")
    delta = ImageChops.difference(a, b).point(lambda p: 255 if p > PIXEL_THRESH else 0)
    pct = round(pct_nonzero(delta), 2)
    if pct < MIN_PCT:
        return {"pct_changed": pct, "change": "none"}

    x0, y0, x1, y1 = delta.getbbox()
    entry = {"pct_changed": pct, "bbox": [x0, y0, x1 - x0, y1 - y0]}

    prev_lines, next_lines = ocr_lines(prev_txt), ocr_lines(next_txt)
    added = [l for l in next_lines if l not in set(prev_lines)]
    removed = [l for l in prev_lines if l not in set(next_lines)]
    if added:
        entry["ocr_added"] = added[:MAX_OCR_LINES]
    if removed:
        entry["ocr_removed"] = removed[:MAX_OCR_LINES]

    if pct >= BULK_PCT:
        title_pct = pct_nonzero(delta.crop((0, 0, delta.width, TITLE_BAND)))
        overlap = (len(set(prev_lines) & set(next_lines)) / len(set(prev_lines))
                   if prev_lines else 0.0)
        if title_pct >= TITLE_SWITCH_PCT:
            entry["change"] = "app-or-window-switch"
        elif overlap >= SCROLL_OVERLAP:
            entry["change"] = "scroll-or-reflow"
        else:
            entry["change"] = "bulk-change"
        return entry

    entry["change"] = "localized"
    if (x1 - x0) * (y1 - y0) > 0.7 * a.width * a.height:
        entry["note"] = "changes dispersed across frame; view after_frame instead of a crop"
        return entry
    cx0, cy0 = max(0, x0 - PAD), max(0, y0 - PAD)
    cx1, cy1 = min(b.width, x1 + PAD), min(b.height, y1 + PAD)
    crop_path = crop_dir / f"{next_png.stem}_diff.png"
    Image.open(next_png).crop((cx0, cy0, cx1, cy1)).save(crop_path)
    entry["crop"] = f"diffs/{crop_path.name}"
    return entry


def compute_pairs(video_dir: Path) -> list:
    frames, ocr, crop_dir = video_dir / "frames", video_dir / "ocr", video_dir / "diffs"
    crop_dir.mkdir(exist_ok=True)
    sides = sorted({p.stem[-1] for p in frames.glob("*_[A-Z].png")})
    entries = []
    for side in sides:
        pngs = sorted(frames.glob(f"*_{side}.png"))
        for prev_png, next_png in zip(pngs, pngs[1:]):
            e = {
                "side": side,
                "from_s": float(prev_png.stem[:-2].replace("_", ".")),
                "to_s": float(next_png.stem[:-2].replace("_", ".")),
                "after_frame": f"frames/{next_png.name}",
            }
            e.update(diff_pair(prev_png, next_png,
                               ocr / f"{prev_png.stem}.txt", ocr / f"{next_png.stem}.txt",
                               crop_dir))
            entries.append(e)
    entries.sort(key=lambda e: (e["from_s"], e["side"]))
    return entries


def densify_intervals(entries: list) -> list:
    raw = sorted((e["from_s"], e["to_s"]) for e in entries
                 if e.get("change") == "bulk-change"
                 and e["to_s"] - e["from_s"] > DENSIFY_MIN_GAP)
    merged = []
    for t0, t1 in raw:
        if merged and t0 <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], t1)
        else:
            merged.append([t0, t1])
    return merged


def extract_frame(params: dict, t: float, video_dir: Path) -> bool:
    stamp = f"{t:07.1f}".replace(".", "_")
    video = params["video"]
    base = ["ffmpeg", "-nostdin", "-v", "error", "-ss", str(t), "-i", video]
    if params["mode"] == "dual":
        hw, h = params["width"] // 2, params["height"]
        outs = [video_dir / "frames" / f"{stamp}_{s}.png" for s in ("L", "R")]
        if outs[0].exists():
            return False
        subprocess.run(base + [
            "-filter_complex",
            f"split=2[a][b];[a]crop={hw}:{h}:0:0[l];[b]crop={hw}:{h}:{hw}:0[r]",
            "-map", "[l]", "-frames:v", "1", str(outs[0]),
            "-map", "[r]", "-frames:v", "1", str(outs[1]), "-y"], check=True)
    else:
        outs = [video_dir / "frames" / f"{stamp}_F.png"]
        if outs[0].exists():
            return False
        subprocess.run(base + ["-frames:v", "1", "-vf", "scale='min(1920,iw)':-2",
                               str(outs[0]), "-y"], check=True)
    for png in outs:
        # relative paths + cwd: leptonica can't open absolute paths containing '&'
        subprocess.run(["tesseract", f"frames/{png.name}", f"ocr/{png.stem}",
                        "-l", "eng", "--psm", "6"],
                       check=False, capture_output=True, cwd=video_dir)
    return True


def rebuild_index(video_dir: Path):
    index = video_dir / "index.md"
    head = []
    if index.exists():
        for line in index.read_text().splitlines():
            if line.startswith("| time"):
                break
            head.append(line)
    else:
        head = [f"# {video_dir.name}", ""]
    rows = ["| time (s) | frames |", "|---|---|"]
    stamps = sorted({p.stem[:-2] for p in (video_dir / "frames").glob("*_[A-Z].png")})
    for s in stamps:
        names = " ".join(p.name for p in sorted((video_dir / "frames").glob(f"{s}_*.png")))
        rows.append(f"| {s.replace('_', '.')} | {names} |")
    index.write_text("\n".join(head + rows) + "\n")


def process(video_dir: Path, densify: bool):
    video_dir = video_dir.resolve()
    out_path = video_dir / "diffs.json"
    frames = list((video_dir / "frames").glob("*.png"))
    if not frames:
        print(f"SKIP {video_dir.name}: no frames/", file=sys.stderr)
        return
    if out_path.exists() and out_path.stat().st_mtime > max(p.stat().st_mtime for p in frames):
        print(f"SKIP {video_dir.name}: diffs.json up to date")
        return

    entries = compute_pairs(video_dir)
    params_path = video_dir / "params.json"
    densified = []
    if densify and params_path.exists():
        params = json.loads(params_path.read_text())
        if Path(params["video"]).exists():
            budget = DENSIFY_MAX_FRAMES
            for t0, t1 in densify_intervals(entries):
                t = t0 + DENSIFY_STEP
                while t < t1 - DENSIFY_STEP / 2 and budget > 0:
                    budget -= extract_frame(params, round(t, 1), video_dir)
                    t += DENSIFY_STEP
                densified.append([t0, t1])
                if budget <= 0:
                    print(f"WARN {video_dir.name}: densify frame budget "
                          f"({DENSIFY_MAX_FRAMES}) exhausted; remaining intervals skipped"
                          " — re-run with a higher DENSIFY_MAX_FRAMES", file=sys.stderr)
                    break
            if densified:
                rebuild_index(video_dir)
                entries = compute_pairs(video_dir)  # recompute over the denser timeline

    out_path.write_text(json.dumps(
        {"densified_intervals": densified, "pairs": entries}, indent=1))
    by_class = {}
    for e in entries:
        by_class[e.get("change", "?")] = by_class.get(e.get("change", "?"), 0) + 1
    print(f"DONE {video_dir.name}: {len(entries)} pairs {by_class}"
          + (f", densified {densified}" if densified else ""))


if __name__ == "__main__":
    dirs = [a for a in sys.argv[1:] if a != "--no-densify"]
    if not dirs:
        sys.exit(__doc__)
    for arg in dirs:
        process(Path(arg), densify="--no-densify" not in sys.argv)
