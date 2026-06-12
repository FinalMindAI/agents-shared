---
name: convert-video-to-sop
version: 1.0.0
description: |
  Convert screen-recording videos (a single file or a directory) into
  high-fidelity Standard Operating Procedures with embedded screenshots,
  delivered as Word + PDF. Local preprocessing (scene detection, OCR,
  classified frame diffs) keeps token cost low; estimates cost up front
  and confirms with the user before the AI analysis pass.
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Agent
  - AskUserQuestion
---

# /convert-video-to-sop — Screen recording → illustrated SOP

Turns "watch someone do the task" recordings into step-by-step SOPs an untrained
operator could follow. Heavy lifting (frame extraction, OCR, change detection)
runs locally at zero token cost; the model only reads pre-digested text and a
small, selective set of images.

## Arguments

- `/convert-video-to-sop <video.mp4 | directory>` — required input
- `--out <dir>` — override output root (default: `<input dir>/sop-build` for
  artifacts, `<input dir>/SOPs/` for finals)
- `--formats <list>` — default `docx,pdf` (markdown is always produced as the
  source of truth); honor whatever the user asks for
- `--no-densify` / `--fast` — reduced-fidelity mode (see step 2)

## Requirements

`ffmpeg`, `ffprobe`, `tesseract` (hard requirements — check with `which` first,
tell the user the `brew install` line if missing). `pandoc` + `soffice`
(LibreOffice) for docx/PDF — only needed at the final step; warn early if
missing but don't block preprocessing. `whisper-cli` + a ggml model are
optional, used only when a video has real narration.

## Workflow

### 1. Resolve input and output locations

Single file or directory both work. Choose output locations and tell the user:

```
<input dir>/sop-build/artifacts/<video-name>/   # intermediate, deletable
<input dir>/SOPs/<video-name>/                  # final: SOP.md, .docx, .pdf, images/
```

If the input lives somewhere unwritable or cloud-synced and the user may care
(e.g. a shared Google Drive folder getting hundreds of PNGs), mention it and
offer `--out`.

### 2. Estimate cost and confirm — BEFORE any token spend

```bash
python3 {skill_dir}/scripts/estimate.py <input>
```

This prints per-video duration/resolution/size, predicted frame count, an
estimated token range for the AI analysis pass, and a tier. Then:

- **Tier LOW (≤150k est.)**: state the estimate and proceed.
- **Tier MODERATE/HIGH**: stop and use AskUserQuestion. Present the estimate
  and offer: (a) proceed full fidelity; (b) reduced fidelity — sets
  `SCENE_THRESH=0.08 FALLBACK_EVERY=40` for extract and `--no-densify` for
  diff, roughly halving frames and tokens at the cost of possibly missing
  fast sub-steps; (c) process only a subset of the videos (list them);
  (d) cancel.

Never silently launch a six-figure-token pass.

### 3. Extract (local, free)

```bash
{skill_dir}/scripts/extract.sh <input> [output-root]   # background if >10 min of video
```

Scene-change keyframes (plus periodic fallback frames), auto-detected capture
geometry (aspect ≥ 3:1 → split into `_L`/`_R` monitor halves; else single `_F`
frame scaled to ≤1920px), tesseract OCR sidecar per frame, `index.md`,
`params.json`, and a transcript — real narration is whisper-transcribed;
silent or missing audio is flagged so nobody trusts hallucinated text.

### 4. Diff, classify, densify (local, free)

```bash
python3 {skill_dir}/scripts/diff.py <output-root>/artifacts/*/
```

Per consecutive frame pair: `pct_changed`, changed-region `bbox`,
`ocr_added`/`ocr_removed` line diffs, a pre-cropped change-region PNG, and a
classification: `none` | `localized` | `scroll-or-reflow` |
`app-or-window-switch` | `bulk-change`. Bulk-change intervals (real work
happening between frames — title bar stable but content replaced) are
automatically re-sampled at 1s steps; app switches and scrolls are atomic and
are not. If it warns the densify budget was exhausted, re-run with
`DENSIFY_MAX_FRAMES=300`.

### 5. Author the SOP (the only token spend)

One sub-agent per video; run them in parallel when there are several. Each
agent gets the artifact dir, the SOP definition below, and these reading rules:

- **Escalation ladder — stay low**: read `diffs.json` first (skip
  `change:"none"` pairs — typically half); the OCR diff usually names the
  exact button/field/value involved. Only when that's ambiguous, view the
  `diffs/` crop (~200 tokens). Only for major state changes or unclear
  layouts, view the full `frames/` PNG (~1,500 tokens).
- Infer *actions* from *state diffs*: "field X went from empty to 'ACME'" →
  "Enter the insured name in X". Quote labels exactly as OCR shows them.
- Use `transcript.txt`/`transcript.srt` when real narration exists — it
  states intent the pixels can't show. Ignore flagged silent transcripts.
- A transient UI element (menu open <2s) can fall between frames; if a state
  jump is unexplained, say so in the SOP draft rather than inventing the path,
  and densify that interval if it matters.

The agent writes `SOP.md` plus an `images/` folder (see image rules), into
`<input dir>/SOPs/<video-name>/`.

### 6. Convert to deliverables

```bash
cd "<SOPs>/<video-name>" &&
pandoc SOP.md -o "<video-name> SOP.docx" --resource-path=. &&
soffice --headless --convert-to pdf "<video-name> SOP.docx" --outdir .
```

Verify both files exist and are non-trivial in size (images actually
embedded). Honor `--formats` overrides; SOP.md always remains as source.

### 7. Report

Table of video → SOP files, where artifacts live, actual vs. estimated tokens,
and any steps flagged as uncertain (these need a human pass — say so plainly).

## What a good SOP is

A reader who has never seen the task can execute it without asking anyone.
Required structure:

1. **Title & purpose** — what the procedure accomplishes and when to run it.
2. **Scope & prerequisites** — systems touched (with exact names from OCR),
   required access/roles, inputs that must exist before starting.
3. **Numbered steps**, each with:
   - One action, imperative voice ("Click **Save & Close**", not "the user
     saves"). Exact UI labels in bold, exact values/formulas in code spans.
   - **Expected result** — what the screen shows when the step worked.
   - A screenshot when the step changes visible state (see image rules).
4. **Decision points** as explicit if/then branches, never prose ambiguity.
5. **Troubleshooting** — anything the recording shows going wrong or being
   retried, plus likely failure modes at each fragile step.
6. **Definitions** — jargon, acronyms, system names (only those that appear).
7. **Revision history** — date generated, source video filename and duration,
   "generated from screen recording; steps marked ⚠ need human verification".

Quality bar: no step that says "do the thing" generically; no invented
details — anything not visible in frames or stated in narration is marked ⚠;
timestamps `[mm:ss]` per step so a human can verify against the video.

## Image rules (SOPs must be illustrated)

- Every major step or screen transition gets an image. Prefer the `diffs/`
  crop when the change is localized (it's already zoomed to what matters);
  use the full frame for new screens/dialogs.
- Copy chosen images into the SOP's `images/` folder with descriptive names
  (`step-03-submit-button.png`), don't reference artifact paths — artifacts
  are deletable.
- Reference in markdown with a caption and bounded width so pandoc lays out
  cleanly: `![Step 3 — Submit confirmation](images/step-03-submit-button.png){width=6in}`
- 10–25 images per SOP is the sweet spot; one per trivial keystroke is noise.

## Tunables

| env | default | meaning |
|---|---|---|
| `SCENE_THRESH` | 0.04 | scene-change sensitivity (higher = fewer frames) |
| `MIN_GAP` | 2 | min seconds between keyframes |
| `FALLBACK_EVERY` | 20 | periodic frame interval (s) |
| `DENSIFY_MAX_FRAMES` | 120 | densify budget per video per run |
| `WHISPER_MODEL` | `~/models/ggml-large-v3-turbo.bin` | whisper.cpp model path |
