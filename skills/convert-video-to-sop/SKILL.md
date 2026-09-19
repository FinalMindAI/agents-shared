---
name: convert-video-to-sop
version: 1.2.0
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

- **Escalation ladder — stay low, but go high to pin a destination**: read
  `diffs.json` first (skip `change:"none"` pairs — typically half); the OCR
  diff usually names the exact button/field/value involved. Only when that's
  ambiguous, view the `diffs/` crop (~200 tokens). For major state changes,
  unclear layouts, or to read the exact destination field/cell a value lands
  in (see the data-entry rule below), view the full `frames/` PNG (~1,500
  tokens) — staying low is what produces useless "enter the data" steps.
- Infer *actions* from *state diffs*: "field X went from empty to 'ACME'" →
  "Enter the insured name in X". Quote labels exactly as OCR shows them.
- **Document CHANGES, not labels. The unit of an SOP is what the operator
  did — every field they entered or modified, and nothing they didn't.** A
  form can show a hundred labels; only the cells the operator actually *set*
  during the recording are steps. The arbiter of "changed" is the diff
  pipeline, not the at-rest screen: a field counts as a change when `diffs.json`
  shows its value entering or changing (`ocr_added`/`ocr_removed`, empty→value,
  or value→new value), or you can otherwise see the operator touch it. A field
  that is blank throughout, or that already held its value the first time it
  appeared and never changed, is NOT a step.
- **Exclude the noise — do not enumerate any of these as if they were edits:**
  - **Blank / empty fields** — nothing was entered. Drop them. (A *required*
    field left conspicuously blank can be one line in Troubleshooting, not a
    data-entry row.)
  - **Untouched system defaults, prefilled, and computed/derived values** — a
    value the form arrived with (carried from a prior system, a template
    default, or auto-calculated) that the operator did not change is context,
    not an action. Note such prefill briefly in prose ("the form opens
    pre-filled from IMS with the insured block") — do NOT list each prefilled
    cell field-by-field.
  - **Static labels, column headers, section titles, legends.**
- **Dense forms still get field-by-field treatment — for the fields that
  changed.** When the operator works a multi-field form (a rater/rating screen,
  underwriting screen, valuation form, SOV, application workbook tab) and enters
  many values, enumerate *each entered/changed value* in a table (field | value
  | source) — never collapse real data entry into "complete the POLICY /
  PROPERTY tabs" or "answer the questions." The failure mode runs both ways:
  summarizing real edits into one generic step (under-documentation), AND
  padding the step with every untouched label and blank cell (noise). Capture
  the changes, all of them; skip everything else. A later tab/row that resembles
  an earlier one still has its *own changed values* — enumerate those, don't
  write "same as above" unless the changed values are provably identical.
- **Equal fidelity end-to-end — do not let the tail of a long video decay.**
  The last steps of a 30-minute recording deserve the same field-level detail
  as the first. If you find yourself summarizing later sections because "it's
  the same idea as earlier," stop and enumerate them — that is exactly where
  fidelity is silently lost.
- **Data-entry tasks are source→destination mappings — capture the
  destination, not just the source.** When the operator transfers data into a
  form/sheet/system, the SOP's entire value is *which destination field gets
  which value from which source*. The OCR diff names the value, but a value
  with no target is useless to an operator. For every value entered, pin BOTH
  ends: escalate to the destination frame *at rest* (the populated state after
  a bulk change or state jump) and read the exact target tab, column, row, or
  field label — even when the keystrokes themselves fell between frames and
  only the before/after states exist. Never write a generic "enter the data"
  or "answer the questions" step; write the field-by-field mapping.
- **Trace every answer's source; assume it is on screen somewhere.** Each
  populated value came from somewhere — a specific source document, an email
  line, or *another sheet/tab* in the same workbook. On a dual-monitor capture
  (`_L`/`_R`) the source and destination are usually on opposite monitors;
  read both halves at the same timestamp to pair them, and escalate to higher
  fidelity when OCR can't resolve the source. If a value the operator *entered*
  has no source visible in any frame, flag ⚠ "source not shown — confirm with
  process owner"; do NOT assume it came from the nearest obvious source
  document. (Real example: a policy *deductible* shown on an Underwriting tab is
  sourced from a separate rating sheet — not the Locations tab or the ACORD form
  it sits beside.) **The ⚠ "source not shown" flag is only ever for a value the
  operator demonstrably set.** It is NOT for unchanged defaults, prefills, or
  blank cells — those should not be in the SOP at all, so they generate no
  source question. If you find yourself attaching ⚠ to a field that wasn't
  changed, the fix is to delete the row, not to flag it. A page full of ⚠
  "source not shown" is the tell-tale sign the step is enumerating labels
  instead of edits.
- Use `transcript.txt`/`transcript.srt` when real narration exists — it
  states intent the pixels can't show. Ignore flagged silent transcripts.
- A transient UI element (menu open <2s) can fall between frames; if a state
  jump is unexplained, say so in the SOP draft rather than inventing the path,
  and densify that interval if it matters.

For a long or many-step recording (roughly >15 min or >20 steps), split the
authoring across sub-agents by phase/section of the video (e.g. one per major
part, with overlapping context at the seams) and stitch their drafts, so no
single agent rushes the tail. Each phase agent still follows all reading rules.

The agent writes `SOP.md` plus an `images/` folder (see image rules), into
`<input dir>/SOPs/<video-name>/`.

#### 5b. Completeness self-check — REQUIRED before declaring the SOP done

After the draft exists, run a verification pass (a fresh sub-agent is ideal —
it has no investment in the draft). The check is two-directional — catch both
missing edits and noise:

1. Build the **changed-field set** for each data-entry screen from `diffs.json`
   (the values that entered or changed across that screen's frame pairs —
   `ocr_added`/`ocr_removed`, empty→value, value→new value). This is the set of
   fields the operator actually set.
2. **Under-documentation check:** every changed field must appear in the SOP
   with its value and source. A changed field missing from the SOP is a defect —
   add it (⚠ only if the operator set it but the source isn't shown).
3. **Noise check (equally important):** every data-entry row in the SOP must
   correspond to a changed field. A row that is blank, an untouched
   default/prefill, a computed value the operator didn't set, or a bare label —
   especially one carrying ⚠ "source not shown" — is a defect: **remove it.**
4. Any caption that names a tab/screen not matching the frame's highlighted tab
   is a defect: fix it.

The SOP is not done until (a) every changed field is documented and (b) every
documented data-entry row maps to a real change. Report in the final summary:
screens checked, fields recovered (under-documentation), and rows removed
(noise) — and confirm no ⚠ remains on an unchanged field.

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
   - For **data-entry / transcription steps**, an explicit source→destination
     mapping: each destination field (exact tab + column/row or field label)
     paired with its source (document / sheet / field it's copied from). A
     small table beats prose. Any field whose source is not visible in the
     recording is marked ⚠, not assumed.
4. **Decision points** as explicit if/then branches, never prose ambiguity.
5. **Troubleshooting** — anything the recording shows going wrong or being
   retried, plus likely failure modes at each fragile step.
6. **Definitions** — jargon, acronyms, system names (only those that appear).
7. **Revision history** — date generated, source video filename and duration,
   "generated from screen recording; steps marked ⚠ need human verification".

Quality bar: no step that says "do the thing" generically — and for data
entry, no "enter the data" / "answer the questions" without naming each field
and its source; no invented details — anything not visible in frames or stated
in narration is marked ⚠;
timestamps `[mm:ss]` per step so a human can verify against the video.

## Image rules (SOPs must be illustrated)

- Every major step or screen transition gets an image. Prefer the `diffs/`
  crop when the change is localized (it's already zoomed to what matters);
  use the full frame for new screens/dialogs.
- **Data-entry screens: capture the POPULATED, at-rest state**, not the empty
  form or a transitional frame — the value of the image is showing the filled
  fields. **The caption must name the tab/screen actually shown in the frame.**
  Before finalizing, verify the highlighted/active tab in the chosen frame's
  OCR matches the caption (a frame whose tab bar reads PROPERTY but whose body
  is the INSURED tab is mislabeled — pick the frame that actually shows the tab
  you're captioning).
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
