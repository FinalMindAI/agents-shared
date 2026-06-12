#!/usr/bin/env bash
# extract.sh — pass 1 of convert-video-to-sop: scene-change keyframes + OCR.
#
# Usage: extract.sh <video-file | directory> [output-root]
#   output-root defaults to <input dir>/sop-build
#
# Per video, writes <output-root>/artifacts/<name>/:
#   frames/        keyframes; _L/_R = monitor halves (dual capture), _F = full frame
#   ocr/           tesseract text sidecar per frame
#   index.md       timestamp -> frame/OCR lookup
#   params.json    source path + capture geometry (consumed by diff.py)
#   transcript.txt narration transcript, or a note when silent/absent
#
# Idempotent: existing frames/transcripts are skipped; safe to re-run.
# Tunables (env): SCENE_THRESH=0.04 MIN_GAP=2 FALLBACK_EVERY=20
#                 WHISPER_MODEL=~/models/ggml-large-v3-turbo.bin
set -euo pipefail

SCENE_THRESH="${SCENE_THRESH:-0.04}"
MIN_GAP="${MIN_GAP:-2}"
FALLBACK_EVERY="${FALLBACK_EVERY:-20}"
WHISPER_MODEL="${WHISPER_MODEL:-$HOME/models/ggml-large-v3-turbo.bin}"

in="${1:?usage: extract.sh <video|dir> [output-root]}"
vids=()
if [[ -d "$in" ]]; then
  dir="$in"
  while IFS= read -r -d '' v; do vids+=("$v"); done < <(
    find "$in" -maxdepth 1 \( -iname '*.mp4' -o -iname '*.mov' -o -iname '*.m4v' \) -print0 | sort -z)
else
  dir="$(dirname "$in")"
  vids=("$in")
fi
[[ ${#vids[@]} -gt 0 ]] || { echo "No videos found in $in" >&2; exit 1; }
root="${2:-$dir/sop-build}"

for f in "${vids[@]}"; do
  name="$(basename "${f%.*}")"
  out="$root/artifacts/$name"
  mkdir -p "$out/frames" "$out/ocr"
  abs="$(cd "$(dirname "$f")" && pwd)/$(basename "$f")"

  # --- Capture geometry: aspect >= 3:1 means side-by-side dual monitors ----
  IFS=, read -r w h < <(ffprobe -v error -select_streams v:0 \
    -show_entries stream=width,height -of csv=p=0 "$f")
  mode=single; (( w >= 3 * h )) && mode=dual
  printf '{"video":"%s","mode":"%s","width":%s,"height":%s}\n' \
    "$abs" "$mode" "$w" "$h" > "$out/params.json"

  # --- Transcript (only when a non-silent audio track and whisper exist) ---
  if [[ ! -f "$out/transcript.txt" ]]; then
    if [[ -z "$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$f")" ]]; then
      echo "(no audio track)" > "$out/transcript.txt"
    else
      maxvol=$(ffmpeg -nostdin -v info -i "$f" -map a:0 -af volumedetect -f null - 2>&1 |
        sed -n 's/.*max_volume: \(-\{0,1\}[0-9.]*\) dB.*/\1/p')
      if awk -v v="${maxvol:--91}" 'BEGIN{exit !(v < -80)}'; then
        echo "(audio track is silent — no narration to transcribe)" > "$out/transcript.txt"
      elif command -v whisper-cli >/dev/null && [[ -f "$WHISPER_MODEL" ]]; then
        ffmpeg -nostdin -v error -i "$f" -ar 16000 -ac 1 -c:a pcm_s16le "$out/audio.wav" -y
        whisper-cli -m "$WHISPER_MODEL" -f "$out/audio.wav" -osrt -otxt -of "$out/transcript"
        rm -f "$out/audio.wav"
      else
        echo "(audio present but whisper-cli/model unavailable — transcript skipped)" \
          > "$out/transcript.txt"
      fi
    fi
  fi

  # --- Scene-change timestamps + periodic fallback --------------------------
  duration=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f")
  duration=${duration%.*}
  scene_ts=$(ffmpeg -nostdin -v info -i "$f" \
      -vf "select='gt(scene,$SCENE_THRESH)',metadata=print" -f null - 2>&1 |
    grep -o 'pts_time:[0-9.]*' | cut -d: -f2)
  ts_list=$( { echo "$scene_ts"; seq 0 "$FALLBACK_EVERY" "$duration"; } |
    sort -n | awk -v gap="$MIN_GAP" 'NF && ($1 - last >= gap || NR == 1) {print $1; last = $1}')

  # --- Frames + OCR ----------------------------------------------------------
  hw=$(( w / 2 ))
  while read -r t; do
    [[ -z "$t" ]] && continue
    stamp=$(printf '%07.1f' "$t" | tr '.' '_')
    if [[ "$mode" == dual ]]; then
      L="$out/frames/${stamp}_L.png"; R="$out/frames/${stamp}_R.png"
      [[ -f "$L" ]] && continue
      ffmpeg -nostdin -v error -ss "$t" -i "$f" \
        -filter_complex "split=2[a][b];[a]crop=${hw}:${h}:0:0[l];[b]crop=${hw}:${h}:${hw}:0[r]" \
        -map '[l]' -frames:v 1 "$L" -map '[r]' -frames:v 1 "$R" -y
    else
      F="$out/frames/${stamp}_F.png"
      [[ -f "$F" ]] && continue
      ffmpeg -nostdin -v error -ss "$t" -i "$f" -frames:v 1 \
        -vf "scale='min(1920,iw)':-2" "$F" -y
    fi
  done <<< "$ts_list"

  # OCR backfill for any frame missing its sidecar. Relative paths + cd:
  # leptonica can't open absolute paths containing '&'.
  for p in "$out"/frames/*.png; do
    b="$(basename "${p%.png}")"
    [[ -f "$out/ocr/$b.txt" ]] ||
      (cd "$out" && tesseract "frames/$b.png" "ocr/$b" -l eng --psm 6 2>/dev/null)
  done

  # --- Index ------------------------------------------------------------------
  {
    echo "# $name"
    echo
    echo "Source: $abs (${w}x${h}, $mode capture, ${duration}s)."
    echo "Frame suffixes: _L/_R = left/right monitor half, _F = full frame."
    echo "Each frames/<stamp>_X.png has a matching ocr/<stamp>_X.txt."
    echo
    echo "| time (s) | frames |"
    echo "|---|---|"
    ls "$out/frames" | sed 's/_[A-Z]\.png$//' | sort -u | while read -r s; do
      echo "| $(echo "$s" | tr '_' '.') | $(ls "$out/frames" | grep "^${s}_" | tr '\n' ' ')|"
    done
  } > "$out/index.md"

  echo "DONE: $name -> $(ls "$out/frames" | wc -l | tr -d ' ') frames ($mode)"
done
