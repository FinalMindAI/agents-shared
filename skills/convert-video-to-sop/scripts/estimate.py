#!/usr/bin/env python3
"""Estimate the token cost of converting screen-recording video(s) to SOPs.

Usage: python3 estimate.py <video-file | directory>

Prints a per-video table (duration, resolution, size, predicted keyframes,
estimated token range for the AI analysis pass) plus totals and a
recommendation tier. Preprocessing (ffmpeg/OCR/diff) is local and free;
the estimate covers only the AI synthesis pass.

Empirical anchors (dual-monitor Excel/Outlook workflows): ~0.25 extracted
frame-halves per second after densification; analysis costs roughly
4k-12k tokens per minute of video when the agent reads OCR diffs first
and views images selectively.
"""
import json
import subprocess
import sys
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi"}
TOK_PER_MIN_LOW, TOK_PER_MIN_HIGH = 4_000, 12_000


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    return {
        "name": path.name,
        "duration_s": float(data["format"]["duration"]),
        "width": data["streams"][0]["width"],
        "height": data["streams"][0]["height"],
        "size_mb": path.stat().st_size / 1e6,
    }


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    target = Path(sys.argv[1])
    videos = ([target] if target.is_file()
              else sorted(p for p in target.iterdir() if p.suffix.lower() in VIDEO_EXTS))
    if not videos:
        sys.exit(f"No videos found at {target}")

    rows, tot_min, tot_lo, tot_hi = [], 0.0, 0, 0
    for v in videos:
        p = probe(v)
        mins = p["duration_s"] / 60
        lo, hi = int(mins * TOK_PER_MIN_LOW), int(mins * TOK_PER_MIN_HIGH)
        mode = "dual-monitor" if p["width"] >= 3 * p["height"] else "single"
        frames = int(p["duration_s"] * (0.25 if mode == "dual-monitor" else 0.15))
        rows.append((p["name"], f"{mins:.1f}m", f"{p['width']}x{p['height']} ({mode})",
                     f"{p['size_mb']:.0f}MB", f"~{frames}", f"{lo//1000}k-{hi//1000}k"))
        tot_min += mins
        tot_lo += lo
        tot_hi += hi

    widths = [max(len(r[i]) for r in rows + [("video", "len", "capture", "size", "frames", "est tokens")])
              for i in range(6)]
    header = ("video", "len", "capture", "size", "frames", "est tokens")
    for r in [header] + rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths)))
    print(f"\nTOTAL: {len(videos)} video(s), {tot_min:.0f} min, "
          f"estimated {tot_lo//1000}k-{tot_hi//1000}k tokens for the AI analysis pass")
    tier = ("LOW — fine to proceed" if tot_hi <= 150_000
            else "MODERATE — confirm with user" if tot_hi <= 500_000
            else "HIGH — confirm with user and offer reduced-fidelity options")
    print(f"TIER: {tier}")


if __name__ == "__main__":
    main()
